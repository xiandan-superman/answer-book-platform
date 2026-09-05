"""Compatibility imports for historical callers; Word generation is C-only.

Legacy A/B settings are intentionally ignored when resuming old tasks.
"""
from contextlib import contextmanager


def selected_word_tool_variant(value: str | None = None) -> str:
    return "C"


@contextmanager
def word_tool_selection(variant: str | None):
    yield


def word_tool_runtime_info() -> dict:
    from .pandoc_word import runtime_info

    return runtime_info()
