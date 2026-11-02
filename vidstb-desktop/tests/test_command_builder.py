"""命令行构造测试，与 Android 版 FFmpegCommandBuilderTest / MediaProbeTest 对齐。"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import command_builder  # noqa: E402
from app.models import VideoMetadata  # noqa: E402

METADATA = VideoMetadata(1920, 1080, 8_000_000, 60_000_000)


class SoftwareModeTest(unittest.TestCase):
    def test_software_mode_uses_high_quality_without_scaling(self):
        args = command_builder.build("/tmp/in.mkv", "/tmp/out.mkv", [0], METADATA,
                                     {"container": "mkv", "crf": 16, "preset": "slow"},
                                     "libx264")
        self.assertIn("libx264", args)
        self.assertIn("16", args)
        self.assertIn("slow", args)
        self.assertIn("copy", args)
        self.assertNotIn("scale", args)
        self.assertNotIn("-r", args)
        self.assertNotIn("h264_nvenc", args)

    def test_pix_fmt_and_muxing_queue(self):
        args = command_builder.build("/tmp/in.mkv", "/tmp/out.mkv", [0], METADATA,
                                     {"container": "mkv"}, "libx264")
        self.assertIn("yuv420p", args)
        self.assertIn("1024", args)

    def test_rejects_empty_subtitle_selection(self):
        with self.assertRaises(ValueError):
            command_builder.build("/tmp/in.mkv", "/tmp/out.mkv", [], METADATA,
                                  {"container": "mkv"}, "libx264")


class HardwareModeTest(unittest.TestCase):
    def test_hardware_bitrate_uses_source_headroom(self):
        # 1920x1080 -> 像素下限 8,294,400；源码率 8M * 1.5 = 12M，取大者
        self.assertEqual(command_builder.hardware_bit_rate(METADATA, 1.5), 12_000_000)

    def test_hardware_bitrate_falls_back_to_pixel_floor(self):
        small = VideoMetadata(640, 360, 300_000, 10_000_000)
        self.assertEqual(command_builder.hardware_bit_rate(small, 1.5), 2_000_000)

    def test_nvenc_args_contain_quality_controls(self):
        args = command_builder.build("/tmp/in.mkv", "/tmp/out.mkv", [1], METADATA,
                                     {"container": "mkv", "hw_bitrate_ratio": 1.5},
                                     "h264_nvenc")
        self.assertIn("h264_nvenc", args)
        index = args.index("-b:v")
        self.assertEqual(args[index + 1], "12000000")
        self.assertIn("-rc", args)
        self.assertIn("vbr", args)


class SubtitleFilterTest(unittest.TestCase):
    def test_single_subtitle_keeps_original_style(self):
        result = command_builder.build_subtitle_filter("/tmp/input.mkv", [2], 1080)
        self.assertEqual(result, "subtitles=filename=input.mkv:si=2")
        self.assertNotIn("force_style", result)

    def test_multiple_subtitles_use_separate_vertical_margins(self):
        result = command_builder.build_subtitle_filter("/tmp/input.mkv", [0, 2], 1080)
        self.assertIn("si=0", result)
        self.assertIn("si=2", result)
        self.assertIn("MarginV=60", result)
        self.assertIn("MarginV=150", result)

    def test_filter_escapes_special_path_characters(self):
        # 冒号与逗号在滤镜表达式里都有特殊含义，必须转义
        result = command_builder.build_subtitle_filter("/tmp/a:b,c.mkv", [0], 720)
        self.assertIn("a\\\\:b\\,c.mkv", result)

    def test_filter_uses_file_name_only(self):
        # Windows 绝对路径的盘符冒号在滤镜里转义不稳定，因此只传文件名，
        # 由调用方把子进程工作目录设为视频所在目录。
        result = command_builder.build_subtitle_filter(r"D:\movies\a b.mkv", [0], 720)
        self.assertEqual(result, r"subtitles=filename=a b.mkv:si=0")

    def test_custom_style(self):
        result = command_builder.build_subtitle_filter(
            "/tmp/input.mkv", [0], 1080,
            style={"font_name": "Microsoft YaHei", "font_size": 48, "margin_v": 80})
        self.assertIn("FontName=Microsoft YaHei", result)
        self.assertIn("Fontsize=48", result)
        self.assertIn("MarginV=80", result)

    def test_stack_disabled_keeps_original_position(self):
        result = command_builder.build_subtitle_filter("/tmp/input.mkv", [0, 1], 1080,
                                                       stack=False)
        self.assertNotIn("MarginV", result)


class AudioAndContainerTest(unittest.TestCase):
    def test_mp4_transcodes_unsupported_audio(self):
        metadata = VideoMetadata(1920, 1080, 8_000_000, 60_000_000,
                                 audio_codec="dts")
        args = command_builder.build("/tmp/in.mkv", "/tmp/out.mp4", [0], metadata,
                                     {"container": "mp4"}, "libx264")
        self.assertIn("aac", args)
        self.assertIn("+faststart", args)

    def test_mkv_copies_audio(self):
        metadata = VideoMetadata(1920, 1080, 8_000_000, 60_000_000,
                                 audio_codec="dts")
        args = command_builder.build("/tmp/in.mkv", "/tmp/out.mkv", [0], metadata,
                                     {"container": "mkv"}, "libx264")
        self.assertIn("copy", args)


class ImageSubtitleTest(unittest.TestCase):
    """图片字幕（PGS / DVDSUB）走 overlay 叠加，而不是 subtitles 滤镜。"""

    def test_image_only_uses_overlay_with_absolute_stream(self):
        tracks = [{"kind": "image", "si": 1, "stream_index": 6}]
        graph = command_builder.build_filter_graph("/tmp/in.mkv", tracks, 1080)
        self.assertEqual(graph, "[0:v:0][0:s:6]overlay[vout]")
        self.assertNotIn("subtitles", graph)

    def test_multiple_image_tracks_chain(self):
        tracks = [{"kind": "image", "si": 0, "stream_index": 4},
                  {"kind": "image", "si": 1, "stream_index": 7}]
        graph = command_builder.build_filter_graph("/tmp/in.mkv", tracks, 1080)
        self.assertEqual(graph, "[0:v:0][0:s:4]overlay[vimg1];[vimg1][0:s:7]overlay[vout]")

    def test_mixed_text_then_image(self):
        tracks = [{"kind": "text", "si": 0, "stream_index": 2},
                  {"kind": "image", "si": 1, "stream_index": 5}]
        graph = command_builder.build_filter_graph("/tmp/in.mkv", tracks, 1080)
        self.assertTrue(graph.startswith("[0:v:0]subtitles=filename=in.mkv:si=0[vimg0];"))
        self.assertTrue(graph.endswith("[vimg0][0:s:5]overlay[vout]"))

    def test_image_track_adds_fix_sub_duration_and_maps_vout(self):
        metadata = VideoMetadata(1920, 1080, 8_000_000, 60_000_000)
        tracks = [{"kind": "image", "si": 0, "stream_index": 3}]
        args = command_builder.build("/tmp/in.mkv", "/tmp/out.mkv", [0], metadata,
                                     {"container": "mkv"}, "libx264", None, tracks)
        self.assertIn("-fix_sub_duration", args)
        self.assertIn("-filter_complex", args)
        self.assertIn("[vout]", args)
        # 图片字幕不需要 -vf
        self.assertNotIn("-vf", args)

    def test_text_only_has_no_fix_sub_duration(self):
        metadata = VideoMetadata(1920, 1080, 8_000_000, 60_000_000)
        args = command_builder.build("/tmp/in.mkv", "/tmp/out.mkv", [0], metadata,
                                     {"container": "mkv"}, "libx264")
        self.assertNotIn("-fix_sub_duration", args)

    def test_no_tracks_raises(self):
        with self.assertRaises(ValueError):
            command_builder.build_filter_graph("/tmp/in.mkv", [], 1080)


if __name__ == "__main__":
    unittest.main(verbosity=2)
