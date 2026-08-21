from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from PIL import Image

from app.pipeline import figure_visual_qa_blocking_findings, figure_visual_qa_issue_count


class FigureVisualQualityBoundaryTests(unittest.TestCase):
    def test_model_semantic_disagreement_is_advisory_for_valid_image(self) -> None:
        with tempfile.TemporaryDirectory() as raw_tmp:
            image_path = Path(raw_tmp) / "phase.png"
            Image.new("RGB", (320, 180), "white").save(image_path)
            report = {
                "enabled": True,
                "items": [
                    {
                        "question_id": "q1",
                        "figure_id": "fig1",
                        "path": str(image_path),
                        "qa": {"ok": False, "summary": "模型认为学科标签有争议"},
                    }
                ],
            }

            self.assertEqual(1, figure_visual_qa_issue_count(report))
            self.assertEqual([], figure_visual_qa_blocking_findings(report))

    def test_missing_image_is_a_deterministic_blocker(self) -> None:
        report = {
            "enabled": True,
            "items": [
                {
                    "question_id": "q1",
                    "figure_id": "fig1",
                    "path": "/definitely/missing/figure.png",
                    "qa": {"ok": False},
                }
            ],
        }

        findings = figure_visual_qa_blocking_findings(report)

        self.assertEqual(1, len(findings))
        self.assertEqual("figure image missing", findings[0]["reason"])

    def test_unreadable_image_is_a_deterministic_blocker(self) -> None:
        with tempfile.TemporaryDirectory() as raw_tmp:
            image_path = Path(raw_tmp) / "broken.png"
            image_path.write_text("not an image", encoding="utf-8")
            report = {
                "enabled": True,
                "items": [
                    {
                        "question_id": "q1",
                        "figure_id": "fig1",
                        "path": str(image_path),
                        "qa": {"ok": False},
                    }
                ],
            }

            findings = figure_visual_qa_blocking_findings(report)

        self.assertEqual(1, len(findings))
        self.assertTrue(findings[0]["reason"].startswith("figure image unreadable:"))


if __name__ == "__main__":
    unittest.main()
