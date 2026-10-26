package com.xlabs.vidstb;

public final class VideoMetadata {
    private final int width;
    private final int height;
    private final long bitRate;
    private final long durationUs;

    public VideoMetadata(int width, int height, long bitRate, long durationUs) {
        this.width = width;
        this.height = height;
        this.bitRate = bitRate;
        this.durationUs = durationUs;
    }

    public int getWidth() {
        return width;
    }

    public int getHeight() {
        return height;
    }

    public long getBitRate() {
        return bitRate;
    }

    public long getDurationUs() {
        return durationUs;
    }
}
