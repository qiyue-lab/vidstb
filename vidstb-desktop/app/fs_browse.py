"""本机文件系统浏览与视频扫描，供 Web 界面的文件选择器使用。"""

from __future__ import annotations

import os
import string
import subprocess
import sys
import threading
from typing import Optional

IS_WINDOWS = sys.platform.startswith("win")

VIDEO_EXTENSIONS = {
    ".mkv", ".mp4", ".mov", ".m4v", ".avi", ".webm", ".flv", ".ts", ".m2ts",
    ".wmv", ".mpg", ".mpeg", ".vob", ".3gp", ".rmvb", ".av1",
}


def is_video(path: str) -> bool:
    return os.path.splitext(path)[1].lower() in VIDEO_EXTENSIONS


def drives() -> list:
    if IS_WINDOWS:
        return [f"{letter}:\\" for letter in string.ascii_uppercase
                if os.path.exists(f"{letter}:\\")]
    return ["/"]


def list_dir(path: str) -> dict:
    if not path:
        return {"path": "", "parent": "", "entries": [
            {"name": drive, "path": drive, "is_dir": True, "is_video": False,
             "size": 0, "mtime": 0} for drive in drives()]}

    path = os.path.abspath(path)
    if not os.path.isdir(path):
        return {"path": path, "parent": "", "entries": [], "error": "目录不存在"}

    parent = os.path.dirname(path)
    if IS_WINDOWS and parent and not parent.endswith(os.sep):
        parent_with_sep = parent + os.sep
    else:
        parent_with_sep = parent

    entries = []
    try:
        names = sorted(os.listdir(path), key=lambda value: value.lower())
    except OSError as error:
        return {"path": path, "parent": parent_with_sep, "entries": [], "error": str(error)}

    for name in names:
        full = os.path.join(path, name)
        try:
            stat = os.stat(full)
        except OSError:
            continue
        is_dir = os.path.isdir(full)
        entries.append({
            "name": name,
            "path": full,
            "is_dir": is_dir,
            "is_video": (not is_dir) and is_video(full),
            "size": 0 if is_dir else stat.st_size,
            "mtime": int(stat.st_mtime),
        })
    entries.sort(key=lambda item: (not item["is_dir"], item["name"].lower()))
    return {"path": path, "parent": parent_with_sep, "entries": entries}


def scan_dir(path: str, recursive: bool = False, limit: int = 500) -> list:
    path = os.path.abspath(path)
    if not os.path.isdir(path):
        return []
    found = []
    if recursive:
        for root, dirs, files in os.walk(path):
            dirs.sort()
            for name in sorted(files):
                if is_video(name):
                    found.append(os.path.join(root, name))
                    if len(found) >= limit:
                        return found
    else:
        try:
            names = sorted(os.listdir(path), key=lambda value: value.lower())
        except OSError:
            return []
        for name in names:
            full = os.path.join(path, name)
            if os.path.isfile(full) and is_video(full):
                found.append(full)
                if len(found) >= limit:
                    break
    return found


def quick_links() -> list:
    home = os.path.expanduser("~")
    links = [{"name": "用户目录", "path": home}]
    desktop = os.path.join(home, "Desktop")
    if os.path.isdir(desktop):
        links.append({"name": "桌面", "path": desktop})
    downloads = os.path.join(home, "Downloads")
    if os.path.isdir(downloads):
        links.append({"name": "下载", "path": downloads})
    movies = os.path.join(home, "Videos")
    if os.path.isdir(movies):
        links.append({"name": "视频", "path": movies})
    for drive in drives():
        links.append({"name": drive, "path": drive})
    return links


PICK_MARK_OK = "VIDSTB_OK"
PICK_MARK_CANCEL = "VIDSTB_CANCEL"
PICK_MARK_ERROR = "VIDSTB_ERROR"

_PICK_SCRIPT = r"""
$ErrorActionPreference = 'Stop'
try {
  Add-Type -AssemblyName System.Windows.Forms | Out-Null
  [System.Windows.Forms.Application]::EnableVisualStyles()
  $d = New-Object System.Windows.Forms.OpenFileDialog
  $d.Multiselect = $true
  $d.Title = '选择视频文件'
  $d.Filter = '视频文件|*.mkv;*.mp4;*.mov;*.m4v;*.avi;*.webm;*.flv;*.ts;*.m2ts;*.wmv;*.mpg;*.mpeg;*.vob;*.3gp;*.rmvb|所有文件|*.*'
  # 无宿主窗口时弹窗常被主窗口挡住甚至不显示，这里用一个透明的置顶窗口做宿主
  $owner = New-Object System.Windows.Forms.Form
  $owner.TopMost = $true
  $owner.ShowInTaskbar = $false
  $owner.StartPosition = 'CenterScreen'
  $owner.Size = New-Object System.Drawing.Size(320, 200)
  $owner.Opacity = 0
  [void]$owner.Show()
  $owner.Activate()
  $res = $d.ShowDialog($owner)
  $owner.Dispose()
  if ($res -eq [System.Windows.Forms.DialogResult]::OK) {
    'VIDSTB_OK'
    $d.FileNames
  } else {
    'VIDSTB_CANCEL'
  }
} catch {
  'VIDSTB_ERROR'
  $_.Exception.Message
}
"""


_PROBE_SCRIPT = r"""
$ErrorActionPreference = 'Stop'
try {
  Add-Type -AssemblyName System.Windows.Forms | Out-Null
  $f = New-Object System.Windows.Forms.Form
  $f.TopMost = $true
  $f.ShowInTaskbar = $false
  $f.StartPosition = 'Manual'
  $f.Location = New-Object System.Drawing.Point(-20000, -20000)
  $f.Size = New-Object System.Drawing.Size(16, 16)
  $f.Opacity = 0.01
  [void]$f.Show()
  $f.Activate()
  Start-Sleep -Milliseconds 500
  $active = [System.Windows.Forms.Form]::ActiveForm
  $ok = ($active -ne $null) -and ($active.Handle -eq $f.Handle)
  $f.Dispose()
  if ($ok) { 'VIDSTB_PROBE_OK' } else { 'VIDSTB_PROBE_NO' }
} catch {
  'VIDSTB_PROBE_NO'
}
"""

_dialog_support: Optional[bool] = None
_dialog_lock = threading.Lock()


def can_show_native_dialog(refresh: bool = False) -> bool:
    """探测本机能否弹出系统窗口。

    服务运行在后台会话、计划任务或沙箱里时窗口弹不出来，
    此时必须回退到内置浏览器，否则点击按钮会长时间无响应。
    结果会被缓存，每次进程只探测一次。
    """
    global _dialog_support
    if not IS_WINDOWS:
        return False
    with _dialog_lock:
        if _dialog_support is not None and not refresh:
            return _dialog_support
        try:
            result = subprocess.run(
                ["powershell", "-NoProfile", "-STA", "-WindowStyle", "Hidden",
                 "-Command", _PROBE_SCRIPT],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=25,
                creationflags=subprocess.CREATE_NO_WINDOW,
            )
            _dialog_support = "VIDSTB_PROBE_OK" in result.stdout.decode("utf-8", "replace")
        except Exception:  # noqa: BLE001
            _dialog_support = False
        return _dialog_support


def pick_files_native(timeout: int = 180) -> dict:
    """调用系统文件选择框。

    返回 ``{"ok": bool, "files": [...], "error": str}``：
    ``ok=True`` 表示弹窗确实弹出来了（files 为空 = 用户主动取消）；
    ``ok=False`` 表示弹不出来，调用方应回退到内置浏览器。
    """
    if not IS_WINDOWS:
        return {"ok": False, "files": [], "error": "当前系统不支持系统文件选择框"}

    try:
        result = subprocess.run(
            ["powershell", "-NoProfile", "-STA", "-WindowStyle", "Hidden",
             "-Command", _PICK_SCRIPT],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=timeout,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
    except subprocess.TimeoutExpired:
        return {"ok": False, "files": [], "error": "系统文件选择框无响应（已超时）"}
    except Exception as error:  # noqa: BLE001
        return {"ok": False, "files": [], "error": str(error)}

    lines = [line.strip() for line in
             result.stdout.decode("utf-8", "replace").splitlines() if line.strip()]
    marker = lines[0] if lines else ""
    if marker == PICK_MARK_OK:
        return {"ok": True, "files": lines[1:], "error": ""}
    if marker == PICK_MARK_CANCEL:
        return {"ok": True, "files": [], "error": ""}
    error = " ".join(lines[1:]).strip()
    if not error:
        error = result.stderr.decode("utf-8", "replace").strip()[:200]
    if not error:
        error = ("系统文件选择框被关闭或无法显示（退出码 %s）" % result.returncode
                 if result.returncode else "系统文件选择框无响应")
    return {"ok": False, "files": [], "error": error}
