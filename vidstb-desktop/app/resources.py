"""网盘资源库：保存分享链接，并缓存解析出来的文件清单。

数据放在项目目录 runtime/resources.json（不写系统盘），整个目录拷走即用。
"""

from __future__ import annotations

import json
import os
import re
import shutil
import threading
import time
import uuid
from typing import Dict, List, Optional, Tuple

from . import quark
from .paths import BASE_DIR
STORE_PATH = os.path.join(BASE_DIR, "runtime", "resources.json")
# 删除的资源先进回收站，可恢复；同时每次保存前留一份 .bak
TRASH_PATH = os.path.join(BASE_DIR, "runtime", "resources.trash.json")

PROVIDER_QUARK = "quark"
PROVIDER_BAIDU = "baidu"
PROVIDER_OTHER = "other"

PROVIDER_LABELS = {
    PROVIDER_QUARK: "夸克网盘",
    PROVIDER_BAIDU: "百度网盘",
    PROVIDER_OTHER: "其它链接",
}

# 资源本身的状态：还在连载更新 / 已经完结 / 链接失效
STATUS_UPDATING = "updating"
STATUS_FINISHED = "finished"
STATUS_DEAD = "dead"

STATUS_LABELS = {
    STATUS_UPDATING: "在更",
    STATUS_FINISHED: "已完结",
    STATUS_DEAD: "已失效",
}

# 老版本用的是「想看 / 在看 / 已看」，读旧数据时自动迁到新状态
STATUS_LEGACY_MAP = {
    "watchlist": STATUS_UPDATING,
    "watching": STATUS_UPDATING,
    "done": STATUS_FINISHED,
}


def normalize_status(value: str) -> str:
    """把任意（含旧版）状态值收敛成一个合法状态，默认「在更」。"""
    text = (value or "").strip()
    if text in STATUS_LABELS:
        return text
    return STATUS_LEGACY_MAP.get(text, STATUS_UPDATING)

# 分组筛选的哨兵值：前端传这个表示「未分组」
GROUP_NONE = "__none__"

# 更新提示最多记住多少个新文件（存太多会把 resources.json 撑大）
MAX_NEW_FILES = 50

# 文件名尾部的分辨率 / 编码 / 格式标记，猜标题时要去掉
TITLE_NOISE = re.compile(
    r"^(\d{3,4}[pPiI]|4K|2160p?|1080p?|720p?|WEB-?DL|BluRay|BDRip|[xh]\.?26[45]|"
    r"HEVC|AAC|FLAC|10bit|8bit|HDR|DV|CHS|CHT|GB|BIG5|MP4|MKV|SRT|ASS)$",
    re.IGNORECASE)

# 从标题里猜番剧名，用于自动打标签：斗破苍穹.S01E05 -> 斗破苍穹
TITLE_CLEAN = re.compile(
    r"[.\[\]]*\s*(S\d{1,2}\s*E?\d{0,3}|第\s*\d+\s*[集季话部]|EP?\d{1,3}|"
    r"\d{3,4}[pPiI]|4K|2160p|1080p|720p|WEB-?DL|BluRay|BDRip|x264|x265|H\.?264|"
    r"HEVC|AAC|FLAC|10bit|HDR|DV|CHS|CHT|GB|BIG5|MP4|MKV)\b.*$",
    re.IGNORECASE)


def _now() -> float:
    return time.time()


def file_key(row: dict) -> str:
    """文件身份：以路径为准（同一集的 path 稳定，重命名才算新文件）。"""
    path = (row.get("path") or row.get("name") or "").replace("\\", "/").strip()
    return path.lower()


def file_keys(files) -> List[str]:
    return [file_key(row) for row in (files or []) if isinstance(row, dict)]


def share_key(provider: str, share_id: str) -> str:
    """一条资源的唯一身份：网盘 + 分享 id。

    用来判断「盘搜里这条我是不是已经存过」——同一个分享换链接写法、
    补了提取码都应该算同一条。
    """
    return "%s:%s" % ((provider or "").strip().lower(),
                      (share_id or "").strip().lower())


def item_share_key(item: dict) -> str:
    return share_key(item.get("provider") or "", item.get("share_id") or "")


class ResourceStore:
    def __init__(self, path: str = "", trash_path: str = ""):
        self.path = path or STORE_PATH
        self.trash_path = trash_path or TRASH_PATH
        self.lock = threading.RLock()
        self.items: List[dict] = []
        self.groups: List[dict] = []
        self.trash_items: List[dict] = []
        # 杂项：例如 last_update_check（上次自动检查更新的时间）
        self.meta: dict = {}
        self.restored_from_backup = 0
        self.load()
        self.load_trash()

    # ---------- 持久化 ----------
    @staticmethod
    def _read_all(path: str) -> Optional[dict]:
        """读出 {items, groups}；文件不存在或损坏返回 None。"""
        try:
            with open(path, "r", encoding="utf-8") as handle:
                data = json.load(handle)
        except (OSError, ValueError):
            return None
        if not isinstance(data, dict):
            raw_items = data if isinstance(data, list) else []
            return {"items": raw_items, "groups": []}
        return {
            "items": [item for item in (data.get("items") or []) if isinstance(item, dict)],
            "groups": [row for row in (data.get("groups") or []) if isinstance(row, dict)],
            "meta": data.get("meta") if isinstance(data.get("meta"), dict) else {},
        }

    @classmethod
    def _read(cls, path: str):
        payload = cls._read_all(path)
        return None if payload is None else payload["items"]

    def load(self) -> None:
        payload = self._read_all(self.path)
        if payload is None:
            # 主文件丢了 / 坏了，用备份兜底（.bak 是最新版，.bak2 是上一版）
            for backup in (f"{self.path}.bak", f"{self.path}.bak2"):
                payload = self._read_all(backup)
                if payload and payload["items"]:
                    self.restored_from_backup = len(payload["items"])
                    break
        payload = payload or {"items": [], "groups": [], "meta": {}}
        self.items = payload["items"]
        self.groups = payload["groups"]
        self.meta = payload.get("meta") or {}
        # 旧版本的数据没有 group 字段，补齐避免后面到处判空；
        # 状态也从「想看 / 在看 / 已看」迁到「在更 / 已完结」
        migrated = False
        for item in self.items:
            if not item.get("group"):
                item["group"] = ""
            status = normalize_status(item.get("status"))
            if status != item.get("status"):
                item["status"] = status
                migrated = True
        if self.restored_from_backup or migrated:
            self.save()

    def save(self) -> None:
        directory = os.path.dirname(self.path)
        if directory:
            os.makedirs(directory, exist_ok=True)
        # 现有备份退一级，保存完再把最新版本同步成 .bak
        try:
            if os.path.exists(f"{self.path}.bak"):
                shutil.copy2(f"{self.path}.bak", f"{self.path}.bak2")
        except OSError:
            pass
        payload = {"version": 3, "updated_at": _now(),
                   "items": self.items, "groups": self.groups, "meta": self.meta}
        temp = f"{self.path}.tmp"
        with open(temp, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
        os.replace(temp, self.path)
        try:
            shutil.copy2(self.path, f"{self.path}.bak")
        except OSError:
            pass

    # ---------- 回收站 ----------
    def load_trash(self) -> None:
        self.trash_items = self._read(self.trash_path) or []

    def save_trash(self) -> None:
        directory = os.path.dirname(self.trash_path)
        if directory:
            os.makedirs(directory, exist_ok=True)
        payload = {"version": 1, "updated_at": _now(), "items": self.trash_items}
        temp = f"{self.trash_path}.tmp"
        with open(temp, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
        os.replace(temp, self.trash_path)

    # ---------- 增删改 ----------
    def add(self, text: str, title: str = "", tags: Optional[List[str]] = None,
            note: str = "", status: str = STATUS_UPDATING,
            group: str = "") -> dict:
        parsed = quark.parse_link(text)
        if group and self.find_group(group) is None:
            group = ""
        item = {
            "id": uuid.uuid4().hex[:10],
            "url": parsed["url"],
            "provider": parsed["provider"],
            "share_id": parsed["share_id"],
            "passcode": parsed["passcode"],
            "title": (title or "").strip() or guess_title(parsed),
            "tags": [tag.strip() for tag in (tags or []) if tag.strip()],
            "note": note or "",
            "group": group or "",
            "status": normalize_status(status),
            "created_at": _now(),
            "updated_at": _now(),
            "scan": None,
            "scanned_at": 0,
            "baseline": None,
            "update": None,
        }
        if not item["tags"]:
            guessed = guess_tag(item["title"])
            if guessed:
                item["tags"] = [guessed]
        with self.lock:
            self.items.insert(0, item)
            self.save()
        return item

    def add_many(self, texts: List[str], title: str = "",
                 tags: Optional[List[str]] = None, note: str = "",
                 status: str = STATUS_UPDATING,
                 group: str = "") -> Tuple[List[dict], int]:
        """一次添加多条链接，共用标题 / 分组 / 标签 / 状态。

        每条链接单独解析，各自的提取码不会串到另一条；已经存过的分享
        （同网盘 + 同分享 id）直接跳过，不改动原有条目。
        返回 ``(新建的条目列表, 跳过的条数)``。
        """
        created: List[dict] = []
        skipped = 0
        with self.lock:
            known = {item_share_key(item) for item in self.items}
            known_urls = {(item.get("url") or "").strip() for item in self.items}
            for text in texts or []:
                if not isinstance(text, str) or not text.strip():
                    continue
                parsed = quark.parse_link(text)
                share_id = parsed.get("share_id") or ""
                # 认不出来的普通网址没有 share_id，只能按整段文本去重
                if share_id:
                    duplicate = share_key(parsed.get("provider") or "",
                                          share_id) in known
                else:
                    duplicate = text.strip() in known_urls
                if duplicate:
                    skipped += 1
                    continue
                item = self.add(text, title=title, tags=tags, note=note,
                                status=status, group=group)
                created.append(item)
                known.add(item_share_key(item))
                known_urls.add((item.get("url") or "").strip())
        return created, skipped

    def find_by_share(self, provider: str, share_id: str) -> Optional[dict]:
        """按「网盘 + 分享 id」找已存过的资源，找不到返回 None。"""
        share_id = (share_id or "").strip()
        if not share_id:
            return None
        key = share_key(provider, share_id)
        with self.lock:
            for item in self.items:
                if item_share_key(item) == key:
                    return item
        return None

    def saved_map(self) -> Dict[str, str]:
        """share_key -> 资源 id。给盘搜判断「这条我是不是已经存过」。"""
        result: Dict[str, str] = {}
        with self.lock:
            for item in self.items:
                key = item_share_key(item)
                if key.endswith(":"):        # 没有分享 id 的没法比对
                    continue
                result[key] = item.get("id") or ""
        return result

    def add_or_update(self, text: str, title: str = "",
                      tags: Optional[List[str]] = None, note: str = "",
                      status: str = "", group: str = "") -> Tuple[dict, bool]:
        """存一条资源：同一个分享已经存过就更新，不重复添加。

        返回 (item, created)。盘搜保存走这里，避免同一个链接存出一堆副本。
        """
        parsed = quark.parse_link(text)
        provider = parsed.get("provider") or ""
        with self.lock:
            existing = self.find_by_share(provider, parsed.get("share_id") or "")
            if existing is None:
                item = self.add(text, title=title, tags=tags, note=note,
                                status=status or STATUS_UPDATING, group=group)
                return item, True
            patch: dict = {}
            new_title = (title or "").strip()
            if new_title and new_title != existing.get("title"):
                patch["title"] = new_title
            if (parsed.get("passcode") or "") and not existing.get("passcode"):
                patch["passcode"] = parsed["passcode"]
            if group:
                patch["group"] = group
            if status:
                patch["status"] = status
            if note and not existing.get("note"):
                patch["note"] = note
            if tags:
                merged = list(existing.get("tags") or [])
                for tag in tags:
                    clean = (tag or "").strip()
                    if clean and clean not in merged:
                        merged.append(clean)
                patch["tags"] = merged
            if patch:
                self.update(existing["id"], patch)
                existing = self.find(existing["id"]) or existing
            return existing, False

    def update(self, item_id: str, patch: dict) -> Optional[dict]:
        allowed = ("title", "tags", "note", "status", "passcode", "url", "group")
        with self.lock:
            item = self.find(item_id)
            if item is None:
                return None
            old_title = item.get("title", "")
            old_tags = list(item.get("tags") or [])
            for key in allowed:
                if key in patch:
                    value = patch[key]
                    if key == "tags":
                        value = [tag.strip() for tag in (value or []) if tag.strip()]
                    elif key == "status":
                        value = normalize_status(value)
                    elif key in ("title", "note", "passcode", "url"):
                        value = (value or "").strip()
                    item[key] = value
            # 标题清空时回退成自动标题，避免出现一条没有名字的记录
            if not (item.get("title") or "").strip():
                item["title"] = guess_title(quark.parse_link(item.get("url", "")))
            # 改了标题、而现有标签还是旧标题自动猜出来的，就重新猜一次
            if "title" in patch and _only_auto_tags(old_tags, old_title):
                guessed = guess_tag(item["title"])
                item["tags"] = [guessed] if guessed else []
            item["updated_at"] = _now()
            self.save()
            return item

    def remove(self, item_id: str, purge: bool = False) -> bool:
        """删除一条资源。默认进回收站，purge=True 才彻底删掉。"""
        with self.lock:
            before = len(self.items)
            target = self.find(item_id)
            self.items = [item for item in self.items if item.get("id") != item_id]
            if len(self.items) == before:
                return False
            if not purge and target is not None:
                row = dict(target)
                row["deleted_at"] = _now()
                self.trash_items.insert(0, row)
                self.save_trash()
            self.save()
            return True

    def restore(self, item_id: str) -> Optional[dict]:
        """从回收站恢复。"""
        with self.lock:
            index = next((i for i, row in enumerate(self.trash_items)
                          if row.get("id") == item_id), None)
            if index is None:
                return None
            row = self.trash_items.pop(index)
            row.pop("deleted_at", None)
            row["updated_at"] = _now()
            # 原来所在的分组可能已经被删掉了
            if row.get("group") and self.find_group(row["group"]) is None:
                row["group"] = ""
            self.items.insert(0, row)
            self.save()
            self.save_trash()
            return row

    def purge(self, item_id: str) -> bool:
        """彻底删除回收站里的一条。"""
        with self.lock:
            before = len(self.trash_items)
            self.trash_items = [row for row in self.trash_items
                                if row.get("id") != item_id]
            if len(self.trash_items) == before:
                return False
            self.save_trash()
            return True

    def clear_trash(self) -> int:
        with self.lock:
            removed = len(self.trash_items)
            self.trash_items = []
            if removed:
                self.save_trash()
            return removed

    def trash(self) -> List[dict]:
        with self.lock:
            return list(self.trash_items)

    # ---------- 分组 ----------
    def create_group(self, name: str) -> Optional[dict]:
        """新建一个分组；重名或空名返回 None。"""
        name = (name or "").strip()
        if not name:
            return None
        with self.lock:
            if any(row.get("name", "").lower() == name.lower() for row in self.groups):
                return None
            group = {"id": uuid.uuid4().hex[:8], "name": name[:40],
                     "created_at": _now()}
            self.groups.append(group)
            self.save()
            return group

    def rename_group(self, group_id: str, name: str) -> Optional[dict]:
        name = (name or "").strip()
        if not name:
            return None
        with self.lock:
            target = self.find_group(group_id)
            if target is None:
                return None
            if any(row.get("name", "").lower() == name.lower()
                   and row.get("id") != group_id for row in self.groups):
                return None
            target["name"] = name[:40]
            self.save()
            return target

    def remove_group(self, group_id: str) -> int:
        """删除分组，组里的资源回到「未分组」，返回受影响条数。"""
        with self.lock:
            before = len(self.groups)
            self.groups = [row for row in self.groups if row.get("id") != group_id]
            if len(self.groups) == before:
                return 0
            moved = 0
            for item in self.items:
                if item.get("group") == group_id:
                    item["group"] = ""
                    item["updated_at"] = _now()
                    moved += 1
            self.save()
            return moved

    def find_group(self, group_id: str) -> Optional[dict]:
        for row in self.groups:
            if row.get("id") == group_id:
                return row
        return None

    def groups_with_stats(self) -> List[dict]:
        """分组列表 + 每组资源条数与已读取的总大小。"""
        with self.lock:
            counts: Dict[str, int] = {}
            sizes: Dict[str, int] = {}
            for item in self.items:
                key = item.get("group") or ""
                counts[key] = counts.get(key, 0) + 1
                sizes[key] = sizes.get(key, 0) + int(
                    ((item.get("scan") or {}).get("stats") or {}).get("total_bytes") or 0)
            return [{"id": row.get("id", ""), "name": row.get("name", ""),
                     "count": counts.get(row.get("id", ""), 0),
                     "total_bytes": sizes.get(row.get("id", ""), 0)}
                    for row in self.groups]

    def ungrouped_stats(self) -> dict:
        """「未分组」这一栏的条数与总大小。"""
        with self.lock:
            count = 0
            size = 0
            for item in self.items:
                if item.get("group"):
                    continue
                count += 1
                size += int(((item.get("scan") or {}).get("stats") or {})
                            .get("total_bytes") or 0)
            return {"count": count, "total_bytes": size}

    def move_to_group(self, item_ids: List[str], group_id: str) -> int:
        """批量把资源移进某个分组（group_id 为空表示未分组）。"""
        group_id = group_id or ""
        if group_id and self.find_group(group_id) is None:
            return 0
        with self.lock:
            wanted = set(item_ids or [])
            moved = 0
            for item in self.items:
                if item.get("id") in wanted and item.get("group", "") != group_id:
                    item["group"] = group_id
                    item["updated_at"] = _now()
                    moved += 1
            if moved:
                self.save()
            return moved

    def find(self, item_id: str) -> Optional[dict]:
        for item in self.items:
            if item.get("id") == item_id:
                return item
        return None

    # ---------- 查询 ----------
    def list(self, keyword: str = "", provider: str = "", status: str = "",
             tag: str = "", group: str = "", updates: bool = False) -> List[dict]:
        keyword = (keyword or "").strip().lower()
        with self.lock:
            items = list(self.items)
        result = []
        for item in items:
            if provider and item.get("provider") != provider:
                continue
            if status and item.get("status") != status:
                continue
            if tag and tag not in (item.get("tags") or []):
                continue
            if group and not self._in_group(item, group):
                continue
            if keyword and not self._match(item, keyword):
                continue
            if updates and not has_update(item):
                continue
            result.append(item)
        return result

    def get_meta(self, key: str, default=None):
        with self.lock:
            return self.meta.get(key, default)

    def set_meta(self, key: str, value) -> None:
        with self.lock:
            self.meta[key] = value
            self.save()

    @staticmethod
    def _in_group(item: dict, group: str) -> bool:
        """group 为 GROUP_NONE 标记时筛选「未分组」的资源。"""
        current = item.get("group") or ""
        if group == GROUP_NONE:
            return current == ""
        return current == group

    @staticmethod
    def _match(item: dict, keyword: str) -> bool:
        haystack = " ".join([
            item.get("title", ""),
            item.get("url", ""),
            item.get("note", ""),
            " ".join(item.get("tags") or []),
        ]).lower()
        if keyword in haystack:
            return True
        # 文件清单也算进搜索范围
        scan = item.get("scan") or {}
        for row in (scan.get("files") or [])[:500]:
            if keyword in row.get("path", "").lower():
                return True
        return False

    def tags(self) -> List[str]:
        seen: Dict[str, int] = {}
        with self.lock:
            for item in self.items:
                for tag in item.get("tags") or []:
                    seen[tag] = seen.get(tag, 0) + 1
        return sorted(seen, key=lambda name: (-seen[name], name))

    def stats(self) -> dict:
        counts = {key: 0 for key in STATUS_LABELS}
        by_provider: Dict[str, int] = {}
        updated = 0
        with self.lock:
            for item in self.items:
                status = normalize_status(item.get("status"))
                counts[status] = counts.get(status, 0) + 1
                provider = item.get("provider") or PROVIDER_OTHER
                by_provider[provider] = by_provider.get(provider, 0) + 1
                if has_update(item):
                    updated += 1
        return {"total": len(self.items), "status": counts, "provider": by_provider,
                "updated": updated}

    # ---------- 解析结果 ----------
    def set_scan(self, item_id: str, result: dict, detect: bool = True) -> Optional[dict]:
        """写入解析结果；detect 为真时顺带和基线比对，看有没有新增文件。"""
        with self.lock:
            item = self.find(item_id)
            if item is None:
                return None
            item["scan"] = result
            item["scanned_at"] = _now()
            item["updated_at"] = item["scanned_at"]
            if detect:
                self._diff_update(item)
            self.save()
            return item

    def _diff_update(self, item: dict) -> None:
        """拿当前清单和基线比一比，把新增的文件记进 item["update"]。"""
        scan = item.get("scan") or {}
        if not scan.get("ok"):
            # 读取失败不动画面：基线保持原样，等下次成功再比
            return
        files = [row for row in (scan.get("files") or []) if isinstance(row, dict)]
        if not files:
            return
        keys = file_keys(files)
        baseline = item.get("baseline") or {}
        base_keys = set(baseline.get("keys") or [])
        if not base_keys:
            # 第一次读到清单：先把这一版记成基线，不算「更新」
            item["baseline"] = {"keys": keys, "count": len(keys),
                                "total_bytes": int((scan.get("stats") or {})
                                                   .get("total_bytes") or 0),
                                "saved_at": _now()}
            item["update"] = None
            return
        known = set(base_keys)
        new_files: List[dict] = []
        seen = set()
        for row, key in zip(files, keys):
            if not key or key in known or key in seen:
                continue
            seen.add(key)
            new_files.append({"name": row.get("name") or "",
                              "path": row.get("path") or "",
                              "size": int(row.get("size") or 0)})
        if not new_files:
            item["update"] = None
            return
        previous = item.get("update") or {}
        item["update"] = {
            "new_count": len(new_files),
            "new_files": new_files[:MAX_NEW_FILES],
            "new_bytes": sum(row["size"] for row in new_files),
            "detected_at": previous.get("detected_at") or _now(),
            "checked_at": _now(),
        }

    def ack_update(self, item_id: str) -> bool:
        """「我知道了」：把当前清单设成新基线，清掉更新提示。"""
        with self.lock:
            item = self.find(item_id)
            if item is None:
                return False
            return self._ack(item)

    def _ack(self, item: dict, save: bool = True) -> bool:
        scan = item.get("scan") or {}
        files = [row for row in (scan.get("files") or []) if isinstance(row, dict)]
        if files:
            item["baseline"] = {"keys": file_keys(files), "count": len(files),
                                "total_bytes": int((scan.get("stats") or {})
                                                   .get("total_bytes") or 0),
                                "saved_at": _now()}
        item["update"] = None
        item["updated_at"] = _now()
        if save:
            self.save()
        return True

    def ack_all_updates(self) -> int:
        """把所有「有更新」一次性标记为已读，返回处理条数。"""
        changed = 0
        with self.lock:
            for item in self.items:
                if not (item.get("update") or {}).get("new_count"):
                    continue
                self._ack(item, save=False)
                changed += 1
            if changed:
                self.save()
        return changed

    def clear_scan(self, item_id: str) -> bool:
        with self.lock:
            item = self.find(item_id)
            if item is None:
                return False
            item["scan"] = None
            item["scanned_at"] = 0
            # 清单都没了，基线也跟着作废，下次读到的那一版重新当基线
            item["baseline"] = None
            item["update"] = None
            self.save()
            return True

    # ---------- 导入导出 ----------
    def export(self) -> List[dict]:
        with self.lock:
            return json.loads(json.dumps(self.items, ensure_ascii=False))

    def import_items(self, items: List[dict]) -> int:
        added = 0
        with self.lock:
            for raw in items or []:
                if not isinstance(raw, dict):
                    continue
                url = (raw.get("url") or "").strip()
                if not url:
                    continue
                if any(item.get("url") == url for item in self.items):
                    continue
                item = {
                    "id": uuid.uuid4().hex[:10],
                    "url": url,
                    "provider": raw.get("provider") or quark.parse_link(url)["provider"],
                    "share_id": raw.get("share_id") or quark.parse_link(url)["share_id"],
                    "passcode": raw.get("passcode") or "",
                    "title": raw.get("title") or guess_title(quark.parse_link(url)),
                    "tags": [tag for tag in (raw.get("tags") or []) if tag],
                    "note": raw.get("note") or "",
                    "group": raw.get("group") or "",
                    "status": normalize_status(raw.get("status")),
                    "created_at": _now(),
                    "updated_at": _now(),
                    "scan": None,
                    "scanned_at": 0,
                    "baseline": None,
                    "update": None,
                }
                self.items.insert(0, item)
                added += 1
            if added:
                self.save()
        return added


def guess_title(parsed: dict) -> str:
    """没有手写标题时，用网盘 + 分享 ID 兜底一个。"""
    label = PROVIDER_LABELS.get(parsed.get("provider"), "链接")
    share_id = parsed.get("share_id") or ""
    return f"{label} {share_id[:8]}".strip()


def _only_auto_tags(tags: List[str], title: str) -> bool:
    """标签是不是「程序自动猜的、没有手工价值」，是就允许被新标题覆盖。"""
    tags = [tag for tag in (tags or []) if tag]
    if not tags:
        return True
    if len(tags) == 1 and tags[0] in PROVIDER_LABELS.values():
        return True
    guessed = guess_tag(title)
    return bool(guessed) and tags == [guessed]


def suggest_title(item: dict) -> str:
    """读取过文件清单后，用顶层目录 / 文件名的公共前缀猜一个标题。"""
    scan = item.get("scan") or {}
    files = scan.get("files") or []
    tops: List[str] = []
    for row in files:
        path = (row.get("path") or row.get("name") or "").replace("\\", "/")
        first = path.split("/")[0].strip()
        if first:
            tops.append(first)
    if not tops:
        # 顶层全是目录（百度未登录时只会读到目录），改用顶层目录名
        for node in scan.get("tree") or []:
            name = (node.get("name") or "").strip()
            if name:
                tops.append(name)
    if not tops:
        return (scan.get("title_hint") or "").strip()[:60]
    tops = [os.path.splitext(top)[0] for top in tops]
    if len(tops) == 1:
        tokens = _tokens(tops[0])
    else:
        # 按「词」取公共前缀，避免切在 E01 / E02 这种半截上
        columns = [_tokens(top) for top in tops]
        tokens = []
        for index in range(min(len(row) for row in columns)):
            values = {row[index] for row in columns}
            if len(values) != 1:
                break
            tokens.append(values.pop())
    # 去掉尾部的分辨率 / 编码 / 格式标记，保留季数集数
    while tokens and TITLE_NOISE.match(tokens[-1]):
        tokens.pop()
    candidate = " ".join(tokens).strip()
    return candidate[:60] if len(candidate) >= 2 else ""


def _tokens(name: str) -> List[str]:
    return [part for part in re.split(r"[._\-\s\[\]（）()【】]+", name) if part]


def guess_tag(title: str) -> str:
    """从标题里猜一个剧名当标签。"""
    text = (title or "").strip()
    if not text:
        return ""
    text = os.path.splitext(text)[0]
    parts = re.split(r"[._\-\s]+", TITLE_CLEAN.sub("", text))
    parts = [part for part in parts if part]
    if not parts:
        return ""
    # 取第一段里像剧名的部分（去掉纯数字/英文年份）
    head = parts[0]
    if re.fullmatch(r"\d{1,4}", head):
        return ""
    # 兜底标题是「夸克网盘 xxxxxxxx」，不该拿网盘名当标签
    if head in PROVIDER_LABELS.values():
        return ""
    return head[:24]


def has_update(item: dict) -> bool:
    return int((item.get("update") or {}).get("new_count") or 0) > 0


def public_view(item: dict) -> dict:
    """给前端的最小视图：不把 stoken 这类敏感字段带出去。"""
    scan = item.get("scan") or {}
    update = item.get("update") or {}
    update_view = None
    if int(update.get("new_count") or 0) > 0:
        update_view = {
            "new_count": int(update.get("new_count") or 0),
            "new_bytes": int(update.get("new_bytes") or 0),
            "new_files": update.get("new_files") or [],
            "detected_at": update.get("detected_at", 0),
            "checked_at": update.get("checked_at", 0),
        }
    return {
        "id": item.get("id", ""),
        "url": item.get("url", ""),
        "provider": item.get("provider", PROVIDER_OTHER),
        "provider_label": PROVIDER_LABELS.get(item.get("provider"), "链接"),
        "share_id": item.get("share_id", ""),
        "passcode": item.get("passcode", ""),
        "title": item.get("title", ""),
        "suggested_title": suggest_title(item),
        "tags": item.get("tags") or [],
        "group": item.get("group") or "",
        "note": item.get("note", ""),
        "status": normalize_status(item.get("status")),
        "status_label": STATUS_LABELS.get(normalize_status(item.get("status")), "在更"),
        "created_at": item.get("created_at", 0),
        "updated_at": item.get("updated_at", 0),
        "scanned_at": item.get("scanned_at", 0),
        "update": update_view,
        "scannable": (item.get("provider") in (PROVIDER_QUARK, PROVIDER_BAIDU)
                      and bool(item.get("share_id"))),
        "scan": {
            "ok": bool(scan.get("ok")),
            "error": scan.get("error", ""),
            "files": scan.get("files", []),
            "tree": scan.get("tree", []),
            "stats": scan.get("stats", {}),
            "scanned_at": item.get("scanned_at", 0),
        } if scan else None,
    }
