from __future__ import annotations

import hashlib
from types import SimpleNamespace

from app import update_manager
from scripts.build_update_manifest import build_manifest


def test_version_comparison_handles_beta_and_stable_versions() -> None:
    assert update_manager.is_newer_version("0.9.1-beta", "0.9.0-beta") is True
    assert update_manager.is_newer_version("0.9.0", "0.9.0-beta") is True
    assert update_manager.is_newer_version("0.9.0-beta", "0.9.0") is False
    assert update_manager.is_newer_version("0.9.0-beta", "0.9.0-beta") is False


def test_platform_asset_keys_are_stable() -> None:
    assert update_manager.platform_asset_key(system="Darwin", machine="arm64") == "macos-arm64"
    assert update_manager.platform_asset_key(system="Windows", machine="AMD64") == "windows-x86_64"


def test_update_status_selects_verified_platform_asset(monkeypatch) -> None:
    manifest = {
        "schema_version": update_manager.UPDATE_MANIFEST_SCHEMA,
        "version": "0.9.1-beta",
        "release_tag": "v0.9.1-beta",
        "notes": "quality improvements",
        "platforms": {
            "macos-arm64": {
                "asset_name": "answer-book-macos-arm64.zip",
                "size_bytes": 123,
                "sha256": "a" * 64,
            }
        },
    }

    monkeypatch.setattr(update_manager, "_github_json", lambda _url, **_kwargs: manifest)
    monkeypatch.setattr(update_manager, "get_app_version", lambda: "0.9.0-beta")
    monkeypatch.setattr(update_manager, "installation_kind", lambda: "desktop_app")
    monkeypatch.setattr(update_manager, "platform_asset_key", lambda **_kwargs: "macos-arm64")

    status = update_manager._build_update_status(
        {
            "enabled": True,
            "repository": "example/releases",
            "channel": "beta",
            "manifest_url": "https://raw.githubusercontent.com/example/releases/main/update-beta.json",
            "manifest_asset": "update-manifest.json",
        }
    )

    assert status["update_available"] is True
    assert status["action"] == "download_installer"
    assert status["asset"]["sha256"] == "a" * 64
    assert status["asset"]["download_url"].endswith("/v0.9.1-beta/answer-book-macos-arm64.zip")


def test_update_status_refuses_release_without_checksum(monkeypatch) -> None:
    manifest = {
        "schema_version": update_manager.UPDATE_MANIFEST_SCHEMA,
        "version": "0.9.1-beta",
        "release_tag": "v0.9.1-beta",
        "platforms": {"macos-arm64": {"asset_name": "app.zip"}},
    }
    monkeypatch.setattr(update_manager, "_github_json", lambda _url, **_kwargs: manifest)
    monkeypatch.setattr(update_manager, "get_app_version", lambda: "0.9.0-beta")
    monkeypatch.setattr(update_manager, "installation_kind", lambda: "desktop_app")
    monkeypatch.setattr(update_manager, "platform_asset_key", lambda **_kwargs: "macos-arm64")

    status = update_manager._build_update_status(
        {
            "enabled": True,
            "repository": "example/repo",
            "channel": "beta",
            "manifest_url": "https://raw.githubusercontent.com/example/repo/main/update-beta.json",
        }
    )

    assert status["update_available"] is False
    assert status["release_incomplete"] is True


def test_release_update_manifest_records_asset_size_and_checksum(tmp_path) -> None:
    asset = tmp_path / "answer-book-macos-arm64.zip"
    asset.write_bytes(b"verified-installer")

    manifest = build_manifest(
        version="0.9.1-beta",
        assets=[("macos-arm64", asset)],
        notes="内部体验版更新",
    )

    entry = manifest["platforms"]["macos-arm64"]
    assert entry["asset_name"] == asset.name
    assert entry["size_bytes"] == len(b"verified-installer")
    assert entry["sha256"] == hashlib.sha256(b"verified-installer").hexdigest()
    assert manifest["release_tag"] == "v0.9.1-beta"


def test_source_archive_update_uses_verified_source_asset(monkeypatch) -> None:
    manifest = {
        "schema_version": update_manager.UPDATE_MANIFEST_SCHEMA,
        "version": "0.9.2",
        "release_tag": "v0.9.2",
        "platforms": {
            "source": {
                "asset_name": "answer-book-source.zip",
                "size_bytes": 456,
                "sha256": "b" * 64,
                "dependency_fingerprint": "c" * 64,
            }
        },
    }
    monkeypatch.setattr(update_manager, "_github_json", lambda _url, **_kwargs: manifest)
    monkeypatch.setattr(update_manager, "get_app_version", lambda: "0.9.1")
    monkeypatch.setattr(update_manager, "installation_kind", lambda: "source_archive")
    monkeypatch.setattr(update_manager, "_current_dependency_fingerprint", lambda: "d" * 64)
    status = update_manager._build_update_status({
        "enabled": True,
        "repository": "example/releases",
        "channel": "stable",
        "manifest_url": "https://raw.githubusercontent.com/example/releases/main/update-stable.json",
    })
    assert status["action"] == "replace_source"
    assert status["asset"]["sha256"] == "b" * 64
    assert status["dependency_update_required"] is True


def test_source_archive_apply_stages_supervisor_plan(tmp_path, monkeypatch) -> None:
    archive = tmp_path / "source.zip"
    archive.write_bytes(b"verified")
    monkeypatch.setattr(update_manager, "DATA_ROOT", tmp_path / "data")
    monkeypatch.setattr(update_manager, "_download_asset", lambda _status: archive)
    monkeypatch.setattr(update_manager, "_schedule_supervised_restart", lambda: True)
    result = update_manager._stage_source_archive_update({
        "latest_version": "0.9.2",
        "dependency_update_required": True,
    })
    plan = update_manager._read_json(tmp_path / "data" / "runtime" / "pending-source-update.json")
    assert result["automatic_restart"] is True
    assert plan["archive"] == str(archive)
    assert plan["dependency_update_required"] is True


def test_git_checkout_updates_to_release_tag_not_unpublished_main(monkeypatch) -> None:
    calls: list[tuple[str, ...]] = []
    revisions = iter(["a" * 40, "b" * 40])

    def fake_git(*args, **_kwargs):
        calls.append(tuple(args))
        if args[:2] == ("status", "--porcelain"):
            return SimpleNamespace(stdout="")
        if args[:2] == ("branch", "--show-current"):
            return SimpleNamespace(stdout="main\n")
        if args[:2] == ("rev-parse", "HEAD"):
            return SimpleNamespace(stdout=next(revisions) + "\n")
        return SimpleNamespace(stdout="")

    monkeypatch.setattr(update_manager, "installation_kind", lambda: "source_checkout")
    monkeypatch.setattr(update_manager, "_git", fake_git)
    result = update_manager._pull_source_update(
        {"source_remote": "origin", "source_branch": "main"},
        "v0.9.2",
    )
    assert result["changed"] is True
    assert ("fetch", "--prune", "origin", "refs/tags/v0.9.2:refs/tags/v0.9.2") in calls
    assert ("merge", "--ff-only", "v0.9.2") in calls
    assert not any(call[:3] == ("fetch", "--prune", "origin") and call[-1] == "main" for call in calls)
