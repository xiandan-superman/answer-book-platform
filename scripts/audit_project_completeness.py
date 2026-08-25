#!/usr/bin/env python3
from __future__ import annotations

import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


REQUIRED_FILES = [
    "README.md",
    "CHANGELOG.md",
    "MIGRATION_README.md",
    "VERSION",
    "requirements.txt",
    "requirements-windows.txt",
    ".env.example",
    "app/server.py",
    "app/http_errors.py",
    "app/task_runner.py",
    "app/version.py",
    "app/pipeline.py",
    "app/pipeline_delivery.py",
    "app/pipeline_checkpoints.py",
    "app/pipeline_telemetry.py",
    "app/answer_generation.py",
    "app/v4_schema.py",
    "app/omml.py",
    "app/docx_v4.py",
    "app/docx_audit.py",
    "app/render_audit.py",
    "app/final_acceptance.py",
    "app/delivery_package.py",
    "app/answer_coverage_audit.py",
    "app/review_export.py",
    "app/page_map_admin.py",
    "app/local_config.py",
    "scripts/check_environment.py",
    "scripts/create_task.py",
    "scripts/run_task.py",
    "scripts/run_platform.py",
    "scripts/start_platform.py",
    "scripts/install_dependencies.py",
    "scripts/audit_answer_fragments.py",
    "scripts/audit_answer_coverage.py",
    "scripts/audit_final_acceptance.py",
    "scripts/export_question_review.py",
    "scripts/package_task_delivery.py",
    "scripts/check_version_consistency.py",
    "scripts/extract_release_notes.py",
    "scripts/data_inventory.py",
    "scripts/run_quality_gates.py",
    "scripts/clean_runtime_state.py",
    "web/index.html",
    "web/app.js",
    "web/motion.js",
    "web/platform-api.js",
    "web/task-contract-ui.js",
    "web/icon-compat.js",
    "web/styles.css",
    "web/styles/foundation.css",
    "web/vendor/gsap.min.js",
    "web/vendor/mathjax/tex-mml-chtml.js",
    "start_platform.command",
    "start_platform_windows.bat",
    "docs/DELIVERY_CHECKLIST.md",
    "docs/V4_FORMULA_CHAIN.md",
    "docs/ARCHITECTURE.md",
]


REQUIRED_TEXT = {
    "app/server.py": [
        "/api/version",
        "/api/providers/local-keys",
        "final-acceptance",
        "answer-fragments",
        "review-export",
        "page-map",
        "download",
        "delivery-package",
    ],
    "app/pipeline.py": [
        "answer_coverage",
        "complete_pipeline_delivery",
        "require_preferred_formula_chain",
    ],
    "app/pipeline_delivery.py": [
        "docx_audit.json",
        "render_audit.json",
        "quality_shadow",
        "final_acceptance",
    ],
    "web/index.html": [
        "versionBox",
        "API Key 配置",
        "刷新任务列表",
        "查看文件",
        "导出交付包",
        "最终验收",
        "质量审查报告",
        "reviewList",
        "结构化答案编辑",
        "教材页码校准",
    ],
    "web/app.js": [
        "/api/version",
        "startTaskPolling",
        "summarizeTaskStatus",
    ],
    "README.md": [
        "MIGRATION_README.md",
        "CHANGELOG.md",
        "RELEASE_MANIFEST.json",
        "平台质量检查",
        "最终验收",
        "passed_with_warnings",
        "结构化答案复核",
        "逐题复核",
    ],
    "MIGRATION_README.md": [
        "/api/version",
        "RELEASE_MANIFEST.json",
        "正式执行顺序",
        "passed_with_warnings",
        "verify_release_package.py",
    ],
}


def main() -> int:
    issues: list[str] = []
    for rel in REQUIRED_FILES:
        if not (ROOT / rel).exists():
            issues.append(f"missing required file: {rel}")
    for rel, needles in REQUIRED_TEXT.items():
        path = ROOT / rel
        if not path.exists():
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        for needle in needles:
            if needle not in text:
                issues.append(f"{rel}: missing text marker {needle!r}")
    result = {
        "ok": not issues,
        "checked_files": len(REQUIRED_FILES),
        "issue_count": len(issues),
        "issues": issues,
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
