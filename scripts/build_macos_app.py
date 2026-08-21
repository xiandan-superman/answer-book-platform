#!/usr/bin/env python3
from __future__ import annotations

import platform
import shutil
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SPEC = ROOT / "build" / "answer_book_platform.spec"
DIST = ROOT / "dist"
WORK = ROOT / "build" / "pyinstaller-work"


def seed_builder_api_keys() -> None:
    """Keep the builder's existing keys outside the App without shipping them."""
    source = ROOT / "config" / "api_keys.json"
    target = Path.home() / "Library" / "Application Support" / "Answer Book Platform" / "config" / "api_keys.json"
    if not source.exists() or target.exists():
        return
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, target)
    try:
        target.chmod(0o600)
    except OSError:
        pass
    print(f"已迁移本机 API Key 配置：{target}")


def main() -> int:
    if sys.platform != "darwin":
        print("macOS .app 必须在 macOS 上构建。")
        return 2
    app_path = DIST / "真题解析与生题平台.app"
    if app_path.exists():
        shutil.rmtree(app_path)
    command = [
        sys.executable,
        "-m",
        "PyInstaller",
        "--noconfirm",
        "--clean",
        "--distpath",
        str(DIST),
        "--workpath",
        str(WORK),
        str(SPEC),
    ]
    result = subprocess.run(command, cwd=ROOT, check=False)
    if result.returncode:
        return result.returncode
    version = (ROOT / "APP_VERSION").read_text(encoding="utf-8").strip()
    architecture = "arm64" if platform.machine().lower() in {"arm64", "aarch64"} else "x86_64"
    dmg_path = DIST / f"answer-book-platform-{version}-macos-{architecture}.dmg"
    staging = ROOT / "build" / "dmg-staging"
    if staging.exists():
        shutil.rmtree(staging)
    staging.mkdir(parents=True)
    shutil.copytree(app_path, staging / app_path.name)
    (staging / "Applications").symlink_to("/Applications")
    dmg_path.unlink(missing_ok=True)
    dmg_result = subprocess.run(
        [
            "hdiutil",
            "create",
            "-volname",
            "真题解析与生题平台",
            "-srcfolder",
            str(staging),
            "-ov",
            "-format",
            "UDZO",
            str(dmg_path),
        ],
        cwd=ROOT,
        check=False,
    )
    shutil.rmtree(staging)
    if dmg_result.returncode:
        return dmg_result.returncode
    seed_builder_api_keys()
    print(f"已生成：{app_path}")
    print(f"已生成：{dmg_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
