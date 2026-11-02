"""数据模型：与 Android 版 VideoMetadata / SubtitleTrack / VideoSegment 对齐。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional

# 文本字幕：libass 直接渲染，可改字体/字号/颜色
TEXT_SUBTITLE_CODECS = {
    "subrip",
    "srt",
    "ass",
    "ssa",
    "mov_text",
    "webvtt",
    "text",
}

# 图片字幕：位图，用 overlay 直接叠加烧录，无需 OCR，但样式改不了
OVERLAY_SUBTITLE_CODECS = {
    "hdmv_pgs_bluray",
    "pgssub",
    "dvd_subtitle",
    "dvb_subtitle",
    "xsub",
}

# 闭路字幕 / 图文电视：ffmpeg 没有可直接叠加的解码输出，暂不支持
UNSUPPORTED_SUBTITLE_CODECS = {
    "dvb_teletext",
    "eia_608",
    "eia_708",
}

SUPPORTED_SUBTITLE_CODECS = TEXT_SUBTITLE_CODECS | OVERLAY_SUBTITLE_CODECS
IMAGE_SUBTITLE_HINTS = OVERLAY_SUBTITLE_CODECS  # 兼容旧引用


@dataclass
class VideoMetadata:
    width: int = 0
    height: int = 0
    bit_rate: int = 0
    duration_us: int = 0
    frame_rate: float = 30.0
    has_audio: bool = True
    rotation: int = 0
    audio_codec: str = ""
    format_name: str = ""

    @property
    def duration_seconds(self) -> float:
        return self.duration_us / 1_000_000.0

    def to_dict(self) -> dict:
        return {
            "width": self.width,
            "height": self.height,
            "bit_rate": self.bit_rate,
            "duration_us": self.duration_us,
            "frame_rate": round(self.frame_rate, 3),
            "has_audio": self.has_audio,
            "rotation": self.rotation,
            "audio_codec": self.audio_codec,
            "format_name": self.format_name,
        }


@dataclass
class SubtitleTrack:
    stream_index: int          # 文件中的绝对流索引
    subtitle_index: int        # 字幕流之间的相对索引（ffmpeg subtitles 滤镜的 si 参数）
    codec_name: str
    language: str = ""
    title: str = ""
    default_track: bool = False
    forced: bool = False

    def is_text(self) -> bool:
        return self.codec_name.lower() in TEXT_SUBTITLE_CODECS

    def is_image(self) -> bool:
        """图片字幕（PGS / DVDSUB 等），用 overlay 直接叠加烧录。"""
        return self.codec_name.lower() in OVERLAY_SUBTITLE_CODECS

    def is_supported(self) -> bool:
        return self.codec_name.lower() in SUPPORTED_SUBTITLE_CODECS

    def display_name(self) -> str:
        label = self.language or "未知语言"
        if self.title:
            label += f" · {self.title}"
        label += f" · {(self.codec_name or '未知格式').upper()}"
        if self.default_track:
            label += " · 默认"
        if self.forced:
            label += " · 强制"
        if self.is_image():
            label += " · 图片字幕（叠加烧录，样式不可改）"
        elif not self.is_supported():
            label += "（暂不支持：闭路字幕需先转成 SRT/ASS）"
        return label

    def to_dict(self) -> dict:
        return {
            "stream_index": self.stream_index,
            "subtitle_index": self.subtitle_index,
            "codec_name": self.codec_name,
            "language": self.language,
            "title": self.title,
            "default": self.default_track,
            "forced": self.forced,
            "supported": self.is_supported(),
            "display_name": self.display_name(),
        }


@dataclass
class ProbeResult:
    path: str
    metadata: VideoMetadata
    subtitles: List[SubtitleTrack] = field(default_factory=list)
    font_attachments: List[dict] = field(default_factory=list)
    error: Optional[str] = None

    def to_dict(self) -> dict:
        return {
            "path": self.path,
            "metadata": self.metadata.to_dict(),
            "subtitles": [s.to_dict() for s in self.subtitles],
            "font_attachments": self.font_attachments,
            "error": self.error,
        }
