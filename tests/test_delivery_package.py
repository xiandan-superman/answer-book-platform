from __future__ import annotations

import hashlib
import json
import zipfile

import pytest

from app import delivery_package
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


def test_failed_package_rebuild_preserves_previous_zip_and_cleans_temporary_file(
    tmp_path, monkeypatch
) -> None:
    stage = tmp_path / "stage"
    output = tmp_path / "output"
    delivery = output / "delivery"
    stage.mkdir()
    delivery.mkdir(parents=True)
    (output / "answer_book.docx").write_bytes(b"approved")
    previous = delivery / "task_delivery.zip"
    previous.write_bytes(b"previous-valid-package")
    final = {
        "status": "passed",
        "delivery_tier": "formal",
        "formal_acceptance_passed": True,
        "warning_count": 0,
        "warnings": [],
        "diagnostic_advisories": {},
    }
    monkeypatch.setattr(delivery_package, "build_final_acceptance_report", lambda *_args, **_kwargs: final)
    monkeypatch.setattr(delivery_package, "build_model_usage_report", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        delivery_package,
        "_verify_delivery_zip",
        lambda _path: (_ for _ in ()).throw(RuntimeError("verification interrupted")),
    )

    with pytest.raises(RuntimeError, match="verification interrupted"):
        delivery_package.build_task_delivery_package("task", stage, output)

    assert previous.read_bytes() == b"previous-valid-package"
    assert list(delivery.glob(".*.zip.tmp")) == []
