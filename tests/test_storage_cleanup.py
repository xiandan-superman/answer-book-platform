from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


class StorageCleanupTests(unittest.TestCase):
    def _prepare(self, raw_tmp: str) -> tuple[Path, Path, Path]:
        root = Path(raw_tmp)
        cache = root / "cache"
        packages = cache / "textbook_packages"
        indexes = cache / "textbook_indexes"
        packages.mkdir(parents=True)
        indexes.mkdir(parents=True)

        # One extracted package whose source zip still exists in the library.
        library_zip = root / "library.zip"
        library_zip.write_bytes(b"zip")
        pkg = packages / "aaaaaaaaaaaaaaaaaaaaaaaa"
        pkg.mkdir()
        (pkg / ".extracted_from").write_text(str(library_zip), encoding="utf-8")
        (pkg / "content_list.json").write_text("[]", encoding="utf-8")

        # One index cache entry that no manifest references (deletable).
        from app.textbook_index import BLOCK_FIELDS, PAGE_MAP_FIELDS

        orphan = indexes / "bbbbbbbbbbbbbbbbbbbbbbbb"
        orphan.mkdir()
        # block_count=0 so an empty CSV validates; page map has no row count check.
        status_payload = json.dumps({"textbook_count": 1, "block_count": 0, "page_map_ok": True})
        (orphan / "textbook_blocks.csv").write_text(
            ",".join(BLOCK_FIELDS) + "\n", encoding="utf-8"
        )
        (orphan / "textbook_page_map.csv").write_text(
            ",".join(PAGE_MAP_FIELDS) + "\n", encoding="utf-8"
        )
        (orphan / "textbook_index_status.json").write_text(status_payload, encoding="utf-8")
        (orphan / "manifest.json").write_text(
            json.dumps({"key": "b" * 24, "files": [{"name": "missing.zip", "size": 1, "mtime_ns": 1}]}),
            encoding="utf-8",
        )

        # One in-use index entry whose file still exists with the same size.
        live_file = root / "live.zip"
        live_file.write_bytes(b"x" * 3)
        inuse = indexes / "cccccccccccccccccccccccc"
        inuse.mkdir()
        for name in ("textbook_blocks.csv", "textbook_page_map.csv", "textbook_index_status.json"):
            (inuse / name).write_bytes((orphan / name).read_bytes())
        (inuse / "manifest.json").write_text(
            json.dumps({"key": "c" * 24, "files": [{"name": "live.zip", "size": 3, "mtime_ns": 0}]}),
            encoding="utf-8",
        )
        return packages, indexes, inuse

    def test_overview_marks_packages_deletable_and_in_use_index_protected(self) -> None:
        from app import storage_cleanup

        with tempfile.TemporaryDirectory() as raw_tmp:
            packages, indexes, inuse = self._prepare(raw_tmp)
            fake_library = {"textbooks": []}

            def fake_scan():
                return dict(fake_library, exams=[])

            def fake_roots():
                return {
                    "textbooks": [
                        {"path": str(Path(raw_tmp) / "live.zip")},
                    ]
                }

            # Point module constants at the temp tree and neutralize library scan.
            with patch.object(storage_cleanup, "PACKAGE_CACHE_DIR", packages), \
                 patch.object(storage_cleanup, "TEXTBOOK_INDEX_CACHE_DIR", indexes), \
                 patch.object(storage_cleanup, "scan_library_files", side_effect=lambda: fake_roots() if False else {"exams": [], "textbooks": [{"path": str(Path(raw_tmp) / "live.zip")}]}):
                overview = storage_cleanup.storage_overview()
            areas = {area["kind"]: area for area in overview["areas"]}
            self.assertEqual(1, len(areas["textbook_packages"]["entries"]))
            self.assertTrue(areas["textbook_packages"]["entries"][0]["deletable"])
            by_key = {entry["key"]: entry for entry in areas["textbook_indexes"]["entries"]}
            self.assertFalse(by_key["b" * 24]["in_use"])
            self.assertTrue(by_key["b" * 24]["deletable"])
            self.assertTrue(by_key["c" * 24]["in_use"])
            self.assertFalse(by_key["c" * 24]["deletable"])

    def test_cleanup_removes_selected_and_skips_in_use(self) -> None:
        from app import storage_cleanup

        with tempfile.TemporaryDirectory() as raw_tmp:
            packages, indexes, inuse = self._prepare(raw_tmp)

            def fake_overview_factory(packages_dir, indexes_dir):
                def fake_overview():
                    return {
                        "ok": True,
                        "cleanable_bytes": 10,
                        "areas": [
                            {
                                "kind": "textbook_packages",
                                "label": "",
                                "total_bytes": 5,
                                "entries": [{
                                    "id": "aaaaaaaaaaaaaaaaaaaaaaaa",
                                    "title": "x",
                                    "size_bytes": 5,
                                    "deletable": True,
                                }],
                            },
                            {
                                "kind": "textbook_indexes",
                                "label": "",
                                "total_bytes": 5,
                                "entries": [
                                    {"key": "b" * 24, "size_bytes": 4, "deletable": True},
                                    {"key": "c" * 24, "size_bytes": 1, "deletable": False},
                                ],
                            },
                        ],
                    }
                return fake_overview

            with patch.object(storage_cleanup, "PACKAGE_CACHE_DIR", packages), \
                 patch.object(storage_cleanup, "TEXTBOOK_INDEX_CACHE_DIR", indexes), \
                 patch.object(storage_cleanup, "storage_overview", side_effect=fake_overview_factory(packages, indexes)):
                result = storage_cleanup.cleanup_storage(
                    "textbook_indexes",
                    ["b" * 24, "c" * 24],
                )

            self.assertEqual(["b" * 24], result["deleted"])
            self.assertEqual(1, len(result["skipped"]))
            self.assertFalse((indexes / ("b" * 24)).exists())
            self.assertTrue(inuse.exists())

    def test_unknown_kind_raises(self) -> None:
        from app import storage_cleanup

        with self.assertRaises(ValueError):
            storage_cleanup.cleanup_storage("nope")


if __name__ == "__main__":
    unittest.main()
