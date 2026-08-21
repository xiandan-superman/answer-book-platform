#!/usr/bin/env python3
from __future__ import annotations

import argparse
import os
import plistlib
import shutil
import subprocess
import sys
from pathlib import Path

from support_receiver import default_root, ensure_token

LABEL = "com.answerbook.support-receiver"


def _launch_agent_path() -> Path:
    return Path.home() / "Library" / "LaunchAgents" / f"{LABEL}.plist"


def _domain() -> str:
    return f"gui/{os.getuid()}"


def _launchctl(*arguments: str, check: bool = False) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["launchctl", *arguments], check=check, capture_output=True, text=True)


def install(*, upload_port: int, admin_port: int, quota_mib: int) -> Path:
    if sys.platform != "darwin":
        raise RuntimeError("该安装器只支持 macOS。")
    root = default_root().resolve()
    root.mkdir(parents=True, exist_ok=True)
    ensure_token(root / "receiver_token")
    installed_script = root / "support_receiver.py"
    shutil.copy2(Path(__file__).with_name("support_receiver.py"), installed_script)
    installed_script.chmod(0o700)

    plist_path = _launch_agent_path()
    plist_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = plist_path.with_suffix(".plist.tmp")
    payload = {
        "Label": LABEL,
        "ProgramArguments": [
            sys.executable,
            str(installed_script),
            "--root",
            str(root),
            "--upload-port",
            str(upload_port),
            "--admin-port",
            str(admin_port),
            "--quota-mib",
            str(quota_mib),
        ],
        "WorkingDirectory": str(root),
        "EnvironmentVariables": {
            "PATH": "/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin",
        },
        "RunAtLoad": True,
        "KeepAlive": True,
        "ThrottleInterval": 30,
        "StandardOutPath": str(root / "receiver.stdout.log"),
        "StandardErrorPath": str(root / "receiver.stderr.log"),
    }
    with temporary.open("wb") as handle:
        plistlib.dump(payload, handle, sort_keys=True)
    os.replace(temporary, plist_path)
    plist_path.chmod(0o644)

    _launchctl("bootout", _domain(), str(plist_path))
    _launchctl("bootstrap", _domain(), str(plist_path), check=True)
    _launchctl("enable", f"{_domain()}/{LABEL}", check=True)
    _launchctl("kickstart", "-k", f"{_domain()}/{LABEL}", check=True)
    return root


def uninstall() -> None:
    plist_path = _launch_agent_path()
    _launchctl("bootout", _domain(), str(plist_path))
    plist_path.unlink(missing_ok=True)


def main() -> int:
    parser = argparse.ArgumentParser(description="Install the Answer Book support receiver as a macOS LaunchAgent")
    parser.add_argument("--uninstall", action="store_true")
    parser.add_argument("--upload-port", type=int, default=8777)
    parser.add_argument("--admin-port", type=int, default=8778)
    parser.add_argument("--quota-mib", type=int, default=512)
    args = parser.parse_args()
    if args.uninstall:
        uninstall()
        print("问题反馈接收器已停止并取消开机自启；已有问题数据未删除。")
        return 0
    root = install(upload_port=args.upload_port, admin_port=args.admin_port, quota_mib=max(32, args.quota_mib))
    print(f"问题反馈接收器已安装并启动：{root}")
    print(f"本机管理页面：http://127.0.0.1:{args.admin_port}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
