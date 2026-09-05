from __future__ import annotations

import re

from docx.oxml import OxmlElement
from docx.oxml.ns import qn

from .expression_normalization import normalize_expression_latex

LATEX_REPL = {
    r"\Delta": "Δ",
    r"\theta": "θ",
    r"\Theta": "Θ",
    r"\gamma": "γ",
    r"\delta": "δ",
    r"\mu": "μ",
    r"\lambda": "λ",
    r"\nu": "ν",
    r"\pi": "π",
    r"\partial": "∂",
    r"\sin": "sin",
    r"\cos": "cos",
    r"\tan": "tan",
    r"\ln": "ln",
    r"\log": "log",
    r"\exp": "exp",
    r"\prod": "∏",
    r"\sum": "∑",
    r"\propto": "∝",
    r"\rightarrow": "→",
    r"\to": "→",
    r"\rightleftharpoons": "⇌",
    r"\leftrightharpoons": "⇌",
    r"\leftharpoons": "↼",
    r"\rightharpoons": "⇀",
    r"\pm": "±",
    r"\leq": "≤",
    r"\le": "≤",
    r"\geq": "≥",
    r"\ge": "≥",
    r"\times": "×",
    r"\cdot": "·",
    r"\ominus": "⊖",
    r"\degree": "°",
    r"\circ": "°",
    r"\left": "",
    r"\right": "",
    r"\,": " ",
    r"\ ": " ",
}


MATH_FONT = "Cambria Math"


CJK_MATH_FONT = "宋体"


class FormulaConversionError(RuntimeError):
    """Raised when the production TeX-to-OMML chain cannot preserve a formula."""


def normalize_latex(src: str) -> str:
    text = normalize_expression_latex(str(src or "").replace(r"\ominus", r"\theta"))
    # Preserve reaction conditions; rendering limitations must not delete content.
    # LibreOffice's OMML importer can misread one upright run such as
    # ``\mathrm{at.\%Ni}`` as the internal token ``%N`` and draw a red
    # unknown-glyph marker.  Keep the percent sign and following chemical
    # symbol in separate upright runs; Microsoft Word renders both forms the
    # same, while the split form is portable across the bundled PDF renderer.
    text = re.sub(
        r"\\mathrm\{(?P<prefix>[^{}]*?\\%)(?P<element>[A-Z][a-z]?)(?P<suffix>[^{}]*)\}",
        lambda match: (
            rf"\mathrm{{{match.group('prefix')}}}"
            rf"\mathrm{{{match.group('element')}{match.group('suffix')}}}"
        ),
        text,
    )
    # Word's linear-math parser treats the domain label ``Le`` as the relation
    # keyword ``le`` (≤). In mass-fraction notation it is an identifier, so
    # make the token boundary and upright typography explicit.
    text = re.sub(r"(?<=w\()Le(?=\))", r"\\mathrm{Le}", text)
    text = re.sub(r"\\ce\{([^{}]+)\}", r"\1", text)
    text = re.sub(r"\\ce(?=[A-Za-z0-9])", "", text)
    # TeX command names consume following letters. Model-produced compact
    # reactions such as ``L\tobcc`` otherwise become the unknown command
    # ``\tobcc`` instead of an arrow followed by the phase label ``bcc``.
    text = re.sub(
        r"\\(to|rightarrow|rightleftharpoons|leftrightharpoons|leftharpoons|rightharpoons)(?=[A-Za-z])",
        r"\\\1 ",
        text,
    )
    text = re.sub(r"\\left\s*\\\|", r"\\Vert ", text)
    text = re.sub(r"\\right\s*\\\|", r"\\Vert ", text)
    text = re.sub(r"\\left\s*\|", r"\\vert ", text)
    text = re.sub(r"\\right\s*\|", r"\\vert ", text)
    text = text.replace(r"\lVert", r"\Vert")
    text = text.replace(r"\rVert", r"\Vert")
    text = text.replace(r"\lvert", r"\vert")
    text = text.replace(r"\rvert", r"\vert")
    text = text.replace(r"\|", r"\Vert")
    text = text.replace(r"\rightleftharpoons", "⇌")
    text = text.replace(r"\leftrightharpoons", "⇌")
    text = text.replace(r"\leftharpoons", "↼")
    text = text.replace(r"\rightharpoons", "⇀")
    text = text.replace("<=>", "⇌")
    text = text.replace("->", r"\rightarrow")
    text = text.replace(r"\,", " ")
    text = text.replace(r"\!", "")
    # Keep paired scalable delimiters for the MathML renderer.  Stripping
    # ``\left(...\right)`` before conversion can create a one-sided Word
    # delimiter when the closing parenthesis carries a script.
    text = text.replace("|", r"\vert")
    text = text.replace(r"\mathrmH", "H")
    text = text.replace(r"\mathrmC", "C")
    text = text.replace(r"\mathrmN", "N")
    text = text.replace(r"\mathrmO", "O")
    text = text.replace(r"\mathrmK", "K")
    text = text.replace(r"\mathrmPa", r"\mathrm{Pa}")
    return text


def m_el(name: str, text: str | None = None):
    el = OxmlElement(f"m:{name}")
    if text is not None:
        el.text = text
    return el


def apply_expression_math_style(omath, *, expression_kind: str = "formula"):
    # Product contract: once content is promoted to a Word formula object,
    # every visible math run is italic, including chemical notation, units,
    # state symbols and text originally emitted through ``\\mathrm``.
    for node in omath.iter():
        if not str(node.tag).endswith("}r"):
            continue
        r_pr = next((child for child in list(node) if str(child.tag).endswith("}rPr")), None)
        if r_pr is None:
            r_pr = m_el("rPr")
            node.insert(0, r_pr)
        for child in list(r_pr):
            if str(child.tag).endswith("}sty") or str(child.tag).endswith("}nor"):
                r_pr.remove(child)
        sty = m_el("sty")
        sty.set(qn("m:val"), "i")
        r_pr.insert(0, sty)
        word_r_pr = next((child for child in list(node) if str(child.tag).endswith("}rPr") and str(child.tag).startswith("{http://schemas.openxmlformats.org/wordprocessingml/2006/main}")), None)
        if word_r_pr is None:
            word_r_pr = OxmlElement("w:rPr")
            node.insert(1, word_r_pr)
        fonts = word_r_pr.find(qn("w:rFonts"))
        if fonts is None:
            fonts = OxmlElement("w:rFonts")
            word_r_pr.insert(0, fonts)
        run_text = "".join(node.itertext())
        fonts.set(qn("w:ascii"), MATH_FONT)
        fonts.set(qn("w:hAnsi"), MATH_FONT)
        fonts.set(qn("w:cs"), MATH_FONT)
        fonts.set(qn("w:eastAsia"), CJK_MATH_FONT if re.search(r"[\u3400-\u9fff]", run_text) else MATH_FONT)
    # Converter releases differ in whether identifiers such as ``mol`` and
    # ``Ni`` become one run or one run per character.  Rejoin only adjacent
    # ASCII-letter runs under the same structural parent.  Punctuation stays
    # separate, so the portable ``%`` / ``Ni`` boundary is preserved.
    for parent in list(omath.iter()):
        children = list(parent)
        index = 0
        while index + 1 < len(children):
            current, following = children[index], children[index + 1]
            if not (
                str(current.tag).endswith("}r")
                and str(following.tag).endswith("}r")
            ):
                index += 1
                continue
            current_texts = [child for child in list(current) if str(child.tag).endswith("}t")]
            following_texts = [child for child in list(following) if str(child.tag).endswith("}t")]
            current_text = current_texts[0].text or "" if len(current_texts) == 1 else ""
            following_text = following_texts[0].text or "" if len(following_texts) == 1 else ""
            if re.fullmatch(r"[A-Za-z]+", current_text) and re.fullmatch(r"[A-Za-z]+", following_text):
                current_texts[0].text = current_text + following_text
                parent.remove(following)
                children.pop(index + 1)
                continue
            index += 1
    return omath


def apply_italic_math_style(omath):
    """Apply the product-wide all-italic Word formula contract."""

    return apply_expression_math_style(omath)


def latex_to_plain(src: str) -> str:
    text = normalize_latex(src)
    text = re.sub(r"\\mathrm\{([^{}]+)\}", r"\1", text)
    text = re.sub(r"\\mathrm(?=[A-Za-z0-9])", "", text)
    text = re.sub(r"\\text\{([^{}]+)\}", r"\1", text)
    text = re.sub(r"\\text(?=[A-Za-z\u4e00-\u9fff])", "", text)
    text = re.sub(r"\\bar\{([^{}]+)\}", r"¯\1", text)
    for old, new in LATEX_REPL.items():
        text = text.replace(old, new)
    text = re.sub(r"\\([A-Za-z]+)", r"\1", text)
    text = text.replace("\\", "")
    text = text.replace("{", "").replace("}", "")
    return text


def omml_from_latex(src: str, *, expression_kind: str = "formula"):
    """Convert with the sole production engine; never fall back to A/B."""
    from .pandoc_word import convert

    try:
        return apply_expression_math_style(convert(normalize_latex(src)), expression_kind=expression_kind)
    except Exception as exc:
        raise FormulaConversionError(f"Pandoc C formula conversion failed: {exc}") from exc



def clear_omml_caches() -> None:
    """Refresh the sole converter after a runtime configuration change."""
    from .pandoc_word import _runtime, _xml

    _runtime.cache_clear()
    _xml.cache_clear()
