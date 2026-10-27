package com.xlabs.vidstb;

import org.junit.Test;

import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertFalse;
import static org.junit.Assert.assertTrue;

public class EditCommandBuilderTest {
    @Test
    public void buildsFrameAccurateMultiSegmentGraph() {
        EditProject project = EditProjectTest.projectWithTwoSegments();
        VideoSegment first = project.getSegments().get(0);
        first.setCrop(new CropRect(0.1F, 0.2F, 0.8F, 0.9F));
        first.setRotation(90);
        first.setHorizontalFlip(true);
        first.setVolume(0.5F);

        EditCommandBuilder.BuildResult result = EditCommandBuilder.build(project,
                "/tmp/filter.txt", "/tmp/output.mp4", false);
        String script = result.getFilterScript();

        assertTrue(script.contains("trim=start=1.000000:end=5.000000"));
        assertTrue(script.contains("crop=w=iw*0.700000:h=ih*0.700000"));
        assertTrue(script.contains("transpose=1"));
        assertTrue(script.contains("hflip"));
        assertTrue(script.contains("scale=1280:720"));
        assertTrue(script.contains("fps=30"));
        assertTrue(script.contains("xfade=transition=fade:duration=0.500000:offset=3.500000"));
        assertTrue(script.contains("acrossfade=d=0.500000"));
        assertTrue(script.contains("anullsrc=channel_layout=stereo"));
        assertTrue(result.getArguments().contains("libx264"));
        assertEquals("9.500000", result.getArguments().get(result.getArguments().indexOf("-t") + 1));
    }

    @Test
    public void disablesTransitionsWithConcat() {
        EditProject project = EditProjectTest.projectWithTwoSegments();
        project.getOutput().setTransitionEnabled(false);

        String script = EditCommandBuilder.build(project, "/tmp/filter.txt", "/tmp/out.mp4", true)
                .getFilterScript();

        assertTrue(script.contains("concat=n=2:v=1:a=1"));
        assertFalse(script.contains("xfade"));
    }

    @Test
    public void backgroundMusicMixesWithOriginalAudio() {
        EditProject project = EditProjectTest.projectWithTwoSegments();
        project.getOutput().setBackgroundAudioPath("/tmp/music.m4a");
        project.getOutput().setOriginalVolume(0.8F);
        project.getOutput().setBackgroundVolume(0.3F);

        EditCommandBuilder.BuildResult result = EditCommandBuilder.build(project,
                "/tmp/filter.txt", "/tmp/out.mp4", false);

        assertTrue(result.getFilterScript().contains("amix=inputs=2"));
        assertTrue(result.getFilterScript().contains("volume=0.800000"));
        assertTrue(result.getFilterScript().contains("volume=0.300000"));
        assertTrue(result.getArguments().contains("/tmp/music.m4a"));
    }

    @Test
    public void replacementAudioTakesPrecedenceOverBackground() {
        EditProject project = EditProjectTest.projectWithTwoSegments();
        project.getOutput().setReplacementAudioPath("/tmp/replacement.m4a");
        project.getOutput().setBackgroundAudioPath("/tmp/music.m4a");

        EditCommandBuilder.BuildResult result = EditCommandBuilder.build(project,
                "/tmp/filter.txt", "/tmp/out.mp4", false);

        assertTrue(result.getArguments().contains("/tmp/replacement.m4a"));
        assertFalse(result.getArguments().contains("/tmp/music.m4a"));
        assertFalse(result.getFilterScript().contains("amix"));
        assertFalse(result.getFilterScript().contains("acrossfade"));
        assertTrue(result.getFilterScript().contains("apad=whole_dur=9.500000"));
    }

    @Test
    public void singleSegmentReplacementDoesNotLeaveOriginalAudioUnconnected() {
        EditProject project = new EditProject();
        project.getSegments().add(EditProjectTest.segment(1, 1_000_000L, 4_000_000L, true));
        project.getOutput().setWidth(640);
        project.getOutput().setHeight(360);
        project.getOutput().setReplacementAudioPath("/tmp/replacement.m4a");

        String script = EditCommandBuilder.build(project, "/tmp/filter.txt", "/tmp/out.mp4", false)
                .getFilterScript();

        assertFalse(script.contains("[a0]"));
        assertTrue(script.contains("apad=whole_dur=3.000000"));
    }
}
