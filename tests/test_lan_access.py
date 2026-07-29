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


if __name__ == "__main__":
    unittest.main()
