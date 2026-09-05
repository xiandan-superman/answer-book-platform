from __future__ import annotations

import json
import sys
import zipfile

import pytest

from app.dependency_profiles import SUPPORTED_RUNTIME_PYTHON
from scripts import source_launcher


def test_python_311_uses_a_separate_managed_runtime() -> None:
    assert SUPPORTED_RUNTIME_PYTHON == (3, 11)
    assert source_launcher.RUNTIME_ENV_NAME == "python-env-py311"


def test_incompatible_managed_runtime_is_preserved_before_rebuild(tmp_path) -> None:
    env_dir = tmp_path / "runtime" / source_launcher.RUNTIME_ENV_NAME
    env_dir.mkdir(parents=True)
    (env_dir / "marker.txt").write_text("Python 3.14 environment", encoding="utf-8")

    quarantined = source_launcher.quarantine_incompatible_runtime(env_dir)

    assert not env_dir.exists()
    assert quarantined.name.startswith("python-env-py311-incompatible-")
    assert (quarantined / "marker.txt").read_text(encoding="utf-8") == "Python 3.14 environment"


def test_missing_runtime_pip_is_repaired(monkeypatch, tmp_path) -> None:
    python = tmp_path / "python.exe"
    python.write_text("", encoding="utf-8")
    commands: list[list[str]] = []

    class Result:
        def __init__(self, returncode: int) -> None:
            self.returncode = returncode

    results = iter((Result(1), Result(0), Result(0)))

    def fake_run(command, **_kwargs):
        commands.append(command)
        return next(results)

    monkeypatch.setattr(source_launcher.subprocess, "run", fake_run)
    source_launcher.ensure_runtime_pip(python)

    assert commands == [
        [str(python), "-m", "pip", "--version"],
        [str(python), "-m", "ensurepip", "--upgrade"],
        [str(python), "-m", "pip", "--version"],
    ]


def test_source_launch_entries_require_python_311_exactly() -> None:
    root = source_launcher.ROOT
    mac = (root / "start_platform.command").read_text(encoding="utf-8")
    lan = (root / "start_platform_lan.command").read_text(encoding="utf-8")
    windows = (root / "start_platform_windows.bat").read_text(encoding="utf-8")

    expected = "sys.version_info[:2] == (3, 11)"
    assert expected in mac
    assert expected in lan
    assert windows.count(expected) == 3
    assert "sys.version_info >= (3, 11)" not in mac + lan + windows


def test_dependency_fingerprint_changes_with_requirements(tmp_path) -> None:
    (tmp_path / "requirements.txt").write_text("huey>=3\n", encoding="utf-8")
    first = source_launcher.dependency_fingerprint(tmp_path)
    (tmp_path / "requirements.txt").write_text("huey>=4\n", encoding="utf-8")
    assert source_launcher.dependency_fingerprint(tmp_path) != first


def test_pending_dependencies_reports_unmet_direct_requirements(tmp_path) -> None:
    (tmp_path / "requirements.txt").write_text(
        "answer-book-missing-test-package>=999\nignored-on-this-platform>=1; sys_platform == 'never'\n",
        encoding="utf-8",
    )

    assert source_launcher.pending_dependencies(type(tmp_path)(sys.executable), tmp_path) == [
        "answer-book-missing-test-package"
    ]


def test_pip_progress_identifies_component_without_exposing_download_url() -> None:
    assert source_launcher.pip_component_from_line("Collecting Pillow>=10") == "Pillow"
    assert source_launcher.pip_component_from_line("Building wheel for bm25s (pyproject.toml)") == "bm25s"
    assert source_launcher.pip_component_from_line("Downloading https://user:secret@example.test/package.whl") == ""


def test_dependency_failure_details_are_actionable_and_sanitized() -> None:
    message, hint = source_launcher.dependency_failure_details(
        ["HTTPSConnectionPool: Read timed out while downloading https://user:secret@example.test/file"],
        "Pillow",
    )

    assert message == "下载 Pillow 时网络连接中断。"
    assert "重新尝试" in hint
    assert "secret" not in message + hint


def test_dependency_install_streams_current_component_and_count(tmp_path) -> None:
    events = []
    command = [
        sys.executable,
        "-c",
        "print('Collecting Pillow>=10', flush=True); print('Installing collected packages: Pillow', flush=True)",
    ]

    returncode, lines, component = source_launcher.run_dependency_install(
        command,
        project_root=tmp_path,
        pending=["Pillow"],
        progress=lambda status, percent, message, **details: events.append((status, percent, message, details)),
    )

    assert returncode == 0
    assert component == "Pillow"
    assert lines[-1] == "Installing collected packages: Pillow"
    assert any(event[0] == "downloading_dependencies" and event[3]["current_index"] == 1 for event in events)
    assert any(event[0] == "installing_dependencies" for event in events)


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
        bundle.writestr("answer-book-platform/start_platform_windows.bat", "new launcher")
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


def test_pending_source_update_reports_offline_install_phases(tmp_path) -> None:
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
        bundle.writestr("answer-book-platform/start_platform_windows.bat", "launcher")
    plan = data / "runtime" / "pending-source-update.json"
    plan.write_text(json.dumps({"archive": str(archive), "version": "1.0.3"}), encoding="utf-8")
    events = []

    source_launcher.apply_pending_source_update(
        project,
        data,
        lambda status, percent, message, **details: events.append((status, percent, message, details)),
    )

    phases = []
    for event in events:
        if not phases or phases[-1] != event[0]:
            phases.append(event[0])
    assert phases == ["extracting", "backing_up", "installing", "verifying_install"]
    assert events[-1][1] == 98


def test_failed_update_plan_is_quarantined_instead_of_retried_forever(tmp_path) -> None:
    runtime = tmp_path / "runtime"
    runtime.mkdir()
    plan = runtime / "pending-source-update.json"
    plan.write_text("{}", encoding="utf-8")

    failed = source_launcher.quarantine_failed_update(tmp_path)

    assert failed is not None and failed.is_file()
    assert failed.name.startswith("failed-source-update-")
    assert not plan.exists()


def test_restore_update_backup_only_uses_valid_backup_root(tmp_path) -> None:
    project = tmp_path / "program"
    (project / "scripts").mkdir(parents=True)
    (project / "scripts" / "start_platform.py").write_text("new", encoding="utf-8")
    data = tmp_path / "data"
    backup = data / "runtime" / "source-backups" / "0.9.19-test"
    (backup / "scripts").mkdir(parents=True)
    (backup / "scripts" / "start_platform.py").write_text("old", encoding="utf-8")
    recovery = data / "runtime" / "source-update-recovery.json"
    recovery.write_text(json.dumps({"backup": str(backup)}), encoding="utf-8")

    assert source_launcher.restore_update_backup(project, data) is True
    assert (project / "scripts" / "start_platform.py").read_text(encoding="utf-8") == "old"


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
        bundle.writestr("answer-book-platform/start_platform_windows.bat", "new launcher")
    plan = data / "runtime" / "pending-source-update.json"
    plan.write_text(json.dumps({"archive": str(archive), "version": "1.0.1"}), encoding="utf-8")
    (data / "config").mkdir()
    user_config = data / "config" / "api_keys.json"
    user_config.write_text("keep me", encoding="utf-8")

    original_copytree = source_launcher.shutil.copytree

    def fail_copy(source, destination, *args, **kwargs):
        source_path = source if isinstance(source, type(project)) else type(project)(source)
        if kwargs.get("dirs_exist_ok") and source_path.name.startswith(".program-update-"):
            (project / "scripts" / "start_platform.py").write_text("partial", encoding="utf-8")
            raise OSError("simulated copy failure")
        return original_copytree(source, destination, *args, **kwargs)

    monkeypatch.setattr(source_launcher.shutil, "copytree", fail_copy)
    with pytest.raises(OSError, match="simulated copy failure"):
        source_launcher.apply_pending_source_update(project, data)

    assert (project / "scripts" / "start_platform.py").read_text(encoding="utf-8") == "old"
    assert user_config.read_text(encoding="utf-8") == "keep me"
    assert plan.exists()


def test_source_update_prepares_new_tree_before_copying_live_source(monkeypatch, tmp_path) -> None:
    project = tmp_path / "program"
    (project / "scripts").mkdir(parents=True)
    (project / "scripts" / "start_platform.py").write_text("old", encoding="utf-8")
    (project / "app").mkdir()
    (project / "web").mkdir()
    (project / "start_platform_windows.bat").write_text("old launcher", encoding="utf-8")
    data = tmp_path / "data"
    archive = data / "runtime" / "source.zip"
    archive.parent.mkdir(parents=True)
    with zipfile.ZipFile(archive, "w") as bundle:
        bundle.writestr("answer-book-platform/scripts/start_platform.py", "new")
        bundle.writestr("answer-book-platform/app/__init__.py", "")
        bundle.writestr("answer-book-platform/web/index.html", "new")
        bundle.writestr("answer-book-platform/start_platform_windows.bat", "new launcher")
    plan = data / "runtime" / "pending-source-update.json"
    plan.write_text(json.dumps({"archive": str(archive), "version": "1.0.2"}), encoding="utf-8")
    original_copytree = source_launcher.shutil.copytree
    observations: list[tuple[str, str]] = []

    def observe_copy(source, destination, *args, **kwargs):
        observations.append((type(project)(source).name, (project / "scripts" / "start_platform.py").read_text(encoding="utf-8")))
        return original_copytree(source, destination, *args, **kwargs)

    monkeypatch.setattr(source_launcher.shutil, "copytree", observe_copy)

    assert source_launcher.apply_pending_source_update(project, data) is True
    assert observations[0][1] == "old"
    assert observations[1][1] == "old"
    assert (project / "scripts" / "start_platform.py").read_text(encoding="utf-8") == "new"
    assert not plan.exists()


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
