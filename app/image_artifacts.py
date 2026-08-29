from __future__ import annotations

import base64
import hashlib
import json
import shutil
from dataclasses import asdict, dataclass
from io import BytesIO
from pathlib import Path
from typing import Any

from PIL import Image


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
                artifact = ImageArtifact(**item)
            except TypeError:
                continue
            path = Path(artifact.path)
            if path.exists() and path.is_file():
                self._artifacts[artifact.asset_id] = artifact

    def _write_manifest(self) -> None:
        payload = {
            "schema_version": "answer_book.image_artifacts.v1",
            "artifacts": [item.to_dict() for item in self._artifacts.values()],
        }
        temporary = self.manifest_path.with_suffix(".json.tmp")
        temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        temporary.replace(self.manifest_path)

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
        if not destination.exists():
            temporary = destination.with_suffix(destination.suffix + ".tmp")
            shutil.copyfile(source, temporary)
            temporary.replace(destination)
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
        )
        self._artifacts[asset_id] = artifact
        self._write_manifest()
        return artifact

    def get(self, asset_id: str) -> ImageArtifact | None:
        return self._artifacts.get(str(asset_id or "").strip())

    def data_url(self, asset_id: str) -> str:
        artifact = self.get(asset_id)
        if artifact is None:
            raise KeyError(f"unknown image asset: {asset_id}")
        raw = Path(artifact.path).read_bytes()
        return f"data:{artifact.mime_type};base64,{base64.b64encode(raw).decode('ascii')}"

    def all(self) -> list[ImageArtifact]:
        return list(self._artifacts.values())
