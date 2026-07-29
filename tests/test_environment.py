from __future__ import annotations

import unittest
from unittest.mock import patch


class DrawingRuntimeEnvironmentTests(unittest.TestCase):
    def test_drawing_runtime_probe_reports_renderer_result(self) -> None:
        from app.environment import _check_drawing_runtime

        fake_result = type("Result", (), {"ok": True, "issues": [], "returncode": 0, "stderr": ""})()
        with patch("app.environment.find_spec", return_value=object()), patch(
            "app.environment.run_drawing_code", return_value=fake_result
        ) as run:
            result = _check_drawing_runtime()

        self.assertTrue(result["ok"])
        self.assertTrue(result["matplotlib_available"])
        self.assertIn(result["resource_limits"], {"available", "not_available_on_this_platform"})
        self.assertTrue(run.called)
