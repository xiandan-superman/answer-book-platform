from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.pydantic_shadow import (  # noqa: E402
    SHADOW_REPORT_JSON,
    build_pydantic_shadow_report,
    record_shadow_review,
)


def main() -> int:
    parser = argparse.ArgumentParser(description="汇总本地 Pydantic 影子观察结果，不触发模型调用。")
    parser.add_argument("--output", type=Path, default=SHADOW_REPORT_JSON)
    parser.add_argument("--review-event", default="", help="要人工复核的影子事件 ID")
    parser.add_argument("--verdict", choices=("confirmed_issue", "false_positive"))
    args = parser.parse_args()
    if args.review_event:
        if not args.verdict:
            parser.error("--review-event 必须同时提供 --verdict")
        record_shadow_review(args.review_event, args.verdict)
    report = build_pydantic_shadow_report()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
