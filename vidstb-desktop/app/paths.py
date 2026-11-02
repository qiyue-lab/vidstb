"""临时目录与路径管理。

默认把运行时临时文件放在项目目录下的 runtime/temp，而不是系统盘（Windows 的
C:\\Users\\...\\AppData\\Local\\Temp）。处理视频时临时文件可能很大：导出的字体附件
几十 MB，合并功能的抽样实测会真编码一段 4K 视频，放在系统盘既占空间又拖慢系统。

优先级：config.temp_dir > 环境变量 VIDSTB_TEMP_DIR > 项目 runtime/temp > 系统临时目录。
"""

from __future__ import annotations

import os
import shutil
import sys
import tempfile
import time
from typing import List, Optional

# 打包成 exe 后项目根目录的标记（有其一就认为是项目根）
_MARKERS = ("web", "runtime", "config.json", "server.py")


def _find_project_root(start: str, levels: int = 4) -> str:
    """从 start 往上找项目根目录，找不到就原样返回 start。"""
    current = os.path.abspath(start)
    for _ in range(levels):
        if any(os.path.exists(os.path.join(current, name)) for name in _MARKERS):
            return current
        parent = os.path.dirname(current)
        if parent == current:
            break
        current = parent
    return os.path.abspath(start)


def base_dir() -> str:
    """项目根目录（vidstb-desktop）。

    正常运行时是 app/ 的上一级；打包成 exe 后 __file__ 指向的是临时解压目录，
    那里没有 config.json / runtime/，所以要按 exe 的位置往上找真正的目录，
    保证 exe 和 `python server.py` 用的是同一份配置和资源数据。

    环境变量 VIDSTB_HOME 优先级最高（不管是不是 exe）——测试、多开实例都要靠它
    把数据指到别处，否则会把真实配置和资源改掉。
    """
    env = (os.environ.get("VIDSTB_HOME") or "").strip()
    if env:
        return os.path.abspath(env)
    if getattr(sys, "frozen", False):
        return _find_project_root(os.path.dirname(os.path.abspath(sys.executable)))
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


# 项目根目录（vidstb-desktop）
BASE_DIR = base_dir()

# 项目内的默认临时目录
DEFAULT_TEMP_DIR = os.path.join(BASE_DIR, "runtime", "temp")

# 超过这个时间没被改动的临时内容会被清理，避免长期堆积
STALE_SECONDS = 48 * 3600

_configured: Optional[str] = None


def set_temp_dir(path: Optional[str]) -> None:
    """由配置指定临时目录；传空字符串或 None 表示恢复默认。"""
    global _configured
    _configured = (path or "").strip() or None


def candidates() -> List[str]:
    """按优先级列出可选的临时目录。"""
    result: List[str] = []
    if _configured:
        result.append(_configured)
    env = os.environ.get("VIDSTB_TEMP_DIR")
    if env:
        result.append(env)
    result.append(DEFAULT_TEMP_DIR)
    system = tempfile.gettempdir()
    if system not in result:
        result.append(system)
    return result


def temp_dir() -> str:
    """取一个可用的临时目录，取不到就退回系统临时目录。"""
    for path in candidates():
        try:
            os.makedirs(path, exist_ok=True)
            # 确认真的可写
            probe = os.path.join(path, ".vidstb-write-test")
            with open(probe, "w", encoding="utf-8") as handle:
                handle.write("1")
            os.remove(probe)
            return path
        except OSError:
            continue
    return tempfile.gettempdir()


def make_temp_dir(prefix: str = "vidstb-") -> str:
    """在受管临时目录下建一个子目录。"""
    return tempfile.mkdtemp(prefix=prefix, dir=temp_dir())


def clean_stale(max_age_seconds: int = STALE_SECONDS) -> int:
    """清理受管临时目录里过期的条目，返回删除数量。

    只在启动阶段调用；正在使用的文件删不掉会被 OSError 捕获并跳过。

    注意：只清理「当前生效的受管目录」。绝不能去动系统临时目录——那里
    放着其他程序的文件，删了会造成难以排查的问题。
    """
    removed = 0
    deadline = time.time() - max_age_seconds
    for root in [temp_dir()]:
        if not os.path.isdir(root):
            continue
        try:
            entries = list(os.scandir(root))
        except OSError:
            continue
        for entry in entries:
            try:
                age = entry.stat().st_mtime
            except OSError:
                continue
            if age > deadline:
                continue
            try:
                if entry.is_dir(follow_symlinks=False):
                    shutil.rmtree(entry.path, ignore_errors=True)
                else:
                    os.remove(entry.path)
                removed += 1
            except OSError:
                continue
    return removed
