from __future__ import annotations

import json
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class MathVerification:
    available: bool
    equivalent: bool | None
    engine: str = ""
    error: str = ""


def verify_math_equivalence(gold: str, answer: str, *, timeout_seconds: int = 8) -> MathVerification:
    """Compare LaTeX in an isolated process; never pickle SymPy expressions."""

    try:
        completed = subprocess.run(
            [sys.executable, str(Path(__file__).with_name("math_worker.py"))],
            input=json.dumps({"gold": str(gold), "answer": str(answer)}, ensure_ascii=False),
            capture_output=True,
            text=True,
            timeout=max(1, int(timeout_seconds)),
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return MathVerification(False, None, error=f"{type(exc).__name__}: {exc}")
    try:
        data = json.loads(completed.stdout)
    except Exception:
        return MathVerification(False, None, error=(completed.stderr or completed.stdout or "invalid worker output")[-1000:])
    if not data.get("ok"):
        return MathVerification(False, None, error=str(data.get("error") or "math worker failed"))
    return MathVerification(True, bool(data.get("equivalent")), engine=str(data.get("engine") or ""))
