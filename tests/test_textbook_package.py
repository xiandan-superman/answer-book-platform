from __future__ import annotations

import csv
import json
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


def make_mineru_package(path: Path) -> None:
    content = [
        {"type": "text", "page_idx": 0, "text": "第 42 页 相平衡教材正文。"},
        {
            "type": "image",
            "page_idx": 1,
            "img_path": "images/fig.jpg",
            "image_caption": ["图 1 相图示意"],
            "image_footnote": ["横轴为组成，纵轴为温度。"],
        },
        {"type": "image", "page_idx": 2, "img_path": "images/unreadable.jpg"},
        {
            "type": "table",
            "page_idx": 3,
            "img_path": "images/table.jpg",
            "table_caption": ["表 1 热力学数据"],
            "table_body": "<table><tr><th>T/K</th><th>G</th></tr><tr><td>298</td><td>-10</td></tr></table>",
        },
        {"type": "equation", "page_idx": 4, "text": "G=H-TS", "img_path": "images/eq.jpg"},
    ]
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("mineru/book_content_list.json", json.dumps(content, ensure_ascii=False))
        zf.writestr("mineru/book_content_list_v2.json", "[]")
        zf.writestr("mineru/layout.json", "{}")
        zf.writestr("mineru/full.md", "# 相平衡\n")
        zf.writestr("mineru/book_origin.pdf", b"%PDF-1.4\n")
        zf.writestr("mineru/images/fig.jpg", b"fake-jpg")
        zf.writestr("mineru/images/unreadable.jpg", b"fake-jpg")
        zf.writestr("mineru/images/table.jpg", b"fake-jpg")
        zf.writestr("mineru/images/eq.jpg", b"fake-jpg")


class TextbookPackageTests(unittest.TestCase):
    def test_mineru_zip_indexes_assets_and_visual_limitations(self) -> None:
        from app.textbook_index import build_textbook_index_for_files

        with tempfile.TemporaryDirectory() as raw_tmp:
            tmp = Path(raw_tmp)
            package_zip = tmp / "测试教材.zip"
            stage = tmp / "stage"
            make_mineru_package(package_zip)

            result = build_textbook_index_for_files(
                [package_zip],
                stage,
                citation_names_by_source={str(package_zip.resolve()): "测试教材"},
            )
            rows = read_csv(Path(result.blocks_csv))
            audit = json.loads((stage / "textbook_package_audit.json").read_text(encoding="utf-8"))

        self.assertEqual(1, result.textbook_count)
        self.assertGreaterEqual(result.block_count, 5)
        self.assertEqual(True, audit["packages"][0]["ok"])
        self.assertEqual(4, audit["packages"][0]["asset_reference_count"])
        self.assertTrue(any(row["source_type"] == "figure_block" and row["caption"] == "图 1 相图示意" for row in rows))
        unreadable = next(row for row in rows if row["page_idx"] == "2")
        self.assertEqual("figure_block", unreadable["source_type"])
        self.assertEqual("needs_visual_understanding", unreadable["visual_status"])
        self.assertIn("缺少可供文本模型读取", unreadable["visual_unreadable_reason"])
        table = next(row for row in rows if row["source_type"] == "table_block")
        self.assertIn("<table>", table["table_html"])
        self.assertIn("298", table["retrieval_text"])

    def test_task_index_install_requires_prepared_cache(self) -> None:
        from app.textbook_index_cache import install_textbook_index_cache, prepare_textbook_index_cache, textbook_index_cache_status

        with tempfile.TemporaryDirectory() as raw_tmp:
            tmp = Path(raw_tmp)
            package_zip = tmp / "测试教材_独立索引.zip"
            stage = tmp / "stage"
            make_mineru_package(package_zip)
            selected = [str(package_zip)]
            names = {str(package_zip.resolve()): "测试教材独立索引"}

            status = textbook_index_cache_status(selected, names)
            self.assertFalse(status["indexed"])
            with self.assertRaises(ValueError):
                install_textbook_index_cache(selected, stage, names)

            prepared = prepare_textbook_index_cache(selected, names)
            self.assertTrue(prepared["indexed"])
            installed = install_textbook_index_cache(selected, stage, names)
            self.assertTrue(Path(installed["blocks_csv"]).exists())
            self.assertTrue(Path(installed["page_map_csv"]).exists())

    def test_combined_selection_reuses_prepared_subset_caches(self) -> None:
        from unittest.mock import patch

        from app.textbook_index_cache import install_textbook_index_cache, prepare_textbook_index_cache, textbook_index_cache_status

        with tempfile.TemporaryDirectory() as raw_tmp:
            tmp = Path(raw_tmp)
            first = tmp / "材料现代分析测试方法1.zip"
            second = tmp / "材料现代分析测试方法2.zip"
            stage = tmp / "stage"
            cache = tmp / "cache" / "textbook_indexes"
            package_cache = tmp / "cache" / "textbook_packages"
            make_mineru_package(first)
            make_mineru_package(second)
            first_names = {str(first.resolve()): "材料现代分析测试方法"}
            second_names = {str(second.resolve()): "材料现代分析测试方法"}
            combined = [str(first), str(second)]
            combined_names = {**first_names, **second_names}

            with patch("app.textbook_index_cache.TEXTBOOK_INDEX_CACHE_DIR", cache), patch(
                "app.textbook_package.PACKAGE_CACHE_DIR", package_cache
            ):
                prepare_textbook_index_cache([str(first)], first_names)
                prepare_textbook_index_cache([str(second)], second_names)

                status = textbook_index_cache_status(combined, combined_names)
                self.assertTrue(status["indexed"])
                self.assertEqual(2, len(status["manifest"]))
                combined_status = json.loads((cache / status["cache_key"] / "textbook_index_status.json").read_text(encoding="utf-8"))
                self.assertEqual(2, len(combined_status["composed_from"]))

                installed = install_textbook_index_cache(combined, stage, combined_names)
                self.assertTrue(Path(installed["blocks_csv"]).exists())
                self.assertTrue(Path(installed["page_map_csv"]).exists())

    def test_text_model_candidate_payload_hides_asset_paths_and_marks_unreadable_visuals(self) -> None:
        from app.evidence_selection import _candidate_payload
        from app.retrieval import EvidenceCandidate

        candidate = EvidenceCandidate(
            evidence_id="ev1",
            question_id="q1",
            textbook="测试教材",
            citation_textbook="测试教材",
            chapter_section="",
            source_file="content_list.json",
            pdf_page_idx="2",
            printed_page="",
            score=8.0,
            evidence_text="",
            verified_page=False,
            source_type="figure_block",
            asset_path="/tmp/textbook/images/unreadable.jpg",
            visual_unreadable_reason="只有图片，没有文字化摘要。",
        )

        payload = _candidate_payload([candidate], include_visual_assets=False)

        self.assertEqual("", payload[0]["asset_path"])
        self.assertEqual(True, payload[0]["asset_available"])
        self.assertEqual(False, payload[0]["text_model_can_read_visual"])
        self.assertIn("只有图片", payload[0]["visual_warning"])

    def test_answer_prompt_reports_asset_availability_without_exposing_asset_path(self) -> None:
        from app.prompts import build_answer_draft_prompt

        messages = build_answer_draft_prompt(
            {"question_id": "q1", "stem": "说明图中相区含义。", "question_type": "简答题"},
            [
                {
                    "citation_textbook": "测试教材",
                    "source_type": "figure_block",
                    "evidence_text": "",
                    "asset_path": "/tmp/secret/textbook/images/figure.jpg",
                    "visual_unreadable_reason": "只有图片，没有文字化摘要。",
                }
            ],
        )
        user_text = messages[1]["content"]

        self.assertIsInstance(user_text, str)
        self.assertIn('"asset_available": true', user_text)
        self.assertIn('"text_model_can_read_visual": false', user_text)
        self.assertIn("只有图片，没有文字化摘要", user_text)
        self.assertNotIn("/tmp/secret", user_text)


if __name__ == "__main__":
    unittest.main()
