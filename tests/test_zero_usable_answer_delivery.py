from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.final_acceptance import AUDIT_FILES, build_final_acceptance_report
from app.pipeline_delivery import complete_pipeline_delivery, heavy_document_delivery_skip_decision


def _write_fragments(stage: Path, *, provider: str, fragments: list[dict]) -> None:
    (stage / "answer_fragments.json").write_text(
        json.dumps({"provider": provider, "fragments": fragments}, ensure_ascii=False),
        encoding="utf-8",
    )


def _all_unusable_fragment(*, message: str = "模型超时") -> dict:
    return {
        "question_id": "q1",
        "answer": "待复核",
        "_review_flags": [{"code": "answer_generation_failed", "message": message}],
    }


class TestZeroUsableAnswerDelivery(unittest.TestCase):
    def test_heavy_document_delivery_skip_condition_matrix(self) -> None:
        cases = [
        (
            "demo placeholder",
            "demo",
            [{"question_id": "q1", "answer": "待复核"}],
            False,
            False,
            True,
        ),
        (
            "configured answer failure",
            "configured",
            [_all_unusable_fragment(message="configure provider API key for real generation")],
            False,
            False,
            True,
        ),
        (
            "one usable answer preserves candidate delivery",
            "configured",
            [{"question_id": "q1", "answer": "可用答案"}, _all_unusable_fragment()],
            False,
            False,
            False,
        ),
        (
            "explicit document diagnostics preserve rendering",
            "configured",
            [_all_unusable_fragment()],
            True,
            False,
            False,
        ),
        (
            "retired render failure does not force heavy delivery",
            "configured",
            [_all_unusable_fragment()],
            False,
            True,
            True,
        ),
        (
            "missing fragments do not prove answer-stage failure",
            "configured",
            [],
            False,
            False,
            False,
        ),
        ]
        with tempfile.TemporaryDirectory() as raw_tmp:
            root = Path(raw_tmp)
            for index, (_name, provider, fragments, explicit_diagnostics, prior_render_failure, expected) in enumerate(cases):
                stage = root / str(index)
                stage.mkdir()
                _write_fragments(stage, provider=provider, fragments=fragments)
                if prior_render_failure:
                    (stage / "render_audit.json").write_text(
                        json.dumps({"ok": False, "issues": ["PDF text mismatch"]}), encoding="utf-8"
                    )

                decision = heavy_document_delivery_skip_decision(
                    stage,
                    preserve_document_diagnostics=explicit_diagnostics,
                )

                self.assertIs(decision["skip_heavy_delivery"], expected)


def _write_final_acceptance_inputs(stage: Path) -> None:
    for name, filename in AUDIT_FILES.items():
        if name in {"docx", "figure_size", "render"}:
            continue
        report: dict = {"ok": True, "issues": [], "warnings": []}
        if name == "environment":
            report["formula_conversion"] = {"preferred_chain_ready": True}
        if name == "content_quality":
            report = {
                "ok": False,
                "issue_count": 1,
                "issues": [{"question_id": "q1", "code": "missing_answer", "message": "答案为空"}],
                "warnings": [],
            }
        (stage / filename).write_text(json.dumps(report, ensure_ascii=False), encoding="utf-8")
    (stage / "structured_exam.json").write_text(json.dumps({"items": []}), encoding="utf-8")
    (stage / "pipeline_status.json").write_text(json.dumps({"stages": []}), encoding="utf-8")


class TestZeroUsableAnswerDeliveryBuild(unittest.TestCase):
    def test_zero_usable_answers_skip_document_build_but_preserve_failure_diagnostics(self) -> None:
        from app import pipeline_delivery

        with tempfile.TemporaryDirectory() as raw_tmp:
            root = Path(raw_tmp)
            stage = root / "stage"
            output = root / "output"
            stage.mkdir()
            output.mkdir()
            _write_final_acceptance_inputs(stage)
            fragments = [_all_unusable_fragment(message="configure provider API key for real generation")]
            _write_fragments(stage, provider="configured", fragments=fragments)
            original_fragments = (stage / "answer_fragments.json").read_text(encoding="utf-8")
            events: list[tuple[str, str]] = []

            def fake_model_usage(_stage: Path, out: Path, _task_id: str) -> Path:
                report = out / "模型调用汇总.md"
                report.write_text("diagnostic", encoding="utf-8")
                return report

            with (
                patch.object(pipeline_delivery, "checkpoint", lambda _task_id: None),
                patch.object(pipeline_delivery, "update_task", lambda *_args, **_kwargs: None),
                patch.object(pipeline_delivery, "build_model_usage_report", fake_model_usage),
            ):
                with self.assertRaisesRegex(RuntimeError, "Final acceptance audit failed"):
                    complete_pipeline_delivery(
                        task_id="all-unusable",
                        fragments_json=stage / "answer_fragments.json",
                        stage_dir=stage,
                        output_dir=output,
                        structured_exam={"items": []},
                        candidates=[],
                        selection_data={},
                        provider=object(),
                        model="test",
                        use_model=False,
                        render_with_word=True,
                        content_quality={"ok": False},
                        mark=lambda stage_name, status, _detail: events.append((stage_name, status)),
                        write_json=lambda path, value: path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8"),
                        build_docx_with_repair=lambda *_args, **_kwargs: (_ for _ in ()).throw(
                            AssertionError("all-unusable answers must not build Word")
                        ),
                    )

            skip = json.loads((stage / "document_delivery_skip.json").read_text(encoding="utf-8"))
            final = json.loads((stage / "final_acceptance_report.json").read_text(encoding="utf-8"))
            self.assertTrue(skip["skip_heavy_delivery"])
            self.assertEqual(0, skip["answer_fragment_delivery_summary"]["usable_count"])
            self.assertTrue(final["document_delivery_skipped"])
            self.assertEqual(0, final["answer_fragment_delivery_summary"]["usable_count"])
            self.assertTrue(final["gates"]["docx"]["skipped"])
            self.assertTrue(final["gates"]["render"]["skipped"])
            self.assertFalse((output / "answer_book.docx").exists())
            self.assertFalse((output / "answer_book_review_candidate.docx").exists())
            self.assertFalse((output / "word_rendered").exists())
            self.assertFalse((output / "question_review.docx").exists())
            self.assertTrue((stage / "acceptance_report.json").exists())
            self.assertEqual(original_fragments, (stage / "answer_fragments.json").read_text(encoding="utf-8"))
            self.assertIn(("delivery_short_circuit", "skipped"), events)
            self.assertIn(("final_acceptance", "failed"), events)

    def test_non_skipped_record_never_suppresses_document_acceptance_gates(self) -> None:
        with tempfile.TemporaryDirectory() as raw_tmp:
            root = Path(raw_tmp)
            stage = root / "stage"
            output = root / "output"
            stage.mkdir()
            output.mkdir()
            _write_final_acceptance_inputs(stage)
            _write_fragments(stage, provider="configured", fragments=[{"question_id": "q1", "answer": "可用答案"}])
            (stage / "acceptance_report.json").write_text(json.dumps({"status": "passed"}), encoding="utf-8")
            (stage / "document_delivery_skip.json").write_text(
                json.dumps({"status": "not_applicable", "skip_heavy_delivery": False}),
                encoding="utf-8",
            )

            report = build_final_acceptance_report(stage, output, require_render=False)

            self.assertFalse(report["document_delivery_skipped"])
            self.assertTrue(any(issue.startswith("output missing:") for issue in report["issues"]))

    def test_explicit_document_diagnostics_is_persisted_for_recovery(self) -> None:
        from app import task_store
        from app.task_store import TaskRecord

        with tempfile.TemporaryDirectory() as raw_tmp:
            tasks = Path(raw_tmp) / "tasks"
            tasks.mkdir()
            record = TaskRecord(
                task_id="preserve-document-diagnostics",
                exam_path="exam.docx",
                textbooks_dir="textbooks",
                provider="test",
                model="test",
                status="created",
                created_at="2026-08-27 00:00:00",
                updated_at="2026-08-27 00:00:00",
            )
            with patch.object(task_store, "TASKS_DIR", tasks):
                task_store.task_dir(record.task_id).mkdir()
                task_store.save_task(record)
                task_store.remember_task_run_options(
                    record.task_id,
                    use_model=False,
                    render=True,
                    reuse_fragments=False,
                    document_diagnostics=True,
                )
                recovered = task_store.load_task(record.task_id)

            self.assertTrue(recovered.last_run_document_diagnostics)
