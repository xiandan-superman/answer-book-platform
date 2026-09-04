from __future__ import annotations

import json
import sys
from typing import Any


def _verify(gold: str, answer: str) -> dict[str, Any]:
    from math_verify import parse, verify

    gold_parsed = parse(gold)
    answer_parsed = parse(answer)
    equivalent = bool(verify(gold_parsed, answer_parsed))
    engine = "math-verify"
    if not equivalent:
        # Direct conversion gives algebraic expressions a second deterministic
        # route while Math-Verify remains authoritative for boxed/interval/set
        # answer formats.
        try:
            import sympy
            from latex2sympy2_extended import latex2sympy

            left = latex2sympy(gold.strip("$"))
            right = latex2sympy(answer.strip("$"))
            equivalent = bool(sympy.simplify(left - right) == 0)
            if equivalent:
                engine = "latex2sympy2_extended+sympy"
        except Exception:
            pass
    return {"ok": True, "equivalent": equivalent, "engine": engine}


def main() -> int:
    try:
        payload = json.loads(sys.stdin.read())
        result = _verify(str(payload.get("gold") or ""), str(payload.get("answer") or ""))
    except Exception as exc:
        result = {"ok": False, "equivalent": None, "engine": "", "error": f"{type(exc).__name__}: {exc}"}
    sys.stdout.write(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
