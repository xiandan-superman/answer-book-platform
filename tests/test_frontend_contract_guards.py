from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
APP_JS = (ROOT / "web" / "app.js").read_text(encoding="utf-8")
INDEX_HTML = (ROOT / "web" / "index.html").read_text(encoding="utf-8")


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


def test_local_app_exposes_verified_user_initiated_updates() -> None:
    assert 'id="checkUpdateBtn"' in INDEX_HTML
    assert "async function checkPlatformUpdate()" in APP_JS
    assert 'api("/api/update/status?refresh=1")' in APP_JS
    assert 'api("/api/update/apply"' in APP_JS
    assert "API Key、教材、任务和输出不会被覆盖" in APP_JS


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
    assert 'value="balanced"' in INDEX_HTML
    assert 'value="quality"' in INDEX_HTML
    assert 'value="economy"' in INDEX_HTML
    assert 'id="examModelRoleDetails"' in INDEX_HTML
    assert 'EXAM_MODEL_PRESET_STORAGE_KEY = "answerBook.examModelPreset.v1"' in APP_JS
    assert 'reasoning: ["deepseek", "deepseek-v4-flash"]' in APP_JS
    assert 'answer: ["lingsuan_openai", "gpt-5.6-terra"]' in APP_JS
    assert 'correctness: ["lingsuan_openai", "gpt-5.6-sol"]' in APP_JS
    assert 'answer: ["lingsuan_google", "gemini-3.6-flash"]' in APP_JS


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


def test_practice_status_banner_tracks_blueprint_confirmation_stage() -> None:
    assert 'setPracticeStatusBanner("等待确认训练蓝图", "loading");' in APP_JS


def test_completed_generation_message_distinguishes_warnings_from_failures() -> None:
    assert "function completedGenerationTaskMessage(task = {})" in APP_JS
    assert "if (failedCount > 0)" in APP_JS
    assert "项非阻断提示" in APP_JS
    assert 'task.status === "completed_with_issues" ? "部分题目生成失败"' not in APP_JS


def test_blueprint_audit_failed_question_has_explicit_local_review_retry() -> None:
    assert 'item.generation_error?.code === "blueprint_audit_failed"' in APP_JS
    assert '"复审并生成本题"' in APP_JS
    assert "系统只修复并复审这一蓝图项" in APP_JS
    assert "response.practice_updates" in APP_JS
    assert "practice_updates: practiceUpdates" in APP_JS
    assert "hasAuditReviewFailures" in APP_JS
    assert "完成待复核 · 已生成" in APP_JS
    assert 'auditNeedsReview ? "存在未完成项（需复核）"' in APP_JS


def test_partial_practice_status_does_not_claim_everything_completed() -> None:
    assert "存在未完成项（需复核）" in (ROOT / "app" / "task_contracts.py").read_text(encoding="utf-8")
    assert 'completed_with_issues: { icon: "fas fa-triangle-exclamation", label: "完成待复核" }' in APP_JS


def test_review_candidate_download_prefers_explicit_candidate_filename() -> None:
    assert 'file.name === "answer_book_review_candidate.docx"' in APP_JS


def test_resumed_practice_job_uses_public_error_presentation() -> None:
    assert "job.error_presentation?.message" in APP_JS
    assert "function practicePublicErrorText" in APP_JS
    assert "诊断编号：${supportId}" in APP_JS
    assert 'action === "job-config"' in APP_JS
    assert 'confirmText: configurationRequired ? "检查 API 配置" : "从检查点重试"' in APP_JS
    assert 'String(presentation?.kind || "")' in APP_JS


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
    assert "knowledgeSourceFiles = Array.isArray(request.source_files)" in reuse


def test_task_manager_labels_creation_time_and_truncates_only_material_name() -> None:
    assert "function shortTaskMaterialName(value, limit = 18)" in APP_JS
    assert "function shortTaskModelName(value, provider = \"\")" in APP_JS
    assert "${kindMeta.label} · ${shortTaskModelName(primaryModel, task.provider)} · ${shortTaskMaterialName(materialName)}" in APP_JS
    assert "开始于 ${escapeHtml(formatTaskTimestamp(task.created_at))}" in APP_JS


def test_task_manager_opens_all_tasks_instead_of_action_required_filter() -> None:
    assert 'onclick="openTaskManager()" aria-label="打开任务管理"' in INDEX_HTML
    start = APP_JS.index('function openTaskManager(kind = "all")')
    end = APP_JS.index("function openWordFormatReviewer", start)
    assert 'filterTasks("all");' in APP_JS[start:end]


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
    assert "openPracticeEditor(index, generatedCandidate)" in save_flow
    assert "本次生成候选均已保留" in save_flow
    assert 'editConflict = error?.code === "practice_edit_conflict"' in editor_flow
    assert "当前填写内容仍保留在编辑框中" in editor_flow
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
