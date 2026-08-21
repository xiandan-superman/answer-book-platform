from __future__ import annotations

import json
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


class QuestionUnderstandingProgressTests(unittest.TestCase):
    def test_writes_per_question_progress_for_task_monitoring(self) -> None:
        from app.question_understanding import build_question_understandings

        with tempfile.TemporaryDirectory() as raw_tmp:
            root = Path(raw_tmp)
            output = root / "question_understanding.json"
            progress = root / "question_understanding_progress.json"
            exam = {
                "items": [
                    {"question_id": "q01", "number": "1", "stem": "解释布拉格定律。"},
                    {"question_id": "q02", "number": "2", "stem": "列出体心立方的消光规律。"},
                ]
            }

            report = build_question_understandings(exam, output, progress_json=progress)
            saved = json.loads(progress.read_text(encoding="utf-8"))

        self.assertEqual(2, report["question_count"])
        self.assertEqual("completed", saved["status"])
        self.assertEqual(2, saved["total"])
        self.assertEqual(2, saved["completed"])
        self.assertEqual({}, saved["active"])
        self.assertEqual("question_completed", saved["recent_events"][-1]["event"])

    def test_parallel_workers_keep_report_and_exam_order_stable(self) -> None:
        from app.question_understanding import build_question_understandings

        thread_ids: set[int] = set()

        def fake_understanding(question, _output_dir, **_kwargs):
            thread_ids.add(threading.get_ident())
            time.sleep(0.04 if question["question_id"] != "q02" else 0.01)
            return {"question_id": question["question_id"], "needs_vision_model": False, "vision_used": False}

        with tempfile.TemporaryDirectory() as raw_tmp, patch("app.question_understanding.build_question_understanding", side_effect=fake_understanding):
            root = Path(raw_tmp)
            exam = {"items": [{"question_id": "q01"}, {"question_id": "q02"}, {"question_id": "q03"}]}
            report = build_question_understandings(exam, root / "understanding.json")

        self.assertEqual(["q01", "q02", "q03"], [item["question_id"] for item in report["items"]])
        self.assertEqual(["q01", "q02", "q03"], [item["question_understanding"]["question_id"] for item in exam["items"]])
        self.assertTrue(report["concurrency"]["parallel_enabled"])
        self.assertGreaterEqual(len(thread_ids), 2)


if __name__ == "__main__":
    unittest.main()
