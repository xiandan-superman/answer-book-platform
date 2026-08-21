from __future__ import annotations

import csv
import difflib
import json
import re
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from .capabilities.catalog import capability_policy_contributions
from .text_utils import clean_text, formulas_equivalent, normalize_formula, tokenize_zh_en


@dataclass
class EvidenceCandidate:
    evidence_id: str
    question_id: str
    textbook: str
    citation_textbook: str
    chapter_section: str
    source_file: str
    pdf_page_idx: str
    printed_page: str
    score: float
    evidence_text: str
    verified_page: bool
    knowledge_point: str = ""
    block_id: str = ""
    block_type: str = ""
    source_type: str = "text_block"
    bbox: str = ""
    caption: str = ""
    ocr_text: str = ""
    asset_path: str = ""
    table_html: str = ""
    visual_summary: str = ""
    visual_status: str = ""
    visual_unreadable_reason: str = ""
    table_rows: str = ""
    surrounding_text_refs: str = ""
    surrounding_text_preview: str = ""


NOISE_TEXT_RE = re.compile(r"(目录|索引|本章小结|习题)")
CATALOG_LINE_RE = re.compile(r"(?:章|§\s*\d|第[一二三四五六七八九十]+章).*(?:…{2,}|\.{3,}|……).*\d")
LEADER_DOT_RE = re.compile(r"(?:…{2,}|\.{3,}|……)")
TABLE_TAG_RE = re.compile(r"</?(table|tr|td|th)\b", re.IGNORECASE)
NON_EVIDENCE_BLOCK_TYPES = {"page_number", "page-num", "page_num", "page"}
RETRIEVAL_CONTEXT_POLICY_VERSION = "answer_book.retrieval_context.v3"
TABLE_QUERY_RE = re.compile(r"(表|表格|数据|数值|参数|比较|对照|列出|计算|查表|性能|成分|波长|电压|温度)")
FIGURE_QUERY_RE = re.compile(r"(图|图示|曲线|示意|坐标|图像|谱)")


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


def _page_identity(row: dict[str, str], page_key: str = "page_idx") -> tuple[str, str, str]:
    """Use source_file because each textbook volume starts its PDF page index at zero."""
    return (
        row.get("textbook", ""),
        row.get("source_file", ""),
        row.get(page_key, ""),
    )


def build_page_lookup(page_map_csv: Path) -> dict[tuple[str, str, str], dict[str, str]]:
    out = {}
    for row in read_csv(page_map_csv):
        out[_page_identity(row, "pdf_page_idx")] = row
    return out


def score_text(query_tokens: list[str], text: str) -> float:
    """Legacy single-document scorer retained for callers outside corpus retrieval."""
    if not query_tokens:
        return 0.0
    text_tokens = tokenize_zh_en(text)
    if not text_tokens:
        return 0.0
    freq: dict[str, int] = {}
    for token in text_tokens:
        freq[token] = freq.get(token, 0) + 1
    score = 0.0
    for token in query_tokens:
        if token in freq:
            score += 1.0 + min(freq[token] - 1, 4) * 0.25
    if clean_text("".join(query_tokens)) in clean_text(text):
        score += 8.0
    return score


class CorpusTextScorer:
    """BM25 corpus scorer with a deterministic compatibility fallback."""

    def __init__(self, texts: list[str]) -> None:
        self.texts = texts
        self.tokens = [tokenize_zh_en(text) for text in texts]
        self.retriever: Any | None = None
        if not any(self.tokens):
            return
        try:
            import bm25s

            retriever = bm25s.BM25(method="lucene", backend="numpy", csc_backend="numpy")
            retriever.index(self.tokens, show_progress=False)
            self.retriever = retriever
        except Exception:
            # Keep retrieval usable in partially upgraded environments. The
            # environment gate reports the missing/invalid dependency.
            self.retriever = None

    @property
    def backend(self) -> str:
        return "bm25s" if self.retriever is not None else "legacy"

    def scores(self, query_tokens: list[str]) -> list[float]:
        if not query_tokens:
            return [0.0] * len(self.texts)
        if self.retriever is None:
            return [score_text(query_tokens, text) for text in self.texts]
        raw_scores = self.retriever.get_scores(query_tokens)
        return [float(value) for value in raw_scores]


def _has_shared_phrase(text: str, point: str, min_len: int = 2) -> bool:
    compact_text = re.sub(r"\s+", "", clean_text(text))
    compact_point = re.sub(r"\s+", "", clean_text(point))
    for size in range(min(len(compact_text), len(compact_point)), min_len - 1, -1):
        for index in range(0, len(compact_text) - size + 1):
            if compact_text[index : index + size] in compact_point:
                return True
    return False


def _related_to_point(text: str, point: str, *, strict: bool = False) -> bool:
    text = clean_text(text)
    point = clean_text(point)
    if not text or not point:
        return False
    if text in point or point in text:
        return True
    if _has_shared_phrase(text, point, min_len=3 if strict else 2):
        return True
    point_tokens = set(tokenize_zh_en(point))
    text_tokens = set(tokenize_zh_en(text))
    overlap = point_tokens & text_tokens
    if strict:
        return len(overlap) >= 2
    return bool(overlap)


def _point_queries(question: dict[str, Any], knowledge_plan: dict[str, Any] | None = None, target_points: list[str] | None = None) -> list[tuple[str, str]]:
    if not knowledge_plan:
        return [("", clean_text(" ".join([str(question.get("section", "")), str(question.get("stem", ""))])))]
    points = [clean_text(str(x)) for x in (target_points or knowledge_plan.get("knowledge_points") or []) if clean_text(str(x))]
    if not points:
        return [("", retrieval_query_text(question, knowledge_plan))]
    key_terms = [clean_text(str(x)) for x in knowledge_plan.get("key_terms", []) if clean_text(str(x))]
    search_queries = [clean_text(str(x)) for x in knowledge_plan.get("search_queries", []) if clean_text(str(x))]
    formulas = [clean_text(str(x)) for x in knowledge_plan.get("formulas", []) if clean_text(str(x))]
    out: list[tuple[str, str]] = []
    for index, point in enumerate(points):
        parts = [point]
        parts.extend(term for term in key_terms if _related_to_point(term, point))
        parts.extend(query for query in search_queries if _related_to_point(query, point, strict=True))
        parts.extend(formula for formula in formulas if _related_to_point(formula, point) or (len(points) == 1 or index == 0))
        out.append((point, clean_text(" ".join(dict.fromkeys(parts)))))
    return out


def retrieval_query_text(question: dict[str, Any], knowledge_plan: dict[str, Any] | None = None) -> str:
    if knowledge_plan:
        parts: list[str] = []
        for key in ("search_queries", "knowledge_points", "formulas", "key_terms"):
            value = knowledge_plan.get(key)
            if isinstance(value, list):
                parts.extend(str(x) for x in value)
            elif isinstance(value, str):
                parts.append(value)
        query = clean_text(" ".join(parts))
        if query:
            return query
    return clean_text(" ".join([str(question.get("section", "")), str(question.get("stem", ""))]))


def is_invalid_evidence_row(row: dict[str, str], page_map: dict[str, str]) -> bool:
    if not str(page_map.get("printed_page", "")).strip():
        return True
    block_type = str(row.get("block_type", "")).strip().lower()
    source_type = str(row.get("source_type", "")).strip().lower()
    text = clean_text(row.get("text", ""))
    if block_type in NON_EVIDENCE_BLOCK_TYPES:
        return True
    if source_type == "text_block" and TABLE_TAG_RE.search(text):
        return True
    if NOISE_TEXT_RE.search(text):
        return True
    if len(LEADER_DOT_RE.findall(text)) >= 3 and len(re.findall(r"\d{1,4}", text)) >= 3:
        return True
    if CATALOG_LINE_RE.search(text) and len(re.findall(r"(?:…{2,}|\.{3,}|……)", text)) >= 1:
        return True
    if len(text) < 8:
        return True
    return False


def retrieval_text_for_row(row: dict[str, str]) -> str:
    return clean_text(row.get("retrieval_text") or row.get("text") or "")


def source_type_score_bonus(query: str, row: dict[str, str]) -> float:
    source_type = str(row.get("source_type", "")).strip()
    if source_type == "table_block" and TABLE_QUERY_RE.search(query):
        return 4.0
    capability_figure_query = any(
        contribution.get("figure_query") is True
        for contribution in capability_policy_contributions(
            "retrieval_source_routing",
            {"query": query, "row": row, "text": query},
            text=query,
        )
        if isinstance(contribution, dict)
    )
    if source_type == "figure_block" and (FIGURE_QUERY_RE.search(query) or capability_figure_query):
        return 3.0
    if source_type == "equation_block" and re.search(r"(公式|方程|关系式|推导|计算|表达式|定律|判据)", query):
        return 3.0
    return 0.0


def planned_formulas_for_query(query: str, knowledge_plan: dict[str, Any] | None) -> list[str]:
    """Find planned formulas explicitly associated with a knowledge-point query."""
    if not knowledge_plan:
        return []
    compact_query = normalize_formula(query)
    out: list[str] = []
    for raw in knowledge_plan.get("formulas", []) or []:
        formula = clean_text(raw)
        canonical = normalize_formula(formula)
        if formula and canonical and canonical in compact_query and formula not in out:
            out.append(formula)
    return out


def formula_match_score(planned_formula: str, row_text: str, *, context: str = "") -> float:
    """Score only algebraically equivalent textbook formula blocks.

    A positive result is intentionally high enough to reserve a candidate slot;
    normal keyword retrieval still determines all non-formula evidence.
    """
    expression = str(row_text or "").split("\n相邻教材说明：", 1)[0]
    return 100.0 if formulas_equivalent(planned_formula, expression, context=context) else 0.0


def _equation_context(row: dict[str, str], page_rows: list[dict[str, str]]) -> tuple[str, str]:
    """Keep the equation first, then append short adjacent prose for LLM review."""
    row_text = retrieval_text_for_row(row)
    if str(row.get("source_type", "")).strip().lower() != "equation_block":
        return row_text, str(row.get("surrounding_text_preview", ""))
    try:
        index = page_rows.index(row)
    except ValueError:
        return row_text, str(row.get("surrounding_text_preview", ""))
    nearby: list[str] = []
    for other in page_rows[max(0, index - 2) : index + 3]:
        if other is row or str(other.get("source_type", "")).strip().lower() != "text_block":
            continue
        text = retrieval_text_for_row(other)
        if text:
            nearby.append(text[:280])
    context = " ".join(dict.fromkeys(nearby))[:560]
    if not context:
        return row_text, str(row.get("surrounding_text_preview", ""))
    return f"{row_text}\n相邻教材说明：{context}", context


def _adjacent_text_context(row: dict[str, str], page_rows: list[dict[str, str]]) -> tuple[str, str]:
    """Preserve the end of a selected paragraph with its next text block.

    MinerU may split one semantic paragraph at a block boundary.  Returning a
    candidate ending in a comma (as happened for a phase-transformation
    explanation) hides the conclusion from evidence selection and answer
    generation even though it is on the same textbook page.
    """

    row_text = retrieval_text_for_row(row)
    if str(row.get("source_type", "")).strip().lower() != "text_block":
        return row_text, str(row.get("surrounding_text_preview", ""))
    try:
        index = page_rows.index(row)
    except ValueError:
        return row_text, str(row.get("surrounding_text_preview", ""))
    needs_continuation = bool(re.search(r"[,，；;:：]$", row_text.strip())) or len(row_text) < 220
    if not needs_continuation:
        return row_text, str(row.get("surrounding_text_preview", ""))
    adjacent: list[str] = []
    # A paragraph continuation is often separated by a figure/table block.
    # Scan a bounded same-page window and collect text blocks rather than
    # inspecting only the next two physical blocks.
    for other in page_rows[index + 1 : index + 9]:
        if str(other.get("source_type", "")).strip().lower() != "text_block":
            continue
        text = retrieval_text_for_row(other)
        if text:
            adjacent.append(text[:500])
        if len(adjacent) >= 2:
            break
    context = " ".join(adjacent)[:800]
    if not context:
        return row_text, str(row.get("surrounding_text_preview", ""))
    return f"{row_text}\n同页后续说明：{context}", context


def _canonical_semantic_row(row: dict[str, str], page_rows: list[dict[str, str]]) -> dict[str, str]:
    """Prefer complete page text over duplicated, truncated figure/table OCR."""

    source_type = str(row.get("source_type", "")).strip().lower()
    visual_text = re.sub(r"\s+", "", retrieval_text_for_row(row))
    if source_type not in {"figure_block", "table_block"} or len(visual_text) < 100:
        return row
    best = row
    best_match = 0
    for other in page_rows:
        if str(other.get("source_type", "")).strip().lower() != "text_block":
            continue
        if str(other.get("block_type", "")).strip().lower() in {"header", "footer", "page_number"}:
            continue
        prose = re.sub(r"\s+", "", retrieval_text_for_row(other))
        if len(prose) < 100:
            continue
        match = difflib.SequenceMatcher(None, visual_text, prose, autojunk=False).find_longest_match()
        if match.size > best_match:
            best_match = match.size
            best = other
    return best if best_match >= 80 else row


def build_candidates(
    structured_exam: dict,
    blocks_csv: Path,
    page_map_csv: Path,
    output_csv: Path,
    top_k: int = 5,
    knowledge_plans: dict[str, dict[str, Any]] | None = None,
    target_points_by_qid: dict[str, list[str]] | None = None,
    id_offset_by_qid: dict[str, int] | None = None,
) -> list[EvidenceCandidate]:
    blocks = read_csv(blocks_csv)
    page_lookup = build_page_lookup(page_map_csv)
    retrieval_texts = [retrieval_text_for_row(row) for row in blocks]
    text_scorer = CorpusTextScorer(retrieval_texts)
    rows_by_page: dict[tuple[str, str, str], list[dict[str, str]]] = {}
    for row in blocks:
        rows_by_page.setdefault(_page_identity(row), []).append(row)
    candidates: list[EvidenceCandidate] = []
    fields = [
        "evidence_id",
        "question_id",
        "textbook",
        "citation_textbook",
        "chapter_section",
        "source_file",
        "pdf_page_idx",
        "printed_page",
        "score",
        "evidence_text",
        "verified_page",
        "knowledge_point",
        "block_id",
        "block_type",
        "source_type",
        "bbox",
        "caption",
        "ocr_text",
        "asset_path",
        "table_html",
        "visual_summary",
        "visual_status",
        "visual_unreadable_reason",
        "table_rows",
        "surrounding_text_refs",
        "surrounding_text_preview",
    ]
    for item in structured_exam.get("items", []):
        qid = str(item.get("question_id", ""))
        if target_points_by_qid is not None and qid not in target_points_by_qid:
            continue
        plan = (knowledge_plans or {}).get(qid)
        point_queries = _point_queries(item, plan, (target_points_by_qid or {}).get(qid))
        q_counter = int((id_offset_by_qid or {}).get(qid, 0))
        for knowledge_point, query in point_queries:
            q_tokens = tokenize_zh_en(query)
            corpus_scores = text_scorer.scores(q_tokens)
            scored: list[tuple[float, dict[str, str], str]] = []
            formula_scored: list[tuple[float, dict[str, str], str]] = []
            planned_formulas = planned_formulas_for_query(query, plan)
            seen_rows: set[tuple[str, str, str, str]] = set()
            for row_index, row in enumerate(blocks):
                pm = page_lookup.get(_page_identity(row), {})
                if is_invalid_evidence_row(row, pm):
                    continue
                row_text = retrieval_text_for_row(row)
                score = corpus_scores[row_index]
                if clean_text("".join(q_tokens)) in clean_text(row_text):
                    score += 8.0
                score += source_type_score_bonus(query, row)
                if score <= 0:
                    continue
                row_key = (*_page_identity(row), row.get("block_index", ""))
                if row_key in seen_rows:
                    continue
                seen_rows.add(row_key)
                scored.append((score, row, row_text))
                if str(row.get("source_type", "")).strip().lower() == "equation_block":
                    formula_score = max(
                        (formula_match_score(formula, row_text, context=query) for formula in planned_formulas),
                        default=0.0,
                    )
                    if formula_score > 0:
                        formula_scored.append((formula_score, row, row_text))
            scored.sort(key=lambda x: x[0], reverse=True)
            formula_scored.sort(key=lambda x: x[0], reverse=True)
            selected_rows: list[tuple[float, dict[str, str], str]] = []
            selected_keys: set[tuple[str, str, str, str]] = set()
            # Preserve exact/equivalent planned formulas even if lexical ranking is poor.
            for score, row, row_text in formula_scored[:3]:
                key = (*_page_identity(row), row.get("block_index", ""))
                selected_rows.append((score, row, row_text))
                selected_keys.add(key)
            for score, row, row_text in scored[:top_k]:
                key = (*_page_identity(row), row.get("block_index", ""))
                if key not in selected_keys:
                    selected_rows.append((score, row, row_text))
                    selected_keys.add(key)
            for score, row, _row_text in selected_rows:
                q_counter += 1
                page_rows = rows_by_page.get(_page_identity(row), [])
                row = _canonical_semantic_row(row, page_rows)
                pm = page_lookup.get(_page_identity(row), {})
                evidence_text, equation_context = _equation_context(
                    row,
                    page_rows,
                )
                if evidence_text == retrieval_text_for_row(row):
                    evidence_text, equation_context = _adjacent_text_context(
                        row,
                        page_rows,
                    )
                candidate = EvidenceCandidate(
                    evidence_id=f"ev_{qid}_{q_counter:02d}",
                    question_id=qid,
                    textbook=row.get("textbook", ""),
                    citation_textbook=pm.get("citation_textbook") or row.get("textbook", ""),
                    chapter_section=row.get("chapter_section", ""),
                    source_file=row.get("source_file", ""),
                    pdf_page_idx=row.get("page_idx", ""),
                    printed_page=pm.get("printed_page", ""),
                    score=round(score, 4),
                    evidence_text=evidence_text[:1000],
                    verified_page=str(pm.get("verified", "")).lower() == "true",
                    knowledge_point=knowledge_point,
                    block_id=row.get("block_id", ""),
                    block_type=row.get("block_type", ""),
                    source_type=row.get("source_type", "text_block"),
                    bbox=row.get("bbox", ""),
                    caption=row.get("caption", ""),
                    ocr_text=row.get("ocr_text", ""),
                    asset_path=row.get("asset_path", ""),
                    table_html=row.get("table_html", ""),
                    visual_summary=row.get("visual_summary", ""),
                    visual_status=row.get("visual_status", ""),
                    visual_unreadable_reason=row.get("visual_unreadable_reason", ""),
                    table_rows=row.get("table_rows", ""),
                    surrounding_text_refs=row.get("surrounding_text_refs", ""),
                    surrounding_text_preview=equation_context,
                )
                candidates.append(candidate)
    output_csv.parent.mkdir(parents=True, exist_ok=True)
    with output_csv.open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        writer.writerows([asdict(x) for x in candidates])
    summary = {
        "retrieval_context_policy_version": RETRIEVAL_CONTEXT_POLICY_VERSION,
        "text_retrieval_backend": text_scorer.backend,
        "question_count": len(structured_exam.get("items", [])),
        "candidate_count": len(candidates),
        "questions_without_candidates": [
            item.get("question_id")
            for item in structured_exam.get("items", [])
            if not any(c.question_id == item.get("question_id") for c in candidates)
        ],
    }
    (output_csv.parent / "retrieval_candidates.summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    return candidates


def candidates_for_question(candidates: list[EvidenceCandidate], qid: str, limit: int = 5) -> list[dict]:
    rows = [c for c in candidates if c.question_id == qid]
    if not any(c.knowledge_point for c in rows):
        rows.sort(key=lambda c: (not c.verified_page, -c.score))
        return [asdict(c) for c in rows[:limit]]
    grouped: dict[str, list[EvidenceCandidate]] = {}
    for row in rows:
        grouped.setdefault(row.knowledge_point or "考查点", []).append(row)
    out: list[dict] = []
    for group_rows in grouped.values():
        group_rows.sort(key=lambda c: (not c.verified_page, -c.score))
        out.extend(asdict(c) for c in group_rows[:limit])
    return out
