from __future__ import annotations

import json
from pathlib import Path

from docx import Document

from app.analysis_profiles import (
    EVIDENCE_BACKED_ANALYSIS,
    QUESTION_ONLY_ANALYSIS,
    analysis_uses_textbook_evidence,
    normalize_analysis_profile,
)
from app.answer_coverage_audit import audit_answer_coverage
from app.audit_model_repair import _repair_prompt as build_audit_repair_prompt
from app.docx_model_repair import _repair_prompt as build_docx_repair_prompt
from app.docx_v4 import build_docx_from_fragments
from app.prompts import build_answer_draft_prompt
from app.task_read_model import build_exam_run


def _user_payload(messages: list[dict]) -> dict:
    content = messages[-1]["content"]
    if isinstance(content, list):
        content = content[0]["text"]
    return json.loads(content)


def test_analysis_profile_defaults_and_textbook_policy() -> None:
    assert normalize_analysis_profile(None) == EVIDENCE_BACKED_ANALYSIS
    assert normalize_analysis_profile(QUESTION_ONLY_ANALYSIS) == QUESTION_ONLY_ANALYSIS
    assert analysis_uses_textbook_evidence(EVIDENCE_BACKED_ANALYSIS)
    assert not analysis_uses_textbook_evidence(QUESTION_ONLY_ANALYSIS)
    coverage = audit_answer_coverage(
        {"items": [{"question_id": "q1", "section": "", "number": "1"}]},
        {"fragments": [{"question_id": "q1", "section": "", "number": "1", "answer": "答案"}]},
        require_evidence=False,
    )
    assert coverage["warnings"] == []


def test_question_only_prompt_is_derived_without_textbook_payload() -> None:
    messages = build_answer_draft_prompt(
        {"question_id": "q1", "number": "1", "stem": "解释测试概念。", "question_type": "名词解释"},
        [{"textbook": "不应出现", "evidence_text": "不应进入提示"}],
        include_textbook_evidence=False,
    )
    payload = _user_payload(messages)
    serialized = json.dumps(payload, ensure_ascii=False)
    assert payload["analysis_profile"] == QUESTION_ONLY_ANALYSIS
    assert "textbook_content" not in payload
    assert "不应进入提示" not in serialized
    assert "This profile does not run textbook indexing" in serialized
    assert "不得要求用户补教材" in messages[0]["content"]


def test_question_only_repair_prompts_do_not_restore_evidence_context() -> None:
    question = {"question_id": "q1", "number": "1", "stem": "解释测试概念。", "question_type": "名词解释"}
    audit_messages = build_audit_repair_prompt(
        audit_stage="content_quality",
        question=question,
        evidence=[{"evidence_text": "不应进入回修"}],
        fragment={"question_id": "q1", "answer": "待修复"},
        issues=[{"question_id": "q1", "code": "answer_too_short"}],
        include_textbook_evidence=False,
    )
    audit_payload = _user_payload(audit_messages)
    assert audit_payload["analysis_profile"] == QUESTION_ONLY_ANALYSIS
    assert audit_payload["confirmed_evidence"] == []
    assert "不应进入回修" not in json.dumps(audit_payload, ensure_ascii=False)

    docx_messages = build_docx_repair_prompt(
        question,
        [{"evidence_text": "不应进入格式回修"}],
        {"question_id": "q1", "answer": "待修复", "blocks": []},
        [{"question_id": "q1", "message": "测试问题"}],
        ["测试问题"],
        include_textbook_evidence=False,
    )
    docx_payload = _user_payload(docx_messages)
    assert docx_payload["analysis_profile"] == QUESTION_ONLY_ANALYSIS
    assert docx_payload["confirmed_evidence"] == []
    assert "不应进入格式回修" not in json.dumps(docx_payload, ensure_ascii=False)


def test_question_only_document_title_and_public_task_label(tmp_path: Path) -> None:
    source = tmp_path / "fragments.json"
    output = tmp_path / "result.docx"
    source.write_text(
        json.dumps(
            {
                "document_title": "题目解析",
                "analysis_profile": QUESTION_ONLY_ANALYSIS,
                "fragments": [],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    build_docx_from_fragments(source, output)
    assert Document(output).paragraphs[0].text == "题目解析"

    task = build_exam_run(
        {
            "task_id": "task-1",
            "exam_path": "/tmp/example.docx",
            "provider": "demo",
            "model": "demo-model",
            "status": "created",
            "current_stage": "created",
            "error": "",
            "created_at": "2026-08-29 00:00:00",
            "updated_at": "2026-08-29 00:00:00",
            "analysis_profile": QUESTION_ONLY_ANALYSIS,
        }
    )
    assert task["analysis_profile"] == QUESTION_ONLY_ANALYSIS
    assert task["display_title"].startswith("题目解析")


def test_question_only_analysis_uses_shared_manual_structure_review_gate() -> None:
    import inspect

    from app import pipeline

    source = inspect.getsource(pipeline._run_pipeline_impl)
    review_call = source.index("wait_for_exam_structure_review(")
    question_only_textbook_skip = source.index('if not textbook_evidence_enabled:')

    assert review_call < question_only_textbook_skip
    assert "auto_confirm_exam_structure" not in source


def test_question_only_entry_and_request_contract_are_present() -> None:
    root = Path(__file__).resolve().parents[1]
    index = (root / "web" / "index.html").read_text(encoding="utf-8")
    app = (root / "web" / "app.js").read_text(encoding="utf-8")
    assert 'id="questionAnalysisUtilityTitle">题目解析' in index
    assert 'id="reasoningModelRoleCard"' in index
    assert "function startQuestionAnalysis()" in app
    assert 'currentExamAnalysisProfile = "question_only";\n  goToPage("env");' in app
    assert '$("reasoningModelRoleCard")?.classList.toggle("hidden", questionOnly);' in app
    assert "function examRequiredTextRoutes()" in app
    assert "analysis_profile: currentExamAnalysisProfile" in app
    assert "if (!questionOnly) await requirePreparedTextbookIndex();" in app
