"""ffmpeg 命令构造。对应 Android 版 FFmpegCommandBuilder.java，并扩展了 PC 显卡编码器与字幕样式。"""

from __future__ import annotations

import os
from typing import List, Optional, Sequence

SOFTWARE_ENCODER = "libx264"

# MP4 容器装不下的音轨，遇到时转为 AAC
MP4_UNSUPPORTED_AUDIO = {
    "dts", "dts_hd", "truehd", "mlp", "vorbis", "opus", "flac",
    "pcm_s16le", "pcm_s24le", "pcm_s32le", "pcm_f32le", "pcm_bluray", "s302m",
}


def escape_filter_path(path: str) -> str:
    """滤镜参数里的路径转义。

    在 ffmpeg 的滤镜表达式中，冒号用于分隔选项、逗号用于分隔滤镜，
    方括号和单引号也有特殊含义，因此都需要转义。
    """
    return (path.replace("\\", "\\\\")
                .replace(":", "\\\\:")
                .replace("'", "\\'")
                .replace(",", "\\,")
                .replace("[", "\\[")
                .replace("]", "\\]"))


def filter_filename(input_path: str) -> str:
    """字幕滤镜使用的文件名。

    Windows 下绝对路径带盘符冒号，在 ffmpeg 滤镜表达式里转义行为不稳定，
    因此统一只传文件名，并把子进程工作目录设为视频所在目录。
    """
    return escape_filter_path(os.path.basename(input_path))


def hardware_bit_rate(metadata, ratio: float = 1.5) -> int:
    """与 Android 版一致：像素下限与源码率取较大值。"""
    pixels = max(1, metadata.width * metadata.height)
    minimum = max(2_000_000, pixels * 4)
    source = metadata.bit_rate if metadata.bit_rate > 0 else 0
    source_with_headroom = int(source * ratio) if source > 0 else 0
    return max(minimum, source_with_headroom)


def _style_pairs(style: Optional[dict], margin: Optional[int] = None,
                 alignment: Optional[int] = None) -> List[str]:
    pairs: List[str] = []
    if not style:
        style = {}
    font_name = (style.get("font_name") or "").strip()
    if font_name:
        pairs.append(f"FontName={font_name}")
    font_size = int(style.get("font_size") or 0)
    if font_size > 0:
        pairs.append(f"Fontsize={font_size}")
    outline = style.get("outline")
    if outline not in (None, "", 0):
        pairs.append(f"Outline={outline}")
    colour = (style.get("primary_colour") or "").strip()
    if colour:
        pairs.append(f"PrimaryColour={colour}")
    if alignment:
        pairs.append(f"Alignment={alignment}")
    if margin:
        pairs.append(f"MarginV={margin}")
    return pairs


def build_subtitle_filter(path: str, subtitle_indexes: Sequence[int], video_height: int,
                          style: Optional[dict] = None, stack: bool = True,
                          sources: Optional[Sequence[str]] = None) -> str:
    """多字幕时从底部向上错位显示，与 Android 版同一套间距规则。

    sources 为预先导出的字幕文件列表，提供时不使用 si，直接引用文件。
    """
    if sources:
        entries: List[tuple] = [(None, source) for source in sources]
    else:
        entries = [(index, path) for index in subtitle_indexes]
    return build_text_chain(entries, video_height, style, stack)


def build_text_chain(entries: Sequence[tuple], video_height: int,
                     style: Optional[dict] = None, stack: bool = True) -> str:
    """把若干条文本字幕渲染成一条滤镜链（entries = [(si 或 None, 文件名), ...]）。"""
    entries = list(entries)
    if not entries:
        raise ValueError("至少需要选择一条字幕")

    multiple = len(entries) > 1
    base_margin = max(24, video_height // 18)
    margin_step = max(36, video_height // 12)

    chunks: List[str] = []
    for position, (index, source) in enumerate(entries):
        chunk = "subtitles=filename=" + filter_filename(source)
        if index is not None:
            chunk += f":si={index}"
        margin = None
        alignment = None
        if multiple and stack:
            margin = int((style or {}).get("margin_v") or 0) or (base_margin + position * margin_step)
            alignment = 2
        else:
            margin = int((style or {}).get("margin_v") or 0) or None
        pairs = _style_pairs(style, margin, alignment)
        if pairs:
            chunk += ":force_style=" + "\\,".join(pairs)
        chunks.append(chunk)
    return ",".join(chunks)


def encoder_video_args(encoder: str, metadata, config) -> List[str]:
    """返回编码器专属参数（不含 -c:v 本身和 -pix_fmt）。"""
    if encoder == SOFTWARE_ENCODER or encoder.startswith("libx26"):
        return ["-preset", str(config.get("preset", "slow")),
                "-crf", str(config.get("crf", 16))]
    bit_rate = hardware_bit_rate(metadata, float(config.get("hw_bitrate_ratio", 1.5)))
    if encoder == "h264_nvenc":
        return ["-preset", "p4", "-rc", "vbr", "-cq", "20",
                "-b:v", str(bit_rate),
                "-maxrate", str(bit_rate * 2),
                "-bufsize", str(bit_rate * 4)]
    if encoder == "h264_qsv":
        return ["-preset", "medium",
                "-b:v", str(bit_rate),
                "-maxrate", str(bit_rate * 2),
                "-bufsize", str(bit_rate * 4)]
    if encoder == "h264_amf":
        return ["-usage", "transcoding", "-quality", "balanced", "-rc", "vbr",
                "-b:v", str(bit_rate),
                "-maxrate", str(bit_rate * 2),
                "-bufsize", str(bit_rate * 4)]
    if encoder == "h264_videotoolbox":
        return ["-b:v", str(bit_rate), "-maxrate", str(bit_rate * 2)]
    # 未知编码器，退回固定码率写法
    return ["-b:v", str(bit_rate)]


def pick_audio_args(metadata, container: str) -> List[str]:
    """优先直接复制音轨；MP4 装不下的格式转 AAC 320k。"""
    if container == "mp4" and metadata.audio_codec in MP4_UNSUPPORTED_AUDIO:
        return ["-c:a", "aac", "-b:a", "320k"]
    return ["-c:a", "copy"]


def build_filter_graph(input_path: str, tracks: Sequence[dict], video_height: int,
                       style: Optional[dict] = None, stack: bool = True,
                       subtitle_sources: Optional[Sequence[str]] = None) -> str:
    """构造 filter_complex：文本字幕走 subtitles 滤镜，图片字幕走 overlay 叠加。

    tracks 每项为 {"kind": "text"|"image", "si": int, "stream_index": int}；
    si 是字幕流之间的相对序号，stream_index 是文件里的绝对流号。
    """
    text_entries: List[tuple] = []
    image_tracks: List[dict] = []
    source_queue = list(subtitle_sources or [])
    for track in tracks:
        if track.get("kind") == "image":
            image_tracks.append(track)
        elif source_queue:
            text_entries.append((None, source_queue.pop(0)))
        else:
            text_entries.append((int(track.get("si", 0)), input_path))
    if not text_entries and not image_tracks:
        raise ValueError("至少需要选择一条字幕")

    parts: List[str] = []
    current = "0:v:0"
    if text_entries:
        chain = build_text_chain(text_entries, video_height, style, stack)
        next_label = "vimg0" if image_tracks else "vout"
        parts.append(f"[{current}]{chain}[{next_label}]")
        current = next_label
    for index, track in enumerate(image_tracks):
        next_label = "vout" if index == len(image_tracks) - 1 else f"vimg{index + 1}"
        parts.append(f"[{current}][0:s:{int(track['stream_index'])}]overlay[{next_label}]")
        current = next_label
    return ";".join(parts)


def build(input_path: str, output_path: str, subtitle_indexes: Sequence[int],
          metadata, config: dict, encoder: str = SOFTWARE_ENCODER,
          subtitle_sources: Optional[Sequence[str]] = None,
          tracks: Optional[Sequence[dict]] = None) -> List[str]:
    container = (config.get("container") or "mkv").lower()
    style = config.get("style") or {}
    stack = bool(config.get("stack_subtitles", True))

    if tracks is None:  # 兼容：只有序号时按文本字幕处理
        tracks = [{"kind": "text", "si": int(index), "stream_index": int(index)}
                  for index in subtitle_indexes]
    graph = build_filter_graph(input_path, tracks, metadata.height, style, stack,
                               subtitle_sources)
    has_image = any(track.get("kind") == "image" for track in tracks)

    args: List[str] = ["-hide_banner", "-loglevel", "warning", "-y"]
    if has_image:
        args += ["-fix_sub_duration"]  # DVDSUB/PGS 常有无限时长的时间戳
    args += [
        "-i", input_path,
        "-filter_complex", graph,
        "-map", "[vout]", "-map", "0:a?",
        "-map_metadata", "0", "-map_chapters", "0",
        "-c:v", encoder,
    ]
    args += encoder_video_args(encoder, metadata, config)
    args += ["-pix_fmt", "yuv420p"]
    args += pick_audio_args(metadata, container)
    args += ["-max_muxing_queue_size", "1024"]
    if container == "mp4":
        args += ["-movflags", "+faststart"]
    args.append(output_path)
    return args
