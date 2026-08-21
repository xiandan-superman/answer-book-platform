from __future__ import annotations

import base64
import tempfile
import unittest
from email.message import Message
from pathlib import Path
from unittest.mock import patch

from app import lan_access
from app.server import PlatformHandler


class LanAccessConfigTests(unittest.TestCase):
    def test_config_is_created_and_reused(self) -> None:
        with tempfile.TemporaryDirectory() as raw_tmp:
            config_path = Path(raw_tmp) / "lan_access.json"
            with patch.object(lan_access, "LAN_CONFIG_PATH", config_path):
                first = lan_access.ensure_lan_access_config()
                second = lan_access.ensure_lan_access_config()
        self.assertEqual("monitor", first["username"])
        self.assertTrue(first["password"])
        self.assertEqual(first, second)

    def test_environment_password_takes_precedence(self) -> None:
        with tempfile.TemporaryDirectory() as raw_tmp:
            config_path = Path(raw_tmp) / "lan_access.json"
            with (
                patch.object(lan_access, "LAN_CONFIG_PATH", config_path),
                patch.dict("os.environ", {"ANSWER_BOOK_LAN_PASSWORD": "fixed-password"}),
            ):
                value = lan_access.ensure_lan_access_config()
        self.assertEqual("fixed-password", value["password"])

    def test_loopback_server_does_not_advertise_unreachable_lan_url(self) -> None:
        info = lan_access.lan_access_info(8766, include_secret=True, bind_host="127.0.0.1")

        self.assertFalse(info["enabled"])
        self.assertEqual([], info["urls"])
        self.assertNotIn("password", info)


class LanAuthenticationTests(unittest.TestCase):
    def _handler(self, address: str, authorization: str = "") -> PlatformHandler:
        handler = object.__new__(PlatformHandler)
        handler.client_address = (address, 12345)
        headers = Message()
        if authorization:
            headers["Authorization"] = authorization
        handler.headers = headers
        return handler

    def test_local_client_does_not_need_password(self) -> None:
        self.assertTrue(self._handler("127.0.0.1").lan_request_allowed())

    def test_remote_client_requires_valid_basic_auth(self) -> None:
        encoded = base64.b64encode(b"monitor:secret").decode("ascii")
        with (
            patch("app.server.lan_access_enabled", return_value=True),
            patch("app.server.lan_credentials", return_value=("monitor", "secret")),
        ):
            self.assertFalse(self._handler("192.168.1.20").lan_request_allowed())
            self.assertTrue(self._handler("192.168.1.20", f"Basic {encoded}").lan_request_allowed())

    def test_version_endpoint_is_public_when_lan_auth_enabled(self) -> None:
        """局域网监控鉴权开启时，/api/version 必须无鉴权可访问，否则前端版本标签加载失败。"""
        self.assertIn("/api/version", PlatformHandler.PUBLIC_LAN_PATHS)
        # 其它敏感/业务路径仍必须鉴权，不能混入公开集
        for path in ("/api/lan/access", "/api/tasks", "/api/practice/history"):
            self.assertNotIn(path, PlatformHandler.PUBLIC_LAN_PATHS)
        # do_GET 对公开路径在 LAN 鉴权开启时仍然能走到 /api/version 分支：
        # 验证门禁逻辑把 version 视为公开（version_is_public=True）
        self.assertEqual(PlatformHandler.PUBLIC_LAN_PATHS, {"/api/version"})

    def test_index_version_is_server_injected(self) -> None:
        """服务端把版本标签注入首页 #platformVersion，首次进入即可见（不依赖前端 refresh）。"""
        from app.server import _inject_index_version, _index_version_label
        html = '<span id="platformVersion">版本加载中...</span>'
        out = _inject_index_version(html)
        label = _index_version_label()
        self.assertIn(f">{label}</span>", out)
        self.assertNotIn("版本加载中", out)
        # 无占位时不改动
        self.assertEqual(_inject_index_version("<p>no placeholder</p>"), "<p>no placeholder</p>")
        self.assertTrue(hasattr(PlatformHandler, "serve_static"))


if __name__ == "__main__":
    unittest.main()
