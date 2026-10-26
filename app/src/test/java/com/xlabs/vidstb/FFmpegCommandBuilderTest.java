package com.xlabs.vidstb;

import org.junit.Test;

import java.util.Arrays;
import java.util.Collections;
import java.util.List;

import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertFalse;
import static org.junit.Assert.assertTrue;

public class FFmpegCommandBuilderTest {
    private final VideoMetadata metadata = new VideoMetadata(1920, 1080, 8_000_000L, 60_000_000L);

    @Test
    public void softwareModeUsesHighQualityWithoutScaling() {
        List<String> args = FFmpegCommandBuilder.build("/tmp/input.mkv", "/tmp/output.mkv",
                Collections.singletonList(0), false, metadata);

        assertTrue(args.contains("libx264"));
        assertTrue(args.contains("16"));
        assertTrue(args.contains("slow"));
        assertTrue(args.contains("copy"));
        assertFalse(args.contains("scale"));
        assertFalse(args.contains("-r"));
        assertFalse(args.contains("h264_mediacodec"));
    }

    @Test
    public void hardwareModeUsesMediaCodecAndSourceBitrateHeadroom() {
        List<String> args = FFmpegCommandBuilder.build("/tmp/input.mkv", "/tmp/output.mkv",
                Collections.singletonList(1), true, metadata);

        assertTrue(args.contains("h264_mediacodec"));
        assertEquals("12000000", args.get(args.indexOf("-b:v") + 1));
    }

    @Test
    public void singleSubtitleKeepsOriginalStyle() {
        String filter = FFmpegCommandBuilder.buildSubtitleFilter("/tmp/input.mkv",
                Collections.singletonList(2), 1080);

        assertEquals("subtitles=filename=/tmp/input.mkv:si=2", filter);
        assertFalse(filter.contains("force_style"));
    }

    @Test
    public void multipleSubtitlesUseSeparateVerticalMargins() {
        String filter = FFmpegCommandBuilder.buildSubtitleFilter("/tmp/input.mkv",
                Arrays.asList(0, 2), 1080);

        assertTrue(filter.contains("si=0"));
        assertTrue(filter.contains("si=2"));
        assertTrue(filter.contains("MarginV=60"));
        assertTrue(filter.contains("MarginV=150"));
    }

    @Test
    public void filterEscapesSpecialPathCharacters() {
        String filter = FFmpegCommandBuilder.buildSubtitleFilter("/tmp/a:b,c.mkv",
                Collections.singletonList(0), 720);

        assertTrue(filter.contains("a\\:b\\,c.mkv"));
    }

    @Test(expected = IllegalArgumentException.class)
    public void rejectsEmptySubtitleSelection() {
        FFmpegCommandBuilder.build("in.mkv", "out.mkv", Collections.emptyList(), false, metadata);
    }
}
