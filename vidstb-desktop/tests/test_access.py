"""访问控制（访问密码 + IP 白名单）的单元测试。

不启服务，直接测判断逻辑本身；服务端把这段逻辑抽成了 access_decision()。
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import access  # noqa: E402
import server  # noqa: E402


class PasswordTest(unittest.TestCase):
    def test_hash_is_stable_and_not_plain(self):
        hashed = access.password_hash("6foJ")
        self.assertEqual(hashed, access.password_hash("6foJ"))
        self.assertNotIn("6foJ", hashed)
        self.assertNotEqual(hashed, access.password_hash("6foj"))  # 大小写敏感

    def test_check_password(self):
        config = {"access_password": access.password_hash("abc123")}
        self.assertTrue(access.check_password(config, "abc123"))
        self.assertFalse(access.check_password(config, "abc124"))
        self.assertFalse(access.check_password(config, ""))

    def test_no_password_means_open(self):
        config = {"access_password": ""}
        self.assertFalse(access.has_password(config))
        self.assertTrue(access.check_password(config, "随便"))
        self.assertTrue(access.cookie_ok(config, ""))

    def test_cookie_roundtrip(self):
        config = {"access_password": access.password_hash("pw")}
        good = f"{access.COOKIE_NAME}={config['access_password']}"
        self.assertTrue(access.cookie_ok(config, good))
        self.assertFalse(access.cookie_ok(config, f"{access.COOKIE_NAME}=xxx"))
        self.assertFalse(access.cookie_ok(config, "other=1"))
        self.assertEqual(access.token_from_cookie(f"a=1; {good}; b=2"),
                         config["access_password"])


class IpWhitelistTest(unittest.TestCase):
    def test_empty_means_allow_all(self):
        self.assertTrue(access.ip_allowed("8.8.8.8", []))
        self.assertTrue(access.ip_allowed("100.1.2.3", None))

    def test_single_ip(self):
        allow = ["100.101.102.103"]
        self.assertTrue(access.ip_allowed("100.101.102.103", allow))
        self.assertFalse(access.ip_allowed("100.101.102.104", allow))

    def test_cidr(self):
        # Tailscale 网段
        self.assertTrue(access.ip_allowed("100.101.102.103", ["100.64.0.0/10"]))
        self.assertFalse(access.ip_allowed("192.168.1.5", ["100.64.0.0/10"]))
        # 局域网网段
        self.assertTrue(access.ip_allowed("192.168.71.70", ["192.168.71.0/24"]))
        self.assertFalse(access.ip_allowed("192.168.72.70", ["192.168.71.0/24"]))

    def test_multiple_and_bad_entries(self):
        allow = ["100.64.0.0/10", "192.168.71.0/24", "写错的"]
        self.assertTrue(access.ip_allowed("100.99.1.1", allow))
        self.assertTrue(access.ip_allowed("192.168.71.9", allow))
        self.assertFalse(access.ip_allowed("10.0.0.1", allow))

    def test_ipv6_mapped_ipv4(self):
        self.assertTrue(access.ip_allowed("::ffff:192.168.71.70",
                                          ["192.168.71.0/24"]))

    def test_normalize_and_loopback(self):
        self.assertEqual(access.normalize_ip("::ffff:192.168.1.5"), "192.168.1.5")
        self.assertTrue(access.is_loopback("127.0.0.1"))
        self.assertTrue(access.is_loopback("::1"))
        self.assertFalse(access.is_loopback("100.101.102.103"))


class DecisionTest(unittest.TestCase):
    """server.access_decision()：把上面两者合起来的那道门。"""

    OPEN = {"access_password": "", "allowed_ips": []}

    def test_loopback_always_ok(self):
        config = {"access_password": access.password_hash("pw"),
                  "allowed_ips": ["100.64.0.0/10"]}
        # 本机既不在白名单里、也没带 cookie，依然放行
        self.assertEqual(server.access_decision(config, "127.0.0.1", "", "/"), "ok")
        self.assertEqual(server.access_decision(config, "::1", "", "/api/state"), "ok")

    def test_open_config(self):
        self.assertEqual(server.access_decision(self.OPEN, "8.8.8.8", "", "/"), "ok")

    def test_denied_by_whitelist(self):
        config = {"access_password": "", "allowed_ips": ["100.64.0.0/10"]}
        self.assertEqual(server.access_decision(config, "8.8.8.8", "", "/"), "denied")
        self.assertEqual(server.access_decision(config, "8.8.8.8", "", "/api/login"),
                         "denied")

    def test_password_required(self):
        config = {"access_password": access.password_hash("pw"), "allowed_ips": []}
        self.assertEqual(server.access_decision(config, "100.1.1.1", "", "/"), "login")
        self.assertEqual(server.access_decision(config, "100.1.1.1", "", "/api/state"),
                         "login")
        # 登录页和登录接口本身要放行，不然永远登不进去
        self.assertEqual(server.access_decision(config, "100.1.1.1", "", "/login"), "ok")
        self.assertEqual(server.access_decision(config, "100.1.1.1", "", "/api/login"),
                         "ok")

    def test_password_with_cookie(self):
        stored = access.password_hash("pw")
        config = {"access_password": stored, "allowed_ips": []}
        cookie = f"{access.COOKIE_NAME}={stored}"
        self.assertEqual(server.access_decision(config, "100.1.1.1", cookie, "/"), "ok")

    def test_both_gates(self):
        stored = access.password_hash("pw")
        config = {"access_password": stored, "allowed_ips": ["100.64.0.0/10"]}
        ip = "100.100.1.1"          # 在 Tailscale 网段内
        # 白名单过了、密码没过
        self.assertEqual(server.access_decision(config, ip, "", "/"), "login")
        # 都过了
        self.assertEqual(server.access_decision(
            config, ip, f"{access.COOKIE_NAME}={stored}", "/"), "ok")
        # 白名单没过就轮不到密码
        self.assertEqual(server.access_decision(config, "8.8.8.8", "", "/"), "denied")


class AuthCookieTest(unittest.TestCase):
    """登录后下发的 Cookie 字符串。"""

    class StubApp:
        def __init__(self, config):
            self.config = config

    class StubHandler:
        def __init__(self, app):
            self.app = app

        _auth_cookie = server.Handler._auth_cookie

    def test_cookie_has_token_and_flags(self):
        stored = access.password_hash("pw")
        handler = self.StubHandler(self.StubApp({"access_password": stored}))
        cookie = handler._auth_cookie()
        self.assertIn(f"{access.COOKIE_NAME}={stored}", cookie)
        self.assertIn("HttpOnly", cookie)
        self.assertIn("Path=/", cookie)

    def test_logout_clears(self):
        handler = self.StubHandler(self.StubApp({"access_password": "x"}))
        self.assertIn("Max-Age=0", handler._auth_cookie(clear=True))


if __name__ == "__main__":
    unittest.main()
