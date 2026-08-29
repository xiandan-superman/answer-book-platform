from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def _academic_candidates(count: int) -> dict[str, Any]:
    return {
        "review_candidates": [
            {
                "candidate_id": f"expr_{index}",
                "question_id": f"q{index}",
                "code": "semantic_context_risk",
                "reason": "chemical_or_reaction_semantics_require_context",
                "expression_id": f"expr_{index}",
                "kind": "chemical_notation",
                "raw": rf"\ce{{A{index} -> B{index}}}",
                "normalized": rf"\ce{{A{index} -> B{index}}}",
                "location": "formulas[0].latex",
                "confidence": 1.0,
            }
            for index in range(1, count + 1)
        ]
    }


def _exam_and_fragments(count: int) -> tuple[dict[str, Any], dict[str, Any]]:
    return (
        {
            "items": [
                {"question_id": f"q{index}", "stem": f"判断反应 {index}。", "question_type": "简答题"}
                for index in range(1, count + 1)
            ]
        },
        {
            "fragments": [
                {
                    "question_id": f"q{index}",
                    "answer": "见解析",
                    "formulas": [{"formula_id": "f1", "latex": rf"\ce{{A{index} -> B{index}}}"}],
                    "blocks": [],
                }
                for index in range(1, count + 1)
            ]
        },
    )


class FakeReviewClient:
    def __init__(self, response: dict[str, Any] | None = None, error: Exception | None = None):
        self.response = response or {"decisions": []}
        self.error = error
        self.calls = 0
        self.payloads: list[dict[str, Any]] = []

    def chat_json_object(self, messages: list[dict[str, Any]], **_kwargs: object) -> dict[str, Any]:
        self.calls += 1
        content = messages[1]["content"]
        if isinstance(content, list):
            content = next(item["text"] for item in content if item.get("type") == "text")
        self.payloads.append(json.loads(str(content)))
        if self.error is not None:
            raise self.error
        return self.response


class SelectiveQualityReviewTests(unittest.TestCase):
    def test_formula_quote_grounding_accepts_equivalent_unicode_and_latex(self) -> None:
        from app.capabilities.selective_review import _compact_formula_quote

        answer = r'{"formulas":[{"latex":"\\Delta U=-2.26\\times10^3\\,\\mathrm{kJ}"}]}'
        quote = "ΔU=-2.26×10^3 kJ"

        self.assertIn(_compact_formula_quote(quote), _compact_formula_quote(answer))

    def test_no_candidates_means_no_model_call(self) -> None:
        from app.capabilities.selective_review import review_selective_quality

        client = FakeReviewClient()
        with tempfile.TemporaryDirectory() as raw_tmp:
            report = review_selective_quality(
                academic_report={},
                content_quality_report={},
                structured_exam={"items": []},
                fragments_data={"fragments": []},
                report_json=Path(raw_tmp) / "review.json",
                provider=SimpleNamespace(name="test", api_key="key", default_model="model"),
                model="model",
                client=client,
            )

        self.assertEqual("not_needed", report["status"])
        self.assertEqual(0, report["remote_model_calls_this_run"])
        self.assertEqual(0, client.calls)

    def test_candidates_are_truncated_and_sent_in_exactly_one_batch(self) -> None:
        from app.capabilities.selective_review import review_selective_quality

        exam, fragments = _exam_and_fragments(12)
        response = {
            "decisions": [
                {
                    "candidate_id": f"expr_{index}",
                    "decision": "pass",
                    "confidence": 0.95,
                    "reason": "与题意一致",
                    "suggested_fix": "",
                }
                for index in range(1, 9)
            ]
        }
        client = FakeReviewClient(response)
        with tempfile.TemporaryDirectory() as raw_tmp:
            report = review_selective_quality(
                academic_report=_academic_candidates(12),
                content_quality_report={},
                structured_exam=exam,
                fragments_data=fragments,
                report_json=Path(raw_tmp) / "review.json",
                provider=SimpleNamespace(name="test", api_key="key", default_model="model"),
                model="model",
                max_candidates=8,
                max_batches=1,
                client=client,
            )

        self.assertEqual(1, client.calls)
        self.assertEqual(1, report["batch_count"])
        self.assertEqual(8, report["selection"]["selected_count"])
        self.assertTrue(report["selection"]["truncated"])
        self.assertEqual(8, len(client.payloads[0]["candidates"]))

    def test_reviewer_receives_authoritative_requirement_and_coverage_manifests(self) -> None:
        from app.capabilities.selective_review import review_selective_quality

        exam = {
            "items": [
                {
                    "question_id": "q1",
                    "number": "1",
                    "stem": "计算组织组成并作图。",
                    "question_type": "计算题",
                    "subquestions": [
                        {
                            "number": "1",
                            "stem": "计算组织组成并作图。",
                            "requirements": [
                                {"number": "1.1", "stem": "计算组织组成"},
                                {"number": "1.2", "stem": "画出组织图"},
                            ],
                        }
                    ],
                }
            ]
        }
        fragments = {
            "fragments": [
                {
                    "question_id": "q1",
                    "answer": "见分项答案",
                    "formulas": [],
                    "blocks": [],
                    "answer_units": [
                        {"number": "1.1", "answer": "A 40%，B 60%", "steps": []},
                        {"number": "1.2", "answer": "", "figure_specs": [{"kind": "microstructure_schematic"}]},
                    ],
                    "calculation_contract": {"result_quantities": []},
                }
            ]
        }
        client = FakeReviewClient(
            {
                "decisions": [
                    {
                        "candidate_id": "expr_1",
                        "decision": "pass",
                        "confidence": 1,
                        "reason": "已覆盖",
                        "suggested_fix": "",
                    }
                ]
            }
        )
        with tempfile.TemporaryDirectory() as raw_tmp:
            review_selective_quality(
                academic_report=_academic_candidates(1),
                content_quality_report={},
                structured_exam=exam,
                fragments_data=fragments,
                report_json=Path(raw_tmp) / "review.json",
                provider=SimpleNamespace(name="test", api_key="key", default_model="model"),
                model="model",
                client=client,
            )

        question = client.payloads[0]["questions"][0]
        answer = client.payloads[0]["current_answers"][0]
        self.assertEqual(["1.1", "1.2"], [item["number"] for item in question["required_answer_units"]])
        self.assertTrue(answer["coverage_manifest"][1]["has_figure_spec"])
        self.assertIn("defects", client.payloads[0]["output_schema"]["decisions"][0])

    def test_reviewer_manifest_counts_main_model_generated_image_as_figure(self) -> None:
        from app.capabilities.selective_review import review_selective_quality

        exam, fragments = _exam_and_fragments(1)
        fragment = fragments["fragments"][0]
        fragment["generated_images"] = [
            {"asset_id": "img_sha256_example", "answer_unit_number": "1"}
        ]
        fragment["answer_units"] = [{"number": "1", "answer": "见图", "steps": []}]
        fragment["blocks"] = [
            {
                "label": "图示",
                "segments": [
                    {
                        "type": "image_ref",
                        "role": "answer_generated_figure",
                        "answer_unit_number": "1",
                        "path": "figures/q1.png",
                    }
                ],
            }
        ]
        client = FakeReviewClient(
            {"decisions": [{"candidate_id": "expr_1", "decision": "pass", "confidence": 1, "reason": "ok"}]}
        )
        with tempfile.TemporaryDirectory() as raw_tmp:
            review_selective_quality(
                academic_report=_academic_candidates(1),
                content_quality_report={},
                structured_exam=exam,
                fragments_data=fragments,
                report_json=Path(raw_tmp) / "review.json",
                provider=SimpleNamespace(name="test", api_key="key", default_model="model"),
                model="model",
                client=client,
            )

        answer = client.payloads[0]["current_answers"][0]
        self.assertTrue(answer["coverage_manifest"][0]["has_figure_spec"])

    def test_reviewer_receives_single_part_visible_reasoning_blocks(self) -> None:
        from app.capabilities.selective_review import review_selective_quality

        exam, fragments = _exam_and_fragments(1)
        fragments["fragments"][0]["blocks"] = [
            {"label": "解析", "segments": [{"type": "text", "text": "结论虽为不一定，但这里给出了完整因果理由。"}]}
        ]
        client = FakeReviewClient(
            {"decisions": [{"candidate_id": "expr_1", "decision": "pass", "confidence": 1, "reason": "ok"}]}
        )
        with tempfile.TemporaryDirectory() as raw_tmp:
            review_selective_quality(
                academic_report=_academic_candidates(1),
                content_quality_report={},
                structured_exam=exam,
                fragments_data=fragments,
                report_json=Path(raw_tmp) / "review.json",
                provider=SimpleNamespace(name="test", api_key="key", default_model="model"),
                model="model",
                client=client,
            )

        self.assertIn("完整因果理由", client.payloads[0]["current_answers"][0]["visible_blocks"][0]["text"])

    def test_reviewer_receives_confirmed_visual_facts_for_diagram_calculation(self) -> None:
        from app.capabilities.selective_review import review_selective_quality

        exam, fragments = _exam_and_fragments(1)
        exam["items"][0]["question_understanding"] = {
            "images": [
                {
                    "image_id": "img1",
                    "axes": {"x": "composition", "y": "temperature"},
                    "data_points": ["alloy=40", "eutectic=60"],
                    "answer_relevant_observations": ["hypoeutectic alloy"],
                    "uncertainties": ["right solubility approximately 95"],
                }
            ]
        }
        client = FakeReviewClient(
            {"decisions": [{"candidate_id": "expr_1", "decision": "pass", "confidence": 1, "reason": "ok"}]}
        )
        with tempfile.TemporaryDirectory() as raw_tmp:
            review_selective_quality(
                academic_report=_academic_candidates(1),
                content_quality_report={},
                structured_exam=exam,
                fragments_data=fragments,
                report_json=Path(raw_tmp) / "review.json",
                provider=SimpleNamespace(name="test", api_key="key", default_model="model"),
                model="model",
                client=client,
            )

        facts = client.payloads[0]["questions"][0]["confirmed_visual_facts"][0]
        self.assertEqual(["alloy=40", "eutectic=60"], facts["data_points"])
        self.assertEqual("composition", facts["axes"]["x"])

    def test_symbolic_contract_does_not_require_numeric_repair_ledger(self) -> None:
        from app.capabilities.selective_review import _decision_validation_context, _normalized_decisions

        candidates = [{"candidate_id": "c1", "question_id": "q1"}]
        questions = [{"question_id": "q1", "required_answer_units": [{"number": "1", "stem": "求面间距"}]}]
        fragments = [{
            "question_id": "q1",
            "calculation_contract": {"result_quantities": [{"quantity_id": "d", "value": "a/2"}]},
            "coverage_manifest": [{"number": "1", "answer": "面间距为a/2", "analysis_texts": [], "step_texts": []}],
        }]
        context = _decision_validation_context(candidates, questions, fragments, {"q1": []})
        decisions = _normalized_decisions(
            {"decisions": [{
                "candidate_id": "c1", "decision": "repair", "confidence": 0.9,
                "reason": "符号结果错误", "suggested_fix": "改为a",
                "defects": [{
                    "answer_unit_number": "1", "defect_kind": "incorrect",
                    "requirement_quote": "求面间距", "current_answer_quote": "面间距为a/2",
                }],
            }]},
            {"c1"}, context,
        )

        self.assertEqual("repair", decisions[0]["decision"])
        self.assertEqual([], decisions[0]["numeric_patch_validation_issues"])

    def test_reviewer_evidence_is_balanced_across_knowledge_points(self) -> None:
        from app.capabilities.selective_review import review_selective_quality

        exam, fragments = _exam_and_fragments(1)
        evidence = {
            "q1": [
                *[
                    {
                        "evidence_id": f"broad_{index}",
                        "knowledge_point": "一般原理",
                        "source_type": "text_block",
                        "evidence_text": f"一般原理 {index}",
                        "score": 100 - index,
                    }
                    for index in range(20)
                ],
                {
                    "evidence_id": "direct_late",
                    "knowledge_point": "目标结论",
                    "source_type": "text_block",
                    "evidence_text": "全部前驱体转变为目标产物。",
                    "score": 1,
                },
            ]
        }
        client = FakeReviewClient(
            {
                "decisions": [
                    {
                        "candidate_id": "expr_1",
                        "decision": "pass",
                        "confidence": 1,
                        "reason": "证据完整",
                    }
                ]
            }
        )
        with tempfile.TemporaryDirectory() as raw_tmp:
            review_selective_quality(
                academic_report=_academic_candidates(1),
                content_quality_report={},
                structured_exam=exam,
                fragments_data=fragments,
                report_json=Path(raw_tmp) / "review.json",
                provider=SimpleNamespace(name="test", api_key="key", default_model="model"),
                model="model",
                client=client,
                evidence_context=evidence,
            )

        packed = client.payloads[0]["confirmed_textbook_evidence_by_question"]["q1"]
        self.assertLessEqual(len(packed), 16)
        self.assertIn("direct_late", [item["evidence_id"] for item in packed])
        self.assertIn("knowledge_point", packed[0])

    def test_unchanged_review_is_reused_without_remote_call(self) -> None:
        from app.capabilities.selective_review import review_selective_quality

        exam, fragments = _exam_and_fragments(1)
        client = FakeReviewClient(
            {
                "decisions": [
                    {
                        "candidate_id": "expr_1",
                        "decision": "warn",
                        "confidence": 0.8,
                        "reason": "需保留反应条件",
                        "suggested_fix": "",
                    }
                ]
            }
        )
        with tempfile.TemporaryDirectory() as raw_tmp:
            output = Path(raw_tmp) / "review.json"
            kwargs: dict[str, Any] = {
                "academic_report": _academic_candidates(1),
                "content_quality_report": {},
                "structured_exam": exam,
                "fragments_data": fragments,
                "report_json": output,
                "provider": SimpleNamespace(name="test", api_key="key", default_model="model"),
                "model": "model",
                "client": client,
            }
            first = review_selective_quality(**kwargs)
            second = review_selective_quality(**kwargs)

        self.assertEqual(1, client.calls)
        self.assertFalse(first["cache"]["hit"])
        self.assertTrue(second["cache"]["hit"])
        self.assertEqual(0, second["remote_model_calls_this_run"])

    def test_degraded_review_is_retried_after_provider_recovers(self) -> None:
        from app.capabilities.selective_review import review_selective_quality

        exam, fragments = _exam_and_fragments(1)
        client = FakeReviewClient(error=TimeoutError("temporary outage"))
        with tempfile.TemporaryDirectory() as raw_tmp:
            output = Path(raw_tmp) / "review.json"
            kwargs: dict[str, Any] = {
                "academic_report": _academic_candidates(1),
                "content_quality_report": {},
                "structured_exam": exam,
                "fragments_data": fragments,
                "report_json": output,
                "provider": SimpleNamespace(name="test", api_key="key", default_model="model"),
                "model": "model",
                "client": client,
            }
            first = review_selective_quality(**kwargs)
            client.error = None
            client.response = {
                "decisions": [
                    {"candidate_id": "expr_1", "decision": "pass", "confidence": 1, "reason": "ok"}
                ]
            }
            second = review_selective_quality(**kwargs)

        self.assertEqual("degraded", first["status"])
        self.assertEqual("completed", second["status"])
        self.assertFalse(second["cache"]["hit"])
        self.assertEqual(2, client.calls)

    def test_unavailable_or_failed_reviewer_degrades_without_blocking(self) -> None:
        from app.capabilities.selective_review import review_selective_quality

        exam, fragments = _exam_and_fragments(1)
        with tempfile.TemporaryDirectory() as raw_tmp:
            root = Path(raw_tmp)
            unavailable = review_selective_quality(
                academic_report=_academic_candidates(1),
                content_quality_report={},
                structured_exam=exam,
                fragments_data=fragments,
                report_json=root / "unavailable.json",
                provider=SimpleNamespace(name="test", api_key="", default_model="model"),
                model="model",
            )
            failed = review_selective_quality(
                academic_report=_academic_candidates(1),
                content_quality_report={},
                structured_exam=exam,
                fragments_data=fragments,
                report_json=root / "failed.json",
                provider=SimpleNamespace(name="test", api_key="key", default_model="model"),
                model="model",
                client=FakeReviewClient(error=TimeoutError("timeout")),
            )

        self.assertTrue(unavailable["ok"])
        self.assertEqual("degraded", unavailable["status"])
        self.assertEqual(0, unavailable["remote_model_calls_this_run"])
        self.assertTrue(failed["ok"])
        self.assertEqual("degraded", failed["status"])
        self.assertEqual(1, failed["remote_model_calls_this_run"])
        self.assertEqual([], failed["decisions"])
        self.assertTrue(all(row["code"] == "reviewer_unavailable" for row in failed["warnings"]))

    def test_compact_retry_is_opt_in_and_counts_both_real_requests(self) -> None:
        from app.capabilities.selective_review import review_selective_quality

        exam, fragments = _exam_and_fragments(1)
        client = FakeReviewClient(error=TimeoutError("timeout"))
        with tempfile.TemporaryDirectory() as raw_tmp:
            report = review_selective_quality(
                academic_report=_academic_candidates(1),
                content_quality_report={},
                structured_exam=exam,
                fragments_data=fragments,
                report_json=Path(raw_tmp) / "failed.json",
                provider=SimpleNamespace(name="test", api_key="key", default_model="model"),
                model="model",
                max_attempts_per_batch=2,
                client=client,
            )

        self.assertEqual(2, client.calls)
        self.assertEqual(2, report["remote_model_calls_this_run"])

    def test_shadow_only_review_never_calls_provider_or_emits_service_warning(self) -> None:
        from app.capabilities.selective_review import review_selective_quality

        exam, fragments = _exam_and_fragments(1)
        client = FakeReviewClient(error=AssertionError("provider must not be called"))
        with tempfile.TemporaryDirectory() as raw_tmp:
            report = review_selective_quality(
                academic_report=_academic_candidates(1),
                content_quality_report={},
                structured_exam=exam,
                fragments_data=fragments,
                report_json=Path(raw_tmp) / "shadow.json",
                provider=SimpleNamespace(name="test", api_key="key", default_model="model"),
                model="model",
                shadow_only=True,
                client=client,
            )

        self.assertEqual("shadow_only", report["status"])
        self.assertEqual(0, client.calls)
        self.assertEqual(0, report["remote_model_calls_this_run"])
        self.assertEqual([], report["warnings"])

    def test_missing_or_invalid_model_decision_becomes_warning(self) -> None:
        from app.capabilities.selective_review import review_selective_quality

        exam, fragments = _exam_and_fragments(2)
        client = FakeReviewClient(
            {
                "decisions": [
                    {"candidate_id": "expr_1", "decision": "invalid", "confidence": 1},
                    {"candidate_id": "invented", "decision": "repair", "confidence": 1},
                ]
            }
        )
        with tempfile.TemporaryDirectory() as raw_tmp:
            report = review_selective_quality(
                academic_report=_academic_candidates(2),
                content_quality_report={},
                structured_exam=exam,
                fragments_data=fragments,
                report_json=Path(raw_tmp) / "review.json",
                provider=SimpleNamespace(name="test", api_key="key", default_model="model"),
                model="model",
                client=client,
            )

        self.assertEqual(2, len(report["decisions"]))
        self.assertEqual({"warn"}, {item["decision"] for item in report["decisions"]})
        self.assertEqual(2, len(report["warnings"]))

    def test_repair_without_atomic_defect_evidence_becomes_protocol_warning(self) -> None:
        from app.capabilities.selective_review import _normalized_decisions

        decisions = _normalized_decisions(
            {
                "decisions": [
                    {
                        "candidate_id": "expr_1",
                        "decision": "repair",
                        "confidence": 0.99,
                        "reason": "需要修改",
                        "suggested_fix": "改正答案",
                    }
                ]
            },
            {"expr_1"},
        )

        decision = decisions[0]
        self.assertEqual("warn", decision["decision"])
        self.assertEqual("reviewer_repair_missing_atomic_defect_evidence", decision["reason"])
        self.assertEqual([], decision["defects"])

    def test_repair_with_atomic_defect_evidence_is_preserved(self) -> None:
        from app.capabilities.selective_review import _normalized_decisions

        decisions = _normalized_decisions(
            {
                "decisions": [
                    {
                        "candidate_id": "c1",
                        "decision": "repair",
                        "confidence": 0.9,
                        "reason": "数值错误",
                        "suggested_fix": "修正数值",
                        "defects": [
                            {
                                "answer_unit_number": "2.3",
                                "defect_kind": "incorrect",
                                "requirement_quote": "计算组成",
                                "current_answer_quote": "A为50%",
                                "evidence_quote": "A应为36.5%",
                                "missing_output_quote": "",
                            }
                        ],
                    }
                ]
            },
            {"c1"},
        )

        self.assertEqual("repair", decisions[0]["decision"])
        self.assertEqual("2.3", decisions[0]["defects"][0]["answer_unit_number"])

    def test_unverified_evidence_quote_does_not_discard_grounded_answer_defect(self) -> None:
        from app.capabilities.selective_review import _normalized_decisions

        decisions = _normalized_decisions(
            {
                "decisions": [
                    {
                        "candidate_id": "c1",
                        "decision": "repair",
                        "confidence": 0.95,
                        "reason": "把嵌套相分数误当成整个组织组成物分数。",
                        "suggested_fix": "按同一组织基底重新计算。",
                        "defects": [
                            {
                                "answer_unit_number": "2.3",
                                "defect_kind": "incorrect",
                                "requirement_quote": "计算室温组织组成",
                                "current_answer_quote": "珠光体质量分数为4.1%",
                                "evidence_quote": "所有前驱体全部转变为珠光体（模型轻微改写）",
                            }
                        ],
                    }
                ]
            },
            {"c1"},
            {
                "c1": {
                    "required_units": {"2.3": "计算其室温组织组成和组成质量比"},
                    "answer_corpus": "室温组织组成：珠光体质量分数为4.1%。",
                    "evidence_corpus": "当温度降至727℃时，所有奥氏体都发生共析转变而成为珠光体。",
                }
            },
        )

        decision = decisions[0]
        self.assertEqual("repair", decision["decision"])
        self.assertEqual("计算其室温组织组成和组成质量比", decision["defects"][0]["requirement_quote"])
        self.assertFalse(decision["defects"][0]["evidence_quote_verified"])
        self.assertEqual("", decision["defects"][0]["evidence_quote"])

    def test_missing_defect_must_be_in_scope_and_absent_from_answer(self) -> None:
        from app.capabilities.selective_review import _normalized_decisions

        raw = {
            "decisions": [
                {
                    "candidate_id": "c1",
                    "decision": "repair",
                    "confidence": 0.9,
                    "reason": "缺少相组成",
                    "suggested_fix": "补充相组成",
                    "defects": [
                        {
                            "answer_unit_number": "1.3",
                            "defect_kind": "missing",
                            "requirement_quote": "计算相组成和组织组成",
                            "current_answer_quote": "",
                            "evidence_quote": "",
                            "missing_output_quote": "相组成",
                        }
                    ],
                }
            ]
        }
        context = {
            "c1": {
                "required_units": {"1.3": "计算相组成和组织组成"},
                "answer_corpus": "相组成：α73.3%，β26.7%；组织组成：初生α66.7%",
                "evidence_corpus": "",
            }
        }

        decision = _normalized_decisions(raw, {"c1"}, context)[0]

        self.assertEqual("warn", decision["decision"])
        self.assertEqual("reviewer_repair_missing_atomic_defect_evidence", decision["reason"])

    def test_repair_decision_that_admits_answer_is_correct_becomes_pass(self) -> None:
        from app.capabilities.selective_review import _normalized_decisions

        decisions = _normalized_decisions(
            {
                "decisions": [
                    {
                        "candidate_id": "c1",
                        "decision": "repair",
                        "confidence": 0.9,
                        "reason": "The final numbers are correct and the answer is actually correct.",
                        "suggested_fix": "Restate the same values.",
                        "defects": [
                            {
                                "answer_unit_number": "1.3",
                                "defect_kind": "incorrect",
                                "requirement_quote": "计算组成",
                                "current_answer_quote": "A为73.3%",
                            }
                        ],
                    }
                ]
            },
            {"c1"},
        )

        self.assertEqual("pass", decisions[0]["decision"])
        self.assertEqual([], decisions[0]["defects"])
        self.assertEqual("", decisions[0]["suggested_fix"])

    def test_numeric_composition_repair_without_evidence_or_machine_ledger_is_rejected(self) -> None:
        from app.capabilities.selective_review import _normalized_decisions

        decisions = _normalized_decisions(
            {
                "decisions": [
                    {
                        "candidate_id": "c1",
                        "decision": "repair",
                        "confidence": 0.95,
                        "reason": "组成计算错误，应增加从父项析出的新组分。",
                        "suggested_fix": "改为 A 60%、B 40%",
                        "defects": [
                            {
                                "answer_unit_number": "1.3",
                                "defect_kind": "incorrect",
                                "requirement_quote": "计算组成",
                                "current_answer_quote": "A 50%、B 50%",
                            }
                        ],
                    }
                ]
            },
            {"c1"},
            {
                "c1": {
                    "required_units": {"1.3": "计算组成"},
                    "answer_corpus": "A 50%、B 50%",
                    "evidence_corpus": "",
                    "has_calculation_contract": True,
                }
            },
        )

        self.assertEqual("pass", decisions[0]["decision"])
        self.assertEqual([], decisions[0]["defects"])
        self.assertTrue(decisions[0]["reviewer_repair_rejected"])
        self.assertIn("numeric_patch_missing", decisions[0]["reason"])
        self.assertEqual({}, decisions[0]["proposed_calculation_contract"])

    def test_conservative_numeric_composition_patch_retains_repair_authority(self) -> None:
        from app.capabilities.selective_review import _normalized_decisions

        decisions = _normalized_decisions(
            {
                "decisions": [
                    {
                        "candidate_id": "c1",
                        "decision": "repair",
                        "confidence": 0.95,
                        "reason": "新组分 X 应从父项 A0 析出，并从剩余 A 中扣除。",
                        "suggested_fix": "按守恒账本修正组成。",
                        "defects": [
                            {
                                "answer_unit_number": "1.3",
                                "defect_kind": "incorrect",
                                "requirement_quote": "计算组成",
                                "current_answer_quote": "A 66.7%、E 33.3%",
                            }
                        ],
                        "proposed_calculation_contract": {
                            "result_quantities": [
                                {"quantity_id": "a", "name": "A", "value": 0.578, "basis": "whole"},
                                {"quantity_id": "x", "name": "X", "value": 0.089, "basis": "whole"},
                                {"quantity_id": "e", "name": "E", "value": 0.333, "basis": "whole"},
                            ],
                            "intermediate_quantities": [
                                {"quantity_id": "a0", "value": 0.667, "basis": "whole"}
                            ],
                            "partitions": [
                                {"component_quantity_ids": ["a", "x", "e"], "expected_total": 1}
                            ],
                            "transitions": [
                                {
                                    "transition_id": "t1",
                                    "parent_quantity_id": "a0",
                                    "product_quantity_ids": ["a", "x"],
                                    "derived_quantity_id": "x",
                                    "local_fraction": 0.1333,
                                }
                            ],
                            "derivations": [
                                {
                                    "quantity_id": "a",
                                    "expression": "0.667-0.089",
                                    "source_quotes": ["parent 0.667 and derived 0.089"],
                                },
                                {
                                    "quantity_id": "x",
                                    "expression": "0.667*0.1333",
                                    "source_quotes": ["parent 0.667 and local fraction 0.1333"],
                                },
                            ],
                        },
                    }
                ]
            },
            {"c1"},
            {
                "c1": {
                    "required_units": {"1.3": "计算组成"},
                    "answer_corpus": "A 66.7%、E 33.3%",
                    "evidence_corpus": "",
                    "has_calculation_contract": True,
                    "calculation_contract": {
                        "result_quantities": [
                            {"name": "A", "value": 0.667},
                            {"name": "E", "value": 0.333},
                        ]
                    },
                    "source_corpus": "parent 0.667 and derived 0.089; parent 0.667 and local fraction 0.1333",
                }
            },
        )

        self.assertEqual("repair", decisions[0]["decision"])
        self.assertEqual([], decisions[0]["numeric_patch_validation_issues"])
        self.assertEqual(0.089, decisions[0]["proposed_calculation_contract"]["result_quantities"][1]["value"])

    def test_numeric_change_with_verified_evidence_keeps_defect_but_rejects_invalid_patch(self) -> None:
        from app.capabilities.selective_review import _normalized_decisions

        raw = {
            "decisions": [
                {
                    "candidate_id": "c1",
                    "decision": "repair",
                    "confidence": 0.9,
                    "reason": "组成计算错误。",
                    "suggested_fix": "A改为73.3%",
                    "defects": [{
                        "answer_unit_number": "1",
                        "defect_kind": "incorrect",
                        "requirement_quote": "计算组成",
                        "current_answer_quote": "A为66.7%",
                        "evidence_quote": "A应为73.3%",
                    }],
                    "proposed_calculation_contract": {
                        "result_quantities": [
                            {"quantity_id": "a", "name": "A", "value": 0.733, "basis": "whole"},
                            {"quantity_id": "b", "name": "B", "value": 0.267, "basis": "whole"},
                        ],
                        "partitions": [{"component_quantity_ids": ["a", "b"], "expected_total": 1}],
                    },
                }
            ]
        }
        context = {"c1": {
            "required_units": {"1": "计算组成"},
            "answer_corpus": "A为66.7%",
            "evidence_corpus": "依据教材相图杠杆定律，A应为73.3%。",
            "has_calculation_contract": True,
            "calculation_contract": {"result_quantities": [{"name": "A", "value": 0.667}]},
            "source_corpus": "合金成分40，端点20和95。",
        }}

        decision = _normalized_decisions(raw, {"c1"}, context)[0]

        self.assertEqual("repair", decision["decision"])
        self.assertTrue(decision["defects"])
        self.assertTrue(decision["defects"][0]["evidence_quote_verified"])
        self.assertEqual({}, decision["proposed_calculation_contract"])
        self.assertIn("numeric_patch_missing_derivation:a", decision["reason"])

    def test_numbered_subquestion_categorical_repair_is_not_misclassified_as_numeric(self) -> None:
        from app.capabilities.selective_review import _normalized_decisions

        decisions = _normalized_decisions(
            {
                "decisions": [
                    {
                        "candidate_id": "c1",
                        "decision": "repair",
                        "confidence": 0.95,
                        "reason": "第(3)题漏列一种符合条件的晶体结构。",
                        "suggested_fix": "在第(3)题答案中补充CsCl，并说明其属于简单立方点阵。",
                        "defects": [
                            {
                                "answer_unit_number": "3",
                                "defect_kind": "incorrect",
                                "requirement_quote": "哪些具有上面涉及到的晶体结构",
                                "current_answer_quote": "α-Fe、CuZn",
                                "evidence_quote": "CsCl属于简单立方点阵",
                            }
                        ],
                    }
                ]
            },
            {"c1"},
            {
                "c1": {
                    "required_units": {"3": "哪些具有上面涉及到的晶体结构"},
                    "answer_corpus": "α-Fe、CuZn",
                    "evidence_corpus": "CsCl属于简单立方点阵",
                    "has_calculation_contract": True,
                    "calculation_contract": {"result_quantities": [{"name": "d", "value": 1.0}]},
                }
            },
        )

        assert decisions[0]["decision"] == "repair"
        assert decisions[0]["numeric_patch_validation_issues"] == []
        assert decisions[0]["defects"][0]["evidence_quote_verified"] is True

    def test_zero_bucket_and_same_positive_results_have_no_repair_authority(self) -> None:
        from app.capabilities.selective_review import _normalized_decisions

        decisions = _normalized_decisions(
            {
                "decisions": [
                    {
                        "candidate_id": "c1",
                        "decision": "repair",
                        "confidence": 0.95,
                        "reason": "组成计算错误，需补充析出组分。",
                        "suggested_fix": "A 36.5%、E 63.5%、X 0%",
                        "defects": [
                            {
                                "answer_unit_number": "1.3",
                                "defect_kind": "incorrect",
                                "requirement_quote": "计算组成",
                                "current_answer_quote": "A 36.5%、E 63.5%",
                            }
                        ],
                        "proposed_calculation_contract": {
                            "result_quantities": [
                                {"quantity_id": "a", "name": "A", "value": 0.365, "basis": "whole"},
                                {"quantity_id": "e", "name": "E", "value": 0.635, "basis": "whole"},
                                {"quantity_id": "x", "name": "X", "value": 0.0, "basis": "whole"},
                            ],
                            "intermediate_quantities": [
                                {"quantity_id": "a0", "name": "A0", "value": 0.365, "basis": "whole"}
                            ],
                            "partitions": [
                                {"component_quantity_ids": ["a", "e", "x"], "expected_total": 1}
                            ],
                            "transitions": [
                                {
                                    "transition_id": "t1",
                                    "parent_quantity_id": "a0",
                                    "product_quantity_ids": ["a", "x"],
                                    "derived_quantity_id": "a",
                                    "local_fraction": 1,
                                }
                            ],
                        },
                    }
                ]
            },
            {"c1"},
            {
                "c1": {
                    "required_units": {"1.3": "计算组成"},
                    "answer_corpus": "A 36.5%、E 63.5%",
                    "evidence_corpus": "",
                    "has_calculation_contract": True,
                    "calculation_contract": {
                        "result_quantities": [
                            {"name": "A", "value": 0.365},
                            {"name": "E", "value": 0.635},
                            {"name": "A:E", "value": 0.575},
                        ]
                    },
                }
            },
        )

        self.assertEqual("pass", decisions[0]["decision"])
        self.assertEqual({}, decisions[0]["proposed_calculation_contract"])
        self.assertIn("numeric_patch_no_effective_result_change", decisions[0]["reason"])
        self.assertTrue(decisions[0]["reviewer_repair_rejected"])

    def test_nullable_external_json_degrades_to_warning_instead_of_crashing(self) -> None:
        from app.capabilities.selective_review import _normalized_decisions, _question_context

        context = _question_context(
            {
                "items": [
                    {
                        "question_id": "q1",
                        "number": "1",
                        "stem": "计算组成。",
                        "question_understanding": {"images": None},
                    }
                ]
            },
            {"q1"},
        )
        decisions = _normalized_decisions(
            {
                "decisions": [
                    {
                        "candidate_id": "c1",
                        "decision": "repair",
                        "confidence": 0.9,
                        "reason": "计算结果可能错误。",
                        "defects": [
                            {
                                "answer_unit_number": "1",
                                "defect_kind": "incorrect",
                                "requirement_quote": "计算组成",
                                "current_answer_quote": "A 为 50%",
                            }
                        ],
                        "proposed_calculation_contract": {
                            "result_quantities": None,
                            "intermediate_quantities": None,
                            "partitions": None,
                            "transitions": None,
                            "derivations": None,
                        },
                    }
                ]
            },
            {"c1"},
            {
                "c1": {
                    "required_units": {"1": "计算组成"},
                    "answer_corpus": "A 为 50%",
                    "evidence_corpus": "",
                    "has_calculation_contract": True,
                    "calculation_contract": {"result_quantities": None},
                }
            },
        )

        self.assertEqual([], context[0]["confirmed_visual_facts"])
        self.assertEqual("pass", decisions[0]["decision"])
        self.assertEqual([], decisions[0]["defects"])
        self.assertTrue(decisions[0]["reviewer_repair_rejected"])
        self.assertEqual({}, decisions[0]["proposed_calculation_contract"])
        self.assertIn("numeric_patch_missing_quantities", decisions[0]["reason"])

    def test_shadow_governance_caps_reviewer_opinion_at_warning(self) -> None:
        from app.capabilities.shadow_quality import build_shadow_quality_report

        with tempfile.TemporaryDirectory() as raw_tmp:
            stage = Path(raw_tmp)
            (stage / "selective_quality_review.json").write_text(
                json.dumps(
                    {
                        "ok": True,
                        "issues": [],
                        "warnings": [
                            {
                                "question_id": "q1",
                                "code": "repair_suggested",
                                "message": "反应式配平可能有误",
                            }
                        ],
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            shadow = build_shadow_quality_report(stage)

        finding = shadow["findings"][0]
        self.assertEqual("selective_quality.repair_suggested", finding["code"])
        self.assertEqual("warn", finding["action"])
        self.assertEqual("warn_only", finding["governance"]["action_ceiling"])


if __name__ == "__main__":
    unittest.main()
