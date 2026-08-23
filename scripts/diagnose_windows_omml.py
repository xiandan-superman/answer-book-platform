#!/usr/bin/env python3
"""Capture the exact production MathML-to-OMML failure for one practice history.

Run this script through the installed frozen Windows executable so imports and
dependencies are identical to the affected desktop application.
"""

from __future__ import annotations

import hashlib
import json
import platform
import sys
import traceback
from datetime import datetime
from pathlib import Path
from typing import Any


DEFAULT_HISTORY_ID = "practice_20260823115404_1412e328"


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _exception_payload(exc: BaseException) -> dict[str, str]:
    return {
        "type": exc.__class__.__name__,
        "message": str(exc),
        "traceback": traceback.format_exc(),
    }


def _write_report(path: Path, report: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def main() -> int:
    history_id = str(sys.argv[1] if len(sys.argv) > 1 else DEFAULT_HISTORY_ID).strip()
    output_path = (
        Path(sys.argv[2]).expanduser()
        if len(sys.argv) > 2
        else Path.home() / "Desktop" / "answer-book-omml-diagnostic.json"
    )
    report: dict[str, Any] = {
        "created_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "history_id": history_id,
        "platform": platform.platform(),
        "python": platform.python_version(),
        "executable": sys.executable,
        "formula_results": [],
    }

    try:
        from app import omml
        from app.practice_export import _normalize_standard_state_latex
        from app.practice_store import load_practice_record

        xsl_path = omml.find_mathml2omml_xsl()
        xsl_info: dict[str, Any] = {
            "path": str(xsl_path) if xsl_path else None,
            "exists": bool(xsl_path and xsl_path.is_file()),
        }
        if xsl_path and xsl_path.is_file():
            xsl_bytes = xsl_path.read_bytes()
            xsl_info.update(
                {
                    "size_bytes": len(xsl_bytes),
                    "sha256": _sha256_bytes(xsl_bytes),
                    "last_modified_ns": xsl_path.stat().st_mtime_ns,
                }
            )
        report["xsl"] = xsl_info

        record = load_practice_record(history_id)
        data = record.get("data") if isinstance(record.get("data"), dict) else {}
        exercises = data.get("exercises") if isinstance(data.get("exercises"), list) else []
        report["exercise_count"] = len(exercises)

        for exercise_index, exercise in enumerate(exercises, start=1):
            if not isinstance(exercise, dict):
                continue
            question_number = exercise.get("number") or exercise_index
            formulas = exercise.get("formulas") if isinstance(exercise.get("formulas"), list) else []
            for formula_index, formula in enumerate(formulas, start=1):
                if not isinstance(formula, dict):
                    continue
                raw_latex = str(formula.get("latex") or "").strip()
                normalized_latex = _normalize_standard_state_latex(raw_latex)
                item: dict[str, Any] = {
                    "question_number": question_number,
                    "formula_index": formula_index,
                    "formula_id": str(formula.get("formula_id") or ""),
                    "role": str(formula.get("role") or ""),
                    "raw_latex": raw_latex,
                    "normalized_latex": normalized_latex,
                }
                try:
                    from latex2mathml.converter import convert as latex_to_mathml

                    mathml = latex_to_mathml(omml.normalize_latex(normalized_latex))
                    item["mathml"] = {
                        "size_chars": len(mathml),
                        "sha256": _sha256_bytes(mathml.encode("utf-8")),
                        "prefix": mathml[:500],
                    }
                except Exception as exc:
                    item["latex_to_mathml_error"] = _exception_payload(exc)

                try:
                    converted = omml.omml_from_latex_via_mathml(normalized_latex)
                    from lxml import etree

                    xml = etree.tostring(converted, encoding="utf-8")
                    item["omml"] = {
                        "ok": True,
                        "root_tag": str(converted.tag),
                        "size_bytes": len(xml),
                        "sha256": _sha256_bytes(xml),
                        "prefix": xml[:500].decode("utf-8", errors="replace"),
                    }
                except Exception as exc:
                    item["omml"] = {"ok": False, "error": _exception_payload(exc)}
                report["formula_results"].append(item)

        report["formula_count"] = len(report["formula_results"])
        report["failed_formula_count"] = sum(
            1 for item in report["formula_results"] if not item.get("omml", {}).get("ok")
        )
        report["ok"] = report["failed_formula_count"] == 0
    except Exception as exc:
        report["ok"] = False
        report["fatal_error"] = _exception_payload(exc)

    _write_report(output_path, report)
    return 0 if report.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
