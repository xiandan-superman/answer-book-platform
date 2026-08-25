from __future__ import annotations

import pytest

from app.expression_normalization import normalize_expression_latex, repair_json_escaped_latex


@pytest.mark.parametrize(
    "wrapped",
    [
        r"$x^2+y^2$",
        r"$$x^2+y^2$$",
        r"\(x^2+y^2\)",
        r"\[x^2+y^2\]",
        r" $$ \(x^2+y^2\) $$ ",
    ],
)
def test_structured_formula_normalization_removes_provider_delimiters(wrapped: str) -> None:
    assert normalize_expression_latex(wrapped) == "x^2+y^2"


def test_structured_formula_normalization_is_idempotent_after_unwrapping() -> None:
    normalized = normalize_expression_latex(r"$\Delta U = Q + W$")

    assert normalized == r"\Delta U = Q + W"
    assert normalize_expression_latex(normalized) == normalized


def test_calculator_style_parenthesized_exponent_is_normalized_for_word_math() -> None:
    normalized = normalize_expression_latex(
        r"T_{1}V_{1}^(\gamma-1)=T_{2}V_{2}^(\gamma-1)"
    )

    assert normalized == r"T_{1}V_{1}^{\gamma-1}=T_{2}V_{2}^{\gamma-1}"


def test_compact_partial_derivative_is_normalized_to_explicit_fraction() -> None:
    normalized = normalize_expression_latex(
        r"(\partial E/\partial T)_p = -4.20 \times 10^{-4}\ \mathrm{V\ K^{-1}}"
    )

    assert normalized == (
        r"\left(\frac{\partial E}{\partial T}\right)_p = -4.20 \times 10^{-4}\ \mathrm{V\ K^{-1}}"
    )


def test_json_control_escape_before_tex_command_restores_missing_command_slash() -> None:
    normalized = repair_json_escaped_latex(
        r"\mathrm{CaCO_3(s)}\rightleftharpoons\u0000mathrm{CaO(s)}+\u0000mathrm{CO_2(g)}"
    )

    assert normalized == r"\mathrm{CaCO_3(s)}\rightleftharpoons\mathrm{CaO(s)}+\mathrm{CO_2(g)}"


def test_transport_control_marker_and_synthetic_four_wrappers_are_removed() -> None:
    assert repair_json_escaped_latex(r"4\mathrm{ZnCO_3(s)}\u00024") == r"\mathrm{ZnCO_3(s)}"
    assert repair_json_escaped_latex(r"4\mathrm{H_2(g)}4") == r"\mathrm{H_2(g)}"
    assert repair_json_escaped_latex(r"4\mathrm{HCl(g)}") == r"4\mathrm{HCl(g)}"
