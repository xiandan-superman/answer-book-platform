from __future__ import annotations

from importlib import metadata

from app import dependency_diagnostics


def test_dependency_report_is_non_blocking_and_reports_exact_drift(monkeypatch, tmp_path) -> None:
    (tmp_path / "requirements.txt").write_text("example>=1\n", encoding="utf-8")
    (tmp_path / "constraints-py311.txt").write_text("example==1.2\n", encoding="utf-8")
    (tmp_path / "constraints-source-macos-py311.txt").write_text("shell==2.0\n", encoding="utf-8")

    installed = {"example": "1.1", "shell": "2.0"}

    def fake_version(name: str) -> str:
        try:
            return installed[name]
        except KeyError as exc:
            raise metadata.PackageNotFoundError(name) from exc

    monkeypatch.setattr(dependency_diagnostics.metadata, "version", fake_version)
    report = dependency_diagnostics.dependency_version_report(
        tmp_path,
        (3, 11),
        platform_name="darwin",
    )

    assert report["profile"] == "py311-macos"
    assert report["non_blocking"] is True
    assert report["status"] == "drift"
    assert report["mismatch_count"] == 1
    assert report["mismatches"] == [{
        "package": "example",
        "installed": "1.1",
        "recommended": "1.2",
    }]


def test_dependency_report_marks_unlocked_python_profile_as_bounded(tmp_path) -> None:
    (tmp_path / "requirements.txt").write_text("example>=1\n", encoding="utf-8")

    report = dependency_diagnostics.dependency_version_report(
        tmp_path,
        (3, 10),
        platform_name="darwin",
    )

    assert report["profile"] == "py310-macos"
    assert report["status"] == "bounded"
    assert report["recommended_count"] == 0
