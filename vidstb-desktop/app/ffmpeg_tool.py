"""定位并调用本机的 ffmpeg / ffprobe。"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from typing import Callable, List, Optional, Tuple

IS_WINDOWS = sys.platform.startswith("win")

# 常见安装位置，按顺序尝试
COMMON_DIRS: List[str] = [
    r"D:\tools\ffmpeg\bin",
    r"C:\tools\ffmpeg\bin",
    r"C:\ffmpeg\bin",
    r"C:\Program Files\ffmpeg\bin",
    os.path.join(os.path.expanduser("~"), "ffmpeg", "bin"),
    "/usr/local/bin",
    "/usr/bin",
    "/opt/homebrew/bin",
]

_ENCODER_CACHE: dict = {}


def _exe_name(base: str) -> str:
    return base + (".exe" if IS_WINDOWS else "")


def _candidate_paths(base: str, configured_dir: str = "") -> List[str]:
    paths: List[str] = []
    if configured_dir:
        paths.append(os.path.join(configured_dir, _exe_name(base)))
    found = shutil.which(base)
    if found:
        paths.append(found)
    for directory in COMMON_DIRS:
        paths.append(os.path.join(directory, _exe_name(base)))
    return paths


def resolve(base: str, configured_dir: str = "") -> Optional[str]:
    """返回可用的可执行文件路径，找不到返回 None。"""
    for path in _candidate_paths(base, configured_dir):
        if path and os.path.isfile(path) and os.access(path, os.X_OK):
            return os.path.abspath(path)
    return None


class Ffmpeg:
    """对 ffmpeg / ffprobe 的轻量封装。"""

    def __init__(self, ffmpeg_dir: str = ""):
        self.configured_dir = ffmpeg_dir
        self.ffmpeg_path = resolve("ffmpeg", ffmpeg_dir)
        self.ffprobe_path = resolve("ffprobe", ffmpeg_dir)
        self.ffmpeg_version = ""
        self._error = ""
        if not self.ffmpeg_path or not self.ffprobe_path:
            self._error = "没有找到 ffmpeg / ffprobe，请在设置中指定目录，或运行 install_ffmpeg.py 安装。"
        else:
            self.ffmpeg_version = self._read_version()

    @property
    def available(self) -> bool:
        return bool(self.ffmpeg_path and self.ffprobe_path)

    @property
    def error(self) -> str:
        return self._error

    def _read_version(self) -> str:
        try:
            output = self.run_ffmpeg(["-hide_banner", "-version"], timeout=20)
        except Exception:
            return ""
        for line in output.splitlines():
            line = line.strip()
            if line.startswith("ffmpeg version"):
                parts = line.split()
                return parts[2] if len(parts) > 2 else line
        return ""

    def run(self, executable: str, args: List[str], timeout: int = 60,
            env: Optional[dict] = None,
            on_process: Optional[Callable] = None) -> Tuple[int, str, str]:
        # stdin 必须关闭：ffmpeg 默认会监听键盘输入（q 退出），继承管道时可能卡住。
        process = subprocess.Popen(
            [executable] + args,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            stdin=subprocess.DEVNULL,
            env=env,
            creationflags=subprocess.CREATE_NO_WINDOW if IS_WINDOWS else 0,
        )
        if on_process is not None:
            try:
                on_process(process)
            except Exception:
                pass
        try:
            out_raw, err_raw = process.communicate(timeout=timeout)
        except subprocess.TimeoutExpired:
            try:
                process.kill()
            except Exception:
                pass
            out_raw, err_raw = process.communicate()
            raise
        out = out_raw.decode("utf-8", "replace")
        err = err_raw.decode("utf-8", "replace")
        return process.returncode, out, err

    def run_ffmpeg(self, args: List[str], timeout: int = 60, env: Optional[dict] = None,
                   on_process: Optional[Callable] = None) -> str:
        if not self.ffmpeg_path:
            raise RuntimeError("ffmpeg 不可用")
        code, out, err = self.run(self.ffmpeg_path, args, timeout, env, on_process)
        return out + err

    def run_ffprobe(self, args: List[str], timeout: int = 60, env: Optional[dict] = None) -> str:
        if not self.ffprobe_path:
            raise RuntimeError("ffprobe 不可用")
        code, out, err = self.run(self.ffprobe_path, args, timeout, env)
        if code != 0 and not out:
            raise RuntimeError(err.strip() or "ffprobe 执行失败")
        return out

    def list_encoders(self, keyword: str = "h264") -> List[str]:
        cache_key = (self.ffmpeg_path, keyword)
        if cache_key in _ENCODER_CACHE:
            return _ENCODER_CACHE[cache_key]
        names: List[str] = []
        try:
            output = self.run_ffmpeg(["-hide_banner", "-encoders"], timeout=60)
        except Exception:
            _ENCODER_CACHE[cache_key] = names
            return names
        started = False
        for line in output.splitlines():
            stripped = line.strip()
            if stripped.startswith("------"):
                started = True
                continue
            if not started:
                continue
            parts = stripped.split()
            if len(parts) >= 2 and keyword in parts[1]:
                names.append(parts[1])
        _ENCODER_CACHE[cache_key] = names
        return names

    def has_encoder(self, name: str) -> bool:
        return name in self.list_encoders(name.split("_")[-1][:4] if "_" in name else "h264")

    def has_filter(self, name: str) -> bool:
        try:
            output = self.run_ffmpeg(["-hide_banner", "-filters"], timeout=60)
        except Exception:
            return False
        return f" {name} " in output or f"… {name} " in output or name in output

    def info(self) -> dict:
        encoders = self.list_encoders("h264")
        return {
            "available": self.available,
            "error": self._error,
            "ffmpeg_path": self.ffmpeg_path or "",
            "ffprobe_path": self.ffprobe_path or "",
            "version": self.ffmpeg_version,
            "h264_encoders": encoders,
        }
