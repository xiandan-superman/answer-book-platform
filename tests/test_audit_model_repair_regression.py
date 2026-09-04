from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


class AuditModelRepairRegressionTests(unittest.TestCase):
    def test_image_route_repair_never_silently_calls_plain_text_model(self) -> None:
        from app.audit_model_repair import repair_fragments_with_model_for_audit
        from app.llm_client import OpenAICompatibleClient
        from app.settings import ProviderConfig

        provider = ProviderConfig(
            name="custom-repair",
            type="openai_compatible",
            base_url="https://example.invalid",
            api_key="key",
            default_model="text-model",
            model_options=("text-model",),
            allow_custom_model=True,
            model_hint="",
            temperature=0.1,
            max_tokens=1000,
            supports_vision=False,
            model_capabilities={"text-model": ("text",)},
            model_profiles={"text-model": {"supports_tool_calls": False}},
        )
        client = OpenAICompatibleClient(provider)
        client.chat_json_object = lambda *args, **kwargs: self.fail(
            "plain-text repair must not run when the image tool route is required"
        )

        with tempfile.TemporaryDirectory() as tmp:
            fragments_json = Path(tmp) / "answer_fragments.json"
            original = {
                "fragments": [
                    {
                        "question_id": "q1",
                        "answer": "保留原答案",
                        "blocks": [],
                        "formulas": [],
                    }
                ]
            }
            fragments_json.write_text(json.dumps(original, ensure_ascii=False), encoding="utf-8")
            report = repair_fragments_with_model_for_audit(
                fragments_json,
                {"items": [{"question_id": "q1", "question_type": "作图题", "stem": "作图"}]},
                [],
                selection_data=None,
                provider=provider,
                model="text-model",
                audit_stage="answer_generation",
                audit_report={
                    "issues": [
                        {
                            "question_id": "q1",
                            "code": "missing_required_figure",
                            "message": "缺少必要图示",
                        }
                    ],
                    "warnings": [],
                },
                client=client,
                image_provider=provider,
                image_model="image-model",
            )

            self.assertFalse(report["changed"])
            self.assertIn("未登记等价的原生图片工具回路", report["issues"][0]["issues"][0])
            self.assertEqual(original, json.loads(fragments_json.read_text(encoding="utf-8")))

    def test_partial_multipart_repair_is_rejected(self) -> None:
        from app.audit_model_repair import _repair_regressions

        question = {
            "question_id": "q1",
            "question_type": "计算题",
            "subquestions": [
                {"number": "1", "question_type": "简答题"},
                {"number": "2", "question_type": "计算题"},
            ],
        }
        original = {
            "answer": "两问均已作答",
            "answer_units": [
                {"number": "1", "answer": "结论一"},
                {"number": "2", "answer": "结论二", "steps": [{"text": "计算"}]},
            ],
            "blocks": [
                {"label": "解析", "segments": [{"type": "text", "text": "完整解析"}]},
                {"label": "解题步骤", "segments": [{"type": "text", "text": "完整步骤"}]},
            ],
            "formulas": [{"formula_id": "f1", "latex": "x=1"}],
        }
        partial = {
            "answer": "只修复第二问",
            "answer_units": [
                {"number": "1", "answer": "", "analysis_segments": [], "steps": []},
                {"number": "2", "answer": "结论二", "steps": [{"text": "计算"}]},
            ],
            "blocks": [{"label": "解题步骤", "segments": [{"type": "text", "text": "新步骤"}]}],
            "formulas": [{"formula_id": "f1", "latex": "x=1"}],
        }

        regressions = _repair_regressions(
            original,
            partial,
            question,
            [{"code": "calculation_missing_substitution"}],
        )

        self.assertTrue(any(item.startswith("missing_answer_units:") for item in regressions))
        self.assertIn("repair_removed_existing_block:解析", regressions)

    def test_warning_repair_cannot_delete_existing_analysis(self) -> None:
        from app.audit_model_repair import _repair_regressions

        question = {"question_id": "q2", "question_type": "计算题", "subquestions": []}
        original = {
            "answer": "A更强",
            "blocks": [{"label": "解析", "segments": [{"type": "text", "text": "冷却更快，晶粒更细。"}]}],
            "formulas": [{"formula_id": "f1", "latex": "x=1"}],
        }
        repaired = {
            "answer": "A更强",
            "blocks": [{"label": "解题步骤", "segments": [{"type": "text", "text": "代入比较。"}]}],
            "formulas": [{"formula_id": "f1", "latex": "x=1"}],
        }

        self.assertIn(
            "repair_removed_existing_block:解析",
            _repair_regressions(
                original,
                repaired,
                question,
                [{"code": "calculation_missing_substitution"}],
            ),
        )

    def test_safe_mistake_note_is_preserved_during_scoped_repair(self) -> None:
        from app.audit_model_repair import _merge_safe_preserved_blocks

        original = {
            "blocks": [
                {"label": "易错点及注意事项", "segments": [{"type": "text", "text": "注意计算基准。"}]}
            ],
            "formulas": [],
        }
        repaired = {"blocks": [], "formulas": []}

        _merge_safe_preserved_blocks(original, repaired)

        self.assertEqual("易错点及注意事项", repaired["blocks"][0]["label"])

    def test_text_repair_preserves_only_proven_main_model_image_binding(self) -> None:
        from app.audit_model_repair import _preserve_accepted_generated_images

        image = {"asset_id": "img_sha256_valid", "caption": "相图", "placement": "analysis"}
        artifact = {"asset_id": "img_sha256_valid", "path": "/tmp/valid.png"}
        original = {
            "generated_images": [image],
            "_meta": {
                "image_tool_loop": {
                    "steps": 2,
                    "tool_calls": 1,
                    "generated_artifacts": [artifact],
                }
            },
        }
        repaired = {"generated_images": [], "_draft": {"generated_images": []}}

        _preserve_accepted_generated_images(original, repaired)

        self.assertEqual([image], repaired["generated_images"])
        self.assertEqual([image], repaired["_draft"]["generated_images"])
        self.assertEqual([artifact], repaired["_meta"]["image_tool_loop"]["generated_artifacts"])

    def test_unproven_image_reference_is_not_preserved(self) -> None:
        from app.audit_model_repair import _preserve_accepted_generated_images

        original = {
            "generated_images": [{"asset_id": "img_sha256_unseen"}],
            "_meta": {"review_candidate_issues": ["main model referenced image assets it had not inspected"]},
        }
        repaired = {"generated_images": [], "_draft": {"generated_images": []}}

        _preserve_accepted_generated_images(original, repaired)

        self.assertEqual([], repaired["generated_images"])

    def test_generated_image_satisfies_missing_figure_repair_postcondition(self) -> None:
        from app.audit_model_repair import _repair_regressions

        regressions = _repair_regressions(
            {"answer": "见图", "blocks": []},
            {
                "answer": "见图",
                "blocks": [],
                "generated_images": [{"asset_id": "img_sha256_valid"}],
            },
            {"question_id": "q1", "question_type": "作图题"},
            [{"code": "missing_required_figure"}],
        )

        self.assertNotIn("repair_did_not_add_required_figure_spec", regressions)

    def test_mistake_note_keeps_prose_and_drops_stale_formula_reference(self) -> None:
        from app.audit_model_repair import _merge_safe_preserved_blocks

        original = {
            "blocks": [
                {
                    "label": "易错点及注意事项",
                    "segments": [
                        {"type": "text", "text": "注意计算基准。"},
                        {"type": "formula_ref", "formula_id": "old_formula"},
                    ],
                }
            ],
            "formulas": [{"formula_id": "old_formula"}],
        }
        repaired = {
            "blocks": [],
            "formulas": [{"formula_id": "new_formula", "latex": "x=2"}],
        }

        _merge_safe_preserved_blocks(original, repaired)

        self.assertEqual(
            [{"type": "text", "text": "注意计算基准。"}],
            repaired["blocks"][0]["segments"],
        )

    def test_formula_like_optional_repair_note_cannot_reject_core_numeric_fix(self) -> None:
        from app.audit_model_repair import (
            _drop_formula_like_repair_advisories,
            _merge_safe_preserved_blocks,
        )

        original = {
            "blocks": [
                {"label": "易错点及注意事项", "segments": [{"type": "text", "text": "统一计算基准。"}]}
            ],
            "formulas": [],
        }
        repaired = {
            "blocks": [
                {
                    "label": "解析",
                    "segments": [{"type": "text", "text": "数值账本已经修正。"}],
                },
                {
                    "label": "易错点及注意事项",
                    "segments": [
                        {
                            "type": "text",
                            "text": "该比例等于父项质量分数乘以局部分数。",
                        }
                    ],
                },
            ],
            "formulas": [],
        }

        self.assertEqual(1, _drop_formula_like_repair_advisories(repaired))
        _merge_safe_preserved_blocks(original, repaired)

        note = next(block for block in repaired["blocks"] if block["label"] == "易错点及注意事项")
        self.assertEqual("统一计算基准。", note["segments"][0]["text"])

    def test_readable_chinese_formula_paraphrase_is_deferred_but_symbol_leak_is_hard(self) -> None:
        from app.audit_model_repair import _repair_formula_leaks

        hard, deferred = _repair_formula_leaks(
            {
                "blocks": [
                    {
                        "label": "解析",
                        "segments": [
                            {"type": "text", "text": "该比例等于父项质量分数乘以局部分数。"},
                            {"type": "text", "text": r"错误残留为 \\frac{a}{b}。"},
                        ],
                    }
                ]
            }
        )

        self.assertEqual(1, len(hard))
        self.assertEqual(1, len(deferred))

    def test_repair_context_exposes_authoritative_arithmetic_diagnostic(self) -> None:
        from app.audit_model_repair import _repair_context

        context = _repair_context(
            {
                "question_id": "q1",
                "_draft": {
                    "formulas": [
                        {"latex": r"w=\frac{4.3-3.5}{4.3-2.11}", "role": "substitution"}
                    ]
                },
            },
            [
                {
                    "code": "answer_generation_validation",
                    "message": "formula_substitution_result_mismatch:1:0.3652968!=[50.0]",
                }
            ],
        )

        diagnostic = context["deterministic_numeric_diagnostics"][0]
        self.assertAlmostEqual(0.3652968, diagnostic["computed_decimal"])
        self.assertAlmostEqual(36.52968, diagnostic["computed_percentage"])
        self.assertEqual("50.0", diagnostic["rejected_declared_values"])

    def test_repair_context_computes_detailed_diagnostics_from_generic_audit_issue(self) -> None:
        from app.audit_model_repair import _repair_context

        context = _repair_context(
            {
                "question_id": "q1",
                "_draft": {
                    "formulas": [
                        {"latex": r"r=1.264/0.63212", "role": "substitution"},
                        {"latex": r"r=3", "role": "result"},
                    ]
                },
            },
            [
                {
                    "code": "calculation_internal_inconsistency",
                    "message": "计算题的公式等式、代入结果或步骤结论存在数值矛盾。",
                }
            ],
        )

        self.assertTrue(context["deterministic_validation_issues"])
        self.assertTrue(context["deterministic_numeric_diagnostics"])
        self.assertAlmostEqual(1.264 / 0.63212, context["deterministic_numeric_diagnostics"][0]["computed_decimal"], places=6)

    def test_retry_prompt_keeps_candidate_and_deterministic_failures(self) -> None:
        import json

        from app.audit_model_repair import _repair_retry_prompt

        messages = _repair_retry_prompt(
            [{"role": "system", "content": "system"}, {"role": "user", "content": "initial"}],
            {"answer": "已修正为36.5%", "calculation_contract": {"partitions": []}},
            ["calculation_contract_mixed_partition_basis:1"],
        )

        self.assertEqual("assistant", messages[-2]["role"])
        self.assertIn("已修正为36.5%", messages[-2]["content"])
        retry = json.loads(messages[-1]["content"])
        self.assertEqual(["calculation_contract_mixed_partition_basis:1"], retry["deterministic_validation_issues"])
        self.assertIn("previous_candidate", retry)
        self.assertEqual("answer_book.repair_validation_result.v1", retry["validation_tool_result"]["schema_version"])
        self.assertEqual("model_output", retry["validation_tool_result"]["error"]["responsibility"])
        self.assertTrue(retry["validation_tool_result"]["error"]["suggestion"])
        self.assertEqual(64, len(retry["validation_tool_result"]["meta"]["candidate_sha256"]))

    def test_retry_prompt_exposes_parsed_authoritative_arithmetic(self) -> None:
        import json

        from app.audit_model_repair import _repair_retry_prompt

        messages = _repair_retry_prompt(
            [{"role": "system", "content": "system"}, {"role": "user", "content": "large initial prompt"}],
            {"answer": "候选答案"},
            ["formula_substitution_result_mismatch:8:0.082618243!=[2.6]"],
        )

        retry = json.loads(messages[-1]["content"])
        diagnostic = retry["authoritative_arithmetic_diagnostics"][0]
        self.assertEqual(8, diagnostic["formula_index"])
        self.assertAlmostEqual(8.2618243, diagnostic["authoritative_percentage"])
        self.assertEqual(3, len(messages))

    def test_contract_diagnostics_expose_partition_and_transition_deficits(self) -> None:
        from app.audit_model_repair import _deterministic_contract_diagnostics

        draft = {
            "calculation_contract": {
                "result_quantities": [
                    {"quantity_id": "a", "name": "A", "value": 0.234, "basis": "总体"},
                    {"quantity_id": "b", "name": "B", "value": 0.041, "basis": "总体"},
                    {"quantity_id": "c", "name": "C", "value": 0.407, "basis": "总体"},
                ],
                "intermediate_quantities": [
                    {"quantity_id": "parent", "name": "父项", "value": 0.365, "basis": "总体"}
                ],
                "partitions": [
                    {"basis": "总体", "component_quantity_ids": ["a", "b", "c"], "expected_total": 1.0}
                ],
                "transitions": [
                    {"basis": "总体", "parent_quantity_id": "parent", "product_quantity_ids": ["a", "b"]}
                ],
            }
        }
        diagnostics = _deterministic_contract_diagnostics(
            draft,
            [
                "calculation_contract_partition_sum_mismatch:1:0.682!=1",
                "calculation_contract_transition_conservation_mismatch:1:products=0.275:parent=0.365",
            ],
        )

        self.assertAlmostEqual(0.318, diagnostics[0]["required_delta"])
        self.assertEqual(["a", "b", "c"], [row["quantity_id"] for row in diagnostics[0]["components"]])
        self.assertAlmostEqual(0.09, diagnostics[1]["unaccounted_amount"])
        self.assertEqual("parent", diagnostics[1]["parent"]["quantity_id"])

    def test_retry_prompt_includes_contract_diagnostics_from_candidate(self) -> None:
        import json

        from app.audit_model_repair import _repair_retry_prompt

        candidate = {
            "calculation_contract": {
                "result_quantities": [
                    {"quantity_id": "a", "value": 0.4, "basis": "whole"},
                    {"quantity_id": "b", "value": 0.4, "basis": "whole"},
                ],
                "partitions": [
                    {"basis": "whole", "component_quantity_ids": ["a", "b"], "expected_total": 1.0}
                ],
            }
        }
        messages = _repair_retry_prompt(
            [{"role": "system", "content": "system"}, {"role": "user", "content": "initial"}],
            candidate,
            ["calculation_contract_partition_sum_mismatch:1:0.8!=1"],
        )

        retry = json.loads(messages[-1]["content"])
        self.assertAlmostEqual(0.2, retry["authoritative_contract_diagnostics"][0]["required_delta"])


if __name__ == "__main__":
    unittest.main()
