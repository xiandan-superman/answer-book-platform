#!/usr/bin/env python3
from __future__ import annotations

import argparse
import base64
import json
import os
import time
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Iterable


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT_DIR = ROOT / "logs" / "remote_monitor"
DEFAULT_CONFIG_PATH = ROOT / "config" / "remote_monitor.local.json"
DEFAULT_PORTS = "18766-18795,8766"
DIAGNOSTIC_STATUSES = {"failed", "running", "queued", "paused", "completed_with_issues"}


def _auth_header(credentials: tuple[str, str] | None) -> str:
    if not credentials:
        return ""
    username, password = credentials
    encoded = base64.b64encode(f"{username}:{password}".encode("utf-8")).decode("ascii")
    return f"Basic {encoded}"


def read_json(
    url: str,
    timeout: int,
    credentials: tuple[str, str] | None = None,
) -> dict[str, Any]:
    headers = {"Accept": "application/json", "User-Agent": "answer-book-codex-monitor/1"}
    authorization = _auth_header(credentials)
    if authorization:
        headers["Authorization"] = authorization
    request = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(request, timeout=timeout) as response:
        value = json.loads(response.read().decode("utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{url} 未返回 JSON 对象")
    return value


def write_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def parse_ports(raw: str) -> list[int]:
    ports: list[int] = []
    for part in str(raw or "").split(","):
        value = part.strip()
        if not value:
            continue
        if "-" in value:
            start_text, end_text = value.split("-", 1)
            start, end = int(start_text), int(end_text)
            if start > end:
                start, end = end, start
            candidates: Iterable[int] = range(start, end + 1)
        else:
            candidates = (int(value),)
        for port in candidates:
            if not 1 <= port <= 65535:
                raise ValueError(f"端口超出范围：{port}")
            if port not in ports:
                ports.append(port)
    if not ports:
        raise ValueError("未提供可用端口")
    return ports


def _normalized_host(host: str) -> str:
    value = str(host or "").strip().rstrip("/")
    if not value:
        return ""
    parsed = urllib.parse.urlparse(value if "://" in value else f"http://{value}")
    if not parsed.hostname:
        raise ValueError(f"无法识别主机地址：{host}")
    return parsed.hostname


def discover_base_url(host: str, ports: list[int], timeout: int) -> str:
    hostname = _normalized_host(host)
    if not hostname:
        raise ValueError("缺少用户电脑的 Tailscale 主机名或 IP")
    errors: list[str] = []
    for port in ports:
        base_url = f"http://{hostname}:{port}"
        try:
            version = read_json(f"{base_url}/api/version", timeout)
        except Exception as exc:
            errors.append(f"{port}: {exc.__class__.__name__}")
            continue
        if version.get("platform") == "Answer Book Platform":
            return base_url
    checked = ", ".join(str(port) for port in ports)
    hint = "; ".join(errors[-3:])
    raise ConnectionError(
        f"未在 {hostname} 找到真题平台（已检查端口 {checked}）。"
        f"请确认用户已启动程序、Tailscale 在线且 Windows 防火墙已放行。{hint}"
    )


def _load_config(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"无法读取远程监控配置 {path}：{exc}") from exc
    if not isinstance(value, dict):
        raise ValueError(f"远程监控配置必须是 JSON 对象：{path}")
    return value


def _prune_snapshots(output_dir: Path, retain: int) -> None:
    snapshots = sorted(output_dir.glob("remote_monitor_*.json"), key=lambda path: path.stat().st_mtime)
    for path in snapshots[:-max(1, retain)]:
        try:
            path.unlink()
        except OSError:
            pass


def task_brief(task: dict[str, Any]) -> dict[str, Any]:
    return {
        "task_id": task.get("task_id"),
        "kind": task.get("kind"),
        "name": task.get("name") or task.get("title"),
        "status": task.get("status"),
        "health_status": task.get("health_status"),
        "current_stage": task.get("current_stage"),
        "provider": task.get("provider"),
        "model": task.get("model"),
        "created_at": task.get("created_at"),
        "updated_at": task.get("updated_at"),
        "error": task.get("error"),
    }


def capture(
    base_url: str,
    output_dir: Path,
    timeout: int,
    credentials: tuple[str, str] | None = None,
    *,
    requested_task_ids: Iterable[str] = (),
    retain: int = 12,
) -> dict[str, Any]:
    base = base_url.rstrip("/")
    status = read_json(f"{base}/api/system/status", timeout, credentials)
    logs = read_json(f"{base}/api/system/logs", timeout, credentials)
    try:
        hybrid = read_json(f"{base}/api/hybrid/settings", timeout, credentials)
    except Exception as exc:
        # v0.9.9 and earlier do not expose this optional endpoint.
        hybrid = {"available": False, "error": str(exc), "error_type": exc.__class__.__name__}
    tasks = status.get("tasks", {}).get("recent", [])
    diagnostic_ids = {
        str(task.get("task_id") or "")
        for task in tasks
        if task.get("status") in DIAGNOSTIC_STATUSES or task.get("health_status") in {"warning", "error"}
    }
    diagnostic_ids.update(str(task_id).strip() for task_id in requested_task_ids)
    diagnostic_ids.discard("")
    diagnostics: dict[str, Any] = {}
    for task_id in sorted(diagnostic_ids):
        url = f"{base}/api/tasks/{urllib.parse.quote(task_id, safe='')}/diagnostics"
        try:
            diagnostics[task_id] = read_json(url, timeout, credentials)
        except Exception as exc:
            diagnostics[task_id] = {"error": str(exc), "error_type": exc.__class__.__name__}

    snapshot = {
        "captured_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "base_url": base,
        "system": status,
        "logs": logs,
        "hybrid": hybrid,
        "diagnostics": diagnostics,
    }
    stamp = time.strftime("%Y%m%d_%H%M%S")
    write_json(output_dir / f"remote_monitor_{stamp}.json", snapshot)
    write_json(output_dir / "latest.json", snapshot)
    _prune_snapshots(output_dir, retain)
    return snapshot


def print_summary(snapshot: dict[str, Any]) -> None:
    system = snapshot.get("system", {})
    host = system.get("host", {})
    counts = system.get("tasks", {}).get("counts", {})
    tasks = system.get("tasks", {}).get("recent", [])
    diagnostics = snapshot.get("diagnostics", {})
    logs = snapshot.get("logs", {}).get("logs", [])
    print(json.dumps(
        {
            "captured_at": snapshot.get("captured_at"),
            "base_url": snapshot.get("base_url"),
            "version": system.get("version"),
            "health": system.get("health"),
            "execution": snapshot.get("hybrid", {}),
            "host": {
                "name": host.get("name"),
                "system": host.get("system"),
                "platform": host.get("platform"),
                "python": host.get("python"),
                "pid": host.get("pid"),
                "project_root": host.get("project_root"),
            },
            "task_counts": counts,
            "recent_tasks": [task_brief(task) for task in tasks[:8]],
            "recent_logs": logs[-12:],
            "diagnostic_task_ids": list(diagnostics.keys()),
        },
        ensure_ascii=False,
        indent=2,
    ))


def main() -> int:
    parser = argparse.ArgumentParser(description="通过 Tailscale 捕获远程真题平台的脱敏状态、日志和任务诊断")
    parser.add_argument("--config", default=str(DEFAULT_CONFIG_PATH), help="本机私有 JSON 配置（默认不进 Git）")
    parser.add_argument("--host", default="", help="用户电脑的 Tailscale 主机名或 100.x IP")
    parser.add_argument("--base-url", default="", help="已知完整地址时跳过端口发现")
    parser.add_argument("--ports", default="", help=f"发现端口，默认 {DEFAULT_PORTS}")
    parser.add_argument("--username", default="", help="监控账号，默认 monitor")
    parser.add_argument("--task-id", action="append", default=[], help="指定要诊断的任务 ID，可重复")
    parser.add_argument("--output-dir", default="")
    parser.add_argument("--retain", default=12, type=int, help="保留快照数，默认 12")
    parser.add_argument("--timeout", default=4, type=int)
    args = parser.parse_args()

    config_path = Path(args.config).expanduser()
    config = _load_config(config_path)
    base_url = str(args.base_url or config.get("base_url") or os.environ.get("ANSWER_BOOK_MONITOR_URL") or "").strip()
    host = str(args.host or config.get("host") or os.environ.get("ANSWER_BOOK_MONITOR_HOST") or "").strip()
    ports_raw = str(args.ports or config.get("ports") or DEFAULT_PORTS)
    username = str(args.username or config.get("username") or "monitor").strip() or "monitor"
    password = str(os.environ.get("ANSWER_BOOK_MONITOR_PASSWORD") or config.get("password") or "")
    output_dir = Path(args.output_dir or config.get("output_dir") or DEFAULT_OUTPUT_DIR).expanduser()

    if not base_url:
        base_url = discover_base_url(host, parse_ports(ports_raw), args.timeout)
    else:
        base_url = base_url.rstrip("/")
    if not password:
        raise SystemExit(
            "缺少监控密码。请写入 config/remote_monitor.local.json 的 password，"
            "或设置 ANSWER_BOOK_MONITOR_PASSWORD；不要把密码写在命令行或提交到 Git。"
        )
    snapshot = capture(
        base_url,
        output_dir,
        args.timeout,
        (username, password),
        requested_task_ids=args.task_id,
        retain=max(1, args.retain),
    )
    print_summary(snapshot)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
