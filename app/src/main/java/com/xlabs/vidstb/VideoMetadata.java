package com.xlabs.vidstb;

public final class VideoMetadata {
    private final int width;
    private final int height;
    private final long bitRate;
    private final long durationUs;
    private final double frameRate;
    private final boolean hasAudio;
    private final int rotation;

    public VideoMetadata(int width, int height, long bitRate, long durationUs) {
        this(width, height, bitRate, durationUs, 30D, true, 0);
    }

    public VideoMetadata(int width, int height, long bitRate, long durationUs,
                         double frameRate, boolean hasAudio, int rotation) {
        this.width = width;
        this.height = height;
        this.bitRate = bitRate;
        this.durationUs = durationUs;
        this.frameRate = frameRate;
        this.hasAudio = hasAudio;
        this.rotation = rotation;
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

    public double getFrameRate() {
        return frameRate;
    }

    public boolean hasAudio() {
        return hasAudio;
    }

    public int getRotation() {
        return rotation;
    }
}
