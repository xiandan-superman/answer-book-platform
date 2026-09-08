from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Callable

from .evidence_audit import audit_retrieval_candidates
from .evidence_selection import confirm_evidence_selection
from .knowledge_planning import generate_knowledge_plans, load_knowledge_plans
from .retrieval import EvidenceCandidate, build_candidates
from .settings import ProviderConfig
from .textbook_index_cache import install_textbook_index_cache

TEXTBOOK_EVIDENCE_SCHEMA_VERSION = "answer_book.textbook_evidence.v1"
TEXTBOOK_EVIDENCE_MARKDOWN_SUFFIX = "真题教材依据.md"


@dataclass(frozen=True)
class TextbookEvidenceRequest:
    structured_exam: dict[str, Any]
    selected_textbooks: tuple[str, ...]
    textbook_display_names: dict[str, str]
    stage_dir: Path
    provider: ProviderConfig
    model: str
    label: str
    use_model: bool = True
    visual_provider: ProviderConfig | None = None
    visual_model: str = ""


@dataclass(frozen=True)
class TextbookEvidenceResult:
    ok: bool
    question_count: int
    knowledge_point_count: int
    citation_count: int
    unresolved_count: int
    markdown_path: str
    evidence_json_path: str
    knowledge_plans_path: str
    retrieval_candidates_path: str
    evidence_selection_path: str
    index_detail: dict[str, Any]


def _write_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def _safe_label(value: str) -> str:
    cleaned = "".join(character for character in str(value or "").strip() if character not in '<>:"/\\|?*')
    return cleaned or "真题"


def _candidate_lookup(candidates: list[EvidenceCandidate]) -> dict[str, EvidenceCandidate]:
    return {candidate.evidence_id: candidate for candidate in candidates if candidate.evidence_id}


def _strings(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return list(dict.fromkeys(str(item).strip() for item in value if str(item).strip()))


def build_concise_evidence_payload(
    structured_exam: dict[str, Any],
    selection_data: dict[str, Any],
    candidates: list[EvidenceCandidate],
) -> dict[str, Any]:
    """Project internal evidence decisions to the public knowledge-point/page contract.

    Candidate excerpts remain private stage evidence. The public artifact contains
    only question identity, final knowledge points and visually verified printed pages.
    """

    by_id = _candidate_lookup(candidates)
    question_numbers = {
        str(item.get("question_id") or "").strip(): str(item.get("number") or item.get("question_id") or "").strip()
        for item in structured_exam.get("items", [])
        if isinstance(item, dict) and str(item.get("question_id") or "").strip()
    }
    questions: list[dict[str, Any]] = []
    unresolved: list[dict[str, str]] = []
    for selection in selection_data.get("selections", []):
        if not isinstance(selection, dict):
            continue
        question_id = str(selection.get("question_id") or "").strip()
        points: list[dict[str, Any]] = []
        for point in selection.get("knowledge_points", []):
            if not isinstance(point, dict):
                continue
            knowledge_point = str(point.get("knowledge_point") or "").strip()
            selected_ids = _strings(point.get("selected_evidence_ids"))
            citations: list[dict[str, str]] = []
            seen: set[tuple[str, str]] = set()
            for evidence_id in selected_ids:
                candidate = by_id.get(evidence_id)
                if candidate is None or not candidate.verified_page or not str(candidate.printed_page).strip():
                    continue
                textbook = str(candidate.citation_textbook or candidate.textbook or "教材").strip()
                printed_page = str(candidate.printed_page).strip()
                identity = (textbook, printed_page)
                if identity in seen:
                    continue
                seen.add(identity)
                citations.append(
                    {
                        "textbook": textbook,
                        "printed_page": printed_page,
                        "evidence_id": evidence_id,
                    }
                )
            if not knowledge_point or not citations:
                unresolved.append(
                    {
                        "question_id": question_id,
                        "knowledge_point": knowledge_point or "未命名知识点",
                        "reason": str(
                            point.get("no_suitable_evidence_reason")
                            or point.get("reason")
                            or "没有已验证印刷页码的教材依据"
                        ).strip(),
                    }
                )
                continue
            points.append({"knowledge_point": knowledge_point, "citations": citations})
        questions.append(
            {
                "question_id": question_id,
                "question_number": question_numbers.get(question_id, question_id),
                "knowledge_points": points,
            }
        )
    return {
        "schema_version": TEXTBOOK_EVIDENCE_SCHEMA_VERSION,
        "questions": questions,
        "unresolved": unresolved,
    }


def render_concise_evidence_markdown(label: str, payload: dict[str, Any]) -> str:
    lines = [f"# {_safe_label(label)}真题教材依据", ""]
    for question in payload.get("questions", []):
        if not isinstance(question, dict):
            continue
        point_texts: list[str] = []
        for point in question.get("knowledge_points", []):
            if not isinstance(point, dict):
                continue
            grouped: dict[str, list[str]] = {}
            for citation in point.get("citations", []):
                if not isinstance(citation, dict):
                    continue
                textbook = str(citation.get("textbook") or "教材").strip()
                page = str(citation.get("printed_page") or "").strip()
                if page and page not in grouped.setdefault(textbook, []):
                    grouped[textbook].append(page)
            pages = "、".join(
                f"{textbook}-p{'、p'.join(values)}" for textbook, values in grouped.items() if values
            )
            if pages:
                point_texts.append(f"{str(point.get('knowledge_point') or '').strip()}：{pages}")
        number = str(question.get("question_number") or question.get("question_id") or "").strip()
        if point_texts:
            lines.append(f"{number}、教材依据：" + "；".join(point_texts))
    return "\n".join(lines).rstrip() + "\n"


class TextbookEvidenceService:
    """Single reusable owner of the textbook-evidence workflow."""

    def run(
        self,
        request: TextbookEvidenceRequest,
        *,
        progress: Callable[[str, dict[str, Any]], None] | None = None,
    ) -> TextbookEvidenceResult:
        stage_dir = request.stage_dir
        stage_dir.mkdir(parents=True, exist_ok=True)

        def report(stage: str, detail: dict[str, Any]) -> None:
            if progress:
                progress(stage, detail)

        report("textbook_index", {"status": "started"})
        index_detail = install_textbook_index_cache(
            list(request.selected_textbooks),
            stage_dir,
            request.textbook_display_names,
        )
        if not index_detail.get("page_map_ok", True):
            raise RuntimeError("教材页码读取失败，不能生成未验证的教材引用。")
        report("textbook_index", {"status": "passed", **index_detail})

        plans_path = stage_dir / "knowledge_plans.json"
        report("knowledge_planning", {"status": "started"})
        plan_result = generate_knowledge_plans(
            request.structured_exam,
            request.provider,
            request.model,
            plans_path,
            use_model=request.use_model,
            progress_json=stage_dir / "knowledge_planning_progress.json",
            visual_provider=request.visual_provider,
            visual_model=request.visual_model,
        )
        if not plan_result.ok:
            raise RuntimeError(plan_result.failure_message or "考点规划失败。")
        knowledge_plans = load_knowledge_plans(plans_path)
        report("knowledge_planning", {"status": "passed", **asdict(plan_result)})

        candidates_path = stage_dir / "retrieval_candidates.csv"
        report("retrieval", {"status": "started"})
        candidates = build_candidates(
            request.structured_exam,
            stage_dir / "textbook_blocks.csv",
            stage_dir / "textbook_page_map.csv",
            candidates_path,
            knowledge_plans=knowledge_plans,
        )
        issues = audit_retrieval_candidates(
            request.structured_exam,
            candidates_path,
            stage_dir / "retrieval_audit.json",
        )
        if issues:
            raise RuntimeError("教材候选检索校验失败。")
        report("retrieval", {"status": "passed", "candidate_count": len(candidates)})

        selection_path = stage_dir / "evidence_selection.json"
        report("evidence_selection", {"status": "started"})
        selection_result, confirmed_candidates = confirm_evidence_selection(
            request.structured_exam,
            knowledge_plans,
            candidates,
            request.provider,
            request.model,
            selection_path,
            stage_dir / "textbook_blocks.csv",
            stage_dir / "textbook_page_map.csv",
            progress_json=stage_dir / "evidence_selection_progress.json",
            use_model=request.use_model,
            visual_provider=request.visual_provider,
            visual_model=request.visual_model,
        )
        if not selection_result.ok:
            raise RuntimeError("教材依据审定失败。")
        selection_data = json.loads(selection_path.read_text(encoding="utf-8"))
        payload = build_concise_evidence_payload(
            request.structured_exam,
            selection_data,
            confirmed_candidates,
        )
        evidence_json = stage_dir / "textbook_evidence.json"
        _write_json(evidence_json, payload)
        markdown_path = stage_dir / f"{_safe_label(request.label)}{TEXTBOOK_EVIDENCE_MARKDOWN_SUFFIX}"
        markdown_path.write_text(
            render_concise_evidence_markdown(request.label, payload),
            encoding="utf-8",
        )
        unresolved_count = len(payload["unresolved"])
        point_count = sum(len(question["knowledge_points"]) for question in payload["questions"])
        citation_count = sum(
            len(point["citations"])
            for question in payload["questions"]
            for point in question["knowledge_points"]
        )
        report(
            "evidence_selection",
            {
                "status": "passed" if unresolved_count == 0 else "completed_with_issues",
                "unresolved_count": unresolved_count,
                "knowledge_point_count": point_count,
                "citation_count": citation_count,
            },
        )
        return TextbookEvidenceResult(
            ok=unresolved_count == 0,
            question_count=len(payload["questions"]),
            knowledge_point_count=point_count,
            citation_count=citation_count,
            unresolved_count=unresolved_count,
            markdown_path=str(markdown_path),
            evidence_json_path=str(evidence_json),
            knowledge_plans_path=str(plans_path),
            retrieval_candidates_path=str(candidates_path),
            evidence_selection_path=str(selection_path),
            index_detail=index_detail,
        )


def publish_concise_evidence_artifacts(
    *,
    structured_exam: dict[str, Any],
    selection_data: dict[str, Any],
    candidates: list[EvidenceCandidate],
    stage_dir: Path,
    output_dir: Path,
    label: str,
) -> dict[str, Any]:
    """Publish the shared public contract from an already completed evidence run."""

    payload = build_concise_evidence_payload(structured_exam, selection_data, candidates)
    evidence_json = output_dir / "textbook_evidence.json"
    markdown_path = output_dir / f"{_safe_label(label)}{TEXTBOOK_EVIDENCE_MARKDOWN_SUFFIX}"
    _write_json(evidence_json, payload)
    markdown_path.parent.mkdir(parents=True, exist_ok=True)
    markdown_path.write_text(render_concise_evidence_markdown(label, payload), encoding="utf-8")
    # Keep the same public projection in stages for resumability and diagnostics.
    _write_json(stage_dir / "textbook_evidence.json", payload)
    unresolved_count = len(payload["unresolved"])
    return {
        "ok": unresolved_count == 0,
        "status": "completed" if unresolved_count == 0 else "completed_with_issues",
        "question_count": len(payload["questions"]),
        "knowledge_point_count": sum(len(question["knowledge_points"]) for question in payload["questions"]),
        "citation_count": sum(
            len(point["citations"])
            for question in payload["questions"]
            for point in question["knowledge_points"]
        ),
        "unresolved_count": unresolved_count,
        "markdown_path": str(markdown_path),
        "evidence_json_path": str(evidence_json),
    }
