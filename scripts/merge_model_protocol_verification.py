from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


def _route_status(rows: list[dict[str, Any]]) -> str:
    return "passed" if any(row.get("status") == "passed" for row in rows) else "failed_snapshot"


def _observation(row: dict[str, Any]) -> dict[str, Any]:
    return {
        key: row.get(key)
        for key in (
            "attempt",
            "status",
            "elapsed_seconds",
            "structured_output",
            "streaming_complete",
            "error_kind",
            "error_title",
            "responsibility",
            "retryable",
            "http_status",
        )
    }


def merge(registry: dict[str, Any], reports: list[dict[str, Any]]) -> dict[str, Any]:
    grouped: dict[tuple[str, str, str, str], list[dict[str, Any]]] = {}
    latest_timestamp = str(registry.get("snapshot_at", ""))
    for report in reports:
        latest_timestamp = max(latest_timestamp, str(report.get("created_at", "")))
        for row in report.get("results", []):
            if row.get("status") == "not_tested_missing_key":
                continue
            key = (
                str(row["provider"]),
                str(row["model"]),
                str(row["kind"]),
                str(row["protocol"]),
            )
            grouped.setdefault(key, []).append(row)

    for (provider, model, kind, protocol), rows in grouped.items():
        provider_record = registry.setdefault("providers", {}).setdefault(provider, {"models": {}})
        model_record = provider_record.setdefault("models", {}).setdefault(model, {"kind": kind, "routes": {}})
        model_record["kind"] = kind
        model_record.setdefault("routes", {})[protocol] = {
            "status": _route_status(rows),
            "observations": [_observation(row) for row in sorted(rows, key=lambda item: item.get("attempt") or 0)],
        }

    route_records = [
        route
        for provider in registry.get("providers", {}).values()
        for model in provider.get("models", {}).values()
        for route in model.get("routes", {}).values()
    ]
    model_records = [
        model
        for provider in registry.get("providers", {}).values()
        for model in provider.get("models", {}).values()
    ]
    summary = registry.setdefault("summary", {})
    summary.update(
        {
            "providers": len(registry.get("providers", {})),
            "models": len(model_records),
            "routes": len(route_records),
            "passed_routes": sum(route.get("status") == "passed" for route in route_records),
            "failed_snapshot_routes": sum(route.get("status") == "failed_snapshot" for route in route_records),
        }
    )
    registry["snapshot_at"] = latest_timestamp
    return registry


def main() -> int:
    parser = argparse.ArgumentParser(description="Merge key-free live probe reports into the protocol registry.")
    parser.add_argument("--registry", type=Path, required=True)
    parser.add_argument("--report", type=Path, action="append", required=True)
    args = parser.parse_args()
    registry = json.loads(args.registry.read_text(encoding="utf-8"))
    reports = [json.loads(path.read_text(encoding="utf-8")) for path in args.report]
    args.registry.write_text(json.dumps(merge(registry, reports), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
