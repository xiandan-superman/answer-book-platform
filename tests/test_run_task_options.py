from __future__ import annotations

import contextlib
import sys
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts import run_task


class RunTaskOptionPersistenceTests(unittest.TestCase):
    def test_cli_persists_the_explicit_recovery_options(self) -> None:
        remembered: dict[str, object] = {}

        def remember(task_id: str, **options: object) -> None:
            remembered["task_id"] = task_id
            remembered.update(options)

        with (
            patch.object(run_task, "platform_process_lock", return_value=contextlib.nullcontext()),
            patch.object(run_task, "remember_task_run_options", side_effect=remember),
            patch.object(run_task, "clear_task_control"),
            patch.object(run_task, "run_pipeline", return_value={"ok": True}),
            patch.object(sys, "argv", ["run_task.py", "task_cli_options", "--no-model", "--reuse-fragments"]),
        ):
            self.assertEqual(run_task.main(), 0)

        self.assertEqual(
            remembered,
            {
                "task_id": "task_cli_options",
                "use_model": False,
                "render": False,
                "reuse_fragments": True,
            },
        )


if __name__ == "__main__":
    unittest.main()
