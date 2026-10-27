package com.xlabs.vidstb;

import com.arthenica.ffmpegkit.FFprobeKit;
import com.arthenica.ffmpegkit.FFprobeSession;
import com.arthenica.ffmpegkit.ReturnCode;

import org.json.JSONArray;
import org.json.JSONObject;

import java.io.File;
import java.util.ArrayList;
import java.util.Collections;
import java.util.List;

public final class MediaProbe {
    private MediaProbe() {
    }

    public static Result probe(File input) throws Exception {
        String command = "-v error -show_entries "
                + "stream=index,codec_name,codec_type,width,height,bit_rate,avg_frame_rate,r_frame_rate:"
                + "stream_tags=language,title:stream_disposition=default,forced:stream_side_data=rotation:"
                + "format=duration,bit_rate -of json " + quote(input.getAbsolutePath());
        FFprobeSession session = FFprobeKit.execute(command);
        if (!ReturnCode.isSuccess(session.getReturnCode())) {
            throw new IllegalArgumentException("无法读取视频信息：" + session.getOutput());
        }
        return parse(session.getOutput());
    }

    static Result parse(String json) throws Exception {
        JSONObject root = new JSONObject(json);
        JSONArray streams = root.optJSONArray("streams");
        List<SubtitleTrack> subtitles = new ArrayList<>();
        int width = 0;
        int height = 0;
        long videoBitRate = 0L;
        int subtitleIndex = 0;
        double frameRate = 30D;
        boolean hasAudio = false;
        int rotation = 0;
        if (streams != null) {
            for (int i = 0; i < streams.length(); i++) {
                JSONObject stream = streams.getJSONObject(i);
                String type = stream.optString("codec_type");
                if ("video".equals(type) && width == 0) {
                    width = stream.optInt("width");
                    height = stream.optInt("height");
                    videoBitRate = parseLong(stream.optString("bit_rate"));
                    frameRate = parseFrameRate(stream.optString("avg_frame_rate"));
                    if (frameRate <= 0D) {
                        frameRate = parseFrameRate(stream.optString("r_frame_rate"));
                    }
                    JSONArray sideData = stream.optJSONArray("side_data_list");
                    if (sideData != null) {
                        for (int j = 0; j < sideData.length(); j++) {
                            JSONObject data = sideData.getJSONObject(j);
                            if (data.has("rotation")) {
                                rotation = (int) data.optDouble("rotation");
                            }
                        }
                    }
                } else if ("audio".equals(type)) {
                    hasAudio = true;
                } else if ("subtitle".equals(type)) {
                    JSONObject tags = stream.optJSONObject("tags");
                    JSONObject disposition = stream.optJSONObject("disposition");
                    subtitles.add(new SubtitleTrack(
                            stream.optInt("index"), subtitleIndex++, stream.optString("codec_name"),
                            tags == null ? "" : tags.optString("language"),
                            tags == null ? "" : tags.optString("title"),
                            disposition != null && disposition.optInt("default") == 1,
                            disposition != null && disposition.optInt("forced") == 1));
                }
            }
        }
        if (width <= 0 || height <= 0) {
            throw new IllegalArgumentException("没有找到可处理的视频轨");
        }
        JSONObject format = root.optJSONObject("format");
        if (format != null && isStillImageContainer(format.optString("format_name"))) {
            throw new IllegalArgumentException("所选文件是图片，不是可编辑的视频，请重新选择视频文件");
        }
        long formatBitRate = format == null ? 0L : parseLong(format.optString("bit_rate"));
        double durationSeconds = format == null ? 0D : parseDouble(format.optString("duration"));
        VideoMetadata metadata = new VideoMetadata(width, height,
                videoBitRate > 0 ? videoBitRate : formatBitRate, (long) (durationSeconds * 1_000_000D),
                frameRate > 0D ? frameRate : 30D, hasAudio, rotation);
        return new Result(metadata, subtitles);
    }

    static boolean isStillImageContainer(String formatName) {
        if (formatName == null) return false;
        for (String format : formatName.toLowerCase(java.util.Locale.ROOT).split(",")) {
            switch (format.trim()) {
                case "image2":
                case "jpeg_pipe":
                case "jpg_pipe":
                case "png_pipe":
                case "webp_pipe":
                case "gif":
                case "bmp_pipe":
                case "tiff_pipe":
                case "svg_pipe":
                case "heic":
                case "avif":
                    return true;
                default:
            }
        }
        return false;
    }

    private static double parseFrameRate(String value) {
        if (value == null || value.isEmpty() || value.startsWith("0/")) {
            return 0D;
        }
        try {
            int separator = value.indexOf('/');
            if (separator > 0) {
                double numerator = Double.parseDouble(value.substring(0, separator));
                double denominator = Double.parseDouble(value.substring(separator + 1));
                return denominator == 0D ? 0D : numerator / denominator;
            }
            return Double.parseDouble(value);
        } catch (NumberFormatException ignored) {
            return 0D;
        }
    }

    private static long parseLong(String value) {
        try {
            return Long.parseLong(value);
        } catch (NumberFormatException ignored) {
            return 0L;
        }
    }

    private static double parseDouble(String value) {
        try {
            return Double.parseDouble(value);
        } catch (NumberFormatException ignored) {
            return 0D;
        }
    }

    private static String quote(String value) {
        return "'" + value.replace("'", "'\\''") + "'";
    }

    public static final class Result {
        private final VideoMetadata metadata;
        private final List<SubtitleTrack> subtitles;

        Result(VideoMetadata metadata, List<SubtitleTrack> subtitles) {
            this.metadata = metadata;
            this.subtitles = Collections.unmodifiableList(subtitles);
        }

        public VideoMetadata getMetadata() {
            return metadata;
        }

        public List<SubtitleTrack> getSubtitles() {
            return subtitles;
        }
    }
}
