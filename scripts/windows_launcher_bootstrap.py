#!/usr/bin/env python3
from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time
import urllib.request
from pathlib import Path
from typing import Any, Callable

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_LAUNCHER_PORT = 18876


def user_data_root() -> Path:
    override = os.environ.get("ANSWER_BOOK_DATA_DIR", "").strip()
    if override:
        return Path(override).expanduser().resolve()
    base = Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local")
    return base / "Answer Book Platform"


def bootstrap_log_path() -> Path:
    return user_data_root() / "runtime" / "launcher-bootstrap.log"


def launcher_ready(port: int) -> bool:
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=0.8) as response:
            return response.status == 200
    except Exception:
        return False


def launcher_command(python_executable: str, forwarded_args: list[str]) -> list[str]:
    return [python_executable, str(ROOT / "scripts" / "source_launcher_gui.py"), *forwarded_args]


def show_bootstrap_error(message: str) -> None:
    try:
        import ctypes

        ctypes.windll.user32.MessageBoxW(0, message, "真题解析与生题平台 · 启动失败", 0x10)
    except Exception:
        print(message, file=sys.stderr)


def supervise_launcher(
    forwarded_args: list[str],
    *,
    launcher_port: int,
    log_path: Path | None = None,
    health_check: Callable[[int], bool] = launcher_ready,
    popen_factory: Callable[..., Any] = subprocess.Popen,
    sleep: Callable[[float], None] = time.sleep,
    error_reporter: Callable[[str], None] = show_bootstrap_error,
) -> int:
    path = log_path or bootstrap_log_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    command = launcher_command(sys.executable, forwarded_args)
    try:
        with path.open("a", encoding="utf-8", errors="replace") as log:
            log.write(f"\n[{time.strftime('%Y-%m-%d %H:%M:%S')}] Windows launcher bootstrap\n")
            log.write(f"python={sys.executable}\nproject={ROOT}\n")
            log.flush()
            kwargs: dict[str, Any] = {
                "cwd": ROOT,
                "env": {
                    **os.environ,
                    "ANSWER_BOOK_LAUNCHER_BOOTSTRAP_LOG": str(path),
                    "PYTHONIOENCODING": "utf-8",
                    "PYTHONUTF8": "1",
                },
                "stdout": log,
                "stderr": subprocess.STDOUT,
            }
            if sys.platform.startswith("win"):
                kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
            process = popen_factory(command, **kwargs)
            while True:
                if health_check(launcher_port):
                    log.write("launcher_health=ready\n")
                    return 0
                return_code = process.poll()
                if return_code is not None:
                    log.write(f"launcher_exit={return_code}\n")
                    log.flush()
                    if return_code == 0:
                        return 0
                    error_reporter(
                        "启动器没有成功打开。\n\n"
                        f"诊断日志：{path}\n\n"
                        "请保留该日志，并重新解压完整程序包后再试。"
                    )
                    return int(return_code or 1)
                sleep(0.25)
    except Exception as exc:
        try:
            with path.open("a", encoding="utf-8", errors="replace") as log:
                log.write(f"bootstrap_error={type(exc).__name__}: {exc}\n")
        except OSError:
            pass
        error_reporter(f"Windows 启动入口无法运行。\n\n{exc}\n\n诊断日志：{path}")
        return 1


def main(argv: list[str] | None = None) -> int:
    forwarded = list(sys.argv[1:] if argv is None else argv)
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--launcher-port", type=int, default=DEFAULT_LAUNCHER_PORT)
    known, _unknown = parser.parse_known_args(forwarded)
    return supervise_launcher(forwarded, launcher_port=known.launcher_port)


if __name__ == "__main__":
    raise SystemExit(main())
