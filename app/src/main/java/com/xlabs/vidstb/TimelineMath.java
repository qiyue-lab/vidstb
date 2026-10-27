package com.xlabs.vidstb;

public final class TimelineMath {
    public static final long MIN_WINDOW_US = 5_000_000L;

    private TimelineMath() {
    }

    public static long timeAt(float x, int width, long windowStart, long windowDuration,
                              long videoDuration) {
        if (width <= 0 || windowDuration <= 0L) return 0L;
        float fraction = Math.max(0F, Math.min(1F, x / width));
        return clamp(windowStart + (long) (fraction * windowDuration), 0L, videoDuration);
    }

    public static long[] clampWindow(long start, long duration, long videoDuration) {
        long safeDuration = Math.max(1L, Math.min(duration, Math.max(1L, videoDuration)));
        long maxStart = Math.max(0L, videoDuration - safeDuration);
        long safeStart = clamp(start, 0L, maxStart);
        return new long[]{safeStart, safeDuration};
    }

    public static long clamp(long value, long min, long max) {
        return Math.max(min, Math.min(max, value));
    }
}
