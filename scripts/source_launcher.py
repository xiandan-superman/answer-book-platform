#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
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
                    "dependencies": "准备运行依赖",
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
        "import docx,lxml,latex2mathml,PIL,pydantic,instructor,litellm,sympy,"
        "latex2sympy2_extended,math_verify,pypdfium2,bm25s,huey,matplotlib,numpy"
    )
    if sys.platform.startswith("win") or sys.platform == "darwin":
        probe += ",webview,pystray"
    result = subprocess.run([str(python), "-c", probe], capture_output=True, timeout=30)
    if result.returncode != 0:
        return False
    check = subprocess.run([str(python), "-m", "pip", "check"], capture_output=True, timeout=60)
    return check.returncode == 0


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
        env_dir.parent.mkdir(parents=True, exist_ok=True)
        venv.EnvBuilder(with_pip=True, clear=False).create(env_dir)
    fingerprint = dependency_fingerprint(project_root)
    state_path = data_root / "runtime" / "dependencies-py311.json"
    try:
        state = json.loads(state_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        state = {}
    needs_install = first_install or state.get("fingerprint") != fingerprint or not dependencies_healthy(python)
    if not needs_install:
        return python
    if not first_install and not approved and not confirm_dependency_install():
        raise RuntimeError("依赖安装已取消，程序仍保留原版本和用户数据。")
    command = [str(python), "-m", "pip", "install", "--disable-pip-version-check"]
    for requirements in dependency_files(project_root):
        if requirements.name.startswith("constraints-"):
            command.extend(["-c", str(requirements)])
        else:
            command.extend(["-r", str(requirements)])
    print("首次启动需要准备运行环境，正在安装 Python 依赖，请保持此窗口打开……" if first_install else "正在补充或更新 Python 依赖，请保持此窗口打开……", flush=True)
    process = subprocess.Popen(command, cwd=project_root, **gui_subprocess_kwargs())
    while process.poll() is None:
        if progress:
            progress("dependencies", 99, "正在安装新版所需依赖，请保持更新窗口打开。")
        time.sleep(0.25)
    if process.returncode != 0 or not dependencies_healthy(python):
        raise RuntimeError("Python 依赖安装失败，请检查网络后重新双击启动程序。")
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text(json.dumps({
        "fingerprint": fingerprint,
        "python": str(python),
        "updated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    print("Python 依赖准备完成。", flush=True)
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
    update_failed = False
    browser_opened = False
    try:
        planned_version = str(json.loads(plan_path.read_text(encoding="utf-8")).get("version") or "")
    except (OSError, json.JSONDecodeError):
        planned_version = ""
    try:
        approved = apply_pending_source_update(project_root, data_root, update_progress.update)
    except Exception as exc:
        restored = restore_update_backup(project_root, data_root, expected_version=planned_version)
        intact = (project_root / "scripts" / "start_platform.py").is_file() and (project_root / "start_platform_windows.bat").is_file()
        failed_plan = quarantine_failed_update(data_root)
        update_progress.update(
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
            update_progress.update("dependencies", 99, "正在核对新版运行依赖。")
        try:
            python = ensure_dependencies(
                project_root,
                data_root,
                approved=approved,
                progress=update_progress.update if approved else None,
            )
        except Exception as exc:
            if not update_progress.enabled or update_failed:
                raise
            restored = restore_update_backup(project_root, data_root, expected_version=planned_version)
            update_failed = True
            update_progress.update(
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
        if update_progress.enabled and not update_failed:
            update_progress.update("starting", 99, "新版程序正在启动，完成后原网页会自动恢复。")

        def mark_ready(
            reporter: SourceUpdateProgress = update_progress,
            failed: bool = update_failed,
        ) -> None:
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
        update_failed = False
        try:
            planned_version = str(json.loads(plan_path.read_text(encoding="utf-8")).get("version") or "")
        except (OSError, json.JSONDecodeError):
            planned_version = ""
        try:
            approved = apply_pending_source_update(project_root, data_root, update_progress.update)
        except Exception as exc:
            restored = restore_update_backup(project_root, data_root, expected_version=planned_version)
            intact = (project_root / "scripts" / "start_platform.py").is_file() and (project_root / "start_platform_windows.bat").is_file()
            failed_plan = quarantine_failed_update(data_root)
            update_progress.update(
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
