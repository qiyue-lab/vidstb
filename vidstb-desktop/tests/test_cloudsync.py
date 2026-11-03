"""cloudsync 客户端与真实云端（vidstb-cloud）的端到端同步测试。

测试方式：用子进程起一个真实的云端服务（vidstb-cloud/server.py），
桌面版 cloudsync 客户端走真实 HTTP 协议同步。两个项目包名都叫 app，
没法在一个进程里 import，所以云端只能跑子进程——这反而更好：
测的是真实协议而不是 mock，两端字段一旦错位立刻暴露。

需要 vidstb-cloud 项目在本机（默认相对路径定位，可用
VIDSTB_CLOUD_DIR 环境变量覆盖）。找不到就跳过。
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import cloudsync, resources  # noqa: E402

_DESKTOP_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_CLOUD_DIR = os.environ.get(
    "VIDSTB_CLOUD_DIR",
    os.path.normpath(os.path.join(
        _DESKTOP_ROOT, "..", "..", "..", "vidstb-cloud")))
_CLOUD_SERVER = os.path.join(_CLOUD_DIR, "server.py")


def _wait_ready(port: int, timeout: float = 15.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(
                    f"http://127.0.0.1:{port}/healthz", timeout=2) as resp:
                if resp.status == 200:
                    return True
        except OSError:
            time.sleep(0.3)
    return False


@unittest.skipUnless(os.path.isfile(_CLOUD_SERVER),
                     f"找不到 vidstb-cloud 项目（{_CLOUD_SERVER}）")
class CloudSyncE2ETest(unittest.TestCase):
    """桌面版 <-> 真实云端 的双向同步。"""

    PORT = 18877
    PASSWORD = "e2e-sync-test"

    @classmethod
    def setUpClass(cls):
        cls.cloud_dir = tempfile.mkdtemp(prefix="vidstb-cloud-e2e-")
        env = dict(os.environ)
        env["VIDSTB_DATA_DIR"] = cls.cloud_dir
        env["VIDSTB_PASSWORD"] = cls.PASSWORD
        env["VIDSTB_ROLE"] = ""
        cls.proc = subprocess.Popen(
            [sys.executable, _CLOUD_SERVER, "--port", str(cls.PORT),
             "--host", "127.0.0.1"],
            cwd=_CLOUD_DIR, env=env,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        if not _wait_ready(cls.PORT):
            cls.proc.terminate()
            output = cls.proc.stdout.read().decode("utf-8", "replace")[:2000]
            raise RuntimeError(f"云端起不来：\n{output}")
        cls.base_url = f"http://127.0.0.1:{cls.PORT}"

    @classmethod
    def tearDownClass(cls):
        cls.proc.terminate()
        try:
            cls.proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            cls.proc.kill()
        shutil.rmtree(cls.cloud_dir, ignore_errors=True)

    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="vidstb-desktop-e2e-")
        self.store = resources.ResourceStore(
            os.path.join(self.dir, "resources.json"),
            os.path.join(self.dir, "trash.json"))
        self.config = {"cloud_url": self.base_url,
                       "cloud_password": self.PASSWORD}
        # 每个用例都从空云端开始
        self._cloud_reset()

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    # ---------- 工具 ----------
    def _cloud_call(self, path, payload=None, method="POST"):
        cookie = getattr(self, "_cookie", None)
        if cookie is None:
            req = urllib.request.Request(
                f"{self.base_url}/api/login",
                data=json.dumps({"password": self.PASSWORD}).encode(),
                headers={"Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=10) as resp:
                self._cookie = resp.headers["Set-Cookie"].split(";")[0]
            cookie = self._cookie
        data = json.dumps(payload).encode() if payload is not None else None
        req = urllib.request.Request(f"{self.base_url}{path}", data=data,
                                     method=method,
                                     headers={"Content-Type": "application/json",
                                              "Cookie": cookie})
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read().decode("utf-8"))

    def _cloud_reset(self):
        for row in self._cloud_call("/api/resources", method="GET")["items"]:
            self._cloud_call(f"/api/resources/{row['id']}", method="DELETE")
        self._cloud_call("/api/resources/trash", method="DELETE")

    def _cloud_titles(self):
        data = self._cloud_call("/api/resources", method="GET")
        return sorted(item["title"] for item in data["items"])

    # ---------- 用例 ----------
    def test_push_local_to_cloud(self):
        """本地有、云端没有的，推上去。"""
        self.store.add("https://pan.quark.cn/s/local1 提取码：ab12",
                       title="本地独有")
        summary = cloudsync.sync_now(self.store, self.config)
        self.assertEqual(summary["added"], 1)
        self.assertEqual(self._cloud_titles(), ["本地独有"])
        cloud_item = self._cloud_call("/api/resources", method="GET")["items"][0]
        self.assertEqual(cloud_item["passcode"], "ab12")

    def test_pull_cloud_to_local(self):
        """云端有、本地没有的，拉下来。"""
        self._cloud_call("/api/resources",
                         {"urls": ["https://pan.quark.cn/s/cloud1"],
                          "title": "云端独有"})
        summary = cloudsync.sync_now(self.store, self.config)
        self.assertEqual([i["title"] for i in self.store.items], ["云端独有"])
        self.assertEqual(summary["total"], 1)

    def test_bidirectional_union(self):
        """两边各有独有数据，同步后两边都是并集。"""
        self.store.add("https://pan.quark.cn/s/local1", title="本地的")
        self.store.add("https://pan.baidu.com/s/local2", title="还是本地的")
        self._cloud_call("/api/resources",
                         {"urls": ["https://pan.quark.cn/s/cloud1"],
                          "title": "云端的"})
        cloudsync.sync_now(self.store, self.config)
        expect = ["云端的", "本地的", "还是本地的"]
        self.assertEqual(sorted(i["title"] for i in self.store.items), expect)
        self.assertEqual(self._cloud_titles(), expect)

    def test_local_delete_propagates(self):
        """本地删过的（且云端没动过），同步后云端也删。"""
        self._cloud_call("/api/resources",
                         {"urls": ["https://pan.quark.cn/s/doomed"],
                          "title": "将被删除"})
        cloudsync.sync_now(self.store, self.config)          # 拉到本地
        item = self.store.items[0]
        self.store.remove(item["id"])                        # 本地删除
        time.sleep(0.01)                                     # 保证时间戳递增
        summary = cloudsync.sync_now(self.store, self.config)
        self.assertEqual(summary["deleted"], 1)
        self.assertEqual(self._cloud_titles(), [])
        self.assertEqual(self.store.items, [])

    def test_cloud_delete_propagates(self):
        """云端删过的（且本地没动过），同步后本地也删。"""
        self.store.add("https://pan.quark.cn/s/doomed2", title="也将被删")
        cloudsync.sync_now(self.store, self.config)          # 推上云端
        cloud_id = self._cloud_call("/api/resources",
                                    method="GET")["items"][0]["id"]
        self._cloud_call(f"/api/resources/{cloud_id}", method="DELETE")
        time.sleep(0.01)
        cloudsync.sync_now(self.store, self.config)
        self.assertEqual(self.store.items, [])

    def test_idempotent_second_run(self):
        """第二次同步零变化。"""
        self.store.add("https://pan.quark.cn/s/local1", title="本地的")
        self._cloud_call("/api/resources",
                         {"urls": ["https://pan.quark.cn/s/cloud1"],
                          "title": "云端的"})
        cloudsync.sync_now(self.store, self.config)
        time.sleep(0.01)
        summary = cloudsync.sync_now(self.store, self.config)
        self.assertEqual(summary["added"], 0)
        self.assertEqual(summary["updated"], 0)
        self.assertEqual(summary["deleted"], 0)

    def test_groups_sync_both_ways(self):
        """分组双向合并，条目分组按名字对齐。"""
        group = self.store.create_group("本地分组")
        self.store.add("https://pan.quark.cn/s/g1", title="带组的",
                       group=group["id"])
        self._cloud_call("/api/groups", {"name": "云端分组"})
        cloudsync.sync_now(self.store, self.config)
        names = sorted(g["name"] for g in self.store.groups)
        self.assertEqual(names, ["云端分组", "本地分组"])
        cloud_groups = self._cloud_call("/api/groups", method="GET")["groups"]
        self.assertEqual(sorted(g["name"] for g in cloud_groups), names)
        # 云端那条「带组的」应该在「本地分组」里（按名字对上）
        cloud_items = self._cloud_call("/api/resources", method="GET")["items"]
        by_title = {i["title"]: i for i in cloud_items}
        local_group_id = [g for g in self.store.groups
                          if g["name"] == "本地分组"][0]["id"]
        self.assertEqual(by_title["带组的"]["group"], local_group_id)

    def test_wrong_password_raises(self):
        """口令不对要给用户看得懂的错误，不能是堆栈。"""
        self.config["cloud_password"] = "wrong"
        with self.assertRaises(cloudsync.SyncError) as caught:
            cloudsync.sync_now(self.store, self.config)
        self.assertIn("口令", str(caught.exception))

    def test_missing_url_raises(self):
        """没配置地址时给提示。"""
        self.config["cloud_url"] = ""
        with self.assertRaises(cloudsync.SyncError) as caught:
            cloudsync.sync_now(self.store, self.config)
        self.assertIn("云端地址", str(caught.exception))

    def test_unreachable_raises(self):
        """连不上要给网络错误，不能是堆栈。"""
        self.config["cloud_url"] = "http://127.0.0.1:1"
        with self.assertRaises(cloudsync.SyncError) as caught:
            cloudsync.sync_now(self.store, self.config)
        message = str(caught.exception)
        self.assertTrue("连不上" in message or "网络" in message)


if __name__ == "__main__":
    unittest.main()
