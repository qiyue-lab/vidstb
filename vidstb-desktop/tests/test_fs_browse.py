import os
import subprocess
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import fs_browse  # noqa: E402


class FakeCompleted:
    def __init__(self, stdout="", stderr="", returncode=0):
        self.stdout = stdout.encode("utf-8")
        self.stderr = stderr.encode("utf-8")
        self.returncode = returncode


class PickFilesNativeTest(unittest.TestCase):
    def setUp(self):
        self.patcher = mock.patch.object(fs_browse.subprocess, "run")
        self.run = self.patcher.start()
        self.addCleanup(self.patcher.stop)
        self.windows = mock.patch.object(fs_browse, "IS_WINDOWS", True)
        self.windows.start()
        self.addCleanup(self.windows.stop)

    def test_ok_marker_returns_files(self):
        self.run.return_value = FakeCompleted("VIDSTB_OK\nD:\\a.mkv\nD:\\b.mp4\n")
        result = fs_browse.pick_files_native()
        self.assertTrue(result["ok"])
        self.assertEqual(result["files"], ["D:\\a.mkv", "D:\\b.mp4"])
        self.assertEqual(result["error"], "")

    def test_cancel_is_ok_with_empty_files(self):
        self.run.return_value = FakeCompleted("VIDSTB_CANCEL\n")
        result = fs_browse.pick_files_native()
        self.assertTrue(result["ok"])
        self.assertEqual(result["files"], [])

    def test_error_marker_reports_reason(self):
        self.run.return_value = FakeCompleted("VIDSTB_ERROR\n拒绝访问\n")
        result = fs_browse.pick_files_native()
        self.assertFalse(result["ok"])
        self.assertIn("拒绝访问", result["error"])

    def test_empty_output_is_failure(self):
        self.run.return_value = FakeCompleted("", stderr="something", returncode=1)
        result = fs_browse.pick_files_native()
        self.assertFalse(result["ok"])
        self.assertIn("something", result["error"])

    def test_timeout_is_failure(self):
        self.run.side_effect = subprocess.TimeoutExpired("powershell", 180)
        result = fs_browse.pick_files_native()
        self.assertFalse(result["ok"])
        self.assertIn("超时", result["error"])

    def test_passes_sta_and_hidden_window(self):
        self.run.return_value = FakeCompleted("VIDSTB_CANCEL\n")
        fs_browse.pick_files_native()
        args = self.run.call_args[0][0]
        self.assertEqual(args[0], "powershell")
        self.assertIn("-STA", args)
        self.assertIn("-WindowStyle", args)


class DialogSupportTest(unittest.TestCase):
    def test_false_on_non_windows(self):
        with mock.patch.object(fs_browse, "IS_WINDOWS", False):
            self.assertFalse(fs_browse.can_show_native_dialog(refresh=True))


class PathHelpersTest(unittest.TestCase):
    def test_is_video(self):
        self.assertTrue(fs_browse.is_video("a.MKV"))
        self.assertTrue(fs_browse.is_video("b.mp4"))
        self.assertFalse(fs_browse.is_video("c.txt"))

    def test_list_dir_missing(self):
        result = fs_browse.list_dir(os.path.join("Z:\\", "definitely-not-here"))
        self.assertEqual(result["entries"], [])
        self.assertTrue(result.get("error"))

    def test_scan_dir_returns_only_videos(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            for name in ("a.mkv", "b.mp4", "c.txt"):
                with open(os.path.join(tmp, name), "w", encoding="utf-8") as handle:
                    handle.write("x")
            found = fs_browse.scan_dir(tmp)
        self.assertEqual(len(found), 2)
        self.assertTrue(all(path.endswith((".mkv", ".mp4")) for path in found))


if __name__ == "__main__":
    unittest.main()
