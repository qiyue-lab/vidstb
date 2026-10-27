package com.xlabs.vidstb;

import java.util.Locale;

public final class TimeCode {
    private TimeCode() {
    }

    public static String format(long microseconds) {
        long totalMs = Math.max(0L, microseconds / 1000L);
        long hours = totalMs / 3_600_000L;
        long minutes = totalMs / 60_000L % 60L;
        long seconds = totalMs / 1000L % 60L;
        long millis = totalMs % 1000L;
        return String.format(Locale.ROOT, "%02d:%02d:%02d.%03d", hours, minutes, seconds, millis);
    }

    public static long parse(String value) {
        String[] parts = value.trim().split(":");
        if (parts.length < 1 || parts.length > 3) {
            throw new NumberFormatException("时间格式无效");
        }
        double seconds = 0D;
        for (String part : parts) {
            seconds = seconds * 60D + Double.parseDouble(part);
        }
        if (seconds < 0D || !Double.isFinite(seconds)) {
            throw new NumberFormatException("时间格式无效");
        }
        return Math.round(seconds * 1_000_000D);
    }
}
