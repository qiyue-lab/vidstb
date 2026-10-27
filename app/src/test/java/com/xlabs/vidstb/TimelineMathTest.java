package com.xlabs.vidstb;

import org.junit.Test;

import static org.junit.Assert.assertArrayEquals;
import static org.junit.Assert.assertEquals;

public class TimelineMathTest {
    @Test
    public void mapsPositionToTimeWithinWindow() {
        assertEquals(15_000_000L, TimelineMath.timeAt(50F, 100,
                10_000_000L, 10_000_000L, 60_000_000L));
        assertEquals(10_000_000L, TimelineMath.timeAt(-10F, 100,
                10_000_000L, 10_000_000L, 60_000_000L));
        assertEquals(20_000_000L, TimelineMath.timeAt(150F, 100,
                10_000_000L, 10_000_000L, 60_000_000L));
    }

    @Test
    public void clampsScaledWindowToVideoBounds() {
        assertArrayEquals(new long[]{0L, 5_000_000L},
                TimelineMath.clampWindow(-2_000_000L, 5_000_000L, 60_000_000L));
        assertArrayEquals(new long[]{55_000_000L, 5_000_000L},
                TimelineMath.clampWindow(58_000_000L, 5_000_000L, 60_000_000L));
        assertArrayEquals(new long[]{0L, 3_000_000L},
                TimelineMath.clampWindow(0L, 10_000_000L, 3_000_000L));
    }

    @Test
    public void handlesInvalidWidthWithoutThrowing() {
        assertEquals(0L, TimelineMath.timeAt(10F, 0, 0L, 1_000_000L, 10_000_000L));
    }
}
