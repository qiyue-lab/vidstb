package com.xlabs.vidstb;

import org.junit.Test;

import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertTrue;
import static org.junit.Assert.fail;

public class EditProjectTest {
    @Test
    public void outputDurationSubtractsTransitions() {
        EditProject project = projectWithTwoSegments();
        project.getOutput().setTransitionMs(500);
        assertEquals(9_500_000L, project.getOutputDurationUs());
        project.validate();
    }

    @Test
    public void rejectsOddOutputDimensions() {
        EditProject project = projectWithTwoSegments();
        project.getOutput().setWidth(1279);
        assertInvalid(project, "偶数");
    }

    @Test
    public void rejectsTransitionLongerThanSegment() {
        EditProject project = projectWithTwoSegments();
        project.getOutput().setTransitionMs(4000);
        assertInvalid(project, "长于转场");
    }

    @Test
    public void rejectsInvalidTimeRange() {
        EditProject project = projectWithTwoSegments();
        project.getSegments().get(0).setStartUs(5_000_000L);
        assertInvalid(project, "起止时间");
    }

    @Test
    public void timeCodeRoundTripsMilliseconds() {
        long value = 3_723_456_789L;
        assertEquals(value / 1000L * 1000L, TimeCode.parse(TimeCode.format(value)));
        assertEquals(61_500_000L, TimeCode.parse("01:01.500"));
    }

    static EditProject projectWithTwoSegments() {
        EditProject project = new EditProject();
        project.getSegments().add(segment(1, 1_000_000L, 5_000_000L, true));
        project.getSegments().add(segment(2, 2_000_000L, 8_000_000L, false));
        project.getOutput().setWidth(1280);
        project.getOutput().setHeight(720);
        project.getOutput().setFrameRate(30);
        project.getOutput().setTransitionMs(500);
        return project;
    }

    static VideoSegment segment(long id, long start, long end, boolean audio) {
        return new VideoSegment(id, "/tmp/source" + id + ".mp4", "source" + id + ".mp4",
                10_000_000L, start, end, audio, 1920, 1080, 30D);
    }

    private static void assertInvalid(EditProject project, String expectedMessage) {
        try {
            project.validate();
            fail("应当拒绝无效项目");
        } catch (IllegalArgumentException error) {
            assertTrue(error.getMessage(), error.getMessage().contains(expectedMessage));
        }
    }
}
