from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


class TextbookGroupingTests(unittest.TestCase):
    def test_scan_ignores_hidden_shared_library_manifest(self) -> None:
        from app.library_files import _scan_dir

        with tempfile.TemporaryDirectory() as raw_tmp:
            root = Path(raw_tmp)
            (root / ".shared_library_manifest.json").write_text("{}", encoding="utf-8")
            (root / "材料现代分析测试方法1.zip").write_bytes(b"textbook")
            files = _scan_dir(root, {".json", ".zip"})

        self.assertEqual(["材料现代分析测试方法1.zip"], [item["name"] for item in files])

    def test_marks_file_as_indexed_when_a_valid_cache_manifest_contains_it(self) -> None:
        from app.library_files import attach_textbook_index_statuses

        with tempfile.TemporaryDirectory() as raw_tmp:
            root = Path(raw_tmp)
            book = root / "材料科学基础.zip"
            book.write_bytes(b"textbook")
            stat = book.stat()
            cache = root / "textbook_indexes" / "cache_01"
            cache.mkdir(parents=True)
            (cache / "textbook_blocks.csv").write_text("block_id\nblock_01\n", encoding="utf-8")
            (cache / "textbook_page_map.csv").write_text("textbook\n材料科学基础.zip\n", encoding="utf-8")
            (cache / "textbook_index_status.json").write_text(json.dumps({"page_map_ok": True}), encoding="utf-8")
            (cache / "manifest.json").write_text(
                json.dumps({"files": [{"name": book.name, "size": stat.st_size, "mtime_ns": stat.st_mtime_ns}]}, ensure_ascii=False),
                encoding="utf-8",
            )
            file_info = {"name": book.name, "path": str(book)}
            with patch("app.library_files.TEXTBOOK_INDEX_CACHE_DIR", root / "textbook_indexes"):
                attach_textbook_index_statuses([file_info])

        self.assertTrue(file_info["index_status"]["indexed"])
        self.assertEqual(1, file_info["index_status"]["cache_count"])

    def test_groups_explicit_volume_names(self) -> None:
        from app.library_files import textbook_group_suggestions

        groups = textbook_group_suggestions(
            [
                {"name": "物理化学第6版上1.json", "path": "/books/physical-up-1.json"},
                {"name": "物理化学第6版上2.json", "path": "/books/physical-up-2.json"},
                {"name": "材料科学基础第2版.pdf", "path": "/books/material.pdf"},
            ]
        )

        self.assertEqual(1, len(groups))
        self.assertEqual("物理化学第6版", groups[0]["name"])
        self.assertEqual("high", groups[0]["confidence"])
        self.assertEqual(2, len(groups[0]["files"]))

    def test_groups_numeric_split_files_with_confirmation_confidence(self) -> None:
        from app.library_files import textbook_group_suggestions

        groups = textbook_group_suggestions(
            [
                {"name": "材料现代分析测试方法1.json", "path": "/books/analysis-1.json"},
                {"name": "材料现代分析测试方法2.json", "path": "/books/analysis-2.json"},
            ]
        )

        self.assertEqual(1, len(groups))
        self.assertEqual("材料现代分析测试方法", groups[0]["name"])
        self.assertEqual("medium", groups[0]["confidence"])

    def test_groups_contiguous_page_range_files_with_high_confidence(self) -> None:
        from app.library_files import textbook_group_suggestions

        groups = textbook_group_suggestions(
            [
                {"name": "材料科学基础课本第三版1-180.json", "path": "/books/material-1-180.json"},
                {"name": "材料科学基础课本第三版181-350.json", "path": "/books/material-181-350.json"},
            ]
        )

        self.assertEqual(1, len(groups))
        self.assertEqual("材料科学基础课本第三版", groups[0]["name"])
        self.assertEqual("high", groups[0]["confidence"])
        self.assertEqual("文件名包含连续页码范围分段", groups[0]["reason"])

    def test_shared_library_citation_name_overrides_filename_heuristics(self) -> None:
        from app.library_files import textbook_group_suggestions

        groups = textbook_group_suggestions(
            [
                {"name": "part-a.zip", "path": "/books/part-a.zip", "citation_textbook": "材料现代分析测试方法"},
                {"name": "part-b.zip", "path": "/books/part-b.zip", "citation_textbook": "材料现代分析测试方法"},
            ]
        )

        self.assertEqual(1, len(groups))
        self.assertEqual("材料现代分析测试方法", groups[0]["name"])
        self.assertEqual("共享教材包声明了相同的教材引用名称", groups[0]["reason"])

    def test_does_not_group_unrelated_title_numbers(self) -> None:
        from app.library_files import textbook_group_suggestions

        groups = textbook_group_suggestions(
            [
                {"name": "材料科学基础第2版.pdf", "path": "/books/material-v2.pdf"},
                {"name": "材料现代分析测试方法第2版.pdf", "path": "/books/analysis-v2.pdf"},
            ]
        )

        self.assertEqual([], groups)


if __name__ == "__main__":
    unittest.main()
