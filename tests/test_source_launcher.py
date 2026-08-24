from __future__ import annotations

import json
import zipfile

import pytest

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
    (data / "config").mkdir()
    (data / "config" / "api_keys.json").write_text("user api configuration", encoding="utf-8")
    (data / "textbooks").mkdir()
    (data / "textbooks" / "user-book.pdf").write_text("user textbook", encoding="utf-8")
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
    assert (data / "config" / "api_keys.json").read_text(encoding="utf-8") == "user api configuration"
    assert (data / "textbooks" / "user-book.pdf").read_text(encoding="utf-8") == "user textbook"
    assert not plan.exists()


def test_failed_source_replacement_restores_code_without_touching_user_data(monkeypatch, tmp_path) -> None:
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
    plan.write_text(json.dumps({"archive": str(archive), "version": "1.0.1"}), encoding="utf-8")
    (data / "config").mkdir()
    user_config = data / "config" / "api_keys.json"
    user_config.write_text("keep me", encoding="utf-8")

    def fail_copy(*args, **kwargs):
        raise OSError("simulated copy failure")

    monkeypatch.setattr(source_launcher.shutil, "copytree", fail_copy)
    with pytest.raises(OSError, match="simulated copy failure"):
        source_launcher.apply_pending_source_update(project, data)

    assert (project / "scripts" / "start_platform.py").read_text(encoding="utf-8") == "old"
    assert user_config.read_text(encoding="utf-8") == "keep me"
    assert plan.exists()


def test_delayed_server_readiness_still_opens_browser_once(monkeypatch) -> None:
    class RunningProcess:
        def poll(self):
            return None

    readiness = iter([False] * 125 + [True])
    opened = []
    monkeypatch.setattr(source_launcher, "server_ready", lambda _url: next(readiness))
    monkeypatch.setattr(source_launcher, "open_browser", lambda url: opened.append(url) or True)
    monkeypatch.setattr(source_launcher.time, "sleep", lambda _seconds: None)

    assert source_launcher.wait_until_ready_and_open(RunningProcess(), "http://127.0.0.1:8766") is True
    assert opened == ["http://127.0.0.1:8766"]


def test_browser_fallback_is_used_when_webbrowser_cannot_open(monkeypatch) -> None:
    calls = []
    monkeypatch.setattr(source_launcher.webbrowser, "open", lambda _url: False)
    monkeypatch.setattr(source_launcher.sys, "platform", "darwin")
    monkeypatch.setattr(source_launcher.subprocess, "Popen", lambda command, **_kwargs: calls.append(command))

    assert source_launcher.open_browser("http://127.0.0.1:8766") is True
    assert calls == [["open", "http://127.0.0.1:8766"]]
