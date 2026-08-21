from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

from app import practice_jobs
from app.exercise_generation import (
    PracticeGenerationStopped,
    _batch_sibling_variant_issues,
    _blueprint_multi_question_config,
    _expand_blueprint_items_for_generation,
    generate_practice_from_plan,
    recompute_practice_quality,
    reconcile_practice_generation,
)
from app.llm_client import LLMError


def test_generation_uses_bounded_semantic_batches_and_restores_internal_ids() -> None:
    calls: list[str] = []

    def fake_call(_client, messages, **_kwargs):
        prompt = str(messages[-1]["content"])
        calls.append(prompt)
        batch_index = 1
        # The model sees only a temporary batch ordinal; persistent plan/source
        # IDs must remain in server-side mapping state.
        assert "plan_item_01" not in prompt
        assert "source_01" not in prompt
        return {
            "exercises": [{
                "batch_index": batch_index,
                "question_type": "综合题",
                "difficulty": "进阶",
                "target_skill": "完整推理",
                "variation_type": "情境变化",
                "stem": "完整题干",
                "options": [],
                "knowledge_points": ["知识点"],
                "verification_note": "已检查",
                "formulas": [],
                "tables": [],
                "figures": [],
            }, {
                "batch_index": 2,
                "question_type": "综合题",
                "difficulty": "挑战",
                "target_skill": "完整推理",
                "variation_type": "情境变化",
                "stem": "第二道完整题干",
                "options": [],
                "knowledge_points": ["知识点"],
                "verification_note": "已检查",
                "formulas": [],
                "tables": [],
                "figures": [],
            }]
        }

    payload = {
        "source_mode": "exam",
        "generation_strategy": "parallel_exam",
        "question_text": "原题材料",
        "selected_source_questions": [{"source_question_id": "source_01", "title": "原题"}],
        "plan": {
            "source_analysis": {"subject": "材料科学"},
            "selected_source_questions": [{"source_question_id": "source_01", "title": "原题"}],
            "blueprint": {
                "generation_strategy": "parallel_exam",
                "training_goal": "保持研究生层级与完整题目",
                "exercise_plan": [
                    {"plan_item_id": "plan_item_01", "source_question_id": "source_01", "question_type": "综合题", "difficulty": "进阶"},
                    {"plan_item_id": "plan_item_02", "source_question_id": "source_01", "question_type": "综合题", "difficulty": "挑战"},
                ],
            },
        },
    }
    provider = SimpleNamespace(name="test", supports_vision=False)
    with (
        patch("app.exercise_generation._primary_model_runtime", return_value=(provider, "test-model")),
        patch("app.exercise_generation.OpenAICompatibleClient", return_value=object()),
        patch("app.exercise_generation._call_practice_json", side_effect=fake_call),
    ):
        result = generate_practice_from_plan(payload)

    assert len(calls) == 1
    assert "第 1 至 2 题" in calls[0]
    assert '"batch_index": 1' in calls[0]
    assert "原题材料" in calls[0]
    assert "原题语义约束" in calls[0]
    assert [item["plan_item_id"] for item in result["exercises"]] == ["plan_item_01", "plan_item_02"]
    assert all(not item["parent_plan_item_id"] for item in result["exercises"])
    assert all("answer" not in item and "solution_steps" not in item for item in result["exercises"])
    assert "不得输出答案、解析" in calls[0]


def test_cancelled_job_does_not_submit_the_next_generation_batch(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(practice_jobs, "PRACTICE_JOB_DIR", tmp_path / "jobs")
    job = practice_jobs.create_practice_job("generate_from_plan", {"source_mode": "exam"})
    practice_jobs.update_practice_job(job["job_id"], status="running")
    calls = 0

    def fake_call(_client, _messages, **_kwargs):
        nonlocal calls
        calls += 1
        practice_jobs.cancel_practice_job(job["job_id"])
        return {"exercises": [{
            "batch_index": 1,
            "question_type": "综合题",
            "difficulty": "进阶",
            "target_skill": "完整推理",
            "variation_type": "情境变化",
            "stem": "完整题干",
            "options": [],
            "knowledge_points": ["知识点"],
            "verification_note": "已检查",
            "formulas": [],
            "tables": [],
            "figures": [],
        }]}

    exercise_plan = [
        {
            "plan_item_id": f"plan_item_{index:02d}",
            "source_question_id": "source_01",
            "question_type": "综合题",
            "difficulty": "进阶",
            "required_knowledge_points": ["知识点"],
        }
        for index in (1, 2)
    ]
    payload = {
        "_job_id": job["job_id"],
        "source_mode": "exam",
        "generation_strategy": "parallel_exam",
        "question_text": "原题材料",
        "generation_batch_size": 1,
        "generation_max_concurrency": 1,
        "plan": {
            "source_analysis": {"subject": "测试学科"},
            "blueprint": {
                "generation_strategy": "parallel_exam",
                "training_goal": "完整题目",
                "exercise_plan": exercise_plan,
            },
        },
    }
    provider = SimpleNamespace(name="test", supports_vision=False)

    with (
        patch("app.exercise_generation._primary_model_runtime", return_value=(provider, "test-model")),
        patch("app.exercise_generation.OpenAICompatibleClient", return_value=object()),
        patch("app.exercise_generation._call_practice_json", side_effect=fake_call),
    ):
        try:
            generate_practice_from_plan(payload)
        except PracticeGenerationStopped:
            pass
        else:
            raise AssertionError("cancelled generation unexpectedly continued")

    assert calls == 1


def test_transport_failure_splits_batch_and_recovers_each_question() -> None:
    calls = 0

    def fake_call(_client, _messages, **_kwargs):
        nonlocal calls
        calls += 1
        if calls <= 2:
            raise LLMError("Provider streaming response ended without a completed response")
        label = "甲" if calls == 3 else "乙"
        return {"exercises": [{
            "batch_index": 1,
            "question_type": "简答题",
            "difficulty": "进阶",
            "target_skill": f"{label}类条件分析",
            "variation_type": "条件迁移",
            "stem": f"{label}类研究情境中，根据完整边界条件分析状态变化，并说明判断所依据的核心概念。",
            "options": [],
            "knowledge_points": [f"知识点{label}"],
            "verification_note": "题干条件完整。",
            "formulas": [],
            "tables": [],
            "figures": [],
        }]}

    payload = {
        "question_text": "用于生成两道独立练习题的来源材料。",
        "generation_batch_size": 2,
        "generation_concurrency": 1,
        "generation_transport_attempts": 2,
        "generation_retry_backoff_seconds": 0,
        "plan": {"blueprint": {"exercise_plan": [
            {
                "plan_item_id": "plan_item_01",
                "question_type": "简答题",
                "difficulty": "进阶",
                "required_knowledge_points": ["知识点甲"],
            },
            {
                "plan_item_id": "plan_item_02",
                "question_type": "简答题",
                "difficulty": "进阶",
                "required_knowledge_points": ["知识点乙"],
            },
        ]}},
    }
    provider = SimpleNamespace(name="test", supports_vision=False)
    with (
        patch("app.exercise_generation._primary_model_runtime", return_value=(provider, "test-model")),
        patch("app.exercise_generation.OpenAICompatibleClient", return_value=object()),
        patch("app.exercise_generation._call_practice_json", side_effect=fake_call),
    ):
        result = generate_practice_from_plan(payload)

    assert calls == 4
    assert result["generation"]["failed_count"] == 0
    assert [item["plan_item_id"] for item in result["exercises"]] == ["plan_item_01", "plan_item_02"]
    diagnostic = result["generation"]["batch_diagnostics"][0]
    assert diagnostic["status"] == "recovered_by_single_item_split"
    assert len(diagnostic["split_recovery"]) == 2


def test_generation_retries_missing_choice_options_without_requiring_answers() -> None:
    calls: list[str] = []

    def exercise(batch_index: int, *, options: list[dict[str, str]] | None = None) -> dict:
        return {
            "batch_index": batch_index,
            "question_type": "单选题" if batch_index == 1 else "填空题",
            "difficulty": "进阶",
            "target_skill": "结构完整性",
            "variation_type": "条件迁移",
            "stem": "在给定边界下选择正确结论。" if batch_index == 1 else "该过程的状态量变化为____。",
            "options": options or [],
            "knowledge_points": ["结构校验"],
            "verification_note": "题干条件完整。",
        }

    def fake_call(_client, messages, **_kwargs):
        prompt = str(messages[-1]["content"])
        calls.append(prompt)
        if len(calls) == 1:
            # The provider returned the two ordinals but omitted the choice
            # options. The fill-in item intentionally has no answer field.
            return {"exercises": [exercise(1), exercise(2)]}
        assert "单选题缺少有效选项" in prompt
        return {"exercises": [
            exercise(1, options=[{"text": "结论甲"}, {"text": "结论乙"}]),
            exercise(2),
        ]}

    payload = {
        "question_text": "用于生成两道练习题的来源材料。",
        "generation_batch_size": 2,
        "generation_concurrency": 1,
        "plan": {"blueprint": {"exercise_plan": [
            {
                "plan_item_id": "plan_item_01",
                "question_type": "单选题",
                "difficulty": "进阶",
                "required_knowledge_points": ["结构校验"],
            },
            {
                "plan_item_id": "plan_item_02",
                "question_type": "填空题",
                "difficulty": "进阶",
                "required_knowledge_points": ["结构校验"],
            },
        ]}},
    }
    provider = SimpleNamespace(name="test", supports_vision=False)
    with (
        patch("app.exercise_generation._primary_model_runtime", return_value=(provider, "test-model")),
        patch("app.exercise_generation.OpenAICompatibleClient", return_value=object()),
        patch("app.exercise_generation._call_practice_json", side_effect=fake_call),
    ):
        result = generate_practice_from_plan(payload)

    assert len(calls) == 2
    assert result["quality"]["status"] == "passed"
    assert [item["generation_status"] for item in result["exercises"]] == ["completed", "completed"]
    assert all("answer" not in item for item in result["exercises"])
    assert result["exercises"][1]["options"] == []


def test_quality_uses_blueprint_type_and_allows_answerless_fill_in() -> None:
    practice = {
        "requested_count": 2,
        "blueprint": {"exercise_plan": [
            {"plan_item_id": "plan_item_01", "question_type": "单选题"},
            {"plan_item_id": "plan_item_02", "question_type": "填空题"},
        ]},
        "exercises": [
            {
                "number": 1,
                "plan_item_id": "plan_item_01",
                "stem": "请选择正确结论。",
                "options": [{"text": "甲"}, {"text": "乙"}],
            },
            {
                "number": 2,
                "plan_item_id": "plan_item_02",
                "stem": "该量的符号为____。",
                "options": [],
            },
        ],
    }

    quality = recompute_practice_quality(practice)

    assert quality["status"] == "passed"
    assert not any("答案" in issue for issue in quality["blocking_issues"])


def test_generation_repairs_only_the_colliding_question() -> None:
    prompts: list[str] = []
    duplicate_stem = (
        "某密闭热力学系统从初态经过一个可逆过程到达终态，"
        "已知温度压力与吸热量，要求判断过程并计算系统熵变。"
    )

    def fake_call(_client, messages, **_kwargs):
        prompt = str(messages[-1]["content"])
        prompts.append(prompt)
        if "只重新生成" in prompt:
            return {"exercises": [{
                "question_type": "简答题",
                "difficulty": "进阶",
                "target_skill": "判断熵变方向",
                "variation_type": "比较与优化",
                "stem": "给出两种不同的热量交换方案，比较它们的熵产生并选择更接近可逆极限的方案。",
                "options": [],
                "knowledge_points": ["熵变"],
                "verification_note": "已给出比较所需的完整条件。",
                "diversity_signature": {
                    "scenario_family": "两种换热方案",
                    "asked_quantity": "熵产生较小的方案",
                    "solution_family": "分别建立熵产生并比较",
                    "cognitive_operation": "比较",
                },
            }]}
        return {"exercises": [
            {
                "batch_index": index,
                "question_type": "简答题",
                "difficulty": "进阶",
                "target_skill": "判断熵变方向",
                "variation_type": "应用情境",
                "stem": duplicate_stem,
                "options": [],
                "knowledge_points": ["熵变"],
                "verification_note": "条件充分。",
                "diversity_signature": {
                    "scenario_family": "密闭系统可逆过程",
                    "asked_quantity": "系统熵变",
                    "solution_family": "由吸热量计算熵变",
                    "cognitive_operation": "计算",
                },
            }
            for index in (1, 2)
        ]}

    source = {
        "source_question_id": "source_01",
        "title": "熵变来源",
        "source_content": "判断热力学过程中的熵变。",
        "knowledge_points": ["熵变"],
    }
    payload = {
        "source_mode": "exam",
        "generation_strategy": "targeted_set",
        "question_text": "判断热力学过程中的熵变。",
        "generation_batch_size": 2,
        "generation_concurrency": 1,
        "plan": {
            "source_mode": "exam",
            "source_scope": {"mode": "question_set", "questions": [source]},
            "selected_source_questions": [source],
            "blueprint": {
                "generation_strategy": "targeted_set",
                "exercise_plan": [
                    {
                        "plan_item_id": f"plan_item_{index:02d}",
                        "source_question_id": "source_01",
                        "source_refs": ["source_01"],
                        "question_type": "简答题",
                        "difficulty": "进阶",
                        "target_skill": "判断熵变方向",
                        "structural_change": "比较与优化" if index == 2 else "跨情境迁移",
                        "required_knowledge_points": ["熵变"],
                    }
                    for index in (1, 2)
                ],
            },
        },
    }
    provider = SimpleNamespace(name="test", supports_vision=False)
    with (
        patch("app.exercise_generation._primary_model_runtime", return_value=(provider, "test-model")),
        patch("app.exercise_generation.OpenAICompatibleClient", return_value=object()),
        patch("app.exercise_generation._call_practice_json", side_effect=fake_call),
    ):
        result = generate_practice_from_plan(payload)

    assert len(prompts) == 2
    assert result["diversity_repair"]["status"] == "passed_after_repair"
    assert result["diversity_repair"]["attempts"][0]["exercise_index"] == 1
    assert result["quality"]["checks"]["content_diversity"] is True
    assert result["generation"]["status"] == "completed"
    assert result["generation"]["diversity_repair_call_count"] == 1
    assert result["generation"]["prompt_char_count_total"] > 0


def test_generation_keeps_successful_siblings_when_one_item_fails_a_quality_gate() -> None:
    calls = 0

    def fake_call(_client, messages, **_kwargs):
        nonlocal calls
        calls += 1
        if "只修复下面这道题的题干配图数据" in str(messages[-1]["content"]):
            return {"figures": []}
        return {
            "exercises": [
                {
                    "batch_index": 1,
                    "question_type": "简答题",
                    "difficulty": "进阶",
                    "target_skill": "读取循环图",
                    "variation_type": "图示应用",
                    "stem": "根据附图回答循环过程问题。",
                    "options": [],
                    "knowledge_points": ["循环过程"],
                    "verification_note": "已检查",
                    "formulas": [],
                    "tables": [],
                    "figures": [],
                },
                {
                    "batch_index": 2,
                    "question_type": "简答题",
                    "difficulty": "进阶",
                    "target_skill": "区分状态函数",
                    "variation_type": "概念辨析",
                    "stem": "说明内能为何属于状态函数。",
                    "options": [],
                    "knowledge_points": ["状态函数"],
                    "verification_note": "已检查",
                    "formulas": [],
                    "tables": [],
                    "figures": [],
                },
            ]
        }

    payload = {
        "source_mode": "exam",
        "generation_strategy": "parallel_exam",
        "question_text": "用于生成两个独立热力学练习题的原始材料。",
        "generation_batch_size": 2,
        "generation_concurrency": 1,
        "plan": {
            "source_analysis": {"subject": "物理化学"},
            "blueprint": {
                "generation_strategy": "parallel_exam",
                "exercise_plan": [
                    {
                        "plan_item_id": "plan_item_01",
                        "question_type": "简答题",
                        "difficulty": "进阶",
                        "target_skill": "读取循环图",
                        "required_knowledge_points": ["循环过程"],
                        "stem_figure_required": True,
                        "figure_design": {"required_elements": ["P-V坐标系"]},
                    },
                    {
                        "plan_item_id": "plan_item_02",
                        "question_type": "简答题",
                        "difficulty": "进阶",
                        "target_skill": "区分状态函数",
                        "required_knowledge_points": ["状态函数"],
                    },
                ],
            },
        },
    }
    provider = SimpleNamespace(name="test", supports_vision=False)
    with (
        patch("app.exercise_generation._primary_model_runtime", return_value=(provider, "test-model")),
        patch("app.exercise_generation.OpenAICompatibleClient", return_value=object()),
        patch("app.exercise_generation._call_practice_json", side_effect=fake_call),
    ):
        result = generate_practice_from_plan(payload)

    assert calls == 2  # The failing batch received its one automatic repair request.
    assert [item["generation_status"] for item in result["exercises"]] == ["failed", "completed"]
    assert result["exercises"][0]["generation_error"]["code"] == "generation_quality_gate_failed"
    assert result["exercises"][0]["generation_error"]["message"] == "蓝图要求题干配图，但模型没有返回可用题图。"
    assert [row["plan_item_id"] for row in result["generation"]["batch_errors"]] == ["plan_item_01"]


def test_figure_only_repair_preserves_question_text_and_healthy_sibling() -> None:
    calls: list[str] = []
    repair_call_options: list[dict] = []

    def fake_call(_client, messages, **_kwargs):
        prompt = str(messages[-1]["content"])
        calls.append(prompt)
        if "只修复下面这道题的题干配图数据" in prompt:
            repair_call_options.append(_kwargs)
            return {"figures": [{
                "figure_id": "g1",
                "figure_type": "line",
                "title": "P-V图",
                "x_label": "V",
                "y_label": "P",
                "series": [{"name": "过程曲线", "points": [[1, 2], [1.2, 1.7], [1.4, 1.45], [1.7, 1.2], [2, 1]]}],
                "nodes": [],
                "edges": [],
            }]}
        return {"exercises": [
            {
                "batch_index": 1,
                "question_type": "简答题",
                "difficulty": "进阶",
                "target_skill": "读取过程图",
                "variation_type": "图示应用",
                "stem": "根据附图回答过程问题。",
                "options": [],
                "knowledge_points": ["过程"],
                "verification_note": "已检查",
                "formulas": [],
                "tables": [],
                "figures": [],
            },
            {
                "batch_index": 2,
                "question_type": "简答题",
                "difficulty": "进阶",
                "target_skill": "概念辨析",
                "variation_type": "概念应用",
                "stem": "说明状态函数的含义。",
                "options": [],
                "knowledge_points": ["状态函数"],
                "verification_note": "已检查",
                "formulas": [],
                "tables": [],
                "figures": [],
            },
        ]}

    payload = {
        "question_text": "用于生成两道练习题的来源材料。",
        "generation_batch_size": 2,
        "generation_concurrency": 1,
        "plan": {"blueprint": {"exercise_plan": [
            {
                "plan_item_id": "plan_item_01",
                "question_type": "简答题",
                "difficulty": "进阶",
                "required_knowledge_points": ["过程"],
                "stem_figure_required": True,
                "figure_design": {"required_elements": ["P-V坐标系"]},
            },
            {
                "plan_item_id": "plan_item_02",
                "question_type": "简答题",
                "difficulty": "进阶",
                "required_knowledge_points": ["状态函数"],
            },
        ]}},
    }
    provider = SimpleNamespace(name="test", supports_vision=False)
    with (
        patch("app.exercise_generation._primary_model_runtime", return_value=(provider, "test-model")),
        patch("app.exercise_generation.OpenAICompatibleClient", return_value=object()),
        patch("app.exercise_generation._call_practice_json", side_effect=fake_call),
    ):
        result = generate_practice_from_plan(payload)

    assert len(calls) == 2
    assert [item["generation_status"] for item in result["exercises"]] == ["completed", "completed"]
    assert result["exercises"][0]["stem"] == "根据附图回答过程问题。"
    assert result["exercises"][1]["stem"] == "说明状态函数的含义。"
    assert result["exercises"][0]["figure_generation"]["status"] == "repaired"
    assert result["exercises"][0]["figure_generation"]["repair_attempted"] is True
    assert repair_call_options[0]["thinking"] == "disabled"
    assert repair_call_options[0]["timeout_seconds"] == 120


def test_partial_batch_keeps_returned_item_and_recovers_each_missing_slot() -> None:
    calls: list[str] = []

    def exercise(batch_index: int, label: str) -> dict:
        return {
            "batch_index": batch_index,
            "question_type": "简答题",
            "difficulty": "进阶",
            "target_skill": f"能力{label}",
            "variation_type": "概念应用",
            "stem": f"这是第{label}道完整题干。",
            "options": [],
            "knowledge_points": [f"知识点{label}"],
            "verification_note": "已检查",
            "formulas": [],
            "tables": [],
            "figures": [],
        }

    def fake_call(_client, messages, **_kwargs):
        prompt = str(messages[-1]["content"])
        calls.append(prompt)
        if "只补生成这一项" not in prompt:
            return {"exercises": [exercise(3, "三")]}
        if "batch_index=1" in prompt:
            return {"exercises": [exercise(1, "一")]}
        if "batch_index=2" in prompt:
            return {"exercises": [exercise(2, "二")]}
        raise AssertionError(f"未识别的补生请求：{prompt}")

    payload = {
        "question_text": "用于生成三道独立练习题的来源材料。",
        "generation_batch_size": 3,
        "generation_concurrency": 1,
        "plan": {"blueprint": {"exercise_plan": [
            {
                "plan_item_id": f"plan_item_{index:02d}",
                "question_type": "简答题",
                "difficulty": "进阶",
                "required_knowledge_points": [f"知识点{label}"],
            }
            for index, label in enumerate(("一", "二", "三"), start=1)
        ]}},
    }
    provider = SimpleNamespace(name="test", supports_vision=False)
    with (
        patch("app.exercise_generation._primary_model_runtime", return_value=(provider, "test-model")),
        patch("app.exercise_generation.OpenAICompatibleClient", return_value=object()),
        patch("app.exercise_generation._call_practice_json", side_effect=fake_call),
    ):
        result = generate_practice_from_plan(payload)

    assert len(calls) == 3
    assert [item["generation_status"] for item in result["exercises"]] == ["completed"] * 3
    assert result["exercises"][2]["stem"] == "这是第三道完整题干。"
    diagnostic = result["generation"]["batch_diagnostics"][0]
    assert diagnostic["response_attempts"][0]["actual_indexes"] == [3]
    assert diagnostic["response_attempts"][0]["missing_indexes"] == [1, 2]
    assert diagnostic["response_attempts"][0]["final_indexes"] == [1, 2, 3]
    assert diagnostic["recovery_attempt_count"] == 2
    assert diagnostic["status"] == "completed"


def test_content_gate_retries_only_invalid_question_and_preserves_healthy_sibling() -> None:
    calls: list[str] = []

    def short_answer() -> dict:
        return {
            "batch_index": 1,
            "question_type": "简答题",
            "difficulty": "基础",
            "target_skill": "结构辨析",
            "variation_type": "概念比较",
            "stem": "比较两种晶体结构的配位环境。",
            "options": [],
            "knowledge_points": ["配位数"],
            "verification_note": "条件充分",
            "formulas": [],
            "tables": [],
            "figures": [],
        }

    def choice(options: list[str]) -> dict:
        return {
            "batch_index": 2,
            "question_type": "单选题",
            "difficulty": "进阶",
            "target_skill": "致密度判断",
            "variation_type": "定量判断",
            "stem": "下列关于晶体致密度的判断正确的是哪一项？",
            "options": options,
            "knowledge_points": ["致密度"],
            "verification_note": "条件充分",
            "formulas": [],
            "tables": [],
            "figures": [],
        }

    healthy_initial = short_answer()

    def fake_call(_client, messages, **_kwargs):
        prompt = str(messages[-1]["content"])
        calls.append(prompt)
        if "只修复这一题" in prompt:
            assert "batch_index=2" in prompt
            return {"exercises": [choice(["A. 判断一", "B. 判断二", "C. 判断三", "D. 判断四"])]}
        return {"exercises": [healthy_initial, choice([])]}

    payload = {
        "source_mode": "knowledge",
        "question_text": "面心立方与体心立方晶体结构的配位数和致密度",
        "generation_batch_size": 2,
        "generation_concurrency": 1,
        "plan": {"blueprint": {"exercise_plan": [
            {
                "plan_item_id": "plan_item_01",
                "question_type": "简答题",
                "difficulty": "基础",
                "required_knowledge_points": ["配位数"],
            },
            {
                "plan_item_id": "plan_item_02",
                "question_type": "单选题",
                "difficulty": "进阶",
                "required_knowledge_points": ["致密度"],
            },
        ]}},
    }
    provider = SimpleNamespace(name="test", supports_vision=False)
    with (
        patch("app.exercise_generation._primary_model_runtime", return_value=(provider, "test-model")),
        patch("app.exercise_generation.OpenAICompatibleClient", return_value=object()),
        patch("app.exercise_generation._call_practice_json", side_effect=fake_call),
    ):
        result = generate_practice_from_plan(payload)

    assert len(calls) == 2
    assert [item["generation_status"] for item in result["exercises"]] == ["completed", "completed"]
    assert result["exercises"][0]["stem"] == healthy_initial["stem"]
    assert len(result["exercises"][1]["options"]) == 4
    diagnostic = result["generation"]["batch_diagnostics"][0]
    assert diagnostic["content_gate_retry_targets"] == [2]
    assert diagnostic["content_gate_retries"][0]["status"] == "repaired"


def test_partial_batch_fails_only_slot_still_missing_after_two_recovery_attempts() -> None:
    calls: list[str] = []

    def exercise(batch_index: int, label: str) -> dict:
        return {
            "batch_index": batch_index,
            "question_type": "简答题",
            "difficulty": "进阶",
            "target_skill": f"能力{label}",
            "variation_type": "概念应用",
            "stem": f"这是第{label}道完整题干。",
            "options": [],
            "knowledge_points": [f"知识点{label}"],
            "verification_note": "已检查",
            "formulas": [],
            "tables": [],
            "figures": [],
        }

    def fake_call(_client, messages, **_kwargs):
        prompt = str(messages[-1]["content"])
        calls.append(prompt)
        if "只补生成这一项" not in prompt:
            return {"exercises": [exercise(2, "二"), exercise(3, "三")]}
        # The provider keeps returning the wrong temporary ordinal. The two
        # healthy siblings must survive and only slot 1 may become failed.
        return {"exercises": [exercise(2, "二")]}

    payload = {
        "question_text": "用于生成三道独立练习题的来源材料。",
        "generation_batch_size": 3,
        "generation_concurrency": 1,
        "plan": {"blueprint": {"exercise_plan": [
            {
                "plan_item_id": f"plan_item_{index:02d}",
                "question_type": "简答题",
                "difficulty": "进阶",
                "required_knowledge_points": [f"知识点{label}"],
            }
            for index, label in enumerate(("一", "二", "三"), start=1)
        ]}},
    }
    provider = SimpleNamespace(name="test", supports_vision=False)
    with (
        patch("app.exercise_generation._primary_model_runtime", return_value=(provider, "test-model")),
        patch("app.exercise_generation.OpenAICompatibleClient", return_value=object()),
        patch("app.exercise_generation._call_practice_json", side_effect=fake_call),
    ):
        result = generate_practice_from_plan(payload)

    assert len(calls) == 3
    assert [item["generation_status"] for item in result["exercises"]] == ["failed", "completed", "completed"]
    failed = result["exercises"][0]["generation_error"]
    assert failed["code"] == "generation_response_invalid"
    assert failed["message"] == "模型未完整返回本题，逐题补生后仍未成功。"
    assert "首次实际返回 [2, 3]" in failed["detail"]
    assert "经 2 次独立补生仍未返回" in failed["detail"]
    assert [row["plan_item_id"] for row in result["generation"]["batch_errors"]] == ["plan_item_01"]
    diagnostic = result["generation"]["batch_diagnostics"][0]
    assert diagnostic["final_accepted_indexes"] == [2, 3]
    assert diagnostic["failed_plan_item_ids"] == ["plan_item_01"]
    assert diagnostic["status"] == "partial_success"


def test_duplicate_batch_index_is_recovered_without_replacing_healthy_sibling() -> None:
    calls: list[str] = []

    def exercise(batch_index: int, stem: str, knowledge_point: str) -> dict:
        return {
            "batch_index": batch_index,
            "question_type": "简答题",
            "difficulty": "进阶",
            "target_skill": "概念应用",
            "variation_type": "条件变化",
            "stem": stem,
            "options": [],
            "knowledge_points": [knowledge_point],
            "verification_note": "已检查",
            "formulas": [],
            "tables": [],
            "figures": [],
        }

    def fake_call(_client, messages, **_kwargs):
        prompt = str(messages[-1]["content"])
        calls.append(prompt)
        if "只补生成这一项" in prompt:
            assert "batch_index=1" in prompt
            return {"exercises": [exercise(1, "补生后的第一题。", "知识点一")]}
        return {"exercises": [
            exercise(1, "重复候选甲。", "知识点一"),
            exercise(1, "重复候选乙。", "知识点一"),
            exercise(2, "应保留的第二题。", "知识点二"),
        ]}

    payload = {
        "question_text": "用于生成两道独立练习题的来源材料。",
        "generation_batch_size": 2,
        "generation_concurrency": 1,
        "plan": {"blueprint": {"exercise_plan": [
            {
                "plan_item_id": "plan_item_01",
                "question_type": "简答题",
                "difficulty": "进阶",
                "required_knowledge_points": ["知识点一"],
            },
            {
                "plan_item_id": "plan_item_02",
                "question_type": "简答题",
                "difficulty": "进阶",
                "required_knowledge_points": ["知识点二"],
            },
        ]}},
    }
    provider = SimpleNamespace(name="test", supports_vision=False)
    with (
        patch("app.exercise_generation._primary_model_runtime", return_value=(provider, "test-model")),
        patch("app.exercise_generation.OpenAICompatibleClient", return_value=object()),
        patch("app.exercise_generation._call_practice_json", side_effect=fake_call),
    ):
        result = generate_practice_from_plan(payload)

    assert len(calls) == 2
    assert [item["stem"] for item in result["exercises"]] == ["补生后的第一题。", "应保留的第二题。"]
    diagnostic = result["generation"]["batch_diagnostics"][0]["response_attempts"][0]
    assert diagnostic["actual_indexes"] == [1, 1, 2]
    assert diagnostic["duplicate_indexes"] == [1]
    assert diagnostic["final_indexes"] == [1, 2]


def test_blueprint_multi_question_mode_generates_progressive_sibling_variants() -> None:
    calls: list[str] = []

    def fake_call(_client, messages, **_kwargs):
        prompt = str(messages[-1]["content"])
        calls.append(prompt)
        assert '"variant_role": "基础巩固"' in prompt
        assert '"variant_role": "条件转换"' in prompt
        assert '"variant_role": "综合迁移"' in prompt
        return {"exercises": [
            {
                "batch_index": 1,
                "question_type": "简答题",
                "difficulty": "基础",
                "target_skill": "理解状态函数",
                "variation_type": "基础巩固",
                "stem": "给出封闭体系从状态甲变化到状态乙的直接条件，说明判断内能变化所需的状态量。",
                "options": [],
                "knowledge_points": ["状态函数"],
                "verification_note": "条件充分",
                "formulas": [],
                "tables": [],
                "figures": [],
            },
            {
                "batch_index": 2,
                "question_type": "简答题",
                "difficulty": "进阶",
                "target_skill": "理解状态函数",
                "variation_type": "条件转换",
                "stem": "某热力学体系沿两条不同路径到达同一终态，请比较路径信息与内能改变量判断之间的关系。",
                "options": [],
                "knowledge_points": ["状态函数"],
                "verification_note": "条件充分",
                "formulas": [],
                "tables": [],
                "figures": [],
            },
            {
                "batch_index": 3,
                "question_type": "简答题",
                "difficulty": "挑战",
                "target_skill": "理解状态函数",
                "variation_type": "综合迁移",
                "stem": "设计一个包含循环过程与非循环过程的实验情境，要求评价哪些测量结果能够验证内能的状态函数性质。",
                "options": [],
                "knowledge_points": ["状态函数"],
                "verification_note": "条件充分",
                "formulas": [],
                "tables": [],
                "figures": [],
            },
        ]}

    payload = {
        "question_text": "状态函数相关材料。",
        "generation_concurrency": 1,
        "blueprint_multi_question_enabled": True,
        "blueprint_variants_per_item": 3,
        "blueprint_variant_mode": "progressive",
        "plan": {"blueprint": {
            "training_goal": "理解状态函数",
            "exercise_plan": [{
                "plan_item_id": "plan_item_01",
                "question_type": "简答题",
                "difficulty": "进阶",
                "target_skill": "理解状态函数",
                "variation_type": "概念应用",
                "design_intent": "从不同条件理解状态函数。",
                "required_knowledge_points": ["状态函数"],
            }],
        }},
    }
    provider = SimpleNamespace(name="test", supports_vision=False)
    with (
        patch("app.exercise_generation._primary_model_runtime", return_value=(provider, "test-model")),
        patch("app.exercise_generation.OpenAICompatibleClient", return_value=object()),
        patch("app.exercise_generation._call_practice_json", side_effect=fake_call),
    ):
        result = generate_practice_from_plan(payload)

    assert len(calls) == 1
    assert result["requested_count"] == 3
    assert len(result["blueprint"]["exercise_plan"]) == 1
    assert result["blueprint_multi_question"] == {
        "enabled": True,
        "variants_per_item": 3,
        "mode": "progressive",
        "difficulty_precedence": "progressive",
        "base_item_count": 1,
        "total_count": 3,
    }
    assert [item["difficulty"] for item in result["exercises"]] == ["基础", "进阶", "挑战"]
    assert [item["parent_plan_item_id"] for item in result["exercises"]] == ["plan_item_01"] * 3
    assert [item["variant_index"] for item in result["exercises"]] == [1, 2, 3]
    assert len({item["plan_item_id"] for item in result["exercises"]}) == 3
    assert result["variant_groups"][0]["exercise_ids"] == ["practice_01", "practice_02", "practice_03"]
    assert result["quality"]["status"] == "passed"


def test_final_all_challenge_choice_overrides_progressive_variant_difficulties() -> None:
    exercise_plan = [{
        "plan_item_id": "plan_item_01",
        "question_type": "综合题",
        "difficulty": "挑战",
        "target_skill": "跨条件综合推理",
        "variation_type": "综合迁移",
        "design_intent": "保持挑战层级，改变条件与推理路径。",
    }]
    payload = {
        "difficulty_counts": {"基础": 0, "进阶": 0, "挑战": 1},
        "difficulty_selection_order": 8,
        "blueprint_multi_question_enabled": True,
        "blueprint_variants_per_item": 3,
        "blueprint_variant_mode": "progressive",
        "blueprint_variant_selection_order": 5,
    }

    config = _blueprint_multi_question_config(payload, {"exercise_plan": exercise_plan})
    expanded = _expand_blueprint_items_for_generation(exercise_plan, config)

    assert config["difficulty_precedence"] == "confirmed_counts"
    assert [item["difficulty"] for item in expanded] == ["挑战", "挑战", "挑战"]
    assert [item["variant_role"] for item in expanded] == ["核心巩固", "条件转换", "综合迁移"]


def test_reconciled_generation_uses_current_questions_not_stale_initial_errors() -> None:
    repaired = reconcile_practice_generation({
        "exercises": [
            {"plan_item_id": "plan_item_01", "question_type": "简答题", "stem": "修复后的第一题。"},
            {"plan_item_id": "plan_item_02", "question_type": "简答题", "stem": "修复后的第二题。"},
        ],
        "generation": {
            "status": "partial_success",
            "partial_success": True,
            "generated_count": 0,
            "failed_count": 2,
            "batch_errors": [{"plan_item_id": "plan_item_01", "message": "历史错误"}],
        },
    })

    assert repaired["generation"]["status"] == "completed"
    assert repaired["generation"]["partial_success"] is False
    assert repaired["generation"]["generated_count"] == 2
    assert repaired["generation"]["failed_count"] == 0
    assert repaired["generation"]["batch_errors"] == []

    malformed = reconcile_practice_generation({
        "exercises": [{"question_type": "单选题", "stem": "请选择正确结论。", "options": []}],
    })
    assert malformed["quality"]["failed_count"] == 1
    assert malformed["generation"]["status"] == "partial_success"


def test_blueprint_multi_question_total_is_advisory_not_limited_to_thirty() -> None:
    plan = [
        {
            "plan_item_id": f"plan_item_{index:02d}",
            "question_type": "简答题",
            "difficulty": "进阶",
            "target_skill": "核心能力",
            "variation_type": "概念应用",
            "design_intent": "形成多角度训练。",
        }
        for index in range(1, 12)
    ]
    expanded = _expand_blueprint_items_for_generation(plan, {
        "enabled": True,
        "variants_per_item": 3,
        "mode": "same_difficulty",
    })

    assert len(expanded) == 33
    assert expanded[-1]["plan_item_id"] == "variant_plan_11_03"


def test_blueprint_multi_question_generation_does_not_truncate_above_thirty() -> None:
    calls = 0

    def fake_call(_client, _messages, **_kwargs):
        nonlocal calls
        calls += 1
        batch_label = chr(0x4E00 + calls)
        stems = [
            f"{batch_label}类研究：根据给出的直接条件辨认研究对象的基本状态，并说明采用该判断方法所依据的核心概念。",
            f"{batch_label}类实验：将实验描述转换为适用的边界条件，比较两条不同分析路径并说明哪一条能够完成判断。",
            f"{batch_label}类评价：构造跨情境的综合评价任务，在保留核心知识点的前提下论证不同结论的适用范围。",
        ]
        return {"exercises": [
            {
                "batch_index": index,
                "question_type": "简答题",
                "difficulty": "进阶",
                "target_skill": "核心能力",
                "variation_type": "结构变式",
                "stem": stem,
                "options": [],
                "knowledge_points": ["共同知识点"],
                "verification_note": "条件充分",
                "formulas": [],
                "tables": [],
                "figures": [],
            }
            for index, stem in enumerate(stems, start=1)
        ]}

    payload = {
        "question_text": "共同知识点材料。",
        "generation_concurrency": 1,
        "blueprint_multi_question_enabled": True,
        "blueprint_variants_per_item": 3,
        "blueprint_variant_mode": "same_difficulty",
        "plan": {"blueprint": {
            "training_goal": "形成系统训练",
            "exercise_plan": [
                {
                    "plan_item_id": f"plan_item_{index:02d}",
                    "question_type": "简答题",
                    "difficulty": "进阶",
                    "target_skill": f"核心能力{index}",
                    "variation_type": "概念应用",
                    "design_intent": f"完成第{index}项训练。",
                    "required_knowledge_points": ["共同知识点"],
                }
                for index in range(1, 12)
            ],
        }},
    }
    provider = SimpleNamespace(name="test", supports_vision=False)
    with (
        patch("app.exercise_generation._primary_model_runtime", return_value=(provider, "test-model")),
        patch("app.exercise_generation.OpenAICompatibleClient", return_value=object()),
        patch("app.exercise_generation._call_practice_json", side_effect=fake_call),
    ):
        result = generate_practice_from_plan(payload)

    assert calls == 11
    assert result["requested_count"] == 33
    assert len(result["exercises"]) == 33
    assert result["exercises"][-1]["number"] == 33
    assert result["blueprint_multi_question"]["total_count"] == 33
    assert result["quality"]["status"] == "passed"


def test_sibling_variants_cannot_pass_by_only_replacing_numbers() -> None:
    plan = [
        {"parent_plan_item_id": "plan_item_01", "structural_change": "改变未知量"},
        {"parent_plan_item_id": "plan_item_01", "structural_change": "改变求解路径"},
    ]
    exercises = [
        {"batch_index": 1, "stem": "某体系在温度 300 K 下从状态甲变化到状态乙，已知压力和体积，计算过程功并说明依据。"},
        {"batch_index": 2, "stem": "某体系在温度 350 K 下从状态甲变化到状态乙，已知压力和体积，计算过程功并说明依据。"},
    ]

    issues = _batch_sibling_variant_issues(exercises, plan)

    assert {issue["batch_index"] for issue in issues} == {1, 2}
    assert all("仅替换数字或措辞" in issue["reason"] for issue in issues)
