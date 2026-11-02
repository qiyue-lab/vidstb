"""MediaProbe 解析逻辑测试，对应 Android 版 MediaProbeTest。"""

import json
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import media_probe  # noqa: E402

SAMPLE = {
    "streams": [
        {"index": 0, "codec_name": "h264", "codec_type": "video",
         "width": 1920, "height": 1080, "bit_rate": "8000000",
         "avg_frame_rate": "24000/1001",
         "side_data_list": [{"rotation": 0}]},
        {"index": 1, "codec_name": "aac", "codec_type": "audio"},
        {"index": 2, "codec_name": "subrip", "codec_type": "subtitle",
         "tags": {"language": "chi", "title": "中文"},
         "disposition": {"default": 1, "forced": 0}},
        {"index": 3, "codec_name": "ass", "codec_type": "subtitle",
         "tags": {"language": "eng"},
         "disposition": {"default": 0, "forced": 0}},
        {"index": 4, "codec_name": "hdmv_pgs_bluray", "codec_type": "subtitle",
         "tags": {"language": "jpn"}, "disposition": {"default": 0, "forced": 0}},
        {"index": 5, "codec_name": "ttf", "codec_type": "attachment",
         "tags": {"filename": "arial.ttf", "mimetype": "font/sfnt"}},
    ],
    "format": {"duration": "60.0", "bit_rate": "9000000", "format_name": "matroska,webm"},
}


class ParseTest(unittest.TestCase):
    def test_frame_rate_fraction(self):
        self.assertAlmostEqual(media_probe._parse_frame_rate("24000/1001"), 23.976, places=3)

    def test_frame_rate_invalid(self):
        self.assertEqual(media_probe._parse_frame_rate("0/0"), 0.0)
        self.assertEqual(media_probe._parse_frame_rate(""), 0.0)

    def test_still_image_container(self):
        self.assertTrue(media_probe.is_still_image_container("png_pipe"))
        self.assertTrue(media_probe.is_still_image_container("image2,foo"))
        self.assertFalse(media_probe.is_still_image_container("matroska,webm"))

    def test_parse_streams(self):
        result = media_probe.parse_probe_json(SAMPLE, "/tmp/a.mkv")
        self.assertIsNone(result.error)
        self.assertEqual(result.metadata.width, 1920)
        self.assertEqual(result.metadata.height, 1080)
        self.assertEqual(result.metadata.duration_us, 60_000_000)
        self.assertTrue(result.metadata.has_audio)
        self.assertEqual(len(result.subtitles), 3)
        self.assertEqual(result.subtitles[0].subtitle_index, 0)
        self.assertEqual(result.subtitles[1].subtitle_index, 1)
        self.assertTrue(result.subtitles[0].is_supported())
        self.assertTrue(result.subtitles[1].is_supported())
        # PGS 是图片字幕，现在走 overlay 叠加烧录，属于支持范围
        self.assertTrue(result.subtitles[2].is_supported())
        self.assertTrue(result.subtitles[2].is_image())
        self.assertFalse(result.subtitles[0].is_image())
        self.assertEqual(result.subtitles[2].stream_index, 4)
        self.assertTrue(result.subtitles[0].default_track)
        self.assertEqual(len(result.font_attachments), 1)

    def test_closed_caption_still_unsupported(self):
        data = {"streams": [
            {"index": 0, "codec_name": "h264", "codec_type": "video",
             "width": 1920, "height": 1080, "avg_frame_rate": "24/1"},
            {"index": 1, "codec_name": "eia_608", "codec_type": "subtitle"},
        ], "format": {"duration": "10", "format_name": "matroska"}}
        result = media_probe.parse_probe_json(data, "/tmp/a.mkv")
        self.assertFalse(result.subtitles[0].is_supported())
        self.assertFalse(result.subtitles[0].is_image())

    def test_parse_without_video_stream(self):
        data = {"streams": [{"codec_type": "audio"}], "format": {"duration": "1"}}
        result = media_probe.parse_probe_json(data, "/tmp/a.mkv")
        self.assertIsNotNone(result.error)


if __name__ == "__main__":
    unittest.main(verbosity=2)
