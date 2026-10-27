package com.xlabs.vidstb;

import java.io.Serializable;
import java.util.ArrayList;
import java.util.List;

public final class EditProject implements Serializable {
    private final List<VideoSegment> segments = new ArrayList<>();
    private final OutputSettings output = new OutputSettings();

    public List<VideoSegment> getSegments() {
        return segments;
    }

    public OutputSettings getOutput() {
        return output;
    }

    public long getOutputDurationUs() {
        long duration = 0L;
        for (VideoSegment segment : segments) {
            duration += Math.max(0L, segment.getDurationUs());
        }
        if (output.isTransitionEnabled() && segments.size() > 1) {
            duration -= (long) output.getTransitionMs() * 1000L * (segments.size() - 1);
        }
        return Math.max(0L, duration);
    }

    public void validate() {
        if (segments.isEmpty()) {
            throw new IllegalArgumentException("请至少添加一个视频片段");
        }
        if (output.getWidth() < 16 || output.getHeight() < 16
                || output.getWidth() % 2 != 0 || output.getHeight() % 2 != 0) {
            throw new IllegalArgumentException("输出宽高必须是至少 16 的偶数");
        }
        if (output.getFrameRate() < 1 || output.getFrameRate() > 120) {
            throw new IllegalArgumentException("输出帧率必须在 1 到 120 之间");
        }
        if (output.getTransitionMs() < 0 || output.getTransitionMs() > 5000) {
            throw new IllegalArgumentException("转场时长必须在 0 到 5000 毫秒之间");
        }
        for (VideoSegment segment : segments) {
            if (segment.getStartUs() < 0 || segment.getEndUs() > segment.getSourceDurationUs()
                    || segment.getDurationUs() <= 0L) {
                throw new IllegalArgumentException("片段“" + segment.getDisplayName() + "”的起止时间无效");
            }
            if (!segment.getCrop().isValid()) {
                throw new IllegalArgumentException("片段“" + segment.getDisplayName() + "”的裁剪区域无效");
            }
            if (segment.getRotation() % 90 != 0) {
                throw new IllegalArgumentException("旋转角度必须是 90 度的倍数");
            }
            if (output.isTransitionEnabled() && segments.size() > 1
                    && segment.getDurationUs() <= output.getTransitionMs() * 1000L) {
                throw new IllegalArgumentException("每个片段都必须长于转场时长");
            }
        }
        if (getOutputDurationUs() <= 0L) {
            throw new IllegalArgumentException("输出时长无效");
        }
    }
}
