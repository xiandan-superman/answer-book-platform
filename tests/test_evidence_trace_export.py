from __future__ import annotations

import csv
import json
import tempfile
import unittest
from pathlib import Path


def write_csv(path: Path, rows: list[dict[str, str]]) -> None:
    fields = sorted({key for row in rows for key in row})
    with path.open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


class EvidenceTraceExportTests(unittest.TestCase):
    def test_program_candidate_top5_is_globally_ranked_not_csv_order(self) -> None:
        from app.evidence_trace_export import build_evidence_trace_rows

        with tempfile.TemporaryDirectory() as raw_tmp:
            stage = Path(raw_tmp)
            (stage / "structured_exam.json").write_text(
                json.dumps({"items": [{"question_id": "q1", "section": "一、简答题", "number": "1", "stem": "测试题"}]}, ensure_ascii=False),
                encoding="utf-8",
            )
            (stage / "knowledge_plans.json").write_text(json.dumps({"plans": []}, ensure_ascii=False), encoding="utf-8")
            (stage / "evidence_selection.json").write_text(json.dumps({"selections": []}, ensure_ascii=False), encoding="utf-8")
            write_csv(
                stage / "retrieval_candidates.csv",
                [
                    {"question_id": "q1", "evidence_id": "ev_01", "score": "3", "evidence_text": "低分"},
                    {"question_id": "q1", "evidence_id": "ev_02", "score": "99", "evidence_text": "最高分"},
                    {"question_id": "q1", "evidence_id": "ev_03", "score": "70", "evidence_text": "第二高"},
                    {"question_id": "q1", "evidence_id": "ev_04", "score": "60", "evidence_text": "第三高"},
                    {"question_id": "q1", "evidence_id": "ev_05", "score": "50", "evidence_text": "第四高"},
                    {"question_id": "q1", "evidence_id": "ev_06", "score": "40", "evidence_text": "第五高"},
                ],
            )

            row = build_evidence_trace_rows(stage)[0]

        self.assertEqual(6, row["程序候选依据数量"])
        self.assertEqual(
            ["ev_02", "ev_03", "ev_04", "ev_05", "ev_06"],
            [line.split("｜", 1)[0] for line in row["程序候选依据Top5"].splitlines()],
        )
