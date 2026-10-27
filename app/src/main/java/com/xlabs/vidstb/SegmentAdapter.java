package com.xlabs.vidstb;

import android.graphics.Bitmap;
import android.graphics.Color;
import android.media.MediaMetadataRetriever;
import android.os.Handler;
import android.os.Looper;
import android.view.LayoutInflater;
import android.view.View;
import android.view.ViewGroup;
import android.widget.ImageView;
import android.widget.TextView;

import androidx.recyclerview.widget.RecyclerView;

import java.util.List;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;

public final class SegmentAdapter extends RecyclerView.Adapter<SegmentAdapter.ViewHolder> {
    private final ExecutorService executor = Executors.newSingleThreadExecutor();
    private final Handler handler = new Handler(Looper.getMainLooper());
    private List<VideoSegment> segments;
    private int selectedIndex;
    private final OnSegmentClickListener listener;

    public SegmentAdapter(List<VideoSegment> segments, OnSegmentClickListener listener) {
        this.segments = segments;
        this.listener = listener;
    }

    public void update(List<VideoSegment> values, int selected) {
        segments = values;
        selectedIndex = selected;
        notifyDataSetChanged();
    }

    @Override
    public ViewHolder onCreateViewHolder(ViewGroup parent, int viewType) {
        return new ViewHolder(LayoutInflater.from(parent.getContext())
                .inflate(R.layout.item_segment, parent, false));
    }

    @Override
    public void onBindViewHolder(ViewHolder holder, int position) {
        VideoSegment segment = segments.get(position);
        holder.name.setText((position + 1) + ". " + segment.getDisplayName());
        holder.time.setText(TimeCode.format(segment.getStartUs()) + "\n"
                + TimeCode.format(segment.getEndUs()) + " · "
                + String.format("%.2fs", segment.getDurationUs() / 1_000_000D));
        holder.itemView.setBackgroundColor(position == selectedIndex ? 0x55735FFF : Color.TRANSPARENT);
        holder.image.setImageBitmap(null);
        holder.itemView.setOnClickListener(v -> {
            int index = holder.getBindingAdapterPosition();
            if (index != RecyclerView.NO_POSITION) listener.onSegmentClick(index);
        });
        executor.execute(() -> {
            Bitmap bitmap = null;
            MediaMetadataRetriever retriever = new MediaMetadataRetriever();
            try {
                retriever.setDataSource(segment.getSourcePath());
                bitmap = retriever.getFrameAtTime(segment.getStartUs(), MediaMetadataRetriever.OPTION_CLOSEST_SYNC);
            } catch (Exception ignored) {
            } finally {
                try { retriever.release(); } catch (Exception ignored) { }
            }
            Bitmap result = bitmap;
            handler.post(() -> {
                if (holder.getBindingAdapterPosition() == position) holder.image.setImageBitmap(result);
            });
        });
    }

    @Override
    public int getItemCount() {
        return segments == null ? 0 : segments.size();
    }

    public void close() {
        executor.shutdownNow();
    }

    static final class ViewHolder extends RecyclerView.ViewHolder {
        final ImageView image;
        final TextView name;
        final TextView time;

        ViewHolder(View itemView) {
            super(itemView);
            image = itemView.findViewById(R.id.segmentImage);
            name = itemView.findViewById(R.id.segmentName);
            time = itemView.findViewById(R.id.segmentTime);
        }
    }

    public interface OnSegmentClickListener {
        void onSegmentClick(int index);
    }
}
