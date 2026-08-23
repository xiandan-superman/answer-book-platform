from __future__ import annotations

import ast
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
CAPABILITIES_INIT = ROOT / "app" / "capabilities" / "__init__.py"
INDEPENDENT_IMPORT_TARGETS = (
    "app.practice_jobs",
    "app.practice_worker",
    "app.runtime_monitor",
    "app.capabilities",
)


def _run_in_fresh_python(source: str) -> subprocess.CompletedProcess[str]:
    environment = os.environ.copy()
    environment["PYTHONPATH"] = str(ROOT)
    return subprocess.run(
        [sys.executable, "-c", source],
        cwd=ROOT,
        env=environment,
        text=True,
        capture_output=True,
        check=False,
    )


@pytest.mark.parametrize("module_name", INDEPENDENT_IMPORT_TARGETS)
def test_runtime_modules_import_independently_in_fresh_python(module_name: str) -> None:
    result = _run_in_fresh_python(f"import {module_name}")

    assert result.returncode == 0, result.stderr or result.stdout


def test_capabilities_selective_review_exports_are_lazy_and_preserve_identity() -> None:
    result = _run_in_fresh_python(
        "\n".join(
            (
                "import sys",
                "import app.capabilities as capabilities",
                "assert 'app.capabilities.selective_review' not in sys.modules",
                "from app.capabilities import collect_selective_review_candidates, review_selective_quality",
                "from app.capabilities.selective_review import (",
                "    collect_selective_review_candidates as direct_collect,",
                "    review_selective_quality as direct_review,",
                ")",
                "assert collect_selective_review_candidates is direct_collect",
                "assert review_selective_quality is direct_review",
                "assert 'collect_selective_review_candidates' in capabilities.__all__",
                "assert 'review_selective_quality' in capabilities.__all__",
            )
        )
    )

    assert result.returncode == 0, result.stderr or result.stdout


def test_capabilities_boundary_has_no_eager_selective_review_import() -> None:
    tree = ast.parse(CAPABILITIES_INIT.read_text(encoding="utf-8"))
    eager_imports = [
        node
        for node in tree.body
        if isinstance(node, ast.ImportFrom) and node.module == "selective_review"
    ]

    assert eager_imports == []
    assert any(isinstance(node, ast.FunctionDef) and node.name == "__getattr__" for node in tree.body)
