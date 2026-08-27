#!/usr/bin/env python3
from __future__ import annotations

import argparse
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CHANGELOG = ROOT / "CHANGELOG.md"


def extract_version_section(markdown: str, version: str) -> str:
    target = str(version or "").strip()
    if not target:
        raise ValueError("版本号不能为空")
    heading = re.compile(rf"^## \[{re.escape(target)}\](?:\s+-\s+.+)?\s*$")
    version_heading = re.compile(r"^## \[[^\]]+\](?:\s+-\s+.+)?\s*$")
    lines = markdown.splitlines()
    start = next((index for index, line in enumerate(lines) if heading.match(line)), None)
    if start is None:
        raise ValueError(f"未找到版本 {target} 的二级标题")
    end = next(
        (index for index in range(start + 1, len(lines)) if version_heading.match(lines[index])),
        len(lines),
    )
    section = "\n".join(lines[start:end]).strip()
    if len(section.splitlines()) < 3:
        raise ValueError(f"版本 {target} 的更新内容为空")
    return section + "\n"


def source_download_notice(version: str, repository: str) -> str:
    target = str(version or "").strip()
    repo = str(repository or "").strip()
    if not re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+(?:-[0-9A-Za-z.-]+)?", target):
        raise ValueError("源码下载提示的版本号无效")
    if not re.fullmatch(r"[0-9A-Za-z_.-]+/[0-9A-Za-z_.-]+", repo):
        raise ValueError("源码下载提示的仓库名无效")
    asset = f"answer-book-platform-{target}-source.zip"
    url = f"https://github.com/{repo}/releases/download/v{target}/{asset}"
    return (
        "> [!IMPORTANT]\n"
        f"> **Windows 和 macOS 用户请下载：[{asset}]({url})**\n"
        ">\n"
        "> 不要下载页面底部 GitHub 自动生成的 `Source code (zip)` / "
        "`Source code (tar.gz)`；它们只是发布仓库源码，不含平台启动器。\n\n"
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Extract one release section from CHANGELOG.md.")
    parser.add_argument("--version", required=True)
    parser.add_argument("--changelog", default=str(CHANGELOG))
    parser.add_argument("--output", required=True)
    parser.add_argument("--source-download-repository")
    args = parser.parse_args()
    source = Path(args.changelog).expanduser()
    output = Path(args.output).expanduser()
    notes = extract_version_section(source.read_text(encoding="utf-8"), args.version)
    if args.source_download_repository:
        notes = source_download_notice(args.version, args.source_download_repository) + notes
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(notes, encoding="utf-8")
    print(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
