from __future__ import annotations

import re
from collections.abc import Iterator
from dataclasses import dataclass

DELIMITED_MATH_RE = re.compile(
    r"(?<!\\)\$\$(.+?)(?<!\\)\$\$|\\\[(.+?)\\\]|(?<!\\)\$(.+?)(?<!\\)\$|\\\((.+?)\\\)",
    re.DOTALL,
)


@dataclass(frozen=True)
class DelimitedMathSpan:
    start: int
    end: int
    latex: str
    display: bool
    opening: str
    closing: str


def delimited_math_span(match: re.Match[str]) -> DelimitedMathSpan:
    """Convert one shared delimiter match into a renderer-neutral span."""

    group_index = next(
        index for index, value in enumerate(match.groups(), start=1) if value is not None
    )
    delimiters = {
        1: ("$$", "$$", True),
        2: (r"\[", r"\]", True),
        3: ("$", "$", False),
        4: (r"\(", r"\)", False),
    }
    opening, closing, display = delimiters[group_index]
    latex = match.group(group_index)
    if display:
        # Formatting newlines inside display delimiters are whitespace. An
        # explicit LaTeX ``\\`` row break remains present in ``latex``.
        latex = re.sub(r"[ \t]*\r?\n[ \t]*", " ", latex)
    return DelimitedMathSpan(
        start=match.start(),
        end=match.end(),
        latex=latex.strip(),
        display=display,
        opening=opening,
        closing=closing,
    )


def iter_delimited_math(text: str) -> Iterator[DelimitedMathSpan]:
    for match in DELIMITED_MATH_RE.finditer(text):
        yield delimited_math_span(match)


def collapse_delimited_math_newlines(text: str) -> str:
    """Keep complete display expressions intact before paragraph splitting."""

    parts: list[str] = []
    cursor = 0
    for span in iter_delimited_math(text):
        parts.append(text[cursor : span.start])
        parts.append(f"{span.opening}{span.latex}{span.closing}")
        cursor = span.end
    parts.append(text[cursor:])
    return "".join(parts)
