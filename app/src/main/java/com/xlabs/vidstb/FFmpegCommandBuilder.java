package com.xlabs.vidstb;

import java.util.ArrayList;
import java.util.Arrays;
import java.util.Collections;
import java.util.List;
import java.util.Locale;

public final class FFmpegCommandBuilder {
    private FFmpegCommandBuilder() {
    }

    public static List<String> build(String inputPath, String outputPath, List<Integer> subtitleIndexes,
                                     boolean hardware, VideoMetadata metadata) {
        if (subtitleIndexes == null || subtitleIndexes.isEmpty()) {
            throw new IllegalArgumentException("至少需要选择一条字幕");
        }
        List<String> args = new ArrayList<>(Arrays.asList(
                "-hide_banner", "-y", "-i", inputPath,
                "-map", "0:v:0", "-map", "0:a?",
                "-map_metadata", "0", "-map_chapters", "0",
                "-vf", buildSubtitleFilter(inputPath, subtitleIndexes, metadata.getHeight()),
                "-c:v", hardware ? "h264_mediacodec" : "libx264"
        ));
        if (hardware) {
            long bitRate = hardwareBitRate(metadata);
            args.addAll(Arrays.asList("-b:v", String.valueOf(bitRate), "-pix_fmt", "yuv420p"));
        } else {
            args.addAll(Arrays.asList("-preset", "slow", "-crf", "16", "-pix_fmt", "yuv420p"));
        }
        args.addAll(Arrays.asList("-c:a", "copy", "-max_muxing_queue_size", "1024", outputPath));
        return Collections.unmodifiableList(args);
    }

    public static String buildSubtitleFilter(String inputPath, List<Integer> subtitleIndexes, int videoHeight) {
        String path = escapeFilterPath(inputPath);
        boolean multiple = subtitleIndexes.size() > 1;
        int baseMargin = Math.max(24, videoHeight / 18);
        int marginStep = Math.max(36, videoHeight / 12);
        StringBuilder filter = new StringBuilder();
        for (int i = 0; i < subtitleIndexes.size(); i++) {
            if (i > 0) {
                filter.append(',');
            }
            filter.append("subtitles=filename=").append(path)
                    .append(":si=").append(subtitleIndexes.get(i));
            if (multiple) {
                int margin = baseMargin + i * marginStep;
                filter.append(":force_style=Alignment=2\\,MarginV=").append(margin);
            }
        }
        return filter.toString();
    }

    public static long hardwareBitRate(VideoMetadata metadata) {
        long pixels = Math.max(1, (long) metadata.getWidth() * metadata.getHeight());
        long minimum = Math.max(2_000_000L, pixels * 4L);
        long sourceWithHeadroom = metadata.getBitRate() > 0
                ? metadata.getBitRate() + metadata.getBitRate() / 2L : 0L;
        return Math.max(minimum, sourceWithHeadroom);
    }

    private static String escapeFilterPath(String path) {
        return path.replace("\\", "\\\\")
                .replace(":", "\\:")
                .replace("'", "\\'")
                .replace(",", "\\,")
                .replace("[", "\\[")
                .replace("]", "\\]");
    }
}
