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

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.dependency_profiles import runtime_dependency_files, runtime_dependency_fingerprint  # noqa: E402

MIN_PYTHON = (3, 9)
RESTART_EXIT_CODE = 75


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


def dependency_files(project_root: Path) -> list[Path]:
    return runtime_dependency_files(project_root, sys.version_info[:2], platform_name=sys.platform)


def dependency_fingerprint(project_root: Path) -> str:
    return runtime_dependency_fingerprint(project_root, sys.version_info[:2], platform_name=sys.platform)


def dependencies_healthy(python: Path) -> bool:
    probe = "import docx,lxml,latex2mathml,PIL,pydantic,pypdfium2,bm25s,huey,matplotlib"
    result = subprocess.run([str(python), "-c", probe], capture_output=True, timeout=30)
    if result.returncode != 0:
        return False
    check = subprocess.run([str(python), "-m", "pip", "check"], capture_output=True, timeout=60)
    return check.returncode == 0


def confirm_dependency_install() -> bool:
    message = "检测到新版本需要补充或更新 Python 依赖。是否现在安装？"
    if sys.platform == "darwin" and shutil.which("osascript"):
        script = f'display dialog "{message}" buttons {{"取消", "安装"}} default button "安装" with title "真题解析与生题平台"'
        return subprocess.run(["osascript", "-e", script], capture_output=True).returncode == 0
    if sys.stdin.isatty():
        return input(f"{message} [Y/n] ").strip().lower() not in {"n", "no"}
    return False


def ensure_dependencies(project_root: Path, data_root: Path, *, approved: bool) -> Path:
    env_dir = data_root / "runtime" / "python-env"
    python = runtime_python(env_dir)
    first_install = not python.is_file()
    if first_install:
        env_dir.parent.mkdir(parents=True, exist_ok=True)
        venv.EnvBuilder(with_pip=True, clear=False).create(env_dir)
    fingerprint = dependency_fingerprint(project_root)
    state_path = data_root / "runtime" / "dependencies.json"
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
    result = subprocess.run(command, cwd=project_root)
    if result.returncode != 0 or not dependencies_healthy(python):
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


def apply_pending_source_update(project_root: Path, data_root: Path) -> bool:
    plan_path = data_root / "runtime" / "pending-source-update.json"
    if not plan_path.is_file():
        return False
    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    archive = Path(str(plan.get("archive") or "")).resolve()
    if not archive.is_file():
        raise RuntimeError("待安装的源码更新包不存在。")
    runtime_dir = data_root / "runtime"
    runtime_dir.mkdir(parents=True, exist_ok=True)
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
            shutil.copytree(extracted_root, staging)
            if not (staging / "scripts" / "start_platform.py").is_file():
                raise RuntimeError("新源码缺少启动入口。")
            if not (staging / "start_platform_windows.bat").is_file():
                raise RuntimeError("新源码缺少 Windows 启动入口。")

            shutil.copytree(project_root, backup)
            if not (backup / "scripts" / "start_platform.py").is_file():
                raise RuntimeError("旧源码备份校验失败。")
            try:
                shutil.copytree(staging, project_root, dirs_exist_ok=True)
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
    return True


def server_ready(url: str) -> bool:
    try:
        with urllib.request.urlopen(f"{url}/api/version", timeout=1.5) as response:
            return response.status == 200
    except Exception:
        return False


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


def wait_until_ready_and_open(process: subprocess.Popen, url: str, *, poll_seconds: float = 0.5) -> bool:
    """Keep checking until the service is ready or exits; open the page exactly once."""
    print(f"正在启动本地服务，准备完成后会自动打开网页：{url}", flush=True)
    while process.poll() is None:
        if server_ready(url):
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
    if sys.version_info < MIN_PYTHON:
        print("需要 Python 3.9 或更高版本。", file=sys.stderr)
        return 2
    project_root = Path(args.project_root).resolve()
    data_root = user_data_root().resolve()
    data_root.mkdir(parents=True, exist_ok=True)
    approved = apply_pending_source_update(project_root, data_root)
    url = f"http://{args.host}:{args.port}"
    if server_ready(url):
        open_browser(url)
        return 0
    while True:
        python = ensure_dependencies(project_root, data_root, approved=approved)
        approved = False
        env = os.environ.copy()
        env["ANSWER_BOOK_DATA_DIR"] = str(data_root)
        env["ANSWER_BOOK_LAUNCHED_BY_SUPERVISOR"] = "1"
        process = subprocess.Popen(
            [str(python), str(project_root / "scripts" / "start_platform.py"), "--host", args.host, "--port", str(args.port)],
            cwd=project_root,
            env=env,
        )
        wait_until_ready_and_open(process, url)
        code = process.wait()
        if code != RESTART_EXIT_CODE:
            return code
        approved = apply_pending_source_update(project_root, data_root)


if __name__ == "__main__":
    raise SystemExit(main())
