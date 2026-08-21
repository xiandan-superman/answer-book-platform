from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from app import practice_jobs


class PracticeJobCleanupTests(unittest.TestCase):
    def test_completed_job_record_is_removed(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            job_id = "generation_20260801210000_deadbeef"
            path = root / f"{job_id}.json"
            path.write_text(json.dumps({"job_id": job_id, "status": "failed"}), encoding="utf-8")
            with patch.object(practice_jobs, "PRACTICE_JOB_DIR", root):
                result = practice_jobs.delete_practice_job(job_id)
            self.assertTrue(result["ok"])
            self.assertFalse(path.exists())
            self.assertGreater(result["removed_bytes"], 0)

    def test_running_job_is_never_force_deleted(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            job_id = "generation_20260801210000_feedface"
            path = root / f"{job_id}.json"
            path.write_text(json.dumps({"job_id": job_id, "status": "running"}), encoding="utf-8")
            with patch.object(practice_jobs, "PRACTICE_JOB_DIR", root):
                result = practice_jobs.delete_practice_job(job_id)
            self.assertFalse(result["ok"])
            self.assertTrue(path.exists())

    def test_history_cleanup_only_removes_linked_job_records(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            linked = root / "generation_20260801210000_11111111.json"
            other = root / "generation_20260801210000_22222222.json"
            linked.write_text(json.dumps({"history_id": "practice_20260801210000_aaaaaaaa"}), encoding="utf-8")
            other.write_text(json.dumps({"history_id": "practice_20260801210000_bbbbbbbb"}), encoding="utf-8")
            with patch.object(practice_jobs, "PRACTICE_JOB_DIR", root):
                result = practice_jobs.delete_jobs_for_history("practice_20260801210000_aaaaaaaa")
            self.assertEqual(result["removed_job_records"], 1)
            self.assertFalse(linked.exists())
            self.assertTrue(other.exists())

    def test_cleanup_removes_only_expired_terminal_jobs(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            now = datetime.now(timezone.utc)
            old = (now - timedelta(days=3)).isoformat()
            recent = (now - timedelta(hours=1)).isoformat()
            (root / "generation_20260801210000_11111111.json").write_text(
                json.dumps({"job_id": "generation_20260801210000_11111111", "status": "completed", "history_id": "practice_old", "updated_at": old}),
                encoding="utf-8",
            )
            (root / "generation_20260801210000_22222222.json").write_text(
                json.dumps({"job_id": "generation_20260801210000_22222222", "status": "failed", "updated_at": recent}),
                encoding="utf-8",
            )
            with patch.object(practice_jobs, "PRACTICE_JOB_DIR", root), patch.object(practice_jobs, "_COMPLETED_HISTORY_RETENTION_DAYS", 1):
                result = practice_jobs.cleanup_practice_jobs(now=now)
            self.assertEqual(result["removed_count"], 1)
            self.assertFalse((root / "generation_20260801210000_11111111.json").exists())
            self.assertTrue((root / "generation_20260801210000_22222222.json").exists())


if __name__ == "__main__":
    unittest.main()
