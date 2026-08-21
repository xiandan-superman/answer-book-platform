from __future__ import annotations

import hashlib

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
