from __future__ import annotations

import os
from pathlib import Path

import pytest

pytestmark = pytest.mark.e2e


def _draft(text: str, batch_id: str, updated_at: int) -> dict:
    return {
        "schema": "practice_workspace_draft.v1",
        "stage": "input",
        "practice_batch_id": batch_id,
        "updated_at": updated_at,
        "input": {
            "question_text": text,
            "source_files": [],
            "count": "5",
            "difficulty": "基础到进阶",
            "focus": "",
            "question_types": [],
            "include_source_content_in_generation": True,
            "semantic_review_enabled": False,
        },
    }


def _seed_archive(page, record: dict, archived_at: int) -> None:
    page.evaluate(
        """async ([record, archivedAt]) => practiceWorkspaceDatabaseOperation('exam', 'put', {
          ...record,
          mode: `exam:archive:${archivedAt}`,
          workspace_mode: 'exam',
          archived_at: archivedAt,
        })""",
        [record, archived_at],
    )


def test_workspace_draft_buttons_resolve_history_and_ignore_empty_archives() -> None:
    base_url = os.getenv("ANSWER_BOOK_E2E_URL", "").strip()
    if not base_url:
        pytest.skip("set ANSWER_BOOK_E2E_URL to an already running local platform")

    playwright = pytest.importorskip("playwright.sync_api")
    with playwright.sync_playwright() as runtime:
        launch_options = {} if Path(runtime.chromium.executable_path).is_file() else {"channel": "chrome"}
        browser = runtime.chromium.launch(headless=True, **launch_options)

        context = browser.new_context(viewport={"width": 1440, "height": 1000})
        page = context.new_page()
        page.goto(base_url, wait_until="networkidle")
        _seed_archive(page, _draft("有效历史草稿", "valid", 100), 100)
        _seed_archive(page, _draft("", "empty", 200), 200)
        page.evaluate("openPracticeEntry('exam')")
        page.locator("#practiceWorkspaceDraftNotice:not(.hidden)").wait_for()
        assert page.evaluate("practiceWorkspaceRestoreCandidates.exam.record.practice_batch_id") == "valid"

        button_state = page.locator("#practiceWorkspaceDraftClear").evaluate(
            """button => {
              const rect = button.getBoundingClientRect();
              const hit = document.elementFromPoint(rect.left + rect.width / 2, rect.top + rect.height / 2);
              return {type: button.type, disabled: button.disabled, hit: hit?.closest('button')?.id || ''};
            }"""
        )
        assert button_state == {"type": "button", "disabled": False, "hit": "practiceWorkspaceDraftClear"}

        page.locator("#practiceWorkspaceDraftClear").click()
        page.locator("#practiceWorkspaceDraftNotice").wait_for(state="hidden")
        assert page.locator("#practiceQuestionText").input_value() == ""
        assert "当前为新任务" in page.locator("#practiceStatusBanner").inner_text()

        page.reload(wait_until="networkidle")
        page.evaluate("openPracticeEntry('exam')")
        page.locator("#practiceWorkspaceDraftNotice:not(.hidden)").wait_for()
        page.locator("#practiceWorkspaceDraftRestorePrevious").click()
        playwright.expect(page.locator("#practiceQuestionText")).to_have_value("有效历史草稿")
        active = page.evaluate("async () => practiceWorkspaceDatabaseOperation('exam', 'get')")
        assert active["input"]["question_text"] == "有效历史草稿"

        empty_context = browser.new_context(viewport={"width": 1440, "height": 1000})
        empty_page = empty_context.new_page()
        empty_page.goto(base_url, wait_until="networkidle")
        _seed_archive(empty_page, _draft("", "empty-only", 300), 300)
        empty_page.evaluate("openPracticeEntry('exam')")
        empty_page.wait_for_timeout(300)
        assert empty_page.locator("#practiceWorkspaceDraftNotice").is_hidden()
        assert empty_page.evaluate("practiceWorkspaceRestoreCandidates.exam") is None
        empty_page.evaluate("void restorePreviousPracticeWorkspace('exam')")
        empty_page.locator("#platformDialog:not(.hidden)").wait_for()
        assert "还没有可恢复的历史草稿" in empty_page.locator("#platformDialogMessage").inner_text()

        browser.close()
