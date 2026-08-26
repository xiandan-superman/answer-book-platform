from __future__ import annotations

import re
from dataclasses import asdict, dataclass, replace
from typing import Any

from ..expression_normalization import (
    normalize_standard_state_latex as _shared_normalize_standard_state_latex,
)
from ..expression_normalization import (
    normalize_thermodynamic_latex as _shared_normalize_thermodynamic_latex,
)
from ..expression_normalization import (
    repair_json_escaped_latex as _shared_repair_json_escaped_latex,
)
from .builtin.core_expressions import REACTION_GROUP_PATTERN, TEXT_QUANTITY_PATTERN
from .catalog import DEFAULT_CAPABILITY_REGISTRY
from .registry import CapabilityRegistry, ExpressionMatch

STANDARD_STATE_RE = re.compile(
    r"(?P<base>(?<![A-Za-z])(?:\\Delta|Δ|∆)?\s*(?:G|H|S|U|A|F|K|E)(?:_\{?[A-Za-z,]+\}?)?)"
    r"\s*(?:\^\s*\{?\s*(?:o|O|\\circ|θ|\\theta)\s*\}?|[°ºᵒθ])"
)
SUBSCRIPT_TRANSLATION = str.maketrans("₀₁₂₃₄₅₆₇₈₉", "0123456789")
SUPERSCRIPT_TRANSLATION = str.maketrans("⁰¹²³⁴⁵⁶⁷⁸⁹⁺⁻", "0123456789+-")
RENDERABLE_TEXT_RULES = frozenset(
    {
        "core.text_electrode_notation",
        "core.text_reaction",
        "core.text_equation",
        "core.text_thermodynamic_quantity",
        "core.text_symbolic_token",
        "core.text_chemical_species",
    }
)
ASCII_CHEMICAL_FORMULA_RE = re.compile(
    r"(?<![A-Za-z0-9])(?:[A-Z][a-z]?\d*){2,}(?![A-Za-z0-9.])"
)
# ``core.text_equation`` intentionally starts at the first possible formula
# character so it can recognize compact notation without model cleanup.  Its
# permissive character vocabulary also includes ASCII letters and spaces, which
# can turn an English sentence containing one relation into one broad match.
# Keep that recognizer available to audits, but only promote a locally bounded
# mathematical span into a Word math object.
_EQUATION_PROSE_WORD_RE = re.compile(r"(?<!\\)\b[A-Za-z]{4,}\b")
_EQUATION_RELATION_RE = re.compile(r"(?:=|≈|≠|≤|≥|∝|→|⇌)")
_EQUATION_SPAN_BOUNDARIES = frozenset(" \t\r\n,，;；:：.!?！？。")
_EQUATION_TRAILING_PROSE_PUNCTUATION = ".。!?！？,，;；:："
_EQUATION_ASCII_IDENTIFIER_RE = re.compile(r"(?<!\\)[A-Za-z]+")
_EQUATION_WORD_FUNCTIONS = frozenset({"sin", "cos", "tan", "log", "ln", "exp", "max", "min", "det"})
_EQUATION_ROMANIZED_GREEK = frozenset({"rho", "phi", "psi", "eta", "tau", "mu", "nu", "xi", "chi"})
_EQUATION_PROSE_CONNECTIVES = frozenset(
    {
        "a", "an", "as", "at", "by", "for", "if", "in", "is", "of", "on", "or", "the", "to", "use", "via", "we", "with"
    }
)
# IUPAC element symbols. Validating tokens against this closed vocabulary
# prevents identifiers such as L12 or BCC from being formatted as chemistry.
IUPAC_ELEMENT_SYMBOLS = frozenset(
    "H He Li Be B C N O F Ne Na Mg Al Si P S Cl Ar K Ca Sc Ti V Cr Mn Fe Co Ni Cu Zn "
    "Ga Ge As Se Br Kr Rb Sr Y Zr Nb Mo Tc Ru Rh Pd Ag Cd In Sn Sb Te I Xe Cs Ba La "
    "Ce Pr Nd Pm Sm Eu Gd Tb Dy Ho Er Tm Yb Lu Hf Ta W Re Os Ir Pt Au Hg Tl Pb Bi Po "
    "At Rn Fr Ra Ac Th Pa U Np Pu Am Cm Bk Cf Es Fm Md No Lr Rf Db Sg Bh Hs Mt Ds Rg "
    "Cn Nh Fl Mc Lv Ts Og".split()
)
GREEK_LATEX = {
    "α": r"\alpha",
    "β": r"\beta",
    "γ": r"\gamma",
    "δ": r"\delta",
    "ε": r"\varepsilon",
}


@dataclass(frozen=True)
class TextExpressionRenderPlan:
    raw: str
    render_latex: str
    start: int
    end: int
    expression_kind: str
    capability_id: str
    rule_id: str
    confidence: float
    preserve_parentheses: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def repair_json_escaped_latex(value: str) -> str:
    """Recover common LaTeX commands damaged by one JSON escape pass."""

    return _shared_repair_json_escaped_latex(value)


def normalize_standard_state_latex(value: str) -> str:
    """Use theta, not the letter o or a degree sign, for standard state."""

    return _shared_normalize_standard_state_latex(value)


def normalize_thermodynamic_latex(value: str) -> str:
    """Normalize common textbook thermodynamic typography to stable LaTeX.

    Chinese physical-chemistry prose frequently writes compact forms such as
    ``ΔrGmθ`` and ``ΔrCp,m`` without explicit LaTeX scripts.  They are semantic
    symbols, not ordinary prose, so convert them deterministically instead of
    asking a language model to rewrite the surrounding answer.
    """

    return _shared_normalize_thermodynamic_latex(value)


def _reaction_token_latex(token: str) -> str:
    value = str(token or "").strip()
    coefficient = re.fullmatch(r"(\d+(?:\.\d+)?)\s*(.+)", value)
    if coefficient:
        return f"{coefficient.group(1)}{_reaction_token_latex(coefficient.group(2))}"
    grouped = re.fullmatch(r"\((.+)\)(?:_([A-Za-z0-9Α-Ωα-ω一-鿿]+))?", value)
    if grouped:
        body = "+".join(_reaction_token_latex(part) for part in grouped.group(1).split("+"))
        suffix = f"_{{{_reaction_token_latex(grouped.group(2))}}}" if grouped.group(2) else ""
        return f"({body}){suffix}"
    if value in GREEK_LATEX:
        return GREEK_LATEX[value]
    scripted = re.fullmatch(r"(.+?)_([A-Za-z0-9Α-Ωα-ω]+)", value)
    if scripted:
        return f"{_reaction_token_latex(scripted.group(1))}_{{{_reaction_token_latex(scripted.group(2))}}}"
    chemical_parts = re.findall(r"([A-Z][a-z]?)([0-9₀-₉]*)", value)
    if chemical_parts and "".join(element + digits for element, digits in chemical_parts) == value:
        if any(digits for _, digits in chemical_parts) or len(chemical_parts) > 1:
            rendered: list[str] = []
            for element, digits in chemical_parts:
                rendered.append(rf"\mathrm{{{element}}}")
                if digits:
                    rendered.append(rf"_{{{digits.translate(SUBSCRIPT_TRANSLATION)}}}")
            return "".join(rendered)
    if re.fullmatch(r"[A-Z][a-z]?", value) and len(value) > 1:
        return rf"\mathrm{{{value}}}"
    return value


def reaction_text_to_latex(value: str) -> str:
    parts = re.findall(rf"{REACTION_GROUP_PATTERN}|[+→⇌↔]", str(value or ""))
    output: list[str] = []
    for part in parts:
        if part == "+":
            output.append("+")
        elif part == "→":
            output.append(r"\to")
        elif part in {"⇌", "↔"}:
            output.append(r"\rightleftharpoons")
        else:
            output.append(_reaction_token_latex(part))
    return "".join(output)


def _text_match_to_latex(match: ExpressionMatch) -> tuple[str, bool]:
    source = match.value.strip()
    if match.rule_id == "core.text_reaction":
        return reaction_text_to_latex(source), False
    preserve_parentheses = match.rule_id == "core.text_electrode_notation"
    if preserve_parentheses and source.startswith("（") and source.endswith("）"):
        source = source[1:-1]
    latex = normalize_standard_state_latex(source.replace("（", "(").replace("）", ")"))
    latex = normalize_thermodynamic_latex(latex)
    latex = re.sub(
        r"[₀₁₂₃₄₅₆₇₈₉]+",
        lambda value: "_{" + value.group(0).translate(SUBSCRIPT_TRANSLATION) + "}",
        latex,
    )
    latex = re.sub(
        r"[⁰¹²³⁴⁵⁶⁷⁸⁹⁺⁻]+",
        lambda value: "^{" + value.group(0).translate(SUPERSCRIPT_TRANSLATION) + "}",
        latex,
    )
    # Electrode notation needs a literal vertical phase-boundary line.  Word
    # renders the mathematical ``\mid`` glyph (U+2223) slightly oblique in
    # PDF export, so use ``\vert`` (U+007C) for a visibly vertical separator.
    latex = latex.replace("|", r"\vert ")
    latex = re.sub(r"\((aq|s|l|g)\)", lambda value: rf"(\mathrm{{{value.group(1)}}})", latex)
    latex = re.sub(r"(?<![A-Za-z])Cp,?m(?![A-Za-z])", r"C_{p,m}", latex)
    latex = re.sub(r"(?<![A-Za-z])Cv,?m(?![A-Za-z])", r"C_{v,m}", latex)
    chemical_symbols = re.findall(r"[A-Z][a-z]?", source) if match.expression_kind == "chemical_notation" else []
    if match.expression_kind == "chemical_notation" and chemical_symbols and all(
        symbol in IUPAC_ELEMENT_SYMBOLS for symbol in chemical_symbols
    ):
        latex = re.sub(
            r"(?<![A-Za-z\\])((?:[A-Z][a-z]?)+)",
            lambda value: rf"\mathrm{{{value.group(1)}}}",
            latex,
        )
    return latex, preserve_parentheses


def _trim_equation_candidate(value: str) -> str:
    """Drop sentence punctuation, never formula punctuation, from one span."""

    return str(value or "").strip().rstrip(_EQUATION_TRAILING_PROSE_PUNCTUATION).rstrip()


def _contains_nonformula_ascii_identifier(value: str) -> bool:
    """Reject short English connective words that a length-only check misses."""

    for found in _EQUATION_ASCII_IDENTIFIER_RE.finditer(value):
        token = found.group(0)
        lowered = token.lower()
        if lowered in _EQUATION_PROSE_CONNECTIVES:
            return True
        if len(token) == 1 or token in IUPAC_ELEMENT_SYMBOLS:
            continue
        if len(token) <= 3 and not any(char.isspace() for char in value):
            continue
        if len(token) <= 3 and found.end() < len(value) and value[found.end()] in "^_":
            continue
        if lowered in _EQUATION_WORD_FUNCTIONS or lowered in _EQUATION_ROMANIZED_GREEK:
            continue
        if any(char.isupper() for char in token) and not (token[0].isupper() and token[1:].islower()):
            continue
        return True
    return False


def _has_english_prose_context(value: str) -> bool:
    """Detect prose around an equation without penalizing compact variables."""

    if _EQUATION_PROSE_WORD_RE.search(value):
        return True
    for found in _EQUATION_ASCII_IDENTIFIER_RE.finditer(value):
        token = found.group(0)
        if token.lower() in _EQUATION_PROSE_CONNECTIVES:
            return True
        if len(token) > 1 and token[0].isupper() and token[1:].islower() and token not in IUPAC_ELEMENT_SYMBOLS:
            return True
    return False


def _narrow_text_equation_match(match: ExpressionMatch) -> ExpressionMatch | None:
    """Keep a compact equation inside English prose, or leave prose untouched.

    The capability registry deliberately favors recall and can return one broad
    ``core.text_equation`` match such as ``Explain ... pV=nRT.``. Rendering that
    entire span as Office Math loses readable word spacing. A promotion is safe
    only when the relation can be bounded to a span without unescaped multi-word
    English prose. When that boundary cannot be proved deterministically,
    ordinary Word text is safer than a malformed math object.
    """

    source = str(match.value or "")
    if not _has_english_prose_context(source):
        return match

    candidates: list[tuple[int, int, str]] = []
    for relation in _EQUATION_RELATION_RE.finditer(source):
        starts = [0] + [
            index + 1
            for index, char in enumerate(source[: relation.start()])
            if char in _EQUATION_SPAN_BOUNDARIES
        ]
        ends = [
            index
            for index, char in enumerate(source[relation.end() :], start=relation.end())
            if char in _EQUATION_SPAN_BOUNDARIES
        ] + [len(source)]
        for start in starts:
            for end in ends:
                if start >= relation.start() or end <= relation.end():
                    continue
                candidate = _trim_equation_candidate(source[start:end])
                if (
                    not candidate
                    or _EQUATION_PROSE_WORD_RE.search(candidate)
                    or _contains_nonformula_ascii_identifier(candidate)
                ):
                    continue
                relation_in_candidate = _EQUATION_RELATION_RE.search(candidate)
                if relation_in_candidate is None:
                    continue
                left = candidate[: relation_in_candidate.start()]
                right = candidate[relation_in_candidate.end() :]
                if not re.search(r"[A-Za-zΑ-ω∂δΔ∆ΘΓΛΣΠΩ0-9]", left):
                    continue
                if not re.search(r"[A-Za-zΑ-ω∂δΔ∆ΘΓΛΣΠΩ0-9]", right):
                    continue
                candidates.append((start, start + len(candidate), candidate))

    if not candidates:
        return None
    start, end, candidate = max(candidates, key=lambda item: (len(item[2]), -item[0]))
    return replace(
        match,
        value=candidate,
        start=match.start + start,
        end=match.start + end,
    )


def build_text_expression_render_plans(
    text: str,
    *,
    context: str = "",
    registry: CapabilityRegistry = DEFAULT_CAPABILITY_REGISTRY,
) -> list[TextExpressionRenderPlan]:
    """Plan safe inline Word rendering for explicit cross-discipline notation.

    The source is never changed here. Discipline-specific recognizers may still
    participate in the audit registry, but only rules with a deterministic
    renderer are promoted into Word formula objects.
    """

    plans: list[TextExpressionRenderPlan] = []
    # Quantities such as ``10 kPa`` and ``2 MPa`` are already safe Word prose.
    # The registry intentionally scans case-insensitively, so the thermodynamic
    # token ``Kp`` can otherwise capture the ``kP`` prefix of ``kPa`` and leave
    # the final ``a`` outside the formula object. Protect every complete
    # number-unit span before considering narrower mathematical matches.
    protected_quantity_spans = [
        (match.start(), match.end())
        for match in re.finditer(TEXT_QUANTITY_PATTERN, str(text or ""), re.IGNORECASE)
    ]
    for match in registry.match_expressions(text, source_format="text", context=context):
        if match.rule_id not in RENDERABLE_TEXT_RULES:
            continue
        if match.rule_id == "core.text_equation":
            narrowed_match = _narrow_text_equation_match(match)
            if narrowed_match is None:
                continue
            match = narrowed_match
        if any(
            start <= match.start and match.end <= end
            for start, end in protected_quantity_spans
        ):
            continue
        latex, preserve_parentheses = _text_match_to_latex(match)
        plans.append(
            TextExpressionRenderPlan(
                raw=match.value,
                render_latex=latex,
                start=match.start,
                end=match.end,
                expression_kind=match.expression_kind,
                capability_id=match.capability_id,
                rule_id=match.rule_id,
                confidence=match.confidence,
                preserve_parentheses=preserve_parentheses,
            )
        )
    occupied = [(plan.start, plan.end) for plan in plans]
    for regex_match in ASCII_CHEMICAL_FORMULA_RE.finditer(text):
        if any(regex_match.start() < end and start < regex_match.end() for start, end in occupied):
            continue
        token = regex_match.group(0)
        parts = re.findall(r"([A-Z][a-z]?)(\d*)", token)
        if not parts or "".join(symbol + digits for symbol, digits in parts) != token:
            continue
        if any(symbol not in IUPAC_ELEMENT_SYMBOLS for symbol, _digits in parts):
            continue
        # Require an explicit stoichiometric digit. Ordinary capitalized words
        # and acronyms remain prose even if they can be segmented as symbols.
        if not any(digits for _symbol, digits in parts):
            continue
        plans.append(
            TextExpressionRenderPlan(
                raw=token,
                render_latex=_reaction_token_latex(token),
                start=regex_match.start(),
                end=regex_match.end(),
                expression_kind="chemical_notation",
                capability_id="core.academic_expressions",
                rule_id="core.text_ascii_chemical_formula",
                confidence=0.99,
            )
        )
    return sorted(plans, key=lambda plan: (plan.start, -(plan.end - plan.start)))
