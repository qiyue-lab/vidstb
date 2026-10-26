# Vidstb

Vidstb 是一款本地 Android 工具，用于读取 MKV 内嵌字幕，选择一条或多条字幕，并将其烧录为直接显示在画面中的硬字幕。视频处理完全在设备本地完成，不需要上传文件。

## 功能

- 通过系统文件选择器导入 MKV 视频。
- 使用 FFprobe 展示字幕轨的语言、标题、格式、默认及强制标记。
- 支持单选或多选字幕；多字幕从底部向上错位显示，减少遮挡。
- 支持 SRT、ASS、SSA 文本字幕。
- 设置中可开启或关闭硬件加速，选择会持久化保存。
- 硬件模式使用 `h264_mediacodec`，失败时回退软件编码。
- 软件模式使用 `libx264`、`CRF 16` 和 `slow` preset。
- 保持源视频分辨率及帧率，不主动缩放或降帧。
- 保留可用音轨、章节和元数据，音频优先直接复制。
- 配置系统字体及中文回退，支持读取 MKV 字体附件。
- 前台服务执行任务，显示进度通知，支持取消。
- 输出 MKV 到 `Movies/Vidstb`，可直接从应用打开。

## 环境要求

- Android 7.0（API 24）或更高版本。
- 构建使用 Android SDK 36.1、JDK 21 和项目内 Gradle Wrapper。
- FFmpegKit 发布包提供 `arm64-v8a` 和 `x86_64` 原生库。

## 构建

在 Android Studio 中打开项目根目录，或使用命令行：

```bash
# Linux / macOS / Git Bash
./gradlew testDebugUnitTest assembleDebug

# Windows CMD
gradlew.bat testDebugUnitTest assembleDebug
```

Debug APK 输出位置：

```text
app/build/outputs/apk/debug/app-debug.apk
```

连接设备并启用 USB 调试后，可执行：

```bash
adb install -r app/build/outputs/apk/debug/app-debug.apk
```

## 使用

1. 点击“选择 MKV 视频”，从系统文件选择器导入视频。
2. 等待应用读取视频信息和字幕轨。
3. 勾选一条或多条需要烧录的字幕。
4. 点击右上角“设置”，根据需要开启或关闭硬件加速。
5. 点击“生成硬字幕视频”，等待处理完成。
6. 点击“打开结果”，或到 `Movies/Vidstb` 查看输出文件。

转码前应预留足够的可用空间。处理时间、耗电和发热取决于视频时长、分辨率、设备性能及编码模式。

## 画质策略

硬字幕会改变视频像素，因此必须重新编码，无法保证数学意义上的无损。Vidstb 不主动降低分辨率或帧率，并采用以下策略优先保留视觉质量：

- 软件编码：`libx264`、`CRF 16`、`slow`。
- 硬件编码：根据源视频码率和分辨率设置较高目标码率。
- 音频：尽量直接复制，避免额外音频转码。

## 当前限制

- 暂不支持 PGS、VobSub 等图片字幕，也不提供 OCR。
- 多选字幕时会统一采用底部错位布局；复杂 ASS 绝对定位和特效可能需要额外适配。
- HDR、特殊色彩空间及非常规时间轴尚未全面验证。
- 多字幕使用固定间距布局，极端的多行字幕仍可能需要手动调整。
- 长视频转码可能受设备性能、温度和系统后台策略影响。

## 项目结构

```text
app/src/main/java/com/xlabs/vidstb/
├── MainActivity.java             文件导入、字幕多选、设置及状态展示
├── ConversionService.java        后台转码、取消、通知及结果保存
├── MediaProbe.java               FFprobe 视频和字幕轨解析
├── FFmpegCommandBuilder.java     多字幕滤镜及编码参数构造
├── SubtitleFonts.java            fontconfig 和系统字体配置
├── SubtitleTrack.java            字幕轨模型
└── VideoMetadata.java            视频元数据模型
```

## 测试

```bash
./gradlew testDebugUnitTest
```

单元测试覆盖软件/硬件编码参数、单字幕与多字幕滤镜、字幕垂直边距、路径转义及空字幕选择校验。

## 第三方组件与许可

项目使用 FFmpegKit maintained、FFmpeg、libass、x264 和 smart-exception。第三方说明见 `app/src/main/assets/THIRD_PARTY_NOTICES.txt`。

当前依赖的 `ffmpeg-kit-full-gpl` 包含 GPL 组件。分发应用或修改相关依赖前，请确认许可证、源代码提供及其他合规要求。
