from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class RuntimeMonitorFrontendTests(unittest.TestCase):
    def test_monitor_has_user_facing_health_regions_and_ten_second_refresh(self) -> None:
        html = (ROOT / "web" / "index.html").read_text(encoding="utf-8")
        script = (ROOT / "web" / "app.js").read_text(encoding="utf-8")
        self.assertIn('id="systemHealthOverview"', html)
        self.assertIn('id="systemModelHealthLabel"', html)
        self.assertIn('id="systemRunningTasks"', html)
        self.assertIn("function startSystemMonitorPolling()", script)
        self.assertIn("}, 10000);", script)
        self.assertIn("if (document.hidden) stopSystemMonitorPolling();", script)

    def test_task_manager_maps_health_to_four_user_states(self) -> None:
        script = (ROOT / "web" / "app.js").read_text(encoding="utf-8")
        for label in ("正在处理", "正在等待", "等待时间较长", "任务已中断"):
            self.assertIn(label, script)
