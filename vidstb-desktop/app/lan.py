"""局域网访问支持。

手机要连到本机，得知道本机在局域网里的 IPv4 地址。标准库没有直接列出网卡
的跨平台接口，所以这里用两条路互补：

1. ``default_route_ip()`` —— 用 UDP connect 的手法问内核“默认走哪块网卡”，
   拿到的是最可能能被手机访问到的那个地址（不会真的发包）。
2. ``parse_ipconfig()`` —— 解析 ``ipconfig`` 输出，拿到每块网卡的名字和地址，
   好把 VMware / Hyper-V / WSL 这些虚拟网卡的地址剔掉，免得给用户一长串
   根本连不上的地址。

Windows 上 ``ipconfig`` 的输出是 GBK，读不出来就按 utf-8 再试一次，都失败就
忽略解码错误——我们只要里面的 IP，中文网卡名长什么样不影响结果。
"""

from __future__ import annotations

import json
import os
import re
import shutil
import socket
import subprocess

# 网卡名里带这些关键字的一律当成虚拟网卡，不展示给用户
VIRTUAL_KEYWORDS = (
    "vmware", "virtualbox", "hyper-v", "wsl", "tap-windows", "tap-",
    "tunnel", "隧道", "isatap", "teredo", "bluetooth", "virtual",
    "loopback", "回环", "zerotier", "radmin", "sangfor", "easyconnect",
)

_IPV4_RE = re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b")


def is_private(ip: str) -> bool:
    """是否局域网地址（192.168 / 10 / 172.16-31）。"""
    parts = ip.split(".")
    if len(parts) != 4:
        return False
    try:
        nums = [int(p) for p in parts]
    except ValueError:
        return False
    if any(n < 0 or n > 255 for n in nums):
        return False
    if nums[0] == 10:
        return True
    if nums[0] == 172 and 16 <= nums[1] <= 31:
        return True
    if nums[0] == 192 and nums[1] == 168:
        return True
    return False


def is_virtual_adapter(name: str) -> bool:
    lowered = (name or "").lower()
    return any(keyword in lowered for keyword in VIRTUAL_KEYWORDS)


def parse_ipconfig(text: str) -> list[tuple[str, str]]:
    """从 ipconfig 输出里解析 ``(网卡名, IPv4)`` 列表。

    只认“IPv4 地址”那一行（中英文都行），忽略“子网掩码”“默认网关”，
    也忽略自动配置出来的 169.254.x。
    """
    result: list[tuple[str, str]] = []
    adapter = ""
    for raw in (text or "").splitlines():
        if not raw.strip():
            continue
        # 网卡段标题：顶格写、且不以空白开头（Windows 英文版以冒号结尾）
        if not raw[0].isspace():
            adapter = raw.strip().rstrip(":").strip()
            continue
        line = raw.strip()
        if not (line.startswith("IPv4") or line.lower().startswith("ipv4")):
            continue
        # 中英文两种写法： "IPv4 地址 . . . : 192.168.1.5(首选)"
        #                  "IPv4 Address. . . . . : 192.168.1.5(Preferred)"
        tail = line.split(":")[-1]
        match = _IPV4_RE.search(tail)
        if not match:
            continue
        ip = match.group(0)
        if ip.startswith("127.") or ip.startswith("169.254."):
            continue
        result.append((adapter, ip))
    return result


def adapter_addresses() -> list[tuple[str, str]]:
    """读取本机网卡地址；拿不到就返回空列表（不抛异常）。"""
    try:
        completed = subprocess.run(
            ["ipconfig"], capture_output=True, check=False, timeout=10)
    except (OSError, subprocess.SubprocessError):
        return []
    raw = completed.stdout or b""
    for encoding in ("gbk", "utf-8"):
        try:
            text = raw.decode(encoding)
            break
        except UnicodeDecodeError:
            continue
    else:
        text = raw.decode("utf-8", errors="ignore")
    return parse_ipconfig(text)


def default_route_ip() -> str:
    """默认出口那块网卡的 IPv4；拿不到返回空字符串。"""
    sock = None
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.settimeout(0.5)
        sock.connect(("223.5.5.5", 80))  # UDP connect 只是查路由表，不发包
        return sock.getsockname()[0]
    except OSError:
        return ""
    finally:
        if sock is not None:
            sock.close()


def _fallback_addresses() -> list[str]:
    """ipconfig 不可用时的兜底：问主机名对应的所有 IPv4。"""
    found: list[str] = []
    try:
        infos = socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET)
    except OSError:
        return found
    for info in infos:
        ip = info[4][0]
        if ip.startswith("127.") or ip.startswith("169.254."):
            continue
        if ip not in found:
            found.append(ip)
    return found


def _priority(ip: str) -> tuple[int, str]:
    """排序：192.168 最常见排前面，其次 10，最后 172。"""
    if ip.startswith("192.168."):
        return (0, ip)
    if ip.startswith("10."):
        return (1, ip)
    return (2, ip)


def lan_addresses() -> list[str]:
    """返回手机大概率能连上的局域网地址，最可能的排在最前面。"""
    ordered: list[str] = []
    primary = default_route_ip()
    if is_private(primary):
        ordered.append(primary)

    raw = adapter_addresses()
    if not raw:
        # ipconfig 不可用（非 Windows / 被禁用）时才用主机名兜底
        candidates = [ip for ip in _fallback_addresses() if is_private(ip)]
    else:
        # 有网卡信息就只信网卡：剩下一堆虚拟网卡时宁可不显示，也不猜
        candidates = [ip for name, ip in raw
                      if not is_virtual_adapter(name) and is_private(ip)]

    for ip in sorted(set(candidates), key=_priority):
        if ip not in ordered:
            ordered.append(ip)
    return ordered


def lan_urls(port: int, addresses: list[str] | None = None) -> list[str]:
    if addresses is None:
        addresses = lan_addresses()
    return [f"http://{ip}:{port}/" for ip in addresses]


# ---------- Tailscale（手机走流量也能连回来） ----------

# Tailscale 给设备分的地址在这段里（运营商级 NAT 段）
TAILSCALE_CIDR = "100.64.0.0/10"
TAILSCALE_DOWNLOAD = "https://tailscale.com/download"
TAILSCALE_EXE_PATHS = (
    os.path.join(os.environ.get("ProgramFiles", r"C:\Program Files"),
                 "Tailscale", "tailscale.exe"),
    os.path.join(os.environ.get("ProgramFiles(x86)",
                                r"C:\Program Files (x86)"),
                 "Tailscale", "tailscale.exe"),
)

# 用户可以用这两个环境变量手动指定安装目录
TAILSCALE_ENV_VARS = ("VIDSTB_TAILSCALE_DIR", "TAILSCALE_DIR")
# MSI 允许用 INSTALLDIR 装到 D/E/F 盘，这时 Program Files 里什么都没有，
# 只能从注册表问真实的安装目录
TAILSCALE_REG_KEY = r"SOFTWARE\Tailscale IPN"
TAILSCALE_SERVICE_KEY = r"SYSTEM\CurrentControlSet\Services\Tailscale"


def _clean_registry_value(raw: str) -> str:
    """还原注册表里的路径值：可能带引号、带启动参数，也可能是个目录。"""
    value = (raw or "").strip()
    if not value:
        return ""
    if value.startswith('"'):
        end = value.find('"', 1)
        return value[1:end] if end > 0 else value[1:]
    match = re.search(r"^(.*?\.exe)(?=\s|$)", value, re.IGNORECASE)
    return match.group(1) if match else value


def _registry_tailscale_dirs() -> list[str]:
    """从注册表读自定义安装目录；非 Windows 或读不到就返回空列表。

    ``HKLM\\SOFTWARE\\Tailscale IPN`` 的默认值就是安装目录（例如 ``F:\\Tailscale\\``）；
    服务表项 ``ImagePath`` 则是 ``tailscaled.exe`` 的完整路径，取它的上级目录。
    """
    if os.name != "nt":
        return []
    try:
        import winreg
    except ImportError:
        return []
    found: list[str] = []
    for key_path, value_name, is_exe in (
            (TAILSCALE_REG_KEY, "", False),
            (TAILSCALE_SERVICE_KEY, "ImagePath", True)):
        try:
            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, key_path) as key:
                raw, _ = winreg.QueryValueEx(key, value_name)
        except OSError:
            continue
        path = _clean_registry_value(str(raw))
        if is_exe:
            path = os.path.dirname(path)
        if path and path not in found:
            found.append(path)
    return found


def _tailscale_candidate_dirs() -> list[str]:
    """所有可能装着 tailscale.exe 的目录，按可信度排序。"""
    dirs: list[str] = []
    for name in TAILSCALE_ENV_VARS:
        value = os.environ.get(name, "").strip()
        if value:
            dirs.append(value)
    for path in TAILSCALE_EXE_PATHS:
        if path:
            dirs.append(os.path.dirname(path))
    dirs.extend(_registry_tailscale_dirs())
    if os.name == "nt":
        # 兜底：常见自定义安装位置（X:\Tailscale）
        for letter in "DEFGHIJKLMNOPQRSTUVWXYZ":
            candidate = f"{letter}:\\Tailscale"
            if candidate not in dirs:
                dirs.append(candidate)
    return dirs


def _quiet() -> dict:
    """Windows 上跑命令行别弹出黑窗口。"""
    if os.name != "nt":
        return {}
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    return {"creationflags": flags} if flags else {}


def _run(command: list[str], timeout: float = 10.0) -> str:
    try:
        completed = subprocess.run(command, capture_output=True,
                                   check=False, timeout=timeout, **_quiet())
    except (OSError, subprocess.SubprocessError):
        return ""
    return (completed.stdout or b"").decode("utf-8", "replace")


def tailscale_exe() -> str:
    """找到 tailscale 命令行；找不到返回空字符串。

    先看 PATH，再依次试 Program Files、注册表记录的自定义安装目录、
    环境变量和各盘根目录下的 ``Tailscale`` 文件夹 —— MSI 用
    ``INSTALLDIR`` 装到 D/E/F 盘时也照样能找到。
    """
    for name in ("tailscale", "tailscale.exe"):
        found = shutil.which(name)
        if found:
            return found
    for directory in _tailscale_candidate_dirs():
        candidate = os.path.join(directory, "tailscale.exe")
        if os.path.isfile(candidate):
            return candidate
    return ""


def tailscale_info(port: int = 0) -> dict:
    """Tailscale 状态：装没装、连没连、设备地址是多少。

    手机装了 Tailscale 之后，不管走 WiFi 还是流量，都能用这里的地址访问本机。
    """
    exe = tailscale_exe()
    if not exe:
        return {"installed": False, "running": False, "ip": "", "host": "",
                "url": "", "download": TAILSCALE_DOWNLOAD,
                "hint": "电脑上还没装 Tailscale，装了手机才能用流量连回来"}
    text = _run([exe, "status", "--json"], timeout=8)
    data: dict = {}
    if text.strip():
        try:
            loaded = json.loads(text)
            if isinstance(loaded, dict):
                data = loaded
        except ValueError:
            data = {}
    self_node = data.get("Self") if isinstance(data.get("Self"), dict) else {}
    ips = self_node.get("TailscaleIPs") or data.get("TailscaleIPs") or []
    ip = next((item for item in ips if isinstance(item, str) and ":" not in item), "")
    host = (self_node.get("HostName") or data.get("HostName")
            or data.get("MachineName") or "")
    running = str(data.get("BackendState") or "") == "Running"
    if not running:
        hint = ("Tailscale 装了但还没连接：打开电脑上的 Tailscale 登录一次"
                "（命令行里也可以跑 tailscale up）")
    elif not ip:
        hint = "Tailscale 已连接，但还没拿到地址"
    else:
        hint = ""
    return {"installed": True, "running": running and bool(ip),
            "ip": ip, "host": host,
            "url": f"http://{ip}:{port}/" if (ip and port) else "",
            "download": TAILSCALE_DOWNLOAD, "hint": hint}
