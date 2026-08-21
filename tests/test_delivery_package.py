from __future__ import annotations

import hashlib
import json
import zipfile

from app.delivery_package import STAGE_REPORTS, _verify_delivery_zip


def test_delivery_reports_include_checkpoint_reconciliation() -> None:
    assert "answer_checkpoint_reconciliation.json" in STAGE_REPORTS


def test_delivery_zip_verifier_accepts_matching_manifest(tmp_path) -> None:
    answer = b"valid-docx-placeholder-for-package-contract"
    entry = {
        "path": "answer_book.docx",
        "size_bytes": len(answer),
        "sha256": hashlib.sha256(answer).hexdigest(),
    }
    path = tmp_path / "delivery.zip"
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("answer_book.docx", answer)
        archive.writestr("manifest.json", json.dumps({"file_integrity": [entry]}))

    assert _verify_delivery_zip(path)["ok"] is True


def test_delivery_zip_verifier_rejects_hash_mismatch(tmp_path) -> None:
    path = tmp_path / "delivery.zip"
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("answer_book.docx", b"content")
        archive.writestr(
            "manifest.json",
            json.dumps({"file_integrity": [{
                "path": "answer_book.docx",
                "size_bytes": 7,
                "sha256": "0" * 64,
            }]}),
        )

    report = _verify_delivery_zip(path)

    assert report["ok"] is False
    assert any("哈希" in issue for issue in report["issues"])


def test_delivery_zip_verifier_requires_explicit_candidate_name(tmp_path) -> None:
    answer = b"review-candidate"
    entry = {
        "path": "answer_book_review_candidate.docx",
        "size_bytes": len(answer),
        "sha256": hashlib.sha256(answer).hexdigest(),
    }
    path = tmp_path / "candidate-delivery.zip"
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("answer_book_review_candidate.docx", answer)
        archive.writestr(
            "manifest.json",
            json.dumps({"delivery_tier": "review_candidate", "file_integrity": [entry]}),
        )

    assert _verify_delivery_zip(path)["ok"] is True
