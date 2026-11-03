"""配置读写。配置保存在项目根目录的 config.json，便于整个目录拷走即用。"""

from __future__ import annotations

import json
import os
from typing import Any, Dict

from .paths import BASE_DIR

CONFIG_PATH = os.path.join(BASE_DIR, "config.json")

DEFAULT_CONFIG: Dict[str, Any] = {
    # ffmpeg / ffprobe 所在目录，留空表示自动查找（PATH + 常见安装位置）
    "ffmpeg_dir": "",
    # 输出目录，留空表示与源视频同目录
    "output_dir": "",
    # 临时目录，留空表示用项目下的 runtime/temp（不占用系统盘）
    "temp_dir": "",
    # 输出容器：mkv 保留章节/附件，mp4 兼容性最好
    "container": "mkv",
    # 编码模式：auto（优先显卡，失败回退 CPU）/ hardware / software
    "encoder_mode": "auto",
    # 指定显卡编码器：auto / nvenc / qsv / amf
    "hw_encoder": "auto",
    # 软件编码参数（对齐 Android 版：libx264 + CRF 16 + slow）
    "crf": 16,
    "preset": "slow",
    # 显卡编码目标码率相对于源码率的倍率（低于下限值时取下限）
    "hw_bitrate_ratio": 1.5,
    # 同时处理的任务数
    "parallel": 1,
    # 多字幕是否从底部向上错位显示
    "stack_subtitles": True,
    # 是否把 MKV 内嵌字体附件解出来给字幕用
    "use_embedded_fonts": True,
    # 字幕样式覆盖，留空表示沿用字幕自带样式
    "style": {
        "font_name": "",
        "font_size": 0,
        "outline": 0,
        "primary_colour": "",
        "margin_v": 0,
    },
    # ---------- 视频合并（输出固定 4K 60fps） ----------
    # 目标视频码率 Mbps
    "merge_bitrate_mbps": 50,
    # 输出音轨码率 kbps（音轨统一重编码为 AAC 立体声）
    "merge_audio_bitrate_kbps": 256,
    # 输出容器：mp4 / mkv / mov
    "merge_container": "mp4",
    # 画面适配：pad 保持比例加黑边 / crop 裁切铺满 / stretch 拉伸铺满
    "merge_fit": "pad",
    # CPU 编码预设（走显卡编码时不生效）
    "merge_preset": "fast",
    # ---------- 网盘资源 ----------
    # 百度网盘 Cookie（可选，只存在本机 config.json）：
    # 带提取码的分享本来就能展开子目录；没有提取码、或想更稳一些时才需要填
    "baidu_cookie": "",
    # 后台自动检查网盘资源有没有更新（出现新文件时在页面上提示）
    "auto_update_check": True,
    # 自动检查的间隔（小时）
    "update_check_hours": 6,
    # ---------- 手机访问 ----------
    # 是否监听局域网（关掉后只有本机 127.0.0.1 能打开，手机连不上）。
    # 在咖啡厅、公司这种公共网络里建议关掉；改完要重启本工具才生效。
    "lan_access": True,
    # 访问密码（存的是 sha256，不是明文）。留空表示不设密码。
    # 手机走流量进来（Tailscale / 内网穿透）时强烈建议设一个。
    "access_password": "",
    # IP 白名单：只放行这些 IP 或网段（支持 CIDR），留空表示不限制。
    # 例：["100.64.0.0/10", "192.168.71.0/24"] —— 前者是 Tailscale 网段
    "allowed_ips": [],
    # ---------- 云端同步 ----------
    # 云端资源库地址（vidstb-cloud），例如 http://124.220.134.241:8777。
    # 留空表示没配置，「云端同步」弹窗会提示先填地址。
    "cloud_url": "",
    # 云端访问口令（明文存在本机 config.json，和百度 Cookie 一个性质）
    "cloud_password": "",
}


def load_config() -> Dict[str, Any]:
    config = json.loads(json.dumps(DEFAULT_CONFIG))
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as handle:
            stored = json.load(handle)
        if isinstance(stored, dict):
            _merge(config, stored)
    except (OSError, ValueError):
        pass
    return config


def save_config(config: Dict[str, Any]) -> None:
    _merge_defaults(config)
    with open(CONFIG_PATH, "w", encoding="utf-8") as handle:
        json.dump(config, handle, ensure_ascii=False, indent=2)


def _merge(target: Dict[str, Any], source: Dict[str, Any]) -> None:
    for key, value in source.items():
        if isinstance(value, dict) and isinstance(target.get(key), dict):
            _merge(target[key], value)
        else:
            target[key] = value


def _merge_defaults(config: Dict[str, Any]) -> None:
    merged = json.loads(json.dumps(DEFAULT_CONFIG))
    _merge(merged, config)
    config.clear()
    config.update(merged)
