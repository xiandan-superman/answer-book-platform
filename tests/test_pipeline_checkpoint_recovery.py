from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


class PipelineCheckpointRecoveryTests(unittest.TestCase):
    def test_reconciliation_prefers_valid_fragments_over_stale_progress_counter(self) -> None:
        from app.pipeline_checkpoints import reconcile_answer_generation_checkpoint

        valid = {
            "schema_version": "answer_book.answer_fragment.v4",
            "section": "一、简答题",
            "number": "1",
            "answer": "有效答案",
            "evidence_ids": [],
            "blocks": [{"label": "解析", "segments": [{"type": "text", "text": "有效解析。"}]}],
            "formulas": [],
            "warnings": [],
        }
        with tempfile.TemporaryDirectory() as raw_tmp:
            stage = Path(raw_tmp)
            exam = {"items": [{"question_id": "q1"}, {"question_id": "q2"}]}
            (stage / "structured_exam.json").write_text(json.dumps(exam), encoding="utf-8")
            (stage / "answer_fragments.json").write_text(
                json.dumps({"fragments": [{**valid, "question_id": "q1"}, {**valid, "question_id": "q2", "number": "2"}]}),
                encoding="utf-8",
            )
            (stage / "answer_generation_progress.json").write_text(
                json.dumps({"status": "running", "total": 2, "completed": 1}), encoding="utf-8"
            )

            report = reconcile_answer_generation_checkpoint(
                stage, output_json=stage / "answer_checkpoint_reconciliation.json"
            )

            self.assertTrue(report["safe_to_resume"])
            self.assertEqual(["q1", "q2"], report["reusable_question_ids"])
            self.assertEqual([], report["redrive_question_ids"])
            self.assertTrue(any("progress completed 1" in issue for issue in report["inconsistencies"]))
            self.assertTrue((stage / "answer_checkpoint_reconciliation.json").exists())

    def test_reconciliation_redrives_duplicate_missing_and_invalid_fragments(self) -> None:
        from app.pipeline_checkpoints import reconcile_answer_generation_checkpoint

        valid = {
            "schema_version": "answer_book.answer_fragment.v4",
            "section": "一、简答题",
            "number": "1",
            "answer": "有效答案",
            "evidence_ids": [],
            "blocks": [{"label": "解析", "segments": [{"type": "text", "text": "有效解析。"}]}],
            "formulas": [],
            "warnings": [],
        }
        with tempfile.TemporaryDirectory() as raw_tmp:
            stage = Path(raw_tmp)
            exam = {"items": [{"question_id": "q1"}, {"question_id": "q2"}, {"question_id": "q3"}]}
            (stage / "structured_exam.json").write_text(json.dumps(exam), encoding="utf-8")
            (stage / "answer_fragments.json").write_text(
                json.dumps(
                    {
                        "fragments": [
                            {**valid, "question_id": "q1"},
                            {**valid, "question_id": "q1"},
                            {**valid, "question_id": "q2", "answer": "待复核"},
                            {**valid, "question_id": "foreign"},
                        ]
                    }
                ),
                encoding="utf-8",
            )

            report = reconcile_answer_generation_checkpoint(stage)

            self.assertTrue(report["safe_to_resume"])
            self.assertEqual(["q1", "q2", "q3"], report["redrive_question_ids"])
            self.assertEqual(["q1"], report["duplicate_question_ids"])
            self.assertEqual(["q3"], report["missing_question_ids"])
            self.assertEqual(["foreign"], report["foreign_question_ids"])

    def test_reconciliation_treats_missing_fragment_file_as_clean_redrive_plan(self) -> None:
        from app.pipeline_checkpoints import reconcile_answer_generation_checkpoint

        with tempfile.TemporaryDirectory() as raw_tmp:
            stage = Path(raw_tmp)
            exam = {"items": [{"question_id": "q1"}, {"question_id": "q2"}]}
            (stage / "structured_exam.json").write_text(json.dumps(exam), encoding="utf-8")

            report = reconcile_answer_generation_checkpoint(stage)

            self.assertTrue(report["safe_to_resume"])
            self.assertEqual(["q1", "q2"], report["redrive_question_ids"])
            self.assertFalse(any("answer_fragments unreadable" in issue for issue in report["inconsistencies"]))

    def test_reconciliation_discards_malformed_fragment_root_without_blocking_pipeline_resume(self) -> None:
        from app.pipeline_checkpoints import reconcile_answer_generation_checkpoint

        with tempfile.TemporaryDirectory() as raw_tmp:
            stage = Path(raw_tmp)
            exam = {"items": [{"question_id": "q1"}]}
            (stage / "structured_exam.json").write_text(json.dumps(exam), encoding="utf-8")
            (stage / "answer_fragments.json").write_text("[]", encoding="utf-8")

            report = reconcile_answer_generation_checkpoint(stage)

            self.assertTrue(report["safe_to_resume"])
            self.assertFalse(report["checkpoint_reuse_safe"])
            self.assertEqual("discard_malformed_answer_checkpoint_and_regenerate", report["resume_strategy"])
            self.assertEqual(["q1"], report["redrive_question_ids"])
            self.assertTrue(any("answer_fragments unreadable" in issue for issue in report["inconsistencies"]))

    def test_reconciliation_routes_missing_structured_exam_back_to_upstream(self) -> None:
        from app.pipeline_checkpoints import reconcile_answer_generation_checkpoint

        with tempfile.TemporaryDirectory() as raw_tmp:
            report = reconcile_answer_generation_checkpoint(Path(raw_tmp))

            self.assertFalse(report["safe_to_resume"])
            self.assertFalse(report["checkpoint_reuse_safe"])
            self.assertEqual("rerun_upstream_before_answer_generation", report["resume_strategy"])

    def test_partial_answer_checkpoint_reuses_only_individually_valid_fragments(self) -> None:
        from app.pipeline_checkpoints import reusable_answer_fragment_map

        valid = {
            "schema_version": "answer_book.answer_fragment.v4",
            "question_id": "q1",
            "section": "一、简答题",
            "number": "1",
            "answer": "有效答案",
            "evidence_ids": [],
            "blocks": [{"label": "解析", "segments": [{"type": "text", "text": "有效解析。"}]}],
            "formulas": [],
            "warnings": [],
        }
        invalid = {
            **valid,
            "question_id": "q2",
            "number": "2",
            "_meta": {"recovered_by": "review_candidate_preserved"},
            "_review_flags": [{"code": "answer_generation_review_candidate", "message": "内部矛盾"}],
        }
        with tempfile.TemporaryDirectory() as raw_tmp:
            stage = Path(raw_tmp)
            (stage / "answer_fragments.json").write_text(
                json.dumps({"fragments": [valid, invalid]}), encoding="utf-8"
            )
            exam = {"items": [{"question_id": "q1"}, {"question_id": "q2"}]}

            reusable = reusable_answer_fragment_map(stage, exam, requested=True)

            self.assertEqual(["q1"], list(reusable))
            self.assertEqual("q1", reusable["q1"]["question_id"])

    def test_unresolved_formula_reference_is_redriven_instead_of_reused_forever(self) -> None:
        from app.pipeline_checkpoints import answer_checkpoint_reusable, reconcile_answer_generation_checkpoint

        fragment = {
            "schema_version": "answer_book.answer_fragment.v4",
            "question_id": "q1",
            "section": "一、简答题",
            "number": "1",
            "answer": "答案主体仍可阅读。",
            "evidence_ids": [],
            "blocks": [{"label": "解析", "segments": [{"type": "text", "text": "公式编号已丢失。"}]}],
            "formulas": [],
            "warnings": ["模型返回了不存在的公式引用"],
            "_review_flags": [
                {"code": "unresolved_formula_reference_removed", "message": "需要重新生成完整公式"}
            ],
        }
        exam = {"items": [{"question_id": "q1"}]}
        with tempfile.TemporaryDirectory() as raw_tmp:
            stage = Path(raw_tmp)
            (stage / "structured_exam.json").write_text(json.dumps(exam), encoding="utf-8")
            (stage / "answer_fragments.json").write_text(
                json.dumps({"fragments": [fragment]}), encoding="utf-8"
            )

            self.assertFalse(answer_checkpoint_reusable(stage, exam, requested=True))
            report = reconcile_answer_generation_checkpoint(stage, exam)
            self.assertEqual([], report["reusable_question_ids"])
            self.assertEqual(["q1"], report["redrive_question_ids"])

    def test_preserved_candidate_with_failed_model_issues_is_redriven(self) -> None:
        from app.pipeline_checkpoints import reconcile_answer_generation_checkpoint

        fragment = {
            "schema_version": "answer_book.answer_fragment.v4",
            "question_id": "q1",
            "section": "一、简答题",
            "number": "1",
            "answer": "已保留的可读候选答案",
            "evidence_ids": [],
            "blocks": [{"label": "解析", "segments": [{"type": "text", "text": "候选解析。"}]}],
            "formulas": [],
            "warnings": ["候选答案仍有影响交付的未解决提示"],
            "_meta": {"review_candidate_issues": ["Provider request timed out"]},
        }
        exam = {"items": [{"question_id": "q1"}]}
        with tempfile.TemporaryDirectory() as raw_tmp:
            stage = Path(raw_tmp)
            (stage / "answer_fragments.json").write_text(
                json.dumps({"fragments": [fragment]}), encoding="utf-8"
            )

            report = reconcile_answer_generation_checkpoint(stage, exam)
            self.assertEqual([], report["reusable_question_ids"])
            self.assertEqual(["q1"], report["redrive_question_ids"])

    def test_duplicate_fragment_id_is_never_reused_as_complete_or_partial_checkpoint(self) -> None:
        from app.pipeline_checkpoints import answer_checkpoint_reusable, reusable_answer_fragment_map

        valid = {
            "schema_version": "answer_book.answer_fragment.v4",
            "section": "一、简答题",
            "answer": "有效答案",
            "evidence_ids": [],
            "blocks": [{"label": "解析", "segments": [{"type": "text", "text": "有效解析。"}]}],
            "formulas": [],
            "warnings": [],
        }
        exam = {"items": [{"question_id": "q1"}, {"question_id": "q2"}]}
        with tempfile.TemporaryDirectory() as raw_tmp:
            stage = Path(raw_tmp)
            (stage / "answer_fragments.json").write_text(
                json.dumps(
                    {
                        "fragments": [
                            {**valid, "question_id": "q1", "number": "1"},
                            {**valid, "question_id": "q1", "number": "1"},
                            {**valid, "question_id": "q2", "number": "2"},
                        ]
                    }
                ),
                encoding="utf-8",
            )

            self.assertFalse(answer_checkpoint_reusable(stage, exam, requested=True))
            reusable = reusable_answer_fragment_map(stage, exam, requested=True)

            self.assertEqual(["q2"], list(reusable))

    def test_source_fingerprint_redrives_only_questions_whose_contract_changed(self) -> None:
        from app.answer_generation import answer_source_contract
        from app.pipeline_checkpoints import (
            answer_checkpoint_reusable,
            reconcile_answer_generation_checkpoint,
            reusable_answer_fragment_map,
        )

        valid = {
            "schema_version": "answer_book.answer_fragment.v4",
            "section": "一、简答题",
            "answer": "有效答案",
            "evidence_ids": [],
            "blocks": [{"label": "解析", "segments": [{"type": "text", "text": "有效解析。"}]}],
            "formulas": [],
            "warnings": [],
        }
        original_exam = {
            "items": [
                {"question_id": "q1", "text": "原题一"},
                {"question_id": "q2", "text": "原题二"},
            ]
        }
        changed_exam = {
            "items": [
                {"question_id": "q1", "text": "已修改的题目一"},
                {"question_id": "q2", "text": "原题二"},
            ]
        }
        with tempfile.TemporaryDirectory() as raw_tmp:
            stage = Path(raw_tmp)
            (stage / "answer_fragments.json").write_text(
                json.dumps(
                    {
                        "source_contract": answer_source_contract(original_exam),
                        "fragments": [
                            {**valid, "question_id": "q1", "number": "1"},
                            {**valid, "question_id": "q2", "number": "2"},
                        ],
                    }
                ),
                encoding="utf-8",
            )

            self.assertFalse(answer_checkpoint_reusable(stage, changed_exam, requested=True))
            reusable = reusable_answer_fragment_map(stage, changed_exam, requested=True)
            report = reconcile_answer_generation_checkpoint(stage, changed_exam)

            self.assertEqual(["q2"], list(reusable))
            self.assertEqual(["q1"], report["redrive_question_ids"])
            self.assertEqual("mismatched", report["source_contract"]["status"])
            self.assertEqual(["q1"], report["source_contract"]["mismatched_question_ids"])

    def test_legacy_source_contract_migrates_only_after_full_validation(self) -> None:
        from app.pipeline_checkpoints import migrate_legacy_answer_source_contract

        valid = {
            "schema_version": "answer_book.answer_fragment.v4",
            "question_id": "q1",
            "section": "一、简答题",
            "number": "1",
            "answer": "有效答案",
            "evidence_ids": [],
            "blocks": [{"label": "解析", "segments": [{"type": "text", "text": "有效解析。"}]}],
            "formulas": [],
            "warnings": [],
        }
        exam = {"items": [{"question_id": "q1", "text": "题目"}]}
        with tempfile.TemporaryDirectory() as raw_tmp:
            stage = Path(raw_tmp)
            path = stage / "answer_fragments.json"
            path.write_text(json.dumps({"fragments": [valid]}), encoding="utf-8")

            self.assertTrue(migrate_legacy_answer_source_contract(stage, exam))
            migrated = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(
                "answer_book.answer_source_contract.v1",
                migrated["source_contract"]["version"],
            )
            self.assertFalse(migrate_legacy_answer_source_contract(stage, exam))

            path.write_text(
                json.dumps({"fragments": [{**valid, "answer": "待复核"}]}), encoding="utf-8"
            )
            self.assertFalse(migrate_legacy_answer_source_contract(stage, exam))
            self.assertNotIn("source_contract", json.loads(path.read_text(encoding="utf-8")))

    def test_upstream_checkpoint_requires_current_grouping_policy(self) -> None:
        from app.evidence_selection import SCHEMA_VERSION as EVIDENCE_SELECTION_SCHEMA_VERSION
        from app.exam_extract import EXAM_GROUPING_POLICY_VERSION
        from app.pipeline import _answer_checkpoint_reusable, _upstream_checkpoint_reusable
        from app.retrieval import RETRIEVAL_CONTEXT_POLICY_VERSION

        with tempfile.TemporaryDirectory() as raw_tmp:
            stage = Path(raw_tmp)
            for name in ("knowledge_plans.json", "evidence_selection.json", "answer_fragments.json"):
                (stage / name).write_text("{}", encoding="utf-8")
            (stage / "retrieval_candidates.summary.json").write_text(
                json.dumps({"retrieval_context_policy_version": RETRIEVAL_CONTEXT_POLICY_VERSION}),
                encoding="utf-8",
            )
            (stage / "structured_exam.json").write_text("{}", encoding="utf-8")
            self.assertFalse(_upstream_checkpoint_reusable(stage, requested=True))

            (stage / "structured_exam.json").write_text(
                json.dumps({"grouping_policy_version": EXAM_GROUPING_POLICY_VERSION, "items": [{"question_id": "q1"}]}),
                encoding="utf-8",
            )
            (stage / "evidence_selection.json").write_text(
                json.dumps({"schema_version": EVIDENCE_SELECTION_SCHEMA_VERSION}), encoding="utf-8"
            )
            (stage / "answer_fragments.json").write_text(
                json.dumps(
                    {
                        "fragments": [
                            {
                                "schema_version": "answer_book.answer_fragment.v4",
                                "question_id": "q1",
                                "section": "一、简答题",
                                "number": "1",
                                "answer": "有效答案",
                                "evidence_ids": [],
                                "blocks": [
                                    {"label": "解析", "segments": [{"type": "text", "text": "有效解析。"}]}
                                ],
                                "formulas": [],
                                "warnings": [],
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )
            self.assertTrue(_upstream_checkpoint_reusable(stage, requested=True))
            exam = {"items": [{"question_id": "q1"}]}
            self.assertTrue(_answer_checkpoint_reusable(stage, exam, requested=True))

            (stage / "answer_fragments.json").write_text(
                json.dumps({"fragments": [{"question_id": "legacy_q"}]}), encoding="utf-8"
            )
            self.assertTrue(_upstream_checkpoint_reusable(stage, requested=True))
            self.assertFalse(_answer_checkpoint_reusable(stage, exam, requested=True))

    def test_nullable_checkpoint_collections_are_rejected_without_crashing(self) -> None:
        from app.pipeline import _answer_checkpoint_reusable

        with tempfile.TemporaryDirectory() as raw_tmp:
            stage = Path(raw_tmp)
            (stage / "answer_fragments.json").write_text(
                json.dumps({"fragments": None}),
                encoding="utf-8",
            )

            self.assertFalse(
                _answer_checkpoint_reusable(
                    stage,
                    {"items": [{"question_id": "q1"}]},
                    requested=True,
                )
            )
            exam = {"items": [{"question_id": "q1"}]}

            (stage / "answer_fragments.json").write_text(
                json.dumps(
                    {
                        "fragments": [
                            {
                                "schema_version": "answer_book.answer_fragment.v4",
                                "question_id": "q1",
                                "section": "一、计算题",
                                "number": "1",
                                "answer": "见解析",
                                "evidence_ids": [],
                                "blocks": [
                                    {
                                        "label": "解析",
                                        "segments": [
                                            {"type": "text", "text": "直接写入 (95-40)/(95-20)=55/75。"}
                                        ],
                                    }
                                ],
                                "formulas": [],
                                "warnings": [],
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )
            self.assertFalse(_answer_checkpoint_reusable(stage, exam, requested=True))

            (stage / "answer_fragments.json").write_text(
                json.dumps(
                    {
                        "fragments": [
                            {
                                "question_id": "q1",
                                "answer": "待复核",
                                "_meta": {"recovered_by": "failure_placeholder"},
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )
            self.assertFalse(_answer_checkpoint_reusable(stage, exam, requested=True))

    def test_failed_content_repair_rolls_back_to_pre_model_snapshot(self) -> None:
        from app.pipeline import _restore_failed_content_repair_checkpoint

        with tempfile.TemporaryDirectory() as raw_tmp:
            stage = Path(raw_tmp)
            (stage / "pipeline_error.json").write_text(
                json.dumps({"error": "Content quality audit failed after bounded repairs"}),
                encoding="utf-8",
            )
            (stage / "answer_fragments.json").write_text('{"state":"broken"}', encoding="utf-8")
            (stage / "answer_fragments.before_content_quality_model_repair.json").write_text(
                '{"state":"complete"}',
                encoding="utf-8",
            )

            restored = _restore_failed_content_repair_checkpoint(stage)

            self.assertTrue(restored.endswith("before_content_quality_model_repair.json"))
            self.assertEqual(
                {"state": "complete"},
                json.loads((stage / "answer_fragments.json").read_text(encoding="utf-8")),
            )
            self.assertFalse((stage / "pipeline_error.json").exists())

    def test_old_content_repair_transaction_is_not_restored_by_new_run(self) -> None:
        from app.pipeline import _restore_failed_content_repair_checkpoint

        with tempfile.TemporaryDirectory() as raw_tmp:
            stage = Path(raw_tmp)
            (stage / "pipeline_error.json").write_text(
                json.dumps(
                    {
                        "error": "Content quality audit failed after bounded repairs",
                        "run_started_at": "2026-08-13T09:00:00+08:00",
                    }
                ),
                encoding="utf-8",
            )
            (stage / "answer_fragments.json").write_text('{"state":"new"}', encoding="utf-8")
            (stage / "answer_fragments.before_content_quality_model_repair.json").write_text(
                '{"state":"old"}', encoding="utf-8"
            )

            restored = _restore_failed_content_repair_checkpoint(
                stage, current_run_started_at="2026-08-13T10:00:00+08:00"
            )

            self.assertEqual("", restored)
            self.assertEqual(
                {"state": "new"},
                json.loads((stage / "answer_fragments.json").read_text(encoding="utf-8")),
            )

    def test_failed_correctness_repair_rolls_back_only_repaired_question(self) -> None:
        from app.pipeline import _restore_failed_content_repair_checkpoint

        with tempfile.TemporaryDirectory() as raw_tmp:
            stage = Path(raw_tmp)
            (stage / "pipeline_error.json").write_text(
                json.dumps({"error": "High-risk answer correctness audit failed before figure rendering"}),
                encoding="utf-8",
            )
            (stage / "answer_fragments.json").write_text(
                json.dumps(
                    {
                        "fragments": [
                            {"question_id": "q1", "answer": "new unrelated"},
                            {
                                "question_id": "q2",
                                "answer": "failed repair",
                                "_meta": {"recovered_by": "prefigure_correctness_model_repair"},
                            },
                        ]
                    }
                ),
                encoding="utf-8",
            )
            (stage / "answer_fragments.before_prefigure_correctness_repair.json").write_text(
                json.dumps(
                    {
                        "fragments": [
                            {"question_id": "q1", "answer": "old unrelated"},
                            {"question_id": "q2", "answer": "verified original"},
                        ]
                    }
                ),
                encoding="utf-8",
            )

            restored = _restore_failed_content_repair_checkpoint(stage)
            rows = json.loads((stage / "answer_fragments.json").read_text(encoding="utf-8"))["fragments"]

            self.assertEqual("prefigure_correctness:q2", restored)
            self.assertEqual("new unrelated", rows[0]["answer"])
            self.assertEqual("verified original", rows[1]["answer"])

    def test_old_figure_routing_checkpoint_is_invalidated(self) -> None:
        from app.pipeline import _figure_schema_checkpoint_reusable

        self.assertFalse(_figure_schema_checkpoint_reusable({"schema_version": "answer_book.figure_schema_plan.v1"}))
        self.assertTrue(
            _figure_schema_checkpoint_reusable(
                {"routing_policy_version": "answer_book.figure_routing.v6"}
            )
        )


if __name__ == "__main__":
    unittest.main()
