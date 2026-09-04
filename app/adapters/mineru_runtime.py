from __future__ import annotations

import hashlib
import html
import json
import os
import re
import shutil
import subprocess
import sys
import threading
from pathlib import Path

from ..dependency_profiles import runtime_python_supported
from ..paths import CACHE_DIR, DATA_ROOT, PROJECT_ROOT
from ..text_utils import clean_text
from ..textbook_package import TextbookPackage, resolve_package_asset

MINERU_VERSION = "3.4.5"
MINERU_PROFILE = "pipeline"
_INSTALL_LOCK = threading.Lock()


class MinerURuntimeError(RuntimeError):
    pass


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _managed_python() -> Path:
    root = DATA_ROOT / "runtime" / f"mineru-{MINERU_VERSION}-{MINERU_PROFILE}-py311"
    return root / ("Scripts/python.exe" if os.name == "nt" else "bin/python")


def _managed_cli(python: Path) -> Path:
    return python.parent / ("mineru.exe" if os.name == "nt" else "mineru")


def _runtime_marker(python: Path) -> Path:
    return python.parent.parent / ".answer-book-runtime.json"


def _runtime_fingerprint() -> dict[str, str]:
    requirements = PROJECT_ROOT / "requirements-mineru.txt"
    return {
        "engine": "mineru",
        "version": MINERU_VERSION,
        "profile": MINERU_PROFILE,
        "python": "3.11",
        "requirements_sha256": _sha256_file(requirements),
    }


def _probe_runtime(python: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            str(python),
            "-c",
            "import torch; import mineru.backend.pipeline.model_init",
        ],
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )


def _runtime_ready(python: Path) -> bool:
    if not python.is_file() or not _managed_cli(python).is_file():
        return False
    marker = _runtime_marker(python)
    try:
        recorded = json.loads(marker.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    if recorded != _runtime_fingerprint():
        return False
    try:
        return _probe_runtime(python).returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def _install_runtime(python: Path) -> None:
    with _INSTALL_LOCK:
        if not runtime_python_supported():
            current = ".".join(str(part) for part in sys.version_info[:3])
            raise MinerURuntimeError(
                f"MinerU {MINERU_VERSION} 必须由 Python 3.11 运行；当前平台使用 Python {current}。"
                "请安装 Python 3.11，完全退出平台后重新启动。"
            )
        if _runtime_ready(python):
            return
        if os.environ.get("ANSWER_BOOK_MINERU_AUTO_INSTALL", "1").strip().lower() in {"0", "false", "no"}:
            raise MinerURuntimeError("MinerU 运行时尚未安装，且 ANSWER_BOOK_MINERU_AUTO_INSTALL 已关闭")
        python.parent.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run([sys.executable, "-m", "venv", str(python.parent.parent)], check=True, timeout=180)
        requirements = PROJECT_ROOT / "requirements-mineru.txt"
        completed = subprocess.run(
            [str(python), "-m", "pip", "install", "--disable-pip-version-check", "-r", str(requirements)],
            capture_output=True,
            text=True,
            timeout=1800,
            check=False,
        )
        if completed.returncode != 0:
            detail = (completed.stderr or completed.stdout or "")[-1200:].strip()
            if "No matching distribution found" in detail or "Requires-Python" in detail:
                detail = "当前 Python 或操作系统没有兼容的 MinerU 安装包。请确认平台由 Python 3.11 启动。"
            raise MinerURuntimeError(f"MinerU {MINERU_VERSION} 安装失败：{detail}")
        try:
            probe = _probe_runtime(python)
        except (OSError, subprocess.SubprocessError) as exc:
            raise MinerURuntimeError(f"MinerU {MINERU_VERSION} pipeline 引擎自检无法执行：{exc}") from exc
        if probe.returncode != 0:
            detail = (probe.stderr or probe.stdout or "")[-1200:].strip()
            raise MinerURuntimeError(f"MinerU {MINERU_VERSION} 安装未通过 pipeline 引擎自检：{detail}")
        _runtime_marker(python).write_text(
            json.dumps(_runtime_fingerprint(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
def mineru_command() -> list[str]:
    override = os.environ.get("ANSWER_BOOK_MINERU_COMMAND", "").strip()
    if override:
        path = Path(override).expanduser()
        if not path.is_file():
            raise MinerURuntimeError(f"ANSWER_BOOK_MINERU_COMMAND 不存在：{path}")
        return [str(path)]
    python = _managed_python()
    _install_runtime(python)
    cli = _managed_cli(python)
    if not cli.is_file():
        raise MinerURuntimeError(f"MinerU CLI 未生成：{cli}")
    return [str(cli)]


def runtime_status() -> dict[str, object]:
    override = os.environ.get("ANSWER_BOOK_MINERU_COMMAND", "").strip()
    command = Path(override).expanduser() if override else _managed_cli(_managed_python())
    installed = command.is_file() if override else command.is_file() and _runtime_marker(_managed_python()).is_file()
    return {
        "engine": "mineru",
        "version": MINERU_VERSION,
        "installed": installed,
        "profile": MINERU_PROFILE,
        "python_requirement": "3.11.x",
        "python_compatible": runtime_python_supported(),
        "command": str(command),
        "auto_install": os.environ.get("ANSWER_BOOK_MINERU_AUTO_INSTALL", "1").strip().lower() not in {"0", "false", "no"},
        "fallback": False,
    }


def parse_document(path: Path) -> TextbookPackage:
    """Run MinerU as the single PDF/DOCX parser and expose its native package."""

    source = path.expanduser().resolve()
    if not source.is_file():
        raise MinerURuntimeError(f"待解析文档不存在：{source}")
    package_id = hashlib.sha256(f"{MINERU_VERSION}:".encode() + _sha256_file(source).encode()).hexdigest()[:24]
    root = CACHE_DIR / "mineru_runtime" / package_id
    audit_path = root / "answer_book_mineru_audit.json"
    existing = sorted(root.rglob("*content_list.json")) if root.exists() else []
    if not existing:
        root.mkdir(parents=True, exist_ok=True)
        completed = subprocess.run(
            [*mineru_command(), "-p", str(source), "-o", str(root), "-b", MINERU_PROFILE],
            capture_output=True,
            text=True,
            timeout=max(60, int(os.environ.get("ANSWER_BOOK_MINERU_TIMEOUT_SECONDS", "1800"))),
            check=False,
        )
        if completed.returncode != 0:
            detail = (completed.stderr or completed.stdout or "")[-4000:]
            raise MinerURuntimeError(f"MinerU 解析失败（{source.name}）：{detail}")
        existing = sorted(root.rglob("*content_list.json"))
        if not existing:
            raise MinerURuntimeError(f"MinerU 未生成 content_list.json：{source.name}")
        audit_path.write_text(
            json.dumps(
                {
                    "schema_version": "answer_book.mineru_runtime.v1",
                    "mineru_version": MINERU_VERSION,
                    "source_file": str(source),
                    "source_sha256": _sha256_file(source),
                    "command": f"mineru -p <source> -o <cache> -b {MINERU_PROFILE}",
                    "stdout_tail": (completed.stdout or "")[-2000:],
                    "stderr_tail": (completed.stderr or "")[-2000:],
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
    content_list = existing[0]
    try:
        blocks = json.loads(content_list.read_text(encoding="utf-8"))
    except Exception as exc:
        raise MinerURuntimeError(f"MinerU content_list 无法读取：{content_list}") from exc
    if not isinstance(blocks, list):
        raise MinerURuntimeError(f"MinerU content_list 顶层不是数组：{content_list}")
    images = [candidate for candidate in root.rglob("images") if candidate.is_dir()]
    images_root = max(images, key=lambda item: len(list(item.iterdir()))) if images else None
    return TextbookPackage(
        package_id=package_id,
        root=root,
        title=source.stem,
        citation_name=source.stem,
        content_list=content_list,
        content_list_v2=None,
        layout_json=None,
        markdown=next(iter(sorted(root.rglob("*.md"))), None),
        origin_pdf=source if source.suffix.lower() == ".pdf" else None,
        images_root=images_root,
        audit_path=audit_path,
    )


def paragraph_lines(
    package: TextbookPackage,
    *,
    image_dir: Path,
    image_marker_prefix: str,
    table_marker_prefix: str,
) -> list[str]:
    if package.content_list is None:
        return []
    blocks = json.loads(package.content_list.read_text(encoding="utf-8"))
    lines: list[str] = []
    image_index = 0
    for item in blocks:
        if not isinstance(item, dict):
            continue
        kind = str(item.get("type") or "text").lower()
        if kind == "table":
            body = str(item.get("table_body") or item.get("html") or "")
            body = re.sub(r"</(?:td|th)>\s*<(?:td|th)[^>]*>", " | ", body, flags=re.I)
            body = re.sub(r"</tr>\s*<tr[^>]*>", " ; ", body, flags=re.I)
            body = clean_text(html.unescape(re.sub(r"<[^>]+>", " ", body)))
            caption = clean_text(" ".join(map(str, item.get("table_caption") or [])))
            if body or caption:
                lines.append(f"{table_marker_prefix}{json.dumps([[caption, body]], ensure_ascii=False)}")
        else:
            text = clean_text(str(item.get("text") or item.get("content") or ""))
            caption = item.get("image_caption") or item.get("chart_caption") or []
            if isinstance(caption, list):
                text = clean_text(" ".join([text, *map(str, caption)]))
            if text:
                lines.append(text)
        raw_asset = item.get("img_path") or item.get("image_path")
        if raw_asset:
            asset = resolve_package_asset(package.root, package.content_list, package.images_root, raw_asset)
            if asset.is_file():
                image_index += 1
                image_dir.mkdir(parents=True, exist_ok=True)
                target = image_dir / f"source_image_{image_index:03d}{asset.suffix or '.png'}"
                shutil.copy2(asset, target)
                lines.append(f"{image_marker_prefix}{target}")
    return lines
