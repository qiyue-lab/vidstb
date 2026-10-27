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
import java.io.FileOutputStream;
import java.io.InputStream;
import java.io.OutputStream;
import java.nio.charset.StandardCharsets;
import java.text.SimpleDateFormat;
import java.util.Date;
import java.util.List;
import java.util.Locale;
import java.util.concurrent.CopyOnWriteArraySet;

public class VideoEditService extends Service {
    public static final String EXTRA_PROJECT = "edit_project";
    public static final String EXTRA_HARDWARE = "hardware";
    private static final String ACTION_CANCEL = "com.xlabs.vidstb.action.CANCEL_EDIT";
    private static final String CHANNEL_ID = "video_edit";
    private static final int NOTIFICATION_ID = 1002;

    private final IBinder binder = new LocalBinder();
    private final CopyOnWriteArraySet<Listener> listeners = new CopyOnWriteArraySet<>();
    private volatile boolean running;
    private volatile boolean cancelled;
    private volatile FFmpegSession session;
    private volatile String status = "等待编辑任务";
    private volatile int progress;
    private volatile Uri resultUri;
    private EditProject project;
    private File outputFile;
    private File scriptFile;
    private String outputName;
    private long lastNotificationTime;

    @Override
    public IBinder onBind(Intent intent) {
        return binder;
    }

    @SuppressWarnings("deprecation")
    @Override
    public int onStartCommand(Intent intent, int flags, int startId) {
        if (intent != null && ACTION_CANCEL.equals(intent.getAction())) {
            cancelConversion();
            return START_NOT_STICKY;
        }
        if (running || intent == null) return START_NOT_STICKY;
        if (Build.VERSION.SDK_INT >= 33) {
            project = intent.getSerializableExtra(EXTRA_PROJECT, EditProject.class);
        } else {
            project = (EditProject) intent.getSerializableExtra(EXTRA_PROJECT);
        }
        if (project == null) {
            finish(false, "编辑项目为空", null);
            return START_NOT_STICKY;
        }
        try {
            project.validate();
        } catch (Exception error) {
            finish(false, error.getMessage(), null);
            return START_NOT_STICKY;
        }
        running = true;
        cancelled = false;
        resultUri = null;
        progress = 0;
        File workDirectory = new File(project.getSegments().get(0).getSourcePath()).getParentFile();
        outputFile = new File(workDirectory, "edited.mp4");
        scriptFile = new File(workDirectory, "filter_script.txt");
        outputName = "Vidstb_编辑_" + new SimpleDateFormat("yyyyMMdd_HHmmss", Locale.getDefault())
                .format(new Date()) + ".mp4";
        startForegroundWithNotification();
        long inputBytes = 0L;
        for (VideoSegment segment : project.getSegments()) inputBytes += new File(segment.getSourcePath()).length();
        if (workDirectory.getUsableSpace() < inputBytes + 512L * 1024L * 1024L) {
            finish(false, "可用空间不足，请预留更多存储空间", null);
            return START_NOT_STICKY;
        }
        execute(intent.getBooleanExtra(EXTRA_HARDWARE, true));
        return START_NOT_STICKY;
    }

    private void execute(boolean hardware) {
        notifyState(hardware ? "正在使用硬件加速导出编辑视频…" : "正在使用软件高质量模式导出编辑视频…");
        EditCommandBuilder.BuildResult build = EditCommandBuilder.build(project,
                scriptFile.getAbsolutePath(), outputFile.getAbsolutePath(), hardware);
        try (FileOutputStream output = new FileOutputStream(scriptFile, false)) {
            output.write(build.getFilterScript().getBytes(StandardCharsets.UTF_8));
        } catch (Exception error) {
            finish(false, "无法写入编辑滤镜：" + error.getMessage(), null);
            return;
        }
        session = FFmpegKit.executeAsync(toCommandLine(build.getArguments()), completed -> {
            if (cancelled) {
                finish(false, "编辑导出已取消", null);
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
                finish(false, "导出失败：" + lastLines(completed.getAllLogsAsString()), null);
            }
        }, log -> {
        }, this::updateProgress);
    }

    private void updateProgress(Statistics statistics) {
        if (!running || project.getOutputDurationUs() <= 0L) return;
        long timeMs = (long) Math.max(0D, statistics.getTime());
        int value = (int) Math.min(99L, timeMs * 1000L * 100L / project.getOutputDurationUs());
        progress = value;
        for (Listener listener : listeners) listener.onConversionProgress(value, timeMs);
        long now = System.currentTimeMillis();
        if (now - lastNotificationTime >= 1000L) {
            lastNotificationTime = now;
            updateNotification(status, value, false);
        }
    }

    private void publishResult() {
        notifyState("导出完成，正在保存视频…");
        Uri uri = null;
        try {
            ContentValues values = new ContentValues();
            values.put(MediaStore.Video.Media.DISPLAY_NAME, outputName);
            values.put(MediaStore.Video.Media.MIME_TYPE, "video/mp4");
            if (Build.VERSION.SDK_INT >= 29) {
                values.put(MediaStore.Video.Media.RELATIVE_PATH, Environment.DIRECTORY_MOVIES + "/Vidstb");
                values.put(MediaStore.Video.Media.IS_PENDING, 1);
            } else {
                File directory = new File(Environment.getExternalStoragePublicDirectory(
                        Environment.DIRECTORY_MOVIES), "Vidstb");
                if (!directory.mkdirs() && !directory.isDirectory()) throw new IllegalStateException("无法创建输出目录");
                values.put(MediaStore.Video.Media.DATA, new File(directory, outputName).getAbsolutePath());
            }
            uri = getContentResolver().insert(MediaStore.Video.Media.EXTERNAL_CONTENT_URI, values);
            if (uri == null) throw new IllegalStateException("无法创建媒体文件");
            try (InputStream input = new FileInputStream(outputFile);
                 OutputStream output = getContentResolver().openOutputStream(uri, "w")) {
                if (output == null) throw new IllegalStateException("无法写入输出视频");
                byte[] buffer = new byte[1024 * 1024];
                int count;
                while ((count = input.read(buffer)) != -1) output.write(buffer, 0, count);
            }
            if (Build.VERSION.SDK_INT >= 29) {
                ContentValues ready = new ContentValues();
                ready.put(MediaStore.Video.Media.IS_PENDING, 0);
                getContentResolver().update(uri, ready, null, null);
            }
            outputFile.delete();
            finish(true, "已保存到 Movies/Vidstb：" + outputName, uri);
        } catch (Exception error) {
            if (uri != null) getContentResolver().delete(uri, null, null);
            finish(false, "保存失败：" + (error.getMessage() == null
                    ? error.getClass().getSimpleName() : error.getMessage()), null);
        }
    }

    public void cancelConversion() {
        if (!running) return;
        cancelled = true;
        notifyState("正在取消编辑导出…");
        FFmpegSession current = session;
        if (current != null) FFmpegKit.cancel(current.getSessionId());
        else finish(false, "编辑导出已取消", null);
    }

    private void finish(boolean success, String message, Uri output) {
        running = false;
        progress = success ? 100 : progress;
        resultUri = output;
        status = message;
        updateNotification(message, progress, true);
        for (Listener listener : listeners) listener.onConversionFinished(success, message, output);
        stopForeground(false);
        stopSelf();
    }

    private void notifyState(String state) {
        status = state;
        for (Listener listener : listeners) listener.onConversionState(state);
        updateNotification(state, progress, false);
    }

    private void startForegroundWithNotification() {
        createNotificationChannel();
        Notification notification = buildNotification(status, 0, false);
        if (Build.VERSION.SDK_INT >= 29) startForeground(NOTIFICATION_ID, notification,
                ServiceInfo.FOREGROUND_SERVICE_TYPE_DATA_SYNC);
        else startForeground(NOTIFICATION_ID, notification);
    }

    private void updateNotification(String text, int value, boolean finished) {
        NotificationManager manager = getSystemService(NotificationManager.class);
        if (manager != null) manager.notify(NOTIFICATION_ID, buildNotification(text, value, finished));
    }

    private Notification buildNotification(String text, int value, boolean finished) {
        PendingIntent open = PendingIntent.getActivity(this, 0,
                new Intent(this, EditorActivity.class),
                PendingIntent.FLAG_UPDATE_CURRENT | PendingIntent.FLAG_IMMUTABLE);
        NotificationCompat.Builder builder = new NotificationCompat.Builder(this, CHANNEL_ID)
                .setSmallIcon(R.mipmap.ic_launcher)
                .setContentTitle(finished ? "编辑任务状态" : "正在导出编辑视频")
                .setContentText(text)
                .setContentIntent(open)
                .setOnlyAlertOnce(true)
                .setOngoing(!finished)
                .setAutoCancel(finished)
                .setProgress(100, value, !finished && value == 0);
        if (!finished) {
            PendingIntent cancel = PendingIntent.getService(this, 2,
                    new Intent(this, VideoEditService.class).setAction(ACTION_CANCEL),
                    PendingIntent.FLAG_UPDATE_CURRENT | PendingIntent.FLAG_IMMUTABLE);
            builder.addAction(0, "取消", cancel);
        }
        return builder.build();
    }

    private void createNotificationChannel() {
        if (Build.VERSION.SDK_INT >= 26) {
            NotificationChannel channel = new NotificationChannel(CHANNEL_ID, "视频编辑导出",
                    NotificationManager.IMPORTANCE_LOW);
            NotificationManager manager = getSystemService(NotificationManager.class);
            if (manager != null) manager.createNotificationChannel(channel);
        }
    }

    private static String toCommandLine(List<String> arguments) {
        StringBuilder command = new StringBuilder();
        for (String argument : arguments) {
            if (command.length() > 0) command.append(' ');
            command.append('\'').append(argument.replace("'", "'\\''")).append('\'');
        }
        return command.toString();
    }

    private static String lastLines(String logs) {
        if (logs == null || logs.trim().isEmpty()) return "未知 FFmpeg 错误";
        String trimmed = logs.trim();
        return trimmed.length() <= 1200 ? trimmed : trimmed.substring(trimmed.length() - 1200);
    }

    public boolean isRunning() { return running; }
    public String getStatus() { return status; }
    public int getProgress() { return progress; }
    public Uri getResultUri() { return resultUri; }
    public void addListener(Listener listener) { listeners.add(listener); }
    public void removeListener(Listener listener) { listeners.remove(listener); }

    public final class LocalBinder extends Binder {
        public VideoEditService getService() { return VideoEditService.this; }
    }

    public interface Listener {
        void onConversionProgress(int percent, long timeMs);
        void onConversionState(String state);
        void onConversionFinished(boolean success, String message, Uri output);
    }
}
