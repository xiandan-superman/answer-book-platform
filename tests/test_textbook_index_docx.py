from __future__ import annotations

import csv
from pathlib import Path

from docx import Document

from app import textbook_index
from app.textbook_index import build_textbook_index_for_files, discover_textbooks


def _rows(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8-sig", newline="") as stream:
        return list(csv.DictReader(stream))


def test_docx_textbook_is_indexed_as_retrievable_prose_and_table_content(tmp_path: Path) -> None:
    textbook = tmp_path / "ideal_gas_reference.docx"
    document = Document()
    document.add_paragraph("For an ideal gas, pressure, volume, amount, and temperature obey pV = nRT.")
    document.add_paragraph("Use absolute temperature when applying the equation of state.")
    table = document.add_table(rows=2, cols=2)
    table.cell(0, 0).text = "Quantity"
    table.cell(0, 1).text = "Unit"
    table.cell(1, 0).text = "Pressure"
    table.cell(1, 1).text = "Pa"
    document.save(textbook)

    stage = tmp_path / "stage"
    result = build_textbook_index_for_files([textbook], stage)
    indexed = _rows(stage / "textbook_blocks.csv")

    assert result.block_count == 3
    assert all(row["text"].strip() for row in indexed)
    assert any("pV = nRT" in row["retrieval_text"] for row in indexed)
    table_row = next(row for row in indexed if row["block_type"] == "table")
    assert "Pressure | Pa" in table_row["retrieval_text"]
    assert table_row["source_type"] == "table_block"
    assert _rows(stage / "textbook_page_map.csv")[0]["verified"] == "false"


def test_discover_textbooks_includes_supported_docx_and_pdf_files(tmp_path: Path) -> None:
    (tmp_path / "reference.docx").write_bytes(b"placeholder")
    (tmp_path / "reference.pdf").write_bytes(b"placeholder")

    assert [path.name for path in discover_textbooks(tmp_path)] == ["reference.docx", "reference.pdf"]


def test_pdf_textbook_keeps_page_text_and_page_visual_for_model_evidence(tmp_path: Path, monkeypatch) -> None:
    textbook = tmp_path / "reference.pdf"
    textbook.write_bytes(b"pdf")
    page = tmp_path / "page-1.jpg"
    page.write_bytes(b"jpeg")
    monkeypatch.setattr(
        textbook_index,
        "render_page_representation",
        lambda *_args, **_kwargs: {
            "kind": "page_visuals",
            "status": "ready",
            "source_format": "pdf",
            "page_count_total": 1,
            "page_numbers_included": [1],
            "page_numbers_omitted": [],
            "paths": [str(page)],
            "page_texts": ["相平衡条件与相图"],
            "error": "",
        },
    )

    result = build_textbook_index_for_files([textbook], tmp_path / "stage")
    indexed = _rows(Path(result.blocks_csv))
    status = (tmp_path / "stage" / "textbook_index_status.json").read_text(encoding="utf-8")

    assert any(row["source_type"] == "text_block" and "相平衡" in row["retrieval_text"] for row in indexed)
    visual = next(row for row in indexed if row["source_type"] == "figure_block")
    assert visual["asset_path"] == str(page)
    assert "input_representations" in status
