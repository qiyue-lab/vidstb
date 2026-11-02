"""下载并配置 ffmpeg（含 libass，用于字幕烧录）。

用法：
    python install_ffmpeg.py                    # 安装到 D:\\tools\\ffmpeg 并写入 PATH
    python install_ffmpeg.py --dest E:\\ffmpeg  # 指定目录
    python install_ffmpeg.py --no-path          # 只下载，不改环境变量
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import urllib.error
import urllib.request
import zipfile

RELEASE_API = "https://api.github.com/repos/GyanD/codexffmpeg/releases/latest"
MIRRORS = ["", "https://gh-proxy.com/", "https://ghproxy.net/"]

IS_WINDOWS = sys.platform.startswith("win")


def latest_asset() -> tuple:
    request = urllib.request.Request(RELEASE_API, headers={"User-Agent": "vidstb-desktop"})
    with urllib.request.urlopen(request, timeout=30) as response:
        data = json.loads(response.read().decode("utf-8"))
    for asset in data.get("assets", []):
        name = asset.get("name", "")
        if name.endswith("full_build.zip"):
            return name, asset["browser_download_url"], data.get("tag_name", "")
    raise RuntimeError("没有找到 full_build.zip 资源（需要包含 libass 的完整版）")


def download(url: str, target: str, mirrors) -> None:
    last_error = None
    for mirror in mirrors:
        full = mirror + url
        print(f"  尝试：{full}")
        try:
            with urllib.request.urlopen(full, timeout=60) as response, open(target, "wb") as handle:
                total = int(response.headers.get("Content-Length") or 0)
                done = 0
                while True:
                    chunk = response.read(1024 * 256)
                    if not chunk:
                        break
                    handle.write(chunk)
                    done += len(chunk)
                    if total:
                        percent = done * 100 // total
                        print(f"\r  下载 {percent}%  {done // 1048576}MB / {total // 1048576}MB",
                              end="", flush=True)
            print()
            if os.path.getsize(target) > 10 * 1024 * 1024:
                return
        except (urllib.error.URLError, OSError, TimeoutError) as error:
            last_error = error
            print(f"\n  失败：{error}")
    raise RuntimeError(f"下载失败：{last_error}")


def extract(archive: str, dest: str) -> str:
    """解压并把 bin 目录放到 dest/bin，返回 bin 目录路径。"""
    bin_dir = os.path.join(dest, "bin")
    os.makedirs(bin_dir, exist_ok=True)
    with zipfile.ZipFile(archive) as zf:
        for member in zf.namelist():
            base = os.path.basename(member)
            if not base:
                continue
            if not base.lower().endswith((".exe", ".dll")):
                continue
            # 顶层 bin/ 目录下的可执行文件
            if "/bin/" not in member.replace("\\", "/") and "\\bin\\" not in member:
                continue
            target = os.path.join(bin_dir, base)
            with zf.open(member) as source, open(target, "wb") as handle:
                handle.write(source.read())
    return bin_dir


def add_to_path(bin_dir: str) -> bool:
    if not IS_WINDOWS:
        print(f"  请手动把下面这行加入 shell 配置文件：")
        print(f'    export PATH="{bin_dir}:$PATH"')
        return False
    try:
        result = subprocess.run(
            ["powershell", "-NoProfile", "-Command",
             f'[Environment]::GetEnvironmentVariable("Path", "User")'],
            stdout=subprocess.PIPE, timeout=30,
            creationflags=subprocess.CREATE_NO_WINDOW)
        current = result.stdout.decode("utf-8", "replace").strip()
    except Exception:
        current = ""
    if bin_dir.lower() in current.lower():
        print("  PATH 中已存在该目录")
        return True
    new_value = current + (";" if current else "") + bin_dir
    result = subprocess.run(
        ["setx", "Path", new_value], stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=60)
    ok = result.returncode == 0
    print("  已写入用户级 PATH" if ok else f"  写入 PATH 失败：{result.stderr.decode('utf-8','replace').strip()}")
    return ok


def main() -> None:
    parser = argparse.ArgumentParser(description="安装 ffmpeg 完整版")
    parser.add_argument("--dest", default=r"D:\tools\ffmpeg" if IS_WINDOWS else "/opt/ffmpeg")
    parser.add_argument("--no-path", action="store_true", help="不修改环境变量")
    args = parser.parse_args()

    print("查询最新 ffmpeg 版本…")
    name, url, tag = latest_asset()
    print(f"  版本 {tag} · {name}")

    # 下载包约 150MB，放在安装目标旁边，不要占用系统盘
    download_dir = os.path.join(os.path.dirname(os.path.abspath(args.dest)), "_download")
    os.makedirs(download_dir, exist_ok=True)
    temp = os.path.join(download_dir, name)
    print("下载中…")
    download(url, temp, MIRRORS)

    print(f"解压到 {args.dest} …")
    bin_dir = extract(temp, args.dest)
    try:
        os.remove(temp)
    except OSError:
        pass

    exe = os.path.join(bin_dir, "ffmpeg.exe" if IS_WINDOWS else "ffmpeg")
    if not os.path.isfile(exe):
        raise SystemExit(f"解压后没有找到 {exe}")
    print(f"  ffmpeg: {exe}")

    if not args.no_path:
        add_to_path(bin_dir)

    print("\n完成。启动程序：python server.py")
    if IS_WINDOWS and not args.no_path:
        print("注意：新开的命令行窗口才会生效 PATH，当前窗口请重启。")


if __name__ == "__main__":
    main()
