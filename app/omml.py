from __future__ import annotations

import os
import platform
import re
import threading
from functools import lru_cache
from pathlib import Path

from docx.oxml import OxmlElement, parse_xml
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
_XSLT_LOCK = threading.RLock()
_MATH_NS = "http://schemas.openxmlformats.org/officeDocument/2006/math"


class FormulaConversionError(RuntimeError):
    """Raised when the production TeX-to-OMML chain cannot preserve a formula."""


def normalize_latex(src: str) -> str:
    text = normalize_expression_latex(str(src or "").replace(r"\ominus", r"\theta"))
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


@lru_cache(maxsize=16)
def _find_mathml2omml_xsl_cached(
    system: str,
    env_path: str,
    program_files: str,
    program_files_x86: str,
    local_app_data: str,
) -> Path | None:
    if env_path and Path(env_path).exists():
        return Path(env_path)
    candidates = []
    if system == "Darwin":
        candidates.extend(
            (
                Path("/Applications/Microsoft Word.app/Contents/Resources/mathml2omml.xsl"),
                Path("/Applications/Microsoft Word.app/Contents/Resources/MML2OMML.XSL"),
            )
        )
    if system == "Windows":
        # Office normally installs the stylesheet below Microsoft Office.
        # Searching all of Program Files and LOCALAPPDATA for every formula made
        # one export perform hundreds of full directory walks on Windows.
        office_roots = [
            Path(root) / "Microsoft Office"
            for root in (program_files, program_files_x86)
            if root
        ]
        if local_app_data:
            office_roots.append(Path(local_app_data) / "Microsoft" / "Office")
        for office_root in office_roots:
            for office_version in ("Office16", "Office15", "Office14", "Office12"):
                candidates.extend(
                    (
                        office_root / "root" / office_version / "MML2OMML.XSL",
                        office_root / "root" / office_version / "mathml2omml.xsl",
                        office_root / office_version / "MML2OMML.XSL",
                        office_root / office_version / "mathml2omml.xsl",
                    )
                )
    for path in candidates:
        if path.exists():
            return path.resolve()
    # Keep a bounded fallback for non-standard Office layouts.  This is cached
    # and deliberately never walks the whole user profile.
    if system == "Windows":
        for office_root in office_roots:
            if not office_root.is_dir():
                continue
            for filename in ("MML2OMML.XSL", "mathml2omml.xsl"):
                match = next((path for path in office_root.rglob(filename) if path.is_file()), None)
                if match is not None:
                    return match.resolve()
    return None


def find_mathml2omml_xsl() -> Path | None:
    return _find_mathml2omml_xsl_cached(
        platform.system(),
        str(os.environ.get("MATHML2OMML_XSL") or "").strip(),
        str(os.environ.get("ProgramFiles") or "").strip(),
        str(os.environ.get("ProgramFiles(x86)") or "").strip(),
        str(os.environ.get("LOCALAPPDATA") or "").strip(),
    )


def _xsl_signature(path: Path) -> tuple[str, int, int]:
    stat = path.stat()
    return str(path.resolve()), int(stat.st_mtime_ns), int(stat.st_size)


@lru_cache(maxsize=8)
def _compiled_mathml2omml_transform(path: str, _mtime_ns: int, _size: int):
    from lxml import etree

    return etree.XSLT(etree.parse(path))


@lru_cache(maxsize=4096)
def _mathml2omml_xml(latex: str, xsl_path: str, xsl_mtime_ns: int, xsl_size: int) -> str:
    from latex2mathml.converter import convert
    from lxml import etree

    mathml = convert(latex)
    transform = _compiled_mathml2omml_transform(xsl_path, xsl_mtime_ns, xsl_size)
    # lxml transformations are fast after compilation.  A lock keeps the
    # shared compiled stylesheet safe when two HTTP export threads overlap.
    with _XSLT_LOCK:
        result = transform(etree.fromstring(mathml.encode("utf-8")))
    root = result.getroot()
    if root is None:
        raise ValueError("mathml2omml produced empty result")
    return etree.tostring(root, encoding="unicode")


@lru_cache(maxsize=4096)
def _pure_python_mathml2omml_xml(latex: str) -> str:
    """Convert through the packaged cross-platform MathML-to-OMML backend."""
    from latex2mathml.converter import convert as latex_to_mathml
    import math_ml2omml

    mathml = latex_to_mathml(latex)
    xml = str(math_ml2omml.convert(mathml) or "").strip()
    if not xml:
        raise ValueError("pure-Python MathML-to-OMML converter produced empty result")
    # The library emits namespace-prefixed OOXML fragments without declaring
    # the standard Office Math namespace.  Add it to the root before parsing.
    if xml.startswith("<m:oMath>"):
        xml = xml.replace("<m:oMath>", f'<m:oMath xmlns:m="{_MATH_NS}">', 1)
    return xml


def clear_omml_caches() -> None:
    """Clear process caches after an Office/XSL installation changes."""
    _find_mathml2omml_xsl_cached.cache_clear()
    _compiled_mathml2omml_transform.cache_clear()
    _mathml2omml_xml.cache_clear()
    _pure_python_mathml2omml_xml.cache_clear()


def _validated_omml_from_xml(xml: str, latex: str):
    omath = parse_xml(xml)
    if omath.tag.endswith("}oMathPara"):
        children = [child for child in list(omath) if child.tag.endswith("}oMath")]
        if children:
            omath = children[0]
    if not omath.tag.endswith("}oMath"):
        raise ValueError(f"mathml2omml produced unsupported root: {omath.tag}")
    if not list(omath):
        raise ValueError("Refusing to create empty OMML formula")
    # Some pure-Python converters omit the explicit hidden degree slot for a
    # square root.  Word tolerates that shape, but LibreOffice renders empty
    # boxes.  Materialize the standard radPr/deg structure for portability.
    for radical in [node for node in omath.iter() if str(node.tag).endswith("}rad")]:
        children = list(radical)
        if not any(str(child.tag).endswith("}deg") for child in children):
            radical_properties = next(
                (child for child in children if str(child.tag).endswith("}radPr")),
                None,
            )
            if radical_properties is None:
                radical_properties = m_el("radPr")
                radical.insert(0, radical_properties)
            degree_hidden = next(
                (child for child in list(radical_properties) if str(child.tag).endswith("}degHide")),
                None,
            )
            if degree_hidden is None:
                degree_hidden = m_el("degHide")
                radical_properties.append(degree_hidden)
            degree_hidden.set(qn("m:val"), "1")
            radical.insert(list(radical).index(radical_properties) + 1, m_el("deg"))
    # A TeX ``cases`` environment is represented in MathML/OMML as a
    # left-hand brace with an intentionally invisible right delimiter.  Word's
    # XSLT serializes that valid one-sided delimiter as an empty ``endChr``.
    # Continue rejecting every accidental empty opening delimiter and empty
    # closing delimiters outside this explicit one-sided construct.
    allows_invisible_end_delimiter = r"\begin{cases}" in latex or r"\right." in latex
    for node in omath.iter():
        if not str(node.tag).endswith(("}begChr", "}endChr")):
            continue
        value = node.get(qn("m:val"))
        if value is not None and not str(value).strip():
            if str(node.tag).endswith("}endChr") and allows_invisible_end_delimiter:
                continue
            raise ValueError("mathml2omml produced an empty delimiter character")
    return omath


def omml_from_latex_via_mathml(src: str):
    try:
        import latex2mathml  # noqa: F401
        import lxml  # noqa: F401
    except Exception as exc:
        raise RuntimeError("latex2mathml/lxml not available") from exc
    latex = normalize_latex(src)
    conversion_errors: list[str] = []
    xsl_path = find_mathml2omml_xsl()
    if xsl_path:
        try:
            return _validated_omml_from_xml(
                _mathml2omml_xml(latex, *_xsl_signature(xsl_path)),
                latex,
            )
        except Exception as exc:
            conversion_errors.append(f"Microsoft XSLT backend: {exc}")
    try:
        return _validated_omml_from_xml(_pure_python_mathml2omml_xml(latex), latex)
    except Exception as exc:
        conversion_errors.append(f"packaged pure-Python backend: {exc}")
        raise RuntimeError("; ".join(conversion_errors)) from exc


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
    return omath


def apply_italic_math_style(omath):
    """Apply the product-wide all-italic Word formula contract."""

    return apply_expression_math_style(omath)


def math_run(text: str):
    r = m_el("r")
    r_pr = m_el("rPr")
    sty = m_el("sty")
    sty.set(qn("m:val"), "i")
    r_pr.append(sty)
    r.append(r_pr)
    t = m_el("t", text)
    r.append(t)
    return r


def read_braced(text: str, open_pos: int) -> tuple[str, int] | None:
    if open_pos >= len(text) or text[open_pos] != "{":
        return None
    depth = 0
    start = open_pos + 1
    for pos in range(open_pos, len(text)):
        ch = text[pos]
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return text[start:pos], pos + 1
    return None


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


def append_text(omath, text: str) -> None:
    text = latex_to_plain(text)
    if text:
        omath.append(math_run(text))


def append_frac(omath, num_text: str, den_text: str) -> None:
    f = m_el("f")
    num = m_el("num")
    den = m_el("den")
    for child in formula_children(num_text):
        num.append(child)
    for child in formula_children(den_text):
        den.append(child)
    f.append(num)
    f.append(den)
    omath.append(f)


def append_radical(omath, body_text: str) -> None:
    rad = m_el("rad")
    deg = m_el("deg")
    body = m_el("e")
    for child in formula_children(body_text):
        body.append(child)
    rad.append(deg)
    rad.append(body)
    omath.append(rad)


def append_script(omath, base_text: str, script_text: str, kind: str) -> None:
    tag = "sSub" if kind == "_" else "sSup"
    script_tag = "sub" if kind == "_" else "sup"
    node = m_el(tag)
    base = m_el("e")
    script = m_el(script_tag)
    base.append(math_run(latex_to_plain(base_text)))
    for child in formula_children(script_text):
        script.append(child)
    node.append(base)
    node.append(script)
    omath.append(node)


def append_subsup(omath, base_text: str, sub_text: str, sup_text: str) -> None:
    node = m_el("sSubSup")
    base = m_el("e")
    sub = m_el("sub")
    sup = m_el("sup")
    base.append(math_run(latex_to_plain(base_text)))
    for child in formula_children(sub_text):
        sub.append(child)
    for child in formula_children(sup_text):
        sup.append(child)
    node.append(base)
    node.append(sub)
    node.append(sup)
    omath.append(node)


def extract_base(buffer: str) -> tuple[str, str]:
    command_match = re.search(r"\\[A-Za-z]+$", buffer)
    if command_match:
        return buffer[: command_match.start()], command_match.group(0)
    word_match = re.search(r"[A-Za-z]+$", buffer)
    if word_match:
        # Keep multi-letter identifiers as text except the last symbol, e.g. RT is not R with subscript T.
        if len(word_match.group(0)) == 1:
            return buffer[: word_match.start()], word_match.group(0)
    return buffer[:-1], buffer[-1]


def read_script(text: str, pos: int) -> tuple[str, int]:
    if pos < len(text) and text[pos] == "{":
        braced = read_braced(text, pos)
        if braced:
            return braced
    if pos < len(text) and text[pos] == "\\":
        m = re.match(r"\\[A-Za-z]+", text[pos:])
        if m:
            return m.group(0), pos + len(m.group(0))
    return (text[pos] if pos < len(text) else ""), pos + 1


def formula_children(src: str) -> list:
    temp = m_el("oMath")
    parse_into(temp, src)
    return list(temp)


def parse_into(omath, src: str) -> None:
    text = normalize_latex(src)
    i = 0
    buffer = ""
    while i < len(text):
        if text.startswith(r"\sqrt", i):
            append_text(omath, buffer)
            buffer = ""
            pos = i + len(r"\sqrt")
            while pos < len(text) and text[pos].isspace():
                pos += 1
            body = read_braced(text, pos)
            if not body:
                buffer += text[i]
                i += 1
                continue
            body_text, i = body
            append_radical(omath, body_text)
            continue
        if text.startswith(r"\frac", i):
            append_text(omath, buffer)
            buffer = ""
            pos = i + len(r"\frac")
            while pos < len(text) and text[pos].isspace():
                pos += 1
            num = read_braced(text, pos)
            if not num:
                buffer += text[i]
                i += 1
                continue
            num_text, pos = num
            while pos < len(text) and text[pos].isspace():
                pos += 1
            den = read_braced(text, pos)
            if not den:
                buffer += text[i]
                i += 1
                continue
            den_text, i = den
            append_frac(omath, num_text, den_text)
            continue
        if text[i] in "_^":
            kind = text[i]
            if buffer:
                buffer, base = extract_base(buffer)
                append_text(omath, buffer)
                buffer = ""
            else:
                base = ""
            script_text, i = read_script(text, i + 1)
            if i < len(text) and text[i] in "_^" and text[i] != kind:
                second_text, i = read_script(text, i + 1)
                if kind == "_":
                    append_subsup(omath, base, script_text, second_text)
                else:
                    append_subsup(omath, base, second_text, script_text)
            else:
                append_script(omath, base, script_text, kind)
            continue
        buffer += text[i]
        i += 1
    append_text(omath, buffer)


def omml_from_latex(src: str, *, expression_kind: str = "formula"):
    degraded_fallback_enabled = os.environ.get("ANSWER_BOOK_ALLOW_DEGRADED_OMML_FALLBACK") == "1"
    mathml_disabled = os.environ.get("ANSWER_BOOK_DISABLE_MATHML_OMML") == "1"
    if not mathml_disabled:
        try:
            return apply_expression_math_style(
                omml_from_latex_via_mathml(src),
                expression_kind=expression_kind,
            )
        except Exception as exc:
            if not degraded_fallback_enabled:
                raise FormulaConversionError(
                    "Formula conversion failed in the production MathML-to-OMML chain; "
                    f"refusing an untracked partial-OMML fallback ({exc})"
                ) from exc
    elif not degraded_fallback_enabled:
        raise FormulaConversionError(
            "MathML-to-OMML conversion is disabled and degraded fallback was not explicitly enabled"
        )

    # This parser is intentionally available only behind an explicit emergency
    # switch.  It supports a small TeX subset and must never masquerade as the
    # production conversion path after an unexpected dependency/XSLT failure.
    omath = m_el("oMath")
    parse_into(omath, src)
    if not list(omath):
        raise ValueError("Refusing to create empty OMML formula")
    return apply_expression_math_style(omath, expression_kind=expression_kind)
