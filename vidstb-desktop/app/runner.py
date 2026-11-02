"""ffmpeg 子进程执行与进度解析。"""

from __future__ import annotations

import os
import subprocess
import sys
import threading
import time
from collections import deque
from typing import Callable, List, Optional

IS_WINDOWS = sys.platform.startswith("win")


def _creation_flags() -> int:
    return subprocess.CREATE_NO_WINDOW if IS_WINDOWS else 0


class ProcessGuard:
    """可取消的辅助子进程句柄。

    字体导出 / 字幕导出这类准备工作发生在主编码之前，不走 ConversionRunner，
    如果不把子进程登记进来，「停止队列」就杀不掉它们，任务会一直卡在“正在准备…”。
    """

    def __init__(self):
        self._lock = threading.Lock()
        self._process: Optional[subprocess.Popen] = None
        self.cancelled = False

    def attach(self, process: subprocess.Popen) -> None:
        with self._lock:
            self._process = process
            cancelled = self.cancelled
        if cancelled:
            self._kill(process)

    def detach(self, process: subprocess.Popen) -> None:
        with self._lock:
            if self._process is process:
                self._process = None

    def cancel(self) -> None:
        with self._lock:
            self.cancelled = True
            process = self._process
        if process is not None:
            self._kill(process)

    @staticmethod
    def _kill(process: subprocess.Popen) -> None:
        if process is None or process.poll() is not None:
            return
        try:
            process.terminate()
            process.wait(timeout=5)
        except Exception:
            try:
                process.kill()
            except Exception:
                pass


class ConversionRunner:
    """启动 ffmpeg，解析 -progress 输出，支持取消。"""

    def __init__(self, ffmpeg_path: str, args: List[str], duration_us: int = 0,
                 env: Optional[dict] = None, cwd: Optional[str] = None,
                 on_progress: Optional[Callable[[dict], None]] = None,
                 on_log: Optional[Callable[[str], None]] = None):
        self.ffmpeg_path = ffmpeg_path
        self.cwd = cwd
        self.duration_us = duration_us
        self.on_progress = on_progress
        self.on_log = on_log
        self.log: deque = deque(maxlen=400)
        self.returncode: Optional[int] = None
        self.cancelled = False
        self.error = ""
        # 不写进度文件，直接走 pipe
        self.args = ["-nostdin", "-progress", "pipe:1"] + args
        self._process: Optional[subprocess.Popen] = None
        self._env = env or os.environ.copy()
        self._progress = {"percent": 0, "time_us": 0, "speed": "", "fps": 0.0,
                          "size": 0, "bitrate": ""}
        self._last_emit = 0.0

    @property
    def progress(self) -> dict:
        return dict(self._progress)

    def start(self) -> None:
        self._process = subprocess.Popen(
            [self.ffmpeg_path] + self.args,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            stdin=subprocess.DEVNULL,
            env=self._env,
            cwd=self.cwd,
            creationflags=_creation_flags(),
        )
        threading.Thread(target=self._read_progress, daemon=True).start()
        threading.Thread(target=self._read_stderr, daemon=True).start()

    def _read_progress(self) -> None:
        handle = self._process.stdout if self._process else None
        if handle is None:
            return
        buffer: dict = {}
        try:
            for raw in handle:
                line = raw.decode("utf-8", "replace").strip()
                if not line:
                    continue
                if "=" not in line:
                    continue
                key, _, value = line.partition("=")
                buffer[key.strip()] = value.strip()
                if key.strip() == "progress":
                    self._emit(buffer)
                    buffer = {}
        except Exception:
            pass

    def _emit(self, data: dict) -> None:
        time_us = int(data.get("out_time_us") or 0)
        percent = 0
        if self.duration_us > 0 and time_us > 0:
            percent = min(99, int(time_us * 100 / self.duration_us))
        try:
            fps = float(data.get("fps") or 0)
        except ValueError:
            fps = 0.0
        self._progress = {
            "percent": percent,
            "time_us": time_us,
            "speed": data.get("speed", ""),
            "fps": fps,
            "size": int(data.get("total_size") or 0),
            "bitrate": data.get("bitrate", ""),
        }
        if data.get("progress") == "end":
            self._progress["percent"] = 100
        now = time.time()
        if self.on_progress and (now - self._last_emit > 0.2 or self._progress["percent"] >= 100):
            self._last_emit = now
            self.on_progress(self.progress)

    def _read_stderr(self) -> None:
        handle = self._process.stderr if self._process else None
        if handle is None:
            return
        try:
            for raw in handle:
                line = raw.decode("utf-8", "replace").rstrip()
                if not line:
                    continue
                self.log.append(line)
                if self.on_log:
                    self.on_log(line)
        except Exception:
            pass

    def wait(self, timeout: Optional[float] = None) -> int:
        if self._process is None:
            return -1
        try:
            self.returncode = self._process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            self.cancel()
            self.returncode = -1
        return self.returncode

    def cancel(self) -> None:
        self.cancelled = True
        process = self._process
        if process is None or process.poll() is not None:
            return
        try:
            process.terminate()
            process.wait(timeout=5)
        except Exception:
            try:
                process.kill()
            except Exception:
                pass

    def tail(self, count: int = 40) -> List[str]:
        return list(self.log)[-count:]
