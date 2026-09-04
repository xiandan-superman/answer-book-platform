from __future__ import annotations

import json
from types import SimpleNamespace

from app import exercise_generation
from app.answer_generation import build_answer_batch_prompt
from app.audit_model_repair import (
    _repair_prompt as build_audit_repair_prompt,
)
from app.audit_model_repair import (
    _repair_retry_prompt,
)
from app.docx_model_repair import _repair_prompt as build_docx_repair_prompt
from app.drawing_code import build_drawing_code_prompt
from app.image_orchestration import (
    GENERATION_IMAGE_LABEL_LANGUAGE_REQUIREMENT,
    GENERATION_IMAGE_LABEL_LANGUAGE_REQUIREMENT_FIELD,
    ensure_generation_image_label_language_requirement,
)
from app.prompts import build_answer_draft_prompt


def _serialized(messages: list[dict]) -> str:
    return json.dumps(messages, ensure_ascii=False)


def _question() -> dict:
    return {
        "question_id": "q1",
        "number": "1",
        "stem": "说明曲线的变化趋势并作图。",
        "question_type": "作图题",
    }


def test_generation_requirement_is_json_native_and_idempotent() -> None:
    original = [
        {"role": "system", "content": "system"},
        {"role": "user", "content": json.dumps({"task": "generate"}, ensure_ascii=False)},
    ]

    once = ensure_generation_image_label_language_requirement(original)
    twice = ensure_generation_image_label_language_requirement(once)

    assert original != once
    assert once == twice
    payload = json.loads(once[-1]["content"])
    assert payload[GENERATION_IMAGE_LABEL_LANGUAGE_REQUIREMENT_FIELD] == (
        GENERATION_IMAGE_LABEL_LANGUAGE_REQUIREMENT
    )
    assert _serialized(twice).count(GENERATION_IMAGE_LABEL_LANGUAGE_REQUIREMENT) == 1


def test_generation_requirement_preserves_multimodal_content_and_existing_rule() -> None:
    multimodal = [
        {
            "role": "user",
            "content": [
                {"type": "text", "text": '{"task":"generate"}'},
                {"type": "image_url", "image_url": {"url": "data:image/png;base64,AAAA"}},
            ],
        }
    ]
    injected = ensure_generation_image_label_language_requirement(multimodal)
    assert injected[0]["content"][1] == multimodal[0]["content"][1]
    assert _serialized(injected).count(GENERATION_IMAGE_LABEL_LANGUAGE_REQUIREMENT) == 1

    already_present = [
        {
            "role": "user",
            "content": '{"hard_rules":["图片中的标题、说明和自然语言标注应与题目语言保持一致"]}',
        }
    ]
    assert ensure_generation_image_label_language_requirement(already_present) == already_present


def test_exam_generation_and_repair_prompts_receive_one_requirement() -> None:
    question = _question()
    single = build_answer_draft_prompt(question, [], include_textbook_evidence=False)
    batch = build_answer_batch_prompt(
        [{"question": question, "evidence": [], "include_textbook_evidence": False}]
    )
    audit = build_audit_repair_prompt(
        audit_stage="content_quality",
        question=question,
        evidence=[],
        fragment={"question_id": "q1", "answer": "待修复"},
        issues=[{"code": "missing_required_figure"}],
        include_textbook_evidence=False,
    )
    audit_retry = _repair_retry_prompt(
        audit,
        {"question_id": "q1", "answer": "修复候选"},
        ["绘图要求未满足"],
    )
    docx = build_docx_repair_prompt(
        question,
        [],
        {"question_id": "q1", "answer": "待修复", "blocks": []},
        [{"message": "图示结构不完整"}],
        ["图示结构不完整"],
        include_textbook_evidence=False,
    )
    drawing = build_drawing_code_prompt(question, {"question_id": "q1"})

    for messages in (single, batch, audit, audit_retry, docx, drawing):
        assert _serialized(messages).count(GENERATION_IMAGE_LABEL_LANGUAGE_REQUIREMENT) == 1


def test_practice_generation_and_figure_repair_are_covered_but_planning_is_not() -> None:
    calls: list[list[dict]] = []

    class FakeClient:
        config = SimpleNamespace(name="fake", base_url="")

        def chat_json(self, messages, **_kwargs):
            calls.append(messages)
            return SimpleNamespace(content=json.dumps(response))

    for contract_id in ("practice.generation", "practice.figure_repair", "practice.planning"):
        response = {
            "practice.generation": {"exercises": [{"stem": "题目"}]},
            "practice.figure_repair": {"figures": []},
            "practice.planning": {"plan_items": [{"plan_item_id": "p1"}]},
        }[contract_id]
        result = exercise_generation._call_practice_json(
            FakeClient(),
            [{"role": "user", "content": '{"task":"test"}'}],
            model="fake-model",
            temperature=0.1,
            thinking=None,
            repair_invalid_json=False,
            prompt_contract_id=contract_id,
        )
        assert result == response

    assert _serialized(calls[0]).count(GENERATION_IMAGE_LABEL_LANGUAGE_REQUIREMENT) == 1
    assert _serialized(calls[1]).count(GENERATION_IMAGE_LABEL_LANGUAGE_REQUIREMENT) == 1
    assert GENERATION_IMAGE_LABEL_LANGUAGE_REQUIREMENT not in _serialized(calls[2])
