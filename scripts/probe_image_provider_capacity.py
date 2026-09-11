from __future__ import annotations

import argparse
import json
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from contextlib import nullcontext
from dataclasses import asdict
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app import llm_client as llm_client_module  # noqa: E402
from app.settings import get_provider  # noqa: E402
from scripts.verify_model_protocols import _verify_image  # noqa: E402


def _parse_route(value: str) -> tuple[str, str]:
    provider, separator, model = value.partition(":")
    if not separator or not provider.strip() or not model.strip():
        raise argparse.ArgumentTypeError("route must use PROVIDER:MODEL")
    return provider.strip(), model.strip()


def main() -> int:
    parser = argparse.ArgumentParser(description="Probe image model concurrency without retaining generated content.")
    parser.add_argument("--route", action="append", required=True, type=_parse_route)
    parser.add_argument("--concurrency", type=int, default=4)
    parser.add_argument("--timeout", type=int, default=300)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if not 2 <= args.concurrency <= 8:
        parser.error("concurrency must be between 2 and 8")
    llm_client_module.model_request_slot = lambda _provider: nullcontext()

    started = time.monotonic()
    results = []
    for provider_name, model in args.route:
        provider = get_provider(provider_name)
        if not provider.api_key:
            raise RuntimeError(f"API key is not configured for provider: {provider_name}")
        with ThreadPoolExecutor(max_workers=args.concurrency) as executor:
            futures = [
                executor.submit(_verify_image, provider, model, args.timeout, attempt=slot)
                for slot in range(1, args.concurrency + 1)
            ]
            results.extend(asdict(future.result()) for future in as_completed(futures))

    summary = []
    for provider_name, model in args.route:
        rows = [row for row in results if row["provider"] == provider_name and row["model"] == model]
        summary.append(
            {
                "provider": provider_name,
                "model": model,
                "concurrency": args.concurrency,
                "attempts": len(rows),
                "successes": sum(row["status"] == "passed" for row in rows),
                "latency_max_seconds": max((row["elapsed_seconds"] for row in rows), default=None),
                "errors": sorted({row["error_kind"] for row in rows if row["error_kind"]}),
            }
        )
    report = {
        "schema_version": 1,
        "kind": "image_provider_capacity_probe",
        "created_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "credentials_or_content_recorded": False,
        "platform_admission_gate_bypassed": True,
        "elapsed_seconds": round(time.monotonic() - started, 3),
        "summary": summary,
        "results": results,
    }
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
