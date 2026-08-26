from __future__ import annotations

import os
import platform
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
from copy import deepcopy
from importlib.util import find_spec
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from .drawing_code import run_drawing_code
from .omml import clear_omml_caches, find_mathml2omml_xsl
from .omml_input import clear_omml_input_caches, find_omml2mathml_xsl
from .paths import PROJECT_ROOT, ensure_project_dirs
from .render_fonts import project_font_diagnostics
from .settings import list_providers


_STATIC_PROBE_CACHE_LOCK = threading.Lock()
_STATIC_PROBE_CACHE: dict[tuple[str, int], tuple[float, dict[str, Any]]] = {}


def _static_probe_cache_seconds() -> float:
    try:
        return max(0.0, min(300.0, float(os.environ.get("ENVIRONMENT_STATIC_PROBE_CACHE_SECONDS", "30"))))
    except (TypeError, ValueError):
        return 30.0


def clear_environment_probe_cache() -> None:
    with _STATIC_PROBE_CACHE_LOCK:
        _STATIC_PROBE_CACHE.clear()


def _cached_static_probe(name: str, probe: Any) -> dict[str, Any]:
    """Reuse expensive local capability probes while keeping provider state live."""

    ttl = _static_probe_cache_seconds()
    if ttl <= 0:
        return probe()
    key = (name, id(probe))
    now = time.monotonic()
    with _STATIC_PROBE_CACHE_LOCK:
        cached = _STATIC_PROBE_CACHE.get(key)
        if cached is not None and now - cached[0] < ttl:
            return deepcopy(cached[1])
        result = probe()
        _STATIC_PROBE_CACHE[key] = (time.monotonic(), deepcopy(result))
        return result


def _package_data_file_exists(package: str, relative_path: str) -> bool:
    try:
        spec = find_spec(package)
    except (ImportError, ModuleNotFoundError, ValueError):
        return False
    if spec is None:
        return False
    roots = [Path(str(path)) for path in (getattr(spec, "submodule_search_locations", None) or [])]
    origin = getattr(spec, "origin", None)
    if origin:
        roots.append(Path(str(origin)).parent)
    return any((root / relative_path).is_file() for root in roots)


def _run_command(cmd: list[str], timeout: int = 300) -> dict[str, Any]:
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    return {
        "cmd": cmd,
        "returncode": proc.returncode,
        "ok": proc.returncode == 0,
        "stdout": (proc.stdout or "")[-4000:],
        "stderr": (proc.stderr or "")[-4000:],
    }


def _check_word_mac() -> dict[str, Any]:
    if platform.system() != "Darwin":
        return {"applicable": False}
    script = 'tell application "System Events" to exists application process "Microsoft Word"'
    # This only checks whether automation can speak AppleScript. Word may still be installed but not running.
    word_app = "/Applications/Microsoft Word.app"
    soffice = shutil.which("soffice") or shutil.which("libreoffice")
    return {
        "applicable": True,
        "word_app_exists": os.path.exists(word_app),
        "osascript_exists": bool(shutil.which("osascript")),
        "soffice": soffice,
        "render_pdf_available": bool(os.path.exists(word_app) or soffice),
        "note": "Word render checks require Microsoft Word installed locally.",
        "probe_script": script,
    }


def _check_word_windows() -> dict[str, Any]:
    if platform.system() != "Windows":
        return {"applicable": False}
    py = sys.executable or shutil.which("python") or shutil.which("python3")
    soffice = shutil.which("soffice") or shutil.which("libreoffice")
    if not py:
        return {
            "applicable": True,
            "python_exists": False,
            "python": None,
            "pywin32_available": bool(find_spec("win32com")),
            "word_com_available": False,
            "soffice": soffice,
            "render_pdf_available": bool(soffice),
        }
    code = """
import win32com.client

word = None
try:
    # DispatchEx creates an isolated probe instance.  Dispatch may attach to
    # the user's open Word session and a probe must never leave it running or
    # close the user's own documents.
    word = win32com.client.DispatchEx("Word.Application")
    word.Visible = False
    print("ok")
finally:
    if word is not None:
        word.Quit()
""".strip()
    stderr = ""
    stdout = ""
    try:
        proc = subprocess.run([py, "-c", code], capture_output=True, text=True, timeout=10)
        ok = proc.returncode == 0
        stderr = (proc.stderr or "").strip()
        stdout = (proc.stdout or "").strip()
    except (OSError, subprocess.SubprocessError) as exc:
        ok = False
        stderr = str(exc)
    return {
        "applicable": True,
        "python_exists": True,
        "python": py,
        "pywin32_available": bool(find_spec("win32com")),
        "word_com_available": ok,
        "word_probe_stdout": stdout[:500],
        "word_probe_stderr": stderr[:500],
        "soffice": soffice,
        "render_pdf_available": bool(ok or soffice),
        "render_pdf_strategy": "word_com" if ok else ("libreoffice" if soffice else "unavailable"),
    }


def _check_network(providers: dict[str, dict[str, Any]]) -> dict[str, Any]:
    targets: list[dict[str, Any]] = []
    for name, cfg in providers.items():
        parsed = urlparse(str(cfg.get("base_url") or ""))
        host = parsed.hostname
        if not host:
            continue
        port = parsed.port or (443 if parsed.scheme == "https" else 80)
        targets.append({"provider": name, "host": host, "port": port})
    if not targets:
        return {"ok": False, "checked": [], "message": "no provider base_url configured"}

    checked: list[dict[str, Any]] = []
    for target in targets:
        item = dict(target)
        try:
            with socket.create_connection((target["host"], int(target["port"])), timeout=3):
                item.update({"ok": True, "error": ""})
        except OSError as exc:
            item.update({"ok": False, "error": str(exc)})
        checked.append(item)
    return {
        "ok": any(item["ok"] for item in checked),
        "checked": checked,
        "by_provider": {str(item["provider"]): bool(item["ok"]) for item in checked},
        "reachable_providers": [str(item["provider"]) for item in checked if item["ok"]],
        "unreachable_providers": [str(item["provider"]) for item in checked if not item["ok"]],
        "message": "at least one provider host reachable" if any(item["ok"] for item in checked) else "no provider host reachable",
    }


def _check_drawing_runtime() -> dict[str, Any]:
    """Run the same isolated renderer used by model-generated drawing code."""
    if not find_spec("matplotlib"):
        return {
            "ok": False,
            "matplotlib_available": False,
            "resource_limits": "not_checked",
            "issues": ["matplotlib is not installed"],
        }

    resource_limits = "available" if find_spec("resource") else "not_available_on_this_platform"

    code = """
import matplotlib.pyplot as plt

def draw(output_path: str) -> None:
    fig, ax = plt.subplots(figsize=(1, 1))
    ax.plot([0, 1], [0, 1], color='black')
    fig.savefig(output_path, dpi=72)
    plt.close(fig)
""".strip()
    try:
        with tempfile.TemporaryDirectory(prefix="answer_book_drawing_probe_") as raw_tmp:
            probe_dir = Path(raw_tmp)
            result = run_drawing_code(
                code,
                probe_dir / "drawing_probe.png",
                probe_dir / "drawing_probe.py",
                timeout_seconds=15,
            )
    except Exception as exc:
        return {
            "ok": False,
            "matplotlib_available": True,
            "resource_limits": resource_limits,
            "issues": [f"drawing runtime probe failed: {exc.__class__.__name__}: {exc}"],
        }
    return {
        "ok": result.ok,
        "matplotlib_available": True,
        "resource_limits": resource_limits,
        "issues": result.issues,
        "returncode": result.returncode,
        "stderr": result.stderr[-1000:],
    }


def environment_repair_actions(env: dict[str, Any]) -> list[dict[str, Any]]:
    actions: list[dict[str, Any]] = []
    packages = env.get("python_packages", {})
    missing_core = [
        name
        for name in ("python-docx", "lxml", "latex2mathml", "Pillow", "matplotlib", "pydantic", "pypdfium2", "bm25s", "huey")
        if not packages.get(name)
    ]
    if missing_core:
        actions.append(
            {
                "id": "install_python_dependencies",
                "title": "修复 Python 依赖",
                "description": f"安装缺失依赖：{'、'.join(missing_core)}。",
                "button": "安装依赖",
                "impact": "会在当前 Python 环境执行 pip install。",
            }
        )

    word_windows = env.get("microsoft_word", {}).get("windows", {})
    if word_windows.get("applicable") and not word_windows.get("word_com_available"):
        actions.append(
            {
                "id": "install_windows_word_com",
                "title": "修复 Word 调用组件",
                "description": "安装 pywin32，让 Python 可以调用 Microsoft Word。",
                "button": "安装 pywin32",
                "impact": "会在当前 Python 环境安装 Windows Word 自动化组件。",
            }
        )

    if platform.system() == "Windows" and not env.get("document_tools", {}).get("pdf_render_available") and shutil.which("winget"):
        actions.append(
            {
                "id": "install_libreoffice_windows",
                "title": "安装 PDF 渲染工具",
                "description": "安装 LibreOffice，用于 Word 转 PDF 的备用渲染。",
                "button": "安装 LibreOffice",
                "impact": "会调用 winget 安装 LibreOffice，过程可能需要几分钟。",
            }
        )
    return actions


def repair_environment(action: str) -> dict[str, Any]:
    if action == "install_python_dependencies":
        result = _run_command([sys.executable, "-m", "pip", "install", "-r", str(PROJECT_ROOT / "requirements.txt")], timeout=600)
    elif action == "install_windows_word_com":
        if platform.system() != "Windows":
            raise ValueError("此修复仅适用于 Windows")
        result = _run_command([sys.executable, "-m", "pip", "install", "-r", str(PROJECT_ROOT / "requirements-windows.txt")], timeout=600)
    elif action == "install_libreoffice_windows":
        if platform.system() != "Windows":
            raise ValueError("此修复仅适用于 Windows")
        winget = shutil.which("winget")
        if not winget:
            raise ValueError("未找到 winget，无法自动安装 LibreOffice")
        result = _run_command(
            [
                winget,
                "install",
                "--id",
                "TheDocumentFoundation.LibreOffice",
                "-e",
                "--accept-source-agreements",
                "--accept-package-agreements",
            ],
            timeout=1200,
        )
    else:
        raise ValueError(f"未知修复动作：{action}")
    clear_omml_caches()
    clear_omml_input_caches()
    clear_environment_probe_cache()
    return {"ok": bool(result.get("ok")), "action": action, "result": result, "environment": check_environment()}


def check_environment() -> dict[str, Any]:
    ensure_project_dirs()
    providers = {name: cfg.redacted() for name, cfg in list_providers().items()}
    xsl = find_mathml2omml_xsl()
    input_xsl = find_omml2mathml_xsl()
    word_mac = _check_word_mac()
    word_windows = _cached_static_probe("word_windows", _check_word_windows)
    drawing_runtime = _cached_static_probe("drawing_runtime", _check_drawing_runtime)
    soffice = shutil.which("soffice") or shutil.which("libreoffice")
    latex2mathml_data_available = _package_data_file_exists("latex2mathml", "unimathsymbols.txt")
    python_packages = {
        "python-docx": bool(find_spec("docx")),
        "lxml": bool(find_spec("lxml")),
        "latex2mathml": bool(find_spec("latex2mathml")),
        "math_ml2omml": bool(find_spec("math_ml2omml")),
        "Pillow": bool(find_spec("PIL")),
        "matplotlib": bool(find_spec("matplotlib")),
        "pydantic": bool(find_spec("pydantic")),
        "pypdfium2": bool(find_spec("pypdfium2")),
        "bm25s": bool(find_spec("bm25s")),
        "huey": bool(find_spec("huey")),
        "pywin32": bool(find_spec("win32com")),
    }
    env: dict[str, Any] = {
        "platform": platform.platform(),
        "python": platform.python_version(),
        "executables": {
            "python3": shutil.which("python3"),
            "pdftoppm": shutil.which("pdftoppm"),
            "soffice": shutil.which("soffice") or shutil.which("libreoffice"),
        },
        "python_packages": python_packages,
        "formula_conversion": {
            "preferred_chain": "latex2mathml -> Microsoft XSLT or packaged MathML-to-OMML -> Word OMML",
            "latex2mathml_available": python_packages["latex2mathml"],
            "latex2mathml_data_available": latex2mathml_data_available,
            "packaged_mathml2omml_available": python_packages["math_ml2omml"],
            "mathml2omml_xsl": str(xsl) if xsl else None,
            "microsoft_xslt_available": bool(xsl),
            "degraded_fallback": "disabled unless ANSWER_BOOK_ALLOW_DEGRADED_OMML_FALLBACK=1",
            "preferred_chain_ready": bool(
                python_packages["latex2mathml"]
                and latex2mathml_data_available
                and python_packages["lxml"]
                and (python_packages["math_ml2omml"] or xsl)
            ),
        },
        "formula_input_conversion": {
            "preferred_chain": "Word OMML -> omml2mathml.xsl -> MathML",
            "omml2mathml_xsl": str(input_xsl) if input_xsl else None,
            "preferred_chain_ready": bool(python_packages["lxml"] and input_xsl),
            "fallback_chain": "visible OMML tokens with explicit structure-unavailable marker",
        },
        "microsoft_word": {
            "mac": word_mac,
            "windows": word_windows,
        },
        "document_tools": {
            "pdf_render_available": bool(
                word_mac.get("render_pdf_available")
                if platform.system() == "Darwin"
                else word_windows.get("render_pdf_available")
                if platform.system() == "Windows"
                else soffice
            ),
            "pdf_page_render_available": bool(python_packages["pypdfium2"] or shutil.which("pdftoppm")),
            "pdf_page_renderer": "pypdfium2" if python_packages["pypdfium2"] else ("pdftoppm" if shutil.which("pdftoppm") else "unavailable"),
            "pypdfium2_available": python_packages["pypdfium2"],
            "pdftoppm_available": bool(shutil.which("pdftoppm")),
            "project_fonts": project_font_diagnostics(),
        },
        "task_queue": {
            "backend": "huey_sqlite" if python_packages["huey"] else "unavailable",
            "huey_available": python_packages["huey"],
            "persistence": "sqlite",
            "payload_policy": "job_id_only",
        },
        "drawing_runtime": drawing_runtime,
        "providers": providers,
        "network": _check_network(providers),
    }
    env["repair_actions"] = environment_repair_actions(env)
    return env
