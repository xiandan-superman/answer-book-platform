#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.pipeline import stage_dir  # noqa: E402
from app.pipeline_checkpoints import reconcile_answer_generation_checkpoint  # noqa: E402
from app.v4_schema import validate_v4_answer_fragment  # noqa: E402


def audit(path: Path) -> list[str]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError) as exc:
        return [f"answer_fragments could not be read: {exc}"]
    issues: list[str] = []
    if not isinstance(data, dict):
        return ["answer_fragments payload must be a JSON object"]
    fragments = data.get("fragments")
    if not isinstance(fragments, list):
        return ["answer_fragments.fragments must be a list"]
    seen = set()
    for idx, fragment in enumerate(fragments, start=1):
        if not isinstance(fragment, dict):
            issues.append(f"fragment {idx}: must be an object")
            continue
        qid = str(fragment.get("question_id") or "").strip()
        if not qid:
            issues.append(f"fragment {idx}: missing question_id")
        elif qid in seen:
            issues.append(f"fragment {idx}: duplicate question_id {qid}")
        seen.add(qid)
        for issue in validate_v4_answer_fragment(fragment):
            issues.append(f"fragment {idx}: {issue}")
    return issues


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("target", help="task_id or path to answer_fragments.json")
    args = parser.parse_args()
    target = Path(args.target)
    path = target if target.exists() else stage_dir(args.target) / "answer_fragments.json"
    if not path.exists():
        print(json.dumps({"ok": False, "issues": [f"not found: {path}"]}, ensure_ascii=False, indent=2))
        return 1
    issues = audit(path)
    result = {
        "ok": not issues,
        "path": str(path),
        "issue_count": len(issues),
        "issues": issues,
    }
    if (path.parent / "structured_exam.json").exists():
        result["checkpoint_reconciliation"] = reconcile_answer_generation_checkpoint(path.parent)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if not issues else 1


if __name__ == "__main__":
    raise SystemExit(main())
