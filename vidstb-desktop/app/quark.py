"""夸克网盘分享链接解析。

只读取分享页的公开信息（文件列表 / 目录树），不需要登录 Cookie：

    1) POST /1/clouddrive/share/sharepage/token  {"pwd_id":..., "passcode":...}  -> stoken
    2) GET  /1/clouddrive/share/sharepage/detail ?pwd_id=&stoken=&pdir_fid=0 ...  -> data.list
    3) 子目录把 pdir_fid 换成目录 fid 再请求一次

仅用标准库实现（urllib），并保持与项目其它模块一致的“不写 C 盘”约定。
"""

from __future__ import annotations

import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Dict, List, Optional, Tuple

USER_AGENT = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36")
TOKEN_URL = "https://drive-pc.quark.cn/1/clouddrive/share/sharepage/token"
DETAIL_URL = "https://drive-pc.quark.cn/1/clouddrive/share/sharepage/detail"

# 视频后缀，前端会把它们高亮
VIDEO_EXT = (".mp4", ".mkv", ".avi", ".mov", ".flv", ".wmv", ".rmvb", ".rm",
             ".ts", ".m2ts", ".webm", ".mpg", ".mpeg", ".3gp", ".vob", ".m4v")
SUBTITLE_EXT = (".srt", ".ass", ".ssa", ".sub", ".idx", ".vtt", ".sup", ".pgs")

PASSCODE_PATTERNS = [
    # 用 {4,8} 加"后面不能再是字母数字"：不然 6 位码会被截成前 4 位
    re.compile(r"提取码[:：\s]*([0-9a-zA-Z]{4,8})(?![0-9a-zA-Z])"),
    re.compile(r"密码[:：\s]*([0-9a-zA-Z]{4,8})(?![0-9a-zA-Z])"),
    re.compile(r"访问码[:：\s]*([0-9a-zA-Z]{4,8})(?![0-9a-zA-Z])"),
    re.compile(r"[?&]pwd=([0-9a-zA-Z]{4,8})"),
    re.compile(r"[?&]password=([0-9a-zA-Z]{4,8})"),
    re.compile(r"[?&]passcode=([0-9a-zA-Z]{4,8})"),
]

# 夸克分享链接：https://pan.quark.cn/s/<pwd_id>
QUARK_PATTERNS = [
    re.compile(r"pan\.quark\.cn/s/([0-9a-zA-Z_-]+)"),
    re.compile(r"quark\.cn/s/([0-9a-zA-Z_-]+)"),
    re.compile(r"\bpwd_id[=:]\s*([0-9a-zA-Z_-]+)"),
]
# 百度分享链接：https://pan.baidu.com/s/<surl>
BAIDU_PATTERNS = [
    re.compile(r"pan\.baidu\.com/s/([0-9a-zA-Z_-]+)"),
    re.compile(r"pan\.baidu\.com/share/init\?surl=([0-9a-zA-Z_-]+)"),
]


class ScanError(Exception):
    """解析失败：带一个能直接展示给用户的原因。"""

    def __init__(self, message: str, code: Optional[int] = None):
        super().__init__(message)
        self.code = code


def parse_link(text: str) -> Dict[str, str]:
    """从一段文字里识别网盘链接、pwd_id / surl 和提取码。

    支持直接贴整段分享文案（含“链接:xxx 提取码:abcd”）。
    """
    raw = (text or "").strip()
    if not raw:
        return {"provider": "unknown", "url": "", "share_id": "", "passcode": ""}

    passcode = ""
    for pattern in PASSCODE_PATTERNS:
        match = pattern.search(raw)
        if match:
            passcode = match.group(1)
            break

    for pattern in QUARK_PATTERNS:
        match = pattern.search(raw)
        if match:
            share_id = match.group(1)
            return {"provider": "quark", "url": f"https://pan.quark.cn/s/{share_id}",
                    "share_id": share_id, "passcode": passcode}

    for pattern in BAIDU_PATTERNS:
        match = pattern.search(raw)
        if match:
            share_id = match.group(1)
            url = f"https://pan.baidu.com/s/{share_id}"
            if passcode:
                url = f"{url}?pwd={passcode}"
            return {"provider": "baidu", "url": url,
                    "share_id": share_id, "passcode": passcode}

    # 认不出来就当普通网址保存，至少还能一键打开
    return {"provider": "other", "url": raw.split()[0], "share_id": "", "passcode": passcode}


def _request(url: str, data: Optional[bytes] = None, headers: Optional[dict] = None,
             timeout: float = 20.0) -> dict:
    request = urllib.request.Request(url, data=data, method="POST" if data else "GET")
    request.add_header("User-Agent", USER_AGENT)
    request.add_header("Referer", "https://pan.quark.cn/")
    request.add_header("Origin", "https://pan.quark.cn")
    request.add_header("Accept", "application/json, text/plain, */*")
    # 不压缩：urllib 不会自动解压 gzip，省一层麻烦
    request.add_header("Accept-Encoding", "identity")
    if data:
        request.add_header("Content-Type", "application/json;charset=UTF-8")
    for key, value in (headers or {}).items():
        request.add_header(key, value)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            body = response.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as error:
        try:
            body = error.read().decode("utf-8", "replace")
        except Exception:
            body = ""
        raise ScanError(_http_error_text(error.code, body))
    except urllib.error.URLError as error:
        raise ScanError(f"网络请求失败：{error.reason}")
    except Exception as error:  # noqa: BLE001
        raise ScanError(f"网络请求失败：{error}")
    try:
        return json.loads(body)
    except ValueError:
        raise ScanError(f"返回内容不是 JSON：{body[:120]}")


def _http_error_text(code: int, body: str) -> str:
    try:
        payload = json.loads(body)
        message = payload.get("message") or ""
        if message:
            return f"{message}（HTTP {code}）"
    except ValueError:
        pass
    return f"网盘返回 HTTP {code}"


def _unwrap(payload: dict) -> dict:
    """接口用 code==0 表示成功，其余都带 message。"""
    if not isinstance(payload, dict):
        raise ScanError("返回内容格式异常")
    code = payload.get("code", payload.get("status"))
    message = payload.get("message") or ""
    if code not in (0, 200, None):
        known = {
            41006: "分享不存在（链接可能已失效，或粘贴不完整）",
            41007: "分享已被取消",
            14001: "分享链接不存在",
            40003: "需要提取码，或提取码不正确",
        }
        try:
            code_int = int(code)
        except (TypeError, ValueError):
            code_int = -1
        raise ScanError(known.get(code_int) or message or f"接口返回错误码 {code}",
                        code=code_int)
    return payload.get("data") or {}


def fetch_stoken(pwd_id: str, passcode: str = "") -> str:
    payload = json.dumps({"pwd_id": pwd_id, "passcode": passcode or ""}).encode("utf-8")
    data = _unwrap(_request(TOKEN_URL, data=payload))
    stoken = data.get("stoken") or ""
    if not stoken:
        raise ScanError("没有取到访问令牌，分享可能已失效")
    return stoken


def fetch_page(pwd_id: str, stoken: str, pdir_fid: str = "0",
               page: int = 1, page_size: int = 200) -> List[dict]:
    query = urllib.parse.urlencode({
        "pwd_id": pwd_id,
        "stoken": stoken,
        "pdir_fid": pdir_fid,
        "force": "0",
        "_page": page,
        "_page_size": page_size,
        "_sort": "file_type:asc,updated_at:desc",
    })
    data = _unwrap(_request(f"{DETAIL_URL}?{query}"))
    items = data.get("list") or []
    return [item for item in items if isinstance(item, dict)]


def fetch_dir(pwd_id: str, stoken: str, pdir_fid: str = "0",
              max_items: int = 1000) -> List[dict]:
    """拉完一个目录下的全部条目（自动翻页）。"""
    collected: List[dict] = []
    page = 1
    while len(collected) < max_items:
        items = fetch_page(pwd_id, stoken, pdir_fid, page=page)
        if not items:
            break
        collected.extend(items)
        if len(items) < 200:
            break
        page += 1
        time.sleep(0.15)
    return collected[:max_items]


def _size_of(item: dict) -> int:
    try:
        return int(item.get("size") or 0)
    except (TypeError, ValueError):
        return 0


def _name_of(item: dict) -> str:
    return (item.get("file_name") or item.get("name") or "").strip()


def scan(pwd_id: str, passcode: str = "", max_depth: int = 6,
         max_files: int = 3000) -> dict:
    """递归读取分享目录树。返回 {"tree": [...], "stats": {...}}。"""
    stoken = fetch_stoken(pwd_id, passcode)
    stats = {"files": 0, "dirs": 0, "total_bytes": 0,
             "video_files": 0, "subtitle_files": 0, "truncated": False}
    truncated = False

    def walk(pdir_fid: str, depth: int) -> List[dict]:
        nonlocal truncated
        if depth > max_depth or truncated:
            return []
        items = fetch_dir(pwd_id, stoken, pdir_fid)
        nodes: List[dict] = []
        for item in items:
            if stats["files"] + stats["dirs"] >= max_files:
                truncated = True
                break
            name = _name_of(item)
            if not name:
                continue
            is_dir = bool(item.get("dir"))
            size = 0 if is_dir else _size_of(item)
            node = {
                "name": name,
                "fid": str(item.get("fid") or ""),
                "is_dir": is_dir,
                "size": size,
                "updated_at": _format_time(item.get("updated_at")),
            }
            if is_dir:
                stats["dirs"] += 1
                if depth < max_depth:
                    time.sleep(0.12)
                    node["children"] = walk(node["fid"], depth + 1)
                else:
                    node["children"] = []
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

    tree = walk("0", 1)
    stats["truncated"] = truncated
    return {"tree": tree, "stats": stats, "stoken": stoken}


def _format_time(value) -> str:
    """接口给的是毫秒时间戳。"""
    try:
        seconds = int(value)
    except (TypeError, ValueError):
        return ""
    if seconds <= 0:
        return ""
    if seconds > 10_000_000_000:
        seconds //= 1000
    return time.strftime("%Y-%m-%d %H:%M", time.localtime(seconds))


def flatten(tree: List[dict], prefix: str = "") -> List[dict]:
    """把目录树摊平成一维文件清单（用于搜索 / 复制）。"""
    rows: List[dict] = []
    for node in tree or []:
        path = f"{prefix}/{node['name']}" if prefix else node["name"]
        if node.get("is_dir"):
            rows.extend(flatten(node.get("children") or [], path))
        else:
            rows.append({"path": path, "name": node["name"],
                         "size": node.get("size", 0), "kind": node.get("kind", "file")})
    return rows
