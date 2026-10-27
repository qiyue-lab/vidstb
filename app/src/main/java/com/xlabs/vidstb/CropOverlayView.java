package com.xlabs.vidstb;

import android.content.Context;
import android.graphics.Canvas;
import android.graphics.Color;
import android.graphics.Paint;
import android.graphics.RectF;
import android.util.AttributeSet;
import android.view.MotionEvent;
import android.view.View;

public final class CropOverlayView extends View {
    private final Paint shade = new Paint();
    private final Paint border = new Paint();
    private final RectF crop = new RectF(0F, 0F, 1F, 1F);
    private float aspectRatio;
    private int dragCorner = -2;
    private float lastX;
    private float lastY;
    private OnCropChangeListener listener;

    public CropOverlayView(Context context, AttributeSet attrs) {
        super(context, attrs);
        shade.setColor(0x88000000);
        border.setColor(Color.WHITE);
        border.setStyle(Paint.Style.STROKE);
        border.setStrokeWidth(4F);
        border.setAntiAlias(true);
    }

    public void setCrop(CropRect value) {
        crop.set(value.getLeft(), value.getTop(), value.getRight(), value.getBottom());
        invalidate();
    }

    public void setAspectRatio(float ratio) {
        aspectRatio = ratio;
        if (ratio > 0F) {
            float width = 1F;
            float height = getWidth() / (float) Math.max(1, getHeight()) / ratio;
            if (height > 1F) {
                height = 1F;
                width = ratio * getHeight() / (float) Math.max(1, getWidth());
            }
            float left = (1F - width) / 2F;
            float top = (1F - height) / 2F;
            crop.set(left, top, left + width, top + height);
            notifyChanged();
        }
        invalidate();
    }

    public void setOnCropChangeListener(OnCropChangeListener value) {
        listener = value;
    }

    @Override
    protected void onDraw(Canvas canvas) {
        super.onDraw(canvas);
        RectF area = pixels();
        canvas.drawRect(0, 0, getWidth(), area.top, shade);
        canvas.drawRect(0, area.bottom, getWidth(), getHeight(), shade);
        canvas.drawRect(0, area.top, area.left, area.bottom, shade);
        canvas.drawRect(area.right, area.top, getWidth(), area.bottom, shade);
        canvas.drawRect(area, border);
        float size = 28F;
        canvas.drawLine(area.left, area.top, area.left + size, area.top, border);
        canvas.drawLine(area.left, area.top, area.left, area.top + size, border);
        canvas.drawLine(area.right, area.top, area.right - size, area.top, border);
        canvas.drawLine(area.right, area.top, area.right, area.top + size, border);
        canvas.drawLine(area.left, area.bottom, area.left + size, area.bottom, border);
        canvas.drawLine(area.left, area.bottom, area.left, area.bottom - size, border);
        canvas.drawLine(area.right, area.bottom, area.right - size, area.bottom, border);
        canvas.drawLine(area.right, area.bottom, area.right, area.bottom - size, border);
    }

    @Override
    public boolean onTouchEvent(MotionEvent event) {
        if (getWidth() == 0 || getHeight() == 0) {
            return false;
        }
        float x = event.getX() / getWidth();
        float y = event.getY() / getHeight();
        switch (event.getActionMasked()) {
            case MotionEvent.ACTION_DOWN:
                dragCorner = findCorner(x, y);
                lastX = x;
                lastY = y;
                getParent().requestDisallowInterceptTouchEvent(true);
                return true;
            case MotionEvent.ACTION_MOVE:
                updateCrop(x, y);
                lastX = x;
                lastY = y;
                return true;
            case MotionEvent.ACTION_UP:
            case MotionEvent.ACTION_CANCEL:
                dragCorner = -2;
                notifyChanged();
                return true;
            default:
                return true;
        }
    }

    private int findCorner(float x, float y) {
        float threshold = 48F / Math.min(getWidth(), getHeight());
        if (near(x, crop.left, threshold) && near(y, crop.top, threshold)) return 0;
        if (near(x, crop.right, threshold) && near(y, crop.top, threshold)) return 1;
        if (near(x, crop.left, threshold) && near(y, crop.bottom, threshold)) return 2;
        if (near(x, crop.right, threshold) && near(y, crop.bottom, threshold)) return 3;
        return crop.contains(x, y) ? -1 : -2;
    }

    private static boolean near(float value, float target, float threshold) {
        return Math.abs(value - target) <= threshold;
    }

    private void updateCrop(float x, float y) {
        if (dragCorner == -2) return;
        float dx = x - lastX;
        float dy = y - lastY;
        if (dragCorner == -1) {
            float width = crop.width();
            float height = crop.height();
            dx = Math.max(-crop.left, Math.min(dx, 1F - crop.right));
            dy = Math.max(-crop.top, Math.min(dy, 1F - crop.bottom));
            crop.offset(dx, dy);
            crop.right = crop.left + width;
            crop.bottom = crop.top + height;
        } else {
            if (aspectRatio > 0F) {
                float width = Math.max(0.05F, Math.min(x - crop.left, 1F - crop.left));
                float height = width * getWidth() / (float) getHeight() / aspectRatio;
                if (crop.top + height > 1F) {
                    height = 1F - crop.top;
                    width = height * getHeight() / (float) getWidth() * aspectRatio;
                }
                crop.right = crop.left + width;
                crop.bottom = crop.top + height;
            } else {
                if (dragCorner == 0 || dragCorner == 2) crop.left = clamp(x, 0F, crop.right - 0.05F);
                if (dragCorner == 1 || dragCorner == 3) crop.right = clamp(x, crop.left + 0.05F, 1F);
                if (dragCorner == 0 || dragCorner == 1) crop.top = clamp(y, 0F, crop.bottom - 0.05F);
                if (dragCorner == 2 || dragCorner == 3) crop.bottom = clamp(y, crop.top + 0.05F, 1F);
            }
        }
        invalidate();
    }

    private RectF pixels() {
        return new RectF(crop.left * getWidth(), crop.top * getHeight(),
                crop.right * getWidth(), crop.bottom * getHeight());
    }

    private void notifyChanged() {
        if (listener != null) {
            listener.onCropChanged(new CropRect(crop.left, crop.top, crop.right, crop.bottom));
        }
    }

    private static float clamp(float value, float min, float max) {
        return Math.max(min, Math.min(max, value));
    }

    public interface OnCropChangeListener {
        void onCropChanged(CropRect crop);
    }
}
