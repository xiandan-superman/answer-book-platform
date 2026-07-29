from __future__ import annotations

import hashlib
import json
import shutil
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .paths import CACHE_DIR


PACKAGE_CACHE_DIR = CACHE_DIR / "textbook_packages"
PACKAGE_SCHEMA_VERSION = "answer_book.textbook_package.v1"


@dataclass(frozen=True)
class TextbookPackage:
    package_id: str
    root: Path
    title: str
    citation_name: str
    content_list: Path | None
    content_list_v2: Path | None
    layout_json: Path | None
    markdown: Path | None
    origin_pdf: Path | None
    images_root: Path | None
    audit_path: Path


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _safe_extract_zip(zip_path: Path, target_dir: Path) -> None:
    target_dir.mkdir(parents=True, exist_ok=True)
    root = target_dir.resolve()
    with zipfile.ZipFile(zip_path) as zf:
        for info in zf.infolist():
            if info.is_dir():
                continue
            name = info.filename.replace("\\", "/")
            if name.startswith("/") or ".." in Path(name).parts:
                raise ValueError(f"Unsafe path in textbook package zip: {info.filename}")
            target = (target_dir / name).resolve()
            try:
                target.relative_to(root)
            except ValueError as exc:
                raise ValueError(f"Unsafe path in textbook package zip: {info.filename}") from exc
            target.parent.mkdir(parents=True, exist_ok=True)
            with zf.open(info) as src, target.open("wb") as dst:
                shutil.copyfileobj(src, dst)


def _first_existing(root: Path, patterns: list[str], *, prefer: str = "") -> Path | None:
    candidates: list[Path] = []
    for pattern in patterns:
        candidates.extend(path for path in root.rglob(pattern) if path.is_file())
    if not candidates:
        return None
    if prefer:
        preferred = [path for path in candidates if prefer.lower() in path.name.lower()]
        if preferred:
            candidates = preferred
    return sorted(candidates, key=lambda p: (len(p.parts), p.name.lower()))[0]


def _find_images_root(root: Path) -> Path | None:
    candidates = [path for path in root.rglob("images") if path.is_dir()]
    if not candidates:
        return None
    candidates.sort(key=lambda p: (-sum(1 for child in p.iterdir() if child.is_file()), len(p.parts), p.name.lower()))
    return candidates[0]


def _manifest_title(zip_path: Path, root: Path) -> tuple[str, str]:
    manifest = root / "manifest.json"
    if manifest.exists():
        try:
            data = json.loads(manifest.read_text(encoding="utf-8"))
            title = str(data.get("title") or data.get("textbook_title") or "").strip()
            citation = str(data.get("citation_name") or data.get("citation_textbook") or title).strip()
            if title:
                return title, citation or title
        except Exception:
            pass
    title = zip_path.stem
    for suffix in ("_OCR.pdf", ".pdf"):
        title = title.replace(suffix, "")
    return title, title


def _rel(path: Path | None, root: Path) -> str:
    if path is None:
        return ""
    try:
        return str(path.resolve().relative_to(root.resolve()))
    except ValueError:
        return str(path)


def resolve_package_asset(root: Path, content_list: Path | None, images_root: Path | None, raw: Any) -> Path:
    text = str(raw or "").strip()
    if not text:
        return Path()
    path = Path(text)
    if path.is_absolute():
        return path
    candidates = [root / path]
    if content_list is not None:
        candidates.append(content_list.parent / path)
    if images_root is not None:
        candidates.append(images_root / path.name)
    for candidate in candidates:
        if candidate.exists():
            return candidate.resolve()
    return candidates[0].resolve()


def _asset_references(content_list: Path | None) -> list[str]:
    if content_list is None or not content_list.exists():
        return []
    try:
        data = json.loads(content_list.read_text(encoding="utf-8"))
    except Exception:
        return []
    refs: list[str] = []

    def walk(value: Any) -> None:
        if isinstance(value, dict):
            for key in ("img_path", "image_path", "path"):
                raw = value.get(key)
                if isinstance(raw, str) and raw.strip() and ("/" in raw or raw.lower().endswith((".jpg", ".jpeg", ".png", ".webp"))):
                    refs.append(raw.strip())
            for child in value.values():
                if isinstance(child, (dict, list)):
                    walk(child)
        elif isinstance(value, list):
            for item in value:
                walk(item)

    walk(data)
    return list(dict.fromkeys(refs))


def _audit_package(package: TextbookPackage, zip_path: Path) -> dict[str, Any]:
    issues: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []
    if package.content_list is None:
        issues.append({"code": "missing_content_list", "message": "未找到 MinerU content_list.json，无法建立稳定教材索引。"})
    if package.images_root is None:
        warnings.append({"code": "missing_images_root", "message": "未找到 images/ 目录，图表证据只能使用文字化内容。"})
    if package.origin_pdf is None:
        warnings.append({"code": "missing_origin_pdf", "message": "未找到 origin.pdf，页码和原页复核能力降低。"})
    if package.markdown is None:
        warnings.append({"code": "missing_markdown", "message": "未找到 full.md，连续文本兜底能力降低。"})

    refs = _asset_references(package.content_list)
    missing_refs: list[str] = []
    for ref in refs:
        path = resolve_package_asset(package.root, package.content_list, package.images_root, ref)
        if not path.exists():
            missing_refs.append(ref)
    if missing_refs:
        issues.append(
            {
                "code": "missing_asset_files",
                "message": f"content_list 引用了 {len(missing_refs)} 个不存在的图片/表格/公式文件。",
                "examples": missing_refs[:20],
            }
        )
    image_count = 0
    if package.images_root and package.images_root.exists():
        image_count = sum(1 for path in package.images_root.rglob("*") if path.is_file() and path.suffix.lower() in {".jpg", ".jpeg", ".png", ".webp"})
    return {
        "schema_version": PACKAGE_SCHEMA_VERSION,
        "ok": not issues,
        "package_id": package.package_id,
        "source_zip": str(zip_path),
        "root": str(package.root),
        "title": package.title,
        "citation_name": package.citation_name,
        "files": {
            "content_list": _rel(package.content_list, package.root),
            "content_list_v2": _rel(package.content_list_v2, package.root),
            "layout_json": _rel(package.layout_json, package.root),
            "markdown": _rel(package.markdown, package.root),
            "origin_pdf": _rel(package.origin_pdf, package.root),
            "images_root": _rel(package.images_root, package.root),
        },
        "asset_reference_count": len(refs),
        "image_file_count": image_count,
        "issues": issues,
        "warnings": warnings,
    }


def prepare_textbook_package(zip_path: Path) -> TextbookPackage:
    zip_path = zip_path.expanduser().resolve()
    if not zip_path.is_file() or zip_path.suffix.lower() != ".zip":
        raise ValueError(f"Not a textbook package zip: {zip_path}")
    package_id = _sha256_file(zip_path)[:24]
    root = PACKAGE_CACHE_DIR / package_id
    marker = root / ".extracted_from"
    if not marker.exists() or marker.read_text(encoding="utf-8", errors="ignore") != str(zip_path):
        if root.exists():
            shutil.rmtree(root)
        _safe_extract_zip(zip_path, root)
        marker.write_text(str(zip_path), encoding="utf-8")
    title, citation = _manifest_title(zip_path, root)
    content_list = _first_existing(root, ["*content_list.json"], prefer="content_list")
    content_list_v2 = _first_existing(root, ["*content_list_v2.json"], prefer="content_list_v2")
    layout_json = _first_existing(root, ["layout.json", "*layout*.json"], prefer="layout")
    markdown = _first_existing(root, ["full.md", "*.md"], prefer="full")
    origin_pdf = _first_existing(root, ["*origin.pdf", "*.pdf"], prefer="origin")
    images_root = _find_images_root(root)
    package = TextbookPackage(
        package_id=package_id,
        root=root,
        title=title,
        citation_name=citation,
        content_list=content_list,
        content_list_v2=content_list_v2,
        layout_json=layout_json,
        markdown=markdown,
        origin_pdf=origin_pdf,
        images_root=images_root,
        audit_path=root / "textbook_package_audit.json",
    )
    audit = _audit_package(package, zip_path)
    package.audit_path.write_text(json.dumps(audit, ensure_ascii=False, indent=2), encoding="utf-8")
    (root / "manifest.normalized.json").write_text(json.dumps(audit, ensure_ascii=False, indent=2), encoding="utf-8")
    return package


def is_textbook_package(path: Path) -> bool:
    return path.suffix.lower() == ".zip"
