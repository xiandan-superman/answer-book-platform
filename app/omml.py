from __future__ import annotations

import os
import platform
import re
from pathlib import Path

from docx.oxml import OxmlElement, parse_xml
from docx.oxml.ns import qn


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


def normalize_latex(src: str) -> str:
    text = str(src or "").strip()
    text = re.sub(r"\\ce\{([^{}]+)\}", r"\1", text)
    text = re.sub(r"\\ce(?=[A-Za-z0-9])", "", text)
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
    text = re.sub(r"\\(?:left|right)(?=[\\{}\[\]().|])", "", text)
    text = text.replace("|", r"\vert")
    text = text.replace(r"\mathrmH", "H")
    text = text.replace(r"\mathrmC", "C")
    text = text.replace(r"\mathrmN", "N")
    text = text.replace(r"\mathrmO", "O")
    text = text.replace(r"\mathrmK", "K")
    text = text.replace(r"\mathrmPa", r"\mathrm{Pa}")
    text = text.replace(r"\ominus", r"\theta")
    return text


def find_mathml2omml_xsl() -> Path | None:
    env_path = os.environ.get("MATHML2OMML_XSL")
    if env_path and Path(env_path).exists():
        return Path(env_path)
    candidates = [
        Path("/Applications/Microsoft Word.app/Contents/Resources/mathml2omml.xsl"),
        Path("/Applications/Microsoft Word.app/Contents/Resources/MML2OMML.XSL"),
    ]
    if platform.system() == "Windows":
        roots = [
            os.environ.get("ProgramFiles"),
            os.environ.get("ProgramFiles(x86)"),
            os.environ.get("LOCALAPPDATA"),
        ]
        for root in roots:
            if root:
                base = Path(root)
                candidates.extend(base.glob("**/mathml2omml.xsl"))
                candidates.extend(base.glob("**/MML2OMML.XSL"))
    for path in candidates:
        if path.exists():
            return path
    return None


def omml_from_latex_via_mathml(src: str):
    try:
        from latex2mathml.converter import convert
        from lxml import etree
    except Exception as exc:
        raise RuntimeError("latex2mathml/lxml not available") from exc
    xsl_path = find_mathml2omml_xsl()
    if not xsl_path:
        raise RuntimeError("mathml2omml.xsl not found")
    latex = normalize_latex(src)
    mathml = convert(latex)
    transform = etree.XSLT(etree.parse(str(xsl_path)))
    result = transform(etree.fromstring(mathml.encode("utf-8")))
    root = result.getroot()
    if root is None:
        raise ValueError("mathml2omml produced empty result")
    xml = etree.tostring(root, encoding="unicode")
    omath = parse_xml(xml)
    if omath.tag.endswith("}oMathPara"):
        children = [child for child in list(omath) if child.tag.endswith("}oMath")]
        if children:
            return children[0]
    if not omath.tag.endswith("}oMath"):
        raise ValueError(f"mathml2omml produced unsupported root: {omath.tag}")
    if not list(omath):
        raise ValueError("Refusing to create empty OMML formula")
    return omath


def m_el(name: str, text: str | None = None):
    el = OxmlElement(f"m:{name}")
    if text is not None:
        el.text = text
    return el


def apply_italic_math_style(omath):
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
    return omath


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
                second_kind = text[i]
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


def omml_from_latex(src: str):
    if os.environ.get("ANSWER_BOOK_DISABLE_MATHML_OMML") != "1":
        try:
            return apply_italic_math_style(omml_from_latex_via_mathml(src))
        except Exception:
            pass
    omath = m_el("oMath")
    parse_into(omath, src)
    if not list(omath):
        raise ValueError("Refusing to create empty OMML formula")
    return apply_italic_math_style(omath)
