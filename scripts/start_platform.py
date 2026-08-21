#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.environment import check_environment  # noqa: E402
from app.process_lock import ProcessLockUnavailable  # noqa: E402
from app.server import run  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", default=8766, type=int)
    args = parser.parse_args()
    env = check_environment()
    formula_ready = bool(env.get("formula_conversion", {}).get("preferred_chain_ready"))
    if not formula_ready:
        print(json.dumps({"ok": False, "error": "preferred formula conversion chain is not ready", "environment": env}, ensure_ascii=False, indent=2))
        return 1
    print(f"Answer Book Platform starting at http://{args.host}:{args.port}")
    try:
        run(args.host, args.port)
    except KeyboardInterrupt:
        print("\n平台已停止。")
        return 0
    except ProcessLockUnavailable as exc:
        print(str(exc), file=sys.stderr)
        return 2
    except OSError as exc:
        if getattr(exc, "errno", None) in {48, 98, 10048}:
            print(
                f"端口 {args.port} 已被占用，平台没有恢复任务或启动后台工作器。"
                "请关闭占用该端口的程序后重试。",
                file=sys.stderr,
            )
            return 2
        raise
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
