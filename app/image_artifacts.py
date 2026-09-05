from __future__ import annotations

import base64
import hashlib
import json
import os
import shutil
import tempfile
from dataclasses import asdict, dataclass, replace
from io import BytesIO
from pathlib import Path
from typing import Any

from PIL import Image

from .artifact_store import atomic_write_json, fsync_directory_best_effort, verify_immutable_file
from .paths import DATA_ROOT

_MIME_BY_FORMAT = {
    "PNG": "image/png",
    "JPEG": "image/jpeg",
    "WEBP": "image/webp",
}


@dataclass(frozen=True)
class ImageArtifact:
    asset_id: str
    sha256: str
    mime_type: str
    width: int
    height: int
    path: str
    provider: str
    model: str
    source_call_id: str
    size_bytes: int = 0
    source_kind: str = "model_generated_image"
    adopted: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class ImageArtifactStore:
    """Task-local, content-addressed storage for model-generated images."""

    def __init__(self, root: Path, *, max_bytes: int = 25 * 1024 * 1024) -> None:
        self.root = Path(root)
        self.max_bytes = int(max_bytes)
        self.root.mkdir(parents=True, exist_ok=True)
        self._artifacts: dict[str, ImageArtifact] = {}
        self._load_manifest()

    @property
    def manifest_path(self) -> Path:
        return self.root / "manifest.json"

    def _load_manifest(self) -> None:
        if not self.manifest_path.exists():
            return
        try:
            payload = json.loads(self.manifest_path.read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError):
            return
        for item in payload.get("artifacts", []) if isinstance(payload, dict) else []:
            if not isinstance(item, dict):
                continue
            try:
                normalized = dict(item)
                normalized.setdefault("size_bytes", 0)
                normalized.setdefault("source_kind", "model_generated_image")
                normalized.setdefault("adopted", False)
                artifact = ImageArtifact(**normalized)
            except TypeError:
                continue
            path = Path(artifact.path)
            try:
                path.resolve().relative_to(self.root.resolve())
            except (OSError, ValueError):
                # A task directory may have been restored to a new user-data
                # location.  Accept only the same content-addressed filename
                # inside the current store, never the stale external path.
                path = self.root / path.name
            if (
                path.exists()
                and path.is_file()
                and verify_immutable_file(
                    path,
                    sha256=artifact.sha256,
                    size_bytes=artifact.size_bytes or None,
                )
            ):
                self._artifacts[artifact.asset_id] = replace(artifact, path=str(path.resolve()))

    def _write_manifest(self) -> None:
        payload = {
            "schema_version": "answer_book.image_artifacts.v2",
            "artifacts": [item.to_dict() for item in self._artifacts.values()],
        }
        atomic_write_json(self.manifest_path, payload)

    def register(
        self,
        source: Path,
        *,
        provider: str,
        model: str,
        source_call_id: str,
    ) -> ImageArtifact:
        source = Path(source)
        size = source.stat().st_size
        if size <= 0 or size > self.max_bytes:
            raise ValueError(f"generated image has invalid byte size: {size}")
        raw = source.read_bytes()
        digest = hashlib.sha256(raw).hexdigest()
        try:
            with Image.open(BytesIO(raw)) as image:
                image.load()
                mime_type = _MIME_BY_FORMAT.get(str(image.format or "").upper())
                width, height = image.size
        except Exception as exc:
            raise ValueError(f"generated image is unreadable: {type(exc).__name__}") from exc
        if not mime_type:
            raise ValueError("generated image format must be PNG, JPEG, or WebP")
        if width < 64 or height < 64:
            raise ValueError(f"generated image dimensions are too small: {width}x{height}")
        suffix = {"image/png": ".png", "image/jpeg": ".jpg", "image/webp": ".webp"}[mime_type]
        asset_id = f"img_sha256_{digest}"
        destination = self.root / f"{asset_id}{suffix}"
        if not verify_immutable_file(destination, sha256=digest, size_bytes=size):
            fd, raw_tmp = tempfile.mkstemp(prefix=f".{asset_id}-", dir=str(self.root))
            os.close(fd)
            temporary = Path(raw_tmp)
            try:
                shutil.copyfile(source, temporary)
                # Windows rejects fsync on a read-only descriptor with EBADF;
                # open the copied file read/write even though its bytes are not
                # modified so the durability barrier is portable.
                with temporary.open("rb+") as handle:
                    os.fsync(handle.fileno())
                os.replace(temporary, destination)
                fsync_directory_best_effort(self.root)
            finally:
                temporary.unlink(missing_ok=True)
        if not verify_immutable_file(destination, sha256=digest, size_bytes=size):
            raise ValueError("generated image failed immutable digest verification")
        artifact = ImageArtifact(
            asset_id=asset_id,
            sha256=digest,
            mime_type=mime_type,
            width=width,
            height=height,
            path=str(destination.resolve()),
            provider=str(provider),
            model=str(model),
            source_call_id=str(source_call_id),
            size_bytes=size,
        )
        self._artifacts[asset_id] = artifact
        self._write_manifest()
        return artifact

    def get(self, asset_id: str) -> ImageArtifact | None:
        artifact = self._artifacts.get(str(asset_id or "").strip())
        if artifact is None:
            return None
        path = Path(artifact.path)
        try:
            path.resolve().relative_to(self.root.resolve())
        except (OSError, ValueError):
            return None
        if not verify_immutable_file(
            path,
            sha256=artifact.sha256,
            size_bytes=artifact.size_bytes or None,
        ):
            return None
        return artifact

    def mark_adopted(self, asset_ids: list[str] | set[str] | tuple[str, ...]) -> None:
        changed = False
        for asset_id in asset_ids:
            cleaned = str(asset_id or "").strip()
            artifact = self.get(cleaned)
            if artifact is None:
                raise KeyError(f"unknown or corrupted image asset: {cleaned}")
            if not artifact.adopted:
                self._artifacts[cleaned] = replace(artifact, adopted=True)
                changed = True
        if changed:
            self._write_manifest()

    def data_url(self, asset_id: str) -> str:
        artifact = self.get(asset_id)
        if artifact is None:
            raise KeyError(f"unknown image asset: {asset_id}")
        raw = Path(artifact.path).read_bytes()
        return f"data:{artifact.mime_type};base64,{base64.b64encode(raw).decode('ascii')}"

    def all(self) -> list[ImageArtifact]:
        return list(self._artifacts.values())


def mark_final_adopted_assets(payload: Any, *, data_root: Path = DATA_ROOT) -> dict[str, int]:
    """Mark assets referenced by final, quality-gated answer fragments.

    The caller decides which payload crossed the final delivery boundary.  This
    helper only matches selected asset IDs to their recorded task-local stores
    and never searches unrelated user directories.
    """

    marked: set[str] = set()
    missing = 0
    fragments = payload.get("fragments") if isinstance(payload, dict) else None
    for fragment in fragments if isinstance(fragments, list) else []:
        if not isinstance(fragment, dict):
            continue
        selected = {
            str(item.get("asset_id") or "").strip()
            for item in fragment.get("generated_images", []) or []
            if isinstance(item, dict) and str(item.get("asset_id") or "").strip()
        }
        raw_meta = fragment.get("_meta")
        meta: dict[str, Any] = dict(raw_meta) if isinstance(raw_meta, dict) else {}
        raw_loop = meta.get("image_tool_loop")
        loop: dict[str, Any] = dict(raw_loop) if isinstance(raw_loop, dict) else {}
        raw_artifacts = loop.get("generated_artifacts")
        artifacts: list[Any] = list(raw_artifacts) if isinstance(raw_artifacts, list) else []
        for item in artifacts:
            if not isinstance(item, dict):
                continue
            asset_id = str(item.get("asset_id") or "").strip()
            path = Path(str(item.get("path") or ""))
            if asset_id not in selected or not path.is_file():
                continue
            try:
                path.resolve().relative_to(Path(data_root).resolve())
                store = ImageArtifactStore(path.parent)
                store.mark_adopted([asset_id])
            except (OSError, KeyError, ValueError):
                missing += 1
                continue
            marked.add(asset_id)
    practice_artifacts = payload.get("_image_tool_artifacts") if isinstance(payload, dict) else None
    practice_artifacts = practice_artifacts if isinstance(practice_artifacts, list) else []
    practice_selected: set[str] = set()
    exercises = payload.get("exercises") if isinstance(payload, dict) else None
    for exercise in exercises if isinstance(exercises, list) else []:
        if not isinstance(exercise, dict) or exercise.get("generation_status") == "failed":
            continue
        practice_selected.update(
            str(item.get("asset_id") or "").strip()
            for item in exercise.get("generated_images", []) or []
            if isinstance(item, dict) and str(item.get("asset_id") or "").strip()
        )
        practice_selected.update(
            str(item.get("asset_id") or "").strip()
            for item in exercise.get("figures", []) or []
            if isinstance(item, dict) and str(item.get("asset_id") or "").strip()
        )
    for item in practice_artifacts:
        if not isinstance(item, dict):
            continue
        asset_id = str(item.get("asset_id") or "").strip()
        path = Path(str(item.get("path") or ""))
        if asset_id not in practice_selected or not path.is_file():
            continue
        try:
            path.resolve().relative_to(Path(data_root).resolve())
            store = ImageArtifactStore(path.parent)
            store.mark_adopted([asset_id])
        except (OSError, KeyError, ValueError):
            missing += 1
            continue
        marked.add(asset_id)
    return {"final_adopted_count": len(marked), "unresolved_selected_asset_count": missing}
