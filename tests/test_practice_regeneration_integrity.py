from __future__ import annotations

import json
import tempfile
from copy import deepcopy
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from docx import Document

import app.capabilities
from app import exercise_generation, practice_store
from app.practice_export import (
    build_practice_question_docx,
    resolve_practice_export_payload,
    validate_practice_export,
)
from app.practice_question_format import OBJECTIVE_ANSWER_SLOT

# Loading the capability package first avoids the repository's known
# order-dependent llm_client/capabilities import cycle.
_CAPABILITY_IMPORT_GUARD = app.capabilities


ROOT = Path(__file__).resolve().parents[1]
APP_JS = (ROOT / "web" / "app.js").read_text(encoding="utf-8")


def _source(source_id: str, title: str) -> dict:
    return {
        "source_question_id": source_id,
        "title": title,
        "stem_excerpt": f"{title}的教材证据",
        "source_content": f"{title}的完整材料内容",
        "knowledge_points": ["动态软化曲线"],
        "required_constraints": {},
    }


def _question(number: int, plan_item_id: str, source_refs: list[str], stem: str) -> dict:
    return {
        "exercise_id": f"practice_{number:02d}",
        "number": number,
        "plan_item_id": plan_item_id,
        "source_question_id": source_refs[0],
        "source_refs": list(source_refs),
        "question_type": "判断题",
        "difficulty": "进阶",
        "target_skill": "判断动态软化曲线",
        "variation_type": "改变判断情境",
        "stem": stem,
        "options": [],
        "knowledge_points": ["动态软化曲线"],
        "verification_note": "条件充分且命题可独立判断。",
        "diversity_signature": {
            "scenario_family": f"热变形情境{number}",
            "asked_quantity": "曲线命题真伪",
            "solution_family": "边界匹配",
            "cognitive_operation": "判断",
        },
        "difficulty_evidence": {
            "primary_mechanism": "需要组合多个边界条件",
            "student_bottleneck": "容易错配曲线阶段",
        },
        "formulas": [],
        "tables": [],
        "figures": [],
        "generation_status": "completed",
        "generation_error": {},
    }


def _practice() -> dict:
    sources = [
        _source("source_01", "动态回复"),
        _source("source_02", "高应变速率动态再结晶"),
        _source("source_03", "低应变速率动态再结晶"),
    ]
    refs_by_item = {
        "plan_item_01": ["source_01", "source_02"],
        "plan_item_02": ["source_01", "source_02", "source_03"],
    }
    return {
        "source_mode": "knowledge",
        "generation_strategy": "knowledge_overall",
        "source_analysis": {"knowledge_points": ["动态软化曲线"]},
        "source_scope": {"mode": "multiple", "questions": deepcopy(sources)},
        "selected_source_questions": deepcopy(sources),
        "blueprint": {
            "generation_strategy": "knowledge_overall",
            "exercise_plan": [
                {
                    "number": number,
                    "plan_item_id": plan_item_id,
                    "source_question_id": refs[0],
                    "source_refs": list(refs),
                    "question_type": "判断题",
                    "difficulty": "进阶",
                    "target_skill": "判断动态软化曲线",
                    "variation_type": "改变判断情境",
                    "design_intent": "根据全部绑定材料辨析曲线边界。",
                    "required_knowledge_points": ["动态软化曲线"],
                    "required_constraints": {},
                }
                for number, (plan_item_id, refs) in enumerate(refs_by_item.items(), start=1)
            ],
        },
        "exercises": [
            _question(1, "plan_item_01", refs_by_item["plan_item_01"], "未变化的第一题题干。"),
            _question(2, "plan_item_02", refs_by_item["plan_item_02"], "重新生成前的第二题题干。"),
        ],
        "semantic_review": {
            "status": "passed",
            "triggered": True,
            "review_scope": "complete_set",
            "items": [
                {"number": 1, "status": "passed", "risks": []},
                {"number": 2, "status": "passed", "risks": []},
            ],
            "risk_count": 0,
            "actionable_risk_count": 0,
            "missing_numbers": [],
            "set_summary": "原整套题已复核。",
        },
    }


def _candidate(stem: str) -> dict:
    candidate = _question(1, "model_plan", ["source_01"], stem)
    candidate["source_refs"] = ["source_01"]
    candidate["diversity_signature"] = {
        "scenario_family": stem,
        "asked_quantity": f"核对：{stem}",
        "solution_family": "按本题边界逐项判断",
        "cognitive_operation": "纠错",
    }
    candidate["answer"] = "模型不应保存的答案"
    candidate["analysis"] = "模型不应保存的解析"
    return candidate


def _install_model_responses(monkeypatch, responses: list[dict], prompts: list[str]) -> None:
    pending = iter(responses)

    def fake_call(_client, messages, **_kwargs):
        prompts.append(str(messages[-1]["content"]))
        return deepcopy(next(pending))

    monkeypatch.setattr(
        exercise_generation,
        "_primary_model_runtime",
        lambda _payload: (SimpleNamespace(name="fake", supports_vision=False), "fake-model"),
    )
    monkeypatch.setattr(exercise_generation, "OpenAICompatibleClient", lambda _provider: object())
    monkeypatch.setattr(exercise_generation, "_call_practice_json", fake_call)


def _review_item(report: dict, number: int) -> dict:
    return next(item for item in report["items"] if int(item["number"]) == number)


def test_single_regeneration_restores_all_blueprint_sources_and_reviews_only_changed_item(monkeypatch) -> None:
    practice = _practice()
    prompts: list[str] = []
    changed_stem = "重新生成后的第二题：分别判断三类动态软化曲线边界。"
    _install_model_responses(
        monkeypatch,
        [
            {"exercises": [_candidate(changed_stem)]},
        ],
        prompts,
    )

    result = exercise_generation.regenerate_practice_exercise(
        {
            "practice": practice,
            "exercise_index": 1,
            "source_mode": "knowledge",
            "include_source_content_in_generation": True,
            "semantic_review_enabled": True,
        }
    )

    assert result["exercise"]["source_question_id"] == "source_01"
    assert result["exercise"]["source_refs"] == ["source_01", "source_02", "source_03"]
    assert result["exercise"]["stem"] == changed_stem + OBJECTIVE_ANSWER_SLOT
    assert "semantic_review" not in result
    assert len(prompts) == 1


def test_regeneration_without_review_invalidates_only_changed_question(monkeypatch) -> None:
    practice = _practice()
    prompts: list[str] = []
    _install_model_responses(
        monkeypatch,
        [{"exercises": [_candidate("未启用复核时生成的新第二题。")]}],
        prompts,
    )

    result = exercise_generation.regenerate_practice_exercise(
        {
            "practice": practice,
            "exercise_index": 1,
            "source_mode": "knowledge",
            "include_source_content_in_generation": True,
            "semantic_review_enabled": False,
        }
    )

    assert len(prompts) == 1
    assert result["exercise"]["source_refs"] == ["source_01", "source_02", "source_03"]
    assert "semantic_review" not in result
    assert result["quality"]["release_level"] == "formal"


def test_batch_regeneration_keeps_each_blueprint_sources_and_previous_incremental_review(monkeypatch) -> None:
    practice = _practice()
    prompts: list[str] = []
    first_stem = "批量重新生成后的第一题。"
    second_stem = "批量重新生成后的第二题。"
    _install_model_responses(
        monkeypatch,
        [
            {"exercises": [_candidate(first_stem)]},
            {"exercises": [_candidate(second_stem)]},
        ],
        prompts,
    )

    first = exercise_generation.regenerate_practice_exercise(
        {
            "practice": practice,
            "exercise_index": 0,
            "source_mode": "knowledge",
            "include_source_content_in_generation": True,
            "semantic_review_enabled": True,
        }
    )
    after_first = {
        **practice,
        "exercises": [first["exercise"], practice["exercises"][1]],
    }
    assert after_first["exercises"][1]["stem"] == "重新生成前的第二题题干。"

    second = exercise_generation.regenerate_practice_exercise(
        {
            "practice": after_first,
            "exercise_index": 1,
            "source_mode": "knowledge",
            "include_source_content_in_generation": True,
            "semantic_review_enabled": True,
        }
    )
    final_exercises = [after_first["exercises"][0], second["exercise"]]

    assert [item["stem"] for item in final_exercises] == [
        first_stem + OBJECTIVE_ANSWER_SLOT,
        second_stem + OBJECTIVE_ANSWER_SLOT,
    ]
    assert final_exercises[0]["source_refs"] == ["source_01", "source_02"]
    assert final_exercises[1]["source_refs"] == ["source_01", "source_02", "source_03"]
    assert "semantic_review" not in first and "semantic_review" not in second
    assert len(prompts) == 2


def test_history_enforces_blueprint_sources_revisions_and_word_export_isolation() -> None:
    with tempfile.TemporaryDirectory() as raw, patch.object(
        practice_store,
        "PRACTICE_HISTORY_DIR",
        Path(raw),
    ):
        practice = _practice()
        saved = practice_store.save_practice_record(practice, request={})
        history_id = str(saved["history_id"])
        q2_version = saved["data"]["exercises"][1]["_edit_version"]

        updated = practice_store.update_practice_exercise(
            history_id,
            1,
            {
                **_candidate("历史保存后的第二题。"),
                "plan_item_id": "model_attempted_override",
                "source_question_id": "source_01",
                "source_refs": ["source_01"],
            },
            change_reason="regenerate_selected_questions",
            expected_edit_version=q2_version,
        )

        assert updated["revision_count"] == 1
        assert updated["data"]["exercises"][0]["stem"] == "未变化的第一题题干。"
        assert updated["data"]["exercises"][1]["source_refs"] == ["source_01", "source_02", "source_03"]
        assert "answer" not in updated["data"]["exercises"][1]
        assert "analysis" not in updated["data"]["exercises"][1]
        raw_record = json.loads((Path(raw) / f"{history_id}.json").read_text(encoding="utf-8"))
        assert raw_record["revisions"][0]["data"]["exercises"][1]["source_refs"] == [
            "source_01",
            "source_02",
            "source_03",
        ]

        selected = resolve_practice_export_payload(
            {"export_scope": "selected", "selected_exercise_ids": ["plan_item_02"]},
            updated["data"],
        )
        assert selected["exercises"][0]["source_refs"] == ["source_01", "source_02", "source_03"]
        assert validate_practice_export(selected)["release_level"] == "formal"
        word_text = "\n".join(
            paragraph.text
            for paragraph in Document(BytesIO(build_practice_question_docx(selected))).paragraphs
            if paragraph.text.strip()
        )
        assert "历史保存后的第二题" in word_text
        assert "未变化的第一题题干" not in word_text
        assert "答案" not in word_text and "解析" not in word_text

        q2_version = updated["data"]["exercises"][1]["_edit_version"]
        stale = practice_store.update_practice_exercise(
            history_id,
            1,
            {**updated["data"]["exercises"][1], "stem": "再次变化但未复核的第二题。", "source_refs": ["source_01"]},
            change_reason="regenerate_selected_questions",
            expected_edit_version=q2_version,
        )
        assert stale["revision_count"] == 2
        assert stale["data"]["exercises"][1]["source_refs"] == ["source_01", "source_02", "source_03"]
        assert stale["data"]["semantic_review"]["status"] == "passed"
        assert all(item["status"] == "passed" for item in stale["data"]["semantic_review"]["items"])

        selected_q1 = resolve_practice_export_payload(
            {"export_scope": "selected", "selected_exercise_ids": ["plan_item_01"]},
            stale["data"],
        )
        selected_q2 = resolve_practice_export_payload(
            {"export_scope": "selected", "selected_exercise_ids": ["plan_item_02"]},
            stale["data"],
        )
        assert validate_practice_export(selected_q1)["release_level"] == "formal"
        assert validate_practice_export(selected_q2)["release_level"] == "formal"


def test_frontend_regeneration_payload_omits_removed_review_switch() -> None:
    start = APP_JS.index("function practiceRegenerationPayload(index, instruction)")
    end = APP_JS.index("async function regeneratePracticeExercise", start)
    payload_source = APP_JS[start:end]
    assert "semantic_review_enabled" not in payload_source
    assert "formal_quality_review" not in payload_source

    batch_start = APP_JS.index("async function regenerateSelectedPracticeQuestions(button)")
    batch_end = APP_JS.index("async function undoPracticeChange()", batch_start)
    batch_source = APP_JS[batch_start:batch_end]
    assert "const response = await regeneratePracticeExercise(index, instruction, payloads.get(index));" in batch_source
    assert "structuredClone(practiceRegenerationPayload(index, instruction))" in batch_source
    assert "response.semantic_review" not in batch_source


def test_repair_mode_accepts_minimal_candidate_without_diversity_retry(monkeypatch) -> None:
    practice = _practice()
    baseline = deepcopy(practice)
    prompts: list[str] = []
    _install_model_responses(monkeypatch, [{"exercises": [deepcopy(practice["exercises"][1])]}], prompts)
    instruction = "只核对公式。" * 300 + "保留最后的限制。"
    result = exercise_generation.regenerate_practice_exercise({
        "practice": practice, "exercise_index": 1, "repair_only": True,
        "instruction": instruction, "include_source_content_in_generation": True,
    })
    assert len(prompts) == 1
    assert "至少两项" not in prompts[0]
    assert instruction in prompts[0]
    assert result["exercise"]["stem"] == baseline["exercises"][1]["stem"] + OBJECTIVE_ANSWER_SLOT
    assert practice == baseline
