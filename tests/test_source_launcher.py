from __future__ import annotations

import json
import zipfile

from scripts import source_launcher


def test_dependency_fingerprint_changes_with_requirements(tmp_path) -> None:
    (tmp_path / "requirements.txt").write_text("huey>=3\n", encoding="utf-8")
    first = source_launcher.dependency_fingerprint(tmp_path)
    (tmp_path / "requirements.txt").write_text("huey>=4\n", encoding="utf-8")
    assert source_launcher.dependency_fingerprint(tmp_path) != first


def test_pending_source_update_replaces_code_and_retains_backup(tmp_path) -> None:
    project = tmp_path / "program"
    (project / "scripts").mkdir(parents=True)
    (project / "scripts" / "start_platform.py").write_text("old", encoding="utf-8")
    (project / "app").mkdir()
    (project / "web").mkdir()
    data = tmp_path / "data"
    archive = data / "runtime" / "source.zip"
    archive.parent.mkdir(parents=True)
    with zipfile.ZipFile(archive, "w") as bundle:
        bundle.writestr("answer-book-platform/scripts/start_platform.py", "new")
        bundle.writestr("answer-book-platform/app/__init__.py", "")
        bundle.writestr("answer-book-platform/web/index.html", "new")
    plan = data / "runtime" / "pending-source-update.json"
    plan.write_text(json.dumps({"archive": str(archive), "version": "1.0.0"}), encoding="utf-8")

    assert source_launcher.apply_pending_source_update(project, data) is True
    assert (project / "scripts" / "start_platform.py").read_text(encoding="utf-8") == "new"
    backups = list((data / "runtime" / "source-backups").iterdir())
    assert len(backups) == 1
    assert (backups[0] / "scripts" / "start_platform.py").read_text(encoding="utf-8") == "old"
    assert not plan.exists()
