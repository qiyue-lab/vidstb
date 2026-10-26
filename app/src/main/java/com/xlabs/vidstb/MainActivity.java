package com.xlabs.vidstb;

import android.Manifest;
import android.content.ComponentName;
import android.content.Context;
import android.content.Intent;
import android.content.ServiceConnection;
import android.content.SharedPreferences;
import android.content.pm.PackageManager;
import android.database.Cursor;
import android.net.Uri;
import android.os.Build;
import android.os.Bundle;
import android.os.IBinder;
import android.provider.OpenableColumns;
import android.view.View;
import android.widget.CheckBox;
import android.widget.LinearLayout;
import android.widget.ProgressBar;
import android.widget.TextView;
import android.widget.Toast;

import androidx.activity.result.ActivityResultLauncher;
import androidx.activity.result.contract.ActivityResultContracts;
import androidx.appcompat.app.AppCompatActivity;
import androidx.core.content.ContextCompat;

import com.google.android.material.button.MaterialButton;
import com.google.android.material.dialog.MaterialAlertDialogBuilder;
import com.google.android.material.switchmaterial.SwitchMaterial;

import java.io.File;
import java.io.FileOutputStream;
import java.io.InputStream;
import java.io.OutputStream;
import java.util.ArrayList;
import java.util.List;
import java.util.Locale;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;

public class MainActivity extends AppCompatActivity implements ConversionService.Listener {
    private static final String PREFS_NAME = "vidstb_settings";
    private static final String PREF_HARDWARE = "hardware_acceleration";

    private final ExecutorService executor = Executors.newSingleThreadExecutor();
    private final List<CheckBox> subtitleBoxes = new ArrayList<>();
    private SharedPreferences preferences;
    private LinearLayout subtitleContainer;
    private TextView videoInfoText;
    private TextView statusText;
    private ProgressBar progressBar;
    private MaterialButton selectVideoButton;
    private MaterialButton settingsButton;
    private MaterialButton startButton;
    private MaterialButton cancelButton;
    private MaterialButton openResultButton;
    private File inputFile;
    private MediaProbe.Result probeResult;
    private String outputName = "hard_subtitle.mkv";
    private Uri resultUri;
    private ConversionService conversionService;
    private boolean serviceBound;
    private boolean running;
    private boolean reading;

    private final ActivityResultLauncher<String[]> videoPicker =
            registerForActivityResult(new ActivityResultContracts.OpenDocument(), uri -> {
                if (uri != null) {
                    importVideo(uri);
                }
            });
    private final ActivityResultLauncher<String[]> permissionRequest =
            registerForActivityResult(new ActivityResultContracts.RequestMultiplePermissions(), result -> {
                boolean granted = true;
                for (Boolean value : result.values()) {
                    granted &= Boolean.TRUE.equals(value);
                }
                if (granted) {
                    startConversion();
                } else {
                    Toast.makeText(this, "需要通知和存储权限才能继续", Toast.LENGTH_LONG).show();
                }
            });

    private final ServiceConnection serviceConnection = new ServiceConnection() {
        @Override
        public void onServiceConnected(ComponentName name, IBinder service) {
            ConversionService.LocalBinder binder = (ConversionService.LocalBinder) service;
            conversionService = binder.getService();
            conversionService.addListener(MainActivity.this);
            serviceBound = true;
            running = conversionService.isRunning();
            resultUri = conversionService.getResultUri();
            if (running) {
                statusText.setText(conversionService.getStatus());
                progressBar.setVisibility(View.VISIBLE);
                progressBar.setProgress(conversionService.getProgress(), true);
            }
            updateControls();
        }

        @Override
        public void onServiceDisconnected(ComponentName name) {
            if (conversionService != null) {
                conversionService.removeListener(MainActivity.this);
            }
            conversionService = null;
            serviceBound = false;
        }
    };

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        setContentView(R.layout.activity_main);
        preferences = getSharedPreferences(PREFS_NAME, MODE_PRIVATE);
        subtitleContainer = findViewById(R.id.subtitleContainer);
        videoInfoText = findViewById(R.id.videoInfoText);
        statusText = findViewById(R.id.statusText);
        progressBar = findViewById(R.id.progressBar);
        selectVideoButton = findViewById(R.id.selectVideoButton);
        settingsButton = findViewById(R.id.settingsButton);
        startButton = findViewById(R.id.startButton);
        cancelButton = findViewById(R.id.cancelButton);
        openResultButton = findViewById(R.id.openResultButton);

        selectVideoButton.setOnClickListener(v -> videoPicker.launch(new String[]{"video/*", "*/*"}));
        settingsButton.setOnClickListener(v -> showSettings());
        startButton.setOnClickListener(v -> startConversion());
        cancelButton.setOnClickListener(v -> {
            if (conversionService != null) {
                conversionService.cancelConversion();
            }
        });
        openResultButton.setOnClickListener(v -> openResult());
    }

    @Override
    protected void onStart() {
        super.onStart();
        bindService(new Intent(this, ConversionService.class), serviceConnection, Context.BIND_AUTO_CREATE);
    }

    @Override
    protected void onStop() {
        if (serviceBound) {
            conversionService.removeListener(this);
            unbindService(serviceConnection);
            serviceBound = false;
            conversionService = null;
        }
        super.onStop();
    }

    @Override
    protected void onDestroy() {
        executor.shutdownNow();
        super.onDestroy();
    }

    private void showSettings() {
        View view = getLayoutInflater().inflate(R.layout.dialog_settings, null);
        SwitchMaterial hardwareSwitch = view.findViewById(R.id.hardwareSwitch);
        hardwareSwitch.setChecked(preferences.getBoolean(PREF_HARDWARE, true));
        hardwareSwitch.setOnCheckedChangeListener((button, checked) ->
                preferences.edit().putBoolean(PREF_HARDWARE, checked).apply());
        new MaterialAlertDialogBuilder(this)
                .setTitle(R.string.settings)
                .setView(view)
                .setPositiveButton(R.string.close, null)
                .show();
    }

    private void importVideo(Uri uri) {
        if (running || reading) {
            return;
        }
        reading = true;
        resultUri = null;
        probeResult = null;
        inputFile = null;
        subtitleContainer.removeAllViews();
        subtitleBoxes.clear();
        outputName = outputNameFor(queryDisplayName(uri));
        videoInfoText.setText("正在复制并读取视频信息…");
        statusText.setText(R.string.processing);
        updateControls();
        executor.execute(() -> {
            try {
                File root = getExternalFilesDir(null);
                if (root == null) {
                    root = getFilesDir();
                }
                File workDir = new File(root, "jobs/" + System.currentTimeMillis());
                if (!workDir.mkdirs() && !workDir.isDirectory()) {
                    throw new IllegalStateException("无法创建临时目录");
                }
                File copied = new File(workDir, "input.mkv");
                copyUri(uri, copied);
                MediaProbe.Result result = MediaProbe.probe(copied);
                runOnUiThread(() -> {
                    inputFile = copied;
                    probeResult = result;
                    reading = false;
                    showProbeResult(result);
                    updateControls();
                });
            } catch (Exception error) {
                runOnUiThread(() -> {
                    reading = false;
                    videoInfoText.setText(R.string.no_video);
                    statusText.setText("读取失败：" + safeMessage(error));
                    updateControls();
                });
            }
        });
    }

    private void showProbeResult(MediaProbe.Result result) {
        VideoMetadata metadata = result.getMetadata();
        videoInfoText.setText(String.format(Locale.getDefault(), "%s\n%d × %d · %.1f 分钟",
                outputName, metadata.getWidth(), metadata.getHeight(), metadata.getDurationUs() / 60_000_000D));
        subtitleContainer.removeAllViews();
        subtitleBoxes.clear();
        if (result.getSubtitles().isEmpty()) {
            TextView empty = new TextView(this);
            empty.setText("没有检测到内嵌字幕轨");
            subtitleContainer.addView(empty);
            statusText.setText("没有可生成的字幕");
            return;
        }
        boolean hasDefault = false;
        for (SubtitleTrack track : result.getSubtitles()) {
            if (track.isSupported() && track.isDefaultTrack()) {
                hasDefault = true;
                break;
            }
        }
        boolean firstSupported = true;
        for (SubtitleTrack track : result.getSubtitles()) {
            CheckBox box = new CheckBox(this);
            box.setText(track.getDisplayName());
            box.setTag(track);
            box.setEnabled(track.isSupported());
            boolean selected = track.isSupported() && (hasDefault ? track.isDefaultTrack() : firstSupported);
            box.setChecked(selected);
            if (selected) {
                firstSupported = false;
            }
            box.setOnCheckedChangeListener((button, checked) -> updateControls());
            subtitleBoxes.add(box);
            subtitleContainer.addView(box);
        }
        statusText.setText(R.string.waiting);
    }

    private void startConversion() {
        if (running || reading || inputFile == null || probeResult == null) {
            return;
        }
        List<Integer> selected = selectedSubtitleIndexes();
        if (selected.isEmpty()) {
            Toast.makeText(this, "请至少选择一条字幕", Toast.LENGTH_SHORT).show();
            return;
        }
        List<String> permissions = missingPermissions();
        if (!permissions.isEmpty()) {
            permissionRequest.launch(permissions.toArray(new String[0]));
            return;
        }
        VideoMetadata metadata = probeResult.getMetadata();
        int[] indexes = new int[selected.size()];
        for (int i = 0; i < selected.size(); i++) {
            indexes[i] = selected.get(i);
        }
        Intent intent = new Intent(this, ConversionService.class)
                .putExtra(ConversionService.EXTRA_INPUT, inputFile.getAbsolutePath())
                .putExtra(ConversionService.EXTRA_OUTPUT_NAME, outputName)
                .putExtra(ConversionService.EXTRA_SUBTITLES, indexes)
                .putExtra(ConversionService.EXTRA_HARDWARE,
                        preferences.getBoolean(PREF_HARDWARE, true))
                .putExtra(ConversionService.EXTRA_WIDTH, metadata.getWidth())
                .putExtra(ConversionService.EXTRA_HEIGHT, metadata.getHeight())
                .putExtra(ConversionService.EXTRA_BIT_RATE, metadata.getBitRate())
                .putExtra(ConversionService.EXTRA_DURATION_US, metadata.getDurationUs());
        running = true;
        resultUri = null;
        progressBar.setProgress(0, true);
        progressBar.setVisibility(View.VISIBLE);
        statusText.setText("正在准备转码…");
        updateControls();
        ContextCompat.startForegroundService(this, intent);
    }

    private List<String> missingPermissions() {
        List<String> permissions = new ArrayList<>();
        if (Build.VERSION.SDK_INT >= 33
                && ContextCompat.checkSelfPermission(this, Manifest.permission.POST_NOTIFICATIONS)
                != PackageManager.PERMISSION_GRANTED) {
            permissions.add(Manifest.permission.POST_NOTIFICATIONS);
        }
        if (Build.VERSION.SDK_INT <= 28
                && ContextCompat.checkSelfPermission(this, Manifest.permission.WRITE_EXTERNAL_STORAGE)
                != PackageManager.PERMISSION_GRANTED) {
            permissions.add(Manifest.permission.WRITE_EXTERNAL_STORAGE);
        }
        return permissions;
    }

    private List<Integer> selectedSubtitleIndexes() {
        List<Integer> selected = new ArrayList<>();
        for (CheckBox box : subtitleBoxes) {
            if (box.isChecked() && box.isEnabled()) {
                selected.add(((SubtitleTrack) box.getTag()).getSubtitleIndex());
            }
        }
        return selected;
    }

    private void updateControls() {
        startButton.setEnabled(!running && !reading && probeResult != null && !selectedSubtitleIndexes().isEmpty());
        cancelButton.setEnabled(running);
        selectVideoButton.setEnabled(!running && !reading);
        settingsButton.setEnabled(!running && !reading);
        openResultButton.setEnabled(resultUri != null && !running);
        for (CheckBox box : subtitleBoxes) {
            SubtitleTrack track = (SubtitleTrack) box.getTag();
            box.setEnabled(!running && track.isSupported());
        }
    }

    private void openResult() {
        if (resultUri == null) {
            return;
        }
        Intent intent = new Intent(Intent.ACTION_VIEW)
                .setDataAndType(resultUri, "video/x-matroska")
                .addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION);
        try {
            startActivity(Intent.createChooser(intent, "打开视频"));
        } catch (RuntimeException error) {
            Toast.makeText(this, "没有找到可播放此视频的应用", Toast.LENGTH_LONG).show();
        }
    }

    private void copyUri(Uri uri, File destination) throws Exception {
        try (InputStream input = getContentResolver().openInputStream(uri);
             OutputStream output = new FileOutputStream(destination)) {
            if (input == null) {
                throw new IllegalArgumentException("无法打开所选视频");
            }
            byte[] buffer = new byte[1024 * 1024];
            int count;
            while ((count = input.read(buffer)) != -1) {
                output.write(buffer, 0, count);
            }
        }
    }

    private String queryDisplayName(Uri uri) {
        try (Cursor cursor = getContentResolver().query(uri,
                new String[]{OpenableColumns.DISPLAY_NAME}, null, null, null)) {
            if (cursor != null && cursor.moveToFirst()) {
                return cursor.getString(0);
            }
        } catch (RuntimeException ignored) {
        }
        return "video.mkv";
    }

    private static String outputNameFor(String displayName) {
        String base = displayName == null ? "video" : displayName.replaceAll("[\\\\/:*?\"<>|]", "_");
        if (base.toLowerCase(Locale.ROOT).endsWith(".mkv")) {
            base = base.substring(0, base.length() - 4);
        }
        return base + "_硬字幕.mkv";
    }

    private static String safeMessage(Exception error) {
        return error.getMessage() == null ? error.getClass().getSimpleName() : error.getMessage();
    }

    @Override
    public void onConversionProgress(int percent, long timeMs) {
        runOnUiThread(() -> {
            progressBar.setVisibility(View.VISIBLE);
            progressBar.setProgress(percent, true);
        });
    }

    @Override
    public void onConversionState(String state) {
        runOnUiThread(() -> statusText.setText(state));
    }

    @Override
    public void onConversionFinished(boolean success, String message, Uri output) {
        runOnUiThread(() -> {
            running = false;
            resultUri = output;
            statusText.setText(message);
            progressBar.setVisibility(success ? View.VISIBLE : View.GONE);
            if (success) {
                progressBar.setProgress(100, true);
            }
            updateControls();
        });
    }
}
