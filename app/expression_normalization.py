from __future__ import annotations

import re
from typing import Any

STANDARD_STATE_BASE = (
    r"(?P<base>(?<![A-Za-z])(?:\\Delta|Δ|∆)?\s*(?:G|H|S|U|A|F|K|E)"
    r"(?:_\{?[A-Za-z,]+\}?)?)"
)
STANDARD_STATE_SYMBOL = r"(?:o|O|\\circ|θ|\\theta)"
STANDARD_STATE_MARKER = (
    rf"(?:\^\s*(?:\{{\s*{STANDARD_STATE_SYMBOL}\s*\}}|{STANDARD_STATE_SYMBOL})|[°ºᵒθ]|\\theta)"
)
STANDARD_STATE_WITH_CONDITION_RE = re.compile(
    STANDARD_STATE_BASE + r"\s*" + STANDARD_STATE_MARKER + r"\s*(?P<condition>[A-Za-z])(?![A-Za-z])"
)
STANDARD_STATE_RE = re.compile(STANDARD_STATE_BASE + r"\s*" + STANDARD_STATE_MARKER)

# Longest-prefix tokenization repairs any supported TeX command that was
# concatenated with following formula text.  This operates on the command
# grammar, not on a list of observed full formulas.
SUPPORTED_CONTROL_WORDS = frozenset(
    {
        "Delta", "Theta", "alpha", "approx", "bar", "begin", "beta", "boldsymbol",
        "cdot", "ce", "circ", "cos", "degree", "delta", "dfrac", "end", "exp",
        "frac", "gamma", "ge", "geq", "hat", "lambda", "le", "left", "leftharpoons",
        "leftrightarrow", "leq", "ln", "log", "longrightarrow", "lVert", "langle",
        "lvert", "mathbf", "mathit", "mathrm", "mu", "nu", "ominus", "operatorname",
        "overline", "overrightarrow", "partial", "pi", "pm", "prod", "propto", "right",
        "rightleftharpoons", "rightharpoons", "rightarrow", "rVert", "rangle", "rvert",
        "sigma", "sin", "sqrt", "sum", "tan", "text", "tfrac", "theta", "tilde", "times",
        "to", "underline", "varphi", "vec", "Vert", "vert",
    }
)
CONTROL_WORD_RE = re.compile(r"\\(?P<word>[A-Za-z]+)")
FONT_COMMAND_RE = re.compile(r"\\(?P<command>mathrm|mathbf|mathit|text)(?P<body>[A-Za-z]+)\b")
CHEMISTRY_COMMAND_RE = re.compile(r"\\ce(?P<body>[A-Za-z][A-Za-z0-9_+\-]*)\b")
SUPERSCRIPT_TRANSLATION = str.maketrans("⁰¹²³⁴⁵⁶⁷⁸⁹⁺⁻", "0123456789+-")
FORMULA_CONTINUATION_RE = re.compile(
    r"^(?P<continuation>(?:[×·]\s*10\s*)?[⁰¹²³⁴⁵⁶⁷⁸⁹⁺⁻]+)"
)
OUTER_MATH_DELIMITERS = ((r"\[", r"\]"), (r"\(", r"\)"), ("$$", "$$"), ("$", "$"))
ESCAPED_JSON_CONTROL_RE = re.compile(r"\\u00(?:0[0-9A-Fa-f]|1[0-9A-Fa-f])")
_ESCAPED_CONTROL_BEFORE_TEX_RE = re.compile(
    r"\\u00(?:0[0-9A-Fa-f]|1[0-9A-Fa-f])(?=(?:"
    + "|".join(sorted(SUPPORTED_CONTROL_WORDS, key=len, reverse=True))
    + r")(?![A-Za-z]))"
)
_DAMAGED_FOUR_WRAPPER_RE = re.compile(r"^4(?P<body>\\[A-Za-z]+\b.+)4$", re.DOTALL)


def repair_json_escaped_latex(value: str) -> str:
    """Recover common LaTeX commands damaged by one JSON escape pass."""

    repairs = {
        "\t" + "heta": r"\theta",
        "\t" + "imes": r"\times",
        "\t" + "ext": r"\text",
        "\f" + "rac": r"\frac",
        "\r" + "ight": r"\right",
        "\b" + "eta": r"\beta",
    }
    source = str(value or "")
    for damaged, repaired in repairs.items():
        source = source.replace(damaged, repaired)
    # Some OpenAI-compatible gateways preserve damaged JSON control escapes as
    # visible ``\u0000`` text. Before a known TeX word the escape replaced its
    # command slash; elsewhere it is only a transport artifact and must vanish.
    source = _ESCAPED_CONTROL_BEFORE_TEX_RE.sub(lambda _match: "\\", source)
    source = ESCAPED_JSON_CONTROL_RE.sub("", source)
    if wrapped := _DAMAGED_FOUR_WRAPPER_RE.fullmatch(source.strip()):
        source = wrapped.group("body")
    return source


def strip_outer_math_delimiters(value: str) -> str:
    """Return formula content without provider-owned wrapper delimiters.

    Structured formula fields contain LaTeX expressions, not Markdown or TeX
    embedding syntax.  Providers nevertheless use all four common wrapper
    families.  Removing complete outer pairs at this shared boundary keeps
    browser, Word, audit, and checkpoint consumers on the same contract.
    """

    source = str(value or "").strip()
    changed = True
    while changed and source:
        changed = False
        for opening, closing in OUTER_MATH_DELIMITERS:
            if source.startswith(opening) and source.endswith(closing):
                minimum_length = len(opening) + len(closing)
                if len(source) > minimum_length:
                    source = source[len(opening) : len(source) - len(closing)].strip()
                    changed = True
                    break
    return source


def normalize_control_word_boundaries(value: str) -> str:
    """Split unknown TeX control words at their longest supported prefix."""

    source = FONT_COMMAND_RE.sub(
        lambda match: rf"\{match.group('command')}{{{match.group('body')}}}",
        str(value or ""),
    )
    source = CHEMISTRY_COMMAND_RE.sub(lambda match: rf"\ce{{{match.group('body')}}}", source)
    prefixes = sorted(SUPPORTED_CONTROL_WORDS, key=len, reverse=True)

    def split(match: re.Match[str]) -> str:
        word = match.group("word")
        if word in SUPPORTED_CONTROL_WORDS:
            return match.group(0)
        prefix = next((candidate for candidate in prefixes if word.startswith(candidate)), "")
        if not prefix:
            return match.group(0)
        return rf"\{prefix} {word[len(prefix):]}"

    return CONTROL_WORD_RE.sub(split, source)


def normalize_standard_state_latex(value: str) -> str:
    """Use a theta superscript and a condition subscript for standard state."""

    source = repair_json_escaped_latex(value)
    source = STANDARD_STATE_WITH_CONDITION_RE.sub(
        lambda match: f"{match.group('base').strip()}_{{{match.group('condition')}}}^{{\\theta}}",
        source,
    )
    return STANDARD_STATE_RE.sub(lambda match: f"{match.group('base').strip()}^{{\\theta}}", source)


def normalize_thermodynamic_latex(value: str) -> str:
    """Normalize families of compact textbook thermodynamic notation."""

    source = normalize_standard_state_latex(value)
    delta = r"(?:[Δ∆]|\\Delta)"
    theta = r"(?:[θ°ºᵒ]|\\theta)"
    source = re.sub(
        rf"{delta}\s*_?r\s*C([pv]),?m({theta}?)",
        lambda match: rf"\Delta_{{\mathrm{{r}}}} C_{{{match.group(1)},\mathrm{{m}}}}"
        + (r"^{\theta}" if match.group(2) else ""),
        source,
    )
    source = re.sub(rf"\bln\s*K{theta}T\b", lambda _match: r"\ln K_{T}^{\theta}", source)
    source = re.sub(rf"\bK{theta}T\b", lambda _match: r"K_{T}^{\theta}", source)
    source = re.sub(r"([A-Za-zΑ-ω])_外", r"\1_{\\mathrm{外}}", source)
    source = re.sub(r"([A-Za-zΑ-ω])外", r"\1_{\\mathrm{外}}", source)
    source = re.sub(
        r"_(总|隔离|系统|环境)",
        lambda match: rf"_{{\mathrm{{{match.group(1)}}}}}",
        source,
    )
    source = re.sub(
        rf"{delta}\s*_?r\s*([GHUSAF])m({theta}?)",
        lambda match: rf"\Delta_{{\mathrm{{r}}}} {match.group(1)}_{{\mathrm{{m}}}}"
        + (r"^{\theta}" if match.group(2) else ""),
        source,
    )
    source = re.sub(
        rf"{delta}\s*_?r\s*([GHUSAF])({theta}?)",
        lambda match: rf"\Delta_{{\mathrm{{r}}}} {match.group(1)}"
        + (r"^{\theta}" if match.group(2) else ""),
        source,
    )
    source = re.sub(r"\bd(?=[Δ∆])", r"\\mathrm{d}", source)
    source = source.replace("∆", r"\Delta ").replace("Δ", r"\Delta ")
    source = source.replace("∂", r"\partial ").replace("δ", r"\delta ")
    source = source.replace("γ", r"\gamma ")
    source = source.replace(r"d\Delta", r"\mathrm{d}\Delta")
    source = source.replace("常数", r"\mathrm{常数}")
    return re.sub(r"\s+", " ", source).strip()


def normalize_expression_latex(value: str) -> str:
    """Apply the shared semantic and lexical normalization pipeline."""

    source = strip_outer_math_delimiters(value)
    # Providers and OCR frequently emit calculator-style exponents such as
    # ``V^(gamma-1)``.  The parenthesized payload is unambiguous when it has no
    # nested parentheses, but it is not valid TeX superscript syntax for the
    # production MathML-to-OMML converter.  Normalize the syntax at the shared
    # boundary so audit, browser, and Word rendering see the same expression.
    source = re.sub(r"\^\s*\(([^()]*)\)", r"^{\1}", source)
    # Providers often write a compact derivative as ``\partial E/\partial T``.
    # That is readable TeX, but the production MathML converter requires an
    # explicit fraction to preserve both partial symbols in Word.
    atom = r"(?:\\[A-Za-z]+\s*)?[A-Za-zΑ-ω](?:_\{[^{}]+\}|_[A-Za-z0-9]+)?(?:\^\{[^{}]+\})?"
    source = re.sub(
        rf"(?P<open>\(\s*)?\\partial\s+(?P<numerator>{atom})\s*/\s*\\partial\s+(?P<denominator>{atom})(?P<close>\s*\))?",
        lambda match: (
            rf"\left(\frac{{\partial {match.group('numerator').strip()}}}"
            rf"{{\partial {match.group('denominator').strip()}}}\right)"
            if match.group("open") and match.group("close")
            else rf"\frac{{\partial {match.group('numerator').strip()}}}{{\partial {match.group('denominator').strip()}}}"
        ),
        source,
    )
    return normalize_control_word_boundaries(normalize_thermodynamic_latex(source)).strip()


def consume_formula_continuation(value: str) -> tuple[str, str]:
    """Convert a strict leading math tail and return it with remaining prose."""

    source = str(value or "")
    match = FORMULA_CONTINUATION_RE.match(source)
    if not match:
        return "", source
    raw = match.group("continuation")
    exponent_match = re.search(r"[⁰¹²³⁴⁵⁶⁷⁸⁹⁺⁻]+$", raw)
    if exponent_match is None:
        return "", source
    exponent = exponent_match.group(0).translate(SUPERSCRIPT_TRANSLATION)
    prefix = raw[: exponent_match.start()].replace("×", r"\times ").replace("·", r"\cdot ")
    return f"{prefix}^{{{exponent}}}", source[match.end() :]


def normalize_fragment_formula_latex(fragment: dict[str, Any]) -> int:
    """Normalize every declared formula record at the shared data boundary."""

    changed = 0
    formulas = fragment.get("formulas") if isinstance(fragment.get("formulas"), list) else []
    for formula in formulas:
        if not isinstance(formula, dict):
            continue
        raw = str(formula.get("latex") or "")
        normalized = normalize_expression_latex(raw)
        if normalized != raw:
            formula["latex"] = normalized
            changed += 1
    return changed
