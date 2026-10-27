package com.xlabs.vidstb;

import android.Manifest;
import androidx.appcompat.app.AlertDialog;
import android.content.ComponentName;
import android.content.Context;
import android.content.Intent;
import android.content.ServiceConnection;
import android.content.pm.PackageManager;
import android.database.Cursor;
import android.graphics.Color;
import android.net.Uri;
import android.os.Build;
import android.os.Bundle;
import android.os.Handler;
import android.os.IBinder;
import android.os.Looper;
import android.provider.OpenableColumns;
import android.view.View;
import android.widget.AdapterView;
import android.widget.ArrayAdapter;
import android.widget.Button;
import android.widget.CheckBox;
import android.widget.EditText;
import android.widget.ProgressBar;
import android.widget.SeekBar;
import android.widget.Spinner;
import android.widget.TextView;
import android.widget.Toast;

import androidx.activity.result.ActivityResultLauncher;
import androidx.activity.result.contract.ActivityResultContracts;
import androidx.appcompat.app.AppCompatActivity;
import androidx.core.content.ContextCompat;
import androidx.core.graphics.Insets;
import androidx.core.view.ViewCompat;
import androidx.core.view.WindowInsetsCompat;
import androidx.lifecycle.ViewModelProvider;
import androidx.media3.common.MediaItem;
import androidx.media3.exoplayer.ExoPlayer;
import androidx.media3.ui.PlayerView;
import androidx.recyclerview.widget.LinearLayoutManager;
import androidx.recyclerview.widget.RecyclerView;

import com.google.android.material.dialog.MaterialAlertDialogBuilder;

import java.io.File;
import java.io.FileOutputStream;
import java.io.InputStream;
import java.io.OutputStream;
import java.util.ArrayList;
import java.util.Collections;
import java.util.List;
import java.util.Locale;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;

public class EditorActivity extends AppCompatActivity implements VideoEditService.Listener {
    private final ExecutorService executor = Executors.newSingleThreadExecutor();
    private final Handler handler = new Handler(Looper.getMainLooper());
    private EditorViewModel model;
    private EditProject project;
    private ExoPlayer player;
    private PlayerView playerView;
    private CropOverlayView cropOverlay;
    private FrameStripView frameStrip;
    private SegmentAdapter adapter;
    private EditText startTimeEdit;
    private EditText endTimeEdit;
    private TextView currentTimeText;
    private TextView projectSummaryText;
    private TextView exportStatus;
    private TextView segmentVolumeText;
    private ProgressBar exportProgress;
    private Button exportButton;
    private Button openResultButton;
    private Spinner aspectSpinner;
    private CheckBox flipHCheck;
    private CheckBox flipVCheck;
    private CheckBox segmentMuteCheck;
    private SeekBar segmentVolumeSeek;
    private boolean updatingUi;
    private boolean exporting;
    private volatile boolean destroyed;
    private boolean serviceBound;
    private int audioTarget;
    private Uri resultUri;
    private VideoEditService exportService;

    private final ActivityResultLauncher<String[]> videoPicker = registerForActivityResult(
            new ActivityResultContracts.OpenMultipleDocuments(), uris -> {
                if (uris != null && !uris.isEmpty()) importVideos(uris);
            });
    private final ActivityResultLauncher<String[]> audioPicker = registerForActivityResult(
            new ActivityResultContracts.OpenDocument(), uri -> {
                if (uri != null) importAudio(uri);
            });
    private final ActivityResultLauncher<String[]> permissionRequest = registerForActivityResult(
            new ActivityResultContracts.RequestMultiplePermissions(), result -> {
                boolean granted = true;
                for (Boolean value : result.values()) granted &= Boolean.TRUE.equals(value);
                if (granted) startExport();
                else Toast.makeText(this, "需要通知和存储权限才能导出", Toast.LENGTH_LONG).show();
            });

    private final Runnable timeUpdater = new Runnable() {
        @Override
        public void run() {
            VideoSegment segment = selected();
            if (segment != null && player != null) {
                ((Button) findViewById(R.id.playPauseButton)).setText(player.isPlaying() ? "暂停" : "播放");
                long positionUs = player.getCurrentPosition() * 1000L;
                currentTimeText.setText(TimeCode.format(positionUs));
                frameStrip.setPosition(positionUs, true);
                if (positionUs >= segment.getEndUs() && player.isPlaying()) {
                    player.pause();
                    player.seekTo(segment.getEndUs() / 1000L);
                }
            }
            handler.postDelayed(this, 80L);
        }
    };

    private final ServiceConnection serviceConnection = new ServiceConnection() {
        @Override
        public void onServiceConnected(ComponentName name, IBinder binder) {
            exportService = ((VideoEditService.LocalBinder) binder).getService();
            exportService.addListener(EditorActivity.this);
            serviceBound = true;
            exporting = exportService.isRunning();
            resultUri = exportService.getResultUri();
            if (exporting) {
                exportStatus.setText(exportService.getStatus());
                exportProgress.setVisibility(View.VISIBLE);
                exportProgress.setProgress(exportService.getProgress(), true);
            }
            updateExportControls();
        }

        @Override
        public void onServiceDisconnected(ComponentName name) {
            if (exportService != null) exportService.removeListener(EditorActivity.this);
            exportService = null;
            serviceBound = false;
        }
    };

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        setContentView(R.layout.activity_editor);
        applyInsets();
        model = new ViewModelProvider(this).get(EditorViewModel.class);
        project = model.getProject();
        bindViews();
        player = new ExoPlayer.Builder(this).build();
        playerView.setPlayer(player);
        adapter = new SegmentAdapter(project.getSegments(), this::selectSegment);
        RecyclerView list = findViewById(R.id.segmentList);
        list.setLayoutManager(new LinearLayoutManager(this, LinearLayoutManager.HORIZONTAL, false));
        list.setAdapter(adapter);
        setupActions();
        handler.post(timeUpdater);
        if (model.getSelectedIndex() >= 0 && model.getSelectedIndex() < project.getSegments().size()) {
            selectSegment(model.getSelectedIndex());
        } else {
            refreshTimeline();
        }
    }

    private void bindViews() {
        playerView = findViewById(R.id.playerView);
        cropOverlay = findViewById(R.id.cropOverlay);
        frameStrip = findViewById(R.id.frameStrip);
        startTimeEdit = findViewById(R.id.startTimeEdit);
        endTimeEdit = findViewById(R.id.endTimeEdit);
        currentTimeText = findViewById(R.id.currentTimeText);
        projectSummaryText = findViewById(R.id.projectSummaryText);
        exportStatus = findViewById(R.id.exportStatus);
        segmentVolumeText = findViewById(R.id.segmentVolumeText);
        exportProgress = findViewById(R.id.exportProgress);
        exportButton = findViewById(R.id.exportButton);
        openResultButton = findViewById(R.id.openEditResultButton);
        aspectSpinner = findViewById(R.id.aspectSpinner);
        flipHCheck = findViewById(R.id.flipHCheck);
        flipVCheck = findViewById(R.id.flipVCheck);
        segmentMuteCheck = findViewById(R.id.segmentMuteCheck);
        segmentVolumeSeek = findViewById(R.id.segmentVolumeSeek);
    }

    private void setupActions() {
        frameStrip.setOnInteractionListener(new FrameStripView.OnInteractionListener() {
            @Override
            public void onSeek(long positionUs) {
                player.pause();
                player.seekTo(positionUs / 1000L);
                currentTimeText.setText(TimeCode.format(positionUs));
            }

            @Override
            public void onRangeChanged(long startUs, long endUs, boolean finished) {
                VideoSegment segment = selected();
                if (segment == null) return;
                segment.setStartUs(startUs);
                segment.setEndUs(endUs);
                startTimeEdit.setText(TimeCode.format(startUs));
                endTimeEdit.setText(TimeCode.format(endUs));
                if (finished) refreshTimeline();
            }
        });
        findViewById(R.id.backEditorButton).setOnClickListener(v -> finish());
        findViewById(R.id.addVideosButton).setOnClickListener(v ->
                videoPicker.launch(new String[]{"video/*"}));
        findViewById(R.id.addSegmentButton).setOnClickListener(v -> addSameSourceSegment());
        findViewById(R.id.duplicateSegmentButton).setOnClickListener(v -> duplicateSegment());
        findViewById(R.id.deleteSegmentButton).setOnClickListener(v -> deleteSegment());
        findViewById(R.id.moveLeftButton).setOnClickListener(v -> moveSegment(-1));
        findViewById(R.id.moveRightButton).setOnClickListener(v -> moveSegment(1));
        findViewById(R.id.playPauseButton).setOnClickListener(v -> togglePlayback());
        findViewById(R.id.previousFrameButton).setOnClickListener(v -> stepFrame(-1));
        findViewById(R.id.nextFrameButton).setOnClickListener(v -> stepFrame(1));
        findViewById(R.id.setStartButton).setOnClickListener(v -> setBoundary(true));
        findViewById(R.id.setEndButton).setOnClickListener(v -> setBoundary(false));
        findViewById(R.id.applyTimesButton).setOnClickListener(v -> applyTimes());
        findViewById(R.id.resetCropButton).setOnClickListener(v -> {
            VideoSegment segment = selected();
            if (segment != null) {
                segment.setCrop(CropRect.full());
                cropOverlay.setCrop(segment.getCrop());
                refreshTimeline();
            }
        });
        findViewById(R.id.rotateButton).setOnClickListener(v -> {
            VideoSegment segment = selected();
            if (segment != null) {
                segment.setRotation(segment.getRotation() + 90);
                updatePreviewTransform();
            }
        });
        cropOverlay.setOnCropChangeListener(crop -> {
            VideoSegment segment = selected();
            if (!updatingUi && segment != null) segment.setCrop(crop);
        });
        ArrayAdapter<String> aspects = new ArrayAdapter<>(this, android.R.layout.simple_spinner_item,
                new String[]{"自由", "16:9", "9:16", "1:1", "4:3"});
        aspects.setDropDownViewResource(android.R.layout.simple_spinner_dropdown_item);
        aspectSpinner.setAdapter(aspects);
        aspectSpinner.setOnItemSelectedListener(new AdapterView.OnItemSelectedListener() {
            @Override
            public void onItemSelected(AdapterView<?> parent, View view, int position, long id) {
                if (!updatingUi) {
                    float[] ratios = {0F, 16F / 9F, 9F / 16F, 1F, 4F / 3F};
                    cropOverlay.setAspectRatio(ratios[position]);
                }
            }
            @Override public void onNothingSelected(AdapterView<?> parent) { }
        });
        flipHCheck.setOnCheckedChangeListener((button, checked) -> {
            VideoSegment segment = selected();
            if (!updatingUi && segment != null) {
                segment.setHorizontalFlip(checked);
                updatePreviewTransform();
            }
        });
        flipVCheck.setOnCheckedChangeListener((button, checked) -> {
            VideoSegment segment = selected();
            if (!updatingUi && segment != null) {
                segment.setVerticalFlip(checked);
                updatePreviewTransform();
            }
        });
        segmentMuteCheck.setOnCheckedChangeListener((button, checked) -> {
            VideoSegment segment = selected();
            if (!updatingUi && segment != null) {
                segment.setMuted(checked);
                updatePlayerVolume();
            }
        });
        segmentVolumeSeek.setOnSeekBarChangeListener(new SimpleSeekListener() {
            @Override
            public void onProgressChanged(SeekBar seekBar, int progress, boolean fromUser) {
                VideoSegment segment = selected();
                segmentVolumeText.setText("片段音量 " + progress + "%");
                if (!updatingUi && fromUser && segment != null) {
                    segment.setVolume(progress / 100F);
                    updatePlayerVolume();
                }
            }
        });
        findViewById(R.id.outputSettingsButton).setOnClickListener(v -> showOutputSettings());
        findViewById(R.id.audioSettingsButton).setOnClickListener(v -> showAudioSettings());
        exportButton.setOnClickListener(v -> startExport());
        openResultButton.setOnClickListener(v -> openResult());
        findViewById(R.id.cancelEditExportButton).setOnClickListener(v -> {
            if (exportService != null) exportService.cancelConversion();
        });
    }

    private void importVideos(List<Uri> uris) {
        if (exporting) return;
        exportStatus.setText("正在导入并分析视频…");
        executor.execute(() -> {
            try {
                File work = workDirectory();
                List<VideoSegment> added = new ArrayList<>();
                for (Uri uri : uris) {
                    String name = safeFileName(queryDisplayName(uri));
                    File destination = new File(work, "source_" + System.nanoTime() + "_" + name);
                    copyUri(uri, destination);
                    MediaProbe.Result probe = MediaProbe.probe(destination);
                    VideoMetadata metadata = probe.getMetadata();
                    if (metadata.getDurationUs() <= 0L) throw new IllegalArgumentException(name + " 时长无效");
                    added.add(new VideoSegment(model.nextId(), destination.getAbsolutePath(), name,
                            metadata.getDurationUs(), 0L, metadata.getDurationUs(), metadata.hasAudio(),
                            metadata.getWidth(), metadata.getHeight(), metadata.getFrameRate()));
                }
                runOnUiThread(() -> {
                    if (destroyed) return;
                    if (project.getSegments().isEmpty() && !added.isEmpty()) {
                        VideoMetadata metadata = null;
                        VideoSegment first = added.get(0);
                        project.getOutput().setWidth(even(first.getSourceWidth()));
                        project.getOutput().setHeight(even(first.getSourceHeight()));
                        project.getOutput().setFrameRate(Math.max(1, Math.min(120,
                                (int) Math.round(first.getSourceFrameRate()))));
                    }
                    int previousSize = project.getSegments().size();
                    project.getSegments().addAll(added);
                    exportStatus.setText("已添加 " + added.size() + " 个视频");
                    selectSegment(previousSize);
                });
            } catch (Exception error) {
                runOnUiThread(() -> exportStatus.setText("导入失败：" + message(error)));
            }
        });
    }

    private void importAudio(Uri uri) {
        executor.execute(() -> {
            try {
                File destination = new File(workDirectory(), "audio_" + System.nanoTime() + "_"
                        + safeFileName(queryDisplayName(uri)));
                copyUri(uri, destination);
                runOnUiThread(() -> {
                    if (destroyed) return;
                    if (audioTarget == 1) project.getOutput().setReplacementAudioPath(destination.getAbsolutePath());
                    if (audioTarget == 2) project.getOutput().setBackgroundAudioPath(destination.getAbsolutePath());
                    exportStatus.setText("音频已添加");
                    refreshTimeline();
                });
            } catch (Exception error) {
                runOnUiThread(() -> exportStatus.setText("音频导入失败：" + message(error)));
            }
        });
    }

    private File workDirectory() {
        if (!model.getWorkDirectory().isEmpty()) return new File(model.getWorkDirectory());
        File root = getExternalFilesDir(null);
        if (root == null) root = getFilesDir();
        File directory = new File(root, "edit/" + System.currentTimeMillis());
        if (!directory.mkdirs() && !directory.isDirectory()) throw new IllegalStateException("无法创建编辑目录");
        model.setWorkDirectory(directory.getAbsolutePath());
        return directory;
    }

    private void selectSegment(int index) {
        if (destroyed || index < 0 || index >= project.getSegments().size()) return;
        model.setSelectedIndex(index);
        updatingUi = true;
        VideoSegment segment = selected();
        startTimeEdit.setText(TimeCode.format(segment.getStartUs()));
        endTimeEdit.setText(TimeCode.format(segment.getEndUs()));
        flipHCheck.setChecked(segment.isHorizontalFlip());
        flipVCheck.setChecked(segment.isVerticalFlip());
        segmentMuteCheck.setChecked(segment.isMuted());
        segmentVolumeSeek.setProgress(Math.round(segment.getVolume() * 100F));
        segmentVolumeText.setText("片段音量 " + Math.round(segment.getVolume() * 100F) + "%");
        cropOverlay.setCrop(segment.getCrop());
        cropOverlay.setVisibility(View.VISIBLE);
        frameStrip.setVideo(segment.getSourcePath(), segment.getSourceDurationUs(),
                segment.getStartUs(), segment.getEndUs(), segment.getStartUs());
        aspectSpinner.setSelection(0, false);
        updatingUi = false;
        loadPreview();
        refreshTimeline();
    }

    private void loadPreview() {
        VideoSegment segment = selected();
        if (segment == null) {
            player.stop();
            player.clearMediaItems();
            return;
        }
        player.stop();
        player.setMediaItem(MediaItem.fromUri(Uri.fromFile(new File(segment.getSourcePath()))));
        player.prepare();
        player.seekTo(segment.getStartUs() / 1000L);
        updatePreviewTransform();
        updatePlayerVolume();
    }

    private void togglePlayback() {
        VideoSegment segment = selected();
        if (segment == null) return;
        if (player.isPlaying()) player.pause();
        else {
            if (player.getCurrentPosition() * 1000L >= segment.getEndUs()) {
                player.seekTo(segment.getStartUs() / 1000L);
            }
            player.play();
        }
    }

    private void stepFrame(int direction) {
        VideoSegment segment = selected();
        if (segment == null) return;
        player.pause();
        long stepMs = Math.max(1L, Math.round(1000D / segment.getSourceFrameRate()));
        long target = player.getCurrentPosition() + direction * stepMs;
        target = Math.max(segment.getStartUs() / 1000L, Math.min(segment.getEndUs() / 1000L, target));
        player.seekTo(target);
    }

    private void setBoundary(boolean start) {
        VideoSegment segment = selected();
        if (segment == null) return;
        long position = player.getCurrentPosition() * 1000L;
        long frame = Math.max(1L, Math.round(1_000_000D / segment.getSourceFrameRate()));
        if (start && position < segment.getEndUs() - frame) segment.setStartUs(Math.max(0L, position));
        if (!start && position > segment.getStartUs() + frame) segment.setEndUs(
                Math.min(segment.getSourceDurationUs(), position));
        startTimeEdit.setText(TimeCode.format(segment.getStartUs()));
        endTimeEdit.setText(TimeCode.format(segment.getEndUs()));
        frameStrip.setRange(segment.getStartUs(), segment.getEndUs());
        refreshTimeline();
    }

    private void applyTimes() {
        VideoSegment segment = selected();
        if (segment == null) return;
        try {
            long start = TimeCode.parse(startTimeEdit.getText().toString());
            long end = TimeCode.parse(endTimeEdit.getText().toString());
            if (start < 0L || end > segment.getSourceDurationUs() || start >= end) {
                throw new IllegalArgumentException("起止时间超出范围");
            }
            segment.setStartUs(start);
            segment.setEndUs(end);
            player.seekTo(start / 1000L);
            frameStrip.setRange(start, end);
            refreshTimeline();
        } catch (Exception error) {
            Toast.makeText(this, "时间无效：" + message(error), Toast.LENGTH_LONG).show();
        }
    }

    private void addSameSourceSegment() {
        VideoSegment source = selected();
        if (source == null) return;
        VideoSegment added = new VideoSegment(model.nextId(), source.getSourcePath(), source.getDisplayName(),
                source.getSourceDurationUs(), 0L, source.getSourceDurationUs(), source.hasAudio(),
                source.getSourceWidth(), source.getSourceHeight(), source.getSourceFrameRate());
        project.getSegments().add(model.getSelectedIndex() + 1, added);
        selectSegment(model.getSelectedIndex() + 1);
    }

    private void duplicateSegment() {
        VideoSegment source = selected();
        if (source == null) return;
        project.getSegments().add(model.getSelectedIndex() + 1, source.copy(model.nextId()));
        selectSegment(model.getSelectedIndex() + 1);
    }

    private void deleteSegment() {
        if (selected() == null) return;
        project.getSegments().remove(model.getSelectedIndex());
        if (project.getSegments().isEmpty()) {
            model.setSelectedIndex(-1);
            cropOverlay.setVisibility(View.GONE);
            frameStrip.clear();
            loadPreview();
            refreshTimeline();
        } else selectSegment(Math.min(model.getSelectedIndex(), project.getSegments().size() - 1));
    }

    private void moveSegment(int direction) {
        int index = model.getSelectedIndex();
        int target = index + direction;
        if (index < 0 || target < 0 || target >= project.getSegments().size()) return;
        Collections.swap(project.getSegments(), index, target);
        selectSegment(target);
    }

    private void refreshTimeline() {
        adapter.update(project.getSegments(), model.getSelectedIndex());
        OutputSettings output = project.getOutput();
        String summary = project.getSegments().size() + " 个片段 · 输出 "
                + TimeCode.format(project.getOutputDurationUs()) + " · "
                + output.getWidth() + "×" + output.getHeight() + " · " + output.getFrameRate() + " fps";
        if (!output.getReplacementAudioPath().isEmpty()) summary += "\n替换整条音轨";
        else if (!output.getBackgroundAudioPath().isEmpty()) summary += "\n原声与背景音乐混音";
        if (output.isGlobalMute()) summary += " · 全程静音";
        projectSummaryText.setText(summary);
        updateExportControls();
    }

    private void updatePreviewTransform() {
        VideoSegment segment = selected();
        if (segment == null) return;
        playerView.setRotation(segment.getRotation());
        playerView.setScaleX(segment.isHorizontalFlip() ? -1F : 1F);
        playerView.setScaleY(segment.isVerticalFlip() ? -1F : 1F);
    }

    private void updatePlayerVolume() {
        VideoSegment segment = selected();
        if (segment != null) player.setVolume(segment.isMuted() ? 0F : segment.getVolume());
    }

    private void showOutputSettings() {
        OutputSettings output = project.getOutput();
        View view = getLayoutInflater().inflate(R.layout.dialog_output_settings, null);
        EditText width = view.findViewById(R.id.outputWidthEdit);
        EditText height = view.findViewById(R.id.outputHeightEdit);
        EditText frameRate = view.findViewById(R.id.outputFrameRateEdit);
        EditText transition = view.findViewById(R.id.transitionMsEdit);
        CheckBox transitionEnabled = view.findViewById(R.id.transitionEnabledCheck);
        Spinner resolutions = view.findViewById(R.id.resolutionPreset);
        Spinner frameRates = view.findViewById(R.id.frameRatePreset);
        width.setText(String.valueOf(output.getWidth()));
        height.setText(String.valueOf(output.getHeight()));
        frameRate.setText(String.valueOf(output.getFrameRate()));
        transition.setText(String.valueOf(output.getTransitionMs()));
        transitionEnabled.setChecked(output.isTransitionEnabled());
        ArrayAdapter<String> resolutionAdapter = new ArrayAdapter<>(this,
                android.R.layout.simple_spinner_item, new String[]{"自定义", "640×360", "1280×720", "1920×1080"});
        resolutionAdapter.setDropDownViewResource(android.R.layout.simple_spinner_dropdown_item);
        resolutions.setAdapter(resolutionAdapter);
        resolutions.setOnItemSelectedListener(new AdapterView.OnItemSelectedListener() {
            @Override public void onItemSelected(AdapterView<?> parent, View v, int position, long id) {
                if (position > 0) {
                    String[] values = parent.getItemAtPosition(position).toString().split("×");
                    width.setText(values[0]);
                    height.setText(values[1]);
                }
            }
            @Override public void onNothingSelected(AdapterView<?> parent) { }
        });
        ArrayAdapter<String> fpsAdapter = new ArrayAdapter<>(this, android.R.layout.simple_spinner_item,
                new String[]{"自定义", "24", "25", "30", "50", "60"});
        fpsAdapter.setDropDownViewResource(android.R.layout.simple_spinner_dropdown_item);
        frameRates.setAdapter(fpsAdapter);
        frameRates.setOnItemSelectedListener(new AdapterView.OnItemSelectedListener() {
            @Override public void onItemSelected(AdapterView<?> parent, View v, int position, long id) {
                if (position > 0) frameRate.setText(parent.getItemAtPosition(position).toString());
            }
            @Override public void onNothingSelected(AdapterView<?> parent) { }
        });
        AlertDialog dialog = new MaterialAlertDialogBuilder(this).setTitle("输出设置").setView(view)
                .setNegativeButton("取消", null).setPositiveButton("应用", null).create();
        dialog.setOnShowListener(ignored -> dialog.getButton(AlertDialog.BUTTON_POSITIVE).setOnClickListener(v -> {
            try {
                output.setWidth(Integer.parseInt(width.getText().toString()));
                output.setHeight(Integer.parseInt(height.getText().toString()));
                output.setFrameRate(Integer.parseInt(frameRate.getText().toString()));
                output.setTransitionMs(Integer.parseInt(transition.getText().toString()));
                output.setTransitionEnabled(transitionEnabled.isChecked());
                project.validate();
                refreshTimeline();
                dialog.dismiss();
            } catch (Exception error) {
                Toast.makeText(this, message(error), Toast.LENGTH_LONG).show();
            }
        }));
        dialog.show();
    }

    private void showAudioSettings() {
        OutputSettings output = project.getOutput();
        View view = getLayoutInflater().inflate(R.layout.dialog_audio_settings, null);
        SeekBar master = view.findViewById(R.id.masterVolumeSeek);
        SeekBar original = view.findViewById(R.id.originalVolumeSeek);
        SeekBar background = view.findViewById(R.id.backgroundVolumeSeek);
        CheckBox mute = view.findViewById(R.id.globalMuteCheck);
        master.setProgress(Math.round(output.getMasterVolume() * 100F));
        original.setProgress(Math.round(output.getOriginalVolume() * 100F));
        background.setProgress(Math.round(output.getBackgroundVolume() * 100F));
        mute.setChecked(output.isGlobalMute());
        TextView masterText = view.findViewById(R.id.masterVolumeText);
        TextView originalText = view.findViewById(R.id.originalVolumeText);
        TextView backgroundText = view.findViewById(R.id.backgroundVolumeText);
        masterText.setText("整体音量 " + master.getProgress() + "%");
        originalText.setText("混音原声音量 " + original.getProgress() + "%");
        backgroundText.setText("背景音乐音量 " + background.getProgress() + "%");
        ((TextView) view.findViewById(R.id.replacementAudioPathText)).setText(
                output.getReplacementAudioPath().isEmpty() ? "未选择，使用原视频声音" : output.getReplacementAudioPath());
        ((TextView) view.findViewById(R.id.backgroundAudioPathText)).setText(
                output.getBackgroundAudioPath().isEmpty() ? "未选择背景音乐" : output.getBackgroundAudioPath());
        master.setOnSeekBarChangeListener(new SimpleSeekListener() {
            @Override public void onProgressChanged(SeekBar bar, int value, boolean fromUser) {
                output.setMasterVolume(value / 100F);
                masterText.setText("整体音量 " + value + "%");
            }
        });
        original.setOnSeekBarChangeListener(new SimpleSeekListener() {
            @Override public void onProgressChanged(SeekBar bar, int value, boolean fromUser) {
                output.setOriginalVolume(value / 100F);
                originalText.setText("混音原声音量 " + value + "%");
            }
        });
        background.setOnSeekBarChangeListener(new SimpleSeekListener() {
            @Override public void onProgressChanged(SeekBar bar, int value, boolean fromUser) {
                output.setBackgroundVolume(value / 100F);
                backgroundText.setText("背景音乐音量 " + value + "%");
            }
        });
        mute.setOnCheckedChangeListener((button, checked) -> output.setGlobalMute(checked));
        AlertDialog dialog = new MaterialAlertDialogBuilder(this).setTitle("音频设置").setView(view)
                .setPositiveButton("完成", (d, w) -> refreshTimeline()).create();
        view.findViewById(R.id.replacementAudioButton).setOnClickListener(v -> {
            if (!output.getReplacementAudioPath().isEmpty()) {
                output.setReplacementAudioPath("");
                ((TextView) view.findViewById(R.id.replacementAudioPathText)).setText("未选择，使用原视频声音");
            } else {
                audioTarget = 1;
                dialog.dismiss();
                audioPicker.launch(new String[]{"audio/*", "*/*"});
            }
        });
        view.findViewById(R.id.backgroundAudioButton).setOnClickListener(v -> {
            if (!output.getBackgroundAudioPath().isEmpty()) {
                output.setBackgroundAudioPath("");
                ((TextView) view.findViewById(R.id.backgroundAudioPathText)).setText("未选择背景音乐");
            } else {
                audioTarget = 2;
                dialog.dismiss();
                audioPicker.launch(new String[]{"audio/*", "*/*"});
            }
        });
        dialog.show();
    }

    private void startExport() {
        if (exporting) return;
        try {
            project.validate();
        } catch (Exception error) {
            Toast.makeText(this, message(error), Toast.LENGTH_LONG).show();
            return;
        }
        List<String> missing = missingPermissions();
        if (!missing.isEmpty()) {
            permissionRequest.launch(missing.toArray(new String[0]));
            return;
        }
        Intent intent = new Intent(this, VideoEditService.class)
                .putExtra(VideoEditService.EXTRA_PROJECT, project)
                .putExtra(VideoEditService.EXTRA_HARDWARE,
                        getSharedPreferences("vidstb_settings", MODE_PRIVATE).getBoolean("hardware_acceleration", true));
        exporting = true;
        resultUri = null;
        exportProgress.setVisibility(View.VISIBLE);
        exportProgress.setProgress(0, true);
        exportStatus.setText("正在准备导出…");
        updateExportControls();
        ContextCompat.startForegroundService(this, intent);
    }

    private List<String> missingPermissions() {
        List<String> permissions = new ArrayList<>();
        if (Build.VERSION.SDK_INT >= 33 && ContextCompat.checkSelfPermission(this,
                Manifest.permission.POST_NOTIFICATIONS) != PackageManager.PERMISSION_GRANTED) {
            permissions.add(Manifest.permission.POST_NOTIFICATIONS);
        }
        if (Build.VERSION.SDK_INT <= 28 && ContextCompat.checkSelfPermission(this,
                Manifest.permission.WRITE_EXTERNAL_STORAGE) != PackageManager.PERMISSION_GRANTED) {
            permissions.add(Manifest.permission.WRITE_EXTERNAL_STORAGE);
        }
        return permissions;
    }

    private void updateExportControls() {
        boolean hasSelection = selected() != null;
        exportButton.setEnabled(!exporting && !project.getSegments().isEmpty());
        openResultButton.setEnabled(resultUri != null && !exporting);
        findViewById(R.id.cancelEditExportButton).setEnabled(exporting);
        findViewById(R.id.addVideosButton).setEnabled(!exporting);
        findViewById(R.id.outputSettingsButton).setEnabled(!exporting);
        findViewById(R.id.audioSettingsButton).setEnabled(!exporting);
        int[] segmentActions = {R.id.playPauseButton, R.id.previousFrameButton, R.id.nextFrameButton,
                R.id.applyTimesButton, R.id.setStartButton, R.id.setEndButton, R.id.addSegmentButton,
                R.id.duplicateSegmentButton, R.id.deleteSegmentButton, R.id.moveLeftButton,
                R.id.moveRightButton, R.id.resetCropButton, R.id.rotateButton};
        for (int id : segmentActions) {
            findViewById(id).setEnabled(!exporting && hasSelection);
        }
        flipHCheck.setEnabled(!exporting && hasSelection);
        flipVCheck.setEnabled(!exporting && hasSelection);
        segmentMuteCheck.setEnabled(!exporting && hasSelection);
        segmentVolumeSeek.setEnabled(!exporting && hasSelection);
        aspectSpinner.setEnabled(!exporting && hasSelection);
        frameStrip.setEnabled(!exporting && hasSelection);
    }

    private void openResult() {
        if (resultUri == null) return;
        try {
            startActivity(Intent.createChooser(new Intent(Intent.ACTION_VIEW)
                    .setDataAndType(resultUri, "video/mp4")
                    .addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION), "打开视频"));
        } catch (RuntimeException error) {
            Toast.makeText(this, "没有找到可播放视频的应用", Toast.LENGTH_LONG).show();
        }
    }

    private void applyInsets() {
        View root = findViewById(R.id.editorRoot);
        ViewCompat.setOnApplyWindowInsetsListener(root, (view, windowInsets) -> {
            Insets insets = windowInsets.getInsets(WindowInsetsCompat.Type.systemBars()
                    | WindowInsetsCompat.Type.displayCutout());
            view.setPadding(insets.left, insets.top, insets.right, insets.bottom);
            return windowInsets;
        });
    }

    private VideoSegment selected() {
        int index = model.getSelectedIndex();
        return index >= 0 && index < project.getSegments().size() ? project.getSegments().get(index) : null;
    }

    private String queryDisplayName(Uri uri) {
        try (Cursor cursor = getContentResolver().query(uri,
                new String[]{OpenableColumns.DISPLAY_NAME}, null, null, null)) {
            if (cursor != null && cursor.moveToFirst()) return cursor.getString(0);
        } catch (RuntimeException ignored) { }
        return audioTarget == 0 ? "video.mkv" : "audio.m4a";
    }

    private void copyUri(Uri uri, File destination) throws Exception {
        try (InputStream input = getContentResolver().openInputStream(uri);
             OutputStream output = new FileOutputStream(destination)) {
            if (input == null) throw new IllegalArgumentException("无法打开文件");
            byte[] buffer = new byte[1024 * 1024];
            int count;
            while ((count = input.read(buffer)) != -1) output.write(buffer, 0, count);
        }
    }

    private static int even(int value) {
        return Math.max(16, value - value % 2);
    }

    private static String safeFileName(String value) {
        return value == null ? "media" : value.replaceAll("[\\\\/:*?\"<>|']", "_");
    }

    private static String message(Exception error) {
        return error.getMessage() == null ? error.getClass().getSimpleName() : error.getMessage();
    }

    @Override
    protected void onStart() {
        super.onStart();
        bindService(new Intent(this, VideoEditService.class), serviceConnection, Context.BIND_AUTO_CREATE);
    }

    @Override
    protected void onStop() {
        if (serviceBound) {
            exportService.removeListener(this);
            unbindService(serviceConnection);
            serviceBound = false;
            exportService = null;
        }
        if (player != null) player.pause();
        super.onStop();
    }

    @Override
    protected void onDestroy() {
        destroyed = true;
        handler.removeCallbacks(timeUpdater);
        if (player != null) player.release();
        if (adapter != null) adapter.close();
        if (frameStrip != null) frameStrip.close();
        executor.shutdownNow();
        super.onDestroy();
    }

    @Override
    public void onConversionProgress(int percent, long timeMs) {
        runOnUiThread(() -> {
            exportProgress.setVisibility(View.VISIBLE);
            exportProgress.setProgress(percent, true);
        });
    }

    @Override
    public void onConversionState(String state) {
        runOnUiThread(() -> exportStatus.setText(state));
    }

    @Override
    public void onConversionFinished(boolean success, String message, Uri output) {
        runOnUiThread(() -> {
            exporting = false;
            resultUri = output;
            exportStatus.setText(message);
            exportProgress.setVisibility(success ? View.VISIBLE : View.GONE);
            if (success) exportProgress.setProgress(100, true);
            updateExportControls();
        });
    }

    private abstract static class SimpleSeekListener implements SeekBar.OnSeekBarChangeListener {
        @Override public void onStartTrackingTouch(SeekBar seekBar) { }
        @Override public void onStopTrackingTouch(SeekBar seekBar) { }
    }
}
