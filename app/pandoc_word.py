"""Sole production Pandoc 3.11 OMML backend; no silent engine fallback."""
from __future__ import annotations

import hashlib
import io
import json
import os
import platform
import shutil
import subprocess
import tarfile
import tempfile
import threading
import time
import urllib.request
from functools import lru_cache
from pathlib import Path
from zipfile import ZipFile

from docx.oxml import parse_xml
from lxml import etree

PANDOC_VERSION = "3.11"
PANDOC_CONTRACT = "pandoc-3.11-omml-v2-c-only"
# Extracted from the hash-verified upstream 3.11 Windows x64/macOS arm64 archives.
_BINARY_HASHES = {
    "e0057eaf640d08ba028b70721d4f7b63a685c30430de9db89aea84de2d4a1912",
    "a6f7e009fa9f4b9c8302fbf88b4c9be4800b0f038bdd2288431fda669b15566e",
    "aec1331ed5eea2b6d497ac3610b6ad35dc1f10b4d41b6deac38e2dd005354e12",
    "870b811354a230a1864dd2b50dec3b850a981926ff4659ae460aea96ab6b8d28",
}


# Official 3.11 archives, verified against the upstream release API.
_ARCHIVES = {
    ("Windows", "x86_64"): ("pandoc-3.11-windows-x86_64.zip", "2ab72baf2399450e148ddf7a2a8689806c42e1bba71862b57e220fd9b8456d3d", "pandoc-3.11/pandoc.exe"),
    ("Darwin", "arm64"): ("pandoc-3.11-arm64-macOS.zip", "15806bedf9517bfead72e88fe6a6696635c3691efbb6e152173440e9c5bb50b4", "pandoc-3.11-arm64/bin/pandoc"),
    ("Darwin", "x86_64"): ("pandoc-3.11-x86_64-macOS.zip", "3b1c1b57f160112c821d02f23d946ede8b7f57a6ccf4632a25a512d334a9291f", "pandoc-3.11-x86_64/bin/pandoc"),
    ("Linux", "x86_64"): ("pandoc-3.11-linux-amd64.tar.gz", "37edb3bbcf722f921a009941bf5874e2e0c09263226c9b4a2d980788cb062ab6", "pandoc-3.11/bin/pandoc"),
}
_INSTALL_LOCK = threading.Lock()


def _install_runtime() -> Path:
    from .paths import DATA_ROOT

    machine = platform.machine().lower()
    machine = {"amd64": "x86_64", "aarch64": "arm64"}.get(machine, machine)
    key = (platform.system(), machine)
    if key not in _ARCHIVES:
        raise RuntimeError(f"Pandoc C 尚未支持此平台：{key}")
    name, digest, member = _ARCHIVES[key]
    folder = DATA_ROOT / "runtime" / "pandoc-word-c" / PANDOC_VERSION / f"{key[0]}-{machine}"
    binary = folder / ("pandoc.exe" if key[0] == "Windows" else "pandoc")
    with _INSTALL_LOCK:
        if binary.is_file():
            return binary
        folder.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="install-", dir=folder) as tmp:
            archive = Path(tmp) / name
            for attempt in range(3):
                try:
                    request = urllib.request.Request(
                        f"https://github.com/jgm/pandoc/releases/download/{PANDOC_VERSION}/{name}",
                        headers={"User-Agent": "answer-book-platform"},
                    )
                    with urllib.request.urlopen(request, timeout=60) as response, archive.open("wb") as out:
                        shutil.copyfileobj(response, out)
                    break
                except OSError:
                    archive.unlink(missing_ok=True)
                    if attempt == 2:
                        raise
                    time.sleep(0.5 * 2**attempt)
            if hashlib.sha256(archive.read_bytes()).hexdigest() != digest:
                raise RuntimeError("Pandoc archive SHA256 mismatch; refusing installation")
            if name.endswith(".zip"):
                with ZipFile(archive) as zipped:
                    content = zipped.read(member)
            else:
                with tarfile.open(archive) as packed:
                    handle = packed.extractfile(member)
                    if handle is None:
                        raise RuntimeError("Pandoc executable is missing from archive")
                    content = handle.read()
            if hashlib.sha256(content).hexdigest() not in _BINARY_HASHES:
                raise RuntimeError("Pandoc executable SHA256 mismatch; refusing installation")
            staged = Path(tmp) / binary.name
            staged.write_bytes(content)
            staged.chmod(0o755)
            # Preserve the complete upstream archive, including its licenses.
            os.replace(archive, folder / name)
            os.replace(staged, binary)
    return binary


def _binary() -> Path:
    raw = os.environ.get("ANSWER_BOOK_PANDOC_BINARY", "").strip()
    if not raw:
        from .paths import LOCAL_CONFIG_DIR

        config = LOCAL_CONFIG_DIR / "pandoc_runtime.json"
        if config.is_file():
            raw = str(json.loads(config.read_text(encoding="utf-8")).get("binary") or "").strip()
    if not raw:
        return _install_runtime()
    path = Path(raw).resolve(strict=True)
    return path


def _run(binary: str, args: list[str], data: bytes = b"") -> bytes:
    result = subprocess.run(
        [binary, *args], input=data, capture_output=True, timeout=30,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    if result.returncode:
        raise ValueError("Pandoc conversion failed: " + result.stderr.decode("utf-8", errors="replace")[:2000])
    return result.stdout


@lru_cache(maxsize=8)
def _runtime(binary: str, modified_ns: int, size: int) -> tuple[str, tuple[int, ...]]:
    digest = hashlib.sha256(Path(binary).read_bytes()).hexdigest()
    if digest not in _BINARY_HASHES:
        raise RuntimeError("Pandoc binary SHA256 does not match the verified 3.11 release")
    version = _run(binary, ["--version"]).decode("utf-8").splitlines()[0]
    if version != f"pandoc {PANDOC_VERSION}":
        raise RuntimeError("Unexpected Pandoc version: " + version)
    api = json.loads(_run(binary, ["-f", "markdown", "-t", "json"]))["pandoc-api-version"]
    return digest, tuple(api)


def runtime_info() -> dict:
    path = _binary()
    stat = path.stat()
    digest, api = _runtime(str(path), stat.st_mtime_ns, stat.st_size)
    return {"variant": "C", "engine": "Pandoc/texmath + python-docx", "version": PANDOC_VERSION,
            "contract": PANDOC_CONTRACT, "sha256": digest, "binary": str(path), "api_version": api}


@lru_cache(maxsize=4096)
def _xml(src: str, binary: str, digest: str, api: tuple[int, ...]) -> bytes:
    from latex2mathml.converter import convert as validate_syntax

    try:
        validate_syntax(src)
    except Exception as exc:
        raise ValueError(f"Invalid formula syntax: {type(exc).__name__}: {exc}") from exc
    ast = {"pandoc-api-version": api, "meta": {}, "blocks": [
        {"t": "Para", "c": [{"t": "Math", "c": [{"t": "DisplayMath"}, src]}]},
    ]}
    content = _run(binary, ["-f", "json", "-t", "docx", "-o", "-", "--fail-if-warnings"], json.dumps(ast).encode())
    with ZipFile(io.BytesIO(content)) as archive:
        root = etree.fromstring(archive.read("word/document.xml"), etree.XMLParser(resolve_entities=False, no_network=True))
    nodes = root.xpath('//*[local-name()="oMath"]')
    if len(nodes) != 1:
        raise ValueError("Expected exactly one native math object")
    texts = nodes[0].xpath('.//*[local-name()="t"]/text()')
    if not texts or any("\\" in text or "$" in text for text in texts):
        raise ValueError("Empty formula or unconverted markup in Pandoc OMML")
    return etree.tostring(nodes[0])


def convert(src: str):
    info = runtime_info()
    return parse_xml(_xml(src, info["binary"], info["sha256"], info["api_version"]))
