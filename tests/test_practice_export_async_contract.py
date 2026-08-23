from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
APP_JS = (ROOT / "web" / "app.js").read_text(encoding="utf-8")
SERVER = (ROOT / "app" / "server.py").read_text(encoding="utf-8")


def test_frontend_prepares_polls_and_downloads_background_word_job() -> None:
    start = APP_JS.index("async function prepareOrDownloadPracticeWord")
    end = APP_JS.index("function selectedPracticeSet", start)
    export_flow = APP_JS[start:end]

    assert "/api/practice/export/prepare?kind=questions" in export_flow
    assert 'await api("/api/practice/export/prepare?kind=questions"' in export_flow
    assert "waitForPracticeWordExportJob" in export_flow
    assert "downloadRememberedPracticeWord(completedPointer, button)" in export_flow
    assert "practiceWordDesktopSaveApi" in APP_JS
    assert "practiceWordDesktopRuntimeExpected" in APP_JS
    assert "waitForPracticeWordDesktopSaveApi" in APP_JS
    assert "desktopApi.save_practice_word(pointer.job_id, pointer.filename)" in APP_JS
    assert "/api/practice/export-jobs/${encodeURIComponent(pointer.job_id)}/download" in APP_JS


def test_server_keeps_sync_export_and_adds_background_job_routes() -> None:
    assert '"/api/practice/export", "/api/practice/export/prepare"' in SERVER
    assert '["api", "practice", "export-jobs"]' in SERVER
    assert 'parts[4] == "download"' in SERVER
    assert 'parts[4] == "retry"' in SERVER
    assert 'parts[4] == "acknowledge"' not in SERVER


def test_browser_persists_only_versioned_expiring_export_job_pointers() -> None:
    assert 'answerBook.practiceWordExportPointers.v1' in APP_JS
    assert "PRACTICE_WORD_EXPORT_POINTER_SCHEMA_VERSION = 1" in APP_JS
    assert "PRACTICE_WORD_EXPORT_POINTER_TTL_MS" in APP_JS
    assert "export_key: exportKey" in APP_JS
    assert "job_id: jobId" in APP_JS
    assert "filename" in APP_JS
    storage_start = APP_JS.index("function writePracticeWordExportPointers")
    storage_end = APP_JS.index("function practiceWordRecoveryError", storage_start)
    storage_flow = APP_JS[storage_start:storage_end]
    assert "exercises" not in storage_flow
    assert "question_text" not in storage_flow
    assert "source_files" not in storage_flow


def test_export_recovery_is_explicit_and_independent_from_generation_recovery() -> None:
    assert 'id="practiceWordRecoveryNotice"' in (ROOT / "web" / "index.html").read_text(encoding="utf-8")
    assert "resumeRememberedPracticeWordExports" in APP_JS
    assert "Word 已生成，等待你确认下载。" in APP_JS
    assert "downloadRememberedPracticeWord" in APP_JS
    assert "retryRememberedPracticeWord" in APP_JS
    recovery_start = APP_JS.index("async function resumeRememberedPracticeWordExports")
    recovery_end = APP_JS.index("function practiceExerciseExportId", recovery_start)
    recovery_flow = APP_JS[recovery_start:recovery_end]
    assert "goToPage(" not in recovery_flow
    assert "downloadPracticeWord(" in recovery_flow
    delivery_start = APP_JS.index("async function downloadRememberedPracticeWord")
    delivery_end = APP_JS.index("async function retryRememberedPracticeWord", delivery_start)
    delivery_flow = APP_JS[delivery_start:delivery_end]
    assert "forgetPracticeWordExportPointer(pointer.export_key)" not in delivery_flow
    assert "已开始下载：${pointer.filename}" in delivery_flow
    assert "已保存到：${result.path}" in delivery_flow
