"""盘搜（app/pansou.py）解析测试。"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import pansou  # noqa: E402


# 后端现在返回的结构
NEW_PAYLOAD = {
    "code": 0,
    "message": "success",
    "data": {
        "total": 3,
        "merged_by_type": {
            "quark": [
                {"url": "https://pan.quark.cn/s/abc123defg", "password": "",
                 "note": "斗破苍穹 年番 4K 更新217集", "datetime": "2026-11-01T08:12:08Z",
                 "source": "tg:quark", "images": ["https://x/1.jpg"]},
                {"url": "https://pan.quark.cn/s/zzz999", "password": "",
                 "note": "别的剧", "datetime": "0001-01-01T00:00:00Z", "source": "tg:x"},
            ],
            "baidu": [
                {"url": "https://pan.baidu.com/s/1DH0SLeXT4HK7NCw6vYDChg?pwd=ryys",
                 "password": "", "note": "斗破苍穹 全集", "datetime": "2026-10-02T01:02:03Z",
                 "source": "plugin:zhizhen"},
            ],
        },
    },
}

# 老版本（Android 端 pansou-master 用的那套）
OLD_PAYLOAD = {
    "results": [
        {"id": "1", "url": "https://pan.quark.cn/s/oldshare", "title": "凡人修仙传",
         "password": "abcd", "datetime": "2026-01-02T03:04:05Z", "source": "tg:old"},
    ],
    "total": 9,
    "cached": True,
}


class ParseTest(unittest.TestCase):
    def test_new_schema(self):
        parsed = pansou.parse_payload(NEW_PAYLOAD)
        self.assertEqual(parsed["total"], 3)
        self.assertEqual(len(parsed["items"]), 3)
        self.assertEqual(parsed["counts"]["quark"], 2)
        self.assertEqual(parsed["counts"]["baidu"], 1)

        quark_item = parsed["items"][0]
        self.assertEqual(quark_item["platform"], "quark")
        self.assertEqual(quark_item["platform_label"], "夸克")
        self.assertEqual(quark_item["title"], "斗破苍穹 年番 4K 更新217集")
        self.assertEqual(quark_item["share_id"], "abc123defg")
        self.assertEqual(quark_item["datetime"], "2026-11-01")
        self.assertTrue(quark_item["scannable"])
        self.assertEqual(quark_item["images"], ["https://x/1.jpg"])

    def test_baidu_passcode_from_url(self):
        parsed = pansou.parse_payload(NEW_PAYLOAD)
        baidu_item = next(i for i in parsed["items"] if i["platform"] == "baidu")
        self.assertEqual(baidu_item["share_id"], "1DH0SLeXT4HK7NCw6vYDChg")
        # 链接里的 ?pwd= 要当成提取码，后面读文件要用
        self.assertEqual(baidu_item["password"], "ryys")
        self.assertTrue(baidu_item["scannable"])

    def test_zero_datetime_ignored(self):
        parsed = pansou.parse_payload(NEW_PAYLOAD)
        item = next(i for i in parsed["items"] if i["share_id"] == "zzz999")
        self.assertEqual(item["datetime"], "")

    def test_old_schema(self):
        parsed = pansou.parse_payload(OLD_PAYLOAD)
        self.assertEqual(parsed["total"], 9)
        self.assertEqual(len(parsed["items"]), 1)
        item = parsed["items"][0]
        self.assertEqual(item["id"], "1")
        self.assertEqual(item["title"], "凡人修仙传")
        self.assertEqual(item["password"], "abcd")
        self.assertEqual(item["datetime"], "2026-01-02")
        self.assertEqual(item["platform"], "quark")
        self.assertTrue(item["scannable"])

    def test_garbage_payload(self):
        for payload in ({}, {"data": None}, {"results": "x"}, None, "abc"):
            parsed = pansou.parse_payload(payload)
            self.assertEqual(parsed["items"], [])
            self.assertEqual(parsed["counts"], {})

    def test_non_pan_link_not_scannable(self):
        payload = {"data": {"total": 1, "merged_by_type": {"magnet": [
            {"url": "magnet:?xt=urn:btih:abc", "note": "某个种子", "datetime": ""}]}}}
        item = pansou.parse_payload(payload)["items"][0]
        self.assertFalse(item["scannable"])
        self.assertEqual(item["provider"], "other")

    def test_title_falls_back_to_url(self):
        payload = {"data": {"total": 1, "merged_by_type": {"quark": [
            {"url": "https://pan.quark.cn/s/nonotitle", "note": "", "datetime": ""}]}}}
        item = pansou.parse_payload(payload)["items"][0]
        self.assertEqual(item["title"], "nonotitle")

    def test_platform_label(self):
        self.assertEqual(pansou.platform_label("quark"), "夸克")
        self.assertEqual(pansou.platform_label("xunlei"), "迅雷")
        self.assertEqual(pansou.platform_label("未知道"), "未知道")


class SearchTest(unittest.TestCase):
    """用假的后端响应跑一遍 search()，顺便验证本地的相关性排序。"""

    def setUp(self):
        self.original = pansou._try
        self.seen = []

    def tearDown(self):
        pansou._try = self.original

    def _stub(self, payload):
        def fake(path, body=None, timeout=30.0, bases=None):
            self.seen.append((path, body))
            return payload
        pansou._try = fake

    def test_search_sorts_matches_first(self):
        self._stub(NEW_PAYLOAD)
        result = pansou.search("斗破苍穹")
        self.assertEqual(result["total"], 3)
        self.assertEqual(result["matched"], 2)
        titles = [item["title"] for item in result["items"]]
        self.assertTrue(all("斗破苍穹" in title for title in titles[:2]))
        self.assertNotIn("别的剧", titles[:2])
        self.assertTrue(all(item["matched"] for item in result["items"][:2]))
        self.assertFalse(result["items"][-1]["matched"])

    def test_search_request_body(self):
        self._stub(OLD_PAYLOAD)
        pansou.search("凡人修仙传", platform="baidu")
        path, body = self.seen[0]
        self.assertEqual(path, "api/search")
        self.assertEqual(body, {"query": "凡人修仙传", "page": 1, "platform": "baidu"})

    def test_empty_query_short_circuit(self):
        result = pansou.search("   ")
        self.assertEqual(result["items"], [])
        self.assertEqual(self.seen, [])   # 不该发请求

    def test_error_code_raises(self):
        self._stub({"code": 400, "message": "无效的请求参数"})
        with self.assertRaises(pansou.PanSouError) as ctx:
            pansou.search("x")
        self.assertIn("无效的请求参数", str(ctx.exception))

    def test_platforms_needs_no_network(self):
        def boom(path, body=None, timeout=30.0, bases=None):
            raise AssertionError("platforms() 不该再发请求")
        pansou._try = boom
        self.assertEqual(pansou.platforms(), pansou.DEFAULT_PLATFORMS)

    def test_related(self):
        self._stub({"resources": [{"slug": "a", "title": "斗破苍穹 年番"},
                                  {"slug": "b", "title": "斗破苍穹"}]})
        self.assertEqual(pansou.related("斗破苍穹"), ["斗破苍穹 年番", "斗破苍穹"])

    def test_related_failure_is_empty(self):
        def boom(path, body=None, timeout=30.0, bases=None):
            raise pansou.PanSouError("连不上")
        pansou._try = boom
        self.assertEqual(pansou.related("x"), [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
