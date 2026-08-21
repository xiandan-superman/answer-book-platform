from __future__ import annotations

import csv
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tests.test_textbook_package import make_mineru_package


class SharedTextbookLibraryTests(unittest.TestCase):
    def test_publish_and_install_reuses_index_and_rebases_assets(self) -> None:
        from app.shared_textbook_library import (
            install_shared_textbook_package,
            publish_shared_textbook_library,
            shared_library_catalog,
            shared_library_package_path,
        )
        from app.library_files import scan_library_files
        from app.textbook_index_cache import prepare_textbook_index_cache, textbook_index_cache_status

        with tempfile.TemporaryDirectory() as raw_tmp:
            tmp = Path(raw_tmp)
            source = tmp / "材料分析.zip"
            make_mineru_package(source)
            host_cache = tmp / "host-cache" / "textbook_indexes"
            host_package_cache = tmp / "host-cache" / "textbook_packages"
            shared_root = tmp / "shared-library"
            citation_names = {str(source.resolve()): "材料分析"}
            with patch("app.textbook_index_cache.TEXTBOOK_INDEX_CACHE_DIR", host_cache), patch(
                "app.textbook_package.PACKAGE_CACHE_DIR", host_package_cache
            ), patch("app.shared_textbook_library.TEXTBOOK_INDEX_CACHE_DIR", host_cache):
                prepared = prepare_textbook_index_cache([str(source)], citation_names)
                self.assertTrue(prepared["indexed"])
                published = publish_shared_textbook_library(
                    [str(source)], citation_names, library_id="material-analysis", version="v1", root=shared_root
                )
                self.assertEqual("material-analysis", published["library_id"])
                self.assertEqual(1, len(shared_library_catalog(shared_root)["libraries"]))
                archive = shared_library_package_path("material-analysis", "v1", shared_root)

            client_textbooks = tmp / "client-textbooks"
            client_cache = tmp / "client-cache" / "textbook_indexes"
            client_package_cache = tmp / "client-cache" / "textbook_packages"
            with patch("app.textbook_index_cache.TEXTBOOK_INDEX_CACHE_DIR", client_cache), patch(
                "app.textbook_package.PACKAGE_CACHE_DIR", client_package_cache
            ), patch("app.shared_textbook_library.TEXTBOOK_INDEX_CACHE_DIR", client_cache), patch(
                "app.library_files.TEXTBOOKS_DIR", client_textbooks
            ), patch(
                "app.library_files.TEXTBOOK_INDEX_CACHE_DIR", client_cache
            ):
                installed = install_shared_textbook_package(
                    archive,
                    expected_sha256=published["package_sha256"],
                    textbooks_root=client_textbooks,
                    index_cache_root=client_cache,
                )
                self.assertTrue(installed["installed"])
                shared_file = next(item for item in scan_library_files()["textbooks"] if item["path"] in installed["selected_textbooks"])
                self.assertEqual("材料分析", shared_file["citation_textbook"])

                # Windows may round nanosecond timestamps while copying a ZIP.
                # The shipped cache must still be reused through its manifest.
                shared_path = Path(shared_file["path"])
                stat = shared_path.stat()
                os.utime(shared_path, ns=(stat.st_atime_ns, stat.st_mtime_ns + 100))
                status = textbook_index_cache_status([shared_file["path"]], {shared_file["path"]: shared_file["citation_textbook"]})
                self.assertTrue(status["indexed"])
                refreshed = next(item for item in scan_library_files()["textbooks"] if item["path"] == shared_file["path"])
                self.assertTrue(refreshed["index_status"]["indexed"])
                with Path(status["blocks_csv"]).open(encoding="utf-8-sig", newline="") as f:
                    rows = list(csv.DictReader(f))
                asset_rows = [row for row in rows if row.get("asset_path")]
                self.assertTrue(asset_rows)
                self.assertTrue(all(str(client_package_cache) in row["asset_path"] for row in asset_rows))

                # A stale manifest alone must not suppress installation. This
                # mirrors an interrupted download or a manually removed ZIP.
                Path(installed["selected_textbooks"][0]).unlink()
                repaired = install_shared_textbook_package(
                    archive,
                    expected_sha256=published["package_sha256"],
                    textbooks_root=client_textbooks,
                    index_cache_root=client_cache,
                )
                self.assertTrue(repaired["installed"])
                self.assertIn("修复不完整", repaired["message"])
                self.assertTrue(all(Path(path).is_file() for path in repaired["selected_textbooks"]))

                installed_manifest = json.loads(
                    (client_textbooks / "shared" / "material-analysis" / "v1" / ".shared_library_manifest.json").read_text(encoding="utf-8")
                )
                self.assertEqual(prepared["cache_key"], installed_manifest["cache_key"])
                self.assertEqual([source.name], installed_manifest["source_file_names"])

    def test_settings_rejects_plain_http_for_remote_hosts(self) -> None:
        from app.shared_textbook_library import save_shared_library_settings

        with tempfile.TemporaryDirectory() as raw_tmp:
            with self.assertRaises(ValueError):
                save_shared_library_settings("http://textbook-host.example", Path(raw_tmp))
            saved = save_shared_library_settings("https://textbook-host.example/", Path(raw_tmp))
            self.assertEqual("https://textbook-host.example", saved["remote_url"])

    def test_shared_install_manifest_supplies_citation_name_when_status_has_no_frontend_mapping(self) -> None:
        from app.textbook_index_cache import _normalized_citation_names

        with tempfile.TemporaryDirectory() as raw_tmp:
            root = Path(raw_tmp)
            textbook = root / "教材一.zip"
            textbook.write_bytes(b"test")
            (root / ".shared_library_manifest.json").write_text(
                json.dumps({"citation_names_by_file": {"教材一.zip": "材料分析"}}, ensure_ascii=False),
                encoding="utf-8",
            )

            names = _normalized_citation_names([textbook], None)

        self.assertEqual("材料分析", names[str(textbook.resolve())])

    def test_shared_cache_composition_tolerates_windows_timestamp_rounding(self) -> None:
        from app.textbook_index import BLOCK_FIELDS, PAGE_MAP_FIELDS
        from app.textbook_index_cache import _find_composable_cache_roots

        def write_cache(root: Path, rows: list[dict[str, object]]) -> None:
            root.mkdir(parents=True)
            (root / "textbook_blocks.csv").write_text(",".join(BLOCK_FIELDS) + "\n", encoding="utf-8")
            (root / "textbook_page_map.csv").write_text(",".join(PAGE_MAP_FIELDS) + "\n", encoding="utf-8")
            (root / "textbook_index_status.json").write_text(
                json.dumps({"textbook_count": len(rows), "block_count": 0, "page_map_ok": True}),
                encoding="utf-8",
            )
            (root / "manifest.json").write_text(json.dumps({"files": rows}, ensure_ascii=False), encoding="utf-8")

        with tempfile.TemporaryDirectory() as raw_tmp:
            cache_root = Path(raw_tmp) / "indexes"
            source_rows = [
                {"name": "教材甲.zip", "size": 100, "mtime_ns": 1001, "citation_textbook": "教材甲"},
                {"name": "教材乙.zip", "size": 200, "mtime_ns": 2001, "citation_textbook": "教材乙"},
            ]
            write_cache(cache_root / "a", source_rows[:1])
            write_cache(cache_root / "b", source_rows[1:])
            rounded_rows = [
                {**source_rows[0], "mtime_ns": 1000},
                {**source_rows[1], "mtime_ns": 2000},
            ]
            with patch("app.textbook_index_cache.TEXTBOOK_INDEX_CACHE_DIR", cache_root):
                self.assertIsNone(_find_composable_cache_roots(rounded_rows))
                matched = _find_composable_cache_roots(rounded_rows, allow_shared_timestamp_fallback=True)

        self.assertEqual(["a", "b"], [path.name for path in matched or []])

    def test_republishing_replaces_prior_release(self) -> None:
        from app.shared_textbook_library import publish_shared_textbook_library, shared_library_catalog
        from app.textbook_index_cache import prepare_textbook_index_cache

        with tempfile.TemporaryDirectory() as raw_tmp:
            tmp = Path(raw_tmp)
            source = tmp / "材料分析.zip"
            make_mineru_package(source)
            cache_root = tmp / "cache" / "textbook_indexes"
            package_cache = tmp / "cache" / "textbook_packages"
            library_root = tmp / "shared-library"
            citation_names = {str(source.resolve()): "材料分析"}
            with patch("app.textbook_index_cache.TEXTBOOK_INDEX_CACHE_DIR", cache_root), patch(
                "app.textbook_package.PACKAGE_CACHE_DIR", package_cache
            ), patch("app.shared_textbook_library.TEXTBOOK_INDEX_CACHE_DIR", cache_root):
                prepare_textbook_index_cache([str(source)], citation_names)
                publish_shared_textbook_library(
                    [str(source)], citation_names, library_id="material-analysis", version="v1", root=library_root
                )
                second = publish_shared_textbook_library(
                    [str(source)], citation_names, library_id="material-analysis", version="v2", root=library_root
                )

            self.assertEqual(["v1"], second["removed_versions"])
            self.assertFalse((library_root / "published" / "material-analysis" / "v1.zip").exists())
            self.assertTrue((library_root / "published" / "material-analysis" / "v2.zip").exists())
            catalog = shared_library_catalog(library_root)
            self.assertEqual(["v2"], [item["version"] for item in catalog["libraries"][0]["versions"]])


if __name__ == "__main__":
    unittest.main()
