from __future__ import annotations


def test_chinese_constant_normalization_is_idempotent_and_repairs_legacy_nesting() -> None:
    from app.expression_normalization import normalize_expression_latex

    expected = r"[M\cdot] = \mathrm{常数}"
    assert normalize_expression_latex("[M\\cdot] = 常数") == expected
    assert normalize_expression_latex(expected) == expected
    assert (
        normalize_expression_latex(r"[M\cdot] = \mathrm{\mathrm{\mathrm{常数}}}")
        == expected
    )
    assert (
        normalize_expression_latex(
            r"\frac{\mathrm{d}x}{\mathrm{d}t}=0\quad(\text{即 }x=\text{\mathrm{\mathrm{\mathrm{常数}}}})"
        )
        == r"\frac{\mathrm{d}x}{\mathrm{d}t}=0\quad(\text{即 }x=\mathrm{常数})"
    )
