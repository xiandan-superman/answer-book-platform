from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

pytestmark = pytest.mark.e2e


def _practice_recovery_job(job_id: str, status: str, title: str) -> dict:
    return {
        "job_id": job_id,
        "status": status,
        "operation": "analyze",
        "task_kind": "knowledge",
        "title": title,
        "progress_message": "正在梳理恢复测试材料",
        "payload": {
            "source_mode": "knowledge",
            "knowledge_title": title,
            "question_text": "恢复测试材料",
            "source_files": [],
            "blueprint_review_enabled": True,
            "include_source_content_in_generation": True,
        },
        "result": {
            "source_mode": "knowledge",
            "source_analysis": {"subject": "恢复测试", "knowledge_points": ["恢复知识点"]},
            "source_scope": {
                "mode": "question_set",
                "title": title,
                "granularity": "top_level",
                "has_hierarchy": False,
                "questions": [{
                    "source_question_id": "source_01",
                    "number": "1",
                    "title": "恢复知识单元",
                    "stem_excerpt": "用于验证显式恢复入口",
                    "source_content": "用于验证显式恢复入口",
                    "question_type": "概念题",
                    "source_difficulty": "基础",
                    "knowledge_points": ["恢复知识点"],
                    "required_constraints": {
                        "essential_definitions": [],
                        "essential_formulas": [],
                        "applicable_boundaries": [],
                    },
                    "source_ref": {},
                    "constraint_status": "complete",
                }],
            },
            "source_files": [],
            "source_file_diagnostics": [],
            "generation": {"provider": "fixture", "model": "recovery-fixture"},
        },
        "error": "任务未完成测试说明",
        "error_presentation": {"message": "任务未完成测试说明"},
    }


def test_primary_desktop_workflows_have_safe_initial_state() -> None:
    base_url = os.getenv("ANSWER_BOOK_E2E_URL", "").strip()
    if not base_url:
        pytest.skip("set ANSWER_BOOK_E2E_URL to an already running local platform")

    playwright = pytest.importorskip("playwright.sync_api")
    with playwright.sync_playwright() as runtime:
        # Local Playwright packages and their downloaded browser can be
        # upgraded independently.  Prefer the bundled executable, but keep
        # the smoke test usable on a standard desktop installation when that
        # cache has not been downloaded yet.
        launch_options = {} if Path(runtime.chromium.executable_path).is_file() else {"channel": "chrome"}
        browser = runtime.chromium.launch(headless=True, **launch_options)
        page = browser.new_page(viewport={"width": 1440, "height": 1000})
        page.goto(base_url, wait_until="networkidle")

        assert page.locator("#platformVersion").inner_text().strip()
        page.evaluate("goToPage('exam')")
        page.locator("#page-exam.active").wait_for()
        assert page.locator("#examSelect").input_value() == ""
        assert page.locator("#createTaskBtn").is_disabled()

        page.evaluate("goToPage('practice')")
        page.locator("#page-practice.active").wait_for()
        assert page.locator("#practiceGenerateBtn").is_disabled()
        page.locator("#practiceQuestionText").fill("测试题目：1+1 等于多少？")
        assert not page.locator("#practiceGenerateBtn").is_disabled()

        browser.close()


def test_word_export_recovery_keeps_multiple_filenames_and_requires_explicit_download() -> None:
    base_url = os.getenv("ANSWER_BOOK_E2E_URL", "").strip()
    if not base_url:
        pytest.skip("set ANSWER_BOOK_E2E_URL to an already running local platform")

    playwright = pytest.importorskip("playwright.sync_api")
    with playwright.sync_playwright() as runtime:
        launch_options = {} if Path(runtime.chromium.executable_path).is_file() else {"channel": "chrome"}
        browser = runtime.chromium.launch(headless=True, **launch_options)
        context = browser.new_context(viewport={"width": 1440, "height": 1000}, accept_downloads=True)
        page = context.new_page()
        job_id = "practice_word_aaaaaaaaaaaaaaaaaaaaaaaa"
        calls = {"status": 0, "download": 0}

        def export_route(route):
            if route.request.url.endswith("/download"):
                calls["download"] += 1
                route.fulfill(
                    status=200,
                    content_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                    body=b"word-export-fixture",
                )
                return
            calls["status"] += 1
            route.fulfill(
                status=200,
                content_type="application/json",
                body=json.dumps({
                    "ok": True,
                    "job": {
                        "job_id": job_id,
                        "status": "completed",
                        "completed_count": 1,
                        "total_count": 1,
                        "filename": "服务端默认名.docx",
                    },
                }, ensure_ascii=False),
            )

        context.route("**/api/practice/export-jobs/**", export_route)
        page.goto(f"{base_url.rstrip('/')}?page=keys", wait_until="networkidle")
        page.evaluate(
            """(jobId) => {
              rememberPracticeWordExportPointer('history-a:1:独立文件A.docx', jobId, '独立文件A.docx');
              rememberPracticeWordExportPointer('history-a:1:独立文件B.docx', jobId, '独立文件B.docx');
            }""",
            job_id,
        )
        page.reload(wait_until="networkidle")
        page.locator("#practiceWordRecoveryNotice:not(.hidden)").wait_for(timeout=4000)
        page.locator(".practice-word-recovery-item").filter(has_text="独立文件A.docx").wait_for()
        page.locator(".practice-word-recovery-item").filter(has_text="独立文件B.docx").wait_for()

        assert page.locator("#page-keys.active").is_visible()
        assert calls["status"] == 1
        assert calls["download"] == 0
        assert page.locator(".practice-word-recovery-item").count() == 2
        raw_storage = page.evaluate("localStorage.getItem(PRACTICE_WORD_EXPORT_POINTER_STORAGE_KEY)")
        assert '"schema_version":1' in raw_storage
        assert "question_text" not in raw_storage
        assert "exercises" not in raw_storage
        assert "word-export-fixture" not in raw_storage

        page.evaluate(
            """() => showPracticeRecoveryNotice({
              job_id: 'practice-generation-independent',
              status: 'running',
              title: '独立的生题恢复任务',
              progress_message: '生题任务仍在后台运行'
            })"""
        )
        assert page.locator("#practiceRecoveryNotice").is_visible()
        assert page.locator("#practiceWordRecoveryNotice").is_visible()

        first = page.locator(".practice-word-recovery-item").filter(has_text="独立文件A.docx")
        with page.expect_download() as download_info:
            first.get_by_role("button", name="下载 Word").click()
        assert download_info.value.suggested_filename == "独立文件A.docx"
        page.locator("#platformDialog:not(.hidden)").wait_for(timeout=4000)
        page.locator("#platformDialogConfirm").click()
        page.locator(".practice-word-recovery-item").filter(has_text="独立文件A.docx").wait_for(state="detached")
        assert page.locator(".practice-word-recovery-item").filter(has_text="独立文件B.docx").is_visible()
        assert calls["download"] == 1

        remaining_storage = page.evaluate("localStorage.getItem(PRACTICE_WORD_EXPORT_POINTER_STORAGE_KEY)")
        assert "独立文件A.docx" not in remaining_storage
        assert "独立文件B.docx" in remaining_storage
        browser.close()


def test_word_export_recovery_cleans_stale_jobs_sanitizes_failures_and_retries() -> None:
    base_url = os.getenv("ANSWER_BOOK_E2E_URL", "").strip()
    if not base_url:
        pytest.skip("set ANSWER_BOOK_E2E_URL to an already running local platform")

    playwright = pytest.importorskip("playwright.sync_api")
    with playwright.sync_playwright() as runtime:
        launch_options = {} if Path(runtime.chromium.executable_path).is_file() else {"channel": "chrome"}
        browser = runtime.chromium.launch(headless=True, **launch_options)
        context = browser.new_context(viewport={"width": 1440, "height": 1000}, accept_downloads=True)
        page = context.new_page()
        job_id = "practice_word_bbbbbbbbbbbbbbbbbbbbbbbb"
        state = {"status": "missing", "download_fails": True, "retry_calls": 0, "download_calls": 0}

        def export_route(route):
            if route.request.method == "POST" and route.request.url.endswith("/retry"):
                state["retry_calls"] += 1
                state["status"] = "completed"
                route.fulfill(
                    status=202,
                    content_type="application/json",
                    body=json.dumps({"ok": True, "job": {"job_id": job_id, "status": "queued"}}),
                )
                return
            if route.request.url.endswith("/download"):
                state["download_calls"] += 1
                if state["download_fails"]:
                    route.fulfill(
                        status=503,
                        content_type="application/json",
                        body=json.dumps({"error": '{"internal":"storage failed","request_id":"SECRET-123"}'}),
                    )
                else:
                    route.fulfill(status=200, content_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document", body=b"retried-word")
                return
            if state["status"] == "missing":
                route.fulfill(status=404, content_type="application/json", body=json.dumps({"error": "missing"}))
                return
            route.fulfill(
                status=200,
                content_type="application/json",
                body=json.dumps({
                    "ok": True,
                    "job": {
                        "job_id": job_id,
                        "status": state["status"],
                        "error": '{"provider":"internal","request_id":"SECRET-123"}',
                        "completed_count": 1,
                        "total_count": 1,
                    },
                }),
            )

        context.route("**/api/practice/export-jobs/**", export_route)
        page.goto(base_url, wait_until="networkidle")
        page.evaluate(
            """(jobId) => {
              localStorage.setItem(PRACTICE_WORD_EXPORT_POINTER_STORAGE_KEY, JSON.stringify({
                schema_version: 1,
                updated_at: new Date().toISOString(),
                records: [{
                  export_key: 'expired', job_id: jobId, filename: '过期.docx',
                  created_at: new Date(Date.now() - 9 * 86400000).toISOString(),
                  expires_at: new Date(Date.now() - 2 * 86400000).toISOString()
                }]
              }));
            }""",
            job_id,
        )
        page.reload(wait_until="networkidle")
        assert page.evaluate("localStorage.getItem(PRACTICE_WORD_EXPORT_POINTER_STORAGE_KEY)") is None
        assert page.locator("#practiceWordRecoveryNotice").is_hidden()

        page.evaluate("(jobId) => rememberPracticeWordExportPointer('missing', jobId, '不存在.docx')", job_id)
        page.evaluate("resumeRememberedPracticeWordExports()")
        page.wait_for_function("localStorage.getItem(PRACTICE_WORD_EXPORT_POINTER_STORAGE_KEY) === null")
        assert page.locator("#practiceWordRecoveryNotice").is_hidden()

        state["status"] = "failed"
        page.evaluate("(jobId) => rememberPracticeWordExportPointer('retry', jobId, '失败后重试.docx')", job_id)
        page.evaluate("resumeRememberedPracticeWordExports()")
        failed_item = page.locator(".practice-word-recovery-item").filter(has_text="失败后重试.docx")
        failed_item.get_by_role("button", name="重新生成").wait_for(timeout=4000)
        assert "SECRET-123" not in failed_item.inner_text()
        assert "Word 生成未完成" in failed_item.inner_text()

        failed_item.get_by_role("button", name="重新生成").click()
        completed_item = page.locator(".practice-word-recovery-item").filter(has_text="失败后重试.docx")
        completed_item.get_by_role("button", name="下载 Word").wait_for(timeout=5000)
        assert state["retry_calls"] == 1
        assert state["download_calls"] == 0

        completed_item.get_by_role("button", name="下载 Word").click()
        page.locator("#platformDialog:not(.hidden)").wait_for(timeout=4000)
        assert page.locator("#platformDialogTitle").inner_text() == "Word 下载失败"
        assert "SECRET-123" not in page.locator("#platformDialogMessage").inner_text()
        page.locator("#platformDialogConfirm").click()
        assert "失败后重试.docx" in page.evaluate("localStorage.getItem(PRACTICE_WORD_EXPORT_POINTER_STORAGE_KEY)")

        state["download_fails"] = False
        completed_item = page.locator(".practice-word-recovery-item").filter(has_text="失败后重试.docx")
        with page.expect_download() as download_info:
            completed_item.get_by_role("button", name="下载 Word").click()
        assert download_info.value.suggested_filename == "失败后重试.docx"
        page.locator("#platformDialog:not(.hidden)").wait_for(timeout=4000)
        page.locator("#platformDialogConfirm").click()
        assert page.evaluate("localStorage.getItem(PRACTICE_WORD_EXPORT_POINTER_STORAGE_KEY)") is None
        assert state["download_calls"] == 2
        browser.close()


def test_two_pages_cannot_silently_overwrite_the_same_practice_question() -> None:
    base_url = os.getenv("ANSWER_BOOK_E2E_URL", "").strip()
    if not base_url:
        pytest.skip("set ANSWER_BOOK_E2E_URL to an already running local platform")

    playwright = pytest.importorskip("playwright.sync_api")
    with playwright.sync_playwright() as runtime:
        launch_options = {} if Path(runtime.chromium.executable_path).is_file() else {"channel": "chrome"}
        browser = runtime.chromium.launch(headless=True, **launch_options)
        context = browser.new_context(viewport={"width": 1440, "height": 1000})
        first_page = context.new_page()
        second_page = context.new_page()
        first_page.goto(base_url, wait_until="networkidle")
        second_page.goto(base_url, wait_until="networkidle")
        history_id = first_page.evaluate(
            """async () => {
              const record = await PlatformApi.request('/api/practice/history', {
                method: 'POST',
                body: JSON.stringify({
                  data: {exercises: [{number: 1, question_type: '简答题', difficulty: '进阶', stem: '原题'}]},
                  change_reason: 'e2e_conflict_fixture'
                })
              });
              return record.history_id;
            }"""
        )

        async_load_and_open = """async (historyId) => {
          const record = await PlatformApi.request(`/api/practice/history/${encodeURIComponent(historyId)}`);
          goToPage('practice');
          currentPracticeHistoryId = historyId;
          currentPracticeRevisionCount = Number(record.revision_count || 0);
          latestPracticeSet = record.data;
          latestPracticeRequest = record.request || {};
          openPracticeEditor(0);
        }"""
        try:
            first_page.evaluate(async_load_and_open, history_id)
            second_page.evaluate(async_load_and_open, history_id)

            first_page.locator("#practiceEditStem").fill("第一个页面先保存的内容")
            first_page.locator("#practiceEditorSave").click()
            first_page.locator("#practiceEditor").wait_for(state="hidden")

            second_page.locator("#practiceEditStem").fill("第二个旧页面中的草稿")
            second_page.locator("#practiceEditorSave").click()
            second_page.locator("#practiceEditorError:not(.hidden)").wait_for()
            assert "当前填写内容仍保留在编辑框中" in second_page.locator("#practiceEditorError").inner_text()
            assert second_page.locator("#practiceEditStem").input_value() == "第二个旧页面中的草稿"
            assert second_page.locator("#practiceEditorSave").is_disabled()

            latest_stem = first_page.evaluate(
                """async (historyId) => {
                  const record = await PlatformApi.request(`/api/practice/history/${encodeURIComponent(historyId)}`);
                  return record.data.exercises[0].stem;
                }""",
                history_id,
            )
            assert latest_stem == "第一个页面先保存的内容"

            second_page.locator('#practiceEditor button[value="cancel"]').first.click()
            second_page.evaluate("openPracticeEditor(0)")
            assert second_page.locator("#practiceEditStem").input_value() == "第一个页面先保存的内容"
            second_page.locator('#practiceEditor button[value="cancel"]').first.click()

            first_page.evaluate("openPracticeEditor(0)")
            first_page.locator("#practiceEditStem").fill("服务器随后保存的最新内容")
            first_page.locator("#practiceEditorSave").click()
            first_page.locator("#practiceEditor").wait_for(state="hidden")

            second_page.evaluate(
                """async () => {
                  platformPrompt = async () => '';
                  regeneratePracticeExercise = async () => ({
                    exercise: {number: 1, question_type: '简答题', difficulty: '进阶', stem: '已经生成但尚未应用的候选题'},
                    semantic_review: {status: 'passed', items: [{number: 1, status: 'passed', risks: []}]}
                  });
                  const button = document.createElement('button');
                  button.id = 'e2eRegenerateButton';
                  document.body.appendChild(button);
                  await regeneratePracticeQuestion(0, button);
                }"""
            )
            second_page.locator("#practiceEditor").wait_for(state="visible")
            assert second_page.locator("#practiceEditStem").input_value() == "已经生成但尚未应用的候选题"
            assert "候选内容已保留在编辑框中" in second_page.locator("#practiceEditorError").inner_text()

            second_page.reload(wait_until="networkidle")
            second_page.evaluate(async_load_and_open, history_id)
            assert second_page.locator("#practiceEditStem").input_value() == "已经生成但尚未应用的候选题"
            assert "已恢复上次未应用的生成候选" in second_page.locator("#practiceEditorError").inner_text()

            second_page.locator("#practiceEditorDiscardDraft").click()
            assert second_page.locator("#practiceEditStem").input_value() == "服务器随后保存的最新内容"
            second_page.locator("#practiceEditStem").fill("刷新和断网后仍应恢复的手工草稿")
            second_page.wait_for_timeout(350)
            second_page.locator('#practiceEditor button[value="cancel"]').first.click()
            second_page.evaluate("openPracticeEditor(0)")
            assert second_page.locator("#practiceEditStem").input_value() == "刷新和断网后仍应恢复的手工草稿"
            second_page.reload(wait_until="networkidle")
            second_page.evaluate(async_load_and_open, history_id)
            assert second_page.locator("#practiceEditStem").input_value() == "刷新和断网后仍应恢复的手工草稿"
            assert "已恢复上次未保存的编辑内容" in second_page.locator("#practiceEditorError").inner_text()

            exercise_route = "**/api/practice/history/*/exercise"
            second_page.route(exercise_route, lambda route: route.abort())
            second_page.locator("#practiceEditStem").fill("短暂断网时保留的内容")
            second_page.locator("#practiceEditorSave").click()
            second_page.locator("#practiceEditorError:not(.hidden)").wait_for()
            assert second_page.locator("#practiceEditor").is_visible()
            second_page.unroute(exercise_route)
            second_page.reload(wait_until="networkidle")
            second_page.evaluate(async_load_and_open, history_id)
            assert second_page.locator("#practiceEditStem").input_value() == "短暂断网时保留的内容"
            second_page.locator("#practiceEditorSave").click()
            second_page.locator("#practiceEditor").wait_for(state="hidden")
            second_page.evaluate("openPracticeEditor(0)")
            assert second_page.locator("#practiceEditStem").input_value() == "短暂断网时保留的内容"
            assert second_page.locator("#practiceEditorDiscardDraft").is_hidden()
        finally:
            first_page.evaluate(
                """async (historyId) => PlatformApi.request(
                  `/api/practice/history/${encodeURIComponent(historyId)}/delete`,
                  {method: 'POST', body: '{}'}
                )""",
                history_id,
            )
            browser.close()


def test_pre_generation_inputs_scope_and_blueprint_survive_reload_without_cross_mode_leakage() -> None:
    base_url = os.getenv("ANSWER_BOOK_E2E_URL", "").strip()
    if not base_url:
        pytest.skip("set ANSWER_BOOK_E2E_URL to an already running local platform")

    playwright = pytest.importorskip("playwright.sync_api")
    with playwright.sync_playwright() as runtime:
        launch_options = {} if Path(runtime.chromium.executable_path).is_file() else {"channel": "chrome"}
        browser = runtime.chromium.launch(headless=True, **launch_options)
        page = browser.new_page(viewport={"width": 1440, "height": 1000})
        page.goto(base_url, wait_until="networkidle")

        page.evaluate("openPracticeEntry('exam')")
        page.locator("#practiceQuestionText").fill("刷新后仍应保留的原题材料")
        page.evaluate(
            """() => {
              document.getElementById('practiceFocus').value = '保留题生题专项要求';
              document.getElementById('practiceFocus').dispatchEvent(new Event('input', {bubbles: true}));
              const type = document.querySelector('input[name="practiceQuestionType"][value="计算题"]');
              type.checked = true;
              type.dispatchEvent(new Event('change', {bubbles: true}));
              practiceSourceFiles = [{name: '原题附件.txt', type: 'text/plain', size: 6, data_url: 'data:text/plain;base64,5Y6f6aKY'}];
              schedulePracticeWorkspaceDraftSave('exam');
            }"""
        )
        page.wait_for_timeout(500)
        page.reload(wait_until="networkidle")
        page.evaluate("openPracticeEntry('exam')")
        page.locator("#practiceWorkspaceDraftNotice:not(.hidden)").wait_for()
        assert page.locator("#practiceQuestionText").input_value() == "刷新后仍应保留的原题材料"
        assert page.locator("#practiceFocus").input_value() == "保留题生题专项要求"
        assert page.locator('input[name="practiceQuestionType"][value="计算题"]').is_checked()
        assert page.evaluate("practiceSourceFiles[0]?.name") == "原题附件.txt"

        page.evaluate("openKnowledgeEntry()")
        page.locator("#knowledgeTitleInput").fill("知识点独立草稿")
        page.locator("#knowledgeTextInput").fill("知识点模式不能覆盖题生题模式的内容")
        page.evaluate(
            """() => {
              document.getElementById('knowledgeFocusInput').value = '保留知识点出题要求';
              document.getElementById('knowledgeFocusInput').dispatchEvent(new Event('input', {bubbles: true}));
              const type = document.querySelector('input[name="knowledgeQuestionType"][value="简答题"]');
              type.checked = true;
              type.dispatchEvent(new Event('change', {bubbles: true}));
              knowledgeSourceFiles = [{name: '知识附件.txt', type: 'text/plain', size: 6, data_url: 'data:text/plain;base64,55+l6K+G'}];
              schedulePracticeWorkspaceDraftSave('knowledge');
            }"""
        )
        page.wait_for_timeout(500)
        page.evaluate("openPracticeEntry('exam')")
        page.locator("#practiceWorkspaceDraftNotice:not(.hidden)").wait_for()
        assert page.locator("#practiceQuestionText").input_value() == "刷新后仍应保留的原题材料"
        page.evaluate("openKnowledgeEntry()")
        page.locator("#knowledgeWorkspaceDraftNotice:not(.hidden)").wait_for()
        assert page.locator("#knowledgeTitleInput").input_value() == "知识点独立草稿"
        assert page.locator("#knowledgeTextInput").input_value() == "知识点模式不能覆盖题生题模式的内容"
        assert page.evaluate("knowledgeSourceFiles[0]?.name") == "知识附件.txt"

        page.evaluate("openPracticeEntry('exam')")
        page.locator("#practiceWorkspaceDraftNotice:not(.hidden)").wait_for()
        page.evaluate(
            """() => {
              latestPracticeRequest = {
                source_mode: 'exam', question_text: '刷新后仍应保留的原题材料', source_files: practiceSourceFiles,
                count: 5, difficulty: '基础到进阶', question_types: ['计算题'], focus: '范围前要求',
                blueprint_review_enabled: true, include_source_content_in_generation: true
              };
              renderPracticeSourceSelection({
                source_analysis: {subject: '测试学科', knowledge_points: ['甲', '乙']},
                source_scope: {title: '测试范围', granularity: 'atomic', questions: [
                  {source_question_id: 'q1', number: '1', title: '第一项', stem_excerpt: '甲', question_type: '计算题', knowledge_points: ['甲']},
                  {source_question_id: 'q2', number: '2', title: '第二项', stem_excerpt: '乙', question_type: '简答题', knowledge_points: ['乙']}
                ]}
              });
            }"""
        )
        page.evaluate(
            """() => {
              const source = document.querySelector('input[name="practiceSourceQuestion"][value="q2"]');
              source.checked = false;
              source.dispatchEvent(new Event('change', {bubbles: true}));
            }"""
        )
        page.locator("#practiceTargetedCount").fill("7")
        page.locator("#practiceScopeFocus").fill("刷新后保留范围参数")
        page.wait_for_timeout(500)
        page.reload(wait_until="networkidle")
        page.evaluate("openPracticeEntry('exam')")
        page.locator("#practiceScopeDrawer:not(.hidden)").wait_for()
        assert page.locator('input[name="practiceSourceQuestion"][value="q1"]').is_checked()
        assert not page.locator('input[name="practiceSourceQuestion"][value="q2"]').is_checked()
        assert page.locator("#practiceTargetedCount").input_value() == "7"
        assert page.locator("#practiceScopeFocus").input_value() == "刷新后保留范围参数"

        page.evaluate(
            """() => {
              latestPracticePlan = {
                source_mode: 'exam', source_analysis: latestPracticeSourceAnalysis, source_scope: latestPracticeSourceScope,
                selected_source_questions: latestPracticeSourceScope.questions.slice(0, 1),
                blueprint: {training_goal: '原训练目标', generation_strategy: 'targeted_set', exercise_plan: [{
                  number: 1, plan_item_id: 'plan_item_01', source_question_id: 'q1', source_refs: ['q1'],
                  question_type: '计算题', difficulty: '进阶', target_skill: '原目标能力',
                  variation_type: '条件变化', structural_change: '条件变化', design_intent: '原设计意图',
                  required_knowledge_points: ['甲']
                }]},
                blueprint_audit: {status: 'passed', errors: [], warnings: []}
              };
              renderPracticePlan(latestPracticePlan);
            }"""
        )
        page.locator("#practicePlanGoalInput").fill("刷新后保留的训练目标")
        page.locator('[data-plan-field="target_skill"]').fill("刷新后保留的目标能力")
        page.evaluate(
            """() => {
              practicePlanDrafts.plan_item_01 = {
                adopted: true, quality: {status: 'passed', warnings: []},
                draft: {number: 1, question_type: '计算题', difficulty: '进阶', stem: '已付费生成并采用的蓝图题目草案'}
              };
              schedulePracticeWorkspaceDraftSave('exam');
            }"""
        )
        page.wait_for_timeout(500)
        page.reload(wait_until="networkidle")
        page.evaluate("openPracticeEntry('exam')")
        page.locator("#practicePlanReview:not(.hidden)").wait_for()
        assert page.locator("#practicePlanGoalInput").input_value() == "刷新后保留的训练目标"
        assert page.locator('[data-plan-field="target_skill"]').input_value() == "刷新后保留的目标能力"
        assert "已付费生成并采用的蓝图题目草案" in page.locator("[data-plan-draft-card]").inner_text()
        assert "已采用" in page.locator("[data-plan-draft-card]").inner_text()

        page.locator("#practiceWorkspaceDraftClearActive").click()
        page.locator("#practiceQuestionText").wait_for(state="visible")
        assert page.locator("#practiceQuestionText").input_value() == ""
        browser.close()


def test_practice_job_refresh_recovery_never_steals_navigation() -> None:
    base_url = os.getenv("ANSWER_BOOK_E2E_URL", "").strip()
    if not base_url:
        pytest.skip("set ANSWER_BOOK_E2E_URL to an already running local platform")

    playwright = pytest.importorskip("playwright.sync_api")
    with playwright.sync_playwright() as runtime:
        launch_options = {} if Path(runtime.chromium.executable_path).is_file() else {"channel": "chrome"}
        browser = runtime.chromium.launch(headless=True, **launch_options)
        context = browser.new_context(viewport={"width": 1440, "height": 1000})
        page = context.new_page()
        jobs = {
            "recovery-job": _practice_recovery_job("recovery-job", "running", "刷新恢复任务"),
        }

        def route_practice_job(route) -> None:
            job_id = route.request.url.split("/api/practice/jobs/", 1)[1].split("?", 1)[0]
            route.fulfill(status=200, content_type="application/json", body=json.dumps(jobs[job_id], ensure_ascii=False))

        context.route("**/api/practice/jobs/**", route_practice_job)
        page.goto(base_url, wait_until="networkidle")
        page.evaluate("localStorage.setItem('activePracticeJobId', 'recovery-job')")

        for _ in range(2):
            page.reload(wait_until="networkidle")
            page.locator("#practiceRecoveryNotice:not(.hidden)").wait_for(timeout=4000)
            assert page.locator("#practiceRecoveryNotice").count() == 1
            assert page.locator("#practiceRecoveryEyebrow").inner_text() == "已恢复进行中的任务"
            assert page.locator("#practiceRecoveryTitle").inner_text() == "刷新恢复任务"
            assert page.locator("#page-home.active").is_visible()

        page.locator("#practiceRecoveryStayBtn").click()
        assert page.locator("#practiceRecoveryNotice").is_hidden()
        page.evaluate("goToPage('tasks')")
        page.locator("#page-tasks.active").wait_for()

        jobs["recovery-job"]["status"] = "completed"
        page.locator("#practiceRecoveryNotice:not(.hidden)").wait_for(timeout=4000)
        assert page.locator("#practiceRecoveryEyebrow").inner_text() == "后台任务已完成"
        assert page.locator("#practiceRecoveryOpenBtn").inner_text() == "查看结果"
        assert page.locator("#page-tasks.active").is_visible()
        assert page.evaluate("localStorage.getItem('activePracticeJobId')") is None

        page.locator("#practiceRecoveryOpenBtn").click()
        page.locator("#page-practice.active").wait_for(timeout=4000)
        page.locator("#practiceScopeDrawer:not(.hidden)").wait_for(timeout=4000)
        assert "刷新恢复任务" in page.locator("#practiceScopeDrawer").inner_text()
        browser.close()


def test_terminal_recovery_and_stale_callbacks_preserve_the_current_page() -> None:
    base_url = os.getenv("ANSWER_BOOK_E2E_URL", "").strip()
    if not base_url:
        pytest.skip("set ANSWER_BOOK_E2E_URL to an already running local platform")

    playwright = pytest.importorskip("playwright.sync_api")
    with playwright.sync_playwright() as runtime:
        launch_options = {} if Path(runtime.chromium.executable_path).is_file() else {"channel": "chrome"}
        browser = runtime.chromium.launch(headless=True, **launch_options)
        context = browser.new_context(viewport={"width": 1440, "height": 1000})
        page = context.new_page()
        jobs = {
            "old-job": _practice_recovery_job("old-job", "running", "旧恢复任务"),
            "new-job": _practice_recovery_job("new-job", "running", "新恢复任务"),
            "failed-job": _practice_recovery_job("failed-job", "failed", "失败恢复任务"),
            "cancelled-job": _practice_recovery_job("cancelled-job", "cancelled", "取消恢复任务"),
        }

        def route_practice_job(route) -> None:
            job_id = route.request.url.split("/api/practice/jobs/", 1)[1].split("?", 1)[0]
            route.fulfill(status=200, content_type="application/json", body=json.dumps(jobs[job_id], ensure_ascii=False))

        context.route("**/api/practice/jobs/**", route_practice_job)
        page.goto(base_url, wait_until="networkidle")
        page.evaluate("localStorage.setItem('activePracticeJobId', 'old-job')")
        page.reload(wait_until="networkidle")
        page.locator("#practiceRecoveryNotice:not(.hidden)").wait_for(timeout=4000)
        assert page.locator("#practiceRecoveryTitle").inner_text() == "旧恢复任务"

        page.evaluate("openKnowledgeEntry()")
        page.locator("#page-knowledge.active").wait_for()
        assert page.locator("#practiceRecoveryNotice").is_hidden()
        page.evaluate("localStorage.setItem('activePracticeJobId', 'new-job'); void resumeRememberedPracticeJob()")
        page.locator("#practiceRecoveryNotice:not(.hidden)").wait_for(timeout=4000)
        assert page.locator("#practiceRecoveryTitle").inner_text() == "新恢复任务"

        jobs["old-job"]["status"] = "completed"
        page.wait_for_timeout(1600)
        assert page.locator("#practiceRecoveryTitle").inner_text() == "新恢复任务"
        assert page.locator("#page-knowledge.active").is_visible()

        jobs["new-job"]["status"] = "completed"
        page.locator("#practiceRecoveryOpenBtn").filter(has_text="查看结果").wait_for(timeout=4000)
        assert page.locator("#practiceRecoveryTitle").inner_text() == "新恢复任务"
        assert page.locator("#page-knowledge.active").is_visible()

        for job_id, expected_page in (("failed-job", "knowledge"), ("cancelled-job", "knowledge")):
            page.evaluate("jobId => localStorage.setItem('activePracticeJobId', jobId)", job_id)
            page.reload(wait_until="networkidle")
            page.locator("#practiceRecoveryNotice:not(.hidden)").wait_for(timeout=4000)
            assert page.locator("#page-home.active").is_visible()
            assert page.locator("#practiceRecoveryOpenBtn").inner_text() == "查看详情"
            assert page.evaluate("localStorage.getItem('activePracticeJobId')") is None
            page.locator("#practiceRecoveryOpenBtn").click()
            page.locator(f"#page-{expected_page}.active").wait_for(timeout=4000)
            assert "任务未完成测试说明" in page.locator("#knowledgeError").inner_text()

        browser.close()


def test_provider_configuration_failure_has_safe_consistent_copy_and_recovery_actions() -> None:
    base_url = os.getenv("ANSWER_BOOK_E2E_URL", "").strip()
    if not base_url:
        pytest.skip("set ANSWER_BOOK_E2E_URL to an already running local platform")

    playwright = pytest.importorskip("playwright.sync_api")
    with playwright.sync_playwright() as runtime:
        launch_options = {} if Path(runtime.chromium.executable_path).is_file() else {"channel": "chrome"}
        browser = runtime.chromium.launch(headless=True, **launch_options)
        context = browser.new_context(viewport={"width": 1440, "height": 1000})
        page = context.new_page()
        job_id = "generation_provider_config_failure"
        raw_error = (
            'Provider HTTP 404: {"code":"InvalidEndpointOrModel.NotFound",'
            '"request_id":"req-must-not-be-visible"}'
        )
        presentation = {
            "kind": "provider_target_not_found",
            "title": "模型服务配置不匹配",
            "message": "所选模型名称、Endpoint 或可用区域可能不匹配，模型服务未找到可用目标。",
            "retry_hint": "请打开 API 配置，核对模型名称、Endpoint 和可用区域；连接验证成功后再重试。",
            "support_id": "PJ-E2ESAFE001",
        }
        job = _practice_recovery_job(job_id, "failed", "配置恢复测试")
        job.update({
            "error": raw_error,
            "error_presentation": presentation,
            "support_id": presentation["support_id"],
        })
        task = {
            "task_id": job_id,
            "task_kind": "knowledge",
            "practice_batch_id": "batch-provider-config",
            "is_generation_task": True,
            "is_generation_job": True,
            "operation": "analyze",
            "display_title": "知识点出题 · 配置恢复测试",
            "description": "配置恢复测试",
            "exam_path": "配置恢复测试",
            "provider": "ark",
            "model": "invalid-model",
            "status": "failed",
            "current_stage": "failed",
            "created_at": "2026-08-23T10:00:00+08:00",
            "updated_at": "2026-08-23T10:01:00+08:00",
            "error": presentation["message"],
            "error_presentation": presentation,
            "support_id": presentation["support_id"],
            "progress_percent": 100,
            "steps": [{"operation": "analyze", "status": "failed"}],
            "health": {"health_status": "error", "warning_reason": presentation["message"]},
            "capabilities": {"view_detail": True, "view_quality": True, "retry": True},
        }

        context.route(
            "**/api/practice/jobs/**",
            lambda route: route.fulfill(status=200, content_type="application/json", body=json.dumps(job, ensure_ascii=False)),
        )
        context.route(
            "**/api/tasks",
            lambda route: route.fulfill(
                status=200,
                content_type="application/json",
                body=json.dumps({"tasks": [task], "schema_version": 1}, ensure_ascii=False),
            ),
        )

        page.goto(base_url, wait_until="networkidle")
        page.evaluate("jobId => localStorage.setItem('activePracticeJobId', jobId)", job_id)
        page.reload(wait_until="networkidle")
        page.locator("#practiceRecoveryNotice:not(.hidden)").wait_for(timeout=4000)
        recovery_copy = page.locator("#practiceRecoveryNotice").inner_text()
        assert presentation["message"] in recovery_copy
        assert presentation["retry_hint"] in recovery_copy
        assert presentation["support_id"] in recovery_copy
        assert "InvalidEndpointOrModel" not in recovery_copy
        assert "req-must-not-be-visible" not in recovery_copy
        assert page.locator("#page-home.active").is_visible()

        page.locator("#practiceRecoveryOpenBtn").click()
        page.locator("#page-knowledge.active").wait_for(timeout=4000)
        detail_copy = page.locator("#knowledgeError").inner_text()
        assert presentation["message"] in detail_copy
        assert presentation["retry_hint"] in detail_copy
        assert presentation["support_id"] in detail_copy
        assert "InvalidEndpointOrModel" not in detail_copy

        page.evaluate("openTaskManager('knowledge')")
        page.locator("#page-tasks.active").wait_for(timeout=4000)
        card = page.locator("#taskManagerList .task-manager-item").filter(has_text="配置恢复测试")
        task_copy = card.inner_text()
        assert presentation["message"] in task_copy
        assert presentation["retry_hint"] in task_copy
        assert presentation["support_id"] in task_copy
        assert "InvalidEndpointOrModel" not in task_copy
        assert card.locator('[data-action="job-config"]').is_visible()
        assert card.locator('[data-action="job-retry"]').is_visible()

        card.locator('[data-action="job-config"]').click()
        page.locator("#page-keys.active").wait_for(timeout=4000)
        page.evaluate("openTaskManager('knowledge')")
        card = page.locator("#taskManagerList .task-manager-item").filter(has_text="配置恢复测试")
        card.locator('[data-action="job-retry"]').click()
        page.locator("#platformDialog:not(.hidden)").wait_for(timeout=4000)
        assert page.locator("#platformDialogConfirm").inner_text() == "确认重试"
        assert "连接验证成功后再重试" in page.locator("#platformDialogMessage").inner_text()
        page.locator("#platformDialogCancel").click()

        browser.close()


def test_task_manager_tolerates_mixed_error_presentations_and_keeps_terminal_actions_available() -> None:
    base_url = os.getenv("ANSWER_BOOK_E2E_URL", "").strip()
    if not base_url:
        pytest.skip("set ANSWER_BOOK_E2E_URL to an already running local platform")

    playwright = pytest.importorskip("playwright.sync_api")
    with playwright.sync_playwright() as runtime:
        launch_options = {} if Path(runtime.chromium.executable_path).is_file() else {"channel": "chrome"}
        browser = runtime.chromium.launch(headless=True, **launch_options)
        context = browser.new_context(viewport={"width": 1440, "height": 1000})
        page = context.new_page()

        configuration_presentation = {
            "kind": "provider_authentication",
            "title": "模型服务认证失败",
            "message": "API Key 可能无效、已过期，或模型服务未通过认证。",
            "retry_hint": "请打开 API 配置检查 Key。",
            "support_id": "PJ-MIXCONFIG",
        }
        workflow_presentation = {
            "kind": "workflow_failed",
            "title": "任务执行未完成",
            "message": "任务未完成。",
            "retry_hint": "请从检查点重试。",
            "support_id": "PJ-MIXUNKNOWN",
        }

        def job_task(task_id: str, title: str, status: str, presentation, capabilities: dict) -> dict:
            return {
                "task_id": task_id,
                "task_kind": "knowledge",
                "practice_batch_id": f"batch-{task_id}",
                "is_generation_task": True,
                "is_generation_job": True,
                "operation": "analyze",
                "display_title": f"知识点出题 · {title}",
                "description": title,
                "exam_path": title,
                "status": status,
                "current_stage": status,
                "created_at": "2026-08-23T10:00:00+08:00",
                "updated_at": "2026-08-23T10:01:00+08:00",
                "error": "用于混合列表回归的终态说明",
                "error_presentation": presentation,
                "progress_percent": 100,
                "steps": [{"operation": "analyze", "status": status}],
                "health": {"health_status": "error", "warning_reason": "终态记录"},
                "capabilities": capabilities,
            }

        tasks = [
            job_task("cancelled-mixed", "已取消记录", "cancelled", None, {"retry": True, "delete": True}),
            job_task("config-mixed", "配置错误记录", "failed", configuration_presentation, {"view_quality": True, "retry": True, "delete": True}),
            job_task("unknown-mixed", "未知错误记录", "failed", workflow_presentation, {"view_quality": True, "retry": True, "delete": True}),
            job_task("malformed-mixed", "异常字段记录", "failed", "malformed", {"view_quality": True, "retry": True, "delete": True}),
            {
                "task_id": "issues-mixed",
                "task_kind": "knowledge",
                "is_generation_task": True,
                "is_generation_job": False,
                "display_title": "知识点出题 · 完成待复核记录",
                "description": "完成待复核记录",
                "exam_path": "完成待复核记录",
                "status": "completed_with_issues",
                "current_stage": "completed",
                "created_at": "2026-08-23T09:00:00+08:00",
                "updated_at": "2026-08-23T09:01:00+08:00",
                "error": "",
                "error_presentation": None,
                "progress_percent": 100,
                "capabilities": {"view_result": True, "reuse": True, "delete": True},
            },
        ]
        deleted_ids: set[str] = set()

        def route_tasks(route) -> None:
            if route.request.method == "POST":
                payload = json.loads(route.request.post_data or "{}")
                deleted_ids.update(str(item) for item in payload.get("task_ids") or [])
                route.fulfill(
                    status=200,
                    content_type="application/json",
                    body=json.dumps({"ok": True, "deleted": len(deleted_ids), "failed": 0, "results": []}),
                )
                return
            visible = [task for task in tasks if task["task_id"] not in deleted_ids]
            route.fulfill(
                status=200,
                content_type="application/json",
                body=json.dumps({"tasks": visible, "schema_version": 1}, ensure_ascii=False),
            )

        cancelled_job = _practice_recovery_job("cancelled-mixed", "cancelled", "已取消记录")
        context.route("**/api/tasks", route_tasks)
        context.route("**/api/tasks/bulk-delete", route_tasks)
        context.route(
            "**/api/practice/jobs/cancelled-mixed?detail=1",
            lambda route: route.fulfill(
                status=200,
                content_type="application/json",
                body=json.dumps(cancelled_job, ensure_ascii=False),
            ),
        )

        page.goto(base_url, wait_until="networkidle")
        assert page.evaluate(
            """() => [
              practiceErrorNeedsConfiguration(null),
              practiceErrorNeedsConfiguration(undefined),
              practiceErrorNeedsConfiguration({}),
              practiceErrorNeedsConfiguration({kind: 'provider_authentication'}),
              practiceErrorNeedsConfiguration({kind: 'cancelled'}),
              practiceErrorNeedsConfiguration({kind: 'workflow_failed'}),
              practiceErrorNeedsConfiguration('malformed')
            ]"""
        ) == [False, False, False, True, False, False, False]

        page.evaluate("openTaskManager('knowledge')")
        page.locator("#page-tasks.active").wait_for(timeout=4000)
        cards = page.locator("#taskManagerList .task-manager-item")
        assert cards.count() == 5

        cancelled_card = cards.filter(has_text="已取消记录")
        config_card = cards.filter(has_text="配置错误记录")
        malformed_card = cards.filter(has_text="异常字段记录")
        assert cancelled_card.locator('[data-action="job-retry"]').is_visible()
        assert cancelled_card.locator('[data-action="job-config"]').count() == 0
        assert config_card.locator('[data-action="job-config"]').is_visible()
        assert malformed_card.locator('[data-action="job-config"]').count() == 0

        cancelled_card.locator('[data-action="job-retry"]').click()
        page.locator("#platformDialog:not(.hidden)").wait_for(timeout=4000)
        assert page.locator("#platformDialogConfirm").inner_text() == "确认重试"
        page.locator("#platformDialogCancel").click()

        page.locator("#taskBulkModeBtn").click()
        cancelled_card = page.locator("#taskManagerList .task-manager-item").filter(has_text="已取消记录")
        cancelled_card.click()
        assert cancelled_card.locator('[data-task-select="cancelled-mixed"]').is_checked()
        page.locator("#taskBulkDeleteBtn").click()
        page.locator("#platformDialog:not(.hidden)").wait_for(timeout=4000)
        page.locator("#platformDialogConfirm").click()
        page.locator("#platformDialog:not(.hidden)").wait_for(timeout=4000)
        page.locator("#platformDialogConfirm").click()
        page.locator("#taskManagerList .task-manager-item").filter(has_text="已取消记录").wait_for(state="detached", timeout=4000)
        assert page.locator("#taskManagerList .task-manager-item").count() == 4
        assert config_card.locator('[data-action="job-config"]').is_visible()

        browser.close()
