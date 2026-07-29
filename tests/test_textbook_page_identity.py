from __future__ import annotations

import csv
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def write_csv(path: Path, rows: list[dict[str, str]]) -> None:
    fields = sorted({key for row in rows for key in row})
    with path.open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


class TextbookPageIdentityTests(unittest.TestCase):
    def test_page_lookup_keeps_same_pdf_page_from_different_volumes_separate(self) -> None:
        from app.retrieval import build_page_lookup

        with tempfile.TemporaryDirectory() as raw_tmp:
            page_map = Path(raw_tmp) / "textbook_page_map.csv"
            write_csv(
                page_map,
                [
                    {"textbook": "材料现代分析测试方法", "source_file": "第一册.zip", "pdf_page_idx": "150", "printed_page": "138"},
                    {"textbook": "材料现代分析测试方法", "source_file": "第二册.zip", "pdf_page_idx": "150", "printed_page": "338"},
                ],
            )
            lookup = build_page_lookup(page_map)

        self.assertEqual("138", lookup[("材料现代分析测试方法", "第一册.zip", "150")]["printed_page"])
        self.assertEqual("338", lookup[("材料现代分析测试方法", "第二册.zip", "150")]["printed_page"])

    def test_missing_page_inference_uses_each_source_offset(self) -> None:
        from app.textbook_index import infer_missing_pages

        inferred = infer_missing_pages(
            [
                {"textbook": "同一本教材", "source_file": "第一册.zip", "pdf_page_idx": "100", "printed_page": "88", "verified": "true"},
                {"textbook": "同一本教材", "source_file": "第一册.zip", "pdf_page_idx": "101", "printed_page": "", "verified": "false"},
                {"textbook": "同一本教材", "source_file": "第二册.zip", "pdf_page_idx": "100", "printed_page": "288", "verified": "true"},
                {"textbook": "同一本教材", "source_file": "第二册.zip", "pdf_page_idx": "101", "printed_page": "", "verified": "false"},
            ]
        )

        self.assertEqual("89", inferred[1]["printed_page"])
        self.assertEqual("289", inferred[3]["printed_page"])

    def test_retrieval_keeps_candidates_from_same_pdf_page_in_separate_volumes(self) -> None:
        from app.retrieval import build_candidates

        with tempfile.TemporaryDirectory() as raw_tmp:
            tmp = Path(raw_tmp)
            blocks = tmp / "textbook_blocks.csv"
            page_map = tmp / "textbook_page_map.csv"
            output = tmp / "retrieval_candidates.csv"
            write_csv(
                blocks,
                [
                    {
                        "textbook": "材料现代分析测试方法",
                        "source_file": "第一册.zip",
                        "page_idx": "150",
                        "block_index": "1",
                        "block_type": "text",
                        "source_type": "text_block",
                        "text": "晶带定律第一册的推导说明。",
                        "retrieval_text": "晶带定律第一册的推导说明。",
                    },
                    {
                        "textbook": "材料现代分析测试方法",
                        "source_file": "第二册.zip",
                        "page_idx": "150",
                        "block_index": "1",
                        "block_type": "text",
                        "source_type": "text_block",
                        "text": "晶带定律第二册的应用说明。",
                        "retrieval_text": "晶带定律第二册的应用说明。",
                    },
                ],
            )
            write_csv(
                page_map,
                [
                    {"textbook": "材料现代分析测试方法", "source_file": "第一册.zip", "pdf_page_idx": "150", "printed_page": "138", "verified": "true"},
                    {"textbook": "材料现代分析测试方法", "source_file": "第二册.zip", "pdf_page_idx": "150", "printed_page": "338", "verified": "true"},
                ],
            )
            candidates = build_candidates(
                {"items": [{"question_id": "q1", "stem": "说明晶带定律。"}]}, blocks, page_map, output, top_k=2
            )

        self.assertEqual(2, len(candidates))
        self.assertEqual({"138", "338"}, {candidate.printed_page for candidate in candidates})

    def test_manual_page_map_requires_source_file_identity(self) -> None:
        from app.textbook_index import apply_manual_page_map

        rows = [
            {"textbook": "同一本教材", "source_file": "第一册.zip", "pdf_page_idx": "50", "printed_page": ""},
            {"textbook": "同一本教材", "source_file": "第二册.zip", "pdf_page_idx": "50", "printed_page": ""},
        ]
        result = apply_manual_page_map(
            rows,
            {("同一本教材", "第二册.zip", "50"): {"printed_page": "250", "verified": "true"}},
        )

        self.assertEqual("", result[0]["printed_page"])
        self.assertEqual("250", result[1]["printed_page"])


if __name__ == "__main__":
    unittest.main()
