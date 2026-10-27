# Vidstb

Vidstb 是一款本地 Android 视频工具，支持 MKV 多字幕烧录、多段视频剪切、任意规格视频合并、画面与音频编辑。视频处理完全在设备本地完成，不需要上传文件。

## 功能

### 硬字幕

- 通过系统文件选择器导入 MKV 视频。
- 展示字幕轨语言、标题、格式、默认及强制标记，支持 SRT、ASS、SSA。
- 单选或多选字幕；多字幕从底部向上错位显示。
- 保持源视频分辨率及帧率，音频优先直接复制。
- 配置系统字体及中文回退，支持读取 MKV 字体附件。

### 视频编辑

- 一次导入多个视频，同一视频可添加多个独立起止片段。
- 时间线支持复制、删除、左右排序，显示缩略图和片段时长。
- 使用 Media3 预览，支持逐帧定位及当前帧设置剪切点。
- 自由拖动裁剪框，或锁定 16:9、9:16、1:1、4:3 比例。
- 每个片段支持旋转、水平/垂直镜像、独立音量和静音。
- 不同规格的视频统一重编码合并，可添加统一时长的淡入淡出。
- 手动设置输出分辨率和帧率，并提供常用预设。
- 支持整体音量/静音、替换整条音轨、原声与背景音乐混音。
- 编辑结果使用 H.264 + AAC，输出 MP4 到 `Movies/Vidstb`。

### 通用

- 设置中可开启或关闭硬件加速，选择会持久化保存。
- 硬件模式使用 `h264_mediacodec`，失败时回退软件编码。
- 软件模式使用 `libx264`、`CRF 16` 和 `slow` preset。
- 前台服务执行任务，显示进度通知，支持取消。
- 输出文件可直接从应用打开。

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

### 生成硬字幕

1. 点击“选择 MKV 视频”，等待应用读取视频信息和字幕轨。
2. 勾选一条或多条字幕，在“设置”中选择是否开启硬件加速。
3. 点击“生成硬字幕视频”，完成后打开结果或到 `Movies/Vidstb` 查看。

### 剪辑与合并

1. 点击“视频编辑与多段合并”，通过“添加视频”导入一个或多个视频。
2. 在时间线选择片段，使用“同源片段”或“复制”添加片段，按需删除或调整顺序。
3. 输入起止时间，或播放预览并用“当前帧为开始/结束”确定剪切点。
4. 在预览上拖动裁剪框，按需设置比例、旋转、镜像、片段音量或静音。
5. 在输出设置中选择分辨率、帧率和统一淡入淡出时长。
6. 在音频设置中调整整体音量，或选择替换音轨、背景音乐及混音音量。
7. 点击“导出”，完成后打开结果或到 `Movies/Vidstb` 查看。

转码前应预留足够的可用空间。处理时间、耗电和发热取决于视频时长、分辨率、设备性能及编码模式。

## 画质策略

硬字幕和画面编辑都会改变视频像素，因此必须重新编码，无法保证数学意义上的无损。应用采用以下策略优先保留视觉质量：

- 软件编码：`libx264`、`CRF 16`、`slow`。
- 硬件编码：根据源视频码率和输出分辨率设置较高目标码率。
- 硬字幕输出不主动降低源分辨率或帧率，音频尽量直接复制。
- 编辑输出使用手动选择的分辨率和帧率；音频因剪切、转场或混音统一编码为 AAC。

## 当前限制

- 暂不支持 PGS、VobSub 等图片字幕，也不提供 OCR。
- 多选字幕时会统一采用底部错位布局；复杂 ASS 绝对定位和特效可能需要额外适配。
- HDR、特殊色彩空间及非常规时间轴尚未全面验证。
- 多字幕使用固定间距布局，极端的多行字幕仍可能需要手动调整。
- 编辑预览以当前片段为主，最终转场和混音效果以导出结果为准。
- 长视频转码可能受设备性能、温度和系统后台策略影响。

## 项目结构

```text
app/src/main/java/com/xlabs/vidstb/
├── MainActivity.java             硬字幕入口及设置
├── EditorActivity.java           多片段时间线、预览和编辑设置
├── ConversionService.java        硬字幕后台转码
├── VideoEditService.java         编辑视频后台导出
├── MediaProbe.java               FFprobe 视频、音频和字幕轨解析
├── FFmpegCommandBuilder.java     多字幕滤镜及编码参数构造
├── EditCommandBuilder.java       剪切、合并、转场和音频滤镜图
├── EditProject.java              编辑项目及参数校验
├── CropOverlayView.java          可视化裁剪框
├── SubtitleFonts.java            fontconfig 和系统字体配置
└── VideoMetadata.java            视频元数据模型
```

## 测试

```bash
./gradlew testDebugUnitTest
```

单元测试覆盖字幕滤镜、编码参数、时间码、编辑项目校验、多段合并、转场偏移、画面处理、静音补齐及音频替换/混音。

## 第三方组件与许可

项目使用 FFmpegKit maintained、FFmpeg、libass、x264、smart-exception 和 Media3。第三方说明见 `app/src/main/assets/THIRD_PARTY_NOTICES.txt`。

当前依赖的 `ffmpeg-kit-full-gpl` 包含 GPL 组件。分发应用或修改相关依赖前，请确认许可证、源代码提供及其他合规要求。
