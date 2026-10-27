package com.xlabs.vidstb;

import java.io.Serializable;

public final class OutputSettings implements Serializable {
    private int width = 1280;
    private int height = 720;
    private int frameRate = 30;
    private int transitionMs = 500;
    private boolean transitionEnabled = true;
    private float masterVolume = 1F;
    private boolean globalMute;
    private String replacementAudioPath = "";
    private String backgroundAudioPath = "";
    private float originalVolume = 1F;
    private float backgroundVolume = 1F;

    public int getWidth() { return width; }
    public void setWidth(int width) { this.width = width; }
    public int getHeight() { return height; }
    public void setHeight(int height) { this.height = height; }
    public int getFrameRate() { return frameRate; }
    public void setFrameRate(int frameRate) { this.frameRate = frameRate; }
    public int getTransitionMs() { return transitionMs; }
    public void setTransitionMs(int transitionMs) { this.transitionMs = transitionMs; }
    public boolean isTransitionEnabled() { return transitionEnabled; }
    public void setTransitionEnabled(boolean enabled) { transitionEnabled = enabled; }
    public float getMasterVolume() { return masterVolume; }
    public void setMasterVolume(float volume) { masterVolume = volume; }
    public boolean isGlobalMute() { return globalMute; }
    public void setGlobalMute(boolean mute) { globalMute = mute; }
    public String getReplacementAudioPath() { return replacementAudioPath; }
    public void setReplacementAudioPath(String path) { replacementAudioPath = path == null ? "" : path; }
    public String getBackgroundAudioPath() { return backgroundAudioPath; }
    public void setBackgroundAudioPath(String path) { backgroundAudioPath = path == null ? "" : path; }
    public float getOriginalVolume() { return originalVolume; }
    public void setOriginalVolume(float volume) { originalVolume = volume; }
    public float getBackgroundVolume() { return backgroundVolume; }
    public void setBackgroundVolume(float volume) { backgroundVolume = volume; }
}
