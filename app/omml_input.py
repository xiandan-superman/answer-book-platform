from __future__ import annotations

import os
import platform
import threading
import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

from lxml import etree

W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
M_NS = "http://schemas.openxmlformats.org/officeDocument/2006/math"
_TRANSFORM_LOCK = threading.RLock()
STRUCTURED_MATH_METADATA_RE = re.compile(
    r"⟦(?:MATHML:.*?</math>|OMML_STRUCTURE_UNAVAILABLE|OMML_UNREADABLE)⟧",
    re.DOTALL,
)


@dataclass(frozen=True)
class MixedTextResult:
    text: str
    formula_count: int
    structured_formula_count: int
    degraded_formula_count: int


def strip_structured_math_metadata(value: str) -> str:
    """Remove internal Word-formula metadata while retaining visible tokens.

    ``mixed_text_with_structured_math`` always places the visible formula text
    immediately before this marker.  The marker is useful in model input but
    is never user-facing content and must not leak into answer fragments or
    Word output when a model echoes the source question.
    """

    return STRUCTURED_MATH_METADATA_RE.sub("", str(value or ""))


@lru_cache(maxsize=8)
def _find_omml2mathml_xsl_cached(system: str, env_path: str) -> Path | None:
    if env_path and Path(env_path).is_file():
        return Path(env_path).resolve()
    candidates: list[Path] = []
    if system == "Darwin":
        candidates.extend(
            (
                Path("/Applications/Microsoft Word.app/Contents/Resources/omml2mathml.xsl"),
                Path("/Applications/Microsoft Word.app/Contents/Resources/OMML2MATHML.XSL"),
            )
        )
    for candidate in candidates:
        if candidate.is_file():
            return candidate.resolve()
    return None


def find_omml2mathml_xsl() -> Path | None:
    return _find_omml2mathml_xsl_cached(
        platform.system(),
        str(os.environ.get("OMML2MATHML_XSL") or "").strip(),
    )


@lru_cache(maxsize=4)
def _compiled_transform(path: str, mtime_ns: int, size: int) -> etree.XSLT:
    del mtime_ns, size
    return etree.XSLT(etree.parse(path))


def clear_omml_input_caches() -> None:
    _find_omml2mathml_xsl_cached.cache_clear()
    _compiled_transform.cache_clear()


def omml_to_mathml(node: Any) -> str:
    xsl = find_omml2mathml_xsl()
    if xsl is None:
        raise RuntimeError("omml2mathml.xsl not found")
    stat = xsl.stat()
    transform = _compiled_transform(str(xsl), int(stat.st_mtime_ns), int(stat.st_size))
    with _TRANSFORM_LOCK:
        result = transform(node)
    root = result.getroot()
    if root is None:
        raise ValueError("OMML-to-MathML transform produced an empty result")
    return etree.tostring(root, encoding="unicode", with_tail=False)


def mixed_text_with_structured_math(element: Any) -> MixedTextResult:
    """Extract visible text in XML order while preserving OMML structure.

    Microsoft Word's OMML-to-MathML stylesheet is the preferred adapter.  If
    unavailable or unable to convert one expression, the exact visible math
    tokens remain in a marked degraded segment so input is not dropped and the
    caller can expose the loss of structure in diagnostics.
    """

    pieces: list[str] = []
    formula_count = 0
    structured = 0
    degraded = 0

    def walk(node: Any) -> None:
        nonlocal formula_count, structured, degraded
        qname = etree.QName(node)
        if qname.namespace == M_NS and qname.localname in {"oMath", "oMathPara"}:
            formula_count += 1
            try:
                mathml = omml_to_mathml(node)
            except (OSError, RuntimeError, ValueError, etree.Error):
                tokens = "".join(node.xpath(".//m:t/text()", namespaces={"m": M_NS})).strip()
                pieces.append(f"{tokens}⟦OMML_STRUCTURE_UNAVAILABLE⟧" if tokens else "⟦OMML_UNREADABLE⟧")
                degraded += 1
            else:
                tokens = "".join(node.xpath(".//m:t/text()", namespaces={"m": M_NS})).strip()
                pieces.append(f"{tokens}⟦MATHML:{mathml}⟧")
                structured += 1
            return
        if qname.namespace == W_NS and qname.localname == "t":
            pieces.append(str(node.text or ""))
            return
        if qname.namespace == W_NS and qname.localname in {"tab"}:
            pieces.append("\t")
            return
        if qname.namespace == W_NS and qname.localname in {"br", "cr"}:
            pieces.append("\n")
            return
        for child in node:
            walk(child)

    walk(element)
    text = "".join(pieces).strip()
    return MixedTextResult(
        text=text,
        formula_count=formula_count,
        structured_formula_count=structured,
        degraded_formula_count=degraded,
    )
