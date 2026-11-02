"""局域网访问：地址解析 + /api/lan 返回内容。"""

import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import lan  # noqa: E402
import server  # noqa: E402

# 中文 Windows 的 ipconfig 输出（含一块 VMware 虚拟网卡）
SAMPLE_ZH = """
Windows IP 配置

以太网适配器 以太网:

   连接特定的 DNS 后缀 . . . . . . . :
   IPv4 地址 . . . . . . . . . . . . : 192.168.1.7(首选)
   子网掩码  . . . . . . . . . . . . : 255.255.255.0
   默认网关  . . . . . . . . . . . . : 192.168.1.1

以太网适配器 VMware Network Adapter VMnet8:

   IPv4 地址 . . . . . . . . . . . . : 192.168.220.1(首选)
   子网掩码  . . . . . . . . . . . . : 255.255.255.0

无线局域网适配器 WLAN:

   IPv4 地址 . . . . . . . . . . . . : 10.0.0.23(首选)
   默认网关  . . . . . . . . . . . . : 10.0.0.1
"""

SAMPLE_EN = """
Windows IP Configuration

Ethernet adapter Ethernet:

   IPv4 Address. . . . . . . . . . . : 192.168.1.7(Preferred)
   Subnet Mask . . . . . . . . . . . : 255.255.255.0
   Default Gateway . . . . . . . . . : 192.168.1.1
"""


class ParseTest(unittest.TestCase):
    def test_chinese_output(self):
        pairs = lan.parse_ipconfig(SAMPLE_ZH)
        ips = [ip for _, ip in pairs]
        self.assertIn("192.168.1.7", ips)
        self.assertIn("10.0.0.23", ips)
        self.assertIn("192.168.220.1", ips)   # 解析阶段先都留着，过滤交给下一层

    def test_english_output(self):
        pairs = lan.parse_ipconfig(SAMPLE_EN)
        self.assertEqual([ip for _, ip in pairs], ["192.168.1.7"])

    def test_virtual_adapter_filtered(self):
        pairs = lan.parse_ipconfig(SAMPLE_ZH)
        kept = [ip for name, ip in pairs if not lan.is_virtual_adapter(name)]
        self.assertEqual(kept, ["192.168.1.7", "10.0.0.23"])

    def test_is_private(self):
        self.assertTrue(lan.is_private("192.168.1.7"))
        self.assertTrue(lan.is_private("10.0.0.23"))
        self.assertTrue(lan.is_private("172.20.10.5"))
        self.assertFalse(lan.is_private("172.32.0.1"))
        self.assertFalse(lan.is_private("8.8.8.8"))
        self.assertFalse(lan.is_private(""))
        self.assertFalse(lan.is_private("192.168.1.999"))

    def test_empty_input(self):
        self.assertEqual(lan.parse_ipconfig(""), [])


class AddressesTest(unittest.TestCase):
    def test_lan_addresses_prefers_real_adapters(self):
        with mock.patch.object(lan, "adapter_addresses",
                               return_value=[("以太网", "192.168.1.7"),
                                             ("VMware Network Adapter VMnet8", "192.168.220.1")]), \
             mock.patch.object(lan, "default_route_ip", return_value="192.168.1.7"):
            self.assertEqual(lan.lan_addresses(), ["192.168.1.7"])

    def test_lan_urls(self):
        self.assertEqual(lan.lan_urls(8777, ["192.168.1.7"]),
                         ["http://192.168.1.7:8777/"])

    def test_no_address_when_only_virtual(self):
        with mock.patch.object(lan, "adapter_addresses",
                               return_value=[("VMware", "192.168.220.1")]), \
             mock.patch.object(lan, "default_route_ip", return_value=""):
            self.assertEqual(lan.lan_addresses(), [])


class LanInfoTest(unittest.TestCase):
    class StubServer:
        lan_port = 8777
        lan_host = "0.0.0.0"

    def test_local_only(self):
        class Local(self.StubServer):
            lan_host = "127.0.0.1"
        info = server.lan_info(Local())
        self.assertTrue(info["local_only"])
        self.assertEqual(info["addresses"], [])
        self.assertEqual(info["local_url"], "http://127.0.0.1:8777/")

    def test_lan_exposed(self):
        with mock.patch.object(lan, "lan_addresses", return_value=["192.168.1.7"]):
            info = server.lan_info(self.StubServer())
        self.assertFalse(info["local_only"])
        self.assertEqual(info["urls"], ["http://192.168.1.7:8777/"])
        self.assertEqual(info["port"], 8777)

    def test_without_server(self):
        info = server.lan_info()
        self.assertEqual(info["port"], 0)
        self.assertEqual(info["urls"], [])

    def test_lan_info_carries_tailscale(self):
        fake = {"installed": True, "running": True, "ip": "100.101.102.103",
                "url": "http://100.101.102.103:8777/", "hint": ""}
        with mock.patch.object(lan, "lan_addresses", return_value=[]), \
                mock.patch.object(lan, "tailscale_info", return_value=fake):
            info = server.lan_info(self.StubServer())
        self.assertEqual(info["tailscale"]["ip"], "100.101.102.103")


# tailscale status --json 的真实结构（字段做了精简）
TAILSCALE_JSON = """
{
  "BackendState": "Running",
  "Self": {
    "HostName": "desktop-pc",
    "TailscaleIPs": ["100.101.102.103", "fd7a:115c:a1e0::1"],
    "Online": true
  }
}
"""


class TailscaleTest(unittest.TestCase):
    def test_not_installed(self):
        with mock.patch.object(lan, "tailscale_exe", return_value=""):
            info = lan.tailscale_info(8777)
        self.assertFalse(info["installed"])
        self.assertFalse(info["running"])
        self.assertEqual(info["ip"], "")
        self.assertTrue(info["hint"])          # 得有一句提示
        self.assertTrue(info["download"])

    def test_connected(self):
        with mock.patch.object(lan, "tailscale_exe", return_value="tailscale"), \
                mock.patch.object(lan, "_run", return_value=TAILSCALE_JSON):
            info = lan.tailscale_info(8777)
        self.assertTrue(info["installed"])
        self.assertTrue(info["running"])
        self.assertEqual(info["ip"], "100.101.102.103")   # 取 IPv4，跳过 IPv6
        self.assertEqual(info["host"], "desktop-pc")
        self.assertEqual(info["url"], "http://100.101.102.103:8777/")
        self.assertEqual(info["hint"], "")

    def test_installed_but_not_running(self):
        payload = TAILSCALE_JSON.replace('"Running"', '"NeedsLogin"')
        with mock.patch.object(lan, "tailscale_exe", return_value="tailscale"), \
                mock.patch.object(lan, "_run", return_value=payload):
            info = lan.tailscale_info(8777)
        self.assertTrue(info["installed"])
        self.assertFalse(info["running"])
        self.assertIn("登录", info["hint"])

    def test_broken_output_is_safe(self):
        with mock.patch.object(lan, "tailscale_exe", return_value="tailscale"), \
                mock.patch.object(lan, "_run", return_value="不是 json"):
            info = lan.tailscale_info(8777)
        self.assertTrue(info["installed"])
        self.assertFalse(info["running"])
        self.assertEqual(info["ip"], "")

    def test_no_port_no_url(self):
        with mock.patch.object(lan, "tailscale_exe", return_value="tailscale"), \
                mock.patch.object(lan, "_run", return_value=TAILSCALE_JSON):
            self.assertEqual(lan.tailscale_info(0)["url"], "")


class TailscaleDiscoveryTest(unittest.TestCase):
    """装到非系统盘时也要能找到 tailscale.exe。"""

    def test_clean_registry_value_quoted_with_args(self):
        self.assertEqual(
            lan._clean_registry_value(r'"F:\Tailscale\tailscaled.exe" --tun'),
            r"F:\Tailscale\tailscaled.exe")

    def test_clean_registry_value_plain_exe(self):
        self.assertEqual(
            lan._clean_registry_value(r"F:\Tailscale\tailscaled.exe"),
            r"F:\Tailscale\tailscaled.exe")

    def test_clean_registry_value_directory(self):
        # 注册表默认值是安装目录，不是 exe，要原样保留
        self.assertEqual(lan._clean_registry_value("F:\\Tailscale\\"),
                         "F:\\Tailscale\\")

    def test_exe_found_in_custom_dir(self):
        fake_dir = "F:\\Tailscale"
        expected = os.path.join(fake_dir, "tailscale.exe")
        with mock.patch.object(lan.shutil, "which", return_value=""), \
                mock.patch.object(lan, "_tailscale_candidate_dirs",
                                  return_value=[fake_dir]), \
                mock.patch.object(lan.os.path, "isfile",
                                  side_effect=lambda path: path == expected):
            self.assertEqual(lan.tailscale_exe(), expected)

    def test_env_dir_takes_priority(self):
        with mock.patch.dict(os.environ,
                             {"VIDSTB_TAILSCALE_DIR": "F:\\Custom"}), \
                mock.patch.object(lan, "_registry_tailscale_dirs",
                                  return_value=[]):
            dirs = lan._tailscale_candidate_dirs()
        self.assertEqual(dirs[0], "F:\\Custom")


if __name__ == "__main__":
    unittest.main()
