"""视频合并模块的单元测试。"""

from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import merger  # noqa: E402
from app.models import VideoMetadata  # noqa: E402


def make_clip(path: str, width: int, height: int, fps: float, seconds: float,
              has_audio: bool = True) -> merger.MergeClip:
    return merger.MergeClip(
        path=path,
        metadata=VideoMetadata(width=width, height=height, frame_rate=fps,
                               duration_us=int(seconds * 1_000_000),
                               has_audio=has_audio),
        has_audio=has_audio,
    )


class SettingsTest(unittest.TestCase):
    def test_defaults_are_4k60(self):
        params = merger.settings({})
        self.assertEqual(params["width"], 3840)
        self.assertEqual(params["height"], 2160)
        self.assertEqual(params["fps"], 60)
        self.assertEqual(params["container"], "mp4")
        self.assertEqual(params["fit"], merger.FIT_PAD)

    def test_bitrate_is_clamped(self):
        self.assertEqual(merger.settings({"merge_bitrate_mbps": 0})["bitrate_mbps"], 1.0)
        self.assertEqual(merger.settings({"merge_bitrate_mbps": 9999})["bitrate_mbps"], 500.0)

    def test_unknown_fit_falls_back_to_pad(self):
        self.assertEqual(merger.settings({"merge_fit": "wat"})["fit"], merger.FIT_PAD)


class FilterGraphTest(unittest.TestCase):
    def test_pad_mode_scales_then_pads(self):
        chain = merger.fit_chain(merger.FIT_PAD)
        self.assertIn("force_original_aspect_ratio=decrease", chain)
        self.assertIn("pad=w=3840:h=2160", chain)

    def test_crop_mode_uses_increase_and_crop(self):
        chain = merger.fit_chain(merger.FIT_CROP)
        self.assertIn("force_original_aspect_ratio=increase", chain)
        self.assertIn("crop=w=3840:h=2160", chain)

    def test_stretch_mode_has_no_pad_or_crop(self):
        chain = merger.fit_chain(merger.FIT_STRETCH)
        self.assertNotIn("pad=", chain)
        self.assertNotIn("crop=", chain)

    def test_video_chain_is_4k60(self):
        chain = merger.video_chain(2, merger.FIT_PAD)
        self.assertTrue(chain.startswith("[2:v:0]"))
        self.assertIn("fps=60", chain)
        self.assertIn("setsar=1", chain)
        self.assertTrue(chain.endswith("[v2]"))

    def test_missing_audio_gets_silence_of_same_length(self):
        chain = merger.audio_chain(1, False, 12.5)
        self.assertIn("anullsrc=", chain)
        self.assertIn("atrim=duration=12.500", chain)
        self.assertTrue(chain.endswith("[a1]"))

    def test_existing_audio_uses_input_stream(self):
        chain = merger.audio_chain(0, True, 5.0)
        self.assertTrue(chain.startswith("[0:a:0]"))
        self.assertNotIn("anullsrc", chain)

    def test_graph_concatenates_all_clips(self):
        clips = [make_clip("a.mp4", 1920, 1080, 30, 10),
                 make_clip("b.mp4", 1280, 720, 25, 8, has_audio=False),
                 make_clip("c.mp4", 3840, 2160, 60, 6)]
        graph = merger.build_filter_graph(clips, merger.FIT_PAD, True)
        self.assertIn("[v0][v1][v2]concat=n=3:v=1:a=0[vout]", graph)
        self.assertIn("[a0][a1][a2]concat=n=3:v=0:a=1[aout]", graph)
        self.assertIn("atrim=duration=8.000", graph)

    def test_single_clip_skips_concat(self):
        clips = [make_clip("a.mp4", 1920, 1080, 30, 10)]
        graph = merger.build_filter_graph(clips, merger.FIT_PAD, True)
        self.assertNotIn("concat=", graph)
        self.assertIn("[vout]", graph)
        self.assertIn("[aout]", graph)

    def test_no_audio_at_all_omits_audio_branch(self):
        clips = [make_clip("a.mp4", 1920, 1080, 30, 10, has_audio=False),
                 make_clip("b.mp4", 1920, 1080, 30, 10, has_audio=False)]
        graph = merger.build_filter_graph(clips, merger.FIT_PAD, False)
        self.assertNotIn("a0]", graph)
        self.assertIn("concat=n=2:v=1:a=0[vout]", graph)

    def test_empty_clip_list_raises(self):
        with self.assertRaises(ValueError):
            merger.build_filter_graph([], merger.FIT_PAD, False)


class BuildCommandTest(unittest.TestCase):
    def setUp(self):
        self.clips = [make_clip("a.mp4", 1920, 1080, 30, 10),
                      make_clip("b.mp4", 1280, 720, 25, 8, has_audio=False)]

    def test_inputs_and_maps(self):
        args = merger.build(self.clips, "out.mp4", {}, "libx264")
        self.assertEqual(args.count("-i"), 2)
        self.assertIn("a.mp4", args)
        self.assertIn("b.mp4", args)
        self.assertIn("[vout]", args)
        self.assertIn("[aout]", args)
        self.assertEqual(args[-1], "out.mp4")

    def test_output_is_always_4k60(self):
        args = merger.build(self.clips, "out.mp4", {}, "libx264")
        index = args.index("-r")
        self.assertEqual(args[index + 1], "60")
        self.assertIn("fps=60", " ".join(args))

    def test_bitrate_is_applied(self):
        args = merger.build(self.clips, "out.mp4", {"merge_bitrate_mbps": 40}, "libx264")
        index = args.index("-b:v")
        self.assertEqual(args[index + 1], "40000000")

    def test_nvenc_uses_its_own_preset(self):
        args = merger.build(self.clips, "out.mp4", {}, "h264_nvenc")
        index = args.index("-c:v")
        self.assertEqual(args[index + 1], "h264_nvenc")
        self.assertIn("-rc", args)

    def test_mp4_gets_faststart(self):
        args = merger.build(self.clips, "out.mp4", {"merge_container": "mp4"}, "libx264")
        self.assertIn("+faststart", args)

    def test_audio_is_re_encoded_to_aac(self):
        args = merger.build(self.clips, "out.mp4", {"merge_audio_bitrate_kbps": 320}, "libx264")
        index = args.index("-b:a")
        self.assertEqual(args[index + 1], "320k")
        self.assertIn("aac", args)

    def test_empty_clips_raise(self):
        with self.assertRaises(ValueError):
            merger.build([], "out.mp4", {}, "libx264")


class EstimateTest(unittest.TestCase):
    def test_size_matches_bitrate_times_duration(self):
        clips = [make_clip("a.mp4", 1920, 1080, 30, 10),
                 make_clip("b.mp4", 1920, 1080, 30, 20)]
        result = merger.estimate(clips, {"merge_bitrate_mbps": 50,
                                         "merge_audio_bitrate_kbps": 256})
        self.assertEqual(result["total_duration_us"], 30_000_000)
        expected = int((50_000_000 + 256_000) * 30 / 8 * merger.CONTAINER_OVERHEAD)
        self.assertEqual(result["estimated_bytes"], expected)
        # 30 秒 50Mbps 大约 188MB
        self.assertAlmostEqual(result["estimated_bytes"] / 1048576, 180.5, delta=1.0)

    def test_estimate_grows_with_bitrate(self):
        clips = [make_clip("a.mp4", 1920, 1080, 30, 10),
                 make_clip("b.mp4", 1920, 1080, 30, 10)]
        low = merger.estimate(clips, {"merge_bitrate_mbps": 20})["estimated_bytes"]
        high = merger.estimate(clips, {"merge_bitrate_mbps": 80})["estimated_bytes"]
        self.assertGreater(high, low * 3)

    def test_output_spec_is_reported(self):
        result = merger.estimate([make_clip("a.mp4", 1920, 1080, 30, 10)], {})
        self.assertEqual(result["output"]["width"], 3840)
        self.assertEqual(result["output"]["height"], 2160)
        self.assertEqual(result["output"]["fps"], 60)

    def test_clips_are_flagged_when_upscaled(self):
        result = merger.estimate([make_clip("a.mp4", 1920, 1080, 30, 10),
                                  make_clip("b.mp4", 3840, 2160, 60, 10)], {})
        self.assertTrue(result["clips"][0]["upscaled"])
        self.assertFalse(result["clips"][1]["upscaled"])
        self.assertTrue(result["clips"][0]["fps_changed"])

    def test_no_audio_clips_skip_audio_bytes(self):
        clips = [make_clip("a.mp4", 1920, 1080, 30, 10, has_audio=False),
                 make_clip("b.mp4", 1920, 1080, 30, 10, has_audio=False)]
        result = merger.estimate(clips, {})
        self.assertFalse(result["with_audio"])
        self.assertEqual(result["audio_bytes"], 0)

    def test_target_estimate_is_marked_as_upper_bound(self):
        result = merger.estimate([make_clip("a.mp4", 1920, 1080, 30, 10)], {})
        self.assertEqual(result["basis"], "target")


class MeasuredEstimateTest(unittest.TestCase):
    def test_weighted_by_each_clip_duration(self):
        clips = [make_clip("a.mp4", 1920, 1080, 30, 10),
                 make_clip("b.mp4", 1920, 1080, 30, 30)]
        # 第一段 1 MB/s，第二段 2 MB/s
        rates = [1_048_576, 2_097_152]
        config = {"merge_audio_bitrate_kbps": 32}
        result = merger.estimate_from_samples(clips, rates, config)
        self.assertEqual(result["basis"], "measured")
        expected_video = 1_048_576 * 10 + 2_097_152 * 30
        expected_audio = 32_000 * 40 / 8
        self.assertEqual(result["video_bytes"], expected_video)
        self.assertEqual(result["estimated_bytes"],
                         int((expected_video + expected_audio) * merger.CONTAINER_OVERHEAD))
        self.assertEqual(result["total_duration_us"], 40_000_000)
        # 加权平均码率 = 总字节*8/总时长
        self.assertAlmostEqual(result["measured_bitrate_mbps"],
                              expected_video * 8 / 40 / 1_000_000, delta=0.05)

    def test_audio_bytes_use_total_duration(self):
        clips = [make_clip("a.mp4", 1920, 1080, 30, 10),
                 make_clip("b.mp4", 1920, 1080, 30, 10)]
        result = merger.estimate_from_samples(clips, [0, 0],
                                              {"merge_audio_bitrate_kbps": 256})
        self.assertEqual(result["audio_bytes"], int(256_000 * 20 / 8))

    def test_broken_clips_are_excluded(self):
        good = make_clip("a.mp4", 1920, 1080, 30, 10)
        bad = merger.MergeClip(path="x.mp4", error="损坏")
        result = merger.estimate_from_samples([good, bad], [1_000_000], {})
        self.assertEqual(result["sample_count"], 1)
        self.assertEqual(result["total_duration_us"], 10_000_000)


if __name__ == "__main__":
    unittest.main()
