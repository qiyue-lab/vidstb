package com.xlabs.vidstb;

import java.util.ArrayList;
import java.util.Arrays;
import java.util.Collections;
import java.util.List;
import java.util.Locale;

public final class EditCommandBuilder {
    private EditCommandBuilder() {
    }

    public static BuildResult build(EditProject project, String scriptPath, String outputPath,
                                    boolean hardware) {
        project.validate();
        List<VideoSegment> segments = project.getSegments();
        OutputSettings output = project.getOutput();
        List<String> args = new ArrayList<>(Arrays.asList("-hide_banner", "-y"));
        for (VideoSegment segment : segments) {
            args.add("-i");
            args.add(segment.getSourcePath());
        }
        boolean replacement = !output.getReplacementAudioPath().isEmpty();
        boolean background = !replacement && !output.getBackgroundAudioPath().isEmpty();
        int replacementIndex = segments.size();
        if (replacement) {
            args.add("-i");
            args.add(output.getReplacementAudioPath());
        }
        int backgroundIndex = segments.size() + (replacement ? 1 : 0);
        if (background) {
            args.add("-i");
            args.add(output.getBackgroundAudioPath());
        }
        args.add("-filter_complex_script");
        args.add(scriptPath);

        StringBuilder script = new StringBuilder();
        for (int i = 0; i < segments.size(); i++) {
            appendVideoChain(script, segments.get(i), output, i);
            if (!replacement) {
                appendAudioChain(script, segments.get(i), i);
            }
        }
        boolean transitions = output.isTransitionEnabled() && segments.size() > 1
                && output.getTransitionMs() > 0;
        String videoLabel = "v0";
        String audioLabel = "a0";
        if (segments.size() == 1) {
            script.append("[v0]null[vmain];");
            if (!replacement) {
                script.append("[a0]anull[amain];");
            }
            videoLabel = "vmain";
            audioLabel = "amain";
        } else if (!transitions) {
            for (int i = 0; i < segments.size(); i++) {
                script.append("[v").append(i).append("]");
                if (!replacement) script.append("[a").append(i).append("]");
            }
            script.append("concat=n=").append(segments.size()).append(":v=1:a=")
                    .append(replacement ? 0 : 1).append("[vmain]");
            if (!replacement) script.append("[amain]");
            script.append(";");
            videoLabel = "vmain";
            audioLabel = "amain";
        } else {
            double transitionSeconds = output.getTransitionMs() / 1000D;
            long completedDurationUs = segments.get(0).getDurationUs();
            for (int i = 1; i < segments.size(); i++) {
                String nextVideo = i == segments.size() - 1 ? "vmain" : "vx" + i;
                String nextAudio = i == segments.size() - 1 ? "amain" : "ax" + i;
                double offset = (completedDurationUs - i * output.getTransitionMs() * 1000L) / 1_000_000D;
                script.append("[").append(videoLabel).append("][v").append(i).append("]")
                        .append("xfade=transition=fade:duration=").append(seconds(transitionSeconds))
                        .append(":offset=").append(seconds(offset)).append("[").append(nextVideo).append("];");
                if (!replacement) {
                    script.append("[").append(audioLabel).append("][a").append(i).append("]")
                            .append("acrossfade=d=").append(seconds(transitionSeconds))
                            .append("[").append(nextAudio).append("];");
                }
                videoLabel = nextVideo;
                audioLabel = nextAudio;
                completedDurationUs += segments.get(i).getDurationUs();
            }
        }

        float masterVolume = output.isGlobalMute() ? 0F : output.getMasterVolume();
        if (replacement) {
            script.append("[").append(replacementIndex).append(":a]aformat=sample_fmts=fltp:")
                    .append("sample_rates=48000:channel_layouts=stereo,apad=whole_dur=")
                    .append(seconds(project.getOutputDurationUs())).append(",atrim=duration=")
                    .append(seconds(project.getOutputDurationUs())).append(",asetpts=PTS-STARTPTS,volume=")
                    .append(number(masterVolume)).append("[aout]");
        } else if (background) {
            script.append("[").append(audioLabel).append("]volume=").append(number(output.getOriginalVolume()))
                    .append("[aoriginal];");
            script.append("[").append(backgroundIndex).append(":a]aformat=sample_fmts=fltp:")
                    .append("sample_rates=48000:channel_layouts=stereo,apad=whole_dur=")
                    .append(seconds(project.getOutputDurationUs())).append(",atrim=duration=")
                    .append(seconds(project.getOutputDurationUs())).append(",asetpts=PTS-STARTPTS,volume=")
                    .append(number(output.getBackgroundVolume())).append("[abackground];");
            script.append("[aoriginal][abackground]amix=inputs=2:duration=first:normalize=0,volume=")
                    .append(number(masterVolume)).append("[aout]");
        } else {
            script.append("[").append(audioLabel).append("]volume=").append(number(masterVolume))
                    .append("[aout]");
        }
        args.addAll(Arrays.asList("-map", "[" + videoLabel + "]", "-map", "[aout]",
                "-c:v", hardware ? "h264_mediacodec" : "libx264"));
        if (hardware) {
            VideoMetadata metadata = new VideoMetadata(output.getWidth(), output.getHeight(), 0L,
                    project.getOutputDurationUs());
            args.addAll(Arrays.asList("-b:v", String.valueOf(FFmpegCommandBuilder.hardwareBitRate(metadata)),
                    "-pix_fmt", "yuv420p"));
        } else {
            args.addAll(Arrays.asList("-preset", "slow", "-crf", "16", "-pix_fmt", "yuv420p"));
        }
        args.addAll(Arrays.asList("-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart",
                "-t", seconds(project.getOutputDurationUs()), outputPath));
        return new BuildResult(args, script.toString());
    }

    private static void appendVideoChain(StringBuilder script, VideoSegment segment,
                                         OutputSettings output, int index) {
        CropRect crop = segment.getCrop();
        script.append("[").append(index).append(":v]trim=start=").append(seconds(segment.getStartUs()))
                .append(":end=").append(seconds(segment.getEndUs())).append(",setpts=PTS-STARTPTS");
        if (!crop.isFull()) {
            script.append(",crop=w=iw*").append(number(crop.getRight() - crop.getLeft()))
                    .append(":h=ih*").append(number(crop.getBottom() - crop.getTop()))
                    .append(":x=iw*").append(number(crop.getLeft()))
                    .append(":y=ih*").append(number(crop.getTop()));
        }
        if (segment.getRotation() == 90) script.append(",transpose=1");
        if (segment.getRotation() == 180) script.append(",transpose=1,transpose=1");
        if (segment.getRotation() == 270) script.append(",transpose=2");
        if (segment.isHorizontalFlip()) script.append(",hflip");
        if (segment.isVerticalFlip()) script.append(",vflip");
        script.append(",scale=").append(output.getWidth()).append(":").append(output.getHeight())
                .append(":force_original_aspect_ratio=decrease,pad=").append(output.getWidth())
                .append(":").append(output.getHeight()).append(":(ow-iw)/2:(oh-ih)/2:black")
                .append(",setsar=1,fps=").append(output.getFrameRate())
                .append(",format=yuv420p,settb=AVTB[v").append(index).append("];");
    }

    private static void appendAudioChain(StringBuilder script, VideoSegment segment, int index) {
        float volume = segment.isMuted() ? 0F : segment.getVolume();
        if (segment.hasAudio()) {
            script.append("[").append(index).append(":a]atrim=start=").append(seconds(segment.getStartUs()))
                    .append(":end=").append(seconds(segment.getEndUs())).append(",asetpts=PTS-STARTPTS,");
        } else {
            script.append("anullsrc=channel_layout=stereo:sample_rate=48000,atrim=duration=")
                    .append(seconds(segment.getDurationUs())).append(",asetpts=PTS-STARTPTS,");
        }
        script.append("aformat=sample_fmts=fltp:sample_rates=48000:channel_layouts=stereo,volume=")
                .append(number(volume)).append("[a").append(index).append("];");
    }

    private static String seconds(long microseconds) {
        return seconds(microseconds / 1_000_000D);
    }

    private static String seconds(double seconds) {
        return String.format(Locale.ROOT, "%.6f", seconds);
    }

    private static String number(float value) {
        return String.format(Locale.ROOT, "%.6f", value);
    }

    public static final class BuildResult {
        private final List<String> arguments;
        private final String filterScript;

        BuildResult(List<String> arguments, String filterScript) {
            this.arguments = Collections.unmodifiableList(new ArrayList<>(arguments));
            this.filterScript = filterScript;
        }

        public List<String> getArguments() { return arguments; }
        public String getFilterScript() { return filterScript; }
    }
}
