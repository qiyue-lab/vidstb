"""百度网盘分享解析的单元测试。

页面片段取自真实分享页（字段名与结构都和线上一致）。
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import baidu  # noqa: E402
from app.quark import ScanError  # noqa: E402

# 页面里既有静态资源的 file_list（字符串数组），也有真正的文件列表（对象数组）
PAGE_WITH_FILES = """
<script>window.context={"shareid":26873371899,"share_uk":"2054992973"};</script>
<link rel="stylesheet" href="//x/a.css">
<script>window.preload={"file_list":["https://nd-static.bdstatic.com/a.css",
"https://nd-static.bdstatic.com/b.js"]};</script>
<title>flower_photos_免费高速下载|百度网盘-分享无限制</title>
<script>
require(["disk-share/widget/system/uiService"],function(){ ;(function(){
    var d={}; d.file_list=[];
    locals.mset({"file_list":[{"app_id":"250528","fs_id":679344809409611,"isdir":1,
    "path":"/sharelink2054992973-679344809409611/flower_photos",
    "server_filename":"flower_photos","server_mtime":1659690558,"size":0},
    {"app_id":"250528","fs_id":679344809409612,"isdir":0,
    "path":"/sharelink2054992973-679344809409611/斗破苍穹.S01E01.1080p.mkv",
    "server_filename":"斗破苍穹.S01E01.1080p.mkv","server_mtime":1659690559,
    "size":2147483648}],"errortype":-1,"errno":0,"ufcTime":297});
})();});
</script>
"""

PAGE_NEED_PWD = """
<html><head><title>百度网盘 请输入提取码</title></head>
<body><div class="pwd-verify">请输入提取码</div></body></html>
"""

PAGE_GONE = """
<html><head><title>页面不存在_百度网盘</title></head><body>啊哦，页面不存在</body></html>
"""

# 分享要求提取码、但页面里仍然带着 shareid / uk 的情况（现在很常见）
PAGE_NEED_PWD_WITH_META = """
<html><head><title>百度网盘 请输入提取码</title></head>
<script>window.context={"shareid":2595586549,"share_uk":"446014318"};</script>
<body><div class="pwd-verify">请输入提取码</div></body></html>
"""

# 目录接口给根目录内容时，path 带的是「/我的资源」这个虚拟根
ROOT_DIR_ROW = {"app_id": "250528", "fs_id": 679344809409611, "isdir": 1,
                "path": "/我的资源/仙丨逆", "server_filename": "仙丨逆",
                "server_mtime": 1659690558, "size": 0}

VERIFY_OK = '{"errno":0,"err_msg":"","request_id":1,"randsk":"BWVfh7kztUEn4WHhRibw9v8kS7R0Mlu4Z%2FJGi51NBIM%3D"}'


class StubSession:
    """按顺序返回预置页面，并记录请求过的 URL。"""

    def __init__(self, pages):
        self.pages = list(pages)
        self.urls = []
        self.cookies = []

    def open(self, url, data=None, referer="", timeout=20.0):
        self.urls.append(url)
        if not self.pages:
            raise AssertionError("没有更多预置页面了：" + url)
        page = self.pages.pop(0)
        final = "https://pan.baidu.com/share/init?surl=b_t9zkQfKAUD91Vq8b0FAA"
        return page, final

    def set_cookie(self, name, value, domain=".pan.baidu.com"):
        self.cookies.append((name, value, domain))


class ParseTest(unittest.TestCase):
    def test_pick_real_file_list_not_assets(self):
        rows = baidu.parse_file_list(PAGE_WITH_FILES)
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["server_filename"], "flower_photos")
        self.assertEqual(rows[1]["server_filename"], "斗破苍穹.S01E01.1080p.mkv")

    def test_no_file_list_returns_empty(self):
        self.assertEqual(baidu.parse_file_list(PAGE_NEED_PWD), [])
        self.assertEqual(baidu.parse_file_list(""), [])
        self.assertEqual(baidu.parse_file_list("<html></html>"), [])

    def test_parse_meta(self):
        meta = baidu.parse_meta(
            '{"shareid":26873371899,"share_uk":"2054992973","oper_id":"2054992973"}')
        self.assertEqual(meta["shareid"], "26873371899")
        self.assertEqual(meta["uk"], "2054992973")

    def test_parse_meta_empty(self):
        self.assertEqual(baidu.parse_meta("<html></html>"), {"shareid": "", "uk": ""})

    def test_rel_path(self):
        self.assertEqual(
            baidu._rel_path("/sharelink2054992973-679344809409611/flower_photos"),
            "/flower_photos")
        self.assertEqual(baidu._rel_path(""), "/")
        self.assertEqual(baidu._rel_path("/sharelink1-2"), "/")
        # 目录接口给的虚拟根也要去掉
        self.assertEqual(baidu._rel_path("/我的资源/仙丨逆"), "/仙丨逆")

    def test_title_hint(self):
        self.assertEqual(baidu.title_hint(PAGE_WITH_FILES), "flower_photos")
        self.assertEqual(baidu.title_hint("<title>百度网盘-分享文件</title>"), "")
        self.assertEqual(baidu.title_hint(""), "")

    def test_parse_title(self):
        self.assertEqual(baidu.parse_title(PAGE_NEED_PWD), "百度网盘 请输入提取码")

    def test_surl_from_url(self):
        self.assertEqual(baidu._surl_from_url(
            "https://pan.baidu.com/share/init?surl=b_t9zkQfKAUD91Vq8b0FAA",
            "1b_t9zkQfKAUD91Vq8b0FAA"), "b_t9zkQfKAUD91Vq8b0FAA")
        # 兜底：只去掉打头的那一个字符（lstrip 会把 "11abc" 削成 "abc"）
        self.assertEqual(baidu._surl_from_url("https://pan.baidu.com/s/1abc",
                                              "1abc"), "abc")
        self.assertEqual(baidu._surl_from_url("", "11abc"), "1abc")

    def test_parse_cookie(self):
        pairs = baidu._parse_cookie("BDUSS=abc;  STOKEN=def ;broken")
        self.assertEqual(pairs, [("BDUSS", "abc"), ("STOKEN", "def")])
        self.assertEqual(baidu._parse_cookie(""), [])

    def test_errno_text(self):
        self.assertEqual(baidu._errno_text(-62, {}), "提取码不正确")
        self.assertEqual(baidu._errno_text(105, {}), "链接已失效")
        self.assertIn("提取码", baidu._errno_text(-9, {}))
        # 未知错误码优先用网盘自己给的话
        self.assertEqual(baidu._errno_text(99999, {"show_msg": "自定义"}), "自定义")
        self.assertEqual(baidu._errno_text(99999, {}), "网盘返回错误码 99999")

    def test_check_page_gone(self):
        with self.assertRaises(ScanError):
            baidu._check_page(PAGE_GONE)

    def test_verify_writes_raw_randsk(self):
        """randsk 本身就是 URL 编码过的，要原样写进 Cookie。"""
        stub = StubSession([VERIFY_OK])
        baidu.verify(stub, "b_t9zkQfKAUD91Vq8b0FAA", "8888")
        values = [value for name, value, _ in stub.cookies if name == "BDCLND"]
        self.assertEqual(len(values), 1)
        self.assertIn("%2F", values[0])   # unquote 过就没这个了

    def test_fetch_dir_root_flag(self):
        """列根目录必须带 root=1，否则百度返回 errno 2。"""
        stub = StubSession(['{"errno":0,"list":[]}'])
        baidu.fetch_dir(stub, "1", "2", "/", root=True)
        self.assertIn("root=1", stub.urls[-1])
        plain = StubSession(['{"errno":0,"list":[]}'])
        baidu.fetch_dir(plain, "1", "2", "/x")
        self.assertNotIn("root=1", plain.urls[-1])
        # 正常页面不该抛
        baidu._check_page(PAGE_WITH_FILES)


DIR_LISTING = [
    {"fs_id": 11, "isdir": 0, "path": "/sharelink2054992973-1/flower_photos/a.mkv",
     "server_filename": "a.mkv", "server_mtime": 1659690600, "size": 1024},
    {"fs_id": 12, "isdir": 0, "path": "/sharelink2054992973-1/flower_photos/b.srt",
     "server_filename": "b.srt", "server_mtime": 1659690601, "size": 10},
]


class ScanTest(unittest.TestCase):
    def _patch(self, pages):
        stub = StubSession(pages)
        original = baidu._Session

        def factory(cookie=""):
            return stub

        baidu._Session = factory
        self.addCleanup(setattr, baidu, "_Session", original)
        return stub

    def _patch_list(self, result=None, error=None, calls=None):
        """替换子目录接口：result 为返回内容，error 为要抛的异常。"""
        original = baidu.fetch_dir_all

        def fake(session, uk, shareid, dir_path, **kwargs):
            if calls is not None:
                calls.append((uk, shareid, dir_path))
            if error is not None:
                raise error
            return list(result or [])

        baidu.fetch_dir_all = fake
        self.addCleanup(setattr, baidu, "fetch_dir_all", original)

    def test_scan_direct_without_passcode(self):
        self._patch_list(error=ScanError("需要凭证"))
        self._patch([PAGE_WITH_FILES])
        data = baidu.scan("1b_t9zkQfKAUD91Vq8b0FAA")
        self.assertEqual(len(data["tree"]), 2)
        stats = data["stats"]
        self.assertEqual(stats["dirs"], 1)
        self.assertEqual(stats["files"], 1)
        self.assertEqual(stats["video_files"], 1)
        self.assertEqual(stats["total_bytes"], 2147483648)
        # 子目录列不出来，就只保留顶层
        self.assertEqual(stats["unexpanded_dirs"], 1)
        self.assertTrue(data["limited"])
        self.assertEqual(data["tree"][0]["name"], "flower_photos")
        self.assertEqual(data["tree"][0]["children"], [])
        self.assertEqual(data["tree"][1]["kind"], "video")

    def test_scan_descends_into_dirs(self):
        calls = []
        self._patch_list(result=DIR_LISTING, calls=calls)
        self._patch([PAGE_WITH_FILES])
        data = baidu.scan("1b_t9zkQfKAUD91Vq8b0FAA")
        stats = data["stats"]
        # 顶层 1 个文件 + 子目录 2 个文件
        self.assertEqual(stats["files"], 3)
        self.assertEqual(stats["video_files"], 2)
        self.assertEqual(stats["subtitle_files"], 1)
        self.assertEqual(stats["unexpanded_dirs"], 0)
        self.assertFalse(data["limited"])
        self.assertEqual(data["descend_error"], "")
        children = data["tree"][0]["children"]
        self.assertEqual([node["name"] for node in children], ["a.mkv", "b.srt"])
        # 列子目录要用页面里的原始 path（带 sharelink 前缀）
        self.assertEqual(calls[0][2], "/sharelink2054992973-679344809409611/flower_photos")
        self.assertEqual(calls[0][0], "2054992973")

    def test_scan_stops_descending_after_failure(self):
        calls = []
        self._patch_list(error=ScanError("风控"), calls=calls)
        self._patch([PAGE_WITH_FILES])
        data = baidu.scan("1b_t9zkQfKAUD91Vq8b0FAA")
        self.assertEqual(len(calls), 1)  # 失败一次就别再试了
        self.assertIn("风控", data["descend_error"])
        self.assertTrue(data["limited"])

    def test_scan_with_passcode_puts_pwd_in_url(self):
        stub = self._patch([PAGE_WITH_FILES])
        baidu.scan("1abc", passcode="8888")
        self.assertIn("pwd=8888", stub.urls[0])

    def test_scan_verify_when_page_needs_passcode(self):
        self._patch_list(result=DIR_LISTING)
        stub = self._patch([PAGE_NEED_PWD, VERIFY_OK, PAGE_WITH_FILES])
        data = baidu.scan("1b_t9zkQfKAUD91Vq8b0FAA", passcode="8888")
        self.assertEqual(len(data["tree"]), 2)
        # verify 之后凭证要写进 Cookie
        self.assertTrue(any(name == "BDCLND" for name, _, _ in stub.cookies))

    def test_scan_needs_passcode_raises(self):
        self._patch([PAGE_NEED_PWD])
        with self.assertRaises(ScanError) as ctx:
            baidu.scan("1b_t9zkQfKAUD91Vq8b0FAA")
        self.assertIn("提取码", str(ctx.exception))

    def test_scan_wrong_passcode_message(self):
        self._patch([PAGE_NEED_PWD, '{"errno":-62}', PAGE_GONE])
        with self.assertRaises(ScanError) as ctx:
            baidu.scan("1b_t9zkQfKAUD91Vq8b0FAA", passcode="0000")
        self.assertIn("提取码", str(ctx.exception))

    def test_scan_gone_page(self):
        self._patch([PAGE_GONE])
        with self.assertRaises(ScanError) as ctx:
            baidu.scan("1nope")
        self.assertIn("失效", str(ctx.exception))

    def test_scan_requires_surl(self):
        with self.assertRaises(ScanError):
            baidu.scan("")

    def test_scan_respects_max_files(self):
        self._patch([PAGE_WITH_FILES])
        data = baidu.scan("1x", max_files=1)
        self.assertTrue(data["stats"]["truncated"])

    def test_scan_falls_back_to_list_api(self):
        """页面验证完还是提取码页时，改用目录接口取顶层（带 root=1）。"""
        seen = {}
        original = baidu.fetch_dir_all

        def fake(session, uk, shareid, dir_path, **kwargs):
            if kwargs.get("root"):
                seen["root"] = (uk, shareid, dir_path)
                return [dict(ROOT_DIR_ROW)]
            return []          # 子目录这次不展开

        baidu.fetch_dir_all = fake
        self.addCleanup(setattr, baidu, "fetch_dir_all", original)
        stub = self._patch([PAGE_NEED_PWD_WITH_META, VERIFY_OK,
                            PAGE_NEED_PWD_WITH_META])
        data = baidu.scan("1xPi1kr4_10o70nAL7S0Gkw", passcode="6foJ")
        self.assertEqual(seen["root"], ("446014318", "2595586549", "/"))
        self.assertEqual(len(data["tree"]), 1)
        self.assertEqual(data["tree"][0]["name"], "仙丨逆")
        self.assertEqual(data["tree"][0]["path"], "/仙丨逆")
        # 页面没给标题时，顶层只有一个文件夹就先用它当标题
        self.assertEqual(data["title_hint"], "仙丨逆")
        self.assertTrue(any(name == "BDCLND" for name, _, _ in stub.cookies))
        self.assertEqual(len(data["tree"]), 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
