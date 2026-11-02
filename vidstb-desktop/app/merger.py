"""多段视频合并为一条 4K 60fps 视频。

输出规格固定为 3840×2160 / 60fps：每段素材先统一缩放与帧率，
再用 concat 滤镜首尾相接。音轨统一重编码为 AAC 立体声，
缺失音轨的片段自动补等长静音，避免拼接时音画错位。
"""

from __future__ import annotations

import os
import tempfile
from dataclasses import dataclass
from typing import List, Optional, Sequence

from . import paths
from .command_builder import SOFTWARE_ENCODER

TARGET_WIDTH = 3840
TARGET_HEIGHT = 2160
TARGET_FPS = 60
AUDIO_SAMPLE_RATE = 48000
AUDIO_LAYOUT = "stereo"

FIT_PAD = "pad"
FIT_CROP = "crop"
FIT_STRETCH = "stretch"

FIT_LABELS = {
    FIT_PAD: "保持比例 + 黑边（不变形）",
    FIT_CROP: "裁切铺满（不变形，裁掉边缘）",
    FIT_STRETCH: "拉伸铺满（画面会变形）",
}

DEFAULT_BITRATE_MBPS = 50
DEFAULT_AUDIO_KBPS = 256
DEFAULT_PRESET = "fast"
DEFAULT_CONTAINER = "mp4"

# 容器索引 / 元数据带来的经验性开销
CONTAINER_OVERHEAD = 1.004

MPEG_OVERHEAD_HINT = "4K60 建议 40–80 Mbps；低于 30 Mbps 高速运动画面容易出现色块"


@dataclass
class MergeClip:
    """参与合并的一个片段。"""
    path: str
    metadata: Optional[object] = None
    has_audio: bool = True
    error: str = ""

    @property
    def name(self) -> str:
        return os.path.basename(self.path)

    @property
    def duration_us(self) -> int:
        return int(self.metadata.duration_us) if self.metadata else 0

    @property
    def width(self) -> int:
        return int(self.metadata.width) if self.metadata else 0

    @property
    def height(self) -> int:
        return int(self.metadata.height) if self.metadata else 0

    @property
    def frame_rate(self) -> float:
        return float(self.metadata.frame_rate) if self.metadata else 0.0

    def to_dict(self) -> dict:
        return {
            "path": self.path,
            "name": self.name,
            "duration_us": self.duration_us,
            "width": self.width,
            "height": self.height,
            "frame_rate": round(self.frame_rate, 3),
            "has_audio": self.has_audio,
            "error": self.error,
        }


def _as_float(value, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _as_int(value, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def settings(config: Optional[dict] = None) -> dict:
    """取出合并相关配置，缺项用默认值补齐。"""
    config = config or {}
    bitrate = _as_float(config.get("merge_bitrate_mbps"), DEFAULT_BITRATE_MBPS)
    bitrate = min(500.0, max(1.0, bitrate))
    audio = _as_int(config.get("merge_audio_bitrate_kbps"), DEFAULT_AUDIO_KBPS)
    audio = min(640, max(32, audio))
    fit = str(config.get("merge_fit") or FIT_PAD)
    if fit not in FIT_LABELS:
        fit = FIT_PAD
    container = str(config.get("merge_container") or DEFAULT_CONTAINER).lower()
    if container not in ("mp4", "mkv", "mov"):
        container = DEFAULT_CONTAINER
    return {
        "bitrate_mbps": bitrate,
        "audio_kbps": audio,
        "fit": fit,
        "container": container,
        "preset": str(config.get("merge_preset") or DEFAULT_PRESET),
        "width": TARGET_WIDTH,
        "height": TARGET_HEIGHT,
        "fps": TARGET_FPS,
    }


def fit_chain(mode: str) -> str:
    """把任意比例的画面变成 3840×2160 的缩放/填充链。"""
    width, height = TARGET_WIDTH, TARGET_HEIGHT
    if mode == FIT_CROP:
        return (f"scale=w={width}:h={height}:force_original_aspect_ratio=increase:flags=bicubic,"
                f"crop=w={width}:h={height}")
    if mode == FIT_STRETCH:
        return f"scale=w={width}:h={height}:flags=bicubic"
    return (f"scale=w={width}:h={height}:force_original_aspect_ratio=decrease:flags=bicubic,"
            f"pad=w={width}:h={height}:x=(ow-iw)/2:y=(oh-ih)/2:color=black")


def video_chain(index: int, mode: str) -> str:
    return (f"[{index}:v:0]{fit_chain(mode)},"
            f"fps={TARGET_FPS},setsar=1,format=yuv420p[v{index}]")


def audio_chain(index: int, has_audio: bool, duration_seconds: float) -> str:
    normalise = ("aformat=sample_fmts=fltp:sample_rates=%d:channel_layouts=%s"
                 % (AUDIO_SAMPLE_RATE, AUDIO_LAYOUT))
    if has_audio:
        return (f"[{index}:a:0]{normalise},"
                f"aresample=async=1:first_pts=0[a{index}]")
    # 无音轨片段补等长静音，保证 concat 时各段音频参数一致
    return (f"anullsrc=r={AUDIO_SAMPLE_RATE}:cl={AUDIO_LAYOUT},"
            f"atrim=duration={max(0.0, duration_seconds):.3f},{normalise}[a{index}]")


def build_filter_graph(clips: Sequence[MergeClip], mode: str,
                       with_audio: bool) -> str:
    count = len(clips)
    if count == 0:
        raise ValueError("至少需要一段视频")

    parts: List[str] = [video_chain(index, mode) for index in range(count)]
    if with_audio:
        parts += [audio_chain(index, clip.has_audio,
                              clip.duration_us / 1_000_000.0)
                  for index, clip in enumerate(clips)]

    if count == 1:
        # 单段时 concat 无意义，直接把滤镜链输出改名即可
        parts[0] = parts[0].replace("[v0]", "[vout]")
        if with_audio:
            parts[1] = parts[1].replace("[a0]", "[aout]")
            return ";".join(parts[:2])
        return parts[0]
    parts.append("".join(f"[v{i}]" for i in range(count))
                 + f"concat=n={count}:v=1:a=0[vout]")
    if with_audio:
        parts.append("".join(f"[a{i}]" for i in range(count))
                     + f"concat=n={count}:v=0:a=1[aout]")
    return ";".join(parts)


def has_any_audio(clips: Sequence[MergeClip]) -> bool:
    return any(clip.has_audio for clip in clips)


def encoder_video_args(encoder: str, bitrate_mbps: float, preset: str) -> List[str]:
    bps = int(round(bitrate_mbps * 1_000_000))
    if encoder == SOFTWARE_ENCODER or encoder.startswith("libx26"):
        return ["-preset", preset,
                "-b:v", str(bps),
                "-maxrate", str(int(bps * 1.5)),
                "-bufsize", str(bps * 2)]
    if encoder == "h264_nvenc":
        return ["-preset", "p4", "-rc", "vbr", "-cq", "20",
                "-b:v", str(bps),
                "-maxrate", str(bps * 2),
                "-bufsize", str(bps * 4)]
    if encoder == "h264_qsv":
        return ["-preset", "medium",
                "-b:v", str(bps),
                "-maxrate", str(bps * 2),
                "-bufsize", str(bps * 4)]
    if encoder == "h264_amf":
        return ["-usage", "transcoding", "-quality", "balanced", "-rc", "vbr",
                "-b:v", str(bps),
                "-maxrate", str(bps * 2),
                "-bufsize", str(bps * 4)]
    if encoder == "h264_videotoolbox":
        return ["-b:v", str(bps), "-maxrate", str(bps * 2)]
    return ["-b:v", str(bps)]


def build(clips: Sequence[MergeClip], output_path: str, config: dict,
          encoder: str = SOFTWARE_ENCODER) -> List[str]:
    """生成合并命令。输出恒为 3840×2160 / 60fps。"""
    if not clips:
        raise ValueError("至少需要一段视频")
    params = settings(config)
    with_audio = has_any_audio(clips)

    args: List[str] = ["-hide_banner", "-loglevel", "warning", "-y"]
    for clip in clips:
        args += ["-i", clip.path]
    args += ["-filter_complex", build_filter_graph(clips, params["fit"], with_audio),
             "-map", "[vout]"]
    if with_audio:
        args += ["-map", "[aout]"]
    args += ["-c:v", encoder]
    args += encoder_video_args(encoder, params["bitrate_mbps"], params["preset"])
    args += ["-pix_fmt", "yuv420p", "-r", str(TARGET_FPS)]
    if with_audio:
        args += ["-c:a", "aac", "-b:a", f"{params['audio_kbps']}k",
                 "-ar", str(AUDIO_SAMPLE_RATE), "-ac", "2"]
    args += ["-max_muxing_queue_size", "1024"]
    if params["container"] == "mp4":
        args += ["-movflags", "+faststart"]
    args.append(output_path)
    return args


def sample_rate(ffmpeg_tool, clip: "MergeClip", config: dict, encoder: str,
                seconds: float = 2.0) -> Optional[float]:
    """对单个片段抽中间一小段真编码，返回实测的「字节/秒」。

    CQ / VBR 编码会按画面复杂度自适应地省码率，纯靠目标码率算出来的
    体积往往明显偏大，因此需要抽样实测再按各自时长加权外推。
    """
    params = settings(config)
    duration = clip.duration_us / 1_000_000.0
    if duration <= 0:
        return None
    take = min(seconds, duration)
    start = max(0.0, duration / 2.0 - take / 2.0)

    handle = tempfile.NamedTemporaryFile(prefix="vidstb-sample-", suffix=".mkv",
                                         dir=paths.temp_dir(), delete=False)
    handle.close()
    args = ["-hide_banner", "-loglevel", "error", "-y",
            "-ss", f"{start:.3f}", "-i", clip.path, "-t", f"{take:.3f}",
            "-vf", fit_chain(params["fit"]) + f",fps={TARGET_FPS},setsar=1,format=yuv420p",
            "-c:v", encoder]
    args += encoder_video_args(encoder, params["bitrate_mbps"], params["preset"])
    args += ["-pix_fmt", "yuv420p", "-an", handle.name]
    try:
        ffmpeg_tool.run_ffmpeg(args, timeout=900)
        size = os.path.getsize(handle.name) if os.path.isfile(handle.name) else 0
    except Exception:
        return None
    finally:
        try:
            os.remove(handle.name)
        except OSError:
            pass
    if size <= 0:
        return None
    return size / take


def estimate_from_samples(clips: Sequence["MergeClip"], rates: Sequence[float],
                          config: dict) -> dict:
    """用实测出的每段码率（字节/秒）推算总体积。"""
    params = settings(config)
    usable = [clip for clip in clips if not clip.error]
    total_us = sum(clip.duration_us for clip in usable)
    seconds = total_us / 1_000_000.0
    video_bytes = 0.0
    for clip, rate in zip(usable, rates):
        video_bytes += rate * (clip.duration_us / 1_000_000.0)
    audio_bps = params["audio_kbps"] * 1000 if has_any_audio(usable) else 0
    audio_bytes = audio_bps * seconds / 8
    total_bytes = int((video_bytes + audio_bytes) * CONTAINER_OVERHEAD)

    measured_mbps = 0.0
    if seconds > 0:
        measured_mbps = round(video_bytes * 8 / seconds / 1_000_000, 1)
    return {
        "basis": "measured",
        "estimated_bytes": total_bytes,
        "video_bytes": int(video_bytes),
        "audio_bytes": int(audio_bytes),
        "total_duration_us": total_us,
        "total_seconds": round(seconds, 3),
        "measured_bitrate_mbps": measured_mbps,
        "target_bitrate_mbps": params["bitrate_mbps"],
        "with_audio": bool(audio_bps),
        "sample_count": len(usable),
        "output": {
            "width": TARGET_WIDTH,
            "height": TARGET_HEIGHT,
            "fps": TARGET_FPS,
            "container": params["container"],
            "fit": params["fit"],
            "fit_label": FIT_LABELS[params["fit"]],
        },
    }


def estimate(clips: Sequence[MergeClip], config: dict) -> dict:
    """合并前预估输出文件大小。

    采用固定目标码率编码，因此 时长 × 码率 的估算相当接近实际值，
    误差主要来自 VBR 的实际码率波动与容器开销（按经验加 0.4%）。
    """
    params = settings(config)
    total_us = sum(clip.duration_us for clip in clips)
    seconds = total_us / 1_000_000.0
    video_bps = params["bitrate_mbps"] * 1_000_000
    audio_bps = params["audio_kbps"] * 1000 if has_any_audio(clips) else 0
    total_bytes = int((video_bps + audio_bps) * seconds / 8 * CONTAINER_OVERHEAD)

    clips_info = []
    for clip in clips:
        info = clip.to_dict()
        info["upscaled"] = clip.width < TARGET_WIDTH or clip.height < TARGET_HEIGHT
        info["fps_changed"] = abs(clip.frame_rate - TARGET_FPS) > 0.5
        clips_info.append(info)

    return {
        "clips": clips_info,
        "basis": "target",          # target = 按目标码率上限，measured = 抽样实测
        "total_duration_us": total_us,
        "total_seconds": round(seconds, 3),
        "estimated_bytes": total_bytes,
        "video_bytes": int(video_bps * seconds / 8),
        "audio_bytes": int(audio_bps * seconds / 8),
        "with_audio": has_any_audio(clips),
        "bitrate_mbps": params["bitrate_mbps"],
        "audio_kbps": params["audio_kbps"],
        "output": {
            "width": TARGET_WIDTH,
            "height": TARGET_HEIGHT,
            "fps": TARGET_FPS,
            "container": params["container"],
            "fit": params["fit"],
            "fit_label": FIT_LABELS[params["fit"]],
        },
    }
