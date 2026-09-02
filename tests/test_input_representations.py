from __future__ import annotations

from pathlib import Path

from app import exam_extract, practice_inputs


def test_degraded_persisted_docx_adds_page_visual_compensation_without_replacing_raw_or_text(monkeypatch) -> None:
    degraded = {
        "format": "docx",
        "omml_formula_count": 1,
        "omml_structured_formula_count": 0,
        "omml_degraded_formula_count": 1,
        "embedded_image_count": 0,
        "reference_image_order": [],
        "warnings": ["公式结构退化"],
    }
    monkeypatch.setattr(
        practice_inputs,
        "_decode_file",
        lambda _item: ("formula.docx", "application/vnd.openxmlformats-officedocument.wordprocessingml.document", b"raw-docx"),
    )
    monkeypatch.setattr(
        practice_inputs,
        "_docx_content",
        lambda _name, _data: ("反应式 x⟦OMML_STRUCTURE_UNAVAILABLE⟧", [], degraded),
    )
    monkeypatch.setattr(
        practice_inputs,
        "_docx_page_visuals",
        lambda _name, _data: {
            "kind": "page_visuals",
            "status": "ready",
            "source_format": "docx",
            "page_count_total": 1,
            "page_numbers_included": [1],
            "page_numbers_omitted": [],
            "paths": [],
            "page_texts": [],
            "error": "",
            "data_urls": ["data:image/jpeg;base64,AA=="],
        },
    )

    result = practice_inputs.parse_practice_sources({
        "source_files": [{"resource_id": "psrc_" + "0" * 64, "name": "formula.docx"}],
    })

    assert "反应式 x" in result["text"]
    assert result["reference_images"] == ["data:image/jpeg;base64,AA=="]
    assert result["images"] == ["data:image/jpeg;base64,AA=="]
    kinds = {item["kind"]: item["status"] for item in result["file_diagnostics"][0]["representations"]}
    assert kinds["raw_original"] == "ready"
    assert kinds["structured_text"] == "degraded"
    assert kinds["page_visuals"] == "ready"


def test_exam_page_visual_compensation_is_model_input_only(tmp_path: Path, monkeypatch) -> None:
    exam = tmp_path / "exam.docx"
    exam.write_bytes(b"raw")
    page = tmp_path / "page-1.jpg"
    page.write_bytes(b"jpeg")
    item = {
        "question_id": "q1",
        "number": "1",
        "stem": "计算 x⟦OMML_STRUCTURE_UNAVAILABLE⟧ 的值",
        "image_refs": [str(tmp_path / "original.png")],
    }
    monkeypatch.setattr(
        exam_extract,
        "render_page_representation",
        lambda *_args, **_kwargs: {
            "kind": "page_visuals",
            "status": "ready",
            "source_format": "docx",
            "page_count_total": 1,
            "page_numbers_included": [1],
            "page_numbers_omitted": [],
            "paths": [str(page)],
            "page_texts": ["1 计算 x 的值"],
            "error": "",
        },
    )

    report = exam_extract._attach_page_visual_compensation(exam, [item], tmp_path / "structured_exam.json")

    assert item["page_visual_refs"] == [str(page)]
    assert item["image_refs"] == [str(tmp_path / "original.png")]
    assert report["page_visual_compensated_question_ids"] == ["q1"]
