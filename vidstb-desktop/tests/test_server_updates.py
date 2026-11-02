"""服务端「检查更新」任务的小测试（只测任务查询，不启服务）。"""

import os
import sys
import threading
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import server  # noqa: E402


class StubApp:
    """只带上 update_task 需要的那几个字段。"""

    _resource_lock = threading.Lock()
    update_tasks = {}
    _current_update_id = ""

    update_task = server.Application.update_task
    _current_update_task = server.Application._current_update_task


class UpdateTaskTest(unittest.TestCase):
    def setUp(self):
        self.app = StubApp()
        self.app.update_tasks = {"abc": {"id": "abc", "status": "done", "message": "ok"}}
        self.app._current_update_id = "abc"

    def test_query_without_id_returns_current(self):
        self.assertEqual(self.app.update_task()["id"], "abc")

    def test_query_by_id(self):
        self.assertEqual(self.app.update_task("abc")["message"], "ok")

    def test_unknown_id(self):
        self.assertIsNone(self.app.update_task("nope"))

    def test_no_task_at_all(self):
        self.app.update_tasks = {}
        self.app._current_update_id = ""
        self.assertIsNone(self.app.update_task())

    def test_query_does_not_self_deadlock(self):
        """用普通 Lock（非 RLock）也必须能返回：历史上这里嵌套加锁把接口锁死过。"""
        done = threading.Event()

        def run():
            self.app.update_task()
            done.set()

        thread = threading.Thread(target=run, daemon=True)
        thread.start()
        done.wait(3)
        self.assertTrue(done.is_set(), "update_task() 自己把自己锁死了")
