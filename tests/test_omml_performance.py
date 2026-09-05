from __future__ import annotations

from lxml import etree

from app import omml, pandoc_word


def test_repeated_formula_reuses_conversion_but_returns_independent_xml():
    omml.clear_omml_caches()
    first = omml.omml_from_latex(r"W = pV")
    second = omml.omml_from_latex(r"W = pV")
    assert first is not second
    assert etree.tostring(first) == etree.tostring(second)
    assert pandoc_word._xml.cache_info().misses == 1
    assert pandoc_word._xml.cache_info().hits == 1
    assert pandoc_word._runtime.cache_info().misses == 1


def test_pandoc_square_root_has_native_radical():
    formula = omml.omml_from_latex(r"\sqrt{a^2+b^2}")
    assert formula.xpath('.//*[local-name()="rad"]')
    assert not any("\\" in t for t in formula.xpath('.//*[local-name()="t"]/text()'))


def test_formula_chinese_runs_use_cjk_font():
    formula = omml.omml_from_latex(r"x\text{为偶数}")
    xml = etree.tostring(formula, encoding="unicode")
    assert 'w:eastAsia="宋体"' in xml
    assert '为偶数' in xml


def test_cache_refresh_revalidates_runtime():
    omml.omml_from_latex('x')
    omml.clear_omml_caches()
    assert pandoc_word._runtime.cache_info().currsize == 0
    assert pandoc_word._xml.cache_info().currsize == 0
