from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

# Initialize the capability package before llm_client; the application normally
# does this through its server bootstrap, while this focused test imports the
# exercise module directly.
import app.capabilities  # noqa: F401
from app import exercise_generation
from app.llm_client import LLMError, ResponsesAPIClient
from app.settings import ProviderConfig


def _provider(**overrides) -> ProviderConfig:
    values = {
        "name": "lingsuan",
        "type": "openai_compatible",
        "base_url": "https://edge.lingsuan.org/v1",
        "api_key": "test-key",
        "default_model": "gpt-5.6-terra",
        "model_options": ("gpt-5.6-terra",),
        "allow_custom_model": True,
        "model_hint": "",
        "temperature": 0.1,
        "max_tokens": 24576,
        "api_protocol": "responses",
        "responses_streaming": True,
    }
    values.update(overrides)
    return ProviderConfig(**values)


def _result(content: str):
    return SimpleNamespace(content=content, raw={})


def _malformed_latex_json() -> str:
    return '{"exercises":[{"stem":"$' + "\\" + 'beta$"}]}'


def _valid_latex_json() -> str:
    return json.dumps({"exercises": [{"stem": "$\\beta$"}]}, ensure_ascii=False)


def test_lingsuan_gpt_practice_generation_keeps_configured_responses_protocol():
    provider = _provider()

    client = exercise_generation._practice_generation_client(provider, "gpt-5.6-terra")

    assert isinstance(client, ResponsesAPIClient)
    assert client.config.api_protocol == "responses"
    assert client.config.responses_streaming is True
    assert provider.api_protocol == "responses"


def test_lingsuan_non_gpt_model_keeps_configured_protocol():
    provider = _provider(default_model="claude-opus-5")

    client = exercise_generation._practice_generation_client(provider, "claude-opus-5")

    assert isinstance(client, ResponsesAPIClient)
    assert client.config.api_protocol == "responses"


def test_generation_formula_contract_matches_the_normalization_consumer():
    contract = exercise_generation._batch_output_contract([{
        "question_type": "计算题",
        "required_constraints": {"essential_formulas": ["状态方程"]},
    }])["exercises"][0]

    assert "完整包在 $...$ 或 \\[...\\] 中" in contract["stem"]
    assert contract["formulas"][0]["latex"] == "不含美元符号的 LaTeX"

    normalized = exercise_generation.normalize_practice_set(
        {"exercises": [{
            "plan_item_id": "plan_item_01",
            "question_type": "计算题",
            "difficulty": "进阶",
            "stem": "按给定状态方程计算。",
            "formulas": [{"formula_id": "f1", "latex": r"pV=nRT", "location": "stem"}],
        }]},
        requested_count=1,
        subject="物理",
        planned_plan_ids=["plan_item_01"],
    )

    assert normalized["exercises"][0]["formulas"][0]["latex"] == r"pV=nRT"


def test_control_character_in_responses_output_repairs_on_same_route():
    primary_calls = []

    class PrimaryResponsesClient:
        config = _provider()

        def chat_json(self, messages, **kwargs):
            primary_calls.append((messages, kwargs))
            return _result(_malformed_latex_json() if len(primary_calls) == 1 else _valid_latex_json())

    parsed = exercise_generation._call_practice_json(
        PrimaryResponsesClient(),
        [{"role": "user", "content": "return JSON"}],
        model="gpt-5.6-terra",
        temperature=0.35,
        thinking=None,
    )

    assert parsed["exercises"][0]["stem"] == "$\\beta$"
    assert len(primary_calls) == 2
    assert primary_calls[1][1]["thinking"] is None
    assert primary_calls[1][1]["model"] == "gpt-5.6-terra"


def test_invalid_same_route_retry_is_rejected_instead_of_returned():
    class PrimaryResponsesClient:
        config = _provider()

        def chat_json(self, _messages, **_kwargs):
            return _result(_malformed_latex_json())

    with pytest.raises(LLMError, match="同路由 JSON 修复后仍失败"):
        exercise_generation._call_practice_json(
            PrimaryResponsesClient(),
            [{"role": "user", "content": "return JSON"}],
            model="gpt-5.6-terra",
            temperature=0.35,
            thinking=None,
        )


def test_tool_loop_output_repairs_single_escaped_latex_control_before_normalization():
    calls = []

    class ToolLoop:
        def run_json(self, messages, **kwargs):
            calls.append((messages, kwargs))
            return SimpleNamespace(
                value={"exercises": [{"stem": "$" + "\t" + "imes$"}]},
                generated_artifacts=[],
                steps=[],
                tool_calls=[],
            )

    parsed = exercise_generation._call_practice_json(
        object(),
        [{"role": "user", "content": "return JSON"}],
        model="gpt-5.6-terra",
        temperature=0.35,
        thinking=None,
        tool_loop=ToolLoop(),
    )

    assert parsed["exercises"][0]["stem"] == "$\\times$"
    assert exercise_generation._practice_control_character_issues(parsed) == []
    assert len(calls) == 1


@pytest.mark.parametrize("command_suffix", ["heta", "ext", "imes"])
def test_gateway_control_recovery_is_limited_to_known_latex_commands(command_suffix):
    repaired = exercise_generation._repair_gateway_latex_control_characters({
        "stem": "$" + "\t" + command_suffix + "$",
    })

    assert repaired["stem"] == "$\\t" + command_suffix + "$"
    assert exercise_generation._practice_control_character_issues(repaired) == []


def test_gateway_control_recovery_leaves_ambiguous_control_characters_for_the_gate():
    repaired = exercise_generation._repair_gateway_latex_control_characters({"stem": "普通\t文字"})

    assert repaired["stem"] == "普通\t文字"
    assert exercise_generation._practice_control_character_issues(repaired)


def test_control_gate_rejects_formula_escape_controls_but_allows_newline():
    assert exercise_generation._practice_control_character_issues({"stem": "first\nsecond"}) == []

    issues = exercise_generation._practice_control_character_issues({
        "beta": "\beta",
        "frac": "\frac",
        "theta": "\theta",
        "rm": "\rm",
        "del": "x\x7fy",
    })

    assert {issue["code"] for issue in issues} == {
        "U+0008", "U+0009", "U+000C", "U+000D", "U+007F",
    }


@pytest.mark.parametrize("damaged_prefix", ["\x00", "\x02", "\x05"])
def test_luna_gateway_corrupted_formula_prefixes_are_repaired_safely(damaged_prefix):
    damaged = {
        "exercises": [{
            "stem": (
                f"${damaged_prefix}mathrm{{CO_2(g)}}$ 与 ${damaged_prefix}mathrm{{H_2(g)}}$ 建立 "
                f"${damaged_prefix}mathrm{{CO_2}}" + "\r" + "ightleftharpoons " + f"{damaged_prefix}mathrm{{CO}}$。"
            ),
        }],
    }

    parsed = exercise_generation._parse_safe_practice_json(json.dumps(damaged, ensure_ascii=False))
    stem = parsed["exercises"][0]["stem"]

    assert r"\mathrm{CO_2(g)}" in stem
    assert r"\mathrm{H_2(g)}" in stem
    assert r"\rightleftharpoons" in stem
    assert exercise_generation._practice_control_character_issues(parsed) == []


def test_unrepairable_formula_controls_still_use_the_questions_json_retry_budget():
    error = LLMError("专项练习模型输出包含非法控制字符，已拒绝保存：U+0008")

    detail = exercise_generation._generation_error_detail(error)

    assert detail["code"] == "generation_response_invalid"
    assert detail["retryable"] is True


def test_normalization_refuses_contaminated_data_before_persistence():
    with pytest.raises(ValueError, match="不能进入规范化或保存"):
        exercise_generation.normalize_practice_set(
            {"exercises": [{"stem": "$\beta$"}]},
            requested_count=1,
            subject="化学",
        )


@pytest.mark.parametrize("subject", ["按题出题", "知识点出题"])
def test_one_known_slot_control_failure_does_not_destroy_other_results(subject):
    raw = {"exercises": [
        {"plan_item_id": "plan_item_01", "stem": "解释此现象。", "question_type": "简答题"},
        {"plan_item_id": "plan_item_02", "stem": "$\theta$", "question_type": "简答题"},
    ]}
    result = exercise_generation.normalize_practice_set(raw, requested_count=2, subject=subject)
    assert result["exercises"][0]["stem"] == "解释此现象。"
    assert result["exercises"][0]["generation_status"] == "completed"
    rejected = result["exercises"][1]
    assert rejected["number"] == 2
    assert rejected["generation_status"] == "failed"
    assert rejected["generation_error"]["code"] == "invalid_output_control_character"
    assert exercise_generation._practice_control_character_issues(result) == []
    assert "\t" in raw["exercises"][1]["stem"]  # original evidence untouched


def test_corrupt_shared_context_still_blocks_the_set():
    with pytest.raises(ValueError, match="不能进入规范化或保存"):
        exercise_generation.normalize_practice_set(
            {"source_analysis": {"subject": "\t"}, "exercises": [
                {"plan_item_id": "plan_item_01", "stem": "解释此现象。"},
            ]}, requested_count=1, subject="化学",
        )
