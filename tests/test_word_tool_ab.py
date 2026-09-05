"""Regression coverage for retiring historical Word A/B selection."""
from __future__ import annotations

import pytest

from app.officecli_word import selected_word_tool_variant, word_tool_selection
from app.practice_export_jobs import _cache_key


@pytest.mark.parametrize("legacy", ["A", "B", "C", "", "unknown"])
def test_all_legacy_settings_resolve_to_c(monkeypatch, legacy):
    monkeypatch.setenv("ANSWER_BOOK_WORD_TOOL_VARIANT", legacy)
    with word_tool_selection(legacy):
        assert selected_word_tool_variant() == "C"
    assert selected_word_tool_variant(legacy) == "C"


def test_retired_settings_cannot_change_export_cache(monkeypatch):
    data = {"exercises": [{"number": 1, "stem": "题干"}]}
    monkeypatch.setenv("ANSWER_BOOK_WORD_TOOL_VARIANT", "A")
    key = _cache_key(data)
    monkeypatch.setenv("ANSWER_BOOK_WORD_TOOL_VARIANT", "B")
    assert _cache_key(data) == key
