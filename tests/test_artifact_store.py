from __future__ import annotations

from pathlib import Path

from PIL import Image

from app.artifact_store import build_artifact_integrity_report
from app.image_artifacts import ImageArtifactStore, mark_final_adopted_assets


def _image(path: Path, color: str = "white") -> None:
    Image.new("RGB", (96, 80), color).save(path, format="PNG")


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
