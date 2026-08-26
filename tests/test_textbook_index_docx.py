from __future__ import annotations

import csv
from pathlib import Path

from docx import Document

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


def test_discover_textbooks_includes_supported_docx_files(tmp_path: Path) -> None:
    (tmp_path / "reference.docx").write_bytes(b"placeholder")
    (tmp_path / "reference.pdf").write_bytes(b"placeholder")

    assert [path.name for path in discover_textbooks(tmp_path)] == ["reference.docx"]
