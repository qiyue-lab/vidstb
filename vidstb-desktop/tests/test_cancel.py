"""停止队列 / 取消任务的回归测试。

回归背景：以前「停止队列」只能杀掉主编码进程，字体导出、字幕导出这些
准备阶段的 ffmpeg 没被登记，任务会一直卡在“正在准备…”，点了没反应。
"""

import os
import subprocess
import sys
import tempfile
import threading
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import jobs as jobs_module, media_probe  # noqa: E402
from app.models import ProbeResult, SubtitleTrack, VideoMetadata  # noqa: E402
from app.runner import ProcessGuard  # noqa: E402


class FfmpegStub:
    """记录调用参数的假 ffmpeg。"""

    def __init__(self):
        self.ffmpeg_path = "ffmpeg"
        self.calls = []
        self.on_process_calls = []

    def run_ffmpeg(self, args, timeout=60, env=None, on_process=None):
        self.calls.append(list(args))
        if on_process is not None:
            self.on_process_calls.append(on_process)
        return ""


def make_manager(ffmpeg_stub=None, config=None):
    return jobs_module.JobManager(ffmpeg_stub or FfmpegStub(),
                                  config or {"parallel": 1, "container": "mkv"})


def make_job(path="/tmp/a.mkv", kind=jobs_module.KIND_SUBTITLE):
    subtitle = SubtitleTrack(stream_index=2, subtitle_index=0, codec_name="ass",
                             language="chi", title="中文", default_track=True)
    probe = ProbeResult(path, VideoMetadata(width=1920, height=1080, duration_us=60_000_000),
                        [subtitle], [])
    job = jobs_module.Job(path, [0], probe, kind=kind)
    return job


class ProcessGuardTest(unittest.TestCase):
    def test_cancel_kills_attached_process(self):
        guard = ProcessGuard()
        process = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
        guard.attach(process)
        guard.cancel()
        process.wait(timeout=10)
        self.assertIsNotNone(process.poll())

    def test_attach_after_cancel_kills_immediately(self):
        guard = ProcessGuard()
        guard.cancel()
        process = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
        guard.attach(process)
        process.wait(timeout=10)
        self.assertIsNotNone(process.poll())

    def test_cancel_without_process_is_safe(self):
        ProcessGuard().cancel()


class StopQueueTest(unittest.TestCase):
    def test_stop_marks_running_job_even_without_runner(self):
        """准备阶段没有 ConversionRunner，也必须能被中止。"""
        manager = make_manager()
        job = make_job()
        job.status = jobs_module.STATUS_RUNNING
        manager.jobs.append(job)

        stopped = manager.stop_queue()

        self.assertEqual(stopped, 1)
        self.assertTrue(job.is_cancelled)
        self.assertFalse(manager.state()["running"])

    def test_stop_cancels_queued_jobs(self):
        manager = make_manager()
        job = make_job()
        manager.jobs.append(job)  # 默认 queued

        stopped = manager.stop_queue()

        self.assertEqual(stopped, 1)
        self.assertEqual(job.status, jobs_module.STATUS_CANCELLED)
        self.assertTrue(job.is_cancelled)

    def test_stop_kills_registered_guard(self):
        manager = make_manager()
        job = make_job()
        job.status = jobs_module.STATUS_RUNNING
        manager.jobs.append(job)
        guard = ProcessGuard()
        manager._guards[job.id] = guard

        manager.stop_queue()

        self.assertTrue(guard.cancelled)

    def test_stop_ignores_finished_jobs(self):
        manager = make_manager()
        done = make_job()
        done.status = jobs_module.STATUS_DONE
        manager.jobs.append(done)
        self.assertEqual(manager.stop_queue(), 0)
        self.assertFalse(done.is_cancelled)

    def test_resume_clears_cancel_flag(self):
        manager = make_manager()
        job = make_job()
        manager.jobs.append(job)
        manager.stop_queue()
        self.assertTrue(job.is_cancelled)

        resumed = manager.resume_queue()

        self.assertEqual(resumed, 1)
        self.assertFalse(job.is_cancelled)
        # 恢复后分派线程会立刻把它捞起来跑，所以可能是 queued 也可能是 running
        self.assertIn(job.status,
                      (jobs_module.STATUS_QUEUED, jobs_module.STATUS_RUNNING))

    def test_cancel_job_sets_flag_for_running_job(self):
        manager = make_manager()
        job = make_job()
        job.status = jobs_module.STATUS_RUNNING
        manager.jobs.append(job)

        self.assertTrue(manager.cancel_job(job.id))
        self.assertTrue(job.is_cancelled)


class DispatchTest(unittest.TestCase):
    def test_only_parallel_jobs_are_marked_running(self):
        """并发数为 1 时，不能把队列里的任务一次性全标成“运行中”。"""
        manager = make_manager(config={"parallel": 1, "container": "mkv"})
        started = threading.Event()

        def slow_probe(path):
            started.set()
            time.sleep(1.5)
            return make_job(path).probe

        manager.probe_file = slow_probe
        for index in range(3):
            manager.jobs.append(jobs_module.Job(f"/tmp/{index}.mkv", [0], None))

        manager._ensure_dispatcher()
        started.wait(5)
        time.sleep(0.3)
        running = sum(1 for job in manager.jobs
                      if job.status == jobs_module.STATUS_RUNNING)
        self.assertEqual(running, 1)

        stopped = manager.stop_queue()
        self.assertEqual(stopped, 3)
        for job in manager.jobs:
            self.assertTrue(job.is_cancelled)


class FontDumpTest(unittest.TestCase):
    def test_dumps_all_attachments_in_one_call(self):
        """所有字体一次导出（旧的写法是每个字体跑一遍 ffmpeg）。"""
        stub = FfmpegStub()
        with tempfile.TemporaryDirectory() as directory:
            dumped = media_probe.dump_font_attachments(
                stub, "/tmp/a.mkv",
                [{"stream_index": 5}, {"stream_index": 6}, {"stream_index": 7}],
                directory)

        self.assertEqual(len(stub.calls), 1)
        args = stub.calls[0]
        self.assertEqual(sum(1 for item in args if item.startswith("-dump_attachment:")), 3)
        self.assertIn("-c", args)
        self.assertIn("copy", args)
        self.assertEqual(dumped, 0)  # ffmpeg 是假的，自然没导出出文件

    def test_should_stop_skips_dumping(self):
        stub = FfmpegStub()
        with tempfile.TemporaryDirectory() as directory:
            dumped = media_probe.dump_font_attachments(
                stub, "/tmp/a.mkv", [{"stream_index": 5}], directory,
                should_stop=lambda: True)
        self.assertEqual(stub.calls, [])
        self.assertEqual(dumped, 0)


class NeedsFontsTest(unittest.TestCase):
    def test_image_only_subtitles_skip_fonts(self):
        track = SubtitleTrack(stream_index=3, subtitle_index=0,
                              codec_name="hdmv_pgs_bluray", language="jpn")
        probe = ProbeResult("/tmp/a.mkv",
                            VideoMetadata(width=1920, height=1080),
                            [track], [{"stream_index": 5}])
        job = jobs_module.Job("/tmp/a.mkv", [0], probe)
        self.assertEqual(jobs_module.JobManager._selected_tracks(job)[0]["kind"], "image")
        self.assertFalse(jobs_module.JobManager._needs_fonts(job))

    def test_text_subtitles_need_fonts(self):
        self.assertTrue(jobs_module.JobManager._needs_fonts(make_job()))


if __name__ == "__main__":
    unittest.main(verbosity=2)
