#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import re
import shlex
import shutil
import sqlite3
import subprocess
import tempfile
import zipfile
from pathlib import Path, PurePosixPath
from typing import Any


CLOUD_CONFIG_PATH = Path(__file__).resolve().parents[1] / "config" / "support_cloud.json"
REPORT_ID_RE = re.compile(r"^AB-[A-Z0-9-]{8,80}$")

try:
    from .support_receiver import (
        MAX_MEMBER_BYTES,
        MAX_UNCOMPRESSED_BYTES,
        Inbox,
        default_root,
        display_local_time,
        issue_display_summary,
        issue_manifest,
    )
except ImportError:
    from support_receiver import (
        MAX_MEMBER_BYTES,
        MAX_UNCOMPRESSED_BYTES,
        Inbox,
        default_root,
        display_local_time,
        issue_display_summary,
        issue_manifest,
    )


def _safe_member(name: str) -> PurePosixPath | None:
    path = PurePosixPath(name)
    if path.is_absolute() or ".." in path.parts or not path.parts:
        return None
    if name.startswith("attachments/"):
        return path
    if len(path.parts) == 1 and path.suffix.lower() == ".json":
        return path
    return None


def _remove_generated_path(path: Path) -> None:
    if path.is_symlink() or (path.exists() and not path.is_dir()):
        path.unlink(missing_ok=True)
    elif path.is_dir():
        shutil.rmtree(path)


def _default_case_directory(fingerprint: Any) -> Path:
    triage_root = Path(tempfile.gettempdir()) / "answer-book-support-triage"
    triage_root.mkdir(mode=0o700, parents=True, exist_ok=True)
    safe_name = "".join(character for character in str(fingerprint) if character.isalnum() or character in "-_")[:80]
    case_dir = triage_root / (safe_name or "report")
    _remove_generated_path(case_dir)
    case_dir.mkdir(mode=0o700)

    other_cases = sorted(
        (path for path in triage_root.iterdir() if path != case_dir),
        key=lambda path: path.lstat().st_mtime,
        reverse=True,
    )
    for stale in other_cases[4:]:
        _remove_generated_path(stale)
    return case_dir


def inspect_report(report_id: str, *, root: Path, destination: Path | None = None) -> dict[str, Any]:
    inbox = Inbox(root.resolve())
    row = inbox.issue_by_report_id(report_id)
    if not row:
        raise LookupError(f"没有找到反馈编号：{report_id}")

    manifest = issue_manifest(row)
    bundle = Path(str(row.get("bundle_path") or ""))
    case_dir = destination.resolve() if destination else _default_case_directory(row["fingerprint"])
    case_dir.mkdir(parents=True, exist_ok=True)
    extracted: list[str] = []
    skipped: list[str] = []
    total = 0

    if bundle.is_file():
        with zipfile.ZipFile(bundle) as archive:
            for info in archive.infolist():
                safe_path = _safe_member(info.filename)
                if info.is_dir() or safe_path is None:
                    continue
                if info.file_size > MAX_MEMBER_BYTES or total + info.file_size > MAX_UNCOMPRESSED_BYTES:
                    skipped.append(info.filename)
                    continue
                target = case_dir.joinpath(*safe_path.parts)
                target.parent.mkdir(parents=True, exist_ok=True)
                with archive.open(info) as source, target.open("wb") as output:
                    shutil.copyfileobj(source, output, length=64 * 1024)
                total += info.file_size
                extracted.append(str(target))

    context = manifest.get("context") if isinstance(manifest.get("context"), dict) else {}
    return {
        "report": {
            "requested_report_id": report_id,
            "report_id": row["report_id"],
            "fingerprint": row["fingerprint"],
            "status": row["status"],
            "summary": issue_display_summary(row),
            "last_seen_local": display_local_time(row["last_seen"]),
            "occurrence_count": row["occurrence_count"],
            "bundle_available": bundle.is_file(),
        },
        "task": {
            key: context.get(key)
            for key in (
                "task_id", "task_kind", "task_title", "task_model", "task_model_label",
                "task_status", "task_stage", "operation", "practice_batch_id", "report_group_id",
            )
        },
        "manifest": manifest,
        "case_directory": str(case_dir),
        "diagnostic_files": extracted,
        "skipped_files": skipped,
        "instruction": (
            "依次读取 manifest.json、user_feedback.json（如有）、diagnostic_coverage.json、task_lifecycle.json、"
            "runtime_error_context.json、backend_error_traces.json、model_call_summary.json、"
            "related_content.json、failure_context.json（如有）、task_failure_diagnostic.json（如有）、"
            "related_history_content.json（如有）和 model_diagnostics.json；先检查 "
            "missing_expected_evidence，再结合任务输入、"
            "门禁命中证据、模型上下文、阶段日志与错误给出根因。"
        ),
    }


def _cloud_config() -> dict[str, str]:
    value: dict[str, Any] = {}
    try:
        loaded = json.loads(CLOUD_CONFIG_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        loaded = {}
    if isinstance(loaded, dict):
        value.update(loaded)
    return {
        "ssh_host": str(os.environ.get("ANSWER_BOOK_SUPPORT_SSH_HOST") or value.get("ssh_host") or "").strip(),
        "ssh_key": str(os.environ.get("ANSWER_BOOK_SUPPORT_SSH_KEY") or value.get("ssh_key") or "").strip(),
        "remote_root": str(value.get("remote_root") or "/var/lib/answer-book-support").strip(),
        "remote_script": str(value.get("remote_script") or "/opt/answer-book-support/inspect_support_report.py").strip(),
    }


def _remote_ssh_arguments(config: dict[str, str]) -> list[str]:
    arguments = ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10"]
    key = config.get("ssh_key", "")
    if key:
        arguments.extend(("-i", str(Path(key).expanduser()), "-o", "IdentitiesOnly=yes"))
    arguments.append(config["ssh_host"])
    return arguments


def inspect_cloud_report(report_id: str, *, destination: Path | None = None) -> dict[str, Any]:
    config = _cloud_config()
    if not config["ssh_host"]:
        raise LookupError(f"没有找到反馈编号：{report_id}")
    if not REPORT_ID_RE.fullmatch(report_id):
        raise LookupError(f"反馈编号格式无效：{report_id}")
    remote_command = " ".join(
        shlex.quote(part)
        for part in (
            "python3",
            config["remote_script"],
            report_id,
            "--root",
            config["remote_root"],
        )
    )
    completed = subprocess.run(
        [*_remote_ssh_arguments(config), remote_command],
        check=True,
        capture_output=True,
        text=True,
        timeout=30,
    )
    result = json.loads(completed.stdout)
    if not isinstance(result, dict) or not result.get("ok"):
        raise LookupError(str(result.get("error") if isinstance(result, dict) else "云端反馈读取失败"))
    report = result.get("report") if isinstance(result.get("report"), dict) else {}
    remote_case = PurePosixPath(str(result.get("case_directory") or ""))
    allowed_root = PurePosixPath("/tmp/answer-book-support-triage")
    if not remote_case.is_absolute() or remote_case == allowed_root or allowed_root not in remote_case.parents:
        raise OSError("云端反馈导出目录不安全")
    case_dir = destination.resolve() if destination else _default_case_directory(report.get("fingerprint") or report_id)
    case_dir.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [
            "scp",
            "-q",
            *_remote_ssh_arguments(config)[1:-1],
            "-r",
            f"{config['ssh_host']}:{remote_case}/.",
            str(case_dir),
        ],
        check=True,
        capture_output=True,
        text=True,
        timeout=30,
    )
    local_files: list[str] = []
    for raw_path in result.get("diagnostic_files", []):
        remote_path = PurePosixPath(str(raw_path))
        try:
            relative = remote_path.relative_to(remote_case)
        except ValueError:
            continue
        local_path = case_dir.joinpath(*relative.parts)
        if local_path.is_file():
            local_files.append(str(local_path))
    result["case_directory"] = str(case_dir)
    result["diagnostic_files"] = local_files
    result["source"] = "cloud"
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description="Extract one local support report for Codex diagnosis")
    parser.add_argument("report_id", help="反馈编号，例如 AB-20260822-AFEB35CF")
    parser.add_argument("--root", type=Path, default=default_root())
    parser.add_argument("--destination", type=Path)
    args = parser.parse_args()
    report_id = args.report_id.strip()
    try:
        result = inspect_report(report_id, root=args.root, destination=args.destination)
    except LookupError:
        try:
            result = inspect_cloud_report(report_id, destination=args.destination)
        except (LookupError, OSError, subprocess.SubprocessError, json.JSONDecodeError) as exc:
            print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False))
            return 1
    except (OSError, sqlite3.Error, zipfile.BadZipFile) as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False))
        return 1
    print(json.dumps({"ok": True, **result}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
