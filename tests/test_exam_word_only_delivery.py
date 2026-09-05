from __future__ import annotations

import json
from zipfile import ZipFile

import pytest

from app.final_acceptance import AUDIT_FILES, build_final_acceptance_report


def passing_inputs(tmp_path):
    stage, output = tmp_path / "stage", tmp_path / "output"
    stage.mkdir()
    output.mkdir()
    for name, filename in AUDIT_FILES.items():
        if name == "render":
            continue
        report = {"ok": True, "issues": [], "warnings": []}
        if name == "environment":
            report["formula_conversion"] = {"preferred_chain_ready": True}
        (stage / filename).write_text(json.dumps(report))
    (stage / "acceptance_report.json").write_text(json.dumps({"status": "passed", "rendered": True}))
    (stage / "structured_exam.json").write_text(json.dumps({"items": []}))
    (stage / "pipeline_status.json").write_text(json.dumps({"stages": [{"stage": "render", "status": "failed"}]}))
    (output / "answer_book.docx").write_bytes(b"approved-word")
    return stage, output


@pytest.mark.parametrize("legacy_render", [False, True])
def test_word_can_be_formal_without_pdf_or_reading_stale_render_report(tmp_path, legacy_render):
    stage, output = passing_inputs(tmp_path)
    old = stage / "render_audit.json"
    old.write_text("historical invalid JSON that must not be read")
    result = build_final_acceptance_report(stage, output, require_render=legacy_render)
    assert result["formal_acceptance_passed"] is True
    assert result["require_render"] is False
    assert result["delivery_formats"] == ["docx"]
    assert result["gates"]["render"]["skipped"] is True
    assert "pdf" not in result["outputs"]
    assert old.read_text() == "historical invalid JSON that must not be read"


def test_word_failure_still_blocks_delivery(tmp_path):
    stage, output = passing_inputs(tmp_path)
    (stage / "docx_audit.json").write_text(json.dumps({"ok": False, "issues": ["raw latex marker"]}))
    result = build_final_acceptance_report(stage, output, require_render=False)
    assert result["delivery_ready"] is False
    assert any("raw latex marker" in issue for issue in result["issues"])


def test_package_excludes_old_pdf_and_page_images_without_deleting_them(tmp_path, monkeypatch):
    from app import delivery_package
    stage, output = passing_inputs(tmp_path)
    rendered = output / "word_rendered"
    rendered.mkdir()
    (rendered / "answer_book.pdf").write_bytes(b"old-pdf")
    (rendered / "page-1.png").write_bytes(b"old-page")
    monkeypatch.setattr(delivery_package, "build_model_usage_report", lambda *args: None)
    report = delivery_package.build_task_delivery_package("word-only", stage, output)
    assert report["ok"] is True
    with ZipFile(report["zip"]) as archive:
        assert "answer_book.docx" in archive.namelist()
        assert "answer_book.pdf" not in archive.namelist()
        assert not any(name.startswith("rendered_pages/") for name in archive.namelist())
    assert (rendered / "answer_book.pdf").read_bytes() == b"old-pdf"


def test_delivery_does_not_call_pdf_export_even_for_legacy_true(tmp_path, monkeypatch):
    from app import pipeline_delivery as delivery
    from app import render_word
    stage, output = passing_inputs(tmp_path)
    fragments = stage / "answer_fragments.json"
    fragments.write_text(json.dumps({"fragments": [{"question_id": "q1", "answer": "有效答案"}]}))
    monkeypatch.setattr(delivery, "checkpoint", lambda *args: None)
    monkeypatch.setattr(delivery, "update_task", lambda *args, **kwargs: None)
    monkeypatch.setattr(delivery, "audit_docx_figure_sizes", lambda *args, **kwargs: {"ok": True})
    def no_pdf(*args, **kwargs):
        pytest.fail("exam delivery must not invoke PDF export")
    monkeypatch.setattr(render_word, "export_docx_to_pdf", no_pdf)
    def review(sdir, odir, *, render_snapshots):
        assert render_snapshots is False
        path = odir / "question_review.docx"
        path.write_bytes(b"review")
        return path
    monkeypatch.setattr(delivery, "build_question_review_docx", review)
    monkeypatch.setattr(delivery, "build_figure_review_docx", lambda sdir, odir: odir / "figure_review.docx")
    monkeypatch.setattr(delivery, "collect_question_review_items", lambda *args: [])
    monkeypatch.setattr(delivery, "collect_question_figure_review_items", lambda *args: [])
    monkeypatch.setattr(delivery, "build_model_usage_report", lambda sdir, odir, task: odir / "usage.md")
    def build(task, src, dst, *args, **kwargs):
        dst.write_bytes(b"approved-word")
        return {"issues": []}
    def write(path, data):
        path.write_text(json.dumps(data))
    result = delivery.complete_pipeline_delivery(
        task_id="word-only", fragments_json=fragments, stage_dir=stage, output_dir=output,
        structured_exam={"items": []}, candidates=[], selection_data={}, provider=None,
        model="", use_model=False, render_with_word=True, content_quality={"ok": True},
        mark=lambda *args: None, write_json=write, build_docx_with_repair=build,
    )
    assert result["formal_acceptance_passed"] is True
    assert result["rendered"] is False
    assert not (output / "word_rendered").exists()
