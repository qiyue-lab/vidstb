package com.xlabs.vidstb;

import java.io.Serializable;

public final class VideoSegment implements Serializable {
    private final long id;
    private final String sourcePath;
    private final String displayName;
    private final long sourceDurationUs;
    private long startUs;
    private long endUs;
    private final boolean hasAudio;
    private final int sourceWidth;
    private final int sourceHeight;
    private final double sourceFrameRate;
    private CropRect crop = CropRect.full();
    private int rotation;
    private boolean horizontalFlip;
    private boolean verticalFlip;
    private float volume = 1F;
    private boolean muted;

    public VideoSegment(long id, String sourcePath, String displayName, long sourceDurationUs,
                        long startUs, long endUs, boolean hasAudio, int sourceWidth,
                        int sourceHeight, double sourceFrameRate) {
        this.id = id;
        this.sourcePath = sourcePath;
        this.displayName = displayName;
        this.sourceDurationUs = sourceDurationUs;
        this.startUs = startUs;
        this.endUs = endUs;
        this.hasAudio = hasAudio;
        this.sourceWidth = sourceWidth;
        this.sourceHeight = sourceHeight;
        this.sourceFrameRate = sourceFrameRate;
    }

    public long getId() { return id; }
    public String getSourcePath() { return sourcePath; }
    public String getDisplayName() { return displayName; }
    public long getSourceDurationUs() { return sourceDurationUs; }
    public long getStartUs() { return startUs; }
    public void setStartUs(long value) { startUs = value; }
    public long getEndUs() { return endUs; }
    public void setEndUs(long value) { endUs = value; }
    public long getDurationUs() { return endUs - startUs; }
    public boolean hasAudio() { return hasAudio; }
    public int getSourceWidth() { return sourceWidth; }
    public int getSourceHeight() { return sourceHeight; }
    public double getSourceFrameRate() { return sourceFrameRate; }
    public CropRect getCrop() { return crop; }
    public void setCrop(CropRect value) { crop = value; }
    public int getRotation() { return rotation; }
    public void setRotation(int value) { rotation = ((value % 360) + 360) % 360; }
    public boolean isHorizontalFlip() { return horizontalFlip; }
    public void setHorizontalFlip(boolean value) { horizontalFlip = value; }
    public boolean isVerticalFlip() { return verticalFlip; }
    public void setVerticalFlip(boolean value) { verticalFlip = value; }
    public float getVolume() { return volume; }
    public void setVolume(float value) { volume = value; }
    public boolean isMuted() { return muted; }
    public void setMuted(boolean value) { muted = value; }

    public VideoSegment copy(long newId) {
        VideoSegment copy = new VideoSegment(newId, sourcePath, displayName, sourceDurationUs,
                startUs, endUs, hasAudio, sourceWidth, sourceHeight, sourceFrameRate);
        copy.crop = new CropRect(crop.getLeft(), crop.getTop(), crop.getRight(), crop.getBottom());
        copy.rotation = rotation;
        copy.horizontalFlip = horizontalFlip;
        copy.verticalFlip = verticalFlip;
        copy.volume = volume;
        copy.muted = muted;
        return copy;
    }
}
