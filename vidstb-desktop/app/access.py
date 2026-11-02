"""访问控制：谁可以打开这个页面。

两道门，各自都可以关掉（留空就是不启用）：

    1) IP 白名单  —— 只放行填进来的 IP 或网段（支持 CIDR）
    2) 访问密码  —— 第一次打开输一次，之后用 Cookie 记住

本机（127.0.0.1 / ::1）永远放行，免得自己在电脑上双击 exe 还要输密码。
密码只存 sha256，不存明文。
"""

from __future__ import annotations

import hashlib
import ipaddress
from typing import Any, Dict, Iterable, List, Optional

# 密码哈希的前缀，换一个值就等于让旧 Cookie 全部失效
_HASH_SALT = "vidstb-access-v1"
COOKIE_NAME = "vidstb_auth"
LOOPBACK = ("127.0.0.1", "::1", "0:0:0:0:0:0:0:1")


def password_hash(password: str) -> str:
    return hashlib.sha256(f"{_HASH_SALT}:{password or ''}".encode("utf-8")).hexdigest()


def normalize_ip(ip: str) -> str:
    """::ffff:192.168.1.5 -> 192.168.1.5"""
    text = (ip or "").strip()
    if text.lower().startswith("::ffff:") and "." in text:
        return text[7:]
    return text


def is_loopback(ip: str) -> bool:
    return normalize_ip(ip) in LOOPBACK


def parse_networks(values: Optional[Iterable[str]]) -> List[Any]:
    """把配置里的白名单转成 ipaddress 网络对象，忽略写错的条目。"""
    networks: List[Any] = []
    for raw in values or []:
        text = str(raw or "").strip()
        if not text:
            continue
        try:
            networks.append(ipaddress.ip_network(text, strict=False))
        except ValueError:
            continue
    return networks


def ip_allowed(ip: str, values: Optional[Iterable[str]]) -> bool:
    """白名单为空表示不限制。"""
    networks = parse_networks(values)
    if not networks:
        return True
    try:
        address = ipaddress.ip_address(normalize_ip(ip))
    except ValueError:
        return False
    for network in networks:
        if address.version == network.version and address in network:
            return True
    return False


def has_password(config: Optional[Dict[str, Any]]) -> bool:
    return bool((config or {}).get("access_password"))


def check_password(config: Optional[Dict[str, Any]], password: str) -> bool:
    stored = (config or {}).get("access_password") or ""
    if not stored:
        return True
    if not password:
        return False
    return password_hash(password) == stored


def token_from_cookie(cookie_header: str, name: str = COOKIE_NAME) -> str:
    """从 Cookie 头里取出令牌。"""
    for part in (cookie_header or "").split(";"):
        key, _, value = part.partition("=")
        if key.strip() == name:
            return value.strip()
    return ""


def cookie_ok(config: Optional[Dict[str, Any]], cookie_header: str) -> bool:
    stored = (config or {}).get("access_password") or ""
    if not stored:
        return True
    return token_from_cookie(cookie_header) == stored
