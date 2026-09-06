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


def protected_math_spans(text: str) -> list[tuple[int, int, str]]:
    """Protect explicit math and legacy bare TeX from substring promotion.

    Preserve source bytes, including command slashes and balanced arguments.
    Unbalanced input remains whole for the authoritative converter to reject;
    extracting a valid-looking suffix would conceal the actual failure.
    """
    spans = [(s.start, s.end, s.latex) for s in iter_delimited_math(text)]
    for match in re.finditer(r"\\[A-Za-z]+", text):
        start = match.start()
        if any(a <= start < b for a, b, _ in spans):
            continue
        # Keep an immediately preceding mathematical left side/factor with
        # the command (e.g. R_p = 1.76 \\times 10^2). Do not consume prose
        # identifiers; every bare letter token must start at a word boundary.
        while start:
            prefix = re.search(
                r"(?<![A-Za-z0-9_])(?:[A-Za-z](?:_\{?[A-Za-z0-9]+\}?)?|"
                r"\d+(?:\.\d+)?|[=+*/^.-])\s*$",
                text[:start],
            )
            if prefix is None or any(a < start and prefix.start() < b for a, b, _ in spans):
                break
            start = prefix.start()
            while start and text[start - 1].isspace():
                start -= 1
        while start < match.start() and text[start].isspace():
            start += 1
        cursor = start
        depth = 0
        parentheses = 0
        while cursor < len(text):
            char = text[cursor]
            if depth == 0:
                if char in "，。；：、！？$\n" or "\u3400" <= char <= "\u9fff":
                    break
                if char in ")）" and parentheses == 0:
                    break
                if char in "(（":
                    parentheses += 1
                elif char in ")）":
                    parentheses -= 1
                # Do not absorb English prose following a TeX expression.
                if char.isspace() and re.match(r"\s+[A-Za-z]{2,}\b(?!\s*[=_^])", text[cursor:]):
                    break
            if char == "{" and (cursor == 0 or text[cursor - 1] != "\\"):
                depth += 1
            elif char == "}" and (cursor == 0 or text[cursor - 1] != "\\"):
                if depth == 0:
                    break
                depth -= 1
            cursor += 1
        end = cursor
        while end > start and text[end - 1].isspace():
            end -= 1
        if end > start:
            spans.append((start, end, text[start:end]))
    return sorted(spans)


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
