from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
APP_JS = (ROOT / "web" / "app.js").read_text(encoding="utf-8")
INDEX_HTML = (ROOT / "web" / "index.html").read_text(encoding="utf-8")
MOTION_JS = (ROOT / "web" / "motion.js").read_text(encoding="utf-8")
PLATFORM_THEME_CSS = (ROOT / "web" / "platform-theme.css").read_text(encoding="utf-8")
WORD_FORMAT_HTML = (ROOT / "standalone_word_format_reviewer" / "web" / "index.html").read_text(encoding="utf-8")


def test_practice_formula_renderer_strips_provider_delimiters_before_wrapping() -> None:
    assert "function normalizePracticeFormulaLatex" in APP_JS
    assert "normalizePracticeFormulaLatex(formula.latex)" in APP_JS
    assert "escapeHtml(formula.latex)}\\\\]" not in APP_JS


def test_practice_result_renderer_recovers_bare_boldsymbol_commands() -> None:
    assert "function normalizeBarePracticeLatexCommands" in APP_JS
    assert "normalizeStandaloneMathLines(normalizeBarePracticeLatexCommands(value))" in APP_JS
    assert 'load: ["[tex]/boldsymbol"]' in APP_JS
    assert 'packages: { "[+]": ["boldsymbol"] }' in APP_JS


def test_practice_results_offer_direct_full_export_and_selected_export() -> None:
    assert 'id="practiceDownloadAllBtn"' in INDEX_HTML
    assert 'id="practiceDownloadSelectedBtn"' in INDEX_HTML
    assert "function exportablePracticeSet()" in APP_JS
    assert '$("practiceDownloadAllBtn")?.addEventListener("click"' in APP_JS


def test_storage_preview_is_compact_without_narrowing_full_cleanup_scope() -> None:
    assert "const STORAGE_ENTRY_PREVIEW_LIMIT = 8;" in APP_JS
    assert "const storageExpandedKinds = new Set();" in APP_JS
    assert 'data.storageExpandKind = area.kind' not in APP_JS
    assert "toggle.dataset.storageExpandKind = area.kind;" in APP_JS
    assert "for (const area of storageOverviewData?.areas || [])" in APP_JS


def test_api_configuration_links_directly_to_each_model_context() -> None:
    assert 'onclick="startWizard()"' in INDEX_HTML
    assert 'onclick="goToPage(\'practice-models\')"' in INDEX_HTML
    assert 'onclick="goToPage(\'knowledge-models\')"' in INDEX_HTML


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


def test_task_manager_waiting_cards_and_toolbar_avoid_redundant_visual_rows() -> None:
    assert 'class="task-current-stage"' in APP_JS
    assert ".task-manager-needs_input .task-current-stage" in PLATFORM_THEME_CSS
    assert ".task-manager-needs_input .task-approval-badge" in PLATFORM_THEME_CSS
    assert '<div class="task-filter-actions">' in INDEX_HTML
    filter_row = INDEX_HTML.split('<div class="task-filter-row">', 1)[1].split(
        '<div id="taskManagerList"', 1
    )[0]
    assert filter_row.index('id="taskSortSelect"') < filter_row.index('id="taskBulkModeBtn"')


def test_terminal_cards_and_result_pages_do_not_repeat_the_same_status() -> None:
    assert "const showCurrentStage" in APP_JS
    assert "${showCurrentStage ?" in APP_JS
    assert 'classList.add("is-result-summary")' in APP_JS
    assert ".practice-status-banner.is-result-summary" in PLATFORM_THEME_CSS
    assert 'completion.primary_code === "generation_incomplete"' in APP_JS
    assert '"部分题目已生成"' in APP_JS
    assert "Array.from(new Set(" in APP_JS
    assert "需关注的复核风险" in APP_JS


def test_visible_task_and_review_controls_have_effective_feedback() -> None:
    assert 'id="taskStatPaused"' in INDEX_HTML
    assert 'data-filter="paused"' in INDEX_HTML
    assert 'paused: scopedTasks.filter' in APP_JS
    assert 'paused: "已暂停"' in APP_JS
    assert 'item.open = rowIndex === 0' in APP_JS
    assert 'button.textContent = "正在读取…"' in APP_JS
    assert 'button.textContent = "已更新审查映射"' in APP_JS
    assert "审查报告映射读取失败" in APP_JS
    assert "embeddedMessage" in APP_JS
    assert "文档公式数量低于预期，请复核公式是否完整" in APP_JS
    assert 'label !== phaseSteps[phaseIndex - 1]' in APP_JS
    assert 'fas fa-chart-line' in APP_JS


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
    assert '  "ark_image",' not in APP_JS.split("const HIDDEN_USER_PROVIDER_NAMES", 1)[1].split("]);", 1)[0]
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


def test_image_route_is_user_selected_and_main_mode_requires_configuration() -> None:
    assert 'id="imageOrchestrationSwitch"' in INDEX_HTML
    assert 'id="practiceImageOrchestrationSwitch"' in INDEX_HTML
    assert 'id="knowledgeImageOrchestrationSwitch"' in INDEX_HTML
    assert "function imageOrchestrationMode(" in APP_JS
    assert "const imageFallbackConfigured = Boolean" in APP_JS
    assert 'image_provider: imageFallbackConfigured ?' in APP_JS
    assert 'image_model: imageFallbackConfigured ?' in APP_JS
    assert 'image_orchestration: imageOrchestrationMode("exam")' in APP_JS
    assert '已选择“主模型自主生图”' in APP_JS


def test_api_key_password_fields_belong_to_non_submitting_forms() -> None:
    assert '<form class="key-provider-card${expanded ? " expanded" : ""}" data-key-provider=' in APP_JS
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
    assert 'completed_with_issues: { icon: "fas fa-triangle-exclamation", label: "结果需复核" }' in APP_JS


def test_practice_completion_contract_drives_all_public_surfaces() -> None:
    assert 'const PRACTICE_COMPLETION_ISSUES_SCHEMA = "answer_book.practice_completion_issues.v1"' in APP_JS
    assert "function practiceCompletionContract(subject = {})" in APP_JS
    assert "completion.display_label" in APP_JS
    assert "completion.action_label" in APP_JS
    assert "completion.primary.icon" in APP_JS
    assert "结果需复核" in (ROOT / "web" / "index.html").read_text(encoding="utf-8")


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
    assert 'failed: { label: "未完成", meta: "请查看停止原因并按建议重试" }' in APP_JS
    assert '["failed", "cancelled", "paused", "completed", "completed_with_issues"].includes(normalized)' in APP_JS
    assert '"模型请求次数统计中"' in APP_JS
    assert '"模型请求次数暂无数据"' in APP_JS
    assert '"调用预算统计中"' in APP_JS
    assert '"剩余等待上限暂无数据"' in APP_JS
    assert "Number(task.network_attempted_count || 0)" not in APP_JS
    assert "function taskProgressPresentation(task, normalized, progress)" in APP_JS
    assert 'return { label: "调用状态", value: "未发起调用", showBar: false };' in APP_JS
    assert 'return { label: "任务状态", value: "已取消", showBar: false };' in APP_JS
    assert 'return { label: "等待操作", value: "确认后继续", showBar: false };' in APP_JS
    assert 'label: "生成结果"' in APP_JS
    assert 'value: `${completion.generated_count}/${completion.total_count} 题`' in APP_JS
    assert 'normalized === "failed" && !task?.is_generation_task && !task?.is_format_task' in APP_JS
    assert 'return { label: "流程停止位置", value: `${progress.percent}%`, showBar: true };' in APP_JS
    assert 'progressPresentation.showBar ? `<div class="manager-progress-track">' in APP_JS
    paused_start = APP_JS.index('if (job.status === "paused")')
    paused_end = APP_JS.index("latestPracticeRequest = job.payload", paused_start)
    paused_copy = APP_JS[paused_start:paused_end]
    assert "Number(job.network_attempted_count || 0)" not in paused_copy
    assert 'job.network_call_budget !== null' in paused_copy
    assert '"暂无数据"' in paused_copy


def test_failed_exam_uses_real_stage_and_hides_internal_diagnostics_by_default() -> None:
    current_stage = APP_JS[APP_JS.index("function effectiveCurrentStage"):APP_JS.index("function taskStatusMeta")]
    assert 'failed: "failed"' not in current_stage
    assert 'if (!examWorkflow && ["failed", "cancelled", "paused"].includes(task.status)) return task.status;' in current_stage
    assert 'current || (["failed", "cancelled", "paused"].includes(task.status)' in current_stage
    stage_progress = APP_JS[APP_JS.index("function executionStageProgress"):APP_JS.index("function buildTaskExecutionDetail")]
    assert 'visibleStepStage(progressStage) === current' in stage_progress
    assert 'return { percent: 0, label: "本阶段未完成", measurable: true };' in stage_progress
    assert '整体流程未完成 · 已完成 ${completedStages.size} 个子阶段' in APP_JS
    assert 'id="diagnosticsTechnicalDetails"' in INDEX_HTML
    assert '查看技术详情（文件路径与日志事件）' in INDEX_HTML
    assert '未找到可用的教材候选依据' in APP_JS
    assert '答案配图需要人工复核' in APP_JS
    assert 'id="resultDeliveryVerdict"' in INDEX_HTML
    assert 'title = "可正式交付"' in APP_JS
    assert 'id="resultSupportFilesDetails"' in INDEX_HTML
    assert 'answer_book_review_candidate.docx' in APP_JS
    assert 'map((item) => publicDiagnosticMessage(item))' in APP_JS
    assert 'id="deliveryPackageHint"' in INDEX_HTML
    assert 'id="finalAcceptanceBtn" class="secondary-button"' in INDEX_HTML
    assert 'id="deliveryPackageBtn" class="primary-button"' in INDEX_HTML
    assert '>下载正式交付包</button>' in INDEX_HTML
    assert 'setText("metricFileCount", String(primaryFiles.length));' in APP_JS
    assert 'setText("metricFileCount", String(primaryCount));' in APP_JS
    assert 'class="result-question-outline"' in APP_JS
    assert 'data-result-anchor=' in APP_JS
    assert 'taskStateLabel = isActionRequiredTask(task)' in APP_JS


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
    assert flow.count('!silent && currentPage !== "task"') == 3
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


def test_practice_workspace_draft_actions_ignore_empty_history_and_report_failures() -> None:
    assert "function restorablePracticeWorkspaceDraft(record)" in APP_JS
    assert "restorablePracticeWorkspaceDraft(pinned.record)" in APP_JS
    assert "restorablePracticeWorkspaceDraft(active)" in APP_JS
    assert "restorablePracticeWorkspaceDraft(item)" in APP_JS
    assert "openPracticeEntry(mode, false, false)" in APP_JS
    assert "openKnowledgeEntry(false)" in APP_JS
    assert "function runPracticeWorkspaceDraftAction(button, action, errorTitle)" in APP_JS
    assert 'title: errorTitle, tone: "danger"' in APP_JS


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


def test_api_key_platforms_use_single_card_progressive_disclosure() -> None:
    key_cards_start = APP_JS.index("function renderKeyProviderCards()")
    key_cards_end = APP_JS.index("async function recoverDamagedApiConfiguration", key_cards_start)
    key_cards = APP_JS[key_cards_start:key_cards_end]

    assert 'data-key-card-toggle="${escapeHtml(name)}"' in key_cards
    assert 'class="key-provider-details${expanded ? "" : " hidden"}"' in key_cards
    assert 'expandedKeyProviderName = shouldExpand ? selectedName : "";' in key_cards
    assert 'aria-expanded="${expanded ? "true" : "false"}"' in key_cards


def test_monitor_prioritizes_health_and_collapses_infrequent_settings() -> None:
    monitor_start = INDEX_HTML.index('<section id="page-monitor"')
    monitor_end = INDEX_HTML.index('</main>', monitor_start)
    monitor = INDEX_HTML[monitor_start:monitor_end]

    assert 'id="systemMonitorPanel"' in monitor
    assert '<details class="monitor-advanced-settings">' in monitor
    assert 'id="monitorAdvancedSummary"' in monitor
    assert monitor.index('id="systemMonitorPanel"') < monitor.index('id="storagePanel"')
    assert monitor.index('id="systemMonitorPanel"') < monitor.index('<details class="monitor-advanced-settings">') < monitor.index('id="storagePanel"')
    assert monitor.count('<details class="monitor-advanced-settings">') == 1
    assert "function syncMonitorAdvancedSummary()" in APP_JS
    assert "#page-monitor #systemMonitorPanel { order: 1; }" in PLATFORM_THEME_CSS
    assert "#page-monitor #systemMonitorPanel > .monitor-advanced-settings" in PLATFORM_THEME_CSS
    assert '<h3><i class="fas fa-gauge-high"></i>服务概况</h3>' in INDEX_HTML
    assert 'id="systemAccessHost"' in INDEX_HTML
    assert 'setText("systemAccessHost", host.access_host || "本机服务");' in APP_JS
    assert 'setText("systemMonitorSubtitle", "展示当前服务电脑的实时运行记录");' in APP_JS


def test_desktop_operational_pages_preserve_balanced_layouts() -> None:
    assert "@media (max-width: 1100px)" in PLATFORM_THEME_CSS
    assert "#page-keys .key-provider-card.expanded {\n  grid-column: 1 / -1;" in PLATFORM_THEME_CSS
    assert "#page-textbook .file-card-grid:has(> .library-option:only-child)" in PLATFORM_THEME_CSS
    assert "#page-exam #taskTextbookChecklist:has(> .library-option:only-child)" in PLATFORM_THEME_CSS
    assert "@media (min-width: 821px)" in WORD_FORMAT_HTML
    assert ".file-picker { min-height: 100px; }" in WORD_FORMAT_HTML
    assert ".submit-row { margin-top: 18px; margin-bottom: -18px;" in WORD_FORMAT_HTML


def test_practice_review_pages_keep_context_and_show_long_fields() -> None:
    assert "targetRect.top >= visibleTop && targetRect.top <= window.innerHeight * 0.68" in APP_JS
    assert '<textarea id="practicePlanGoalInput" rows="2" maxlength="300"></textarea>' in INDEX_HTML
    assert 'class="practice-plan-compact-textarea" rows="2" data-plan-field="target_skill"' in APP_JS
    assert 'class="practice-plan-wide">变化方式<textarea class="practice-plan-compact-textarea" rows="2" data-plan-field="variation_type"' in APP_JS
    assert "grid-template-columns: repeat(2, minmax(0, 1fr));" in PLATFORM_THEME_CSS
    assert "#page-tasks .task-manager-needs_input .task-manager-progress" in PLATFORM_THEME_CSS
    assert 'setText("knowledgeModelSummary", `${shortTaskModelName(textModel, textProviderName)}${textKeyState}`);' in APP_JS


def test_practice_question_actions_have_visible_labels() -> None:
    assert '<span>反馈</span>' in APP_JS
    assert '<span>编辑</span>' in APP_JS
    assert '"重新生成"}</span>' in APP_JS
    assert "practice-question-action--primary" in APP_JS


def test_exam_material_selection_uses_one_flow_and_searchable_bounded_library() -> None:
    exam_start = INDEX_HTML.index('<section id="page-exam"')
    exam_end = INDEX_HTML.index('<section id="page-task"', exam_start)
    exam = INDEX_HTML[exam_start:exam_end]

    assert 'class="task-flow-strip"' not in exam
    assert 'id="examLibrarySearch"' in exam
    assert 'id="examLibraryToggle"' in exam
    assert "const EXAM_LIBRARY_PREVIEW_LIMIT = 8;" in APP_JS
    assert "function applyExamLibraryFilters()" in APP_JS
    assert 'class="exam-card-select"' in APP_JS
    assert '#page-exam .task-primary-actions,' in PLATFORM_THEME_CSS


def test_task_statistics_are_the_only_status_filter_and_secondary_actions_collapse() -> None:
    tasks_start = INDEX_HTML.index('<section id="page-tasks"')
    tasks_end = INDEX_HTML.index('<section id="page-monitor"', tasks_start)
    tasks = INDEX_HTML[tasks_start:tasks_end]

    assert 'class="task-stat-card active" data-filter="all"' in tasks
    assert 'class="task-filter-tabs"' not in tasks
    assert 'id="taskActiveFilterSummary"' in tasks
    assert 'class="task-card-more"' in APP_JS
    assert 'event.target.closest(".task-card-more") || event.target.closest(".task-technical-details")' in APP_JS
    assert 'bounded.slice(0, 1)' in APP_JS
    assert '#taskManagerList .task-card-more[open]' in APP_JS
    assert 'function initTaskCardMenus()' in APP_JS
    assert 'event.target.closest("#taskManagerList .task-card-more")' in APP_JS
    assert 'event.key !== "Escape"' in APP_JS
    assert 'aria-expanded="false"' in APP_JS
    assert '.task-manager-item.task-menu-open' in PLATFORM_THEME_CSS
    assert '.task-manager-list:has(.task-card-more[open])' in PLATFORM_THEME_CSS
    assert '当前显示：${kindLabels[activeTaskKind]' in APP_JS


def test_terminal_task_page_uses_static_state_copy_and_loading_ids_are_disclosed() -> None:
    assert 'id="taskPageDescription"' in INDEX_HTML
    assert 'id="totalProgressLabel"' in INDEX_HTML
    assert 'id="taskExecutionHeadingText"' in INDEX_HTML
    assert 'title: "任务未完成"' in APP_JS
    assert 'executionHeading: "停止阶段"' in APP_JS
    assert 'title: "任务已暂停"' in APP_JS
    assert 'executionHeading: "暂停位置"' in APP_JS
    assert 'activeTaskFilter !== "failed"' in APP_JS
    assert '<summary>查看任务标识</summary>' in INDEX_HTML
    assert 'row?.classList.toggle("flex"' not in APP_JS
    assert '#page-task[data-task-state="failed"] .progress-card' in PLATFORM_THEME_CSS
    assert '#page-task .task-page-title.inline-title > .icon-button' in PLATFORM_THEME_CSS
    assert 'grid-template-columns: 44px minmax(0, 1fr);' in PLATFORM_THEME_CSS
    assert '#page-task .task-page-title.inline-title > div' in PLATFORM_THEME_CSS


def test_completed_exam_with_issues_prioritizes_review_result() -> None:
    action_start = APP_JS.index("function taskManagerActions")
    action_end = APP_JS.index("function generationTaskManagerActions", action_start)
    actions = APP_JS[action_start:action_end]

    assert 'add(completedNeedsReview, "result"' in actions
    assert actions.index('add(completedNeedsReview, "result"') < actions.index('add(caps.view_progress || caps.view_detail, "detail"')


def test_feedback_requires_user_description_before_posting() -> None:
    start = APP_JS.index("async function submitSupportFeedback")
    end = APP_JS.index("function taskSupportContext", start)
    feedback = APP_JS[start:end]

    assert "await platformPrompt" in feedback
    assert "user_description: normalizedDescription" in feedback
    assert feedback.index("await platformPrompt") < feedback.index("sendSupportFeedback")


def test_practice_secondary_result_context_is_collapsed_behind_one_summary() -> None:
    assert 'id="practiceResultTools" class="practice-result-tools"' in INDEX_HTML
    assert "任务概况与筛选" in INDEX_HTML
    assert '$("practiceResultTools").open = false' in APP_JS


def test_environment_distinguishes_network_configuration_and_actual_model_call() -> None:
    assert 'id="modelConfigCheckIcon"' in INDEX_HTML
    assert 'id="modelCallCheckIcon"' in INDEX_HTML
    assert 'id="testExamModelRoutesBtn"' in INDEX_HTML
    assert "async function testExamModelRoutes()" in APP_JS
    assert 'api("/api/provider-test"' in APP_JS


def test_environment_requires_the_exact_supported_python_runtime() -> None:
    assert "env?.python_supported" in APP_JS
    assert '需要 Python ${env?.python_requirement || "3.11.x"}' in APP_JS
    assert 'rememberModelConnectionTest(route.provider' in APP_JS
    assert "function syncExamModelTestAvailability()" in APP_JS


def test_word_format_start_requires_a_selected_document() -> None:
    assert 'id="submit" class="primary" disabled' in WORD_FORMAT_HTML
    assert "function syncSubmitAvailability()" in WORD_FORMAT_HTML
    assert "settingsReady && Boolean($('#file').files[0])" in WORD_FORMAT_HTML


def test_desktop_focus_and_secondary_text_contrast_have_shared_guards() -> None:
    assert ':where(button, input, select, summary, [tabindex]):focus-visible' in PLATFORM_THEME_CSS
    assert "outline: 3px solid rgba(37, 99, 235, .42) !important;" in PLATFORM_THEME_CSS
    assert "color: #52617a;" in PLATFORM_THEME_CSS


def test_practice_requests_include_the_configured_image_route() -> None:
    practice_start = APP_JS.index("function practiceRequestPayload()")
    knowledge_start = APP_JS.index("function knowledgeRequestPayload()", practice_start)
    practice_flow = APP_JS[practice_start:knowledge_start]
    knowledge_flow = APP_JS[knowledge_start:APP_JS.index("function updateKnowledgeModelSummary", knowledge_start)]
    for flow in (practice_flow, knowledge_flow):
        assert 'image_provider: imageConfigured ? imageProvider : ""' in flow
        assert 'image_model: imageConfigured ? imageModel : ""' in flow
        assert "image_orchestration: imageOrchestrationMode(" in flow


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
    assert "不会额外调用图片生成模型" in APP_JS


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


def test_deep_task_pages_keep_user_facing_labels_and_dense_controls_coherent() -> None:
    assert "确认训练蓝图" in INDEX_HTML
    assert "practice-plan-source-disclosure" in APP_JS
    assert "处理未完成" in APP_JS
    assert "请点击“查看原因”了解详情" not in APP_JS
    assert "第 ${questionIndex + 1} 项" in APP_JS
    assert "原题 ${sourceNumber} · " in APP_JS
    assert "review-technical-details" in APP_JS
    assert "publicDiagnosticMessage(x, row.question_id)" in APP_JS
    assert "以下提示用于交付前后核对，不影响下载" in APP_JS
    assert "grid-template-columns: repeat(3, minmax(0, 1fr));" in (ROOT / "web" / "styles.css").read_text(encoding="utf-8")


def test_zero_result_and_long_task_list_controls_are_actionable() -> None:
    assert 'id="taskSearchInput"' in INDEX_HTML
    assert 'id="taskSearchClearBtn"' in INDEX_HTML
    assert "function taskSearchText(task = {})" in APP_JS
    assert "taskSearchText(task).includes(query)" in APP_JS
    assert "const showTaskLoading = taskManagerLoading && tasks.length === 0;" in APP_JS
    assert "#page-tasks .task-filter-actions {\n  flex: 1 1 760px;\n  flex-wrap: wrap;" in PLATFORM_THEME_CSS
    assert 'successfulCount > 0 ? "部分题目已生成" : "题目尚未生成"' in APP_JS
    assert 'successfulCount === 0 && item.code === "review_required"' in APP_JS
    assert '${generationFailed ? "" : `<button type="button" class="practice-question-action" data-practice-edit=' in APP_JS
    assert '${generationFailed ? "" : `<div class="practice-action-menu practice-action-menu--question"' in APP_JS


def test_batch_actions_and_partial_result_warnings_have_clear_hierarchy() -> None:
    assert "选择本页任务" in INDEX_HTML
    assert "下载已选 Word" in INDEX_HTML
    assert '"下载已选 Word"' in APP_JS
    assert 'const qualityPanel = ({ icon, title, description, actions = "", details = "" })' in APP_JS
    assert 'class="practice-quality__actions"' in APP_JS
    assert 'class="practice-quality__details"' in APP_JS
    assert "查看 ${secondaryReasons.length} 项复核提示" in APP_JS
    assert "#page-tasks #taskBulkCount { border-radius: 999px;" in PLATFORM_THEME_CSS
    assert "#page-practice .practice-quality__content strong { display: block;" in PLATFORM_THEME_CSS
    assert "#page-practice .practice-quality__actions { display: flex;" in PLATFORM_THEME_CSS


def test_monitor_refresh_controls_are_visible_and_report_busy_state() -> None:
    assert 'class="monitor-panel-actions"' in INDEX_HTML
    assert "async function refreshSystemStatusFromButton()" in APP_JS
    assert "async function refreshStorageOverviewFromButton()" in APP_JS
    assert 'button.setAttribute("aria-busy", "true")' in APP_JS
    assert "正在刷新" in APP_JS
    assert "正在统计" in APP_JS
    assert "#page-monitor .system-monitor-header .task-card-button" in PLATFORM_THEME_CSS


def test_continue_actions_use_standard_buttons_and_forward_semantics() -> None:
    assert 'class="primary-button" data-practice-continue' in APP_JS
    assert 'class="secondary-button" data-practice-config' in APP_JS
    assert 'data-practice-continue><i class="fas fa-arrow-right"></i>继续未完成项' in APP_JS
    assert '"history-continue", "green-action", "fas fa-arrow-right", "继续未完成项"' in APP_JS
    assert "#page-practice .practice-quality__actions .primary-button," in PLATFORM_THEME_CSS
    assert 'class="secondary-btn"' not in APP_JS


def test_key_notice_and_task_kind_empty_states_do_not_show_stale_actions() -> None:
    assert 'id="keyConfigNotice" class="result-card muted-card hidden" aria-live="polite"' in INDEX_HTML
    assert 'const emptyKindCreation = !showTaskLoading' in APP_JS
    assert 'format: "新建格式审查"' in APP_JS
    assert 'action.dataset.emptyAction = emptyKindCreation ? "create"' in APP_JS
    assert 'action.innerHTML = `<i class="${actionIcon}"></i><span>${actionLabel}</span>`;' in APP_JS
    assert 'if (kind === "format") openWordFormatReviewer();' in APP_JS
    assert 'eyebrow: "任务恢复"' in APP_JS


def test_model_setting_summaries_use_readable_labels_instead_of_raw_routes() -> None:
    assert "function readableModelLabel(value, cfg = {}, kind = \"text\")" in APP_JS
    assert "const modelLabel = readableModelLabel(modelName, cfg);" in APP_JS
    assert 'primaryHandlesImages ? "可直接读取图文材料"' in APP_JS
    assert ': "生图路线已配置"' in APP_JS
    assert "规则化绘图失败时使用" not in APP_JS


def test_word_format_rule_page_collapses_long_categories_without_hiding_editing() -> None:
    assert "const openRuleCategoryIndexes = {answer:new Set([0]), lecture:new Set([0])};" in WORD_FORMAT_HTML
    assert "function rememberRuleCategory(element)" in WORD_FORMAT_HTML
    assert 'return `<details class="rule-category"' in WORD_FORMAT_HTML
    assert "const containsActiveEdit = category.rows.some" in WORD_FORMAT_HTML
    assert "${ruleCount} 项规则" in WORD_FORMAT_HTML
    assert ".rule-category[open] > .rule-category-head" in WORD_FORMAT_HTML


def test_legacy_uploading_stage_is_presented_as_user_facing_work() -> None:
    task_contract_ui = (ROOT / "web" / "task-contract-ui.js").read_text(encoding="utf-8")
    assert 'uploading: "准备输入材料"' in task_contract_ui


def test_final_acceptance_issues_separate_public_actions_from_technical_details() -> None:
    styles_css = (ROOT / "web" / "styles.css").read_text(encoding="utf-8")

    assert 'class="final-acceptance-issue-list"' in APP_JS
    assert 'class="technical-details final-acceptance-technical"' in APP_JS
    assert '[/output missing:\\s*[^；;]+/gi, "正式 Word 文件尚未生成或已不可用"]' in APP_JS
    assert '[/figure_delivery\\s*:\\s*/gi, "答案配图不完整："]' in APP_JS
    assert '.replace(/对应题目\\s+题干要求/gi, "对应题目要求")' in APP_JS
    assert ".final-acceptance-issue-list li" in styles_css
    assert ".final-acceptance-technical ul" in styles_css


def test_practice_editor_stays_centered_with_persistent_header_and_actions() -> None:
    styles_css = (ROOT / "web" / "styles.css").read_text(encoding="utf-8")

    assert 'class="practice-editor-body"' in INDEX_HTML
    assert "height: min(760px, calc(100vh - 48px));" in styles_css
    assert ".practice-editor form {\n  display: grid;\n  grid-template-rows: auto minmax(0, 1fr) auto;" in styles_css
    assert ".practice-editor-body {" in styles_css
    assert "overflow-y: auto;" in styles_css
    assert "margin: auto;" in styles_css


def test_long_result_pages_keep_context_and_hide_internal_rendering_labels() -> None:
    assert 'requestAnimationFrame(() => window.scrollTo({ top: 0, left: 0, behavior: "auto" }));' in APP_JS
    assert 'aria-label="选择第 ${escapeHtml(item.number || String(idx + 1))} 题"' in APP_JS
    assert '<span>选择</span></label>' in APP_JS
    assert "不会额外调用图片生成模型" in APP_JS
    assert "不会调用 gpt-image-2" not in APP_JS
    assert '[/answer_coverage\\s*:\\s*/gi, ""]' in APP_JS
    assert '[/answer is pending review/gi, "答案内容仍需人工复核"]' in APP_JS
    assert 'const warningList = warnings.length' in APP_JS
    assert '有题目的答案内容仍需人工复核' in APP_JS
    assert '缺少原始解析草稿，生成质量需要人工复核' in APP_JS
    assert '.replace(/⟦(?:MATHML|LATEX):[\\s\\S]*?⟧/gi, "")' in APP_JS
    assert '<small>${escapeHtml(stemPreview)}${plainStem.length > 26 ? "..." : ""}</small>' in APP_JS
    assert "#page-practice .practice-exercise__select:has(input:checked)" in PLATFORM_THEME_CSS
    assert "min-width: 68px;" in PLATFORM_THEME_CSS


def test_practice_requirement_presets_are_compact_editable_and_locally_persisted() -> None:
    styles_css = (ROOT / "web" / "styles.css").read_text(encoding="utf-8")
    server_py = (ROOT / "app" / "server.py").read_text(encoding="utf-8")
    preset_store = (ROOT / "app" / "practice_requirement_presets.py").read_text(encoding="utf-8")

    assert 'id="practiceRequirementPresetsPopover"' in INDEX_HTML
    assert 'id="practiceRequirementPresetAdd"' in INDEX_HTML
    assert 'id="practiceScopeFocus" type="text" role="combobox"' in INDEX_HTML
    assert "输入补充要求，或选择已保存的要求" in INDEX_HTML
    assert 'id="practiceRequirementPresetSaveCurrent"' not in INDEX_HTML
    assert 'id="practiceRequirementPresetsManage"' not in INDEX_HTML
    assert 'data-requirement-preset-edit="${safeId}"' in APP_JS
    assert 'data-requirement-preset-delete="${safeId}"' in APP_JS
    assert 'addEventListener("click", () => createPracticeRequirementPreset())' in APP_JS
    assert 'title="${safeText}"' in APP_JS
    assert "text-overflow: ellipsis;" in styles_css
    assert ".practice-requirement-preset-text" in styles_css
    assert "white-space: nowrap;" in styles_css
    assert 'api("/api/practice/requirement-presets")' in APP_JS
    assert 'parsed.path == "/api/practice/requirement-presets"' in server_py
    assert 'LOCAL_CONFIG_DIR / "practice_requirement_presets.json"' in preset_store
