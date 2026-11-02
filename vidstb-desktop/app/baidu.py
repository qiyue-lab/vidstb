"""百度网盘分享链接解析（免登录，能递归多少就递归多少）。

流程：

    1) GET  https://pan.baidu.com/s/<surl>          -> 302 到 /share/init?surl=xxx
    2) 页面要求提取码时 POST /share/verify          -> randsk，写进 Cookie(BDCLND)
    3) 重新取页面，解析页面内嵌的 "file_list":[...]  -> 顶层文件 / 文件夹
    4) 对每个文件夹 GET /share/list                  -> 子目录内容（递归）

关于子目录：浏览器里"未登录也能进文件夹"，靠的不是登录态，而是提取码验证之后
拿到的那张临时凭证（Cookie 里的 BDCLND，也就是 verify 返回的 randsk）。带着它
调 /share/list 就能列出子目录，和浏览器行为一致；没有提取码的分享能不能列子目录
由百度决定，列不出来就只保留顶层，不影响主流程。config.json 里的 baidu_cookie
是可选的登录态，同样只是用来提高成功率。

仅用标准库实现（urllib + http.cookiejar）。
"""

from __future__ import annotations

import http.cookiejar
import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Dict, List, Optional, Tuple

from .quark import SUBTITLE_EXT, VIDEO_EXT, ScanError

USER_AGENT = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36")
SHARE_URL = "https://pan.baidu.com/s/{surl}"
VERIFY_URL = "https://pan.baidu.com/share/verify"
LIST_URL = "https://pan.baidu.com/share/list"
APP_ID = "250528"

# 百度错误码 -> 能直接展示给用户的话
ERRNO_TEXT = {
    -62: "提取码不正确",
    -63: "提取码不正确",
    -20: "提取码不正确",
    -9: "提取码不正确，或分享的文件已被删除",
    -7: "链接出错，可能已失效",
    2: "请求被拒绝（百度风控；子目录需要登录态）",
    105: "链接已失效",
    112: "链接已失效，或已被分享者取消",
    9019: "需要先通过提取码验证",
    -6: "接口参数不完整",
}


def _errno_text(errno, payload: dict) -> str:
    message = (payload or {}).get("show_msg") or ""
    try:
        code = int(errno)
    except (TypeError, ValueError):
        return message or "网盘返回了错误"
    known = ERRNO_TEXT.get(code)
    if known:
        return known if known == message or not message else f"{known}（{message}）"
    return message or f"网盘返回错误码 {code}"


def _make_cookie(name: str, value: str, domain: str = ".baidu.com") -> http.cookiejar.Cookie:
    return http.cookiejar.Cookie(
        version=0, name=name, value=value, port=None, port_specified=False,
        domain=domain, domain_specified=True, domain_initial_dot=domain.startswith("."),
        path="/", path_specified=True, secure=False, expires=None, discard=True,
        comment=None, comment_url=None, rest={}, rfc2109=False)


def _parse_cookie(raw: str) -> List[Tuple[str, str]]:
    pairs: List[Tuple[str, str]] = []
    for part in (raw or "").split(";"):
        if "=" not in part:
            continue
        name, _, value = part.partition("=")
        name = name.strip()
        value = value.strip()
        if name:
            pairs.append((name, value))
    return pairs


class _Session:
    """带 Cookie 的请求会话（不落盘，只活在内存里）。"""

    def __init__(self, cookie: str = ""):
        self.jar = http.cookiejar.CookieJar()
        for name, value in _parse_cookie(cookie):
            self.jar.set_cookie(_make_cookie(name, value))
        self.opener = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(self.jar))

    def set_cookie(self, name: str, value: str, domain: str = ".pan.baidu.com") -> None:
        self.jar.set_cookie(_make_cookie(name, value, domain))

    def open(self, url: str, data: Optional[bytes] = None, referer: str = "",
             timeout: float = 20.0) -> Tuple[str, str]:
        request = urllib.request.Request(url, data=data, method="POST" if data else "GET")
        request.add_header("User-Agent", USER_AGENT)
        request.add_header("Accept-Encoding", "identity")
        request.add_header("Accept-Language", "zh-CN,zh;q=0.9")
        if referer:
            request.add_header("Referer", referer)
        if data:
            request.add_header("Content-Type", "application/x-www-form-urlencoded")
        try:
            with self.opener.open(request, timeout=timeout) as response:
                body = response.read().decode("utf-8", "replace")
                return body, response.geturl()
        except urllib.error.HTTPError as error:
            try:
                body = error.read().decode("utf-8", "replace")
            except Exception:  # noqa: BLE001
                body = ""
            if error.code == 404:
                raise ScanError("分享链接不存在或已失效（HTTP 404）")
            raise ScanError(f"网盘返回 HTTP {error.code}")
        except urllib.error.URLError as error:
            raise ScanError(f"网络请求失败：{error.reason}")
        except Exception as error:  # noqa: BLE001
            raise ScanError(f"网络请求失败：{error}")


def _match_bracket(text: str, start: int) -> int:
    """给定 '[' 的位置，返回配对的 ']' 之后的下标；找不到返回 -1。"""
    depth = 0
    for index in range(start, len(text)):
        char = text[index]
        if char == "[":
            depth += 1
        elif char == "]":
            depth -= 1
            if depth == 0:
                return index + 1
    return -1


def parse_file_list(html: str) -> List[dict]:
    """从分享页 HTML 里取出内嵌的文件列表。

    页面里有多处叫 file_list 的数组（CSS / JS 资源也叫这个名），
    所以要挑第一个元素里带 server_filename / fs_id 的那个。
    """
    for match in re.finditer(r'"file_list"\s*:\s*\[', html or ""):
        start = match.end() - 1
        end = _match_bracket(html, start)
        if end < 0:
            continue
        try:
            rows = json.loads(html[start:end])
        except ValueError:
            continue
        if rows and isinstance(rows[0], dict) and (
                "server_filename" in rows[0] or "fs_id" in rows[0]):
            return [row for row in rows if isinstance(row, dict)]
    return []


def parse_meta(html: str) -> Dict[str, str]:
    """分享页里的 shareid 与分享者 uk。"""
    meta = {"shareid": "", "uk": ""}
    match = re.search(r'"shareid"\s*:\s*(\d+)', html or "")
    if match:
        meta["shareid"] = match.group(1)
    match = (re.search(r'"share_uk"\s*:\s*"?(\d+)"?', html or "")
             or re.search(r'"oper_id"\s*:\s*"?(\d+)"?', html or ""))
    if match:
        meta["uk"] = match.group(1)
    return meta


def parse_title(html: str) -> str:
    match = re.search(r"<title>(.*?)</title>", html or "", re.S)
    return (match.group(1).strip() if match else "")


def title_hint(html: str) -> str:
    """把页面标题裁成能当剧名用的短标题。

    "flower_photos_免费高速下载|百度网盘-分享无限制" -> "flower_photos"
    """
    raw = parse_title(html)
    if not raw:
        return ""
    head = raw.split("|")[0].strip()
    head = re.sub(r"[_—-]*(免费高速下载|百度网盘.*)$", "", head).strip()
    return head[:60]


def _surl_from_url(url: str, fallback: str) -> str:
    """分享页 302 之后 url 里带真正的 surl（比原始链接少一个前导字符）。"""
    match = re.search(r"[?&]surl=([0-9a-zA-Z_-]+)", url or "")
    if match:
        return match.group(1)
    # /s/1xxxx 里打头的那个字符是链接类型，真正的 surl 要去掉它。
    # 注意只能去掉一个：lstrip("1") 会把 "11abc" 削成 "abc"。
    if fallback and len(fallback) > 1 and fallback[0] in "1234567890":
        return fallback[1:]
    return fallback


def verify(session: _Session, surl: str, passcode: str) -> str:
    query = urllib.parse.urlencode({
        "channel": "chunlei", "clienttype": "0", "web": "1",
        "app_id": APP_ID, "surl": surl, "t": int(time.time() * 1000),
    })
    body = urllib.parse.urlencode(
        {"pwd": passcode, "vcode": "", "vcode_str": ""}).encode("utf-8")
    referer = f"https://pan.baidu.com/share/init?surl={surl}"
    text, _ = session.open(f"{VERIFY_URL}?{query}", data=body, referer=referer)
    try:
        payload = json.loads(text)
    except ValueError:
        raise ScanError("验证接口返回了无法解析的内容")
    if payload.get("errno") != 0:
        raise ScanError(_errno_text(payload.get("errno"), payload))
    randsk = payload.get("randsk") or ""
    if not randsk:
        raise ScanError("验证没有返回访问凭证")
    # 浏览器里就是原样写进 cookie 的（randsk 本身已经 URL 编码过）。
    # 之前这里 unquote 了一次，百度会认成"提取码验证失败"。
    session.set_cookie("BDCLND", randsk)
    return randsk


def fetch_dir(session: _Session, uk: str, shareid: str, dir_path: str,
              page: int = 1, num: int = 100, root: bool = False) -> List[dict]:
    """列一个目录（凭分享凭证 BDCLND，不需要登录）。

    root=True 表示列的是分享根目录，百度要求额外带 root=1，否则会返回 errno 2。
    """
    query = urllib.parse.urlencode({
        "channel": "chunlei", "clienttype": "0", "app_id": APP_ID,
        "uk": uk, "shareid": shareid, "order": "other", "desc": "1",
        "showempty": "0", "web": "5", "page": page, "num": num,
        "dir": dir_path or "/", "t": int(time.time() * 1000),
    })
    if root:
        query += "&root=1"
    text, _ = session.open(f"{LIST_URL}?{query}",
                           referer="https://pan.baidu.com/s/")
    try:
        payload = json.loads(text)
    except ValueError:
        raise ScanError("目录接口返回了无法解析的内容")
    if payload.get("errno") != 0:
        raise ScanError(_errno_text(payload.get("errno"), payload))
    rows = payload.get("list") or []
    return [row for row in rows if isinstance(row, dict)]


def fetch_dir_all(session: _Session, uk: str, shareid: str, dir_path: str,
                  page_size: int = 100, max_pages: int = 20,
                  root: bool = False) -> List[dict]:
    """把一个子目录翻到底（默认一页 100 条，最多 20 页）。"""
    rows: List[dict] = []
    for page in range(1, max_pages + 1):
        part = fetch_dir(session, uk, shareid, dir_path, page=page,
                         num=page_size, root=root)
        rows.extend(part)
        if len(part) < page_size:
            break
        time.sleep(0.08)
    return rows


def _rel_path(path: str) -> str:
    """/sharelink2054992973-679344809409611/flower_photos -> /flower_photos

    目录接口给根目录内容时还会带上 "/我的资源" 这个虚拟根，一并去掉。
    """
    text = (path or "").replace("\\", "/")
    text = re.sub(r"^/sharelink\d+-\d+", "", text)
    text = re.sub(r"^/我的资源(?=/|$)", "", text)
    return text or "/"


def _to_int(value) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _format_time(value) -> str:
    seconds = _to_int(value)
    if seconds <= 0:
        return ""
    if seconds > 10_000_000_000:
        seconds //= 1000
    return time.strftime("%Y-%m-%d %H:%M", time.localtime(seconds))


def _check_page(html: str) -> None:
    title = parse_title(html)
    if "页面不存在" in title:
        raise ScanError("分享链接不存在或已失效")
    if "链接已被取消" in title or "已被取消" in title:
        raise ScanError("分享已被取消")
    if "违规" in title or "违规" in (html or "")[:2000]:
        raise ScanError("分享内容涉嫌违规，已被限制访问")


def scan(surl: str, passcode: str = "", cookie: str = "", max_depth: int = 6,
         max_files: int = 3000) -> dict:
    """读取一个百度分享链接的文件列表。返回 {"tree", "stats", ...}。"""
    if not surl:
        raise ScanError("链接里没有识别到百度网盘的分享 id")
    session = _Session(cookie)
    share_url = SHARE_URL.format(surl=surl)
    # 链接后面带上 pwd 时百度会直接验证并给出完整页面（顺带能拿到带文件名的标题）
    if passcode:
        share_url = f"{share_url}?pwd={urllib.parse.quote(passcode)}"
    html, final_url = session.open(share_url)
    _check_page(html)

    meta = parse_meta(html)
    rows = parse_file_list(html)
    verified = [False]

    def ensure_verified() -> bool:
        """拿到分享凭证（Cookie 里的 BDCLND）。已经拿过就不再重复。"""
        if not passcode or verified[0]:
            return verified[0]
        real_surl = _surl_from_url(final_url, surl)
        verify(session, real_surl, passcode)
        verified[0] = True
        return True

    if not rows and ensure_verified():
        # 有些链接验证完重新取页面就有列表了
        html, final_url = session.open(
            share_url, referer=f"https://pan.baidu.com/share/init?surl="
                               f"{_surl_from_url(final_url, surl)}")
        _check_page(html)
        meta = parse_meta(html)
        rows = parse_file_list(html)

    if not rows and meta.get("uk") and meta.get("shareid") and ensure_verified():
        # 现在百度经常不在页面里塞 file_list（验证完还是"请输入提取码"页），
        # 那就直接问目录接口。列根目录必须带 root=1，否则返回 errno 2。
        try:
            rows = fetch_dir_all(session, meta["uk"], meta["shareid"], "/",
                                 root=True)
        except Exception:  # noqa: BLE001
            rows = []

    if not rows:
        title = parse_title(html)
        if "请输入提取码" in title:
            raise ScanError("这个分享需要提取码，请在资源里填好提取码后再读取")
        if "页面不存在" in title:
            if passcode:
                raise ScanError("提取码不正确，或链接已失效")
            raise ScanError("分享链接不存在或已失效")
        raise ScanError("没能读到文件列表，分享可能已失效或需要登录")

    stats = {"files": 0, "dirs": 0, "total_bytes": 0, "video_files": 0,
             "subtitle_files": 0, "truncated": False, "unexpanded_dirs": 0}
    # 能不能往子目录里钻：先试，失败了就退回"只给顶层"
    descend = {"ok": True, "tried": False, "error": ""}
    uk = meta.get("uk", "")
    shareid = meta.get("shareid", "")

    def list_dir(raw_path: str, root: bool = False) -> List[dict]:
        """列子目录；第一次失败时若还没验证过提取码，就补一次 verify 再试。"""
        time.sleep(0.12)
        try:
            return fetch_dir_all(session, uk, shareid, raw_path, root=root)
        except Exception as error:  # noqa: BLE001
            first = str(error)
        if passcode and not verified[0]:
            try:
                verify(session, _surl_from_url(final_url, surl), passcode)
                verified[0] = True
                return fetch_dir_all(session, uk, shareid, raw_path, root=root)
            except Exception as error:  # noqa: BLE001
                raise ScanError(first) from error
        raise ScanError(first)

    def convert(entries: List[dict], depth: int) -> List[dict]:
        nodes: List[dict] = []
        for row in entries:
            if stats["files"] + stats["dirs"] >= max_files:
                stats["truncated"] = True
                break
            name = (row.get("server_filename") or "").strip()
            if not name:
                continue
            is_dir = _to_int(row.get("isdir")) == 1
            size = 0 if is_dir else _to_int(row.get("size"))
            raw_path = (row.get("path") or "").strip()
            node = {
                "name": name,
                "fid": str(row.get("fs_id") or ""),
                "is_dir": is_dir,
                "size": size,
                "updated_at": _format_time(row.get("server_mtime")
                                           or row.get("local_mtime")),
                "path": _rel_path(raw_path),
            }
            if is_dir:
                stats["dirs"] += 1
                node["children"] = []
                if depth < max_depth and descend["ok"]:
                    try:
                        sub = list_dir(raw_path or node["path"])
                        node["children"] = convert(sub, depth + 1)
                    except Exception as error:  # noqa: BLE001
                        descend["ok"] = False
                        descend["error"] = str(error)
                if depth < max_depth and not descend["ok"]:
                    stats["unexpanded_dirs"] += 1
            else:
                stats["files"] += 1
                stats["total_bytes"] += size
                lower = name.lower()
                if lower.endswith(VIDEO_EXT):
                    stats["video_files"] += 1
                    node["kind"] = "video"
                elif lower.endswith(SUBTITLE_EXT):
                    stats["subtitle_files"] += 1
                    node["kind"] = "subtitle"
                else:
                    node["kind"] = "file"
            nodes.append(node)
        return nodes

    tree = convert(rows, 1)
    hint = title_hint(html)
    if not hint and len(tree) == 1:
        # 页面拿不到标题时（比如走接口兜底），顶层就一个文件夹，先用它的名字
        hint = (tree[0].get("name") or "").strip()
    return {
        "tree": tree,
        "stats": stats,
        "title_hint": hint[:60],
        "shareid": meta.get("shareid", ""),
        "limited": stats["unexpanded_dirs"] > 0,
        "descend_error": descend["error"],
    }
