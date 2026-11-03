"""Vidstb 电脑版 —— 本地 Web 服务。

只需 Python 标准库，启动后自动打开浏览器：

    python server.py
"""

from __future__ import annotations

import argparse
import json
import os
import queue
import re
import shutil
import socket
import subprocess
import sys
import threading
import time
import uuid
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import List, Optional
from urllib.parse import parse_qs, urlparse

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app import access, config as config_module, ffmpeg_tool, fs_browse, hardware, merger, paths  # noqa: E402
from app import baidu, cloudsync, lan, pansou, quark, resources as resources_module  # noqa: E402
from app.jobs import JobManager  # noqa: E402

# 项目根目录：打包成 exe 后由 paths 负责定位（保证和 python server.py 同一份数据）
BASE_DIR = paths.BASE_DIR
# 页面文件：优先用项目里的（改了前端不用重新打包），找不到再用打包进去的。
# BASE_DIR 可能被 VIDSTB_HOME 指到别处（只放数据），这时前端仍要用源码/包里
# 那一份，否则页面会 404。
WEB_DIR = os.path.join(BASE_DIR, "web")
if not os.path.isdir(WEB_DIR):
    source_web = os.path.join(os.path.dirname(os.path.abspath(__file__)), "web")
    if os.path.isdir(source_web):
        WEB_DIR = source_web
    elif getattr(sys, "frozen", False):
        WEB_DIR = os.path.join(getattr(sys, "_MEIPASS", BASE_DIR), "web")

def _with_passcode(url: str, password: str = "") -> str:
    """把提取码拼进链接，免得 parse_link 认不出光秃秃的 4 位码。"""
    url = (url or "").strip()
    password = (password or "").strip()
    if not url or not password or "pwd=" in url:
        return url
    return f"{url}{'&' if '?' in url else '?'}pwd={password}"


MIME_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".js": "application/javascript; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".json": "application/json; charset=utf-8",
    ".svg": "image/svg+xml",
    ".ico": "image/x-icon",
    ".png": "image/png",
}


class Application:
    def __init__(self):
        self.config = config_module.load_config()
        paths.set_temp_dir(self.config.get("temp_dir", ""))
        try:
            self.temp_cleaned = paths.clean_stale()
        except Exception:
            self.temp_cleaned = 0
        self.ff = ffmpeg_tool.Ffmpeg(self.config.get("ffmpeg_dir", ""))
        self.manager = JobManager(self.ff, self.config)
        self.manager.add_listener(self._on_change)
        self.sse_clients: list = []
        self._clients_lock = threading.Lock()
        self.measure_tasks: dict = {}
        self._measure_lock = threading.Lock()
        self.pick_task_state: Optional[dict] = None
        self._pick_lock = threading.Lock()
        self.resource_store = resources_module.ResourceStore()
        self.resource_scans: dict = {}
        # 注意：update_task() 里会调用 _current_update_task()，同一个锁会被重入，
        # 所以用 RLock，否则自己把自己锁死（接口会永久无响应）
        self._resource_lock = threading.RLock()
        # 批量「检查更新」任务：update_tasks 存全部（只留最近 5 个），
        # _current_update_id 指向当前/最近一次，前端不传 id 时就查它
        self.update_tasks: dict = {}
        self._current_update_id = ""
        self._update_stop: dict = {}
        # 盘搜：搜索结果按关键词缓存（一次搜索要几秒、1MB 左右，别反复打接口），
        # 读文件清单按链接缓存
        self.pansou_cache: dict = {}
        self.pansou_files: dict = {}
        self.pansou_file_cache: dict = {}
        self._pansou_related: dict = {}
        self._pansou_lock = threading.RLock()
        # 云端同步任务：同一时间只跑一个，状态给前端轮询
        self._cloud_sync_state: dict = {"status": "idle"}
        self._cloud_sync_lock = threading.Lock()
        threading.Thread(target=self._auto_update_loop, daemon=True).start()

    # ---------- 云端同步 ----------
    def cloud_sync_state(self) -> dict:
        with self._cloud_sync_lock:
            return dict(self._cloud_sync_state)

    def start_cloud_sync(self) -> dict:
        with self._cloud_sync_lock:
            if self._cloud_sync_state.get("status") == "running":
                return dict(self._cloud_sync_state)
            self._cloud_sync_state = {"status": "running", "message": "正在同步…",
                                      "started_at": time.time()}

        def worker() -> None:
            try:
                summary = cloudsync.sync_now(self.resource_store, self.config)
                state = {"status": "done", "summary": summary,
                         "finished_at": time.time()}
            except cloudsync.SyncError as error:
                state = {"status": "error", "error": str(error),
                         "finished_at": time.time()}
            except Exception as error:  # noqa: BLE001  # 后台线程不能把异常吞了
                state = {"status": "error",
                         "error": f"同步中断：{type(error).__name__} {error}",
                         "finished_at": time.time()}
            with self._cloud_sync_lock:
                self._cloud_sync_state = state
            self._on_change()     # 同步改了数据，通知所有页面刷新

        threading.Thread(target=worker, daemon=True).start()
        return self.cloud_sync_state()

    # ---------- 事件推送 ----------
    def _on_change(self) -> None:
        with self._clients_lock:
            clients = list(self.sse_clients)
        for client in clients:
            try:
                client.put_nowait("state")
            except Exception:
                pass

    def register_client(self) -> "queue.Queue":
        client: "queue.Queue" = queue.Queue()
        with self._clients_lock:
            self.sse_clients.append(client)
        return client

    def unregister_client(self, client) -> None:
        with self._clients_lock:
            if client in self.sse_clients:
                self.sse_clients.remove(client)

    # ---------- 状态 ----------
    def reload_ffmpeg(self) -> None:
        self.ff = ffmpeg_tool.Ffmpeg(self.config.get("ffmpeg_dir", ""))
        self.manager.ffmpeg_tool = self.ff

    # ---------- 系统文件选择框 ----------
    def start_pick(self) -> str:
        """后台弹出系统文件选择框，避免 HTTP 请求被模态弹窗长时间挂住。"""
        with self._pick_lock:
            current = self.pick_task_state
            if current and current.get("status") == "running":
                return current["id"]
            task = {"id": uuid.uuid4().hex[:8], "status": "running",
                    "message": "正在打开文件选择框…", "files": [], "error": ""}
            self.pick_task_state = task
        threading.Thread(target=self._pick_worker, args=(task,), daemon=True).start()
        return task["id"]

    def _pick_worker(self, task: dict) -> None:
        try:
            if not fs_browse.can_show_native_dialog():
                task["status"] = "error"
                task["message"] = "当前环境无法弹出系统文件选择框（服务可能运行在后台会话中）"
                task["error"] = task["message"]
                return
            result = fs_browse.pick_files_native(timeout=180)
            task["files"] = result.get("files", [])
            task["error"] = result.get("error", "")
            if not result.get("ok"):
                task["status"] = "error"
                task["message"] = task["error"] or "系统文件选择框不可用"
            elif task["files"]:
                task["status"] = "done"
                task["message"] = f"已选择 {len(task['files'])} 个文件"
            else:
                task["status"] = "done"
                task["message"] = "已取消"
        except Exception as error:  # noqa: BLE001
            task["status"] = "error"
            task["message"] = str(error)
        finally:
            self._on_change()

    def pick_task(self, task_id: str) -> Optional[dict]:
        task = self.pick_task_state
        if task and (not task_id or task["id"] == task_id):
            return dict(task)
        return None

    # ---------- 合并体积实测校准 ----------
    def start_measure(self, clips, config: Optional[dict] = None) -> str:
        """后台抽样实测各段真实码率，返回任务 id。"""
        import uuid as _uuid

        task_id = _uuid.uuid4().hex[:8]
        task = {"id": task_id, "status": "running", "progress": 0,
                "message": "正在准备…", "result": None}
        with self._measure_lock:
            # 只保留最近 8 个任务，避免长期运行后堆积
            for old in list(self.measure_tasks)[:-7]:
                self.measure_tasks.pop(old, None)
            self.measure_tasks[task_id] = task
        threading.Thread(target=self._measure_worker,
                         args=(task_id, clips, config or self.config), daemon=True).start()
        return task_id

    def _measure_worker(self, task_id: str, clips, config: dict) -> None:
        task = self.measure_tasks.get(task_id)
        if task is None:
            return
        try:
            encoder = hardware.choose_encoder(self.ff, config,
                                              self.manager.hardware_info())
            task["encoder"] = encoder
            rates: List[float] = []
            total = len(clips)
            for index, clip in enumerate(clips):
                task["progress"] = int(index * 100 / max(1, total))
                task["message"] = f"正在抽样第 {index + 1}/{total} 段…"
                rate = merger.sample_rate(self.ff, clip, config, encoder)
                if rate is None:
                    task.update(status="error", progress=100,
                                message=f"{clip.name} 抽样编码失败")
                    return
                rates.append(rate)
            result = merger.estimate_from_samples(clips, rates, config)
            result["encoder"] = encoder
            task.update(status="done", progress=100, result=result,
                        message=f"实测 {result['measured_bitrate_mbps']:.1f} Mbps")
        except Exception as error:
            task.update(status="error", progress=100, message=str(error))

    def measure_task(self, task_id: str) -> Optional[dict]:
        with self._measure_lock:
            task = self.measure_tasks.get(task_id)
            return dict(task) if task else None

    # ---------- 网盘资源解析 ----------
    def start_resource_scan(self, item_id: str, passcode: str = "") -> str:
        """后台解析一个夸克分享链接的目录树，返回任务 id。"""
        task_id = uuid.uuid4().hex[:8]
        task = {"id": task_id, "item_id": item_id, "status": "running",
                "progress": 0, "message": "正在连接网盘…", "result": None}
        with self._resource_lock:
            for old in list(self.resource_scans)[:-7]:
                self.resource_scans.pop(old, None)
            self.resource_scans[task_id] = task
        threading.Thread(target=self._resource_scan_worker,
                         args=(task_id, item_id, passcode), daemon=True).start()
        return task_id

    def _scan_item(self, item: dict, passcode: str = "") -> dict:
        """真的去网盘读一次清单（夸克 / 百度），返回结果 dict。"""
        provider = item.get("provider")
        share_id = item.get("share_id") or ""
        code = passcode or item.get("passcode") or ""
        if provider == resources_module.PROVIDER_BAIDU:
            data = baidu.scan(share_id, code,
                              cookie=(self.config.get("baidu_cookie") or ""))
        else:
            data = quark.scan(share_id, code)
        tree = data.get("tree") or []
        return {
            "ok": True,
            "error": "",
            "tree": tree,
            "files": quark.flatten(tree),
            "stats": data.get("stats") or {},
            "title_hint": data.get("title_hint", ""),
        }

    def _resource_scan_worker(self, task_id: str, item_id: str, passcode: str) -> None:
        task = self.resource_scans.get(task_id)
        if task is None:
            return
        try:
            item = self.resource_store.find(item_id)
            if item is None:
                task.update(status="error", message="资源不存在")
                return
            provider = item.get("provider")
            if provider not in (resources_module.PROVIDER_QUARK,
                                resources_module.PROVIDER_BAIDU):
                task.update(status="error",
                            message="只有夸克 / 百度网盘支持直接读取文件列表")
                return
            task["message"] = ("正在打开百度分享页…" if provider == resources_module.PROVIDER_BAIDU
                               else "正在获取访问令牌…")
            result = self._scan_item(item, passcode)
            self.resource_store.set_scan(item_id, result)
            stats = result["stats"]
            task.update(status="done", progress=100, result=result,
                        message=self._scan_message(item_id, stats))
        except Exception as error:  # noqa: BLE001
            reason = str(error)
            self.resource_store.set_scan(item_id, {"ok": False, "error": reason,
                                                   "tree": [], "files": [], "stats": {}})
            task.update(status="error", progress=100, message=reason)
        finally:
            self._on_change()

    def _scan_message(self, item_id: str, stats: dict) -> str:
        """读取完成的提示语；有新文件时直接把「新增 N 个」说出来。"""
        base = (f"共 {stats.get('files', 0)} 个文件、"
                f"{stats.get('video_files', 0)} 个视频")
        item = self.resource_store.find(item_id) or {}
        count = int((item.get("update") or {}).get("new_count") or 0)
        return f"{base}，本次新增 {count} 个文件（{self._item_title(item)}）" if count else base

    @staticmethod
    def _item_title(item: dict) -> str:
        return (item.get("title") or "").strip()[:24]

    # ---------- 网盘资源：批量检查更新 ----------
    def start_update_check(self, item_ids: Optional[list] = None) -> str:
        """串行检查一批（默认全部可读取的）资源有没有新文件，返回任务 id。"""
        if self.update_task_running():
            task = self._current_update_task()
            return task["id"] if task else ""
        task_id = uuid.uuid4().hex[:8]
        with self._resource_lock:
            for old in list(self.update_tasks)[:-4]:
                self.update_tasks.pop(old, None)
            store = self.resource_store
            wanted = [row for row in (item_ids or []) if row]
            items = []
            for item in store.items:
                if wanted and item.get("id") not in wanted:
                    continue
                if item.get("provider") not in (resources_module.PROVIDER_QUARK,
                                                resources_module.PROVIDER_BAIDU):
                    continue
                if not item.get("share_id"):
                    continue
                # 标了「已失效」的就不用浪费时间了
                if item.get("status") == resources_module.STATUS_DEAD:
                    continue
                items.append(item.get("id"))
            task = {"id": task_id, "status": "running", "progress": 0,
                    "message": "正在检查更新…", "total": len(items), "done": 0,
                    "result": None}
            self.update_tasks[task_id] = task
            self._current_update_id = task_id
        threading.Thread(target=self._update_check_worker,
                         args=(task_id, items), daemon=True).start()
        return task_id

    def _update_check_worker(self, task_id: str, item_ids: List[str]) -> None:
        task = self.update_tasks.get(task_id)
        if task is None:
            return
        total = max(1, len(item_ids))
        updated: List[dict] = []
        failed: List[dict] = []
        try:
            for index, item_id in enumerate(item_ids):
                if self._update_stop.get(task_id):
                    break
                item = self.resource_store.find(item_id)
                if item is None:
                    continue
                task["message"] = f"正在检查 {index + 1}/{total}：{self._item_title(item)}"
                try:
                    result = self._scan_item(item)
                    self.resource_store.set_scan(item_id, result)
                    fresh = self.resource_store.find(item_id) or {}
                    count = int((fresh.get("update") or {}).get("new_count") or 0)
                    if count:
                        updated.append({"id": item_id, "title": self._item_title(fresh),
                                        "new_count": count,
                                        "new_bytes": int((fresh.get("update") or {})
                                                         .get("new_bytes") or 0)})
                except Exception as error:  # noqa: BLE001
                    failed.append({"id": item_id, "title": self._item_title(item),
                                   "error": str(error)})
                task["done"] = index + 1
                task["progress"] = int((index + 1) * 100 / total)
                self._on_change()
            summary = f"检查了 {len(item_ids)} 条资源，{len(updated)} 条有更新"
            if failed:
                summary += f"，{len(failed)} 条读取失败"
            task.update(status="done", progress=100, message=summary,
                        result={"checked": len(item_ids), "updated": updated,
                                "failed": failed, "updated_count": len(updated)})
        finally:
            with self._resource_lock:
                if self._current_update_id == task_id:
                    self._current_update_id = ""
                self._update_stop.pop(task_id, None)
            self.resource_store.set_meta("last_update_check", time.time())
            self._on_change()

    def update_task_running(self) -> bool:
        with self._resource_lock:
            task = self.update_tasks.get(self._current_update_id)
            return bool(task and task.get("status") == "running")

    def _current_update_task(self) -> Optional[dict]:
        with self._resource_lock:
            task = self.update_tasks.get(self._current_update_id)
            return dict(task) if task else None

    def update_task(self, task_id: str = "") -> Optional[dict]:
        """不传 id 时返回当前 / 最近一次检查任务（注意别在持锁时嵌套调用）。"""
        with self._resource_lock:
            key = task_id or self._current_update_id
            task = self.update_tasks.get(key)
            return dict(task) if task else None

    def stop_update_check(self) -> bool:
        with self._resource_lock:
            current = self._current_update_id
            if not current:
                return False
            self._update_stop[current] = True
            return True

    def _auto_update_loop(self) -> None:
        """按 config 里的间隔，后台自动检查资源更新（服务开着就会跑）。"""
        time.sleep(15)  # 启动时先让别的初始化跑完
        while True:
            try:
                if not self.config.get("auto_update_check"):
                    time.sleep(60)
                    continue
                hours = float(self.config.get("update_check_hours") or 6)
                last = float(self.resource_store.get_meta("last_update_check", 0) or 0)
                if time.time() - last >= max(0.1, hours) * 3600:
                    self.start_update_check()
                    time.sleep(60)
                    continue
            except Exception:  # noqa: BLE001  # 定时线程不该因为一次异常就死掉
                pass
            time.sleep(60)

    def resource_scan_task(self, task_id: str) -> Optional[dict]:
        with self._resource_lock:
            task = self.resource_scans.get(task_id)
            return dict(task) if task else None

    def resource_state(self, keyword: str = "", provider: str = "",
                       status: str = "", tag: str = "", group: str = "",
                       updates: bool = False) -> dict:
        items = self.resource_store.list(keyword=keyword, provider=provider,
                                         status=status, tag=tag, group=group,
                                         updates=updates)
        return {
            "items": [resources_module.public_view(item) for item in items],
            "stats": self.resource_store.stats(),
            "tags": self.resource_store.tags(),
            "groups": self.resource_store.groups_with_stats(),
            "ungrouped": self.resource_store.ungrouped_stats(),
            "trash_count": len(self.resource_store.trash()),
            "restored_from_backup": self.resource_store.restored_from_backup,
            "last_update_check": self.resource_store.get_meta("last_update_check", 0) or 0,
            "update_task": self._current_update_task(),
        }

    # ---------- 盘搜 ----------
    def pansou_saved_map(self) -> Dict[str, str]:
        """share_key -> 资源 id，前端用它把「已经存过」的结果标出来。"""
        return self.resource_store.saved_map()

    def _mark_saved(self, items: List[dict]) -> None:
        """给搜索结果打上 saved / saved_id（同一条资源重复存会更新而不是新建）。"""
        if not items:
            return
        saved = self.resource_store.saved_map()
        for row in items:
            if not isinstance(row, dict):
                continue
            key = row.get("share_key") or ""
            row["saved"] = bool(key and key in saved)
            row["saved_id"] = saved.get(key, "")

    def pansou_save(self, url: str, title: str = "", password: str = "",
                    group: str = "", status: str = "",
                    tags: Optional[List[str]] = None, note: str = "") -> dict:
        """把一条搜索结果存进「我的资源」。同一个分享已存过就更新。"""
        text = _with_passcode(url, password)
        item, created = self.resource_store.add_or_update(
            text, title=title or "", tags=tags, note=note,
            status=status or "", group=group or "")
        return {"ok": True, "created": created,
                "item": resources_module.public_view(item)}

    def pansou_save_many(self, rows: List[dict], group: str = "",
                         status: str = "",
                         tags: Optional[List[str]] = None) -> dict:
        """批量保存搜索结果。返回新建 / 更新各多少条。"""
        created = 0
        updated = 0
        failed: List[dict] = []
        saved_items: List[dict] = []
        for row in rows or []:
            if not isinstance(row, dict):
                continue
            url = (row.get("url") or "").strip()
            if not url:
                continue
            try:
                result = self.pansou_save(
                    url, row.get("title", ""), row.get("password", ""),
                    group=group, status=status, tags=tags)
            except Exception as error:  # noqa: BLE001
                failed.append({"url": url, "error": str(error)})
                continue
            if result.get("created"):
                created += 1
            else:
                updated += 1
            saved_items.append(result["item"])
        return {"ok": True, "created": created, "updated": updated,
                "failed": failed, "items": saved_items}

    def pansou_search(self, keyword: str, platform: str = "quark",
                      refresh: bool = False) -> dict:
        """搜一次网盘资源；同样关键词 10 分钟内直接走缓存。"""
        keyword = (keyword or "").strip()
        if not keyword:
            return {"ok": True, "items": [], "total": 0, "counts": {},
                    "matched": 0, "platforms": [], "related": [], "cached": False}
        with self._pansou_lock:
            cached = self.pansou_cache.get(keyword)
        now = time.time()
        if cached and not refresh and now - cached.get("at", 0) < 600:
            data = dict(cached["data"])
            data["cached"] = True
            self._mark_saved(data.get("items") or [])
            return data
        try:
            result = pansou.search(keyword, platform=platform or "quark")
        except Exception as error:  # noqa: BLE001
            return {"ok": False, "error": str(error), "items": [], "total": 0,
                    "counts": {}, "matched": 0, "platforms": [], "related": []}
        result["platforms"] = self.pansou_platforms()
        result["related"] = self._pansou_related.get(keyword) or []
        result["at"] = now
        self._mark_saved(result.get("items") or [])
        with self._pansou_lock:
            self.pansou_cache[keyword] = {"at": now, "data": dict(result)}
            if len(self.pansou_cache) > 12:      # 只留最近十来个关键词
                oldest = min(self.pansou_cache, key=lambda k: self.pansou_cache[k]["at"])
                self.pansou_cache.pop(oldest, None)
        return result

    def pansou_related(self, keyword: str) -> List[str]:
        """相关搜索词；接口时有时无，拿不到就返回空，不拖慢主流程。"""
        keyword = (keyword or "").strip()
        if not keyword:
            return []
        with self._pansou_lock:
            if keyword in self._pansou_related:
                return list(self._pansou_related[keyword])
        words = pansou.related(keyword)
        with self._pansou_lock:
            self._pansou_related[keyword] = words
            if len(self._pansou_related) > 20:
                self._pansou_related.clear()
        return words

    def pansou_platforms(self) -> List[str]:
        if not getattr(self, "_pansou_platform_cache", None):
            self._pansou_platform_cache = pansou.platforms()
        return list(self._pansou_platform_cache)

    def start_pansou_files(self, url: str, password: str = "",
                           refresh: bool = False) -> str:
        """后台读一条搜索结果里的文件清单，返回任务 id。"""
        task_id = uuid.uuid4().hex[:8]
        cache_key = (url or "").strip() + "|" + (password or "").strip()
        with self._pansou_lock:
            hit = self.pansou_file_cache.get(cache_key)
        if hit and not refresh:
            task = {"id": task_id, "status": "done", "progress": 100,
                    "message": "来自缓存", "result": hit}
            with self._pansou_lock:
                self.pansou_files[task_id] = task
            return task_id
        task = {"id": task_id, "status": "running", "progress": 0,
                "message": "正在连接网盘…", "result": None}
        with self._pansou_lock:
            for old in list(self.pansou_files)[:-9]:
                self.pansou_files.pop(old, None)
            self.pansou_files[task_id] = task
        threading.Thread(target=self._pansou_files_worker,
                         args=(task_id, url, password), daemon=True).start()
        return task_id

    def _pansou_files_worker(self, task_id: str, url: str, password: str) -> None:
        task = self.pansou_files.get(task_id)
        if task is None:
            return
        parsed = quark.parse_link(f"{url} {password}".strip())
        provider = parsed.get("provider") or "other"
        if provider not in (resources_module.PROVIDER_QUARK,
                            resources_module.PROVIDER_BAIDU):
            task.update(status="error", progress=100,
                        message="这个网盘还不支持直接读文件，只能复制链接去浏览器打开")
            return
        task["message"] = ("正在打开百度分享页…" if provider == resources_module.PROVIDER_BAIDU
                           else "正在获取访问令牌…")
        try:
            result = self._scan_item({"provider": provider,
                                      "share_id": parsed.get("share_id") or "",
                                      "passcode": parsed.get("passcode") or password},
                                     password)
        except Exception as error:  # noqa: BLE001
            task.update(status="error", progress=100, message=str(error))
            return
        stats = result.get("stats") or {}
        task.update(status="done", progress=100, result=result,
                    message=f"共 {stats.get('files', 0)} 个文件、"
                            f"{stats.get('video_files', 0)} 个视频")
        with self._pansou_lock:
            self.pansou_file_cache[(url or "").strip() + "|" + (password or "").strip()] = result
            if len(self.pansou_file_cache) > 40:
                self.pansou_file_cache.clear()

    def pansou_files_task(self, task_id: str) -> Optional[dict]:
        with self._pansou_lock:
            task = self.pansou_files.get(task_id)
            return dict(task) if task else None

    def state(self) -> dict:
        ffmpeg_info = self.ff.info()
        hardware_info = []
        hardware_error = ""
        try:
            hardware_info = self.manager.hardware_info()
        except Exception as error:
            hardware_error = str(error)
        return {
            "config": self.config,
            "ffmpeg": ffmpeg_info,
            "hardware": hardware_info,
            "hardware_error": hardware_error,
            "gpu": self.manager.gpu_names(),
            "queue": self.manager.state(),
            "quick_links": fs_browse.quick_links(),
            "resources": self.resource_state(),
        }


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "VidstbDesktop/1.0"

    # ---------- 基础工具 ----------
    @property
    def app(self) -> Application:
        return self.server.app  # type: ignore[attr-defined]

    def log_message(self, fmt, *args):  # 静音访问日志
        pass

    def _send(self, status: int, body: bytes, content_type: str, extra: dict = None) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        for key, value in (extra or {}).items():
            self.send_header(key, value)
        self.end_headers()
        if body:
            self.wfile.write(body)

    def _send_json(self, payload, status: int = 200, extra: dict = None) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self._send(status, body, "application/json; charset=utf-8", extra)

    def _send_file(self, relative: str) -> None:
        path = os.path.normpath(os.path.join(WEB_DIR, relative))
        if not path.startswith(WEB_DIR) or not os.path.isfile(path):
            self._send(404, b"Not Found", "text/plain; charset=utf-8")
            return
        with open(path, "rb") as handle:
            body = handle.read()
        content_type = MIME_TYPES.get(os.path.splitext(path)[1].lower(),
                                      "application/octet-stream")
        self._send(200, body, content_type)

    def _body_json(self) -> dict:
        length = int(self.headers.get("Content-Length") or 0)
        if length <= 0:
            return {}
        raw = self.rfile.read(length)
        try:
            return json.loads(raw.decode("utf-8"))
        except ValueError:
            return {}

    # ---------- 访问控制 ----------
    def _client_ip(self) -> str:
        return self.client_address[0] if self.client_address else ""

    def _auth_cookie(self, clear: bool = False) -> str:
        value = "" if clear else (self.app.config.get("access_password") or "")
        age = "0" if clear else "2592000"      # 30 天
        return (f"{access.COOKIE_NAME}={value}; Path=/; HttpOnly; "
                f"SameSite=Lax; Max-Age={age}")

    def _guard(self, path: str) -> bool:
        """访问控制。返回 False 表示响应已经发完，别再往下走了。"""
        ip = self._client_ip()
        decision = access_decision(self.app.config, ip,
                                   self.headers.get("Cookie", ""), path)
        if decision == "ok":
            return True
        if decision == "denied":
            self._send(403, _denied_page(ip).encode("utf-8"),
                       "text/html; charset=utf-8")
            return False
        # 需要输密码
        if path.startswith("/api/"):
            self._send_json({"error": "需要密码", "need_login": True}, 401)
        else:
            self._send(200, _login_page().encode("utf-8"),
                       "text/html; charset=utf-8")
        return False

    # ---------- 路由 ----------
    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        path = parsed.path
        query = parse_qs(parsed.query)

        if not self._guard(path):
            return
        if path == "/login":
            self._send(200, _login_page().encode("utf-8"),
                       "text/html; charset=utf-8")
            return
        if path in ("/", "/index.html"):
            self._send_file("index.html")
            return
        if path.startswith("/api/"):
            self._api_get(path, query)
            return
        self._send_file(path.lstrip("/") or "index.html")

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        path = parsed.path
        if not path.startswith("/api/"):
            self._send_json({"error": "not found"}, 404)
            return
        if not self._guard(path):
            return
        payload = self._body_json()
        try:
            self._api_post(path, payload)
        except Exception as error:  # pragma: no cover
            self._send_json({"error": str(error)}, 500)

    def do_DELETE(self) -> None:
        parsed = urlparse(self.path)
        path = parsed.path
        if not self._guard(path):
            return
        parts = [part for part in path.split("/") if part]
        if len(parts) == 3 and parts[0] == "api" and parts[1] == "jobs":
            ok = self.app.manager.remove_job(parts[2])
            self._send_json({"ok": ok})
            return
        if len(parts) >= 3 and parts[0] == "api" and parts[1] == "resources" \
                and parts[2] == "trash":
            # /api/resources/trash —— 清空回收站
            if len(parts) == 3:
                removed = self.app.resource_store.clear_trash()
                self._send_json({"ok": True, "removed": removed})
                self.app._on_change()
                return
            # /api/resources/trash/<id> —— 彻底删除回收站里的一条
            ok = self.app.resource_store.purge(parts[3])
            self._send_json({"ok": ok})
            self.app._on_change()
            return
        if len(parts) == 3 and parts[0] == "api" and parts[1] == "resources":
            ok = self.app.resource_store.remove(parts[2])
            self._send_json({"ok": ok})
            self.app._on_change()
            return
        # /api/groups/<id> —— 删除分组（组里的资源回到未分组）
        if len(parts) == 3 and parts[0] == "api" and parts[1] == "groups":
            moved = self.app.resource_store.remove_group(parts[2])
            self._send_json({"ok": moved >= 0, "moved": moved})
            self.app._on_change()
            return
        self._send_json({"error": "not found"}, 404)

    # ---------- GET 接口 ----------
    def _api_get(self, path: str, query: dict) -> None:
        if path == "/api/events":
            self._stream_events()
            return
        if path == "/api/lan":
            self._send_json(lan_info(self.server))  # type: ignore[attr-defined]
            return
        if path == "/api/access":
            self._send_json(access_state(self.app, self._client_ip()))
            return
        if path == "/api/state":
            self._send_json(self.app.state())
            return
        if path == "/api/ffmpeg/detect":
            refresh = (query.get("refresh", ["0"])[0] or "0") not in ("0", "false", "")
            info = self.app.manager.hardware_info(refresh=refresh)
            self._send_json({"hardware": info,
                             "gpu": self.app.manager.gpu_names(refresh=refresh),
                             "ffmpeg": self.app.ff.info()})
            return
        if path == "/api/fs/list":
            target = (query.get("path", [""])[0] or "")
            self._send_json(fs_browse.list_dir(target))
            return
        if path == "/api/probe":
            target = (query.get("path", [""])[0] or "")
            self._probe(target)
            return
        if path == "/api/jobs/log":
            job_id = (query.get("id", [""])[0] or "")
            self._send_json({"log": self.app.manager.job_log(job_id)})
            return
        if path == "/api/merge/measure":
            task_id = (query.get("id", [""])[0] or "")
            task = self.app.measure_task(task_id)
            self._send_json(task or {"error": "任务不存在"})
            return
        if path == "/api/fs/pick":
            task_id = (query.get("id", [""])[0] or "")
            task = self.app.pick_task(task_id)
            self._send_json(task or {"id": "", "status": "idle",
                                     "message": "没有进行中的文件选择", "files": []})
            return
        if path == "/api/resources":
            self._send_json(self.app.resource_state(
                keyword=(query.get("keyword", [""])[0] or ""),
                provider=(query.get("provider", [""])[0] or ""),
                status=(query.get("status", [""])[0] or ""),
                tag=(query.get("tag", [""])[0] or ""),
                group=(query.get("group", [""])[0] or ""),
                updates=(query.get("updates", [""])[0] or "") in ("1", "true")))
            return
        if path == "/api/groups":
            self._send_json({"ok": True,
                             "groups": self.app.resource_store.groups_with_stats()})
            return
        if path == "/api/cloud/sync":
            self._send_json({"ok": True, "task": self.app.cloud_sync_state(),
                             "last": self.app.resource_store.meta.get(
                                 "last_cloud_sync")})
            return
        if path == "/api/pansou/files":
            task = self.app.pansou_files_task((query.get("id", [""])[0] or ""))
            self._send_json(task or {"id": "", "status": "idle",
                                     "message": "没有进行中的读取", "progress": 0})
            return
        if path == "/api/pansou/platforms":
            self._send_json({"ok": True, "platforms": self.app.pansou_platforms()})
            return
        if path == "/api/pansou/saved":
            self._send_json({"ok": True, "saved": self.app.pansou_saved_map()})
            return
        if path == "/api/pansou/related":
            self._send_json({"ok": True,
                             "related": self.app.pansou_related(
                                 (query.get("q", [""])[0] or ""))})
            return
        if path.startswith("/api/resources/"):
            self._resources_get(path, query)
            return
        self._send_json({"error": "not found"}, 404)

    def _resources_get(self, path: str, query: dict) -> None:
        store = self.app.resource_store
        if path == "/api/resources/scan":
            task_id = (query.get("id", [""])[0] or "")
            task = self.app.resource_scan_task(task_id)
            self._send_json(task or {"id": "", "status": "idle", "message": "没有进行中的解析"})
            return
        if path == "/api/resources/check-updates":
            task = self.app.update_task((query.get("id", [""])[0] or ""))
            self._send_json(task or {"id": "", "status": "idle", "message": "没有进行中的检查",
                                     "progress": 0})
            return
        if path == "/api/resources/export":
            self._send_json({"ok": True, "items": store.export()})
            return
        if path == "/api/resources/trash":
            self._send_json({"ok": True,
                             "items": [resources_module.public_view(row)
                                       for row in store.trash()]})
            return
        parts = [part for part in path.split("/") if part]
        # /api/resources/<id>/files —— 返回缓存的文件清单
        if len(parts) == 4 and parts[3] == "files":
            item = store.find(parts[2])
            scan = (item or {}).get("scan") or {}
            self._send_json({"ok": bool(scan.get("ok")),
                             "error": scan.get("error", ""),
                             "files": scan.get("files", []),
                             "tree": scan.get("tree", []),
                             "stats": scan.get("stats", {})})
            return
        self._send_json({"error": "not found"}, 404)

    def _probe(self, path: str) -> None:
        if not path:
            self._send_json({"error": "缺少路径"}, 400)
            return
        if not self.app.ff.available:
            self._send_json({"error": self.app.ff.error}, 503)
            return
        if not os.path.isfile(path):
            self._send_json({"error": "文件不存在"}, 404)
            return
        try:
            result = self.app.manager.probe_file(path)
        except Exception as error:
            self._send_json({"error": str(error)}, 500)
            return
        self._send_json({"probe": result.to_dict()})

    def _stream_events(self) -> None:
        client = self.app.register_client()
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "keep-alive")
        self.send_header("X-Accel-Buffering", "no")
        self.end_headers()
        try:
            self.wfile.write(b"retry: 2000\n\n")
            self.wfile.flush()
            last_sent = 0.0
            while True:
                try:
                    client.get(timeout=1.0)
                except queue.Empty:
                    if time.time() - last_sent < 15:
                        continue
                payload = json.dumps(self.app.state(), ensure_ascii=False)
                message = f"data: {payload}\n\n".encode("utf-8")
                self.wfile.write(message)
                self.wfile.flush()
                last_sent = time.time()
        except (BrokenPipeError, ConnectionResetError, OSError):
            pass
        finally:
            self.app.unregister_client(client)

    # ---------- POST 接口 ----------
    def _api_post(self, path: str, payload: dict) -> None:
        manager = self.app.manager
        if path == "/api/login":
            if access.check_password(self.app.config, payload.get("password", "")):
                self._send_json({"ok": True},
                                extra={"Set-Cookie": self._auth_cookie()})
            else:
                self._send_json({"ok": False, "error": "密码不对"}, 401)
            return
        if path == "/api/logout":
            self._send_json({"ok": True},
                            extra={"Set-Cookie": self._auth_cookie(clear=True)})
            return
        if path == "/api/access":
            self._update_access(payload)
            return
        if path == "/api/config":
            self._update_config(payload)
            return
        if path == "/api/probe":
            self._probe(payload.get("path", ""))
            return
        if path == "/api/fs/scan":
            target = payload.get("path", "")
            recursive = bool(payload.get("recursive", False))
            files = fs_browse.scan_dir(target, recursive)
            self._send_json({"files": files})
            return
        if path == "/api/fs/pick":
            self._send_json({"ok": True, "id": self.app.start_pick()})
            return
        if path == "/api/merge/estimate":
            self._merge_estimate(payload)
            return
        if path == "/api/merge/jobs":
            self._merge_jobs(payload)
            return
        if path == "/api/merge/measure":
            self._merge_measure(payload)
            return
        if path == "/api/jobs":
            items = payload.get("items") or []
            created = manager.add_jobs(items)
            self._send_json({"ok": True, "ids": [job.id for job in created]})
            return
        if path == "/api/queue/stop":
            stopped = manager.stop_queue()
            self._send_json({"ok": True, "stopped": stopped})
            return
        if path == "/api/queue/start":
            count = manager.resume_queue()
            self._notify_state()
            self._send_json({"ok": True, "resumed": count})
            return
        if path == "/api/open":
            self._open_path(payload.get("path", ""))
            return
        if path == "/api/queue/clear":
            removed = manager.clear_finished()
            self._send_json({"ok": True, "removed": removed})
            return

        # ---------- 网盘资源库 ----------
        if path == "/api/resources":
            common = dict(
                title=payload.get("title", ""),
                tags=payload.get("tags") or [],
                note=payload.get("note", ""),
                status=payload.get("status", resources_module.STATUS_UPDATING),
                group=payload.get("group", ""))
            texts = payload.get("texts")
            if isinstance(texts, list) and texts:
                items, skipped = self.app.resource_store.add_many(texts, **common)
                self._send_json({
                    "ok": True,
                    "items": [resources_module.public_view(item) for item in items],
                    "created": len(items),
                    "skipped": skipped,
                    # 兼容只读 item 的旧前端
                    "item": (resources_module.public_view(items[0])
                             if items else None),
                })
            else:
                item = self.app.resource_store.add(
                    payload.get("text") or payload.get("url") or "", **common)
                self._send_json({"ok": True,
                                 "item": resources_module.public_view(item)})
            self.app._on_change()
            return
        if path == "/api/resources/update":
            item = self.app.resource_store.update(payload.get("id", ""),
                                                  payload.get("patch") or {})
            self._send_json({"ok": item is not None,
                             "item": resources_module.public_view(item) if item else None})
            self.app._on_change()
            return
        if path == "/api/resources/scan":
            item_id = payload.get("id", "")
            if not item_id:
                self._send_json({"error": "缺少资源 id"}, 400)
                return
            self._send_json({"ok": True, "id": self.app.start_resource_scan(
                item_id, payload.get("passcode", ""))})
            return
        if path == "/api/resources/check-updates":
            self._send_json({"ok": True,
                             "id": self.app.start_update_check(payload.get("ids") or [])})
            self.app._on_change()
            return
        if path == "/api/pansou/search":
            self._send_json(self.app.pansou_search(
                (payload.get("query") or "").strip(),
                payload.get("platform", "quark"),
                refresh=bool(payload.get("refresh"))))
            return
        if path == "/api/pansou/files":
            url = (payload.get("url") or "").strip()
            if not url:
                self._send_json({"error": "缺少链接"}, 400)
                return
            self._send_json({"ok": True, "id": self.app.start_pansou_files(
                url, payload.get("password", ""), refresh=bool(payload.get("refresh")))})
            return
        if path == "/api/pansou/save":
            rows = payload.get("items")
            if isinstance(rows, list) and rows:
                self._send_json(self.app.pansou_save_many(
                    rows, group=payload.get("group", ""),
                    status=payload.get("status", ""),
                    tags=payload.get("tags") or []))
            else:
                url = (payload.get("url") or "").strip()
                if not url:
                    self._send_json({"error": "缺少链接"}, 400)
                    return
                self._send_json(self.app.pansou_save(
                    url, payload.get("title", ""), payload.get("password", ""),
                    payload.get("group", ""), payload.get("status", ""),
                    payload.get("tags") or [], payload.get("note", "")))
            self.app._on_change()
            return
        if path == "/api/resources/check-updates/stop":
            self._send_json({"ok": self.app.stop_update_check()})
            self.app._on_change()
            return
        if path == "/api/resources/ack-update":
            if payload.get("all"):
                count = self.app.resource_store.ack_all_updates()
            else:
                ids = payload.get("ids") or ([payload.get("id")] if payload.get("id") else [])
                count = 0
                for item_id in ids:
                    if self.app.resource_store.ack_update(item_id):
                        count += 1
            self._send_json({"ok": True, "count": count})
            self.app._on_change()
            return
        if path == "/api/resources/import":
            added = self.app.resource_store.import_items(payload.get("items") or [])
            self._send_json({"ok": True, "added": added})
            self.app._on_change()
            return
        if path == "/api/cloud/sync":
            # 与云端资源库双向同步（推本地 + 拉云端，云端做合并）
            # 大库同步可能跑几十秒，放后台线程，界面轮询 GET /api/cloud/sync
            state = self.app.start_cloud_sync()
            self._send_json({"ok": True, "task": state})
            return
        if path == "/api/groups":
            group = self.app.resource_store.create_group(payload.get("name", ""))
            self._send_json({"ok": group is not None, "group": group})
            self.app._on_change()
            return
        if path == "/api/groups/rename":
            group = self.app.resource_store.rename_group(payload.get("id", ""),
                                                         payload.get("name", ""))
            self._send_json({"ok": group is not None, "group": group})
            self.app._on_change()
            return
        if path == "/api/resources/move":
            moved = self.app.resource_store.move_to_group(payload.get("ids") or [],
                                                          payload.get("group", ""))
            self._send_json({"ok": True, "moved": moved})
            self.app._on_change()
            return
        if path == "/api/resources/restore":
            item = self.app.resource_store.restore(payload.get("id", ""))
            self._send_json({"ok": item is not None,
                             "item": resources_module.public_view(item) if item else None})
            self.app._on_change()
            return

        parts = [part for part in path.split("/") if part]
        if len(parts) == 3 and parts[0] == "api" and parts[1] == "jobs":
            job_id = parts[2]
            if path.endswith("/cancel"):
                self._send_json({"ok": manager.cancel_job(job_id)})
                return
            if path.endswith("/retry"):
                self._send_json({"ok": manager.retry_job(job_id)})
                return
        self._send_json({"error": "not found"}, 404)

    # ---------- 视频合并 ----------
    def _clips_from_payload(self, items: List[dict]):
        """把前端传来的片段信息还原成 MergeClip（缺探测信息时现场探测）。"""
        clips = []
        for item in items or []:
            path = item.get("path", "")
            result = None
            if path and item.get("probe"):
                result = self.app.manager._probe_from_dict(path, item["probe"])
            if result is None and path:
                try:
                    result = self.app.manager.probe_file(path)
                except Exception as error:
                    result = None
                    clips.append(merger.MergeClip(path=path, error=str(error)))
                    continue
            if result is None:
                clips.append(merger.MergeClip(path=path, error="无法读取视频信息"))
                continue
            clips.append(merger.MergeClip(
                path=path,
                metadata=result.metadata,
                has_audio=bool(result.metadata.has_audio) and not result.error,
                error=result.error or ""))
        return clips

    def _merge_estimate(self, payload: dict) -> None:
        items = payload.get("items")
        if items is None:
            items = [{"path": path} for path in (payload.get("paths") or [])]
        config_patch = payload.get("config") or {}
        config = dict(self.app.config)
        config.update(config_patch)
        clips = self._clips_from_payload(items)
        result = merger.estimate(clips, config)
        result["ok"] = True
        self._send_json(result)

    def _merge_measure(self, payload: dict) -> None:
        items = payload.get("items")
        if items is None:
            items = [{"path": path} for path in (payload.get("paths") or [])]
        config_patch = payload.get("config") or {}
        config = dict(self.app.config)
        config.update(config_patch)
        clips = [clip for clip in self._clips_from_payload(items) if not clip.error]
        if len(clips) < 2:
            self._send_json({"error": "至少需要两段有效视频"}, 400)
            return
        self._send_json({"ok": True, "id": self.app.start_measure(clips, config)})

    def _merge_jobs(self, payload: dict) -> None:
        paths = [path for path in (payload.get("paths") or []) if path]
        if len(paths) < 2:
            self._send_json({"error": "至少需要选择两段视频才能合并"}, 400)
            return
        created = self.app.manager.add_jobs([{
            "kind": "merge",
            "paths": paths,
            "probes": payload.get("probes") or [],
        }])
        if not created:
            self._send_json({"error": "创建合并任务失败"}, 400)
            return
        self._send_json({"ok": True, "ids": [job.id for job in created]})

    def _open_path(self, path: str) -> None:
        if not path or not os.path.exists(path):
            self._send_json({"ok": False, "error": "文件不存在"})
            return
        try:
            if sys.platform.startswith("win"):
                subprocess.Popen(["explorer", "/select,", os.path.normpath(path)])
            elif sys.platform == "darwin":
                subprocess.Popen(["open", "-R", path])
            else:
                subprocess.Popen(["xdg-open", os.path.dirname(path) or "."])
        except Exception as error:
            self._send_json({"ok": False, "error": str(error)})
            return
        self._send_json({"ok": True})

    def _notify_state(self) -> None:
        self.app._on_change()

    def _update_access(self, payload: dict) -> None:
        """改访问密码 / IP 白名单，立即生效，不用重启。"""
        if "password" in payload:
            password = str(payload.get("password") or "")
            self.app.config["access_password"] = (
                access.password_hash(password) if password else "")
        if "allowed_ips" in payload:
            raw = payload.get("allowed_ips")
            if isinstance(raw, str):
                raw = re.split(r"[,，\s]+", raw)
            self.app.config["allowed_ips"] = [
                str(item).strip() for item in (raw or []) if str(item).strip()]
        config_module.save_config(self.app.config)
        self.app._on_change()
        self._send_json({"ok": True,
                         "access": access_state(self.app, self._client_ip())})

    def _update_config(self, payload: dict) -> None:
        previous_dir = self.app.config.get("ffmpeg_dir", "")
        for key, value in payload.items():
            self.app.config[key] = value
        config_module.save_config(self.app.config)
        if "temp_dir" in payload:
            paths.set_temp_dir(self.app.config.get("temp_dir", ""))
        if self.app.config.get("ffmpeg_dir", "") != previous_dir or not self.app.ff.available:
            self.app.reload_ffmpeg()
        self.app.manager.config = self.app.config
        self.app._on_change()
        self._send_json({"ok": True, "config": self.app.config,
                         "ffmpeg": self.app.ff.info()})


def find_free_port(start: int, host: str = "127.0.0.1", attempts: int = 20) -> int:
    for offset in range(attempts):
        port = start + offset
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                sock.bind((host, port))
            except OSError:
                continue
            return port
    return start


def lan_info(server=None) -> dict:
    """手机要用的访问地址：局域网 IP + 端口。"""
    port = getattr(server, "lan_port", 0) or 0
    host = getattr(server, "lan_host", "127.0.0.1") or "127.0.0.1"
    local_only = host in ("127.0.0.1", "localhost", "::1")
    addresses: List[str] = [] if local_only else lan.lan_addresses()
    allowed = bool(config_module.load_config().get("lan_access", True))
    return {"port": port, "host": host, "local_only": local_only,
            "lan_access": allowed,
            "addresses": addresses,
            "urls": lan.lan_urls(port, addresses) if port else [],
            "local_url": f"http://127.0.0.1:{port}/" if port else "",
            "tailscale": lan.tailscale_info(port)}


# ---------- 访问控制 ----------

# Tailscale 用的网段（100.64.0.0/10），一键填白名单时用它
TAILSCALE_CIDR = "100.64.0.0/10"


def suggested_networks() -> List[str]:
    """白名单里"一键添加"的候选：本机局域网网段 + Tailscale 网段。"""
    result: List[str] = []
    seen = set()
    for ip in lan.lan_addresses():
        parts = ip.split(".")
        if len(parts) != 4:
            continue
        cidr = f"{parts[0]}.{parts[1]}.{parts[2]}.0/24"
        if cidr not in seen:
            seen.add(cidr)
            result.append(cidr)
    result.append(TAILSCALE_CIDR)
    return result


def access_decision(config, ip: str, cookie: str, path: str) -> str:
    """判断一次请求该不该放行：ok / denied / login。

    本机（127.0.0.1）永远 ok —— 自己在电脑上用不该被密码挡住。
    """
    if access.is_loopback(ip):
        return "ok"
    if not access.ip_allowed(ip, config.get("allowed_ips")):
        return "denied"
    if access.has_password(config) and not access.cookie_ok(config, cookie):
        return "ok" if path in ("/login", "/api/login") else "login"
    return "ok"


def access_state(app, client_ip: str = "") -> dict:
    config = app.config
    return {"has_password": access.has_password(config),
            "allowed_ips": list(config.get("allowed_ips") or []),
            "client_ip": access.normalize_ip(client_ip),
            "loopback": access.is_loopback(client_ip),
            "suggested": suggested_networks(),
            "tailscale_cidr": TAILSCALE_CIDR}


LOGIN_PAGE = """<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Vidstb 电脑版 · 请输入访问密码</title>
<style>
  *{box-sizing:border-box}
  body{margin:0;min-height:100vh;display:flex;align-items:center;justify-content:center;
       background:#14161a;color:#e6e8ec;font-family:"Microsoft YaHei",system-ui,sans-serif}
  .box{width:min(360px,92vw);background:#1b1e24;border:1px solid #2a2f38;
       border-radius:14px;padding:26px 24px}
  h1{margin:0 0 6px;font-size:17px;font-weight:600}
  p{margin:0 0 18px;font-size:13px;color:#9aa3b0;line-height:1.6}
  input{width:100%;padding:11px 12px;font-size:15px;border-radius:9px;
        border:1px solid #333945;background:#12151a;color:#e6e8ec;outline:none}
  input:focus{border-color:#4c8dff}
  button{margin-top:12px;width:100%;padding:11px;font-size:15px;border:0;border-radius:9px;
         background:#4c8dff;color:#fff;cursor:pointer}
  button:hover{background:#3d7ae8}
  .err{margin-top:10px;font-size:13px;color:#ff6b6b;min-height:18px}
</style></head><body>
<div class="box">
  <h1>需要访问密码</h1>
  <p>这台电脑开了访问密码，输一次就好，之后这台设备会记住。</p>
  <form id="f"><input id="p" type="password" placeholder="访问密码" autofocus></form>
  <button id="go">进入</button>
  <div class="err" id="err"></div>
</div>
<script>
  var p=document.getElementById('p'),err=document.getElementById('err');
  function submit(){
    fetch('/api/login',{method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify({password:p.value})})
      .then(function(r){return r.json()})
      .then(function(d){
        if(d&&d.ok){location.href='/';}
        else{err.textContent=(d&&d.error)||'密码不对';p.select();}
      })
      .catch(function(){err.textContent='连不上服务，请确认电脑上的程序还在运行';});
  }
  document.getElementById('go').addEventListener('click',submit);
  document.getElementById('f').addEventListener('submit',function(e){e.preventDefault();submit();});
</script></body></html>
"""


def _login_page() -> str:
    return LOGIN_PAGE


def _denied_page(ip: str) -> str:
    return ("<!DOCTYPE html><html lang=\"zh-CN\"><head><meta charset=\"utf-8\">"
            "<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">"
            "<title>不允许访问</title><style>"
            "body{margin:0;min-height:100vh;display:flex;align-items:center;"
            "justify-content:center;background:#14161a;color:#e6e8ec;"
            "font-family:'Microsoft YaHei',system-ui,sans-serif;text-align:center}"
            ".box{max-width:340px;padding:0 20px}h1{font-size:17px}p{color:#9aa3b0;"
            "font-size:13px;line-height:1.7}code{color:#e6e8ec}"
            "</style></head><body><div class=\"box\">"
            "<h1>这台设备不在允许列表里</h1>"
            "<p>你的地址是 <code>__IP__</code>。<br>"
            "请在电脑上打开本工具 → 顶栏「手机访问」→ 访问控制，"
            "把这个地址或它所在的网段加进白名单。</p></div></body></html>"
            ).replace("__IP__", html_escape(ip))


def html_escape(text: str) -> str:
    return (text or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def clean_pack_leftovers(max_age_seconds: int = 3600) -> int:
    """清理单文件 exe 上次被强杀留下的解压目录（runtime/pack/_MEI*）。

    正常退出时打包程序会自己删，但直接关窗口 / 被任务管理器杀掉就会剩下
    十几 MB 的空壳，攒多了占地方。只删一小时前的，避免误伤正在跑的实例。
    """
    if not getattr(sys, "frozen", False):
        return 0
    pack_dir = os.path.join(paths.BASE_DIR, "runtime", "pack")
    if not os.path.isdir(pack_dir):
        return 0
    deadline = time.time() - max_age_seconds
    removed = 0
    for name in os.listdir(pack_dir):
        if not name.startswith("_MEI"):
            continue
        target = os.path.join(pack_dir, name)
        try:
            if os.path.getmtime(target) > deadline:
                continue
            shutil.rmtree(target, ignore_errors=True)
            removed += 1
        except OSError:
            continue
    return removed


def main() -> None:
    parser = argparse.ArgumentParser(description="Vidstb 电脑版")
    parser.add_argument("--port", type=int, default=8777)
    parser.add_argument("--host", default="0.0.0.0",
                        help="监听地址，默认 0.0.0.0（同一 WiFi 下的手机也能访问）")
    parser.add_argument("--local", action="store_true",
                        help="只监听 127.0.0.1，手机访问不到，更安全")
    parser.add_argument("--no-browser", action="store_true")
    args = parser.parse_args()

    clean_pack_leftovers()
    app = Application()
    # 网页里可以关掉“允许手机访问”，关掉后就只监听本机
    allow_lan = not args.local and bool(app.config.get("lan_access", True))
    host = "127.0.0.1" if not allow_lan else args.host
    port = find_free_port(args.port, host=host)
    server = ThreadingHTTPServer((host, port), Handler)
    server.app = app  # type: ignore[attr-defined]
    server.daemon_threads = True
    server.lan_port = port  # type: ignore[attr-defined]
    server.lan_host = host  # type: ignore[attr-defined]

    url = f"http://127.0.0.1:{port}/"
    print("=" * 60)
    print("  Vidstb 电脑版 —— 视频内嵌字幕转硬字幕")
    print("=" * 60)
    if app.ff.available:
        print(f"  ffmpeg : {app.ff.ffmpeg_path}")
        print(f"  版本   : {app.ff.ffmpeg_version}")
    else:
        print(f"  ffmpeg : 未找到（{app.ff.error}）")
        print("           请在网页“设置”里指定目录，或运行 python install_ffmpeg.py")
    print(f"  本机   : {url}")
    if host == "127.0.0.1":
        why = "命令行加了 --local" if args.local else "网页里关掉了“允许手机访问”"
        print(f"  手机   : 未开启（{why}，只监听本机）")
    else:
        lan_urls = lan.lan_urls(port)
        if lan_urls:
            print("  手机   : 和电脑连同一个 WiFi，浏览器打开下面任意一个")
            for item in lan_urls:
                print(f"           {item}")
            print("           （Windows 首次可能弹防火墙，选“允许访问”）")
        else:
            print("  手机   : 没找到局域网地址，请确认电脑已连上 WiFi/网线")
    print(f"  停止   : 在本窗口按 Ctrl+C")
    print("=" * 60)

    if not args.no_browser:
        threading.Timer(0.8, lambda: webbrowser.open(url)).start()

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n正在退出…")
    finally:
        server.shutdown()


if __name__ == "__main__":
    main()
