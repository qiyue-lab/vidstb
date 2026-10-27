package com.xlabs.vidstb;

import android.content.Context;
import android.graphics.Bitmap;
import android.graphics.Canvas;
import android.graphics.Color;
import android.graphics.Paint;
import android.graphics.RectF;
import android.media.MediaMetadataRetriever;
import android.os.Handler;
import android.os.Looper;
import android.util.AttributeSet;
import android.view.MotionEvent;
import android.view.ScaleGestureDetector;
import android.view.View;

import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;

public final class FrameStripView extends View {
    private static final int NONE = 0;
    private static final int SEEK = 1;
    private static final int START = 2;
    private static final int END = 3;

    private final Paint background = new Paint();
    private final Paint shade = new Paint();
    private final Paint accent = new Paint();
    private final Paint playhead = new Paint();
    private final Paint text = new Paint();
    private final Handler handler = new Handler(Looper.getMainLooper());
    private final ExecutorService executor = Executors.newSingleThreadExecutor();
    private final ScaleGestureDetector scaleDetector;
    private final RectF destination = new RectF();
    private Bitmap[] thumbnails = new Bitmap[0];
    private String sourcePath = "";
    private long videoDurationUs;
    private long startUs;
    private long endUs;
    private long positionUs;
    private long windowStartUs;
    private long windowDurationUs = 1L;
    private int generation;
    private int dragMode = NONE;
    private boolean pinching;
    private boolean closed;
    private OnInteractionListener listener;

    public FrameStripView(Context context, AttributeSet attrs) {
        super(context, attrs);
        background.setColor(0xFF17171F);
        shade.setColor(0x99000000);
        accent.setColor(0xFF818CF8);
        accent.setStyle(Paint.Style.STROKE);
        accent.setStrokeWidth(4F);
        accent.setAntiAlias(true);
        playhead.setColor(Color.WHITE);
        playhead.setStrokeWidth(4F);
        playhead.setAntiAlias(true);
        text.setColor(Color.WHITE);
        text.setTextSize(26F);
        text.setAntiAlias(true);
        scaleDetector = new ScaleGestureDetector(context, new ScaleListener());
    }

    public void setVideo(String path, long durationUs, long start, long end, long position) {
        if (closed) return;
        generation++;
        recycleThumbnails();
        sourcePath = path;
        videoDurationUs = Math.max(1L, durationUs);
        startUs = start;
        endUs = end;
        positionUs = position;
        windowStartUs = 0L;
        windowDurationUs = videoDurationUs;
        setEnabled(true);
        requestThumbnails();
        invalidate();
    }

    public void clear() {
        generation++;
        sourcePath = "";
        videoDurationUs = 0L;
        recycleThumbnails();
        setEnabled(false);
        invalidate();
    }

    public void setPosition(long position, boolean follow) {
        positionUs = TimelineMath.clamp(position, 0L, videoDurationUs);
        if (follow && videoDurationUs > 0L) {
            if (positionUs < windowStartUs || positionUs > windowStartUs + windowDurationUs) {
                centerOn(positionUs);
            }
        }
        invalidate();
    }

    public void setRange(long start, long end) {
        startUs = start;
        endUs = end;
        invalidate();
    }

    public void setOnInteractionListener(OnInteractionListener value) {
        listener = value;
    }

    private void centerOn(long time) {
        long[] window = TimelineMath.clampWindow(time - windowDurationUs / 2L,
                windowDurationUs, videoDurationUs);
        windowStartUs = window[0];
        windowDurationUs = window[1];
        requestThumbnails();
    }

    private void requestThumbnails() {
        if (closed || getWidth() <= 0 || getHeight() <= 0 || sourcePath.isEmpty()) return;
        final int currentGeneration = ++generation;
        final int count = Math.max(8, Math.min(24, getWidth() / 90));
        final long sampleWindowStart = windowStartUs;
        final long sampleWindowDuration = windowDurationUs;
        final String samplePath = sourcePath;
        final int targetWidth = Math.max(48, getWidth() / count);
        final int targetHeight = Math.max(48, getHeight());
        recycleThumbnails();
        thumbnails = new Bitmap[count];
        executor.execute(() -> {
            MediaMetadataRetriever retriever = new MediaMetadataRetriever();
            try {
                retriever.setDataSource(samplePath);
                for (int i = 0; i < count; i++) {
                    if (currentGeneration != generation) return;
                    long time = sampleWindowStart + i * sampleWindowDuration / Math.max(1, count - 1);
                    Bitmap frame = retriever.getFrameAtTime(time, MediaMetadataRetriever.OPTION_CLOSEST_SYNC);
                    if (frame == null) continue;
                    Bitmap scaled = Bitmap.createScaledBitmap(frame, targetWidth, targetHeight, true);
                    if (scaled != frame) frame.recycle();
                    final int index = i;
                    handler.post(() -> {
                        if (currentGeneration == generation && index < thumbnails.length) {
                            thumbnails[index] = scaled;
                            invalidate();
                        } else {
                            scaled.recycle();
                        }
                    });
                }
            } catch (Exception ignored) {
            } finally {
                try { retriever.release(); } catch (Exception ignored) { }
            }
        });
    }

    @Override
    protected void onSizeChanged(int w, int h, int oldw, int oldh) {
        super.onSizeChanged(w, h, oldw, oldh);
        if (w != oldw || h != oldh) requestThumbnails();
    }

    @Override
    protected void onDraw(Canvas canvas) {
        super.onDraw(canvas);
        canvas.drawRect(0, 0, getWidth(), getHeight(), background);
        if (videoDurationUs <= 0L) {
            canvas.drawText("添加视频后，拖动这里定位画面", 20F, getHeight() / 2F, text);
            return;
        }
        float cellWidth = getWidth() / (float) Math.max(1, thumbnails.length);
        for (int i = 0; i < thumbnails.length; i++) {
            Bitmap bitmap = thumbnails[i];
            if (bitmap != null && !bitmap.isRecycled()) {
                destination.set(i * cellWidth, 0, (i + 1) * cellWidth, getHeight());
                canvas.drawBitmap(bitmap, null, destination, null);
            }
        }
        float startX = xForTime(startUs);
        float endX = xForTime(endUs);
        canvas.drawRect(0, 0, startX, getHeight(), shade);
        canvas.drawRect(endX, 0, getWidth(), getHeight(), shade);
        canvas.drawRect(startX, 0, endX, getHeight(), accent);
        canvas.drawRect(startX - 5F, 0, startX + 5F, getHeight(), playhead);
        canvas.drawRect(endX - 5F, 0, endX + 5F, getHeight(), playhead);
        float positionX = xForTime(positionUs);
        canvas.drawRect(positionX - 2F, 0, positionX + 2F, getHeight(), playhead);
        canvas.drawText(TimeCode.format(windowStartUs), 10F, 30F, text);
        String end = TimeCode.format(windowStartUs + windowDurationUs);
        canvas.drawText(end, Math.max(10F, getWidth() - text.measureText(end) - 10F), 30F, text);
    }

    @Override
    public boolean onTouchEvent(MotionEvent event) {
        if (!isEnabled() || videoDurationUs <= 0L) return false;
        scaleDetector.onTouchEvent(event);
        if (event.getActionMasked() == MotionEvent.ACTION_POINTER_DOWN && event.getPointerCount() > 1) {
            pinching = true;
            dragMode = NONE;
            return true;
        }
        if (pinching) {
            if (event.getActionMasked() == MotionEvent.ACTION_UP
                    || event.getActionMasked() == MotionEvent.ACTION_CANCEL
                    || event.getPointerCount() > 2) {
                pinching = false;
                requestThumbnails();
            }
            return true;
        }
        float x = event.getX();
        switch (event.getActionMasked()) {
            case MotionEvent.ACTION_DOWN:
                float threshold = 42F;
                if (Math.abs(x - xForTime(startUs)) <= threshold) dragMode = START;
                else if (Math.abs(x - xForTime(endUs)) <= threshold) dragMode = END;
                else dragMode = SEEK;
                getParent().requestDisallowInterceptTouchEvent(true);
                if (dragMode == SEEK) seekTo(x);
                return true;
            case MotionEvent.ACTION_MOVE:
                if (dragMode == SEEK) seekTo(x);
                if (dragMode == START || dragMode == END) changeBoundary(x, false);
                return true;
            case MotionEvent.ACTION_UP:
            case MotionEvent.ACTION_CANCEL:
                if (dragMode == START || dragMode == END) changeBoundary(x, true);
                dragMode = NONE;
                performClick();
                return true;
            default:
                return true;
        }
    }

    @Override
    public boolean performClick() {
        super.performClick();
        return true;
    }

    private void seekTo(float x) {
        positionUs = TimelineMath.timeAt(x, getWidth(), windowStartUs, windowDurationUs, videoDurationUs);
        if (listener != null) listener.onSeek(positionUs);
        invalidate();
    }

    private void changeBoundary(float x, boolean finished) {
        long time = TimelineMath.timeAt(x, getWidth(), windowStartUs, windowDurationUs, videoDurationUs);
        long minimum = 40_000L;
        if (dragMode == START) startUs = TimelineMath.clamp(time, 0L, endUs - minimum);
        if (dragMode == END) endUs = TimelineMath.clamp(time, startUs + minimum, videoDurationUs);
        if (listener != null) listener.onRangeChanged(startUs, endUs, finished);
        invalidate();
    }

    private float xForTime(long time) {
        return (time - windowStartUs) / (float) windowDurationUs * getWidth();
    }

    private void requestWindowScale(float scaleFactor, float focusX) {
        long oldDuration = windowDurationUs;
        long focusTime = TimelineMath.timeAt(focusX, getWidth(), windowStartUs, oldDuration, videoDurationUs);
        long minimum = Math.min(TimelineMath.MIN_WINDOW_US, videoDurationUs);
        long requested = TimelineMath.clamp((long) (oldDuration / scaleFactor), minimum, videoDurationUs);
        float fraction = getWidth() == 0 ? 0.5F : focusX / getWidth();
        long[] window = TimelineMath.clampWindow((long) (focusTime - fraction * requested),
                requested, videoDurationUs);
        windowStartUs = window[0];
        windowDurationUs = window[1];
        invalidate();
    }

    public void close() {
        closed = true;
        generation++;
        executor.shutdownNow();
        recycleThumbnails();
    }

    private void recycleThumbnails() {
        for (Bitmap bitmap : thumbnails) {
            if (bitmap != null && !bitmap.isRecycled()) bitmap.recycle();
        }
        thumbnails = new Bitmap[0];
    }

    private final class ScaleListener extends ScaleGestureDetector.SimpleOnScaleGestureListener {
        @Override
        public boolean onScale(ScaleGestureDetector detector) {
            requestWindowScale(detector.getScaleFactor(), detector.getFocusX());
            return true;
        }
    }

    public interface OnInteractionListener {
        void onSeek(long positionUs);
        void onRangeChanged(long startUs, long endUs, boolean finished);
    }
}
