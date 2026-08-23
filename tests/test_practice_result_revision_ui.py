from __future__ import annotations

import tempfile
from pathlib import Path
from unittest.mock import patch

from app import practice_store


ROOT = Path(__file__).resolve().parents[1]
APP_JS = (ROOT / "web" / "app.js").read_text(encoding="utf-8")
INDEX_HTML = (ROOT / "web" / "index.html").read_text(encoding="utf-8")


def _function_source(name: str, next_name: str) -> str:
    start = APP_JS.index(f"function {name}")
    end = APP_JS.index(f"function {next_name}", start)
    return APP_JS[start:end]


def test_result_history_opens_with_reachable_revision_controls() -> None:
    assert 'id="practiceUndoBtn"' in INDEX_HTML
    assert 'id="practiceUndoBtn" type="button" class="secondary-button" disabled' in INDEX_HTML
    assert "撤销上次修改" in INDEX_HTML
    assert '$("practiceUndoBtn")?.addEventListener("click", undoPracticeChange);' in APP_JS

    start = APP_JS.index("async function openGenerationTaskResult(task)")
    end = APP_JS.index("async function reuseGenerationTask(task)", start)
    opened_result = APP_JS[start:end]
    assert "currentPracticeHistoryId = String(record.history_id" in opened_result
    assert "currentPracticeRevisionCount = Number(record.revision_count" in opened_result
    assert opened_result.index("currentPracticeRevisionCount =") < opened_result.index("renderPracticeResults(latestPracticeSet)")

    rendered_result = _function_source("renderPracticeResults", "renderPracticeSourceSelection")
    assert '$("practiceUndoBtn").disabled = currentPracticeRevisionCount < 1 || !data.history_id;' in rendered_result


def test_editor_programmatic_values_refresh_custom_select_labels() -> None:
    applied_values = _function_source("applyPracticeEditorValues", "setPracticeEditorDraftAvailable")
    populated_editor = _function_source("populatePracticeEditor", "openPracticeEditor")

    for control_id in ("practiceEditType", "practiceEditDifficulty"):
        assert f'syncPlatformSelectElement($("{control_id}"));' in applied_values
    assert "syncPlatformSelectElement(typeSelect);" in populated_editor
    assert "syncPlatformSelectElement(difficultySelect);" in populated_editor


def test_edit_then_undo_restores_question_review_and_sources() -> None:
    with tempfile.TemporaryDirectory() as raw, patch.object(
        practice_store,
        "PRACTICE_HISTORY_DIR",
        Path(raw),
    ), patch.object(
        practice_store,
        "_with_current_quality",
        lambda data: practice_store._with_edit_versions(data),
    ):
        saved = practice_store.save_practice_record(
            {
                "semantic_review": {
                    "status": "passed",
                    "review_scope": "complete_set",
                    "items": [{"number": 1, "status": "passed", "risks": []}],
                },
                "exercises": [
                    {
                        "number": 1,
                        "plan_item_id": "plan_item_01",
                        "stem": "原题干",
                        "difficulty": "进阶",
                        "source_refs": ["source_01", "source_02", "source_03"],
                    }
                ],
            }
        )
        history_id = str(saved["history_id"])
        edit_version = saved["data"]["exercises"][0]["_edit_version"]

        edited = practice_store.update_practice_exercise(
            history_id,
            0,
            {
                "number": 1,
                "plan_item_id": "plan_item_01",
                "stem": "编辑后的题干",
                "difficulty": "进阶",
                "source_refs": ["source_01", "source_02", "source_03"],
            },
            change_reason="manual_edit",
            expected_edit_version=edit_version,
        )

        assert edited["revision_count"] == 1
        assert edited["data"]["semantic_review"]["status"] == "failed"
        assert edited["data"]["semantic_review"]["items"][0]["status"] == "not_reviewed"

        restored = practice_store.undo_last_practice_revision(history_id)
        exercise = restored["data"]["exercises"][0]
        assert exercise["stem"] == "原题干"
        assert exercise["difficulty"] == "进阶"
        assert exercise["source_refs"] == ["source_01", "source_02", "source_03"]
        assert restored["data"]["semantic_review"]["status"] == "passed"
        assert restored["data"]["semantic_review"]["items"][0]["status"] == "passed"
