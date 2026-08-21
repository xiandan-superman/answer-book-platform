from __future__ import annotations

import unittest
from unittest.mock import patch


class DrawingRuntimeEnvironmentTests(unittest.TestCase):
    def test_environment_exposes_project_font_render_readiness(self) -> None:
        from app.environment import check_environment

        font_report = {"enabled": True, "font_file_count": 42, "font_directory_count": 3}
        with patch("app.environment.ensure_project_dirs"), patch(
            "app.environment.list_providers", return_value={}
        ), patch("app.environment.find_mathml2omml_xsl", return_value=None), patch(
            "app.environment.find_omml2mathml_xsl", return_value=None
        ), patch(
            "app.environment._check_word_mac", return_value={"applicable": False}
        ), patch("app.environment._check_word_windows", return_value={"applicable": False}), patch(
            "app.environment._check_drawing_runtime", return_value={"ok": True}
        ), patch("app.environment._check_network", return_value={"ok": False}), patch(
            "app.environment.project_font_diagnostics", return_value=font_report
        ), patch("app.environment.find_spec", return_value=None), patch(
            "app.environment.shutil.which", return_value=None
        ), patch("app.environment.platform.system", return_value="Linux"):
            result = check_environment()

        self.assertEqual(font_report, result["document_tools"]["project_fonts"])

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

    def test_network_probe_reports_readiness_for_each_provider(self) -> None:
        from app.environment import _check_network

        connection = type(
            "Connection",
            (),
            {
                "__enter__": lambda self: self,
                "__exit__": lambda self, *_args: False,
            },
        )()
        with patch("app.environment.socket.create_connection", return_value=connection):
            result = _check_network(
                {
                    "first": {"base_url": "https://first.example/v1"},
                    "second": {"base_url": "https://second.example/v1"},
                }
            )

        self.assertEqual({"first": True, "second": True}, result["by_provider"])
        self.assertEqual([], result["unreachable_providers"])
