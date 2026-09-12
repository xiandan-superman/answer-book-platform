from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import patch

from PIL import Image

from app.artifact_store import atomic_write_json, build_artifact_integrity_report, long_path, path_exists, read_text
from app.image_artifacts import ImageArtifactStore, mark_final_adopted_assets


def _image(path: Path, color: str = "white") -> None:
    Image.new("RGB", (96, 80), color).save(path, format="PNG")


def test_directory_fsync_failure_is_nonfatal_and_descriptor_is_closed(tmp_path) -> None:
    from app.artifact_store import fsync_directory_best_effort

    with patch("app.artifact_store.os.open", return_value=123), patch(
        "app.artifact_store.os.fsync", side_effect=OSError(9, "Bad file descriptor")
    ), patch("app.artifact_store.os.close") as close:
        fsync_directory_best_effort(tmp_path)

    close.assert_called_once_with(123)


def test_windows_long_path_prefix_is_added_only_when_needed() -> None:
    short = "/tmp/short.json"
    deep = "/tmp/" + ("x" * 260)
    absolute_short = os.path.abspath(short)
    absolute_deep = os.path.abspath(deep)

    with patch("app.artifact_store.os.name", "nt"):
        assert long_path(short) == absolute_short
        assert long_path(deep) == "\\\\?\\" + absolute_deep


def test_long_path_read_helpers_use_normal_paths_on_posix(tmp_path: Path) -> None:
    target = tmp_path / "nested" / "value.json"
    target.parent.mkdir()
    target.write_text("{\"ok\": true}", encoding="utf-8")
    assert path_exists(target)
    assert read_text(target) == '{"ok": true}'


def test_atomic_json_write_uses_short_temp_leaf_and_adapts_low_level_paths(
    tmp_path, monkeypatch
) -> None:
    import app.artifact_store as artifact_store

    parent = tmp_path
    while len(str(parent)) < 180:
        parent /= "deep-checkpoint"
    target = parent / (("a" * 64) + ".json")
    legacy_temp = target.parent / f".{target.name}-12345678"
    short_temp = target.parent / ".tmp-12345678"
    assert len(str(target)) > 240
    assert len(str(legacy_temp)) >= 260
    assert len(str(short_temp)) < 240
    prefixes: list[str] = []
    adapted: list[str] = []
    real_mkstemp = artifact_store.tempfile.mkstemp
    real_long_path = artifact_store.long_path

    def recording_mkstemp(*, prefix: str, dir: str):
        prefixes.append(prefix)
        return real_mkstemp(prefix=prefix, dir=dir)

    def recording_long_path(value: Path | str) -> str:
        adapted.append(str(value))
        return real_long_path(value)

    monkeypatch.setattr(artifact_store.tempfile, "mkstemp", recording_mkstemp)
    monkeypatch.setattr(artifact_store, "long_path", recording_long_path)

    atomic_write_json(target, {"ok": True})

    assert prefixes == [".tmp-"]
    assert str(target.parent) in adapted
    assert str(target) in adapted
    assert read_text(target).strip().startswith("{")


def test_image_artifact_is_verified_on_write_read_and_adoption(tmp_path) -> None:
    source = tmp_path / "source.png"
    _image(source)
    store = ImageArtifactStore(tmp_path / "store")
    artifact = store.register(source, provider="p", model="m", source_call_id="call")

    assert artifact.size_bytes > 0
    assert store.get(artifact.asset_id) is not None
    store.mark_adopted([artifact.asset_id])
    assert store.get(artifact.asset_id).adopted is True  # type: ignore[union-attr]

    Path(artifact.path).write_bytes(b"corrupted")
    assert store.get(artifact.asset_id) is None


def test_artifact_report_validates_image_and_diagnostic_digests(tmp_path) -> None:
    data_root = tmp_path / "data"
    source = tmp_path / "source.png"
    _image(source)
    store = ImageArtifactStore(data_root / "task" / "agent_images")
    artifact = store.register(source, provider="p", model="m", source_call_id="call")
    diagnostics = tmp_path / "diagnostics"
    attachment_dir = diagnostics / "task" / "attachments"
    attachment_dir.mkdir(parents=True)
    (attachment_dir / ("0" * 64 + ".bin")).write_bytes(b"not-the-declared-digest")

    report = build_artifact_integrity_report(data_root=data_root, diagnostics_root=diagnostics)

    assert report["image_artifact_count"] == 1
    assert report["finding_counts"]["diagnostic_attachment_digest_mismatch"] == 1
    Path(artifact.path).write_bytes(b"bad")
    report = build_artifact_integrity_report(data_root=data_root, diagnostics_root=diagnostics)
    assert report["finding_counts"]["image_artifact_digest_mismatch_or_missing"] == 1


def test_final_delivery_marks_only_selected_quality_gated_assets(tmp_path) -> None:
    data_root = tmp_path / "data"
    source = tmp_path / "source.png"
    _image(source)
    store = ImageArtifactStore(data_root / "task" / "agent_images")
    selected = store.register(source, provider="p", model="m", source_call_id="selected")
    other_source = tmp_path / "other.png"
    _image(other_source, "black")
    other = store.register(other_source, provider="p", model="m", source_call_id="other")
    payload = {
        "fragments": [
            {
                "generated_images": [{"asset_id": selected.asset_id}],
                "_meta": {"image_tool_loop": {"generated_artifacts": [selected.to_dict(), other.to_dict()]}},
            }
        ]
    }

    report = mark_final_adopted_assets(payload, data_root=data_root)

    assert report == {"final_adopted_count": 1, "unresolved_selected_asset_count": 0}
    assert ImageArtifactStore(store.root).get(selected.asset_id).adopted is True  # type: ignore[union-attr]
    assert ImageArtifactStore(store.root).get(other.asset_id).adopted is False  # type: ignore[union-attr]


def test_final_practice_record_marks_selected_nonfailed_asset(tmp_path) -> None:
    data_root = tmp_path / "data"
    source = tmp_path / "source.png"
    _image(source)
    store = ImageArtifactStore(data_root / "practice" / "agent_images")
    artifact = store.register(source, provider="p", model="m", source_call_id="call")
    payload = {
        "_image_tool_artifacts": [artifact.to_dict()],
        "exercises": [
            {
                "generation_status": "completed",
                "generated_images": [{"asset_id": artifact.asset_id}],
                "figures": [{"asset_id": artifact.asset_id}],
            }
        ],
    }

    report = mark_final_adopted_assets(payload, data_root=data_root)

    assert report["final_adopted_count"] == 1
    assert ImageArtifactStore(store.root).get(artifact.asset_id).adopted is True  # type: ignore[union-attr]
