#!/usr/bin/env python3
from __future__ import annotations

import argparse
import importlib.util
import json
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# The repository also contains a frozen desktop-App release surface.  Platform
# gates must not start depending on, packaging, or implicitly approving those
# files merely because they happen to exist in the same working tree.
FROZEN_APP_PYTHON_PATHS = frozenset(
    {
        "scripts/build_macos_app.py",
        "scripts/package_release.py",
        "scripts/verify_release_package.py",
    }
)
FULL_COVERAGE_MIN_PERCENT = 60


def run_step(name: str, cmd: list[str]) -> dict:
    started = time.time()
    proc = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True)
    return {
        "name": name,
        "ok": proc.returncode == 0,
        "returncode": proc.returncode,
        "elapsed_seconds": round(time.time() - started, 3),
        "stdout": proc.stdout[-3000:],
        "stderr": proc.stderr[-3000:],
        "cmd": cmd,
    }


def _module_available(name: str) -> bool:
    return importlib.util.find_spec(name) is not None


def platform_python_files(root: Path = ROOT) -> list[str]:
    files = [str(path.relative_to(root)) for path in sorted((root / "app").rglob("*.py"))]
    files.extend(str(path.relative_to(root)) for path in sorted((root / "scripts").glob("*.py")))
    return [path for path in files if path not in FROZEN_APP_PYTHON_PATHS]


def main() -> int:
    parser = argparse.ArgumentParser(description="Run platform-only quality gates.")
    parser.add_argument("--full", action="store_true", help="also require lint, type checks and coverage")
    args = parser.parse_args()
    py_files = platform_python_files()
    steps = [
        ("py_compile", [sys.executable, "-m", "py_compile", *py_files]),
        ("version_consistency", [sys.executable, "scripts/check_version_consistency.py"]),
        ("pytest", [sys.executable, "-m", "pytest", "-q"]),
        ("formula_omml", [sys.executable, "scripts/test_formula_omml.py"]),
        ("project_completeness", [sys.executable, "scripts/audit_project_completeness.py"]),
    ]
    if args.full:
        missing = [name for name in ("ruff", "mypy", "coverage") if not _module_available(name)]
        if missing:
            print(json.dumps({
                "ok": False,
                "missing_dev_tools": missing,
                "install": (
                    "python3 -m pip install -r requirements.txt -r requirements-dev.txt "
                    "-c constraints-py39.txt  # Python 3.11+ use constraints-py311.txt"
                ),
            }, ensure_ascii=False, indent=2))
            return 2
        lint_scope = [
            "app/http_errors.py", "app/task_runner.py", "app/task_store.py", "app/task_control.py",
            "app/practice_jobs.py", "app/practice_store.py", "app/version.py", "app/environment.py",
            "app/lan_access.py", "scripts/check_version_consistency.py",
            "scripts/data_inventory.py", "scripts/run_quality_gates.py",
        ]
        lint_scope.extend(str(path.relative_to(ROOT)) for path in sorted((ROOT / "app" / "capabilities").rglob("*.py")))
        lint_scope.extend(
            [
                "app/figure_schema_registry.py",
                "app/pipeline_telemetry.py",
                "app/pipeline_checkpoints.py",
                "app/pipeline_delivery.py",
                "app/render_fonts.py",
                "app/render_audit.py",
                "app/render_word.py",
                "app/task_diagnostics.py",
                "app/task_result_view.py",
                "app/dependency_profiles.py",
                "app/update_manager.py",
                "scripts/audit_answer_fragments.py",
                "scripts/build_update_manifest.py",
                "scripts/install_dependencies.py",
                "scripts/source_launcher.py",
                "app/practice_batch_contracts.py",
                "app/practice_result_assembly.py",
                "tests/test_capability_architecture.py",
                "tests/test_academic_expressions.py",
                "tests/test_figure_semantics.py",
                "tests/test_quality_metrics.py",
                "tests/test_quality_shadow.py",
                "tests/test_selective_quality_review.py",
                "tests/test_pipeline_telemetry.py",
                "tests/test_practice_batch_contracts.py",
                "tests/test_practice_result_assembly.py",
                "tests/test_render_fonts.py",
                "tests/test_render_header_clipping.py",
                "tests/test_task_diagnostics_checkpoint.py",
                "tests/test_task_result_checkpoint_view.py",
                "tests/test_task_control_contract.py",
                "tests/test_task_runner_admission.py",
                "tests/test_audit_answer_fragments_script.py",
                "tests/test_dependency_profiles.py",
                "tests/test_server_api_not_found.py",
                "tests/test_source_release_workflow.py",
                "tests/test_update_manager.py",
            ]
        )
        type_scope = [path for path in lint_scope if path != "app/practice_store.py"]
        steps.extend(
            [
                ("ruff", [sys.executable, "-m", "ruff", "check", *lint_scope]),
                ("mypy", [sys.executable, "-m", "mypy", *type_scope]),
                ("coverage", [sys.executable, "-m", "coverage", "run", "-m", "pytest", "-q"]),
                (
                    "coverage_report",
                    [
                        sys.executable,
                        "-m",
                        "coverage",
                        "report",
                        f"--fail-under={FULL_COVERAGE_MIN_PERCENT}",
                    ],
                ),
            ]
        )
    results = []
    for name, cmd in steps:
        result = run_step(name, cmd)
        results.append(result)
        print(json.dumps({k: v for k, v in result.items() if k not in {"stdout", "stderr"}}, ensure_ascii=False, indent=2))
        if not result["ok"]:
            print(json.dumps({"failed_step": name, "stdout": result["stdout"], "stderr": result["stderr"]}, ensure_ascii=False, indent=2))
            report = {"ok": False, "failed_step": name, "results": results}
            (ROOT / "quality_gates_report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
            return 1
    report = {"ok": True, "results": results}
    (ROOT / "quality_gates_report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"ok": True, "report": str(ROOT / "quality_gates_report.json")}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
