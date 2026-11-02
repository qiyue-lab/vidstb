"""显卡编码器检测与冒烟测试。"""

from __future__ import annotations

import subprocess
import sys
from typing import List

from .ffmpeg_tool import Ffmpeg

IS_WINDOWS = sys.platform.startswith("win")

GPU_ENCODERS = [
    {"id": "nvenc", "label": "NVIDIA NVENC", "encoder": "h264_nvenc"},
    {"id": "qsv", "label": "Intel Quick Sync Video", "encoder": "h264_qsv"},
    {"id": "amf", "label": "AMD AMF", "encoder": "h264_amf"},
    {"id": "videotoolbox", "label": "Apple VideoToolbox", "encoder": "h264_videotoolbox"},
]


def gpu_names() -> List[str]:
    """尽力获取显卡名称，拿不到就返回空列表。"""
    names: List[str] = []
    commands = [
        ["nvidia-smi", "--query-gpu=name", "--format=csv,noheader"],
    ]
    if IS_WINDOWS:
        commands.append(["powershell", "-NoProfile", "-Command",
                         "Get-CimInstance Win32_VideoController | "
                         "Select-Object -ExpandProperty Name"])
    for command in commands:
        try:
            result = subprocess.run(
                command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=20,
                creationflags=subprocess.CREATE_NO_WINDOW if IS_WINDOWS else 0,
            )
        except Exception:
            continue
        if result.returncode != 0:
            continue
        text = result.stdout.decode("utf-8", "replace")
        for line in text.splitlines():
            line = line.strip()
            if line and line not in names:
                names.append(line)
    return names


def smoke_test(ffmpeg_tool: Ffmpeg, encoder: str, bit_rate: int = 4_000_000) -> tuple:
    """真正编码几帧验证编码器可用，返回 (是否可用, 说明)。"""
    if encoder == "h264_nvenc":
        extra = ["-preset", "p4", "-rc", "vbr", "-cq", "20", "-b:v", str(bit_rate)]
    elif encoder == "h264_qsv":
        extra = ["-preset", "medium", "-b:v", str(bit_rate)]
    elif encoder == "h264_amf":
        extra = ["-usage", "transcoding", "-quality", "balanced", "-b:v", str(bit_rate)]
    elif encoder == "h264_videotoolbox":
        extra = ["-b:v", str(bit_rate)]
    else:
        extra = ["-b:v", str(bit_rate)]

    args = ["-hide_banner", "-loglevel", "error", "-y",
            "-f", "lavfi", "-i", "color=c=black:s=320x240:d=0.08:r=25",
            "-c:v", encoder] + extra + ["-pix_fmt", "yuv420p", "-f", "null", "-"]
    try:
        code, _out, err = ffmpeg_tool.run(ffmpeg_tool.ffmpeg_path, args, timeout=90)
    except subprocess.TimeoutExpired:
        return False, "编码测试超时"
    except Exception as error:  # pragma: no cover
        return False, f"编码测试异常：{error}"
    if code == 0:
        return True, "可用"
    return False, (err.strip().splitlines() or ["编码器初始化失败"])[-1][:300]


def detect(ffmpeg_tool: Ffmpeg, run_smoke_test: bool = True) -> List[dict]:
    """返回每个显卡编码器的可用状态。"""
    if not ffmpeg_tool.available:
        return [dict(item, available=False, reason="ffmpeg 不可用") for item in GPU_ENCODERS]

    supported = set(ffmpeg_tool.list_encoders("h264"))
    results: List[dict] = []
    for item in GPU_ENCODERS:
        entry = dict(item)
        if item["encoder"] not in supported:
            entry.update(available=False, reason="当前 ffmpeg 未编译该编码器")
            results.append(entry)
            continue
        if run_smoke_test:
            ok, reason = smoke_test(ffmpeg_tool, item["encoder"])
            entry.update(available=ok, reason=reason)
        else:
            entry.update(available=True, reason="已在编码器列表中")
        results.append(entry)
    return results


def choose_encoder(ffmpeg_tool: Ffmpeg, config: dict, hardware_info: List[dict] = None) -> str:
    """按配置挑一个编码器；返回 libx264 表示走软件编码。"""
    mode = (config.get("encoder_mode") or "auto").lower()
    if mode == "software":
        return "libx264"
    if hardware_info is None:
        hardware_info = detect(ffmpeg_tool)
    usable = [item for item in hardware_info if item.get("available")]
    if not usable:
        return "libx264"
    preferred = (config.get("hw_encoder") or "auto").lower()
    if preferred not in ("", "auto"):
        for item in usable:
            if item["id"] == preferred:
                return item["encoder"]
        return "libx264"
    # 优先 NVENC，其次 QSV / AMF
    order = {"nvenc": 0, "qsv": 1, "amf": 2, "videotoolbox": 3}
    usable.sort(key=lambda item: order.get(item["id"], 9))
    return usable[0]["encoder"]
