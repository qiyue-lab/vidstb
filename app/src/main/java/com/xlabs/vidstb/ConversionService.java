package com.xlabs.vidstb;

import android.app.Notification;
import android.app.NotificationChannel;
import android.app.NotificationManager;
import android.app.PendingIntent;
import android.app.Service;
import android.content.ContentValues;
import android.content.Intent;
import android.content.pm.ServiceInfo;
import android.net.Uri;
import android.os.Binder;
import android.os.Build;
import android.os.Environment;
import android.os.IBinder;
import android.provider.MediaStore;

import androidx.core.app.NotificationCompat;

import com.arthenica.ffmpegkit.FFmpegKit;
import com.arthenica.ffmpegkit.FFmpegSession;
import com.arthenica.ffmpegkit.ReturnCode;
import com.arthenica.ffmpegkit.Statistics;

import java.io.File;
import java.io.FileInputStream;
import java.io.InputStream;
import java.io.OutputStream;
import java.util.ArrayList;
import java.util.List;
import java.util.concurrent.CopyOnWriteArraySet;

public class ConversionService extends Service {
    public static final String EXTRA_INPUT = "input";
    public static final String EXTRA_OUTPUT_NAME = "output_name";
    public static final String EXTRA_SUBTITLES = "subtitles";
    public static final String EXTRA_HARDWARE = "hardware";
    public static final String EXTRA_WIDTH = "width";
    public static final String EXTRA_HEIGHT = "height";
    public static final String EXTRA_BIT_RATE = "bit_rate";
    public static final String EXTRA_DURATION_US = "duration_us";
    private static final String ACTION_CANCEL = "com.xlabs.vidstb.action.CANCEL";
    private static final String CHANNEL_ID = "conversion";
    private static final int NOTIFICATION_ID = 1001;

    private final IBinder binder = new LocalBinder();
    private final CopyOnWriteArraySet<Listener> listeners = new CopyOnWriteArraySet<>();
    private volatile boolean running;
    private volatile boolean cancelled;
    private volatile FFmpegSession session;
    private volatile String status = "等待任务";
    private volatile int progress;
    private volatile Uri resultUri;
    private File outputFile;
    private File inputFile;
    private String outputName;
    private List<Integer> subtitleIndexes;
    private VideoMetadata metadata;
    private long lastNotificationTime;

    @Override
    public IBinder onBind(Intent intent) {
        return binder;
    }

    @Override
    public int onStartCommand(Intent intent, int flags, int startId) {
        if (intent != null && ACTION_CANCEL.equals(intent.getAction())) {
            cancelConversion();
            return START_NOT_STICKY;
        }
        if (running || intent == null) {
            return START_NOT_STICKY;
        }
        inputFile = new File(intent.getStringExtra(EXTRA_INPUT));
        outputName = intent.getStringExtra(EXTRA_OUTPUT_NAME);
        int[] indexes = intent.getIntArrayExtra(EXTRA_SUBTITLES);
        if (indexes == null || indexes.length == 0) {
            finish(false, "没有可处理的字幕轨", null);
            return START_NOT_STICKY;
        }
        subtitleIndexes = new ArrayList<>();
        for (int index : indexes) {
            subtitleIndexes.add(index);
        }
        metadata = new VideoMetadata(intent.getIntExtra(EXTRA_WIDTH, 0),
                intent.getIntExtra(EXTRA_HEIGHT, 0), intent.getLongExtra(EXTRA_BIT_RATE, 0L),
                intent.getLongExtra(EXTRA_DURATION_US, 0L));
        outputFile = new File(inputFile.getParentFile(), "output.mkv");
        running = true;
        cancelled = false;
        resultUri = null;
        progress = 0;
        startForegroundWithNotification();
        long requiredSpace = inputFile.length() + 512L * 1024L * 1024L;
        if (inputFile.getUsableSpace() < requiredSpace) {
            finish(false, "可用空间不足，请至少预留视频大小加 512 MB", null);
            return START_NOT_STICKY;
        }
        execute(intent.getBooleanExtra(EXTRA_HARDWARE, true));
        return START_NOT_STICKY;
    }

    private void execute(boolean hardware) {
        try {
            SubtitleFonts.configure(this);
        } catch (Exception error) {
            finish(false, "字体初始化失败：" + error.getMessage(), null);
            return;
        }
        notifyState(hardware ? "正在使用硬件加速生成硬字幕…" : "正在使用软件高质量模式生成硬字幕…");
        List<String> arguments = FFmpegCommandBuilder.build(inputFile.getAbsolutePath(),
                outputFile.getAbsolutePath(), subtitleIndexes, hardware, metadata);
        String command = toCommandLine(arguments);
        session = FFmpegKit.executeAsync(command, completed -> {
            if (cancelled) {
                finish(false, "任务已取消", null);
            } else if (ReturnCode.isSuccess(completed.getReturnCode())) {
                publishResult();
            } else if (hardware) {
                if (outputFile.exists() && !outputFile.delete()) {
                    finish(false, "硬件编码失败后无法清理临时文件", null);
                    return;
                }
                notifyState("硬件编码不可用，正在回退软件高质量模式…");
                execute(false);
            } else {
                finish(false, "转码失败：" + lastLines(completed.getAllLogsAsString()), null);
            }
        }, log -> {
        }, statistics -> updateProgress(statistics));
    }

    private void updateProgress(Statistics statistics) {
        if (!running || metadata.getDurationUs() <= 0L) {
            return;
        }
        long timeMs = (long) Math.max(0D, statistics.getTime());
        int value = (int) Math.min(99L, timeMs * 1000L * 100L / metadata.getDurationUs());
        progress = value;
        for (Listener listener : listeners) {
            listener.onConversionProgress(value, timeMs);
        }
        long now = System.currentTimeMillis();
        if (now - lastNotificationTime >= 1000L) {
            lastNotificationTime = now;
            updateNotification(status, value, false);
        }
    }

    private void publishResult() {
        notifyState("转码完成，正在保存视频…");
        Uri uri = null;
        try {
            ContentValues values = new ContentValues();
            values.put(MediaStore.Video.Media.DISPLAY_NAME, outputName);
            values.put(MediaStore.Video.Media.MIME_TYPE, "video/x-matroska");
            if (Build.VERSION.SDK_INT >= 29) {
                values.put(MediaStore.Video.Media.RELATIVE_PATH,
                        Environment.DIRECTORY_MOVIES + "/Vidstb");
                values.put(MediaStore.Video.Media.IS_PENDING, 1);
            } else {
                File directory = new File(Environment.getExternalStoragePublicDirectory(
                        Environment.DIRECTORY_MOVIES), "Vidstb");
                if (!directory.mkdirs() && !directory.isDirectory()) {
                    throw new IllegalStateException("无法创建输出目录");
                }
                values.put(MediaStore.Video.Media.DATA,
                        new File(directory, outputName).getAbsolutePath());
            }
            uri = getContentResolver().insert(MediaStore.Video.Media.EXTERNAL_CONTENT_URI, values);
            if (uri == null) {
                throw new IllegalStateException("无法创建媒体文件");
            }
            try (InputStream input = new FileInputStream(outputFile);
                 OutputStream output = getContentResolver().openOutputStream(uri, "w")) {
                if (output == null) {
                    throw new IllegalStateException("无法写入输出视频");
                }
                byte[] buffer = new byte[1024 * 1024];
                int count;
                while ((count = input.read(buffer)) != -1) {
                    output.write(buffer, 0, count);
                }
            }
            if (Build.VERSION.SDK_INT >= 29) {
                ContentValues ready = new ContentValues();
                ready.put(MediaStore.Video.Media.IS_PENDING, 0);
                getContentResolver().update(uri, ready, null, null);
            }
            resultUri = uri;
            outputFile.delete();
            finish(true, "已保存到 Movies/Vidstb：" + outputName, uri);
        } catch (Exception error) {
            if (uri != null) {
                getContentResolver().delete(uri, null, null);
            }
            finish(false, "保存失败：" + (error.getMessage() == null
                    ? error.getClass().getSimpleName() : error.getMessage()), null);
        }
    }

    public void cancelConversion() {
        if (!running) {
            return;
        }
        cancelled = true;
        notifyState("正在取消任务…");
        FFmpegSession current = session;
        if (current != null) {
            FFmpegKit.cancel(current.getSessionId());
        } else {
            finish(false, "任务已取消", null);
        }
    }

    private void finish(boolean success, String message, Uri output) {
        running = false;
        progress = success ? 100 : progress;
        resultUri = output;
        status = message;
        updateNotification(message, progress, true);
        for (Listener listener : listeners) {
            listener.onConversionFinished(success, message, output);
        }
        stopForeground(false);
        stopSelf();
    }

    private void notifyState(String state) {
        status = state;
        for (Listener listener : listeners) {
            listener.onConversionState(state);
        }
        updateNotification(state, progress, false);
    }

    private void startForegroundWithNotification() {
        createNotificationChannel();
        Notification notification = buildNotification(status, 0, false);
        if (Build.VERSION.SDK_INT >= 29) {
            startForeground(NOTIFICATION_ID, notification, ServiceInfo.FOREGROUND_SERVICE_TYPE_DATA_SYNC);
        } else {
            startForeground(NOTIFICATION_ID, notification);
        }
    }

    private void updateNotification(String text, int value, boolean finished) {
        NotificationManager manager = getSystemService(NotificationManager.class);
        if (manager != null) {
            manager.notify(NOTIFICATION_ID, buildNotification(text, value, finished));
        }
    }

    private Notification buildNotification(String text, int value, boolean finished) {
        Intent openIntent = new Intent(this, MainActivity.class);
        PendingIntent openPendingIntent = PendingIntent.getActivity(this, 0, openIntent,
                PendingIntent.FLAG_UPDATE_CURRENT | PendingIntent.FLAG_IMMUTABLE);
        NotificationCompat.Builder builder = new NotificationCompat.Builder(this, CHANNEL_ID)
                .setSmallIcon(R.mipmap.ic_launcher)
                .setContentTitle(finished ? "硬字幕任务状态" : getString(R.string.notification_title))
                .setContentText(text)
                .setContentIntent(openPendingIntent)
                .setOnlyAlertOnce(true)
                .setOngoing(!finished)
                .setAutoCancel(finished)
                .setProgress(100, value, !finished && value == 0);
        if (!finished) {
            Intent cancelIntent = new Intent(this, ConversionService.class).setAction(ACTION_CANCEL);
            PendingIntent cancelPendingIntent = PendingIntent.getService(this, 1, cancelIntent,
                    PendingIntent.FLAG_UPDATE_CURRENT | PendingIntent.FLAG_IMMUTABLE);
            builder.addAction(0, "取消", cancelPendingIntent);
        }
        return builder.build();
    }

    private void createNotificationChannel() {
        if (Build.VERSION.SDK_INT >= 26) {
            NotificationChannel channel = new NotificationChannel(CHANNEL_ID,
                    getString(R.string.notification_channel), NotificationManager.IMPORTANCE_LOW);
            NotificationManager manager = getSystemService(NotificationManager.class);
            if (manager != null) {
                manager.createNotificationChannel(channel);
            }
        }
    }

    private static String toCommandLine(List<String> arguments) {
        StringBuilder command = new StringBuilder();
        for (String argument : arguments) {
            if (command.length() > 0) {
                command.append(' ');
            }
            command.append('\'').append(argument.replace("'", "'\\''")).append('\'');
        }
        return command.toString();
    }

    private static String lastLines(String logs) {
        if (logs == null || logs.trim().isEmpty()) {
            return "未知 FFmpeg 错误";
        }
        String trimmed = logs.trim();
        return trimmed.length() <= 1200 ? trimmed : trimmed.substring(trimmed.length() - 1200);
    }

    public boolean isRunning() {
        return running;
    }

    public String getStatus() {
        return status;
    }

    public int getProgress() {
        return progress;
    }

    public Uri getResultUri() {
        return resultUri;
    }

    public void addListener(Listener listener) {
        listeners.add(listener);
    }

    public void removeListener(Listener listener) {
        listeners.remove(listener);
    }

    public final class LocalBinder extends Binder {
        public ConversionService getService() {
            return ConversionService.this;
        }
    }

    public interface Listener {
        void onConversionProgress(int percent, long timeMs);

        void onConversionState(String state);

        void onConversionFinished(boolean success, String message, Uri output);
    }
}
