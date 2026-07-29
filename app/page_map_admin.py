from __future__ import annotations

import csv
from pathlib import Path
from typing import Any

from .textbook_index import PAGE_MAP_FIELDS, citation_textbook_name


def read_csv_rows(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    with path.open(newline="", encoding="utf-8-sig") as f:
        return [{k: str(v or "") for k, v in row.items()} for row in csv.DictReader(f)]


def write_page_map_rows(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    cleaned: list[dict[str, str]] = []
    for row in rows:
        textbook = str(row.get("textbook", "")).strip()
        pdf_page_idx = str(row.get("pdf_page_idx") or row.get("page_idx") or "").strip()
        printed_page = str(row.get("printed_page", "")).strip()
        source_file = str(row.get("source_file", "")).strip()
        if not textbook or not source_file or not pdf_page_idx or not printed_page:
            continue
        cleaned.append(
            {
                "textbook": textbook,
                "citation_textbook": str(row.get("citation_textbook") or citation_textbook_name(textbook)).strip(),
                "source_file": source_file,
                "pdf_page_idx": pdf_page_idx,
                "printed_page": printed_page,
                "page_source": str(row.get("page_source") or "manual").strip(),
                "verified": str(row.get("verified") or "true").strip(),
                "confidence": str(row.get("confidence") or "high").strip(),
                "notes": str(row.get("notes") or "manual verified printed page").strip(),
            }
        )
    with path.open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=PAGE_MAP_FIELDS)
        writer.writeheader()
        writer.writerows(cleaned)


def page_map_summary(page_map_csv: Path, manual_csv: Path) -> dict[str, Any]:
    rows = read_csv_rows(page_map_csv)
    manual_rows = read_csv_rows(manual_csv)
    unverified = [row for row in rows if row.get("verified") != "true" or not row.get("printed_page")]
    low_confidence = [row for row in rows if row.get("confidence") in {"none", "low"}]
    by_textbook: dict[str, dict[str, int]] = {}
    by_source: dict[str, dict[str, int | str]] = {}
    for row in rows:
        textbook = row.get("textbook", "")
        bucket = by_textbook.setdefault(textbook, {"total": 0, "verified": 0, "unverified": 0, "low_confidence": 0})
        bucket["total"] += 1
        if row.get("verified") == "true" and row.get("printed_page"):
            bucket["verified"] += 1
        else:
            bucket["unverified"] += 1
        if row.get("confidence") in {"none", "low"}:
            bucket["low_confidence"] += 1
        source_file = row.get("source_file", "")
        source_key = f"{textbook}::{source_file}"
        source_bucket = by_source.setdefault(
            source_key,
            {
                "textbook": textbook,
                "source_file": source_file,
                "total": 0,
                "verified": 0,
                "unverified": 0,
                "low_confidence": 0,
            },
        )
        source_bucket["total"] = int(source_bucket["total"]) + 1
        if row.get("verified") == "true" and row.get("printed_page"):
            source_bucket["verified"] = int(source_bucket["verified"]) + 1
        else:
            source_bucket["unverified"] = int(source_bucket["unverified"]) + 1
        if row.get("confidence") in {"none", "low"}:
            source_bucket["low_confidence"] = int(source_bucket["low_confidence"]) + 1
    return {
        "page_map_csv": str(page_map_csv),
        "manual_csv": str(manual_csv),
        "total_rows": len(rows),
        "manual_rows": len(manual_rows),
        "unverified_count": len(unverified),
        "low_confidence_count": len(low_confidence),
        "by_textbook": by_textbook,
        "by_source": by_source,
        "unverified_sample": unverified[:100],
        "low_confidence_sample": low_confidence[:100],
        "manual_rows_data": manual_rows,
        "fields": PAGE_MAP_FIELDS,
    }
