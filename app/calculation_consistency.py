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
    # A fixed absolute tolerance accepts orders-of-magnitude errors for small
    # physical quantities. Rounding tolerance must scale with the operands.
    return math.isclose(actual, expected, rel_tol=0.012, abs_tol=0.0)


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
    return scale is not None and abs(actual - expected) <= 0.002 * scale


def formula_numeric_consistency_issues(formulas: list[dict[str, Any]], *,
                                     observations: list[dict[str, Any]] | None = None) -> list[str]:
    """Only complete, literal arithmetic equalities are machine-decidable.

    Variables, units, approximate signs and cross-formula assignments require
    context. They are deliberately left to the responsible model.
    """
    issues: list[str] = []
    for index, formula in enumerate(formulas, start=1):
        if str(formula.get("source_note") or "").startswith("程序"):
            continue
        latex = str(formula.get("latex") or "")
        parts = latex.split("=")
        if len(parts) < 2 or any(not re.fullmatch(r"[0-9.+*/() \-]+", part) for part in parts):
            continue
        values = [_eval_simple_expression(part) for part in parts]
        if any(value is None for value in values):
            continue
        actual = values[0]
        assert actual is not None
        agrees = all(_close(actual, value) for value in values[1:] if value is not None)
        if observations is not None:
            observations.append({"formula_index": index, "status": "passed" if agrees else "failed",
                                 "scope": "literal_numeric_equality", "unit_verification": "not_applicable"})
        if not agrees:
            issues.append(f"formula_{index}_numeric_equality_mismatch:{values}")
    return issues



def calculation_draft_consistency_issues(draft: dict[str, Any], *,
                                       observations: list[dict[str, Any]] | None = None) -> list[str]:
    """Check explicit numeric formula objects, never infer bindings from answer prose."""
    formulas = [item for item in draft.get("formulas", []) or [] if isinstance(item, dict)]
    return formula_numeric_consistency_issues(formulas, observations=observations)



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

    # This optional ledger is authored by the model. Its existence and scope
    # must never be inferred from words in the question or answer.
    contract = draft.get("calculation_contract")
    if contract is None:
        return []
    if not isinstance(contract, dict):
        return ["calculation_contract_invalid_type"]
    issues: list[str] = []
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
            if isinstance(item.get("value"), str) and item["value"].strip():
                # Symbolic values remain bound by ID; their meaning is not parsed.
                by_id[quantity_id] = {**item, "value": None}
                continue
            issues.append(f"calculation_contract_invalid_quantity_value:{quantity_id}")
            continue
        if not math.isfinite(value):
            issues.append(f"calculation_contract_invalid_quantity_value:{quantity_id}")
            continue
        normalized = dict(item)
        normalized["value"] = value
        by_id[quantity_id] = normalized

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
        if any(component["value"] is None for component in components if component is not None):
            continue
        try:
            expected_total = float(partition.get("expected_total", 1.0))
        except (TypeError, ValueError):
            issues.append(f"calculation_contract_invalid_expected_total:{index}")
            continue
        if not math.isfinite(expected_total):
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
        units = {str(component.get("unit") or "").strip() for component in components if component is not None}
        if len(bases) > 1 or len(units) > 1:
            continue  # No arithmetic across potentially different units or bases.
        total = sum(float(component["value"]) for component in components if component is not None)
        if not _close(total, expected_total):
            issues.append(
                f"calculation_contract_partition_sum_mismatch:{index}:{total:.8g}!={expected_total:.8g}"
            )

    transitions = contract.get("transitions") if isinstance(contract.get("transitions"), list) else []
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
        units = {str(item.get("unit") or "").strip() for item in [parent, *products] if item is not None}
        if len(bases) > 1 or len(units) > 1 or any(item["value"] is None for item in [parent, *products] if item is not None):
            continue  # No semantic comparison of units, symbols or basis labels.
        product_total = sum(float(item["value"]) for item in products if item is not None)
        parent_value = float(parent["value"])
        if not _close(product_total, parent_value):
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
            if fraction > 1.0:
                continue  # Legacy percentage representation is ambiguous.
            normalized_fraction = fraction
            derived = by_id[derived_id]
            expected_derived = parent_value * normalized_fraction
            if not _close(float(derived["value"]), expected_derived):
                issues.append(
                    f"calculation_contract_transition_derivation_mismatch:{index}:"
                    f"derived={float(derived['value']):.8g}:expected={expected_derived:.8g}"
                )
    return list(dict.fromkeys(issues))


def reconcile_calculation_reference_structure(draft: dict[str, Any]) -> dict[str, Any]:
    """Preserve model-authored prose and bindings; numeric coincidence proves no identity."""
    return draft
