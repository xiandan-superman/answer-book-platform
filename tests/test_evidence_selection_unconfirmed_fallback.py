from __future__ import annotations

import json
from pathlib import Path

import pytest
from docx import Document

from app.answer_generation import reconcile_confirmed_evidence_binding
from app.docx_v4 import build_docx_from_fragments
from app.evidence_selection import (
    _apply_formula_evidence_guard,
    _program_selection,
    _select_one,
    confirm_evidence_selection,
    selection_needs_expansion,
    unresolved_knowledge_points,
)
from app.llm_client import LLMError
from app.retrieval import EvidenceCandidate
from app.settings import DEFAULT_MODEL_MAX_TOKENS, ProviderConfig


def _provider() -> ProviderConfig:
    return ProviderConfig(
        name="test",
        type="openai_compatible",
        base_url="https://example.test/v1",
        api_key="configured-for-test-only",
        default_model="test-model",
        model_options=(),
        allow_custom_model=True,
        model_hint="",
        temperature=0.1,
        max_tokens=DEFAULT_MODEL_MAX_TOKENS,
    )


def _candidates() -> list[EvidenceCandidate]:
    return [
        EvidenceCandidate(
            "ev_pythagorean",
            "q1",
            "数学教材",
            "数学教材",
            "直角三角形",
            "math.json",
            "1",
            "12",
            9.9,
            "勾股定理说明：直角三角形两直角边平方和等于斜边平方。",
            True,
            "勾股定理",
        ),
        EvidenceCandidate(
            "ev_photosynthesis",
            "q1",
            "生物教材",
            "生物教材",
            "光合作用",
            "biology.json",
            "8",
            "90",
            1.2,
            "光合作用利用光能合成有机物。",
            True,
            "光合作用",
        ),
    ]


def _question() -> dict:
    return {"question_id": "q1", "stem": "直角三角形两直角边为 3 和 4，求斜边长度。"}


def _plan() -> dict:
    return {"question_id": "q1", "knowledge_points": ["勾股定理"]}


def test_unconfirmed_fallback_keeps_ranked_candidates_without_creating_direct_evidence() -> None:
    selection = _program_selection(
        _question(),
        _plan(),
        _candidates(),
        "模型教材引用确认失败；候选仅供复核。",
    )
    point = selection["knowledge_points"][0]

    assert point["selected_evidence_ids"] == []
    assert point["candidate_evidence_ids"] == ["ev_pythagorean", "ev_photosynthesis"]
    assert point["support_type"] == "background_only"
    assert point["evidence_support_types"] == {}
    assert point["evidence_status"] == "unavailable"
    assert point["confirmation_status"] == "unconfirmed"
    assert "确认失败" in point["no_suitable_evidence_reason"]
    assert selection["citation_groups"][0]["selected_evidence_ids"] == []
    assert not selection_needs_expansion(selection)
    assert unresolved_knowledge_points(selection) == []


def test_offline_selection_output_keeps_candidates_out_of_confirmed_snapshot(tmp_path: Path) -> None:
    output_json = tmp_path / "evidence_selection.json"
    result, confirmed = confirm_evidence_selection(
        {"items": [_question()]},
        {"q1": _plan()},
        _candidates(),
        _provider(),
        "test-model",
        output_json,
        tmp_path / "textbook_blocks.csv",
        tmp_path / "textbook_page_map.csv",
        use_model=False,
    )
    saved = json.loads(output_json.read_text(encoding="utf-8"))
    point = saved["selections"][0]["knowledge_points"][0]

    assert result.selected_evidence_count == 0
    assert confirmed == []
    assert point["selected_evidence_ids"] == []
    assert point["candidate_evidence_ids"] == ["ev_pythagorean", "ev_photosynthesis"]
    assert point["confirmation_status"] == "unconfirmed"
    assert (tmp_path / "confirmed_evidence_candidates.csv").is_file()


def test_successful_model_confirmation_keeps_existing_confirmed_direct_selection() -> None:
    class ConfirmingClient:
        last_json_retry_report = {}

        def chat_json_object(self, _messages, **_kwargs):
            return {
                "question_id": "q1",
                "knowledge_points": [
                    {
                        "knowledge_point": "勾股定理",
                        "selected_evidence_ids": ["ev_pythagorean"],
                        "evidence_support_types": {"ev_pythagorean": "direct_support"},
                        "reason": "教材内容直接给出直角三角形边长关系。",
                    }
                ],
            }

    selection = _select_one(
        ConfirmingClient(),
        _provider(),
        "test-model",
        _question(),
        _plan(),
        _candidates(),
    )
    point = selection["knowledge_points"][0]

    assert point["selected_evidence_ids"] == ["ev_pythagorean"]
    assert point["support_type"] == "direct_support"
    assert point["evidence_status"] == "confirmed"
    assert "confirmation_status" not in point


@pytest.mark.parametrize("failure", [LLMError("timeout"), RuntimeError("invalid JSON")])
def test_model_failure_types_leave_candidates_unconfirmed(failure: Exception) -> None:
    class FailingClient:
        last_json_retry_report = {}

        def chat_json_object(self, _messages, **_kwargs):
            raise failure

    selection = _select_one(
        FailingClient(),
        _provider(),
        "test-model",
        _question(),
        _plan(),
        _candidates(),
    )
    point = selection["knowledge_points"][0]

    assert point["selected_evidence_ids"] == []
    assert point["candidate_evidence_ids"] == ["ev_pythagorean", "ev_photosynthesis"]
    assert point["evidence_status"] == "unavailable"
    assert point["confirmation_status"] == "unconfirmed"
    assert selection["_meta"]["fallback"] is True
    assert selection["_meta"]["model_confirmation_attempted"] is True


def test_empty_candidate_pool_remains_unavailable_and_expandable() -> None:
    selection = _program_selection(_question(), _plan(), [])
    point = selection["knowledge_points"][0]

    assert point["selected_evidence_ids"] == []
    assert point["candidate_evidence_ids"] == []
    assert point["evidence_status"] == "unavailable"
    assert point["confirmation_status"] == "unavailable"
    assert point["no_suitable_evidence_reason"] == "未检索到候选教材依据。"
    assert selection_needs_expansion(selection)
    assert unresolved_knowledge_points(selection) == ["勾股定理"]


def test_formula_guard_does_not_override_an_unconfirmed_model_fallback() -> None:
    candidate = EvidenceCandidate(
        "ev_formula",
        "q1",
        "数学教材",
        "数学教材",
        "勾股定理",
        "math.json",
        "1",
        "12",
        9.9,
        "a^2+b^2=c^2",
        True,
        "勾股定理",
        source_type="equation_block",
    )
    selection = _program_selection(
        _question(),
        {"question_id": "q1", "knowledge_points": ["勾股定理"], "formulas": ["a^2+b^2=c^2"]},
        [candidate],
        "模型教材引用确认失败；候选仅供复核。",
    )

    guarded, repaired = _apply_formula_evidence_guard(
        selection,
        {"knowledge_points": ["勾股定理"], "formulas": ["a^2+b^2=c^2"]},
        [candidate],
    )

    assert repaired == []
    assert guarded["knowledge_points"][0]["selected_evidence_ids"] == []
    assert guarded["knowledge_points"][0]["confirmation_status"] == "unconfirmed"


def test_final_true_exam_docx_hides_unconfirmed_candidate_as_non_citation(tmp_path: Path) -> None:
    selection = _program_selection(
        _question(),
        _plan(),
        _candidates(),
        "模型教材引用确认失败；候选仅供复核。",
    )
    fragment = {
        "schema_version": "answer_book.answer_fragment.v4",
        "question_id": "q1",
        "section": "一、简答题",
        "question_type": "简答题",
        "number": "1",
        "answer": "5。",
        "answer_summary": "5。",
        "evidence_ids": ["ev_photosynthesis"],
        "formulas": [],
        "blocks": [],
    }
    evidence = [
        {"question_id": "q1", "evidence_id": candidate.evidence_id, "printed_page": candidate.printed_page}
        for candidate in _candidates()
    ]

    reconcile_confirmed_evidence_binding(fragment, evidence, selection)
    assert fragment["evidence_ids"] == []
    evidence_block = next(block for block in fragment["blocks"] if block["label"] == "教材依据")
    assert evidence_block["segments"][0]["highlight"] == "unconfirmed_evidence"

    source = tmp_path / "fragments.json"
    output = tmp_path / "answer.docx"
    source.write_text(json.dumps({"fragments": [fragment]}, ensure_ascii=False), encoding="utf-8")
    build_docx_from_fragments(source, output)
    final_text = "\n".join(paragraph.text for paragraph in Document(output).paragraphs)

    assert "光合作用" not in final_text
    assert "教材依据：" not in final_text
