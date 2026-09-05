#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import queue
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request
import venv
import webbrowser
import zipfile
from pathlib import Path
from typing import Any, Callable

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.dependency_profiles import (  # noqa: E402
    runtime_dependency_files,
    runtime_dependency_fingerprint,
    runtime_python_supported,
)

RUNTIME_ENV_NAME = "python-env-py311"
RESTART_EXIT_CODE = 75


class DependencyInstallError(RuntimeError):
    def __init__(self, message: str, *, component: str = "", hint: str = "") -> None:
        super().__init__(message)
        self.component = component
        self.hint = hint


class JsonProgressReporter:
    """Persist launcher progress without exposing command output or credentials."""

    def __init__(self, path: Path | None, *, run_id: str = "") -> None:
        self.path = path
        self.run_id = run_id

    def update(self, status: str, percent: int, message: str, **details: Any) -> None:
        if self.path is None:
            return
        payload = {
            "schema_version": "answer_book.startup_progress.v1",
            "status": status,
            "percent": max(0, min(100, int(percent))),
            "message": str(message)[:500],
            "updated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            "updated_at_epoch": time.time(),
            "run_id": self.run_id,
            **{key: value for key, value in details.items() if value is not None},
        }
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(".json.tmp")
        temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        temporary.replace(self.path)


class SourceUpdateProgress:
    """Persist offline update progress and show a small native status window."""

    def __init__(self, data_root: Path, *, enabled: bool) -> None:
        self.path = data_root / "runtime" / "update-progress.json"
        self.enabled = enabled
        self.window: Any = None
        self.stage_label: Any = None
        self.message_label: Any = None
        self.progress_bar: Any = None
        if not enabled:
            return
        try:
            import tkinter as tk
            from tkinter import ttk

            window = tk.Tk()
            window.title("真题解析与生题平台 · 安全更新")
            window.resizable(False, False)
            window.protocol("WM_DELETE_WINDOW", lambda: None)
            frame = ttk.Frame(window, padding=22)
            frame.grid()
            ttk.Label(frame, text="正在安全更新程序", font=("TkDefaultFont", 14, "bold")).grid(row=0, column=0, sticky="w")
            self.stage_label = ttk.Label(frame, text="准备更新")
            self.stage_label.grid(row=1, column=0, sticky="w", pady=(12, 4))
            self.progress_bar = ttk.Progressbar(frame, length=430, maximum=100, mode="determinate")
            self.progress_bar.grid(row=2, column=0, sticky="ew")
            self.message_label = ttk.Label(frame, text="请保持此窗口打开。", wraplength=430)
            self.message_label.grid(row=3, column=0, sticky="w", pady=(8, 0))
            ttk.Label(frame, text="程序目录不会被清空；API Key、教材、任务和输出保持不变。", foreground="#667085").grid(row=4, column=0, sticky="w", pady=(12, 0))
            window.update_idletasks()
            window.update()
            self.window = window
        except Exception:
            self.window = None

    def update(self, status: str, percent: int, message: str, **details: Any) -> None:
        if not self.enabled:
            return
        try:
            current = json.loads(self.path.read_text(encoding="utf-8")) if self.path.is_file() else {}
        except (OSError, json.JSONDecodeError):
            current = {}
        current.update({
            "schema_version": "answer_book.update_progress.v1",
            "ok": status != "failed",
            "status": status,
            "percent": max(0, min(100, int(percent))),
            "message": str(message)[:500],
            "updated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            **{key: value for key, value in details.items() if value is not None},
        })
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(".json.launcher.tmp")
        temporary.write_text(json.dumps(current, ensure_ascii=False, indent=2), encoding="utf-8")
        temporary.replace(self.path)
        print(f"[{percent}%] {message}", flush=True)
        if self.window is not None:
            try:
                self.stage_label.configure(text={
                    "extracting": "解压并检查更新包",
                    "backing_up": "备份当前程序",
                    "installing": "安装新版程序",
                    "verifying_install": "验证安装结果",
                    "checking_dependencies": "检查运行组件",
                    "creating_environment": "创建专用环境",
                    "dependencies_found": "发现待安装组件",
                    "resolving_dependencies": "解析组件版本",
                    "downloading_dependencies": "下载并准备组件",
                    "installing_dependencies": "安装运行组件",
                    "verifying_dependencies": "验证运行组件",
                    "dependencies_ready": "运行组件已就绪",
                    "starting": "启动新版程序",
                    "completed": "更新完成",
                    "failed": "更新失败，已保留旧版",
                }.get(status, "安全更新"))
                self.message_label.configure(text=message)
                self.progress_bar.configure(value=percent)
                self.window.update_idletasks()
                self.window.update()
            except Exception:
                self.window = None

    def close(self) -> None:
        if self.window is not None:
            try:
                self.window.destroy()
            except Exception:
                pass
            self.window = None


def combined_progress_reporter(
    startup_progress: JsonProgressReporter,
    update_progress: SourceUpdateProgress,
) -> Callable[..., None]:
    def report(status: str, percent: int, message: str, **details: Any) -> None:
        startup_progress.update(status, percent, message, **details)
        if update_progress.enabled:
            dependency_stage = status.endswith("dependencies") or status in {"dependencies_found", "dependencies_ready"}
            update_progress.update(status, 99 if dependency_stage else percent, message, **details)

    return report


def user_data_root() -> Path:
    override = os.environ.get("ANSWER_BOOK_DATA_DIR", "").strip()
    if override:
        return Path(override).expanduser().resolve()
    home = Path.home()
    if sys.platform == "darwin":
        return home / "Library" / "Application Support" / "Answer Book Platform"
    if sys.platform.startswith("win"):
        base = Path(os.environ.get("LOCALAPPDATA") or home / "AppData" / "Local")
        return base / "Answer Book Platform"
    return Path(os.environ.get("XDG_DATA_HOME") or home / ".local" / "share") / "answer-book-platform"


def runtime_python(env_dir: Path) -> Path:
    return env_dir / ("Scripts/python.exe" if sys.platform.startswith("win") else "bin/python")


def python_executable_supported(python: Path) -> bool:
    if not python.is_file():
        return False
    try:
        result = subprocess.run(
            [str(python), "-c", "import sys; raise SystemExit(0 if sys.version_info[:2] == (3, 11) else 1)"],
            capture_output=True,
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return result.returncode == 0


def quarantine_incompatible_runtime(env_dir: Path) -> Path:
    stamp = time.strftime("%Y%m%d-%H%M%S")
    candidate = env_dir.with_name(f"{env_dir.name}-incompatible-{stamp}")
    suffix = 1
    while candidate.exists():
        candidate = env_dir.with_name(f"{env_dir.name}-incompatible-{stamp}-{suffix}")
        suffix += 1
    env_dir.replace(candidate)
    return candidate


def ensure_runtime_pip(python: Path) -> None:
    probe = subprocess.run(
        [str(python), "-m", "pip", "--version"],
        capture_output=True,
        timeout=30,
    )
    if probe.returncode == 0:
        return
    repaired = subprocess.run(
        [str(python), "-m", "ensurepip", "--upgrade"],
        capture_output=True,
        timeout=180,
    )
    verified = subprocess.run(
        [str(python), "-m", "pip", "--version"],
        capture_output=True,
        timeout=30,
    )
    if repaired.returncode != 0 or verified.returncode != 0:
        raise RuntimeError("Python 3.11 运行环境缺少 pip，自动修复失败。请重新安装 Python 3.11 后再启动平台。")


def dependency_files(project_root: Path) -> list[Path]:
    return runtime_dependency_files(project_root, sys.version_info[:2], platform_name=sys.platform)


def dependency_fingerprint(project_root: Path) -> str:
    return runtime_dependency_fingerprint(project_root, sys.version_info[:2], platform_name=sys.platform)


def gui_subprocess_kwargs() -> dict[str, Any]:
    if sys.platform.startswith("win") and os.environ.get("ANSWER_BOOK_GUI_LAUNCHER") == "1":
        return {"creationflags": subprocess.CREATE_NO_WINDOW}
    return {}


def dependencies_healthy(python: Path) -> bool:
    probe = (
        "import docx,lxml,latex2mathml,PIL,pydantic,litellm,sympy,"
        "latex2sympy2_extended,math_verify,pypdfium2,bm25s,huey,matplotlib,numpy"
    )
    if sys.platform.startswith("win") or sys.platform == "darwin":
        probe += ",webview,pystray"
    result = subprocess.run([str(python), "-c", probe], capture_output=True, timeout=30)
    if result.returncode != 0:
        return False
    check = subprocess.run([str(python), "-m", "pip", "check"], capture_output=True, timeout=60)
    return check.returncode == 0


def declared_dependency_specs(project_root: Path) -> list[str]:
    specs: list[str] = []
    for path in dependency_files(project_root):
        if path.name.startswith("constraints-"):
            continue
        for raw_line in path.read_text(encoding="utf-8").splitlines():
            line = raw_line.split("#", 1)[0].strip()
            if line and not line.startswith("-"):
                specs.append(line)
    return specs


def pending_dependencies(python: Path, project_root: Path) -> list[str]:
    """Return unmet direct requirements using the managed interpreter's markers."""
    specs = declared_dependency_specs(project_root)
    if not specs:
        return []
    probe = """
import importlib.metadata, json, sys
try:
    from packaging.requirements import Requirement
except ImportError:
    from pip._vendor.packaging.requirements import Requirement
pending = []
for raw in json.loads(sys.argv[1]):
    try:
        requirement = Requirement(raw)
        if requirement.marker is not None and not requirement.marker.evaluate():
            continue
        try:
            installed = importlib.metadata.version(requirement.name)
        except importlib.metadata.PackageNotFoundError:
            installed = ""
        if not installed or (requirement.specifier and installed not in requirement.specifier):
            pending.append(requirement.name)
    except Exception:
        continue
print(json.dumps(pending))
"""
    try:
        result = subprocess.run(
            [str(python), "-c", probe, json.dumps(specs)],
            capture_output=True,
            text=True,
            timeout=30,
        )
        if result.returncode == 0:
            value = json.loads(result.stdout)
            if isinstance(value, list):
                return [str(item) for item in value if str(item).strip()]
    except (OSError, subprocess.SubprocessError, json.JSONDecodeError):
        pass
    return []


_PIP_COMPONENT_PATTERNS = (
    re.compile(r"^(?:Collecting|Building wheel for)\s+([A-Za-z0-9_.-]+)", re.IGNORECASE),
    re.compile(r"^Preparing metadata .*?\(([A-Za-z0-9_.-]+)\)", re.IGNORECASE),
)


def pip_component_from_line(line: str) -> str:
    clean = line.strip()
    for pattern in _PIP_COMPONENT_PATTERNS:
        match = pattern.search(clean)
        if match:
            return match.group(1)
    return ""


def dependency_failure_details(lines: list[str], component: str) -> tuple[str, str]:
    joined = "\n".join(lines).lower()
    label = component or "运行组件"
    if "no matching distribution found" in joined or "could not find a version" in joined:
        return f"找不到与当前系统兼容的 {label} 版本。", "请确认正在使用 Python 3.11，并保留日志后联系维护人员。"
    if any(token in joined for token in ("timed out", "timeout", "connection reset", "failed to establish a new connection")):
        return f"下载 {label} 时网络连接中断。", "请检查网络或代理设置，然后点击“重新尝试”。"
    if any(token in joined for token in ("permission denied", "access is denied", "winerror 5")):
        return f"安装 {label} 时没有写入权限。", "请关闭安全软件拦截或确认用户数据目录可写后重试。"
    if "no space left on device" in joined:
        return f"安装 {label} 时磁盘空间不足。", "请释放系统盘空间后点击“重新尝试”。"
    return f"{label} 未能安装完成。", "请点击“重新尝试”；若仍失败，请把启动日志提供给维护人员。"


def run_dependency_install(
    command: list[str],
    *,
    project_root: Path,
    pending: list[str],
    progress: Callable[..., Any] | None,
) -> tuple[int, list[str], str]:
    output_queue: queue.Queue[str | None] = queue.Queue()
    lines: list[str] = []
    current_component = pending[0] if pending else ""
    pending_positions = {name.lower().replace("_", "-"): index for index, name in enumerate(pending, 1)}
    current_index = 1 if pending else 0
    last_activity = time.monotonic()
    process = subprocess.Popen(
        command,
        cwd=project_root,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        bufsize=1,
        **gui_subprocess_kwargs(),
    )

    def read_output() -> None:
        assert process.stdout is not None
        for raw_line in process.stdout:
            output_queue.put(raw_line.rstrip())
        output_queue.put(None)

    threading.Thread(target=read_output, daemon=True).start()
    output_finished = False
    while process.poll() is None or not output_finished:
        try:
            line = output_queue.get(timeout=0.25)
        except queue.Empty:
            line = ""
        if line is None:
            output_finished = True
        elif line:
            print(line, flush=True)
            lines.append(line)
            lines[:] = lines[-80:]
            component = pip_component_from_line(line)
            if component:
                current_component = component
                normalized = component.lower().replace("_", "-")
                current_index = max(current_index, pending_positions.get(normalized, current_index))
            if line.lower().startswith("installing collected packages"):
                if progress:
                    progress(
                        "installing_dependencies",
                        78,
                        "下载已完成，正在安装运行组件。",
                        current_component=current_component,
                        current_index=len(pending),
                        completed_count=0,
                        total_count=len(pending),
                        progress_mode="indeterminate",
                    )
                continue
        if progress and not output_finished:
            total = len(pending)
            completed = max(0, current_index - 1) if total else 0
            percent = 28 + (int(42 * current_index / total) if total else 12)
            inactive_seconds = int(time.monotonic() - last_activity)
            message = (
                f"正在准备 {current_component}（第 {current_index}/{total} 项）"
                if current_component and total
                else "正在解析并下载所需运行组件。"
            )
            if inactive_seconds >= 45:
                message = f"仍在处理 {current_component or '运行组件'}，部分组件准备时间较长。"
            progress(
                "downloading_dependencies",
                min(percent, 72),
                message,
                current_component=current_component,
                current_index=current_index,
                completed_count=completed,
                total_count=total,
                inactive_seconds=inactive_seconds,
                progress_mode="indeterminate",
            )
        if output_finished and process.poll() is not None:
            break
    return int(process.returncode or 0), lines, current_component


def confirm_dependency_install() -> bool:
    message = "检测到新版本需要补充或更新 Python 依赖。是否现在安装？"
    if sys.platform.startswith("win") and os.environ.get("ANSWER_BOOK_GUI_LAUNCHER") == "1":
        try:
            from tkinter import messagebox

            return bool(messagebox.askyesno("真题解析与生题平台", message))
        except Exception:
            return False
    if sys.platform == "darwin" and shutil.which("osascript"):
        script = f'display dialog "{message}" buttons {{"取消", "安装"}} default button "安装" with title "真题解析与生题平台"'
        return subprocess.run(["osascript", "-e", script], capture_output=True).returncode == 0
    if sys.stdin.isatty():
        return input(f"{message} [Y/n] ").strip().lower() not in {"n", "no"}
    return False


def ensure_dependencies(
    project_root: Path,
    data_root: Path,
    *,
    approved: bool,
    progress: Callable[..., Any] | None = None,
) -> Path:
    if progress:
        progress("checking_dependencies", 6, "正在检查 Python 版本和运行组件。", progress_mode="determinate")
    if not runtime_python_supported():
        current = ".".join(str(part) for part in sys.version_info[:3])
        raise RuntimeError(f"平台运行环境必须使用 Python 3.11；当前为 Python {current}。其他 Python 版本可以保留，但不能用于启动平台。")
    # Use a new path instead of mutating the legacy Python 3.9 environment in
    # place.  Existing user data stays intact and rollback can still run it.
    env_dir = data_root / "runtime" / RUNTIME_ENV_NAME
    python = runtime_python(env_dir)
    if python.is_file() and not python_executable_supported(python):
        quarantined = quarantine_incompatible_runtime(env_dir)
        print(f"检测到非 Python 3.11 的旧运行环境，已保留到：{quarantined}", flush=True)
    first_install = not python.is_file()
    if first_install:
        if progress:
            progress("creating_environment", 10, "正在创建平台专用的 Python 3.11 环境。", progress_mode="indeterminate")
        env_dir.parent.mkdir(parents=True, exist_ok=True)
        venv.EnvBuilder(with_pip=True, clear=False).create(env_dir)
    ensure_runtime_pip(python)
    fingerprint = dependency_fingerprint(project_root)
    state_path = data_root / "runtime" / "dependencies-py311.json"
    try:
        state = json.loads(state_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        state = {}
    needs_install = first_install or state.get("fingerprint") != fingerprint or not dependencies_healthy(python)
    if not needs_install:
        if progress:
            progress("dependencies_ready", 92, "运行组件检查完成。", completed_count=0, total_count=0, progress_mode="determinate")
        return python
    pending = pending_dependencies(python, project_root)
    if progress:
        count_message = f"检测到 {len(pending)} 个组件需要安装或更新。" if pending else "检测到运行组件需要校验或修复。"
        progress(
            "dependencies_found",
            18,
            count_message,
            pending_components=pending,
            completed_count=0,
            total_count=len(pending),
            progress_mode="determinate",
        )
    if not first_install and not approved and not confirm_dependency_install():
        raise RuntimeError("依赖安装已取消，程序仍保留原版本和用户数据。")
    command = [str(python), "-m", "pip", "install", "--disable-pip-version-check"]
    for requirements in dependency_files(project_root):
        if requirements.name.startswith("constraints-"):
            command.extend(["-c", str(requirements)])
        else:
            command.extend(["-r", str(requirements)])
    print("首次启动需要准备运行环境，正在安装 Python 依赖，请保持此窗口打开……" if first_install else "正在补充或更新 Python 依赖，请保持此窗口打开……", flush=True)
    if progress:
        progress(
            "resolving_dependencies",
            24,
            "正在解析组件版本和系统兼容性。",
            pending_components=pending,
            completed_count=0,
            total_count=len(pending),
            progress_mode="indeterminate",
        )
    returncode, output_lines, current_component = run_dependency_install(
        command,
        project_root=project_root,
        pending=pending,
        progress=progress,
    )
    if progress:
        progress(
            "verifying_dependencies",
            90,
            "安装完成，正在验证运行环境。",
            current_component="",
            completed_count=len(pending),
            total_count=len(pending),
            progress_mode="determinate",
        )
    if returncode != 0 or not dependencies_healthy(python):
        message, hint = dependency_failure_details(output_lines, current_component)
        normalized_component = current_component.lower().replace("_", "-")
        pending_positions = {name.lower().replace("_", "-"): index for index, name in enumerate(pending, 1)}
        current_index = pending_positions.get(normalized_component, 0)
        install_started = any(line.lower().startswith("installing collected packages") for line in output_lines)
        failure_percent = 82 if install_started else 28 + (int(42 * current_index / len(pending)) if pending else 12)
        if progress:
            progress(
                "failed",
                failure_percent,
                message,
                failed_component=current_component,
                current_index=current_index,
                hint=hint,
                completed_count=0,
                total_count=len(pending),
                progress_mode="determinate",
            )
        raise DependencyInstallError(message, component=current_component, hint=hint)
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text(json.dumps({
        "fingerprint": fingerprint,
        "python": str(python),
        "updated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    print("Python 依赖准备完成。", flush=True)
    if progress:
        progress(
            "dependencies_ready",
            94,
            "运行组件已准备完成。",
            completed_count=len(pending),
            total_count=len(pending),
            progress_mode="determinate",
        )
    return python


def _safe_extract(archive: Path, destination: Path) -> Path:
    with zipfile.ZipFile(archive) as bundle:
        for member in bundle.infolist():
            target = (destination / member.filename).resolve()
            try:
                target.relative_to(destination.resolve())
            except ValueError as exc:
                raise RuntimeError("源码更新包包含不安全路径。") from exc
        bundle.extractall(destination)
    candidates = [path.parent for path in destination.rglob("scripts/start_platform.py")]
    roots = [path.parent for path in candidates if (path.parent / "app").is_dir() and (path.parent / "web").is_dir()]
    if len(roots) != 1:
        raise RuntimeError("源码更新包结构无效。")
    return roots[0]


def _copytree_with_progress(
    source: Path,
    destination: Path,
    *,
    progress: Callable[..., Any] | None,
    status: str,
    start_percent: int,
    end_percent: int,
    message: str,
    dirs_exist_ok: bool = False,
) -> Path:
    if not progress:
        return shutil.copytree(source, destination, dirs_exist_ok=dirs_exist_ok)
    files = [path for path in source.rglob("*") if path.is_file()]
    total_bytes = max(1, sum(path.stat().st_size for path in files))
    copied_bytes = 0
    last_reported = 0.0

    def copy_file(raw_source: str, raw_destination: str) -> str:
        nonlocal copied_bytes, last_reported
        result = shutil.copy2(raw_source, raw_destination)
        try:
            copied_bytes += Path(raw_source).stat().st_size
        except OSError:
            pass
        now = time.monotonic()
        if now - last_reported >= 0.1 or copied_bytes >= total_bytes:
            ratio = min(1.0, copied_bytes / total_bytes)
            percent = start_percent + round((end_percent - start_percent) * ratio)
            progress(
                status,
                percent,
                message,
                copied_bytes=copied_bytes,
                copy_total_bytes=total_bytes,
            )
            last_reported = now
        return result

    return shutil.copytree(
        source,
        destination,
        dirs_exist_ok=dirs_exist_ok,
        copy_function=copy_file,
    )


def apply_pending_source_update(
    project_root: Path,
    data_root: Path,
    progress: Callable[..., Any] | None = None,
) -> bool:
    plan_path = data_root / "runtime" / "pending-source-update.json"
    if not plan_path.is_file():
        return False
    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    archive = Path(str(plan.get("archive") or "")).resolve()
    if not archive.is_file():
        raise RuntimeError("待安装的源码更新包不存在。")
    runtime_dir = data_root / "runtime"
    runtime_dir.mkdir(parents=True, exist_ok=True)
    if progress:
        progress("extracting", 91, "正在解压并检查新版程序。", latest_version=plan.get("version"))
    with tempfile.TemporaryDirectory(prefix="answer-book-source-update-", dir=runtime_dir) as raw_temp:
        extracted_root = _safe_extract(archive, Path(raw_temp))
        version = str(plan.get("version") or "unknown").replace("/", "-")
        backup = data_root / "runtime" / "source-backups" / f"{version}-{time.strftime('%Y%m%d-%H%M%S')}"
        backup.parent.mkdir(parents=True, exist_ok=True)
        # Never move the live source directory away before the replacement is
        # complete.  That old approach exposed an empty program folder while a
        # cross-volume copy was running; closing the launcher during that gap
        # could strand the user with only the hidden backup.  Prepare the new
        # tree beside the installation first, then retain a full backup while
        # overlaying the stopped source tree in place.  An interrupted overlay
        # keeps both the launcher and pending plan, so the next start can retry.
        project_root.parent.mkdir(parents=True, exist_ok=True)
        staging = Path(tempfile.mkdtemp(
            prefix=f".{project_root.name}-update-{version}-",
            dir=project_root.parent,
        ))
        staging.rmdir()
        try:
            _copytree_with_progress(
                extracted_root,
                staging,
                progress=progress,
                status="extracting",
                start_percent=91,
                end_percent=93,
                message="正在完整准备新版程序，当前版本保持不变。",
            )
            if not (staging / "scripts" / "start_platform.py").is_file():
                raise RuntimeError("新源码缺少启动入口。")
            if not (staging / "start_platform_windows.bat").is_file():
                raise RuntimeError("新源码缺少 Windows 启动入口。")
            if progress:
                progress("backing_up", 94, "正在保留当前版本备份，程序目录保持原位。")
            _copytree_with_progress(
                project_root,
                backup,
                progress=progress,
                status="backing_up",
                start_percent=94,
                end_percent=96,
                message="正在保留当前版本备份，程序目录保持原位。",
            )
            if not (backup / "scripts" / "start_platform.py").is_file():
                raise RuntimeError("旧源码备份校验失败。")
            recovery_path = data_root / "runtime" / "source-update-recovery.json"
            recovery_path.write_text(json.dumps({
                "schema_version": "answer_book.source_update_recovery.v1",
                "backup": str(backup),
                "version": plan.get("version"),
                "created_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            }, ensure_ascii=False, indent=2), encoding="utf-8")
            try:
                if progress:
                    progress("installing", 97, "正在把已校验的新版本覆盖到程序目录。", backup_available=True)
                _copytree_with_progress(
                    staging,
                    project_root,
                    progress=progress,
                    status="installing",
                    start_percent=97,
                    end_percent=98,
                    message="正在安装已校验的新版本。",
                    dirs_exist_ok=True,
                )
                if not (project_root / "scripts" / "start_platform.py").is_file():
                    raise RuntimeError("新源码缺少启动入口。")
            except Exception:
                # Restore by overlay as well: never remove the user's visible
                # program directory, even on a failed replacement.
                shutil.copytree(backup, project_root, dirs_exist_ok=True)
                raise
        finally:
            shutil.rmtree(staging, ignore_errors=True)
    plan_path.unlink(missing_ok=True)
    if progress:
        progress("verifying_install", 98, "程序文件更新完成，正在检查运行环境。", latest_version=plan.get("version"))
    return True


def quarantine_failed_update(data_root: Path) -> Path | None:
    plan = data_root / "runtime" / "pending-source-update.json"
    if not plan.is_file():
        return None
    failed = plan.with_name(f"failed-source-update-{time.strftime('%Y%m%d-%H%M%S')}.json")
    plan.replace(failed)
    return failed


def restore_update_backup(
    project_root: Path,
    data_root: Path,
    *,
    expected_version: str = "",
) -> bool:
    recovery_path = data_root / "runtime" / "source-update-recovery.json"
    try:
        recovery = json.loads(recovery_path.read_text(encoding="utf-8"))
        if expected_version and str(recovery.get("version") or "") != expected_version:
            return False
        backup = Path(str(recovery.get("backup") or "")).resolve()
        backup.relative_to((data_root / "runtime" / "source-backups").resolve())
    except (OSError, json.JSONDecodeError, ValueError):
        return False
    if not (backup / "scripts" / "start_platform.py").is_file():
        return False
    shutil.copytree(backup, project_root, dirs_exist_ok=True)
    return (project_root / "scripts" / "start_platform.py").is_file()


def server_ready(url: str) -> bool:
    try:
        with urllib.request.urlopen(f"{url}/api/version", timeout=1.5) as response:
            return response.status == 200
    except Exception:
        return False


def local_service_url(host: str, port: int) -> str:
    """Return a browser/health URL even when the server listens on all interfaces."""
    browser_host = "127.0.0.1" if host in {"0.0.0.0", "::"} else host
    return f"http://{browser_host}:{port}"


def open_browser(url: str) -> bool:
    try:
        if webbrowser.open(url):
            return True
    except Exception:
        pass
    try:
        if sys.platform == "darwin":
            subprocess.Popen(["open", url], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            return True
        if sys.platform.startswith("win"):
            startfile = getattr(os, "startfile", None)
            if startfile is not None:
                startfile(url)
                return True
        subprocess.Popen(["xdg-open", url], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return True
    except OSError:
        return False


def wait_until_ready_and_open(
    process: subprocess.Popen,
    url: str,
    *,
    poll_seconds: float = 0.5,
    open_page: bool = True,
    on_ready: Callable[[], Any] | None = None,
) -> bool:
    """Keep checking until the service is ready or exits; open the page exactly once."""
    print(f"正在启动本地服务，准备完成后会自动打开网页：{url}", flush=True)
    while process.poll() is None:
        if server_ready(url):
            if on_ready:
                on_ready()
            if not open_page:
                print("新版服务已启动，原网页会自动重新连接。", flush=True)
                return False
            opened = open_browser(url)
            if opened:
                print("网页已打开。使用平台期间请保持此窗口运行。", flush=True)
            else:
                print(f"服务已启动，但系统未能自动打开浏览器，请手动访问：{url}", flush=True)
            return opened
        time.sleep(poll_seconds)
    return False


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", default=8766, type=int)
    parser.add_argument("--project-root", default=str(Path(__file__).resolve().parents[1]))
    args = parser.parse_args()
    if not runtime_python_supported():
        print("平台必须使用 Python 3.11。其他 Python 版本可以保留，但不能用于启动平台。", file=sys.stderr)
        return 2
    project_root = Path(args.project_root).resolve()
    data_root = user_data_root().resolve()
    data_root.mkdir(parents=True, exist_ok=True)
    url = local_service_url(args.host, args.port)
    if server_ready(url):
        open_browser(url)
        return 0
    plan_path = data_root / "runtime" / "pending-source-update.json"
    update_progress = SourceUpdateProgress(data_root, enabled=plan_path.is_file())
    startup_path_value = os.environ.get("ANSWER_BOOK_STARTUP_PROGRESS_PATH", "").strip()
    startup_progress = JsonProgressReporter(
        Path(startup_path_value) if startup_path_value else None,
        run_id=os.environ.get("ANSWER_BOOK_STARTUP_RUN_ID", "").strip(),
    )

    report_progress = combined_progress_reporter(startup_progress, update_progress)

    update_failed = False
    browser_opened = False
    try:
        planned_version = str(json.loads(plan_path.read_text(encoding="utf-8")).get("version") or "")
    except (OSError, json.JSONDecodeError):
        planned_version = ""
    try:
        approved = apply_pending_source_update(project_root, data_root, report_progress)
    except Exception as exc:
        restored = restore_update_backup(project_root, data_root, expected_version=planned_version)
        intact = (project_root / "scripts" / "start_platform.py").is_file() and (project_root / "start_platform_windows.bat").is_file()
        failed_plan = quarantine_failed_update(data_root)
        report_progress(
            "failed",
            100,
            "更新未完成，已保留或恢复原版本，可以继续使用。请回到网页重新检查更新。",
            error="源码更新安装失败。",
            error_type=exc.__class__.__name__,
            rollback_succeeded=restored or intact,
            failed_plan_saved=bool(failed_plan),
        )
        print(f"程序更新未完成：{exc}", file=sys.stderr, flush=True)
        approved = False
        update_failed = True
    while True:
        if approved:
            report_progress("checking_dependencies", 6, "正在核对新版运行组件。", progress_mode="determinate")
        try:
            python = ensure_dependencies(
                project_root,
                data_root,
                approved=approved,
                progress=report_progress,
            )
        except Exception as exc:
            if not isinstance(exc, DependencyInstallError):
                report_progress(
                    "failed",
                    95,
                    str(exc),
                    hint="请点击“重新尝试”；若仍失败，请把启动日志提供给维护人员。",
                    progress_mode="determinate",
                )
            if not update_progress.enabled or update_failed:
                raise
            restored = restore_update_backup(project_root, data_root, expected_version=planned_version)
            update_failed = True
            report_progress(
                "failed",
                100,
                "新版运行环境准备失败，已恢复原版本。平台将使用原版本重新启动。" if restored else "新版运行环境准备失败，请重新双击启动文件并查看错误提示。",
                error="新版运行依赖准备失败。",
                error_type=exc.__class__.__name__,
                rollback_succeeded=restored,
            )
            if not restored:
                raise
            python = ensure_dependencies(project_root, data_root, approved=False)
        approved = False
        env = os.environ.copy()
        env["ANSWER_BOOK_DATA_DIR"] = str(data_root)
        env["ANSWER_BOOK_LAUNCHED_BY_SUPERVISOR"] = "1"
        process = subprocess.Popen(
            [str(python), str(project_root / "scripts" / "start_platform.py"), "--host", args.host, "--port", str(args.port)],
            cwd=project_root,
            env=env,
            **gui_subprocess_kwargs(),
        )
        startup_progress.update(
            "starting",
            97,
            "运行组件已就绪，正在启动平台服务。",
            completed_count=0,
            total_count=0,
            progress_mode="determinate",
        )
        if update_progress.enabled and not update_failed:
            update_progress.update("starting", 99, "新版程序正在启动，完成后原网页会自动恢复。")

        def mark_ready(
            reporter: SourceUpdateProgress = update_progress,
            failed: bool = update_failed,
        ) -> None:
            startup_progress.update(
                "completed",
                100,
                "平台已启动。",
                completed_count=0,
                total_count=0,
                progress_mode="determinate",
            )
            if not reporter.enabled:
                return
            if failed:
                reporter.update("failed", 100, "更新失败后已恢复原版本，平台可以继续使用。", rollback_succeeded=True)
            else:
                reporter.update("completed", 100, "新版已启动，更新完成。", restart_required=False)
            reporter.close()

        opened = wait_until_ready_and_open(
            process,
            url,
            open_page=not browser_opened,
            on_ready=mark_ready,
        )
        browser_opened = browser_opened or opened
        code = process.wait()
        if code != RESTART_EXIT_CODE:
            return code
        update_progress = SourceUpdateProgress(data_root, enabled=plan_path.is_file())
        report_progress = combined_progress_reporter(startup_progress, update_progress)

        update_failed = False
        try:
            planned_version = str(json.loads(plan_path.read_text(encoding="utf-8")).get("version") or "")
        except (OSError, json.JSONDecodeError):
            planned_version = ""
        try:
            approved = apply_pending_source_update(project_root, data_root, report_progress)
        except Exception as exc:
            restored = restore_update_backup(project_root, data_root, expected_version=planned_version)
            intact = (project_root / "scripts" / "start_platform.py").is_file() and (project_root / "start_platform_windows.bat").is_file()
            failed_plan = quarantine_failed_update(data_root)
            report_progress(
                "failed",
                100,
                "更新未完成，已恢复原版本并重新启动。请稍后回到网页重试。",
                error="源码更新安装失败。",
                error_type=exc.__class__.__name__,
                rollback_succeeded=restored or intact,
                failed_plan_saved=bool(failed_plan),
            )
            print(f"程序更新未完成：{exc}", file=sys.stderr, flush=True)
            approved = False
            update_failed = True


if __name__ == "__main__":
    raise SystemExit(main())
