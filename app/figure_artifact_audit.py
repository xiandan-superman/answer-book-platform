"""Check current figure files without a second model judging their content."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from PIL import Image


def audit_figure_artifacts(stage_dir: Path) -> dict[str, Any]:
    specs_path = stage_dir / "figure_specs.json"
    data = json.loads(specs_path.read_text(encoding="utf-8")) if specs_path.exists() else {}
    targets: list[tuple[str, str, Path]] = []
    for spec in data.get("figures", []) or []:
        if not isinstance(spec, dict):
            continue
        figure_id = str(spec.get("figure_id") or "").strip()
        path = stage_dir / "figures" / f"{figure_id}.png"
        targets.append((str(spec.get("question_id") or ""), figure_id, path))
    fragments_path = stage_dir / "answer_fragments.json"
    fragments = json.loads(fragments_path.read_text(encoding="utf-8")) if fragments_path.exists() else {}
    for fragment in fragments.get("fragments", []) or []:
        for block in fragment.get("blocks", []) or []:
            for segment in block.get("segments", []) or []:
                if segment.get("type") != "image_ref":
                    continue
                raw_path = str(segment.get("path") or "")
                path = Path(raw_path) if raw_path else stage_dir / "__missing_image__"
                if not path.is_absolute():
                    path = stage_dir / path
                targets.append((str(fragment.get("question_id") or ""),
                                str(segment.get("image_id") or path.stem), path))
    items: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for question_id, figure_id, path in targets:
        key = (question_id, str(path))
        if key in seen:
            continue
        seen.add(key)
        reason = ""
        if not figure_id or not path.is_file():
            reason = "figure image missing"
        else:
            try:
                with Image.open(path) as image:
                    image.load()
                    if min(image.size) <= 0:
                        reason = "figure image has invalid dimensions"
            except Exception as exc:
                reason = f"figure image unreadable: {type(exc).__name__}"
        items.append({
            "question_id": question_id,
            "figure_id": figure_id,
            "path": str(path),
            "ok": not reason,
            "reason": reason,
        })
    failures = [item for item in items if not item["ok"]]
    return {
        "schema_version": "answer_book.figure_artifact_audit.v1",
        "ok": not failures,
        "independent_visual_review_enabled": False,
        "items": items,
        "failures": failures,
        "issue_count": len(failures),
    }
