from __future__ import annotations

import ast
import math
import re
from typing import Any

NUMBER_RE = re.compile(r"[-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][-+]?\d+)?")
SIMPLE_EXPR_RE = re.compile(r"^[0-9eE+\-*/().\s]+$")
SCIENTIFIC_NUMBER_RE = re.compile(
    r"([-+]?(?:\d+(?:\.\d*)?|\.\d+))\s*(?:\\(?:times|cdot)|[×·])\s*10\s*\^?\s*\{?\s*([-+]?\d+)\s*\}?"
)
SUPERSCRIPT_NUMBER_RE = re.compile(r"([⁺⁻]?[⁰¹²³⁴⁵⁶⁷⁸⁹]+)")
SUPERSCRIPT_NUMBER_TRANSLATION = str.maketrans("⁰¹²³⁴⁵⁶⁷⁸⁹⁺⁻", "0123456789+-")
FORMULA_CLAUSE_SPLIT_RE = re.compile(r"(?:\\(?:quad|qquad)|(?<!\\)[,;；])")
SI_RESULT_UNIT_RE = re.compile(
    r"(?<![A-Za-z])(?P<unit>MJ|kJ|J|MPa|kPa|Pa)(?![A-Za-z])",
    re.IGNORECASE,
)
SI_RESULT_UNIT_SCALES = {
    "mj": 1_000_000.0,
    "kj": 1_000.0,
    "j": 1.0,
    "mpa": 1_000_000.0,
    "kpa": 1_000.0,
    "pa": 1.0,
}


def _normalize_numeric_scripts(text: Any) -> str:
    return SUPERSCRIPT_NUMBER_RE.sub(
        lambda match: "^" + match.group(1).translate(SUPERSCRIPT_NUMBER_TRANSLATION),
        str(text or ""),
    )


def _expand_fractions(text: str) -> str:
    value = text
    pattern = re.compile(r"\\(?:d?frac)\s*\{([^{}]+)\}\s*\{([^{}]+)\}")
    previous = None
    while previous != value:
        previous = value
        value = pattern.sub(r"((\1)/(\2))", value)
    return value


def _strip_latex_units(text: str) -> str:
    r"""Remove typographic unit factors from an arithmetic substitution.

    Unit products such as ``J\cdot mol^{-1}\cdot K^{-1}`` describe
    dimensions, not extra arithmetic operands. Removing the whole unit factor
    (including a preceding unit-only product dot) lets the safe evaluator
    verify the numerical calculation without attempting scientific inference.
    """

    # Unit bodies commonly contain one level of grouped exponents, for example
    # ``\mathrm{J\cdot mol^{-1}\cdot K^{-1}}``.  A flat ``[^{}]*`` pattern
    # leaves that unit in the expression and silently disables arithmetic QA.
    unit_body = r"(?:[^{}]|\{[^{}]*\})*"
    value = re.sub(
        rf"(?:\\cdot\s*)?\\(?:text|mathrm|operatorname)\s*\{{{unit_body}\}}"
        r"\s*(?:\^\s*(?:\{\s*[-+]?\d+\s*\}|[-+]?\d+))?",
        "",
        text,
    )
    value = re.sub(r"\\(?:,|;|!|quad|qquad)\s*", "", value)
    value = re.sub(r"\\\s+", "", value)
    return value


def _numeric_expression(text: Any) -> str:
    value = str(text or "")
    value = _expand_fractions(value)
    value = _strip_latex_units(value)
    value = value.replace("\u2212", "-").replace("\u00d7", "*").replace("\u00f7", "/")
    value = re.sub(r"\\(?:times|cdot)", "*", value)
    # A percentage literal participates in arithmetic as a fraction.  Simply
    # deleting the percent sign turns ``1-71.43%`` into ``-70.43`` and creates a
    # false inconsistency.  ``x*100%`` likewise remains numerically x.
    value = re.sub(
        r"([-+]?(?:\d+(?:\.\d*)?|\.\d+))\s*(?:\\%|%)",
        r"((\1)/100)",
        value,
    )
    # Preserve grouped LaTeX exponents before removing braces. Otherwise
    # ``4^{1/3}`` becomes ``4**1/3`` and is evaluated as ``(4**1)/3``.
    value = re.sub(r"\^\s*\{([^{}]+)\}", r"^(\1)", value)
    value = re.sub(r"\\left|\\right|[{}]", "", value)
    value = value.replace("^", "**")
    return re.sub(r"\s+", "", value)


def _eval_simple_expression(text: Any) -> float | None:
    expression = _numeric_expression(text)
    if not expression or not SIMPLE_EXPR_RE.fullmatch(expression):
        return None
    try:
        node = ast.parse(expression, mode="eval")
    except (SyntaxError, ValueError):
        return None

    def evaluate(current: ast.AST) -> float:
        if isinstance(current, ast.Expression):
            return evaluate(current.body)
        if isinstance(current, ast.Constant) and isinstance(current.value, (int, float)):
            return float(current.value)
        if isinstance(current, ast.UnaryOp) and isinstance(current.op, (ast.UAdd, ast.USub)):
            value = evaluate(current.operand)
            return value if isinstance(current.op, ast.UAdd) else -value
        if isinstance(current, ast.BinOp) and isinstance(current.op, (ast.Add, ast.Sub, ast.Mult, ast.Div, ast.Pow)):
            left, right = evaluate(current.left), evaluate(current.right)
            if isinstance(current.op, ast.Add):
                return left + right
            if isinstance(current.op, ast.Sub):
                return left - right
            if isinstance(current.op, ast.Mult):
                return left * right
            if isinstance(current.op, ast.Div):
                return left / right
            if abs(right) > 12:
                raise ValueError("unsafe exponent")
            return left**right
        raise ValueError("non-numeric expression")

    try:
        result = evaluate(node)
    except (ArithmeticError, OverflowError, ValueError):
        return None
    return result if math.isfinite(result) else None


def _expression_magnitude_scale(text: Any) -> float | None:
    """Return a conservative arithmetic scale for rounded cancellation.

    For addition/subtraction this is the sum of the operand magnitudes; for
    other operations it is the magnitude of the evaluated result.  It lets a
    near-zero result inherit the precision of the large rounded terms that
    cancel, without weakening checks for ordinary nonzero calculations.
    """

    expression = _numeric_expression(text)
    if not expression or not SIMPLE_EXPR_RE.fullmatch(expression):
        return None
    try:
        node = ast.parse(expression, mode="eval")
    except (SyntaxError, ValueError):
        return None

    def evaluate(current: ast.AST) -> tuple[float, float]:
        if isinstance(current, ast.Expression):
            return evaluate(current.body)
        if isinstance(current, ast.Constant) and isinstance(current.value, (int, float)):
            value = float(current.value)
            return value, abs(value)
        if isinstance(current, ast.UnaryOp) and isinstance(current.op, (ast.UAdd, ast.USub)):
            value, scale = evaluate(current.operand)
            return (value if isinstance(current.op, ast.UAdd) else -value), scale
        if isinstance(current, ast.BinOp) and isinstance(current.op, (ast.Add, ast.Sub, ast.Mult, ast.Div, ast.Pow)):
            left, left_scale = evaluate(current.left)
            right, right_scale = evaluate(current.right)
            if isinstance(current.op, ast.Add):
                return left + right, left_scale + right_scale
            if isinstance(current.op, ast.Sub):
                return left - right, left_scale + right_scale
            if isinstance(current.op, ast.Mult):
                value = left * right
            elif isinstance(current.op, ast.Div):
                value = left / right
            else:
                if abs(right) > 12:
                    raise ValueError("unsafe exponent")
                value = left**right
            return value, abs(value)
        raise ValueError("non-numeric expression")

    try:
        _, scale = evaluate(node)
    except (ArithmeticError, OverflowError, ValueError):
        return None
    return scale if math.isfinite(scale) else None


def evaluate_simple_numeric_expression(text: Any) -> float | None:
    """Public, side-effect-free evaluator for machine-verifiable proposals."""

    return _eval_simple_expression(text)


def _last_numeric_value(text: Any) -> float | None:
    source = _normalize_numeric_scripts(text)
    scientific = list(SCIENTIFIC_NUMBER_RE.finditer(source))
    if scientific:
        match = scientific[-1]
        try:
            return float(match.group(1)) * (10 ** int(match.group(2)))
        except (OverflowError, ValueError):
            return None
    # Unit powers such as mol^{-1} and K^{-1} are typography, not the
    # declared numerical result.  Keep powers of ten intact for scientific
    # notation, but remove exponents attached to alphabetic unit symbols.
    source = re.sub(r"(?<=[A-Za-z])\s*\^\s*\{?\s*[-+]?\d+\s*\}?", "", source)
    matches = NUMBER_RE.findall(source)
    if not matches:
        return None
    try:
        return float(matches[-1])
    except ValueError:
        return None


def _numeric_values(text: Any) -> list[float]:
    source = _normalize_numeric_scripts(text)
    values: list[float] = []
    occupied: list[tuple[int, int]] = []
    for match in SCIENTIFIC_NUMBER_RE.finditer(source):
        try:
            values.append(float(match.group(1)) * (10 ** int(match.group(2))))
            occupied.append((match.start(), match.end()))
        except (OverflowError, ValueError):
            continue
    for match in NUMBER_RE.finditer(source):
        if any(match.start() < end and start < match.end() for start, end in occupied):
            continue
        try:
            values.append(float(match.group(0)))
        except ValueError:
            continue
    return values


def _result_unit_scale(text: Any) -> float:
    """Return the SI scale of a declared result unit, or one if absent.

    The checker compares arithmetic performed in base SI units with answers
    that users commonly present using prefixes (for example J versus kJ, or
    Pa versus kPa).  TeX font wrappers do not change the unit semantics.
    """

    source = str(text or "")
    source = re.sub(r"\\(?:mathrm|text|operatorname)\s*\{([^{}]*)\}", r"\1", source)
    match = SI_RESULT_UNIT_RE.search(source)
    return SI_RESULT_UNIT_SCALES.get(match.group("unit").lower(), 1.0) if match else 1.0


def _result_unit_token(text: Any) -> str:
    source = str(text or "")
    source = re.sub(r"\\(?:mathrm|text|operatorname)\s*\{([^{}]*)\}", r"\1", source)
    match = SI_RESULT_UNIT_RE.search(source)
    return match.group("unit") if match else ""


def _replace_result_unit(text: Any, unit: str) -> str:
    if not unit:
        return str(text or "")
    return SI_RESULT_UNIT_RE.sub(unit, str(text or ""), count=1)


def _formula_declared_result_values_in_base_units(text: Any) -> list[float]:
    values: list[float] = []
    for clause in FORMULA_CLAUSE_SPLIT_RE.split(str(text or "")):
        if "=" not in clause:
            continue
        rhs = clause.rsplit("=", 1)[-1]
        value = _last_numeric_value(rhs)
        if value is not None:
            values.append(value * _result_unit_scale(rhs))
    return values


def _formula_declared_result_values(text: Any) -> list[float]:
    """Return final values from independent equality chains in one formula.

    Scanning every number in ``w=(4.3-3.5)/(4.3-2.11)=0.365`` would mistake
    operands for declared results.  A formula may still contain several
    independent results separated by punctuation or LaTeX spacing commands,
    so take only the final numeric literal from each such equality chain.
    """

    clauses = FORMULA_CLAUSE_SPLIT_RE.split(str(text or ""))
    values: list[float] = []
    for clause in clauses:
        if "=" not in clause:
            continue
        # Digits in a symbol such as d_{100} or Fe_3C are indices, not the
        # declared result.  Only inspect the final equality RHS.
        value = _last_numeric_value(clause.rsplit("=", 1)[-1])
        if value is not None:
            values.append(value)
    return values


def _normalize_symbolic_result(text: Any) -> str:
    value = str(text or "").strip()
    value = _expand_fractions(value)
    value = re.sub(r"\\(?:left|right)", "", value)
    value = value.replace("{", "").replace("}", "")
    value = re.sub(r"\s+", "", value)
    # Canonicalize the wrapper produced by _expand_fractions so that a ledger
    # value ``a/2`` matches a declared formula RHS ``\frac{a}{2}``.
    previous = None
    while previous != value:
        previous = value
        value = re.sub(r"\(\(([^()]+)\)/\(([^()]+)\)\)", r"\1/\2", value)
    return value


def _formula_declared_symbolic_results(text: Any) -> list[str]:
    results: list[str] = []
    for clause in FORMULA_CLAUSE_SPLIT_RE.split(str(text or "")):
        if "=" not in clause:
            continue
        value = _normalize_symbolic_result(clause.rsplit("=", 1)[-1])
        if value and re.search(r"[A-Za-z\\\u0391-\u03c9]", value):
            results.append(value)
    return results


def _normalized_formula_lhs(text: Any) -> str:
    lhs = str(text or "").split("=", 1)[0]
    return re.sub(r"\s+", "", lhs).replace(r"\mathrm", "")


def _substitution_expression_value(text: Any) -> float | None:
    """Evaluate a two-part substitution formula's numeric right-hand side."""

    parts = [part.strip() for part in str(text or "").split("=") if part.strip()]
    if len(parts) != 2:
        return None
    return _eval_simple_expression(parts[1])


def _values_match_as_multiset(left: list[float], right: list[float]) -> bool:
    if len(left) != len(right):
        return False
    remaining = list(right)
    for value in left:
        match_index = next(
            (
                index
                for index, candidate in enumerate(remaining)
                if _close_with_percent_equivalence(value, candidate)
            ),
            None,
        )
        if match_index is None:
            return False
        remaining.pop(match_index)
    return True


def _quantity_formula_matches(
    quantity: dict[str, Any],
    formulas: list[dict[str, Any]],
) -> bool:
    try:
        value = float(quantity.get("value"))
        formula_index = int(quantity.get("formula_index"))
    except (TypeError, ValueError):
        return False
    if not 0 < formula_index <= len(formulas):
        return False
    expected = value * _result_unit_scale(quantity.get("unit"))
    return any(
        _close_with_percent_equivalence(expected, declared)
        for declared in _formula_declared_result_values_in_base_units(
            formulas[formula_index - 1].get("latex")
        )
    )


def _quantity_label(name: Any) -> str:
    label = str(name or "").strip()
    return re.sub(r"(?:质量分数|摩尔分数|体积分数|百分比|分数)$", "", label).strip()


def _labeled_number_pattern(name: Any) -> re.Pattern[str] | None:
    label = _quantity_label(name)
    if not label:
        return None
    return re.compile(
        rf"(?<![\w\u3400-\u9fff])({re.escape(label)}(?:质量分数|摩尔分数|体积分数|百分比|分数)?"
        rf"[^\d+\-.]{{0,16}})({NUMBER_RE.pattern}"
        rf"(?:\s*(?:\\(?:times|cdot)|[×·])\s*10\s*\^?\s*\{{?\s*[-+]?\d+\s*\}}?)?)"
        rf"(\s*(?:\\?%|％)?)"
    )


def _display_quantity_value(value: float, percent_suffix: str) -> float:
    if percent_suffix.strip() and abs(value) <= 1.0:
        return value * 100.0
    return value


def _sync_labeled_quantity_value(text: Any, quantity: dict[str, Any]) -> str:
    value = str(text or "")
    pattern = _labeled_number_pattern(quantity.get("name"))
    if pattern is None:
        return value
    try:
        ledger_value = float(quantity.get("value"))
    except (TypeError, ValueError):
        return value

    def replace(match: re.Match[str]) -> str:
        rendered = _display_quantity_value(ledger_value, match.group(3))
        return f"{match.group(1)}{rendered:g}{match.group(3)}"

    return pattern.sub(replace, value)


def _labeled_quantity_values(text: Any, name: Any) -> list[tuple[float, bool, float]]:
    pattern = _labeled_number_pattern(name)
    if pattern is None:
        return []
    source = str(text or "")
    values: list[tuple[float, bool, float]] = []
    for match in pattern.finditer(source):
        parsed = _numeric_values(match.group(2))
        if parsed:
            unit_tail = source[match.end() : match.end() + 32]
            values.append((parsed[0], bool(match.group(3).strip()), _result_unit_scale(unit_tail)))
    return values


def _quantity_answer_aliases(name: Any) -> list[str]:
    """Return conservative labels that may appear in a concise answer.

    Calculation contracts often use a descriptive name such as
    ``整个过程的熵变ΔS`` while the user-facing answer naturally writes
    ``ΔS=...``.  Keep the full label and only extract standalone one-letter or
    delta-prefixed symbols; ordinary English words are intentionally ignored.
    """

    label = str(name or "").strip()
    aliases = [label] if label else []
    aliases.extend(
        match.group(0).replace("∆", "Δ")
        for match in re.finditer(
            r"(?<![A-Za-z])(?:[Δ∆][A-Za-z]|[A-Za-z](?:_[A-Za-z0-9]+)?)(?![A-Za-z])",
            label,
        )
    )
    return list(dict.fromkeys(alias for alias in aliases if alias))


def _close(actual: float, expected: float) -> bool:
    # Model answers commonly round to 3 significant figures.  The tolerance is
    # tight enough to reject 1/3 reported as 2/3 while accepting normal rounding.
    return math.isclose(actual, expected, rel_tol=0.012, abs_tol=0.0015)


def _close_with_percent_equivalence(actual: float, expected: float) -> bool:
    if _close(actual, expected):
        return True
    if abs(actual) <= 1.0 < abs(expected):
        return _close(actual * 100.0, expected)
    if abs(expected) <= 1.0 < abs(actual):
        return _close(actual, expected * 100.0)
    return False


def _close_substitution_result(actual: float, expected: float, scale: float | None) -> bool:
    if _close_with_percent_equivalence(actual, expected):
        return True
    # Three-significant-figure operands can leave a small residual when large
    # terms cancel.  Cap this special allowance at 0.2% of their combined
    # magnitude; material errors remain far outside it.
    return scale is not None and abs(actual - expected) <= max(0.0015, 0.002 * scale)


def formula_numeric_consistency_issues(formulas: list[dict[str, Any]]) -> list[str]:
    issues: list[str] = []
    for index, formula in enumerate(formulas, start=1):
        if not isinstance(formula, dict):
            continue
        if "程序在结构校验前从解析正文中提升" in str(formula.get("source_note") or ""):
            # These objects preserve Word typography for a prose substring.
            # The substring can be a suffix of a longer equality chain, so it
            # is not an authoritative calculation ledger entry.
            continue
        latex = str(formula.get("latex") or "")
        if str(formula.get("role") or "").strip().lower() == "relation":
            units = re.findall(r"\\(?:mathrm|text)\{([^{}]+)\}", latex)
            if len(set(units)) >= 2:
                # A pure unit conversion intentionally has different numeric
                # values on either side (for example 1 cm = 10^7 nm).  The
                # lightweight arithmetic checker is unit-agnostic, so this is
                # not an authoritative equality for its purposes.
                continue
        parts = [part.strip() for part in latex.split("=") if part.strip()]
        if len(parts) < 2:
            continue
        expected = _last_numeric_value(parts[-1])
        actual = _eval_simple_expression(parts[-2])
        if expected is None or actual is None:
            # The existing numeric checker is faster and understands the
            # platform's unit/percentage conventions.  Delegate only symbolic
            # equalities it cannot decide to the isolated OSS math stack.
            left_symbols = set(re.findall(r"(?<!\\)\b[A-Za-z]\b", parts[0]))
            right_symbols = set(re.findall(r"(?<!\\)\b[A-Za-z]\b", parts[1]))
            if len(parts) == 2 and left_symbols and left_symbols == right_symbols:
                from .adapters.math_verifier import verify_math_equivalence

                verification = verify_math_equivalence(f"${parts[0]}$", f"${parts[1]}$")
                if verification.available and verification.equivalent is False:
                    issues.append(f"formula_{index}_symbolic_equality_mismatch")
            continue
        if not _close_with_percent_equivalence(actual, expected):
            issues.append(
                f"formula_{index}_numeric_equality_mismatch:{actual:.8g}!={expected:.8g}"
            )
    substitution_values: dict[str, list[tuple[int, float, float | None]]] = {}
    result_values: dict[str, list[tuple[int, float]]] = {}
    for index, formula in enumerate(formulas, start=1):
        if not isinstance(formula, dict):
            continue
        lhs = _normalized_formula_lhs(formula.get("latex"))
        if not lhs:
            continue
        role = str(formula.get("role") or "").strip().lower()
        if role == "substitution":
            value = _substitution_expression_value(formula.get("latex"))
            if value is not None:
                parts = [part.strip() for part in str(formula.get("latex") or "").split("=") if part.strip()]
                scale = _expression_magnitude_scale(parts[-1]) if len(parts) >= 2 else None
                substitution_values.setdefault(lhs, []).append((index, value, scale))
        elif role == "result":
            values = _formula_declared_result_values_in_base_units(formula.get("latex"))
            if len(values) == 1:
                result_values.setdefault(lhs, []).append((index, values[0]))
    for lhs in sorted(substitution_values.keys() & result_values.keys()):
        for substitution_index, actual, scale in substitution_values[lhs]:
            if any(
                _close_substitution_result(actual, expected, scale)
                for _, expected in result_values[lhs]
            ):
                continue
            declared = [value for _, value in result_values[lhs]]
            issues.append(
                f"formula_substitution_result_mismatch:{substitution_index}:"
                f"{actual:.8g}!={declared}"
            )
    return list(dict.fromkeys(issues))


def calculation_draft_consistency_issues(draft: dict[str, Any]) -> list[str]:
    formulas = [item for item in draft.get("formulas", []) or [] if isinstance(item, dict)]
    issues = formula_numeric_consistency_issues(formulas)
    units = draft.get("answer_units") if isinstance(draft.get("answer_units"), list) else []
    step_groups = [unit.get("steps", []) for unit in units if isinstance(unit, dict)] or [draft.get("steps", [])]
    for steps in step_groups:
        for step in steps or []:
            if not isinstance(step, dict):
                continue
            indices = step.get("result_formula_indices") or []
            if not isinstance(indices, list) or not indices:
                continue
            formula_values: list[float] = []
            for raw_index in indices:
                try:
                    formula = formulas[int(raw_index) - 1]
                except (IndexError, TypeError, ValueError):
                    continue
                formula_values.extend(
                    _formula_declared_result_values_in_base_units(formula.get("latex"))
                )
            result_text = step.get("result_text") or ""
            text_values = [
                value * _result_unit_scale(result_text)
                for value in _numeric_values(result_text)
            ]
            result_sources = [formulas[int(raw_index) - 1] for raw_index in indices if str(raw_index).isdigit() and 0 < int(raw_index) <= len(formulas)]
            if not any(str(formula.get("role") or "").strip().lower() == "result" for formula in result_sources):
                continue
            unmatched = list(text_values)
            mismatch = False
            for formula_value in formula_values:
                match_index = next(
                    (index for index, value in enumerate(unmatched) if _close_with_percent_equivalence(formula_value, value)),
                    None,
                )
                if match_index is None:
                    mismatch = True
                    break
                unmatched.pop(match_index)
            if mismatch:
                number = str(step.get("subquestion_number") or "").strip()
                issues.append(
                    f"step_result_mismatch{':' + number if number else ''}:"
                    f"formula_values={formula_values}:text_values={text_values}"
                )
    # Cross-check simple named final assignments in each answer unit against
    # its worked result formulas. This is deliberately limited to unambiguous
    # symbols such as E, W, T, p, x: it catches E=0.505 in the answer versus
    # E=0.758 in the steps without guessing scientific truth or requiring that
    # every intermediate calculation be repeated in the final summary.
    for unit in units:
        if not isinstance(unit, dict):
            continue
        answer_text = str(unit.get("answer") or "")
        for step in unit.get("steps", []) or []:
            if not isinstance(step, dict):
                continue
            indices = step.get("result_formula_indices") or []
            if not isinstance(indices, list):
                indices = [indices]
            for raw_index in indices:
                try:
                    formula = formulas[int(raw_index) - 1]
                except (IndexError, TypeError, ValueError):
                    continue
                latex = str(formula.get("latex") or "")
                lhs = _normalized_formula_lhs(latex)
                if not re.fullmatch(r"[A-Za-z][A-Za-z0-9]{0,7}", lhs):
                    continue
                answer_match = re.search(
                    rf"(?<![A-Za-z0-9]){re.escape(lhs)}\s*=\s*"
                    rf"({SCIENTIFIC_NUMBER_RE.pattern}|{NUMBER_RE.pattern})",
                    _normalize_numeric_scripts(answer_text),
                )
                if not answer_match:
                    continue
                answer_values = _numeric_values(answer_match.group(0))
                formula_values = _formula_declared_result_values_in_base_units(latex)
                answer_scale = _result_unit_scale(
                    answer_text[answer_match.end() : answer_match.end() + 32]
                )
                if answer_values and formula_values and not any(
                    _close_with_percent_equivalence(answer_values[-1] * answer_scale, value)
                    for value in formula_values
                ):
                    number = _normalized_unit_number(unit.get("number"))
                    issues.append(
                        f"answer_step_result_mismatch{':' + number if number else ''}:"
                        f"{lhs}={answer_values[-1]:.8g}!={formula_values}"
                    )
    # The calculation contract is the normalized result ledger.  Compare its
    # named quantities with each concise answer-unit summary as well as with
    # worked formulas.  This catches stale summaries left behind when a later
    # repair updates the derivation or ledger but not ``answer_units[].answer``.
    contract = draft.get("calculation_contract") if isinstance(draft.get("calculation_contract"), dict) else {}
    quantities = [
        item
        for item in contract.get("result_quantities", []) or []
        if isinstance(item, dict)
    ]
    for unit in units:
        if not isinstance(unit, dict):
            continue
        number = _normalized_unit_number(unit.get("number"))
        answer_text = str(unit.get("answer") or "")
        if not number or not answer_text:
            continue
        for quantity in quantities:
            if _normalized_unit_number(quantity.get("answer_unit_number")) != number:
                continue
            try:
                expected = float(quantity.get("value"))
            except (TypeError, ValueError):
                continue
            mismatches: list[tuple[str, float]] = []
            for alias in _quantity_answer_aliases(quantity.get("name")):
                for actual, has_percent, actual_scale in _labeled_quantity_values(answer_text, alias):
                    normalized_actual = actual / 100.0 if has_percent and abs(expected) <= 1.0 else actual
                    expected_scale = _result_unit_scale(quantity.get("unit"))
                    if not _close_with_percent_equivalence(
                        normalized_actual * actual_scale,
                        expected * expected_scale,
                    ):
                        mismatches.append((alias, actual))
            if mismatches:
                alias, actual = mismatches[0]
                issues.append(
                    f"answer_contract_result_mismatch:{number}:"
                    f"{alias}={actual:.8g}!={expected:.8g}"
                )
    # Some model responses legitimately omit answer_units for a single
    # calculation question.  The top-level concise answer is still public
    # output and must not contradict the normalized result ledger.
    top_answer = str(draft.get("answer") or "")
    if top_answer:
        for quantity in quantities:
            try:
                expected = float(quantity.get("value"))
            except (TypeError, ValueError):
                continue
            for alias in _quantity_answer_aliases(quantity.get("name")):
                matches = _labeled_quantity_values(top_answer, alias)
                mismatch = next(
                    (
                        actual
                        for actual, has_percent, actual_scale in matches
                        if not _close_with_percent_equivalence(
                            (actual / 100.0 if has_percent and abs(expected) <= 1.0 else actual)
                            * actual_scale,
                            expected * _result_unit_scale(quantity.get("unit")),
                        )
                    ),
                    None,
                )
                if mismatch is not None:
                    issues.append(
                        f"answer_contract_result_mismatch:top:"
                        f"{alias}={mismatch:.8g}!={expected:.8g}"
                    )
                    break
    return list(dict.fromkeys(issues))


PARTITION_REQUEST_RE = re.compile(
    r"(?:组成|占比|质量分数|摩尔分数|体积分数|百分比|概率分布|"
    r"composition|fraction|percentage|probability\s+distribution)",
    re.IGNORECASE,
)
NUMERICAL_REQUEST_RE = re.compile(
    r"(?:计算|求解|求出|求得|数值|多少|比值|比例|百分比|分数|组成|占比|"
    r"calculate|compute|determine\s+(?:the\s+)?(?:value|ratio|fraction|percentage|composition))",
    re.IGNORECASE,
)
MULTISTAGE_TRANSITION_RE = re.compile(
    r"(?:析出|沉淀|剩余|余量|剩余量|分解为|反应生成|转移|损失|衰变|"
    r"precipitat|remain(?:ing|der)?|split|decompos|transfer|loss|decay)",
    re.IGNORECASE,
)


def _normalized_unit_number(value: Any) -> str:
    return str(value or "").strip().strip("第小问题（）() ：:、")


def calculation_contract_issues(
    draft: dict[str, Any],
    expected_calculation_units: list[dict[str, Any]] | None = None,
) -> list[str]:
    """Validate the model's lightweight numerical-result ledger.

    The ledger is discipline-neutral.  It lets the local gate verify that
    quantities which claim to form one exhaustive partition share a basis and
    add to the declared total.  This catches plausible-looking but internally
    impossible answers without another model call.
    """

    expected_units = [
        {
            "number": _normalized_unit_number(item.get("number")),
            "stem": str(item.get("stem") or ""),
        }
        for item in (expected_calculation_units or [])
        if isinstance(item, dict) and _normalized_unit_number(item.get("number"))
    ]
    numerical_units = [item for item in expected_units if NUMERICAL_REQUEST_RE.search(item["stem"])]
    contract = draft.get("calculation_contract")
    if not isinstance(contract, dict):
        return ["calculation_contract_missing"] if numerical_units else []

    issues: list[str] = []
    requested = contract.get("requested_outputs") if isinstance(contract.get("requested_outputs"), list) else []
    requested_units = {
        _normalized_unit_number(item.get("answer_unit_number"))
        for item in requested
        if isinstance(item, dict) and _normalized_unit_number(item.get("answer_unit_number"))
    }
    for item in numerical_units:
        if item["number"] not in requested_units:
            issues.append(f"calculation_contract_missing_requested_output:{item['number']}")

    quantities = contract.get("result_quantities") if isinstance(contract.get("result_quantities"), list) else []
    intermediate_quantities = (
        contract.get("intermediate_quantities")
        if isinstance(contract.get("intermediate_quantities"), list)
        else []
    )
    by_id: dict[str, dict[str, Any]] = {}
    for item in quantities:
        if not isinstance(item, dict):
            continue
        quantity_id = str(item.get("quantity_id") or "").strip()
        if not quantity_id or quantity_id in by_id:
            issues.append("calculation_contract_invalid_quantity_id")
            continue
        try:
            formula_index = int(item.get("formula_index"))
        except (TypeError, ValueError):
            issues.append(f"calculation_contract_missing_formula_index:{quantity_id}")
            continue
        formulas = [formula for formula in draft.get("formulas", []) or [] if isinstance(formula, dict)]
        if formula_index < 1 or formula_index > len(formulas):
            issues.append(f"calculation_contract_formula_index_out_of_range:{quantity_id}")
            continue
        try:
            value = float(item.get("value"))
        except (TypeError, ValueError):
            symbolic_value = _normalize_symbolic_result(item.get("value"))
            declared_symbols = _formula_declared_symbolic_results(formulas[formula_index - 1].get("latex"))
            if symbolic_value and symbolic_value in declared_symbols:
                # Symbolic quantities such as d_100=a are valid requested
                # results, but cannot participate in numeric partitions or
                # transition arithmetic.  Their formula binding is sufficient.
                continue
            issues.append(f"calculation_contract_invalid_quantity_value:{quantity_id}")
            continue
        if not math.isfinite(value):
            issues.append(f"calculation_contract_invalid_quantity_value:{quantity_id}")
            continue
        normalized = dict(item)
        normalized["value"] = value
        formula_values = _formula_declared_result_values_in_base_units(
            formulas[formula_index - 1].get("latex")
        )
        ledger_value = value * _result_unit_scale(item.get("unit"))
        if not any(
            _close_with_percent_equivalence(ledger_value, formula_value)
            for formula_value in formula_values
        ):
            issues.append(
                f"calculation_contract_result_mismatch:{quantity_id}:"
                f"ledger={value:.8g}:formula_values={formula_values or ['missing']}"
            )
        by_id[quantity_id] = normalized

        matching_units = [
            unit
            for unit in draft.get("answer_units", []) or []
            if isinstance(unit, dict)
            and _normalized_unit_number(unit.get("number"))
            == _normalized_unit_number(item.get("answer_unit_number"))
        ]
        for unit in matching_units:
            stated_values = _labeled_quantity_values(unit.get("answer"), item.get("name"))
            expected_scale = _result_unit_scale(item.get("unit"))
            if any(
                not _close_with_percent_equivalence(
                    value * stated_scale,
                    _display_quantity_value(normalized["value"], "%" if is_percent else "")
                    * expected_scale,
                )
                for value, is_percent, stated_scale in stated_values
            ):
                issues.append(f"calculation_contract_answer_mismatch:{quantity_id}")


    # Intermediate state quantities are deliberately separate from requested
    # outputs: they describe the parent state needed to validate a later split,
    # but are not silently promoted into the user's requested answer scope.
    for item in intermediate_quantities:
        if not isinstance(item, dict):
            continue
        quantity_id = str(item.get("quantity_id") or "").strip()
        if not quantity_id or quantity_id in by_id:
            issues.append("calculation_contract_invalid_quantity_id")
            continue
        try:
            value = float(item.get("value"))
        except (TypeError, ValueError):
            issues.append(f"calculation_contract_invalid_intermediate_value:{quantity_id}")
            continue
        if not math.isfinite(value):
            issues.append(f"calculation_contract_invalid_intermediate_value:{quantity_id}")
            continue
        normalized = dict(item)
        normalized["value"] = value
        by_id[quantity_id] = normalized

    partitions = contract.get("partitions") if isinstance(contract.get("partitions"), list) else []
    partition_units = {
        _normalized_unit_number(item.get("answer_unit_number"))
        for item in partitions
        if isinstance(item, dict) and _normalized_unit_number(item.get("answer_unit_number"))
    }
    for item in numerical_units:
        if PARTITION_REQUEST_RE.search(item["stem"]) and item["number"] not in partition_units:
            issues.append(f"calculation_contract_missing_partition:{item['number']}")

    for index, partition in enumerate(partitions, start=1):
        if not isinstance(partition, dict):
            issues.append(f"calculation_contract_invalid_partition:{index}")
            continue
        component_ids = partition.get("component_quantity_ids")
        if not isinstance(component_ids, list) or len(component_ids) < 2:
            issues.append(f"calculation_contract_partition_too_small:{index}")
            continue
        components = [by_id.get(str(quantity_id or "").strip()) for quantity_id in component_ids]
        if any(component is None for component in components):
            issues.append(f"calculation_contract_unknown_partition_component:{index}")
            continue
        try:
            expected_total = float(partition.get("expected_total", 1.0))
        except (TypeError, ValueError):
            issues.append(f"calculation_contract_invalid_expected_total:{index}")
            continue
        bases = {
            str(component.get("basis") or "").strip()
            for component in components
            if isinstance(component, dict) and str(component.get("basis") or "").strip()
        }
        # Component quantities carry the actual calculation basis.  If they
        # all agree, a differently worded partition label is metadata drift;
        # genuine whole/subset mixing still has multiple component bases.
        if len(bases) > 1:
            issues.append(f"calculation_contract_mixed_partition_basis:{index}")
        total = sum(float(component["value"]) for component in components if component is not None)
        if not _close_with_percent_equivalence(total, expected_total):
            issues.append(
                f"calculation_contract_partition_sum_mismatch:{index}:{total:.8g}!={expected_total:.8g}"
            )

    transitions = contract.get("transitions") if isinstance(contract.get("transitions"), list) else []
    contract_context = " ".join(
        str(value or "")
        for value in (
            draft.get("answer"),
            draft.get("analysis"),
            draft.get("answer_units"),
            draft.get("steps"),
            draft.get("formulas"),
        )
    )
    has_final_partition = any(
        isinstance(item, dict) and len(item.get("component_quantity_ids", []) or []) >= 2
        for item in partitions
    )
    if has_final_partition and MULTISTAGE_TRANSITION_RE.search(contract_context) and not transitions:
        issues.append("calculation_contract_missing_transition_lineage")
    transition_ids: set[str] = set()
    for index, transition in enumerate(transitions, start=1):
        if not isinstance(transition, dict):
            issues.append(f"calculation_contract_invalid_transition:{index}")
            continue
        transition_id = str(transition.get("transition_id") or "").strip()
        if not transition_id or transition_id in transition_ids:
            issues.append(f"calculation_contract_invalid_transition_id:{index}")
        else:
            transition_ids.add(transition_id)
        parent_id = str(transition.get("parent_quantity_id") or "").strip()
        product_ids = [
            str(value or "").strip()
            for value in transition.get("product_quantity_ids", []) or []
            if str(value or "").strip()
        ]
        parent = by_id.get(parent_id)
        products = [by_id.get(quantity_id) for quantity_id in product_ids]
        if parent is None:
            issues.append(f"calculation_contract_unknown_transition_parent:{index}")
            continue
        if len(product_ids) < 1 or len(set(product_ids)) != len(product_ids) or any(item is None for item in products):
            issues.append(f"calculation_contract_invalid_transition_products:{index}")
            continue
        bases = {
            str(item.get("basis") or "").strip()
            for item in [parent, *products]
            if isinstance(item, dict) and str(item.get("basis") or "").strip()
        }
        if len(bases) > 1:
            issues.append(f"calculation_contract_mixed_transition_basis:{index}")
        product_total = sum(float(item["value"]) for item in products if item is not None)
        parent_value = float(parent["value"])
        if not _close_with_percent_equivalence(product_total, parent_value):
            issues.append(
                f"calculation_contract_transition_conservation_mismatch:{index}:"
                f"products={product_total:.8g}:parent={parent_value:.8g}"
            )

        derived_id = str(transition.get("derived_quantity_id") or "").strip()
        local_fraction = transition.get("local_fraction")
        if derived_id or local_fraction is not None:
            if derived_id not in product_ids:
                issues.append(f"calculation_contract_invalid_transition_derived_quantity:{index}")
                continue
            try:
                fraction = float(local_fraction)
            except (TypeError, ValueError):
                issues.append(f"calculation_contract_invalid_transition_local_fraction:{index}")
                continue
            if not math.isfinite(fraction) or fraction < 0:
                issues.append(f"calculation_contract_invalid_transition_local_fraction:{index}")
                continue
            # Accept either 0.133 or 13.3 for a local 13.3% fraction.  Parent
            # and child retain their own shared representation (0..1 or 0..100).
            normalized_fraction = fraction / 100.0 if fraction > 1.0 else fraction
            derived = by_id[derived_id]
            expected_derived = parent_value * normalized_fraction
            if not _close(float(derived["value"]), expected_derived):
                issues.append(
                    f"calculation_contract_transition_derivation_mismatch:{index}:"
                    f"derived={float(derived['value']):.8g}:expected={expected_derived:.8g}"
                )
    return list(dict.fromkeys(issues))


def reconcile_calculation_reference_structure(draft: dict[str, Any]) -> dict[str, Any]:
    """Project a self-consistent numeric contract into references and prose.

    Models occasionally put two declared final quantities in one sentence but
    create a result formula for only one, or leave a step pointing at a result
    formula from the preceding step.  Mirror already-declared ledger values into
    result-formula objects and reconnect by unique numeric equality.  The later
    correctness reviewer still judges whether those declared values are true.
    This function never derives a new disciplinary result: prose is synchronized
    only when the ledger value already matches its declared result formula.
    """

    if not isinstance(draft, dict):
        return draft
    contract = draft.get("calculation_contract")
    if not isinstance(contract, dict):
        return draft
    formulas = draft.get("formulas") if isinstance(draft.get("formulas"), list) else []
    draft["formulas"] = formulas

    quantities_by_id = {
        str(item.get("quantity_id") or "").strip(): item
        for item in [
            *(contract.get("result_quantities", []) or []),
            *(contract.get("intermediate_quantities", []) or []),
        ]
        if isinstance(item, dict) and str(item.get("quantity_id") or "").strip()
    }
    intermediate_by_id = {
        str(item.get("quantity_id") or "").strip(): item
        for item in contract.get("intermediate_quantities", []) or []
        if isinstance(item, dict) and str(item.get("quantity_id") or "").strip()
    }
    all_quantities_by_id = {**quantities_by_id, **intermediate_by_id}
    for partition in contract.get("partitions", []) or []:
        if not isinstance(partition, dict):
            continue
        components = [
            quantities_by_id.get(str(quantity_id or "").strip())
            for quantity_id in partition.get("component_quantity_ids", []) or []
        ]
        bases = {
            str(component.get("basis") or "").strip()
            for component in components
            if isinstance(component, dict) and str(component.get("basis") or "").strip()
        }
        if len(bases) == 1:
            partition["basis"] = next(iter(bases))

    # One conserved global total may legitimately parent several alternative
    # exhaustive views (for example, a phase partition and an organisation
    # partition).  Stage labels describe observation context, not different
    # denominators.  If every view independently conserves the same parent,
    # normalize the complete connected component to that parent's named whole.
    transitions = [
        item for item in contract.get("transitions", []) or [] if isinstance(item, dict)
    ]
    transitions_by_parent: dict[str, list[dict[str, Any]]] = {}
    for transition in transitions:
        parent_id = str(transition.get("parent_quantity_id") or "").strip()
        if parent_id:
            transitions_by_parent.setdefault(parent_id, []).append(transition)
    for parent_id, parent_transitions in transitions_by_parent.items():
        if len(parent_transitions) < 2:
            continue
        parent = intermediate_by_id.get(parent_id)
        if parent is None:
            continue
        product_groups: list[list[dict[str, Any]]] = []
        try:
            parent_value = float(parent.get("value"))
        except (TypeError, ValueError):
            continue
        for transition in parent_transitions:
            products = [
                all_quantities_by_id.get(str(quantity_id or "").strip())
                for quantity_id in transition.get("product_quantity_ids", []) or []
            ]
            if not products or any(item is None for item in products):
                product_groups = []
                break
            typed_products = [item for item in products if isinstance(item, dict)]
            try:
                conserved = _close_with_percent_equivalence(
                    parent_value,
                    sum(float(item.get("value")) for item in typed_products),
                )
            except (TypeError, ValueError):
                conserved = False
            if not conserved:
                product_groups = []
                break
            product_groups.append(typed_products)
        if len(product_groups) != len(parent_transitions):
            continue
        common_basis = str(parent.get("name") or parent.get("basis") or "总体").strip() or "总体"
        parent["basis"] = common_basis
        connected_ids: set[str] = set()
        for transition, products in zip(parent_transitions, product_groups):
            transition["basis"] = common_basis
            for product in products:
                product["basis"] = common_basis
                connected_ids.add(str(product.get("quantity_id") or "").strip())
        for partition in contract.get("partitions", []) or []:
            if not isinstance(partition, dict):
                continue
            component_ids = {
                str(quantity_id or "").strip()
                for quantity_id in partition.get("component_quantity_ids", []) or []
            }
            if component_ids and component_ids.issubset(connected_ids):
                partition["basis"] = common_basis

    # ``basis`` means denominator/whole, not observation time.  Models often
    # label a conserved parent "before reaction" and its products "final
    # state" even though all are fractions of the same global whole.  When the
    # products already agree and their values exactly conserve the parent,
    # normalize only the intermediate parent's metadata to that global basis.
    for transition in transitions:
        if not isinstance(transition, dict):
            continue
        parent = intermediate_by_id.get(str(transition.get("parent_quantity_id") or "").strip())
        products = [
            all_quantities_by_id.get(str(quantity_id or "").strip())
            for quantity_id in transition.get("product_quantity_ids", []) or []
        ]
        if parent is None or not products or any(item is None for item in products):
            continue
        product_bases = {
            str(item.get("basis") or "").strip()
            for item in products
            if isinstance(item, dict) and str(item.get("basis") or "").strip()
        }
        try:
            conserved = _close_with_percent_equivalence(
                float(parent.get("value")),
                sum(float(item.get("value")) for item in products if item is not None),
            )
        except (TypeError, ValueError):
            conserved = False
        if conserved and len(product_bases) == 1:
            common_basis = next(iter(product_bases))
            parent["basis"] = common_basis
            transition["basis"] = common_basis

    def result_matches(value: float, unit: Any = "") -> list[int]:
        expected = value * _result_unit_scale(unit)
        matches: list[int] = []
        for index, formula in enumerate(formulas, start=1):
            if not isinstance(formula, dict) or str(formula.get("role") or "").strip().lower() != "result":
                continue
            if any(
                _close_with_percent_equivalence(expected, raw)
                for raw in _formula_declared_result_values_in_base_units(formula.get("latex"))
            ):
                matches.append(index)
        return matches

    for quantity in contract.get("result_quantities", []) or []:
        if not isinstance(quantity, dict):
            continue
        try:
            value = float(quantity.get("value"))
        except (TypeError, ValueError):
            continue
        matches = result_matches(value, quantity.get("unit"))
        if len(matches) == 1:
            quantity["formula_index"] = matches[0]
            continue
        if matches:
            try:
                current = int(quantity.get("formula_index"))
            except (TypeError, ValueError):
                current = 0
            quantity["formula_index"] = current if current in matches else matches[0]
            continue
        name = re.sub(r"[{}\\]", "", str(quantity.get("name") or quantity.get("quantity_id") or "result")).strip()
        unit = str(quantity.get("unit") or "").strip()
        suffix = r"\%" if unit in {"%", "百分比"} else ""
        formulas.append(
            {
                "latex": rf"\mathrm{{{name}}}={value:g}{suffix}",
                "role": "result",
                "meaning": "程序从计算结果账本中镜像的已有结果值",
                "display": True,
                "_program_mirrored_from_contract": True,
            }
        )
        quantity["formula_index"] = len(formulas)

    # Once formula and ledger agree, synchronize only explicitly labelled
    # result values.  This prevents a stale prose percentage from contradicting
    # the machine-checked calculation while leaving all explanatory wording and
    # disciplinary claims untouched.
    trusted_quantities = [
        item
        for item in contract.get("result_quantities", []) or []
        if isinstance(item, dict) and _quantity_formula_matches(item, formulas)
    ]
    trusted_quantities.sort(key=lambda item: len(_quantity_label(item.get("name"))), reverse=True)
    for quantity in trusted_quantities:
        draft["answer"] = _sync_labeled_quantity_value(draft.get("answer"), quantity)
        target_number = _normalized_unit_number(quantity.get("answer_unit_number"))
        for unit in draft.get("answer_units", []) or []:
            if not isinstance(unit, dict) or _normalized_unit_number(unit.get("number")) != target_number:
                continue
            unit["answer"] = _sync_labeled_quantity_value(unit.get("answer"), quantity)
            for step in unit.get("steps", []) or []:
                if isinstance(step, dict):
                    step["result_text"] = _sync_labeled_quantity_value(
                        step.get("result_text"), quantity
                    )

    step_groups: list[list[Any]] = []
    for unit in draft.get("answer_units", []) or []:
        if isinstance(unit, dict) and isinstance(unit.get("steps"), list):
            step_groups.append(unit["steps"])
    if isinstance(draft.get("steps"), list):
        step_groups.append(draft["steps"])
    result_indices = [
        index
        for index, formula in enumerate(formulas, start=1)
        if isinstance(formula, dict) and str(formula.get("role") or "").strip().lower() == "result"
    ]
    for steps in step_groups:
        for step in steps:
            if not isinstance(step, dict):
                continue
            text_values = _numeric_values(step.get("result_text"))
            if not text_values:
                continue
            matching = [
                index
                for index in result_indices
                if any(
                    _close_with_percent_equivalence(formula_value, text_value)
                    for formula_value in _formula_declared_result_values(formulas[index - 1].get("latex"))
                    for text_value in text_values
                )
            ]
            current = step.get("result_formula_indices")
            current_values = current if isinstance(current, list) else ([current] if current is not None else [])
            parsed_current: list[int] = []
            for raw in current_values:
                try:
                    parsed_current.append(int(raw))
                except (TypeError, ValueError):
                    continue
            if len(parsed_current) == 1:
                formula_index = parsed_current[0]
                if 0 < formula_index <= len(formulas):
                    formula_latex = str(formulas[formula_index - 1].get("latex") or "")
                    formula_unit = _result_unit_token(formula_latex)
                    text_unit = _result_unit_token(step.get("result_text"))
                    formula_raw_values = _formula_declared_result_values(formula_latex)
                    if (
                        formula_unit
                        and text_unit
                        and formula_unit.lower() != text_unit.lower()
                        and formula_raw_values
                        and text_values
                        and _close_with_percent_equivalence(formula_raw_values[0], text_values[0])
                    ):
                        step["result_text"] = _replace_result_unit(
                            step.get("result_text"), formula_unit
                        )
            if len(parsed_current) == 1 and len(text_values) == 1:
                formula_index = parsed_current[0]
                if 0 < formula_index <= len(formulas):
                    formula_values = _formula_declared_result_values(formulas[formula_index - 1].get("latex"))
                    if len(formula_values) == 1 and not _close_with_percent_equivalence(formula_values[0], text_values[0]):
                        replacement = f"{formula_values[0]:g}"
                        step["result_text"] = NUMBER_RE.sub(replacement, str(step.get("result_text") or ""), count=1)
                        text_values = [formula_values[0]]
            elif len(parsed_current) == 1 and len(text_values) > 1:
                formula_index = parsed_current[0]
                if 0 < formula_index <= len(formulas):
                    formula_values = _formula_declared_result_values(formulas[formula_index - 1].get("latex"))
                    if len(formula_values) == len(text_values) and not _values_match_as_multiset(
                        formula_values, text_values
                    ):
                        replacements = iter(f"{value:g}" for value in formula_values)
                        step["result_text"] = NUMBER_RE.sub(
                            lambda _match, replacements=replacements: next(replacements),
                            str(step.get("result_text") or ""),
                            count=len(formula_values),
                        )
                        text_values = formula_values
            current_valid = []
            for raw in current_values:
                try:
                    index = int(raw)
                except (TypeError, ValueError):
                    continue
                if index in matching and index not in current_valid:
                    current_valid.append(index)
            exact = [
                index
                for index in matching
                if _values_match_as_multiset(
                    _formula_declared_result_values(formulas[index - 1].get("latex")),
                    text_values,
                )
            ]
            if exact:
                selected = exact[:1]
            else:
                eligible = []
                for index in matching:
                    formula_values = _formula_declared_result_values(formulas[index - 1].get("latex"))
                    if formula_values and all(
                        any(_close_with_percent_equivalence(value, text) for text in text_values)
                        for value in formula_values
                    ):
                        eligible.append(index)
                selected = [index for index in current_valid if index in eligible]
                uncovered = [
                    text
                    for text in text_values
                    if not any(
                        _close_with_percent_equivalence(value, text)
                        for index in selected
                        for value in _formula_declared_result_values(formulas[index - 1].get("latex"))
                    )
                ]
                # Greedily cover each remaining stated value while rejecting
                # formulas that introduce a result absent from this step.
                for index in eligible:
                    if index in selected:
                        continue
                    formula_values = _formula_declared_result_values(formulas[index - 1].get("latex"))
                    if not any(
                        _close_with_percent_equivalence(value, text)
                        for value in formula_values
                        for text in uncovered
                    ):
                        continue
                    selected.append(index)
                    uncovered = [
                        text
                        for text in uncovered
                        if not any(_close_with_percent_equivalence(value, text) for value in formula_values)
                    ]
                    if not uncovered:
                        break
            if selected and set(current_valid) != set(selected):
                step["result_formula_indices"] = selected
    return draft
