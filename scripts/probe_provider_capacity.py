from __future__ import annotations

import argparse
import json
import math
import statistics
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.llm_client import create_llm_client  # noqa: E402
from app.settings import get_provider  # noqa: E402


@dataclass(frozen=True)
class ProbeResult:
    provider: str
    model: str
    protocol: str
    concurrency: int
    round: int
    slot: int
    ok: bool
    elapsed_seconds: float
    input_tokens: int | None = None
    output_tokens: int | None = None
    total_tokens: int | None = None
    error_kind: str = ""


def _usage(raw: dict[str, Any]) -> tuple[int | None, int | None, int | None]:
    usage = raw.get("usage") if isinstance(raw, dict) else None
    if not isinstance(usage, dict):
        return None, None, None
    input_tokens = usage.get("input_tokens", usage.get("prompt_tokens"))
    output_tokens = usage.get("output_tokens", usage.get("completion_tokens"))
    total_tokens = usage.get("total_tokens")
    return (
        int(input_tokens) if isinstance(input_tokens, (int, float)) else None,
        int(output_tokens) if isinstance(output_tokens, (int, float)) else None,
        int(total_tokens) if isinstance(total_tokens, (int, float)) else None,
    )


def _error_kind(exc: BaseException) -> str:
    text = str(exc).lower()
    for code in ("429", "524", "503", "502", "500", "408"):
        if code in text:
            return f"http_{code}"
    if "timed out" in text or "timeout" in text:
        return "timeout"
    if "model" in text and ("not found" in text or "does not exist" in text):
        return "model_unavailable"
    if "api key" in text or "unauthorized" in text or "401" in text:
        return "authentication"
    return type(exc).__name__


def _route_protocol(provider: Any, model: str) -> str:
    profile = dict((getattr(provider, "model_profiles", {}) or {}).get(model, {}))
    return str(profile.get("api_protocol") or getattr(provider, "api_protocol", "chat_completions"))


def _probe_once(provider_name: str, model: str, concurrency: int, round_number: int, slot: int, timeout: int) -> ProbeResult:
    provider = get_provider(provider_name)
    protocol = _route_protocol(provider, model)
    client = create_llm_client(provider)
    started = time.monotonic()
    try:
        result = client.chat_json(
            [
                {"role": "system", "content": "Return one compact JSON object only."},
                {"role": "user", "content": '{"probe":"reply with {\\"ok\\":true}"}'},
            ],
            model=model,
            max_tokens=96,
            thinking="low",
            timeout=timeout,
            task_stage="provider_capacity_probe",
        )
        elapsed = time.monotonic() - started
        input_tokens, output_tokens, total_tokens = _usage(result.raw)
        return ProbeResult(
            provider=provider_name,
            model=model,
            protocol=protocol,
            concurrency=concurrency,
            round=round_number,
            slot=slot,
            ok=True,
            elapsed_seconds=round(elapsed, 3),
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            total_tokens=total_tokens,
        )
    except BaseException as exc:
        return ProbeResult(
            provider=provider_name,
            model=model,
            protocol=protocol,
            concurrency=concurrency,
            round=round_number,
            slot=slot,
            ok=False,
            elapsed_seconds=round(time.monotonic() - started, 3),
            error_kind=_error_kind(exc),
        )


def _percentile(values: list[float], fraction: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    index = max(0, min(len(ordered) - 1, math.ceil(len(ordered) * fraction) - 1))
    return round(ordered[index], 3)


def summarize(results: Iterable[ProbeResult]) -> list[dict[str, Any]]:
    buckets: dict[tuple[str, str, str, int], list[ProbeResult]] = {}
    for result in results:
        key = (result.provider, result.model, result.protocol, result.concurrency)
        buckets.setdefault(key, []).append(result)
    summaries: list[dict[str, Any]] = []
    for (provider, model, protocol, concurrency), rows in sorted(buckets.items()):
        latencies = [row.elapsed_seconds for row in rows if row.ok]
        summaries.append(
            {
                "provider": provider,
                "model": model,
                "protocol": protocol,
                "concurrency": concurrency,
                "attempts": len(rows),
                "successes": sum(1 for row in rows if row.ok),
                "success_rate": round(sum(1 for row in rows if row.ok) / len(rows), 4),
                "latency_p50_seconds": round(statistics.median(latencies), 3) if latencies else None,
                "latency_p95_seconds": _percentile(latencies, 0.95),
                "latency_max_seconds": round(max(latencies), 3) if latencies else None,
                "errors": sorted({row.error_kind for row in rows if row.error_kind}),
            }
        )
    return summaries


def run_probe(
    routes: list[tuple[str, str]],
    levels: list[int],
    rounds: int,
    timeout: int,
    *,
    mixed: bool = False,
) -> dict[str, Any]:
    results: list[ProbeResult] = []
    started = time.monotonic()
    for provider_name, _model in routes:
        provider = get_provider(provider_name)
        if not provider.api_key:
            raise RuntimeError(f"API key is not configured for provider: {provider_name}")
    route_groups = [routes] if mixed else [[route] for route in routes]
    for route_group in route_groups:
        for concurrency in levels:
            for round_number in range(1, rounds + 1):
                with ThreadPoolExecutor(max_workers=concurrency) as executor:
                    futures = [
                        executor.submit(
                            _probe_once,
                            route_group[(slot - 1) % len(route_group)][0],
                            route_group[(slot - 1) % len(route_group)][1],
                            concurrency,
                            round_number,
                            slot,
                            timeout,
                        )
                        for slot in range(1, concurrency + 1)
                    ]
                    results.extend(future.result() for future in as_completed(futures))
    return {
        "schema_version": 1,
        "kind": "provider_capacity_probe",
        "mode": "mixed" if mixed else "independent",
        "created_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "elapsed_seconds": round(time.monotonic() - started, 3),
        "routes": [{"provider": provider, "model": model} for provider, model in routes],
        "levels": levels,
        "rounds": rounds,
        "summary": summarize(results),
        "results": [asdict(result) for result in results],
    }


def _parse_route(value: str) -> tuple[str, str]:
    provider, separator, model = value.partition(":")
    if not separator or not provider.strip() or not model.strip():
        raise argparse.ArgumentTypeError("route must use PROVIDER:MODEL")
    return provider.strip(), model.strip()


def _parse_levels(value: str) -> list[int]:
    try:
        levels = sorted({int(item.strip()) for item in value.split(",") if item.strip()})
    except ValueError as exc:
        raise argparse.ArgumentTypeError("levels must be comma-separated integers") from exc
    if not levels or levels[0] < 1 or levels[-1] > 16:
        raise argparse.ArgumentTypeError("levels must be between 1 and 16")
    return levels


def main() -> int:
    parser = argparse.ArgumentParser(description="Probe provider/model concurrency without storing credentials.")
    parser.add_argument("--route", action="append", required=True, type=_parse_route)
    parser.add_argument("--levels", default="1,2,4", type=_parse_levels)
    parser.add_argument("--rounds", default=2, type=int)
    parser.add_argument("--timeout", default=90, type=int)
    parser.add_argument("--mixed", action="store_true", help="Run all routes in the same concurrency wave.")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if not 1 <= args.rounds <= 5:
        parser.error("rounds must be between 1 and 5")
    report = run_probe(args.route, args.levels, args.rounds, args.timeout, mixed=args.mixed)
    rendered = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding="utf-8")
    print(rendered, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
