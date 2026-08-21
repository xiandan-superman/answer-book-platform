from __future__ import annotations

import os
from pathlib import Path

import pytest

pytestmark = pytest.mark.e2e


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
