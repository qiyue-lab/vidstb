"""网盘资源库与夸克链接解析测试。"""

import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import quark, resources  # noqa: E402


class ParseLinkTest(unittest.TestCase):
    def test_quark_plain(self):
        result = quark.parse_link("https://pan.quark.cn/s/abc123XYZ")
        self.assertEqual(result["provider"], "quark")
        self.assertEqual(result["share_id"], "abc123XYZ")
        self.assertEqual(result["url"], "https://pan.quark.cn/s/abc123XYZ")
        self.assertEqual(result["passcode"], "")

    def test_quark_with_chinese_passcode_text(self):
        result = quark.parse_link("链接：https://pan.quark.cn/s/abc123 提取码：ab12 复制本段")
        self.assertEqual(result["provider"], "quark")
        self.assertEqual(result["share_id"], "abc123")
        self.assertEqual(result["passcode"], "ab12")

    def test_quark_with_pwd_query(self):
        result = quark.parse_link("https://pan.quark.cn/s/abc123?pwd=xy9z")
        self.assertEqual(result["passcode"], "xy9z")

    def test_baidu_with_passcode(self):
        result = quark.parse_link("https://pan.baidu.com/s/1AbCdEfGhIjK 提取码: 8x2k")
        self.assertEqual(result["provider"], "baidu")
        self.assertEqual(result["share_id"], "1AbCdEfGhIjK")
        self.assertEqual(result["passcode"], "8x2k")
        self.assertIn("?pwd=8x2k", result["url"])

    def test_baidu_pwd_in_query_keeps_case(self):
        # 用户报的那条：提取码混着大小写，拼在 ?pwd= 后面
        result = quark.parse_link(
            "https://pan.baidu.com/s/1xPi1kr4_10o70nAL7S0Gkw?pwd=6foJ")
        self.assertEqual(result["provider"], "baidu")
        self.assertEqual(result["share_id"], "1xPi1kr4_10o70nAL7S0Gkw")
        self.assertEqual(result["passcode"], "6foJ")
        self.assertTrue(result["url"].endswith("?pwd=6foJ"))

    def test_six_char_passcode_not_truncated(self):
        # 提取码写 6 位时不能被截成前 4 位
        self.assertEqual(
            quark.parse_link("链接：https://pan.quark.cn/s/abc123 提取码：ab12cd")["passcode"],
            "ab12cd")
        self.assertEqual(quark.parse_link("提取码：abcd")["passcode"], "abcd")

    def test_unknown_link_kept(self):
        result = quark.parse_link("https://example.com/share/123")
        self.assertEqual(result["provider"], "other")
        self.assertEqual(result["url"], "https://example.com/share/123")

    def test_empty_input(self):
        self.assertEqual(quark.parse_link("")["provider"], "unknown")
        self.assertEqual(quark.parse_link("   ")["provider"], "unknown")


class ScanTest(unittest.TestCase):
    """用假的 _request 验证递归目录树的组装逻辑。"""

    def setUp(self):
        self.original = quark._request
        self.pages = {}

        def fake_request(url, data=None, headers=None, timeout=20.0):
            if data:
                return {"code": 0, "data": {"stoken": "stub-token"}}
            key = url.split("pdir_fid=")[1].split("&")[0]
            return {"code": 0, "data": {"list": self.pages.get(key, [])}}

        quark._request = fake_request

    def tearDown(self):
        quark._request = self.original

    def test_builds_nested_tree_and_stats(self):
        self.pages = {
            "0": [
                {"fid": "d1", "file_name": "第01季", "dir": True, "size": 0},
                {"fid": "f1", "file_name": "说明.txt", "dir": False, "size": 100},
            ],
            "d1": [
                {"fid": "v1", "file_name": "第01集.mkv", "dir": False, "size": 2_000_000},
                {"fid": "s1", "file_name": "第01集.srt", "dir": False, "size": 50},
            ],
        }
        result = quark.scan("abc", "")
        tree = result["tree"]
        self.assertEqual(len(tree), 2)
        season = tree[0]
        self.assertTrue(season["is_dir"])
        self.assertEqual(len(season["children"]), 2)
        self.assertEqual(season["children"][0]["kind"], "video")
        self.assertEqual(season["children"][1]["kind"], "subtitle")

        stats = result["stats"]
        self.assertEqual(stats["files"], 3)
        self.assertEqual(stats["dirs"], 1)
        self.assertEqual(stats["video_files"], 1)
        self.assertEqual(stats["subtitle_files"], 1)
        self.assertEqual(stats["total_bytes"], 2_000_150)

        flat = quark.flatten(tree)
        paths = [row["path"] for row in flat]
        self.assertIn("第01季/第01集.mkv", paths)
        self.assertIn("说明.txt", paths)

    def test_error_message_is_user_friendly(self):
        def failing(url, data=None, headers=None, timeout=20.0):
            return {"status": 404, "code": 41006, "message": "分享不存在"}

        quark._request = failing
        with self.assertRaises(quark.ScanError) as ctx:
            quark.scan("bad", "")
        self.assertIn("分享不存在", str(ctx.exception))

    def test_depth_limit(self):
        self.pages = {
            "0": [{"fid": "d1", "file_name": "a", "dir": True, "size": 0}],
            "d1": [{"fid": "d2", "file_name": "b", "dir": True, "size": 0}],
            "d2": [{"fid": "v", "file_name": "c.mkv", "dir": False, "size": 10}],
        }
        result = quark.scan("abc", "", max_depth=2)
        # 深度 2：只展开到第 2 层，d2 不再往下走
        self.assertEqual(result["tree"][0]["children"][0]["children"], [])


class StoreTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="vidstb-res-test-")
        self.path = os.path.join(self.dir, "resources.json")
        self.store = resources.ResourceStore(self.path)

    def tearDown(self):
        try:
            os.remove(self.path)
            os.rmdir(self.dir)
        except OSError:
            pass

    def test_add_and_persist(self):
        item = self.store.add("https://pan.quark.cn/s/share1 提取码：ab12",
                              title="斗破苍穹 S01")
        self.assertEqual(item["provider"], "quark")
        self.assertEqual(item["passcode"], "ab12")
        reloaded = resources.ResourceStore(self.path)
        self.assertEqual(len(reloaded.items), 1)
        self.assertEqual(reloaded.items[0]["title"], "斗破苍穹 S01")

    def test_auto_tag_from_title(self):
        item = self.store.add("https://pan.quark.cn/s/share2", title="斗破苍穹.S01.1080p.WEB-DL")
        self.assertEqual(item["tags"], ["斗破苍穹"])

    def test_update_status_and_note(self):
        item = self.store.add("https://pan.quark.cn/s/share3")
        self.store.update(item["id"], {"status": resources.STATUS_FINISHED,
                                       "note": "看到第 5 集"})
        updated = self.store.find(item["id"])
        self.assertEqual(updated["status"], resources.STATUS_FINISHED)
        self.assertEqual(updated["note"], "看到第 5 集")

    def test_remove(self):
        item = self.store.add("https://pan.quark.cn/s/share4")
        self.assertTrue(self.store.remove(item["id"]))
        self.assertFalse(self.store.remove(item["id"]))

    def test_filter_by_provider_status_tag(self):
        self.store.add("https://pan.quark.cn/s/q1", title="夸克资源")
        self.store.add("https://pan.baidu.com/s/b1", title="百度资源")
        self.assertEqual(len(self.store.list(provider="quark")), 1)
        self.assertEqual(len(self.store.list(provider="baidu")), 1)
        self.assertEqual(len(self.store.list(status=resources.STATUS_FINISHED)), 0)

    def test_search_matches_file_names(self):
        item = self.store.add("https://pan.quark.cn/s/q2", title="剧集")
        self.store.set_scan(item["id"], {
            "ok": True, "error": "", "tree": [],
            "files": [{"path": "第01季/斗破苍穹E01.mkv", "name": "", "size": 0, "kind": "video"}],
            "stats": {},
        })
        self.assertEqual(len(self.store.list(keyword="斗破苍穹E01")), 1)
        self.assertEqual(len(self.store.list(keyword="不存在的关键词")), 0)

    def test_import_skips_duplicates(self):
        self.store.add("https://pan.quark.cn/s/dup")
        added = self.store.import_items([
            {"url": "https://pan.quark.cn/s/dup"},
            {"url": "https://pan.quark.cn/s/new1", "title": "新的"},
        ])
        self.assertEqual(added, 1)
        self.assertEqual(len(self.store.items), 2)

    def test_public_view_hides_stoken(self):
        item = self.store.add("https://pan.quark.cn/s/q3")
        self.store.set_scan(item["id"], {
            "ok": True, "stoken": "secret", "tree": [], "files": [], "stats": {}, "error": "",
        })
        view = resources.public_view(item)
        self.assertNotIn("stoken", view)
        self.assertNotIn("secret", str(view))

    # ---- 一次添加多条链接（同一资源的夸克 + 百度） ----
    def test_add_many_shares_metadata_but_keeps_passcodes(self):
        group = self.store.create_group("追更")
        items, skipped = self.store.add_many(
            ["https://pan.quark.cn/s/shareA 提取码：ab12",
             "https://pan.baidu.com/s/1AbCdEf?pwd=8x2k"],
            title="斗破苍穹 年番", tags=["日漫"],
            status=resources.STATUS_FINISHED, group=group["id"])
        self.assertEqual(skipped, 0)
        self.assertEqual(len(items), 2)
        by_provider = {item["provider"]: item for item in items}
        self.assertEqual(set(by_provider), {"quark", "baidu"})
        # 提取码各自独立，不会串到另一条
        self.assertEqual(by_provider["quark"]["passcode"], "ab12")
        self.assertEqual(by_provider["baidu"]["passcode"], "8x2k")
        for item in items:
            self.assertEqual(item["title"], "斗破苍穹 年番")
            self.assertEqual(item["group"], group["id"])
            self.assertEqual(item["status"], resources.STATUS_FINISHED)
            self.assertEqual(item["tags"], ["日漫"])
        self.assertEqual(len(self.store.items), 2)

    def test_add_many_skips_existing_without_touching_it(self):
        existing = self.store.add("https://pan.quark.cn/s/dupA", title="原来的标题")
        items, skipped = self.store.add_many(
            ["https://pan.quark.cn/s/dupA",
             "https://pan.baidu.com/s/1NewOne?pwd=1234"],
            title="批量标题")
        self.assertEqual(skipped, 1)
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["provider"], "baidu")
        # 已存在的那条不能被批量添加改掉标题
        self.assertEqual(self.store.find(existing["id"])["title"], "原来的标题")
        self.assertEqual(len(self.store.items), 2)

    def test_add_many_dedupes_within_batch(self):
        items, skipped = self.store.add_many(
            ["https://pan.quark.cn/s/same",
             "https://pan.quark.cn/s/same",
             "https://pan.quark.cn/s/same?pwd=zzzz"])
        self.assertEqual(len(items), 1)
        self.assertEqual(skipped, 2)

    def test_add_many_ignores_blank_lines(self):
        items, skipped = self.store.add_many(
            ["", "   ", "https://pan.quark.cn/s/onlyone"])
        self.assertEqual(len(items), 1)
        self.assertEqual(skipped, 0)

    def test_add_many_empty_input(self):
        self.assertEqual(self.store.add_many([]), ([], 0))
        self.assertEqual(self.store.add_many(None), ([], 0))


class GuessTagTest(unittest.TestCase):
    def test_strips_quality_tokens(self):
        self.assertEqual(resources.guess_tag("斗破苍穹.S01E05.1080p.WEB-DL.x264"), "斗破苍穹")

    def test_chinese_title(self):
        self.assertEqual(resources.guess_tag("转生成哥布林有问题吗 第01集"), "转生成哥布林有问题吗")

    def test_pure_number_returns_empty(self):
        self.assertEqual(resources.guess_tag("2024"), "")


class EditTitleTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="vidstb-res-test-")
        self.store = resources.ResourceStore(os.path.join(self.tmp, "resources.json"))

    def tearDown(self):
        for name in os.listdir(self.tmp):
            try:
                os.remove(os.path.join(self.tmp, name))
            except OSError:
                pass
        os.rmdir(self.tmp)

    def test_add_then_rename(self):
        item = self.store.add("https://pan.quark.cn/s/abc123XYZ")
        self.assertTrue(item["title"].startswith("夸克网盘"))
        updated = self.store.update(item["id"], {"title": "斗破苍穹 第1季"})
        self.assertEqual(updated["title"], "斗破苍穹 第1季")
        self.assertEqual(self.store.find(item["id"])["title"], "斗破苍穹 第1季")

    def test_cleared_title_falls_back(self):
        item = self.store.add("https://pan.quark.cn/s/abc123XYZ", title="自定义")
        self.store.update(item["id"], {"title": "   "})
        self.assertTrue(self.store.find(item["id"])["title"].strip())

    def test_rename_adds_tag_when_empty(self):
        item = self.store.add("https://pan.quark.cn/s/abc123XYZ")
        self.store.update(item["id"], {"tags": []})
        self.store.update(item["id"], {"title": "斗破苍穹.S01.1080p"})
        self.assertIn("斗破苍穹", self.store.find(item["id"])["tags"])

    def test_existing_tags_untouched(self):
        item = self.store.add("https://pan.quark.cn/s/abc123XYZ", tags=["国漫"])
        self.store.update(item["id"], {"title": "斗破苍穹.S01.1080p"})
        self.assertEqual(self.store.find(item["id"])["tags"], ["国漫"])

    def test_strip_whitespace(self):
        item = self.store.add("https://pan.quark.cn/s/abc123XYZ")
        self.store.update(item["id"], {"title": "  凡人修仙传  ", "note": " 备注 "})
        found = self.store.find(item["id"])
        self.assertEqual(found["title"], "凡人修仙传")
        self.assertEqual(found["note"], "备注")

    def test_fallback_title_has_no_provider_tag(self):
        item = self.store.add("https://pan.quark.cn/s/abc123XYZ")
        self.assertEqual(item["tags"], [])
        self.assertEqual(resources.guess_tag(item["title"]), "")

    def test_rename_replaces_auto_tag(self):
        item = self.store.add("https://pan.quark.cn/s/abc123XYZ", title="斗破苍穹.S01")
        self.assertEqual(item["tags"], ["斗破苍穹"])
        self.store.update(item["id"], {"title": "凡人修仙传.S01"})
        self.assertEqual(self.store.find(item["id"])["tags"], ["凡人修仙传"])

    def test_rename_keeps_manual_multi_tags(self):
        item = self.store.add("https://pan.quark.cn/s/abc123XYZ", title="斗破苍穹.S01")
        self.store.update(item["id"], {"tags": ["国漫", "腾讯"]})
        self.store.update(item["id"], {"title": "凡人修仙传.S01"})
        self.assertEqual(self.store.find(item["id"])["tags"], ["国漫", "腾讯"])

    def test_provider_only_tag_is_overridable(self):
        item = self.store.add("https://pan.quark.cn/s/abc123XYZ")
        self.store.update(item["id"], {"tags": ["夸克网盘"]})
        self.store.update(item["id"], {"title": "斗破苍穹.S01.1080p"})
        self.assertEqual(self.store.find(item["id"])["tags"], ["斗破苍穹"])

    def test_update_missing_returns_none(self):
        self.assertIsNone(self.store.update("nope", {"title": "x"}))


class TrashTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="vidstb-trash-test-")
        self.main = os.path.join(self.tmp, "resources.json")
        self.trash = os.path.join(self.tmp, "resources.trash.json")
        self.store = resources.ResourceStore(self.main, self.trash)

    def tearDown(self):
        for name in os.listdir(self.tmp):
            try:
                os.remove(os.path.join(self.tmp, name))
            except OSError:
                pass
        os.rmdir(self.tmp)

    def test_delete_goes_to_trash(self):
        item = self.store.add("https://pan.quark.cn/s/abc123")
        self.assertTrue(self.store.remove(item["id"]))
        self.assertEqual(self.store.items, [])
        self.assertEqual(len(self.store.trash()), 1)
        self.assertEqual(self.store.trash()[0]["title"], item["title"])
        self.assertTrue(os.path.exists(self.trash))

    def test_restore(self):
        item = self.store.add("https://pan.quark.cn/s/abc123", title="斗破苍穹")
        self.store.remove(item["id"])
        restored = self.store.restore(item["id"])
        self.assertEqual(restored["title"], "斗破苍穹")
        self.assertEqual(len(self.store.items), 1)
        self.assertEqual(self.store.trash(), [])

    def test_purge(self):
        item = self.store.add("https://pan.quark.cn/s/abc123")
        self.store.remove(item["id"])
        self.assertTrue(self.store.purge(item["id"]))
        self.assertEqual(self.store.trash(), [])
        self.assertFalse(self.store.restore(item["id"]))

    def test_clear_trash(self):
        for index in range(3):
            self.store.remove(self.store.add(f"https://pan.quark.cn/s/abc{index}")["id"])
        self.assertEqual(self.store.clear_trash(), 3)
        self.assertEqual(self.store.trash(), [])

    def test_trash_survives_reload(self):
        item = self.store.add("https://pan.quark.cn/s/abc123", title="凡人修仙传")
        self.store.remove(item["id"])
        fresh = resources.ResourceStore(self.main, self.trash)
        self.assertEqual(len(fresh.trash()), 1)
        self.assertEqual(fresh.trash()[0]["title"], "凡人修仙传")

    def test_missing_main_file_restored_from_backup(self):
        self.store.add("https://pan.quark.cn/s/abc123", title="斗破苍穹")
        self.store.add("https://pan.quark.cn/s/def456", title="吞噬星空")
        os.remove(self.main)          # 模拟数据文件被误删
        fresh = resources.ResourceStore(self.main, self.trash)
        self.assertEqual(len(fresh.items), 2)
        self.assertEqual(fresh.restored_from_backup, 2)
        self.assertTrue(os.path.exists(self.main))

    def test_corrupted_main_file_uses_backup(self):
        self.store.add("https://pan.quark.cn/s/abc123", title="斗破苍穹")
        with open(self.main, "w", encoding="utf-8") as handle:
            handle.write("{ 这不是合法 json")
        fresh = resources.ResourceStore(self.main, self.trash)
        titles = [item.get("title") for item in fresh.items]
        self.assertIn("斗破苍穹", titles)

    def test_empty_store_not_confused_with_backup(self):
        """正常清空（列表为空）不该被当成丢失数据而从备份里捞回来。"""
        item = self.store.add("https://pan.quark.cn/s/abc123", title="临时")
        self.store.remove(item["id"], purge=True)
        fresh = resources.ResourceStore(self.main, self.trash)
        self.assertEqual(fresh.items, [])
        self.assertEqual(fresh.restored_from_backup, 0)


class GroupTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="vidstb-group-test-")
        self.store = resources.ResourceStore(
            os.path.join(self.tmp, "resources.json"),
            os.path.join(self.tmp, "resources.trash.json"))

    def tearDown(self):
        for name in os.listdir(self.tmp):
            try:
                os.remove(os.path.join(self.tmp, name))
            except OSError:
                pass
        os.rmdir(self.tmp)

    def test_create_and_list(self):
        group = self.store.create_group("国漫")
        self.assertIsNotNone(group)
        self.assertEqual(group["name"], "国漫")
        self.assertEqual(len(self.store.groups), 1)

    def test_duplicate_name_rejected(self):
        self.store.create_group("国漫")
        self.assertIsNone(self.store.create_group("国漫"))
        self.assertIsNone(self.store.create_group(" 国漫 "))
        self.assertIsNone(self.store.create_group(""))
        self.assertEqual(len(self.store.groups), 1)

    def test_rename(self):
        group = self.store.create_group("国漫")
        self.assertIsNotNone(self.store.rename_group(group["id"], "国产动画"))
        self.assertEqual(self.store.groups[0]["name"], "国产动画")

    def test_rename_to_existing_rejected(self):
        first = self.store.create_group("国漫")
        second = self.store.create_group("日番")
        self.assertIsNone(self.store.rename_group(second["id"], "国漫"))
        self.assertEqual(self.store.find_group(first["id"])["name"], "国漫")

    def test_remove_group_keeps_items(self):
        group = self.store.create_group("国漫")
        item = self.store.add("https://pan.quark.cn/s/abc123",
                              title="斗破苍穹", group=group["id"])
        moved = self.store.remove_group(group["id"])
        self.assertEqual(moved, 1)
        self.assertEqual(self.store.groups, [])
        self.assertEqual(self.store.find(item["id"])["group"], "")

    def test_remove_missing_group(self):
        self.assertEqual(self.store.remove_group("nope"), 0)

    def test_move_single_and_batch(self):
        group = self.store.create_group("国漫")
        first = self.store.add("https://pan.quark.cn/s/aaa", title="甲")
        second = self.store.add("https://pan.quark.cn/s/bbb", title="乙")
        self.assertEqual(self.store.move_to_group([first["id"]], group["id"]), 1)
        self.assertEqual(self.store.find(first["id"])["group"], group["id"])
        self.assertEqual(self.store.find(second["id"])["group"], "")
        self.assertEqual(self.store.move_to_group(
            [first["id"], second["id"]], group["id"]), 1)   # 已在组里的不算
        self.assertEqual(self.store.find(second["id"])["group"], group["id"])

    def test_move_back_to_ungrouped(self):
        group = self.store.create_group("国漫")
        item = self.store.add("https://pan.quark.cn/s/aaa", group=group["id"])
        self.assertEqual(self.store.move_to_group([item["id"]], ""), 1)
        self.assertEqual(self.store.find(item["id"])["group"], "")

    def test_move_to_unknown_group_rejected(self):
        item = self.store.add("https://pan.quark.cn/s/aaa")
        self.assertEqual(self.store.move_to_group([item["id"]], "nope"), 0)
        self.assertEqual(self.store.find(item["id"])["group"], "")

    def test_add_with_unknown_group_falls_back(self):
        item = self.store.add("https://pan.quark.cn/s/aaa", group="nope")
        self.assertEqual(item["group"], "")

    def test_filter_by_group(self):
        group = self.store.create_group("国漫")
        in_group = self.store.add("https://pan.quark.cn/s/aaa", title="甲",
                                  group=group["id"])
        self.store.add("https://pan.quark.cn/s/bbb", title="乙")
        ids = [item["id"] for item in self.store.list(group=group["id"])]
        self.assertEqual(ids, [in_group["id"]])
        ungrouped = [item["title"] for item in
                     self.store.list(group=resources.GROUP_NONE)]
        self.assertEqual(ungrouped, ["乙"])

    def test_group_stats(self):
        group = self.store.create_group("国漫")
        item = self.store.add("https://pan.quark.cn/s/aaa", group=group["id"])
        self.store.set_scan(item["id"], {"ok": True, "error": "", "tree": [],
                                         "files": [], "stats": {"total_bytes": 2048}})
        self.store.add("https://pan.quark.cn/s/bbb", group=group["id"])
        stats = self.store.groups_with_stats()
        self.assertEqual(stats[0]["count"], 2)
        self.assertEqual(stats[0]["total_bytes"], 2048)
        self.assertEqual(self.store.ungrouped_stats()["count"], 0)

    def test_groups_persist(self):
        self.store.create_group("国漫")
        self.store.add("https://pan.quark.cn/s/aaa", group=self.store.groups[0]["id"])
        fresh = resources.ResourceStore(
            os.path.join(self.tmp, "resources.json"),
            os.path.join(self.tmp, "resources.trash.json"))
        self.assertEqual(len(fresh.groups), 1)
        self.assertEqual(fresh.items[0]["group"], fresh.groups[0]["id"])

    def test_old_file_without_groups(self):
        """旧版数据文件没有 groups 字段也要能读。"""
        path = os.path.join(self.tmp, "resources.json")
        with open(path, "w", encoding="utf-8") as handle:
            json.dump({"version": 1, "items": [{"id": "a", "title": "旧数据"}]},
                      handle)
        fresh = resources.ResourceStore(path, os.path.join(self.tmp, "trash.json"))
        self.assertEqual(fresh.groups, [])
        self.assertEqual(len(fresh.items), 1)
        self.assertEqual(fresh.items[0].get("group"), "")

    def test_restore_after_group_deleted(self):
        group = self.store.create_group("国漫")
        item = self.store.add("https://pan.quark.cn/s/aaa", group=group["id"])
        self.store.remove(item["id"])
        self.store.remove_group(group["id"])
        restored = self.store.restore(item["id"])
        self.assertEqual(restored["group"], "")


class SuggestTitleTest(unittest.TestCase):
    @staticmethod
    def _item(paths):
        return {"scan": {"files": [{"path": p} for p in paths]}}

    def test_single_root_dir(self):
        item = self._item(["斗破苍穹 第1季/01.mkv", "斗破苍穹 第1季/02.mkv"])
        self.assertEqual(resources.suggest_title(item), "斗破苍穹 第1季")

    def test_common_prefix_of_files(self):
        item = self._item(["凡人修仙传.E01.1080p.mkv", "凡人修仙传.E02.1080p.mkv"])
        self.assertEqual(resources.suggest_title(item), "凡人修仙传")

    def test_no_files(self):
        self.assertEqual(resources.suggest_title({"scan": None}), "")
        self.assertEqual(resources.suggest_title({}), "")

    def test_public_view_exposes_suggestion(self):
        item = self._item(["斗破苍穹/01.mkv"])
        item.update({"id": "x", "url": "https://pan.quark.cn/s/a", "provider": "quark",
                     "share_id": "a", "title": "夸克网盘 a", "tags": [], "note": "",
                     "status": "watchlist"})
        view = resources.public_view(item)
        self.assertEqual(view["suggested_title"], "斗破苍穹")


def _scan_result(files, ok=True):
    return {
        "ok": ok,
        "error": "" if ok else "读取失败",
        "tree": [],
        "files": list(files),
        "stats": {"files": len(files),
                  "total_bytes": sum(row.get("size", 0) for row in files)},
    }


def _file(path, size=100):
    return {"path": path, "name": path.split("/")[-1], "size": size, "kind": "video"}


class UpdateTest(unittest.TestCase):
    """「资源更新」提示：记住基线，比出新文件，标记已读。"""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="vidstb-update-test-")
        self.store = resources.ResourceStore(
            os.path.join(self.tmp, "resources.json"),
            os.path.join(self.tmp, "resources.trash.json"))

    def tearDown(self):
        for name in os.listdir(self.tmp):
            try:
                os.remove(os.path.join(self.tmp, name))
            except OSError:
                pass
        os.rmdir(self.tmp)

    def _item(self):
        return self.store.add("https://pan.quark.cn/s/share1", title="斗破苍穹")

    def test_first_scan_only_builds_baseline(self):
        item = self._item()
        self.store.set_scan(item["id"], _scan_result([_file("S01/E01.mkv")]))
        fresh = self.store.find(item["id"])
        self.assertIsNone(fresh["update"])
        self.assertEqual(fresh["baseline"]["count"], 1)
        self.assertEqual(resources.has_update(fresh), False)

    def test_new_file_becomes_update(self):
        item = self._item()
        self.store.set_scan(item["id"], _scan_result([_file("S01/E01.mkv")]))
        self.store.set_scan(item["id"], _scan_result(
            [_file("S01/E01.mkv"), _file("S01/E02.mkv", 200)]))
        fresh = self.store.find(item["id"])
        self.assertEqual(fresh["update"]["new_count"], 1)
        self.assertEqual(fresh["update"]["new_files"][0]["name"], "E02.mkv")
        self.assertEqual(fresh["update"]["new_bytes"], 200)
        self.assertTrue(resources.has_update(fresh))
        # 基线不动，提示一直留着
        self.assertEqual(fresh["baseline"]["count"], 1)

    def test_nothing_new_clears_update(self):
        item = self._item()
        self.store.set_scan(item["id"], _scan_result([_file("S01/E01.mkv")]))
        self.store.set_scan(item["id"], _scan_result(
            [_file("S01/E01.mkv"), _file("S01/E02.mkv")]))
        self.store.set_scan(item["id"], _scan_result([_file("S01/E01.mkv")]))
        self.assertIsNone(self.store.find(item["id"])["update"])

    def test_removed_file_is_not_update(self):
        item = self._item()
        self.store.set_scan(item["id"], _scan_result(
            [_file("S01/E01.mkv"), _file("S01/E02.mkv")]))
        self.store.set_scan(item["id"], _scan_result([_file("S01/E01.mkv")]))
        self.assertIsNone(self.store.find(item["id"])["update"])

    def test_failed_scan_keeps_baseline_and_update(self):
        item = self._item()
        self.store.set_scan(item["id"], _scan_result([_file("S01/E01.mkv")]))
        self.store.set_scan(item["id"], _scan_result([], ok=False))
        fresh = self.store.find(item["id"])
        self.assertEqual(fresh["baseline"]["count"], 1)
        self.assertIsNone(fresh["update"])

    def test_ack_clears_update_and_moves_baseline(self):
        item = self._item()
        self.store.set_scan(item["id"], _scan_result([_file("S01/E01.mkv")]))
        self.store.set_scan(item["id"], _scan_result(
            [_file("S01/E01.mkv"), _file("S01/E02.mkv")]))
        self.assertTrue(self.store.ack_update(item["id"]))
        fresh = self.store.find(item["id"])
        self.assertIsNone(fresh["update"])
        self.assertEqual(fresh["baseline"]["count"], 2)
        # 已读之后再读同样的内容，不该再提示
        self.store.set_scan(item["id"], _scan_result(
            [_file("S01/E01.mkv"), _file("S01/E02.mkv")]))
        self.assertIsNone(self.store.find(item["id"])["update"])

    def test_ack_unknown_id(self):
        self.assertFalse(self.store.ack_update("nope"))

    def test_ack_all_updates(self):
        first = self._item()
        second = self.store.add("https://pan.quark.cn/s/share2", title="凡人修仙传")
        for item in (first, second):
            self.store.set_scan(item["id"], _scan_result([_file("E01.mkv")]))
            self.store.set_scan(item["id"], _scan_result([_file("E01.mkv"), _file("E02.mkv")]))
        self.assertEqual(self.store.stats()["updated"], 2)
        self.assertEqual(self.store.ack_all_updates(), 2)
        self.assertEqual(self.store.stats()["updated"], 0)
        self.assertEqual(self.store.ack_all_updates(), 0)

    def test_list_updates_filter(self):
        first = self._item()
        self.store.add("https://pan.quark.cn/s/share2", title="没更新的")
        self.store.set_scan(first["id"], _scan_result([_file("E01.mkv")]))
        self.store.set_scan(first["id"], _scan_result([_file("E01.mkv"), _file("E02.mkv")]))
        self.assertEqual(len(self.store.list()), 2)
        picked = self.store.list(updates=True)
        self.assertEqual(len(picked), 1)
        self.assertEqual(picked[0]["title"], "斗破苍穹")

    def test_public_view_exposes_update(self):
        item = self._item()
        self.store.set_scan(item["id"], _scan_result([_file("E01.mkv")]))
        self.assertIsNone(resources.public_view(self.store.find(item["id"]))["update"])
        self.store.set_scan(item["id"], _scan_result([_file("E01.mkv"), _file("E02.mkv", 5)]))
        view = resources.public_view(self.store.find(item["id"]))
        self.assertEqual(view["update"]["new_count"], 1)
        self.assertEqual(view["update"]["new_files"][0]["path"], "E02.mkv")
        self.assertEqual(view["update"]["new_bytes"], 5)

    def test_update_survives_reload(self):
        item = self._item()
        self.store.set_scan(item["id"], _scan_result([_file("E01.mkv")]))
        self.store.set_scan(item["id"], _scan_result([_file("E01.mkv"), _file("E02.mkv")]))
        reloaded = resources.ResourceStore(self.store.path)
        self.assertEqual(reloaded.find(item["id"])["update"]["new_count"], 1)

    def test_clear_scan_resets_baseline(self):
        item = self._item()
        self.store.set_scan(item["id"], _scan_result([_file("E01.mkv")]))
        self.store.clear_scan(item["id"])
        fresh = self.store.find(item["id"])
        self.assertIsNone(fresh["baseline"])
        self.assertIsNone(fresh["update"])
        # 清掉之后重新读，重新当基线，不算更新
        self.store.set_scan(item["id"], _scan_result([_file("E09.mkv")]))
        self.assertIsNone(self.store.find(item["id"])["update"])

    def test_file_key_ignores_case_and_slashes(self):
        self.assertEqual(resources.file_key({"path": "S01/E01.MKV"}),
                         resources.file_key({"path": "s01\\e01.mkv"}))

    def test_meta_roundtrip(self):
        self.store.set_meta("last_update_check", 1234.5)
        reloaded = resources.ResourceStore(self.store.path)
        self.assertEqual(reloaded.get_meta("last_update_check"), 1234.5)
        self.assertIsNone(reloaded.get_meta("missing"))


class StatusTest(unittest.TestCase):
    """状态改成「在更 / 已完结 / 已失效」后的行为与旧数据迁移。"""

    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="vidstb-status-test-")
        self.path = os.path.join(self.dir, "resources.json")
        self.store = resources.ResourceStore(self.path)

    def tearDown(self):
        try:
            os.remove(self.path)
        except OSError:
            pass
        try:
            os.rmdir(self.dir)
        except OSError:
            pass

    def test_labels(self):
        self.assertEqual(resources.STATUS_LABELS[resources.STATUS_UPDATING], "在更")
        self.assertEqual(resources.STATUS_LABELS[resources.STATUS_FINISHED], "已完结")
        self.assertEqual(resources.STATUS_LABELS[resources.STATUS_DEAD], "已失效")

    def test_normalize_status(self):
        self.assertEqual(resources.normalize_status("finished"), "finished")
        self.assertEqual(resources.normalize_status("dead"), "dead")
        # 旧状态迁移
        self.assertEqual(resources.normalize_status("watchlist"), "updating")
        self.assertEqual(resources.normalize_status("watching"), "updating")
        self.assertEqual(resources.normalize_status("done"), "finished")
        # 空值 / 乱值回落到「在更」
        self.assertEqual(resources.normalize_status(""), "updating")
        self.assertEqual(resources.normalize_status("乱写"), "updating")

    def test_default_is_updating(self):
        item = self.store.add("https://pan.quark.cn/s/s1")
        self.assertEqual(item["status"], "updating")

    def test_update_normalizes_status(self):
        item = self.store.add("https://pan.quark.cn/s/s2")
        self.store.update(item["id"], {"status": "done"})   # 旧值
        self.assertEqual(self.store.find(item["id"])["status"], "finished")

    def test_legacy_items_migrated_on_load(self):
        payload = {"version": 2, "items": [
            {"id": "a", "url": "https://pan.quark.cn/s/a", "provider": "quark",
             "share_id": "a", "title": "旧·想看", "tags": [], "note": "",
             "group": "", "status": "watchlist"},
            {"id": "b", "url": "https://pan.quark.cn/s/b", "provider": "quark",
             "share_id": "b", "title": "旧·已看", "tags": [], "note": "",
             "group": "", "status": "done"},
        ], "groups": []}
        with open(self.path, "w", encoding="utf-8") as handle:
            json.dump(payload, handle)
        reloaded = resources.ResourceStore(self.path)
        self.assertEqual(reloaded.find("a")["status"], "updating")
        self.assertEqual(reloaded.find("b")["status"], "finished")
        # 迁移结果要落盘，别每次启动都重写一遍
        again = resources.ResourceStore(self.path)
        self.assertEqual(again.find("a")["status"], "updating")

    def test_stats_uses_new_status(self):
        self.store.add("https://pan.quark.cn/s/s3")
        self.store.add("https://pan.quark.cn/s/s4")
        self.store.update(self.store.items[0]["id"], {"status": "finished"})
        stats = self.store.stats()["status"]
        self.assertEqual(stats["updating"], 1)
        self.assertEqual(stats["finished"], 1)


class TagEditTest(unittest.TestCase):
    """添加之后还能改标签。"""

    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="vidstb-tag-test-")
        self.path = os.path.join(self.dir, "resources.json")
        self.store = resources.ResourceStore(self.path)

    def tearDown(self):
        try:
            os.remove(self.path)
        except OSError:
            pass
        try:
            os.rmdir(self.dir)
        except OSError:
            pass

    def test_add_tags_after_creation(self):
        item = self.store.add("https://pan.quark.cn/s/t1", title="斗破苍穹 S01")
        self.assertEqual(item["tags"], ["斗破苍穹"])   # 添加时自动猜的
        self.store.update(item["id"], {"tags": ["国漫", "4K"]})
        self.assertEqual(self.store.find(item["id"])["tags"], ["国漫", "4K"])

    def test_tags_trimmed_and_deduped_input(self):
        item = self.store.add("https://pan.quark.cn/s/t2", title="测试")
        self.store.update(item["id"], {"tags": [" 国漫 ", "", "  ", "4K"]})
        self.assertEqual(self.store.find(item["id"])["tags"], ["国漫", "4K"])

    def test_clear_tags(self):
        item = self.store.add("https://pan.quark.cn/s/t3", title="斗破苍穹 S01")
        self.assertEqual(item["tags"], ["斗破苍穹"])
        self.store.update(item["id"], {"tags": []})
        self.assertEqual(self.store.find(item["id"])["tags"], [])

    def test_manual_tags_survive_title_change(self):
        """手动改过的标签，改标题时不能被自动标签覆盖掉。"""
        item = self.store.add("https://pan.quark.cn/s/t4", title="斗破苍穹 S01")
        self.store.update(item["id"], {"tags": ["国漫", "追更"]})
        self.store.update(item["id"], {"title": "斗破苍穹 年番 1080p"})
        self.assertEqual(self.store.find(item["id"])["tags"], ["国漫", "追更"])

    def test_new_tag_shows_in_tag_list(self):
        item = self.store.add("https://pan.quark.cn/s/t5", title="测试")
        self.store.update(item["id"], {"tags": ["自定义标签"]})
        self.assertIn("自定义标签", self.store.tags())


class ShareKeyTest(unittest.TestCase):
    """盘搜保存去重依赖的「网盘 + 分享 id」身份。"""

    def test_share_key_ignores_case_and_space(self):
        self.assertEqual(resources.share_key("Quark", " ABC "), "quark:abc")

    def test_item_share_key(self):
        item = {"provider": "baidu", "share_id": "1AbC"}
        self.assertEqual(resources.item_share_key(item), "baidu:1abc")


class SaveFromSearchTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="vidstb-save-test-")
        self.path = os.path.join(self.dir, "resources.json")
        self.store = resources.ResourceStore(self.path)

    def tearDown(self):
        try:
            os.remove(self.path)
            os.rmdir(self.dir)
        except OSError:
            pass

    def test_same_share_not_duplicated(self):
        first, created1 = self.store.add_or_update(
            "https://pan.quark.cn/s/share1 ab12", title="斗破苍穹 S01")
        self.assertTrue(created1)
        # 同链接换个写法再存一次（这次还带了提取码）
        second, created2 = self.store.add_or_update(
            "https://pan.quark.cn/s/share1?pwd=ab12", title="斗破苍穹 S01")
        self.assertFalse(created2)
        self.assertEqual(len(self.store.items), 1)
        self.assertEqual(second["id"], first["id"])
        self.assertEqual(second["passcode"], "ab12")

    def test_add_or_update_merges_tags_and_group(self):
        group = self.store.create_group("日更")
        item, _ = self.store.add_or_update("https://pan.quark.cn/s/share2",
                                           title="凡人修仙传", tags=["国漫"])
        again, _ = self.store.add_or_update("https://pan.quark.cn/s/share2",
                                            tags=["追更"], group=group["id"],
                                            status="finished")
        self.assertEqual(len(self.store.items), 1)
        self.assertEqual(again["id"], item["id"])
        self.assertEqual(again["group"], group["id"])
        self.assertEqual(again["status"], "finished")
        self.assertIn("国漫", again["tags"])
        self.assertIn("追更", again["tags"])

    def test_different_share_creates_new_item(self):
        self.store.add_or_update("https://pan.quark.cn/s/aaa", title="A")
        self.store.add_or_update("https://pan.baidu.com/s/aaa", title="B")
        self.assertEqual(len(self.store.items), 2)

    def test_saved_map_and_find_by_share(self):
        item, _ = self.store.add_or_update("https://pan.quark.cn/s/share3",
                                           title="遮天")
        saved = self.store.saved_map()
        self.assertEqual(saved.get("quark:share3"), item["id"])
        self.assertIsNotNone(self.store.find_by_share("quark", "share3"))
        self.assertIsNone(self.store.find_by_share("quark", "nope"))
        self.assertIsNone(self.store.find_by_share("quark", ""))

    def test_find_by_share_is_case_insensitive(self):
        self.store.add_or_update("https://pan.quark.cn/s/AbC123", title="X")
        self.assertIsNotNone(self.store.find_by_share("quark", "abc123"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
