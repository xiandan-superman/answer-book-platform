#!/usr/bin/env python3
from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SPEC = ROOT / "build" / "answer_book_platform.spec"
DIST = ROOT / "dist"
WORK = ROOT / "build" / "pyinstaller-work"


def main() -> int:
    if sys.platform != "win32":
        print("Windows App 必须在 Windows 上构建。")
        return 2
    app_dir = DIST / "真题解析与生题平台"
    if app_dir.exists():
        shutil.rmtree(app_dir)
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
    archive_base = DIST / f"answer-book-platform-{version}-windows-x86_64"
    archive_path = Path(shutil.make_archive(str(archive_base), "zip", root_dir=app_dir))
    print(f"已生成：{app_dir}")
    print(f"已生成：{archive_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
