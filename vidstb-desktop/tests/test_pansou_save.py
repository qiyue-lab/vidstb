"""盘搜「保存到我的资源」的后端逻辑（不启服务、不联网）。"""

import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import server  # noqa: E402
from app import resources  # noqa: E402


class StubApp:
    """只带上盘搜保存需要的那点东西。"""

    def __init__(self, store):
        self.resource_store = store

    pansou_save = server.Application.pansou_save
    pansou_save_many = server.Application.pansou_save_many
    pansou_saved_map = server.Application.pansou_saved_map
    _mark_saved = server.Application._mark_saved


class PansouSaveTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="vidstb-psave-")
        self.path = os.path.join(self.dir, "resources.json")
        self.app = StubApp(resources.ResourceStore(self.path))

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def test_save_single_creates_item(self):
        result = self.app.pansou_save("https://pan.quark.cn/s/abc123",
                                      "斗破苍穹 S01", "ab12")
        self.assertTrue(result["ok"])
        self.assertTrue(result["created"])
        self.assertEqual(result["item"]["provider"], "quark")
        self.assertEqual(result["item"]["passcode"], "ab12")
        self.assertEqual(len(self.app.resource_store.items), 1)

    def test_save_twice_updates_instead_of_duplicating(self):
        first = self.app.pansou_save("https://pan.quark.cn/s/abc123", "斗破苍穹")
        second = self.app.pansou_save("https://pan.quark.cn/s/abc123", "斗破苍穹 年番")
        self.assertTrue(first["created"])
        self.assertFalse(second["created"])
        self.assertEqual(len(self.app.resource_store.items), 1)
        self.assertEqual(second["item"]["title"], "斗破苍穹 年番")

    def test_save_with_group_status_and_tags(self):
        group = self.app.resource_store.create_group("追更")
        result = self.app.pansou_save("https://pan.baidu.com/s/1AbCdEf 8x2k",
                                      "凡人修仙传", "8x2k",
                                      group=group["id"], status="finished",
                                      tags=["国漫"])
        self.assertEqual(result["item"]["status"], "finished")
        self.assertEqual(result["item"]["group"], group["id"])
        self.assertIn("国漫", result["item"]["tags"])

    def test_save_many_counts_created_and_updated(self):
        self.app.pansou_save("https://pan.quark.cn/s/aaa", "A")   # 先存一条
        rows = [
            {"url": "https://pan.quark.cn/s/aaa", "title": "A", "password": ""},
            {"url": "https://pan.quark.cn/s/bbb", "title": "B", "password": ""},
            {"url": "https://pan.baidu.com/s/ccc", "title": "C", "password": "12ab"},
        ]
        result = self.app.pansou_save_many(rows)
        self.assertTrue(result["ok"])
        self.assertEqual(result["created"], 2)
        self.assertEqual(result["updated"], 1)
        self.assertEqual(result["failed"], [])
        self.assertEqual(len(self.app.resource_store.items), 3)

    def test_save_many_skips_empty_url(self):
        result = self.app.pansou_save_many([{"url": "  ", "title": "x"}, {}])
        self.assertEqual(result["created"], 0)
        self.assertEqual(result["updated"], 0)
        self.assertEqual(len(self.app.resource_store.items), 0)

    def test_saved_map_marks_search_results(self):
        self.app.pansou_save("https://pan.quark.cn/s/aaa", "A")
        items = [
            {"url": "https://pan.quark.cn/s/aaa", "share_key": "quark:aaa"},
            {"url": "https://pan.quark.cn/s/zzz", "share_key": "quark:zzz"},
        ]
        self.app._mark_saved(items)
        self.assertTrue(items[0]["saved"])
        self.assertEqual(items[0]["saved_id"], self.app.resource_store.items[0]["id"])
        self.assertFalse(items[1]["saved"])
        self.assertEqual(items[1]["saved_id"], "")

    def test_saved_map_empty_store(self):
        self.assertEqual(self.app.pansou_saved_map(), {})


if __name__ == "__main__":
    unittest.main(verbosity=2)
