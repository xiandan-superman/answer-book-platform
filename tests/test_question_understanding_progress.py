from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path


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


if __name__ == "__main__":
    unittest.main()
