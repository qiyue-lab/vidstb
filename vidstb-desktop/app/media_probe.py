"""ffprobe 解析。对应 Android 版 MediaProbe.java。"""

from __future__ import annotations

import json
import os
from typing import List

from .ffmpeg_tool import Ffmpeg
from .models import ProbeResult, SubtitleTrack, VideoMetadata

STILL_IMAGE_FORMATS = {
    "image2", "jpeg_pipe", "jpg_pipe", "png_pipe", "webp_pipe", "gif",
    "bmp_pipe", "tiff_pipe", "svg_pipe", "heic", "avif",
}

FONT_CODECS = {"ttf", "otf", "woff", "woff2", "truetype", "opentype"}

PROBE_ARGS = [
    "-v", "error",
    "-show_entries",
    "stream=index,codec_name,codec_type,width,height,bit_rate,avg_frame_rate,r_frame_rate:"
    "stream_tags=language,title,filename,mimetype:stream_disposition=default,forced:"
    "stream_side_data=rotation:format=duration,bit_rate,format_name",
    "-of", "json",
]


def is_still_image_container(format_name: str) -> bool:
    if not format_name:
        return False
    for item in format_name.lower().split(","):
        if item.strip() in STILL_IMAGE_FORMATS:
            return True
    return False


def _parse_frame_rate(value: str) -> float:
    if not value or value.startswith("0/"):
        return 0.0
    try:
        if "/" in value:
            num, den = value.split("/", 1)
            den_v = float(den)
            return 0.0 if den_v == 0 else float(num) / den_v
        return float(value)
    except ValueError:
        return 0.0


def _parse_long(value) -> int:
    try:
        return int(str(value))
    except (TypeError, ValueError):
        return 0


def _parse_float(value) -> float:
    try:
        return float(str(value))
    except (TypeError, ValueError):
        return 0.0


def probe(ffmpeg_tool: Ffmpeg, path: str) -> ProbeResult:
    if not os.path.isfile(path):
        return ProbeResult(path, VideoMetadata(), error="文件不存在")

    raw = ffmpeg_tool.run_ffprobe(PROBE_ARGS + [path], timeout=90)
    try:
        root = json.loads(raw or "{}")
    except ValueError:
        return ProbeResult(path, VideoMetadata(), error="无法解析 ffprobe 输出")
    return parse_probe_json(root, path)


def parse_probe_json(root: dict, path: str = "") -> ProbeResult:
    """解析 ffprobe 的 JSON 输出，对应 Android 版 MediaProbe.parse(String)。"""
    streams = root.get("streams") or []
    subtitles: List[SubtitleTrack] = []
    fonts = []
    width = height = 0
    video_bit_rate = 0
    subtitle_index = 0
    frame_rate = 0.0
    has_audio = False
    rotation = 0
    audio_codec = ""

    for stream in streams:
        codec_type = stream.get("codec_type")
        codec_name = (stream.get("codec_name") or "").lower()
        if codec_type == "video" and width == 0:
            width = int(stream.get("width") or 0)
            height = int(stream.get("height") or 0)
            video_bit_rate = _parse_long(stream.get("bit_rate"))
            frame_rate = _parse_frame_rate(stream.get("avg_frame_rate") or "")
            if frame_rate <= 0:
                frame_rate = _parse_frame_rate(stream.get("r_frame_rate") or "")
            for data in stream.get("side_data_list") or []:
                if "rotation" in data:
                    rotation = int(_parse_float(data.get("rotation")))
        elif codec_type == "audio":
            if not has_audio:
                audio_codec = codec_name
            has_audio = True
        elif codec_type == "subtitle":
            tags = stream.get("tags") or {}
            disposition = stream.get("disposition") or {}
            subtitles.append(SubtitleTrack(
                stream_index=int(stream.get("index") or 0),
                subtitle_index=subtitle_index,
                codec_name=codec_name,
                language=tags.get("language", ""),
                title=tags.get("title", ""),
                default_track=int(disposition.get("default", 0)) == 1,
                forced=int(disposition.get("forced", 0)) == 1,
            ))
            subtitle_index += 1
        elif codec_type == "attachment":
            tags = stream.get("tags") or {}
            filename = tags.get("filename", "")
            mime = (tags.get("mimetype") or "").lower()
            if codec_name in FONT_CODECS or mime.startswith("font") \
                    or filename.lower().endswith((".ttf", ".otf", ".ttc", ".woff", ".woff2")):
                fonts.append({
                    "stream_index": int(stream.get("index") or 0),
                    "filename": filename,
                    "codec_name": codec_name,
                })

    if width <= 0 or height <= 0:
        return ProbeResult(path, VideoMetadata(), error="没有找到可处理的视频轨，所选文件可能不是视频")

    fmt = root.get("format") or {}
    format_name = fmt.get("format_name") or ""
    if is_still_image_container(format_name):
        return ProbeResult(path, VideoMetadata(), error="所选文件是图片，不是可编辑的视频")

    format_bit_rate = _parse_long(fmt.get("bit_rate"))
    duration_seconds = _parse_float(fmt.get("duration"))
    metadata = VideoMetadata(
        width=width,
        height=height,
        bit_rate=video_bit_rate if video_bit_rate > 0 else format_bit_rate,
        duration_us=int(duration_seconds * 1_000_000),
        frame_rate=frame_rate if frame_rate > 0 else 30.0,
        has_audio=has_audio,
        rotation=rotation,
        audio_codec=audio_codec,
        format_name=format_name,
    )
    return ProbeResult(path, metadata, subtitles, fonts)


def dump_font_attachments(ffmpeg_tool: Ffmpeg, path: str, attachments, target_dir: str,
                          on_process=None, should_stop=None) -> int:
    """把 MKV 内嵌字体导出到目录，返回成功数量。失败不影响主流程。

    所有附件用**一次** ffmpeg 调用导出：以前是每个字体跑一遍 ffmpeg，
    等于把整部片子重复解码 N 次（大文件会卡几十分钟）。
    这里改成 `-c copy`，只读一遍文件，实测 1.4GB 文件约 3 秒。
    """
    import re

    os.makedirs(target_dir, exist_ok=True)
    targets = []
    for position, attachment in enumerate(attachments or []):
        if should_stop and should_stop():
            return 0
        name = attachment.get("filename") or f"font_{attachment.get('stream_index')}.ttf"
        safe = re.sub(r'[\\/:*?"<>|]', "_", os.path.basename(name))
        if not safe.lower().endswith((".ttf", ".otf", ".ttc")):
            safe += ".ttf"
        out_path = os.path.join(target_dir, f"{position:02d}_{safe}")
        targets.append((attachment.get("stream_index"), out_path))
    if not targets:
        return 0

    args = ["-hide_banner", "-v", "error", "-y"]
    for stream_index, out_path in targets:
        args += [f"-dump_attachment:{stream_index}", out_path]
    args += ["-i", path, "-map", "0:v:0", "-c", "copy", "-f", "null", "-"]
    try:
        ffmpeg_tool.run_ffmpeg(args, timeout=300, on_process=on_process)
    except Exception:
        pass
    return sum(1 for _, out_path in targets
               if os.path.isfile(out_path) and os.path.getsize(out_path) > 0)
