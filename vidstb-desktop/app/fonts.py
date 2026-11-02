"""fontconfig 配置。对应 Android 版 SubtitleFonts.java，改为使用系统字体目录。"""

from __future__ import annotations

import os
import sys
from typing import Optional

from . import paths

IS_WINDOWS = sys.platform.startswith("win")
IS_MAC = sys.platform == "darwin"


def _font_dirs() -> list:
    if IS_WINDOWS:
        dirs = [r"C:/Windows/Fonts"]
        local = os.environ.get("LOCALAPPDATA")
        if local:
            dirs.append(os.path.join(local, "Microsoft", "Windows", "Fonts").replace("\\", "/"))
        user_fonts = os.path.join(os.path.expanduser("~"), "AppData", "Local",
                                  "Microsoft", "Windows", "Fonts")
        dirs.append(user_fonts.replace("\\", "/"))
        return dirs
    if IS_MAC:
        return ["/System/Library/Fonts", "/Library/Fonts",
                os.path.expanduser("~/Library/Fonts")]
    return ["/usr/share/fonts", "/usr/local/share/fonts",
            os.path.expanduser("~/.local/share/fonts"),
            os.path.expanduser("~/.fonts")]


def _escape_xml(value: str) -> str:
    return (value.replace("&", "&amp;")
                 .replace("<", "&lt;")
                 .replace(">", "&gt;")
                 .replace('"', "&quot;"))


def configure(extra_dirs: Optional[list] = None) -> dict:
    """生成 fonts.conf，返回需要注入子进程的环境变量。"""
    base = paths.temp_dir()
    cache_dir = os.path.join(base, "vidstb-fontconfig")
    config_dir = os.path.join(base, "vidstb-fontconfig-conf")
    try:
        os.makedirs(cache_dir, exist_ok=True)
        os.makedirs(config_dir, exist_ok=True)
    except OSError:
        return {}

    candidates = [d.replace("\\", "/") for d in _font_dirs()]
    if extra_dirs:
        candidates += [d.replace("\\", "/") for d in extra_dirs]
    dirs = "\n".join(f"  <dir>{_escape_xml(d)}</dir>" for d in candidates if os.path.isdir(d))
    xml = (
        '<?xml version="1.0"?>\n'
        '<!DOCTYPE fontconfig SYSTEM "fonts.dtd">\n'
        '<fontconfig>\n'
        f"{dirs}\n"
        f"  <cachedir>{_escape_xml(cache_dir)}</cachedir>\n"
        '  <alias><family>Arial</family><prefer><family>sans-serif</family></prefer></alias>\n'
        '  <alias><family>sans-serif</family><prefer>'
        '<family>Microsoft YaHei</family><family>Noto Sans CJK SC</family>'
        '<family>Arial</family></prefer></alias>\n'
        '</fontconfig>\n'
    )
    config_path = os.path.join(config_dir, "fonts.conf")
    try:
        with open(config_path, "w", encoding="utf-8") as handle:
            handle.write(xml)
    except OSError:
        return {}

    env = os.environ.copy()
    env["FONTCONFIG_FILE"] = config_path
    env["FONTCONFIG_PATH"] = config_dir
    return env
