from __future__ import annotations

import platform
import shutil
import socket
import subprocess
import sys
import tempfile
from importlib.util import find_spec
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from .drawing_code import run_drawing_code
from .paths import IS_FROZEN, PROJECT_ROOT, ensure_project_dirs
from .settings import list_providers
from .omml import find_mathml2omml_xsl


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
        "word_app_exists": shutil.os.path.exists(word_app),
        "osascript_exists": bool(shutil.which("osascript")),
        "soffice": soffice,
        "render_pdf_available": bool(shutil.os.path.exists(word_app) or soffice),
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
    code = "import win32com.client; win32com.client.Dispatch('Word.Application'); print('ok')"
    stderr = ""
    stdout = ""
    try:
        proc = subprocess.run([py, "-c", code], capture_output=True, text=True, timeout=10)
        ok = proc.returncode == 0
        stderr = (proc.stderr or "").strip()
        stdout = (proc.stdout or "").strip()
    except Exception as exc:
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
        except Exception as exc:
            item.update({"ok": False, "error": str(exc)})
        checked.append(item)
    return {
        "ok": any(item["ok"] for item in checked),
        "checked": checked,
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

    try:
        import resource  # type: ignore[import-not-found]

        resource_limits = "available"
    except ImportError:
        resource_limits = "not_available_on_this_platform"

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
    missing_core = [name for name in ("python-docx", "lxml", "latex2mathml", "Pillow", "matplotlib") if not packages.get(name)]
    if missing_core and not IS_FROZEN:
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
    if word_windows.get("applicable") and not word_windows.get("word_com_available") and not IS_FROZEN:
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
    if IS_FROZEN and action in {"install_python_dependencies", "install_windows_word_com"}:
        raise ValueError("桌面 App 的 Python 依赖已随程序打包，不能在运行时安装；请更新 App。")
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
    return {"ok": bool(result.get("ok")), "action": action, "result": result, "environment": check_environment()}


def check_environment() -> dict[str, Any]:
    ensure_project_dirs()
    providers = {name: cfg.redacted() for name, cfg in list_providers().items()}
    xsl = find_mathml2omml_xsl()
    word_mac = _check_word_mac()
    word_windows = _check_word_windows()
    drawing_runtime = _check_drawing_runtime()
    soffice = shutil.which("soffice") or shutil.which("libreoffice")
    python_packages = {
        "python-docx": bool(find_spec("docx")),
        "lxml": bool(find_spec("lxml")),
        "latex2mathml": bool(find_spec("latex2mathml")),
        "Pillow": bool(find_spec("PIL")),
        "matplotlib": bool(find_spec("matplotlib")),
        "pywin32": bool(find_spec("win32com")),
    }
    env = {
        "platform": platform.platform(),
        "python": platform.python_version(),
        "executables": {
            "python3": shutil.which("python3"),
            "pdftoppm": shutil.which("pdftoppm"),
            "soffice": shutil.which("soffice") or shutil.which("libreoffice"),
        },
        "python_packages": python_packages,
        "formula_conversion": {
            "preferred_chain": "latex2mathml -> mathml2omml.xsl -> Word OMML",
            "latex2mathml_available": python_packages["latex2mathml"],
            "mathml2omml_xsl": str(xsl) if xsl else None,
            "fallback_chain": "built-in minimal LaTeX parser",
            "preferred_chain_ready": bool(python_packages["latex2mathml"] and python_packages["lxml"] and xsl),
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
            "pdftoppm_available": bool(shutil.which("pdftoppm")),
        },
        "drawing_runtime": drawing_runtime,
        "providers": providers,
        "network": _check_network(providers),
    }
    env["repair_actions"] = environment_repair_actions(env)
    return env
