from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


class PracticeRedesignTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.html = (ROOT / "web" / "index.html").read_text(encoding="utf-8")
        cls.js = (ROOT / "web" / "app.js").read_text(encoding="utf-8")
        cls.platform_api = (ROOT / "web" / "platform-api.js").read_text(encoding="utf-8")
        cls.styles = (ROOT / "web" / "styles.css").read_text(encoding="utf-8")
        cls.platform_theme = (ROOT / "web" / "platform-theme.css").read_text(encoding="utf-8")
        cls.server = (ROOT / "app" / "server.py").read_text(encoding="utf-8")

    def test_runtime_assets_are_local(self) -> None:
        self.assertNotIn("@tailwindcss/browser", self.html)
        self.assertNotIn("unpkg.com/lucide", self.html)
        self.assertTrue((ROOT / "web" / "practice.generated.css").is_file())
        self.assertTrue((ROOT / "web" / "vendor" / "lucide.min.js").is_file())

    def test_scope_drawer_and_editor_are_present(self) -> None:
        required_ids = (
            "practiceScopeDrawer",
            "practiceSourceQuestionList",
            "practiceSourceConfirmBtn",
            "practiceEditor",
            "practiceEditorSave",
        )
        for element_id in required_ids:
            self.assertIn(f'id="{element_id}"', self.html)

    def test_practice_workflow_is_inline_and_navigation_detaches_current_view(self) -> None:
        self.assertIn("function normalizePracticeInlineLayout()", self.js)
        self.assertIn('grid.classList.add("practice-inline-flow")', self.js)
        self.assertIn('drawer.querySelector(".practice-scope-drawer__panel")?.setAttribute("aria-modal", "false")', self.js)
        self.assertIn('const leavingPractice = currentPage === "practice" && page !== "practice";', self.js)
        self.assertIn('rememberPracticeJob("");', self.js)
        self.assertIn("#page-practice .practice-scope-drawer {\n  position: static;", self.styles)
        self.assertIn("#page-practice .practice-inline-input .sidebar-icons", self.styles)

    def test_source_unit_actions_do_not_inherit_primary_button_skin(self) -> None:
        self.assertEqual(self.js.count('class="text-button practice-source-unit__action"'), 3)
        self.assertIn(
            "#page-practice .practice-source-unit__actions .practice-source-unit__action {",
            self.styles,
        )
        self.assertIn("all: unset;", self.styles)

    def test_knowledge_plan_generation_uses_confirmed_blueprint_strategy(self) -> None:
        self.assertIn('const defaultStrategy = knowledgeMode ? "knowledge_overall" : "targeted_set";', self.js)
        self.assertIn("latestPracticePlan?.blueprint?.generation_strategy", self.js)
        self.assertIn('setPracticeStage("generate")', self.js)
        self.assertIn("题目生成失败，蓝图和已修改内容仍保留", self.js)

    def test_source_material_switch_is_available_and_persisted_with_the_generation_request(self) -> None:
        for element_id in (
            "practiceIncludeSourceContent",
            "knowledgeIncludeSourceContent",
            "practiceScopeIncludeSourceContent",
            "practiceSourceContentWarning",
            "knowledgeSourceContentWarning",
            "practiceScopeSourceContentWarning",
        ):
            self.assertIn(f'id="{element_id}"', self.html)
        self.assertIn("function includeSourceContentInGeneration()", self.js)
        self.assertIn("function syncPracticeSourceContentPreference", self.js)
        self.assertIn("include_source_content_in_generation: includeSourceContentInGeneration()", self.js)
        self.assertIn('include_source_content_in_generation: $("knowledgeIncludeSourceContent")?.checked !== false', self.js)
        self.assertIn("practiceSourceContentToggleIds.forEach", self.js)
        self.assertIn(".practice-source-content-warning", self.platform_theme)

    def test_high_expansion_with_source_material_requires_blueprint_review(self) -> None:
        self.assertIn("function practiceHighExpansionRisk", self.js)
        self.assertIn("totalCount > selectedCount * 2", self.js)
        self.assertIn("需开启蓝图审查", self.js)
        self.assertIn("&& !blueprintReviewEnabled()", self.js)

    def test_blueprint_shows_and_syncs_required_knowledge_point_combinations(self) -> None:
        self.assertIn("function requiredKnowledgePointsForPlanItem", self.js)
        self.assertIn("function syncPlanItemRequiredKnowledgePoints", self.js)
        self.assertIn("required_knowledge_points", self.js)
        self.assertIn("必考知识点", self.js)
        self.assertIn("难度实现", self.js)
        self.assertIn("difficulty_levers", self.js)
        self.assertIn("difficulty_rationale", self.js)
        self.assertIn("function defaultPlanDifficultyDesign", self.js)
        self.assertIn("function ensurePlanDifficultyDesign", self.js)
        self.assertIn("difficulty_design_level", self.js)
        self.assertIn("data-plan-difficulty-levers", self.js)

    def test_blueprint_confirmation_keeps_the_plan_visible_and_reports_failures(self) -> None:
        self.assertIn('id="practicePlanError"', self.html)
        self.assertIn("function showPracticePlanError", self.js)
        self.assertIn('正在生成练习', self.js)
        self.assertIn('setPracticeStatusBanner("正在生成练习", "loading")', self.js)
        start = self.js.index("async function generatePracticeFromPlan()")
        end = self.js.index("async function regeneratePracticeSet()", start)
        self.assertNotIn("expectedDifficultyCounts", self.js[start:end])

    def test_practice_layout_has_stable_wide_and_mobile_flow_rules(self) -> None:
        self.assertIn("grid-template-columns: repeat(5, minmax(0, 1fr));", self.platform_theme)
        self.assertIn("@media (max-width: 767px)", self.platform_theme)
        self.assertIn("grid-template-columns: repeat(2, minmax(0, 1fr));", self.platform_theme)
        self.assertIn("原题变式、依赖图表任务", self.html)
        self.assertNotIn("逐项变式及依赖图表", self.html)

    def test_reused_task_restores_source_content_switch_and_comprehensive_subset(self) -> None:
        self.assertIn("const retained = current.filter((point) => sourcePoints.includes(point));", self.js)
        self.assertIn("return retained.length ? retained : (sourcePoints.length ? sourcePoints : current);", self.js)
        self.assertIn("syncPracticeSourceContentPreference(request.include_source_content_in_generation !== false);", self.js)
        self.assertIn("syncPracticeSourceContentPreference(latestPracticeRequest?.include_source_content_in_generation !== false);", self.js)

    def test_generation_job_card_uses_job_endpoint_and_get_retries_once(self) -> None:
        self.assertIn("if (task.is_generation_job) openGenerationJob(task);", self.js)
        self.assertIn('if (method !== "GET") throw networkError(method);', self.platform_api)
        self.assertIn("await delay(350);", self.platform_api)
        self.assertIn('task.operation === "plan" ? "查看蓝图"', self.js)
        self.assertIn('"operation": record.get("operation") or ""', self.server)

    def test_long_generation_has_plain_language_progress(self) -> None:
        self.assertIn('id="practiceLoadingDetail"', self.html)
        self.assertIn('id="practiceLoadingElapsed"', self.html)
        self.assertIn("function updatePracticeLoadingProgress(job = {})", self.js)
        self.assertIn("function practiceWaitExpectation(job = {})", self.js)
        self.assertIn("同类题量通常 1–3 分钟完成生成", self.js)
        self.assertIn("updatePracticeLoadingProgress(job)", self.js)
        self.assertIn("task.progress_message", self.js)
        self.assertIn(".practice-live-progress {", self.styles)
        jobs = (ROOT / "app" / "practice_jobs.py").read_text(encoding="utf-8")
        generation = (ROOT / "app" / "exercise_generation.py").read_text(encoding="utf-8")
        self.assertIn('"generate_from_plan": bounded_env_int("PRACTICE_GENERATE_JOB_TIMEOUT_SECONDS", 14400', jobs)
        self.assertIn("内容较长，模型仍在生成完整结果", jobs)
        self.assertIn("timeout_seconds=600", generation)

    def test_partial_generation_has_visible_error_cards_and_keeps_export_enabled(self) -> None:
        self.assertIn('item.generation_status === "failed"', self.js)
        self.assertIn("generationErrorDetailCodes", self.js)
        self.assertIn('"generation_response_invalid"', self.js)
        self.assertIn("generationErrorDetailCodes.has", self.js)
        self.assertIn("generationErrorDetail", self.js)
        self.assertIn('题${auditNeedsReview ? "蓝图待复核" : "生成失败"}', self.js)
        self.assertIn("本题尚未调用生成模型", self.js)
        self.assertIn("hasAuditReviewFailures", self.js)
        self.assertIn("完成待复核 · 已生成", self.js)
        self.assertIn("已保留蓝图位置", self.js)
        self.assertIn("setPracticeExportButtonsEnabled(isPassed, data)", self.js)
        self.assertIn("题生成失败", self.js)
        self.assertIn(".practice-exercise--generation-failed", self.styles)
        self.assertIn(".practice-generation-error", self.styles)

    def test_complete_question_set_with_quality_defect_keeps_results_visible_as_repairable(self) -> None:
        self.assertIn("const hasRepairableResults", self.js)
        self.assertIn("题，${blockingIssues.length} 项需修复", self.js)
        self.assertIn("其余结果已保留，可继续查看和编辑", self.js)
        self.assertIn("题目已生成 · 待修复", self.js)

    def test_blueprint_multi_question_switch_is_optional_and_only_advises_on_large_totals(self) -> None:
        self.assertIn('id="practiceBlueprintMultiQuestionEnabled"', self.html)
        self.assertIn('id="practiceBlueprintVariantsPerItem"', self.html)
        self.assertIn('id="practiceBlueprintVariantMode"', self.html)
        self.assertIn("function practiceBlueprintMultiQuestionConfig", self.js)
        self.assertIn("blueprint_multi_question_enabled: multiQuestion.enabled", self.js)
        self.assertIn("题量较大，生成时间、模型费用和配图处理量会明显增加", self.js)
        self.assertNotIn("超过系统上限 30 题", self.js)
        self.assertIn(".practice-variant-link", self.styles)

    def test_generated_question_output_uses_platform_owned_numbering(self) -> None:
        self.assertIn("function normalizePracticeQuestionText(value)", self.js)
        self.assertIn("nextSubquestionNumber", self.js)
        self.assertIn("ASCII 英文括号 (1)", (ROOT / "app" / "exercise_generation.py").read_text(encoding="utf-8"))

    def test_plan_revision_has_structured_constraints_and_visible_mode_contract(self) -> None:
        self.assertIn("function requestPlanRevisionSpec(item)", self.js)
        self.assertIn('["question_type", "题型"]', self.js)
        self.assertIn('["design_intent", "情境、条件或设计意图"]', self.js)
        self.assertIn('must_preserve: ["source_binding", "graduate_level"]', self.js)
        self.assertIn("revision_spec: revisionSpec", self.js)
        self.assertIn("约束门禁已通过", self.js)
        self.assertIn("综合覆盖矩阵", self.js)
        self.assertIn("单项变式链", self.js)
        self.assertIn(".practice-revision-overlay", self.styles)
        self.assertIn(".practice-mode-contract", self.styles)

    def test_task_manager_supports_confirmed_bulk_cleanup(self) -> None:
        self.assertIn('id="taskBulkModeBtn"', self.html)
        self.assertIn('class="text-button task-bulk-delete"', self.html)
        self.assertIn("const selectedTaskIds = new Set()", self.js)
        self.assertIn("async function deleteSelectedTasks()", self.js)
        self.assertIn('await api("/api/tasks/bulk-delete"', self.js)
        self.assertIn("任务记录、输出结果及可确认归属的过程文件将一并删除", self.js)
        self.assertIn("#page-tasks .task-bulk-toolbar .text-button", self.platform_theme)
        self.assertIn('if parsed.path == "/api/tasks/bulk-delete":', self.server)

    def test_word_export_buttons_sync_state_and_show_visible_errors(self) -> None:
        self.assertIn("function setPracticeExportButtonsEnabled(enabled, data = latestPracticeSet)", self.js)
        self.assertIn("setPracticeExportButtonsEnabled(isPassed, data)", self.js)
        self.assertIn("async function prepareOrDownloadPracticeWord", self.js)
        self.assertIn("downloadPracticeWord(blob, filename)", self.js)
        self.assertIn("浏览器已开始下载", self.js)
        self.assertIn("function downloadPracticeWord", self.js)
        self.assertIn('title: "题目 Word 生成失败"', self.js)
        self.assertNotIn("practiceSolutionWordBtn", self.js)

    def test_word_export_progress_survives_practice_page_rerender(self) -> None:
        self.assertIn("const activePracticeWordExports = new Map()", self.js)
        self.assertIn("function practiceWordExportKey(data, filename", self.js)
        self.assertIn("function syncPracticeWordExportUi()", self.js)
        self.assertIn('data-practice-word-export-key="${escapeHtml(wordExportKey)}"', self.js)
        self.assertIn("正在生成 ${count} 份题目 Word；可切换页面，返回后会继续显示进度。", self.js)
        self.assertIn("syncPracticeWordExportUi();", self.js)

    def test_scope_confirmation_button_keeps_legible_enabled_and_disabled_states(self) -> None:
        self.assertIn(
            "#page-practice .practice-scope-drawer__buttons .practice-generate-button:not(:disabled)",
            self.platform_theme,
        )
        self.assertIn("color: #fff !important;", self.platform_theme)
        self.assertIn(".practice-generate-button:disabled", self.platform_theme)
        self.assertIn("background: #e8eef8 !important;", self.platform_theme)

    def test_practice_result_keeps_single_question_copy_and_word_download(self) -> None:
        self.assertNotIn("快速复制整套", self.html)
        self.assertNotIn('id="practiceCopyWordBtn"', self.html)
        self.assertNotIn("复制整套为 Word 格式", self.html)
        self.assertIn('id="practiceDownloadSelectedBtn"', self.html)
        self.assertIn("下载 Word", self.html)
        self.assertIn('data-practice-copy="${idx}"', self.js)
        self.assertNotIn('data-practice-copy-word="${idx}"', self.js)

    def test_practice_result_scopes_header_actions_to_selected_questions(self) -> None:
        self.assertIn('id="practiceSelectionActions"', self.html)
        self.assertIn('id="practiceRegenerateSetBtn"', self.html)
        self.assertIn('id="practiceSelectAllBtn" type="button" class="practice-selection-text"', self.html)
        self.assertIn('>全选</button>', self.html)
        self.assertIn('>清除选择</button>', self.html)
        self.assertEqual(self.html.count('class="practice-selection-action"'), 2)
        self.assertNotIn('id="practiceMoreActionsBtn"', self.html)
        self.assertNotIn('id="practiceQuestionWordBtn"', self.html)
        self.assertIn("async function regenerateSelectedPracticeQuestions", self.js)
        self.assertIn("selectedPracticeExerciseIndexes", self.js)
        self.assertIn('class="practice-question-more-trigger"', self.js)
        self.assertIn('<span>下载本题 Word</span>', self.js)
        self.assertIn("function closePracticeActionMenus(", self.js)
        self.assertIn('if (event.key !== "Escape") return;', self.js)

    def test_practice_generation_exposes_conditional_subject_review_signal(self) -> None:
        self.assertIn("blocking_issues", (ROOT / "app" / "exercise_generation.py").read_text(encoding="utf-8"))
        self.assertIn('"subject_matter_review_required": subject_review_required', (ROOT / "app" / "exercise_generation.py").read_text(encoding="utf-8"))
        self.assertIn('setText("practiceSummaryQuality", "已完成")', self.js)
        self.assertIn("题目已生成 · 待复核", self.js)
        self.assertIn("结果已保留，可查看、编辑或导出草稿", self.js)

    def test_practice_result_uses_compact_disclosure_layout(self) -> None:
        self.assertIn('id="practiceResultContext"', self.html)
        self.assertIn('<details id="practiceBlueprintSummary"', self.html)
        self.assertIn("#page-practice .practice-result-context {", self.platform_theme)
        self.assertIn("min-height: 42px;", self.platform_theme)

    def test_generation_requires_inline_domain_math_and_theta_standard_state(self) -> None:
        generation = (ROOT / "app" / "exercise_generation.py").read_text(encoding="utf-8")
        self.assertIn("化学式、离子、电极/电池表示法、反应式及热力学符号", generation)
        self.assertIn("标准态上标统一使用", generation)
        self.assertIn("禁止用字母 `o/O`", generation)
        self.assertIn("'Cambria Math'", self.js)
        self.assertIn("'宋体'", self.js)
        self.assertNotIn('id="practiceCopySelectedWordBtn"', self.html)

    def test_config_uses_native_details_toggle_state(self) -> None:
        self.assertIn('addEventListener("toggle", syncPracticeConfigState)', self.js)
        self.assertNotIn('practiceConfigToggle")?.addEventListener("click"', self.js)
        self.assertIn("card.open = forceOpen", self.js)

    def test_count_and_exact_difficulty_are_owned_by_scope_confirmation(self) -> None:
        self.assertNotIn('id="practiceCount"', self.html)
        self.assertNotIn('id="practiceDifficulty"', self.html)
        self.assertIn('id="practiceTargetedCount"', self.html)
        self.assertIn('id="practiceDifficultyBasicCount"', self.html)
        self.assertIn('id="practiceDifficultyIntermediateCount"', self.html)
        self.assertIn('id="practiceDifficultyChallengeCount"', self.html)

    def test_question_entry_matches_the_two_column_generation_flow(self) -> None:
        self.assertIn('id="practiceWorkspaceTitle"', self.html)
        self.assertIn('>按题生题</h1>', self.html)
        self.assertIn('class="practice-entry-guide"', self.html)
        self.assertIn('class="practice-next-step-card"', self.html)
        self.assertIn('id="practiceModelSettingsLink"', self.html)
        self.assertNotIn('id="practiceTextTabLabel"', self.html)
        self.assertNotIn('id="practiceFileTabLabel"', self.html)
        self.assertIn('overview.append(heading)', self.js)
        self.assertIn('overview.append(stage)', self.js)
        self.assertIn('id="practiceWorkflowActions"', self.html)
        self.assertIn('#page-practice .practice-inline-input .sidebar-full {', self.platform_theme)
        self.assertIn('grid-template-areas:\n    "source next"', self.platform_theme)

    def test_sidebar_binding_is_not_added_during_page_switch(self) -> None:
        function_body = self.js.split("function updateStepIndicator(page) {", 1)[1].split("\n}", 1)[0]
        self.assertNotIn("practiceSidebarCollapse", function_body)

    def test_api_keys_have_one_central_configuration_page(self) -> None:
        self.assertIn('id="page-keys"', self.html)
        self.assertIn('id="keyProviderGrid"', self.html)
        self.assertIn('data-key-test=', self.js)
        self.assertIn('data-key-save=', self.js)
        self.assertNotIn('id="providerKeyInput"', self.html)
        self.assertNotIn('id="openApiKeyFileBtn"', self.html)
        self.assertNotIn('id="saveProviderKeysBtn"', self.html)

    def test_homepage_has_two_business_areas_and_global_navigation(self) -> None:
        self.assertIn("选择任务，开始工作", self.html)
        self.assertIn('class="business-domain-card exam-domain"', self.html)
        self.assertIn('class="business-domain-card generation-domain"', self.html)
        self.assertIn('src="/assets/home/exam-evidence.png"', self.html)
        self.assertIn('src="/assets/home/generation-network.png"', self.html)
        self.assertIn("教材证据可追溯", self.html)
        self.assertIn("解析与出题一体化", self.html)
        self.assertIn("任务统一管理", self.html)
        self.assertIn('onclick="startWizard()"', self.html)
        self.assertIn('onclick="openPracticeEntry(\'exam\')"', self.html)
        self.assertIn('onclick="openKnowledgeEntry()"', self.html)
        self.assertIn('onclick="goToPage(\'practice-models\')"', self.html)
        self.assertIn('onclick="goToPage(\'knowledge-models\')"', self.html)
        self.assertNotIn('class="home-api-key-card"', self.html)

    def test_platform_theme_is_loaded_after_page_local_styles(self) -> None:
        self.assertIn('<link rel="stylesheet" href="/platform-theme.css?v=', self.html)
        self.assertGreater(
            self.html.index('<link rel="stylesheet" href="/platform-theme.css?v='),
            self.html.rindex("</style>"),
        )
        self.assertIn('body[data-active-page="home"]', self.platform_theme)
        self.assertIn("#page-home", self.platform_theme)
        self.assertIn("background: transparent", self.platform_theme)

    def test_page_switch_updates_visual_state_and_strengths_animate(self) -> None:
        self.assertIn("document.body.dataset.activePage = page", self.js)
        self.assertIn('".platform-strength-grid article"', self.js)
        self.assertIn("@keyframes platformStrengthFloat", self.platform_theme)
        self.assertIn(".platform-strength-grid article:hover", self.platform_theme)

    def test_sticky_next_action_is_never_hidden_by_reveal_animation(self) -> None:
        self.assertIn('".page-actions:not(.sticky-page-actions)"', self.js)
        self.assertIn('querySelectorAll(".sticky-page-actions")', self.js)
        self.assertIn('el.classList.remove("reveal", "reveal-left", "reveal-right", "reveal-scale")', self.js)
        self.assertIn(".sticky-page-actions {\n  bottom: 16px;\n  opacity: 1;", self.platform_theme)

    def test_model_controls_remain_readable_in_ultrawide_chrome_viewports(self) -> None:
        self.assertIn("@media (min-width: 1900px)", self.platform_theme)
        self.assertIn(".task-model-grid select,\n  .task-model-grid input", self.platform_theme)
        self.assertIn("min-height: 56px;\n    font-size: 17px;", self.platform_theme)
        self.assertIn("#page-practice-models .reference-medium", self.platform_theme)
        self.assertIn("#page-knowledge-models .reference-medium", self.platform_theme)
        self.assertIn("max-width: 2320px;", self.platform_theme)
        self.assertIn("max-width: 2080px;", self.platform_theme)

    def test_homepage_uses_fluid_width_and_stable_type_scale(self) -> None:
        self.assertIn("width: min(100%, 1360px);", self.platform_theme)
        self.assertIn("font-size: clamp(40px, 3.2vw, 58px);", self.platform_theme)
        self.assertIn("min-height: clamp(420px, 28vw, 500px);", self.platform_theme)

    def test_practice_workspace_is_fluid_and_keeps_text_readable(self) -> None:
        self.assertIn("width: min(calc(100% - 48px), 2080px);", self.platform_theme)
        self.assertIn("grid-template-columns: clamp(360px, 26vw, 440px) minmax(0, 1fr);", self.platform_theme)
        self.assertIn("#page-practice .practice-current-model-strip {", self.platform_theme)
        self.assertIn("white-space: normal;", self.platform_theme)
        self.assertIn("#page-practice .practice-stem", self.platform_theme)
        self.assertIn("font-size: clamp(16px, 1.1vw, 18px);", self.platform_theme)

    def test_practice_filter_tags_are_deduplicated_and_progressively_disclosed(self) -> None:
        self.assertIn("function uniquePracticeLabels(values)", self.js)
        self.assertIn("function renderPracticeFilterGroup(", self.js)
        self.assertIn("practice-filter-chip--overflow", self.js)
        self.assertIn("展开其余 ${remaining} 项", self.js)
        self.assertIn(".practice-filter-chip--overflow", self.platform_theme)
        self.assertIn(".is-expanded > .practice-filter-chip--overflow", self.platform_theme)
        self.assertIn("const visibleTags = tagsArr.slice(0, 4);", self.js)

    def test_task_execution_navigation_uses_translucent_glass(self) -> None:
        self.assertIn('body[data-active-page="task"] .app-nav', self.platform_theme)
        self.assertIn("blur(19px) saturate(180%) brightness(1.04)", self.platform_theme)
        self.assertIn('body[data-active-page="task"] .step-indicator', self.platform_theme)
        self.assertIn("blur(17px) saturate(175%) brightness(1.04)", self.platform_theme)

    def test_workflow_chrome_uses_adaptive_liquid_glass_light(self) -> None:
        self.assertIn("--glass-light-x: 28%;", self.platform_theme)
        self.assertIn(".workflow-chrome {\n  display: contents;", self.platform_theme)
        self.assertNotIn(".workflow-chrome.is-scrolled", self.platform_theme)
        self.assertIn("position: sticky;\n  z-index: 40;\n  top: 14px;", self.platform_theme)
        self.assertIn("position: sticky;\n  z-index: 39;\n  top: 100px;", self.platform_theme)
        self.assertIn(".app-nav::before,\n.step-indicator::before", self.platform_theme)
        self.assertIn("function initLiquidGlassLight()", self.js)
        self.assertIn('document.querySelectorAll(".app-nav, .step-indicator")', self.js)
        self.assertIn('glass.style.setProperty("--glass-light-x"', self.js)
        self.assertIn("initLiquidGlassLight();", self.js)

    def test_task_execution_actions_have_clear_primary_hierarchy(self) -> None:
        self.assertIn("#page-task #runTaskBtn", self.platform_theme)
        self.assertIn("min-height: 46px;", self.platform_theme)
        self.assertIn("#page-task .primary-actions + .action-row", self.platform_theme)
        self.assertIn("#page-task .execution-event strong", self.platform_theme)

    def test_exam_structure_review_uses_readable_low_saturation_palette(self) -> None:
        self.assertIn("#examStructureReviewModal .review-decision-header", self.platform_theme)
        self.assertIn("rgba(240, 246, 255, .98)", self.platform_theme)
        self.assertIn("#examStructureReviewModal .exam-structure-image-preview", self.platform_theme)
        self.assertIn("background: rgba(240, 246, 255, .92) !important;", self.platform_theme)
        self.assertIn("#examStructureReviewModal .exam-structure-field textarea", self.platform_theme)

    def test_task_manager_uses_unified_list_container(self) -> None:
        self.assertIn("#page-tasks .task-manager-list", self.platform_theme)
        self.assertIn("#page-tasks .task-manager-item", self.platform_theme)
        self.assertIn("border-bottom: 1px solid rgba(151, 174, 211, .22);", self.platform_theme)
        self.assertIn("#page-tasks .task-stat-grid > article:last-child", self.platform_theme)

    def test_browser_native_dialogs_are_replaced_by_platform_dialog(self) -> None:
        self.assertIn('id="platformDialog"', self.html)
        self.assertIn("function platformAlert(", self.js)
        self.assertIn("function platformConfirm(", self.js)
        self.assertIn("function platformPrompt(", self.js)
        self.assertNotRegex(self.js, r"\bwindow\.(alert|confirm|prompt)\s*\(")
        self.assertIn(".platform-dialog-overlay", self.platform_theme)
        self.assertIn("backdrop-filter: blur(14px)", self.platform_theme)
        self.assertIn('<script src="/app.js?v=', self.html)

    def test_workflow_header_and_frontend_assets_cannot_fall_back_to_stale_ui(self) -> None:
        self.assertIn('<header class="workflow-chrome">', self.html)
        self.assertIn(".workflow-chrome {", self.platform_theme)
        self.assertIn("no-store, no-cache, must-revalidate, max-age=0", self.server)
        initializer = self.js.split("function initSiteEnhancements() {", 1)[1].split("\n}", 1)[0]
        self.assertLess(initializer.index("initPlatformSelects();"), initializer.index("autoMarkReveals();"))

    def test_selects_are_progressively_enhanced_with_platform_menu(self) -> None:
        self.assertIn("function enhancePlatformSelect(select)", self.js)
        self.assertIn('select.dispatchEvent(new Event("change", { bubbles: true }))', self.js)
        self.assertIn("new MutationObserver", self.js)
        self.assertIn(".platform-select-trigger", self.platform_theme)
        self.assertIn(".platform-select-menu", self.platform_theme)
        self.assertIn('role="option"', self.js)

    def test_number_inputs_do_not_show_browser_spinner_controls(self) -> None:
        self.assertIn('input[type="number"]::-webkit-inner-spin-button', self.platform_theme)
        self.assertIn("-webkit-appearance: none;", self.platform_theme)

    def test_practice_displays_text_and_vision_model_sources(self) -> None:
        required_ids = (
            "practiceCurrentModelBadge",
            "practiceTextProviderSelect",
            "practiceTextModelSelect",
            "practiceTextModelInput",
            "practiceTextModelSummary",
            "practiceVisionProviderSelect",
            "practiceVisionModelSelect",
            "practiceVisionModelInput",
            "practiceVisionModelSummary",
        )
        for element_id in required_ids:
            self.assertIn(f'id="{element_id}"', self.html)
        self.assertIn("function updatePracticeModelSummary()", self.js)
        self.assertIn('id="page-practice-models"', self.html)
        self.assertIn('onclick="openCurrentPracticeModelSettings()"', self.html)
        self.assertIn('currentPracticeSourceMode === "knowledge" ? "knowledge-models" : "practice-models"', self.js)
        self.assertIn("TASK_MODEL_STORAGE_KEY", self.js)
        self.assertNotIn("跟随真题解析 · 结构化解析", self.js)
        self.assertNotIn("跟随真题解析 · 有图题读图", self.js)

    def test_practice_requests_use_effective_practice_models(self) -> None:
        payload_body = self.js.split("function practiceRequestPayload() {", 1)[1].split("\n}", 1)[0]
        self.assertIn('knowledgeMode ? knowledgeProviderName("text") : practiceProviderName("text")', payload_body)
        self.assertIn('knowledgeMode ? selectedKnowledgeModel("text") : selectedPracticeModel("text")', payload_body)
        self.assertIn('knowledgeMode ? knowledgeProviderName("vision") : practiceProviderName("vision")', payload_body)
        self.assertIn('knowledgeMode ? selectedKnowledgeModel("vision") : selectedPracticeModel("vision")', payload_body)
        self.assertNotIn("answerProviderSelect", payload_body)

        regenerate_body = self.js.split("function practiceRegenerationPayload(", 1)[1].split("\n}", 1)[0]
        self.assertIn('provider: practiceProviderName("text")', regenerate_body)
        self.assertIn('model: selectedPracticeModel("text")', regenerate_body)
        self.assertNotIn("answerProviderSelect", regenerate_body)

    def test_knowledge_generation_has_independent_entry_and_controls(self) -> None:
        required_ids = (
            "page-knowledge",
            "knowledgeForm",
            "knowledgeTitleInput",
            "knowledgeTextInput",
            "knowledgeFileInput",
            "knowledgeBlueprintReviewEnabled",
            "knowledgeFocusInput",
            "knowledgePlanBtn",
        )
        for element_id in required_ids:
            self.assertIn(f'id="{element_id}"', self.html)
        for question_type in ("单选题", "多选题", "判断题", "填空题", "简答题", "计算题", "作图题", "综合题"):
            self.assertIn(f'name="knowledgeQuestionType" value="{question_type}"', self.html)
        self.assertIn('source_mode: "knowledge"', self.js)
        self.assertIn("function planKnowledgePractice(event)", self.js)
        self.assertIn("knowledge_targeted", self.js)

    def test_knowledge_difficulty_is_configured_exactly_in_scope_confirmation(self) -> None:
        self.assertNotIn('id="knowledgeCount"', self.html)
        self.assertNotIn('id="knowledgeDifficulty"', self.html)
        self.assertIn('id="practiceDifficultyAllocation"', self.html)
        self.assertIn("function practiceDifficultyCounts()", self.js)
        self.assertIn("已分配 ${allocated} / ${n} 题", self.js)

    def test_knowledge_file_remove_button_has_isolated_visual_style(self) -> None:
        self.assertIn('class="knowledge-file-remove"', self.js)
        self.assertIn('title="移除此文件"', self.js)
        self.assertIn("#page-knowledge .knowledge-file-preview .knowledge-file-remove", self.styles)
        self.assertIn("border-radius: 50%;", self.styles)
        self.assertIn("background: #fff !important;", self.styles)

    def test_blueprint_review_supports_compact_multi_question_adjudication(self) -> None:
        for element_id in (
            "practicePlanReviewMode",
            "practicePlanCountBadge",
            "practicePlanExpandAllBtn",
            "practicePlanCollapseAllBtn",
        ):
            self.assertIn(f'id="{element_id}"', self.html)
        render_body = self.js.split("function renderPracticePlan(plan) {", 1)[1].split("\nasync function regeneratePracticePlan", 1)[0]
        self.assertIn('<details class="practice-plan-edit-row"', render_body)
        self.assertIn('data-plan-summary="target_skill"', render_body)
        self.assertIn('index < 2 ? " open" : ""', render_body)
        self.assertIn(".practice-plan-review-toolbar", self.styles)
        self.assertIn(".practice-plan-review-actions { position: sticky;", self.styles)

    def test_practice_stage_indicator_uses_the_classes_that_have_visual_styles(self) -> None:
        stage_body = self.js.split("function setPracticeStage(stage) {", 1)[1].split("\n}", 1)[0]
        self.assertIn('el.classList.add("stage-step--active")', stage_body)
        self.assertIn('el.classList.add("stage-step--done")', stage_body)
        self.assertIn('el.classList.add("stage-step--idle")', stage_body)
        self.assertNotIn("practice-step--active", stage_body)

    def test_shared_workspace_switches_all_core_copy_for_knowledge_mode(self) -> None:
        for element_id in (
            "practiceAvailability",
            "practiceInputKicker",
            "practiceInputTitle",
            "practiceUploadLabel",
            "practiceConfigTitle",
            "practiceFocusLabel",
            "practiceGenerateLabel",
            "practiceSubmitStageLabel",
            "practiceAnalysisSummaryLabel",
        ):
            self.assertIn(f'id="{element_id}"', self.html)
        mode_body = self.js.split("function setPracticeWorkspaceMode(", 1)[1].split("\n}", 1)[0]
        self.assertIn("知识材料", mode_body)
        self.assertIn("知识点出题模型配置", mode_body)
        self.assertIn("syncKnowledgeRequestToPracticeWorkspace", mode_body)

    def test_scope_drawer_is_readable_and_never_blocks_generation(self) -> None:
        self.assertIn("width: clamp(860px, 68vw, 1260px);", self.styles)
        self.assertIn("grid-template-columns: repeat(3, minmax(0, 1fr));", self.styles)
        loading_body = self.js.split("function showPracticeLoading(title) {", 1)[1].split("\n}", 1)[0]
        plan_body = self.js.split("function renderPracticePlan(plan) {", 1)[1].split("\n}", 1)[0]
        self.assertIn("closePracticeScopeDrawer();", loading_body)
        self.assertIn("closePracticeScopeDrawer();", plan_body)
        self.assertIn("确认后才会生成可编辑蓝图", self.html)

    def test_knowledge_jobs_use_the_same_generic_scope_confirmation(self) -> None:
        selection_body = self.js.split("function renderPracticeSourceSelection(data) {", 1)[1].split("\n}", 1)[0]
        self.assertIn('currentPracticeSourceMode === "knowledge"', selection_body)
        self.assertIn('latestPracticeRequest?.source_mode === "knowledge"', selection_body)
        self.assertIn("参与生成的知识单元", selection_body)
        self.assertNotIn("知识点材料无需选择原题范围", selection_body)


if __name__ == "__main__":
    unittest.main()
