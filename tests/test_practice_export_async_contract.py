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
    assert "/api/practice/export-jobs/${encodeURIComponent(job.job_id)}/download" in export_flow


def test_server_keeps_sync_export_and_adds_background_job_routes() -> None:
    assert '"/api/practice/export", "/api/practice/export/prepare"' in SERVER
    assert '["api", "practice", "export-jobs"]' in SERVER
    assert 'parts[4] == "download"' in SERVER
    assert 'parts[4] == "acknowledge"' not in SERVER
