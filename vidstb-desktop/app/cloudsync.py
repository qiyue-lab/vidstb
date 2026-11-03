"""与云端资源库（vidstb-cloud）双向同步。

同步模型：云端是合并权威——本地把全量 {items, groups, trash} 发给
POST /api/sync，云端按「同一条分享最后写入者胜」合并后返回全量结果，
本地整体采纳，两边达到一致。

配置（存在本机 config.json）：
    cloud_url       云端地址，例如 http://124.220.134.241:8777
    cloud_password  云端访问口令

只依赖标准库。所有网络错误统一抛 SyncError，让界面能显示一句人话。
"""

from __future__ import annotations

import gzip
import http.client
import json
import ssl
import time
import urllib.parse
from typing import Optional

from . import resources as resources_module

# 同步的全量数据可能带扫描清单，响应给到 180 秒
CONNECT_TIMEOUT = 15
SYNC_TIMEOUT = 180


class SyncError(Exception):
    """同步失败，message 直接给用户看。"""


def _request(method: str, url: str, body: Optional[dict] = None,
             cookie: str = "", timeout: float = 30.0) -> dict:
    """发一次 JSON 请求，返回解析后的 dict；网络层错误包成 SyncError。"""
    parts = urllib.parse.urlparse(url)
    if parts.scheme not in ("http", "https") or not parts.netloc:
        raise SyncError(f"云端地址格式不对：{url}")
    context = ssl.create_default_context() if parts.scheme == "https" else None
    try:
        if parts.scheme == "https":
            conn = http.client.HTTPSConnection(parts.netloc, timeout=timeout,
                                               context=context)
        else:
            conn = http.client.HTTPConnection(parts.netloc, timeout=timeout)
    except OSError as error:
        raise SyncError(f"连不上云端：{error}")
    path = parts.path or "/"
    if parts.query:
        path = f"{path}?{parts.query}"
    headers = {"Accept-Encoding": "gzip"}
    payload = None
    if body is not None:
        payload = json.dumps(body, ensure_ascii=False).encode("utf-8")
        headers["Content-Type"] = "application/json"
        headers["Content-Length"] = str(len(payload))
    if cookie:
        headers["Cookie"] = cookie
    try:
        conn.request(method, path, body=payload, headers=headers)
        response = conn.getresponse()
        raw = response.read()
        if response.getheader("Content-Encoding") == "gzip":
            raw = gzip.decompress(raw)
        set_cookie = response.getheader("Set-Cookie") or ""
        status = response.status
    except (OSError, http.client.HTTPException) as error:
        raise SyncError(f"网络出错：{error}")
    finally:
        conn.close()
    text = raw.decode("utf-8", "replace")
    try:
        data = json.loads(text) if text else {}
    except ValueError:
        raise SyncError(f"云端返回了无法解析的内容（HTTP {status}，"
                        f"{len(text)} 字节）——地址填对了吗？")
    if status == 401:
        raise SyncError("云端拒绝了访问：口令不对，或者登录已过期")
    if status != 200:
        message = data.get("error") if isinstance(data, dict) else ""
        raise SyncError(f"云端返回 HTTP {status}"
                        + (f"：{message}" if message else ""))
    if not isinstance(data, dict):
        raise SyncError("云端返回的数据结构不对")
    return data, set_cookie


def _login(base_url: str, password: str) -> str:
    """拿会话 Cookie。云端没设口令时返回空串（后续请求照样能过）。"""
    data, set_cookie = _request(
        "POST", f"{base_url}/api/login", {"password": password},
        timeout=CONNECT_TIMEOUT)
    if not data.get("ok"):
        raise SyncError(data.get("error") or "登录失败")
    return set_cookie.split(";")[0] if set_cookie else ""


def sync_now(store, config: dict) -> dict:
    """跑一次双向同步，返回 stats（云端计算的合并统计）。"""
    base_url = (config.get("cloud_url") or "").strip().rstrip("/")
    if not base_url:
        raise SyncError("还没配置云端地址（在「云端同步」里填服务器地址）")
    password = config.get("cloud_password") or ""
    cookie = _login(base_url, password)

    payload = {
        "items": store.export(),          # 含 scan / baseline / update 全字段
        "groups": [dict(row) for row in store.groups],
        "trash": [dict(row) for row in store.trash()],
    }
    result, _ = _request("POST", f"{base_url}/api/sync", payload,
                         cookie=cookie, timeout=SYNC_TIMEOUT)
    if not result.get("ok"):
        raise SyncError(result.get("error") or "云端合并失败")

    _apply(store, result)
    stats = result.get("stats") or {}
    summary = {
        "at": time.time(),
        "added": int(stats.get("added") or 0),
        "updated": int(stats.get("updated") or 0),
        "deleted": int(stats.get("deleted") or 0),
        "kept": int(stats.get("kept") or 0),
        "groups_added": int(stats.get("groups_added") or 0),
        "total": len(result.get("items") or []),
        "trash": len(result.get("trash") or []),
        "ok": True,
    }
    store.meta["last_cloud_sync"] = summary
    store.save()
    return summary


def _apply(store, result: dict) -> None:
    """把云端合并后的全量状态落成本地状态。

    整体替换而不是逐条 merge：合并的权威逻辑在云端做过了，本地再合并
    一遍只会引入两份不一样的规则。save() 自己会留 .bak，真出问题能回滚。
    """
    items = [row for row in (result.get("items") or []) if isinstance(row, dict)]
    groups = [row for row in (result.get("groups") or []) if isinstance(row, dict)]
    trash = [row for row in (result.get("trash") or []) if isinstance(row, dict)]
    with store.lock:
        store.groups = groups
        store.items = items
        store.trash_items = trash
        for item in store.items:
            if not item.get("group"):
                item["group"] = ""
            item["status"] = resources_module.normalize_status(item.get("status"))
        store.save()
        store.save_trash()
