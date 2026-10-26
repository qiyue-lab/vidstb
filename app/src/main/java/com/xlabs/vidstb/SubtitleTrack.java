package com.xlabs.vidstb;

import java.util.Locale;

public final class SubtitleTrack {
    private final int streamIndex;
    private final int subtitleIndex;
    private final String codecName;
    private final String language;
    private final String title;
    private final boolean defaultTrack;
    private final boolean forced;

    public SubtitleTrack(int streamIndex, int subtitleIndex, String codecName, String language,
                         String title, boolean defaultTrack, boolean forced) {
        this.streamIndex = streamIndex;
        this.subtitleIndex = subtitleIndex;
        this.codecName = codecName;
        this.language = language;
        this.title = title;
        this.defaultTrack = defaultTrack;
        this.forced = forced;
    }

    public int getSubtitleIndex() {
        return subtitleIndex;
    }

    public boolean isDefaultTrack() {
        return defaultTrack;
    }

    public boolean isSupported() {
        return "subrip".equals(codecName) || "srt".equals(codecName)
                || "ass".equals(codecName) || "ssa".equals(codecName);
    }

    public String getDisplayName() {
        StringBuilder label = new StringBuilder();
        label.append(language == null || language.isEmpty() ? "未知语言" : language);
        if (title != null && !title.isEmpty()) {
            label.append(" · ").append(title);
        }
        label.append(" · ").append(codecName == null ? "未知格式" : codecName.toUpperCase(Locale.ROOT));
        if (defaultTrack) {
            label.append(" · 默认");
        }
        if (forced) {
            label.append(" · 强制");
        }
        if (!isSupported()) {
            label.append("（暂不支持）");
        }
        return label.toString();
    }
}
