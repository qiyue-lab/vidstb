package com.xlabs.vidstb;

import org.junit.Test;

import static org.junit.Assert.assertFalse;
import static org.junit.Assert.assertTrue;

public class MediaProbeTest {
    @Test
    public void detectsStillImageContainers() {
        assertTrue(MediaProbe.isStillImageContainer("image2"));
        assertTrue(MediaProbe.isStillImageContainer("jpeg_pipe"));
        assertTrue(MediaProbe.isStillImageContainer("png_pipe"));
        assertTrue(MediaProbe.isStillImageContainer("image2,other"));
    }

    @Test
    public void acceptsRealVideoContainers() {
        assertFalse(MediaProbe.isStillImageContainer("mov,mp4,m4a,3gp,3g2,mj2"));
        assertFalse(MediaProbe.isStillImageContainer("matroska,webm"));
        assertFalse(MediaProbe.isStillImageContainer(""));
    }
}
