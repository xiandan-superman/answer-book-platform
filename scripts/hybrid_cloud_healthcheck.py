#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import time
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


def main() -> int:
    parser = argparse.ArgumentParser(description="Persist a proactive hybrid cloud health snapshot.")
    parser.add_argument("--url", required=True)
    parser.add_argument("--tokens", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--attempts", type=int, default=5)
    parser.add_argument("--retry-delay-seconds", type=float, default=1.0)
    args = parser.parse_args()
    tokens = json.loads(Path(args.tokens).read_text(encoding="utf-8")).get("tokens") or []
    if not tokens or not tokens[0].get("token"):
        raise RuntimeError("Hybrid health check has no token")
    attempts = max(1, min(10, args.attempts))
    value: dict[str, object] = {"ok": False, "error": "health_check_not_run"}
    for attempt in range(1, attempts + 1):
        request = Request(args.url, headers={"Authorization": f"Bearer {tokens[0]['token']}", "Accept": "application/json"})
        try:
            with urlopen(request, timeout=15) as response:
                value = json.loads(response.read(2 * 1024 * 1024).decode("utf-8"))
            break
        except HTTPError as exc:
            raw = exc.read(2 * 1024 * 1024)
            try:
                value = json.loads(raw.decode("utf-8"))
            except Exception:
                value = {"ok": False, "error": f"HTTP {exc.code}"}
            break
        except (OSError, URLError, json.JSONDecodeError) as exc:
            value = {"ok": False, "error": str(exc)[:500], "attempt": attempt}
            if attempt < attempts:
                time.sleep(max(0.1, min(10.0, args.retry_delay_seconds)))
    value["checked_at_epoch"] = int(time.time())
    target = Path(args.output)
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(f".{target.name}.{os.getpid()}.tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(temporary, target)
    health = value.get("health") if isinstance(value, dict) else None
    return 0 if value.get("ok") and isinstance(health, dict) and health.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
