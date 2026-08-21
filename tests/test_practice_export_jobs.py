from __future__ import annotations

import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from app import practice_export_jobs
from app.practice_document_contracts import PRACTICE_DOCUMENT_CONTRACT_VERSION


def _sample_data() -> dict:
    return {
        "source_mode": "exam",
        "history_id": "practice_test",
        "exercises": [
            {
                "number": 1,
                "question_type": "简答题",
                "stem": "说明材料的基本性质。",
                "options": [],
                "formulas": [],
                "tables": [],
                "figures": [],
            }
        ],
    }


class PracticeExportJobTests(unittest.TestCase):
    def setUp(self) -> None:
        practice_export_jobs._JOBS.clear()
        practice_export_jobs._ACTIVE.clear()

    def _wait_for_terminal_job(self, job_id: str) -> dict:
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            job = practice_export_jobs.load_practice_export_job(job_id)
            if job["status"] not in {"queued", "running"}:
                return job
            time.sleep(0.01)
        self.fail("Word export job did not finish")

    def test_background_export_reports_progress_and_reuses_cache(self) -> None:
        with tempfile.TemporaryDirectory() as raw_tmp, patch.object(
            practice_export_jobs, "EXPORT_CACHE_DIR", Path(raw_tmp)
        ), patch.object(practice_export_jobs, "append_runtime_log"):
            created = practice_export_jobs.create_or_reuse_practice_export_job(
                _sample_data(), "测试题目.docx"
            )
            completed = self._wait_for_terminal_job(created["job_id"])
            cached = practice_export_jobs.create_or_reuse_practice_export_job(
                _sample_data(), "测试题目.docx"
            )

            self.assertEqual(PRACTICE_DOCUMENT_CONTRACT_VERSION, completed["document_contract_version"])

        self.assertEqual("completed", completed["status"])
        self.assertEqual(1, completed["completed_count"])
        self.assertGreater(completed["size_bytes"], 0)
        self.assertEqual("completed", cached["status"])
        self.assertTrue(cached["cached"])
        self.assertEqual(PRACTICE_DOCUMENT_CONTRACT_VERSION, cached["document_contract_version"])
        self.assertEqual(created["job_id"], cached["job_id"])

    def test_review_candidate_metadata_survives_background_generation_and_cache(self) -> None:
        data = _sample_data()
        data["semantic_review"] = {
            "status": "failed",
            "items": [{"number": 1, "status": "not_reviewed", "risks": []}],
        }
        with tempfile.TemporaryDirectory() as raw_tmp, patch.object(
            practice_export_jobs, "EXPORT_CACHE_DIR", Path(raw_tmp)
        ), patch.object(practice_export_jobs, "append_runtime_log"):
            created = practice_export_jobs.create_or_reuse_practice_export_job(data, "测试题目-待复核.docx")
            completed = self._wait_for_terminal_job(created["job_id"])
            cached = practice_export_jobs.create_or_reuse_practice_export_job(data, "测试题目-待复核.docx")

        self.assertEqual("review_candidate", completed["release_level"])
        self.assertTrue(completed["warning_issues"])
        self.assertEqual("review_candidate", cached["release_level"])
        self.assertEqual("测试题目-待复核.docx", cached["filename"])

    def test_post_generation_integrity_failure_blocks_and_is_not_cached(self) -> None:
        with tempfile.TemporaryDirectory() as raw_tmp, patch.object(
            practice_export_jobs, "EXPORT_CACHE_DIR", Path(raw_tmp)
        ), patch.object(practice_export_jobs, "append_runtime_log"), patch.object(
            practice_export_jobs,
            "validate_docx_output",
            return_value={"ok": False, "issues": ["题图没有嵌入 Word。"]},
        ):
            created = practice_export_jobs.create_or_reuse_practice_export_job(
                _sample_data(), "测试题目.docx"
            )
            failed = self._wait_for_terminal_job(created["job_id"])
            cached_files = list(Path(raw_tmp).glob("*.docx"))

        self.assertEqual("failed", failed["status"])
        self.assertIn("未通过完整性校验", failed["error"])
        self.assertEqual([], cached_files)

    def test_repeated_export_clicks_share_one_active_build(self) -> None:
        original_build = practice_export_jobs.build_practice_question_docx
        started = threading.Event()
        release = threading.Event()
        build_calls = 0

        def blocking_build(data, progress_callback=None):
            nonlocal build_calls
            build_calls += 1
            started.set()
            self.assertTrue(release.wait(2))
            return original_build(data, progress_callback=progress_callback)

        with tempfile.TemporaryDirectory() as raw_tmp, patch.object(
            practice_export_jobs, "EXPORT_CACHE_DIR", Path(raw_tmp)
        ), patch.object(practice_export_jobs, "append_runtime_log"), patch.object(
            practice_export_jobs, "build_practice_question_docx", blocking_build
        ):
            first = practice_export_jobs.create_or_reuse_practice_export_job(_sample_data(), "测试题目.docx")
            self.assertTrue(started.wait(2))
            repeated = practice_export_jobs.create_or_reuse_practice_export_job(_sample_data(), "测试题目.docx")
            release.set()
            completed = self._wait_for_terminal_job(first["job_id"])

        self.assertEqual(first["job_id"], repeated["job_id"])
        self.assertIn(repeated["status"], {"queued", "running"})
        self.assertEqual("completed", completed["status"])
        self.assertEqual(1, build_calls)

    def test_damaged_export_cache_is_rebuilt_instead_of_downloaded(self) -> None:
        with tempfile.TemporaryDirectory() as raw_tmp, patch.object(
            practice_export_jobs, "EXPORT_CACHE_DIR", Path(raw_tmp)
        ), patch.object(practice_export_jobs, "append_runtime_log"):
            first = practice_export_jobs.create_or_reuse_practice_export_job(_sample_data(), "测试题目.docx")
            completed = self._wait_for_terminal_job(first["job_id"])
            cache_path = next(Path(raw_tmp).glob("*.docx"))
            cache_path.write_bytes(b"not-a-docx")

            retried = practice_export_jobs.create_or_reuse_practice_export_job(_sample_data(), "测试题目.docx")
            rebuilt = self._wait_for_terminal_job(retried["job_id"])

        self.assertEqual("completed", completed["status"])
        self.assertEqual(first["job_id"], retried["job_id"])
        self.assertEqual("completed", rebuilt["status"])
        self.assertFalse(rebuilt["cached"])
        self.assertGreater(rebuilt["size_bytes"], len(b"not-a-docx"))

    def test_completed_export_survives_process_memory_reset(self) -> None:
        with tempfile.TemporaryDirectory() as raw_tmp, patch.object(
            practice_export_jobs, "EXPORT_CACHE_DIR", Path(raw_tmp)
        ), patch.object(practice_export_jobs, "append_runtime_log"):
            created = practice_export_jobs.create_or_reuse_practice_export_job(_sample_data(), "测试题目.docx")
            completed = self._wait_for_terminal_job(created["job_id"])
            practice_export_jobs._JOBS.clear()
            practice_export_jobs._ACTIVE.clear()

            restored = practice_export_jobs.load_practice_export_job(created["job_id"])
            download, filename = practice_export_jobs.practice_export_download(created["job_id"])
            download_exists = download.is_file()

        self.assertEqual("completed", completed["status"])
        self.assertEqual("completed", restored["status"])
        self.assertTrue(download_exists)
        self.assertEqual("测试题目.docx", filename)

    def test_interrupted_export_is_requeued_from_persisted_request(self) -> None:
        with tempfile.TemporaryDirectory() as raw_tmp, patch.object(
            practice_export_jobs, "EXPORT_CACHE_DIR", Path(raw_tmp)
        ), patch.object(practice_export_jobs, "append_runtime_log"):
            data = _sample_data()
            key = practice_export_jobs._cache_key(data)
            job_id = f"practice_word_{key[:24]}"
            record = {
                "job_id": job_id,
                "status": "running",
                "current_operation": "正在生成",
                "created_at": practice_export_jobs._now(),
                "updated_at": practice_export_jobs._now(),
                "completed_count": 0,
                "total_count": 1,
                "size_bytes": 0,
                "filename": "测试题目.docx",
                "cache_key": key,
                "cache_path": str(Path(raw_tmp) / f"{key}.docx"),
                "cached": False,
                "error": "",
                "warning_issues": [],
                "document_contract_version": PRACTICE_DOCUMENT_CONTRACT_VERSION,
                "payload": data,
            }
            practice_export_jobs._persist_job(record)
            recovery = practice_export_jobs.recover_practice_export_jobs()
            completed = self._wait_for_terminal_job(job_id)

        self.assertEqual(1, recovery["resumed"])
        self.assertEqual("completed", completed["status"])


if __name__ == "__main__":
    unittest.main()
