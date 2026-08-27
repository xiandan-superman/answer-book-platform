from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
APP_JS = (ROOT / "web" / "app.js").read_text(encoding="utf-8")
INDEX_HTML = (ROOT / "web" / "index.html").read_text(encoding="utf-8")
MOTION_JS = (ROOT / "web" / "motion.js").read_text(encoding="utf-8")
PLATFORM_THEME_CSS = (ROOT / "web" / "platform-theme.css").read_text(encoding="utf-8")


def test_practice_formula_renderer_strips_provider_delimiters_before_wrapping() -> None:
    assert "function normalizePracticeFormulaLatex" in APP_JS
    assert "normalizePracticeFormulaLatex(formula.latex)" in APP_JS
    assert "escapeHtml(formula.latex)}\\\\]" not in APP_JS


def test_font_icon_compatibility_has_supported_fallbacks() -> None:
    icon_compat = (ROOT / "web" / "icon-compat.js").read_text(encoding="utf-8")

    assert '"file-pdf": "file-text"' in icon_compat
    assert "function supportedIcon(name)" in icon_compat
    assert 'raw.startsWith("file-")' in icon_compat


def test_final_acceptance_summary_is_separate_from_file_hint() -> None:
    assert 'id="finalAcceptanceSummary"' in INDEX_HTML
    assert 'id="finalResultHint"' in INDEX_HTML
    assert 'id="taskResultPageBtn"' in INDEX_HTML


def test_task_controls_are_capability_driven() -> None:
    assert 'setTaskControlVisibility("taskResultPageBtn", Boolean(caps.view_result))' in APP_JS
    assert "task.capabilities || {}" in APP_JS


def test_review_candidate_checkpoint_retry_is_not_cut_off_by_card_action_limit() -> None:
    retry_action = 'add(caps.retry && !caps.reopen_review, "retry-exam"'
    download_action = 'add(caps.download, "download"'

    assert APP_JS.index(retry_action) < APP_JS.index(download_action)


def test_exam_flow_has_explicit_high_risk_correctness_model_route() -> None:
    assert 'id="correctnessProviderSelect"' in INDEX_HTML
    assert 'id="correctnessModelSelect"' in INDEX_HTML
    assert 'correctness_provider: $("correctnessProviderSelect")' in APP_JS
    assert 'correctness_model: selectedTextRoleModel("correctness")' in APP_JS


def test_new_practice_entries_detach_from_the_previously_viewed_job() -> None:
    assert "function beginNewPracticeSession()" in APP_JS
    assert "rememberPracticeJob(\"\");" in APP_JS
    assert "practiceBatchId = newPracticeBatchId();" in APP_JS
    assert "function openPracticeEntry(mode = \"exam\", openModelSettings = false)" in APP_JS
    assert "function openKnowledgeEntry()" in APP_JS


def test_practice_result_selection_is_scoped_to_history_identity() -> None:
    assert "const historyChanged = incomingHistoryId !== currentPracticeHistoryId;" in APP_JS
    assert (
        "if (historyChanged || latestPracticeSet !== data) "
        "selectedPracticeExerciseIndexes.clear();"
    ) in APP_JS


def test_application_has_only_one_main_landmark() -> None:
    assert INDEX_HTML.count("<main") == 1
    assert INDEX_HTML.count("</main>") == 1


def test_motion_layer_is_accessible_and_limited_to_compositor_properties() -> None:
    assert "prefers-reduced-motion: reduce" in MOTION_JS
    assert "engine?.matchMedia()" in MOTION_JS
    assert 'clearProps: "opacity,visibility,transform,willChange"' in MOTION_JS
    assert "width:" not in MOTION_JS
    assert "height:" not in MOTION_JS


def test_task_manager_animates_only_entries_that_are_new_or_changed() -> None:
    assert "taskManagerMotionStatuses" in APP_JS
    assert "previousStatus !== normalized" in APP_JS
    assert "taskItemsChanged(animatedItems)" in APP_JS


def test_local_app_exposes_verified_user_initiated_updates() -> None:
    assert 'id="checkUpdateBtn"' in INDEX_HTML
    assert "async function checkPlatformUpdate()" in APP_JS
    assert 'api("/api/update/status?refresh=1")' in APP_JS
    assert 'api("/api/update/apply"' in APP_JS
    assert 'api("/api/update/progress")' in APP_JS
    assert 'id="platformUpdateNotice"' in INDEX_HTML
    assert 'id="platformUpdateProgress"' in INDEX_HTML
    assert 'role="progressbar"' in INDEX_HTML
    assert "function checkPlatformUpdateSilently()" in APP_JS
    assert "showPlatformUpdateNotice(status)" in APP_JS
    assert "API Key、教材、任务和输出不会被删除" in APP_JS
    assert 'backing_up: "备份当前版本"' in APP_JS
    assert 'title: "任务完成后再更新"' in APP_JS
    assert "独立更新窗口正在执行备份和安装" in APP_JS


def test_runtime_monitor_exposes_an_explicit_default_off_hybrid_switch() -> None:
    assert 'id="hybridExecutionEnabled"' in INDEX_HTML
    assert "本机执行（默认）" in INDEX_HTML
    assert 'api("/api/hybrid/settings")' in APP_JS
    assert 'body: JSON.stringify({ enabled: requested })' in APP_JS
    assert "任务材料不上传到混合云服务器" in INDEX_HTML


def test_upload_feedback_resets_when_files_or_upload_tabs_change() -> None:
    assert "function resetUploadFeedback(kind)" in APP_JS
    assert 'renderUploadSelection("textbook");\n    resetUploadFeedback("textbook");' in APP_JS
    assert 'renderUploadSelection("exam");\n    resetUploadFeedback("exam");' in APP_JS
    assert 'input.addEventListener("change", () => {\n    renderUploadSelection(kind);\n    resetUploadFeedback(kind);' in APP_JS


def test_frontend_displays_formal_app_version_without_legacy_internal_label() -> None:
    assert 'version.app_version || versionParts[0]' in APP_JS
    assert '$("platformVersion").textContent = `v${appVersion}`;' in APP_JS
    assert "V${baseVersion}+${sourceRevision}" not in APP_JS


def test_multimodal_answer_model_hides_redundant_vision_stage() -> None:
    assert "const answerDirectVision = modelLooksVisionCapable" in APP_JS
    assert "结构化解析模型（直接读图）" in APP_JS
    assert "不再先调用独立识图模型" in APP_JS


def test_model_configuration_defaults_to_simple_presets_with_advanced_routes() -> None:
    assert 'id="examModelPresetSelect"' in INDEX_HTML
    assert 'value="balanced"' not in INDEX_HTML
    assert "稳定推荐" not in INDEX_HTML
    assert 'value="quality"' in INDEX_HTML
    assert 'value="economy"' in INDEX_HTML
    assert 'id="examModelRoleDetails"' in INDEX_HTML
    assert 'EXAM_MODEL_PRESET_STORAGE_KEY = "answerBook.examModelPreset.v1"' in APP_JS
    assert 'label: "质量优先（推荐）"' in APP_JS
    assert 'reasoning: ["lingsuan_openai", "gpt-5.6-terra"]' in APP_JS
    assert 'answer: ["lingsuan_openai", "gpt-5.6-terra"]' in APP_JS
    assert 'correctness: ["lingsuan_openai", "gpt-5.6-sol"]' in APP_JS
    assert 'answer: ["lingsuan_google", "gemini-3.6-flash"]' in APP_JS
    assert 'if (saved === "balanced") return "quality";' in APP_JS
    assert 'return "quality";' in APP_JS


def test_hidden_providers_are_omitted_from_every_user_facing_model_entry() -> None:
    for provider in ("ark", "bailian", "sensenova", "openrouter", "lingsuan_xai", "lingsuan_anthropic"):
        assert f'  "{provider}",' in APP_JS
    assert "function userVisibleProviderEntries" in APP_JS
    assert "const entries = userVisibleProviderEntries();" in APP_JS
    assert "const entries = userVisibleProviderEntries().sort" in APP_JS


def test_practice_generation_defaults_to_lingsuan_gemini_and_image_two() -> None:
    assert 'const preferredProvider = kind === "image" ? "lingsuan_image" : "lingsuan_google";' in APP_JS
    assert 'populateProviderSelect("imageProviderSelect", "image", "lingsuan_image");' in APP_JS
    assert 'populateImageModelControls("gpt-image-2");' in APP_JS
    assert 'id="practiceImageProviderSelect"' in INDEX_HTML
    assert 'id="knowledgeImageProviderSelect"' in INDEX_HTML
    assert 'for (const kind of ["text", "vision", "image"])' in APP_JS


def test_practice_and_knowledge_expose_one_primary_model_by_default() -> None:
    assert INDEX_HTML.count("高级：主模型不能读图时的图片回退") == 2
    assert INDEX_HTML.count("<h3>主生成模型</h3>") == 2
    assert 'class="task-model-fallback-details"' in INDEX_HTML


def test_unconfigured_optional_image_fallback_does_not_block_exam_creation() -> None:
    assert "const imageFallbackConfigured = Boolean" in APP_JS
    assert 'image_provider: imageFallbackConfigured ?' in APP_JS
    assert 'image_model: imageFallbackConfigured ?' in APP_JS


def test_api_key_password_fields_belong_to_non_submitting_forms() -> None:
    assert '<form class="key-provider-card" data-key-provider=' in APP_JS
    assert 'grid.querySelectorAll("form[data-key-provider]")' in APP_JS
    assert 'event.preventDefault()' in APP_JS


def test_api_key_configuration_failure_is_local_retryable_and_partial() -> None:
    assert "async function loadApiConfiguration" in APP_JS
    assert "Promise.allSettled" in APP_JS
    assert "API 配置加载失败，请重试" in APP_JS
    assert "API 配置保存状态加载失败，请重试" in APP_JS
    assert 'data-key-config-retry' in APP_JS
    assert 'apiKeyConfigLoadState.providers === "ready"' in APP_JS
    assert 'apiKeyConfigLoadState.keyFile === "ready"' in APP_JS
    assert "async function recoverDamagedApiConfiguration" in APP_JS
    assert "备份损坏配置并重建" in APP_JS
    assert 'body: JSON.stringify({ confirm: true })' in APP_JS
    platform_api = (ROOT / "web" / "platform-api.js").read_text(encoding="utf-8")
    assert "error.recoveryAction = data.recovery_action" in platform_api


def test_practice_status_banner_tracks_blueprint_confirmation_stage() -> None:
    assert 'setPracticeStatusBanner("等待确认训练蓝图", "loading");' in APP_JS


def test_completed_generation_message_distinguishes_warnings_from_failures() -> None:
    assert "function completedGenerationTaskMessage(task = {})" in APP_JS
    assert 'practiceCompletionHas(task, "generation_incomplete")' in APP_JS
    assert "项非阻断提示" in APP_JS
    assert 'task.status === "completed_with_issues" ? "部分题目生成失败"' not in APP_JS


def test_blueprint_audit_failed_question_has_explicit_local_review_retry() -> None:
    assert 'item.generation_error?.code === "blueprint_audit_failed"' in APP_JS
    assert '"复审并生成本题"' in APP_JS
    assert "系统只修复并复审这一蓝图项" in APP_JS
    assert "response.practice_updates" in APP_JS
    assert "practice_updates: practiceUpdates" in APP_JS
    assert 'issueCodes.has("review_required")' not in APP_JS
    assert 'item.code === "review_required"' in APP_JS
    assert "题目已生成 · 待复核" in APP_JS


def test_partial_practice_status_does_not_claim_everything_completed() -> None:
    contracts = (ROOT / "app" / "task_contracts.py").read_text(encoding="utf-8")
    assert '"generation_incomplete"' in contracts
    assert '"label": "存在未完成题目"' in contracts
    assert 'completed_with_issues: { icon: "fas fa-triangle-exclamation", label: "完成待复核" }' in APP_JS


def test_practice_completion_contract_drives_all_public_surfaces() -> None:
    assert 'const PRACTICE_COMPLETION_ISSUES_SCHEMA = "answer_book.practice_completion_issues.v1"' in APP_JS
    assert "function practiceCompletionContract(subject = {})" in APP_JS
    assert "completion.display_label" in APP_JS
    assert "completion.action_label" in APP_JS
    assert "completion.primary.icon" in APP_JS
    assert "完成有待处理项" in (ROOT / "web" / "index.html").read_text(encoding="utf-8")


def test_review_candidate_download_prefers_explicit_candidate_filename() -> None:
    assert 'file.name === "answer_book_review_candidate.docx"' in APP_JS


def test_resumed_practice_job_uses_public_error_presentation() -> None:
    assert "job.error_presentation?.message" in APP_JS
    assert "function practicePublicErrorText" in APP_JS
    assert "诊断编号：${supportId}" in APP_JS
    assert 'action === "job-config"' in APP_JS
    assert 'confirmText: configurationRequired ? "检查 API 配置" : "从检查点重试"' in APP_JS
    assert 'String(presentation?.kind || "")' in APP_JS


def test_missing_key_action_requires_explicit_backend_contract() -> None:
    assert "function practiceErrorExplicitlyNeedsConfiguration(subject = {})" in APP_JS
    assert 'subject?.requires_configuration === true' in APP_JS
    assert 'Boolean(String(subject?.configuration_provider || "").trim())' in APP_JS
    assert 'String(subject?.configuration_reason || "") === "missing_api_key"' in APP_JS
    assert INDEX_HTML.count("前往 API 配置") == 2


def test_stale_practice_draft_requires_explicit_resolution() -> None:
    assert "base_edit_version: practiceEditorDraftBaseVersion" in APP_JS
    assert "practiceEditorDraftBaseVersion = String(record.base_edit_version" in APP_JS
    assert "setPracticeEditorStaleState(true);" in APP_JS
    assert 'id="practiceEditorCopyDraft"' in INDEX_HTML
    assert 'id="practiceEditorMergeDraft"' in INDEX_HTML
    assert "放弃旧稿并加载最新版本" in INDEX_HTML


def test_stale_practice_job_callbacks_cannot_replace_a_newer_workspace() -> None:
    assert "if (sessionVersion !== practiceSessionVersion) return;" in APP_JS
    assert "async function openGenerationJob(task)" in APP_JS
    assert "async function resumeRememberedPracticeJob()" in APP_JS


def test_cancelled_practice_job_stops_polling_and_clears_resume_pointer() -> None:
    start = APP_JS.index("async function waitForPracticeJob(jobId, { onUpdate = null } = {})")
    end = APP_JS.index("async function submitPracticeJob", start)
    polling = APP_JS[start:end]
    assert 'job.status === "cancelled"' in polling
    assert 'rememberPracticeJob("");' in polling
    assert "后台出题任务已取消" in polling
    assert "terminalError.practiceJob = job;" in polling


def test_practice_network_pause_is_visible_and_never_polls_forever() -> None:
    assert 'data-action="job-pause"' in APP_JS
    assert 'data-action="job-resume"' in APP_JS
    assert 'controlGenerationJob(task, "pause")' in APP_JS
    assert 'controlGenerationJob(task, "resume")' in APP_JS
    start = APP_JS.index("async function waitForPracticeJob(jobId, { onUpdate = null } = {})")
    end = APP_JS.index("async function submitPracticeJob", start)
    polling = APP_JS[start:end]
    assert 'job.status === "paused"' in polling
    assert "pausedError.practiceJob = job;" in polling
    assert "function generationNetworkSummary(task = {})" in APP_JS
    assert "deadline_remaining_seconds" in APP_JS


def test_terminal_practice_job_resume_never_forces_the_visible_workspace() -> None:
    start = APP_JS.index("async function resumeRememberedPracticeJob()")
    end = APP_JS.index("async function refresh()", start)
    resume = APP_JS[start:end]
    assert "const stoppedJob = error?.practiceJob" in resume
    assert 'showPracticeRecoveryNotice(stoppedJob' in resume
    assert 'goToPage("knowledge");' not in resume
    assert 'goToPage("practice");' not in resume


def test_practice_recovery_notice_is_accessible_non_blocking_and_unique() -> None:
    assert INDEX_HTML.count('id="practiceRecoveryNotice"') == 1
    assert 'aria-labelledby="practiceRecoveryTitle"' in INDEX_HTML
    assert 'aria-describedby="practiceRecoveryMessage"' in INDEX_HTML
    assert 'role="status" aria-live="polite" aria-atomic="true"' in INDEX_HTML
    assert 'id="practiceRecoveryOpenBtn"' in INDEX_HTML
    assert 'id="practiceRecoveryStayBtn"' in INDEX_HTML
    assert "practiceRecoveryNoticeSignature" in APP_JS
    assert 'signature === practiceRecoveryNoticeSignature' in APP_JS


def test_practice_recovery_requires_explicit_open_and_tracks_navigation() -> None:
    start = APP_JS.index("async function resumeRememberedPracticeJob()")
    end = APP_JS.index("async function refresh()", start)
    resume = APP_JS[start:end]
    assert "const navigationVersion = practiceNavigationVersion;" in resume
    assert "navigationVersion !== practiceNavigationVersion" in resume
    assert "practiceRecoveryContextIsCurrent(context)" in resume
    assert "rememberPracticeJob(\"\");" in resume
    assert 'goToPage("practice");' not in resume
    assert "async function openPracticeRecoveryNoticeJob()" in APP_JS
    assert 'await openGenerationJob({' in APP_JS


def test_new_practice_session_invalidates_old_recovery_observer() -> None:
    start = APP_JS.index("function beginNewPracticeSession()")
    end = APP_JS.index("function updateStepIndicator", start)
    new_session = APP_JS[start:end]
    assert "invalidatePracticeRecoveryObserver();" in new_session
    assert "practiceSessionVersion += 1;" in new_session
    assert "rememberPracticeJob(\"\");" in new_session


def test_reusing_knowledge_generation_restores_files_as_a_new_session() -> None:
    start = APP_JS.index("async function reuseGenerationTask(task)")
    end = APP_JS.index("async function deleteGenerationTask", start)
    reuse = APP_JS[start:end]
    assert "openKnowledgeEntry();" in reuse
    assert "knowledgeSourceFiles = normalizeSourceFileList(request.source_files)" in reuse


def test_task_manager_uses_persisted_public_title_without_model_or_paths() -> None:
    assert "function shortTaskMaterialName(value, limit = 18)" in APP_JS
    title_block = APP_JS[APP_JS.index("function taskManagerTitle"):APP_JS.index("function renderTaskManagerPagination")]
    assert "task.display_title" in title_block
    assert "shortName(task.description || task.exam_display_name || task.exam_path" in title_block
    assert "shortTaskModelName" not in title_block
    assert "开始于 ${escapeHtml(formatTaskTimestamp(task.created_at))}" in APP_JS


def test_task_manager_terminal_copy_and_unknown_network_statistics_are_truthful() -> None:
    assert 'failed: { label: "执行失败", meta: "请查看原因并按建议重试" }' in APP_JS
    assert '["failed", "cancelled", "paused", "completed", "completed_with_issues"].includes(normalized)' in APP_JS
    assert '"模型请求次数统计中"' in APP_JS
    assert '"模型请求次数暂无数据"' in APP_JS
    assert '"调用预算统计中"' in APP_JS
    assert '"剩余等待上限暂无数据"' in APP_JS
    assert "Number(task.network_attempted_count || 0)" not in APP_JS
    assert "function taskProgressPresentation(task, normalized, progress)" in APP_JS
    assert 'return { label: "调用状态", value: "未发起调用", showBar: false };' in APP_JS
    assert 'return { label: "任务状态", value: "已取消", showBar: false };' in APP_JS
    assert 'progressPresentation.showBar ? `<div class="manager-progress-track">' in APP_JS
    paused_start = APP_JS.index('if (job.status === "paused")')
    paused_end = APP_JS.index("latestPracticeRequest = job.payload", paused_start)
    paused_copy = APP_JS[paused_start:paused_end]
    assert "Number(job.network_attempted_count || 0)" not in paused_copy
    assert 'job.network_call_budget !== null' in paused_copy
    assert '"暂无数据"' in paused_copy


def test_mobile_navigation_keeps_all_critical_actions_visible_and_targetable() -> None:
    mobile_start = PLATFORM_THEME_CSS.index("@media (max-width: 720px)")
    mobile_end = PLATFORM_THEME_CSS.index("@media", mobile_start + 1)
    mobile_css = PLATFORM_THEME_CSS[mobile_start:mobile_end]
    assert "grid-template-columns: repeat(5, minmax(0, 1fr));" in mobile_css
    assert "min-height: 108px;" in mobile_css
    assert "min-height: 42px;" in mobile_css
    assert ".nav-actions { display: none" not in mobile_css
    assert ".nav-actions .ghost-button { display: none" not in mobile_css


def test_exam_task_list_tooltips_never_render_storage_paths() -> None:
    render_start = APP_JS.index("function renderTasks(tasks)")
    render_end = APP_JS.index("function taskProgressPercent", render_start)
    renderer = APP_JS[render_start:render_end]
    assert "task.textbook_material_names" in renderer
    assert 'row.title = `${task.exam_path' not in renderer
    assert 'pretty({ selected_task: task })' not in renderer


def test_task_manager_opens_all_tasks_instead_of_action_required_filter() -> None:
    assert 'onclick="openTaskManager()" aria-label="打开任务管理"' in INDEX_HTML
    start = APP_JS.index('function openTaskManager(kind = "all")')
    end = APP_JS.index("function openWordFormatReviewer", start)
    assert 'filterTasks("all");' in APP_JS[start:end]
    assert "taskManagerLoading = true;" in APP_JS[start:end]


def test_task_manager_ignores_stale_list_responses_and_has_loading_copy() -> None:
    start = APP_JS.index("async function loadTasks(options = {})")
    end = APP_JS.index("async function runTask", start)
    flow = APP_JS[start:end]
    assert "const requestVersion = ++taskLoadVersion;" in flow
    assert "if (requestVersion !== taskLoadVersion) return;" in flow
    assert "正在读取任务" in APP_JS


def test_exam_confirmation_counts_textbook_groups_instead_of_file_parts() -> None:
    assert "function selectedTextbookNames()" in APP_JS
    assert "const selectedBookNames = selectedTextbookNames();" in APP_JS
    assert "教材：已选择 ${selectedBookNames.length} 本（${selectedBookNames.join(\"、\")}）" in APP_JS
    assert "教材：已选择 ${selectedBooks.length} 本" not in APP_JS


def test_action_required_task_cards_do_not_present_as_fully_completed() -> None:
    assert 'const currentStageText = reviewPending\n      ? "等待确认"' in APP_JS
    assert 'const progressMessage = reviewPending\n      ? "当前步骤已完成，等待你确认后继续。"' in APP_JS


def test_generation_task_title_can_be_renamed_from_task_manager() -> None:
    assert 'data-action="rename-title"' in APP_JS
    assert "async function renameGenerationTask(task)" in APP_JS
    assert "/api/practice/tasks/${encodeURIComponent(task.task_id)}/title" in APP_JS


def test_completed_practice_result_cannot_resurface_scope_confirmation() -> None:
    start = APP_JS.index("function renderPracticeResults(data)")
    end = APP_JS.index("function closePracticeActionMenus", start)
    result_renderer = APP_JS[start:end]
    assert 'closePracticeScopeDrawer();\n  $("practiceScopeResume")?.classList.add("hidden");' in result_renderer


def test_resumed_active_generation_hides_material_entry_and_restores_its_stage() -> None:
    assert "function setPracticeSourceEntryVisibility(visible)" in APP_JS
    assert 'sidebar.classList.toggle("practice-stage-hidden", !visible);' in APP_JS
    assert "function practiceStageForJobOperation(operation)" in APP_JS
    assert 'showPracticeOperationLoading(\n        job.operation === "analyze"' in APP_JS
    assert "job.operation\n      );" in APP_JS


def test_question_copy_repairs_legacy_formula_notation_and_uses_word_payload() -> None:
    assert "function repairPracticeClipboardLatex(value)" in APP_JS
    assert 'join("\\\\mid ")' in APP_JS
    assert "Missing close brace" in APP_JS
    assert "ε-\\\\mathrm{Fe_3N}" in APP_JS
    start = APP_JS.index("async function copyPracticeQuestion(index, button)")
    end = APP_JS.index("let practiceMathJaxPromise", start)
    question_copy = APP_JS[start:end]
    assert "{ word: true, includeQuestionHeading: false }" in question_copy


def test_word_copy_uses_paragraphs_and_recovers_markdown_pipe_tables() -> None:
    assert "function practiceClipboardParagraphsHtml" in APP_JS
    start = APP_JS.index("function practiceClipboardTextSegment(value)")
    end = APP_JS.index("function practiceClipboardDomainTextHtml", start)
    assert 'replace(/\\n/g, "<br>")' not in APP_JS[start:end]
    assert "function extractPracticeMarkdownTables(stem)" in APP_JS
    assert "function normalizePracticeMarkdownTables(item)" in APP_JS
    assert "data.exercises = data.exercises.map(normalizePracticeMarkdownTables)" in APP_JS


def test_blueprint_review_exposes_grouped_design_fallbacks() -> None:
    assert "const refinement = plan.blueprint_refinement || {};" in APP_JS
    assert "分组设计调度" in APP_JS
    assert "细化失败，已保留全局方案" in APP_JS


def test_blueprint_warning_reason_is_visible_when_errors_are_empty() -> None:
    assert "const blueprintAuditMessages = (blueprintAudit.errors || []).length" in APP_JS
    assert "blueprintAuditMessages.slice(0, 2)" in APP_JS
    assert "(blueprintAudit.errors || blueprintAudit.warnings || [])" not in APP_JS


def test_comprehensive_count_risk_is_visible_without_blocking_submission() -> None:
    assert 'id="practiceTargetedCountRisk"' in INDEX_HTML
    assert "function updatePracticeStrategySettings()" in APP_JS
    assert "建议至少输入 ${recommendedCount} 道题" in APP_JS
    assert "继续生成时系统将优先覆盖核心知识点" in APP_JS


def test_word_export_has_no_human_warning_acknowledgement_path() -> None:
    start = APP_JS.index("function practiceExportRequestPayload")
    end = APP_JS.index("function selectedPracticeSet", start)
    export_flow = APP_JS[start:end]
    assert "export_warning_confirmation_required" not in export_flow
    assert "confirmPracticeExportWarnings" not in export_flow
    assert "export_warning_acknowledged" not in export_flow


def test_selected_word_export_reads_checked_dom_and_sends_stable_question_ids() -> None:
    start = APP_JS.index("function practiceExerciseExportId")
    end = APP_JS.index("function updatePracticeSelectionActions", start)
    export_selection = APP_JS[start:end]
    assert "function practiceExportRequestPayload" in export_selection
    assert 'export_scope: selectedScope ? "selected" : "all"' in export_selection
    assert "selected_exercise_ids: selectedScope ? requestedIds : []" in export_selection
    assert "document.querySelectorAll('input[data-practice-select]:checked')" in export_selection
    assert 'export_scope: "selected"' in export_selection


def test_saved_word_export_posts_only_history_identity_and_selection() -> None:
    start = APP_JS.index("function practiceExportRequestPayload")
    end = APP_JS.index("function practiceWordLabel", start)
    payload_builder = APP_JS[start:end]

    assert "return historyId ? selection : { ...data, ...selection };" in payload_builder
    assert "history_id: historyId" in payload_builder
    assert "selected_exercise_ids: selectedScope ? requestedIds : []" in payload_builder


def test_practice_question_save_sends_edit_version_and_handles_conflicts() -> None:
    editor_start = APP_JS.index("async function applyPracticeEditor")
    start = APP_JS.index("async function saveRegeneratedPracticeExercise")
    end = APP_JS.index("async function regenerateSelectedPracticeQuestions", start)
    editor_flow = APP_JS[editor_start:start]
    save_flow = APP_JS[start:end]
    assert "expected_edit_version:" in save_flow
    assert 'error?.code === "practice_edit_conflict"' in save_flow
    assert "/api/practice/history/${encodeURIComponent(historyId)}" in save_flow
    assert "generatedCandidate = response.exercise" in save_flow
    assert "openPracticeEditor(index, generatedCandidate, regenerationBaseEditVersion)" in save_flow
    assert "const regenerationBaseEditVersion" in save_flow
    assert "本次生成候选均已保留" in save_flow
    assert 'editConflict = error?.code === "practice_edit_conflict"' in editor_flow
    assert "当前填写内容已作为旧稿保留并锁定" in editor_flow
    assert "persistPracticeEditorDraft(practiceEditorDraftSource)" in editor_flow
    assert "saveButton.disabled = editConflict" in editor_flow


def test_practice_editor_persists_and_restores_unsaved_drafts() -> None:
    assert 'PRACTICE_EDITOR_DRAFT_PREFIX = "answerBook.practiceEditorDraft.v1."' in APP_JS
    assert "function persistPracticeEditorDraft(" in APP_JS
    assert "function restorePracticeEditorDraft(" in APP_JS
    assert 'persistPracticeEditorDraft("regeneration_candidate")' in APP_JS
    assert "clearPracticeEditorDraft();" in APP_JS
    assert 'window.addEventListener("beforeunload"' in APP_JS
    assert 'id="practiceEditorDiscardDraft"' in INDEX_HTML


def test_pre_generation_workspace_is_persisted_by_mode_and_stage() -> None:
    assert 'PRACTICE_WORKSPACE_DB_NAME = "answerBook.practiceWorkspace.v1"' in APP_JS
    assert "function persistPracticeWorkspaceDraft(" in APP_JS
    assert "function restorePersistentPracticeWorkspace(" in APP_JS
    assert 'mode === "knowledge" ? "knowledge" : "exam"' in APP_JS
    assert 'stage === "scope" ? capturePracticeScopeConfig()' in APP_JS
    assert 'stage === "plan" ? copyPracticeWorkspaceValue(latestPracticePlan)' in APP_JS
    assert "plan_drafts:" in APP_JS
    assert "pending_plan_candidate:" in APP_JS
    assert 'id="practiceWorkspaceDraftClear"' in INDEX_HTML
    assert 'id="practiceWorkspaceDraftClearActive"' in INDEX_HTML
    assert 'id="knowledgeWorkspaceDraftClear"' in INDEX_HTML
    assert 'id="practiceWorkspaceDraftRestorePrevious"' in INDEX_HTML
    assert 'id="knowledgeWorkspaceDraftRestorePrevious"' in INDEX_HTML
    assert "function restorePreviousPracticeWorkspace(" in APP_JS
    assert "workspace_mode: normalizedMode" in APP_JS
    assert 'id="practiceSemanticReviewEnabled"' in INDEX_HTML
    assert 'id="knowledgeSemanticReviewEnabled"' in INDEX_HTML
    assert 'semantic_review_enabled: $("practiceSemanticReviewEnabled")?.checked === true' in APP_JS
    assert 'semantic_review_enabled: $("knowledgeSemanticReviewEnabled")?.checked === true' in APP_JS
    assert "function announceAvailablePracticeWorkspaceDraft(" in APP_JS
    assert "当前已保持新任务空白" in APP_JS
    assert "practiceWorkspaceDraftEpochs[normalizedMode] += 1" in APP_JS


def test_workspace_restore_uses_a_stable_candidate_and_confirms_before_overwrite() -> None:
    assert 'PRACTICE_WORKSPACE_RESTORE_CANDIDATE_SCHEMA = "practice_workspace_restore_candidate.v1"' in APP_JS
    assert "const practiceWorkspaceRestoreCandidates = { exam: null, knowledge: null };" in APP_JS
    assert "function discoverPracticeWorkspaceRestoreCandidate(" in APP_JS
    assert "practiceWorkspaceRestoreCandidateKey(normalizedMode)" in APP_JS
    assert "candidate.source === \"active\"" in APP_JS
    assert 'title: "恢复草稿会替换当前输入"' in APP_JS
    assert 'cancelText: "保留当前输入"' in APP_JS
    assert "restorePersistentPracticeWorkspace(normalizedMode, sessionVersion, stableRecord)" in APP_JS
    assert "clearPracticeWorkspaceRestoreCandidate(normalizedMode)" in APP_JS


def test_workspace_restore_falls_back_to_latest_archive_for_both_modes() -> None:
    discovery_start = APP_JS.index("async function discoverPracticeWorkspaceRestoreCandidate(")
    discovery_end = APP_JS.index("function capturePracticeScopeConfig", discovery_start)
    discovery = APP_JS[discovery_start:discovery_end]
    assert 'source = "archive";' in discovery
    assert "Number(right.archived_at || 0) - Number(left.archived_at || 0)" in discovery
    assert 'mode === "knowledge" ? "knowledge" : "exam"' in APP_JS
    assert "rememberPracticeWorkspaceEntryBaseline(mode, sessionVersion)" in APP_JS
    assert 'rememberPracticeWorkspaceEntryBaseline("knowledge", sessionVersion)' in APP_JS


def test_knowledge_submission_matches_practice_empty_material_guard() -> None:
    assert 'id="knowledgePlanBtn" class="primary-button knowledge-submit" type="submit" disabled aria-disabled="true"' in INDEX_HTML
    assert "function syncKnowledgeSubmitAvailability()" in APP_JS
    assert '$("knowledgeTitleInput")?.value.trim()' in APP_JS
    assert '$("knowledgeTextInput")?.value.trim()' in APP_JS
    assert "knowledgeSourceFiles.length > 0" in APP_JS
    assert 'button.title = ready ? "解析知识材料并确认范围" : "请先填写知识点、粘贴材料或上传文件"' in APP_JS
    assert '$("knowledgeTitleInput")?.focus();' in APP_JS


def test_exam_material_page_points_to_the_visible_library_action() -> None:
    assert "教材只允许使用已在教材管理中建立索引的内容" in INDEX_HTML
    assert "请点击上方“打开教材管理”，上传教材并建立索引" in APP_JS
    assert "上一步已建立索引" not in INDEX_HTML
    assert "右上角“教材管理”" not in APP_JS


def test_saved_api_key_cards_render_as_configured_instead_of_waiting_for_test() -> None:
    key_cards_start = APP_JS.index("function renderKeyProviderCards()")
    key_cards_end = APP_JS.index("async function recoverDamagedApiConfiguration", key_cards_start)
    key_cards = APP_JS[key_cards_start:key_cards_end]

    assert '${cfg.api_key_set ? "已配置" : "等待测试"}' in key_cards
    assert "已保存，可直接使用；如需替换，请输入新 Key 并重新测试。" in key_cards
    assert '<div class="key-provider-status idle" data-key-status><strong>等待测试</strong>' not in key_cards


def test_practice_requests_include_the_configured_image_route() -> None:
    practice_start = APP_JS.index("function practiceRequestPayload()")
    knowledge_start = APP_JS.index("function knowledgeRequestPayload()", practice_start)
    practice_flow = APP_JS[practice_start:knowledge_start]
    knowledge_flow = APP_JS[knowledge_start:APP_JS.index("function updateKnowledgeModelSummary", knowledge_start)]
    for flow in (practice_flow, knowledge_flow):
        assert 'image_provider: imageConfigured ? imageProvider : ""' in flow
        assert 'image_model: imageConfigured ? imageModel : ""' in flow


def test_practice_and_knowledge_preflight_missing_model_configuration_before_job_submission() -> None:
    assert "function practiceSubmissionConfigurationIssue(" in APP_JS
    assert "showPracticeSubmissionConfigurationIssue(sourceMode, configurationIssue)" in APP_JS
    assert 'showPracticeSubmissionConfigurationIssue("knowledge", configurationIssue)' in APP_JS
    assert "缺少 ${providerLabel} API Key" in APP_JS
    assert 'id="practiceConfigurationAction"' in INDEX_HTML
    assert 'id="knowledgeConfigurationAction"' in INDEX_HTML
    assert 'title: "需要先完成 API 配置"' in APP_JS
    assert 'caps.retry && !configurationRequired' in APP_JS


def test_practice_drawing_question_explains_why_answer_image_is_not_generated() -> None:
    assert "本题要求学生作图" in APP_JS
    assert "不会调用 gpt-image-2 生成答案图" in APP_JS


def test_practice_preview_renders_real_diagrams_and_exposes_invalid_figures() -> None:
    assert "function practiceDiagramSvg(figure)" in APP_JS
    assert "practice-diagram-svg" in APP_JS
    assert "题图生成失败：缺少可绘制的数据或节点关系" in APP_JS


def test_word_format_reviewer_is_a_secondary_home_tool_and_managed_task_kind() -> None:
    assert 'class="home-utility-entry"' in INDEX_HTML
    assert 'onclick="openWordFormatReviewer()"' in INDEX_HTML
    assert 'data-kind="format"' in INDEX_HTML
    assert 'onclick="openTaskManager(\'format\')"' in INDEX_HTML
    assert 'task.is_format_task' in APP_JS
    assert '"format-open"' in APP_JS
    assert '"format-download"' in APP_JS
    assert '"format-delete"' in APP_JS
    assert 'window.location.href = `/word-format${query}`' in APP_JS


def test_generation_network_summary_exposes_each_transport_layer() -> None:
    assert 'provider_connect_timeout: "连接超时"' in APP_JS
    assert 'provider_first_byte_timeout: "首字节超时"' in APP_JS
    assert 'provider_read_idle_timeout: "读取空闲超时"' in APP_JS
    assert 'provider_call_deadline_exceeded: "单次调用截止"' in APP_JS
    assert "network_attempted_count" in APP_JS
    assert "deadline_remaining_seconds" in APP_JS
    task_contract_ui = (ROOT / "web" / "task-contract-ui.js").read_text(encoding="utf-8")
    assert 'paused: "已暂停"' in task_contract_ui.split("const statusLabels", 1)[0]
    hydrate_start = APP_JS.index("async function hydrateLiveTaskDetails")
    hydrate_end = APP_JS.index("async function loadTasks", hydrate_start)
    assert "!task.is_generation_job" in APP_JS[hydrate_start:hydrate_end]


def test_practice_loading_shows_copyable_task_id() -> None:
    assert 'id="practiceLoadingTaskId"' in INDEX_HTML
    assert 'id="practiceLoadingCopyTaskId"' in INDEX_HTML
    assert "showPracticeLoadingTaskId(queued.job_id || queued.task_id)" in APP_JS


def test_failed_analysis_material_is_replaced_and_scope_snapshot_is_pinned() -> None:
    assert "let practiceMaterialReplacementRequired = false;" in APP_JS
    assert "const replaceFailedTaskMaterial = practiceMaterialReplacementRequired;" in APP_JS
    assert "上次失败任务的材料已自动移出" in APP_JS
    assert "latestPracticeRequest = { ...latestPracticeRequest, source_snapshot: data.source_snapshot };" in APP_JS
    assert "practiceMaterialReplacementRequired = request.source_files.length > 0;" in APP_JS
