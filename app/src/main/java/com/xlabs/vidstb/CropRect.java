package com.xlabs.vidstb;

import java.io.Serializable;

public final class CropRect implements Serializable {
    private final float left;
    private final float top;
    private final float right;
    private final float bottom;

    public CropRect(float left, float top, float right, float bottom) {
        this.left = left;
        this.top = top;
        this.right = right;
        this.bottom = bottom;
    }

    public static CropRect full() {
        return new CropRect(0F, 0F, 1F, 1F);
    }

    public float getLeft() {
        return left;
    }

    public float getTop() {
        return top;
    }

    public float getRight() {
        return right;
    }

    public float getBottom() {
        return bottom;
    }

    public boolean isFull() {
        return left == 0F && top == 0F && right == 1F && bottom == 1F;
    }

    public boolean isValid() {
        return left >= 0F && top >= 0F && right <= 1F && bottom <= 1F
                && right - left >= 0.05F && bottom - top >= 0.05F;
    }
}
