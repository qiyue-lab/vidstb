"""盘搜（PanSou / kuake.so）网盘资源搜索。

Android 端 pansou-master 调的就是这套后端接口，这里用标准库搬到电脑版：

    POST /api/search    {query, page, platform} -> 资源列表
    GET  /api/platforms                         -> 可用平台
    GET  /api/related?q=                        -> 相关搜索词

后端这些年改过返回结构，两种都兼容：

    新版：{"code":0,"data":{"total":N,"merged_by_type":{"quark":[{url,password,
          note,datetime,source,images}], ...}}}
    旧版：{"results":[{"id","url","title","password","datetime","source"}],
          "total":N,"cached":bool}

注意 platform 参数实测不做过滤（后端一律返回全部平台），所以这里一次拿全，
由调用方按平台挑；好处是切平台不用重新请求（一次搜索 1MB 左右、几秒）。
"""

import hashlib
import json
import re
import urllib.error
import urllib.parse
import urllib.request
from typing import Dict, List, Optional

from app import quark
from app.resources import share_key

# 主域名优先，失败再退回 pansou.de（同一个站的两个域名）
BASE_URLS = ["https://kuake.so", "https://pansou.de"]

USER_AGENT = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")

# 平台标识 -> 显示名（Android 端 PLATFORM_NAMES 的超集）
PLATFORM_NAMES = {
    "quark": "夸克",
    "baidu": "百度",
    "uc": "UC",
    "xunlei": "迅雷",
    "115": "115",
    "aliyun": "阿里云",
    "123": "123",
    "mobile": "移动云",
    "guangya": "光亚",
    "magnet": "磁力",
    "ed2k": "电驴",
    "other": "其它",
}

# /api/platforms 只公布夸克，实测后端支持这些，两个取并集
DEFAULT_PLATFORMS = ["quark", "baidu", "uc", "xunlei", "115", "aliyun", "123", "mobile"]

# 能直接读出文件清单的网盘（复用已有的夸克 / 百度解析）
SCANNABLE = ("quark", "baidu")


class PanSouError(Exception):
    """搜索失败。"""


def platform_label(key: str) -> str:
    return PLATFORM_NAMES.get((key or "").strip(), (key or "其它").strip())


def _openers():
    """两个 opener：先直连，失败再按系统代理走。

    有些环境（比如带代理的沙箱）代理不支持 CONNECT 隧道会直接 502，直连反而能通；
    直连优先可以省下等代理超时的十几秒。
    """
    return [urllib.request.build_opener(urllib.request.ProxyHandler({})),
            urllib.request.build_opener()]


def _http(url: str, data: Optional[bytes] = None, timeout: float = 30.0) -> str:
    last = ""
    for opener in _openers():
        request = urllib.request.Request(url, data=data,
                                         method="POST" if data else "GET")
        request.add_header("User-Agent", USER_AGENT)
        request.add_header("Accept-Encoding", "identity")
        if data:
            request.add_header("Content-Type", "application/json")
        try:
            return opener.open(request, timeout=timeout).read().decode("utf-8", "replace")
        except (urllib.error.URLError, OSError, TimeoutError) as error:
            last = str(error)
            continue
    raise PanSouError(f"连接盘搜失败：{last or '未知错误'}")


def _request(base: str, path: str, body: Optional[dict] = None,
             timeout: float = 30.0) -> dict:
    url = f"{base}/{path.lstrip('/')}"
    raw = _http(url, json.dumps(body).encode("utf-8") if body else None, timeout)
    try:
        return json.loads(raw)
    except ValueError:
        raise PanSouError(f"盘搜返回了无法解析的内容（{len(raw)} 字节）")


def _try(path: str, body: Optional[dict] = None, timeout: float = 30.0,
         bases: Optional[List[str]] = None) -> dict:
    """依次尝试各个域名，返回第一个解析成功的响应。"""
    last = None
    for base in (bases or BASE_URLS):
        try:
            return _request(base, path, body, timeout)
        except PanSouError as error:
            last = error
            continue
    raise last or PanSouError("连接盘搜失败")


# ---------- 结果规范化 ----------

def _item_id(url: str, index: int) -> str:
    digest = hashlib.md5((url or "").encode("utf-8", "replace")).hexdigest()
    return f"{digest[:10]}{index:02d}"


def _format_date(value: str) -> str:
    """'2026-11-01T08:12:08Z' / '2026-11-01T07:16:16+08:00' -> '2026-11-01'。"""
    text = (value or "").strip()
    match = re.match(r"(\d{4})-(\d{2})-(\d{2})", text)
    if not match:
        return ""
    year, month, day = match.groups()
    if year == "0001":        # 后端拿不到时间时会给零值
        return ""
    return f"{year}-{month}-{day}"


def _clean_title(text: str) -> str:
    title = re.sub(r"\s+", " ", (text or "").strip())
    return title


def _normalize_item(row: dict, platform: str, index: int) -> Optional[dict]:
    url = (row.get("url") or "").strip()
    if not url:
        return None
    title = _clean_title(row.get("note") or row.get("title") or "")
    password = (row.get("password") or "").strip()
    parsed = quark.parse_link(url if not password else f"{url} {password}")
    provider = parsed.get("provider") or "other"
    if provider == "unknown":
        provider = "other"
    # 标题为空时用分享 id 顶一下，列表里不至于出现空行
    if not title:
        title = parsed.get("share_id") or url
    return {
        "id": row.get("id") or _item_id(url, index),
        "url": parsed.get("url") or url,
        "raw_url": url,
        "title": title,
        "password": password or parsed.get("passcode") or "",
        "datetime": _format_date(row.get("datetime") or ""),
        "source": (row.get("source") or "").strip(),
        "images": [img for img in (row.get("images") or []) if isinstance(img, str)][:3],
        "platform": platform,
        "platform_label": platform_label(platform),
        "provider": provider,
        "share_id": parsed.get("share_id") or "",
        "share_key": share_key(provider, parsed.get("share_id") or ""),
        "scannable": provider in SCANNABLE and bool(parsed.get("share_id")),
    }


def parse_payload(payload: dict) -> dict:
    """把后端返回（新版 / 旧版都行）整理成统一的搜索结果。"""
    if not isinstance(payload, dict):
        return {"items": [], "total": 0, "counts": {}}

    items: List[dict] = []
    total = 0
    counts: Dict[str, int] = {}

    data = payload.get("data") if isinstance(payload.get("data"), dict) else None
    if data is not None:
        # 新版：按平台分桶
        total = int(data.get("total") or 0)
        merged = data.get("merged_by_type")
        if not isinstance(merged, dict):
            merged = {}
        index = 0
        for platform, rows in merged.items():
            if not isinstance(rows, list):
                continue
            for row in rows:
                if not isinstance(row, dict):
                    continue
                item = _normalize_item(row, platform, index)
                index += 1
                if item is None:
                    continue
                items.append(item)
                counts[platform] = counts.get(platform, 0) + 1
    else:
        # 旧版：扁平的 results
        rows = payload.get("results")
        if not isinstance(rows, list):
            rows = []
        total = int(payload.get("total") or len(rows))
        for index, row in enumerate(rows):
            if not isinstance(row, dict):
                continue
            item = _normalize_item(row, (row.get("platform") or "quark"), index)
            if item is None:
                continue
            items.append(item)
            counts[item["platform"]] = counts.get(item["platform"], 0) + 1

    if not total:
        total = len(items)
    return {"items": items, "total": total, "counts": counts}


# ---------- 对外接口 ----------

def _matched(title: str, query: str) -> bool:
    """后端实测不做关键词过滤（换个词返回的还是同一批），命中判断放到本地。"""
    title = (title or "").lower()
    return all(part in title for part in re.split(r"\s+", (query or "").lower().strip())
               if part)


def search(query: str, page: int = 1, platform: str = "quark",
           timeout: float = 40.0) -> dict:
    """搜一次。platform 只作为「默认展示哪个平台」带回，后端不做过滤。"""
    query = (query or "").strip()
    if not query:
        return {"ok": True, "query": "", "items": [], "total": 0, "counts": {},
                "platform": platform or "quark", "cached": False, "matched": 0}
    payload = _try("api/search", {"query": query, "page": max(1, int(page or 1)),
                                  "platform": platform or "quark"}, timeout)
    code = payload.get("code")
    if code not in (0, None, "0"):
        raise PanSouError(payload.get("message") or f"盘搜返回错误 {code}")
    parsed = parse_payload(payload)
    items = parsed["items"]
    # 命中关键词的排前面，没命中的按时间倒序垫在后面
    for item in items:
        item["matched"] = _matched(item.get("title"), query)
    hits = [item for item in items if item["matched"]]
    rest = sorted((item for item in items if not item["matched"]),
                  key=lambda row: row.get("datetime") or "", reverse=True)
    return {
        "ok": True,
        "query": query,
        "page": max(1, int(page or 1)),
        "platform": platform or "quark",
        "cached": bool(payload.get("cached")),
        "items": hits + rest,
        "total": parsed["total"],
        "matched": len(hits),
        "counts": parsed["counts"],
    }


def platforms(timeout: float = 15.0) -> List[str]:
    """可用平台列表。

    /api/platforms 现在只有 pansou.de 上有，而且只公布夸克，实测后端支持更多，
    所以直接用内置列表（它是接口返回值的超集），省一次网络往返。
    """
    return list(DEFAULT_PLATFORMS)


def related(query: str, timeout: float = 8.0) -> List[str]:
    """相关搜索词；接口不通就返回空列表（不影响主流程）。

    只问主域名、短超时：这个接口时有时无，不能让它拖慢搜索。
    """
    query = (query or "").strip()
    if not query:
        return []
    try:
        payload = _try(f"api/related?{urllib.parse.urlencode({'q': query})}",
                       None, timeout, bases=BASE_URLS[:1])
    except PanSouError:
        return []
    rows = payload.get("resources")
    words: List[str] = []
    if isinstance(rows, list):
        for row in rows:
            if isinstance(row, dict):
                title = (row.get("title") or "").strip()
            else:
                title = str(row).strip()
            if title and title not in words:
                words.append(title)
    return words[:8]
