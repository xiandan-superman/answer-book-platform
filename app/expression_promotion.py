from __future__ import annotations

import re
from typing import Any

from .capabilities.catalog import DEFAULT_CAPABILITY_REGISTRY
from .capabilities.text_expression_rendering import build_text_expression_render_plans, reaction_text_to_latex
from .docx_v4 import _answer_summary_formula_candidates
from .expression_normalization import (
    consume_formula_continuation,
    normalize_expression_latex,
    normalize_fragment_formula_latex,
)

_OPERATOR_SYMBOLS = {
    r"\Delta": "Δ",
    r"\delta": "δ",
    r"\partial": "∂",
    r"\nabla": "∇",
}
_OPERANDLESS_OPERATOR_RE = re.compile(
    r"^(?:(?:\\(?:Delta|delta|partial|nabla))|[Δδ∂∇])(?:\s|[{}_^])*?$"
)


def _is_operandless_operator(latex: str) -> bool:
    """Return whether a change/differential operator lacks an operand."""

    return bool(_OPERANDLESS_OPERATOR_RE.fullmatch(str(latex or "").strip()))


def _operator_as_prose(latex: str) -> str:
    value = str(latex or "").strip()
    for command, symbol in _OPERATOR_SYMBOLS.items():
        value = value.replace(command, symbol)
    return value


def _normalize_operator_commands_in_prose(text: str) -> str:
    """Use Unicode when an operator command directly continues as prose."""

    return re.sub(
        r"\\(Delta|delta|partial|nabla)(?=[\u3400-\u9fff])",
        lambda match: _OPERATOR_SYMBOLS[rf"\{match.group(1)}"],
        str(text or ""),
    )


def promote_split_partial_derivatives(fragment: dict[str, Any]) -> dict[str, Any]:
    """Join a complete partial derivative split across typed segments.

    Model output can legitimately reference the numerator and denominator as
    separate formula objects while leaving the surrounding derivative syntax
    in text segments.  Once the complete five-segment structure is present,
    merging it is a lossless representation normalization and belongs before
    schema validation, not in the late DOCX recovery path.
    """

    formulas = [item for item in fragment.get("formulas", []) or [] if isinstance(item, dict)]
    formula_by_id = {
        str(item.get("formula_id") or ""): str(item.get("latex") or "")
        for item in formulas
    }
    existing_ids = set(formula_by_id)
    created = 0

    def next_id() -> str:
        nonlocal created
        qid = re.sub(r"[^0-9A-Za-z_.-]+", "_", str(fragment.get("question_id") or "q")).strip("._") or "q"
        while True:
            created += 1
            candidate = f"f_{qid}_partial_promoted_{created:02d}"
            if candidate not in existing_ids:
                existing_ids.add(candidate)
                return candidate

    for block in fragment.get("blocks", []) or []:
        if not isinstance(block, dict) or str(block.get("label") or "").strip() == "教材依据":
            continue
        segments = [item for item in block.get("segments", []) or [] if isinstance(item, dict)]
        promoted: list[dict[str, Any]] = []
        index = 0
        while index < len(segments):
            window = segments[index : index + 5]
            if len(window) == 5:
                first, numerator_ref, slash, denominator_ref, tail = window
                first_text = str(first.get("text") or "") if first.get("type") == "text" else ""
                slash_text = str(slash.get("text") or "") if slash.get("type") == "text" else ""
                tail_text = str(tail.get("text") or "") if tail.get("type") == "text" else ""
                numerator = formula_by_id.get(str(numerator_ref.get("formula_id") or ""), "")
                denominator = formula_by_id.get(str(denominator_ref.get("formula_id") or ""), "")
                prefix_match = re.search(r"[（(]\s*(?:∂|\\partial)\s*[（(]\s*$", first_text)
                tail_match = re.match(r"^\s*[)）]\s*_?\s*([A-Za-zΑ-ω]+)", tail_text)
                if (
                    prefix_match
                    and numerator_ref.get("type") == "formula_ref"
                    and slash_text.strip() == "/"
                    and denominator_ref.get("type") == "formula_ref"
                    and numerator
                    and denominator
                    and tail_match
                ):
                    prefix = first_text[: prefix_match.start()]
                    suffix = tail_text[tail_match.end() :]
                    numerator_latex = numerator if "\\partial" in numerator else rf"\partial {numerator}"
                    denominator_latex = denominator if "\\partial" in denominator else rf"\partial {denominator}"
                    formula_id = next_id()
                    formulas.append(
                        {
                            "formula_id": formula_id,
                            "latex": rf"\left(\frac{{{numerator_latex}}}{{{denominator_latex}}}\right)_{{{tail_match.group(1)}}}",
                            "role": "relation",
                            "display": False,
                            "source_note": "程序在结构校验前从相邻文本与公式对象中确定性合并的偏导表达式。",
                        }
                    )
                    if prefix:
                        promoted.append({"type": "text", "text": prefix})
                    promoted.append({"type": "formula_ref", "formula_id": formula_id, "inline": True})
                    if suffix:
                        promoted.append({"type": "text", "text": suffix})
                    index += 5
                    continue
            promoted.append(segments[index])
            index += 1
        block["segments"] = promoted
    fragment["formulas"] = formulas
    return fragment


def _declared_formula_text_aliases(latex: str) -> set[str]:
    """Return conservative prose spellings for an already declared formula.

    Models sometimes declare a valid LaTeX formula and repeat the same formula
    in an adjacent prose segment using compact textbook notation.  Reusing the
    declared formula id is safer than asking the model to rewrite the answer.
    Only equation/relation-shaped aliases are returned, so ordinary prose
    letters and numbers cannot be captured accidentally.
    """

    value = re.sub(r"\s+", "", str(latex or ""))
    value = re.sub(r"\\(?:mathbf|mathrm|mathit|operatorname)\{([^{}]+)\}", r"\1", value)
    previous = None
    fraction = re.compile(r"\\(?:d?frac)\{([^{}]+)\}\{([^{}]+)\}")
    while previous != value:
        previous = value
        value = fraction.sub(r"(\1/\2)", value)
    value = value.replace(r"\left", "").replace(r"\right", "")
    value = value.replace(r"\langle", "<").replace(r"\rangle", ">")
    value = value.replace(r"\to", "→").replace(r"\rightarrow", "→")
    aliases = {value} if any(token in value for token in ("=", "<", ">", "→", "≈", "≤", "≥")) else set()
    # Prose commonly omits the parentheses introduced by \frac on one side of
    # an equality (``d=a/2``).  This remains an exact alias of the declared RHS.
    aliases.update(re.sub(r"=\(([^()]+/[^()]+)\)$", r"=\1", item) for item in list(aliases))
    # A Burgers-vector/direction expression is also commonly repeated without
    # its left-hand symbol.  The paired fraction and <uvw> direction make this
    # RHS sufficiently distinctive; a bare scalar RHS is intentionally not
    # promoted.
    for item in list(aliases):
        rhs = item.rsplit("=", 1)[-1]
        if re.fullmatch(r"\([^()]+/[^()]+\)<[^<>]+>", rhs):
            aliases.add(rhs)
        # A declared symbolic scalar fraction is unambiguously mathematical
        # when repeated verbatim (for example a/2 in a correction note).
        scalar_rhs = rhs[1:-1] if rhs.startswith("(") and rhs.endswith(")") else rhs
        if re.fullmatch(r"[A-Za-zΑ-ω]+/\d+(?:\.\d+)?", scalar_rhs):
            aliases.add(scalar_rhs)
    return {item for item in aliases if len(item) >= 4 or re.fullmatch(r"[A-Za-zΑ-ω]+/\d+", item)}


def promote_inline_reactions(fragment: dict[str, Any]) -> dict[str, Any]:
    """Promote reaction expressions in prose to typed inline formula refs.

    This is a presentation normalization only: surrounding prose and the raw
    expression meaning are preserved, while the expression gains the same OMML
    rendering and audit path as model-declared formulas.
    """

    formulas = [item for item in fragment.get("formulas", []) or [] if isinstance(item, dict)]
    by_latex = {
        str(item.get("latex") or "").strip(): str(item.get("formula_id") or "").strip()
        for item in formulas
        if str(item.get("latex") or "").strip() and str(item.get("formula_id") or "").strip()
    }
    existing_ids = {str(item.get("formula_id") or "").strip() for item in formulas}
    qid = re.sub(r"[^A-Za-z0-9_]+", "_", str(fragment.get("question_id") or "q"))
    created = 0

    def formula_id_for(latex: str) -> str:
        nonlocal created
        if latex in by_latex:
            return by_latex[latex]
        while True:
            created += 1
            formula_id = f"f_{qid}_promoted_{created:02d}"
            if formula_id not in existing_ids:
                break
        existing_ids.add(formula_id)
        by_latex[latex] = formula_id
        formulas.append(
            {
                "formula_id": formula_id,
                "latex": latex,
                "role": "reaction",
                "display": False,
                "source_note": "程序从解析正文中识别并提升的反应式。",
            }
        )
        return formula_id

    for block in fragment.get("blocks", []) or []:
        if not isinstance(block, dict) or str(block.get("label") or "").strip() == "教材依据":
            continue
        replaced: list[dict[str, Any]] = []
        for segment in block.get("segments", []) or []:
            if not isinstance(segment, dict) or segment.get("type") != "text":
                replaced.append(segment)
                continue
            text = str(segment.get("text") or "")
            cursor = 0
            matches = [
                match
                for match in DEFAULT_CAPABILITY_REGISTRY.match_expressions(
                    text,
                    source_format="text",
                )
                if match.rule_id == "core.text_reaction"
            ]
            if not matches:
                replaced.append(segment)
                continue
            for match in matches:
                if match.start > cursor:
                    replaced.append({"type": "text", "text": text[cursor : match.start]})
                latex = reaction_text_to_latex(match.value)
                replaced.append({"type": "formula_ref", "formula_id": formula_id_for(latex), "inline": True})
                cursor = match.end
            if cursor < len(text):
                replaced.append({"type": "text", "text": text[cursor:]})
        block["segments"] = replaced
    fragment["formulas"] = formulas
    return fragment


def promote_inline_mathematical_expressions(fragment: dict[str, Any]) -> dict[str, Any]:
    """Promote deterministic inline relations before generation validation."""

    normalize_fragment_formula_latex(fragment)
    # Preserve cross-segment operator context before the per-text scanner can
    # independently promote a trailing comparison such as ``T>0``.
    promote_split_partial_derivatives(fragment)
    formulas = [item for item in fragment.get("formulas", []) or [] if isinstance(item, dict)]
    operandless_promoted_ids = {
        str(item.get("formula_id") or "")
        for item in formulas
        if _is_operandless_operator(str(item.get("latex") or ""))
        and "程序在结构校验前" in str(item.get("source_note") or "")
    }
    if operandless_promoted_ids:
        formulas_by_operandless_id = {
            str(item.get("formula_id") or ""): item
            for item in formulas
            if str(item.get("formula_id") or "") in operandless_promoted_ids
        }
        for block in fragment.get("blocks", []) or []:
            if not isinstance(block, dict):
                continue
            segments = block.get("segments") if isinstance(block.get("segments"), list) else []
            block["segments"] = [
                {
                    "type": "text",
                    "text": _operator_as_prose(
                        str(formulas_by_operandless_id[str(segment.get("formula_id") or "")].get("latex") or "")
                    ),
                }
                if isinstance(segment, dict)
                and segment.get("type") == "formula_ref"
                and str(segment.get("formula_id") or "") in operandless_promoted_ids
                else segment
                for segment in segments
            ]
        formulas = [
            item
            for item in formulas
            if str(item.get("formula_id") or "") not in operandless_promoted_ids
        ]
    existing_ids = {str(item.get("formula_id") or "") for item in formulas}
    by_latex = {
        str(item.get("latex") or "").strip(): str(item.get("formula_id") or "").strip()
        for item in formulas
        if str(item.get("latex") or "").strip() and str(item.get("formula_id") or "").strip()
    }
    declared_aliases: list[tuple[str, str]] = []
    for item in formulas:
        formula_id = str(item.get("formula_id") or "").strip()
        if not formula_id:
            continue
        declared_aliases.extend(
            (alias, formula_id) for alias in _declared_formula_text_aliases(str(item.get("latex") or ""))
        )
    declared_aliases.sort(key=lambda row: -len(row[0]))
    qid = re.sub(r"[^A-Za-z0-9_]+", "_", str(fragment.get("question_id") or "q"))
    created = 0

    def add_formula(latex: str) -> str:
        nonlocal created
        normalized_latex = normalize_expression_latex(latex)
        if normalized_latex in by_latex:
            return by_latex[normalized_latex]
        while True:
            created += 1
            formula_id = f"f_{qid}_inline_math_{created:02d}"
            if formula_id not in existing_ids:
                break
        existing_ids.add(formula_id)
        by_latex[normalized_latex] = formula_id
        formulas.append(
            {
                "formula_id": formula_id,
                "latex": normalized_latex,
                "role": "relation",
                "display": False,
                "source_note": "程序在结构校验前从解析正文中提升的数学关系。",
            }
        )
        return formula_id

    for block in fragment.get("blocks", []) or []:
        if not isinstance(block, dict) or str(block.get("label") or "").strip() == "教材依据":
            continue
        replaced: list[dict[str, Any]] = []
        for segment in block.get("segments", []) or []:
            if not isinstance(segment, dict) or segment.get("type") != "text":
                replaced.append(segment)
                continue
            text = _normalize_operator_commands_in_prose(str(segment.get("text") or ""))
            segment["text"] = text
            # Collect spans first and materialize formula objects only after
            # overlap resolution. Otherwise discarded broad matches leave
            # orphan formula records in the durable fragment.
            candidates: list[tuple[int, int, str, bool]] = []
            for alias, formula_id in declared_aliases:
                candidates.extend(
                    (match.start(), match.end(), formula_id, True)
                    for match in re.finditer(re.escape(alias), text)
                )
            for start, end, latex in _answer_summary_formula_candidates(text):
                if not _is_operandless_operator(latex):
                    candidates.append((start, end, latex, False))
            for plan in build_text_expression_render_plans(text):
                start = plan.start
                end = plan.end
                latex = plan.render_latex
                if _is_operandless_operator(latex):
                    continue
                # The generic equation recognizer intentionally starts on the
                # first mathematical character.  If that character belongs to
                # a parenthesized left-hand side, retain the opening bracket so
                # ``(TS)=...`` is not promoted as the malformed ``TS)=...``.
                if start > 0 and text[start - 1] in "（(" and ")" in text[start:end].split("=", 1)[0]:
                    start -= 1
                    latex = "(" + latex
                # Exact aliases and the answer-summary parser are more
                # specific than the shared scanner.  Do not even create a new
                # formula object when a broader scanner match would later be
                # discarded as overlapping one of those candidates.
                if any(
                    start < existing_end and existing_start < end
                    for existing_start, existing_end, _, _ in candidates
                ):
                    continue
                candidates.append((start, end, latex, False))
            if not candidates:
                replaced.append(segment)
                continue
            candidates.sort(key=lambda item: (item[0], -(item[1] - item[0])))
            selected: list[tuple[int, int, str, bool]] = []
            occupied_until = -1
            for candidate in candidates:
                if candidate[0] < occupied_until:
                    continue
                selected.append(candidate)
                occupied_until = candidate[1]
            cursor = 0
            for start, end, value, is_formula_id in selected:
                if start > cursor:
                    replaced.append({"type": "text", "text": text[cursor:start]})
                formula_id = value if is_formula_id else add_formula(value)
                replaced.append({"type": "formula_ref", "formula_id": formula_id, "inline": True})
                cursor = end
            if cursor < len(text):
                replaced.append({"type": "text", "text": text[cursor:]})
        block["segments"] = replaced

    # A previous normalization pass or model repair can split a symbol into a
    # formula reference followed by a plain script suffix, e.g. ``ΔC`` +
    # ``_{V,m}``.  Rejoin that boundary by cloning/reusing a formula object;
    # never mutate the original formula because it may be referenced elsewhere.
    split_suffix_re = re.compile(r"^(_(?:\{[A-Za-zΑ-ω,]+\}|[A-Za-zΑ-ω]+|(?:总|隔离|系统|环境|外|内)))")
    formulas_by_id = {
        str(item.get("formula_id") or ""): item
        for item in formulas
        if isinstance(item, dict) and str(item.get("formula_id") or "")
    }
    consumed_tail_formula_ids: set[str] = set()
    for block in fragment.get("blocks", []) or []:
        if not isinstance(block, dict) or str(block.get("label") or "").strip() == "教材依据":
            continue
        segments = block.get("segments") if isinstance(block.get("segments"), list) else []
        # Join a dangling multiplicative/delta prefix in prose with the typed
        # symbol that immediately follows it (``-TΔ`` + ``S_m``).  Both sides
        # must be present; this does not infer a missing scientific symbol.
        dangling_delta_re = re.compile(r"(?P<prefix>[+\-−]?\s*[A-Za-zΑ-ω]?\s*[Δδ])\s*$")
        for index in range(0, len(segments) - 1):
            current = segments[index]
            following = segments[index + 1]
            if not (
                isinstance(current, dict)
                and current.get("type") == "text"
                and isinstance(following, dict)
                and following.get("type") == "formula_ref"
            ):
                continue
            text = str(current.get("text") or "")
            match = dangling_delta_re.search(text)
            formula = formulas_by_id.get(str(following.get("formula_id") or ""))
            if not match or not isinstance(formula, dict):
                continue
            following_latex = str(formula.get("latex") or "").strip()
            if not re.match(r"^(?:[A-Za-zΑ-ω]|\\(?:mathrm|mathit|mathbf)\{)", following_latex):
                continue
            prefix_latex = match.group("prefix").replace("−", "-").replace("Δ", r"\Delta ").replace("δ", r"\delta ")
            following["formula_id"] = add_formula(prefix_latex + following_latex)
            current["text"] = text[: match.start()]
        formulas_by_id = {
            str(item.get("formula_id") or ""): item
            for item in formulas
            if isinstance(item, dict) and str(item.get("formula_id") or "")
        }
        for index in range(1, len(segments) - 1):
            previous = segments[index - 1]
            current = segments[index]
            following = segments[index + 1]
            if not (
                isinstance(previous, dict)
                and previous.get("type") == "formula_ref"
                and isinstance(current, dict)
                and current.get("type") == "text"
                and str(current.get("text") or "") == "_{"
                and isinstance(following, dict)
                and following.get("type") == "formula_ref"
            ):
                continue
            base_formula = formulas_by_id.get(str(previous.get("formula_id") or ""))
            tail_formula = formulas_by_id.get(str(following.get("formula_id") or ""))
            if not isinstance(base_formula, dict) or not isinstance(tail_formula, dict):
                continue
            base_latex = re.sub(
                r"\\Delta_\{rC\}$",
                r"\\Delta_{\\mathrm{r}} C",
                str(base_formula.get("latex") or "").strip(),
            )
            tail_latex = str(tail_formula.get("latex") or "").strip()
            if not base_latex or "=" in base_latex or not re.match(r"^[A-Za-zΑ-ω,]+\}", tail_latex):
                continue
            previous["formula_id"] = add_formula(base_latex + "_{" + tail_latex)
            current["text"] = ""
            segments[index + 1] = {"type": "text", "text": ""}
            consumed_tail_formula_ids.add(str(tail_formula.get("formula_id") or ""))
        for index in range(1, len(segments)):
            previous = segments[index - 1]
            current = segments[index]
            if not isinstance(previous, dict) or previous.get("type") != "formula_ref":
                continue
            if not isinstance(current, dict) or current.get("type") != "text":
                continue
            text = str(current.get("text") or "")
            match = split_suffix_re.match(text)
            if not match:
                continue
            formula = formulas_by_id.get(str(previous.get("formula_id") or ""))
            if not isinstance(formula, dict):
                continue
            latex = str(formula.get("latex") or "").strip()
            latex = re.sub(r"\\Delta_\{rC\}$", r"\\Delta_{\\mathrm{r}} C", latex)
            if not latex or "=" in latex or (latex.endswith(("}", ")")) and not latex.endswith(r"\mathrm{r}} C")):
                continue
            suffix = match.group(1)
            suffix_latex = re.sub(
                r"^_(总|隔离|系统|环境|外|内)$",
                lambda item: rf"_{{\mathrm{{{item.group(1)}}}}}",
                suffix,
            )
            new_id = add_formula(latex + suffix_latex)
            previous["formula_id"] = new_id
            current["text"] = text[match.end() :]
        for index in range(1, len(segments)):
            previous = segments[index - 1]
            current = segments[index]
            if not (
                isinstance(previous, dict)
                and previous.get("type") == "formula_ref"
                and bool(previous.get("inline"))
                and isinstance(current, dict)
                and current.get("type") == "text"
            ):
                continue
            continuation, remaining = consume_formula_continuation(str(current.get("text") or ""))
            if not continuation:
                continue
            formula = formulas_by_id.get(str(previous.get("formula_id") or ""))
            if not isinstance(formula, dict):
                continue
            latex = str(formula.get("latex") or "").strip()
            if not latex:
                continue
            previous["formula_id"] = add_formula(latex + continuation)
            current["text"] = remaining
    referenced_ids = {
        str(segment.get("formula_id") or "")
        for block in fragment.get("blocks", []) or []
        if isinstance(block, dict)
        for segment in block.get("segments", []) or []
        if isinstance(segment, dict) and segment.get("type") == "formula_ref"
    }
    if consumed_tail_formula_ids:
        formulas = [
            formula
            for formula in formulas
            if str(formula.get("formula_id") or "") not in consumed_tail_formula_ids
            or str(formula.get("formula_id") or "") in referenced_ids
        ]
    # A prior run may already have joined the visible segments while leaving
    # behind the now-unreferenced malformed tail object (for example
    # ``V,m}=0``). Such an object cannot reach Word and would only make the
    # deterministic LaTeX preflight fail on a stale implementation detail.
    def balanced_braces(latex: str) -> bool:
        depth = 0
        escaped = False
        for character in latex:
            if escaped:
                escaped = False
                continue
            if character == "\\":
                escaped = True
            elif character == "{":
                depth += 1
            elif character == "}":
                depth -= 1
                if depth < 0:
                    return False
        return depth == 0

    formulas = [
        formula
        for formula in formulas
        if str(formula.get("formula_id") or "") in referenced_ids
        or balanced_braces(str(formula.get("latex") or ""))
    ]
    formulas = [
        formula
        for formula in formulas
        if str(formula.get("formula_id") or "") in referenced_ids
        or "程序在结构校验前从解析正文中提升" not in str(formula.get("source_note") or "")
    ]
    fragment["formulas"] = formulas
    fragment = promote_split_partial_derivatives(fragment)
    return promote_answer_summary_mathematical_expressions(fragment)


def promote_answer_summary_mathematical_expressions(fragment: dict[str, Any]) -> dict[str, Any]:
    """Give ``answer_summary`` the same typed math boundary as body blocks.

    The human-readable scalar remains for UI and old checkpoint compatibility;
    Word and other strict consumers use ``answer_summary_segments``. Rebuilding
    the segments from the scalar on every pass also prevents stale structured
    data after a model or user repair changes the summary.
    """

    summary = str(fragment.get("answer_summary") or "")
    formulas = [item for item in fragment.get("formulas", []) or [] if isinstance(item, dict)]
    existing_ids = {str(item.get("formula_id") or "") for item in formulas}
    by_latex = {
        normalize_expression_latex(str(item.get("latex") or "")): str(item.get("formula_id") or "")
        for item in formulas
        if str(item.get("latex") or "").strip() and str(item.get("formula_id") or "").strip()
    }
    qid = re.sub(r"[^A-Za-z0-9_]+", "_", str(fragment.get("question_id") or "q"))
    created = 0

    def formula_id_for(latex: str, *, display: bool) -> str:
        nonlocal created
        normalized = normalize_expression_latex(latex)
        if normalized in by_latex:
            return by_latex[normalized]
        while True:
            created += 1
            formula_id = f"f_{qid}_answer_summary_math_{created:02d}"
            if formula_id not in existing_ids:
                break
        existing_ids.add(formula_id)
        by_latex[normalized] = formula_id
        formulas.append(
            {
                "formula_id": formula_id,
                "latex": normalized,
                "role": "result",
                "display": display,
                "source_note": "程序在结构校验前从答案摘要中提升的数学表达式。",
            }
        )
        return formula_id

    candidates = _answer_summary_formula_candidates(summary)
    if not candidates:
        fragment["answer_summary_segments"] = (
            [{"type": "text", "text": summary}] if summary else []
        )
        fragment["formulas"] = formulas
        return fragment

    segments: list[dict[str, Any]] = []
    cursor = 0
    for start, end, latex in candidates:
        if start > cursor:
            segments.append({"type": "text", "text": summary[cursor:start]})
        display = summary[start:end].startswith("$$")
        segments.append(
            {
                "type": "formula_ref",
                "formula_id": formula_id_for(latex, display=display),
                "inline": not display,
                "display": display,
            }
        )
        cursor = end
    if cursor < len(summary):
        segments.append({"type": "text", "text": summary[cursor:]})
    fragment["answer_summary_segments"] = segments
    fragment["formulas"] = formulas
    return fragment
