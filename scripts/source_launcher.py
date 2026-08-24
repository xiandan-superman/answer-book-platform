#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
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
    rows = [project_root / "requirements.txt"]
    if sys.platform.startswith("win"):
        rows.append(project_root / "requirements-windows.txt")
    return [path for path in rows if path.is_file()]


def dependency_fingerprint(project_root: Path) -> str:
    digest = hashlib.sha256()
    for name in ("requirements.txt", "requirements-windows.txt"):
        path = project_root / name
        if path.is_file():
            digest.update(path.name.encode("utf-8"))
            digest.update(path.read_bytes())
    return digest.hexdigest()


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
        command.extend(["-r", str(requirements)])
    result = subprocess.run(command, cwd=project_root)
    if result.returncode != 0 or not dependencies_healthy(python):
        raise RuntimeError("Python 依赖安装失败，请检查网络后重新双击启动程序。")
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text(json.dumps({
        "fingerprint": fingerprint,
        "python": str(python),
        "updated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
    }, ensure_ascii=False, indent=2), encoding="utf-8")
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
        previous_cwd = Path.cwd()
        os.chdir(runtime_dir)
        try:
            shutil.move(str(project_root), str(backup))
            try:
                shutil.copytree(extracted_root, project_root)
                if not (project_root / "scripts" / "start_platform.py").is_file():
                    raise RuntimeError("新源码缺少启动入口。")
            except Exception:
                shutil.rmtree(project_root, ignore_errors=True)
                shutil.move(str(backup), str(project_root))
                raise
        finally:
            if previous_cwd.exists():
                os.chdir(previous_cwd)
    plan_path.unlink(missing_ok=True)
    return True


def server_ready(url: str) -> bool:
    try:
        with urllib.request.urlopen(f"{url}/api/version", timeout=1.5) as response:
            return response.status == 200
    except Exception:
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
        webbrowser.open(url)
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
        for _ in range(120):
            if server_ready(url):
                webbrowser.open(url)
                break
            if process.poll() is not None:
                break
            time.sleep(0.25)
        code = process.wait()
        if code != RESTART_EXIT_CODE:
            return code
        approved = apply_pending_source_update(project_root, data_root)


if __name__ == "__main__":
    raise SystemExit(main())
