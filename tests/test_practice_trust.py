from __future__ import annotations

import json
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import app.practice_store as practice_store
from app.exercise_generation import (
    ensure_unique_figure_ids,
    normalize_practice_set,
    recompute_practice_quality,
    validate_generation_plan_identity,
)
from app.practice_export import build_practice_question_docx, validate_docx_output
from app.practice_store import (
    _compact_request,
    find_completed_by_plan,
    load_practice_record,
    plan_fingerprint,
    save_practice_record,
    undo_last_practice_revision,
    update_practice_exercise,
)

ROOT = Path(__file__).resolve().parents[1]


class PracticeTrustTests(unittest.TestCase):
    def test_frontend_exposes_local_semantic_repair_and_phase_batch_ids(self) -> None:
        js = (ROOT / "web" / "app.js").read_text(encoding="utf-8")
        self.assertIn("按复核建议修复本题", js)
        self.assertIn("data-practice-semantic-fix", js)
        self.assertIn("<span>阶段</span>", js)
        self.assertIn("<span>流程</span>", js)
        self.assertIn("button?.dataset.taskId || task?.task_id", js)

    def test_figure_ids_are_namespaced_and_unique_across_exercises(self) -> None:
        exercises = [
            {"exercise_id": "practice_01", "figures": [{"figure_id": "g1"}, {"figure_id": "shared"}]},
            {"exercise_id": "practice_02", "figures": [{"figure_id": "g1"}, {"figure_id": "shared"}]},
        ]
        ensure_unique_figure_ids(exercises)
        ids = [figure["figure_id"] for item in exercises for figure in item["figures"]]
        self.assertEqual(len(ids), len(set(ids)))
        self.assertTrue(all(value.startswith(("practice_01_", "practice_02_")) for value in ids))

    def test_figure_id_namespacing_is_idempotent_and_repairs_legacy_prefix_stacking(self) -> None:
        exercises = [{
            "exercise_id": "practice_03",
            "figures": [{"figure_id": "practice_03_" * 8 + "practice_01_g1"}],
        }]

        ensure_unique_figure_ids(exercises)
        first = exercises[0]["figures"][0]["figure_id"]
        ensure_unique_figure_ids(exercises)

        self.assertEqual("practice_03_g1", first)
        self.assertEqual(first, exercises[0]["figures"][0]["figure_id"])

    def test_quality_is_recomputed_after_edit_and_detects_duplicates(self) -> None:
        practice = {
            "exercises": [
                {"stem": "题干", "figures": [{"figure_id": "same"}]},
                {"stem": "题干", "figures": [{"figure_id": "same"}]},
            ]
        }
        quality = recompute_practice_quality(practice)
        self.assertTrue(quality["recomputed"])
        self.assertEqual(quality["generated_count"], 2)
        self.assertEqual(quality["status"], "passed")
        self.assertFalse(any("解析" in warning for warning in quality["warnings"]))
        self.assertNotEqual(
            practice["exercises"][0]["figures"][0]["figure_id"],
            practice["exercises"][1]["figures"][0]["figure_id"],
        )

    def test_history_request_keeps_recoverable_material_or_marks_blocked(self) -> None:
        ready = _compact_request({"source_files": [{"name": "题卷.docx", "type": "application/octet-stream", "size": 4, "data_url": "data:application/octet-stream;base64,AAAA"}]})
        blocked = _compact_request({"source_files": [{"name": "题卷.docx", "type": "application/octet-stream", "size": 4}]})
        self.assertEqual(ready["source_recovery"]["status"], "ready")
        self.assertEqual(ready["source_files"][0]["name"], "题卷.docx")
        self.assertEqual(blocked["source_recovery"]["status"], "blocked")

    def test_history_request_keeps_source_material_generation_switch(self) -> None:
        request = _compact_request({"include_source_content_in_generation": False})
        self.assertIs(request["include_source_content_in_generation"], False)

    def test_regeneration_accepts_original_plan_item_id(self) -> None:
        result = normalize_practice_set(
            {"source_analysis": {}, "blueprint": {}, "exercises": [{"plan_item_id": "plan_item_07", "stem": "题干"}]},
            requested_count=1,
            subject="材料科学",
            planned_plan_ids=["plan_item_07"],
        )
        self.assertEqual(result["exercises"][0]["plan_item_id"], "plan_item_07")

    def test_recovery_rejects_a_plan_with_a_different_strategy(self) -> None:
        with self.assertRaisesRegex(ValueError, "strategy.*不一致"):
            validate_generation_plan_identity(
                {"generation_strategy": "per_question"},
                {"generation_strategy": "parallel_exam"},
            )
        validate_generation_plan_identity(
            {"generation_strategy": "per_question"},
            {"generation_strategy": "per_question"},
        )

    def test_frontend_reuses_materials_and_refreshes_quality_after_edit(self) -> None:
        js = (ROOT / "web" / "app.js").read_text(encoding="utf-8")
        self.assertIn("source_recovery?.status === \"blocked\"", js)
        self.assertIn("latestPracticeRequest || practiceRequestPayload()", js)
        self.assertIn('response.semantic_review, response.practice_updates', js)
        self.assertIn('auditNeedsReview ? "review_and_regenerate_question" : "regenerate_question"', js)
        self.assertIn("semantic_review: semanticReview", js)
        self.assertIn("/exercise`, {", js)
        self.assertIn("latestPracticeSet = record.data", js)
        self.assertIn("restorePracticePreferenceOrders(latestPracticeRequest)", js)
        self.assertIn("generation_strategy: latestPracticeSet?.generation_strategy", js)
        self.assertIn("latestPracticeRequest?.blueprint_review_enabled !== false && savedData.blueprint ?", js)
        self.assertIn('"generate_from_contract"', js)
        self.assertIn("source_scope_checkpoint", js)
        self.assertIn('await saveRegeneratedPracticeExercise(practiceEditingIndex, editedExercise, "manual_edit")', js)
        self.assertNotIn('saveCurrentPractice(false, "manual_edit").catch(() => {})', js)
        self.assertIn("修改未保存，原题已保留", js)
        self.assertIn("split(/\\s+\\|\\s+/)", js)

    def test_completed_plan_is_reused_by_fingerprint(self) -> None:
        payload = {"source_mode": "exam", "generation_strategy": "parallel_exam", "count": 2, "difficulty": "进阶", "source_scope": {"mode": "top_level", "questions": [{"source_question_id": "q1", "title": "原题一", "knowledge_points": ["晶界"]}]}, "plan": {"blueprint": {"exercise_plan": [{"plan_item_id": "plan_item_01"}]}}}
        with tempfile.TemporaryDirectory() as raw:
            old = practice_store.PRACTICE_HISTORY_DIR
            try:
                practice_store.PRACTICE_HISTORY_DIR = Path(raw)
                path = Path(raw) / "practice_existing.json"
                path.write_text(
                    __import__("json").dumps({"history_id": "practice_existing", "status": "completed", "plan_fingerprint": plan_fingerprint(payload), "data": {"exercises": []}}),
                    encoding="utf-8",
                )
                self.assertIsNone(find_completed_by_plan(payload))
            finally:
                practice_store.PRACTICE_HISTORY_DIR = old

    def test_loaded_legacy_history_hides_answer_content_without_rewriting_audit_file(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            old = practice_store.PRACTICE_HISTORY_DIR
            try:
                practice_store.PRACTICE_HISTORY_DIR = Path(raw)
                path = Path(raw) / "practice_existing.json"
                path.write_text(__import__("json").dumps({"history_id": "practice_existing", "data": {"exercises": [{"stem": "题干", "answer": "旧答案", "solution_steps": ["旧解析"]}]}}), encoding="utf-8")
                loaded = load_practice_record("practice_existing")
                self.assertNotIn("answer", loaded["data"]["exercises"][0])
                self.assertIn('"answer"', path.read_text(encoding="utf-8"))
            finally:
                practice_store.PRACTICE_HISTORY_DIR = old

    def test_question_only_storage_preserves_answerability_check_fact_not_note(self) -> None:
        public = practice_store.strip_practice_answer_content(
            {"exercises": [{"stem": "题干", "verification_note": "条件充分，不含答案。"}]}
        )

        self.assertNotIn("verification_note", public["exercises"][0])
        self.assertEqual("reported", public["exercises"][0]["answerability_check_status"])

    def test_loaded_legacy_history_uses_current_quality_gate_without_rewriting_file(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            old = practice_store.PRACTICE_HISTORY_DIR
            try:
                practice_store.PRACTICE_HISTORY_DIR = Path(raw)
                path = Path(raw) / "practice_existing.json"
                original = {
                    "history_id": "practice_existing",
                    "status": "completed",
                    "data": {
                        "quality": {"status": "passed"},
                        "exercises": [
                            {
                                "stem": "请根据示意图作答。",
                                "knowledge_points": ["晶体缺陷"],
                                "verification_note": "条件充分。",
                                "figures": [{"figure_id": "g1", "figure_type": "diagram", "description": "文字图规格"}],
                            }
                        ],
                    },
                }
                path.write_text(__import__("json").dumps(original, ensure_ascii=False), encoding="utf-8")

                loaded = load_practice_record("practice_existing")

                self.assertEqual("completed", loaded["status"])
                self.assertEqual("passed", loaded["data"]["quality"]["status"])
                self.assertEqual([], loaded["data"]["quality"]["warnings"])
                self.assertIn('"status": "passed"', path.read_text(encoding="utf-8"))
            finally:
                practice_store.PRACTICE_HISTORY_DIR = old

    def test_saved_edits_create_bounded_reversible_versions(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            old = practice_store.PRACTICE_HISTORY_DIR
            try:
                practice_store.PRACTICE_HISTORY_DIR = Path(raw)
                first = save_practice_record({"exercises": [{"number": 1, "stem": "A"}]})
                history_id = first["history_id"]
                save_practice_record({"history_id": history_id, "exercises": [{"number": 1, "stem": "B"}]}, change_reason="manual_edit")

                loaded = load_practice_record(history_id)
                self.assertEqual(1, loaded["revision_count"])
                self.assertEqual("manual_edit", loaded["revisions"][0]["reason"])
                self.assertNotIn("data", loaded["revisions"][0])

                restored = undo_last_practice_revision(history_id)
                self.assertEqual("A", restored["data"]["exercises"][0]["stem"])
                redone = undo_last_practice_revision(history_id)
                self.assertEqual("B", redone["data"]["exercises"][0]["stem"])
            finally:
                practice_store.PRACTICE_HISTORY_DIR = old

    def test_manual_question_edit_invalidates_only_that_questions_semantic_review(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            old = practice_store.PRACTICE_HISTORY_DIR
            try:
                practice_store.PRACTICE_HISTORY_DIR = Path(raw)
                first = save_practice_record({
                    "semantic_review": {
                        "status": "passed",
                        "items": [
                            {"number": 1, "status": "passed", "risks": []},
                            {"number": 2, "status": "passed", "risks": []},
                        ],
                    },
                    "exercises": [
                        {"number": 1, "plan_item_id": "plan_item_01", "stem": "原第一题"},
                        {"number": 2, "plan_item_id": "plan_item_02", "stem": "原第二题"},
                    ],
                })
                edit_version = first["data"]["exercises"][0]["_edit_version"]

                updated = update_practice_exercise(
                    first["history_id"],
                    0,
                    {"number": 1, "plan_item_id": "plan_item_01", "stem": "用户修改后的第一题"},
                    change_reason="manual_edit",
                    expected_edit_version=edit_version,
                )

                review = updated["data"]["semantic_review"]
                self.assertEqual("failed", review["status"])
                self.assertEqual("stale_after_edit", review["review_scope"])
                self.assertEqual("not_reviewed", review["items"][0]["status"])
                self.assertEqual("passed", review["items"][1]["status"])
                self.assertEqual("用户修改后的第一题", updated["data"]["exercises"][0]["stem"])
                self.assertEqual("原第二题", updated["data"]["exercises"][1]["stem"])
            finally:
                practice_store.PRACTICE_HISTORY_DIR = old

    def test_unchanged_save_does_not_create_noise_revision(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            old = practice_store.PRACTICE_HISTORY_DIR
            try:
                practice_store.PRACTICE_HISTORY_DIR = Path(raw)
                first = save_practice_record({"exercises": [{"number": 1, "stem": "same"}]})
                save_practice_record({"history_id": first["history_id"], "exercises": [{"number": 1, "stem": "same"}]})
                self.assertEqual(0, load_practice_record(first["history_id"])["revision_count"])
            finally:
                practice_store.PRACTICE_HISTORY_DIR = old

    def test_history_writes_are_atomic_and_leave_no_temporary_files(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            old = practice_store.PRACTICE_HISTORY_DIR
            try:
                practice_store.PRACTICE_HISTORY_DIR = Path(raw)
                first = save_practice_record({"exercises": [{"number": 1, "stem": "initial"}]})
                history_id = first["history_id"]

                def update(index: int) -> None:
                    save_practice_record(
                        {"history_id": history_id, "exercises": [{"number": 1, "stem": f"edit-{index}"}]},
                        change_reason="concurrency_test",
                    )

                with ThreadPoolExecutor(max_workers=4) as pool:
                    list(pool.map(update, range(8)))

                saved_path = Path(raw) / f"{history_id}.json"
                saved = json.loads(saved_path.read_text(encoding="utf-8"))
                self.assertEqual(history_id, saved["history_id"])
                self.assertEqual([], list(Path(raw).glob(".*.tmp")))
            finally:
                practice_store.PRACTICE_HISTORY_DIR = old

    def test_question_patch_updates_merge_instead_of_overwriting_other_regenerations(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            old = practice_store.PRACTICE_HISTORY_DIR
            try:
                practice_store.PRACTICE_HISTORY_DIR = Path(raw)
                first = save_practice_record({"exercises": [
                    {"number": 1, "plan_item_id": "plan_item_01", "question_type": "简答题", "stem": "原第一题"},
                    {"number": 2, "plan_item_id": "plan_item_02", "question_type": "简答题", "stem": "原第二题"},
                ]})
                history_id = first["history_id"]
                edit_versions = [item["_edit_version"] for item in first["data"]["exercises"]]

                def patch_question(index: int) -> None:
                    update_practice_exercise(
                        history_id,
                        index,
                        {"question_type": "简答题", "stem": f"并发生成后的第 {index + 1} 题"},
                        change_reason="regenerate_selected_questions",
                        expected_edit_version=edit_versions[index],
                    )

                with ThreadPoolExecutor(max_workers=2) as pool:
                    list(pool.map(patch_question, [0, 1]))

                loaded = load_practice_record(history_id)
                self.assertEqual(
                    ["并发生成后的第 1 题", "并发生成后的第 2 题"],
                    [item["stem"] for item in loaded["data"]["exercises"]],
                )
                self.assertEqual(2, loaded["revision_count"])
                self.assertTrue(all(row["summary"]["changed_count"] == 1 for row in loaded["revisions"]))
            finally:
                practice_store.PRACTICE_HISTORY_DIR = old

    def test_question_edit_without_version_is_rejected_as_an_old_page(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            old = practice_store.PRACTICE_HISTORY_DIR
            try:
                practice_store.PRACTICE_HISTORY_DIR = Path(raw)
                saved = save_practice_record({"exercises": [{"number": 1, "stem": "服务器内容"}]})
                with self.assertRaisesRegex(practice_store.PracticeEditConflict, "缺少编辑版本"):
                    update_practice_exercise(
                        saved["history_id"],
                        0,
                        {"number": 1, "stem": "无版本旧页面内容"},
                    )
                self.assertEqual(
                    "服务器内容",
                    load_practice_record(saved["history_id"])["data"]["exercises"][0]["stem"],
                )
            finally:
                practice_store.PRACTICE_HISTORY_DIR = old

    def test_stale_same_question_edit_is_rejected_without_overwriting_newer_content(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            old = practice_store.PRACTICE_HISTORY_DIR
            try:
                practice_store.PRACTICE_HISTORY_DIR = Path(raw)
                first = save_practice_record({"exercises": [{"number": 1, "stem": "原题"}]})
                history_id = first["history_id"]
                initial = load_practice_record(history_id)
                version = initial["data"]["exercises"][0]["_edit_version"]

                updated = update_practice_exercise(
                    history_id,
                    0,
                    {"number": 1, "stem": "先保存的新内容"},
                    expected_edit_version=version,
                )
                self.assertNotEqual(version, updated["data"]["exercises"][0]["_edit_version"])

                with self.assertRaisesRegex(practice_store.PracticeEditConflict, "另一个页面"):
                    update_practice_exercise(
                        history_id,
                        0,
                        {"number": 1, "stem": "旧页面后保存的内容"},
                        expected_edit_version=version,
                    )

                loaded = load_practice_record(history_id)
                self.assertEqual("先保存的新内容", loaded["data"]["exercises"][0]["stem"])
            finally:
                practice_store.PRACTICE_HISTORY_DIR = old

    def test_edit_versions_are_public_metadata_but_not_persisted_content(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            old = practice_store.PRACTICE_HISTORY_DIR
            try:
                practice_store.PRACTICE_HISTORY_DIR = Path(raw)
                saved = save_practice_record({"exercises": [{"number": 1, "stem": "题干"}]})
                history_id = saved["history_id"]
                self.assertTrue(saved["data"]["exercises"][0]["_edit_version"])
                self.assertTrue(saved["data"]["_record_edit_version"])
                self.assertTrue(load_practice_record(history_id)["data"]["exercises"][0]["_edit_version"])
                stored = json.loads((Path(raw) / f"{history_id}.json").read_text(encoding="utf-8"))
                self.assertNotIn("_record_edit_version", stored["data"])
                self.assertNotIn("_edit_version", stored["data"]["exercises"][0])
            finally:
                practice_store.PRACTICE_HISTORY_DIR = old

    def test_plan_fingerprint_includes_scope_identity(self) -> None:
        base = {"source_mode": "exam", "generation_strategy": "parallel_exam", "count": 1, "plan": {"blueprint": {"exercise_plan": [{"plan_item_id": "plan_item_01"}]}}}
        left = {**base, "source_scope": {"mode": "top_level", "questions": [{"source_question_id": "q1", "title": "题一", "knowledge_points": ["A"]}]}}
        right = {**base, "source_scope": {"mode": "top_level", "questions": [{"source_question_id": "q1", "title": "题一", "knowledge_points": ["B"]}]}}
        self.assertNotEqual(plan_fingerprint(left), plan_fingerprint(right))

    def test_plan_fingerprint_includes_source_material_generation_switch(self) -> None:
        base = {
            "source_mode": "exam",
            "generation_strategy": "parallel_exam",
            "count": 1,
            "plan": {"blueprint": {"exercise_plan": [{"plan_item_id": "plan_item_01"}]}},
        }
        self.assertNotEqual(
            plan_fingerprint({**base, "include_source_content_in_generation": True}),
            plan_fingerprint({**base, "include_source_content_in_generation": False}),
        )

    def test_plan_fingerprint_includes_blueprint_multi_question_settings(self) -> None:
        base = {
            "source_mode": "exam",
            "generation_strategy": "parallel_exam",
            "count": 1,
            "plan": {"blueprint": {"exercise_plan": [{"plan_item_id": "plan_item_01"}]}},
        }
        single = plan_fingerprint({**base, "blueprint_multi_question_enabled": False})
        progressive = plan_fingerprint({
            **base,
            "blueprint_multi_question_enabled": True,
            "blueprint_variants_per_item": 3,
            "blueprint_variant_mode": "progressive",
        })
        same_difficulty = plan_fingerprint({
            **base,
            "blueprint_multi_question_enabled": True,
            "blueprint_variants_per_item": 3,
            "blueprint_variant_mode": "same_difficulty",
        })
        self.assertNotEqual(single, progressive)
        self.assertNotEqual(progressive, same_difficulty)

    def test_generation_run_id_creates_an_independent_result_identity(self) -> None:
        base = {
            "source_mode": "exam",
            "generation_strategy": "parallel_exam",
            "count": 1,
            "difficulty_counts": {"基础": 0, "进阶": 1, "挑战": 0},
            "plan": {"blueprint": {"exercise_plan": [{"plan_item_id": "plan_item_01"}]}},
        }
        self.assertNotEqual(
            plan_fingerprint({**base, "generation_run_id": "run_1"}),
            plan_fingerprint({**base, "generation_run_id": "run_2"}),
        )

    def test_plan_fingerprint_includes_scope_granularity_and_parent(self) -> None:
        base = {"source_mode": "exam", "generation_strategy": "parallel_exam", "count": 1, "plan": {"blueprint": {"exercise_plan": [{"plan_item_id": "plan_item_01"}]}}}
        top = {**base, "source_scope": {"mode": "top_level", "granularity": "top_level", "questions": [{"source_question_id": "q1", "parent_id": "", "title": "题一"}]}}
        child = {**base, "source_scope": {"mode": "top_level", "granularity": "atomic", "questions": [{"source_question_id": "q1", "parent_id": "q_parent", "title": "题一"}]}}
        self.assertNotEqual(plan_fingerprint(top), plan_fingerprint(child))

    def test_invalid_status_is_not_reused_but_completed_is(self) -> None:
        payload = {"source_mode": "exam", "generation_strategy": "parallel_exam", "count": 1, "plan": {"blueprint": {"exercise_plan": [{"plan_item_id": "plan_item_01"}]}}}
        with tempfile.TemporaryDirectory() as raw:
            old = practice_store.PRACTICE_HISTORY_DIR
            try:
                practice_store.PRACTICE_HISTORY_DIR = Path(raw)
                import json
                fingerprint = plan_fingerprint(payload)
                for name, status in (("practice_invalid", "invalid"), ("practice_completed", "completed")):
                    (Path(raw) / f"{name}.json").write_text(json.dumps({"history_id": name, "status": status, "plan_fingerprint": fingerprint, "data": {"exercises": [{"stem": "题"}]}}), encoding="utf-8")
                reused = find_completed_by_plan(payload)
                self.assertEqual(reused["history_id"], "practice_completed")
            finally:
                practice_store.PRACTICE_HISTORY_DIR = old

    def test_docx_output_reports_missing_embedded_figure_media(self) -> None:
        data = {"exercises": [{"stem": "题干", "figures": [{"figure_id": "g1", "figure_type": "diagram", "title": "图"}]}]}
        result = validate_docx_output(build_practice_question_docx(data), data)
        self.assertFalse(result["ok"])
        self.assertTrue(result["issues"])


if __name__ == "__main__":
    unittest.main()
