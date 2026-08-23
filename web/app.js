const $ = (id) => document.getElementById(id);
let taskPollTimer = null;
let taskManagerPollTimer = null;
let taskManagerPollInFlight = false;
let systemMonitorPollTimer = null;
let systemMonitorPollInFlight = false;
let taskBulkMode = false;
const selectedTaskIds = new Set();
let providerConfigs = {};
let apiKeyFileInfo = {};
let apiKeyConfigLoadState = { providers: "loading", keyFile: "loading", recoveryAvailable: false };
let hybridExecutionSettings = {};
const keyConfigTests = {};
let libraryFiles = { exams: [], textbooks: [], exams_root: "", textbooks_root: "" };
let activeTaskId = "";
let currentPage = "home";
let selectedTextbookPaths = new Set();
let textbookSelectionInitialized = false;
let activeTextbookGroups = {};
let disabledTextbookGroupKeys = new Set();
let latestTasks = [];
let activeTaskFilter = "all";
let activeTaskKind = "all";
let activeTaskSort = "smart";
let taskManagerPage = 1;
const TASK_MANAGER_PAGE_SIZE = 20;
const FAILED_TASK_FEEDBACK_STORAGE_KEY = "answerBook.failedTaskFeedback.v1";
const FAILED_TASK_FEEDBACK_DISMISS_KEY = "answerBook.failedTaskFeedbackDismiss.v1";
let resultViewData = null;
let activeResultQuestionId = "";
let modelQuestionTypeTab = "text";
let modelConnectionTests = loadStoredModelConnectionTests();
let practiceSourceFiles = [];
let knowledgeSourceFiles = [];
const uploadFileReadChains = { practice: Promise.resolve(), knowledge: Promise.resolve() };
const uploadFileReadPending = { practice: 0, knowledge: 0 };
let currentPracticeSourceMode = "exam";
const practiceWorkspaceDrafts = { exam: null, knowledge: null };
const practiceWorkspaceDraftTimers = { exam: null, knowledge: null };
const practiceWorkspaceWriteChains = { exam: Promise.resolve(), knowledge: Promise.resolve() };
const practiceWorkspaceRestorePromises = { exam: Promise.resolve(false), knowledge: Promise.resolve(false) };
let practiceWorkspaceRestoreInProgress = false;
let latestPracticeSourceScope = null;
let latestPracticeSourceAnalysis = null;
let latestPracticePlan = null;
let pendingPracticePlanCandidate = null;
let latestPracticeRequest = null;
let latestPracticeSet = null;
const selectedPracticeExerciseIndexes = new Set();
const activePracticeWordExports = new Map();
const practiceWordRecoveryJobs = new Map();
const PRACTICE_WORD_EXPORT_POINTER_STORAGE_KEY = "answerBook.practiceWordExportPointers.v1";
const PRACTICE_WORD_EXPORT_POINTER_SCHEMA_VERSION = 1;
const PRACTICE_WORD_EXPORT_POINTER_TTL_MS = 7 * 24 * 60 * 60 * 1000;
const PRACTICE_WORD_EXPORT_POINTER_LIMIT = 24;
let practiceWordRecoveryPollTimer = null;
let practiceWordRecoveryRefreshInFlight = false;
let practiceWordRecoveryRefreshVersion = 0;
// 蓝图页按 plan_item_id 保存的题目草案：{ [plan_item_id]: { draft, adopted } }
const practicePlanDrafts = {};
const practicePlanRevisionReceipts = {};
let currentPlanDraftBlueprintKey = "";
let practiceSessionVersion = 0;
let practiceBatchId = "";
let practiceEditingIndex = -1;
let practiceEditorDraftKey = "";
let practiceEditorDraftBaseVersion = "";
let practiceEditorDraftSource = "manual";
let practiceEditorDraftTimer = null;
let currentPracticeRevisionCount = 0;
let currentPracticeHistoryId = "";
let activePracticeJobId = "";
let practiceNavigationVersion = 0;
let practiceRecoveryObserverVersion = 0;
let practiceRecoveryNoticeJob = null;
let practiceRecoveryNoticeDismissedKey = "";
let practiceRecoveryNoticeSignature = "";
let practicePreferenceSequence = 0;
let practiceDifficultySelectionOrder = 0;
let practiceVariantSelectionOrder = 0;
let practiceRegenerationInProgress = false;
const pendingReviewTaskIds = new Set();
const handledReviewDecisionRequests = new Set();
let examStructureReviewModalOpen = false;

const textModelRoles = {
  reasoning: {
    providerId: "reasoningProviderSelect",
    modelSelectId: "reasoningModelSelect",
    modelInputId: "reasoningModelInput",
    hintId: "reasoningModelHint",
    icon: "fa-list-check",
    label: "知识点识别与证据确认"
  },
  answer: {
    providerId: "answerProviderSelect",
    modelSelectId: "answerModelSelect",
    modelInputId: "answerModelInput",
    hintId: "answerModelHint",
    icon: "fa-pen-to-square",
    label: "结构化解析"
  },
  correctness: {
    providerId: "correctnessProviderSelect",
    modelSelectId: "correctnessModelSelect",
    modelInputId: "correctnessModelInput",
    hintId: "correctnessModelHint",
    icon: "fa-shield-check",
    label: "高风险正确性复核"
  }
};

const EXAM_MODEL_PRESET_STORAGE_KEY = "answerBook.examModelPreset.v1";
const examModelPresets = {
  balanced: {
    label: "稳定推荐",
    description: "DeepSeek 负责考点与证据，GPT-5.6 Terra 生成解析，GPT-5.6 Sol 只复核高风险题。",
    base: ["lingsuan_openai", "gpt-5.6-terra"],
    reasoning: ["deepseek", "deepseek-v4-flash"],
    answer: ["lingsuan_openai", "gpt-5.6-terra"],
    correctness: ["lingsuan_openai", "gpt-5.6-sol"],
    vision: ["lingsuan_openai", "gpt-5.6-terra"],
    image: ["bailian", "qwen-image-2.0-pro"],
  },
  quality: {
    label: "质量优先",
    description: "GPT-5.6 Sol 统一完成考点、解析、复核和直接读图，减少跨模型信息损失。",
    base: ["lingsuan_openai", "gpt-5.6-sol"],
    reasoning: ["lingsuan_openai", "gpt-5.6-sol"],
    answer: ["lingsuan_openai", "gpt-5.6-sol"],
    correctness: ["lingsuan_openai", "gpt-5.6-sol"],
    vision: ["lingsuan_openai", "gpt-5.6-sol"],
    image: ["bailian", "qwen-image-2.0-pro"],
  },
  economy: {
    label: "性价比",
    description: "Gemini 3.6 Flash 统一完成文字和图片理解，适合常规题量与成本敏感场景。",
    base: ["lingsuan_google", "gemini-3.6-flash"],
    reasoning: ["lingsuan_google", "gemini-3.6-flash"],
    answer: ["lingsuan_google", "gemini-3.6-flash"],
    correctness: ["lingsuan_google", "gemini-3.6-flash"],
    vision: ["lingsuan_google", "gemini-3.6-flash"],
    image: ["bailian", "qwen-image-2.0"],
  },
};

const pageOrder = ["home", "keys", "knowledge", "knowledge-models", "practice", "practice-models", "env", "textbook", "exam", "task", "result", "tasks", "monitor"];
const workflowStepPages = ["env", "exam", "task", "result"];
const taskStageGroups = [
  { key: "prepare", title: "准备真题", summary: "读取题目并确认题型和分值", stages: ["hybrid_preprocess", "environment", "extract_exam", "exam_structure_review", "question_understanding", "figure_schema_planning"] },
  { key: "evidence", title: "检索教材依据", summary: "判断考点并核对教材依据", stages: ["hybrid_upload", "cloud_queue", "recovered_after_restart", "cloud_pipeline", "textbook_index", "knowledge_planning", "retrieval", "evidence_selection"] },
  { key: "answer", title: "生成解析", summary: "组织答案并检查覆盖范围", stages: ["answer_generation", "answer_coverage"] },
  { key: "figures", title: "生成图件", summary: "绘制、审查和必要时回修图件", stages: ["figures"] },
  { key: "quality", title: "质量审查", summary: "检查内容完整性与专业表达", stages: ["content_quality", "content_quality_model_repair", "figures_after_content_quality_model_repair", "content_quality_local_repair"] },
  { key: "delivery", title: "生成交付物", summary: "生成 Word、渲染复核并最终验收", stages: ["awaiting_download", "hybrid_download", "local_delivery", "docx", "docx_model_repair", "docx_repair", "question_review", "render", "acceptance", "final_acceptance", "completed"] }
];
const stageProgressMilestones = {
  hybrid_preprocess: 2, hybrid_upload: 18, cloud_queue: 19, recovered_after_restart: 19, cloud_pipeline: 20,
  environment: 3, extract_exam: 6, exam_structure_review: 10, question_understanding: 13, figure_schema_planning: 16,
  textbook_index: 19, knowledge_planning: 25, retrieval: 31, evidence_selection: 40, answer_generation: 55,
  answer_coverage: 59, figures: 73, content_quality: 81, content_quality_model_repair: 83,
  figures_after_content_quality_model_repair: 85, content_quality_local_repair: 86, awaiting_download: 87,
  hybrid_download: 88, local_delivery: 90, docx: 91,
  docx_model_repair: 92, docx_repair: 93,
  question_review: 95, render: 97, acceptance: 98, final_acceptance: 99, completed: 100
};
const progressStageOrder = [
  "environment",
  "extract_exam",
  "exam_structure_review",
  "question_understanding",
  "figure_schema_planning",
  "textbook_index",
  "knowledge_planning",
  "retrieval",
  "evidence_selection",
  "answer_generation",
  "answer_coverage",
  "figures",
  "content_quality",
  "content_quality_model_repair",
  "figures_after_content_quality_model_repair",
  "content_quality_local_repair",
  "docx",
  "docx_model_repair",
  "docx_repair",
  "question_review",
  "render",
  "acceptance",
  "final_acceptance",
  "completed"
];

function pretty(value) {
  return JSON.stringify(value, null, 2);
}

function setText(id, value) {
  const el = $(id);
  if (el) el.textContent = value;
}

function startWizard() {
  goToPage("env");
}

function goToPage(page) {
  window.SupportTelemetry?.record("navigation", { action: "go_to_page", target: page });
  practiceNavigationVersion += 1;
  if (page !== currentPage) {
    if (currentPage === "knowledge") flushScheduledPracticeWorkspaceDraft("knowledge");
    if (currentPage === "practice") flushScheduledPracticeWorkspaceDraft(currentPracticeSourceMode);
  }
  const leavingPractice = currentPage === "practice" && page !== "practice";
  const leavingResult = currentPage === "result" && page !== "result";
  if (leavingPractice) {
    // Leaving the workspace only detaches this browser view. The durable
    // backend job continues and remains available from Task Manager.
    practiceSessionVersion += 1;
    rememberPracticeJob("");
    closePracticeScopeDrawer();
    if ($("practiceExerciseList")) $("practiceExerciseList").innerHTML = "";
  }
  if (leavingResult) {
    if ($("resultQuestionList")) $("resultQuestionList").innerHTML = "";
    if ($("resultQuestionDetail")) $("resultQuestionDetail").innerHTML = "";
  }
  currentPage = page;
  document.body.dataset.activePage = page;
  if (page !== "practice") $("practiceWorkflowActions")?.classList.add("hidden");
  if (page !== "tasks") stopTaskManagerPolling();
  if (page !== "monitor") stopSystemMonitorPolling();
  document.querySelectorAll(".page").forEach((el) => el.classList.remove("active"));
  const target = $(`page-${page}`);
  if (target) target.classList.add("active");
  const indicator = $("stepIndicator");
  if (indicator) indicator.classList.toggle("hidden", !workflowStepPages.includes(page));
  updateStepIndicator(page);
  if (page === "env") {
    loadEnvironmentStatus().catch((err) => {
      $("environmentBox").textContent = String(err);
      setEnvironmentChecking("环境检查失败，请查看技术详情。", "error");
    });
  }
  if (page === "textbook") {
    loadSharedLibrarySettings().catch(() => {});
  }
  if (page === "task") {
    renderTaskStepList();
    loadTasks().catch(() => {});
  }
  if (page === "tasks") {
    loadTasks({ silent: true, includeLiveDetails: true }).catch(() => {});
    startTaskManagerPolling();
  }
  if (page === "monitor") {
    loadSystemStatus().catch(() => {});
    startSystemMonitorPolling();
  }
  if (page === "keys") {
    renderKeyProviderCards();
  }
  if (page === "practice") {
    updatePracticeModelSummary();
    syncPracticeWorkflowActions();
    if (latestPracticeSet && !$("practiceResults")?.classList.contains("hidden") && !$("practiceExerciseList")?.children.length) {
      renderPracticeResults(latestPracticeSet);
    }
  }
  if (page === "practice-models" || page === "knowledge-models") {
    populateTaskModelSettings(page === "practice-models" ? "practice" : "knowledge");
  }
  if (page === "knowledge") {
    updateKnowledgeModelSummary();
  }
  if (page === "result") {
    hydrateResultPage().catch(() => syncResultFiles());
  }
  // 页面切换时立即回到顶部，避免粘性导航和步骤条在平滑滚动期间压住新页面标题。
  window.scrollTo({ top: 0, left: 0, behavior: "auto" });
}

function setPracticeWorkspaceMode(mode = "exam") {
  const nextMode = mode === "knowledge" ? "knowledge" : "exam";
  const previousMode = currentPracticeSourceMode;
  if (previousMode !== nextMode) {
    flushScheduledPracticeWorkspaceDraft(previousMode);
    capturePracticeWorkspaceDraft(previousMode);
  }
  currentPracticeSourceMode = nextMode;
  const knowledgeMode = currentPracticeSourceMode === "knowledge";
  document.querySelectorAll(".practice-knowledge-strategy").forEach((item) => item.classList.toggle("hidden", !knowledgeMode));
  document.querySelectorAll('input[name="practiceSetStrategy"]:not(.practice-knowledge-strategy input)').forEach((input) => input.closest("label")?.classList.toggle("hidden", knowledgeMode));
  if (knowledgeMode) {
    const selected = document.querySelector('input[name="practiceSetStrategy"]:checked');
    if (!selected || !["knowledge_overall", "knowledge_item_wise"].includes(selected.value)) {
      const overall = document.querySelector('input[name="practiceSetStrategy"][value="knowledge_overall"]');
      if (overall) overall.checked = true;
    }
  }
  setText("practiceWorkspaceEyebrow", "模拟出题 · 两阶段生成");
  setText("practiceWorkspaceTitle", knowledgeMode ? "知识点出题" : "按题生题");
  setText(
    "practiceWorkspaceSubtitle",
    knowledgeMode ? "提交知识材料，确认知识单元范围后生成可复核的针对性模拟题。" : "提交一道题或一套题，确认考点范围后生成针对性专项练习。"
  );
  setText("practiceGuideOneTitle", knowledgeMode ? "提交知识材料" : "提交原题材料");
  setText("practiceGuideOneCopy", knowledgeMode ? "粘贴教材原文、知识点说明或截图，也可以上传多个资料文件。" : "粘贴题目文字、截图，或上传图片、PDF、Word 等文件。");
  setText("practiceGuideTwoTitle", knowledgeMode ? "识别知识单元与范围" : "识别考点与范围");
  setText("practiceGuideTwoCopy", knowledgeMode ? "平台先拆解核心概念和知识单元，再由你确认参与出题的内容。" : "平台先拆解原题结构和考查范围，再由你确认参与出题的内容。");
  setText("practiceGuideThreeTitle", knowledgeMode ? "生成模拟题" : "生成专项练习");
  setText("practiceGuideThreeCopy", knowledgeMode ? "按确认的范围、难度与题型设计可编辑的蓝图和模拟题。" : "按确认的范围、难度与题型设计可编辑的训练蓝图和练习题。");
  setText("practiceAvailability", knowledgeMode ? "当前可用：上传知识材料" : "当前可用：上传题目");
  setText("practiceInputKicker", knowledgeMode ? "知识材料" : "原题");
  setText("practiceInputTitle", knowledgeMode ? "上传或粘贴知识点文档" : "上传或粘贴题目");
  setText("practiceUploadLabel", knowledgeMode ? "上传知识点文件" : "上传题目文件");
  setText("practicePasteHint", knowledgeMode ? "知识材料中的截图也可直接粘贴 (Cmd+V)" : "也可以直接粘贴截图 (Cmd+V)");
  setText("practicePresetHeading", knowledgeMode ? "知识点出题预设" : "快速预设");
  setText("practiceConfigTitle", knowledgeMode ? "出题配置" : "训练配置");
  setText("practiceFocusLabel", knowledgeMode ? "出题要求" : "专项要求");
  setText("practiceModelNote", knowledgeMode ? "知识点出题使用独立模型配置，API Key 始终从统一配置中心读取。" : "按题出题使用独立模型配置，API Key 始终从统一配置中心读取。");
  setText("practiceModelPageLabel", knowledgeMode ? "打开知识点出题模型配置" : "打开按题出题模型配置");
  setText("practiceGenerateLabel", "解析考点与范围");
  setText("practiceSubmitStageLabel", knowledgeMode ? "提交知识材料" : "提交原题");
  setText("practiceAnalysisSummaryLabel", knowledgeMode ? "知识材料分析" : "原题诊断");
  const textarea = $("practiceQuestionText");
  if (textarea) textarea.placeholder = knowledgeMode
    ? "粘贴知识点、教材章节或教学要求；也可按 Command/Ctrl + V 直接粘贴截图。公式可以使用 LaTeX。"
    : "粘贴题目文字，或在这里按 Command/Ctrl + V 直接粘贴截图。公式可以使用 LaTeX。";
  const focus = $("practiceFocus");
  if (focus) focus.placeholder = knowledgeMode ? "例如：覆盖核心概念、计算与综合应用，避免超纲" : "例如：重点练习受力分析，不增加超纲知识";
  $("practiceRailInput")?.setAttribute("title", knowledgeMode ? "知识材料输入" : "原题输入");
  $("practiceModelSettingsLink")?.setAttribute("title", knowledgeMode ? "打开知识点出题模型配置" : "打开按题出题模型配置");
  if (knowledgeMode && latestPracticeRequest?.source_mode === "knowledge") syncKnowledgeRequestToPracticeWorkspace(latestPracticeRequest);
  else if (previousMode !== nextMode) restorePracticeWorkspaceDraft(nextMode);
  updatePracticeModelSummary();
}

function blueprintReviewEnabled() {
  const source = currentPracticeSourceMode === "knowledge"
    ? $("knowledgeBlueprintReviewEnabled")
    : $("practiceBlueprintReviewEnabled");
  return source ? source.checked : latestPracticeRequest?.blueprint_review_enabled !== false;
}

const practiceSourceContentToggleIds = [
  "practiceIncludeSourceContent",
  "knowledgeIncludeSourceContent",
  "practiceScopeIncludeSourceContent"
];
const practiceSourceContentWarningIds = [
  "practiceSourceContentWarning",
  "knowledgeSourceContentWarning",
  "practiceScopeSourceContentWarning"
];

function includeSourceContentInGeneration() {
  const scopeToggle = $("practiceScopeIncludeSourceContent");
  if (scopeToggle) return scopeToggle.checked;
  const workspaceToggle = $("practiceIncludeSourceContent");
  if (workspaceToggle) return workspaceToggle.checked;
  const knowledgeToggle = $("knowledgeIncludeSourceContent");
  return knowledgeToggle ? knowledgeToggle.checked : latestPracticeRequest?.include_source_content_in_generation !== false;
}

function syncPracticeSourceContentPreference(enabled = includeSourceContentInGeneration(), originId = "") {
  const includeSourceContent = enabled !== false;
  practiceSourceContentToggleIds.forEach((toggleId) => {
    const toggle = $(toggleId);
    if (toggle && toggleId !== originId) toggle.checked = includeSourceContent;
  });
  practiceSourceContentWarningIds.forEach((warningId) => {
    $(warningId)?.classList.toggle("hidden", includeSourceContent);
  });
}

function requiredKnowledgePointsForPlanItem(planItem, sourceCatalog, generationStrategy = latestPracticePlan?.blueprint?.generation_strategy) {
  const refs = Array.from(new Set((planItem.source_refs || [planItem.source_question_id]).filter(Boolean)));
  const sourceById = new Map((sourceCatalog || []).map((source) => [String(source.source_question_id || ""), source]));
  const sourcePoints = [];
  refs.forEach((sourceId) => {
    (sourceById.get(String(sourceId))?.knowledge_points || []).forEach((point) => {
      const knowledgePoint = String(point || "").trim();
      if (knowledgePoint && !sourcePoints.includes(knowledgePoint)) sourcePoints.push(knowledgePoint);
    });
  });
  const current = Array.from(new Set((planItem.required_knowledge_points || planItem.knowledge_points || []).map((point) => String(point || "").trim()).filter(Boolean)));
  const comprehensive = ["targeted_set", "knowledge_overall"].includes(generationStrategy);
  if (!comprehensive) return sourcePoints.length ? sourcePoints : current;
  const retained = current.filter((point) => sourcePoints.includes(point));
  return retained.length ? retained : (sourcePoints.length ? sourcePoints : current);
}

function syncPlanItemRequiredKnowledgePoints(planItem, sourceCatalog, generationStrategy) {
  if (!planItem) return;
  planItem.required_knowledge_points = requiredKnowledgePointsForPlanItem(planItem, sourceCatalog, generationStrategy);
}

function defaultPlanDifficultyDesign(difficulty, questionType, structuralChange, targetSkill) {
  const defaults = {
    "基础": ["条件直接程度", "提示和解题支架程度"],
    "进阶": ["条件识别或转换要求", "方法选择与组合要求"],
    "挑战": ["知识综合与迁移程度", "隐含关系识别", "正向、逆向、比较、评价或优化任务"]
  };
  const level = Object.hasOwn(defaults, difficulty) ? difficulty : "进阶";
  const levers = [...defaults[level]];
  if (["计算题", "综合题"].includes(questionType) && !levers.includes("计算、论证或数据处理负担")) levers.push("计算、论证或数据处理负担");
  if (["逆向", "比较", "优化", "评价"].some((token) => String(structuralChange || "").includes(token))) levers.unshift("正向、逆向、比较、评价或优化任务");
  const selectedLevers = [...new Set(levers)].slice(0, 3);
  const skill = String(targetSkill || "目标能力").trim() || "目标能力";
  const rationale = level === "基础"
    ? `围绕${skill}保留必考知识点，条件表达更直接，并提供必要提示或支架。`
    : level === "进阶"
      ? `围绕${skill}要求识别或转换条件，并完成方法选择或知识组合。`
      : `围绕${skill}设置综合迁移、隐含关系或${structuralChange || "逆向/比较/优化"}要求，需作出独立判断。`;
  return { levers: selectedLevers, rationale };
}

function ensurePlanDifficultyDesign(planItem) {
  if (!planItem) return;
  const levers = Array.isArray(planItem.difficulty_levers) ? planItem.difficulty_levers.filter(Boolean) : [];
  const rationale = String(planItem.difficulty_rationale || "").trim();
  if (!levers.length || !rationale || /待补充/.test(`${levers.join(" ")} ${rationale}`)) {
    const design = defaultPlanDifficultyDesign(
      planItem.difficulty,
      planItem.question_type,
      planItem.structural_change,
      planItem.target_skill,
    );
    planItem.difficulty_levers = design.levers;
    planItem.difficulty_rationale = design.rationale;
  }
  planItem.difficulty_design_level = planItem.difficulty;
}

function showPracticePlanError(message) {
  const errorBox = $("practicePlanError") || $("practiceError");
  if (!errorBox) return;
  errorBox.textContent = message;
  errorBox.classList.remove("hidden");
  errorBox.scrollIntoView({ behavior: "smooth", block: "center" });
}

function syncPracticeBlueprintPath(enabled = blueprintReviewEnabled()) {
  $("practicePlanStageStep")?.classList.toggle("hidden", !enabled);
  $("practicePlanStageArrow")?.classList.toggle("hidden", !enabled);
  const generateStep = document.querySelector('.practice-step[data-stage="generate"] b');
  if (generateStep) generateStep.textContent = enabled ? "5" : "4";
  const backButton = $("practiceBackToPlanBtn");
  if (backButton) backButton.innerHTML = enabled
    ? '<i data-lucide="layout-grid" class="h-4 w-4"></i><span>返回蓝图</span>'
    : '<i data-lucide="list-checks" class="h-4 w-4"></i><span>返回范围</span>';
  window.lucide?.createIcons();
}

function openCurrentPracticeModelSettings() {
  goToPage(currentPracticeSourceMode === "knowledge" ? "knowledge-models" : "practice-models");
}

function capturePracticeWorkspaceDraft(mode) {
  if (!$("practiceQuestionText")) return;
  practiceWorkspaceDrafts[mode] = {
    question_text: $("practiceQuestionText").value,
    source_files: practiceSourceFiles.map((file) => ({ ...file })),
    count: $("practiceCount")?.value || "5",
    difficulty: $("practiceDifficulty")?.value || "基础到进阶",
    question_types: Array.from(document.querySelectorAll('input[name="practiceQuestionType"]:checked')).map((input) => input.value),
    focus: $("practiceFocus")?.value || "",
    include_source_content_in_generation: includeSourceContentInGeneration()
  };
}

function normalizeSourceFileList(files) {
  const seenIds = new Set();
  return Array.from(files || []).filter((file) => file && typeof file === "object").map((file) => {
    const normalized = { ...file };
    let itemId = String(normalized.upload_item_id || "").trim();
    if (!itemId || seenIds.has(itemId)) itemId = newUploadItemId();
    normalized.upload_item_id = itemId;
    seenIds.add(itemId);
    return normalized;
  });
}

function restorePracticeWorkspaceDraft(mode) {
  const draft = practiceWorkspaceDrafts[mode] || {};
  if ($("practiceQuestionText")) $("practiceQuestionText").value = draft.question_text || "";
  practiceSourceFiles = normalizeSourceFileList(draft.source_files);
  if ($("practiceCount")) $("practiceCount").value = draft.count || "5";
  if ($("practiceDifficulty")) $("practiceDifficulty").value = draft.difficulty || "基础到进阶";
  if ($("practiceFocus")) $("practiceFocus").value = draft.focus || "";
  syncPracticeSourceContentPreference(draft.include_source_content_in_generation !== false);
  const selectedTypes = new Set(draft.question_types || []);
  document.querySelectorAll('input[name="practiceQuestionType"]').forEach((input) => { input.checked = selectedTypes.has(input.value); });
  renderPracticeFilePreview();
  updatePracticeConfigSummary();
}

const PRACTICE_WORKSPACE_DB_NAME = "answerBook.practiceWorkspace.v1";
const PRACTICE_WORKSPACE_STORE = "drafts";

function openPracticeWorkspaceDatabase() {
  return new Promise((resolve, reject) => {
    if (!globalThis.indexedDB) {
      reject(new Error("当前浏览器不支持工作区草稿存储。"));
      return;
    }
    const request = indexedDB.open(PRACTICE_WORKSPACE_DB_NAME, 1);
    request.onupgradeneeded = () => {
      if (!request.result.objectStoreNames.contains(PRACTICE_WORKSPACE_STORE)) {
        request.result.createObjectStore(PRACTICE_WORKSPACE_STORE, { keyPath: "mode" });
      }
    };
    request.onsuccess = () => resolve(request.result);
    request.onerror = () => reject(request.error || new Error("无法打开工作区草稿存储。"));
  });
}

async function practiceWorkspaceDatabaseOperation(mode, operation, value = null) {
  const database = await openPracticeWorkspaceDatabase();
  try {
    return await new Promise((resolve, reject) => {
      const transaction = database.transaction(PRACTICE_WORKSPACE_STORE, operation === "get" ? "readonly" : "readwrite");
      const store = transaction.objectStore(PRACTICE_WORKSPACE_STORE);
      const request = operation === "get" ? store.get(mode) : operation === "delete" ? store.delete(mode) : store.put(value);
      request.onsuccess = () => resolve(request.result || null);
      request.onerror = () => reject(request.error || new Error("工作区草稿操作失败。"));
    });
  } finally {
    database.close();
  }
}

function queuePracticeWorkspaceDatabaseOperation(mode, operation, value = null) {
  const normalizedMode = mode === "knowledge" ? "knowledge" : "exam";
  const queuedOperation = practiceWorkspaceWriteChains[normalizedMode]
    .catch(() => {})
    .then(() => practiceWorkspaceDatabaseOperation(normalizedMode, operation, value));
  practiceWorkspaceWriteChains[normalizedMode] = queuedOperation;
  return queuedOperation;
}

function copyPracticeWorkspaceValue(value) {
  try { return JSON.parse(JSON.stringify(value)); } catch (_error) { return null; }
}

function capturePracticeScopeConfig() {
  return {
    selected_source_ids: Array.from(document.querySelectorAll('input[name="practiceSourceQuestion"]:checked')).map((input) => input.value),
    strategy: document.querySelector('input[name="practiceSetStrategy"]:checked')?.value || "",
    targeted_count: $("practiceTargetedCount")?.value || "5",
    knowledge_per_count: $("practiceKnowledgePerCount")?.value || "1",
    variants_per_question: $("practiceVariantsPerQuestion")?.value || "1",
    difficulty_counts: {
      basic: $("practiceDifficultyBasicCount")?.value || "0",
      intermediate: $("practiceDifficultyIntermediateCount")?.value || "0",
      challenge: $("practiceDifficultyChallengeCount")?.value || "0"
    },
    focus: $("practiceScopeFocus")?.value || "",
    question_types: Array.from(document.querySelectorAll('input[name="practiceScopeQuestionType"]:checked')).map((input) => input.value),
    granularity: $("practiceScopeGranularity")?.value || "atomic",
    include_source_content_in_generation: includeSourceContentInGeneration()
  };
}

function restorePracticeScopeConfig(config = {}) {
  if ($("practiceScopeGranularity")) $("practiceScopeGranularity").value = config.granularity === "top_level" ? "top_level" : "atomic";
  if (latestPracticeSourceScope) latestPracticeSourceScope.granularity = $("practiceScopeGranularity")?.value || "atomic";
  if (latestPracticeSourceScope?.questions) renderPracticeScopeQuestionList(latestPracticeSourceScope.questions);
  const selectedIds = new Set(config.selected_source_ids || []);
  const selectedIdsRecorded = Array.isArray(config.selected_source_ids);
  document.querySelectorAll('input[name="practiceSourceQuestion"]').forEach((input) => {
    if (selectedIdsRecorded) input.checked = selectedIds.has(input.value);
  });
  document.querySelectorAll('input[name="practiceSetStrategy"]').forEach((input) => { input.checked = input.value === config.strategy; });
  if ($("practiceTargetedCount") && config.targeted_count) $("practiceTargetedCount").value = config.targeted_count;
  if ($("practiceKnowledgePerCount") && config.knowledge_per_count) $("practiceKnowledgePerCount").value = config.knowledge_per_count;
  if ($("practiceVariantsPerQuestion") && config.variants_per_question) $("practiceVariantsPerQuestion").value = config.variants_per_question;
  if ($("practiceDifficultyBasicCount")) $("practiceDifficultyBasicCount").value = config.difficulty_counts?.basic ?? $("practiceDifficultyBasicCount").value;
  if ($("practiceDifficultyIntermediateCount")) $("practiceDifficultyIntermediateCount").value = config.difficulty_counts?.intermediate ?? $("practiceDifficultyIntermediateCount").value;
  if ($("practiceDifficultyChallengeCount")) $("practiceDifficultyChallengeCount").value = config.difficulty_counts?.challenge ?? $("practiceDifficultyChallengeCount").value;
  if ($("practiceScopeFocus")) $("practiceScopeFocus").value = config.focus || "";
  const types = new Set(config.question_types || []);
  document.querySelectorAll('input[name="practiceScopeQuestionType"]').forEach((input) => { input.checked = types.has(input.value); });
  syncPracticeSourceContentPreference(config.include_source_content_in_generation !== false);
  updatePracticeStrategySettings();
  updatePracticeScopePreview();
}

function captureKnowledgeInputWorkspace() {
  return {
    title: $("knowledgeTitleInput")?.value || "",
    text: $("knowledgeTextInput")?.value || "",
    source_files: knowledgeSourceFiles.map((file) => ({ ...file })),
    focus: $("knowledgeFocusInput")?.value || "",
    question_types: Array.from(document.querySelectorAll('input[name="knowledgeQuestionType"]:checked')).map((input) => input.value),
    blueprint_review_enabled: $("knowledgeBlueprintReviewEnabled")?.checked !== false,
    include_source_content_in_generation: $("knowledgeIncludeSourceContent")?.checked !== false
  };
}

function restoreKnowledgeInputWorkspace(input = {}) {
  if ($("knowledgeTitleInput")) $("knowledgeTitleInput").value = input.title || "";
  if ($("knowledgeTextInput")) $("knowledgeTextInput").value = input.text || "";
  knowledgeSourceFiles = normalizeSourceFileList(input.source_files);
  if ($("knowledgeFocusInput")) $("knowledgeFocusInput").value = input.focus || "";
  const types = new Set(input.question_types || []);
  document.querySelectorAll('input[name="knowledgeQuestionType"]').forEach((item) => { item.checked = types.has(item.value); });
  if ($("knowledgeBlueprintReviewEnabled")) $("knowledgeBlueprintReviewEnabled").checked = input.blueprint_review_enabled !== false;
  if ($("knowledgeIncludeSourceContent")) $("knowledgeIncludeSourceContent").checked = input.include_source_content_in_generation !== false;
  syncPracticeSourceContentPreference(input.include_source_content_in_generation !== false);
  renderKnowledgeFilePreview();
}

function currentPracticeWorkspaceStage() {
  const activeStage = $("practiceWorkflowActions")?.dataset.stage || "submit";
  if (activeStage === "plan" && latestPracticePlan) return "plan";
  if (activeStage === "scope" && latestPracticeSourceScope) return "scope";
  return "input";
}

function showPracticeWorkspaceDraftNotice(mode, message) {
  const notice = mode === "knowledge" && currentPage === "knowledge" ? $("knowledgeWorkspaceDraftNotice") : $("practiceWorkspaceDraftNotice");
  if (!notice) return;
  const text = notice.querySelector("span");
  if (text) text.textContent = message;
  notice.classList.remove("hidden");
  if (currentPage === "practice") $("practiceWorkspaceDraftClearActive")?.classList.remove("hidden");
}

function capturePersistentPracticeWorkspace(mode = currentPracticeSourceMode) {
  const normalizedMode = mode === "knowledge" ? "knowledge" : "exam";
  const ownsActivePracticeWorkspace = currentPage === "practice" && currentPracticeSourceMode === normalizedMode;
  const onKnowledgeInputPage = normalizedMode === "knowledge" && !ownsActivePracticeWorkspace;
  const stage = ownsActivePracticeWorkspace ? currentPracticeWorkspaceStage() : "input";
  if (!practiceBatchId) practiceBatchId = newPracticeBatchId();
  if (ownsActivePracticeWorkspace) capturePracticeWorkspaceDraft(normalizedMode);
  const input = onKnowledgeInputPage ? captureKnowledgeInputWorkspace() : copyPracticeWorkspaceValue(practiceWorkspaceDrafts[normalizedMode] || {});
  return {
    mode: normalizedMode,
    schema: "practice_workspace_draft.v1",
    stage,
    practice_batch_id: practiceBatchId,
    updated_at: Date.now(),
    input,
    request: stage === "input" ? null : copyPracticeWorkspaceValue(latestPracticeRequest),
    source_scope: stage === "input" ? null : copyPracticeWorkspaceValue(latestPracticeSourceScope),
    source_analysis: stage === "input" ? null : copyPracticeWorkspaceValue(latestPracticeSourceAnalysis),
    scope_config: stage === "scope" ? capturePracticeScopeConfig() : null,
    plan: stage === "plan" ? copyPracticeWorkspaceValue(latestPracticePlan) : null,
    plan_drafts: stage === "plan" ? copyPracticeWorkspaceValue(practicePlanDrafts) : null,
    revision_receipts: stage === "plan" ? copyPracticeWorkspaceValue(practicePlanRevisionReceipts) : null,
    pending_plan_candidate: stage === "plan" ? copyPracticeWorkspaceValue(pendingPracticePlanCandidate) : null
  };
}

async function persistPracticeWorkspaceDraft(mode = currentPracticeSourceMode, capturedRecord = null) {
  if (practiceWorkspaceRestoreInProgress && !capturedRecord) return;
  const normalizedMode = mode === "knowledge" ? "knowledge" : "exam";
  const record = capturedRecord || capturePersistentPracticeWorkspace(normalizedMode);
  try {
    await queuePracticeWorkspaceDatabaseOperation(normalizedMode, "put", record);
  } catch (_error) {
    showPracticeWorkspaceDraftNotice(normalizedMode, "浏览器无法保存当前工作区；刷新前请先提交任务或复制重要内容。 ");
  }
}

function schedulePracticeWorkspaceDraftSave(mode = currentPracticeSourceMode) {
  if (practiceWorkspaceRestoreInProgress) return;
  const normalizedMode = mode === "knowledge" ? "knowledge" : "exam";
  if (practiceWorkspaceDraftTimers[normalizedMode]) clearTimeout(practiceWorkspaceDraftTimers[normalizedMode]);
  practiceWorkspaceDraftTimers[normalizedMode] = setTimeout(() => {
    practiceWorkspaceDraftTimers[normalizedMode] = null;
    persistPracticeWorkspaceDraft(normalizedMode);
  }, 150);
}

function flushScheduledPracticeWorkspaceDraft(mode = currentPracticeSourceMode) {
  const normalizedMode = mode === "knowledge" ? "knowledge" : "exam";
  if (!practiceWorkspaceDraftTimers[normalizedMode]) return;
  clearTimeout(practiceWorkspaceDraftTimers[normalizedMode]);
  practiceWorkspaceDraftTimers[normalizedMode] = null;
  persistPracticeWorkspaceDraft(normalizedMode);
}

function persistUploadSelectionDraft(mode = currentPracticeSourceMode) {
  const normalizedMode = mode === "knowledge" ? "knowledge" : "exam";
  if (practiceWorkspaceDraftTimers[normalizedMode]) {
    clearTimeout(practiceWorkspaceDraftTimers[normalizedMode]);
    practiceWorkspaceDraftTimers[normalizedMode] = null;
  }
  const record = capturePersistentPracticeWorkspace(normalizedMode);
  return persistPracticeWorkspaceDraft(normalizedMode, record);
}

async function restorePersistentPracticeWorkspace(mode, sessionVersion) {
  const normalizedMode = mode === "knowledge" ? "knowledge" : "exam";
  let record;
  try {
    record = await queuePracticeWorkspaceDatabaseOperation(normalizedMode, "get");
  } catch (_error) {
    return false;
  }
  if (!record || record.schema !== "practice_workspace_draft.v1" || sessionVersion !== practiceSessionVersion) return false;
  practiceWorkspaceRestoreInProgress = true;
  try {
    practiceBatchId = record.practice_batch_id || practiceBatchId;
    if (record.stage === "input") {
      if (normalizedMode === "knowledge" && currentPage === "knowledge") restoreKnowledgeInputWorkspace(record.input || {});
      else {
        practiceWorkspaceDrafts[normalizedMode] = record.input || {};
        restorePracticeWorkspaceDraft(normalizedMode);
      }
      await persistPracticeWorkspaceDraft(normalizedMode, capturePersistentPracticeWorkspace(normalizedMode));
      showPracticeWorkspaceDraftNotice(normalizedMode, normalizedMode === "knowledge" ? "已恢复上次未提交的知识材料、文件和参数。" : "已恢复上次未提交的题目、文件和参数。");
      syncPracticeSubmitAvailability();
      return true;
    }
    latestPracticeRequest = record.request || null;
    latestPracticeSourceScope = record.source_scope || null;
    latestPracticeSourceAnalysis = record.source_analysis || null;
    setPracticeWorkspaceMode(normalizedMode);
    goToPage("practice");
    if (record.stage === "scope" && latestPracticeSourceScope) {
      renderPracticeSourceSelection({ source_scope: latestPracticeSourceScope, source_analysis: latestPracticeSourceAnalysis });
      restorePracticeScopeConfig(record.scope_config || {});
      showPracticeWorkspaceDraftNotice(normalizedMode, "已恢复上次修改过的范围和出题参数。");
      setPracticeStatusBanner("已恢复未完成的范围确认", "warning");
      return true;
    }
    if (record.stage === "plan" && record.plan) {
      latestPracticePlan = record.plan;
      for (const key of Object.keys(practicePlanDrafts)) delete practicePlanDrafts[key];
      Object.assign(practicePlanDrafts, record.plan_drafts || {});
      for (const key of Object.keys(practicePlanRevisionReceipts)) delete practicePlanRevisionReceipts[key];
      Object.assign(practicePlanRevisionReceipts, record.revision_receipts || {});
      pendingPracticePlanCandidate = record.pending_plan_candidate || null;
      currentPlanDraftBlueprintKey = practiceBlueprintKey(latestPracticePlan);
      renderPracticePlan(latestPracticePlan);
      if (pendingPracticePlanCandidate) {
        $("practicePlanCandidateActions")?.classList.remove("hidden");
        if ($("practicePlanConfirmBtn")) $("practicePlanConfirmBtn").disabled = true;
      }
      showPracticeWorkspaceDraftNotice(normalizedMode, "已恢复上次修改过的蓝图和已生成草案。");
      setPracticeStatusBanner("已恢复未完成的蓝图审查", "warning");
      return true;
    }
    return false;
  } finally {
    practiceWorkspaceRestoreInProgress = false;
  }
}

async function clearPersistentPracticeWorkspace(mode) {
  const normalizedMode = mode === "knowledge" ? "knowledge" : "exam";
  try { await queuePracticeWorkspaceDatabaseOperation(normalizedMode, "delete"); } catch (_error) {}
  $("practiceWorkspaceDraftNotice")?.classList.add("hidden");
  $("knowledgeWorkspaceDraftNotice")?.classList.add("hidden");
  $("practiceWorkspaceDraftClearActive")?.classList.add("hidden");
}

async function clearAndStartFreshPracticeWorkspace(mode, knowledgeInputPage = false) {
  const normalizedMode = mode === "knowledge" ? "knowledge" : "exam";
  if (practiceWorkspaceDraftTimers[normalizedMode]) clearTimeout(practiceWorkspaceDraftTimers[normalizedMode]);
  practiceWorkspaceDraftTimers[normalizedMode] = null;
  await clearPersistentPracticeWorkspace(mode);
  if (mode === "knowledge" && knowledgeInputPage) openKnowledgeEntry();
  else openPracticeEntry(mode);
}

function syncKnowledgeRequestToPracticeWorkspace(request = {}) {
  if (request.source_mode !== "knowledge") return;
  if ($("practiceQuestionText")) $("practiceQuestionText").value = request.question_text || "";
  practiceSourceFiles = normalizeSourceFileList(request.source_files);
  if ($("practiceCount") && request.count) $("practiceCount").value = String(request.count);
  if ($("practiceDifficulty") && request.difficulty) $("practiceDifficulty").value = request.difficulty;
  if ($("practiceFocus")) $("practiceFocus").value = request.focus || "";
  syncPracticeSourceContentPreference(request.include_source_content_in_generation !== false);
  const selectedTypes = new Set(request.question_types || []);
  document.querySelectorAll('input[name="practiceQuestionType"]').forEach((input) => { input.checked = selectedTypes.has(input.value); });
  renderPracticeFilePreview();
  updatePracticeConfigSummary();
}

function openPracticeEntry(mode = "exam", openModelSettings = false) {
  if (openModelSettings) {
    goToPage(mode === "knowledge" ? "knowledge-models" : "practice-models");
    return;
  }
  if (currentPage === "knowledge") flushScheduledPracticeWorkspaceDraft("knowledge");
  if (currentPage === "practice") flushScheduledPracticeWorkspaceDraft(currentPracticeSourceMode);
  beginNewPracticeSession();
  latestPracticeSourceScope = null;
  latestPracticeSourceAnalysis = null;
  latestPracticePlan = null;
  latestPracticeRequest = null;
  latestPracticeSet = null;
  syncPracticeSourceContentPreference(true);
  for (const key of Object.keys(practicePlanRevisionReceipts)) delete practicePlanRevisionReceipts[key];
  practiceSourceFiles = [];
  if ($("practiceQuestionText")) $("practiceQuestionText").value = "";
  renderPracticeFilePreview();
  closePracticeScopeDrawer();
  $("practiceLoading")?.classList.add("hidden");
  $("practicePlanReview")?.classList.add("hidden");
  $("practiceResults")?.classList.add("hidden");
  $("practiceEmpty")?.classList.remove("hidden");
  setPracticeStage("submit");
  setPracticeStageDescription(mode === "knowledge" ? "请先提交知识材料，平台将解析知识单元与范围。" : "请先提交题目材料，平台将解析考点与原题范围。");
  setPracticeStatusBanner("新任务 · 等待提交");
  setText("practiceSourceStatus", "等待输入");
  setPracticeWorkspaceMode(mode);
  goToPage("practice");
  const sessionVersion = practiceSessionVersion;
  practiceWorkspaceRestorePromises[mode] = restorePersistentPracticeWorkspace(mode, sessionVersion).catch(() => false);
}

function openKnowledgeEntry() {
  if (currentPage === "knowledge") flushScheduledPracticeWorkspaceDraft("knowledge");
  if (currentPage === "practice") flushScheduledPracticeWorkspaceDraft(currentPracticeSourceMode);
  beginNewPracticeSession();
  knowledgeSourceFiles = [];
  syncPracticeSourceContentPreference(true);
  if ($("knowledgeTitleInput")) $("knowledgeTitleInput").value = "";
  if ($("knowledgeTextInput")) $("knowledgeTextInput").value = "";
  renderKnowledgeFilePreview();
  $("knowledgeError")?.classList.add("hidden");
  goToPage("knowledge");
  const sessionVersion = practiceSessionVersion;
  practiceWorkspaceRestorePromises.knowledge = restorePersistentPracticeWorkspace("knowledge", sessionVersion).catch(() => false);
}

function newPracticeBatchId() {
  return globalThis.crypto?.randomUUID
    ? crypto.randomUUID()
    : `batch_${Date.now()}_${Math.random().toString(16).slice(2)}`;
}

function beginNewPracticeSession() {
  // A new entry is a new user intent, even when its materials are identical to
  // an active task. Detach this page from any old job before submitting again.
  invalidatePracticeRecoveryObserver();
  practiceSessionVersion += 1;
  practiceBatchId = newPracticeBatchId();
  restorePracticePreferenceOrders();
  rememberPracticeJob("");
  return practiceBatchId;
}

function updateStepIndicator(page) {
  const currentIndex = workflowStepPages.indexOf(page);
  document.querySelectorAll(".step-pill").forEach((button) => {
    const index = workflowStepPages.indexOf(button.dataset.page || "");
    button.classList.toggle("active", index === currentIndex);
    button.classList.toggle("done", currentIndex > index && index >= 0);
  });
}

function switchTextbookTab(tab) {
  const isUpload = tab === "upload";
  $("textbook-existing")?.classList.toggle("hidden", tab !== "existing");
  $("textbook-upload")?.classList.toggle("hidden", !isUpload);
  $("tab-existing")?.classList.toggle("active", tab === "existing");
  $("tab-upload")?.classList.toggle("active", isUpload);
  $("duplicateReviewBox")?.classList.toggle("hidden", isUpload);
  $("selectedTextbookBar")?.classList.toggle("hidden", isUpload || !selectedTextbooks().length);
  $("textbookIndexActionRow")?.classList.toggle("hidden", isUpload);
  $("textbookIndexBox")?.classList.toggle("hidden", isUpload);
  $("sharedTextbookLibraryPanel")?.classList.toggle("hidden", isUpload);
  const primary = $("textbookPrimaryActionBtn");
  if (primary) {
    primary.classList.toggle("hidden", isUpload);
    primary.innerHTML = '去创建解析任务<i class="fas fa-arrow-right"></i>';
    primary.onclick = () => goToPage("exam");
  }
  if (!isUpload) {
    loadLibraryFiles().catch((err) => {
      $("libraryResult").textContent = `刷新教材列表失败：${String(err).replace(/^Error:\s*/, "")}`;
    });
  } else {
    renderUploadSelection("textbook");
    resetUploadFeedback("textbook");
  }
}

function showUploadIndexAction() {
  const primary = $("textbookPrimaryActionBtn");
  if (!primary) return;
  primary.classList.remove("hidden");
  primary.innerHTML = '去建立索引<i class="fas fa-arrow-right"></i>';
  primary.onclick = () => prepareUploadedTextbookIndex();
}

function switchExamTab(tab) {
  $("exam-existing")?.classList.toggle("hidden", tab !== "existing");
  $("exam-upload")?.classList.toggle("hidden", tab !== "upload");
  $("tab-exam-existing")?.classList.toggle("active", tab === "existing");
  $("tab-exam-upload")?.classList.toggle("active", tab === "upload");
  if (tab === "upload") {
    renderUploadSelection("exam");
    resetUploadFeedback("exam");
  }
}

function switchResultTab(tab) {
  $("result-analysis")?.classList.toggle("hidden", tab !== "analysis");
  $("result-review")?.classList.toggle("hidden", tab !== "review");
  $("tab-result-analysis")?.classList.toggle("active", tab === "analysis");
  $("tab-result-review")?.classList.toggle("active", tab === "review");
}

function setVisual(id, title, body, kind = "info") {
  const el = $(id);
  if (!el) return;
  el.className = `result-card result-${kind}`;
  el.innerHTML = `<strong>${escapeHtml(title)}</strong><p>${escapeHtml(body)}</p>`;
}

function setProgress(text, percent = 0, kind = "info") {
  const panel = $("progressPanel");
  const totalBar = $("totalProgressBar");
  const totalText = $("totalProgressText");
  if (panel) {
    const executionClass = panel.classList.contains("execution-progress") ? " execution-progress" : "";
    panel.className = `progress-panel${executionClass} result-${kind}`;
  }
  setText("progressText", text);
  setText("currentStepText", text);
  if (totalText) totalText.textContent = `${Math.max(0, Math.min(100, Number(percent) || 0))}%`;
  if (totalBar) totalBar.style.width = `${Math.max(0, Math.min(100, Number(percent) || 0))}%`;
}

function wrapTechnicalDetails() {
  document.querySelectorAll("pre").forEach((pre) => {
    if (pre.closest("details")) return;
    const details = document.createElement("details");
    details.className = "technical-details";
    const summary = document.createElement("summary");
    summary.textContent = "查看技术详情";
    pre.parentNode.insertBefore(details, pre);
    details.append(summary, pre);
  });
}

function applyIconAccessibility(root = document) {
  if (root instanceof Element && root.matches("i")) root.setAttribute("aria-hidden", "true");
  root.querySelectorAll?.("i").forEach((icon) => icon.setAttribute("aria-hidden", "true"));
  root.querySelectorAll?.("button[title]:not([aria-label])").forEach((button) => {
    const visibleText = String(button.textContent || "").trim();
    if (!visibleText) button.setAttribute("aria-label", button.getAttribute("title") || "操作");
  });
}

const stageLabels = window.TaskContractUI.stageLabels;

function stageLabel(stage) {
  return window.TaskContractUI.stageLabel(stage);
}

function statusLabel(status) {
  return window.TaskContractUI.statusLabel(status);
}

function taskFilterStatus(status, currentStage = "") {
  return window.TaskContractUI.filterStatus(status, currentStage);
}

function taskDisplayStatus(task = {}) {
  const base = window.TaskContractUI.displayStatus(task);
  if (task.is_generation_task && !task.is_generation_job && ["completed", "completed_with_issues"].includes(base)) {
    return practiceCompletionContract(task).issues.length ? "completed_with_issues" : "completed";
  }
  return base;
}

const PRACTICE_COMPLETION_ISSUES_SCHEMA = "answer_book.practice_completion_issues.v1";
const PRACTICE_COMPLETION_PRESENTATION = {
  configuration_blocked: { priority: 400, label: "需要检查 API 配置", action: "check_configuration", action_label: "检查 API 配置", class_name: "blocked", icon: "fas fa-key" },
  generation_incomplete: { priority: 300, label: "存在未完成题目", action: "continue_incomplete", action_label: "继续未完成项", class_name: "warning", icon: "fas fa-triangle-exclamation" },
  review_required: { priority: 200, label: "题目已生成 · 待复核", action: "review_result", action_label: "查看并处理复核", class_name: "warning", icon: "fas fa-triangle-exclamation" },
  warning_only: { priority: 100, label: "已完成 · 有提示", action: "view_warnings", action_label: "查看提示", class_name: "warning", icon: "fas fa-circle-info" },
  completed: { priority: 0, label: "已完成", action: "view_result", action_label: "查看结果", class_name: "passed", icon: "fas fa-check" }
};

function practiceCompletionContract(subject = {}) {
  const supplied = subject?.completion_issues;
  if (supplied?.schema_version === PRACTICE_COMPLETION_ISSUES_SCHEMA && Array.isArray(supplied.issues) && supplied.primary) {
    return supplied;
  }
  const generation = subject?.generation || {};
  const quality = subject?.quality || {};
  const exercises = Array.isArray(subject?.exercises) ? subject.exercises : [];
  const explicitFailed = Number(subject?.failed_count ?? generation.failed_count ?? quality.failed_count);
  const failedSlots = exercises.filter((item) => item?.generation_status === "failed" || item?.generation_error).length;
  const failedCount = Number.isFinite(explicitFailed) ? Math.max(0, explicitFailed) : failedSlots;
  const explicitGenerated = Number(subject?.generated_count ?? generation.generated_count ?? quality.generated_count);
  const generatedCount = Number.isFinite(explicitGenerated) ? Math.max(0, explicitGenerated) : Math.max(0, exercises.length - failedSlots);
  const plannedCount = Array.isArray(subject?.blueprint?.exercise_plan) ? subject.blueprint.exercise_plan.length : 0;
  const explicitTotal = Number(subject?.total_count ?? generation.total_count ?? quality.total_count);
  const totalCount = Number.isFinite(explicitTotal) ? Math.max(0, explicitTotal) : (plannedCount || exercises.length);
  const explicitUnfinished = Number(subject?.unfinished_count ?? generation.unfinished_count ?? quality.unfinished_count);
  const unfinishedCount = Math.max(failedCount, Number.isFinite(explicitUnfinished) ? Math.max(0, explicitUnfinished) : Math.max(0, totalCount - generatedCount));
  const batchErrors = Array.isArray(generation.batch_errors) ? generation.batch_errors : [];
  const configurationBlocked = Boolean(
    subject?.configuration_blocked || subject?.requires_configuration || generation.configuration_blocked
    || generation.status === "configuration_blocked" || batchErrors.some((item) => item?.requires_configuration)
    || exercises.some((item) => item?.generation_error?.requires_configuration)
  );
  const reviewReasons = Array.isArray(quality.blocking_issues) ? quality.blocking_issues.filter(Boolean).map(String) : [];
  const auditCount = Math.max(
    exercises.filter((item) => item?.audit_status === "audit_failed" || item?.generation_error?.code === "blueprint_audit_failed").length,
    batchErrors.filter((item) => item?.code === "blueprint_audit_failed").length
  );
  if (auditCount) reviewReasons.push(`${auditCount} 题蓝图需要复核`);
  const semantic = subject?.semantic_review && typeof subject.semantic_review === "object" ? subject.semantic_review : null;
  const semanticRisks = (semantic?.items || []).flatMap((item) => item?.risks || []).filter((risk) => ["medium", "high"].includes(String(risk?.severity || "").toLowerCase()));
  const semanticReviewIncomplete = Boolean(semantic) && !["passed", "warning"].includes(String(semantic.status || "").toLowerCase());
  if (semanticReviewIncomplete) reviewReasons.push("语义审查未完成，需人工复核");
  if (semanticRisks.length) reviewReasons.push(...semanticRisks.map((risk) => String(risk.message || risk.summary || "语义风险需要复核")));
  if (quality.release_level === "review_candidate" && !reviewReasons.length) reviewReasons.push("当前成果需复核后使用");
  const warningReasons = Array.isArray(quality.warnings) ? quality.warnings.filter(Boolean).map(String) : [];
  const issues = [];
  const add = (code, reasons, count = 0) => issues.push({ code, reasons: Array.from(new Set(reasons)).slice(0, 20), count: Math.max(0, Number(count) || 0), ...PRACTICE_COMPLETION_PRESENTATION[code] });
  if (configurationBlocked) add("configuration_blocked", ["模型服务配置不可用，修正配置后可继续未完成项"], unfinishedCount);
  if (unfinishedCount > 0 || failedCount > 0) add("generation_incomplete", [`${Math.max(unfinishedCount, failedCount)} 题尚未完成`], Math.max(unfinishedCount, failedCount));
  if (reviewReasons.length) add("review_required", reviewReasons, reviewReasons.length);
  if (warningReasons.length && !reviewReasons.length) add("warning_only", warningReasons, warningReasons.length);
  else if (!issues.length && (generation.partial_success || generation.status === "partial_success" || ["warning", "warn"].includes(String(quality.status || "").toLowerCase()))) {
    add("warning_only", warningReasons.length ? warningReasons : ["旧记录含质量提示，请查看结果"], Math.max(1, warningReasons.length));
  }
  issues.sort((left, right) => right.priority - left.priority);
  const primary = issues[0] || { code: "completed", count: 0, reasons: [], ...PRACTICE_COMPLETION_PRESENTATION.completed };
  return {
    schema_version: PRACTICE_COMPLETION_ISSUES_SCHEMA,
    issues,
    primary,
    primary_code: primary.code,
    display_label: primary.label,
    action: primary.action,
    action_label: primary.action_label,
    generated_count: generatedCount,
    total_count: totalCount,
    unfinished_count: unfinishedCount,
    failed_count: failedCount
  };
}

function practiceCompletionHas(subject, code) {
  return practiceCompletionContract(subject).issues.some((item) => item.code === code);
}

function isReviewDecisionTask(task) {
  return window.TaskContractUI.isReviewDecisionTask(task);
}

function isExamStructureReviewTask(task) {
  return window.TaskContractUI.isExamStructureReviewTask(task);
}

function isActionRequiredTask(task) {
  return window.TaskContractUI.isActionRequiredTask(task);
}

function reviewBadgeText(count) {
  return count > 99 ? "99+项待处理" : `${count}项待处理`;
}

function openPendingTasks() {
  goToPage("tasks");
  filterTasks("needs_input");
  loadTasks({ silent: true, includeLiveDetails: true }).catch(() => {});
}

function updateReviewNotificationBadges(tasks = latestTasks) {
  pendingReviewTaskIds.clear();
  for (const task of tasks || []) {
    if (isActionRequiredTask(task) && task.task_id) pendingReviewTaskIds.add(task.task_id);
  }
  const badge = $("taskReviewBadge");
  if (badge) {
    const count = pendingReviewTaskIds.size;
    badge.textContent = reviewBadgeText(count);
    badge.classList.toggle("hidden", count === 0);
  }
}

function stageOrderIndex(stage) {
  return progressStageOrder.indexOf(String(stage || ""));
}

function visibleStepStage(stage) {
  const parents = {
    content_quality_model_repair: "content_quality",
    figures_after_content_quality_model_repair: "content_quality",
    content_quality_local_repair: "content_quality",
    docx_model_repair: "docx",
    docx_repair: "docx",
    figures: "content_quality",
    acceptance: "final_acceptance"
  };
  return parents[stage] || stage;
}

function latestPipelineStage(stages = []) {
  const rows = Array.isArray(stages) ? stages.filter((stage) => stage && stage.stage && stage.stage !== "pipeline") : [];
  return rows.length ? rows[rows.length - 1] : null;
}

function effectiveCurrentStage(task = {}, stages = []) {
  if (task.status === "completed" || task.current_stage === "completed") return "completed";
  let current = task.effective_current_stage || task.current_stage || "";
  const last = latestPipelineStage(stages);
  if (last && stageOrderIndex(last.stage) >= stageOrderIndex(current)) current = last.stage;
  return current;
}

function taskStatusMeta(status, currentStage = "") {
  const normalized = taskFilterStatus(status, currentStage);
  const meta = {
    running: { icon: "fas fa-spinner fa-spin", label: "进行中" },
    queued: { icon: "fas fa-hourglass-start", label: "排队中" },
    completed: { icon: "fas fa-check", label: "已完成" },
    completed_with_issues: { icon: "fas fa-triangle-exclamation", label: "完成待复核" },
    needs_input: { icon: "fas fa-user-check", label: "待人工处理" },
    paused: { icon: "fas fa-pause", label: "已暂停" },
    cancelled: { icon: "fas fa-ban", label: "已取消" },
    failed: { icon: "fas fa-triangle-exclamation", label: "需要处理" }
  };
  return meta[normalized] || meta.queued;
}

function shortName(path) {
  const value = String(path || "").trim();
  if (!value) return "未填写";
  return value.split(/[\\/]/).filter(Boolean).pop() || value;
}

function fileLabel(file) {
  return `${file.name} · ${formatBytes(file.size || 0)}`;
}

function stripFileExtension(name) {
  return String(name || "").replace(/\.(pdf|docx?|json|md|txt)$/i, "");
}

function displayBookName(fileOrName) {
  return stripFileExtension(typeof fileOrName === "string" ? fileOrName : fileOrName?.name || "");
}

function groupCandidateName(name) {
  return displayBookName(name)
    .replace(/[（(]副本[）)]/g, "")
    .replace(/第([一二三四五六七八九十\d]+)版([上下])?\d*$/u, "第$1版")
    .replace(/([上下])\d+$/u, "")
    .replace(/\d+$/u, "")
    .trim();
}

function providerEnvKey(providerName) {
  const name = String(providerName || "").toLowerCase();
  const configured = String(providerConfigs?.[name]?.api_key_env || "").trim();
  if (configured) return configured;
  const map = {
    deepseek: "DEEPSEEK_API_KEY",
    ark: "ARK_API_KEY",
    bailian: "DASHSCOPE_API_KEY",
    lingsuan_openai: "LINGSUAN_OPENAI_API_KEY",
    lingsuan_google: "LINGSUAN_GOOGLE_API_KEY",
    lingsuan_xai: "LINGSUAN_XAI_API_KEY",
    lingsuan_anthropic: "LINGSUAN_ANTHROPIC_API_KEY"
  };
  return map[name] || "";
}

function displayProviderName(name) {
  const labels = {
    deepseek: "DeepSeek",
    ark: "火山方舟",
    bailian: "阿里云百炼",
    lingsuan_openai: "灵算 · OpenAI",
    lingsuan_google: "灵算 · Google Gemini",
    lingsuan_xai: "灵算 · xAI",
    lingsuan_anthropic: "灵算 · Anthropic"
  };
  return labels[String(name || "").toLowerCase()] || name;
}

function setCheckState(iconId, statusId, ok, successText = "通过", failText = "需要处理") {
  const icon = $(iconId);
  if (icon) {
    icon.className = ok ? "fas fa-check-circle" : "fas fa-exclamation-circle";
    icon.style.color = ok ? "#22c55e" : "#f97316";
  }
  const status = $(statusId);
  if (status) {
    status.textContent = ok ? successText : failText;
    status.classList.toggle("passed", Boolean(ok));
    status.classList.toggle("failed", !ok);
    status.classList.remove("unknown");
  }
}

function setCheckUnknown(iconId, statusId, text = "未检查") {
  const icon = $(iconId);
  if (icon) {
    icon.className = "fas fa-minus-circle";
    icon.style.color = "#94a3b8";
  }
  const status = $(statusId);
  if (status) {
    status.textContent = text;
    status.classList.remove("passed", "failed");
    status.classList.add("unknown");
  }
}

function setEnvironmentChecking(message = "正在检查运行环境...", kind = "info") {
  setEnvNextEnabled(false);
  $("environmentRepairBox")?.classList.add("hidden");
  const checks = [
    ["runtimeCheckIcon", "environmentSummary"],
    ["toolsCheckIcon", "environmentHint"],
    ["networkCheckIcon", "networkSummary"]
  ];
  for (const [iconId, textId] of checks) {
    const icon = $(iconId);
    if (icon) {
      icon.className = "fas fa-circle-notch fa-spin";
      icon.style.color = "#94a3b8";
    }
    const status = $(textId);
    if (status) {
      status.textContent = "检测中...";
      status.classList.remove("passed", "failed", "unknown");
    }
  }
  const visual = $("environmentVisualResult");
  if (visual) {
    visual.className = `status-result result-${kind}`;
    const icon = kind === "error" ? "fas fa-exclamation-circle" : "fas fa-circle-notch fa-spin";
    visual.innerHTML = `<i class="${icon}"></i><strong>${escapeHtml(message)}</strong>`;
  }
}

function setEnvNextEnabled(enabled, hint = "") {
  const button = $("envNextBtn");
  if (!button) return;
  button.disabled = !enabled;
  button.classList.toggle("disabled", !enabled);
  setText("envNextHint", hint || (enabled ? "环境已就绪，可以继续选择真题" : "环境检查通过后即可继续"));
}

function taskStageGroupIndex(stage = "") {
  const normalized = String(stage || "");
  const index = taskStageGroups.findIndex((group) => group.stages.includes(normalized));
  return index >= 0 ? index : 0;
}

function renderTaskStepList(currentStage = "", status = "") {
  const list = $("taskStepList");
  if (!list) return;
  const currentIndex = taskStageGroupIndex(currentStage);
  const caption = $("taskStepCaption");
  if (caption) caption.textContent = status === "completed" ? "所有核心阶段均已完成。" : `当前正在：${stageLabel(currentStage)}`;
  list.innerHTML = "";
  for (const [index, group] of taskStageGroups.entries()) {
    const item = document.createElement("div");
    item.className = "task-step-item";
    if (status === "failed" && index === currentIndex) item.classList.add("failed");
    else if (index === currentIndex && status !== "completed") item.classList.add("active");
    else if (status === "completed" || (currentStage && index < currentIndex)) item.classList.add("done");
    const stateIcon = item.classList.contains("done")
      ? '<i class="fas fa-check-circle"></i>'
      : item.classList.contains("failed")
        ? '<i class="fas fa-times-circle"></i>'
        : item.classList.contains("active")
          ? '<i class="fas fa-circle-notch fa-spin"></i>'
          : '<i class="far fa-circle"></i>';
    item.innerHTML = `
      <span class="task-step-index">${index + 1}</span>
      <span class="task-step-copy"><span class="task-step-title">${escapeHtml(group.title)}</span><small>${escapeHtml(index === currentIndex && status !== "completed" ? stageLabel(currentStage) : group.summary)}</small></span>
      <span>${stateIcon}</span>
    `;
    list.appendChild(item);
  }
}

function updateEnvironmentSummary(env) {
  const formulaReady = Boolean(env?.formula_conversion?.preferred_chain_ready);
  const packages = env?.python_packages || {};
  const packageReady = ["python-docx", "lxml", "latex2mathml", "Pillow", "matplotlib"].every((name) => Boolean(packages[name]));
  const renderReady = Boolean(env?.document_tools?.pdf_render_available);
  const drawingRuntimeReady = Boolean(env?.drawing_runtime?.ok);
  const runtimeReady = Boolean(Object.keys(packages).length);
  const toolsReady = formulaReady && packageReady && renderReady && drawingRuntimeReady;
  const hasNetworkCheck = Boolean(env && Object.prototype.hasOwnProperty.call(env, "network"));
  const requiredRoutes = [textRoleRoute("reasoning", ""), textRoleRoute("answer", "")];
  const providerNetwork = env?.network?.by_provider || {};
  const routesConfigured = requiredRoutes.every((route) => route.keySaved && route.capabilityOk);
  const networkReady = hasNetworkCheck && requiredRoutes.every((route) => providerNetwork[route.provider] === true);
  const routeTests = requiredRoutes
    .map((route) => modelConnectionTests[modelConnectionTestKey(route.provider, route.model)])
    .filter(Boolean);
  const routeTestFailed = routeTests.some((test) => test.ok === false);
  const allRoutesTested = routeTests.length === requiredRoutes.length && routeTests.every((test) => test.ok === true);
  const ready = runtimeReady && toolsReady && routesConfigured && networkReady && !routeTestFailed;
  const readyHint = allRoutesTested
    ? "当前解析模型已测试，可以继续选择真题"
    : ready
      ? "当前模型网络可达；建议先测试连接，再继续选择真题"
      : !routesConfigured
        ? "请先为知识识别和结构化解析配置模型与 Key"
        : !networkReady
          ? "当前选用的模型服务网络不可达"
          : routeTestFailed
            ? "当前模型连接测试失败，请修复后继续"
            : "环境检查通过后即可继续";
  setEnvNextEnabled(ready, readyHint);
  setCheckState("runtimeCheckIcon", "environmentSummary", runtimeReady);
  setCheckState(
    "toolsCheckIcon",
    "environmentHint",
    toolsReady,
    "通过",
    !drawingRuntimeReady ? "绘图运行异常" : renderReady ? "依赖不完整" : "渲染工具缺失"
  );
  if (hasNetworkCheck) setCheckState("networkCheckIcon", "networkSummary", networkReady);
  else setCheckUnknown("networkCheckIcon", "networkSummary");
  const visual = $("environmentVisualResult");
  if (visual) {
    visual.className = `status-result ${ready ? "result-ok" : "result-warn"}`;
    visual.innerHTML = ready
      ? allRoutesTested
        ? '<i class="fas fa-check-circle"></i><strong>环境和当前解析模型均已测试通过</strong>'
        : '<i class="fas fa-circle-info"></i><strong>基础环境就绪；当前模型网络可达，但尚未完成连接测试</strong>'
      : hasNetworkCheck
        ? `<i class="fas fa-exclamation-circle"></i><strong>${escapeHtml(readyHint)}</strong>`
        : '<i class="fas fa-minus-circle"></i><strong>网络连通性尚未检查，请重启本地服务后重试</strong>';
  }
  renderEnvironmentRepairs(env);
}

function renderEnvironmentRepairs(env) {
  const box = $("environmentRepairBox");
  if (!box) return;
  const actions = Array.isArray(env?.repair_actions) ? env.repair_actions : [];
  if (!actions.length) {
    box.classList.add("hidden");
    box.innerHTML = "";
    return;
  }
  box.classList.remove("hidden");
  box.innerHTML = `
    <div class="env-repair-head">
      <span><i class="fas fa-wrench"></i></span>
      <div>
        <strong>发现可主动修复项</strong>
        <p>修复前会再次确认，执行结果会写入运行监控日志。</p>
      </div>
    </div>
    <div class="env-repair-actions">
      ${actions.map((action) => `
        <button class="env-repair-action" type="button" data-action="${escapeHtml(action.id)}">
          <span>
            <strong>${escapeHtml(action.title || "环境修复")}</strong>
            <small>${escapeHtml(action.description || action.impact || "")}</small>
          </span>
          <em>${escapeHtml(action.button || "修复")}</em>
        </button>
      `).join("")}
    </div>
  `;
  box.querySelectorAll("[data-action]").forEach((button) => {
    button.addEventListener("click", () => runEnvironmentRepair(actions.find((item) => item.id === button.dataset.action)));
  });
}

async function runEnvironmentRepair(action) {
  if (!action?.id) return;
  const ok = await platformConfirm({
    eyebrow: "环境修复",
    title: `执行“${action.title}”`,
    message: [action.description, action.impact].filter(Boolean).join("\n\n"),
    confirmText: action.button || "确认修复",
    tone: "warning"
  });
  if (!ok) return;
  const box = $("environmentRepairBox");
  if (box) {
    box.classList.remove("hidden");
    box.innerHTML = `<div class="env-repair-head"><span><i class="fas fa-circle-notch fa-spin"></i></span><div><strong>正在执行修复</strong><p>${escapeHtml(action.title)}，请不要关闭服务窗口。</p></div></div>`;
  }
  try {
    const result = await api("/api/environment/repair", {
      method: "POST",
      body: JSON.stringify({ action: action.id })
    });
    $("environmentBox").textContent = pretty(result.environment || result);
    updateEnvironmentSummary(result.environment || {});
  } catch (err) {
    if (box) {
      box.innerHTML = `<div class="env-repair-head env-repair-error"><span><i class="fas fa-exclamation-circle"></i></span><div><strong>修复失败</strong><p>${escapeHtml(String(err).replace(/^Error:\s*/, ""))}</p></div></div>`;
    }
    await loadEnvironmentStatus().catch(() => {});
  }
}

function updateProviderSummary(providers) {
  const provider = providers?.[$("providerSelect")?.value] || Object.values(providers || {}).find((cfg) => cfg.api_key_set);
  if (!provider) {
    setText("providerSummary", "待配置");
    return;
  }
  setText("providerSummary", provider.api_key_set ? `${provider.name} 已连接` : `${provider.name} 未配置 Key`);
}

function updateTaskSummary(task) {
  if (!task) {
    setText("taskSummary", "未选择");
    return;
  }
  setText("taskSummary", `${statusLabel(task.status)} · ${shortName(task.exam_path)}`);
  applyExamTaskControls(task, task.quality_summary || {});
}

function setTaskControlVisibility(id, visible) {
  const button = $(id);
  if (button) button.classList.toggle("hidden", !visible);
  return button;
}

function renderFinalAcceptanceSummary(task = {}, report = null) {
  const hint = $("finalAcceptanceSummary");
  if (!hint) return;
  const data = report && typeof report === "object" ? report : null;
  if (data?.status === "completed_with_issues") {
    const warnings = (data.warnings || []).slice(0, 3);
    hint.className = "result-card result-warn";
    hint.innerHTML = `<strong><i class="fas fa-triangle-exclamation"></i> 完成待复核</strong><p>文件可下载，但图片存在明确的科学性或语义风险，不视为最终验收通过。${warnings.length ? `风险：${warnings.map((item) => escapeHtml(item)).join("；")}` : "请查看图件复核报告。"}</p>`;
    return;
  }
  if (dataFormalAcceptancePassed(data)) {
    hint.className = "result-card result-ok";
    hint.innerHTML = `<strong><i class="fas fa-circle-check"></i> 最终验收通过</strong><p>答案覆盖、文档、公式与渲染检查均已完成，可以导出正式交付包。</p>`;
    return;
  }
  if (data?.delivery_ready === false || data?.ok === false) {
    const issues = (data.issues || []).slice(0, 4);
    hint.className = "result-card result-error";
    hint.innerHTML = `<strong><i class="fas fa-circle-xmark"></i> 最终验收未通过</strong><p>当前结果不能作为正式交付。${issues.length ? `阻断项：${issues.map((item) => escapeHtml(item)).join("；")}` : "请查看质量与诊断。"}</p>`;
    return;
  }
  hint.className = "result-card muted-card";
  hint.innerHTML = `<strong>尚无最终验收报告</strong><p>${task.status === "completed" ? "请执行最终验收后再导出交付包。" : "任务尚未完成，当前不能导出正式交付包。"}</p>`;
}

function applyExamTaskControls(task = {}, qualitySummary = {}) {
  if (task.workflow_type && task.workflow_type !== "exam_analysis") return;
  const caps = task.capabilities || {
    start: ["created", "pending", "queued"].includes(task.status),
    retry: ["failed", "cancelled"].includes(task.status),
    view_detail: true,
    view_progress: true,
    view_quality: ["failed", "completed", "completed_with_issues"].includes(task.status),
    view_files: ["failed", "completed", "completed_with_issues"].includes(task.status),
    download: task.status === "completed"
  };
  const startButton = setTaskControlVisibility("runTaskBtn", Boolean(caps.start || caps.retry));
  if (startButton) {
    startButton.dataset.runMode = caps.retry ? "retry" : "start";
    startButton.innerHTML = caps.retry
      ? '<i class="fas fa-rotate"></i>从检查点重跑'
      : '<i class="fas fa-play"></i>开始生成';
  }
  setTaskControlVisibility("runTaskReuseBtn", false);
  setTaskControlVisibility("runTaskNoModelBtn", Boolean(caps.start));
  setTaskControlVisibility("taskStatusBtn", Boolean(caps.view_progress || caps.view_detail));
  setTaskControlVisibility("taskQualityBtn", Boolean(caps.view_quality));
  setTaskControlVisibility("taskFilesBtn", Boolean(caps.view_files));
  setTaskControlVisibility("taskResultPageBtn", Boolean(caps.view_result));
  const resultStep = document.querySelector('.step-pill[data-page="result"]');
  if (resultStep) {
    resultStep.disabled = !caps.view_result;
    resultStep.setAttribute("aria-disabled", caps.view_result ? "false" : "true");
    resultStep.title = caps.view_result ? "查看任务结果" : "当前任务尚未形成可查看的交付结果";
  }

  const report = qualitySummary?.final_acceptance || task.quality_summary?.final_acceptance || null;
  const acceptanceButton = $("finalAcceptanceBtn");
  if (acceptanceButton) {
    const canInspect = Boolean(report) || ["completed", "completed_with_issues"].includes(task.status);
    acceptanceButton.classList.toggle("hidden", !canInspect);
    acceptanceButton.innerHTML = report
      ? '<i class="fas fa-clipboard-check"></i>查看验收报告'
      : '<i class="fas fa-clipboard-check"></i>执行最终验收';
  }
  const deliveryButton = $("deliveryPackageBtn");
  if (deliveryButton) {
    const deliveryAllowed = Boolean(caps.download) && dataDeliveryReady(report);
    deliveryButton.disabled = !deliveryAllowed;
    deliveryButton.setAttribute("aria-disabled", deliveryAllowed ? "false" : "true");
    deliveryButton.title = deliveryAllowed ? "导出交付包（含待复核风险时会一并附带报告）" : "机器可验证的交付门禁通过后才能导出";
  }
  renderFinalAcceptanceSummary(task, report);
}

function dataDeliveryReady(report) {
  return Boolean(report && report.delivery_ready !== false && report.ok === true);
}

function dataFormalAcceptancePassed(report) {
  if (!report || typeof report !== "object") return false;
  if (typeof report.formal_acceptance_passed === "boolean") return report.formal_acceptance_passed;
  if (report.status) return ["passed", "passed_with_warnings"].includes(report.status);
  return report.ok === true;
}

async function api(path, options = {}) {
  return window.PlatformApi.request(path, options);
}

function supportFeedbackPayload(scope = "page", extra = {}) {
  const taskScopedPage = ["task", "result", "practice", "knowledge", "tasks"].includes(currentPage);
  return {
    scope,
    page: currentPage,
    session_id: window.SupportTelemetry?.sessionId || "",
    events: window.SupportTelemetry?.snapshot() || [],
    selection: window.SupportTelemetry?.selectedText() || "",
    task_id: taskScopedPage ? (activeTaskId || "") : "",
    question_id: taskScopedPage ? (activeResultQuestionId || "") : "",
    history_id: taskScopedPage ? (currentPracticeHistoryId || "") : "",
    ...extra
  };
}

async function sendSupportFeedback(scope = "page", extra = {}) {
  const payload = supportFeedbackPayload(scope, extra);
  window.SupportTelemetry?.record("support_feedback", {
    action: "submit_feedback",
    task_id: payload.task_id,
    question_id: payload.question_id,
    history_id: payload.history_id,
    exercise_index: payload.exercise_index,
    status: "started"
  });
  return api("/api/support/report", { method: "POST", body: JSON.stringify(payload) });
}

async function submitSupportFeedback(scope = "page", extra = {}, button = null) {
  const original = button?.innerHTML || "";
  if (button) {
    button.disabled = true;
    button.innerHTML = '<i class="fas fa-spinner fa-spin"></i><span>正在提交</span>';
  }
  try {
    const result = await sendSupportFeedback(scope, extra);
    const queued = result.status === "queued";
    await platformAlert(
      `${result.message || (queued ? "反馈已保存，将自动提交。" : "问题反馈已提交。")}\n问题编号：${result.report_id || "-"}`,
      { title: queued ? "反馈已保存" : "反馈成功", tone: queued ? "warning" : "success" }
    );
    return result;
  } catch (error) {
    await platformAlert(String(error).replace(/^Error:\s*/, ""), { title: "反馈未能保存", tone: "danger" });
    return null;
  } finally {
    if (button) {
      button.disabled = false;
      button.innerHTML = original;
    }
  }
}

function taskSupportContext(task = {}, reportGroupId = "") {
  const taskId = String(task.task_id || "");
  return {
    task_id: taskId,
    question_id: "",
    exercise_index: null,
    job_id: task.is_generation_job ? taskId : "",
    history_id: task.is_generation_task && !task.is_generation_job ? taskId : "",
    task_kind: String(task.task_kind || ""),
    task_status: String(task.status || ""),
    task_stage: String(task.current_stage || ""),
    operation: String(task.operation || ""),
    task_title: String(task.description || task.exam_path || task.display_title || ""),
    task_model: String(task.model_label || task.answer_model || task.model || ""),
    task_model_label: shortTaskModelName(task.model_label || task.answer_model || task.model || "", task.provider),
    practice_batch_id: String(task.practice_batch_id || ""),
    report_group_id: reportGroupId,
    task_run_started_at: String(task.run_started_at || task.started_at || task.created_at || "")
  };
}

function failedTaskFeedbackKey(task = {}) {
  return [
    String(task.task_id || ""),
    String(task.run_started_at || task.started_at || task.created_at || ""),
    String(task.status || ""),
    String(task.error || task.error_presentation?.message || ""),
  ].join("::");
}

function readFailedTaskFeedback() {
  try {
    const value = JSON.parse(localStorage.getItem(FAILED_TASK_FEEDBACK_STORAGE_KEY) || "{}");
    return value && typeof value === "object" && !Array.isArray(value) ? value : {};
  } catch (_error) {
    return {};
  }
}

function failedTaskFeedbackReported(task = {}) {
  return Boolean(readFailedTaskFeedback()[failedTaskFeedbackKey(task)]);
}

function rememberFailedTaskFeedback(task = {}, reportId = "") {
  const records = readFailedTaskFeedback();
  records[failedTaskFeedbackKey(task)] = { report_id: String(reportId || ""), time: new Date().toISOString() };
  const bounded = Object.fromEntries(
    Object.entries(records)
      .sort((left, right) => String(right[1]?.time || "").localeCompare(String(left[1]?.time || "")))
      .slice(0, 200)
  );
  try {
    localStorage.setItem(FAILED_TASK_FEEDBACK_STORAGE_KEY, JSON.stringify(bounded));
  } catch (_error) {}
}

function failedTaskSetSignature(tasks = []) {
  return tasks.map(failedTaskFeedbackKey).sort().join("|");
}

function dismissFailedTaskFeedback() {
  const failedTasks = latestTasks.filter((task) => taskDisplayStatus(task) === "failed");
  try {
    localStorage.setItem(FAILED_TASK_FEEDBACK_DISMISS_KEY, failedTaskSetSignature(failedTasks));
  } catch (_error) {}
  $("taskFailedFeedbackBar")?.classList.add("hidden");
}

async function submitTaskSupportFeedback(task, button = null) {
  const status = taskDisplayStatus(task);
  const failed = status === "failed";
  const reported = failedTaskFeedbackReported(task);
  let feedbackNote = "";
  if (!failed || reported) {
    feedbackNote = await platformPrompt({
      eyebrow: reported ? "再次反馈" : (task.is_format_task ? "Word 与格式反馈" : "任务质量反馈"),
      title: reported ? "补充说明或重新发送" : (task.is_format_task ? "说明 Word 或排版问题" : "说明结果哪里需要改进"),
      message: reported
        ? "之前反馈不影响再次提交。可以补充新的情况；如果只是重新发送，问题描述可留空。"
        : (task.is_format_task
          ? "任务显示成功也可以反馈。请说明下载、排版、公式、图片、分页或格式上的问题。"
          : "任务显示成功也可以反馈。可说明题目质量、答案内容、模型表现、Word 导出或使用体验问题。"),
      inputLabel: reported ? "补充说明（选填）" : "问题描述（选填）",
      placeholder: reported
        ? "例如：刚才显示为待发送；还需要补充第 3 题导出后图片缺失"
        : "例如：Word 中第 3 题图片缺失；题目难度与选择不一致；答案解释不充分",
      confirmText: reported ? "再次提交反馈" : "提交反馈"
    });
    if (feedbackNote === null) return null;
  }
  const feedbackKind = failed
    ? "task_failure"
    : (task.is_format_task ? "word_format_quality" : "completed_task_quality");
  const result = await submitSupportFeedback("task", {
    ...taskSupportContext(task),
    feedback_kind: feedbackKind,
    feedback_note: feedbackNote,
  }, button);
  if (result) {
    rememberFailedTaskFeedback(task, result.report_id);
    renderTaskManager();
  }
  return result;
}

function rememberPracticeJob(jobId) {
  activePracticeJobId = String(jobId || "");
  try {
    if (activePracticeJobId) localStorage.setItem("activePracticeJobId", activePracticeJobId);
    else localStorage.removeItem("activePracticeJobId");
  } catch (e) {}
}

function practiceRecoveryNoticeKey(job = {}) {
  return `${String(job.job_id || "")}:${String(job.status || "unknown")}`;
}

function practiceErrorNeedsConfiguration(presentation = {}) {
  return [
    "provider_authentication",
    "provider_permission",
    "provider_target_not_found",
    "provider_configuration",
  ].includes(String(presentation?.kind || ""));
}

function practicePublicErrorText(presentation = {}, fallback = "任务执行失败。", { includeAction = true } = {}) {
  const message = String(presentation.message || fallback || "任务执行失败。").trim();
  const action = String(presentation.retry_hint || "").trim();
  const supportId = String(presentation.support_id || "").trim();
  return [
    message,
    includeAction && action ? `建议：${action}` : "",
    supportId ? `诊断编号：${supportId}` : "",
  ].filter(Boolean).join("\n");
}

function hidePracticeRecoveryNotice({ dismiss = false } = {}) {
  const notice = $("practiceRecoveryNotice");
  if (dismiss && practiceRecoveryNoticeJob) {
    practiceRecoveryNoticeDismissedKey = practiceRecoveryNoticeKey(practiceRecoveryNoticeJob);
  }
  notice?.classList.add("hidden");
}

function invalidatePracticeRecoveryObserver({ hideNotice = true } = {}) {
  practiceRecoveryObserverVersion += 1;
  practiceRecoveryNoticeJob = null;
  practiceRecoveryNoticeDismissedKey = "";
  practiceRecoveryNoticeSignature = "";
  if (hideNotice) hidePracticeRecoveryNotice();
}

function practiceRecoveryNoticeMeta(job = {}, { navigationChanged = false } = {}) {
  const status = String(job.status || "unknown");
  const taskName = String(job.title || job.payload?.knowledge_title || "模拟出题任务");
  if (["completed", "completed_with_issues"].includes(status)) {
    const completion = practiceCompletionContract(job.result || job);
    return {
      eyebrow: completion.display_label,
      title: taskName,
      message: `${completedGenerationTaskMessage(job.result || job)}。${navigationChanged ? "当前页面不会被打断。" : "结果已保留。"}`,
      action: completion.action_label,
      completionAction: completion.action,
      tone: completion.primary_code === "completed" ? "success" : "warning",
    };
  }
  if (status === "failed") {
    const presentation = job.error_presentation || {};
    return {
      eyebrow: "后台任务未完成",
      title: taskName,
      message: practicePublicErrorText(presentation, job.error || "任务执行失败，当前页面和已有任务记录均已保留。"),
      action: "查看详情",
      tone: "danger",
    };
  }
  if (status === "cancelled") {
    return {
      eyebrow: "后台任务已取消",
      title: taskName,
      message: job.error_presentation?.message || job.error || "任务已取消，当前页面和已有任务记录均已保留。",
      action: "查看详情",
      tone: "warning",
    };
  }
  if (status === "unavailable") {
    return {
      eyebrow: "正在恢复后台任务",
      title: taskName,
      message: "暂时无法刷新任务状态。任务标识已保留，可稍后继续查看。",
      action: "继续查看",
      tone: "warning",
    };
  }
  return {
    eyebrow: status === "queued" ? "已恢复排队中的任务" : "已恢复进行中的任务",
    title: taskName,
    message: job.progress_message || "任务仍在后台继续，停留在当前页面不会影响执行。",
    action: "继续查看",
    tone: "progress",
  };
}

function showPracticeRecoveryNotice(job = {}, options = {}) {
  if (!job.job_id) return;
  const notice = $("practiceRecoveryNotice");
  if (!notice) return;
  practiceRecoveryNoticeJob = { ...job };
  const key = practiceRecoveryNoticeKey(job);
  if (practiceRecoveryNoticeDismissedKey === key) return;
  const meta = practiceRecoveryNoticeMeta(job, options);
  const signature = JSON.stringify([key, meta.eyebrow, meta.title, meta.message, meta.action]);
  if (signature === practiceRecoveryNoticeSignature && !notice.classList.contains("hidden")) return;
  practiceRecoveryNoticeSignature = signature;
  setText("practiceRecoveryEyebrow", meta.eyebrow);
  setText("practiceRecoveryTitle", meta.title);
  setText("practiceRecoveryMessage", meta.message);
  setText("practiceRecoveryOpenBtn", meta.action);
  notice.dataset.status = String(job.status || "unknown");
  notice.dataset.completionAction = String(meta.completionAction || "");
  notice.dataset.tone = meta.tone;
  notice.classList.remove("hidden");
}

function practiceRecoveryContextIsCurrent(context = {}) {
  return context.observerVersion === practiceRecoveryObserverVersion
    && context.sessionVersion === practiceSessionVersion;
}

function renderStoppedPracticeRecoveryJob(job = {}) {
  const stoppedKnowledgeAnalyze = job.task_kind === "knowledge" && job.operation === "analyze";
  latestPracticeRequest = job.payload || latestPracticeRequest;
  restorePracticePreferenceOrders(latestPracticeRequest);
  syncPracticeSourceContentPreference(latestPracticeRequest?.include_source_content_in_generation !== false);
  setPracticeWorkspaceMode(job.task_kind === "knowledge" ? "knowledge" : "exam");
  if (stoppedKnowledgeAnalyze) {
    goToPage("knowledge");
  } else {
    goToPage("practice");
    setPracticeSourceEntryVisibility(true);
    setPracticeStatusBanner(job.status === "cancelled" ? "任务已取消" : "任务未完成", "error");
  }
  const errorBox = $(stoppedKnowledgeAnalyze ? "knowledgeError" : "practiceError");
  if (errorBox) {
    errorBox.textContent = job.status === "cancelled"
      ? (job.error_presentation?.message || job.error || "后台出题任务已取消。")
      : practicePublicErrorText(job.error_presentation || {}, job.error || "后台出题任务失败。");
    errorBox.classList.remove("hidden");
  }
}

async function openPracticeRecoveryNoticeJob() {
  const remembered = practiceRecoveryNoticeJob;
  if (!remembered?.job_id) return;
  invalidatePracticeRecoveryObserver();
  rememberPracticeJob("");
  const job = await api(`/api/practice/jobs/${encodeURIComponent(remembered.job_id)}?detail=1`);
  const completion = practiceCompletionContract(job.result || job);
  if (["completed", "completed_with_issues"].includes(String(job.status || "")) && completion.action === "check_configuration") {
    goToPage("keys");
    return;
  }
  if (["completed", "completed_with_issues"].includes(String(job.status || "")) && completion.action === "continue_incomplete") {
    const historyId = String(job.result?.history_id || job.history_id || "");
    if (historyId) {
      await continuePracticeHistory(historyId, null, job.task_kind);
      return;
    }
  }
  if (["failed", "cancelled"].includes(String(job.status || ""))) {
    practiceSessionVersion += 1;
    renderStoppedPracticeRecoveryJob(job);
    return;
  }
  await openGenerationJob({
    task_id: job.job_id,
    task_kind: job.task_kind,
    error_presentation: job.error_presentation || {},
  });
}

function practiceJobDelay(ms = 1200) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

function formatPracticeWaitTime(seconds = 0) {
  const value = Math.max(0, Number(seconds) || 0);
  if (value < 10) return "刚刚开始";
  if (value < 60) return `已等待 ${Math.floor(value)} 秒`;
  return `已等待 ${Math.floor(value / 60)} 分 ${Math.floor(value % 60)} 秒`;
}

function practiceWaitExpectation(job = {}) {
  const operation = String(job.operation || "");
  const total = Math.max(0, Number(job.total_count || 0));
  if (operation === "analyze") return "同类材料通常 1–3 分钟完成范围解析";
  if (operation === "plan") return total > 12 ? "同类任务通常 2–5 分钟完成蓝图" : "同类任务通常 1–2 分钟完成蓝图";
  if (["generate_from_plan", "generate_from_contract"].includes(operation)) {
    if (total > 15) return "同类题量通常 4–10 分钟完成生成";
    if (total > 6) return "同类题量通常 2–6 分钟完成生成";
    return "同类题量通常 1–3 分钟完成生成";
  }
  return "复杂公式、图片或模型重试可能延长等待";
}

function updatePracticeLoadingProgress(job = {}) {
  const operation = String(job.operation || "");
  const elapsed = Number(job.elapsed_seconds || 0);
  let activity = "任务正在继续";
  const generated = Number(job.generated_count || 0);
  const total = Number(job.total_count || 0);
  if (["generate_from_plan", "generate_from_contract"].includes(operation) && generated > 0 && total > 0) activity = `已完成 ${generated}/${total} 道题，正在生成下一题`;
  else if (operation === "analyze") activity = "正在梳理材料内容与考点范围";
  else if (operation === "plan") activity = "正在根据确认范围设计训练蓝图";
  else if (elapsed < 30) activity = "正在整理蓝图，准备生成题目";
  else if (elapsed < 120) activity = "正在生成题目";
  else activity = "生成内容较长，正在等待完整结果";
  setText("practiceLoadingDetail", activity);
  setText("practiceLoadingElapsed", `${formatPracticeWaitTime(elapsed)} · ${practiceWaitExpectation(job)}`);
}

async function waitForPracticeJob(jobId, { onUpdate = null } = {}) {
  let transientFailures = 0;
  while (true) {
    try {
      const job = await api(`/api/practice/jobs/${encodeURIComponent(jobId)}?detail=1`);
      transientFailures = 0;
      if (typeof onUpdate === "function") {
        try { onUpdate(job); } catch (e) {}
      }
      if (job.progress_message && activePracticeJobId === jobId) {
        setPracticeStageDescription(job.progress_message);
        updatePracticeLoadingProgress(job);
      }
      if (job.status === "completed") {
        return job;
      }
      if (job.status === "failed") {
        if (activePracticeJobId === jobId) rememberPracticeJob("");
        const terminalError = new Error(job.error || "后台出题任务失败。");
        terminalError.practiceJob = job;
        throw terminalError;
      }
      if (job.status === "cancelled") {
        if (activePracticeJobId === jobId) rememberPracticeJob("");
        const terminalError = new Error(job.error || "后台出题任务已取消。");
        terminalError.practiceJob = job;
        throw terminalError;
      }
    } catch (error) {
      transientFailures += 1;
      if (transientFailures >= 5 || !/fetch|network|连接|Failed to fetch/i.test(String(error))) throw error;
    }
    await practiceJobDelay();
  }
}

async function submitPracticeJob(operation, payload) {
  const batchId = payload?.practice_batch_id || practiceBatchId || newPracticeBatchId();
  practiceBatchId = batchId;
  const queuedPayload = { ...(payload || {}), practice_batch_id: batchId };
  const queued = await api("/api/practice/jobs", {
    method: "POST",
    body: JSON.stringify({ operation, payload: queuedPayload })
  });
  rememberPracticeJob(queued.job_id);
  return waitForPracticeJob(queued.job_id);
}

async function resumeRememberedPracticeJob() {
  const sessionVersion = practiceSessionVersion;
  const navigationVersion = practiceNavigationVersion;
  let jobId = "";
  try { jobId = localStorage.getItem("activePracticeJobId") || ""; } catch (e) {}
  if (!jobId || activePracticeJobId) return;
  const observerVersion = ++practiceRecoveryObserverVersion;
  const context = { observerVersion, sessionVersion, navigationVersion, jobId };
  rememberPracticeJob(jobId);
  try {
    const initialJob = await api(`/api/practice/jobs/${encodeURIComponent(jobId)}?detail=1`);
    if (!practiceRecoveryContextIsCurrent(context)) return;
    const navigationChanged = navigationVersion !== practiceNavigationVersion;
    showPracticeRecoveryNotice(initialJob, { navigationChanged });
    if (["completed", "failed", "cancelled"].includes(String(initialJob.status || ""))) {
      rememberPracticeJob("");
      loadTasks({ silent: true, includeLiveDetails: true }).catch(() => {});
      return;
    }
    const job = await waitForPracticeJob(jobId, {
      onUpdate(currentJob) {
        if (!practiceRecoveryContextIsCurrent(context)) return;
        showPracticeRecoveryNotice(currentJob, {
          navigationChanged: navigationVersion !== practiceNavigationVersion,
        });
      },
    });
    if (!practiceRecoveryContextIsCurrent(context)) return;
    rememberPracticeJob("");
    showPracticeRecoveryNotice(job, {
      navigationChanged: navigationVersion !== practiceNavigationVersion,
    });
    loadTasks({ silent: true, includeLiveDetails: true }).catch(() => {});
  } catch (error) {
    if (!practiceRecoveryContextIsCurrent(context)) return;
    const stoppedJob = error?.practiceJob && typeof error.practiceJob === "object"
      ? error.practiceJob
      : null;
    if (stoppedJob) {
      rememberPracticeJob("");
      showPracticeRecoveryNotice(stoppedJob, {
        navigationChanged: navigationVersion !== practiceNavigationVersion,
      });
      loadTasks({ silent: true, includeLiveDetails: true }).catch(() => {});
      return;
    }
    showPracticeRecoveryNotice({
      job_id: jobId,
      status: "unavailable",
      title: practiceRecoveryNoticeJob?.title || "模拟出题任务",
    }, { navigationChanged: navigationVersion !== practiceNavigationVersion });
  }
}

async function refresh() {
  const version = await api("/api/version");
  const versionParts = String(version.version || "").trim().split(/\s+/);
  const appVersion = String(version.app_version || versionParts[0] || "未知").replace(/^v/i, "");
  $("platformVersion").textContent = `v${appVersion}`;
  $("versionBox").textContent = `应用版本 v${appVersion} · ${version.release_manifest_exists ? "正式发布清单已就绪" : "本地源码预览"}`;
  await loadApiConfiguration();
  await Promise.all([loadLibraryFiles(), loadPracticeHistory()]);
}

function syncProviderControls(providers) {
  const select = $("providerSelect");
  const previousProvider = select.value;
  select.innerHTML = "";
  for (const [name, cfg] of Object.entries(providers)) {
    const option = document.createElement("option");
    option.value = name;
    option.textContent = displayProviderName(name);
    option.dataset.model = cfg.default_model || "";
    select.appendChild(option);
  }
  const configuredProvider = Object.entries(providers).find(([, cfg]) => cfg.api_key_set)?.[0];
  if (previousProvider && providers[previousProvider]) select.value = previousProvider;
  else if (configuredProvider) select.value = configuredProvider;
  updateModelControls();
  initializeExamModelPreset();
  updateProviderSummary(providers);
  populatePracticeModelSettings();
}

async function loadApiConfiguration({ showLoading = true } = {}) {
  if (showLoading) {
    apiKeyConfigLoadState = { providers: "loading", keyFile: "loading", recoveryAvailable: false };
    renderKeyProviderCards();
  }
  const [providersResult, keyFileResult] = await Promise.allSettled([
    api("/api/providers"),
    api("/api/providers/key-file")
  ]);
  apiKeyConfigLoadState = {
    providers: providersResult.status === "fulfilled" ? "ready" : "error",
    keyFile: keyFileResult.status === "fulfilled" ? "ready" : "error",
    recoveryAvailable: [providersResult, keyFileResult].some((result) => (
      result.status === "rejected"
      && result.reason?.recoveryAction === "backup_and_reset_api_configuration"
    ))
  };
  if (providersResult.status === "fulfilled") {
    providerConfigs = providersResult.value || {};
    syncProviderControls(providerConfigs);
  }
  if (keyFileResult.status === "fulfilled") {
    apiKeyFileInfo = keyFileResult.value || {};
  }
  renderApiKeyFileInfo();
  renderKeyProviderCards();
  return apiKeyConfigLoadState.providers === "ready" && apiKeyConfigLoadState.keyFile === "ready";
}

async function checkPlatformUpdate() {
  const button = $("checkUpdateBtn");
  const label = button?.querySelector("span");
  if (button?.disabled) return;
  if (button) button.disabled = true;
  if (label) label.textContent = "检查中";
  try {
    const status = await api("/api/update/status?refresh=1");
    if (!status.enabled) {
      await platformAlert(status.message || "GitHub 首次发布时会自动配置更新源。", {
        title: "更新源尚未发布",
        tone: "warning"
      });
      return;
    }
    if (!status.update_available) {
      await platformAlert(status.message || "当前已是最新版本。", {
        title: status.release_incomplete ? "新版本尚未准备完成" : "无需更新",
        tone: status.release_incomplete ? "warning" : "success"
      });
      return;
    }
    const notes = String(status.release_notes || "本次更新包含稳定性与质量改进。").trim();
    const actionText = status.action === "pull_source"
      ? "程序将仅在源码无未保存修改时执行快进拉取。"
      : "程序将下载、校验并打开当前系统的安装包。";
    const confirmed = await platformConfirm({
      eyebrow: `当前 ${status.current_version} → 新版 ${status.latest_version}`,
      title: "发现可用更新",
      message: `${notes.slice(0, 1200)}\n\n${actionText}\nAPI Key、教材、任务和输出不会被覆盖。`,
      confirmText: status.action === "pull_source" ? "拉取更新" : "下载更新",
      cancelText: "稍后再说"
    });
    if (!confirmed) return;
    if (label) label.textContent = status.action === "pull_source" ? "拉取中" : "下载中";
    const result = await api("/api/update/apply", { method: "POST", body: "{}" });
    await platformAlert(result.message || "更新已准备完成。", {
      title: result.restart_required ? "请重启程序" : "更新完成",
      tone: "success"
    });
  } catch (error) {
    await platformAlert(String(error).replace(/^Error:\s*/, ""), {
      title: "更新未完成",
      tone: "danger"
    });
  } finally {
    if (button) button.disabled = false;
    if (label) label.textContent = "检查更新";
  }
}

function practiceFileLabel(file) {
  if (!file) return "未选择文件";
  return `${file.name} · ${(file.size / 1024).toFixed(file.size > 1024 * 1024 ? 0 : 1)} KB`;
}

function uploadFileSelectionApi() {
  if (!globalThis.UploadFileSelection) throw new Error("文件选择组件加载失败，请刷新页面后重试。");
  return globalThis.UploadFileSelection;
}

function uploadFeedbackBox(mode) {
  return $(mode === "knowledge" ? "knowledgeError" : "practiceError");
}

function showUploadFeedback(mode, message, tone = "error") {
  const box = uploadFeedbackBox(mode);
  if (!box) return;
  box.textContent = String(message || "");
  box.dataset.uploadFeedback = "true";
  box.classList.toggle("practice-upload-note", tone === "info");
  box.classList.toggle("hidden", !message);
}

function clearUploadFeedback(mode) {
  const box = uploadFeedbackBox(mode);
  if (!box || box.dataset.uploadFeedback !== "true") return;
  box.textContent = "";
  delete box.dataset.uploadFeedback;
  box.classList.remove("practice-upload-note");
  box.classList.add("hidden");
}

async function fileAsDataUrl(file) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(String(reader.result || ""));
    reader.onerror = () => reject(new Error(`${file.name} 读取失败。`));
    reader.onabort = () => reject(new Error(`${file.name} 读取已取消。`));
    reader.readAsDataURL(file);
  });
}

async function fileSha256(file) {
  if (!globalThis.crypto?.subtle) throw new Error(`${file.name} 无法计算内容摘要，请使用最新版浏览器。`);
  const bytes = await file.arrayBuffer();
  const digest = await crypto.subtle.digest("SHA-256", bytes);
  return Array.from(new Uint8Array(digest), (value) => value.toString(16).padStart(2, "0")).join("");
}

function newUploadItemId() {
  const seed = globalThis.crypto?.randomUUID
    ? crypto.randomUUID()
    : `${Date.now()}-${Math.random().toString(16).slice(2)}`;
  return `upload_${seed}`;
}

async function prepareUploadFile(file) {
  const dataUrl = await fileAsDataUrl(file);
  const sha256 = await fileSha256(file);
  return {
    upload_item_id: newUploadItemId(),
    sha256,
    name: file.name,
    type: file.type || "application/octet-stream",
    size: file.size,
    data_url: dataUrl
  };
}

async function readUploadFilesAtomically(fileList, currentFiles, canCommit) {
  const files = Array.from(fileList || []);
  if (!files.length) return { files: currentFiles(), added: [], duplicates: [] };
  if (!canCommit()) throw new Error("本次选择未加入；页面已切换，请在当前页面重新选择文件。");
  const selection = uploadFileSelectionApi();
  const initialValidation = selection.validateSelection(currentFiles(), files);
  if (!initialValidation.ok) throw new Error(selection.formatValidationError(initialValidation.rejected));

  const prepared = [];
  for (let index = 0; index < files.length; index += 1) {
    const file = files[index];
    try {
      prepared.push(await prepareUploadFile(file));
    } catch (error) {
      const rejected = files.map((candidate, candidateIndex) => ({
        name: candidate.name || `未命名文件 ${candidateIndex + 1}`,
        reasons: candidateIndex === index
          ? [String(error).replace(/^Error:\s*/, "")]
          : ["同批次有文件读取失败，本文件也未加入"]
      }));
      throw new Error(selection.formatValidationError(rejected));
    }
  }
  if (!canCommit()) throw new Error("本次选择未加入；页面已切换，请在当前页面重新选择文件。");
  const latestFiles = currentFiles();
  const commitValidation = selection.validateSelection(latestFiles, files);
  if (!commitValidation.ok) throw new Error(selection.formatValidationError(commitValidation.rejected));
  return selection.mergePreparedFiles(latestFiles, prepared);
}

function queueUploadFileRead(kind, operation) {
  const queued = uploadFileReadChains[kind].catch(() => {}).then(operation);
  uploadFileReadChains[kind] = queued.catch(() => {});
  return queued;
}

function uploadDisplayName(file, files) {
  return uploadFileSelectionApi().displayName(file, files);
}

function duplicateUploadMessage(duplicates) {
  if (!duplicates?.length) return "";
  const names = duplicates.map(({ file, duplicateOf }) => `${file.name}（与 ${duplicateOf.name} 内容相同）`);
  return `重复文件未再次加入：${names.join("、")}`;
}

function uploadItemKey(file, index) {
  return String(file?.upload_item_id || `legacy-${index}`);
}

function uploadItemIndex(files, itemKey) {
  return files.findIndex((file, index) => uploadItemKey(file, index) === itemKey);
}

function renderPracticeFilePreview() {
  const preview = $("practiceFilePreview");
  if (!preview) return;
  if (!practiceSourceFiles.length) {
    preview.innerHTML = `<i class="fas fa-file-circle-plus"></i><span>${currentPracticeSourceMode === "knowledge" ? "未选择知识点文件，也可以直接粘贴材料截图" : "未选择文件，也可以直接粘贴截图"}</span>`;
    syncPracticeSubmitAvailability();
    return;
  }
  preview.innerHTML = practiceSourceFiles.map((file, index) => `
    <div class="practice-source-file" data-upload-item-id="${escapeHtml(uploadItemKey(file, index))}">
      ${String(file.type || "").startsWith("image/")
        ? `<img src="${file.data_url}" alt="">`
        : `<i class="fas ${file.type === "application/pdf" ? "fa-file-pdf" : String(file.name || "").toLowerCase().endsWith(".docx") ? "fa-file-word" : "fa-file-lines"}"></i>`}
      <span class="practice-source-file__meta"><strong>${escapeHtml(uploadDisplayName(file, practiceSourceFiles))}</strong><small>${(Number(file.size || 0) / 1024).toFixed(1)} KB</small></span>
      <span class="practice-source-file__actions">
        ${practiceSourceFiles.length > 1 ? `<button class="practice-file-action" type="button" data-practice-file-up="${escapeHtml(uploadItemKey(file, index))}" title="上移" aria-label="上移文件" ${index === 0 ? "disabled" : ""}><i class="fas fa-arrow-up"></i></button>
        <button class="practice-file-action" type="button" data-practice-file-down="${escapeHtml(uploadItemKey(file, index))}" title="下移" aria-label="下移文件" ${index === practiceSourceFiles.length - 1 ? "disabled" : ""}><i class="fas fa-arrow-down"></i></button>` : ""}
        <button class="practice-file-action practice-file-action--remove" type="button" data-practice-file-remove="${escapeHtml(uploadItemKey(file, index))}" title="删除" aria-label="删除文件"><i class="fas fa-xmark"></i></button>
      </span>
    </div>
  `).join("");
  preview.querySelectorAll("[data-practice-file-remove]").forEach((button) => {
    button.addEventListener("click", () => {
      const index = uploadItemIndex(practiceSourceFiles, button.dataset.practiceFileRemove || "");
      if (index < 0) return;
      practiceSourceFiles.splice(index, 1);
      renderPracticeFilePreview();
      clearUploadFeedback("practice");
      setText("practiceSourceStatus", practiceSourceFiles.length ? `已读取 ${practiceSourceFiles.length} 个文件` : "等待输入");
      persistUploadSelectionDraft(currentPracticeSourceMode);
    });
  });
  const move = (itemKey, offset) => {
    const index = uploadItemIndex(practiceSourceFiles, itemKey);
    if (index < 0) return;
    const target = index + offset;
    if (target < 0 || target >= practiceSourceFiles.length) return;
    [practiceSourceFiles[index], practiceSourceFiles[target]] = [practiceSourceFiles[target], practiceSourceFiles[index]];
    renderPracticeFilePreview();
    persistUploadSelectionDraft(currentPracticeSourceMode);
  };
  preview.querySelectorAll("[data-practice-file-up]").forEach((button) => {
    button.addEventListener("click", () => move(button.dataset.practiceFileUp || "", -1));
  });
  preview.querySelectorAll("[data-practice-file-down]").forEach((button) => {
    button.addEventListener("click", () => move(button.dataset.practiceFileDown || "", 1));
  });
  syncPracticeSubmitAvailability();
}

function syncPracticeSubmitAvailability() {
  const hasText = Boolean($("practiceQuestionText")?.value.trim());
  const ready = (hasText || practiceSourceFiles.length > 0) && uploadFileReadPending.practice === 0;
  for (const button of [$("practiceGenerateBtn"), $("practiceRailGenerateBtn")]) {
    if (!button) continue;
    button.disabled = !ready;
    button.setAttribute("aria-disabled", ready ? "false" : "true");
    button.title = ready ? "解析材料并确认考点范围" : "请先粘贴材料或上传文件";
  }
  return ready;
}

function syncKnowledgeUploadAvailability() {
  const button = $("knowledgePlanBtn");
  if (button && uploadFileReadPending.knowledge > 0) button.disabled = true;
}

async function readPracticeFiles(fileList) {
  const files = Array.from(fileList || []);
  if (!files.length) {
    renderPracticeFilePreview();
    return { files: practiceSourceFiles, added: [], duplicates: [] };
  }
  const sessionVersion = practiceSessionVersion;
  const sourceMode = currentPracticeSourceMode;
  uploadFileReadPending.practice += 1;
  syncPracticeSubmitAvailability();
  try {
    await practiceWorkspaceRestorePromises[sourceMode];
    return await queueUploadFileRead("practice", async () => {
      const result = await readUploadFilesAtomically(
        files,
        () => practiceSourceFiles,
        () => sessionVersion === practiceSessionVersion && sourceMode === currentPracticeSourceMode
      );
      practiceSourceFiles = result.files;
      renderPracticeFilePreview();
      setText("practiceSourceStatus", `已读取 ${practiceSourceFiles.length} 个文件`);
      const duplicateMessage = duplicateUploadMessage(result.duplicates);
      if (duplicateMessage) showUploadFeedback("practice", duplicateMessage, "info");
      else clearUploadFeedback("practice");
      await persistUploadSelectionDraft(sourceMode);
      return result;
    });
  } finally {
    uploadFileReadPending.practice = Math.max(0, uploadFileReadPending.practice - 1);
    syncPracticeSubmitAvailability();
  }
}

function renderKnowledgeFilePreview() {
  const preview = $("knowledgeFilePreview");
  if (!preview) return;
  if (!knowledgeSourceFiles.length) {
    preview.innerHTML = "<span>尚未选择文件</span>";
    return;
  }
  preview.innerHTML = knowledgeSourceFiles.map((file, index) => `
    <div data-upload-item-id="${escapeHtml(uploadItemKey(file, index))}">
      <span><i class="fas ${file.type === "application/pdf" ? "fa-file-pdf" : String(file.name || "").toLowerCase().endsWith(".docx") ? "fa-file-word" : String(file.type || "").startsWith("image/") ? "fa-file-image" : "fa-file-lines"}"></i> ${escapeHtml(uploadDisplayName(file, knowledgeSourceFiles))} · ${(Number(file.size || 0) / 1024).toFixed(1)} KB</span>
      <button class="knowledge-file-remove" type="button" data-knowledge-file-remove="${escapeHtml(uploadItemKey(file, index))}" title="移除此文件" aria-label="移除 ${escapeHtml(uploadDisplayName(file, knowledgeSourceFiles))}"><i class="fas fa-xmark" aria-hidden="true"></i></button>
    </div>
  `).join("");
  preview.querySelectorAll("[data-knowledge-file-remove]").forEach((button) => {
    button.addEventListener("click", () => {
      const index = uploadItemIndex(knowledgeSourceFiles, button.dataset.knowledgeFileRemove || "");
      if (index < 0) return;
      knowledgeSourceFiles.splice(index, 1);
      renderKnowledgeFilePreview();
      clearUploadFeedback("knowledge");
      persistUploadSelectionDraft("knowledge");
    });
  });
}

async function readKnowledgeFiles(fileList) {
  const files = Array.from(fileList || []);
  if (!files.length) return { files: knowledgeSourceFiles, added: [], duplicates: [] };
  const sessionVersion = practiceSessionVersion;
  uploadFileReadPending.knowledge += 1;
  syncKnowledgeUploadAvailability();
  try {
    await practiceWorkspaceRestorePromises.knowledge;
    return await queueUploadFileRead("knowledge", async () => {
      const result = await readUploadFilesAtomically(
        files,
        () => knowledgeSourceFiles,
        () => sessionVersion === practiceSessionVersion && currentPage === "knowledge"
      );
      knowledgeSourceFiles = result.files;
      renderKnowledgeFilePreview();
      const duplicateMessage = duplicateUploadMessage(result.duplicates);
      if (duplicateMessage) showUploadFeedback("knowledge", duplicateMessage, "info");
      else clearUploadFeedback("knowledge");
      await persistUploadSelectionDraft("knowledge");
      return result;
    });
  } finally {
    uploadFileReadPending.knowledge = Math.max(0, uploadFileReadPending.knowledge - 1);
    if (uploadFileReadPending.knowledge === 0) $("knowledgePlanBtn").disabled = false;
  }
}

async function pastePracticeImages(event) {
  const items = Array.from(event.clipboardData?.items || []);
  // Word, PDF readers and some web pages put both text and a rendered image on
  // the clipboard.  In that case this is a text paste, not a screenshot paste.
  // Let the browser insert the plain text and only fall back to images when the
  // clipboard contains no usable text.
  const plainText = String(event.clipboardData?.getData("text/plain") || "").trim();
  if (plainText) return;
  const imageFiles = items
    .filter((item) => item.kind === "file" && item.type.startsWith("image/"))
    .map((item, index) => {
      const blob = item.getAsFile();
      if (!blob) return null;
      const extension = item.type.split("/")[1]?.replace("jpeg", "jpg") || "png";
      return new File([blob], `粘贴截图-${new Date().toISOString().replace(/[:.]/g, "-")}-${index + 1}.${extension}`, { type: item.type });
    })
    .filter(Boolean);
  if (!imageFiles.length) return;
  event.preventDefault();
  const result = await readPracticeFiles(imageFiles);
  setText("practiceSourceStatus", `已粘贴 ${result.added.length} 张截图`);
}

async function pasteKnowledgeImages(event) {
  const items = Array.from(event.clipboardData?.items || []);
  const plainText = String(event.clipboardData?.getData("text/plain") || "").trim();
  if (plainText) return;
  const imageFiles = items
    .filter((item) => item.kind === "file" && item.type.startsWith("image/"))
    .map((item, index) => {
      const blob = item.getAsFile();
      if (!blob) return null;
      const extension = item.type.split("/")[1]?.replace("jpeg", "jpg") || "png";
      return new File([blob], `知识材料截图-${new Date().toISOString().replace(/[:.]/g, "-")}-${index + 1}.${extension}`, { type: item.type });
    })
    .filter(Boolean);
  if (!imageFiles.length) return;
  event.preventDefault();
  await readKnowledgeFiles(imageFiles);
}

function practicePlainText(data) {
  const lines = [
    `专项训练目标：${data.blueprint?.training_goal || ""}`,
    `原题诊断：${data.source_analysis?.question_type || ""} · ${data.source_analysis?.difficulty || ""}`,
    ""
  ];
  for (const rawItem of data.exercises || []) {
    const item = normalizePracticeMarkdownTables(rawItem);
    lines.push(`${item.number}. [${item.difficulty}] ${item.stem}`);
    for (const table of item.tables || []) {
      if (!String(table.location || "stem").includes("stem")) continue;
      if (table.headers?.length) lines.push(table.headers.join("\t"));
      for (const row of table.rows || []) lines.push(row.join("\t"));
    }
    for (const [optionIndex, option] of (item.options || []).entries()) {
      lines.push(`${String.fromCharCode(65 + optionIndex)}. ${practiceClipboardOptionText(option.text)}`);
    }
    lines.push("");
  }
  return lines.join("\n");
}

function practiceClipboardOptionText(value) {
  const text = String(value || "")
    .replace(/^\s*[A-Ha-h]\s*(?:[.．、:：]|[）)])\s*/, "")
    .replace(/^\s*[（(]\s*[A-Ha-h]\s*[）)]\s*/, "")
    .replace(/\*\*/g, "")
    .replace(/[。．.!！?？;；,，]+\s*$/, "")
    .trim();
  return text ? `${text}。` : "";
}

function normalizePracticeStandardStateLatex(value) {
  return String(value || "").replace(
    /((?:\\Delta|Δ|∆)?\s*(?:G|H|S|U|A|F|K|E)(?:_\{?[A-Za-z,]+\}?)?)\s*(?:\^\s*\{?\s*(?:o|O|\\circ|θ|\\theta)\s*\}?|[°ºᵒθ])/g,
    "$1^{\\theta}",
  );
}

function repairPracticeClipboardLatex(value) {
  const source = normalizePracticeStandardStateLatex(String(value || "").trim());
  // Some older model responses expressed an electrochemical cell as one
  // `\\mathrm{...` block separated by literal pipes, but omitted the closing
  // brace before the later species. MathJax correctly reports that as
  // "Missing close brace"; repair this presentation-only legacy form before
  // turning it into clipboard MathML. Stored question text remains untouched.
  if (!source.startsWith("\\mathrm{") || !source.includes("|")) return source;
  return source.slice("\\mathrm{".length).split("|").map((rawPart) => {
    const part = rawPart.trim();
    const braceBalance = [...part].reduce((total, character) => (
      total + (character === "{" ? 1 : character === "}" ? -1 : 0)
    ), 0);
    return braceBalance > 0 ? `${part}}` : part;
  }).join("\\mid ");
}

function practiceDomainNotationLatex(value) {
  const subscripts = "₀₁₂₃₄₅₆₇₈₉";
  const superscripts = "⁰¹²³⁴⁵⁶⁷⁸⁹⁺⁻";
  const superscriptValues = "0123456789+-";
  const raw = String(value || "").trim();
  const source = raw.startsWith("（") && raw.endsWith("）") && raw.includes("|") ? raw.slice(1, -1) : raw;
  return normalizePracticeStandardStateLatex(source.replace(/（/g, "(").replace(/）/g, ")"))
    .replace(/[₀₁₂₃₄₅₆₇₈₉]+/g, (text) => `_{${[...text].map((char) => subscripts.indexOf(char)).join("")}}`)
    .replace(/[⁰¹²³⁴⁵⁶⁷⁸⁹⁺⁻]+/g, (text) => `^{${[...text].map((char) => superscriptValues[superscripts.indexOf(char)]).join("")}}`)
    .replace(/\|/g, "\\mid ")
    .replace(/\((aq|s|l|g)\)/g, "(\\mathrm{$1})")
    .replace(/\bCp,?m\b/g, "C_{p,m}")
    .replace(/\bCv,?m\b/g, "C_{v,m}");
}

function practiceClipboardTextSegment(value) {
  return escapeHtml(String(value || ""))
    .replace(/\*\*([^*\n]+)\*\*/g, "<strong>$1</strong>")
    .replace(/`([^`\n]+)`/g, "<code>$1</code>")
    // 常见科学量在历史数据中可能以 T0、p1、CO2 形式存在。只在普通
    // 文本片段中补下标；显式 LaTeX 由 MathML 分支处理，避免破坏公式。
    .replace(/([A-Za-zΑ-ω]{1,8})(\d+)/g, "$1<sub>$2</sub>");
}

function practiceClipboardDomainTextHtml(value, mathJax, word = false) {
  const source = String(value || "");
  const pattern = /(（[^（），。；：!?！？、\n]{1,180}\|[^（），。；：!?！？、\n]{1,180}）)|((?<![\w$])[A-Za-zΑ-ωΔ∆ΘΓΛΣΠΩ][A-Za-zΑ-ωΔ∆ΘΓΛΣΠΩ0-9_{}()[\],°ºᵒθ₀-₉⁰¹²³⁴⁵⁶⁷⁸⁹⁺⁻+\-*/|·×\\'\s]{0,180}(?:=|≈|≠|≤|≥|∝|→|⇌)[A-Za-zΑ-ωΔ∆ΘΓΛΣΠΩ0-9_{}()[\],°ºᵒθ₀-₉⁰¹²³⁴⁵⁶⁷⁸⁹⁺⁻+\-*/|·×\\'.\s]{1,180})|((?<![A-Za-z])(?:(?:[Δ∆](?:_[A-Za-z]+)?\s*[GHUSAF](?:_[A-Za-z,]+)?|C(?:_\{?[pv](?:,m)?\}?|[pv],?m?))(?:\s*(?:\^\s*\{?\s*(?:o|O|\\circ|θ|\\theta)\s*\}?|[°ºᵒθ]))?|[GHUSAFKE](?:\s*(?:\^\s*\{?\s*(?:o|O|\\circ|θ|\\theta)\s*\}?|[°ºᵒθ]))))|((?<![A-Za-z])(?=[A-Za-z0-9_{}^₀-₉⁰¹²³⁴⁵⁶⁷⁸⁹⁺⁻+\-]{1,36}(?:[₀-₉⁰¹²³⁴⁵⁶⁷⁸⁹⁺⁻]|_\{?\d|\^\{?[+\-\d]|\((?:aq|s|l|g)\)))(?:[A-Z][a-z]?(?:\d+|_\{?\d+\}?|[₀-₉]+)?){1,8}(?:\^\{?[+\-\d]+\}?|[⁰¹²³⁴⁵⁶⁷⁸⁹⁺⁻]+)?(?:\((?:aq|s|l|g)\))?)/g;
  const parts = [];
  let cursor = 0;
  for (let match; (match = pattern.exec(source));) {
    parts.push(practiceClipboardTextSegment(source.slice(cursor, match.index)));
    const electrode = match[0].startsWith("（") && match[0].endsWith("）") && match[0].includes("|");
    if (electrode) parts.push("（");
    try {
      parts.push(practiceClipboardMathHtml(practiceDomainNotationLatex(match[0]), mathJax, false, word));
    } catch {
      parts.push(practiceClipboardTextSegment(electrode ? match[0].slice(1, -1) : match[0]));
    }
    if (electrode) parts.push("）");
    cursor = match.index + match[0].length;
  }
  parts.push(practiceClipboardTextSegment(source.slice(cursor)));
  return parts.join("");
}

function practiceClipboardMathHtml(latex, mathJax, display = false, word = false) {
  const source = repairPracticeClipboardLatex(latex);
  const mathml = String(mathJax.tex2mml(source, { display })).trim();
  // MathML defaults to italic identifiers in browsers, but some clipboard
  // consumers import unannotated <mi> nodes as regular text. Make the
  // conventional variable style explicit without overriding commands such as
  // \\mathrm{} that MathJax already marks as upright.
  const styledMathml = mathml.replace(/<mi(?![^>]*\bmathvariant=)([^>]*)>/g, '<mi$1 mathvariant="italic">');
  // Microsoft Word imports MathML embedded in clipboard HTML as native Office
  // Math (OMML). Converting it to styled spans preserves appearance but loses
  // equation editability, so Word mode must keep the original MathML tree.
  if (word) return styledMathml;
  return styledMathml.replace(/<math([^>]*)>([\s\S]*)<\/math>/, (_match, attributes, body) =>
    `<math${attributes}><semantics>${body}<annotation encoding="application/x-tex">${escapeHtml(source)}</annotation></semantics></math>`
  );
}

function practiceClipboardTextHtml(value, mathJax, { word = false } = {}) {
  const source = String(value || "");
  const parts = [];
  let cursor = 0;
  // Generated material occasionally contains a chemical formula such as
  // ε-\\mathrm{Fe_3N} outside explicit `\\(...\\)` delimiters. Treat the
  // entire notation as one formula so Word cannot retain `\\mathrm{` while
  // dropping the embedded MathML characters.
  const mathPattern = /(\\\(([\s\S]*?)\\\)|\\\[([\s\S]*?)\\\]|\$\$([\s\S]*?)\$\$|\$([^$\n]+)\$|((?:[A-Za-zΑ-ωεγ'′]+\s*-\s*)?\\mathrm\{(?:[^{}]|\{[^{}]*\})+\}))/g;
  for (let match; (match = mathPattern.exec(source));) {
    parts.push(practiceClipboardDomainTextHtml(source.slice(cursor, match.index), mathJax, word));
    const latex = match[2] || match[3] || match[4] || match[5] || match[6] || "";
    const display = Boolean(match[3] || match[4]);
    try {
      parts.push(practiceClipboardMathHtml(latex, mathJax, display, word));
    } catch {
      parts.push(`<code>${escapeHtml(match[0])}</code>`);
    }
    cursor = match.index + match[0].length;
  }
  parts.push(practiceClipboardDomainTextHtml(source.slice(cursor), mathJax, word));
  return parts.join("");
}

function practiceClipboardParagraphsHtml(value, mathJax, { word = false, paragraphStyle = "" } = {}) {
  const blocks = [];
  let current = [];
  const flush = () => {
    const text = current.join("").trim();
    if (text) blocks.push(text);
    current = [];
  };
  for (const rawLine of String(value || "").replace(/\r/g, "").split("\n")) {
    const line = rawLine.trim();
    if (!line) {
      flush();
      continue;
    }
    // Keep list/sub-question starts distinct. Other provider line wraps are
    // merged, so Word wraps them naturally instead of receiving manual breaks.
    if (/^(?:[-•●]|[（(]?\d+[）).、])\s*/.test(line) && current.length) flush();
    current.push(line);
  }
  flush();
  return blocks.map((block) => `<p style="${paragraphStyle}">${practiceClipboardTextHtml(block, mathJax, { word })}</p>`).join("");
}

function practiceClipboardTableHtml(table, mathJax, { word = false, tableStyle = "", cellStyle = "" } = {}) {
  const header = (table.headers || []).map((cell) => `<th style="${cellStyle};background:#f3f4f6">${practiceClipboardTextHtml(cell, mathJax, { word })}</th>`).join("");
  const rows = (table.rows || []).map((row) => `<tr>${row.map((cell) => `<td style="${cellStyle}">${practiceClipboardTextHtml(cell, mathJax, { word })}</td>`).join("")}</tr>`).join("");
  return `<table border="1" cellspacing="0" cellpadding="0" style="${tableStyle}"><caption style="font-weight:700;margin-bottom:4pt">${escapeHtml(table.title || "")}</caption>${header ? `<thead><tr>${header}</tr></thead>` : ""}<tbody>${rows}</tbody></table>`;
}

function practiceRichClipboardHtml(data, mathJax, { word = false, includeQuestionHeading = true } = {}) {
  const paragraphStyle = "margin:0;line-height:1.5;font-family:'宋体','SimSun',serif;font-size:11pt";
  const headingStyle = "margin:12pt 0 6pt;font-family:'宋体','SimSun',serif;font-size:13pt;font-weight:700";
  const tableStyle = "border-collapse:collapse;margin:8pt 0;font-family:'宋体','SimSun',serif;font-size:10.5pt";
  const cellStyle = "border:1px solid #666;padding:4pt 6pt;vertical-align:top";
  const items = (data.exercises || []).map((rawItem) => {
    const item = normalizePracticeMarkdownTables(rawItem);
    const options = (item.options || []).map((option, optionIndex) =>
      `<p style="${paragraphStyle};margin-left:22pt;text-indent:0;font-weight:400"><span style="font-weight:400">${String.fromCharCode(65 + optionIndex)}. </span>${practiceClipboardTextHtml(practiceClipboardOptionText(option.text), mathJax, { word })}</p>`
    ).join("");
    const tables = (item.tables || []).filter((table) => String(table.location || "stem").includes("stem"))
      .map((table) => practiceClipboardTableHtml(table, mathJax, { word, tableStyle, cellStyle })).join("");
    const formulas = (item.formulas || []).filter((formula) => String(formula.location || "stem").includes("stem")).map((formula) => {
      try {
        return `<p style="${paragraphStyle};font-family:'Cambria Math','Times New Roman',serif">${formula.caption ? `${escapeHtml(formula.caption)}：` : ""}${practiceClipboardMathHtml(formula.latex, mathJax, true, word)}</p>`;
      } catch {
        return `<p style="${paragraphStyle}">${escapeHtml(formula.caption || "公式")}：<code>${escapeHtml(formula.latex || "")}</code></p>`;
      }
    }).join("");
    const heading = includeQuestionHeading
      ? `<h2 style="${headingStyle}">第 ${escapeHtml(item.number || "")} 题</h2>`
      : "";
    return `<section style="margin:0 0 14pt">${heading}${practiceClipboardParagraphsHtml(item.stem, mathJax, { word, paragraphStyle })}${options}${formulas}${tables}</section>`;
  }).join("<hr>");
  const fragment = `<article style="color:#111;background:#fff;font-family:'宋体','SimSun',serif"><h1 style="margin:0 0 12pt;font-family:'宋体','SimSun',serif;font-size:16pt;font-weight:700">${escapeHtml(data.blueprint?.training_goal || "专项练习")}</h1>${items}</article>`;
  if (!word) return fragment;
  return `<!DOCTYPE html><html xmlns:o="urn:schemas-microsoft-com:office:office" xmlns:w="urn:schemas-microsoft-com:office:word" xmlns:m="http://schemas.microsoft.com/office/2004/12/omml"><head><meta charset="utf-8"><meta name="ProgId" content="Word.Document"><style>body{font-family:'宋体','SimSun',serif}.WordSection1{page:WordSection1}math,math *{font-family:'Cambria Math','STIX Two Math',serif;font-style:italic}</style></head><body><div class="WordSection1">${fragment}</div></body></html>`;
}

function copyPracticeWithLegacyEvent(html, plainText) {
  let handled = false;
  const onCopy = (event) => {
    if (!event.clipboardData) return;
    event.preventDefault();
    event.clipboardData.setData("text/html", html);
    event.clipboardData.setData("text/plain", plainText);
    handled = true;
  };
  document.addEventListener("copy", onCopy, { once: true });
  let copied = false;
  try {
    copied = document.execCommand("copy");
  } finally {
    document.removeEventListener("copy", onCopy);
  }
  if (!copied || !handled) throw new Error("当前浏览器未允许写入富文本剪贴板");
}

async function writePracticeClipboard(html, plainText) {
  if (navigator.clipboard?.write && window.ClipboardItem) {
    try {
      await navigator.clipboard.write([new ClipboardItem({
        "text/html": new Blob([html], { type: "text/html" }),
        "text/plain": new Blob([plainText], { type: "text/plain" }),
      })]);
      return "rich";
    } catch {
      // 权限拒绝或非安全上下文时，继续尝试用户点击事件内的兼容复制。
    }
  }
  try {
    copyPracticeWithLegacyEvent(html, plainText);
    return "legacy-rich";
  } catch {
    if (navigator.clipboard?.writeText) {
      await navigator.clipboard.writeText(plainText);
      return "plain";
    }
  }
  throw new Error("浏览器未允许复制。请改用下载题目 Word，或使用 HTTPS/localhost 访问后重试。");
}

async function copyPracticeAsRichText(data, mode = "quick") {
  const plainText = practicePlainText(data);
  const mathJax = await ensurePracticeMathJax();
  const html = practiceRichClipboardHtml(data, mathJax, { word: mode === "word" });
  return writePracticeClipboard(html, plainText);
}

async function copyPracticeWithFeedback(data, mode, button, idleHtml, successText) {
  if (!data || !button) return false;
  button.disabled = true;
  try {
    const result = await copyPracticeAsRichText(data, mode);
    button.innerHTML = `<i class="fas fa-check"></i>${successText}`;
    if (result === "plain") {
      await platformAlert("浏览器只允许复制纯文本；公式与版式可能丢失。请使用“下载题目 Word”获得完整格式。", {
        title: "仅复制了纯文本",
        tone: "warning"
      });
    }
    return true;
  } catch (error) {
    await platformAlert(String(error).replace(/^Error:\s*/, ""), {
      title: mode === "word" ? "Word 格式复制失败" : "快速复制失败",
      tone: "danger"
    });
    return false;
  } finally {
    window.setTimeout(() => {
      button.disabled = false;
      button.innerHTML = idleHtml;
    }, 1600);
  }
}

function practiceQuestionPlainText(item) {
  item = normalizePracticeMarkdownTables(item);
  const titleParts = [
    `第 ${item.number || ""} 题`,
    item.question_type ? `（${item.question_type}）` : "",
    item.difficulty ? `［${item.difficulty}］` : ""
  ].filter(Boolean);
  const lines = [titleParts.join(" "), String(item.stem || "").trim()];
  for (const table of item.tables || []) {
    if (!String(table.location || "stem").includes("stem")) continue;
    if (table.headers?.length) lines.push(table.headers.join("\t"));
    for (const row of table.rows || []) lines.push(row.join("\t"));
  }
  for (const [optionIndex, option] of (item.options || []).entries()) {
    lines.push(`${String.fromCharCode(65 + optionIndex)}. ${practiceClipboardOptionText(option.text)}`);
  }
  return lines.filter(Boolean).join("\n");
}

async function copyPracticeQuestion(index, button) {
  const item = latestPracticeSet?.exercises?.[index];
  if (!item || item.generation_status === "failed" || !button) return;
  const idleHtml = button.innerHTML;
  button.disabled = true;
  try {
    const mathJax = await ensurePracticeMathJax();
    const result = await writePracticeClipboard(
      practiceRichClipboardHtml({
        blueprint: { training_goal: `第 ${item.number || index + 1} 题` },
        exercises: [item]
      // The individual-copy action is most often pasted into Word. Use the
      // Office-compatible MathML payload so subscripts, numeric values and
      // units are retained instead of relying on Word's generic HTML import.
      }, mathJax, { word: true, includeQuestionHeading: false }),
      practiceQuestionPlainText(item)
    );
    button.innerHTML = '<i class="fas fa-check"></i><span>已复制</span>';
    if (result === "plain") {
      await platformAlert("浏览器仅支持复制纯文本；公式与版式可能丢失。", {
        title: "仅复制了纯文本",
        tone: "warning"
      });
    }
  } catch (error) {
    await platformAlert(String(error).replace(/^Error:\s*/, ""), {
      title: "复制本题失败",
      tone: "danger"
    });
  } finally {
    window.setTimeout(() => {
      button.disabled = false;
      button.innerHTML = idleHtml;
    }, 1600);
  }
}

let practiceMathJaxPromise = null;

function normalizeStandaloneMathLines(value) {
  return String(value || "").split("\n").map((line) => {
    const trimmed = line.trim();
    if (!trimmed || /^(?:\\\(|\\\[|\$)/.test(trimmed)) return line;
    const labeled = trimmed.match(/^(.*?公式[：:]\s*)(.+)$/);
    const label = labeled ? labeled[1] : "";
    const candidate = labeled ? labeled[2] : trimmed;
    const hasLatexCommand = /\\(?:frac|dfrac|tfrac|Delta|delta|partial|left|right|quad|neq|leq|geq|times|cdot|sum|int|oint|mathrm|text|ln|exp|sqrt|ominus|theta|gamma|xi)\b/.test(candidate);
    const looksLikeEquation = !/[\u4e00-\u9fff]/.test(candidate) && /(?:=|<|>|\\Rightarrow|\\approx)/.test(candidate);
    const simpleEquation = /^[A-Za-zΑ-ωΔΣΠΩμνρλθ][A-Za-z0-9Α-ωΔΣΠΩμνρλθ_{}' ]*\s*(?:=|<|>)/.test(candidate);
    if (!looksLikeEquation || (!hasLatexCommand && !simpleEquation)) return line;
    const leading = line.slice(0, line.indexOf(trimmed));
    return `${leading}${label}\\(${candidate}\\)`;
  }).join("\n");
}

function mathAwareHtml(value) {
  const preserved = [];
  let html = escapeHtml(normalizeStandaloneMathLines(value)).replace(/(\\\([\s\S]*?\\\)|\\\[[\s\S]*?\\\]|\$\$[\s\S]*?\$\$|\$[^$\n]+\$)/g, (match) => {
    preserved.push(match);
    return `@@MATH${preserved.length - 1}@@`;
  });
  html = html
    .replace(/(^|[^\w@])((?:[A-Za-zΑ-ωΔΣΠΩμνρλθ]|\d+(?:\.\d+)?)\w*\s*\^\s*(?:\{[^}\n]+\}|[-+]?\d+(?:\.\d+)?|[A-Za-z]+))/g, (_, prefix, token) => `${prefix}\\(${token.replace(/\s+/g, "")}\\)`)
    .replace(/(^|[^\w@])([A-Za-zΑ-ωΔΣΠΩμνρλθ]+_(?:\{[^}\n]+\}|[A-Za-z0-9]+)(?:\^(?:\{[^}\n]+\}|[-+]?\d+|[A-Za-z]+))?)/g, (_, prefix, token) => `${prefix}\\(${token}\\)`);
  return html.replace(/@@MATH(\d+)@@/g, (_, index) => preserved[Number(index)] || "");
}

function practiceMarkdown(value) {
  return mathAwareHtml(value)
    .replace(/\*\*([^*\n]+)\*\*/g, "<strong>$1</strong>")
    .replace(/`([^`\n]+)`/g, "<code>$1</code>")
    .replace(/\n/g, "<br>");
}

function practiceMarkdownTableCells(line) {
  let raw = String(line || "").trim();
  if (!raw.includes("|")) return null;
  if (raw.startsWith("|")) raw = raw.slice(1);
  if (raw.endsWith("|")) raw = raw.slice(0, -1);
  const cells = raw.split(/(?<!\\)\|/).map((cell) => cell.trim().replace(/\\\|/g, "|"));
  return cells.length >= 2 ? cells : null;
}

function isPracticeMarkdownTableDivider(cells) {
  return Array.isArray(cells) && cells.length > 0 && cells.every((cell) => /^:?-{3,}:?$/.test(cell));
}

function extractPracticeMarkdownTables(stem) {
  const lines = String(stem || "").replace(/\r/g, "").split("\n");
  const kept = [];
  const tables = [];
  for (let index = 0; index < lines.length;) {
    const headers = practiceMarkdownTableCells(lines[index]);
    const divider = practiceMarkdownTableCells(lines[index + 1]);
    if (!headers || headers.length < 2 || !divider || divider.length !== headers.length || !isPracticeMarkdownTableDivider(divider)) {
      kept.push(lines[index]);
      index += 1;
      continue;
    }
    const rows = [];
    let cursor = index + 2;
    while (cursor < lines.length) {
      const row = practiceMarkdownTableCells(lines[cursor]);
      if (!row || row.length !== headers.length) break;
      rows.push(row);
      cursor += 1;
    }
    if (!rows.length) {
      kept.push(lines[index]);
      index += 1;
      continue;
    }
    tables.push({ table_id: `markdown_t${tables.length + 1}`, location: "stem", title: "", headers, rows });
    if (kept.length && kept[kept.length - 1].trim()) kept.push("");
    index = cursor;
  }
  return { stem: kept.join("\n").replace(/\n{3,}/g, "\n\n").trim(), tables };
}

function normalizePracticeQuestionText(value) {
  const blocks = [];
  let paragraph = [];
  let seenContent = false;
  let nextSubquestionNumber = 1;
  const flush = () => {
    const text = paragraph.filter(Boolean).join(" ").trim();
    if (text) blocks.push(text);
    paragraph = [];
  };
  String(value || "").replace(/\r\n?/g, "\n").split("\n").forEach((rawLine) => {
    let line = rawLine.trim();
    if (!line) {
      flush();
      return;
    }
    if (!seenContent) {
      line = line.replace(/^\s*(?:#{1,6}\s*)?(?:第\s*\d+\s*题|题目\s*\d+)\s*(?:[：:.．、-]\s*)?/, "").trim();
      if (!line) return;
    }
    const subquestion = line.match(/^\s*(?:[（(]\s*(\d{1,2})\s*[）)]|(\d{1,2})\s*[.)）．、])\s*(?:[、.．:：-]\s*)?/);
    const circled = line.match(/^\s*([①②③④⑤⑥⑦⑧⑨⑩⑪⑫⑬⑭⑮⑯⑰⑱⑲⑳])\s*(?:[、.．:：-]\s*)?/);
    if (subquestion) {
      flush();
      blocks.push(`(${nextSubquestionNumber}) ${line.slice(subquestion[0].length).trim()}`.trim());
      nextSubquestionNumber += 1;
    } else if (circled) {
      flush();
      blocks.push(`${circled[1]} ${line.slice(circled[0].length).trim()}`.trim());
    } else if (/^【(?:材料|已知|说明|注|提示)】/.test(line)) {
      flush();
      blocks.push(line);
    } else {
      paragraph.push(line);
    }
    seenContent = true;
  });
  flush();
  return blocks.join("\n\n");
}

function normalizePracticeMarkdownTables(item) {
  if (!item || typeof item !== "object") return item;
  // Preserve pipe-table row boundaries until the table extractor has lifted
  // them; the question-text normalizer may then safely join ordinary wraps.
  const extracted = extractPracticeMarkdownTables(item.stem);
  const stem = normalizePracticeQuestionText(extracted.stem);
  if (!extracted.tables.length) return { ...item, stem };
  const tables = Array.isArray(item.tables) ? item.tables.map((table) => ({ ...table })) : [];
  const seen = new Set(tables.map((table) => JSON.stringify([table.headers || [], table.rows || []])));
  extracted.tables.forEach((table) => {
    const signature = JSON.stringify([table.headers, table.rows]);
    if (seen.has(signature)) return;
    table.table_id = `t${tables.length + 1}`;
    tables.push(table);
    seen.add(signature);
  });
  return { ...item, stem, tables };
}

function ensurePracticeMathJax() {
  if (window.MathJax?.typesetPromise) return Promise.resolve(window.MathJax);
  if (practiceMathJaxPromise) return practiceMathJaxPromise;
  window.MathJax = {
    tex: {
      inlineMath: [["\\(", "\\)"], ["$", "$"]],
      displayMath: [["\\[", "\\]"], ["$$", "$$"]],
      processEscapes: true
    },
    options: {
      skipHtmlTags: ["script", "noscript", "style", "textarea", "pre"]
    }
  };
  practiceMathJaxPromise = new Promise((resolve, reject) => {
    const script = document.createElement("script");
    script.src = "/vendor/mathjax/tex-mml-chtml.js";
    script.async = true;
    script.onload = () => resolve(window.MathJax);
    script.onerror = () => reject(new Error("公式渲染组件加载失败"));
    document.head.appendChild(script);
  });
  return practiceMathJaxPromise;
}

async function typesetMath(container) {
  if (!container) return;
  try {
    const mathJax = await ensurePracticeMathJax();
    mathJax.typesetClear?.([container]);
    await mathJax.typesetPromise([container]);
  } catch {
    // 网络不可用时保留可读的 LaTeX 原文，不影响题目和导出。
  }
}

async function typesetPracticeMath() {
  return typesetMath($("practiceResults"));
}

function normalizePracticeFormulaLatex(value) {
  let latex = String(value || "").trim();
  // The practice renderer owns the outer display delimiters. Remove one
  // provider-supplied pair so delimiters are not rendered as literal text.
  if (latex.startsWith("\\[") && latex.endsWith("\\]")) latex = latex.slice(2, -2).trim();
  else if (latex.startsWith("\\(") && latex.endsWith("\\)")) latex = latex.slice(2, -2).trim();
  else if (latex.startsWith("$$") && latex.endsWith("$$")) latex = latex.slice(2, -2).trim();
  else if (latex.startsWith("$") && latex.endsWith("$") && latex.length > 1) latex = latex.slice(1, -1).trim();
  return latex;
}

function practiceDiagramSvg(figure) {
  const nodes = (figure.nodes || []).filter((node) => node?.id && node?.label);
  if (nodes.length < 2) return "";
  const positions = new Map(nodes.map((node) => [String(node.id), {
    ...node,
    x: 70 + Math.max(0, Math.min(1, Number(node.x))) * 860,
    y: 45 + Math.max(0, Math.min(1, Number(node.y))) * 330
  }]));
  const markerId = `arrow-${String(figure.figure_id || "diagram").replace(/[^a-zA-Z0-9_-]/g, "-")}`;
  const edges = (figure.edges || []).map((edge) => {
    const from = positions.get(String(edge?.from));
    const to = positions.get(String(edge?.to));
    if (!from || !to) return "";
    const labelX = (from.x + to.x) / 2;
    const labelY = (from.y + to.y) / 2 - 8;
    return `<g><line x1="${from.x}" y1="${from.y}" x2="${to.x}" y2="${to.y}" stroke="#475569" stroke-width="2" ${edge.directed === false ? "" : `marker-end="url(#${markerId})"`}/>${edge.label ? `<text x="${labelX}" y="${labelY}" text-anchor="middle">${escapeHtml(edge.label)}</text>` : ""}</g>`;
  }).join("");
  const nodeShapes = nodes.map((raw) => {
    const node = positions.get(String(raw.id));
    const shape = String(node.shape || "box");
    const body = shape === "circle"
      ? `<circle cx="${node.x}" cy="${node.y}" r="38"/>`
      : shape === "ellipse"
        ? `<ellipse cx="${node.x}" cy="${node.y}" rx="75" ry="34"/>`
        : `<rect x="${node.x - 72}" y="${node.y - 32}" width="144" height="64" rx="10"/>`;
    return `<g class="practice-diagram-node">${body}<text x="${node.x}" y="${node.y + 5}" text-anchor="middle">${escapeHtml(node.label)}</text></g>`;
  }).join("");
  return `<svg class="practice-diagram-svg" viewBox="0 0 1000 420" role="img" aria-label="${escapeHtml(figure.title || "题目示意图")}"><defs><marker id="${markerId}" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="6" markerHeight="6" orient="auto-start-reverse"><path d="M 0 0 L 10 5 L 0 10 z" fill="#475569"/></marker></defs>${edges}${nodeShapes}</svg>`;
}

function practiceExtrasHtml(item, location) {
  const formulas = (item.formulas || []).filter((row) => String(row.location || "stem").includes(location));
  const tables = (item.tables || []).filter((row) => String(row.location || "stem").includes(location));
  const figures = (item.figures || []).filter((row) => String(row.location || "stem").includes(location));
  return [
    ...formulas.map((formula) => `
      <figure class="practice-formula">
        ${formula.caption ? `<figcaption>${escapeHtml(formula.caption)}</figcaption>` : ""}
        <div class="practice-math">\\[${escapeHtml(normalizePracticeFormulaLatex(formula.latex))}\\]</div>
      </figure>
    `),
    ...tables.map((table) => `
      <figure class="practice-data-table">
        ${table.title ? `<figcaption>${escapeHtml(table.title)}</figcaption>` : ""}
        <div><table>
          ${table.headers?.length ? `<thead><tr>${table.headers.map((cell) => `<th>${escapeHtml(cell)}</th>`).join("")}</tr></thead>` : ""}
          <tbody>${(table.rows || []).map((row) => `<tr>${row.map((cell) => `<td>${escapeHtml(cell)}</td>`).join("")}</tr>`).join("")}</tbody>
        </table></div>
      </figure>
    `),
    ...figures.map((figure) => figure.series?.some((series) => series.points?.length >= 2)
      ? `<figure class="practice-generated-chart">
          ${figure.title ? `<figcaption>${escapeHtml(figure.title)}</figcaption>` : ""}
          <canvas data-practice-figure="${escapeHtml(figure.figure_id)}" aria-label="${escapeHtml(figure.title || "题目图表")}"></canvas>
          ${figure.description ? `<p>${escapeHtml(figure.description)}</p>` : ""}
        </figure>`
      : practiceDiagramSvg(figure)
        ? `<figure class="practice-diagram-spec">
          <figcaption>${escapeHtml(figure.title || "图示")}</figcaption>
          ${practiceDiagramSvg(figure)}
          ${figure.description ? `<p>${escapeHtml(figure.description)}</p>` : ""}
        </figure>`
        : `<div class="practice-figure-error" role="alert">题图生成失败：缺少可绘制的数据或节点关系，当前题目不能正式导出。</div>`
    )
  ].join("");
}

function drawPracticeCharts(data) {
  const figures = new Map();
  for (const item of data.exercises || []) {
    for (const figure of item.figures || []) figures.set(String(figure.figure_id), figure);
  }
  document.querySelectorAll("canvas[data-practice-figure]").forEach((canvas) => {
    const figure = figures.get(canvas.dataset.practiceFigure);
    if (!figure) return;
    const width = Math.max(520, canvas.parentElement?.clientWidth || 520);
    const height = 260;
    const scale = window.devicePixelRatio || 1;
    canvas.width = width * scale;
    canvas.height = height * scale;
    canvas.style.width = `${width}px`;
    canvas.style.height = `${height}px`;
    const ctx = canvas.getContext("2d");
    ctx.scale(scale, scale);
    ctx.fillStyle = "#fff";
    ctx.fillRect(0, 0, width, height);
    const pad = { left: 54, right: 20, top: 20, bottom: 42 };
    const chartNodes = (figure.nodes || []).filter((node) => Number.isFinite(Number(node?.x)) && Number.isFinite(Number(node?.y)));
    const points = [
      ...(figure.series || []).flatMap((series) => series.points || []),
      ...chartNodes.map((node) => [Number(node.x), Number(node.y)])
    ];
    if (!points.length) return;
    const xs = points.map((point) => Number(point[0]));
    const ys = points.map((point) => Number(point[1]));
    const xMin = Math.min(...xs), xMax = Math.max(...xs);
    const yMin = Math.min(...ys), yMax = Math.max(...ys);
    const px = (x) => pad.left + ((x - xMin) / (xMax - xMin || 1)) * (width - pad.left - pad.right);
    const py = (y) => height - pad.bottom - ((y - yMin) / (yMax - yMin || 1)) * (height - pad.top - pad.bottom);
    ctx.strokeStyle = "#94a3b8"; ctx.lineWidth = 1;
    ctx.beginPath(); ctx.moveTo(pad.left, pad.top); ctx.lineTo(pad.left, height - pad.bottom); ctx.lineTo(width - pad.right, height - pad.bottom); ctx.stroke();
    const colors = ["#2563eb", "#7c3aed", "#0f9f6e", "#ea580c"];
    (figure.series || []).forEach((series, index) => {
      const rows = series.points || [];
      ctx.strokeStyle = colors[index % colors.length];
      ctx.fillStyle = colors[index % colors.length];
      ctx.lineWidth = 2;
      if (figure.figure_type === "bar") {
        const barWidth = Math.max(8, (width - pad.left - pad.right) / Math.max(rows.length * 2, 4));
        rows.forEach(([x, y]) => ctx.fillRect(px(x) - barWidth / 2, py(y), barWidth, height - pad.bottom - py(y)));
      } else {
        ctx.beginPath();
        rows.forEach(([x, y], rowIndex) => rowIndex ? ctx.lineTo(px(x), py(y)) : ctx.moveTo(px(x), py(y)));
        if (figure.figure_type !== "scatter") ctx.stroke();
        rows.forEach(([x, y]) => { ctx.beginPath(); ctx.arc(px(x), py(y), 3.5, 0, Math.PI * 2); ctx.fill(); });
      }
    });
    chartNodes.forEach((node) => {
      const x = px(Number(node.x));
      const y = py(Number(node.y));
      ctx.beginPath();
      ctx.arc(x, y, 4.5, 0, Math.PI * 2);
      ctx.fillStyle = "#0f172a";
      ctx.fill();
      ctx.font = "12px sans-serif";
      ctx.textAlign = "left";
      ctx.fillText(String(node.label || ""), x + 7, y - 7);
    });
    ctx.fillStyle = "#64748b"; ctx.font = "12px sans-serif";
    ctx.textAlign = "left";
    ctx.fillText(figure.x_label || "", width / 2, height - 10);
    ctx.save(); ctx.translate(14, height / 2); ctx.rotate(-Math.PI / 2); ctx.fillText(figure.y_label || "", 0, 0); ctx.restore();
  });
}

/* =========================================================
   专项练习 · 工作台模式
   ========================================================= */
const PRACTICE_PRESETS = {
  basic:    { count: 5,  difficulty: "基础到进阶", questionTypes: [],            focus: "" },
  error:    { count: 8,  difficulty: "同等难度",  questionTypes: [],            focus: "针对原题易错点生成相似变式" },
  advanced: { count: 10, difficulty: "进阶到挑战", questionTypes: ["综合题", "计算题"], focus: "跨章节综合应用" }
};
const PRACTICE_FILTERS = { type: new Set(), difficulty: new Set(), tag: new Set() };
let practiceDrawerWasSkipped = false;
let practiceScopeReturnFocus = null;

function applyPracticePreset(presetKey) {
  const preset = PRACTICE_PRESETS[presetKey];
  if (!preset) return;
  const count = $("practiceCount");
  const difficulty = $("practiceDifficulty");
  const focus = $("practiceFocus");
  if (count) count.value = String(preset.count);
  if (difficulty) difficulty.value = preset.difficulty;
  document.querySelectorAll('input[name="practiceQuestionType"]').forEach((input) => {
    input.checked = preset.questionTypes.includes(input.value);
  });
  if (focus) focus.value = preset.focus;
  togglePracticeConfig(true);
  updatePracticeConfigSummary();
  document.querySelectorAll(".practice-preset").forEach((btn) => {
    btn.classList.toggle("active", btn.dataset.practicePreset === presetKey);
  });
}

function syncPracticeConfigState() {
  const card = $("practiceConfigCard");
  if (!card) return;
  const isOpen = card.open;
  card.classList.toggle("practice-config-card--collapsed", !isOpen);
  $("practiceConfigToggle")?.setAttribute("aria-expanded", String(isOpen));
}

function togglePracticeConfig(forceOpen) {
  const card = $("practiceConfigCard");
  if (!card) return;
  card.open = forceOpen !== undefined ? !!forceOpen : !card.open;
  syncPracticeConfigState();
}

function updatePracticeConfigSummary() {
  const types = Array.from(document.querySelectorAll('input[name="practiceQuestionType"]:checked')).map((i) => i.value);
  const typeLabel = types.length === 0 ? "题型随机" : types.join("+");
  setText("practiceConfigSummary", `${typeLabel} · 题量与难度在范围确认设置`);
}

function openPracticeScopeDrawer() {
  const drawer = $("practiceScopeDrawer");
  if (!drawer) return;
  drawer.classList.remove("hidden");
  drawer.classList.add("practice-scope-drawer--open");
  drawer.setAttribute("aria-hidden", "false");
  $("practiceScopeResume")?.classList.add("hidden");
  syncPracticeWorkflowActions("scope");
  requestAnimationFrame(() => drawer.scrollIntoView({ behavior: "smooth", block: "start" }));
}

function closePracticeScopeDrawer() {
  const drawer = $("practiceScopeDrawer");
  if (!drawer) return;
  drawer.classList.add("hidden");
  drawer.classList.remove("practice-scope-drawer--open");
  drawer.setAttribute("aria-hidden", "true");
  if (latestPracticeSourceScope && !latestPracticePlan) $("practiceScopeResume")?.classList.remove("hidden");
  syncPracticeWorkflowActions("scope");
}

function normalizePracticeInlineLayout() {
  const grid = document.querySelector("#page-practice .workbench-grid");
  const flow = grid?.querySelector(":scope > section");
  const input = $("workbench-sidebar");
  const stage = $("section-stage");
  const drawer = $("practiceScopeDrawer");
  if (!grid || !flow || !input || !stage || !drawer) return;

  grid.classList.add("practice-inline-flow");
  input.classList.add("practice-inline-input");
  const heading = input.querySelector(".practice-workspace-heading");
  const guide = input.querySelector(".practice-entry-guide");
  const statusRow = stage.nextElementSibling;
  let overview = $("practiceWorkflowOverview");
  if (!overview) {
    overview = document.createElement("section");
    overview.id = "practiceWorkflowOverview";
    overview.className = "practice-workflow-overview";
    stage.insertAdjacentElement("beforebegin", overview);
  }
  if (heading) overview.append(heading);
  overview.append(stage);
  if (guide) overview.insertAdjacentElement("afterend", guide);
  statusRow?.insertAdjacentElement("afterend", input);

  const loading = $("practiceLoading");
  loading?.insertAdjacentElement("afterend", drawer);
  drawer.querySelector(".practice-scope-drawer__panel")?.setAttribute("aria-modal", "false");
  syncPracticeWorkflowActions();
}

function returnToPracticeSourceInput() {
  closePracticeScopeDrawer();
  $("practiceScopeResume")?.classList.add("hidden");
  if (currentPracticeSourceMode === "knowledge") {
    goToPage("knowledge");
    return;
  }
  $("practiceEmpty")?.classList.remove("hidden");
  setPracticeStage("submit");
  setPracticeStageDescription("可修改原题材料，再重新解析考点与范围。");
  setText("practiceSourceStatus", "可调整材料");
}

function updatePracticeScopePreview() {
  const strategy = document.querySelector('input[name="practiceSetStrategy"]:checked')?.value || "";
  const selectedCount = document.querySelectorAll('input[name="practiceSourceQuestion"]:checked').length;
  const targeted = Number($("practiceTargetedCount")?.value || 5);
  const variants = Number($("practiceVariantsPerQuestion")?.value || 1);
  const perPoint = Number($("practiceKnowledgePerCount")?.value || 1);
  let n = 0;
  if (strategy === "targeted_set") n = Math.min(targeted, 30);
  else if (strategy === "parallel_exam") n = Math.min(30, selectedCount);
  else if (strategy === "per_question") n = Math.min(30, selectedCount * variants);
  else if (strategy === "knowledge_item_wise") n = Math.min(30, selectedCount * perPoint);
  else if (strategy === "knowledge_overall") n = Math.min(targeted, 30);
  setText("practiceScopePreviewCount", `${n} 题`);
  const difficultyBox = document.querySelector(".practice-difficulty-counts");
  if (n > 0 && difficultyBox?.dataset.total !== String(n)) {
    setDefaultPracticeDifficultyCounts(n, latestPracticeRequest?.difficulty || "基础到进阶", practiceDifficultyCounts());
    difficultyBox.dataset.total = String(n);
  }
  const counts = practiceDifficultyCounts();
  const allocated = counts["基础"] + counts["进阶"] + counts["挑战"];
  const allocation = $("practiceDifficultyAllocation");
  if (allocation) allocation.textContent = `已分配 ${allocated} / ${n} 题`;
  allocation?.closest(".practice-difficulty-counts")?.classList.toggle("is-invalid", allocated !== n);
  const confirmBtn = $("practiceSourceConfirmBtn");
  if (confirmBtn) {
    confirmBtn.disabled = !strategy || selectedCount === 0 || n < 1 || allocated !== n;
    const label = confirmBtn.querySelector("span");
    if (label) label.textContent = blueprintReviewEnabled() ? "按确认范围设计蓝图" : "按确认范围直接生题";
    const icon = confirmBtn.querySelector("i");
    if (icon) icon.className = blueprintReviewEnabled() ? "fas fa-diagram-project" : "fas fa-wand-magic-sparkles";
  }
  syncPracticeWorkflowActions("scope");
}

function practiceDifficultyCounts() {
  const read = (id) => Math.max(0, Math.min(30, Number($(id)?.value || 0)));
  return {
    "基础": read("practiceDifficultyBasicCount"),
    "进阶": read("practiceDifficultyIntermediateCount"),
    "挑战": read("practiceDifficultyChallengeCount")
  };
}

function nextPracticePreferenceOrder(kind) {
  practicePreferenceSequence += 1;
  if (kind === "difficulty") practiceDifficultySelectionOrder = practicePreferenceSequence;
  if (kind === "variant") practiceVariantSelectionOrder = practicePreferenceSequence;
  return practicePreferenceSequence;
}

function restorePracticePreferenceOrders(request = null) {
  const difficultyOrder = Math.max(0, Number(request?.difficulty_selection_order || 0));
  const variantOrder = Math.max(0, Number(request?.blueprint_variant_selection_order || 0));
  practiceDifficultySelectionOrder = Number.isFinite(difficultyOrder) ? difficultyOrder : 0;
  practiceVariantSelectionOrder = Number.isFinite(variantOrder) ? variantOrder : 0;
  practicePreferenceSequence = Math.max(practiceDifficultySelectionOrder, practiceVariantSelectionOrder);
}

function setDefaultPracticeDifficultyCounts(total, mode = "基础到进阶", existing = null) {
  total = Math.max(1, Math.min(30, Number(total) || 1));
  let counts = existing;
  if (!counts || Object.values(counts).reduce((sum, value) => sum + Number(value || 0), 0) !== total) {
    let basic = 0;
    let intermediate = 0;
    let challenge = 0;
    if (["进阶为主", "进阶到挑战", "挑战"].includes(mode)) {
      challenge = mode === "挑战" ? total : Math.max(1, Math.round(total * (mode === "进阶到挑战" ? .4 : .3)));
      intermediate = total - challenge;
    } else {
      intermediate = mode === "进阶" ? total : Math.round(total * (mode === "基础为主" ? .2 : .4));
      basic = total - intermediate;
    }
    counts = { "基础": basic, "进阶": intermediate, "挑战": challenge };
  }
  if ($("practiceDifficultyBasicCount")) $("practiceDifficultyBasicCount").value = String(counts["基础"] || 0);
  if ($("practiceDifficultyIntermediateCount")) $("practiceDifficultyIntermediateCount").value = String(counts["进阶"] || 0);
  if ($("practiceDifficultyChallengeCount")) $("practiceDifficultyChallengeCount").value = String(counts["挑战"] || 0);
}

function setPracticeStage(stage) {
  const order = ["submit", "analyze", "scope", "plan", "generate", "results"];
  const activeIndex = order.indexOf(stage);
  document.querySelectorAll(".practice-step").forEach((el) => {
    const idx = order.indexOf(el.dataset.stage);
    el.classList.remove("stage-step--active", "stage-step--done", "stage-step--idle", "stage-step--skipped");
    if (idx === -1) return;
    if (idx < activeIndex) el.classList.add("stage-step--done");
    else if (idx === activeIndex) el.classList.add("stage-step--active");
    else el.classList.add("stage-step--idle");
  });
  const grid = document.querySelector("#page-practice .workbench-grid");
  grid?.classList.toggle("practice-focus-stage", stage !== "submit");
  setPracticeSourceEntryVisibility(stage === "submit");
  syncPracticeWorkflowActions(stage);
}

function syncPracticeWorkflowActions(stage) {
  const actions = $("practiceWorkflowActions");
  const back = $("practiceWorkflowBackBtn");
  const submit = $("practiceGenerateBtn");
  const primary = $("practiceWorkflowPrimaryBtn");
  const step = $("practiceWorkflowActionStep");
  const hint = $("practiceWorkflowActionHint");
  if (!actions || !back || !submit || !primary || !step || !hint) return;

  const activeStage = stage || actions.dataset.stage || "submit";
  actions.dataset.stage = activeStage;
  const isLoading = !$("practiceLoading")?.classList.contains("hidden");
  const visible = currentPage === "practice" && !isLoading && ["submit", "scope", "plan"].includes(activeStage);
  actions.classList.toggle("hidden", !visible);
  submit.classList.toggle("hidden", activeStage !== "submit");
  primary.classList.toggle("hidden", activeStage === "submit");
  if (!visible) return;

  const setPrimary = ({ stepText, hintText, backText, primaryText, disabled = false, icon = "arrow-right" }) => {
    step.textContent = stepText;
    hint.textContent = hintText;
    back.querySelector("span").textContent = backText;
    primary.disabled = disabled;
    primary.innerHTML = `<i data-lucide="${icon}" class="h-4 w-4"></i><span>${primaryText}</span>`;
  };

  if (activeStage === "submit") {
    step.textContent = "第 1 步 · 提交材料";
    hint.textContent = "确认材料后开始解析考点与范围";
    back.querySelector("span").textContent = "返回首页";
    syncPracticeSubmitAvailability();
  } else if (activeStage === "scope") {
    const scopeOpen = !$("practiceScopeDrawer")?.classList.contains("hidden");
    const confirm = $("practiceSourceConfirmBtn");
    setPrimary({
      stepText: "第 3 步 · 确认范围",
      hintText: scopeOpen ? "确认范围、题量与难度后进入下一任务" : "解析结果已保存，可继续确认出题范围",
      backText: "返回修改材料",
      primaryText: scopeOpen
        ? (blueprintReviewEnabled() ? "按确认范围设计蓝图" : "按确认范围直接生题")
        : "继续确认范围",
      disabled: scopeOpen && Boolean(confirm?.disabled),
      icon: scopeOpen ? (blueprintReviewEnabled() ? "workflow" : "wand-2") : "list-checks"
    });
  } else if (activeStage === "plan") {
    setPrimary({
      stepText: "第 4 步 · 审查蓝图",
      hintText: "逐项核对蓝图后，开始生成完整练习",
      backText: "返回范围调整",
      primaryText: "按此蓝图生成练习",
      disabled: Boolean($("practicePlanConfirmBtn")?.disabled),
      icon: "wand-2"
    });
  }
  window.lucide?.createIcons();
}

function handlePracticeWorkflowBack() {
  const stage = $("practiceWorkflowActions")?.dataset.stage || "submit";
  if (stage === "submit") {
    goToPage("home");
    return;
  }
  if (stage === "scope") {
    returnToPracticeSourceInput();
    return;
  }
  $("practicePlanBackBtn")?.click();
}

function handlePracticeWorkflowPrimary() {
  const stage = $("practiceWorkflowActions")?.dataset.stage;
  if (stage === "scope") {
    if ($("practiceScopeDrawer")?.classList.contains("hidden")) openPracticeScopeDrawer();
    else $("practiceSourceConfirmBtn")?.click();
    return;
  }
  if (stage === "plan") $("practicePlanConfirmBtn")?.click();
}

// The source form is moved into the inline workflow layout after startup, so
// it is no longer a direct child of .workbench-grid.  Do not rely on the grid
// layout selector to hide it: every workflow stage controls it explicitly.
function setPracticeSourceEntryVisibility(visible) {
  const sidebar = $("workbench-sidebar");
  if (!sidebar) return;
  sidebar.classList.toggle("practice-stage-hidden", !visible);
  sidebar.setAttribute("aria-hidden", String(!visible));
}

function practiceStageForJobOperation(operation) {
  if (operation === "analyze") return "analyze";
  if (operation === "plan") return "plan";
  return "generate";
}

function setPracticeStageSkipped(stage) {
  const el = document.querySelector(`.practice-step[data-stage="${stage}"]`);
  if (!el) return;
  el.classList.remove("stage-step--active", "stage-step--done", "stage-step--idle");
  el.classList.add("stage-step--skipped");
}

function markAllPracticeStagesDone() {
  document.querySelectorAll(".practice-step").forEach((el) => {
    el.classList.remove("stage-step--active", "stage-step--idle", "stage-step--skipped");
    el.classList.add("stage-step--done");
  });
}

function setPracticeStageDescription(text) {
  setText("practiceStageDescription", text);
}

function setPracticeStatusBanner(text, mode) {
  const banner = $("practiceStatusBanner");
  if (!banner) return;
  banner.textContent = text;
  banner.className = "practice-status-banner";
  if (mode) banner.classList.add(`is-${mode}`);
}

function updatePracticeAsideSummary(data) {
  const labels = {
    single: "单题专项",
    targeted_set: "整套专项补强",
    parallel_exam: "平行试卷",
    per_question: "逐题变式"
  };
  setText("practiceResultModeAside", labels[data.generation_strategy] || "专项练习");
  setText("practiceTrainingGoalAside", data.blueprint?.training_goal || "训练目标");
  setText("practiceSummaryCount", String(data.exercises?.length || 0));
  const completion = practiceCompletionContract(data);
  setText("practiceSummaryQuality", completion.display_label);
  setText("practiceSummaryStatus", completion.primary_code === "completed" ? "已生成 · 完成" : completion.display_label);
}

function renderPracticeBlueprintSummary(data) {
  const exercises = data.exercises || [];
  const knowledgeCounts = {};
  const difficultyCounts = {};
  exercises.forEach((item) => {
    (item.knowledge_points || []).forEach((tag) => {
      const key = String(tag);
      knowledgeCounts[key] = (knowledgeCounts[key] || 0) + 1;
    });
    const diff = item.difficulty || "进阶";
    difficultyCounts[diff] = (difficultyCounts[diff] || 0) + 1;
  });
  const maxK = Math.max(1, ...Object.values(knowledgeCounts));
  const maxD = Math.max(1, ...Object.values(difficultyCounts));
  const kContainer = $("practiceKnowledgeBars");
  if (kContainer) {
    const kRows = Object.entries(knowledgeCounts).sort((a, b) => b[1] - a[1]).slice(0, 6);
    kContainer.innerHTML = kRows.length ? kRows.map(([name, count]) => `
      <div class="practice-bar-row">
        <span>${escapeHtml(name)}</span>
        <div class="practice-bar-track"><i style="width:${Math.round((count / maxK) * 100)}%"></i></div>
        <em>${count}</em>
      </div>
    `).join("") : '<p class="practice-recent-empty">暂无知识点</p>';
  }
  const dContainer = $("practiceDifficultyBars");
  if (dContainer) {
    const dRows = Object.entries(difficultyCounts);
    dContainer.innerHTML = dRows.length ? dRows.map(([name, count]) => `
      <div class="practice-bar-row">
        <span>${escapeHtml(name)}</span>
        <div class="practice-bar-track"><i style="width:${Math.round((count / maxD) * 100)}%"></i></div>
        <em>${count}</em>
      </div>
    `).join("") : '<p class="practice-recent-empty">暂无难度</p>';
  }
}

function uniquePracticeLabels(values) {
  const seen = new Set();
  return (values || []).reduce((labels, value) => {
    const label = String(value || "").trim().replace(/\s+/g, " ");
    const key = label.toLocaleLowerCase();
    if (!label || seen.has(key)) return labels;
    seen.add(key);
    labels.push(label);
    return labels;
  }, []);
}

function renderPracticeFilterGroup(containerId, values, dataAttribute, visibleLimit = 6) {
  const container = $(containerId);
  if (!container) return;
  const labels = uniquePracticeLabels(values);
  const chips = labels.map((label, index) => `
    <button type="button" class="practice-filter-chip${index >= visibleLimit ? " practice-filter-chip--overflow" : ""}" ${dataAttribute}="${escapeHtml(label)}">${escapeHtml(label)}</button>
  `).join("");
  const remaining = Math.max(0, labels.length - visibleLimit);
  container.classList.toggle("has-overflow", remaining > 0);
  container.classList.remove("is-expanded");
  container.innerHTML = chips + (remaining ? `
    <button type="button" class="practice-filter-more" data-practice-filter-more="${escapeHtml(containerId)}" aria-expanded="false">展开其余 ${remaining} 项</button>
  ` : "");
}

function renderPracticeFilters(data) {
  const exercises = data.exercises || [];
  const types = [];
  const difficulties = [];
  const tags = [];
  exercises.forEach((item) => {
    if (item.question_type) types.push(item.question_type);
    if (item.difficulty) difficulties.push(item.difficulty);
    tags.push(...(item.knowledge_points || []));
  });
  renderPracticeFilterGroup("practiceTypeFilterChips", types, "data-filter-type", 5);
  renderPracticeFilterGroup("practiceDifficultyFilterChips", difficulties, "data-filter-difficulty", 5);
  renderPracticeFilterGroup("practiceTagFilterChips", tags, "data-filter-tag", 6);
  PRACTICE_FILTERS.type.clear();
  PRACTICE_FILTERS.difficulty.clear();
  PRACTICE_FILTERS.tag.clear();
  document.querySelectorAll(".practice-filter-chip").forEach((c) => c.classList.remove("active"));
  document.querySelectorAll("[data-practice-filter-more]").forEach((button) => {
    button.addEventListener("click", () => {
      const container = $(button.dataset.practiceFilterMore);
      if (!container) return;
      const expanded = container.classList.toggle("is-expanded");
      button.setAttribute("aria-expanded", String(expanded));
      const hiddenCount = container.querySelectorAll(".practice-filter-chip--overflow").length;
      button.textContent = expanded ? "收起知识点" : `展开其余 ${hiddenCount} 项`;
    });
  });
}

function applyExerciseFilters() {
  const items = Array.from(document.querySelectorAll(".practice-exercise"));
  let visible = 0;
  items.forEach((el) => {
    const type = el.dataset.exerciseType || "";
    const difficulty = el.dataset.exerciseDifficulty || "";
    const tags = (el.dataset.exerciseTags || "").split("|").filter(Boolean);
    const matchType = !PRACTICE_FILTERS.type.size || PRACTICE_FILTERS.type.has(type);
    const matchDifficulty = !PRACTICE_FILTERS.difficulty.size || PRACTICE_FILTERS.difficulty.has(difficulty);
    const matchTag = !PRACTICE_FILTERS.tag.size || tags.some((t) => PRACTICE_FILTERS.tag.has(t));
    const show = matchType && matchDifficulty && matchTag;
    el.style.display = show ? "" : "none";
    if (show) visible++;
  });
  setText("practiceExerciseCount", `可见 ${visible} / 总数 ${items.length}`);
}

function togglePracticeFilter(group, value, button) {
  const set = PRACTICE_FILTERS[group];
  if (!set) return;
  if (set.has(value)) {
    set.delete(value);
    button?.classList.remove("active");
  } else {
    set.add(value);
    button?.classList.add("active");
  }
  applyExerciseFilters();
}

function renderPracticeRecentHistory(records) {
  const list = $("practiceRecentList");
  if (!list) return;
  const recent = (records || []).slice(0, 3);
  if (!recent.length) {
    list.innerHTML = '<p class="practice-recent-empty">暂无历史记录</p>';
    return;
  }
  list.innerHTML = recent.map((row) => `
    <div class="practice-recent-item" data-practice-recent="${escapeHtml(row.history_id)}">
      <div>
        <strong>${escapeHtml(row.title || "研究生专项练习")}</strong>
        <small>共 ${row.total_count ?? row.question_count ?? 0} 题：已生成 ${row.generated_count ?? row.question_count ?? 0} 题 · ${escapeHtml(String(row.updated_at || "").replace("T", " ").slice(0, 16))}</small>
      </div>
      <button type="button" class="practice-recent-reuse"><i class="fas fa-rotate-left"></i>复用</button>
    </div>
  `).join("");
  list.querySelectorAll(".practice-recent-item").forEach((item) => {
    const id = item.dataset.practiceRecent;
    item.addEventListener("click", async () => {
      try {
        const record = await api(`/api/practice/history/${encodeURIComponent(id)}`);
        if (record.request) {
          $("practiceQuestionText").value = record.request.question_text || "";
          if ($("practiceCount")) $("practiceCount").value = String(record.request.count || $("practiceCount").value || 5);
          if (record.request.difficulty && $("practiceDifficulty")) $("practiceDifficulty").value = record.request.difficulty;
          $("practiceFocus").value = record.request.focus || "";
          document.querySelectorAll('input[name="practiceQuestionType"]').forEach((input) => {
            input.checked = (record.request.question_types || []).includes(input.value);
          });
          updatePracticeConfigSummary();
        }
        latestPracticeRequest = record.request || null;
        restorePracticePreferenceOrders(latestPracticeRequest);
        syncPracticeSourceContentPreference(latestPracticeRequest?.include_source_content_in_generation !== false);
        practiceSourceFiles = normalizeSourceFileList(latestPracticeRequest?.source_files).filter((file) => file?.data_url);
        currentPracticeSourceMode = latestPracticeRequest?.source_mode === "knowledge" ? "knowledge" : "exam";
        renderPracticeFilePreview();
        const savedData = record.data || {};
        latestPracticePlan = latestPracticeRequest?.blueprint_review_enabled !== false && savedData.blueprint ? {
          source_mode: currentPracticeSourceMode,
          source_analysis: savedData.source_analysis || latestPracticeRequest?.source_analysis || {},
          source_scope: savedData.source_scope || latestPracticeRequest?.source_scope_checkpoint || {},
          selected_source_questions: savedData.selected_source_questions || latestPracticeRequest?.selected_source_questions || [],
          blueprint: savedData.blueprint,
          scope_cover: savedData.scope_cover || {},
          mode_contract: savedData.mode_contract || {},
          blueprint_audit: savedData.blueprint_audit || {},
        } : null;
        renderPracticeResults(record.data);
        setText("practiceSourceStatus", latestPracticeRequest?.source_recovery?.status === "blocked"
          ? "历史已载入，但原始材料不可恢复；请重新上传后再运行"
          : "已复用历史记录");
      } catch (error) {
        $("practiceError").textContent = String(error).replace(/^Error:\s*/, "");
        $("practiceError").classList.remove("hidden");
      }
    });
  });
}

function togglePracticeSidebar(forceCollapsed) {
  const sidebar = $("workbench-sidebar");
  const grid = sidebar?.closest(".workbench-grid");
  if (!sidebar || !grid) return;
  const collapsed = forceCollapsed !== undefined
    ? !!forceCollapsed
    : !sidebar.classList.contains("sidebar-collapsed");
  sidebar.classList.toggle("sidebar-collapsed", collapsed);
  grid.classList.toggle("sidebar-collapsed", collapsed);
  $("practiceSidebarCollapse")?.setAttribute("aria-expanded", String(!collapsed));
  $("practiceSidebarExpand")?.setAttribute("aria-expanded", String(!collapsed));
  try { localStorage.setItem("practiceSidebarCollapsed", collapsed ? "1" : "0"); } catch (e) {}
}

function restorePracticeSidebarState() {
  let collapsed = false;
  try {
    collapsed = localStorage.getItem("practiceSidebarCollapsed") === "1";
  } catch (e) {}
  togglePracticeSidebar(collapsed);
}

function openPracticeRailTarget(target) {
  togglePracticeSidebar(false);
  requestAnimationFrame(() => {
    if (target === "input") $("practiceQuestionText")?.focus();
    if (target === "presets") $("section-presets")?.scrollIntoView({ behavior: "smooth", block: "center" });
    if (target === "config") {
      togglePracticeConfig(true);
      $("practiceConfigCard")?.scrollIntoView({ behavior: "smooth", block: "center" });
    }
    if (target === "history") {
      const details = $("practiceShowAllHistory")?.closest("details");
      if (details) details.open = true;
      $("practiceShowAllHistory")?.focus();
    }
  });
}

function renderPracticeResults(data) {
  // Historical results may predate structured-table enforcement. Recover any
  // Markdown pipe table before rendering, exporting, or copying them.
  if (Array.isArray(data?.exercises)) data.exercises = data.exercises.map(normalizePracticeMarkdownTables);
  syncPracticeBlueprintPath(latestPracticeRequest?.blueprint_review_enabled !== false && data?.blueprint_review_enabled !== false);
  const incomingHistoryId = String(data?.history_id || "");
  const historyChanged = incomingHistoryId !== currentPracticeHistoryId;
  if (historyChanged) {
    currentPracticeHistoryId = incomingHistoryId;
    currentPracticeRevisionCount = 0;
  }
  // Selection belongs to one result identity. Several entry paths assign the
  // loaded record to latestPracticeSet before rendering it, so object identity
  // alone cannot prevent selections leaking into the next task.
  if (historyChanged || latestPracticeSet !== data) selectedPracticeExerciseIndexes.clear();
  latestPracticeSet = data;
  clearPreparedPracticeWords();
  $("practiceEmpty")?.classList.add("hidden");
  $("practiceLoading")?.classList.add("hidden");
  $("practicePlanReview")?.classList.add("hidden");
  $("practiceResults")?.classList.remove("hidden");
  // Closing a saved scope drawer can offer its resume card again.  A completed
  // result must never show that earlier-step card, even when a stale scope is
  // still held in memory from the same page session.
  closePracticeScopeDrawer();
  $("practiceScopeResume")?.classList.add("hidden");
  setPracticeStage("results");
  markAllPracticeStagesDone();
  setPracticeStageDescription("练习结果已保留，可继续下载、编辑或回到蓝图调整。");
  const strategyLabels = {
    single: "单题专项练习已生成",
    knowledge_targeted: "知识点模拟题已生成",
    knowledge_overall: "知识点综合练习已生成",
    knowledge_item_wise: "逐知识单元练习已生成",
    targeted_set: "整套专项补强已生成",
    parallel_exam: "平行试卷已生成",
    per_question: "逐题变式已生成"
  };
  setText("practiceResultMode", strategyLabels[data.generation_strategy] || "专项练习已生成");
  setText("practiceTrainingGoal", data.blueprint?.training_goal || "专项练习");
  const analysis = data.source_analysis || {};
  setText("practiceAnalysisTitle", [analysis.subject, analysis.question_type, analysis.difficulty].filter(Boolean).join(" · "));
  setText("practiceAnalysisMeta", [analysis.question_type, analysis.difficulty].filter(Boolean).join(" · "));
  const analysisTags = uniquePracticeLabels([...(analysis.knowledge_points || []), ...(analysis.skills || [])]);
  const visibleAnalysisTags = analysisTags.slice(0, 8);
  $("practiceKnowledgeTags").innerHTML = visibleAnalysisTags.map((tag) => `<span>${escapeHtml(tag)}</span>`).join("")
    + (analysisTags.length > visibleAnalysisTags.length ? `<span class="practice-tag-count">其余 ${analysisTags.length - visibleAnalysisTags.length} 项见下方筛选</span>` : "");
  $("practiceStrategy").innerHTML = (analysis.solution_strategy || []).map((item) => `<li>${escapeHtml(item)}</li>`).join("");
  const completion = practiceCompletionContract(data);
  const primaryIssue = completion.primary;
  const issueCodes = new Set(completion.issues.map((item) => item.code));
  const configurationBlocked = issueCodes.has("configuration_blocked");
  const generationIncomplete = issueCodes.has("generation_incomplete");
  const successfulCount = completion.generated_count;
  const resultCount = completion.total_count;
  const unfinishedCount = completion.unfinished_count;
  const isPassed = primaryIssue.code === "completed" && successfulCount > 0;
  $("practiceQuality").className = `practice-quality ${isPassed ? "passed" : "warning"}`;
  const secondaryIssues = completion.issues.filter((item) => item.code !== primaryIssue.code);
  const secondarySummary = secondaryIssues.length
    ? `另有：${secondaryIssues.map((item) => item.label).join("；")}。`
    : "";
  const primaryReasons = (primaryIssue.reasons || []).slice(0, 4).join("；");
  $("practiceQuality").innerHTML = primaryIssue.code === "completed"
    ? `<i class="fas fa-circle-check"></i><span><strong>已完成</strong>共 ${resultCount} 题：已生成 ${successfulCount} 题，可直接查看、编辑或导出。</span>`
    : primaryIssue.code === "configuration_blocked"
      ? `<i class="fas fa-key"></i><span><strong>需要检查 API 配置</strong>共 ${resultCount} 题：已生成 ${successfulCount} 题，${unfinishedCount} 题待配置。${escapeHtml(secondarySummary)}</span><button type="button" class="secondary-btn" data-practice-config><i class="fas fa-key"></i>检查 API 配置</button>${data.history_id && generationIncomplete ? '<button type="button" class="secondary-btn" data-practice-continue><i class="fas fa-play"></i>继续未完成项</button>' : ""}`
    : primaryIssue.code === "generation_incomplete"
      ? `<i class="fas fa-triangle-exclamation"></i><span><strong>存在未完成题目</strong>共 ${resultCount} 题：已生成 ${successfulCount} 题，${unfinishedCount} 题未完成。${escapeHtml(secondarySummary)}</span>${data.history_id ? '<button type="button" class="secondary-btn" data-practice-continue><i class="fas fa-play"></i>继续未完成项</button>' : ""}`
    : primaryIssue.code === "review_required"
      ? `<i class="fas fa-triangle-exclamation"></i><span><strong>题目已生成 · 待复核</strong>${escapeHtml(primaryReasons || "结果已保留，请复核后使用。")} 结果可继续查看和编辑。</span>`
    : `<i class="fas fa-circle-info"></i><span><strong>已完成 · 有提示</strong>${escapeHtml(primaryReasons || "结果含非阻断提示，请查看后使用。")} 题目已完整保留。</span>`;
  const badge = $("practiceQualityBadge");
  if (badge) {
    badge.className = `practice-quality-badge ${isPassed ? "passed" : "warning"}`;
    badge.innerHTML = `<i class="${escapeHtml(primaryIssue.icon)}"></i>${escapeHtml(primaryIssue.label)}`;
  }
  const statusCountText = generationIncomplete || configurationBlocked
    ? `共 ${resultCount} 题：已生成 ${successfulCount} 题，${unfinishedCount} 题${configurationBlocked ? "待配置" : "未完成"}`
    : `共 ${resultCount} 题：已生成 ${successfulCount} 题`;
  setPracticeStatusBanner(`${primaryIssue.label} · ${statusCountText}`, isPassed ? "done" : (successfulCount > 0 ? "warning" : "error"));
  renderPracticeBlueprintSummary(data);
  renderPracticeFilters(data);
  const practiceSourceLookup = new Map((data.selected_source_questions || []).map((item) => [String(item.source_question_id), item]));
  const practiceBlueprintItemNumbers = new Map((data.blueprint?.exercise_plan || []).map((item, index) => [String(item.plan_item_id || ""), index + 1]));
  const generationErrorDetailCodes = new Set([
    "generation_quality_gate_failed",
    "generation_response_invalid",
    "provider_generation_missing",
    "blueprint_audit_failed"
  ]);
  $("practiceExerciseList").innerHTML = (data.exercises || []).map((item, idx) => {
    const sourceRefs = uniquePracticeLabels([
      ...(Array.isArray(item.source_refs) ? item.source_refs : []),
      item.source_question_id
    ].filter(Boolean));
    const sourceQuestions = sourceRefs
      .map((sourceRef) => practiceSourceLookup.get(String(sourceRef)))
      .filter(Boolean);
    const sourceCaption = sourceQuestions.length
      ? `${data.source_mode === "knowledge" ? "来源知识单元" : "来源原题"}：${sourceQuestions.map((sourceQuestion) =>
          `${sourceQuestion.number || ""} · ${sourceQuestion.title || sourceQuestion.source_question_id || "未命名来源"}`
        ).join("；")}`
      : "";
    const tagsArr = uniquePracticeLabels(item.knowledge_points || []);
    const visibleTags = tagsArr.slice(0, 4);
    const generationFailed = item.generation_status === "failed";
    const auditNeedsReview = item.audit_status === "audit_failed" || item.generation_error?.code === "blueprint_audit_failed";
    const configurationNeedsReview = item.generation_error?.requires_configuration === true;
    const generationError = item.generation_error?.message || "上游模型未返回本题。";
    const generationErrorDetail = generationErrorDetailCodes.has(String(item.generation_error?.code || ""))
      ? (item.generation_error?.detail || "")
      : "";
    const parentPlanItemId = String(item.parent_plan_item_id || "");
    const variantIndex = Number(item.variant_index || 0);
    const variantCount = Number(item.variant_count || 0);
    const blueprintItemNumber = practiceBlueprintItemNumbers.get(parentPlanItemId);
    const variantLabel = parentPlanItemId && variantIndex
      ? `蓝图 ${blueprintItemNumber || "-"} · 变式 ${variantIndex}/${variantCount || "-"}${item.variant_role ? ` · ${item.variant_role}` : ""}`
      : "";
    const wordExportFilename = `专项练习-第${item.number || ""}题.docx`;
    const wordExportKey = practiceWordExportKey({ ...data, exercises: [item] }, wordExportFilename);
    return `
    <article class="practice-exercise${generationFailed ? " practice-exercise--generation-failed" : ""}${variantIndex === 1 ? " practice-exercise--variant-start" : ""}" data-exercise-index="${idx}" data-exercise-type="${escapeHtml(item.question_type || "")}" data-exercise-difficulty="${escapeHtml(item.difficulty || "")}" data-exercise-tags="${escapeHtml(tagsArr.join("|"))}" data-variant-parent="${escapeHtml(parentPlanItemId)}">
      ${sourceCaption ? `<div class="practice-source-link"><i class="fas fa-link"></i>${escapeHtml(sourceCaption)}</div>` : ""}
      ${variantLabel ? `<div class="practice-variant-link"><i class="fas fa-layer-group"></i>${escapeHtml(variantLabel)}</div>` : ""}
      <header class="practice-exercise__header">
        <div class="practice-exercise__identity"><b>第 ${escapeHtml(item.number || String(idx + 1))} 题</b><span>${auditNeedsReview ? "蓝图待复核" : configurationNeedsReview ? "待配置" : generationFailed ? "生成失败" : escapeHtml(item.question_type || "综合题")}</span></div>
        <div class="practice-exercise__meta">
          <small>${escapeHtml(item.target_skill || "核心能力训练")}</small>
          <em class="${item.difficulty === "挑战" ? "hard" : item.difficulty === "基础" ? "easy" : ""}">${escapeHtml(item.difficulty)}</em>
          <div class="practice-exercise__actions">
            <label title="选择本题" class="practice-exercise__select"><input type="checkbox" data-practice-select="${idx}" ${selectedPracticeExerciseIndexes.has(idx) ? "checked" : ""}><span class="visually-hidden">选择本题</span></label>
            <button type="button" data-practice-feedback="${idx}" title="反馈此题" aria-label="反馈此题"><i class="fas fa-bug"></i></button>
            <button type="button" data-practice-edit="${idx}" title="编辑本题" aria-label="编辑本题"><i class="fas fa-pen"></i></button>
            <button type="button" ${configurationNeedsReview ? "data-practice-config" : `data-practice-regenerate="${idx}"`} title="${configurationNeedsReview ? "检查 API 配置" : auditNeedsReview ? "复审并生成本题" : "重新生成本题"}" aria-label="${configurationNeedsReview ? "检查 API 配置" : auditNeedsReview ? "复审并生成本题" : "重新生成本题"}"><i class="fas ${configurationNeedsReview ? "fa-key" : "fa-rotate"}"></i></button>
            <div class="practice-action-menu practice-action-menu--question" data-practice-action-menu>
              <button type="button" class="practice-question-more-trigger" title="更多操作" aria-label="更多操作" aria-haspopup="menu" aria-expanded="false" data-practice-menu-trigger><i class="fas fa-ellipsis"></i></button>
              <div class="practice-action-menu__panel hidden" role="menu" data-practice-menu-panel>
                <button type="button" class="practice-action-menu__item" role="menuitem" data-practice-copy="${idx}" ${generationFailed ? "disabled" : ""}><i class="far fa-copy"></i><span>复制本题</span></button>
                <button type="button" class="practice-action-menu__item" role="menuitem" data-practice-download="${idx}" data-practice-word-export-key="${escapeHtml(wordExportKey)}" data-practice-word-export-label="下载本题 Word" data-practice-word-export-available="${generationFailed ? "false" : "true"}" ${generationFailed ? "disabled" : ""}><i class="fas fa-file-word"></i><span>下载本题 Word</span></button>
              </div>
            </div>
          </div>
        </div>
      </header>
      ${generationFailed ? `
        <div class="practice-generation-error" role="alert">
          <i class="fas fa-triangle-exclamation"></i>
          <div><strong>第 ${escapeHtml(item.number || String(idx + 1))} 题${auditNeedsReview ? "蓝图待复核" : configurationNeedsReview ? "待配置" : "生成失败"}</strong><p>${escapeHtml(generationError)}</p>${generationErrorDetail ? `<small class="practice-generation-error__detail">${escapeHtml(generationErrorDetail)}</small>` : ""}<small>${auditNeedsReview ? "本题尚未调用生成模型；点击右上角“复审并生成本题”只处理这一项，其他题目不受影响。" : configurationNeedsReview ? "本题尚未完成；请先检查 API 配置，再使用“继续未完成项”，已生成题目不会重复调用或被覆盖。" : "已保留蓝图位置；可点击右上角“重新生成本题”补齐，其他题目不受影响。"}</small></div>
        </div>
      ` : `
        <div class="practice-stem">${practiceMarkdown(item.stem)}</div>
        ${practiceExtrasHtml(item, "stem")}
        ${item.options?.length ? `<div class="practice-options">${item.options.map((option) => `<p><b>${escapeHtml(option.label)}</b>${practiceMarkdown(option.text)}</p>`).join("")}</div>` : ""}
        ${tagsArr.length ? `<div class="practice-exercise-tags">${visibleTags.map((t) => `<span>${escapeHtml(t)}</span>`).join("")}${tagsArr.length > visibleTags.length ? `<span class="practice-tag-count">+${tagsArr.length - visibleTags.length}</span>` : ""}</div>` : ""}
      `}
    </article>
  `;
  }).join("");
  updatePracticeAsideSummary(data);
  setPracticeExportButtonsEnabled(isPassed, data);
  if ($("practiceUndoBtn")) $("practiceUndoBtn").disabled = currentPracticeRevisionCount < 1 || !data.history_id;
  requestAnimationFrame(() => {
    drawPracticeCharts(data);
    typesetPracticeMath();
    applyExerciseFilters();
  });
  document.querySelectorAll("[data-practice-edit]").forEach((button) => {
    button.addEventListener("click", () => openPracticeEditor(Number(button.dataset.practiceEdit)));
  });
  document.querySelectorAll("[data-practice-feedback]").forEach((button) => {
    button.addEventListener("click", () => submitSupportFeedback("question", {
      history_id: currentPracticeHistoryId,
      exercise_index: Number(button.dataset.practiceFeedback),
      task_id: activePracticeJobId || latestPracticeSet?.generation?.job_id || ""
    }, button));
  });
  document.querySelectorAll("[data-practice-regenerate]").forEach((button) => {
    button.addEventListener("click", () => regeneratePracticeQuestion(Number(button.dataset.practiceRegenerate), button));
  });
  document.querySelectorAll("[data-practice-config]").forEach((button) => {
    button.addEventListener("click", () => goToPage("keys"));
  });
  document.querySelectorAll("[data-practice-continue]").forEach((button) => {
    button.addEventListener("click", () => continuePracticeHistory(currentPracticeHistoryId, button));
  });
  document.querySelectorAll("[data-practice-download]").forEach((button) => {
    button.addEventListener("click", () => {
      const item = latestPracticeSet?.exercises?.[Number(button.dataset.practiceDownload)];
      if (item && latestPracticeSet) prepareOrDownloadPracticeWord({ ...latestPracticeSet, exercises: [item] }, button, `专项练习-第${item.number || ""}题.docx`).catch((error) => {
        platformAlert(String(error).replace(/^Error:\s*/, ""), { title: "题目 Word 生成失败", tone: "danger" });
      });
    });
  });
  document.querySelectorAll("[data-practice-copy]").forEach((button) => {
    button.addEventListener("click", () => copyPracticeQuestion(Number(button.dataset.practiceCopy), button));
  });
  document.querySelectorAll("[data-practice-select]").forEach((input) => {
    input.addEventListener("change", () => {
      const index = Number(input.dataset.practiceSelect);
      if (input.checked) selectedPracticeExerciseIndexes.add(index); else selectedPracticeExerciseIndexes.delete(index);
      updatePracticeSelectionActions();
    });
  });
  updatePracticeSelectionActions();
  syncPracticeWordExportUi();
  document.querySelectorAll(".practice-filter-chip").forEach((chip) => {
    chip.addEventListener("click", () => {
      const group = chip.dataset.filterType ? "type" : chip.dataset.filterDifficulty ? "difficulty" : chip.dataset.filterTag ? "tag" : null;
      if (!group) return;
      const value = chip.dataset.filterType || chip.dataset.filterDifficulty || chip.dataset.filterTag;
      togglePracticeFilter(group, value, chip);
    });
  });
}

function closePracticeActionMenus(exceptMenu = null) {
  document.querySelectorAll("[data-practice-action-menu]").forEach((menu) => {
    if (menu === exceptMenu) return;
    menu.querySelector("[data-practice-menu-panel]")?.classList.add("hidden");
    menu.querySelector("[data-practice-menu-trigger]")?.setAttribute("aria-expanded", "false");
  });
}

function initPracticeActionMenus() {
  document.addEventListener("click", (event) => {
    const trigger = event.target.closest("[data-practice-menu-trigger]");
    if (trigger) {
      const menu = trigger.closest("[data-practice-action-menu]");
      const panel = menu?.querySelector("[data-practice-menu-panel]");
      if (!menu || !panel) return;
      const opening = panel.classList.contains("hidden");
      closePracticeActionMenus(opening ? menu : null);
      panel.classList.toggle("hidden", !opening);
      trigger.setAttribute("aria-expanded", String(opening));
      if (opening) panel.querySelector('[role="menuitem"]:not(:disabled)')?.focus();
      return;
    }
    if (event.target.closest('[role="menuitem"]')) {
      closePracticeActionMenus();
      return;
    }
    if (!event.target.closest("[data-practice-action-menu]")) closePracticeActionMenus();
  });
  document.addEventListener("keydown", (event) => {
    if (event.key !== "Escape") return;
    const openPanel = document.querySelector("[data-practice-menu-panel]:not(.hidden)");
    if (!openPanel) return;
    const trigger = openPanel.closest("[data-practice-action-menu]")?.querySelector("[data-practice-menu-trigger]");
    closePracticeActionMenus();
    trigger?.focus();
  });
}

function practiceRequestPayload() {
  const knowledgeMode = currentPracticeSourceMode === "knowledge";
  return {
    source_mode: knowledgeMode ? "knowledge" : "exam",
    knowledge_title: knowledgeMode ? (latestPracticeRequest?.knowledge_title || "") : undefined,
    question_text: $("practiceQuestionText").value.trim(),
    source_files: practiceSourceFiles.map((file) => ({ ...file })),
    blueprint_review_enabled: blueprintReviewEnabled(),
    semantic_review_enabled: true,
    include_source_content_in_generation: includeSourceContentInGeneration(),
    count: Number($("practiceCount")?.value || 5),
    difficulty: $("practiceDifficulty")?.value || "基础到进阶",
    question_types: Array.from(document.querySelectorAll('input[name="practiceQuestionType"]:checked')).map((input) => input.value),
    focus: $("practiceFocus").value.trim(),
    provider: knowledgeMode ? knowledgeProviderName("text") : practiceProviderName("text"),
    model: knowledgeMode ? selectedKnowledgeModel("text") : selectedPracticeModel("text"),
    vision_provider: knowledgeMode ? knowledgeProviderName("vision") : practiceProviderName("vision"),
    vision_model: knowledgeMode ? selectedKnowledgeModel("vision") : selectedPracticeModel("vision"),
    thinking: selectedThinkingMode()
  };
}

function knowledgeRequestPayload() {
  const title = $("knowledgeTitleInput")?.value.trim() || "";
  const material = $("knowledgeTextInput")?.value.trim() || "";
  const questionText = [
    title ? `# 知识点名称\n\n${title}` : "",
    material ? `# 知识材料\n\n${material}` : ""
  ].filter(Boolean).join("\n\n");
  const count = Number($("knowledgeCount")?.value || 5);
  return {
    source_mode: "knowledge",
    knowledge_title: title,
    question_text: questionText,
    source_files: knowledgeSourceFiles.map((file) => ({ ...file })),
    blueprint_review_enabled: $("knowledgeBlueprintReviewEnabled")?.checked !== false,
    semantic_review_enabled: true,
    include_source_content_in_generation: $("knowledgeIncludeSourceContent")?.checked !== false,
    count,
    difficulty_mode: count === 1 ? "single" : "distribution",
    difficulty: $("knowledgeDifficulty")?.value || "基础到进阶",
    question_types: Array.from(document.querySelectorAll('input[name="knowledgeQuestionType"]:checked')).map((input) => input.value),
    focus: $("knowledgeFocusInput")?.value.trim() || "",
    provider: knowledgeProviderName("text"),
    model: selectedKnowledgeModel("text"),
    vision_provider: knowledgeProviderName("vision"),
    vision_model: selectedKnowledgeModel("vision"),
    thinking: selectedThinkingMode()
  };
}

const KNOWLEDGE_SINGLE_DIFFICULTIES = [
  ["基础", "基础"],
  ["进阶", "进阶"],
  ["挑战", "挑战"]
];
const KNOWLEDGE_SET_DIFFICULTIES = [
  ["基础为主", "基础为主"],
  ["基础到进阶", "基础 → 进阶"],
  ["进阶为主", "进阶为主"],
  ["进阶到挑战", "进阶 → 挑战"]
];

function updateKnowledgeDifficultyControl() {
  const count = Number($("knowledgeCount")?.value || 5);
  const select = $("knowledgeDifficulty");
  if (!select) return;
  const singleMode = count === 1;
  const current = select.value;
  const options = singleMode ? KNOWLEDGE_SINGLE_DIFFICULTIES : KNOWLEDGE_SET_DIFFICULTIES;
  const mapped = singleMode
    ? ({ "基础为主": "基础", "基础到进阶": "基础", "进阶为主": "进阶", "进阶到挑战": "挑战" }[current] || current)
    : ({ "基础": "基础为主", "进阶": "进阶为主", "挑战": "进阶到挑战" }[current] || current);
  select.innerHTML = options.map(([value, label]) => `<option value="${value}"${value === mapped ? " selected" : ""}>${label}</option>`).join("");
  if (!options.some(([value]) => value === select.value)) select.value = singleMode ? "进阶" : "基础到进阶";
  const label = singleMode ? "本题难度" : "整套题难度分布";
  select.setAttribute("aria-label", label);
  select.closest(".platform-select")?.querySelector(".platform-select-trigger")?.setAttribute("aria-label", label);
  setText("knowledgeDifficultyLabel", label);
  setText("knowledgeDifficultyBadge", singleMode ? "单题" : `${count} 题`);
  setText("knowledgeDifficultyHint", singleMode ? "直接指定这一道题的认知与解题复杂度。" : "决定多道题在基础、进阶和挑战层级之间如何分配。");
  setText("knowledgeDifficultyGuideTitle", singleMode ? "正在设置这一道题的难度" : `正在配置 ${count} 道题的整体梯度`);
  setText(
    "knowledgeDifficultyGuideText",
    singleMode
      ? "基础偏直接应用，进阶增加条件转换，挑战强调多步骤综合推理；均不会故意超出知识材料。"
      : "系统会先分配知识点覆盖，再按所选分布安排每道题的难度；具体分配可在蓝图中继续修改。"
  );
  $("knowledgeDifficultyGuide")?.classList.toggle("single-mode", singleMode);
  const icon = $("knowledgeDifficultyGuideIcon");
  if (icon) icon.className = `fas ${singleMode ? "fa-bullseye" : "fa-layer-group"}`;
  setText("knowledgeTypeLabel", singleMode ? "本题题型" : "题型组合");
  setText("knowledgeTypeHint", singleMode ? "不选则根据知识材料自动判断；选择时只能选一种" : "不选则根据知识材料自动搭配；可选择多种题型");
  if (singleMode) {
    const checked = Array.from(document.querySelectorAll('input[name="knowledgeQuestionType"]:checked'));
    checked.slice(1).forEach((input) => { input.checked = false; });
  }
}

function enforceKnowledgeQuestionTypeMode(event) {
  if (Number($("knowledgeCount")?.value || 5) !== 1 || !event.target.checked) return;
  document.querySelectorAll('input[name="knowledgeQuestionType"]').forEach((input) => {
    if (input !== event.target) input.checked = false;
  });
}

async function planKnowledgePractice(event) {
  event.preventDefault();
  const sessionVersion = practiceSessionVersion;
  await practiceWorkspaceRestorePromises.knowledge;
  await uploadFileReadChains.knowledge;
  if (sessionVersion !== practiceSessionVersion || currentPage !== "knowledge") return;
  const request = knowledgeRequestPayload();
  clearUploadFeedback("knowledge");
  const errorBox = $("knowledgeError");
  if (!request.question_text && !request.source_files.length) {
    errorBox.textContent = "请填写知识点名称、粘贴知识材料，或上传至少一个文件。";
    errorBox.classList.remove("hidden");
    $("practiceQuestionText")?.focus();
    return;
  }
  errorBox.classList.add("hidden");
  const button = $("knowledgePlanBtn");
  const original = button.innerHTML;
  button.disabled = true;
  button.innerHTML = '<i class="fas fa-circle-notch fa-spin"></i>正在解析知识范围';
  latestPracticeRequest = request;
  setPracticeWorkspaceMode("knowledge");
  goToPage("practice");
  showPracticeLoading("正在解析知识材料与知识单元");
  setPracticeStage("analyze");
  setPracticeStageDescription("正在识别核心概念、前置知识、能力层次和适合的出题方向。");
  setPracticeStatusBanner("分析知识材料", "loading");
  try {
    const job = await submitPracticeJob("analyze", request);
    if (sessionVersion !== practiceSessionVersion) return;
    rememberPracticeJob("");
    renderPracticeSourceSelection(job.result);
    setPracticeStageDescription("知识材料已拆分为可选知识单元；请确认整体综合或逐项出题方式。");
  } catch (error) {
    goToPage("knowledge");
    errorBox.textContent = String(error).replace(/^Error:\s*/, "");
    errorBox.classList.remove("hidden");
  } finally {
    button.disabled = false;
    button.innerHTML = original;
  }
}

function showPracticeLoading(title) {
  closePracticeScopeDrawer();
  // This must happen independently of the stage selector: an active task
  // opened from Task Manager restores its material payload before this view.
  setPracticeSourceEntryVisibility(false);
  $("practiceEmpty")?.classList.add("hidden");
  $("practiceResults")?.classList.add("hidden");
  $("practiceSourceSelection")?.classList.add("hidden");
  $("practicePlanReview")?.classList.add("hidden");
  $("practiceScopeResume")?.classList.add("hidden");
  $("practiceLoading")?.classList.remove("hidden");
  setText("practiceLoadingTitle", title);
  setText("practiceLoadingDetail", "任务已在后台开始");
  setText("practiceLoadingElapsed", "刚刚开始");
}

function showPracticeOperationLoading(title, operation) {
  showPracticeLoading(title);
  setPracticeStage(practiceStageForJobOperation(operation));
}

function renderPracticeSourceSelection(data) {
  const knowledgeMode = currentPracticeSourceMode === "knowledge" || latestPracticeRequest?.source_mode === "knowledge";
  const reviewEnabled = latestPracticeRequest?.blueprint_review_enabled !== false;
  syncPracticeSourceContentPreference(latestPracticeRequest?.include_source_content_in_generation !== false);
  if (knowledgeMode && $("knowledgeBlueprintReviewEnabled")) $("knowledgeBlueprintReviewEnabled").checked = reviewEnabled;
  if (!knowledgeMode && $("practiceBlueprintReviewEnabled")) $("practiceBlueprintReviewEnabled").checked = reviewEnabled;
  latestPracticeSourceScope = data.source_scope || null;
  latestPracticeSourceAnalysis = data.source_analysis || latestPracticeSourceAnalysis || null;
  latestPracticePlan = null;
  syncPracticeBlueprintPath(reviewEnabled);
  if (data.generation?.model) {
    const modeLabel = data.generation.model_route === "primary_multimodal"
      ? "主模型原生图文解析"
      : data.generation.model_route === "vision_fallback"
        ? "图片回退解析"
        : data.generation.input_mode === "text" ? "文字解析" : (data.generation.input_mode === "mixed" ? "图文混合解析" : "视觉解析");
    setText("practiceCurrentModelBadge", `${displayProviderName(data.generation.provider || "")} / ${data.generation.model} · ${modeLabel}`);
  }
  $("practiceLoading")?.classList.add("hidden");
  $("practiceEmpty")?.classList.add("hidden");
  $("practiceResults")?.classList.add("hidden");
  $("practicePlanReview")?.classList.add("hidden");
  setPracticeStage("scope");
  setPracticeStageDescription(latestPracticeRequest?.blueprint_review_enabled === false
    ? "材料解析已完成；请确认范围、题量和各难度题数，确认后将直接生题。"
    : "材料解析已完成；请确认参与出题的内容、生成方式及蓝图配置。");
  setPracticeStatusBanner("等待确认考点与范围", "loading");
  const questions = data.source_scope?.questions || [];
  document.querySelectorAll(".practice-knowledge-strategy").forEach((item) => item.classList.toggle("hidden", !knowledgeMode));
  document.querySelectorAll('input[name="practiceSetStrategy"]:not(.practice-knowledge-strategy input)').forEach((input) => input.closest("label")?.classList.toggle("hidden", knowledgeMode));
  setText("practiceScopeEyebrow", knowledgeMode ? "知识单元 · 范围确认" : "考点 / 原题 · 范围确认");
  setText("practiceScopeIntro", reviewEnabled
    ? "先确认内容范围和出题方式，再设置题量、题型与难度；确认后进入可编辑蓝图。"
    : "先确认内容范围和出题方式，再设置题量、题型与难度；确认后直接生成题目。");
  setText("practiceScopeConfigHint", reviewEnabled ? "这些设置将约束蓝图，后续可在配额内逐题调整" : "这些设置将直接成为生题约束，不再进入蓝图阶段");
  setText("practiceScopeTypeHint", reviewEnabled ? "可多选；不选则由蓝图自动搭配" : "可多选；不选则由程序按范围自动搭配");
  setText("practiceSourceSetTitle", data.source_scope?.title || "确认参与出题的内容与生成方式");
  setText("practiceSourceCount", `${questions.length} ${knowledgeMode ? "个知识单元" : "道原题"}`);
  renderPracticeSourceDiagnostics(data.source_file_diagnostics || []);
  setText("practiceScopeItemsLabel", knowledgeMode ? "参与生成的知识单元" : "参与生成的原题");
  // 粒度选择：有层级时显示，否则隐藏并回退 atomic
  const scope = latestPracticeSourceScope || {};
  const granularityRow = $("practiceScopeGranularityRow");
  if (granularityRow) granularityRow.classList.toggle("hidden", !scope.has_hierarchy);
  const granularitySelect = $("practiceScopeGranularity");
  if (granularitySelect) granularitySelect.value = scope.granularity === "top_level" ? "top_level" : "atomic";
  renderPracticeScopeQuestionList(questions);
  const defaultStrategy = knowledgeMode ? "knowledge_overall" : "targeted_set";
  document.querySelectorAll('input[name="practiceSetStrategy"]').forEach((input) => {
    input.checked = input.value === defaultStrategy;
  });
  if ($("practiceTargetedCount")) $("practiceTargetedCount").value = String(latestPracticeRequest?.count || 5);
  setDefaultPracticeDifficultyCounts(
    Number(latestPracticeRequest?.count || 5),
    latestPracticeRequest?.difficulty || "基础到进阶",
    latestPracticeRequest?.difficulty_counts
  );
  if ($("practiceScopeFocus")) $("practiceScopeFocus").value = latestPracticeRequest?.focus || "";
  const existingTypes = new Set(latestPracticeRequest?.question_types || []);
  document.querySelectorAll('input[name="practiceScopeQuestionType"]').forEach((input) => { input.checked = existingTypes.has(input.value); });
  $("practiceStrategySettings")?.classList.remove("hidden");
  $("practiceSourceSelectionError")?.classList.add("hidden");
  updatePracticeScopePreview();
  openPracticeScopeDrawer();
  updatePracticeStrategySettings();
  setText("practiceSourceStatus", "范围待确认");
  schedulePracticeWorkspaceDraftSave(knowledgeMode ? "knowledge" : "exam");
}

function scopeGranularityUnits(units) {
  // 与后端 resolve_scope_granularity 对齐：
  // top_level：只保留顶层项；atomic：有子项的父项被其子项替代，保留叶节点。
  const scope = latestPracticeSourceScope || {};
  const granularity = (($("practiceScopeGranularity") || {}).value) || scope.granularity || "atomic";
  const hasHierarchy = !!scope.has_hierarchy;
  if (!hasHierarchy || granularity !== "top_level") {
    // atomic / 无层级
    if (hasHierarchy) {
      const parentIds = new Set(units.map((u) => u.parent_id).filter(Boolean));
      return units.filter((u) => !parentIds.has(u.source_question_id));
    }
    return units;
  }
  return units.filter((u) => !u.parent_id);
}

function renderPracticeScopeQuestionList(allUnits) {
  const units = scopeGranularityUnits(allUnits);
  const box = $("practiceSourceQuestionList");
  if (!box) return;
  box.innerHTML = units.map((item) => `
    <div class="practice-source-unit" data-source-question-id="${escapeHtml(item.source_question_id)}">
      <label class="practice-source-unit__toggle">
        <input type="checkbox" name="practiceSourceQuestion" value="${escapeHtml(item.source_question_id)}" checked>
        <span>
          <b>${escapeHtml(item.number || item.source_question_id)}</b>
          <div><strong>${escapeHtml(item.title || "未命名题目")}</strong><small>${escapeHtml([item.question_type, item.source_difficulty ? `来源难度：${item.source_difficulty}` : "来源难度待确认", ...(item.knowledge_points || []), item.source_ref?.page ? `页码 p${item.source_ref.page}` : ""].filter(Boolean).join(" · "))}</small><p>${escapeHtml(item.stem_excerpt || "")}</p></div>
        </span>
      </label>
      <div class="practice-source-unit__actions">
        <button type="button" class="text-button practice-source-unit__action" data-edit-source="${escapeHtml(item.source_question_id)}"><i class="fas fa-pen"></i>编辑</button>
        <button type="button" class="text-button practice-source-unit__action" data-split-source="${escapeHtml(item.source_question_id)}"><i class="fas fa-split"></i>拆分</button>
        <button type="button" class="text-button practice-source-unit__action" data-merge-source="${escapeHtml(item.source_question_id)}"><i class="fas fa-compress-alt"></i>合并</button>
      </div>
    </div>
  `).join("");
}


let practiceScopeMergeSelection = [];

function togglePracticeSourceMerge(unitId, checkbox) {
  if (checkbox.checked) {
    if (!practiceScopeMergeSelection.includes(unitId)) practiceScopeMergeSelection.push(unitId);
  } else {
    practiceScopeMergeSelection = practiceScopeMergeSelection.filter((id) => id !== unitId);
  }
}

async function addPracticeSourceUnit() {
  const result = await showPlatformDialog("prompt", {
    title: "手工新增来源单元",
    message: "输入新单元的标题与知识要点（用｜分隔标题与要点）：",
    placeholder: "例如：绝热过程｜热力学第一定律、绝热指数",
    confirmText: "新增",
  });
  if (!result) return;
  const [title, ...rest] = String(result).split("｜");
  const scope = latestPracticeSourceScope || {};
  const units = scope.questions || [];
  const baseId = "manual_" + Date.now().toString(36);
  const unit = {
    source_question_id: baseId,
    number: String(units.length + 1),
    title: (title || "新增单元").trim(),
    stem_excerpt: rest.length ? rest.join("｜").trim() : "手工新增单元",
    question_type: (latestPracticeRequest?.source_mode === "knowledge" ? "知识单元" : "综合题"),
    knowledge_points: rest.length ? rest.join("｜").split(/[、,，]/).map((s) => s.trim()).filter(Boolean) : [],
    source_difficulty: "基础",
    source_ref: { page: "", block: "", fragment: "手工新增" },
    manual: true,
  };
  scope.questions = [...units, unit];
  latestPracticeSourceScope = scope;
  renderPracticeScopeQuestionList(scope.questions);
  setText("practiceSourceCount", `${scope.questions.length} ${(latestPracticeRequest?.source_mode === "knowledge") ? "个知识单元" : "道原题"}`);
  schedulePracticeWorkspaceDraftSave(currentPracticeSourceMode);
}

async function editPracticeSourceUnit(unitId) {
  const scope = latestPracticeSourceScope || {};
  const unit = (scope.questions || []).find((u) => u.source_question_id === unitId);
  if (!unit) return;
  const value = await showPlatformDialog("prompt", {
    title: "编辑来源单元",
    message: `编辑标题与知识要点（格式：标题｜要点1、要点2）：\n当前：${unit.title || ""}`,
    placeholder: `${unit.title || ""}｜${(unit.knowledge_points || []).join("、")}`,
    confirmText: "保存",
  });
  if (value === null || value === undefined) return;
  const text = String(value).trim();
  const [title, ...rest] = text.split("｜");
  unit.title = (title || unit.title || "未命名").trim();
  const kps = rest.length ? rest.join("｜").split(/[、,，]/).map((s) => s.trim()).filter(Boolean) : (unit.knowledge_points || []);
  if (kps.length) unit.knowledge_points = kps;
  latestPracticeSourceScope = scope;
  renderPracticeScopeQuestionList(scope.questions);
  schedulePracticeWorkspaceDraftSave(currentPracticeSourceMode);
}

async function splitPracticeSourceUnit(unitId) {
  const scope = latestPracticeSourceScope || {};
  const units = scope.questions || [];
  const unit = units.find((u) => u.source_question_id === unitId);
  if (!unit || unit.parent_id) {
    setText("practiceSourceSelectionError", "只能拆分顶层来源单元（子项需先合并或拆分后再处理）。");
    $("practiceSourceSelectionError")?.classList.remove("hidden");
    return;
  }
  const value = await showPlatformDialog("prompt", {
    title: "拆分来源单元",
    message: `将「${unit.title || unitId}」拆分为多个子项。输入各子项标题，用“/或/”分隔：（保留来源原文与页码引用）`,
    placeholder: "子项A / 子项B / 子项C",
    confirmText: "拆分",
  });
  if (!value || typeof value !== "string") return;
  const parts = value.split(/\s*\/\s*|\s*或\s*/).map((s) => s.trim()).filter(Boolean);
  if (parts.length < 2) {
    setText("practiceSourceSelectionError", "拆分至少需要 2 个子项标题。");
    $("practiceSourceSelectionError")?.classList.remove("hidden");
    return;
  }
  // 原单元转成顶层父项，拆分出的子项作为其子项（继承 source_ref/fragment），并保留父项摘要
  const parentId = unitId;
  const parent = { ...unit, parent_id: "", title: unit.title || "未命名" };
  const children = parts.slice(0, 8).map((title, idx) => {
    const frag = unit.stem_excerpt ? `${unit.stem_excerpt.slice(0, 120)}` : `${title}`;
    return {
      ...unit,
      number: `${unit.number || ""}.${idx + 1}`,
      source_question_id: `${parentId}_s${idx + 1}`,
      title,
      stem_excerpt: frag,
      parent_id: parentId,
      knowledge_points: [title],
    };
  });
  scope.questions = [...units.filter((u) => u.source_question_id !== unitId), parent, ...children];
  scope.has_hierarchy = true;
  const granularityRow = $("practiceScopeGranularityRow");
  if (granularityRow) granularityRow.classList.remove("hidden");
  const granularitySelect = $("practiceScopeGranularity");
  if (granularitySelect && granularitySelect.value === "top_level") scope.granularity = "top_level";
  latestPracticeSourceScope = scope;
  renderPracticeScopeQuestionList(scope.questions);
  $("practiceSourceSelectionError")?.classList.add("hidden");
  schedulePracticeWorkspaceDraftSave(currentPracticeSourceMode);
}

function mergePracticeSourceUnits() {
  const scope = latestPracticeSourceScope || {};
  const units = scope.questions || [];
  const chosen = (practiceScopeMergeSelection || []).filter((id) => units.some((u) => u.source_question_id === id));
  if (chosen.length < 2) {
    setText("practiceSourceSelectionError", "请先在列表中选择要合并的多个来源单元（至少 2 个）。");
    $("practiceSourceSelectionError")?.classList.remove("hidden");
    return;
  }
  const picked = units.filter((u) => chosen.includes(u.source_question_id));
  // 合并生成的顶层父项：保留首个子项 id 作为父 id，其余（及自身）标记为子项
  const parentId = picked[0].source_question_id;
  const parent = {
    ...picked[0],
    title: picked.map((u) => u.title || "未命名").join("；"),
    stem_excerpt: picked.map((u) => u.stem_excerpt || "").join("\n"),
    knowledge_points: [...new Set(picked.flatMap((u) => u.knowledge_points || []))].slice(0, 60),
    source_difficulty: picked[0]?.source_difficulty || "基础",
    parent_id: "",
  };
  // 每个被合并单元成为该顶层父项的子项，继承来源引用；改用子项 id 避免与父项冲突
  const children = picked.map((u, idx) => {
    const childId = `${parentId}_${idx + 1}`;
    return {
      ...u,
      source_question_id: childId,
      number: `${u.number || ""}.${idx + 1}`,
      parent_id: parentId,
      stem_excerpt: u.stem_excerpt || "",
    };
  });
  const others = units.filter((u) => !chosen.includes(u.source_question_id));
  // 父项+子项都保留：顶层数量 = 父项 + 其它顶层；原子数量 = 子项 + 其它顶层
  scope.questions = [...others, parent, ...children];
  scope.has_hierarchy = true;
  scope.granularity = "top_level";
  const granularityRow = $("practiceScopeGranularityRow");
  if (granularityRow) granularityRow.classList.remove("hidden");
  const granularitySelect = $("practiceScopeGranularity");
  if (granularitySelect) granularitySelect.value = "top_level";
  practiceScopeMergeSelection = [];
  latestPracticeSourceScope = scope;
  renderPracticeScopeQuestionList(scope.questions);
  $("practiceSourceSelectionError")?.classList.add("hidden");
  setText("practiceSourceCount", `${scope.questions.length} ${(latestPracticeRequest?.source_mode === "knowledge") ? "个知识单元" : "道原题"}`);
  schedulePracticeWorkspaceDraftSave(currentPracticeSourceMode);
}

function renderPracticeSourceDiagnostics(diagnostics) {
  const box = $("practiceSourceDiagnostics");
  const list = $("practiceSourceDiagnosticsList");
  if (!box || !list) return;
  const rows = (Array.isArray(diagnostics) ? diagnostics : []).map((item) => {
    const warnings = Array.isArray(item.warnings) ? item.warnings : [];
    const name = escapeHtml(item.name || "未命名文件");
    const formulas = Number(item.omml_formula_count || 0);
    const tables = Number(item.table_count || 0);
    const included = Number(item.image_count_included || 0);
    const total = Number(item.embedded_image_count || 0);
    const totalPages = Number(item.page_count_total || 0);
    const usedPages = Array.isArray(item.page_numbers_used) ? item.page_numbers_used.length : 0;
    const omittedPages = Array.isArray(item.page_numbers_omitted) ? item.page_numbers_omitted.length : 0;
    const pageText = totalPages ? ` · PDF 页码 ${usedPages}/${totalPages} 已使用${omittedPages ? `，${omittedPages} 页未使用` : ""}` : "";
    return `<div class="practice-source-diagnostic"><b>${name}</b><span>公式 ${formulas} · 表格 ${tables} · 图片 ${included}/${total} 已传递${pageText}</span>${warnings.map((warning) => `<p>${escapeHtml(warning)}</p>`).join("")}</div>`;
  });
  list.innerHTML = rows.join("");
  box.classList.toggle("hidden", rows.length === 0);
}

function practiceHighExpansionRisk(strategy, selectedCount, totalCount) {
  return ["targeted_set", "knowledge_overall"].includes(strategy)
    && selectedCount > 0
    && totalCount > selectedCount * 2
    && includeSourceContentInGeneration();
}

function updatePracticeStrategySettings() {
  const strategy = document.querySelector('input[name="practiceSetStrategy"]:checked')?.value || "";
  const settings = $("practiceStrategySettings");
  settings?.classList.toggle("hidden", !strategy);
  // 题量控件：
  //  - 综合覆盖（targeted_set / knowledge_overall）：总题量，默认5
  //  - 逐项扩展（knowledge_item_wise）：每知识点生题数，默认1
  //  - 按题变式（per_question）：每道原题变式数
  $("practiceTargetedCountRow")?.classList.toggle("hidden", !["targeted_set", "knowledge_overall"].includes(strategy));
  $("practiceKnowledgePerCountRow")?.classList.toggle("hidden", strategy !== "knowledge_item_wise");
  $("practiceVariantsRow")?.classList.toggle("hidden", strategy !== "per_question");
  const selectedCount = document.querySelectorAll('input[name="practiceSourceQuestion"]:checked').length;
  const knowledgeMode = currentPracticeSourceMode === "knowledge";
  const variants = Number($("practiceVariantsPerQuestion")?.value || 1);
  const perCount = Number($("practiceKnowledgePerCount")?.value || 1);
  if (strategy === "knowledge_overall") {
    setText("practiceStrategyHint", `综合 ${selectedCount} 个知识单元，生成总共 ${Number($("practiceTargetedCount")?.value || 5)} 道题。`);
  } else if (strategy === "knowledge_item_wise") {
    setText("practiceStrategyHint", `每个知识单元生成 ${perCount} 道题，共 ${Math.min(30, selectedCount * perCount)} 道起。`);
  } else if (strategy === "targeted_set") {
    setText("practiceStrategyHint", `综合选中的 ${selectedCount} 项内容与考点权重，生成总共 ${Number($("practiceTargetedCount")?.value || 10)} 道题。`);
  } else if (strategy === "parallel_exam") {
    setText("practiceStrategyHint", `将生成 ${selectedCount} 道题，每个选中项目对应一道。`);
  } else if (strategy === "per_question") {
    const total = Math.min(30, selectedCount * variants);
    setText("practiceStrategyHint", `预计生成 ${total} 道变式；为保证单次生成稳定，总量最多 30 道。`);
  } else {
    setText("practiceStrategyHint", "");
  }
  const countRisk = $("practiceTargetedCountRisk");
  const totalCount = Math.max(1, Number($("practiceTargetedCount")?.value || 5));
  const isComprehensive = ["targeted_set", "knowledge_overall"].includes(strategy);
  if (countRisk && isComprehensive && selectedCount > 1 && totalCount < selectedCount) {
    const recommendedCount = Math.min(20, selectedCount);
    countRisk.textContent = selectedCount > 20
      ? `当前已选 ${selectedCount} 项，但单套最多 20 道题；建议缩小范围或拆分为多套练习，避免覆盖不完整。`
      : `当前已选 ${selectedCount} 项，但只生成 ${totalCount} 道综合题，可能无法覆盖全部范围。建议至少输入 ${recommendedCount} 道题；继续生成时系统将优先覆盖核心知识点。`;
    countRisk.classList.remove("hidden");
  } else if (
    countRisk
    && practiceHighExpansionRisk(strategy, selectedCount, totalCount)
    && !blueprintReviewEnabled()
  ) {
    countRisk.textContent = `当前从 ${selectedCount} 项来源扩展到 ${totalCount} 道题，且开启了来源材料参考。为避免同源题复制题面或解法，请开启蓝图审查，或将题量降到 ${selectedCount * 2} 道以内。`;
    countRisk.classList.remove("hidden");
  } else {
    countRisk?.classList.add("hidden");
    if (countRisk) countRisk.textContent = "";
  }
  updatePracticeScopePreview();
}

async function planSelectedSourceQuestions() {
  const sessionVersion = practiceSessionVersion;
  const selectedIds = Array.from(document.querySelectorAll('input[name="practiceSourceQuestion"]:checked')).map((input) => input.value);
  const errorBox = $("practiceSourceSelectionError");
  const strategy = document.querySelector('input[name="practiceSetStrategy"]:checked')?.value || "";
  if (!strategy) {
    errorBox.textContent = "请先选择整套专项补强、平行试卷或逐题变式。";
    errorBox.classList.remove("hidden");
    return;
  }
  if (!selectedIds.length) {
    errorBox.textContent = "请至少选择一道原题。";
    errorBox.classList.remove("hidden");
    return;
  }
  const questions = latestPracticeSourceScope?.questions || [];
  const selected = questions.filter((item) => selectedIds.includes(item.source_question_id));
  const totalCount = Number($("practiceTargetedCount")?.value || 5);
  const perPointCount = Number($("practiceKnowledgePerCount")?.value || 1);
  if (
    practiceHighExpansionRisk(strategy, selected.length, totalCount)
    && !blueprintReviewEnabled()
  ) {
    errorBox.textContent = `当前从 ${selected.length} 项来源扩展到 ${totalCount} 道题并参考来源材料，需开启蓝图审查，或将题量降到 ${selected.length * 2} 道以内。`;
    errorBox.classList.remove("hidden");
    return;
  }
  latestPracticeRequest = {
    ...latestPracticeRequest,
    source_scope: latestPracticeSourceScope,
    source_analysis: latestPracticeSourceAnalysis,
    selected_source_questions: selected,
    generation_strategy: strategy,
    granularity: (($("practiceScopeGranularity") || {}).value) || latestPracticeSourceScope?.granularity || "atomic",
    // knowledge_item_wise 使用每知识点生题数；其余综合策略使用总题量
    strategy_count: strategy === "knowledge_item_wise" ? perPointCount : totalCount,
    variants_per_question: strategy === "knowledge_item_wise" ? perPointCount : Number($("practiceVariantsPerQuestion")?.value || 1),
    count: strategy === "knowledge_item_wise" ? perPointCount : totalCount,
    difficulty: "精确题数",
    difficulty_counts: practiceDifficultyCounts(),
    difficulty_selection_order: practiceDifficultySelectionOrder,
    blueprint_review_enabled: blueprintReviewEnabled(),
    include_source_content_in_generation: includeSourceContentInGeneration(),
    question_types: Array.from(document.querySelectorAll('input[name="practiceScopeQuestionType"]:checked')).map((input) => input.value),
    focus: $("practiceScopeFocus")?.value.trim() || ""
  };
  const button = $("practiceSourceConfirmBtn");
  const original = button.innerHTML;
  button.disabled = true;
  button.innerHTML = latestPracticeRequest.blueprint_review_enabled
    ? '<i class="fas fa-circle-notch fa-spin"></i>正在设计蓝图'
    : '<i class="fas fa-circle-notch fa-spin"></i>正在生成题目';
  closePracticeScopeDrawer();
  const operation = latestPracticeRequest.blueprint_review_enabled ? "plan" : "generate_from_contract";
  showPracticeOperationLoading(
    latestPracticeRequest.blueprint_review_enabled ? "正在围绕确认范围设计训练蓝图" : "正在按确认范围直接生成题目",
    operation
  );
  try {
    if (!latestPracticeRequest.blueprint_review_enabled) {
      latestPracticeRequest = {
        ...latestPracticeRequest,
        generation_run_id: globalThis.crypto?.randomUUID ? crypto.randomUUID() : `run_${Date.now()}_${Math.random().toString(16).slice(2)}`
      };
      setPracticeStage("generate");
    }
    const job = await submitPracticeJob(operation, latestPracticeRequest);
    if (sessionVersion !== practiceSessionVersion) return;
    rememberPracticeJob("");
    if (latestPracticeRequest.blueprint_review_enabled) {
      const plan = job.result;
      if (plan.requires_source_selection) throw new Error("题目范围尚未确认，请重新选择。");
      renderPracticePlan(plan);
    } else {
      renderPracticeResults(job.result);
      await clearPersistentPracticeWorkspace(currentPracticeSourceMode);
      markAllPracticeStagesDone();
      setPracticeStageDescription("已按确认范围直接生成题目，并保存为独立版本。");
      await loadPracticeHistory();
    }
  } catch (error) {
    renderPracticeSourceSelection({ source_scope: latestPracticeSourceScope, source_analysis: latestPracticeSourceAnalysis });
    errorBox.textContent = String(error).replace(/^Error:\s*/, "");
    errorBox.classList.remove("hidden");
  } finally {
    button.disabled = false;
    button.innerHTML = original;
  }
}

function practiceBlueprintMultiQuestionConfig(plan = latestPracticePlan) {
  const blueprint = plan?.blueprint || {};
  const stored = blueprint.multi_question && typeof blueprint.multi_question === "object" ? blueprint.multi_question : {};
  const enabled = stored.enabled === true;
  const variantsPerItem = enabled ? Math.max(2, Math.min(3, Number(stored.variants_per_item || 3))) : 1;
  const mode = ["progressive", "same_difficulty"].includes(stored.mode) ? stored.mode : "progressive";
  const baseItemCount = Array.isArray(blueprint.exercise_plan) ? blueprint.exercise_plan.length : 0;
  const counts = latestPracticeRequest?.difficulty_counts || {};
  const positiveDifficultyLevels = ["基础", "进阶", "挑战"].filter((level) => Number(counts[level] || 0) > 0);
  const difficultyPrecedence = (positiveDifficultyLevels.length === 1
      || mode === "same_difficulty"
      || practiceDifficultySelectionOrder >= practiceVariantSelectionOrder
      ? "confirmed_counts"
      : "progressive");
  return {
    enabled,
    variants_per_item: variantsPerItem,
    mode,
    difficulty_precedence: difficultyPrecedence,
    base_item_count: baseItemCount,
    total_count: baseItemCount * variantsPerItem
  };
}

function syncPracticeBlueprintMultiQuestionControls() {
  if (!latestPracticePlan?.blueprint) return;
  const config = practiceBlueprintMultiQuestionConfig(latestPracticePlan);
  latestPracticePlan.blueprint.multi_question = config;
  const enabledInput = $("practiceBlueprintMultiQuestionEnabled");
  const countSelect = $("practiceBlueprintVariantsPerItem");
  const modeSelect = $("practiceBlueprintVariantMode");
  if (enabledInput) enabledInput.checked = config.enabled;
  if (countSelect) countSelect.value = String(config.enabled ? config.variants_per_item : Math.max(2, Number(countSelect.value || 3)));
  if (modeSelect) modeSelect.value = config.mode;
  $("practiceBlueprintMultiQuestionOptions")?.classList.toggle("hidden", !config.enabled);
  const summary = $("practiceBlueprintMultiQuestionSummary");
  if (summary) {
    const difficultyText = config.mode === "progressive"
      ? (config.difficulty_precedence === "confirmed_counts"
        ? "按最后确认的难度题数生成，递进只改变条件和认知要求"
        : (config.variants_per_item === 3 ? "每组按基础、进阶、挑战递进" : "每组按基础、进阶递进"))
      : "每组保持各蓝图项当前难度";
    const largeSetAdvice = config.total_count > 30
      ? "题量较大，生成时间、模型费用和配图处理量会明显增加；如不赶时间可直接继续。"
      : "";
    summary.textContent = `最终将生成 ${config.total_count} 题（${config.base_item_count} 个蓝图项 × 每项 ${config.variants_per_item} 题）；${difficultyText}。已采用的单题草案仅作为该组第 1 题。${largeSetAdvice}`;
    summary.classList.toggle("is-warning", Boolean(largeSetAdvice));
  }
  setText("practicePlanCountBadge", config.enabled ? `${config.base_item_count} 项蓝图 → ${config.total_count} 题` : `${config.base_item_count} 项待审查`);
  const candidatePending = !$("practicePlanCandidateActions")?.classList.contains("hidden");
  const auditBlocked = latestPracticePlan.blueprint_audit?.status === "blocked";
  if ($("practicePlanConfirmBtn")) $("practicePlanConfirmBtn").disabled = candidatePending || auditBlocked;
}

function bindPracticeBlueprintMultiQuestionControls() {
  const enabledInput = $("practiceBlueprintMultiQuestionEnabled");
  const countSelect = $("practiceBlueprintVariantsPerItem");
  const modeSelect = $("practiceBlueprintVariantMode");
  if (enabledInput) enabledInput.onchange = () => {
    nextPracticePreferenceOrder("variant");
    const previous = practiceBlueprintMultiQuestionConfig(latestPracticePlan);
    latestPracticePlan.blueprint.multi_question = {
      ...previous,
      enabled: enabledInput.checked,
      variants_per_item: enabledInput.checked ? Math.max(2, Number(countSelect?.value || 3)) : 1,
    };
    syncPracticeBlueprintMultiQuestionControls();
  };
  if (countSelect) countSelect.onchange = () => {
    nextPracticePreferenceOrder("variant");
    latestPracticePlan.blueprint.multi_question = {
      ...practiceBlueprintMultiQuestionConfig(latestPracticePlan),
      enabled: true,
      variants_per_item: Math.max(2, Math.min(3, Number(countSelect.value || 3))),
    };
    syncPracticeBlueprintMultiQuestionControls();
  };
  if (modeSelect) modeSelect.onchange = () => {
    nextPracticePreferenceOrder("variant");
    latestPracticePlan.blueprint.multi_question = {
      ...practiceBlueprintMultiQuestionConfig(latestPracticePlan),
      mode: modeSelect.value,
    };
    syncPracticeBlueprintMultiQuestionControls();
  };
}

function renderPracticePlan(plan) {
  closePracticeScopeDrawer();
  latestPracticePlan = plan;
  $("practiceScopeResume")?.classList.add("hidden");
  $("practiceLoading")?.classList.add("hidden");
  $("practiceEmpty")?.classList.add("hidden");
  $("practiceResults")?.classList.add("hidden");
  $("practiceSourceSelection")?.classList.add("hidden");
  $("practicePlanReview")?.classList.remove("hidden");
  setText("practicePlanGoal", plan.blueprint?.training_goal || "训练蓝图");
  const planItems = plan.blueprint?.exercise_plan || [];
  // Visible fallbacks must be written into the plan object before submit.
  planItems.forEach((item, index) => {
    if (!item.target_skill) item.target_skill = "核心能力";
    if (!item.variation_type) item.variation_type = item.structural_change || "结构变化";
    if (!item.design_intent) item.design_intent = `围绕${item.target_skill}完成${item.variation_type}训练。`;
    if (!item.plan_item_id) item.plan_item_id = `plan_item_${String(index + 1).padStart(2, "0")}`;
    ensurePlanDifficultyDesign(item);
  });
  $("practicePlanError")?.classList.add("hidden");
  setText("practicePlanReviewMode", currentPracticeSourceMode === "knowledge" ? "知识点出题 · 蓝图审查" : "专项练习 · 蓝图审查");
  setText("practicePlanCountBadge", `${planItems.length} 项待审查`);
  if ($("practicePlanGoalInput")) $("practicePlanGoalInput").value = plan.blueprint?.training_goal || "";
  const analysis = plan.source_analysis || {};
  const modeContract = plan.mode_contract || {};
  const blueprintAudit = plan.blueprint_audit || {};
  const auditReviewPlanItemIds = new Set(blueprintAudit.review_item_ids || []);
  const blueprintAuditMessages = (blueprintAudit.errors || []).length
    ? blueprintAudit.errors
    : (blueprintAudit.warnings || []);
  const refinement = plan.blueprint_refinement || {};
  const refinementFailures = refinement.failures || [];
  const fallbackPlanItemIds = new Set(refinementFailures.flatMap((row) => row.plan_item_ids || []));
  const comprehensiveMode = modeContract.mode === "comprehensive" || ["targeted_set", "knowledge_overall"].includes(plan.blueprint?.generation_strategy);
  $("practicePlanAnalysis").innerHTML = `
    <span><i class="fas fa-microscope"></i>${currentPracticeSourceMode === "knowledge" ? "知识材料分析" : "原题分析"}</span>
    <div><strong>${escapeHtml([analysis.subject, analysis.question_type, analysis.difficulty].filter(Boolean).join(" · ") || "待确认分析结果")}</strong>
    <p>${escapeHtml([...(analysis.knowledge_points || []), ...(analysis.skills || [])].join("、") || "请逐题核对下方蓝图内容。")}</p>
    <div class="practice-mode-contract ${comprehensiveMode ? "comprehensive" : "single"}"><i class="fas ${comprehensiveMode ? "fa-diagram-project" : "fa-code-branch"}"></i><strong>${comprehensiveMode ? "综合覆盖矩阵" : "单项变式链"}</strong><span>${comprehensiveMode ? `跨来源 ${modeContract.metrics?.multi_source_count || 0}/${modeContract.metrics?.exercise_count || planItems.length} 题` : "每项严格单一来源"}</span></div>
    <div class="practice-blueprint-audit ${blueprintAudit.status === "blocked" ? "is-blocked" : blueprintAudit.status === "warning" ? "is-warning" : "is-passed"}"><i class="fas ${blueprintAudit.status === "blocked" ? "fa-triangle-exclamation" : blueprintAudit.status === "warning" ? "fa-circle-exclamation" : "fa-circle-check"}"></i><strong>${blueprintAudit.status === "blocked" ? "确认门禁未通过" : blueprintAudit.status === "warning" ? "确认前建议复核" : "确认门禁可通过"}</strong><span>${escapeHtml(blueprintAuditMessages.slice(0, 2).join("；") || "来源、计划项和模式约束已完成程序检查")}</span></div>${refinement.enabled ? `<div class="practice-blueprint-audit ${refinementFailures.length ? "is-warning" : "is-passed"}"><i class="fas ${refinementFailures.length ? "fa-arrows-rotate" : "fa-diagram-project"}"></i><strong>分组设计调度</strong><span>${escapeHtml(`${refinement.unit_count || 0} 个生成单元 · ${refinement.call_count || 0} 次调用${refinementFailures.length ? ` · ${refinementFailures.length} 组已自动重试后保留全局方案，可逐项重新设计或整份重新生成` : " · 全部设计批次已完成"}`)}</span></div>` : ""}</div>
  `;
  renderPracticePlanCoverage(plan.scope_cover || (plan.selected_source_questions ? {
    per_unit: {},
    counts: { selected_units: (plan.selected_source_questions || []).length, covered_units: 0, uncovered_units: 0, planned_exercises: planItems.length },
    complete: false,
  } : null));
  const planTypes = ["单选题", "多选题", "判断题", "填空题", "简答题", "计算题", "作图题", "综合题"];
  const planDifficulties = ["基础", "进阶", "挑战"];
  const sourceCatalog = plan.selected_source_questions || plan.source_scope?.questions || [];
  planItems.forEach((item) => syncPlanItemRequiredKnowledgePoints(item, sourceCatalog, plan.blueprint?.generation_strategy));
  $("practicePlanList").innerHTML = planItems.map((item, index) => `
    <details class="practice-plan-edit-row" data-plan-index="${index}"${index < 2 ? " open" : ""}>
      <summary>
        <b>${item.number}</b>
        <div><strong data-plan-summary="target_skill">${escapeHtml(item.target_skill || "核心能力")}</strong><span><em data-plan-summary="question_type">${escapeHtml(item.question_type || "自动题型")}</em><em data-plan-summary="difficulty">${escapeHtml(item.difficulty || "进阶")}</em><em>${escapeHtml(item.coverage_role || (comprehensiveMode ? "连接" : "变式"))} · ${(item.source_refs || [item.source_question_id]).filter(Boolean).length} 来源</em>${fallbackPlanItemIds.has(item.plan_item_id) ? '<em class="practice-plan-fallback">细化失败，已保留全局方案</em>' : ""}${auditReviewPlanItemIds.has(item.plan_item_id) ? '<em class="practice-plan-fallback">本项待复核，整批可继续</em>' : ""}</span></div>
        <i class="fas fa-chevron-down"></i>
      </summary>
      <div class="practice-plan-edit-body"><div class="practice-plan-edit-grid">
        <label>题型<select data-plan-field="question_type">${planTypes.map((type) => `<option${type === item.question_type ? " selected" : ""}>${type}</option>`).join("")}</select></label>
        <label>难度<select data-plan-field="difficulty">${planDifficulties.map((level) => `<option${level === item.difficulty ? " selected" : ""}>${level}</option>`).join("")}</select></label>
        <label class="practice-plan-wide">目标能力<input data-plan-field="target_skill" value="${escapeHtml(item.target_skill || "核心能力")}"></label>
        <label>变化方式<input data-plan-field="variation_type" value="${escapeHtml(item.variation_type || "")}"></label>
        <label class="practice-plan-wide">设计意图<textarea rows="2" data-plan-field="design_intent">${escapeHtml(item.design_intent || "")}</textarea></label>
        <label class="practice-plan-wide">难度实现<input data-plan-difficulty-levers value="${escapeHtml((item.difficulty_levers || []).join("、") || "待补充难度调节方式")}" readonly aria-label="第 ${index + 1} 项难度调节方式"></label>
        <label class="practice-plan-wide">难度依据<textarea data-plan-difficulty-rationale rows="2" readonly aria-label="第 ${index + 1} 项难度依据">${escapeHtml(item.difficulty_rationale || "待补充难度依据")}</textarea></label>
        <label class="practice-plan-wide">必考知识点<input value="${escapeHtml((item.required_knowledge_points || []).join("、") || "待从绑定来源补齐")}" readonly aria-label="第 ${index + 1} 项必考知识点"></label>
        <fieldset class="practice-plan-wide practice-plan-source-editor"><legend>来源绑定</legend>${sourceCatalog.map((source) => {
          const sourceId = String(source.source_question_id || "");
          const refs = item.source_refs || [item.source_question_id];
          return `<label><input type="${comprehensiveMode ? "checkbox" : "radio"}" name="plan-source-${escapeHtml(item.plan_item_id)}" data-plan-source="${escapeHtml(sourceId)}"${refs.includes(sourceId) ? " checked" : ""}><span>${escapeHtml(source.number || source.title || sourceId)}</span></label>`;
        }).join("") || "<span>当前流程无需来源绑定</span>"}</fieldset>
      </div>
      <div class="practice-plan-draft">
        <div class="practice-plan-draft__actions">
          <button type="button" data-plan-move="up" data-plan-item-index="${index}" class="practice-plan-draft__btn" title="上移"${index === 0 ? " disabled" : ""}><i class="fas fa-arrow-up"></i></button>
          <button type="button" data-plan-move="down" data-plan-item-index="${index}" class="practice-plan-draft__btn" title="下移"${index === planItems.length - 1 ? " disabled" : ""}><i class="fas fa-arrow-down"></i></button>
          <button type="button" data-plan-delete="${index}" class="practice-plan-draft__btn" title="删除计划项"${planItems.length === 1 ? " disabled" : ""}><i class="fas fa-trash"></i></button>
          <button type="button" data-plan-item-regenerate="${index}" class="practice-plan-draft__btn" title="只重新设计这一项蓝图，其他项不变"><i class="fas fa-arrows-rotate"></i>重新设计本项蓝图</button>
          <button type="button" data-plan-draft="${index}" class="practice-plan-draft__btn" title="按本计划项生成本题草案，可先预览再修订"><i class="fas fa-file-circle-plus"></i>生成本题草案</button>
        </div>
        ${practicePlanRevisionReceipts[item.plan_item_id] ? `<div class="practice-revision-receipt"><i class="fas fa-circle-check"></i><span><strong>约束门禁已通过</strong>${escapeHtml((practicePlanRevisionReceipts[item.plan_item_id].applied_changes || []).map((entry) => entry.evidence).filter(Boolean).join("；") || "字段变化已由程序核验，请人工确认语义说明。")}</span></div>` : ""}
        <div class="practice-plan-draft__body hidden" data-plan-draft-view="${index}"></div>
      </div></div>
    </details>
  `).join("");
  $("practicePlanGoalInput")?.addEventListener("input", (event) => {
    latestPracticePlan.blueprint.training_goal = event.target.value;
    setText("practicePlanGoal", event.target.value || "训练蓝图");
  });
  $("practicePlanList")?.querySelectorAll("[data-plan-field]").forEach((control) => {
    const syncPlanField = () => {
      const row = control.closest("[data-plan-index]");
      const target = latestPracticePlan?.blueprint?.exercise_plan?.[Number(row?.dataset.planIndex)];
      if (target) {
        const field = control.dataset.planField;
        const difficultyChanged = field === "difficulty" && target.difficulty !== control.value;
        if (difficultyChanged) {
          const design = defaultPlanDifficultyDesign(control.value, target.question_type, target.structural_change, target.target_skill);
          target.difficulty_levers = design.levers;
          target.difficulty_rationale = design.rationale;
          target.difficulty_design_level = control.value;
          const leversInput = row.querySelector("[data-plan-difficulty-levers]");
          const rationaleInput = row.querySelector("[data-plan-difficulty-rationale]");
          if (leversInput) leversInput.value = design.levers.join("、");
          if (rationaleInput) rationaleInput.value = design.rationale;
        }
        target[field] = control.value;
        row.querySelector(`[data-plan-summary="${field}"]`)?.replaceChildren(control.value);
        // 计划项被编辑后，其已采用的草案不再对应当前配置：取消采用，避免把旧草案注入修改后的计划项
        const planItemId = String(target.plan_item_id || "");
        if (planItemId && practicePlanDrafts[planItemId] && practicePlanDrafts[planItemId].adopted) {
          practicePlanDrafts[planItemId].adopted = false;
          const idx = Number(row?.dataset.planIndex);
          const view = document.querySelector(`[data-plan-draft-view="${idx}"]`);
          if (view && practicePlanDrafts[planItemId]) view.innerHTML = renderPlanItemDraft(planItemId);
          renderPlanDraftAdoptSummary();
        }
      }
    };
    control.addEventListener("input", syncPlanField);
    control.addEventListener("change", syncPlanField);
  });
  $("practicePlanList")?.querySelectorAll("[data-plan-source]").forEach((control) => {
    control.addEventListener("change", () => {
      const row = control.closest("[data-plan-index]");
      const target = latestPracticePlan?.blueprint?.exercise_plan?.[Number(row?.dataset.planIndex)];
      if (!target) return;
      const refs = [...row.querySelectorAll("[data-plan-source]:checked")].map((input) => input.dataset.planSource).filter(Boolean);
      target.source_refs = refs;
      target.source_question_id = refs[0] || "";
      syncPlanItemRequiredKnowledgePoints(
        target,
        latestPracticePlan.selected_source_questions || latestPracticePlan.source_scope?.questions || [],
        latestPracticePlan.blueprint?.generation_strategy,
      );
      auditAndRenderPracticePlan();
    });
  });
  // 蓝图重渲染后按 plan_item_id 恢复已有草案与采用状态，并按蓝图身份清理旧草案
  syncPracticePlanDraftsToBlueprint(plan, planItems);
  syncPracticeBlueprintMultiQuestionControls();
  bindPracticeBlueprintMultiQuestionControls();
  setPracticeStage("plan");
  setPracticeStageDescription("训练蓝图已生成；确认后将按此蓝图生成具体练习题。");
  setPracticeStatusBanner("等待确认训练蓝图", "loading");
  setText("practiceSourceStatus", "蓝图待确认");
  schedulePracticeWorkspaceDraftSave(currentPracticeSourceMode);
}

function renumberPracticePlanItems(items) {
  items.forEach((item, index) => { item.number = index + 1; });
}

async function auditAndRenderPracticePlan() {
  if (!latestPracticePlan) return;
  try {
    const audit = await api("/api/practice/plan-audit", { method: "POST", body: JSON.stringify({ plan: latestPracticePlan }) });
    Object.assign(latestPracticePlan, audit);
  } catch (error) {
    latestPracticePlan.blueprint_audit = { status: "blocked", errors: [String(error).replace(/^Error:\s*/, "")] };
  }
  renderPracticePlan(latestPracticePlan);
}

function addPracticePlanItem() {
  const items = latestPracticePlan?.blueprint?.exercise_plan;
  if (!Array.isArray(items) || items.length >= 30) return;
  const sourceCatalog = latestPracticePlan.selected_source_questions || latestPracticePlan.source_scope?.questions || [];
  const sourceIds = sourceCatalog.map((item) => String(item.source_question_id || "")).filter(Boolean);
  const comprehensive = ["targeted_set", "knowledge_overall"].includes(latestPracticePlan.blueprint?.generation_strategy);
  const sourceRefs = comprehensive ? sourceIds.slice(0, Math.min(2, sourceIds.length)) : sourceIds.slice(0, 1);
  items.push({
    number: items.length + 1,
    plan_item_id: `plan_item_user_${Date.now().toString(36)}`,
    source_question_id: sourceRefs[0] || "",
    source_refs: sourceRefs,
    coverage_role: comprehensive ? "综合" : "变式",
    question_type: "简答题",
    difficulty: "进阶",
    target_skill: "核心能力",
    variation_type: "结构变化",
    design_intent: "新增训练项，请在确认前补充具体设计意图。",
    required_knowledge_points: requiredKnowledgePointsForPlanItem({ source_refs: sourceRefs }, sourceCatalog, latestPracticePlan.blueprint?.generation_strategy),
  });
  auditAndRenderPracticePlan();
}

async function regeneratePracticePlan() {
  if (!latestPracticeRequest) return;
  const instruction = await platformPrompt({
    eyebrow: "重新生成蓝图",
    title: "说明不合理之处",
    message: "系统会保留原始题目/知识材料，重新分析并生成一份全新的蓝图。",
    inputLabel: "调整要求（选填）",
    placeholder: "例如：题型分布不合理、难度不合适、覆盖点重复、设计意图不满意",
    confirmText: "重新生成"
  });
  if (instruction === null) return;
  const sessionVersion = practiceSessionVersion;
  const button = $("practicePlanRegenerateBtn");
  const original = button.innerHTML;
  button.disabled = true;
  button.innerHTML = '<i class="fas fa-circle-notch fa-spin"></i>正在重新生成';
  showPracticeOperationLoading("正在重新分析并生成全新蓝图", "plan");
  try {
    const request = {
      ...latestPracticeRequest,
      focus: [latestPracticeRequest.focus, instruction ? `蓝图调整要求：${instruction}` : "请重新设计一份不同且合理的蓝图。"].filter(Boolean).join("\n")
    };
    const job = await submitPracticeJob("plan", request);
    if (sessionVersion !== practiceSessionVersion) return;
    rememberPracticeJob("");
    if (job.result?.requires_source_selection) renderPracticeSourceSelection(job.result);
    else {
      if (job.result?.blueprint) {
        job.result.blueprint.multi_question = practiceBlueprintMultiQuestionConfig(latestPracticePlan);
      }
      pendingPracticePlanCandidate = { original: latestPracticePlan, candidate: job.result };
      renderPracticePlan(job.result);
      $("practicePlanCandidateActions")?.classList.remove("hidden");
      $("practicePlanConfirmBtn").disabled = true;
    }
  } catch (error) {
    if (sessionVersion !== practiceSessionVersion) return;
    renderPracticePlan(latestPracticePlan);
    $("practiceError").textContent = String(error).replace(/^Error:\s*/, "");
    $("practiceError").classList.remove("hidden");
  } finally {
    button.disabled = false;
    button.innerHTML = original;
  }
}

function adoptPracticePlanCandidate() {
  if (!pendingPracticePlanCandidate) return;
  pendingPracticePlanCandidate = null;
  for (const key of Object.keys(practicePlanDrafts)) delete practicePlanDrafts[key];
  for (const key of Object.keys(practicePlanRevisionReceipts)) delete practicePlanRevisionReceipts[key];
  $("practicePlanCandidateActions")?.classList.add("hidden");
  $("practicePlanConfirmBtn").disabled = false;
}

function keepOriginalPracticePlan() {
  const original = pendingPracticePlanCandidate?.original;
  pendingPracticePlanCandidate = null;
  $("practicePlanCandidateActions")?.classList.add("hidden");
  if (original) renderPracticePlan(original);
  $("practicePlanConfirmBtn").disabled = false;
}

async function planPractice(event) {
  event.preventDefault();
  const sessionVersion = practiceSessionVersion;
  const sourceMode = currentPracticeSourceMode;
  await practiceWorkspaceRestorePromises[sourceMode];
  await uploadFileReadChains.practice;
  if (sessionVersion !== practiceSessionVersion || sourceMode !== currentPracticeSourceMode || currentPage !== "practice") return;
  const request = practiceRequestPayload();
  clearUploadFeedback("practice");
  const errorBox = $("practiceError");
  if (!request.question_text && !request.source_files.length) {
    errorBox.textContent = currentPracticeSourceMode === "knowledge" ? "请粘贴知识材料或上传知识点文件。" : "请粘贴题目文字或上传题目文件。";
    errorBox.classList.remove("hidden");
    return;
  }
  errorBox.classList.add("hidden");
  showPracticeOperationLoading(currentPracticeSourceMode === "knowledge" ? "正在解析知识材料与知识单元" : "正在解析原题、考点与范围", "analyze");
  setPracticeStageDescription(currentPracticeSourceMode === "knowledge" ? "正在识别核心概念、能力层次与知识单元。" : "正在识别原题结构、考点与可参与出题的范围。");
  setPracticeStatusBanner(currentPracticeSourceMode === "knowledge" ? "分析知识材料" : "解析中", "loading");
  const button = $("practiceGenerateBtn");
  button.disabled = true;
  button.innerHTML = '<i class="fas fa-circle-notch fa-spin"></i>正在解析范围';
  try {
    latestPracticeRequest = request;
    const job = await submitPracticeJob("analyze", request);
    if (sessionVersion !== practiceSessionVersion) return;
    rememberPracticeJob("");
    renderPracticeSourceSelection(job.result);
  } catch (error) {
    $("practiceLoading")?.classList.add("hidden");
    $("practiceEmpty")?.classList.remove("hidden");
    setPracticeStage("submit");
    errorBox.textContent = String(error).replace(/^Error:\s*/, "");
    errorBox.classList.remove("hidden");
  } finally {
    button.innerHTML = `<i class="fas fa-magnifying-glass-chart"></i><span id="practiceGenerateLabel">解析考点与范围</span>`;
    syncPracticeSubmitAvailability();
  }
}

function renderPracticePlanCoverage(cover) {
  const box = $("practicePlanCoverage");
  const badge = $("practicePlanCoverageBadge");
  const text = $("practicePlanCoverageText");
  if (!box || !cover || !cover.counts) {
    box?.classList.add("hidden");
    return;
  }
  const c = cover.counts;
  const perUnit = cover.per_unit || {};
  const sourceComplete = typeof cover.source_complete === "boolean"
    ? cover.source_complete
    : c.selected_units > 0 && c.covered_units === c.selected_units;
  const knowledge = cover.knowledge_points || {};
  const knowledgeApplicable = knowledge.applicable === true;
  const knowledgeComplete = !knowledgeApplicable || knowledge.complete !== false;
  const contentComplete = typeof cover.content_complete === "boolean"
    ? cover.content_complete
    : sourceComplete && knowledgeComplete;
  box.classList.remove("hidden");
  if (badge) {
    const sourceBadge = `来源 ${c.covered_units}/${c.selected_units}`;
    const knowledgeBadge = knowledgeApplicable
      ? ` · 知识点 ${knowledge.covered_count || 0}/${knowledge.expected_count || 0}`
      : "";
    badge.textContent = `${contentComplete ? "✅" : "⚠️"} ${sourceBadge}${knowledgeBadge}`;
    badge.style.color = contentComplete
      ? "var(--brand-success, #16a34a)"
      : sourceComplete
        ? "var(--brand-warning, #b45309)"
        : "var(--brand-danger, #dc2626)";
  }
  if (text) {
    const entries = Object.entries(perUnit);
    const detail = entries.length
      ? `（${entries.map(([id, n]) => `${id}:${n}题`).join("，")}）`
      : "";
    const sourceText = `${c.selected_units} 个来源单元 / ${c.planned_exercises} 道计划题目${detail}；${sourceComplete ? "来源引用完整。" : "存在未覆盖来源，已拦截生成。请返回范围页编辑、合并、拆分或新增单元后重试。"}`;
    if (!knowledgeApplicable) {
      text.textContent = sourceText;
      return;
    }
    const coveredPoints = Array.isArray(knowledge.covered_points) ? knowledge.covered_points : [];
    const uncoveredPoints = Array.isArray(knowledge.uncovered_points) ? knowledge.uncovered_points : [];
    const coveredText = coveredPoints.length ? coveredPoints.join("、") : "无";
    const uncoveredText = uncoveredPoints.length ? uncoveredPoints.join("、") : "无";
    const knowledgeText = `必考知识点 ${knowledge.covered_count || 0}/${knowledge.expected_count || 0}；本次纳入：${coveredText}；本次未纳入：${uncoveredText}。`;
    const conclusion = contentComplete
      ? "来源单元与必考知识点均覆盖完整，可生成。"
      : sourceComplete
        ? "蓝图可继续生成，但只覆盖本次纳入的知识点范围。"
        : "";
    text.textContent = `${sourceText}${knowledgeText}${conclusion}`;
  }
}

async function generatePracticeFromPlan() {
  if (!latestPracticePlan || !latestPracticeRequest) {
    showPracticePlanError("当前蓝图状态已失效，请返回范围确认页重新加载蓝图后再生成。");
    return;
  }
  const cover = latestPracticePlan?.scope_cover;
  if (cover && cover.counts && cover.counts.selected_units > 0 && cover.complete === false) {
    showPracticePlanError(`生成被拦截：所选范围有 ${cover.counts.uncovered_units} 个来源单元未被蓝图覆盖（已选 ${cover.counts.selected_units}，已覆盖 ${cover.counts.covered_units}）。请返回调整范围或覆盖后重试。`);
    return;
  }
  const planItems = latestPracticePlan?.blueprint?.exercise_plan || [];
  const planErrors = [];
  const seenPlanIds = new Set();
  planItems.forEach((item, index) => {
    const itemId = String(item?.plan_item_id || "");
    if (!itemId) planErrors.push(`第 ${index + 1} 项缺少计划项 ID`);
    if (seenPlanIds.has(itemId)) planErrors.push(`计划项 ID 重复：${itemId}`);
    seenPlanIds.add(itemId);
    if (!String(item?.target_skill || "").trim() || !String(item?.variation_type || "").trim() || !String(item?.design_intent || "").trim()) {
      planErrors.push(`第 ${index + 1} 项缺少目标能力、变化方式或设计意图`);
    }
  });
  if (planErrors.length) {
    showPracticePlanError(`生成被拦截：${planErrors.slice(0, 4).join("；")}${planErrors.length > 4 ? `；另有 ${planErrors.length - 4} 项` : ""}`);
    return;
  }
  const multiQuestion = practiceBlueprintMultiQuestionConfig(latestPracticePlan);
  const sessionVersion = practiceSessionVersion;
  const button = $("practicePlanConfirmBtn");
  const original = button.innerHTML;
  button.disabled = true;
  button.innerHTML = `<i class="fas fa-circle-notch fa-spin"></i>正在生成 ${multiQuestion.total_count} 题`;
  showPracticeOperationLoading(`蓝图已确认，正在生成 ${multiQuestion.total_count} 道练习`, "generate_from_plan");
  setPracticeStageDescription(`蓝图已确认，后台将按 ${planItems.length} 个蓝图项生成 ${multiQuestion.total_count} 道题。`);
  setPracticeStatusBanner("正在生成练习", "loading");
  try {
    // The confirmed blueprint is the source of truth. This also repairs older
    // knowledge tasks whose request was accidentally stored as targeted_set.
    const confirmedStrategy = latestPracticePlan?.blueprint?.generation_strategy
      || latestPracticeRequest.generation_strategy;
    latestPracticeRequest = {
      ...latestPracticeRequest,
      generation_strategy: confirmedStrategy,
      blueprint_review_enabled: true,
      blueprint_multi_question_enabled: multiQuestion.enabled,
      blueprint_variants_per_item: multiQuestion.variants_per_item,
      blueprint_variant_mode: multiQuestion.mode,
      blueprint_variant_selection_order: practiceVariantSelectionOrder,
      generation_run_id: latestPracticeRequest.generation_run_id || (globalThis.crypto?.randomUUID ? crypto.randomUUID() : `run_${Date.now()}_${Math.random().toString(16).slice(2)}`)
    };
    // 采用中的蓝图草案：按 plan_item_id 注入，正式生成时替换对应计划项（由后端判定）
    const adoptedDrafts = Object.entries(practicePlanDrafts)
      .filter(([, e]) => e && e.adopted)
      .reduce((acc, [id, e]) => { acc[id] = e.draft; return acc; }, {});
    const job = await submitPracticeJob("generate_from_plan", { ...latestPracticeRequest, plan: latestPracticePlan, plan_drafts: adoptedDrafts });
    if (sessionVersion !== practiceSessionVersion) return;
    rememberPracticeJob("");
    const data = job.result;
    renderPracticeResults(data);
    await clearPersistentPracticeWorkspace(currentPracticeSourceMode);
    markAllPracticeStagesDone();
    setPracticeStageDescription("练习题已生成完毕，可继续下载、编辑或回到蓝图调整。");
    setText("practiceSourceStatus", "已生成并保存");
    await loadPracticeHistory();
  } catch (error) {
    $("practiceLoading")?.classList.add("hidden");
    $("practicePlanReview")?.classList.remove("hidden");
    setPracticeStage("plan");
    setPracticeStageDescription("题目生成失败，蓝图和已修改内容仍保留，可查看原因后重试。");
    showPracticePlanError(`生成失败：${String(error).replace(/^Error:\s*/, "")}`);
  } finally {
    button.disabled = false;
    button.innerHTML = original;
  }
}

async function regeneratePracticeSet() {
  if (!latestPracticeRequest || !latestPracticeSet) return;
  const confirmed = await platformConfirm({
    eyebrow: "生成新版本",
    title: "重新生成整套题目",
    message: "将保留当前版本，并使用相同材料、范围、题量、题型和难度重新调用模型生成一个独立版本。",
    confirmText: "重新生题"
  });
  if (!confirmed) return;
  const sessionVersion = practiceSessionVersion;
  const enabled = latestPracticeRequest.blueprint_review_enabled !== false;
  if (enabled && !latestPracticePlan) {
    await platformAlert("当前任务缺少已确认蓝图，请先返回范围确认。", { title: "无法重新生题", tone: "danger" });
    return;
  }
  const runId = globalThis.crypto?.randomUUID ? crypto.randomUUID() : `run_${Date.now()}_${Math.random().toString(16).slice(2)}`;
  latestPracticeRequest = {
    ...latestPracticeRequest,
    generation_run_id: runId,
    generation_contract: enabled ? undefined : (latestPracticeSet.generation_contract || latestPracticeRequest.generation_contract)
  };
  showPracticeOperationLoading("正在重新生成整套题目", enabled ? "generate_from_plan" : "generate_from_contract");
  setPracticeStageDescription("保持已确认约束不变，正在生成新的独立版本。");
  try {
    const operation = enabled ? "generate_from_plan" : "generate_from_contract";
    const payload = enabled ? { ...latestPracticeRequest, plan: latestPracticePlan } : latestPracticeRequest;
    const job = await submitPracticeJob(operation, payload);
    if (sessionVersion !== practiceSessionVersion) return;
    rememberPracticeJob("");
    renderPracticeResults(job.result);
    markAllPracticeStagesDone();
    setPracticeStageDescription("新版本已生成并独立保存；旧版本仍保留在历史记录中。");
    await loadPracticeHistory();
  } catch (error) {
    if (sessionVersion !== practiceSessionVersion) return;
    $("practiceLoading")?.classList.add("hidden");
    $("practiceResults")?.classList.remove("hidden");
    const errorBox = $("practiceError");
    errorBox.textContent = `重新生题失败：${String(error).replace(/^Error:\s*/, "")}`;
    errorBox.classList.remove("hidden");
  }
}

async function saveCurrentPractice(showFeedback = true, changeReason = "manual_save") {
  if (!latestPracticeSet) return;
  const record = await api("/api/practice/history", {
    method: "POST",
    body: JSON.stringify({ data: latestPracticeSet, request: latestPracticeRequest || practiceRequestPayload(), change_reason: changeReason })
  });
  currentPracticeHistoryId = String(record.history_id || record.data?.history_id || "");
  currentPracticeRevisionCount = Number(record.revisions?.length || 0);
  latestPracticeSet = record.data;
  latestPracticeRequest = record.request || latestPracticeRequest;
  renderPracticeResults(latestPracticeSet);
  if (showFeedback) {
    setPracticeStatusBanner("修改已自动保存", "done");
  }
  await loadPracticeHistory();
}

async function loadPracticeHistory() {
  const container = $("practiceHistoryList");
  if (!container) return;
  const data = await api("/api/practice/history");
  const rows = data.records || [];
  container.innerHTML = rows.length ? rows.map((row) => {
    const completion = practiceCompletionContract(row);
    return `
    <button type="button" data-practice-history="${escapeHtml(row.history_id)}">
      <strong>${escapeHtml(row.title || "研究生专项练习")}</strong>
      <span>${escapeHtml(completion.display_label)} · 共 ${completion.total_count} 题：已生成 ${completion.generated_count} 题 · ${escapeHtml(String(row.updated_at || "").replace("T", " ").slice(0, 16))}</span>
    </button>
  `; }).join("") : "<p>暂无历史记录</p>";
  container.querySelectorAll("[data-practice-history]").forEach((button) => {
    button.addEventListener("click", async () => {
      try {
        const record = await api(`/api/practice/history/${encodeURIComponent(button.dataset.practiceHistory)}`);
        latestPracticeRequest = record.request || null;
        restorePracticePreferenceOrders(latestPracticeRequest);
        syncPracticeSourceContentPreference(latestPracticeRequest?.include_source_content_in_generation !== false);
        practiceSourceFiles = normalizeSourceFileList(latestPracticeRequest?.source_files).filter((file) => file?.data_url);
        renderPracticeFilePreview();
        latestPracticePlan = null;
        currentPracticeHistoryId = String(record.history_id || record.data?.history_id || "");
        currentPracticeRevisionCount = Number(record.revision_count || record.revisions?.length || 0);
        renderPracticeResults(record.data);
        setText("practiceSourceStatus", latestPracticeRequest?.source_recovery?.status === "blocked"
          ? "历史已载入，但原始材料不可恢复；请重新上传后再运行"
          : "已载入历史记录");
      } catch (error) {
        $("practiceError").textContent = String(error).replace(/^Error:\s*/, "");
        $("practiceError").classList.remove("hidden");
      }
    });
  });
  renderPracticeRecentHistory(rows);
}

const PRACTICE_EDITOR_DRAFT_PREFIX = "answerBook.practiceEditorDraft.v1.";
const PRACTICE_EDITOR_FIELD_IDS = [
  "practiceEditType", "practiceEditDifficulty", "practiceEditSkill", "practiceEditStem",
  "practiceEditOptions", "practiceEditFormulas", "practiceEditTables", "practiceEditFigures"
];

function practiceEditorStorageKey(index, item) {
  const historyId = String(latestPracticeSet?.history_id || currentPracticeHistoryId || "").trim();
  if (!historyId || !item) return "";
  const identity = practiceExerciseExportId(item, index);
  return `${PRACTICE_EDITOR_DRAFT_PREFIX}${encodeURIComponent(historyId)}.${encodeURIComponent(identity)}`;
}

function practiceEditorValues() {
  return Object.fromEntries(PRACTICE_EDITOR_FIELD_IDS.map((id) => [id, $(id)?.value ?? ""]));
}

function applyPracticeEditorValues(values) {
  PRACTICE_EDITOR_FIELD_IDS.forEach((id) => {
    if ($(id) && Object.prototype.hasOwnProperty.call(values || {}, id)) $(id).value = String(values[id] ?? "");
  });
  syncPlatformSelectElement($("practiceEditType"));
  syncPlatformSelectElement($("practiceEditDifficulty"));
}

function setPracticeEditorDraftAvailable(available) {
  $("practiceEditorDiscardDraft")?.classList.toggle("hidden", !available);
}

function cleanupPracticeEditorDrafts() {
  try {
    const rows = [];
    for (let index = 0; index < localStorage.length; index += 1) {
      const key = localStorage.key(index) || "";
      if (!key.startsWith(PRACTICE_EDITOR_DRAFT_PREFIX)) continue;
      try {
        const value = JSON.parse(localStorage.getItem(key) || "{}");
        rows.push({ key, savedAt: Number(value.saved_at || 0) });
      } catch (_error) {
        localStorage.removeItem(key);
      }
    }
    const expiry = Date.now() - 7 * 24 * 60 * 60 * 1000;
    rows.sort((left, right) => right.savedAt - left.savedAt).forEach((row, index) => {
      if (row.savedAt < expiry || index >= 20) localStorage.removeItem(row.key);
    });
  } catch (_error) {}
}

function persistPracticeEditorDraft(source = practiceEditorDraftSource) {
  if (!practiceEditorDraftKey || practiceEditingIndex < 0) return false;
  const record = {
    schema: "practice_editor_draft.v1",
    history_id: String(latestPracticeSet?.history_id || currentPracticeHistoryId || ""),
    exercise_index: practiceEditingIndex,
    base_edit_version: practiceEditorDraftBaseVersion,
    source,
    saved_at: Date.now(),
    values: practiceEditorValues()
  };
  try {
    localStorage.setItem(practiceEditorDraftKey, JSON.stringify(record));
    practiceEditorDraftSource = source;
    setPracticeEditorDraftAvailable(true);
    cleanupPracticeEditorDrafts();
    return true;
  } catch (_error) {
    $("practiceEditorError").textContent = "当前草稿过大或浏览器禁止本地存储，刷新前请先保存或复制内容。";
    $("practiceEditorError").classList.remove("hidden");
    return false;
  }
}

function schedulePracticeEditorDraftSave() {
  if (practiceEditorDraftTimer) clearTimeout(practiceEditorDraftTimer);
  practiceEditorDraftTimer = setTimeout(() => {
    practiceEditorDraftTimer = null;
    persistPracticeEditorDraft("manual");
  }, 250);
}

function clearPracticeEditorDraft() {
  if (practiceEditorDraftTimer) clearTimeout(practiceEditorDraftTimer);
  practiceEditorDraftTimer = null;
  try {
    if (practiceEditorDraftKey) localStorage.removeItem(practiceEditorDraftKey);
  } catch (_error) {}
  setPracticeEditorDraftAvailable(false);
}

function restorePracticeEditorDraft() {
  if (!practiceEditorDraftKey) return null;
  try {
    const record = JSON.parse(localStorage.getItem(practiceEditorDraftKey) || "null");
    if (!record || record.schema !== "practice_editor_draft.v1" || !record.values) return null;
    applyPracticeEditorValues(record.values);
    practiceEditorDraftSource = String(record.source || "manual");
    setPracticeEditorDraftAvailable(true);
    const stale = String(record.base_edit_version || "") !== practiceEditorDraftBaseVersion;
    $("practiceEditorError").textContent = stale
      ? "已恢复旧版本的未保存草稿。服务器题目后来发生过变化，请比较后再决定是否应用。"
      : (practiceEditorDraftSource === "regeneration_candidate"
          ? "已恢复上次未应用的生成候选，请确认后再保存。"
          : "已恢复上次未保存的编辑内容。请确认后保存，或点击“放弃草稿”。");
    $("practiceEditorError").classList.remove("hidden");
    return record;
  } catch (_error) {
    try { localStorage.removeItem(practiceEditorDraftKey); } catch (_storageError) {}
    return null;
  }
}

function populatePracticeEditor(item) {
  const typeSelect = $("practiceEditType");
  typeSelect.innerHTML = ["单选题", "多选题", "判断题", "填空题", "简答题", "计算题", "作图题", "综合题"]
    .map((type) => `<option${type === item.question_type ? " selected" : ""}>${type}</option>`).join("");
  const difficultySelect = $("practiceEditDifficulty");
  difficultySelect.value = item.difficulty || "进阶";
  syncPlatformSelectElement(typeSelect);
  syncPlatformSelectElement(difficultySelect);
  $("practiceEditSkill").value = item.target_skill || "";
  $("practiceEditStem").value = item.stem || "";
  $("practiceEditOptions").value = (item.options || []).map((option) => option.text || "").join("\n");
  $("practiceEditFormulas").value = (item.formulas || []).map((row) => `${row.location || "stem"} | ${row.latex || ""} | ${row.caption || ""}`).join("\n");
  $("practiceEditTables").value = JSON.stringify(item.tables || [], null, 2);
  $("practiceEditFigures").value = JSON.stringify(item.figures || [], null, 2);
}

function openPracticeEditor(index, draftItem = null) {
  const currentItem = latestPracticeSet?.exercises?.[index];
  if (!currentItem) return;
  const item = draftItem && typeof draftItem === "object" ? draftItem : currentItem;
  practiceEditingIndex = index;
  practiceEditorDraftKey = practiceEditorStorageKey(index, currentItem);
  practiceEditorDraftBaseVersion = String(currentItem._edit_version || "");
  practiceEditorDraftSource = draftItem ? "regeneration_candidate" : "manual";
  populatePracticeEditor(item);
  $("practiceEditorError").classList.add("hidden");
  setPracticeEditorDraftAvailable(false);
  if (draftItem) persistPracticeEditorDraft("regeneration_candidate");
  else restorePracticeEditorDraft();
  const saveButton = $("practiceEditorSave");
  if (saveButton) saveButton.disabled = false;
  setText("practiceEditorTitle", `编辑第 ${index + 1} 题`);
  $("practiceEditor").showModal();
}

function parsePracticeFormulaEditorLine(line, index) {
  const parts = String(line || "").split(/\s+\|\s+/);
  if (parts.length < 2) {
    return { formula_id: `f${index + 1}`, location: "stem", latex: String(line || "").trim(), caption: "", display: true };
  }
  const location = String(parts.shift() || "stem").trim() || "stem";
  const caption = parts.length > 1 ? String(parts.pop() || "").trim() : "";
  const latex = parts.join(" | ").trim();
  return { formula_id: `f${index + 1}`, location, latex, caption, display: true };
}

async function applyPracticeEditor(event) {
  event.preventDefault();
  const item = latestPracticeSet?.exercises?.[practiceEditingIndex];
  if (!item) return;
  const saveButton = $("practiceEditorSave");
  let editConflict = false;
  try {
    const tables = JSON.parse($("practiceEditTables").value || "[]");
    const figures = JSON.parse($("practiceEditFigures").value || "[]");
    const options = $("practiceEditOptions").value.split("\n").map((text) => text.trim()).filter(Boolean)
      .map((text, index) => ({ label: String.fromCharCode(65 + index), text }));
    const formulas = $("practiceEditFormulas").value.split("\n")
      .map(parsePracticeFormulaEditorLine)
      .filter((row) => row.latex);
    const editedExercise = {
      ...item,
      question_type: $("practiceEditType").value,
      difficulty: $("practiceEditDifficulty").value,
      target_skill: $("practiceEditSkill").value.trim(),
      stem: $("practiceEditStem").value.trim(),
      options,
      formulas,
      tables: Array.isArray(tables) ? tables : [],
      figures: Array.isArray(figures) ? figures : []
    };
    if (saveButton) saveButton.disabled = true;
    $("practiceEditorError").classList.add("hidden");
    await saveRegeneratedPracticeExercise(practiceEditingIndex, editedExercise, "manual_edit");
    clearPracticeEditorDraft();
    $("practiceEditor").close();
    renderPracticeResults(latestPracticeSet);
    setPracticeStatusBanner(`第 ${practiceEditingIndex + 1} 题已保存；原语义复核已失效，请复核后再作为正式结果使用。`, "warning");
    await loadPracticeHistory();
  } catch (error) {
    editConflict = error?.code === "practice_edit_conflict";
    if (editConflict) {
      try {
        const historyId = String(latestPracticeSet?.history_id || currentPracticeHistoryId || "");
        if (historyId) {
          const latest = await api(`/api/practice/history/${encodeURIComponent(historyId)}`);
          currentPracticeRevisionCount = Number(latest.revision_count || latest.revisions?.length || 0);
          latestPracticeSet = latest.data;
          latestPracticeRequest = latest.request || latestPracticeRequest;
        }
      } catch (_reloadError) {}
    }
    const message = String(error).replace(/^Error:\s*/, "");
    $("practiceEditorError").textContent = editConflict
      ? `修改未保存，其他页面的新内容未被覆盖。当前填写内容仍保留在编辑框中；请复制需要保留的部分，关闭后重新打开本题，再基于最新版本合并：${message}`
      : `修改未保存，原题已保留：${message}`;
    $("practiceEditorError").classList.remove("hidden");
  } finally {
    // A conflicted draft must not be retried against a newly fetched token,
    // because that would turn a safe conflict into a silent overwrite.
    if (saveButton) saveButton.disabled = editConflict;
  }
}

function practiceRegenerationPayload(index, instruction) {
  const knowledgeMode = (latestPracticeRequest?.source_mode || latestPracticeSet?.source_mode || currentPracticeSourceMode) === "knowledge";
  const modelRequest = knowledgeMode
    ? {
        provider: knowledgeProviderName("text"),
        model: selectedKnowledgeModel("text"),
        vision_provider: knowledgeProviderName("vision"),
        vision_model: selectedKnowledgeModel("vision")
      }
    : {
        provider: practiceProviderName("text"),
        model: selectedPracticeModel("text"),
        vision_provider: practiceProviderName("vision"),
        vision_model: selectedPracticeModel("vision")
      };
  return {
    practice: latestPracticeSet,
    exercise_index: index,
    instruction,
    source_mode: latestPracticeRequest?.source_mode || latestPracticeSet?.source_mode || currentPracticeSourceMode,
    generation_strategy: latestPracticeSet?.generation_strategy || latestPracticeRequest?.generation_strategy || "",
    selected_source_questions: latestPracticeSet?.selected_source_questions || latestPracticeRequest?.selected_source_questions || [],
    source_scope: latestPracticeSet?.source_scope || latestPracticeRequest?.source_scope || {},
    include_source_content_in_generation: latestPracticeRequest?.include_source_content_in_generation !== false,
    semantic_review_enabled: latestPracticeRequest?.semantic_review_enabled !== false,
    formal_quality_review: latestPracticeRequest?.formal_quality_review === true,
    question_types: latestPracticeRequest?.question_types || [],
    question_text: latestPracticeRequest?.question_text || "",
    source_files: latestPracticeRequest?.source_files || practiceSourceFiles || [],
    ...modelRequest,
    thinking: selectedThinkingMode()
  };
}

async function regeneratePracticeExercise(index, instruction) {
  return api("/api/practice/regenerate", {
    method: "POST",
    body: JSON.stringify(practiceRegenerationPayload(index, instruction))
  });
}

function setPracticeRegenerationBusy(busy) {
  practiceRegenerationInProgress = busy;
  document.querySelectorAll("[data-practice-regenerate], #practiceRegenerateSetBtn").forEach((button) => {
    button.disabled = busy;
  });
}

async function saveRegeneratedPracticeExercise(index, exercise, changeReason = "regenerate_question", semanticReview = null, practiceUpdates = null) {
  const historyId = String(latestPracticeSet?.history_id || currentPracticeHistoryId || "");
  if (!historyId) {
    latestPracticeSet.exercises[index] = exercise;
    await saveCurrentPractice(false, changeReason);
    return latestPracticeSet;
  }
  const record = await api(`/api/practice/history/${encodeURIComponent(historyId)}/exercise`, {
    method: "POST",
    body: JSON.stringify({
      exercise_index: index,
      exercise,
      expected_edit_version: String(latestPracticeSet?.exercises?.[index]?._edit_version || exercise?._edit_version || ""),
      change_reason: changeReason,
      ...(semanticReview ? { semantic_review: semanticReview } : {}),
      ...(practiceUpdates && Object.keys(practiceUpdates).length ? { practice_updates: practiceUpdates } : {})
    })
  });
  const revisionCount = Number(record.revision_count || record.revisions?.length || 0);
  if (revisionCount >= currentPracticeRevisionCount) {
    currentPracticeHistoryId = String(record.history_id || historyId);
    currentPracticeRevisionCount = revisionCount;
    latestPracticeRequest = record.request || latestPracticeRequest;
    latestPracticeSet = record.data;
  }
  return latestPracticeSet;
}

async function regeneratePracticeQuestion(index, button) {
  if (!latestPracticeSet || practiceRegenerationInProgress) return;
  const auditNeedsReview = latestPracticeSet?.exercises?.[index]?.audit_status === "audit_failed"
    || latestPracticeSet?.exercises?.[index]?.generation_error?.code === "blueprint_audit_failed";
  const instruction = await platformPrompt({
    eyebrow: auditNeedsReview ? "局部复审" : "重新生成",
    title: auditNeedsReview ? "复审并生成本题" : "填写本次调整要求",
    message: auditNeedsReview ? "系统只修复并复审这一蓝图项；通过后才生成本题，其他题目不会重跑。" : "留空会保持当前训练目标，仅生成另一种变式。",
    inputLabel: "调整要求（选填）",
    placeholder: "例如：换一个生活化情境，计算量保持不变",
    confirmText: auditNeedsReview ? "复审并生成" : "重新生成"
  });
  if (instruction === null) return;
  const original = button.innerHTML;
  setPracticeRegenerationBusy(true);
  button.innerHTML = '<i class="fas fa-circle-notch fa-spin"></i>';
  setPracticeStatusBanner(auditNeedsReview
    ? `正在复审第 ${index + 1} 题蓝图：通过后只生成本题`
    : `正在重新生成第 ${index + 1} 题：生成后会自动检查并必要时修复配图`, "loading");
  let generatedCandidate = null;
  try {
    const response = await regeneratePracticeExercise(index, instruction);
    generatedCandidate = response.exercise;
    await saveRegeneratedPracticeExercise(index, response.exercise, auditNeedsReview ? "review_and_regenerate_question" : "regenerate_question", response.semantic_review, response.practice_updates);
    renderPracticeResults(latestPracticeSet);
    await loadPracticeHistory();
  } catch (error) {
    const message = String(error).replace(/^Error:\s*/, "");
    if (error?.code === "practice_edit_conflict") {
      try {
        const historyId = String(latestPracticeSet?.history_id || currentPracticeHistoryId || "");
        if (historyId) {
          const latest = await api(`/api/practice/history/${encodeURIComponent(historyId)}`);
          currentPracticeRevisionCount = Number(latest.revision_count || latest.revisions?.length || 0);
          latestPracticeSet = latest.data;
          latestPracticeRequest = latest.request || latestPracticeRequest;
          renderPracticeResults(latestPracticeSet);
        }
      } catch (_reloadError) {}
      if (generatedCandidate) {
        openPracticeEditor(index, generatedCandidate);
        $("practiceEditorError").textContent = "这道候选题已生成，但其他页面刚保存了更新，因此没有自动覆盖。候选内容已保留在编辑框中；请与最新题目比较后决定是否应用。";
        $("practiceEditorError").classList.remove("hidden");
      }
    }
    $("practiceError").textContent = message;
    $("practiceError").classList.remove("hidden");
    setPracticeStatusBanner(
      generatedCandidate && error?.code === "practice_edit_conflict"
        ? `第 ${index + 1} 题未自动替换；服务器新内容和本次生成候选均已保留，请在编辑器中比较。`
        : `第 ${index + 1} 题未替换，原结果已保留：${message}`,
      "warning"
    );
  } finally {
    button.innerHTML = original;
    setPracticeRegenerationBusy(false);
    updatePracticeSelectionActions();
  }
}

async function regenerateSelectedPracticeQuestions(button) {
  if (!latestPracticeSet || !button || practiceRegenerationInProgress) return;
  const indexes = [...selectedPracticeExerciseIndexes].sort((a, b) => a - b)
    .filter((index) => latestPracticeSet?.exercises?.[index]);
  if (!indexes.length) {
    await platformAlert("请先选择至少一道可操作题目。", { title: "尚未选择题目", tone: "warning" });
    return;
  }
  const instruction = await platformPrompt({
    eyebrow: `重新生成已选 ${indexes.length} 题`,
    title: "填写本次调整要求",
    message: "将只重新生成已选择的题目，未选择的题目保持不变。留空会按原训练目标生成另一种变式。",
    inputLabel: "调整要求（选填）",
    placeholder: "例如：换一种情境，难度保持不变",
    confirmText: `重新生成 ${indexes.length} 题`
  });
  if (instruction === null) return;
  const original = button.innerHTML;
  setPracticeRegenerationBusy(true);
  const succeeded = [];
  const failures = [];
  try {
    for (const [position, index] of indexes.entries()) {
      button.innerHTML = `<i class="fas fa-circle-notch fa-spin"></i><span>${position + 1}/${indexes.length}</span>`;
      setPracticeStatusBanner(`正在重新生成已选题目（${position + 1}/${indexes.length}）`, "loading");
      try {
        const response = await regeneratePracticeExercise(index, instruction);
        await saveRegeneratedPracticeExercise(
          index,
          response.exercise,
          "regenerate_selected_questions",
          response.semantic_review
        );
        succeeded.push(index);
      } catch (error) {
        failures.push({ index, message: String(error).replace(/^Error:\s*/, "") });
      }
    }
    if (succeeded.length) {
      renderPracticeResults(latestPracticeSet);
      succeeded.forEach((index) => selectedPracticeExerciseIndexes.add(index));
      renderPracticeResults(latestPracticeSet);
      await loadPracticeHistory();
    }
    if (failures.length) {
      await platformAlert(
        `已重新生成 ${succeeded.length} 题；${failures.length} 题未完成：${failures.slice(0, 3).map((row) => `第 ${row.index + 1} 题`).join("、")}${failures.length > 3 ? "等" : ""}。`,
        { title: "部分题目未重新生成", tone: "warning" }
      );
    } else {
      setPracticeStatusBanner(`已重新生成已选 ${succeeded.length} 题。`, "done");
    }
  } finally {
    button.innerHTML = original;
    setPracticeRegenerationBusy(false);
    updatePracticeSelectionActions();
  }
}

async function undoPracticeChange() {
  const historyId = String(latestPracticeSet?.history_id || currentPracticeHistoryId || "");
  if (!historyId || currentPracticeRevisionCount < 1) return;
  const confirmed = await platformConfirm({
    eyebrow: "撤销已保存修改",
    title: "恢复上一个题目版本？",
    message: "当前版本也会被保留，恢复后仍可再次撤销。",
    confirmText: "恢复上一版",
    tone: "warning",
  });
  if (!confirmed) return;
  const button = $("practiceUndoBtn");
  if (button) button.disabled = true;
  try {
    const record = await api(`/api/practice/history/${encodeURIComponent(historyId)}/undo`, { method: "POST", body: "{}" });
    currentPracticeHistoryId = String(record.history_id || historyId);
    currentPracticeRevisionCount = Number(record.revision_count || record.revisions?.length || 0);
    latestPracticeRequest = record.request || latestPracticeRequest;
    syncPracticeSourceContentPreference(latestPracticeRequest?.include_source_content_in_generation !== false);
    renderPracticeResults(record.data);
    await loadPracticeHistory();
  } catch (error) {
    await platformAlert(String(error).replace(/^Error:\s*/, ""), { title: "撤销失败", tone: "danger" });
    if (button) button.disabled = currentPracticeRevisionCount < 1;
  }
}

function requestPlanRevisionSpec(item) {
  return new Promise((resolve) => {
    const overlay = document.createElement("div");
    overlay.className = "practice-revision-overlay";
    overlay.innerHTML = `
      <section class="practice-revision-card" role="dialog" aria-modal="true" aria-labelledby="practiceRevisionTitle">
        <header><div><small>单项蓝图重设计</small><h2 id="practiceRevisionTitle">明确这次必须怎么改</h2></div><button type="button" data-revision-close aria-label="关闭"><i class="fas fa-xmark"></i></button></header>
        <p class="practice-revision-current">当前：${escapeHtml(item.question_type || "综合题")} · ${escapeHtml(item.difficulty || "进阶")} · ${escapeHtml(item.target_skill || "核心能力")}</p>
        <fieldset><legend>必须改变（可多选）</legend><div class="practice-revision-options">
          ${[["question_type", "题型"], ["difficulty", "难度"], ["target_skill", "目标能力"], ["variation_type", "变化方式"], ["design_intent", "情境、条件或设计意图"]].map(([value, label]) => `<label><input type="checkbox" data-revision-change value="${value}"><span>${label}</span></label>`).join("")}
        </div></fieldset>
        <div class="practice-revision-locks"><strong>系统强制保留</strong><span><i class="fas fa-lock"></i>来源绑定</span><span><i class="fas fa-lock"></i>研究生层级</span></div>
        <label class="practice-revision-field"><span>禁止出现（选填，用逗号或换行分隔）</span><textarea rows="2" data-revision-forbid placeholder="例如：纯概念背诵、超纲公式"></textarea></label>
        <label class="practice-revision-field"><span>具体意见（选填）</span><textarea rows="3" data-revision-note placeholder="例如：改成需要两步推导的计算题，条件更明确"></textarea></label>
        <p class="practice-revision-error hidden" data-revision-error></p>
        <footer><button type="button" class="secondary-button" data-revision-cancel>取消</button><button type="button" class="primary-button" data-revision-confirm>按约束重新设计</button></footer>
      </section>`;
    document.body.appendChild(overlay);
    document.body.classList.add("platform-dialog-open");
    const finish = (value) => {
      document.removeEventListener("keydown", onKeydown);
      overlay.remove();
      document.body.classList.remove("platform-dialog-open");
      resolve(value);
    };
    const onKeydown = (event) => { if (event.key === "Escape") finish(null); };
    document.addEventListener("keydown", onKeydown);
    overlay.querySelector("[data-revision-close]").addEventListener("click", () => finish(null));
    overlay.querySelector("[data-revision-cancel]").addEventListener("click", () => finish(null));
    overlay.querySelector("[data-revision-confirm]").addEventListener("click", () => {
      const mustChange = [...overlay.querySelectorAll("[data-revision-change]:checked")].map((input) => input.value);
      const note = overlay.querySelector("[data-revision-note]").value.trim();
      if (!mustChange.length && !note) {
        const error = overlay.querySelector("[data-revision-error]");
        error.textContent = "请至少选择一项必须改变的内容，或填写具体意见。";
        error.classList.remove("hidden");
        return;
      }
      const forbid = overlay.querySelector("[data-revision-forbid]").value.split(/[，,、\n]+/).map((value) => value.trim()).filter(Boolean).slice(0, 8);
      finish({ must_change: mustChange, must_preserve: ["source_binding", "graduate_level"], forbid, note });
    });
    overlay.querySelector("[data-revision-change]")?.focus();
  });
}

async function regeneratePlanItem(index, button) {
  if (!latestPracticePlan || !latestPracticeRequest) return;
  const currentItem = latestPracticePlan.blueprint?.exercise_plan?.[index];
  if (!currentItem) return;
  const revisionSpec = await requestPlanRevisionSpec(currentItem);
  if (revisionSpec === null) return;
  const original = button.innerHTML;
  button.disabled = true;
  button.innerHTML = '<i class="fas fa-circle-notch fa-spin"></i>重新设计中';
  try {
    const result = await api("/api/practice/plan-item-regenerate", { method: "POST", body: JSON.stringify({ ...latestPracticeRequest, plan: latestPracticePlan, plan_index: index, revision_spec: revisionSpec }) });
    const items = latestPracticePlan.blueprint?.exercise_plan || [];
    if (!result.plan_item || !items[index]) throw new Error("未获得可用的蓝图候选项。");
    items[index] = result.plan_item;
    practicePlanRevisionReceipts[result.plan_item.plan_item_id] = {
      applied_changes: result.applied_changes || [],
      hard_checks: result.hard_checks || {},
      request_evidence: result.request_evidence || {},
    };
    for (const key of Object.keys(practicePlanDrafts)) delete practicePlanDrafts[key];
    renderPracticePlan(latestPracticePlan);
  } catch (error) {
    await platformAlert(String(error).replace(/^Error:\s*/, ""), { title: "本项蓝图重设计失败", tone: "danger" });
  } finally { button.disabled = false; button.innerHTML = original; }
}

async function generatePlanItemDraft(index, button) {
  if (!latestPracticePlan || !latestPracticeRequest) return;
  const planItem = latestPracticePlan?.blueprint?.exercise_plan?.[index];
  if (!planItem) return;
  const instruction = await platformPrompt({
    eyebrow: "生成本题草案",
    title: `生成第 ${index + 1} 题的草案`,
    message: "可填写本次调整要求，作为模型的针对性反馈；留空则按原方案生成一种有效变式。",
    inputLabel: "调整要求（选填）",
    placeholder: "例如：换一个生活化情境、加重计算推导、避免超纲等",
    confirmText: "生成草案",
    cancelText: "取消",
  });
  if (instruction === null) return;
  const original = button.innerHTML;
  button.disabled = true;
  button.innerHTML = '<i class="fas fa-circle-notch fa-spin"></i>生成中';
  const view = document.querySelector(`[data-plan-draft-view="${index}"]`);
  try {
    const knowledgeMode = (latestPracticeRequest?.source_mode || latestPracticePlan?.source_mode || currentPracticeSourceMode) === "knowledge";
    const modelRequest = knowledgeMode
      ? {
          provider: knowledgeProviderName("text"),
          model: selectedKnowledgeModel("text"),
          vision_provider: knowledgeProviderName("vision"),
          vision_model: selectedKnowledgeModel("vision")
        }
      : {
          provider: practiceProviderName("text"),
          model: selectedPracticeModel("text"),
          vision_provider: practiceProviderName("vision"),
          vision_model: selectedPracticeModel("vision")
        };
    const response = await api("/api/practice/plan-draft", {
      method: "POST",
      body: JSON.stringify({
        plan: latestPracticePlan,
        plan_item_id: planItem.plan_item_id,
        plan_index: index,
        instruction,
        source_mode: latestPracticeRequest?.source_mode || latestPracticePlan?.source_mode || currentPracticeSourceMode,
        generation_strategy: latestPracticePlan?.blueprint?.generation_strategy || latestPracticeRequest?.generation_strategy || "",
        selected_source_questions: latestPracticePlan?.selected_source_questions || latestPracticeRequest?.selected_source_questions || [],
        source_scope: latestPracticePlan?.source_scope || latestPracticeRequest?.source_scope || {},
        include_source_content_in_generation: latestPracticeRequest?.include_source_content_in_generation !== false,
        question_types: latestPracticeRequest?.question_types || [],
        question_text: latestPracticeRequest?.question_text || "",
        source_files: latestPracticeRequest?.source_files || practiceSourceFiles || [],
        ...modelRequest,
        thinking: selectedThinkingMode()
      })
    });
    // 按 plan_item_id 保存草案，跨重渲染/流程切换不丢失
    practicePlanDrafts[planItem.plan_item_id] = { draft: response.draft || {}, adopted: false, quality: response.quality || {} };
    schedulePracticeWorkspaceDraftSave(currentPracticeSourceMode);
    if (view) {
      view.classList.remove("hidden");
      view.innerHTML = renderPlanItemDraft(planItem.plan_item_id);
    }
  } catch (error) {
    if (view) view.classList.remove("hidden");
    else if ($("practiceError")) { $("practiceError").textContent = String(error).replace(/^Error:\s*/, ""); $("practiceError").classList.remove("hidden"); }
  } finally {
    button.disabled = false;
    button.innerHTML = original;
  }
}

function renderPlanItemDraft(planItemId) {
  const entry = practicePlanDrafts[planItemId];
  if (!entry) return "";
  const draft = entry.draft || {};
  const quality = entry.quality || {};
  const adopted = !!entry.adopted;
  const qualityText = quality.status === "passed" && !quality.warnings?.length
    ? "✅ 结构检查通过"
    : quality.status === "passed"
      ? `⚠️ 结果已保留，仍有 ${quality.warnings.length} 项需复核：${quality.warnings.slice(0, 2).join("；")}`
      : `⚠️ ${quality.status || "warning"}${quality.warnings?.length ? "：" + quality.warnings.slice(0, 2).join("；") : ""}`;
  const options = (draft.options || []).map((o) => `<p><b>${escapeHtml(o.label || "")}</b>${practiceMarkdown(o.text || "")}</p>`).join("");
  return `<div class="practice-plan-draft__card" data-plan-draft-card data-plan-draft-id="${escapeHtml(planItemId)}">
    <div class="practice-plan-draft__head">
      <strong>草稿 #${draft.number || ""}</strong><em>${escapeHtml(draft.question_type || "")} · ${escapeHtml(draft.difficulty || "")}</em>
      <span class="${quality.status === "passed" && !quality.warnings?.length ? "ok" : "warn"}">${qualityText}</span>
      <b class="practice-plan-draft__status ${adopted ? "adopted" : ""}">${adopted ? "✅ 已采用（将进入正式生成）" : "仅预览（未采用）"}</b>
    </div>
    <div class="practice-plan-draft__stem">${practiceMarkdown(draft.stem || "")}</div>
    ${options ? `<div class="practice-plan-draft__options">${options}</div>` : ""}
    <div class="practice-plan-draft__actions">
      <button type="button" data-plan-draft-adopt="${escapeHtml(planItemId)}">${adopted ? "取消采用" : "采用此草案"}</button>
      <button type="button" data-plan-draft-clear="${escapeHtml(planItemId)}">清除草案</button>
    </div>
  </div>`;
}

function togglePlanItemDraftAdopt(planItemId, button) {
  const entry = practicePlanDrafts[planItemId];
  if (!entry) return;
  entry.adopted = !entry.adopted;
  const cardEl = button.closest("[data-plan-draft-card]");
  if (cardEl) cardEl.outerHTML = renderPlanItemDraft(planItemId);
  renderPlanDraftAdoptSummary();
  schedulePracticeWorkspaceDraftSave(currentPracticeSourceMode);
}

function clearPlanItemDraft(planItemId, button) {
  delete practicePlanDrafts[planItemId];
  const cardEl = button.closest("[data-plan-draft-card]");
  const view = cardEl?.closest("[data-plan-draft-view]");
  if (view) view.classList.add("hidden");
  renderPlanDraftAdoptSummary();
  schedulePracticeWorkspaceDraftSave(currentPracticeSourceMode);
}

function renderPlanDraftAdoptSummary() {
  const adopted = Object.values(practicePlanDrafts).filter((e) => e && e.adopted).length;
  if (adopted > 0) {
    setText("practicePlanDraftSummary", `已采用 ${adopted} 份草案，将替换对应计划项进入正式生成。`);
    $("practicePlanDraftSummary")?.classList.remove("hidden");
  } else {
    $("practicePlanDraftSummary")?.classList.add("hidden");
  }
}

function practiceBlueprintKey(plan) {
  if (!plan) return "";
  const blueprint = plan.blueprint || {};
  const items = (blueprint.exercise_plan || []).map((i) => JSON.stringify({
    plan_item_id: i && i.plan_item_id,
    question_type: i && i.question_type,
    difficulty: i && i.difficulty,
    target_skill: i && i.target_skill,
    variation_type: i && i.variation_type,
    design_intent: i && i.design_intent,
    source_question_id: i && i.source_question_id,
    number: i && i.number,
  }));
  // 稳定内容指纹：training_goal + 策略 + 逐计划项关键字段 + 来源范围快照
  const sourceScope = plan.source_scope || {};
  const scopeFinger = JSON.stringify({
    title: sourceScope.title,
    granularity: sourceScope.granularity,
    qids: Array.isArray(sourceScope.questions) ? sourceScope.questions.map((q) => String((q && q.source_question_id) || "")) : [],
  });
  const raw = `${blueprint.training_goal || ""}::${blueprint.generation_strategy || ""}::${scopeFinger}::${items.join("|")}`;
  // djb2 哈希，缩短比较串且对顺序敏感
  let hash = 5381;
  for (let i = 0; i < raw.length; i++) hash = ((hash << 5) + hash + raw.charCodeAt(i)) >>> 0;
  return `${hash.toString(36)}:${raw.length}`;
}

function syncPracticePlanDraftsToBlueprint(plan, planItems) {
  // 蓝图身份变化：清空旧草案，避免把旧蓝图的草案注入新蓝图（防串题）
  const key = practiceBlueprintKey(plan);
  if (currentPlanDraftBlueprintKey !== key) {
    for (const pid of Object.keys(practicePlanDrafts)) delete practicePlanDrafts[pid];
    currentPlanDraftBlueprintKey = key;
  }
  // 只保留当前蓝图计划项内的草案
  const aliveIds = new Set((planItems || []).map((i) => String(i?.plan_item_id || "")).filter(Boolean));
  for (const pid of Object.keys(practicePlanDrafts)) {
    if (!aliveIds.has(pid)) delete practicePlanDrafts[pid];
  }
  // 按 plan_item_id 把已有草案重新渲染回各 data-plan-draft-view，并恢复采用状态
  (planItems || []).forEach((item, index) => {
    const pid = String(item?.plan_item_id || "");
    const view = document.querySelector(`[data-plan-draft-view="${index}"]`);
    if (!view || !pid || !practicePlanDrafts[pid]) return;
    view.classList.remove("hidden");
    view.innerHTML = renderPlanItemDraft(pid);
  });
  renderPlanDraftAdoptSummary();
}

function practiceWordButton() {
  return $("practiceDownloadSelectedBtn");
}

function practiceWordExportKey(data, filename = "") {
  const historyId = String(data?.history_id || latestPracticeSet?.history_id || "current");
  const exerciseIds = (data?.exercises || []).map((item, index) => String(
    item?.plan_item_id || item?.question_id || item?.id || item?.number || index + 1
  ));
  return `${historyId}:${exerciseIds.join(",") || "all"}:${String(filename || "default")}`;
}

function normalizePracticeWordExportPointer(raw) {
  if (!raw || typeof raw !== "object" || Array.isArray(raw)) return null;
  const exportKey = String(raw.export_key || "").trim();
  const jobId = String(raw.job_id || "").trim();
  const filename = String(raw.filename || "专项练习-题目.docx").split(/[\\/]/).pop().slice(0, 200);
  const createdAtMs = Date.parse(String(raw.created_at || ""));
  const expiresAtMs = Date.parse(String(raw.expires_at || ""));
  if (!exportKey || exportKey.length > 600) return null;
  if (!/^practice_word_[a-zA-Z0-9_-]{8,96}$/.test(jobId)) return null;
  if (!Number.isFinite(createdAtMs) || !Number.isFinite(expiresAtMs)) return null;
  if (expiresAtMs <= Date.now() || expiresAtMs <= createdAtMs) return null;
  return {
    export_key: exportKey,
    job_id: jobId,
    filename: filename || "专项练习-题目.docx",
    created_at: new Date(createdAtMs).toISOString(),
    expires_at: new Date(expiresAtMs).toISOString(),
  };
}

function writePracticeWordExportPointers(records = []) {
  const deduplicated = new Map();
  (Array.isArray(records) ? records : []).forEach((raw) => {
    const pointer = normalizePracticeWordExportPointer(raw);
    if (!pointer) return;
    deduplicated.set(`${pointer.export_key}\u0000${pointer.job_id}`, pointer);
  });
  const normalized = [...deduplicated.values()]
    .sort((left, right) => Date.parse(right.created_at) - Date.parse(left.created_at))
    .slice(0, PRACTICE_WORD_EXPORT_POINTER_LIMIT);
  try {
    if (!normalized.length) {
      localStorage.removeItem(PRACTICE_WORD_EXPORT_POINTER_STORAGE_KEY);
      return [];
    }
    localStorage.setItem(PRACTICE_WORD_EXPORT_POINTER_STORAGE_KEY, JSON.stringify({
      schema_version: PRACTICE_WORD_EXPORT_POINTER_SCHEMA_VERSION,
      updated_at: new Date().toISOString(),
      records: normalized,
    }));
  } catch (_) {
    // Export continues even when browser storage is unavailable.
  }
  return normalized;
}

function readPracticeWordExportPointers() {
  let parsed;
  try {
    parsed = JSON.parse(localStorage.getItem(PRACTICE_WORD_EXPORT_POINTER_STORAGE_KEY) || "null");
  } catch (_) {
    parsed = null;
  }
  if (!parsed || parsed.schema_version !== PRACTICE_WORD_EXPORT_POINTER_SCHEMA_VERSION || !Array.isArray(parsed.records)) {
    try { localStorage.removeItem(PRACTICE_WORD_EXPORT_POINTER_STORAGE_KEY); } catch (_) {}
    return [];
  }
  return writePracticeWordExportPointers(parsed.records);
}

function rememberPracticeWordExportPointer(exportKey, jobId, filename, createdAt = new Date().toISOString()) {
  const createdAtMs = Number.isFinite(Date.parse(createdAt)) ? Date.parse(createdAt) : Date.now();
  const pointer = normalizePracticeWordExportPointer({
    export_key: exportKey,
    job_id: jobId,
    filename,
    created_at: new Date(createdAtMs).toISOString(),
    expires_at: new Date(createdAtMs + PRACTICE_WORD_EXPORT_POINTER_TTL_MS).toISOString(),
  });
  if (!pointer) return null;
  const records = readPracticeWordExportPointers().filter((item) => item.export_key !== pointer.export_key);
  writePracticeWordExportPointers([pointer, ...records]);
  return pointer;
}

function forgetPracticeWordExportPointer(exportKey) {
  const normalizedKey = String(exportKey || "");
  writePracticeWordExportPointers(readPracticeWordExportPointers().filter((item) => item.export_key !== normalizedKey));
  practiceWordRecoveryJobs.delete(normalizedKey);
  activePracticeWordExports.delete(normalizedKey);
}

function practiceWordRecoveryError(job = {}) {
  const raw = String(job?.error || "").trim();
  if (!raw) return "Word 生成未完成，请重新尝试。";
  if (/^[\[{]/.test(raw) || /traceback|request[_ -]?id|stack trace|internal[_ -]?server/i.test(raw)) {
    return "Word 生成未完成，请重新尝试；如持续失败可反馈问题。";
  }
  const firstLine = raw.split(/\r?\n/)[0].replace(/\s+/g, " ").trim();
  return firstLine.length > 160 ? `${firstLine.slice(0, 157)}...` : firstLine;
}

function practiceWordRecoveryMeta(job = {}) {
  const status = String(job.status || "checking");
  if (status === "completed") return {
    tone: "success",
    message: "Word 已生成，等待你确认下载。",
    action: "download",
    actionLabel: "下载 Word",
  };
  if (status === "failed") return {
    tone: "danger",
    message: practiceWordRecoveryError(job),
    action: "retry",
    actionLabel: "重新生成",
  };
  if (status === "unavailable") return {
    tone: "warning",
    message: "暂时无法连接服务，任务标识已保留，将自动继续查询。",
    action: "refresh",
    actionLabel: "刷新状态",
  };
  if (["queued", "running"].includes(status)) {
    const completed = Number(job.completed_count || 0);
    const total = Number(job.total_count || 0);
    return {
      tone: "progress",
      message: total > 0 ? `后台生成中 · 已处理 ${completed}/${total} 题，不会切换当前页面。` : "后台生成中，不会切换当前页面。",
      action: "refresh",
      actionLabel: "刷新状态",
    };
  }
  return {
    tone: "progress",
    message: "正在向服务端确认导出状态。",
    action: "refresh",
    actionLabel: "刷新状态",
  };
}

function renderPracticeWordRecoveryNotice() {
  const notice = $("practiceWordRecoveryNotice");
  const list = $("practiceWordRecoveryList");
  if (!notice || !list) return;
  const pointers = readPracticeWordExportPointers();
  if (!pointers.length) {
    notice.classList.add("hidden");
    list.innerHTML = "";
    return;
  }
  notice.classList.remove("hidden");
  list.innerHTML = pointers.map((pointer, index) => {
    const entry = practiceWordRecoveryJobs.get(pointer.export_key);
    const job = entry?.job || { status: "checking" };
    const meta = practiceWordRecoveryMeta(job);
    return `
      <section class="practice-word-recovery-item" data-tone="${escapeHtml(meta.tone)}">
        <div class="practice-word-recovery-item__copy">
          <strong title="${escapeHtml(pointer.filename)}">${escapeHtml(pointer.filename)}</strong>
          <p>${escapeHtml(meta.message)}</p>
        </div>
        <div class="practice-word-recovery-item__actions">
          <button class="${meta.action === "download" ? "primary-button" : "ghost-button"}" type="button" data-practice-word-recovery-action="${escapeHtml(meta.action)}" data-practice-word-recovery-index="${index}">${escapeHtml(meta.actionLabel)}</button>
          <button class="text-button" type="button" data-practice-word-recovery-action="dismiss" data-practice-word-recovery-index="${index}">关闭提示</button>
        </div>
      </section>`;
  }).join("");
  list.querySelectorAll("[data-practice-word-recovery-action]").forEach((button) => {
    button.addEventListener("click", () => handlePracticeWordRecoveryAction(
      button.dataset.practiceWordRecoveryAction,
      pointers[Number(button.dataset.practiceWordRecoveryIndex)],
      button
    ));
  });
}

function schedulePracticeWordRecoveryRefresh(delayMs = 1500) {
  if (practiceWordRecoveryPollTimer) window.clearTimeout(practiceWordRecoveryPollTimer);
  if (!readPracticeWordExportPointers().length) {
    practiceWordRecoveryPollTimer = null;
    return;
  }
  practiceWordRecoveryPollTimer = window.setTimeout(() => {
    practiceWordRecoveryPollTimer = null;
    resumeRememberedPracticeWordExports().catch(() => {});
  }, delayMs);
}

async function loadPracticeWordRecoveryJob(pointer) {
  const response = await fetch(`/api/practice/export-jobs/${encodeURIComponent(pointer.job_id)}`, { cache: "no-store" });
  if (response.status === 404) return null;
  if (!response.ok) throw new Error("export_job_unavailable");
  const payload = await response.json();
  return payload?.job && typeof payload.job === "object" ? payload.job : null;
}

async function resumeRememberedPracticeWordExports() {
  if (practiceWordRecoveryRefreshInFlight) return;
  const pointers = readPracticeWordExportPointers();
  if (!pointers.length) {
    renderPracticeWordRecoveryNotice();
    return;
  }
  const refreshVersion = ++practiceWordRecoveryRefreshVersion;
  practiceWordRecoveryRefreshInFlight = true;
  pointers.forEach((pointer) => {
    if (!practiceWordRecoveryJobs.has(pointer.export_key)) {
      practiceWordRecoveryJobs.set(pointer.export_key, { pointer, job: { status: "checking" } });
    }
  });
  renderPracticeWordRecoveryNotice();
  try {
    const jobRequests = new Map();
    pointers.forEach((pointer) => {
      if (!jobRequests.has(pointer.job_id)) {
        jobRequests.set(pointer.job_id, loadPracticeWordRecoveryJob(pointer));
      }
    });
    const results = await Promise.all(pointers.map(async (pointer) => {
      try {
        return { pointer, job: await jobRequests.get(pointer.job_id) };
      } catch (_) {
        return { pointer, job: { status: "unavailable" } };
      }
    }));
    if (refreshVersion !== practiceWordRecoveryRefreshVersion) return;
    let shouldPoll = false;
    results.forEach(({ pointer, job }) => {
      if (!job) {
        forgetPracticeWordExportPointer(pointer.export_key);
        return;
      }
      practiceWordRecoveryJobs.set(pointer.export_key, { pointer, job });
      if (["queued", "running"].includes(job.status)) {
        activePracticeWordExports.set(pointer.export_key, {
          filename: pointer.filename,
          jobId: pointer.job_id,
          completed: Number(job.completed_count || 0),
          total: Number(job.total_count || 0),
          restored: true,
        });
        shouldPoll = true;
      } else {
        activePracticeWordExports.delete(pointer.export_key);
        if (job.status === "unavailable") shouldPoll = true;
      }
    });
    syncPracticeWordExportUi();
    renderPracticeWordRecoveryNotice();
    if (shouldPoll) schedulePracticeWordRecoveryRefresh(results.some(({ job }) => job?.status === "unavailable") ? 4000 : 1200);
  } finally {
    practiceWordRecoveryRefreshInFlight = false;
  }
}

async function downloadRememberedPracticeWord(pointer, button) {
  button.disabled = true;
  const response = await fetch(`/api/practice/export-jobs/${encodeURIComponent(pointer.job_id)}/download`, { cache: "no-store" });
  if (!response.ok) {
    const payload = await response.json().catch(() => ({}));
    window.setTimeout(() => resumeRememberedPracticeWordExports().catch(() => {}), 0);
    throw new Error(practiceWordRecoveryError({ error: payload.error || "Word 下载失败，请稍后重试。" }));
  }
  const blob = await response.blob();
  downloadPracticeWord(blob, pointer.filename);
  forgetPracticeWordExportPointer(pointer.export_key);
  renderPracticeWordRecoveryNotice();
  await platformAlert("Word 已开始下载，恢复提示已清理。", { title: "题目 Word 已下载", tone: "success" });
}

async function retryRememberedPracticeWord(pointer, button) {
  button.disabled = true;
  const payload = await api(`/api/practice/export-jobs/${encodeURIComponent(pointer.job_id)}/retry`, {
    method: "POST",
    body: "{}",
  });
  const job = payload?.job || {};
  const refreshed = rememberPracticeWordExportPointer(pointer.export_key, job.job_id || pointer.job_id, pointer.filename, pointer.created_at) || pointer;
  practiceWordRecoveryJobs.set(refreshed.export_key, { pointer: refreshed, job });
  renderPracticeWordRecoveryNotice();
  schedulePracticeWordRecoveryRefresh(500);
}

async function handlePracticeWordRecoveryAction(action, pointer, button) {
  if (!pointer) return;
  if (action === "dismiss") {
    practiceWordRecoveryRefreshVersion += 1;
    forgetPracticeWordExportPointer(pointer.export_key);
    renderPracticeWordRecoveryNotice();
    return;
  }
  try {
    if (action === "download") await downloadRememberedPracticeWord(pointer, button);
    else if (action === "retry") await retryRememberedPracticeWord(pointer, button);
    else await resumeRememberedPracticeWordExports();
  } catch (error) {
    await platformAlert(practiceWordRecoveryError({ error: String(error).replace(/^Error:\s*/, "") }), {
      title: action === "download" ? "Word 下载失败" : "Word 任务未能继续",
      tone: "danger",
    });
  } finally {
    if (button?.isConnected) button.disabled = false;
  }
}

function practiceExerciseExportId(item, index = 0) {
  return String(item?.plan_item_id || item?.exercise_id || item?.question_id || item?.id || item?.number || index + 1);
}

function practiceExportRequestPayload(data) {
  const requestedExercises = Array.isArray(data?.exercises) ? data.exercises : [];
  const requestedIds = requestedExercises.map(practiceExerciseExportId);
  const allExercises = Array.isArray(latestPracticeSet?.exercises) ? latestPracticeSet.exercises : requestedExercises;
  const allIds = allExercises.map(practiceExerciseExportId);
  const selectedScope = data?.export_scope === "selected"
    || requestedIds.length !== allIds.length
    || requestedIds.some((value, index) => value !== allIds[index]);
  const historyId = String(data?.history_id || latestPracticeSet?.history_id || currentPracticeHistoryId || "").trim();
  const selection = {
    history_id: historyId,
    export_scope: selectedScope ? "selected" : "all",
    selected_exercise_ids: selectedScope ? requestedIds : []
  };
  // Saved histories are the source of truth. Sending only their identity and
  // selection avoids reposting embedded/base64 figures, which can exceed the
  // normal JSON request limit for image-heavy question sets.
  return historyId ? selection : { ...data, ...selection };
}

function practiceWordLabel(generating = false, label = "下载题目 Word") {
  return `<i class="fas ${generating ? "fa-circle-notch fa-spin" : "fa-file-word"}"></i>${generating ? "正在生成 Word" : label}`;
}

function syncPracticeWordExportButton(button, key, available, label = "下载题目 Word") {
  if (!button) return;
  const normalizedKey = String(key || "");
  const generating = Boolean(normalizedKey && activePracticeWordExports.has(normalizedKey));
  button.dataset.practiceWordExportKey = normalizedKey;
  button.dataset.practiceWordExportAvailable = available ? "true" : "false";
  button.dataset.practiceWordExportLabel = label;
  button.disabled = !available || generating;
  button.classList.toggle("opacity-60", !available || generating);
  button.classList.toggle("cursor-not-allowed", !available || generating);
  button.setAttribute("aria-disabled", !available || generating ? "true" : "false");
  button.setAttribute("aria-busy", generating ? "true" : "false");
  button.innerHTML = practiceWordLabel(generating, label);
}

function syncPracticeWordExportUi() {
  document.querySelectorAll("[data-practice-word-export-key]").forEach((button) => {
    syncPracticeWordExportButton(
      button,
      button.dataset.practiceWordExportKey,
      button.dataset.practiceWordExportAvailable === "true",
      button.dataset.practiceWordExportLabel || "下载题目 Word"
    );
  });
  if (activePracticeWordExports.size && latestPracticeSet && !$("practiceResults")?.classList.contains("hidden")) {
    const count = activePracticeWordExports.size;
    setPracticeStatusBanner(
      `正在生成 ${count} 份题目 Word；可切换页面，返回后会继续显示进度。`,
      "loading"
    );
  }
}

function clearPreparedPracticeWords() {
  syncPracticeWordExportUi();
}

function downloadPracticeWord(blob, filename) {
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = filename;
  document.body.appendChild(link);
  link.click();
  link.remove();
  window.setTimeout(() => URL.revokeObjectURL(url), 0);
}

async function waitForPracticeWordExportJob(initialJob, exportKey) {
  let job = initialJob || {};
  const deadline = Date.now() + 10 * 60 * 1000;
  while (["queued", "running"].includes(job.status)) {
    if (Date.now() >= deadline) throw new Error("Word 生成超过 10 分钟，请稍后重试并查看运行监控。");
    const completed = Number(job.completed_count || 0);
    const total = Number(job.total_count || 0);
    activePracticeWordExports.set(exportKey, { ...activePracticeWordExports.get(exportKey), jobId: job.job_id, completed, total });
    setPracticeStatusBanner(
      total > 0 ? `正在生成 Word · 已处理 ${completed}/${total} 题，可切换页面。` : "正在准备 Word，可切换页面。",
      "loading"
    );
    await new Promise((resolve) => window.setTimeout(resolve, 500));
    const payload = await api(`/api/practice/export-jobs/${encodeURIComponent(job.job_id)}`, { cache: "no-store" });
    job = payload.job || {};
  }
  if (job.status === "failed") throw new Error(job.error || "Word 生成失败");
  if (job.status !== "completed") throw new Error("Word 生成状态异常，请重试。");
  return job;
}

async function prepareOrDownloadPracticeWord(data = latestPracticeSet, button = practiceWordButton(), filename = `${data?.source_mode === "knowledge" ? "知识点模拟题" : "按题出题"}-题目.docx`) {
  if (!data || !button) return;
  const exportKey = practiceWordExportKey(data, filename);
  if (activePracticeWordExports.has(exportKey)) {
    syncPracticeWordExportUi();
    return;
  }
  const label = button.dataset.practiceWordExportLabel || "下载题目 Word";
  activePracticeWordExports.set(exportKey, { filename, startedAt: Date.now() });
  syncPracticeWordExportButton(button, exportKey, true, label);
  syncPracticeWordExportUi();
  try {
    const prepared = await api("/api/practice/export/prepare?kind=questions", {
      method: "POST",
      body: JSON.stringify(practiceExportRequestPayload(data))
    });
    const preparedJob = prepared?.job || {};
    const pointer = rememberPracticeWordExportPointer(exportKey, preparedJob.job_id, filename);
    if (!pointer) throw new Error("Word 导出任务未返回有效标识，请重新尝试。");
    practiceWordRecoveryJobs.set(exportKey, { pointer, job: preparedJob });
    renderPracticeWordRecoveryNotice();
    const job = await waitForPracticeWordExportJob(preparedJob, exportKey);
    if (!job) {
      setPracticeStatusBanner("已返回处理，未下载 Word。", "info");
      return;
    }
    const downloadResponse = await fetch(`/api/practice/export-jobs/${encodeURIComponent(job.job_id)}/download`);
    if (!downloadResponse.ok) {
      const errorData = await downloadResponse.json().catch(() => ({}));
      throw new Error(errorData.error || "Word 下载失败");
    }
    const blob = await downloadResponse.blob();
    const downloadedFilename = filename || job.filename || "专项练习-题目.docx";
    downloadPracticeWord(blob, downloadedFilename);
    forgetPracticeWordExportPointer(exportKey);
    activePracticeWordExports.delete(exportKey);
    renderPracticeWordRecoveryNotice();
    syncPracticeWordExportUi();
    const reviewCandidate = job.release_level === "review_candidate";
    setPracticeStatusBanner(reviewCandidate ? "待复核题目 Word 已生成；可继续修改，但不要作为正式版发布。" : "题目 Word 已生成，浏览器已开始下载。", reviewCandidate ? "warning" : "done");
    await platformAlert(reviewCandidate ? "Word 已生成并标记为待复核；可用于查看和继续修改，复核通过后再正式使用。" : "题目 Word 已生成，浏览器已开始下载。", {
      title: reviewCandidate ? "已下载待复核 Word" : "题目 Word 已下载",
      tone: reviewCandidate ? "warning" : "success"
    });
  } catch (error) {
    setPracticeStatusBanner("题目 Word 生成失败，请查看提示后重试。", "error");
    window.setTimeout(() => resumeRememberedPracticeWordExports().catch(() => {}), 0);
    throw new Error(practiceWordRecoveryError({ error: String(error).replace(/^Error:\s*/, "") }));
  } finally {
    activePracticeWordExports.delete(exportKey);
    syncPracticeWordExportUi();
  }
}

function selectedPracticeSet() {
  const checkedIndexes = Array.from(document.querySelectorAll('input[data-practice-select]:checked'))
    .map((input) => Number(input.dataset.practiceSelect))
    .filter((index) => Number.isInteger(index) && index >= 0);
  selectedPracticeExerciseIndexes.clear();
  checkedIndexes.forEach((index) => selectedPracticeExerciseIndexes.add(index));
  const indexes = [...selectedPracticeExerciseIndexes].sort((a, b) => a - b);
  const exercises = indexes
    .map((index) => latestPracticeSet?.exercises?.[index]).filter((item) => item && item.generation_status !== "failed");
  const selectedIds = indexes
    .map((index) => latestPracticeSet?.exercises?.[index])
    .filter((item) => item && item.generation_status !== "failed")
    .map(practiceExerciseExportId);
  return exercises.length && latestPracticeSet ? {
    ...latestPracticeSet,
    exercises,
    export_scope: "selected",
    selected_exercise_ids: selectedIds
  } : null;
}

function updatePracticeSelectionActions() {
  const data = selectedPracticeSet();
  const selectedIndexes = [...selectedPracticeExerciseIndexes]
    .filter((index) => latestPracticeSet?.exercises?.[index]);
  const selectedCount = selectedIndexes.length;
  const exportableCount = data?.exercises?.length || 0;
  const total = (latestPracticeSet?.exercises || []).length;
  $("practiceSelectionActions")?.classList.toggle("hidden", total === 0);
  setText("practiceSelectionCount", `已选 ${selectedCount} 题`);
  const selectAllButton = $("practiceSelectAllBtn");
  if (selectAllButton) {
    selectAllButton.disabled = total === 0 || selectedCount === total;
    selectAllButton.title = selectedCount === total ? "已全选全部题目" : "全选全部题目";
  }
  syncPracticeWordExportButton(
    $("practiceDownloadSelectedBtn"),
    data ? practiceWordExportKey(data, `专项练习-已选${exportableCount}题.docx`) : "",
    exportableCount > 0,
    "下载 Word"
  );
  if ($("practiceClearSelectedBtn")) $("practiceClearSelectedBtn").disabled = selectedCount === 0;
  const regenerateButton = $("practiceRegenerateSetBtn");
  if (regenerateButton) {
    regenerateButton.disabled = selectedCount === 0 || practiceRegenerationInProgress;
    regenerateButton.title = selectedCount ? `重新生成已选 ${selectedCount} 题` : "请先选择题目";
    regenerateButton.setAttribute("aria-label", regenerateButton.title);
  }
}

function setPracticeExportButtonsEnabled(enabled, data = latestPracticeSet) {
  // Result-level actions are intentionally scoped to selected exercises.
  // A single failed question must not prevent exporting another selected one.
  updatePracticeSelectionActions();
}

async function loadEnvironmentStatus() {
  setEnvironmentChecking();
  $("environmentBox").textContent = "环境检查中...";
  const [env] = await Promise.all([
    api("/api/environment"),
    new Promise((resolve) => setTimeout(resolve, 500))
  ]);
  updateEnvironmentSummary(env);
  $("environmentBox").textContent = pretty(env);
  return env;
}

async function loadLibraryFiles() {
  libraryFiles = await api("/api/library-files");
  renderLibraryFiles();
}

function sharedLibraryUrl() {
  return String($("sharedLibraryUrl")?.value || "").trim().replace(/\/$/, "");
}

function sharedLibraryVersionLabel(item) {
  const pieces = [
    item.version || "未命名版本",
    `${Number(item.textbook_count || 0)} 本教材`,
    `${Number(item.block_count || 0)} 个片段`
  ];
  if (item.package_size) pieces.push(formatBytes(item.package_size));
  return pieces.join(" · ");
}

function renderSharedLibraryCatalog(data) {
  const container = $("sharedLibraryCatalog");
  if (!container) return;
  const libraries = Array.isArray(data?.libraries) ? data.libraries : [];
  if (!libraries.length) {
    container.innerHTML = '<p class="empty-hint">教材库当前没有已发布的教材版本。</p>';
    return;
  }
  container.innerHTML = "";
  for (const library of libraries) {
    const versions = Array.isArray(library.versions) ? library.versions : [];
    for (const release of versions) {
      const item = document.createElement("div");
      item.className = "shared-library-item";
      item.innerHTML = `
        <span>
          <strong>${escapeHtml(library.title || library.library_id || "未命名教材")}</strong>
          <small>${escapeHtml(sharedLibraryVersionLabel(release))}</small>
        </span>
        <button class="secondary-button" type="button"><i class="fas fa-download"></i>下载到本机</button>
      `;
      const button = item.querySelector("button");
      button?.addEventListener("click", () => syncSharedLibraryVersion(library.library_id, release.version, button));
      container.appendChild(item);
    }
  }
}

async function loadSharedLibrarySettings() {
  const data = await api("/api/shared-textbook-library/settings");
  if ($("sharedLibraryUrl") && document.activeElement !== $("sharedLibraryUrl")) {
    $("sharedLibraryUrl").value = data.remote_url || "";
  }
  if (data.remote_url) await refreshSharedLibraryCatalog(data.remote_url);
  return data;
}

async function saveSharedLibrarySettings() {
  const remoteUrl = sharedLibraryUrl();
  const data = await api("/api/shared-textbook-library/settings", {
    method: "POST",
    body: JSON.stringify({ remote_url: remoteUrl })
  });
  $("sharedLibraryUrl").value = data.remote_url || "";
  setVisual("libraryVisualResult", "教材库已连接", data.remote_url || "已清除教材库地址。", "ok");
  if (data.remote_url) await refreshSharedLibraryCatalog(data.remote_url);
}

async function refreshSharedLibraryCatalog(remoteUrl = sharedLibraryUrl()) {
  const container = $("sharedLibraryCatalog");
  if (!remoteUrl) {
    if (container) container.innerHTML = '<p class="empty-hint">填写并连接教材库地址后，可查看已发布教材。</p>';
    return;
  }
  if (container) container.innerHTML = '<p class="empty-hint">正在读取共享教材库目录...</p>';
  try {
    const data = await api("/api/shared-textbook-library/remote-catalog", {
      method: "POST",
      body: JSON.stringify({ remote_url: remoteUrl })
    });
    renderSharedLibraryCatalog(data);
  } catch (err) {
    if (container) container.innerHTML = `<p class="empty-hint">无法连接教材库：${escapeHtml(String(err).replace(/^Error:\s*/, ""))}</p>`;
  }
}

async function syncSharedLibraryVersion(libraryId, version, button) {
  const previous = button?.innerHTML || "";
  try {
    if (button) {
      button.disabled = true;
      button.innerHTML = '<i class="fas fa-circle-notch fa-spin"></i>下载中';
    }
    const data = await api("/api/shared-textbook-library/sync", {
      method: "POST",
      body: JSON.stringify({ library_id: libraryId, version, remote_url: sharedLibraryUrl() })
    });
    selectedTextbookPaths = new Set(data.selected_textbooks || []);
    await loadLibraryFiles();
    setVisual("libraryVisualResult", "共享教材已安装", `${data.title || libraryId} ${version} 已下载到本机并可用于新任务。`, "ok");
    $("libraryResult").textContent = pretty(data);
  } catch (err) {
    setVisual("libraryVisualResult", "共享教材下载失败", String(err).replace(/^Error:\s*/, ""), "error");
  } finally {
    if (button) {
      button.disabled = false;
      button.innerHTML = previous;
    }
  }
}

async function publishSharedLibrary() {
  const selected = selectedTextbooks();
  if (!selected.length) throw new Error("请先选择需要发布且已建立索引的教材。");
  const button = $("publishSharedLibraryBtn");
  const previous = button?.innerHTML || "";
  try {
    if (button) {
      button.disabled = true;
      button.innerHTML = '<i class="fas fa-circle-notch fa-spin"></i>正在发布';
    }
    const data = await api("/api/shared-textbook-library/publish", {
      method: "POST",
      body: JSON.stringify({
        selected_textbooks: selected,
        textbook_display_names: selectedTextbookDisplayNames(),
        library_id: $("sharedLibraryId")?.value.trim() || "",
        title: $("sharedLibraryTitle")?.value.trim() || "",
        version: $("sharedLibraryVersion")?.value.trim() || ""
      })
    });
    setVisual("libraryVisualResult", "共享教材已发布", `${data.title} ${data.version} 已打包发布。`, "ok");
    $("libraryResult").textContent = pretty(data);
    if (sharedLibraryUrl()) await refreshSharedLibraryCatalog();
  } finally {
    if (button) {
      button.disabled = false;
      button.innerHTML = previous;
    }
  }
}

function renderLibraryFiles() {
  const examSelect = $("examSelect");
  const textbookChecklist = $("textbookChecklist");
  const taskTextbookChecklist = $("taskTextbookChecklist");
  const examCardList = $("examCardList");
  const previousExamPath = examSelect.value || $("examPath")?.value || "";
  examSelect.innerHTML = "";
  textbookChecklist.innerHTML = "";
  if (taskTextbookChecklist) taskTextbookChecklist.innerHTML = "";
  if (examCardList) examCardList.innerHTML = "";
  $("textbooksDir").value = libraryFiles.textbooks_root || "";
  const textbookPaths = (libraryFiles.textbooks || []).map((file) => file.path);
  if (!textbookSelectionInitialized && textbookPaths.length) {
    selectedTextbookPaths = new Set();
    textbookSelectionInitialized = true;
  } else {
    selectedTextbookPaths = new Set(Array.from(selectedTextbookPaths).filter((path) => textbookPaths.includes(path)));
  }

  if (!(libraryFiles.exams || []).length) {
    const option = document.createElement("option");
    option.value = "";
    option.textContent = "还没有真题文件，请先上传 DOCX";
    examSelect.appendChild(option);
    $("examPath").value = "";
  } else {
    const placeholder = document.createElement("option");
    placeholder.value = "";
    placeholder.textContent = "请选择一份真题";
    examSelect.appendChild(placeholder);
    for (const [index, file] of (libraryFiles.exams || []).entries()) {
      const option = document.createElement("option");
      option.value = file.path;
      option.textContent = fileLabel(file);
      examSelect.appendChild(option);
      if (examCardList) {
        const card = document.createElement("button");
        card.type = "button";
        card.className = "exam-card";
        card.dataset.path = file.path;
        card.innerHTML = `
          <span class="library-delete-icon" data-action="delete-file" title="删除"><i class="fas fa-trash"></i></span>
          <span class="file-thumb"><i class="fas fa-file-alt"></i></span>
          <span class="file-meta">
            <strong>${escapeHtml(file.name)}</strong>
            <small>${escapeHtml(formatBytes(file.size || 0))}</small>
            <small>点击选择该真题</small>
          </span>
        `;
        card.addEventListener("click", () => {
          selectExamFile(file.path);
          setVisual("taskVisualResult", "已选择真题", file.name, "info");
        });
        card.querySelector("[data-action='delete-file']")?.addEventListener("click", async (event) => {
          event.preventDefault();
          event.stopPropagation();
          await deleteLibraryFile("exam", file.path, file.name);
        });
        examCardList.appendChild(card);
      }
    }
    const preservedExamPath = (libraryFiles.exams || []).some((file) => file.path === previousExamPath) ? previousExamPath : "";
    selectExamFile(preservedExamPath);
  }

  if (!(libraryFiles.textbooks || []).length) {
    const empty = document.createElement("p");
    empty.className = "empty-hint";
    empty.textContent = "还没有教材文件，请先上传教材。";
    textbookChecklist.appendChild(empty);
    if (taskTextbookChecklist) {
      const taskEmpty = document.createElement("p");
      taskEmpty.className = "empty-hint";
      taskEmpty.textContent = "还没有可选教材。请先通过右上角“教材管理”上传教材并建立索引。";
      taskTextbookChecklist.appendChild(taskEmpty);
    }
  } else {
    renderTextbookMergeBox();
    for (const [index, item] of buildTextbookDisplayItems().entries()) {
      textbookChecklist.appendChild(renderTextbookCard(item, index, { allowDelete: true }));
      if (taskTextbookChecklist) {
        taskTextbookChecklist.appendChild(renderTextbookCard(item, index, { allowDelete: false }));
      }
    }
  }
  updateSelectedTextbookBar();
  updateCreateTaskAvailability();
  renderDuplicateReview(libraryFiles.duplicate_review);
  const duplicateIssues = Number(libraryFiles.duplicate_review?.issue_count || 0);
  setVisual(
    "libraryVisualResult",
    duplicateIssues ? "文件库需要确认" : "文件库已扫描",
    `发现 ${((libraryFiles.exams || []).length)} 份真题、${((libraryFiles.textbooks || []).length)} 份教材。${duplicateIssues ? `有 ${duplicateIssues} 个疑似重复项，请先确认勾选范围。` : "未发现重复文件。"} `,
    duplicateIssues ? "warn" : "ok"
  );
  $("libraryResult").textContent = pretty({
    真题数量: (libraryFiles.exams || []).length,
    教材数量: (libraryFiles.textbooks || []).length,
    重复审查: libraryFiles.duplicate_review || null
  });
}

function localTextbookGroupSuggestions() {
  const groups = {};
  for (const file of libraryFiles.textbooks || []) {
    const key = groupCandidateName(file.name);
    if (!key) continue;
    if (!groups[key]) groups[key] = [];
    groups[key].push(file);
  }
  return Object.entries(groups)
    .filter(([key, files]) => files.length > 1 && key.length >= 4)
    .map(([key, files]) => ({
      key,
      files,
      name: activeTextbookGroups[key]?.name || key,
      confidence: "medium",
      reason: "文件名主体相同"
    }));
}

function textbookGroupSuggestions() {
  const filesByPath = new Map((libraryFiles.textbooks || []).map((file) => [file.path, file]));
  const detected = Array.isArray(libraryFiles.textbook_groups) ? libraryFiles.textbook_groups : [];
  const suggestions = detected
    .map((group) => {
      const files = (group.files || []).map((file) => filesByPath.get(file.path) || file).filter((file) => file?.path);
      return {
        key: String(group.key || "").trim(),
        files,
        name: activeTextbookGroups[group.key]?.name || String(group.name || "").trim(),
        confidence: String(group.confidence || "medium"),
        reason: String(group.reason || "自动识别为同一套教材")
      };
    })
    .filter((group) => group.key && group.name && group.files.length > 1);
  return suggestions.length ? suggestions : localTextbookGroupSuggestions();
}

function enableDefaultTextbookGroups(suggestions) {
  for (const suggestion of suggestions) {
    if (disabledTextbookGroupKeys.has(suggestion.key)) continue;
    if (!activeTextbookGroups[suggestion.key]) {
      activeTextbookGroups[suggestion.key] = { name: suggestion.name || suggestion.key };
    }
  }
}

function renderTextbookMergeBox() {
  const box = $("textbookMergeBox");
  if (!box) return;
  const suggestions = textbookGroupSuggestions();
  enableDefaultTextbookGroups(suggestions);
  box.innerHTML = "";
  box.classList.toggle("hidden", !suggestions.length);
  if (!suggestions.length) return;
  const title = document.createElement("strong");
  title.textContent = "自动识别为同一套教材";
  const note = document.createElement("p");
  note.textContent = "识别结果会作为一本教材统一勾选、索引和引用。可修改展示名称，或取消汇总后按单独文件处理。";
  box.append(title, note);
  for (const suggestion of suggestions) {
    const row = document.createElement("div");
    row.className = "merge-row";
    const input = document.createElement("input");
    input.value = suggestion.name;
    input.placeholder = "展示名称";
    input.addEventListener("input", () => {
      if (activeTextbookGroups[suggestion.key]) {
        activeTextbookGroups[suggestion.key].name = input.value.trim() || suggestion.key;
      }
    });
    const names = document.createElement("span");
    const confidence = suggestion.confidence === "high" ? "高置信" : "建议确认";
    names.textContent = `${confidence} · ${suggestion.reason} · ${suggestion.files.length} 个部分：${suggestion.files.map((file) => displayBookName(file)).join("、")}`;
    const button = document.createElement("button");
    button.type = "button";
    button.className = activeTextbookGroups[suggestion.key] ? "secondary-button" : "outline-button";
    button.textContent = activeTextbookGroups[suggestion.key] ? "取消汇总" : "按一本书显示";
    button.addEventListener("click", () => {
      if (activeTextbookGroups[suggestion.key]) {
        delete activeTextbookGroups[suggestion.key];
        disabledTextbookGroupKeys.add(suggestion.key);
      } else {
        activeTextbookGroups[suggestion.key] = { name: input.value.trim() || suggestion.key };
        disabledTextbookGroupKeys.delete(suggestion.key);
      }
      renderLibraryFiles();
    });
    row.append(input, names, button);
    box.appendChild(row);
  }
}

function buildTextbookDisplayItems() {
  const files = libraryFiles.textbooks || [];
  const activeKeys = new Set(Object.keys(activeTextbookGroups));
  const consumed = new Set();
  const items = [];
  const suggestions = textbookGroupSuggestions();
  for (const suggestion of suggestions) {
    if (!activeKeys.has(suggestion.key)) continue;
    const paths = suggestion.files.map((file) => file.path);
    paths.forEach((path) => consumed.add(path));
    items.push({
      type: "group",
      key: suggestion.key,
      name: activeTextbookGroups[suggestion.key]?.name || suggestion.name,
      files: suggestion.files,
      paths,
      size: suggestion.files.reduce((sum, file) => sum + Number(file.size || 0), 0)
    });
  }
  for (const file of files) {
    if (!consumed.has(file.path)) {
      items.push({ type: "file", name: displayBookName(file), files: [file], paths: [file.path], size: file.size || 0 });
    }
  }
  return items;
}

function selectedTextbookDisplayNames() {
  const selected = new Set(selectedTextbooks());
  const mapping = {};
  for (const file of libraryFiles.textbooks || []) {
    if (!selected.has(file.path)) continue;
    const citationName = String(file.citation_textbook || "").trim();
    if (citationName) mapping[file.path] = citationName;
  }
  for (const item of buildTextbookDisplayItems()) {
    const name = String(item.name || "").trim();
    if (!name) continue;
    for (const path of item.paths || []) {
      if (selected.has(path) && !mapping[path]) mapping[path] = name;
    }
  }
  return mapping;
}

function textbookFormatStatus(item) {
  const names = (item.files || []).map((file) => String(file.name || file.path || "").toLowerCase());
  if (names.some((name) => name.endsWith(".zip"))) {
    return {
      className: "recommended",
      label: "推荐格式",
      detail: "MinerU ZIP：支持图片、表格、公式资源定位。"
    };
  }
  if (names.some((name) => name.endsWith(".json"))) {
    return {
      className: "limited",
      label: "旧 JSON 可用",
      detail: "可做文字检索；通常缺少图片文件，视觉证据能力受限。"
    };
  }
  if (names.some((name) => /\.(pdf|docx?|md|txt)$/i.test(name))) {
    return {
      className: "limited",
      label: "文字格式可用",
      detail: "可抽取文字建立索引；复杂图表和版面结构不完整。"
    };
  }
  return {
    className: "limited",
    label: "兼容格式",
    detail: "可尝试建立文字索引；完整性需以索引结果为准。"
  };
}

function textbookIndexStatus(item) {
  const files = item.files || [];
  const indexedCount = files.filter((file) => Boolean(file.index_status?.indexed)).length;
  if (files.length && indexedCount === files.length) {
    return {
      className: "ready",
      label: "索引已就绪",
      detail: item.type === "group" ? `${indexedCount} 个部分均已纳入可复用索引。` : "已纳入可复用索引，可直接创建解析任务。"
    };
  }
  if (indexedCount) {
    return {
      className: "partial",
      label: "部分已索引",
      detail: `${indexedCount}/${files.length} 个部分已纳入索引；请补建其余部分。`
    };
  }
  return {
    className: "pending",
    label: "未建立索引",
    detail: "需先建立索引，才能用于创建解析任务。"
  };
}

function renderTextbookCard(item, index, options = {}) {
  const allowDelete = options.allowDelete !== false;
  const label = document.createElement("label");
  label.className = `library-option ${item.type === "group" ? "group-option" : ""}`;
  const checked = item.paths.every((path) => selectedTextbookPaths.has(path));
  const checkbox = document.createElement("input");
  checkbox.type = "checkbox";
  checkbox.checked = checked;
  checkbox.id = `textbookItem_${index}`;
  const thumb = document.createElement("span");
  thumb.className = `file-thumb book-color-${index % 6}`;
  thumb.innerHTML = '<i class="fas fa-book"></i>';
  const text = document.createElement("span");
  text.className = "file-meta";
  const partText = item.type === "group" ? `${item.files.length} 个部分` : "教材文件";
  const formatStatus = textbookFormatStatus(item);
  const indexStatus = textbookIndexStatus(item);
  text.innerHTML = `
    <strong>${escapeHtml(item.name)}</strong>
    <small>${escapeHtml(partText)} · ${escapeHtml(formatBytes(item.size || 0))}</small>
    <small class="textbook-format-status">
      <em class="textbook-format-badge ${escapeHtml(formatStatus.className)}">${escapeHtml(formatStatus.label)}</em>
      ${escapeHtml(formatStatus.detail)}
    </small>
    <small class="textbook-index-status">
      <em class="textbook-index-badge ${escapeHtml(indexStatus.className)}">${escapeHtml(indexStatus.label)}</em>
      ${escapeHtml(indexStatus.detail)}
    </small>
    <small>${checked ? "已勾选为本次参考教材" : "点击选择该教材"}</small>
  `;
  const deleteButton = document.createElement("button");
  deleteButton.type = "button";
  deleteButton.className = "library-delete-icon";
  deleteButton.title = "删除";
  deleteButton.innerHTML = '<i class="fas fa-trash"></i>';
  if (allowDelete) label.append(checkbox, deleteButton, thumb, text);
  else label.append(checkbox, thumb, text);
  label.classList.toggle("selected", checked);
  checkbox.addEventListener("change", () => {
    for (const path of item.paths) {
      if (checkbox.checked) selectedTextbookPaths.add(path);
      else selectedTextbookPaths.delete(path);
    }
    renderLibraryFiles();
  });
  if (allowDelete) {
    deleteButton.addEventListener("click", async (event) => {
      event.preventDefault();
      event.stopPropagation();
      await deleteLibraryFile("textbook", item.paths, item.name);
    });
  }
  return label;
}

function updateSelectedTextbookBar() {
  const box = $("selectedTextbookBar");
  const current = $("currentTextbookDisplay");
  const names = selectedTextbookNames();
  const preview = names.slice(0, 3).join("、");
  const detail = names.length > 3 ? `${preview} 等 ${names.length} 本` : preview;
  const text = names.length ? `已选择 ${names.length} 本教材` : "尚未选择教材";
  if (box) {
    box.classList.toggle("hidden", !names.length);
    box.innerHTML = `<span><i class="fas fa-check-circle"></i><strong>${escapeHtml(text)}</strong><small>${escapeHtml(detail)}</small></span><button class="text-button" type="button" onclick="clearTextbookSelection()">清空选择</button>`;
  }
  if (current) current.textContent = names.length ? `当前教材：${text}（${detail}）` : "当前教材：尚未选择，请至少勾选一本已建立索引的教材。";
  updateCreateTaskAvailability();
}

function selectedTextbookNames() {
  const selected = selectedTextbooks();
  const displayNames = selectedTextbookDisplayNames();
  const seen = new Set();
  return selected
    .map((path) => displayNames[path] || displayBookName((libraryFiles.textbooks || []).find((file) => file.path === path)?.name || shortName(path)))
    .filter(Boolean)
    .filter((name) => {
      if (seen.has(name)) return false;
      seen.add(name);
      return true;
    });
}

function clearTextbookSelection() {
  selectedTextbookPaths.clear();
  renderLibraryFiles();
  updateSelectedTextbookBar();
}

function renderDuplicateReview(review) {
  const box = $("duplicateReviewBox");
  if (!box) return;
  const issues = Array.isArray(review?.issues) ? review.issues : [];
  if (!issues.length) {
    box.className = "helper-box ok-box";
    box.innerHTML = "<strong>重复文件审查</strong><p>未发现同名相似或内容完全相同的真题/教材文件。</p>";
    return;
  }
  box.className = "helper-box warning-box";
  const list = document.createElement("ul");
  for (const issue of issues) {
    const item = document.createElement("li");
    const names = (issue.files || []).map((file) => file.name).join("、");
    item.textContent = `${issue.scope}：${issue.reason}：${names}`;
    list.appendChild(item);
  }
  box.innerHTML = "<strong>重复文件审查</strong><p>发现可能重复的文件，建议创建任务前确认只勾选需要的版本。</p>";
  box.appendChild(list);
}

function selectedTextbooks() {
  return Array.from(selectedTextbookPaths).filter(Boolean);
}

function selectExamFile(path) {
  const value = String(path || "");
  if ($("examSelect")) $("examSelect").value = value;
  if ($("examPath")) $("examPath").value = value;
  document.querySelectorAll(".exam-card").forEach((card) => {
    card.classList.toggle("selected", card.dataset.path === value);
  });
  updateCreateTaskAvailability();
}

function updateCreateTaskAvailability() {
  const button = $("createTaskBtn");
  if (!button) return;
  const examPath = $("examSelect")?.value || $("examPath")?.value || "";
  const textbookCount = selectedTextbooks().length;
  const ready = Boolean(examPath && textbookCount);
  button.disabled = !ready;
  button.setAttribute("aria-disabled", ready ? "false" : "true");
  button.title = ready ? "确认本次解析范围后开始" : !examPath ? "请先选择一份真题" : "请至少选择一本教材";
}

async function prepareTextbookIndex() {
  const selected = selectedTextbooks();
  $("textbookIndexBox").className = "helper-box";
  $("textbookIndexBox").innerHTML = "<strong>教材索引状态</strong><p>正在建立教材索引，请稍等。</p>";
  try {
    if (!selected.length) throw new Error("请先至少勾选一本教材");
    const data = await api("/api/textbook-index/prepare", {
      method: "POST",
      body: JSON.stringify({
        selected_textbooks: selected,
        textbook_display_names: selectedTextbookDisplayNames()
      })
    });
    $("textbookIndexBox").className = "helper-box ok-box";
    $("textbookIndexBox").innerHTML = `<strong>${data.cached ? "已建立索引可复用" : "教材索引已建立"}</strong><p>已处理 ${data.textbook_count} 本教材，形成 ${data.block_count} 个可检索片段。后续解析任务只会复用这份索引，不会在任务中重新建立索引。</p>`;
    $("libraryResult").textContent = pretty(data);
    await loadLibraryFiles();
  } catch (err) {
    $("textbookIndexBox").className = "helper-box warning-box";
    $("textbookIndexBox").innerHTML = `<strong>教材索引未完成</strong><p>${escapeHtml(String(err).replace(/^Error:\s*/, ""))}</p>`;
  }
}

async function prepareUploadedTextbookIndex() {
  const selected = selectedTextbooks();
  const primary = $("textbookPrimaryActionBtn");
  const { list } = uploadElements("textbook");
  try {
    if (!selected.length) throw new Error("没有找到刚上传的教材，请重新上传或切到“选择已有教材”手动选择。");
    if (primary) {
      primary.disabled = true;
      primary.innerHTML = '正在建立索引<i class="fas fa-circle-notch fa-spin"></i>';
    }
    setVisual("libraryVisualResult", "正在建立教材索引", "正在为刚上传的教材生成可复用索引。", "info");
    if (list) {
      list.classList.remove("hidden");
      list.innerHTML = `
        <div class="upload-complete-row">
          <i class="fas fa-circle-notch fa-spin"></i>
          <strong>正在建立教材索引</strong>
        </div>
      `;
    }
    const data = await api("/api/textbook-index/prepare", {
      method: "POST",
      body: JSON.stringify({
        selected_textbooks: selected,
        textbook_display_names: selectedTextbookDisplayNames()
      })
    });
    setVisual("libraryVisualResult", "教材索引已建立", `已处理 ${data.textbook_count} 本教材，形成 ${data.block_count} 个可检索片段。`, "ok");
    $("libraryResult").textContent = pretty(data);
    await loadLibraryFiles();
    if (list) {
      list.innerHTML = `
        <div class="upload-complete-row">
          <i class="fas fa-check-circle"></i>
          <strong>教材索引已建立，可用于解析任务</strong>
        </div>
      `;
    }
    if (primary) {
      primary.disabled = false;
      primary.innerHTML = '去创建解析任务<i class="fas fa-arrow-right"></i>';
      primary.onclick = () => goToPage("exam");
    }
  } catch (err) {
    const message = String(err).replace(/^Error:\s*/, "");
    setVisual("libraryVisualResult", "教材索引未完成", message, "error");
    $("libraryResult").textContent = `建立索引失败：${message}`;
    if (list) {
      list.classList.remove("hidden");
      list.innerHTML = `
        <div class="upload-complete-row upload-complete-error">
          <i class="fas fa-triangle-exclamation"></i>
          <strong>教材索引未完成</strong>
        </div>
      `;
    }
    if (primary) {
      primary.disabled = false;
      primary.innerHTML = '重试建立索引<i class="fas fa-arrow-right"></i>';
      primary.onclick = () => prepareUploadedTextbookIndex();
    }
  }
}

async function requirePreparedTextbookIndex() {
  const selected = selectedTextbooks();
  if (!selected.length) throw new Error("请先在教材页选择已建立索引的教材");
  const data = await api("/api/textbook-index/status", {
    method: "POST",
    body: JSON.stringify({
      selected_textbooks: selected,
      textbook_display_names: selectedTextbookDisplayNames()
    })
  });
  if (!data.indexed) {
    throw new Error(data.message || "所选教材尚未建立索引，请先在教材页点击“建立教材索引”。");
  }
  if (data.page_map_ok === false) {
    throw new Error("所选教材索引存在页码问题，请先在教材页重建或校准索引。");
  }
  return data;
}

function uploadElements(kind) {
  return {
    input: kind === "exam" ? $("examUploadInput") : $("textbookUploadInput"),
    list: kind === "exam" ? $("examUploadList") : $("textbookUploadList"),
    button: kind === "exam" ? $("uploadExamBtn") : $("uploadTextbooksBtn")
  };
}

function renderUploadSelection(kind, progress = {}) {
  const { input, list } = uploadElements(kind);
  if (!input || !list) return;
  const files = Array.from(input.files || []);
  list.classList.toggle("hidden", files.length === 0);
  if (!files.length) {
    list.innerHTML = "";
    return;
  }
  list.innerHTML = files
    .map((file, index) => {
      const itemProgress = progress[index] || {};
      const percent = Math.max(0, Math.min(100, Number(itemProgress.percent ?? 0)));
      const status = itemProgress.status || "ready";
      const label = status === "done" ? "已上传" : status === "uploading" ? `上传中 ${percent}%` : status === "error" ? "上传失败" : "等待上传";
      const canRemove = status === "ready" || status === "error";
      return `
        <div class="upload-file-row upload-${escapeHtml(status)}">
          <button class="upload-remove-button" type="button" data-index="${index}" title="移除" ${canRemove ? "" : "disabled"}><i class="fas fa-times"></i></button>
          <span class="upload-file-icon"><i class="fas fa-file-alt"></i></span>
          <span class="upload-file-meta">
            <strong>${escapeHtml(file.name)}</strong>
            <small>${escapeHtml(formatBytes(file.size || 0))} · ${escapeHtml(label)}</small>
            <span class="upload-progress-track"><span style="width: ${status === "ready" ? 0 : percent}%"></span></span>
          </span>
        </div>
      `;
    })
    .join("");
  list.querySelectorAll(".upload-remove-button").forEach((button) => {
    button.addEventListener("click", () => removePendingUpload(kind, Number(button.dataset.index)));
  });
}

function resetUploadFeedback(kind) {
  const { input } = uploadElements(kind);
  const count = Array.from(input?.files || []).length;
  if (kind === "exam") {
    $("taskResult").textContent = count ? `已选择 ${count} 个待上传真题。` : "等待选择真题 DOCX 文件。";
    setVisual(
      "taskVisualResult",
      count ? "待上传真题已更新" : "等待选择真题",
      count ? "确认文件无误后点击上传真题。" : "请选择一个 DOCX 文件后再上传。",
      "info"
    );
    return;
  }
  $("libraryResult").textContent = count ? `已选择 ${count} 个待上传教材文件。` : "等待选择教材文件。";
  setVisual(
    "libraryVisualResult",
    count ? "待上传教材已更新" : "等待选择教材",
    count ? "确认文件无误后点击上传教材。" : "请选择教材文件后再上传。",
    "info"
  );
}

function removePendingUpload(kind, removeIndex) {
  const { input } = uploadElements(kind);
  if (!input) return;
  const files = Array.from(input.files || []);
  const transfer = new DataTransfer();
  files.forEach((file, index) => {
    if (index !== removeIndex) transfer.items.add(file);
  });
  input.files = transfer.files;
  renderUploadSelection(kind);
  resetUploadFeedback(kind);
}

async function deleteLibraryFile(kind, paths, label) {
  const pathList = Array.isArray(paths) ? paths : [paths];
  const validPaths = pathList.filter(Boolean);
  if (!validPaths.length) return;
  const message = kind === "exam"
    ? `确定删除真题“${label}”吗？`
    : `确定删除教材“${label}”吗？${validPaths.length > 1 ? `这会删除 ${validPaths.length} 个文件。` : ""}`;
  if (!await platformConfirm({
    eyebrow: "文件管理",
    title: kind === "exam" ? "删除真题" : "删除教材",
    message,
    confirmText: "确认删除",
    tone: "danger"
  })) return;
  try {
    for (const path of validPaths) {
      await api("/api/library-delete", {
        method: "POST",
        body: JSON.stringify({ kind, path })
      });
      if (kind === "textbook") selectedTextbookPaths.delete(path);
      if (kind === "exam" && $("examPath")?.value === path) $("examPath").value = "";
    }
    await loadLibraryFiles();
    if (kind === "exam") {
      setVisual("taskVisualResult", "真题已删除", label, "ok");
    } else {
      setVisual("libraryVisualResult", "教材已删除", label, "ok");
    }
  } catch (err) {
    const messageText = String(err).replace(/^Error:\s*/, "");
    if (kind === "exam") setVisual("taskVisualResult", "真题删除失败", messageText, "error");
    else setVisual("libraryVisualResult", "教材删除失败", messageText, "error");
  }
}

function uploadFileWithProgress(kind, file, index, progress) {
  return new Promise((resolve, reject) => {
    const url = `/api/library-upload?kind=${encodeURIComponent(kind)}&filename=${encodeURIComponent(file.name)}`;
    const xhr = new XMLHttpRequest();
    xhr.open("POST", url);
    xhr.setRequestHeader("Content-Type", "application/octet-stream");
    progress[index] = { status: "uploading", percent: 0 };
    renderUploadSelection(kind, progress);
    xhr.upload.addEventListener("progress", (event) => {
      if (!event.lengthComputable) return;
      progress[index] = { status: "uploading", percent: Math.round((event.loaded / event.total) * 100) };
      renderUploadSelection(kind, progress);
    });
    xhr.addEventListener("load", () => {
      let data = {};
      try {
        data = xhr.responseText ? JSON.parse(xhr.responseText) : {};
      } catch (err) {
        data = { error: "服务器返回内容无法解析" };
      }
      if (xhr.status >= 200 && xhr.status < 300) {
        progress[index] = { status: "done", percent: 100 };
        renderUploadSelection(kind, progress);
        resolve(data.file);
      } else {
        progress[index] = { status: "error", percent: 100 };
        renderUploadSelection(kind, progress);
        reject(new Error(data.error || xhr.statusText || "上传失败"));
      }
    });
    xhr.addEventListener("error", () => {
      progress[index] = { status: "error", percent: 100 };
      renderUploadSelection(kind, progress);
      reject(new Error("网络连接中断，上传失败"));
    });
    xhr.send(file);
  });
}

function uploadedFilePath(file) {
  if (typeof file === "string") return file;
  if (file && typeof file === "object") return String(file.path || "");
  return "";
}

function setupUploadInput(kind) {
  const { input } = uploadElements(kind);
  if (!input) return;
  const zone = input.closest(".upload-zone");
  input.addEventListener("change", () => {
    renderUploadSelection(kind);
    resetUploadFeedback(kind);
  });
  if (!zone) return;
  ["dragenter", "dragover"].forEach((eventName) => {
    zone.addEventListener(eventName, (event) => {
      event.preventDefault();
      zone.classList.add("dragover");
    });
  });
  ["dragleave", "drop"].forEach((eventName) => {
    zone.addEventListener(eventName, (event) => {
      event.preventDefault();
      zone.classList.remove("dragover");
    });
  });
  zone.addEventListener("drop", (event) => {
    const files = Array.from(event.dataTransfer?.files || []);
    if (!files.length) return;
    const transfer = new DataTransfer();
    const allowed = kind === "exam" ? [".docx"] : [".pdf", ".doc", ".docx", ".json", ".md", ".txt", ".zip"];
    for (const file of files) {
      const lower = file.name.toLowerCase();
      if (allowed.some((suffix) => lower.endsWith(suffix))) transfer.items.add(file);
      if (kind === "exam" && transfer.files.length) break;
    }
    input.files = transfer.files;
    renderUploadSelection(kind);
    resetUploadFeedback(kind);
  });
}

async function uploadLibraryFiles(kind) {
  const input = kind === "exam" ? $("examUploadInput") : $("textbookUploadInput");
  const files = Array.from(input.files || []);
  if (!files.length) throw new Error(kind === "exam" ? "请先选择一个真题 DOCX 文件" : "请先选择教材文件");
  const uploaded = [];
  const progress = {};
  const { button, list } = uploadElements(kind);
  if (button) button.disabled = true;
  try {
    for (const [index, file] of files.entries()) {
      const uploadedFile = await uploadFileWithProgress(kind, file, index, progress);
      const uploadedPath = uploadedFilePath(uploadedFile);
      if (uploadedPath) uploaded.push(uploadedPath);
    }
  } finally {
    if (button) button.disabled = false;
  }
  input.value = "";
  await loadLibraryFiles();
  if (kind === "exam") {
    const latest = uploaded[uploaded.length - 1];
    if (latest) selectExamFile(latest);
    switchExamTab("existing");
  } else {
    selectedTextbookPaths = new Set(uploaded.filter(Boolean));
    renderLibraryFiles();
    switchTextbookTab("upload");
    showUploadIndexAction();
  }
  if (list) {
    list.classList.remove("hidden");
    list.innerHTML = `
      <div class="upload-complete-row">
        <i class="fas fa-check-circle"></i>
        <strong>${escapeHtml(kind === "exam" ? "真题已上传，已切回已有真题列表" : "教材已上传")}</strong>
      </div>
    `;
  }
  $("libraryResult").textContent = pretty({ ok: true, uploaded });
  return uploaded;
}

function currentProviderConfig() {
  return providerConfigs[$("providerSelect").value] || {};
}

function providerConfigBySelect(id, fallback = currentProviderConfig()) {
  const el = $(id);
  return providerConfigs[el?.value] || fallback || {};
}

function selectedVisionProviderConfig() {
  return providerConfigBySelect("visionProviderSelect", currentProviderConfig());
}

function selectedImageProviderConfig() {
  return providerConfigBySelect("imageProviderSelect", currentProviderConfig());
}

function providerHasVision(cfg = currentProviderConfig()) {
  return Boolean(cfg.supports_vision && cfg.vision_model);
}

function providerHasImageModel(cfg = currentProviderConfig()) {
  return Boolean(cfg.supports_image_generation !== false && cfg.image_model_set);
}

function providerCapabilityRiskMessages({ hasImages = false, hasDrawing = false } = {}) {
  const visionCfg = selectedVisionProviderConfig();
  const imageCfg = selectedImageProviderConfig();
  const messages = [];
  const visionLabel = displayProviderName(visionCfg.name || $("visionProviderSelect")?.value || "读图模型");
  const imageLabel = displayProviderName(imageCfg.name || $("imageProviderSelect")?.value || "生图模型");
  const answerCfg = selectedTextRoleProviderConfig("answer");
  const answerModel = selectedTextRoleModel("answer") || answerCfg.default_model || "";
  const answerReadsImagesDirectly = modelLooksVisionCapable(answerModel, answerCfg);
  if (hasImages && !answerReadsImagesDirectly && !providerHasVision(visionCfg)) {
    messages.push(`${visionLabel} 未配置多模态视觉模型；有图题无法可靠读图，请改选支持 vision_model 的多模态服务商/模型，或确认已通过其他方式完成图像结构化。`);
  }
  if (hasDrawing && !providerHasImageModel(imageCfg)) {
    messages.push(`${imageLabel} 未配置生图模型；作图题会优先走规则绘图，但规则绘图失败时无法使用生图模型兜底。`);
  }
  return messages;
}

function renderModelCapabilityRisk(target, messages) {
  if (!target) return;
  const list = Array.isArray(messages) ? messages.filter(Boolean) : [];
  target.classList.toggle("hidden", list.length === 0);
  target.innerHTML = list.length
    ? `<i class="fas fa-triangle-exclamation"></i>${list.map((item) => `<span>${escapeHtml(item)}</span>`).join("")}`
    : "";
}

function updateModelCapabilityRisk() {
  const cfg = currentProviderConfig();
  const visionCfg = selectedVisionProviderConfig();
  const imageCfg = selectedImageProviderConfig();
  const genericRisks = [];
  const providerLabel = displayProviderName(cfg.name || $("providerSelect")?.value || "当前模型");
  const visionLabel = displayProviderName(visionCfg.name || $("visionProviderSelect")?.value || "读图模型");
  const imageLabel = displayProviderName(imageCfg.name || $("imageProviderSelect")?.value || "生图模型");
  if (!providerHasVision(visionCfg) && !providerHasImageModel(imageCfg)) {
    genericRisks.push(`${providerLabel} 当前按纯文本能力配置；遇到有图题需另选多模态模型，遇到作图题需另选或配置生图模型，否则后续会标记风险。`);
  } else if (!providerHasVision(visionCfg)) {
    genericRisks.push(`${visionLabel} 当前未配置多模态视觉模型；有图题需要改选支持视觉的模型或服务商。`);
  } else if (!providerHasImageModel(imageCfg)) {
    genericRisks.push(`${imageLabel} 当前未配置生图模型；作图题规则绘图失败时无法兜底生图。`);
  }
  renderModelCapabilityRisk($("modelCapabilityRisk"), genericRisks);
}

function providerEntriesByCapability(kind) {
  const entries = Object.entries(providerConfigs || {});
  if (kind === "vision") return entries.filter(([, cfg]) => providerHasVision(cfg));
  if (kind === "image") return entries.filter(([, cfg]) => providerHasImageModel(cfg));
  return entries;
}

function modelLooksVisionCapable(model, cfg = currentProviderConfig()) {
  const value = String(model || "").trim();
  if (!value || !providerHasVision(cfg)) return false;
  const label = String((cfg.model_option_labels || {})[value] || "");
  const explicitCapabilities = Array.isArray((cfg.model_capabilities || {})[value])
    ? cfg.model_capabilities[value].map((capability) => String(capability).toLowerCase())
    : [];
  if (explicitCapabilities.length) {
    return explicitCapabilities.some((capability) => ["vision", "multimodal", "image_input"].includes(capability));
  }
  const combined = `${value} ${label}`.toLowerCase();
  return value === cfg.vision_model
    || combined.includes("vl")
    || combined.includes("vision")
    || combined.includes("ocr")
    || label.includes("多模态")
    || label.includes("视觉")
    || label.includes("识图")
    || label.includes("图像");
}

function populateProviderSelect(selectId, kind, preferredName) {
  const select = $(selectId);
  if (!select) return "";
  const entries = providerEntriesByCapability(kind);
  select.innerHTML = "";
  for (const [name, cfg] of entries) {
    const option = document.createElement("option");
    option.value = name;
    option.textContent = `${displayProviderName(name)}${cfg.api_key_set ? "（已保存Key）" : ""}`;
    select.appendChild(option);
  }
  const fallback = entries.find(([name]) => name === preferredName)?.[0]
    || entries.find(([, cfg]) => cfg.api_key_set)?.[0]
    || entries[0]?.[0]
    || "";
  if (fallback) select.value = fallback;
  return fallback;
}

function selectedTextRoleProviderConfig(roleKey) {
  const role = textModelRoles[roleKey];
  return role ? providerConfigBySelect(role.providerId, currentProviderConfig()) : currentProviderConfig();
}

function populateTextRoleModelSelect(roleKey, preferredModel = "") {
  const role = textModelRoles[roleKey];
  if (!role) return;
  const cfg = selectedTextRoleProviderConfig(roleKey);
  const select = $(role.modelSelectId);
  const input = $(role.modelInputId);
  if (!select || !input) return;
  const options = Array.isArray(cfg.model_options) ? cfg.model_options : [];
  const labels = cfg.model_option_labels || {};
  const allowCustom = Boolean(cfg.allow_custom_model);
  select.innerHTML = "";
  if (options.length) {
    if (allowCustom) {
      const placeholder = document.createElement("option");
      placeholder.value = "";
      placeholder.textContent = "选择模型，或在下方填写模型 ID";
      select.appendChild(placeholder);
    }
    for (const model of options) {
      const option = document.createElement("option");
      const label = labels[model] || model;
      option.value = model;
      option.textContent = model === cfg.default_model ? `${label} (默认)` : label;
      option.title = model;
      select.appendChild(option);
    }
    const preferred = String(preferredModel || "").trim();
    select.value = options.includes(preferred) ? preferred : (cfg.default_model || options[0] || "");
  } else {
    const option = document.createElement("option");
    option.value = cfg.default_model || "";
    option.textContent = cfg.default_model ? `${cfg.default_model} (默认)` : "默认模型";
    select.appendChild(option);
  }
  select.hidden = false;
  input.hidden = !allowCustom;
  input.value = "";
  input.placeholder = cfg.model_hint || "如需使用其他模型，请填写模型 ID";
  updateTextRoleHint(roleKey);
}

function selectedTextRoleModel(roleKey) {
  const role = textModelRoles[roleKey];
  if (!role) return "";
  const cfg = selectedTextRoleProviderConfig(roleKey);
  const customModel = Boolean(cfg.allow_custom_model) ? $(role.modelInputId)?.value.trim() : "";
  if (customModel) return customModel;
  return $(role.modelSelectId)?.value || cfg.default_model || "";
}

function updateTextRoleHint(roleKey) {
  const role = textModelRoles[roleKey];
  if (!role) return;
  const cfg = selectedTextRoleProviderConfig(roleKey);
  const hint = $(role.hintId);
  if (!hint) return;
  const providerName = displayProviderName(cfg.name || $(role.providerId)?.value || "当前服务商");
  hint.innerHTML = `<i class="fas ${role.icon}"></i><span title="${escapeHtml(role.label)}使用 ${escapeHtml(providerName)} / ${escapeHtml(selectedTextRoleModel(roleKey) || cfg.default_model || "默认模型")}">${escapeHtml(providerName)} / ${escapeHtml(selectedTextRoleModel(roleKey) || cfg.default_model || "默认模型")}</span>`;
  updateModelRoleCards();
}

function modelPill(providerName, modelName, tone = "blue") {
  const provider = String(providerName || "").trim();
  const model = String(modelName || "").trim();
  return `
    <span class="model-provider-pill ${tone}">${escapeHtml(displayProviderName(provider) || "未配置")}</span>
    <span class="model-name-pill">${escapeHtml(model || "未配置模型")}</span>
  `;
}

function modelConnectionTestKey(providerName, modelName) {
  const provider = String(providerName || "").trim().toLowerCase();
  const model = String(modelName || "").trim();
  return provider && model ? `${provider}::${model}` : "";
}

function loadStoredModelConnectionTests() {
  try {
    const stored = JSON.parse(localStorage.getItem("answerBook.modelConnectionTests.v1") || "{}");
    return stored && typeof stored === "object" ? stored : {};
  } catch (error) {
    return {};
  }
}

function persistModelConnectionTests() {
  try {
    localStorage.setItem("answerBook.modelConnectionTests.v1", JSON.stringify(modelConnectionTests));
  } catch (error) {
    // Private browsing may reject storage. The current-page evidence still works.
  }
}

function rememberModelConnectionTest(providerName, modelName, ok, error = "") {
  const key = modelConnectionTestKey(providerName, modelName);
  if (!key) return;
  modelConnectionTests[key] = {
    ok: Boolean(ok),
    error: String(error || "").slice(0, 300),
    testedAt: new Date().toISOString()
  };
  persistModelConnectionTests();
}

function routeConnectionStatus(route) {
  if (!route?.keySaved) {
    return { ok: false, tone: "warn", icon: "fa-triangle-exclamation", label: "未保存Key" };
  }
  if (!route?.capabilityOk) {
    return { ok: false, tone: "warn", icon: "fa-triangle-exclamation", label: "能力需检查" };
  }
  const tested = modelConnectionTests[modelConnectionTestKey(route.provider, route.model)];
  if (tested?.ok === true) {
    return { ok: true, tone: "ok", icon: "fa-circle", label: "可用" };
  }
  if (tested?.ok === false) {
    return { ok: false, tone: "warn", icon: "fa-circle-xmark", label: "不可用" };
  }
  return { ok: false, tone: "neutral", icon: "fa-circle-info", label: "Key已保存 · 未测试" };
}

function aggregateRouteStatus(routes) {
  const statuses = (routes || []).map(routeConnectionStatus);
  const firstWarn = statuses.find((status) => status.tone === "warn");
  if (firstWarn) return firstWarn;
  if (statuses.length && statuses.every((status) => status.ok)) {
    return { ok: true, tone: "ok", icon: "fa-circle", label: "可用" };
  }
  return { ok: false, tone: "neutral", icon: "fa-circle-info", label: "Key已保存 · 未测试" };
}

function textRoleRoute(roleKey, label) {
  const role = textModelRoles[roleKey];
  const cfg = selectedTextRoleProviderConfig(roleKey);
  const model = selectedTextRoleModel(roleKey) || cfg.default_model || "";
  return {
    label,
    provider: cfg.name || $(role.providerId)?.value || $("providerSelect")?.value || "",
    model,
    tone: roleKey === "answer" ? "purple" : "blue",
    capabilityOk: Boolean(model),
    keySaved: Boolean(cfg.api_key_set)
  };
}

function visionRoute() {
  const cfg = selectedVisionProviderConfig();
  const model = selectedVisionModel() || cfg.vision_model || "";
  return {
    label: "读图理解模型",
    provider: cfg.name || $("visionProviderSelect")?.value || "",
    model,
    tone: "orange",
    capabilityOk: providerHasVision(cfg) && Boolean(model),
    keySaved: Boolean(cfg.api_key_set)
  };
}

function imageRoute() {
  const cfg = selectedImageProviderConfig();
  const model = selectedImageModel() || cfg.image_model || "";
  return {
    label: "生图模型",
    provider: cfg.name || $("imageProviderSelect")?.value || "",
    model,
    tone: "orange",
    capabilityOk: providerHasImageModel(cfg) && Boolean(model),
    keySaved: Boolean(cfg.api_key_set)
  };
}

function setModelRoleStatus(elementId, state) {
  const target = $(elementId);
  if (!target) return;
  const resolved = state && typeof state === "object"
    ? state
    : { ok: Boolean(state), tone: state ? "ok" : "warn", icon: state ? "fa-circle" : "fa-triangle-exclamation", label: state ? "可用" : "需检查" };
  target.classList.toggle("ok", resolved.tone === "ok");
  target.classList.toggle("warn", resolved.tone === "warn");
  target.classList.toggle("neutral", resolved.tone === "neutral");
  target.innerHTML = `<i class="fas ${resolved.icon}"></i>${escapeHtml(resolved.label)}`;
}

function updateModelRoleCards() {
  setModelRoleStatus("reasoningRoleStatus", routeConnectionStatus(textRoleRoute("reasoning", "")));
  setModelRoleStatus("answerRoleStatus", routeConnectionStatus(textRoleRoute("answer", "")));
  setModelRoleStatus("correctnessRoleStatus", routeConnectionStatus(textRoleRoute("correctness", "")));
  setModelRoleStatus("visionRoleStatus", routeConnectionStatus(visionRoute()));
  setModelRoleStatus("imageRoleStatus", routeConnectionStatus(imageRoute()));
}

function questionTypeModelCards() {
  const reasoning = textRoleRoute("reasoning", "文本推理模型");
  const answer = textRoleRoute("answer", "结构化解析模型");
  const correctness = textRoleRoute("correctness", "高风险正确性复核模型");
  const vision = visionRoute();
  const answerDirectVision = modelLooksVisionCapable(answer.model, selectedTextRoleProviderConfig("answer"));
  const visualUnderstandingRoute = answerDirectVision
    ? { ...answer, label: "结构化解析模型（直接读图）", tone: "purple" }
    : vision;
  const image = imageRoute();
  return [
    {
      key: "text",
      icon: "fa-file-lines",
      title: "普通文本题",
      desc: "纯文字题目，不涉及图片",
      configHint: "普通文本题只需要文本推理与结构化解析两个阶段。",
      stages: ["reasoning", "answer", "correctness"],
      routes: [reasoning, answer, correctness]
    },
    {
      key: "vision_calc",
      icon: "fa-image",
      title: "有图计算题",
      desc: "含图片/表格，需要读图",
      configHint: answerDirectVision
        ? "结构化解析模型已具备多模态能力，原图会直接随题目发送，不再先调用独立识图模型。"
        : "当前解析模型不读图，有图题先由读图模型建立可复用的题面结构，再进行解析。",
      stages: answerDirectVision ? ["reasoning", "answer", "correctness"] : ["reasoning", "answer", "correctness", "vision"],
      routes: answerDirectVision
        ? [reasoning, visualUnderstandingRoute, correctness]
        : [reasoning, answer, correctness, visualUnderstandingRoute]
    },
    {
      key: "drawing",
      icon: "fa-pen-ruler",
      title: "作图题",
      desc: "需要生成专业图形",
      configHint: "作图题会生成结构化解析，并在规则绘图失败时调用生图模型兜底。",
      stages: ["reasoning", "answer", "correctness", "image"],
      routes: [reasoning, answer, correctness, image]
    },
    {
      key: "vision_drawing",
      icon: "fa-icons",
      title: "有图且需作图题",
      desc: "含图片并需要生成图形",
      configHint: answerDirectVision
        ? "原图直接交给多模态解析模型；生图模型只用于需要生成新图的兜底。"
        : "该题型同时启用读图、文本解析与生图兜底。",
      stages: answerDirectVision ? ["reasoning", "answer", "correctness", "image"] : ["reasoning", "answer", "correctness", "vision", "image"],
      routes: answerDirectVision
        ? [reasoning, visualUnderstandingRoute, correctness, image]
        : [reasoning, answer, correctness, visualUnderstandingRoute, image]
    }
  ];
}

function renderQuestionTypeModelCards() {
  const root = $("modelTypeCards");
  if (!root) return;
  root.innerHTML = questionTypeModelCards().map((card) => {
    const status = aggregateRouteStatus(card.routes);
    return `
      <button class="model-type-card ${card.key === modelQuestionTypeTab ? "active" : ""}" type="button" data-question-type-card="${escapeHtml(card.key)}" onclick="switchQuestionTypeTab('${escapeHtml(card.key)}')">
        <i class="fas ${escapeHtml(card.icon)}"></i>
        <strong>${escapeHtml(card.title)}</strong>
        <span class="model-type-desc">${escapeHtml(card.desc)}</span>
        <span class="model-route-list">
          ${card.routes.map((route) => `
            <span class="model-route-row">
              <em>${escapeHtml(route.label)}</em>
              <span class="model-route-pills">${modelPill(route.provider, route.model, route.tone)}</span>
            </span>
          `).join("")}
        </span>
        <span class="model-type-status ${escapeHtml(status.tone)}">状态：${escapeHtml(status.label)}</span>
      </button>
    `;
  }).join("");
  renderSelectedQuestionTypeConfig();
}

function selectedQuestionTypeConfig() {
  return questionTypeModelCards().find((card) => card.key === modelQuestionTypeTab) || questionTypeModelCards()[0];
}

function renderSelectedQuestionTypeConfig() {
  const card = selectedQuestionTypeConfig();
  const title = $("selectedConfigTitle");
  const desc = $("selectedConfigDesc");
  const summary = $("selectedConfigSummary");
  if (title) title.textContent = `${card.title}配置`;
  if (desc) desc.textContent = card.configHint || card.desc || "";
  if (summary) {
    summary.innerHTML = card.routes.map((route) => `
      <span>
        <em>${escapeHtml(route.label)}</em>
        ${modelPill(route.provider, route.model, route.tone)}
      </span>
    `).join("");
  }
  const activeStages = new Set(card.stages || []);
  document.querySelectorAll("[data-stage-config]").forEach((section) => {
    section.classList.toggle("stage-config-hidden", !activeStages.has(section.dataset.stageConfig));
  });
  document.querySelectorAll(".model-role-grid, .capability-model-grid").forEach((grid) => {
    const configurableChildren = Array.from(grid.querySelectorAll("[data-stage-config]"));
    if (!configurableChildren.length) return;
    grid.classList.toggle("stage-grid-hidden", configurableChildren.every((item) => item.classList.contains("stage-config-hidden")));
  });
}

function updateTextRoleModelControls() {
  const currentName = $("providerSelect")?.value || "";
  for (const roleKey of Object.keys(textModelRoles)) {
    const role = textModelRoles[roleKey];
    populateProviderSelect(role.providerId, "all", currentName);
    populateTextRoleModelSelect(roleKey, selectedModel());
  }
}

function examPresetRequiredProviders(preset) {
  return Array.from(new Set(
    [preset?.base, preset?.reasoning, preset?.answer, preset?.correctness]
      .map((route) => route?.[0])
      .filter(Boolean)
  ));
}

function recommendedExamModelPreset() {
  const saved = localStorage.getItem(EXAM_MODEL_PRESET_STORAGE_KEY) || "";
  if (saved === "custom" || examModelPresets[saved]) return saved;
  for (const key of ["balanced", "quality", "economy"]) {
    const preset = examModelPresets[key];
    if (examPresetRequiredProviders(preset).every((name) => providerConfigs[name]?.api_key_set)) return key;
  }
  return "custom";
}

function selectHasValue(select, value) {
  return Boolean(select && Array.from(select.options || []).some((option) => option.value === value));
}

function setExamTextRoleRoute(roleKey, route) {
  const role = textModelRoles[roleKey];
  if (!role || !Array.isArray(route)) return;
  const [providerName, modelName] = route;
  const providerSelect = $(role.providerId);
  if (!providerConfigs[providerName] || !selectHasValue(providerSelect, providerName)) return;
  providerSelect.value = providerName;
  populateTextRoleModelSelect(roleKey, modelName);
  const modelSelect = $(role.modelSelectId);
  if (selectHasValue(modelSelect, modelName)) modelSelect.value = modelName;
  updateTextRoleHint(roleKey);
}

function renderExamModelPresetSummary(key) {
  const target = $("examModelPresetSummary");
  if (!target) return;
  if (key === "custom" || !examModelPresets[key]) {
    target.classList.remove("warn");
    target.innerHTML = '<i class="fas fa-sliders"></i>当前使用自定义分工；可展开下方高级设置逐项调整。';
    return;
  }
  const preset = examModelPresets[key];
  const missingRequired = examPresetRequiredProviders(preset)
    .filter((name) => !providerConfigs[name]?.api_key_set)
    .map(displayProviderName);
  const imageProviderName = preset.image?.[0] || "";
  const imageOptionalMissing = imageProviderName && !providerConfigs[imageProviderName]?.api_key_set;
  const notices = [];
  if (missingRequired.length) notices.push(`需先配置：${missingRequired.join("、")}`);
  if (imageOptionalMissing) notices.push(`${displayProviderName(imageProviderName)} 未配置时仍可运行，只是不启用生图兜底`);
  target.classList.toggle("warn", missingRequired.length > 0);
  target.innerHTML = `<i class="fas ${missingRequired.length ? "fa-triangle-exclamation" : "fa-circle-check"}"></i><strong>${escapeHtml(preset.label)}</strong>：${escapeHtml(preset.description)}${notices.length ? `<span>${escapeHtml(notices.join("；"))}</span>` : ""}`;
}

function applyExamModelPreset(key, { persist = true } = {}) {
  const select = $("examModelPresetSelect");
  const preset = examModelPresets[key];
  if (select) select.value = preset ? key : "custom";
  syncPlatformSelectElement(select);
  if (!preset) {
    if (persist) localStorage.setItem(EXAM_MODEL_PRESET_STORAGE_KEY, "custom");
    renderExamModelPresetSummary("custom");
    return;
  }
  const [baseProviderName, baseModel] = preset.base;
  const baseProviderSelect = $("providerSelect");
  if (providerConfigs[baseProviderName] && selectHasValue(baseProviderSelect, baseProviderName)) {
    baseProviderSelect.value = baseProviderName;
    updateModelControls();
    if (selectHasValue($("modelSelect"), baseModel)) $("modelSelect").value = baseModel;
  }
  setExamTextRoleRoute("reasoning", preset.reasoning);
  setExamTextRoleRoute("answer", preset.answer);
  setExamTextRoleRoute("correctness", preset.correctness);

  const [visionProviderName, visionModel] = preset.vision;
  if (providerConfigs[visionProviderName] && selectHasValue($("visionProviderSelect"), visionProviderName)) {
    $("visionProviderSelect").value = visionProviderName;
    populateVisionModelSelect(visionModel);
    if (selectHasValue($("visionModelSelect"), visionModel)) $("visionModelSelect").value = visionModel;
  }
  const [imageProviderName, imageModel] = preset.image;
  if (providerConfigs[imageProviderName] && selectHasValue($("imageProviderSelect"), imageProviderName)) {
    $("imageProviderSelect").value = imageProviderName;
    populateImageModelControls(imageModel);
    if (selectHasValue($("imageModelSelect"), imageModel)) $("imageModelSelect").value = imageModel;
  }
  if ($("thinkingModeSelect")) $("thinkingModeSelect").value = "high";
  updateCapabilityModelHints();
  updateModelCapabilityRisk();
  renderQuestionTypeModelCards();
  switchQuestionTypeTab(modelQuestionTypeTab);
  if (persist) localStorage.setItem(EXAM_MODEL_PRESET_STORAGE_KEY, key);
  renderExamModelPresetSummary(key);
}

function initializeExamModelPreset() {
  const key = recommendedExamModelPreset();
  applyExamModelPreset(key, { persist: true });
}

function markExamModelPresetCustom() {
  const select = $("examModelPresetSelect");
  if (select) select.value = "custom";
  syncPlatformSelectElement(select);
  localStorage.setItem(EXAM_MODEL_PRESET_STORAGE_KEY, "custom");
  renderExamModelPresetSummary("custom");
}

function switchQuestionTypeTab(tab) {
  modelQuestionTypeTab = ["text", "vision_calc", "drawing", "vision_drawing"].includes(tab) ? tab : "text";
  document.querySelectorAll(".model-type-tab").forEach((button) => {
    button.classList.toggle("active", button.dataset.questionTypeTab === modelQuestionTypeTab);
  });
  document.querySelectorAll(".model-type-card").forEach((card) => {
    card.classList.toggle("active", card.dataset.questionTypeCard === modelQuestionTypeTab);
  });
  renderSelectedQuestionTypeConfig();
}

function toggleModelAdvanced() {
  const details = $("modelAdvancedDetails");
  if (!details) return;
  details.open = !details.open;
  const button = $("modelAdvancedToggleBtn");
  if (button) {
    button.classList.toggle("active", details.open);
    button.innerHTML = details.open
      ? '<i class="fas fa-xmark"></i>收起全局设置'
      : '<i class="fas fa-sliders"></i>全局设置';
  }
}

function populateVisionModelSelect(preferredModel = "") {
  const cfg = selectedVisionProviderConfig();
  const select = $("visionModelSelect");
  const input = $("visionModelInput");
  if (!select || !input) return;
  const configuredOptions = Array.isArray(cfg.vision_model_options)
    ? cfg.vision_model_options.filter(Boolean)
    : [];
  const candidateModels = configuredOptions.length
    ? configuredOptions
    : [
      cfg.vision_model,
      cfg.default_model,
      ...(Array.isArray(cfg.model_options) ? cfg.model_options : []),
    ].filter((model) => model && (model === cfg.vision_model || modelLooksVisionCapable(model, cfg)));
  const options = Array.from(new Set([
    cfg.vision_model,
    ...candidateModels,
  ].filter(Boolean)));
  const labels = cfg.model_option_labels || {};
  select.innerHTML = "";
  if (!options.length) {
    const option = document.createElement("option");
    option.value = "";
    option.textContent = "未配置 vision_model";
    select.appendChild(option);
  } else {
    for (const model of options) {
      const option = document.createElement("option");
      option.value = model;
      option.textContent = model === cfg.vision_model ? `${labels[model] || model}（默认读图）` : (labels[model] || model);
      option.title = model;
      select.appendChild(option);
    }
    const preferred = String(preferredModel || "").trim();
    select.value = options.includes(preferred) ? preferred : (cfg.vision_model || cfg.default_model || options[0]);
  }
  input.hidden = !cfg.allow_custom_model;
  if (!preferredModel) input.value = "";
  input.placeholder = cfg.model_hint || "填写 vision model ID";
  updateCapabilityModelHints();
}

function populateImageModelControls(preferredModel = "") {
  const cfg = selectedImageProviderConfig();
  const select = $("imageModelSelect");
  const input = $("imageModelInput");
  if (!input) return;
  const options = Array.from(new Set([
    cfg.image_model,
    ...(Array.isArray(cfg.image_model_options) ? cfg.image_model_options : []),
  ].filter(Boolean)));
  const labels = cfg.image_model_option_labels || {};
  if (select) {
    select.innerHTML = "";
    if (!options.length) {
      const option = document.createElement("option");
      option.value = "";
      option.textContent = "未配置 image model";
      select.appendChild(option);
    } else {
      for (const model of options) {
        const option = document.createElement("option");
        option.value = model;
        option.textContent = model === cfg.image_model ? `${labels[model] || model}（默认）` : (labels[model] || model);
        option.title = model;
        select.appendChild(option);
      }
      const preferred = String(preferredModel || "").trim();
      select.value = options.includes(preferred) ? preferred : (cfg.image_model || options[0]);
    }
    select.hidden = false;
  }
  input.hidden = !cfg.allow_custom_model;
  if (!preferredModel) input.value = "";
  input.placeholder = cfg.image_model || "填写 image model ID";
  updateCapabilityModelHints();
}

function updateCapabilityModelHints() {
  const visionCfg = selectedVisionProviderConfig();
  const imageCfg = selectedImageProviderConfig();
  const visionHint = $("visionModelHint");
  if (visionHint) {
    visionHint.innerHTML = providerHasVision(visionCfg)
      ? `<i class="fas fa-eye"></i><span title="有图片或复杂表格时使用 ${escapeHtml(displayProviderName(visionCfg.name || $("visionProviderSelect")?.value))} / ${escapeHtml(selectedVisionModel() || visionCfg.vision_model)}">${escapeHtml(displayProviderName(visionCfg.name || $("visionProviderSelect")?.value))} / ${escapeHtml(selectedVisionModel() || visionCfg.vision_model)}</span>`
      : '<i class="fas fa-triangle-exclamation"></i>当前服务商未配置多模态识图模型。';
  }
  const imageHint = $("imageModelHint");
  if (imageHint) {
    imageHint.innerHTML = providerHasImageModel(imageCfg)
      ? `<i class="fas fa-image"></i><span title="规则化绘图失败时使用 ${escapeHtml(displayProviderName(imageCfg.name || $("imageProviderSelect")?.value))} / ${escapeHtml(selectedImageModel() || imageCfg.image_model)}">${escapeHtml(displayProviderName(imageCfg.name || $("imageProviderSelect")?.value))} / ${escapeHtml(selectedImageModel() || imageCfg.image_model)}</span>`
      : '<i class="fas fa-triangle-exclamation"></i>当前服务商未配置生图模型。';
  }
  updateModelRoleCards();
}

function updateCapabilityModelControls() {
  const currentName = $("providerSelect")?.value || "";
  populateProviderSelect("visionProviderSelect", "vision", currentName);
  populateProviderSelect("imageProviderSelect", "image", currentName);
  syncVisionModelFromAnswerModel();
  populateImageModelControls();
}

function syncVisionModelFromAnswerModel() {
  const cfg = currentProviderConfig();
  const providerName = $("providerSelect")?.value || "";
  const answerModel = selectedModel();
  const visionProviderSelect = $("visionProviderSelect");
  if (visionProviderSelect && modelLooksVisionCapable(answerModel, cfg)) {
    const hasSameProvider = Array.from(visionProviderSelect.options).some((option) => option.value === providerName);
    if (hasSameProvider) visionProviderSelect.value = providerName;
    populateVisionModelSelect(answerModel);
  } else {
    populateVisionModelSelect();
  }
  updateModelCapabilityRisk();
}

function updateModelControls() {
  const cfg = currentProviderConfig();
  const options = Array.isArray(cfg.model_options) ? cfg.model_options : [];
  const optionLabels = cfg.model_option_labels || {};
  const allowCustom = Boolean(cfg.allow_custom_model);
  const modelSelect = $("modelSelect");
  const modelInput = $("modelInput");
  const thinkingSelect = $("thinkingModeSelect");
  if (thinkingSelect) {
    const configuredThinking = cfg.thinking_mode === "enabled" ? "medium" : (cfg.thinking_mode || "auto");
    thinkingSelect.value = Array.from(thinkingSelect.options).some((option) => option.value === configuredThinking)
      ? configuredThinking
      : "auto";
  }
  modelSelect.innerHTML = "";
  if (options.length) {
    if (allowCustom) {
      const placeholder = document.createElement("option");
      placeholder.value = "";
      placeholder.textContent = "请选择已确认可 API 调用的模型，或在下方填写 ep- 接入点 ID";
      modelSelect.appendChild(placeholder);
    }
    for (const model of options) {
      const option = document.createElement("option");
      const label = optionLabels[model] || model;
      option.value = model;
      option.textContent = model === cfg.default_model ? `${label} (默认)` : label;
      option.title = model;
      modelSelect.appendChild(option);
    }
    modelSelect.value = cfg.default_model || "";
    modelSelect.hidden = false;
    modelInput.hidden = !allowCustom;
    modelInput.value = "";
    modelInput.placeholder = cfg.model_hint || "如需使用其他模型，请填写模型 ID";
    updateTextRoleModelControls();
    updateCapabilityModelControls();
    updateModelCapabilityRisk();
    renderQuestionTypeModelCards();
    switchQuestionTypeTab(modelQuestionTypeTab);
    return;
  }
  const fallbackModel = cfg.default_model || "默认模型";
  const option = document.createElement("option");
  option.value = cfg.default_model || "";
  option.textContent = cfg.default_model ? `${cfg.default_model} (默认)` : fallbackModel;
  modelSelect.appendChild(option);
  modelSelect.hidden = false;
  modelInput.hidden = !allowCustom;
  modelInput.value = "";
  modelInput.placeholder = cfg.model_hint || "如需使用其他模型，请填写模型 ID";
  updateTextRoleModelControls();
  updateCapabilityModelControls();
  updateModelCapabilityRisk();
  renderQuestionTypeModelCards();
  switchQuestionTypeTab(modelQuestionTypeTab);
}

function renderApiKeyFileInfo() {
  if (apiKeyConfigLoadState.keyFile === "error") {
    setText("homeApiKeyFileStatus", "API 配置保存状态暂时无法读取，可进入配置中心重试");
    return;
  }
  const count = Number(apiKeyFileInfo?.configured_count || 0);
  const total = Object.keys(providerConfigs || {}).length;
  setText(
    "homeApiKeyFileStatus",
    count ? `已配置 ${count} 个平台，可在配置中心测试、替换或删除` : `已接入 ${total} 个平台，请先配置需要使用的平台`
  );
}

function keyProviderCapabilityText(cfg) {
  const items = ["文字模型"];
  if (cfg.supports_vision) items.push("视觉理解");
  if (cfg.supports_image_generation && cfg.image_model) items.push("图片生成");
  return items.join(" · ");
}

function keyProviderStatus(card, kind, title, detail = "") {
  const status = card?.querySelector("[data-key-status]");
  if (!status) return;
  status.className = `key-provider-status ${kind || "idle"}`;
  status.innerHTML = `<strong>${escapeHtml(title)}</strong>${detail ? `<span>${escapeHtml(detail)}</span>` : ""}`;
}

function renderKeyProviderCards() {
  const grid = $("keyProviderGrid");
  if (!grid) return;
  if (apiKeyConfigLoadState.providers === "loading") {
    grid.innerHTML = '<p class="empty-hint">正在加载已接入的平台...</p>';
    return;
  }
  if (apiKeyConfigLoadState.providers === "error") {
    const recoverAction = apiKeyConfigLoadState.recoveryAvailable ? `
        <button type="button" class="outline-button danger-text" data-key-config-recover><i class="fas fa-shield-halved"></i>备份损坏配置并重建</button>` : "";
    grid.innerHTML = `
      <div class="key-config-load-error" role="alert">
        <strong>API 配置加载失败，请重试</strong>
        <p>${apiKeyConfigLoadState.recoveryAvailable ? "检测到配置文件损坏。可先安全备份原文件，再重建空白配置。" : "平台列表暂时不可用，其他工作区仍可继续使用。"}</p>
        <button type="button" class="outline-button" data-key-config-retry><i class="fas fa-rotate"></i>重试加载</button>
        ${recoverAction}
      </div>`;
    grid.querySelector("[data-key-config-retry]")?.addEventListener("click", () => loadApiConfiguration());
    grid.querySelector("[data-key-config-recover]")?.addEventListener("click", recoverDamagedApiConfiguration);
    return;
  }
  const entries = Object.entries(providerConfigs || {});
  if (!entries.length) {
    grid.innerHTML = '<p class="empty-hint">当前没有可配置的平台。</p>';
    return;
  }
  const keyFileWarning = apiKeyConfigLoadState.keyFile === "error" ? `
    <div class="key-config-load-error" role="alert">
      <strong>API 配置保存状态加载失败，请重试</strong>
      <p>平台列表已加载，但暂时无法确认本地保存状态。</p>
      <button type="button" class="outline-button" data-key-config-retry><i class="fas fa-rotate"></i>重试加载</button>
      ${apiKeyConfigLoadState.recoveryAvailable ? '<button type="button" class="outline-button danger-text" data-key-config-recover><i class="fas fa-shield-halved"></i>备份损坏配置并重建</button>' : ""}
    </div>` : "";
  grid.innerHTML = keyFileWarning + entries.map(([name, cfg]) => `
    <form class="key-provider-card" data-key-provider="${escapeHtml(name)}" autocomplete="off">
      <header>
        <span class="key-provider-mark"><i class="fas fa-cloud"></i></span>
        <div>
          <h3>${escapeHtml(displayProviderName(name))}</h3>
          <p>${escapeHtml(keyProviderCapabilityText(cfg))}</p>
        </div>
        <span class="key-saved-badge ${cfg.api_key_set ? "saved" : ""}">
          <i class="fas ${cfg.api_key_set ? "fa-circle-check" : "fa-circle"}"></i>${cfg.api_key_set ? "已保存" : "未配置"}
        </span>
      </header>
      <label for="key-input-${escapeHtml(name)}">${escapeHtml(displayProviderName(name))} API Key</label>
      <div class="key-provider-input-row">
        <input id="key-input-${escapeHtml(name)}" type="password" autocomplete="off"
          data-key-input="${escapeHtml(name)}" placeholder="${cfg.api_key_set ? "输入新 Key 可替换已保存配置" : "粘贴此平台的 API Key"}">
        <button type="button" class="key-toggle-button" data-key-toggle="${escapeHtml(name)}" title="显示或隐藏 Key" aria-label="显示或隐藏 ${escapeHtml(displayProviderName(name))} API Key"><i class="fas fa-eye" aria-hidden="true"></i></button>
      </div>
      <p class="key-test-model">连接测试使用：${escapeHtml(cfg.default_model || "平台默认模型")}</p>
      <div class="key-provider-actions">
        <button type="button" class="outline-button" data-key-test="${escapeHtml(name)}"><i class="fas fa-plug"></i>测试连接</button>
        <button type="button" class="secondary-button" data-key-save="${escapeHtml(name)}" disabled><i class="fas fa-floppy-disk"></i>保存</button>
        ${cfg.api_key_set ? `<button type="button" class="text-button danger-text" data-key-delete="${escapeHtml(name)}" aria-label="删除 ${escapeHtml(displayProviderName(name))} API Key">删除</button>` : ""}
      </div>
      <div class="key-provider-status idle" data-key-status><strong>等待测试</strong><span>新 Key 必须测试成功后才能保存。</span></div>
    </form>
  `).join("");
  grid.querySelector("[data-key-config-retry]")?.addEventListener("click", () => loadApiConfiguration());
  grid.querySelector("[data-key-config-recover]")?.addEventListener("click", recoverDamagedApiConfiguration);
  grid.querySelectorAll("form[data-key-provider]").forEach((form) => {
    form.addEventListener("submit", (event) => event.preventDefault());
  });
  grid.querySelectorAll("[data-key-input]").forEach((input) => {
    input.addEventListener("input", () => {
      const name = input.dataset.keyInput;
      delete keyConfigTests[name];
      const card = input.closest(".key-provider-card");
      const save = card?.querySelector("[data-key-save]");
      if (save) save.disabled = true;
      keyProviderStatus(card, "idle", "等待测试", "Key 发生变化，请重新测试。");
    });
  });
  grid.querySelectorAll("[data-key-toggle]").forEach((button) => {
    button.addEventListener("click", () => {
      const input = grid.querySelector(`[data-key-input="${CSS.escape(button.dataset.keyToggle)}"]`);
      if (!input) return;
      input.type = input.type === "password" ? "text" : "password";
      const icon = button.querySelector("i");
      if (icon) icon.className = input.type === "password" ? "fas fa-eye" : "fas fa-eye-slash";
    });
  });
  grid.querySelectorAll("[data-key-test]").forEach((button) => {
    button.addEventListener("click", () => testKeyProvider(button.dataset.keyTest));
  });
  grid.querySelectorAll("[data-key-save]").forEach((button) => {
    button.addEventListener("click", () => saveKeyProvider(button.dataset.keySave));
  });
  grid.querySelectorAll("[data-key-delete]").forEach((button) => {
    button.addEventListener("click", () => deleteKeyProvider(button.dataset.keyDelete));
  });
}

async function recoverDamagedApiConfiguration(event) {
  const button = event?.currentTarget;
  const confirmed = await platformConfirm({
    eyebrow: "API 配置恢复",
    title: "备份损坏配置并重建？",
    message: "平台会在当前电脑安全备份原文件，并创建空白配置。原有 Key 不会显示，但重建后需要重新配置。",
    confirmText: "确认备份并重建",
    tone: "danger"
  });
  if (!confirmed) return;
  if (button) button.disabled = true;
  try {
    const result = await api("/api/providers/recover-local-keys", {
      method: "POST",
      body: JSON.stringify({ confirm: true })
    });
    await loadApiConfiguration();
    setVisual(
      "keyConfigNotice",
      result.already_recovered ? "API 配置已恢复" : "损坏配置已安全备份并重建",
      "请重新配置需要使用的平台 API Key。",
      "ok"
    );
  } catch (error) {
    setVisual("keyConfigNotice", "API 配置恢复失败", error?.userMessage || "请检查文件权限或磁盘状态后重试。", "error");
    if (button?.isConnected) button.disabled = false;
  }
}

async function testKeyProvider(providerName) {
  const card = document.querySelector(`[data-key-provider="${CSS.escape(providerName)}"]`);
  const input = card?.querySelector("[data-key-input]");
  const button = card?.querySelector("[data-key-test]");
  const save = card?.querySelector("[data-key-save]");
  const cfg = providerConfigs[providerName] || {};
  const key = input?.value.trim() || "";
  if (!key && !cfg.api_key_set) {
    keyProviderStatus(card, "error", "请先填写 API Key");
    return;
  }
  button.disabled = true;
  if (save) save.disabled = true;
  keyProviderStatus(card, "testing", "正在测试连接", cfg.default_model || "平台默认模型");
  try {
    const data = await api("/api/provider-test", {
      method: "POST",
      body: JSON.stringify({
        provider: providerName,
        model: cfg.default_model || undefined,
        thinking_mode: cfg.thinking_mode || "auto",
        api_key: key || undefined
      })
    });
    if (key) {
      keyConfigTests[providerName] = key;
      if (save) save.disabled = false;
    }
    rememberModelConnectionTest(providerName, data.model || cfg.default_model || "", true);
    keyProviderStatus(card, "ok", "连接成功", `${displayProviderName(providerName)} / ${data.model}`);
    setVisual("keyConfigNotice", "连接测试通过", key ? "现在可以保存这个 Key。" : "已保存的 Key 仍可正常使用。", "ok");
  } catch (err) {
    delete keyConfigTests[providerName];
    const message = String(err).replace(/^Error:\s*/, "");
    rememberModelConnectionTest(providerName, cfg.default_model || "", false, message);
    const advice = providerErrorAdvice(message);
    keyProviderStatus(card, "error", advice.title, advice.body);
    setVisual("keyConfigNotice", advice.title, advice.body, "error");
  } finally {
    button.disabled = false;
  }
}

async function saveKeyProvider(providerName) {
  const card = document.querySelector(`[data-key-provider="${CSS.escape(providerName)}"]`);
  const input = card?.querySelector("[data-key-input]");
  const key = input?.value.trim() || "";
  if (!key || keyConfigTests[providerName] !== key) {
    keyProviderStatus(card, "error", "请先测试当前填写的 Key");
    return;
  }
  const envKey = providerEnvKey(providerName);
  if (!envKey) {
    keyProviderStatus(card, "error", "该平台没有可保存的 Key 配置");
    return;
  }
  try {
    await api("/api/providers/local-keys", {
      method: "POST",
      body: JSON.stringify({ keys: { [envKey]: key } })
    });
    delete keyConfigTests[providerName];
    setVisual("keyConfigNotice", "API Key 已保存", `${displayProviderName(providerName)} 已可供全软件使用。`, "ok");
    await refresh();
  } catch (err) {
    const message = String(err).replace(/^Error:\s*/, "");
    keyProviderStatus(card, "error", "保存失败", message);
  }
}

async function deleteKeyProvider(providerName) {
  if (!await platformConfirm({
    eyebrow: "API 配置",
    title: "删除已保存的 API Key",
    message: `删除后，${displayProviderName(providerName)} 会在调用模型时提示未配置。`,
    confirmText: "确认删除",
    tone: "danger"
  })) return;
  const envKey = providerEnvKey(providerName);
  if (!envKey) return;
  try {
    await api("/api/providers/local-keys", {
      method: "POST",
      body: JSON.stringify({ keys: { [envKey]: "" } })
    });
    delete keyConfigTests[providerName];
    setVisual("keyConfigNotice", "API Key 已删除", `${displayProviderName(providerName)} 将在使用时提示未配置。`, "ok");
    await refresh();
  } catch (err) {
    setVisual("keyConfigNotice", "删除失败", String(err).replace(/^Error:\s*/, ""), "error");
  }
}

const TASK_MODEL_STORAGE_KEY = "answerBook.taskModels.v1";

function taskModelControlIds(profile, kind) {
  const prefix = profile === "knowledge" ? "knowledge" : "practice";
  const type = kind === "vision" ? "Vision" : "Text";
  return {
    provider: `${prefix}${type}ProviderSelect`,
    model: `${prefix}${type}ModelSelect`,
    input: `${prefix}${type}ModelInput`,
    summary: profile === "knowledge"
      ? `${prefix}${type}ModelDetail`
      : `${prefix}${type}ModelSummary`,
  };
}

function practiceModelControlIds(kind) {
  return taskModelControlIds("practice", kind);
}

function readTaskModelSettings() {
  try {
    return JSON.parse(localStorage.getItem(TASK_MODEL_STORAGE_KEY) || "{}");
  } catch (_error) {
    return {};
  }
}

function defaultTaskProvider(kind) {
  const entries = kind === "vision"
    ? Object.entries(providerConfigs || {}).filter(([, cfg]) => providerHasVision(cfg))
    : Object.entries(providerConfigs || {});
  return entries.find(([, cfg]) => cfg.api_key_set)?.[0] || entries[0]?.[0] || "";
}

function savedTaskModelSetting(profile, kind) {
  return readTaskModelSettings()?.[profile]?.[kind] || {};
}

function saveTaskModelSetting(profile, kind) {
  const ids = taskModelControlIds(profile, kind);
  const all = readTaskModelSettings();
  all[profile] ||= {};
  all[profile][kind] = {
    provider: $(ids.provider)?.value || "",
    model: $(ids.model)?.value || "",
    custom: $(ids.input)?.value.trim() || "",
  };
  localStorage.setItem(TASK_MODEL_STORAGE_KEY, JSON.stringify(all));
}

function taskProviderName(profile, kind) {
  const ids = taskModelControlIds(profile, kind);
  return $(ids.provider)?.value || savedTaskModelSetting(profile, kind).provider || defaultTaskProvider(kind);
}

function practiceProviderName(kind) {
  return taskProviderName("practice", kind);
}

function knowledgeProviderName(kind) {
  return taskProviderName("knowledge", kind);
}

function practiceProviderConfig(kind) {
  return providerConfigs[practiceProviderName(kind)] || {};
}

function practiceModelOptions(kind, cfg) {
  if (kind !== "vision") return Array.isArray(cfg.model_options) ? cfg.model_options.filter(Boolean) : [];
  const configured = Array.isArray(cfg.vision_model_options) ? cfg.vision_model_options.filter(Boolean) : [];
  if (configured.length) return Array.from(new Set([cfg.vision_model, ...configured].filter(Boolean)));
  return Array.from(new Set([
    cfg.vision_model,
    cfg.default_model,
    ...(Array.isArray(cfg.model_options) ? cfg.model_options : []),
  ].filter((model) => model && (model === cfg.vision_model || modelLooksVisionCapable(model, cfg)))));
}

function populateTaskModelControl(profile, kind, preferredModel = "") {
  const ids = taskModelControlIds(profile, kind);
  const providerSelect = $(ids.provider);
  const modelSelect = $(ids.model);
  const input = $(ids.input);
  if (!providerSelect || !modelSelect || !input) return;
  const providerName = providerSelect.value || defaultTaskProvider(kind);
  modelSelect.innerHTML = "";
  const cfg = providerConfigs[providerName] || {};
  const options = practiceModelOptions(kind, cfg);
  const labels = cfg.model_option_labels || {};
  modelSelect.disabled = false;
  if (options.length) {
    for (const model of options) {
      const option = document.createElement("option");
      option.value = model;
      const isDefault = model === (kind === "vision" ? cfg.vision_model : cfg.default_model);
      option.textContent = `${labels[model] || model}${isDefault ? "（默认）" : ""}`;
      modelSelect.appendChild(option);
    }
    const preferred = String(preferredModel || "").trim();
    const fallback = kind === "vision" ? (cfg.vision_model || cfg.default_model) : cfg.default_model;
    modelSelect.value = options.includes(preferred) ? preferred : (fallback || options[0]);
  } else {
    const option = document.createElement("option");
    const fallback = kind === "vision" ? (cfg.vision_model || cfg.default_model || "") : (cfg.default_model || "");
    option.value = fallback;
    option.textContent = fallback || "未配置默认模型";
    modelSelect.appendChild(option);
  }
  input.hidden = !cfg.allow_custom_model;
  input.value = cfg.allow_custom_model ? (savedTaskModelSetting(profile, kind).custom || "") : "";
  input.placeholder = cfg.model_hint || "填写模型 ID";
}

function populateTaskModelSettings(profile) {
  for (const kind of ["text", "vision"]) {
    const ids = taskModelControlIds(profile, kind);
    const select = $(ids.provider);
    if (!select) continue;
    const saved = savedTaskModelSetting(profile, kind);
    const previousProvider = select.value || saved.provider || defaultTaskProvider(kind);
    const previousModel = $(ids.model)?.value || saved.model || "";
    const entries = kind === "vision"
      ? Object.entries(providerConfigs || {}).filter(([, cfg]) => providerHasVision(cfg))
      : Object.entries(providerConfigs || {});
    select.innerHTML = "";
    for (const [name, cfg] of entries) {
      const option = document.createElement("option");
      option.value = name;
      option.textContent = `${displayProviderName(name)}${cfg.api_key_set ? "（Key 已配置）" : "（缺少 Key）"}`;
      select.appendChild(option);
    }
    select.value = entries.some(([name]) => name === previousProvider) ? previousProvider : (defaultTaskProvider(kind) || "");
    populateTaskModelControl(profile, kind, previousModel);
    saveTaskModelSetting(profile, kind);
  }
  updateTaskModelSummary(profile);
}

function populatePracticeModelControl(kind, preferredModel = "") {
  populateTaskModelControl("practice", kind, preferredModel);
}

function populatePracticeModelSettings() {
  populateTaskModelSettings("practice");
  populateTaskModelSettings("knowledge");
}

function selectedTaskModel(profile, kind) {
  const ids = taskModelControlIds(profile, kind);
  const providerName = taskProviderName(profile, kind);
  const cfg = providerConfigs[providerName] || {};
  const custom = Boolean(cfg.allow_custom_model) ? $(ids.input)?.value.trim() : "";
  const saved = savedTaskModelSetting(profile, kind);
  return custom || $(ids.model)?.value || saved.custom || saved.model || (kind === "vision" ? cfg.vision_model : cfg.default_model) || cfg.default_model || "";
}

function selectedPracticeModel(kind) {
  return selectedTaskModel("practice", kind);
}

function selectedKnowledgeModel(kind) {
  return selectedTaskModel("knowledge", kind);
}

function resetPracticeModelSettings() {
  const all = readTaskModelSettings();
  delete all.practice;
  localStorage.setItem(TASK_MODEL_STORAGE_KEY, JSON.stringify(all));
  populateTaskModelSettings("practice");
}

function updateTaskModelSummary(profile) {
  const textProviderName = taskProviderName(profile, "text");
  const textProvider = providerConfigs[textProviderName] || {};
  const textModel = selectedTaskModel(profile, "text");
  const visionProviderName = taskProviderName(profile, "vision");
  const visionProvider = providerConfigs[visionProviderName] || {};
  const visionModel = selectedTaskModel(profile, "vision");
  const textKeyState = textProvider.api_key_set ? "" : " · 缺少 Key";
  const visionKeyState = visionProvider.api_key_set ? "" : " · 缺少 Key";
  const primaryHandlesImages = modelLooksVisionCapable(textModel, textProvider);
  setText(taskModelControlIds(profile, "text").summary, `${displayProviderName(textProviderName || "未选择")} / ${textModel || "未选择模型"}${textKeyState}`);
  setText(
    taskModelControlIds(profile, "vision").summary,
    primaryHandlesImages
      ? `备用：${displayProviderName(visionProviderName || "未选择")} / ${visionModel || "未选择模型"}（主模型已支持图文，默认不启用）`
      : `${displayProviderName(visionProviderName || "未选择")} / ${visionModel || "未选择模型"}${visionKeyState}（主模型遇图时启用）`
  );
  if (profile === "practice" || (profile === "knowledge" && currentPracticeSourceMode === "knowledge")) {
    setText("practiceCurrentModelBadge", `${displayProviderName(textProviderName || "未选择")} / ${textModel || "未选择模型"}${textKeyState}`);
  }
  if (profile === "knowledge") {
    setText("knowledgeModelSummary", `${displayProviderName(textProviderName || "未选择")} / ${textModel || "未选择模型"}${textKeyState}`);
  }
}

function updatePracticeModelSummary() {
  updateTaskModelSummary(currentPracticeSourceMode === "knowledge" ? "knowledge" : "practice");
}

function updateKnowledgeModelSummary() {
  updateTaskModelSummary("knowledge");
}

function selectedModel() {
  const cfg = currentProviderConfig();
  const options = Array.isArray(cfg.model_options) ? cfg.model_options : [];
  const customModel = Boolean(cfg.allow_custom_model) ? $("modelInput").value.trim() : "";
  if (customModel) return customModel;
  if (options.length) return $("modelSelect").value || cfg.default_model;
  return $("modelSelect").value || cfg.default_model || undefined;
}

function selectedVisionModel() {
  const cfg = selectedVisionProviderConfig();
  const custom = Boolean(cfg.allow_custom_model) ? $("visionModelInput")?.value.trim() : "";
  return custom || $("visionModelSelect")?.value || cfg.vision_model || cfg.default_model || "";
}

function selectedImageModel() {
  const cfg = selectedImageProviderConfig();
  const custom = Boolean(cfg.allow_custom_model) ? $("imageModelInput")?.value.trim() : "";
  return custom || $("imageModelSelect")?.value || cfg.image_model || "";
}

function selectedThinkingMode() {
  const value = $("thinkingModeSelect")?.value || "auto";
  return ["auto", "enabled", "disabled", "low", "medium", "high", "xhigh"].includes(value) ? value : "auto";
}

function displayThinkingMode(value) {
  if (value === "enabled") return "开启 thinking";
  if (value === "disabled") return "关闭 thinking";
  if (["low", "medium", "high", "xhigh"].includes(value)) return `${value} 推理强度`;
  return "自动 thinking";
}

function taskDurationText(task) {
  if (task?.duration_text) return task.duration_text;
  const start = Date.parse(String(task?.created_at || "").replace(" ", "T"));
  if (!Number.isFinite(start)) return "暂无";
  const end = task?.status === "running" || task?.status === "queued" || task?.status === "paused"
    ? Date.now()
    : Date.parse(String(task?.updated_at || "").replace(" ", "T"));
  const seconds = Math.max(0, Math.floor(((Number.isFinite(end) ? end : Date.now()) - start) / 1000));
  if (seconds >= 3600) return `${Math.floor(seconds / 3600)}小时${Math.floor((seconds % 3600) / 60)}分`;
  if (seconds >= 60) return `${Math.floor(seconds / 60)}分${seconds % 60}秒`;
  return `${seconds}秒`;
}

function requireSelectedModel() {
  const model = selectedModel();
  if (!model) {
    throw new Error("请先选择可 API 调用的模型，或填写火山方舟推理接入点 ID（通常以 ep- 开头）。");
  }
  return model;
}

function providerErrorAdvice(message) {
  const text = String(message || "");
  if (text.includes("ModelNotOpen")) {
    const modelMatch = text.match(/model\s+([A-Za-z0-9_.:-]+)/);
    const model = modelMatch?.[1] || selectedModel() || "当前模型";
    return {
      title: "模型未开通",
      body: `${model} 尚未在当前火山方舟账号中开通。请在火山方舟控制台开通该模型，或填写一个已经开通的推理接入点 ID（通常以 ep- 开头）后再测试。`
    };
  }
  if (text.includes("InvalidEndpointOrModel.NotFound")) {
    return {
      title: "模型或接入点不存在",
      body: "方舟没有识别当前填写的模型名。请到火山方舟控制台进入该模型的 API 调用页，复制推理接入点 ID（通常以 ep- 开头），填到模型输入框后再测试。"
    };
  }
  if (text.includes("Invalid API key") || text.includes("Authentication") || text.includes("401")) {
    return {
      title: "API Key 无效",
      body: "请确认复制的是火山方舟 API Key，且 Key 未删除、未过期。"
    };
  }
  return { title: "模型测试失败", body: text };
}

async function createTask() {
  $("taskResult").textContent = "创建中...";
  setVisual("taskVisualResult", "正在创建真题项目", "平台会检查所选教材是否已有可复用索引。", "info");
  try {
    const examPath = $("examSelect").value || $("examPath").value.trim();
    if (!examPath) throw new Error("请先选择或上传一个真题 DOCX");
    const selectedBooks = selectedTextbooks();
    if (!selectedBooks.length) throw new Error("请至少选择一本已建立索引的教材");
    const selectedBookNames = selectedTextbookNames();
    await requirePreparedTextbookIndex();
    const confirmed = await platformConfirm({
      eyebrow: "开始真题解析",
      title: "确认本次解析范围",
      message: `真题：${shortName(examPath)}\n教材：已选择 ${selectedBookNames.length} 本（${selectedBookNames.join("、")}）\n\n开始后会调用当前配置的模型，并在后台持续执行。`,
      confirmText: "确认开始解析",
      tone: "primary"
    });
    if (!confirmed) {
      setVisual("taskVisualResult", "尚未开始", "你可以继续调整真题或教材范围。", "info");
      return;
    }
    const imageFallbackConfigured = Boolean(selectedImageProviderConfig()?.api_key_set && selectedImageModel());
    const data = await api("/api/tasks", {
      method: "POST",
      body: JSON.stringify({
        exam_path: examPath,
        textbooks_dir: $("textbooksDir").value.trim() || libraryFiles.textbooks_root,
        selected_textbooks: selectedBooks,
        textbook_display_names: selectedTextbookDisplayNames(),
        provider: $("providerSelect").value,
        model: requireSelectedModel(),
        reasoning_provider: $("reasoningProviderSelect")?.value || $("providerSelect").value,
        reasoning_model: selectedTextRoleModel("reasoning") || requireSelectedModel(),
        answer_provider: $("answerProviderSelect")?.value || $("providerSelect").value,
        answer_model: selectedTextRoleModel("answer") || requireSelectedModel(),
        correctness_provider: $("correctnessProviderSelect")?.value || $("answerProviderSelect")?.value || $("providerSelect").value,
        correctness_model: selectedTextRoleModel("correctness") || selectedTextRoleModel("answer") || requireSelectedModel(),
        vision_provider: $("visionProviderSelect")?.value || "",
        vision_model: selectedVisionModel(),
        image_provider: imageFallbackConfigured ? ($("imageProviderSelect")?.value || "") : "",
        image_model: imageFallbackConfigured ? selectedImageModel() : "",
        model_thinking: selectedThinkingMode()
      })
    });
    $("taskResult").textContent = pretty(data);
    if (data.task && data.task.task_id) {
      $("taskIdInput").value = data.task.task_id;
      activeTaskId = data.task.task_id;
      clearTaskDiagnostics();
      updateTaskSummary(data.task);
      setVisual("taskVisualResult", "任务已创建", `当前任务：${shortName(data.task.exam_path)}，正在启动解析流程。`, "ok");
      goToPage("task");
      await runTask(false);
    }
    await loadTasks();
  } catch (err) {
    const message = String(err).replace(/^Error:\s*/, "");
    $("taskResult").textContent = `创建失败：${message}`;
    setVisual("taskVisualResult", "创建失败", message, "error");
  }
}

function renderTasks(tasks) {
  const list = $("taskList");
  if (!list) return;
  list.innerHTML = "";
  const examTasks = (tasks || []).filter((task) => !task.is_generation_task && (!task.workflow_type || task.workflow_type === "exam_analysis"));
  if (!examTasks.length) {
    list.textContent = "暂无任务";
    return;
  }
  for (const task of examTasks.slice(0, 20)) {
    const row = document.createElement("button");
    row.type = "button";
    row.className = "task-row";
    row.classList.toggle("selected", task.task_id === activeTaskId);
    const title = document.createElement("strong");
    title.textContent = shortName(task.exam_path);
    const meta = document.createElement("span");
    meta.textContent = `${statusLabel(task.status)} · ${stageLabel(task.current_stage)} · ${formatTaskTimestamp(task.updated_at)}`;
    const badge = document.createElement("em");
    badge.className = `status-badge status-${task.status || "unknown"}`;
    badge.textContent = statusLabel(task.status);
    row.append(title, meta, badge);
    row.title = `${task.exam_path || ""}\n${task.textbooks_dir || ""}`;
    row.addEventListener("click", async () => {
      $("taskIdInput").value = task.task_id;
      activeTaskId = task.task_id;
      document.querySelectorAll(".task-row").forEach((el) => el.classList.remove("selected"));
      row.classList.add("selected");
      updateTaskSummary(task);
      $("runResult").textContent = pretty({ selected_task: task });
      try {
        const data = await api(`/api/tasks/${encodeURIComponent(task.task_id)}`);
        renderTaskVisual(data);
        $("runResult").textContent = pretty(summarizeTaskStatus(data));
        maybeOpenActiveReviewDecision(data.task);
        await taskFiles();
      } catch (err) {
        setVisual("runVisualResult", "任务状态读取失败", String(err).replace(/^Error:\s*/, ""), "error");
      }
    });
    list.appendChild(row);
  }
}

function taskProgressPercent(task) {
  if (!task) return 0;
  if (task.configuration_blocked && !task.is_generation_job) return 100;
  const progress = task.current_progress || null;
  const current = effectiveCurrentStage(task, (task.pipeline_status && task.pipeline_status.stages) || []);
  if (["completed", "completed_with_issues"].includes(task.status) || current === "completed") return 100;
  const basePercent = Number(stageProgressMilestones[current] ?? stageProgressPercent(current));
  const futureStages = progressStageOrder.slice(Math.max(0, stageOrderIndex(current) + 1));
  const nextPercent = Number(futureStages.map((stage) => stageProgressMilestones[stage]).find((value) => Number.isFinite(value)) ?? basePercent);
  let stageFraction = 0;
  if ((current === "question_understanding" || current === "knowledge_planning" || current === "evidence_selection" || current === "answer_generation") && progress && Number(progress.total || 0) > 0) {
    const total = Number(progress.total || 0);
    const completed = Number(progress.completed || 0);
    stageFraction = completed / total;
    if (current === "evidence_selection" && progress.mode === "expansion" && Number(progress.expansion_total || 0) > 0) {
      const expansionTotal = Number(progress.expansion_total || 0);
      const expansionCompleted = Number(progress.expansion_completed || 0);
      stageFraction = (completed + expansionCompleted) / Math.max(1, total + expansionTotal);
    }
  } else if (current === "figures" && progress) {
    const operationFraction = { prepare_figures: 0.3, visual_qa: 0.62, visual_qa_repair: 0.82 };
    stageFraction = Number(operationFraction[progress.operation] ?? 0.08);
  }
  if (task.status === "failed" || task.status === "running") {
    const percent = basePercent + (nextPercent - basePercent) * Math.max(0, Math.min(1, stageFraction));
    return Math.max(0, Math.min(99, Math.round(percent)));
  }
  if (Number.isFinite(Number(task.progress_percent))) return Math.max(0, Math.min(100, Number(task.progress_percent)));
  return 0;
}

function compactTaskId(taskId) {
  const text = String(taskId || "").trim();
  if (!text) return "未知";
  const timeMatch = text.match(/(\d{8}_\d{6})$/);
  if (timeMatch) return `#${timeMatch[1].slice(-6)}`;
  if (text.length <= 10) return `#${text}`;
  return `#${text.slice(-8)}`;
}

function taskSortTimestamp(task) {
  const raw = String(task?.updated_at || task?.created_at || "");
  const parsed = Date.parse(raw.replace(" ", "T"));
  if (Number.isFinite(parsed)) return parsed;
  const idTime = String(task?.task_id || "").match(/(\d{8})_(\d{6})$/);
  if (idTime) {
    const [, date, time] = idTime;
    const iso = `${date.slice(0, 4)}-${date.slice(4, 6)}-${date.slice(6, 8)}T${time.slice(0, 2)}:${time.slice(2, 4)}:${time.slice(4, 6)}`;
    const idParsed = Date.parse(iso);
    if (Number.isFinite(idParsed)) return idParsed;
  }
  return 0;
}

function taskCreatedTimestamp(task) {
  const raw = String(task?.created_at || "");
  const parsed = Date.parse(raw.replace(" ", "T"));
  if (Number.isFinite(parsed)) return parsed;
  return taskSortTimestamp(task);
}

function taskSmartRank(task) {
  if (isActionRequiredTask(task)) return 0;
  const normalized = taskFilterStatus(task?.status, task?.current_stage);
  if (normalized === "needs_input") return 0;
  if (task?.status === "paused") return 1;
  if (normalized === "running") return 1;
  if (normalized === "failed") return 2;
  if (normalized === "queued") return 3;
  if (normalized === "completed_with_issues") return 3;
  if (normalized === "cancelled") return 4;
  if (normalized === "completed") return 5;
  return 5;
}

function sortedTasks(tasks = []) {
  return [...tasks].sort((a, b) => {
    if (activeTaskSort === "name") {
      const nameDiff = shortName(a.exam_path || "").localeCompare(shortName(b.exam_path || ""), "zh-Hans-CN");
      if (nameDiff) return nameDiff;
      return taskSortTimestamp(b) - taskSortTimestamp(a);
    }
    if (activeTaskSort === "created") {
      const createdDiff = taskCreatedTimestamp(b) - taskCreatedTimestamp(a);
      if (createdDiff) return createdDiff;
      return String(b.task_id || "").localeCompare(String(a.task_id || ""));
    }
    if (activeTaskSort === "updated") {
      const updatedDiff = taskSortTimestamp(b) - taskSortTimestamp(a);
      if (updatedDiff) return updatedDiff;
      return String(b.task_id || "").localeCompare(String(a.task_id || ""));
    }
    const rankDiff = taskSmartRank(a) - taskSmartRank(b);
    if (rankDiff) return rankDiff;
    const smartTimeDiff = taskSortTimestamp(b) - taskSortTimestamp(a);
    if (smartTimeDiff) return smartTimeDiff;
    return String(b.task_id || "").localeCompare(String(a.task_id || ""));
  });
}

function taskProgressSummary(task) {
  const stages = (task?.pipeline_status && task.pipeline_status.stages) || [];
  const current = effectiveCurrentStage(task, stages);
  const progress = task?.current_progress || null;
  const percent = taskProgressPercent(task);

  if (current === "question_understanding" && progress && Number(progress.total || 0) > 0) {
    const total = Number(progress.total || 0);
    const completed = Number(progress.completed || 0);
    const active = progress.active || {};
    return {
      percent,
      stage: current,
      label: `题面理解：${completed}/${total}`,
      meta: active.phase || "整理题干、图片和表格"
    };
  }

  if (current === "knowledge_planning" && progress && Number(progress.total || 0) > 0) {
    const total = Number(progress.total || 0);
    const completed = Number(progress.completed || 0);
    return {
      percent,
      stage: current,
      label: `模型考点判断：${completed}/${total}`,
      meta: progress.parallel_enabled ? `并发 ${progress.max_workers || 1}` : stageLabel(current)
    };
  }

  if (current === "evidence_selection" && progress && Number(progress.total || 0) > 0) {
    const total = Number(progress.total || 0);
    const completed = Number(progress.completed || 0);
    const expansionTotal = Number(progress.expansion_total || 0);
    const expansionCompleted = Number(progress.expansion_completed || 0);
    if (progress.mode === "expansion" && expansionTotal > 0) {
      return {
        percent,
        stage: current,
        label: `引用证据扩建：${expansionCompleted}/${expansionTotal}`,
        meta: `模型二次确认 ${completed}/${total}`
      };
    }
    return {
      percent,
      stage: current,
      label: `模型教材引用确认：${completed}/${total}`,
      meta: stageLabel(current)
    };
  }

  if (current === "answer_generation" && progress && Number(progress.total || 0) > 0) {
    const total = Number(progress.total || 0);
    const completed = Number(progress.completed || 0);
    const currentText = progress.current_number || progress.current_question_id || "";
    const active = progress.active || {};
    const activeBits = [];
    if (active.model) activeBits.push(active.model);
    if (active.strategy) activeBits.push(active.strategy);
    if (active.status) activeBits.push(displayAttemptStatus(active.status));
    if (progress.elapsed_text) activeBits.push(`耗时 ${progress.elapsed_text}`);
    return {
      percent,
      stage: current,
      label: `结构化答案生成：${completed}/${total}`,
      meta: activeBits.length ? activeBits.join(" · ") : currentText ? `当前：${currentText}` : stageLabel(current)
    };
  }

  return {
    percent,
    stage: current,
    label: stageLabel(current || ""),
    meta: stageLabel(current || "")
  };
}

function displayAttemptStatus(status) {
  const map = {
    preparing: "准备请求",
    started: "请求中",
    succeeded: "已返回",
    failed: "已重试",
    running: "运行中"
  };
  return map[String(status || "")] || String(status || "");
}

function answerGenerationProgressText(progress, completed, total, currentText = "") {
  const active = progress?.active || {};
  const parts = [`结构化答案生成：已完成 ${completed}/${total} 题`];
  if (currentText) parts.push(`当前：${currentText}`);
  if (active.model) parts.push(`模型：${active.model}`);
  if (active.strategy) parts.push(`策略：${active.strategy}`);
  if (active.status) parts.push(`状态：${displayAttemptStatus(active.status)}`);
  if (progress?.elapsed_text) parts.push(`已耗时：${progress.elapsed_text}`);
  if (active.full_evidence_count || active.prompt_evidence_count) {
    parts.push(`依据：${active.prompt_evidence_count || 0}/${active.full_evidence_count || 0}`);
  }
  if (active.error) parts.push(`最近错误：${active.error}`);
  return parts.join("，");
}

function renderAnswerProgressDetails(progress) {
  const box = $("answerProgressDetails");
  if (!box) return;
  if (!progress || !progress.active) {
    box.classList.add("hidden");
    box.innerHTML = "";
    return;
  }

  const active = progress.active || {};
  const activeText = [
    active.model ? `模型：${active.model}` : "",
    active.strategy ? `策略：${active.strategy}` : "",
    active.status ? `状态：${displayAttemptStatus(active.status)}` : "",
    active.elapsed_text ? `耗时：${active.elapsed_text}` : ""
  ].filter(Boolean).join(" · ");
  const evidenceText = active.full_evidence_count || active.prompt_evidence_count
    ? `依据压缩：${active.prompt_evidence_count || 0}/${active.full_evidence_count || 0}`
    : "";
  const events = Array.isArray(progress.recent_events) ? progress.recent_events.slice(-4).reverse() : [];
  box.innerHTML = `
    <div class="answer-progress-active">
      <i class="fas fa-circle-notch fa-spin"></i>
      <strong>${escapeHtml(activeText || "等待模型返回")}</strong>
      ${evidenceText ? `<span>${escapeHtml(evidenceText)}</span>` : ""}
    </div>
    ${events.map((event) => `
      <div class="answer-progress-event">
        <span>${escapeHtml([event.model, event.strategy, displayAttemptStatus(event.status), event.error].filter(Boolean).join(" · "))}</span>
        <span>${escapeHtml(event.time || "")}</span>
      </div>
    `).join("")}
  `;
  box.classList.remove("hidden");
}

function updateTaskManagerStats(tasks) {
  const failedTasks = tasks.filter((task) => taskDisplayStatus(task) === "failed");
  const counts = {
    total: tasks.length,
    running: tasks.filter((task) => taskDisplayStatus(task) === "running").length,
    queued: tasks.filter((task) => taskDisplayStatus(task) === "queued").length,
    needsInput: tasks.filter((task) => taskDisplayStatus(task) === "needs_input").length,
    issues: tasks.filter((task) => taskDisplayStatus(task) === "completed_with_issues").length,
    failed: failedTasks.length,
    cancelled: tasks.filter((task) => taskDisplayStatus(task) === "cancelled").length,
    completed: tasks.filter((task) => taskDisplayStatus(task) === "completed").length
  };
  setText("taskStatTotal", counts.total);
  setText("taskStatRunning", counts.running);
  setText("taskStatQueued", counts.queued);
  setText("taskStatNeedsInput", counts.needsInput);
  setText("taskStatIssues", counts.issues);
  setText("taskStatFailed", counts.failed);
  setText("taskStatCancelled", counts.cancelled);
  setText("taskStatCompleted", counts.completed);
  let dismissedSignature = "";
  try {
    dismissedSignature = localStorage.getItem(FAILED_TASK_FEEDBACK_DISMISS_KEY) || "";
  } catch (_error) {}
  const dismissed = Boolean(failedTasks.length && dismissedSignature === failedTaskSetSignature(failedTasks));
  $("taskFailedFeedbackBar")?.classList.toggle("hidden", failedTasks.length === 0 || dismissed);
  setText("taskFailedFeedbackTitle", `${failedTasks.length} 个失败任务的诊断已自动处理`);
}

function formatTaskTimestamp(value) {
  const raw = String(value || "").trim();
  if (!raw) return "暂无时间";
  const parsed = new Date(raw.replace(" ", "T"));
  if (!Number.isFinite(parsed.getTime())) return raw;
  const pad = (number) => String(number).padStart(2, "0");
  return `${parsed.getFullYear()}-${pad(parsed.getMonth() + 1)}-${pad(parsed.getDate())} ${pad(parsed.getHours())}:${pad(parsed.getMinutes())}`;
}

function shortTaskMaterialName(value, limit = 18) {
  const text = String(value || "未命名材料").trim() || "未命名材料";
  const characters = Array.from(text);
  return characters.length > limit ? `${characters.slice(0, limit).join("")}…` : text;
}

function shortTaskModelName(value, provider = "") {
  let model = String(value || "").trim().split("/").filter(Boolean).pop() || "";
  model = model
    .replace(/-(?:preview|latest)$/i, "")
    .replace(/-(?:20\d{2}[01]\d(?:[0-3]\d)?|20\d{6})$/i, "");
  const readableSuffix = (value) => String(value || "")
    .split(/[-_]+/)
    .filter(Boolean)
    .map((part) => /^v\d/i.test(part) ? part.toUpperCase() : `${part[0].toUpperCase()}${part.slice(1).toLowerCase()}`)
    .join(" ");
  const known = [
    [/^gpt[-_]?([\d.]+)(?:[-_](sol|terra|luna))?/i, (_all, version, tier) => `GPT-${version}${tier ? ` ${tier[0].toUpperCase()}${tier.slice(1)}` : ""}`],
    [/^deepseek[-_]?(.+)?/i, (_all, version) => `DeepSeek${version ? ` ${readableSuffix(version)}` : ""}`],
    [/^gemini[-_]?(.+)?/i, (_all, version) => `Gemini${version ? ` ${readableSuffix(version)}` : ""}`],
    [/^claude[-_]?(.+)?/i, (_all, version) => `Claude${version ? ` ${readableSuffix(version)}` : ""}`],
  ];
  for (const [pattern, format] of known) {
    const match = model.match(pattern);
    if (match) return shortTaskMaterialName(format(...match), 22);
  }
  if (model) return shortTaskMaterialName(model.replace(/[-_]+/g, " "), 22);
  return shortTaskMaterialName(displayProviderName(provider) || "默认模型", 22);
}

function taskManagerTitle(task, kindMeta) {
  const materialName = task.description || task.exam_path || "未命名材料";
  const primaryModel = task.model_label || task.answer_model || task.model || "";
  return `${kindMeta.label} · ${shortTaskModelName(primaryModel, task.provider)} · ${shortTaskMaterialName(materialName)}`;
}

function renderTaskManagerPagination(total) {
  const pagination = $("taskManagerPagination");
  if (!pagination) return;
  const pages = Math.max(1, Math.ceil(total / TASK_MANAGER_PAGE_SIZE));
  taskManagerPage = Math.min(Math.max(1, taskManagerPage), pages);
  pagination.classList.toggle("hidden", total <= TASK_MANAGER_PAGE_SIZE);
  if (total <= TASK_MANAGER_PAGE_SIZE) {
    pagination.innerHTML = "";
    return;
  }
  pagination.innerHTML = `
    <span>第 ${taskManagerPage}/${pages} 页 · 共 ${total} 个任务</span>
    <div>
      <button type="button" data-task-page="${taskManagerPage - 1}" ${taskManagerPage <= 1 ? "disabled" : ""} title="上一页"><i class="fas fa-chevron-left"></i></button>
      <button type="button" data-task-page="${taskManagerPage + 1}" ${taskManagerPage >= pages ? "disabled" : ""} title="下一页"><i class="fas fa-chevron-right"></i></button>
    </div>`;
  pagination.querySelectorAll("[data-task-page]").forEach((button) => {
    button.addEventListener("click", () => {
      taskManagerPage = Number(button.dataset.taskPage || 1);
      renderTaskManager();
      $("taskManagerList")?.scrollIntoView({ block: "start" });
    });
  });
}

function completedGenerationTaskMessage(task = {}) {
  const completion = practiceCompletionContract(task);
  const { generated_count: generatedCount, total_count: totalCount, unfinished_count: unfinishedCount } = completion;
  if (practiceCompletionHas(task, "configuration_blocked")) return `共 ${totalCount} 题：已生成 ${generatedCount} 题，${unfinishedCount} 题待配置`;
  if (practiceCompletionHas(task, "generation_incomplete")) return `共 ${totalCount} 题：已生成 ${generatedCount} 题，${unfinishedCount} 题未完成`;
  const review = completion.issues.find((item) => item.code === "review_required");
  if (review) return `题目已生成，${Math.max(1, review.count)} 项需复核`;
  const warning = completion.issues.find((item) => item.code === "warning_only");
  if (warning) return `题目已生成，含 ${Math.max(1, warning.count)} 项非阻断提示`;
  return "题目已生成并保存";
}

function renderTaskManager(tasks = latestTasks) {
  const list = $("taskManagerList");
  const empty = $("taskManagerEmpty");
  if (!list) return;
  updateTaskManagerStats(tasks);
  const filteredVisible = sortedTasks(tasks.filter((task) => {
    const statusMatch = activeTaskFilter === "all" || taskDisplayStatus(task) === activeTaskFilter;
    const kind = task.task_kind || "exam";
    return statusMatch && (activeTaskKind === "all" || kind === activeTaskKind);
  }));
  const pageCount = Math.max(1, Math.ceil(filteredVisible.length / TASK_MANAGER_PAGE_SIZE));
  taskManagerPage = Math.min(Math.max(1, taskManagerPage), pageCount);
  const pageStart = (taskManagerPage - 1) * TASK_MANAGER_PAGE_SIZE;
  const visible = filteredVisible.slice(pageStart, pageStart + TASK_MANAGER_PAGE_SIZE);
  list.innerHTML = "";
  if (empty) empty.classList.toggle("hidden", visible.length > 0);
  renderTaskManagerPagination(filteredVisible.length);
  for (const [index, task] of visible.entries()) {
    const normalized = taskDisplayStatus(task);
    const reviewPending = isActionRequiredTask(task);
    const approvalText = task.error_presentation?.kind === "review_rejected" ? "结构被拒绝 · 待修正" : (isExamStructureReviewTask(task) ? "需确认题目结构" : "需要人工处理");
    const baseStatusMeta = taskStatusMeta(normalized);
    const progress = taskProgressSummary(task);
    const percent = progress.percent;
    const taskHealth = task.health || {};
    const taskHealthState = String(taskHealth.health_status || "unknown");
    const taskHealthMeta = healthPresentation(taskHealthState);
    const stageText = progress.label;
    const taskId = task.task_id || "";
    const kind = task.task_kind || "exam";
    const generationTask = Boolean(task.is_generation_task);
    const formatTask = Boolean(task.is_format_task);
    const completion = generationTask && !task.is_generation_job ? practiceCompletionContract(task) : null;
    const meta = completion && ["completed", "completed_with_issues"].includes(normalized)
      ? { icon: completion.primary.icon, label: completion.display_label }
      : baseStatusMeta;
    const kindMeta = {
      exam: { label: "真题解析", icon: "fas fa-book-open" },
      practice: { label: "按题出题", icon: "fas fa-layer-group" },
      knowledge: { label: "知识点出题", icon: "fas fa-lightbulb" },
      format: { label: "格式审查", icon: "fas fa-file-alt" },
    }[kind] || { label: "其他任务", icon: "fas fa-file-alt" };
    const title = taskManagerTitle(task, kindMeta);
    const phaseLabels = { analyze: "范围解析", plan: "蓝图设计", generate_from_plan: "题目生成" };
    const phaseText = Array.isArray(task.steps) && task.steps.length
      ? task.steps.map((phase) => phase.label || phaseLabels[phase.operation] || phase.operation).filter(Boolean).join(" → ")
      : "题目生成";
    const subtitle = formatTask
      ? `${task.format_profile_label || "格式标准"} · ${task.mode_label || "格式审查与修改"}`
      : generationTask
      ? (task.is_generation_job ? `${task.description || kindMeta.label} · ${phaseText}` : `${task.description || kindMeta.label} · 共 ${Number(task.total_count ?? task.question_count ?? 0)} 题：已生成 ${Number(task.generated_count ?? task.question_count ?? 0)} 题`)
      : `教材：${shortName(task.textbooks_dir || "教材库")}`;
    const contractQuality = task.quality_presentation;
    const qualityMeta = contractQuality ? {
      label: contractQuality.label,
      className: contractQuality.class_name,
      icon: contractQuality.icon,
    } : null;
    const errorMessage = task.error_presentation
      ? practicePublicErrorText(task.error_presentation, task.error || "")
      : (task.error || "");
    const defaultProgressMessage = formatTask
      ? (errorMessage || task.progress_message || "格式审查任务已保存")
      : generationTask
      ? (task.is_generation_job ? (errorMessage || task.progress_message || (normalized === "queued" ? "任务已进入等待队列" : normalized === "needs_input" ? "当前步骤已完成，等待确认后继续" : "任务正在后台执行")) : completedGenerationTaskMessage(task))
      : (errorMessage || (normalized === "queued" ? "等待开始" : stageText));
    const progressMessage = reviewPending
      ? "当前步骤已完成，等待你确认后继续。"
      : (isLiveTask(task) && taskHealth.health_status ? healthTaskSummary(task) : defaultProgressMessage);
    const currentStageText = reviewPending
      ? "等待确认"
      : (generationTask ? (task.is_generation_job ? stageLabel(task.current_stage) : phaseText) : (progress.meta || stageText));
    const displayCurrentStageText = formatTask
      ? (reviewPending ? "等待确认修改" : "格式处理完成")
      : currentStageText;
    const item = document.createElement("article");
    item.className = `task-manager-item task-manager-${normalized}`;
    item.classList.toggle("task-selection-mode", taskBulkMode);
    item.classList.toggle("task-selected", selectedTaskIds.has(taskId));
    item.dataset.status = normalized;
    item.innerHTML = `
      <div class="task-manager-main">
        ${taskBulkMode ? `<label class="task-select-control" title="选择此任务"><input type="checkbox" data-task-select="${escapeHtml(taskId)}"${selectedTaskIds.has(taskId) ? " checked" : ""}><span aria-hidden="true"><i class="fas fa-check"></i></span></label>` : ""}
        <div class="task-manager-icon task-color-${index % 6}"><i class="${kindMeta.icon}"></i></div>
        <div class="task-manager-copy">
          <h3><span>${escapeHtml(title)}</span>${generationTask ? `<button class="task-title-edit" type="button" data-action="rename-title" title="修改任务名称" aria-label="修改任务名称"><i class="fas fa-pen"></i></button>` : ""}</h3>
          <p>${escapeHtml(subtitle)}</p>
          <div class="task-manager-meta">
            <span class="task-kind-chip"><i class="${kindMeta.icon}"></i>${kindMeta.label}</span>
            <span class="task-status-chip status-${normalized}"><i class="${meta.icon}"></i>${escapeHtml(meta.label)}</span>
            ${isLiveTask(task) && taskHealth.health_status ? `<span class="task-health-chip health-${escapeHtml(taskHealthState)}"><i class="${taskHealthMeta.icon}"></i>${escapeHtml(taskHealthMeta.label)}</span>` : ""}
            ${qualityMeta && !reviewPending && qualityMeta.label !== meta.label ? `<span class="task-quality-chip quality-${qualityMeta.className}"><i class="${qualityMeta.icon}"></i>${qualityMeta.label}</span>` : ""}
            <button class="task-id-copy" type="button" data-action="copy-task-id" data-task-id="${escapeHtml(taskId)}" title="复制完整ID"><i class="fas fa-hashtag"></i><strong>${escapeHtml(compactTaskId(taskId).replace(/^#/, ""))}</strong><i class="far fa-copy task-id-copy-icon"></i></button>
            <span title="任务开始时间"><i class="far fa-clock"></i>开始于 ${escapeHtml(formatTaskTimestamp(task.created_at))}</span>
            ${generationTask ? "" : `<span><i class="fas fa-hourglass-half"></i>运行 ${escapeHtml(taskDurationText(task))}</span>`}
            <span><i class="fas fa-layer-group"></i>${escapeHtml(displayCurrentStageText)}</span>
          </div>
        </div>
      </div>
      <div class="task-manager-side">
        ${reviewPending ? `<span class="task-approval-badge"><i class="fas fa-bell"></i>${approvalText}</span>` : ""}
        <div class="task-manager-actions">${taskManagerActions(task, reviewPending)}</div>
      </div>
      <div class="task-manager-progress">
        <div>
          <span>${["completed", "completed_with_issues"].includes(normalized) ? "完成进度" : normalized === "queued" ? "等待执行" : "当前进度"}</span>
          <strong>${percent}%</strong>
        </div>
        <div class="manager-progress-track"><span style="width: ${percent}%"></span></div>
        <p>${escapeHtml(progressMessage)}</p>
      </div>
    `;
    item.addEventListener("click", (event) => {
      const selector = event.target.closest("[data-task-select]");
      if (selector) {
        selector.checked ? selectedTaskIds.add(taskId) : selectedTaskIds.delete(taskId);
        item.classList.toggle("task-selected", selector.checked);
        updateTaskBulkControls();
        return;
      }
      if (taskBulkMode) {
        selectedTaskIds.has(taskId) ? selectedTaskIds.delete(taskId) : selectedTaskIds.add(taskId);
        renderTaskManager(tasks);
        updateTaskBulkControls();
        return;
      }
      const button = event.target.closest("button");
      if (button) {
        handleTaskManagerAction(task, button.dataset.action || "detail", button);
        return;
      }
      if (formatTask) {
        openWordFormatReviewer(task.task_id);
      } else if (generationTask) {
        if (task.is_generation_job) openGenerationJob(task);
        else openGenerationTaskResult(task);
      }
      else if (["completed", "completed_with_issues"].includes(normalized)) openTaskResult(task);
      else openTaskDetail(task);
    });
    list.appendChild(item);
  }
}

function updateTaskBulkControls() {
  setText("taskBulkCount", `已选择 ${selectedTaskIds.size} 项`);
  const button = $("taskBulkDeleteBtn");
  if (button) button.disabled = selectedTaskIds.size === 0;
}

function setTaskBulkMode(enabled) {
  taskBulkMode = Boolean(enabled);
  if (!taskBulkMode) selectedTaskIds.clear();
  $("taskBulkModeBtn")?.classList.toggle("hidden", taskBulkMode);
  $("taskBulkActions")?.classList.toggle("hidden", !taskBulkMode);
  renderTaskManager();
  updateTaskBulkControls();
}

async function deleteSelectedTasks() {
  const taskIds = Array.from(selectedTaskIds);
  if (!taskIds.length) return;
  const confirmed = await platformConfirm({
    eyebrow: "任务管理",
    title: `删除所选 ${taskIds.length} 个任务？`,
    message: "任务记录、输出结果及可确认归属的过程文件将一并删除。正在运行的任务不会被强制删除。此操作无法撤销。",
    confirmText: `确认删除 ${taskIds.length} 项`,
    tone: "danger"
  });
  if (!confirmed) return;
  try {
    const result = await api("/api/tasks/bulk-delete", {
      method: "POST",
      body: JSON.stringify({ task_ids: taskIds })
    });
    setTaskBulkMode(false);
    await Promise.all([loadTasks(), loadPracticeHistory()]);
    const message = result.failed
      ? `已删除 ${result.deleted} 项，另有 ${result.failed} 项未删除。进行中的任务需先取消或等待完成。`
      : `已删除 ${result.deleted} 项，相关过程文件已一并清理。`;
    await platformAlert(message, { title: result.failed ? "部分任务未删除" : "批量删除完成", tone: result.failed ? "warning" : "success" });
  } catch (error) {
    await platformAlert(String(error).replace(/^Error:\s*/, ""), { title: "批量删除失败", tone: "danger" });
  }
}

function formatLogTime(value) {
  const text = String(value || "");
  return text.includes(" ") ? text.split(" ").slice(1).join(" ") : text || "-";
}

function formatElapsedSeconds(value) {
  const seconds = Math.max(0, Number(value || 0));
  if (seconds >= 3600) return `${Math.floor(seconds / 3600)}小时${Math.floor((seconds % 3600) / 60)}分`;
  if (seconds >= 60) return `${Math.floor(seconds / 60)}分${Math.floor(seconds % 60)}秒`;
  return `${Math.floor(seconds)}秒`;
}

function healthPresentation(status) {
  return {
    normal: { label: "正在处理", icon: "fas fa-circle-check" },
    waiting: { label: "正在等待", icon: "fas fa-hourglass-half" },
    warning: { label: "等待时间较长", icon: "fas fa-triangle-exclamation" },
    error: { label: "任务已中断", icon: "fas fa-circle-xmark" },
    unknown: { label: "暂无运行记录", icon: "fas fa-circle-question" }
  }[String(status || "unknown")] || { label: "暂无运行记录", icon: "fas fa-circle-question" };
}

function healthTaskSummary(task = {}) {
  const health = task.health || task;
  const state = String(health.health_status || "unknown");
  const completed = Number(health.completed_count || 0);
  const total = Number(health.total_count || 0);
  const progress = total > 0 ? ` · 已完成 ${completed}/${total}` : "";
  if (state === "waiting" && health.current_operation === "正在排队") return "正在排队 · 前面还有任务处理中";
  if (state === "waiting") return `正在等待模型返回${progress}`;
  if (state === "warning") return `等待时间较长${progress} · 建议继续等待或查看详情`;
  if (state === "error") return "任务已中断 · 可重新运行";
  if (state === "normal") return `运行正常${progress}`;
  return "暂无运行记录";
}

function renderSystemStatus(data) {
  const host = data?.host || {};
  const counts = data?.tasks?.counts || {};
  const health = data?.health || {};
  const service = data?.service || {};
  const models = data?.models || {};
  const runningTasks = data?.tasks?.running || [];
  const logs = data?.runtime_logs || [];
  const events = data?.task_events || [];
  const issueCount = Number(counts.error || 0);
  const healthState = String(health.status || "unknown");
  const healthMeta = healthPresentation(healthState);

  setText("systemHostName", host.name || "当前服务电脑");
  setText("systemPid", host.pid ? `PID ${host.pid}` : "-");
  setText("systemUptime", formatElapsedSeconds(service.uptime_seconds));
  setText("systemActiveCount", counts.active ?? counts.running ?? 0);
  setText("systemWaitingCount", `${counts.waiting || counts.queued || 0} / ${counts.warning || 0}`);
  setText("systemIssueCount", issueCount);
  setText(
    "systemMonitorSubtitle",
    host.access_host
      ? `当前监控地址：${host.access_host}，展示的是这台服务电脑的真实运行记录`
      : "显示当前打开服务所在电脑的运行状态"
  );
  setText("systemHealthHeadline", health.headline || healthMeta.label);
  setText("systemHealthDescription", health.errors?.length ? health.errors.join("；") : (healthState === "warning" ? "服务仍在运行，请留意等待时间较长的任务。" : "根据服务、任务和模型的真实运行记录持续更新。"));
  const overview = $("systemHealthOverview");
  if (overview) overview.className = `system-health-overview health-${healthState}`;
  const icon = $("systemHealthIcon");
  if (icon) icon.innerHTML = `<i class="${healthMeta.icon}"></i>`;

  const serviceNotice = $("systemServiceNotice");
  const serviceProblems = [
    ...(health.errors || []),
    ...((service.directories || []).filter((item) => !item.writable).map((item) => `${item.name || "目录"}不可写`)),
    service.disk?.error ? "无法读取剩余磁盘空间" : ""
  ].filter(Boolean);
  if (serviceNotice) {
    serviceNotice.classList.toggle("hidden", !serviceProblems.length);
    serviceNotice.textContent = serviceProblems.length ? `服务检查提示：${serviceProblems.join("；")}` : "";
  }

  setText("systemModelHealthLabel", models.label || "暂无调用记录");
  setText("systemModelConcurrency", `${models.active_count || 0} / ${models.concurrency_limit || 0}`);
  setText("systemModelActive", models.active_count || 0);
  setText("systemModelWaiting", models.waiting_count || 0);
  setText("systemModelSuccess", models.recent_success_count || 0);
  setText("systemModelIssues", `${models.recent_timeout_count || 0} / ${(models.recent_failure_count || 0) + (models.recent_rate_limited_count || 0)}`);
  setText("systemModelRetries", models.recent_retry_count || 0);
  $("systemModelHealthLabel")?.closest(".system-model-health")?.classList.remove("health-normal", "health-waiting", "health-warning", "health-error", "health-unknown");
  $("systemModelHealthLabel")?.closest(".system-model-health")?.classList.add(`health-${models.health_status || "unknown"}`);

  const runningBox = $("systemRunningTasks");
  if (runningBox) {
    runningBox.innerHTML = runningTasks.length
      ? runningTasks.map((task) => {
          const taskHealth = healthPresentation(task.health_status);
          const countText = Number(task.total_count || 0) > 0 ? `${Number(task.completed_count || 0)}/${Number(task.total_count || 0)}` : "处理中";
          const lastProgress = task.progress_age_seconds == null ? "暂无实际进展记录" : `${formatElapsedSeconds(task.progress_age_seconds)}前有进展`;
          return `<button type="button" class="system-running-task health-${escapeHtml(task.health_status || "unknown")}" data-task-id="${escapeHtml(task.task_id || "")}">
            <span><i class="${taskHealth.icon}"></i></span>
            <div><strong>${escapeHtml(task.title || "任务")}</strong><p>${escapeHtml(task.current_operation || healthTaskSummary(task))} · ${escapeHtml(countText)} · ${escapeHtml(lastProgress)}</p></div>
            <em>${escapeHtml(taskHealth.label)}</em>
          </button>`;
        }).join("")
      : '<div class="system-empty-line">当前没有正在运行的任务</div>';
    runningBox.querySelectorAll("[data-task-id]").forEach((button) => {
      button.addEventListener("click", async () => {
        const task = latestTasks.find((item) => item.task_id === button.dataset.taskId);
        if (task) await openTaskDetail(task, true);
        else goToPage("tasks");
      });
    });
  }

  const logBox = $("systemRecentLogs");
  if (logBox) {
    const rows = logs.slice(-8).reverse();
    logBox.innerHTML = rows.length
      ? rows
          .map(
            (row) => `
              <div class="system-log-row log-${escapeHtml(row.level || "info")}">
                <span>${escapeHtml(formatLogTime(row.time))}</span>
                <strong>${escapeHtml(row.source || "system")}</strong>
                <p>${escapeHtml(row.message || "")}</p>
              </div>
            `
          )
          .join("")
      : `<div class="system-empty-line">暂无服务日志</div>`;
  }

  const eventBox = $("systemRecentEvents");
  if (eventBox) {
    const rows = events.slice(0, 8);
    eventBox.innerHTML = rows.length
      ? rows
          .map((row) => {
            const payload = row.payload || {};
            const statusText = payload.status ? ` · ${statusLabel(payload.status)}` : "";
            const stageText = payload.current_stage ? ` · ${stageLabel(payload.current_stage)}` : "";
            return `
              <button class="system-log-row system-event-row" type="button" data-task-id="${escapeHtml(row.task_id || "")}">
                <span>${escapeHtml(formatLogTime(row.time))}</span>
                <strong>${escapeHtml(shortName(row.task_id || "任务"))}</strong>
                <p>${escapeHtml((row.event || "事件") + statusText + stageText)}</p>
              </button>
            `;
          })
          .join("")
      : `<div class="system-empty-line">暂无任务事件</div>`;
    eventBox.querySelectorAll("[data-task-id]").forEach((button) => {
      button.addEventListener("click", async () => {
        const taskId = button.dataset.taskId;
        const task = latestTasks.find((item) => item.task_id === taskId);
        if (task) await openTaskDetail(task, true);
      });
    });
  }
}

async function loadSystemStatus() {
  const [data] = await Promise.all([
    api("/api/system/status"),
    loadLanAccessInfo().catch(() => null),
    loadHybridExecutionSettings().catch(() => null)
  ]);
  renderSystemStatus(data);
  return data;
}

function renderHybridExecutionSettings(data = {}) {
  hybridExecutionSettings = data || {};
  const enabled = Boolean(data?.enabled);
  const available = Boolean(data?.available);
  const locked = Boolean(data?.environment_locked);
  const checkbox = $("hybridExecutionEnabled");
  const panel = $("hybridExecutionPanel");
  if (checkbox) {
    checkbox.checked = enabled;
    checkbox.disabled = !available || locked;
  }
  panel?.classList.toggle("hybrid-mode", enabled);
  panel?.classList.toggle("local-mode", !enabled);
  const hostText = data?.server_host ? `服务器：${data.server_host}。` : "";
  setText(
    "hybridExecutionLabel",
    enabled ? "混合云服务器执行" : "本机执行（默认）"
  );
  setText(
    "hybridExecutionHint",
    !available
      ? "当前安装包未配置混合云服务器，真题解析固定在本机执行。"
      : locked
        ? `${data.message || ""}${hostText}开关由启动环境锁定。`
        : `${data.message || ""}${hostText}`
  );
}

async function loadHybridExecutionSettings() {
  const data = await api("/api/hybrid/settings");
  renderHybridExecutionSettings(data);
  return data;
}

async function saveHybridExecutionSetting() {
  const checkbox = $("hybridExecutionEnabled");
  if (!checkbox) return;
  const requested = Boolean(checkbox.checked);
  if (requested) {
    const confirmed = await platformConfirm({
      eyebrow: "执行位置",
      title: "启用混合云服务器？",
      message: "启用后，新的真题解析任务会将必要材料上传到已配置的混合云服务器计算。按题出题、知识点出题和 Word 工具仍在本机执行。",
      confirmText: "确认启用",
      tone: "warning"
    });
    if (!confirmed) {
      checkbox.checked = Boolean(hybridExecutionSettings?.enabled);
      return;
    }
  }
  checkbox.disabled = true;
  try {
    const data = await api("/api/hybrid/settings", {
      method: "POST",
      body: JSON.stringify({ enabled: requested })
    });
    renderHybridExecutionSettings(data);
    await platformAlert(
      requested ? "新的真题解析任务将使用混合云服务器。" : "新的真题解析任务将在当前电脑执行。",
      { title: "执行位置已更新", tone: "success" }
    );
  } catch (error) {
    checkbox.checked = Boolean(hybridExecutionSettings?.enabled);
    checkbox.disabled = !hybridExecutionSettings?.available || Boolean(hybridExecutionSettings?.environment_locked);
    await platformAlert(String(error).replace(/^Error:\s*/, ""), { title: "无法修改执行位置", tone: "warning" });
  }
}

function stopSystemMonitorPolling() {
  if (systemMonitorPollTimer) clearInterval(systemMonitorPollTimer);
  systemMonitorPollTimer = null;
  systemMonitorPollInFlight = false;
}

function startSystemMonitorPolling() {
  if (systemMonitorPollTimer || currentPage !== "monitor" || document.hidden) return;
  systemMonitorPollTimer = setInterval(async () => {
    if (currentPage !== "monitor" || document.hidden || systemMonitorPollInFlight) return;
    systemMonitorPollInFlight = true;
    try {
      await loadSystemStatus();
    } catch (err) {
      console.warn("System monitor refresh failed", err);
    } finally {
      systemMonitorPollInFlight = false;
    }
  }, 10000);
}

let latestLanAccessInfo = null;

async function loadLanAccessInfo() {
  const data = await api("/api/lan/access");
  latestLanAccessInfo = data;
  const firstUrl = data?.urls?.[0] || "";
  const enabled = Boolean(data?.enabled && data?.listening_on_lan && firstUrl);
  setText("lanAccessUrl", enabled ? firstUrl : "当前未启用");
  setText("lanAccessUsername", enabled ? (data?.username || "monitor") : "—");
  setText("lanAccessPassword", enabled ? (data?.password || "已启用密码保护") : "—");
  setText(
    "lanAccessHint",
    enabled
      ? (data.warning || "同一局域网内的电脑可通过下方地址查看运行状态和日志。")
      : (data?.reason || "当前服务没有启用局域网监听。")
  );
  $("lanAccessPanel")?.classList.toggle("lan-access-disabled", !enabled);
  $("lanAccessPasswordRow")?.classList.toggle("remote-secret-hidden", !enabled || !data?.password);
  if ($("copyLanAccessBtn")) $("copyLanAccessBtn").disabled = !enabled;
  return data;
}

async function copyLanAccessInfo() {
  const data = latestLanAccessInfo || (await loadLanAccessInfo());
  const url = data?.urls?.[0] || "";
  if (!url) return;
  const lines = [
    `局域网监控地址：${url}`,
    `账号：${data?.username || "monitor"}`,
  ];
  if (data?.password) lines.push(`密码：${data.password}`);
  await copyTextToClipboard(lines.join("\n"));
  const button = $("copyLanAccessBtn");
  if (button) {
    const original = button.innerHTML;
    button.innerHTML = '<i class="fas fa-check"></i>已复制';
    setTimeout(() => { button.innerHTML = original; }, 1200);
  }
}

function taskManagerActions(task = {}, reviewPending = false) {
  const caps = task.capabilities || {};
  const actions = [];
  const add = (enabled, action, color, icon, label) => {
    if (enabled) actions.push([action, color, icon, label]);
  };
  if (task.is_format_task) {
    add(caps.view_result || caps.view_progress, "format-open", "blue-action", "fas fa-eye", reviewPending ? "查看并确认" : "查看结果");
    add(caps.download, "format-download", "green-action", "fas fa-download", "下载文件");
    add(caps.delete, "format-delete", "gray-action", "fas fa-trash", "删除");
  } else if (task.is_generation_task) {
    if (task.is_generation_job) {
      add(caps.view_result, "job-result", "blue-action", "fas fa-eye", task.operation === "plan" ? "审查蓝图" : task.operation === "analyze" ? "审查范围" : "查看题目");
      add(caps.view_progress && !caps.view_result, "job-status", "blue-action", task.status === "running" ? "fas fa-spinner fa-spin" : "fas fa-eye", task.status === "failed" ? "查看原因" : "查看进度");
      add(caps.view_quality && task.status === "failed", "job-status", "red-action", "fas fa-triangle-exclamation", "查看原因");
      add(caps.retry && practiceErrorNeedsConfiguration(task.error_presentation), "job-config", "blue-action", "fas fa-key", "检查 API 配置");
      add(caps.retry, "job-retry", "green-action", "fas fa-rotate", "从检查点重试");
      add(caps.cancel, "job-cancel", "red-action", "fas fa-times", "取消任务");
    } else {
      const completion = practiceCompletionContract(task);
      const configurationBlocked = completion.issues.some((item) => item.code === "configuration_blocked");
      const generationIncomplete = completion.issues.some((item) => item.code === "generation_incomplete");
      const resultLabel = ["review_result", "view_warnings"].includes(completion.action) ? completion.action_label : "查看结果";
      add(caps.view_result, "result", "blue-action", "fas fa-eye", resultLabel);
      add(configurationBlocked, "history-config", "blue-action", "fas fa-key", "检查 API 配置");
      add(generationIncomplete && caps.retry, "history-continue", "green-action", "fas fa-play", "继续未完成项");
      add(caps.reuse && !configurationBlocked && !generationIncomplete, "reuse", "green-action", "fas fa-rotate", "再次出题");
      add(caps.delete, "delete", "gray-action", "fas fa-trash", "删除");
    }
  } else {
    add(caps.reopen_review, "reopen-review", "red-action", "fas fa-pen-to-square", "重新打开结构确认");
    add(caps.view_progress || caps.view_detail, "detail", "blue-action", "fas fa-eye", reviewPending && !caps.reopen_review ? "去处理" : "查看详情");
    add(caps.view_quality && !reviewPending, "log", "gray-action", "fas fa-list-check", "质量与诊断");
    add(caps.pause, "pause", "yellow-action", "fas fa-pause", "暂停");
    add(caps.resume, "resume", "green-action", "fas fa-play", "继续");
    add(caps.cancel, "cancel", "red-action", "fas fa-times", "取消");
      add(caps.view_result, "result", task.status === "completed_with_issues" ? "yellow-action" : "blue-action", "fas fa-eye", task.status === "completed_with_issues" ? "审核结果" : "查看结果");
    add(caps.download, "download", "green-action", "fas fa-download", "下载交付物");
    add(caps.retry && !caps.reopen_review, "retry-exam", "green-action", "fas fa-rotate", "从检查点重跑");
    add(caps.delete, "delete", "gray-action", "fas fa-trash", "删除");
  }
  const feedbackStatus = taskDisplayStatus(task);
  if (["failed", "completed", "completed_with_issues"].includes(feedbackStatus)) {
    const reported = failedTaskFeedbackReported(task);
    actions.splice(Math.min(1, actions.length), 0, [
      "support-task",
      "blue-action",
      "fas fa-comment-dots",
      reported ? "再次反馈" : (feedbackStatus === "failed" ? "反馈此任务" : "反馈质量"),
    ]);
  }
  return actions
    .slice(0, 5)
    .map(([action, color, icon, label, disabled = false]) => `<button type="button" class="task-card-button ${color}" data-action="${action}"${disabled ? " disabled" : ""}><i class="${icon}"></i>${label}</button>`)
    .join("");
}

function generationTaskManagerActions(task = {}) {
  if (task.is_generation_job) {
    if (task.status === "completed") {
      const resultLabel = task.operation === "plan" ? "查看蓝图" : (task.operation === "analyze" ? "查看范围" : "查看题目");
      return `<button type="button" class="task-card-button blue-action" data-action="job-result"><i class="fas fa-eye"></i>${resultLabel}</button>`;
    }
    if (task.status === "failed") {
      const configAction = practiceErrorNeedsConfiguration(task.error_presentation)
        ? '<button type="button" class="task-card-button blue-action" data-action="job-config"><i class="fas fa-key"></i>检查 API 配置</button>'
        : "";
      return `<button type="button" class="task-card-button red-action" data-action="job-status"><i class="fas fa-triangle-exclamation"></i>查看原因</button>${configAction}<button type="button" class="task-card-button green-action" data-action="job-retry"><i class="fas fa-rotate"></i>重试任务</button>`;
    }
    return '<button type="button" class="task-card-button blue-action" data-action="job-status"><i class="fas fa-spinner fa-spin"></i>查看进度</button><button type="button" class="task-card-button red-action" data-action="job-cancel"><i class="fas fa-times"></i>取消任务</button>';
  }
  return [
    '<button type="button" class="task-card-button blue-action" data-action="result"><i class="fas fa-eye"></i>查看结果</button>',
    '<button type="button" class="task-card-button green-action" data-action="reuse"><i class="fas fa-rotate"></i>再次出题</button>',
    '<button type="button" class="task-card-button gray-action" data-action="delete"><i class="fas fa-trash"></i>删除</button>',
  ].join("");
}

async function copyTextToClipboard(text) {
  const value = String(text || "");
  if (!value) return false;
  if (navigator.clipboard?.writeText) {
    await navigator.clipboard.writeText(value);
    return true;
  }
  const input = document.createElement("textarea");
  input.value = value;
  input.setAttribute("readonly", "");
  input.style.position = "fixed";
  input.style.opacity = "0";
  document.body.appendChild(input);
  input.select();
  const ok = document.execCommand("copy");
  input.remove();
  return ok;
}

async function copyTaskId(task, button) {
  const taskId = task?.task_id || button?.dataset.taskId || "";
  if (!taskId) return;
  const original = button?.innerHTML || "";
  try {
    await copyTextToClipboard(taskId);
    if (button) {
      button.classList.add("copied");
      button.innerHTML = '<i class="fas fa-check"></i><strong>已复制</strong>';
      setTimeout(() => {
        button.classList.remove("copied");
        button.innerHTML = original;
      }, 1200);
    }
  } catch (err) {
    if (button) {
      button.classList.add("copy-failed");
      button.innerHTML = '<i class="fas fa-exclamation-circle"></i><strong>复制失败</strong>';
      setTimeout(() => {
        button.classList.remove("copy-failed");
        button.innerHTML = original;
      }, 1200);
    }
  }
}

async function handleTaskManagerAction(task, action, button = null) {
  if (!task?.task_id) return;
  if (action === "copy-task-id") {
    await copyTaskId(task, button);
    return;
  }
  if (action === "support-task") {
    await submitTaskSupportFeedback(task, button);
    return;
  }
  if (task.is_format_task) {
    if (action === "format-open") openWordFormatReviewer(task.task_id);
    else if (action === "format-download") downloadWordFormatTask(task);
    else if (action === "format-delete") await deleteWordFormatTask(task);
    return;
  }
  if (action === "rename-title" && task.is_generation_task) {
    await renameGenerationTask(task);
    return;
  }
  if (task.is_generation_task) {
    if (task.is_generation_job && (action === "job-status" || action === "job-result")) await openGenerationJob(task);
    else if (task.is_generation_job && action === "job-config") goToPage("keys");
    else if (task.is_generation_job && action === "job-retry") await retryGenerationJob(task);
    else if (task.is_generation_job && action === "job-cancel") await cancelGenerationJob(task);
    else if (!task.is_generation_job && action === "history-config") goToPage("keys");
    else if (!task.is_generation_job && action === "history-continue") await continuePracticeHistory(task.task_id, null, task.task_kind);
    else if (action === "result") await openGenerationTaskResult(task);
    else if (action === "reuse") await reuseGenerationTask(task);
    else if (action === "delete") await deleteGenerationTask(task);
    return;
  }
  if (action === "detail" || action === "log") {
    await openTaskDetail(task, action === "log");
    return;
  }
  if (action === "result") {
    await openTaskResult(task);
    return;
  }
  if (action === "download") {
    await downloadTaskResult(task);
    return;
  }
  if (action === "retry-exam" || action === "reopen-review") {
    await retryExamTask(task, action === "reopen-review");
    return;
  }
  if (["pause", "resume", "cancel", "move-up"].includes(action)) {
    await controlTask(task.task_id, action);
    return;
  }
  if (action === "delete") {
    await deleteTaskFromManager(task);
  }
}

async function renameGenerationTask(task) {
  const currentTitle = String(task.description || task.exam_path || "").trim();
  const title = await platformPrompt({
    eyebrow: "任务管理",
    title: "修改任务名称",
    message: "该名称用于区分这次出题任务，后续步骤和最终结果会保持一致。",
    inputLabel: "任务名称",
    placeholder: "例如：热力学讲义",
    defaultValue: currentTitle,
    confirmText: "保存"
  });
  if (title === null) return;
  const cleanTitle = String(title).trim();
  if (!cleanTitle) {
    await platformAlert("任务名称不能为空。", { title: "无法保存", tone: "warning" });
    return;
  }
  try {
    await api(`/api/practice/tasks/${encodeURIComponent(task.task_id)}/title`, {
      method: "POST",
      body: JSON.stringify({ title: cleanTitle })
    });
    await loadTasks({ silent: true, includeLiveDetails: true });
  } catch (error) {
    await platformAlert(String(error).replace(/^Error:\s*/, ""), { title: "修改任务名称失败", tone: "danger" });
  }
}

async function cancelGenerationJob(task) {
  const confirmed = await platformConfirm({
    eyebrow: "任务控制",
    title: "取消后台出题任务？",
    message: "任务会停止接受迟到的模型结果；已保存的部分题目和蓝图仍会保留，之后可以从检查点重试。",
    confirmText: "确认取消",
    tone: "danger"
  });
  if (!confirmed) return;
  await api(`/api/practice/jobs/${encodeURIComponent(task.task_id)}/cancel`, {
    method: "POST",
    body: JSON.stringify({ reason: "用户取消出题任务" })
  });
  await loadTasks({ silent: true, includeLiveDetails: true });
}

async function retryExamTask(task, reopenReview = false) {
  const presentation = task.error_presentation || {};
  const confirmed = await platformConfirm({
    eyebrow: reopenReview ? "结构确认" : "任务恢复",
    title: reopenReview ? "重新打开题目结构确认？" : "从已保存内容重新运行？",
    message: reopenReview
      ? "平台会重新读取真题并回到结构确认步骤。原任务记录和已有产物会保留，可继续对照。"
      : `${presentation.retry_hint || "平台会复用可用的中间产物并重新执行后续质量检查。"} 本次操作会产生新的模型调用记录。`,
    confirmText: reopenReview ? "重新打开确认" : "开始重新运行",
    tone: reopenReview ? "warning" : "primary"
  });
  if (!confirmed) return;
  await api(`/api/tasks/${encodeURIComponent(task.task_id)}/run`, {
    method: "POST",
    body: JSON.stringify({ no_model: false, reuse_fragments: !reopenReview, render: true })
  });
  await openTaskDetail(task);
  startTaskPolling(task.task_id);
}

async function retryGenerationJob(task) {
  const requestedSessionVersion = practiceSessionVersion;
  const failed = await api(`/api/practice/jobs/${encodeURIComponent(task.task_id)}?detail=1`);
  if (requestedSessionVersion !== practiceSessionVersion) return;
  if (!failed.payload || !failed.operation) throw new Error("原任务参数不完整，无法自动重试。");
  const operationHints = {
    analyze: "将从范围解析重新执行，不复用尚未确认的范围结果。",
    plan: "将复用原始材料和已确认范围，重新设计蓝图。",
    generate_from_plan: "将复用已确认蓝图和可用的部分题目，只对缺失或失败内容继续调用模型。"
  };
  const presentation = failed.error_presentation || task.error_presentation || {};
  const confirmed = await platformConfirm({
    eyebrow: "任务恢复",
    title: presentation.title || "重试出题任务？",
    message: `${operationHints[failed.operation] || "将从当前步骤重新执行。"} ${presentation.retry_hint || "重试会新增模型调用记录，原失败记录会保留。"}`,
    confirmText: "确认重试",
    tone: "warning"
  });
  if (!confirmed) return;
  if (requestedSessionVersion !== practiceSessionVersion) return;
  beginNewPracticeSession();
  const sessionVersion = practiceSessionVersion;
  latestPracticeRequest = { ...failed.payload, resume_from_job_id: failed.job_id, reset_generation_retry_state: true };
  restorePracticePreferenceOrders(latestPracticeRequest);
  syncPracticeSourceContentPreference(latestPracticeRequest.include_source_content_in_generation !== false);
  latestPracticePlan = failed.payload?.plan || null;
  setPracticeWorkspaceMode(failed.task_kind === "knowledge" ? "knowledge" : "exam");
  goToPage("practice");
  const labels = {
    analyze: "正在重新解析考点与范围",
    plan: "正在重新设计蓝图",
    generate_from_plan: "正在重新生成完整练习"
  };
  showPracticeOperationLoading(labels[failed.operation] || "正在重试任务", failed.operation);
  const finished = await submitPracticeJob(failed.operation, latestPracticeRequest);
  if (sessionVersion !== practiceSessionVersion) return;
  rememberPracticeJob("");
  if (finished.operation === "analyze") renderPracticeSourceSelection(finished.result);
  else if (finished.operation === "plan") renderPracticePlan(finished.result);
  else renderPracticeResults(finished.result);
  await loadTasks({ silent: true, includeLiveDetails: true });
}

async function continuePracticeHistory(historyId, button = null, taskKind = "") {
  const targetHistoryId = String(historyId || "").trim();
  if (!targetHistoryId) throw new Error("缺少需要继续的练习记录。");
  const requestedSessionVersion = practiceSessionVersion;
  const confirmed = await platformConfirm({
    eyebrow: "继续未完成项",
    title: "已完成 API 配置检查？",
    message: "平台只会继续尚未完成的题目；已成功题目将直接复用，不会重复调用或覆盖。",
    confirmText: "继续未完成项",
    tone: "primary"
  });
  if (!confirmed || requestedSessionVersion !== practiceSessionVersion) return;
  if (button) button.disabled = true;
  try {
    // This ID identifies one explicit user attempt. Replaying the same HTTP
    // request reuses its terminal result, while a new confirmed click after a
    // failure gets a fresh attempt. Server-side continuation_key still owns
    // cross-tab and concurrent active-job deduplication.
    const continuationAttemptId = newPracticeBatchId();
    beginNewPracticeSession();
    const sessionVersion = practiceSessionVersion;
    setPracticeWorkspaceMode(taskKind === "knowledge" || latestPracticeSet?.source_mode === "knowledge" ? "knowledge" : "exam");
    goToPage("practice");
    showPracticeOperationLoading("正在继续未完成题目", "generate_from_plan");
    const started = await api(`/api/practice/history/${encodeURIComponent(targetHistoryId)}/continue`, {
      method: "POST",
      body: JSON.stringify({ continuation_attempt_id: continuationAttemptId })
    });
    if (sessionVersion !== practiceSessionVersion) return;
    rememberPracticeJob(started.job_id);
    const finished = await waitForPracticeJob(started.job_id);
    if (sessionVersion !== practiceSessionVersion) return;
    rememberPracticeJob("");
    setPracticeWorkspaceMode(finished.result?.source_mode === "knowledge" ? "knowledge" : "exam");
    renderPracticeResults(finished.result);
    await loadTasks({ silent: true, includeLiveDetails: true });
  } catch (error) {
    await platformAlert(String(error).replace(/^Error:\s*/, ""), { title: "继续未完成项失败", tone: "danger" });
  } finally {
    if (button) button.disabled = false;
  }
}

async function openGenerationJob(task) {
  invalidatePracticeRecoveryObserver();
  const sessionVersion = practiceSessionVersion + 1;
  practiceSessionVersion = sessionVersion;
  rememberPracticeJob("");
  try {
    const job = await api(`/api/practice/jobs/${encodeURIComponent(task.task_id)}?detail=1`);
    if (sessionVersion !== practiceSessionVersion) return;
    if (job.status === "failed") {
      const presentation = job.error_presentation || task.error_presentation || {};
      const configurationRequired = practiceErrorNeedsConfiguration(presentation);
      const nextAction = await platformConfirm({
        eyebrow: "任务未完成",
        title: presentation.title || "出题失败",
        message: practicePublicErrorText(presentation, job.error || "后台出题任务失败。"),
        tone: "danger",
        confirmText: configurationRequired ? "检查 API 配置" : "从检查点重试",
        cancelText: "暂不处理"
      });
      if (nextAction && configurationRequired) goToPage("keys");
      else if (nextAction) await retryGenerationJob(task);
      return;
    }
    latestPracticeRequest = job.payload || latestPracticeRequest;
    restorePracticePreferenceOrders(latestPracticeRequest);
    syncPracticeSourceContentPreference(latestPracticeRequest?.include_source_content_in_generation !== false);
    latestPracticePlan = job.payload?.plan || latestPracticePlan;
    setPracticeWorkspaceMode(job.task_kind === "knowledge" ? "knowledge" : "exam");
    goToPage("practice");
    if (job.status !== "completed") {
      showPracticeOperationLoading(
        job.operation === "analyze" ? "正在后台解析考点与范围" : (job.operation === "plan" ? "正在后台生成蓝图" : "正在后台生成完整练习"),
        job.operation
      );
      rememberPracticeJob(job.job_id);
      waitForPracticeJob(job.job_id).then((finished) => {
        if (sessionVersion !== practiceSessionVersion) return;
        latestPracticeRequest = finished.payload || latestPracticeRequest;
        restorePracticePreferenceOrders(latestPracticeRequest);
        syncPracticeSourceContentPreference(latestPracticeRequest?.include_source_content_in_generation !== false);
        if (finished.operation === "analyze") renderPracticeSourceSelection(finished.result);
        else if (finished.operation === "plan") renderPracticePlan(finished.result);
        else renderPracticeResults(finished.result);
        rememberPracticeJob("");
      }).catch((error) => {
        if (sessionVersion !== practiceSessionVersion) return;
        $("practiceError").textContent = String(error).replace(/^Error:\s*/, "");
        $("practiceError").classList.remove("hidden");
      });
      return;
    }
    if (sessionVersion !== practiceSessionVersion) return;
    if (job.operation === "analyze") renderPracticeSourceSelection(job.result);
    else if (job.operation === "plan") renderPracticePlan(job.result);
    else renderPracticeResults(job.result);
  } catch (err) {
    if (sessionVersion !== practiceSessionVersion) return;
    await platformAlert(String(err).replace(/^Error:\s*/, ""), { title: "无法打开出题任务", tone: "danger" });
  }
}

async function openGenerationTaskResult(task) {
  invalidatePracticeRecoveryObserver();
  const sessionVersion = practiceSessionVersion + 1;
  practiceSessionVersion = sessionVersion;
  rememberPracticeJob("");
  try {
    const record = await api(`/api/practice/history/${encodeURIComponent(task.task_id)}`);
    if (sessionVersion !== practiceSessionVersion) return;
    latestPracticeRequest = record.request || {};
    restorePracticePreferenceOrders(latestPracticeRequest);
    syncPracticeSourceContentPreference(latestPracticeRequest.include_source_content_in_generation !== false);
    currentPracticeHistoryId = String(record.history_id || record.data?.history_id || task.task_id || "");
    currentPracticeRevisionCount = Number(record.revision_count || record.revisions?.length || 0);
    latestPracticeSet = record.data || {};
    setPracticeWorkspaceMode(task.task_kind === "knowledge" ? "knowledge" : "exam");
    goToPage("practice");
    renderPracticeResults(latestPracticeSet);
    setPracticeStage("results");
    setPracticeStageDescription(task.task_kind === "knowledge" ? "知识点出题结果" : "按题出题结果");
  } catch (err) {
    if (sessionVersion !== practiceSessionVersion) return;
    await platformAlert(String(err).replace(/^Error:\s*/, ""), { title: "无法打开出题结果", tone: "danger" });
  }
}

async function reuseGenerationTask(task) {
  const requestedSessionVersion = practiceSessionVersion;
  try {
    const record = await api(`/api/practice/history/${encodeURIComponent(task.task_id)}`);
    if (requestedSessionVersion !== practiceSessionVersion) return;
    const request = record.request || {};
    if (task.task_kind === "knowledge") {
      openKnowledgeEntry();
      const reuseSessionVersion = practiceSessionVersion;
      await practiceWorkspaceRestorePromises.knowledge;
      if (reuseSessionVersion !== practiceSessionVersion) return;
      syncPracticeSourceContentPreference(request.include_source_content_in_generation !== false);
      if ($("knowledgeTitleInput")) $("knowledgeTitleInput").value = request.knowledge_title || "";
      const source = String(request.question_text || "").replace(/^# 知识点名称[\\s\\S]*?# 知识材料\\s*/m, "");
      if ($("knowledgeTextInput")) $("knowledgeTextInput").value = source;
      knowledgeSourceFiles = normalizeSourceFileList(request.source_files);
      renderKnowledgeFilePreview();
      setText("knowledgeError", "");
    } else {
      openPracticeEntry("exam");
      const reuseSessionVersion = practiceSessionVersion;
      await practiceWorkspaceRestorePromises.exam;
      if (reuseSessionVersion !== practiceSessionVersion) return;
      syncPracticeSourceContentPreference(request.include_source_content_in_generation !== false);
      if ($("practiceQuestionText")) $("practiceQuestionText").value = request.question_text || "";
      practiceSourceFiles = normalizeSourceFileList(request.source_files);
      renderPracticeFilePreview();
    }
  } catch (err) {
    if (requestedSessionVersion !== practiceSessionVersion) return;
    await platformAlert(String(err).replace(/^Error:\s*/, ""), { title: "无法复用任务", tone: "danger" });
  }
}

async function deleteGenerationTask(task) {
  if (!await platformConfirm({
    eyebrow: "任务管理",
    title: "删除出题记录",
    message: `确定删除“${shortName(task.exam_path)}”这条出题记录吗？`,
    confirmText: "确认删除",
    tone: "danger"
  })) return;
  try {
    await api(`/api/practice/history/${encodeURIComponent(task.task_id)}/delete`, {
      method: "POST",
      body: JSON.stringify({}),
    });
    await Promise.all([loadTasks(), loadPracticeHistory()]);
  } catch (err) {
    await platformAlert(String(err).replace(/^Error:\s*/, ""), { title: "删除失败", tone: "danger" });
  }
}

async function controlTask(taskId, action) {
  try {
    const data = await api(`/api/tasks/${encodeURIComponent(taskId)}/control`, {
      method: "POST",
      body: JSON.stringify({ action })
    });
    if (data.message) await platformAlert(data.message, { title: "任务状态已更新" });
    await loadTasks();
  } catch (err) {
    await platformAlert(String(err).replace(/^Error:\s*/, ""), { title: "操作失败", tone: "danger" });
  }
}

async function deleteTaskFromManager(task) {
  if (!await platformConfirm({
    eyebrow: "任务管理",
    title: "删除任务及输出文件",
    message: `确定删除任务“${shortName(task.exam_path)}”及其输出文件吗？此操作无法撤销。`,
    confirmText: "确认删除",
    tone: "danger"
  })) return;
  try {
    const data = await api(`/api/tasks/${encodeURIComponent(task.task_id)}/delete`, {
      method: "POST",
      body: JSON.stringify({})
    });
    if (data.message) await platformAlert(data.message, { title: "任务已删除" });
    if (activeTaskId === task.task_id) activeTaskId = "";
    await loadTasks();
  } catch (err) {
    await platformAlert(String(err).replace(/^Error:\s*/, ""), { title: "删除失败", tone: "danger" });
  }
}

function filterTasks(filter) {
  activeTaskFilter = filter;
  taskManagerPage = 1;
  document.querySelectorAll(".task-filter-btn").forEach((button) => {
    button.classList.toggle("active", button.dataset.filter === filter);
  });
  renderTaskManager();
}

function filterTaskKind(kind) {
  activeTaskKind = ["exam", "practice", "knowledge", "format"].includes(kind) ? kind : "all";
  taskManagerPage = 1;
  document.querySelectorAll(".task-kind-btn").forEach((button) => {
    button.classList.toggle("active", button.dataset.kind === activeTaskKind);
  });
  renderTaskManager();
}

function openTaskManager(kind = "all") {
  filterTasks("all");
  filterTaskKind(kind);
  goToPage("tasks");
}

function openWordFormatReviewer(taskId = "") {
  const query = taskId ? `?task_id=${encodeURIComponent(taskId)}` : "";
  window.location.href = `/word-format${query}`;
}

function downloadWordFormatTask(task) {
  if (!task?.task_id) return;
  const link = document.createElement("a");
  link.href = `/api/word-format/tasks/${encodeURIComponent(task.task_id)}/download`;
  link.download = "";
  document.body.appendChild(link);
  link.click();
  link.remove();
}

async function deleteWordFormatTask(task) {
  if (!task?.task_id) return;
  const confirmed = await platformConfirm({
    eyebrow: "格式审查",
    title: "删除格式审查任务？",
    message: `将删除“${shortName(task.exam_path)}”的审查记录、原始副本和修改文件，此操作无法撤销。`,
    confirmText: "确认删除",
    tone: "danger"
  });
  if (!confirmed) return;
  try {
    const data = await api(`/api/word-format/tasks/${encodeURIComponent(task.task_id)}/delete`, {
      method: "POST",
      body: JSON.stringify({})
    });
    if (data.message) await platformAlert(data.message, { title: "任务已删除" });
    await loadTasks({ silent: true, includeLiveDetails: true });
  } catch (error) {
    await platformAlert(String(error).replace(/^Error:\s*/, ""), { title: "删除失败", tone: "danger" });
  }
}

async function openTaskDetail(task, showDiagnostics = false) {
  if (!task?.task_id) return;
  activeTaskId = task.task_id;
  if ($("taskIdInput")) $("taskIdInput").value = task.task_id;
  clearTaskDiagnostics();
  goToPage("task");
  try {
    const data = await api(`/api/tasks/${encodeURIComponent(task.task_id)}`);
    updateTaskSummary(data.task);
    renderTaskVisual(data);
    $("runResult").textContent = pretty(summarizeTaskStatus(data));
    maybeOpenActiveReviewDecision(data.task);
    if (showDiagnostics || data.task?.status === "failed") await loadTaskDiagnostics(task.task_id);
  } catch (err) {
    setVisual("runVisualResult", "任务状态读取失败", String(err).replace(/^Error:\s*/, ""), "error");
  }
}

async function openTaskResult(task) {
  if (!task?.task_id) return;
  activeTaskId = task.task_id;
  if ($("taskIdInput")) $("taskIdInput").value = task.task_id;
  goToPage("result");
  await Promise.allSettled([loadTaskResultView(task.task_id), taskFiles(), loadReview()]);
}

function renderDiagnosticsList(title, items, formatter, emptyText = "暂无") {
  const rows = (items || []).slice(0, 12);
  if (!rows.length) return `<section><h4>${escapeHtml(title)}</h4><p>${escapeHtml(emptyText)}</p></section>`;
  return `<section><h4>${escapeHtml(title)}</h4><ul>${rows.map((item) => `<li>${formatter(item)}</li>`).join("")}</ul></section>`;
}

function clearTaskDiagnostics() {
  const panel = $("diagnosticsPanel");
  if (!panel) return;
  panel.classList.add("hidden");
  $("diagnosticsSummary").textContent = "正在读取任务日志...";
  $("diagnosticsRecommendations").innerHTML = "";
  $("diagnosticsQuestions").innerHTML = "";
  $("diagnosticsIssues").innerHTML = "";
  $("diagnosticsFiles").innerHTML = "";
  $("diagnosticsEvents").innerHTML = "";
}

function renderTaskDiagnostics(data) {
  const panel = $("diagnosticsPanel");
  if (!panel) return;
  panel.classList.remove("hidden");
  const summary = data.summary || {};
  const statusText = data.error ? `${summary.stage || data.primary_stage_label}：${data.error}` : `${summary.stage || data.primary_stage_label} · ${data.status}`;
  $("diagnosticsSummary").innerHTML = `
    <strong>${escapeHtml(summary.title || "任务日志摘要")}</strong>
    <span>${escapeHtml(statusText)}</span>
    <small>${Number(summary.issue_count || 0)} 个问题 · ${Number(summary.warning_count || 0)} 个提示 · ${Number(summary.completed_stage_count || 0)} 个阶段已完成</small>
  `;
  $("diagnosticsRecommendations").innerHTML = renderDiagnosticsList(
    "建议排查动作",
    data.recommendations || [],
    (item) => escapeHtml(item)
  );
  $("diagnosticsQuestions").innerHTML = renderDiagnosticsList(
    "涉及题号",
    data.question_summary || [],
    (item) => {
      const count = Number(item.issue_count || 0) + Number(item.warning_count || 0);
      const messages = (item.messages || []).slice(0, 2).join("；");
      return `<strong>${escapeHtml(item.question_id)}</strong><span>${count} 项：${escapeHtml(messages)}</span>`;
    },
    "未定位到具体题号"
  );
  $("diagnosticsIssues").innerHTML = renderDiagnosticsList(
    "具体问题",
    data.issues || [],
    (item) => {
      const qid = item.question_id ? `${item.question_id} · ` : "";
      const code = item.code ? `（${item.code}）` : "";
      return `<strong>${escapeHtml(item.stage_label || item.stage)}</strong><span>${escapeHtml(qid + item.message + code)}</span>`;
    },
    "当前没有明确问题项"
  );
  $("diagnosticsFiles").innerHTML = renderDiagnosticsList(
    "相关文件",
    data.related_files || [],
    (item) => `<strong>${escapeHtml(item.name)}</strong><span>${escapeHtml(item.path || "")}</span>`,
    "暂无相关文件"
  );
  $("diagnosticsEvents").innerHTML = renderDiagnosticsList(
    "最近日志事件",
    data.recent_events || [],
    (item) => `<strong>${escapeHtml(item.time || "")}</strong><span>${escapeHtml(item.event || "")}</span>`,
    "暂无日志事件"
  );
}

async function loadTaskDiagnostics(taskId = activeTaskId) {
  const panel = $("diagnosticsPanel");
  if (panel) {
    panel.classList.remove("hidden");
    $("diagnosticsSummary").textContent = "正在分析任务日志...";
  }
  try {
    const data = await api(`/api/tasks/${encodeURIComponent(taskId)}/diagnostics`);
    if (taskId && activeTaskId && taskId !== activeTaskId) return data;
    renderTaskDiagnostics(data);
    return data;
  } catch (err) {
    if (panel) {
      $("diagnosticsSummary").innerHTML = `<strong>日志分析失败</strong><span>${escapeHtml(String(err).replace(/^Error:\s*/, ""))}</span>`;
    }
    throw err;
  }
}

async function downloadTaskResult(task) {
  if (!task?.task_id) return;
  try {
    const data = await api(`/api/tasks/${encodeURIComponent(task.task_id)}/files`);
    const files = data.files || [];
    const isReviewCandidate = task.status === "completed_with_issues"
      || task.quality_presentation?.label === "可交付待复核";
    const preferred = (isReviewCandidate
      ? files.find((file) => file.kind === "output" && file.name === "answer_book_review_candidate.docx")
      : null)
      || files.find((file) => /\.zip$/i.test(file.name || ""))
      || files.find((file) => file.kind === "output" && file.name === "answer_book.docx")
      || files.find((file) => file.kind === "output" && /\.(docx|pdf)$/i.test(file.name || ""))
      || files.find((file) => file.kind === "output")
      || files[0];
    if (!preferred?.download_url) throw new Error("当前任务还没有可下载文件");
    const link = document.createElement("a");
    link.href = preferred.download_url;
    link.download = preferred.name || "";
    document.body.appendChild(link);
    link.click();
    link.remove();
  } catch (err) {
    await platformAlert(String(err).replace(/^Error:\s*/, ""), { title: "下载失败", tone: "danger" });
  }
}

function isLiveTask(task) {
  const status = task?.status || "";
  return status === "running" || status === "paused" || taskFilterStatus(status, task?.current_stage) === "queued";
}

function reviewStageText(stage) {
  const map = {
    exam_structure_review: "题目结构确认",
    answer_coverage: "覆盖检查",
    content_quality: "质量审查",
    docx: "DOCX 审计"
  };
  return map[stage] || stage || "审查确认";
}

function questionTypeOptions(types = [], selected = "") {
  const fallback = ["选择题", "判断题", "填空题", "名词解释", "简答题", "计算题", "作图题"];
  const options = Array.from(new Set([...(types || []), ...fallback].filter(Boolean)));
  return options
    .map((type) => `<option value="${escapeHtml(type)}"${type === selected ? " selected" : ""}>${escapeHtml(type)}</option>`)
    .join("");
}

function scoreInputValue(item) {
  const value = item?.confirmed_score ?? item?.score ?? item?.suggested_score ?? "";
  return value == null ? "" : String(value);
}

function scoreFieldHtml(item, label = "确认分值", attr = "data-score-input") {
  const suggested = item?.suggested_score ? `建议：${item.suggested_score} 分` : "未识别到建议分值";
  return `
    <label class="exam-structure-field exam-structure-score-field">
      <span>${escapeHtml(label)}</span>
      <input ${attr} type="number" min="0" step="0.5" value="${escapeHtml(scoreInputValue(item))}" placeholder="必填">
      <small>${escapeHtml(suggested)}</small>
    </label>
  `;
}

const DRAWING_GENERATION_MODES = [
  { value: "code", label: "代码绘图（默认）" },
  { value: "figure_specs", label: "规则化 figure_specs" }
];

function normalizeDrawingGenerationMode(value) {
  return String(value || "").trim() === "figure_specs" ? "figure_specs" : "code";
}

function drawingGenerationModeOptions(selected = "code") {
  const normalized = normalizeDrawingGenerationMode(selected);
  return DRAWING_GENERATION_MODES
    .map((item) => `<option value="${item.value}"${item.value === normalized ? " selected" : ""}>${escapeHtml(item.label)}</option>`)
    .join("");
}

function itemHasDrawingType(item, parentType = "") {
  if ((parentType || item?.question_type || item?.inferred_question_type) === "作图题") return true;
  return (Array.isArray(item?.subquestions) ? item.subquestions : []).some((sub) => {
    if (sub?.question_type === "作图题") return true;
    return (Array.isArray(sub?.requirements) ? sub.requirements : []).some((req) => req?.question_type === "作图题");
  });
}

const DRAWING_CUE_RE = /(画出|绘制|作图|示意图|图示|标出|衍射花样|晶胞)/;

function hasDrawingCue(text) {
  return DRAWING_CUE_RE.test(String(text || ""));
}

function drawingRiskWarningHtml(text, selected) {
  const hidden = hasDrawingCue(text) && selected !== "作图题" ? "" : " hidden";
  return `<small class="exam-structure-type-risk${hidden}" data-drawing-risk>题干疑似包含作图要求，当前未选“作图题”，可能存在题型选错风险。</small>`;
}

function updateDrawingRiskWarning(container, text, selected) {
  const warning = container?.querySelector("[data-drawing-risk]");
  if (!warning) return;
  warning.classList.toggle("hidden", !(hasDrawingCue(text) && selected !== "作图题"));
}

function itemHasInputImages(item) {
  return Boolean(
    (Array.isArray(item?.image_refs) && item.image_refs.length)
    || (Array.isArray(item?.images) && item.images.length)
    || (item?.attachments && Array.isArray(item.attachments.images) && item.attachments.images.length)
  );
}

function requestHasInputImages(items = []) {
  return (items || []).some((item) => itemHasInputImages(item));
}

function rowHasDrawingType(row) {
  if (!row) return false;
  if (row.querySelector('select[data-question-type-select="parent"]')?.value === "作图题") return true;
  return Array.from(row.querySelectorAll("[data-subquestion-type], [data-requirement-type]")).some((select) => select.value === "作图题");
}

function rowsHaveDrawingType(root = document) {
  return Array.from(root.querySelectorAll(".exam-structure-review-row[data-question-id]")).some((row) => rowHasDrawingType(row));
}

function updateDrawingGenerationModeControls(root = document) {
  root.querySelectorAll(".exam-structure-review-row[data-question-id]").forEach((row) => {
    const visible = rowHasDrawingType(row);
    const wrap = row.querySelector("[data-drawing-mode-wrap]");
    const select = row.querySelector("[data-drawing-generation-mode]");
    if (!wrap || !select) return;
    wrap.classList.toggle("hidden", !visible);
    select.disabled = !visible;
    if (!select.value) select.value = "code";
  });
}

function updateExamStructureCapabilityRisk(root = document, items = []) {
  const target = $("examStructureCapabilityRisk");
  if (!target) return;
  const messages = providerCapabilityRiskMessages({
    hasImages: requestHasInputImages(items),
    hasDrawing: rowsHaveDrawingType(root)
  });
  renderModelCapabilityRisk(target, messages);
}

function updateExamStructureDrawingRisks(root = document) {
  root.querySelectorAll(".exam-structure-review-row[data-question-id]").forEach((row) => {
    const selected = row.querySelector('select[data-question-type-select="parent"]')?.value || "";
    const text = row.querySelector("[data-question-stem]")?.value || "";
    updateDrawingRiskWarning(row.querySelector(".exam-structure-type"), text, selected);
  });
  root.querySelectorAll("[data-subquestion-row]").forEach((row) => {
    const selected = row.querySelector("[data-subquestion-type]")?.value || "";
    const text = row.querySelector("[data-subquestion-stem]")?.value || "";
    updateDrawingRiskWarning(row.querySelector(".exam-structure-sub-type"), text, selected);
  });
  root.querySelectorAll("[data-requirement-row]").forEach((row) => {
    const selected = row.querySelector("[data-requirement-type]")?.value || "";
    const text = row.querySelector("[data-requirement-stem]")?.value || "";
    updateDrawingRiskWarning(row.querySelector(".exam-structure-req-type"), text, selected);
  });
  updateDrawingGenerationModeControls(root);
}

function subquestionNumberOptions(selected = "", count = 1) {
  const selectedNumber = Number(selected);
  const max = Math.max(20, count + 5, Number.isFinite(selectedNumber) ? selectedNumber : 0);
  return Array.from({ length: max }, (_, index) => String(index + 1))
    .map((number) => `<option value="${number}"${number === String(selected || "") ? " selected" : ""}>${number}</option>`)
    .join("");
}

function sortSubquestionRows(list) {
  if (!list) return;
  const rows = Array.from(list.querySelectorAll("[data-subquestion-row]"));
  rows
    .sort((a, b) => {
      const av = Number(a.querySelector("[data-subquestion-number]")?.value || 0);
      const bv = Number(b.querySelector("[data-subquestion-number]")?.value || 0);
      return av - bv;
    })
    .forEach((row) => list.appendChild(row));
}

function requirementNumberOptions(parentNumber = "1", selected = "", count = 1) {
  const prefix = `${parentNumber || "1"}.`;
  const selectedIndex = String(selected || "").startsWith(prefix) ? Number(String(selected).slice(prefix.length)) : Number(selected);
  const max = Math.max(8, count + 4, Number.isFinite(selectedIndex) ? selectedIndex : 0);
  return Array.from({ length: max }, (_, index) => `${prefix}${index + 1}`)
    .map((number) => `<option value="${escapeHtml(number)}"${number === String(selected || "") ? " selected" : ""}>${escapeHtml(number)}</option>`)
    .join("");
}

function sortRequirementRows(list) {
  if (!list) return;
  const rows = Array.from(list.querySelectorAll("[data-requirement-row]"));
  rows
    .sort((a, b) => {
      const av = String(a.querySelector("[data-requirement-number]")?.value || "");
      const bv = String(b.querySelector("[data-requirement-number]")?.value || "");
      return av.localeCompare(bv, "zh-Hans-CN", { numeric: true });
    })
    .forEach((row) => list.appendChild(row));
}

function requirementEditorRowHtml(req, types, parentType, parentNumber, reqIndex) {
  const reqNumber = req?.number || `${parentNumber || "1"}.${reqIndex + 1}`;
  const selected = req?.question_type || parentType || "简答题";
  return `
    <div class="exam-structure-requirement" data-requirement-row>
      <label class="exam-structure-field exam-structure-req-number">
        <span>编号</span>
        <select data-requirement-number>
          ${requirementNumberOptions(parentNumber, reqNumber, reqIndex + 1)}
        </select>
      </label>
      <label class="exam-structure-field exam-structure-req-stem">
        <span>作答要求</span>
        <div class="exam-structure-formula-preview" data-formula-preview>${practiceMarkdown(req?.stem || "")}</div>
        <textarea rows="2" data-requirement-stem>${escapeHtml(req?.stem || "")}</textarea>
      </label>
      <label class="exam-structure-type exam-structure-req-type">
        <span>要求题型</span>
        <select data-requirement-type>
          ${questionTypeOptions(types, selected)}
        </select>
        ${drawingRiskWarningHtml(req?.stem || "", selected)}
      </label>
      ${scoreFieldHtml(req, "要求分值", "data-requirement-score")}
      <button class="exam-structure-icon-btn" type="button" data-requirement-remove title="删除二级要求">
        <i class="fa-solid fa-trash"></i>
      </button>
    </div>
  `;
}

function renderRequirementRows(sub, types, parentType, parentNumber) {
  const requirements = Array.isArray(sub?.requirements) ? sub.requirements : [];
  return `
    <div class="exam-structure-requirement-editor" data-requirement-editor>
      <div class="exam-structure-requirement-head">
        <strong>二级拆分</strong>
        <button type="button" data-requirement-add>
          <i class="fa-solid fa-plus"></i>
          新增要求
        </button>
      </div>
      <div class="exam-structure-requirements" data-requirement-list>
        ${requirements.map((req, reqIndex) => requirementEditorRowHtml(req, types, parentType, parentNumber, reqIndex)).join("")}
      </div>
    </div>
  `;
}

function subquestionEditorRowHtml(sub, types, parentType, subIndex) {
  const subNumber = sub?.number || String(subIndex + 1);
  const selected = sub?.question_type || parentType || "简答题";
  return `
    <div class="exam-structure-subquestion" data-subquestion-row>
      <div class="exam-structure-subquestion-main">
        <label class="exam-structure-field exam-structure-sub-number">
          <span>编号</span>
          <select data-subquestion-number>
            ${subquestionNumberOptions(subNumber, subIndex + 1)}
          </select>
        </label>
        <label class="exam-structure-field exam-structure-sub-stem">
          <span>小问题干</span>
          <div class="exam-structure-formula-preview" data-formula-preview>${practiceMarkdown(sub?.stem || "")}</div>
          <textarea rows="2" data-subquestion-stem>${escapeHtml(sub?.stem || "")}</textarea>
        </label>
        <label class="exam-structure-type exam-structure-sub-type">
          <span>小问题型</span>
          <select data-subquestion-type>
            ${questionTypeOptions(types, selected)}
          </select>
          ${drawingRiskWarningHtml(sub?.stem || "", selected)}
        </label>
        ${scoreFieldHtml(sub, "小问分值", "data-subquestion-score")}
        <button class="exam-structure-icon-btn" type="button" data-subquestion-remove title="删除小问">
          <i class="fa-solid fa-trash"></i>
        </button>
      </div>
      ${renderRequirementRows(sub, types, selected, subNumber)}
    </div>
  `;
}

function renderQuestionImagePreview(item) {
  const snapshot = Array.isArray(item.question_snapshot_refs) ? item.question_snapshot_refs[0] : null;
  const fallbackImage = Array.isArray(item.image_refs) ? item.image_refs[0] : null;
  const image = snapshot?.preview_url ? snapshot : fallbackImage;
  if (!image?.preview_url) return "";
  const isSnapshot = Boolean(snapshot?.preview_url);
  const label = isSnapshot ? "原题截图" : (image.name || "原图");
  return `
    <button class="exam-structure-image-preview" type="button" data-question-image="${escapeHtml(image.preview_url)}" data-question-image-name="${escapeHtml(label)}">
      <img src="${escapeHtml(image.preview_url)}" alt="${escapeHtml(label)}">
      <span>${isSnapshot ? "原题截图" : "原图"}</span>
    </button>
  `;
}

function renderSubquestionTypeRows(item, types, parentType) {
  const subquestions = Array.isArray(item.subquestions) ? item.subquestions : [];
  return `
    <div class="exam-structure-subquestion-editor" data-subquestion-editor>
      <div class="exam-structure-subquestion-head">
        <strong>小问拆分</strong>
        <button type="button" data-subquestion-add>
          <i class="fa-solid fa-plus"></i>
          新增小问
        </button>
      </div>
      <div class="exam-structure-subquestions" data-subquestion-list>
        ${subquestions.map((sub, subIndex) => subquestionEditorRowHtml(sub, types, parentType, subIndex)).join("")}
      </div>
    </div>
  `;
}

function stemPreview(text, maxLength = 180) {
  const normalized = String(text || "").replace(/\s+/g, " ").trim();
  if (!normalized) return "未读取到题干文本";
  return normalized.length > maxLength ? `${normalized.slice(0, maxLength)}...` : normalized;
}

function showExamStructureReviewModal(request) {
  const modal = $("examStructureReviewModal");
  if (!modal) return Promise.resolve(null);
  if (examStructureReviewModalOpen) return Promise.resolve(null);
  examStructureReviewModalOpen = true;
  const items = request.items || [];
  const types = request.question_types || [];
  $("examStructureReviewCount").textContent = `${items.length} 题`;
  const subtitle = $("examStructureReviewSubtitle");
  if (subtitle) {
    subtitle.textContent = "请确认每道题抽取、题型和分值。后续解析复杂度按确认分值执行：≤2分为简洁解析，2-5分为标准解析，5-10分为展开解析，≥10分为深度解析；计算题会保留必要公式与步骤。";
  }
  $("examStructureReviewBody").innerHTML = items.length
    ? items
        .map((item, index) => {
          const selected = item.question_type || item.inferred_question_type || "简答题";
          const drawingMode = normalizeDrawingGenerationMode(item.drawing_generation_mode || "code");
          const drawingModeHidden = itemHasDrawingType(item, selected) ? "" : " hidden";
          const subCount = Number(item.subquestion_count || 0);
          return `
            <article class="exam-structure-review-row" data-question-id="${escapeHtml(item.question_id || "")}">
              <span class="exam-structure-index">${index + 1}</span>
              <div class="exam-structure-copy">
                <div class="exam-structure-row-head">
                  <strong>${escapeHtml(item.number || item.question_id || `题目 ${index + 1}`)}</strong>
                  <span>${escapeHtml(item.section || item.section_raw || "未识别分区")}</span>
                  ${subCount ? `<em>${subCount} 个小问</em>` : ""}
                </div>
                <div class="exam-structure-stem-layout">
                  <label class="exam-structure-field exam-structure-stem-field">
                    <span>题干</span>
                    <div class="exam-structure-formula-preview" data-formula-preview>${practiceMarkdown(item.stem || item.question_text || "")}</div>
                    <textarea rows="4" data-question-stem>${escapeHtml(item.stem || item.question_text || "")}</textarea>
                  </label>
                  ${renderQuestionImagePreview(item)}
                </div>
                <small>原题型判断：${escapeHtml(item.extracted_type || item.inferred_question_type || selected)}</small>
                ${renderSubquestionTypeRows(item, types, selected)}
              </div>
              <label class="exam-structure-type">
                <span>整题默认题型</span>
                <select data-question-id="${escapeHtml(item.question_id || "")}" data-question-type-select="parent">
                  ${questionTypeOptions(types, selected)}
                </select>
                ${drawingRiskWarningHtml(item.stem || item.question_text || "", selected)}
                ${scoreFieldHtml(item, "整题分值", "data-question-score")}
                <div class="exam-structure-drawing-mode${drawingModeHidden}" data-drawing-mode-wrap>
                  <span>作图流程</span>
                  <select data-drawing-generation-mode>
                    ${drawingGenerationModeOptions(drawingMode)}
                  </select>
                  <small>默认代码绘图；失败重试后再生图兜底。</small>
                </div>
              </label>
            </article>
          `;
        })
        .join("")
    : `<div class="system-empty-line">没有待确认的题目结构。</div>`;
  modal.classList.remove("hidden");
  requestAnimationFrame(() => typesetMath($("examStructureReviewBody")));
  return new Promise((resolve) => {
    const confirmBtn = $("examStructureConfirmBtn");
    const rejectBtn = $("examStructureRejectBtn");
    const body = $("examStructureReviewBody");
    const onBodyClick = (event) => {
      const addBtn = event.target.closest("[data-subquestion-add]");
      const removeBtn = event.target.closest("[data-subquestion-remove]");
      const requirementAddBtn = event.target.closest("[data-requirement-add]");
      const requirementRemoveBtn = event.target.closest("[data-requirement-remove]");
      const imageBtn = event.target.closest("[data-question-image]");
      if (imageBtn) {
        showExamStructureImageZoom(imageBtn.dataset.questionImage, imageBtn.dataset.questionImageName || "原题截图");
        return;
      }
      if (addBtn) {
        const row = addBtn.closest(".exam-structure-review-row");
        const list = row?.querySelector("[data-subquestion-list]");
        const parentSelect = row?.querySelector('select[data-question-type-select="parent"]');
        if (!row || !list) return;
        const numbers = Array.from(list.querySelectorAll("[data-subquestion-number]"))
          .map((input) => Number(input.value))
          .filter((value) => Number.isFinite(value));
        const nextNumber = String(numbers.length ? Math.max(...numbers) + 1 : list.children.length + 1);
        const temp = document.createElement("div");
        temp.innerHTML = subquestionEditorRowHtml(
          { number: nextNumber, marker: `(${nextNumber})`, stem: "" },
          types,
          parentSelect?.value || "简答题",
          list.children.length
        );
        list.appendChild(temp.firstElementChild);
        sortSubquestionRows(list);
        updateExamStructureDrawingRisks(row);
      }
      if (requirementAddBtn) {
        const subRow = requirementAddBtn.closest("[data-subquestion-row]");
        const list = subRow?.querySelector("[data-requirement-list]");
        const parentNumber = subRow?.querySelector("[data-subquestion-number]")?.value || "1";
        const subType = subRow?.querySelector("[data-subquestion-type]")?.value || "简答题";
        if (!subRow || !list) return;
        const indexes = Array.from(list.querySelectorAll("[data-requirement-number]"))
          .map((input) => {
            const value = String(input.value || "");
            return Number(value.includes(".") ? value.split(".").pop() : value);
          })
          .filter((value) => Number.isFinite(value));
        const nextNumber = `${parentNumber}.${indexes.length ? Math.max(...indexes) + 1 : list.children.length + 1}`;
        const temp = document.createElement("div");
        temp.innerHTML = requirementEditorRowHtml(
          { number: nextNumber, marker: nextNumber, stem: "" },
          types,
          subType,
          parentNumber,
          list.children.length
        );
        list.appendChild(temp.firstElementChild);
        sortRequirementRows(list);
        updateExamStructureDrawingRisks(subRow);
      }
      if (removeBtn) {
        removeBtn.closest("[data-subquestion-row]")?.remove();
      }
      if (requirementRemoveBtn) {
        requirementRemoveBtn.closest("[data-requirement-row]")?.remove();
      }
      updateExamStructureCapabilityRisk(body, items);
    };
    let formulaPreviewTimer = null;
    const onBodyChange = (event) => {
      if (event.target.matches("[data-question-stem], [data-subquestion-stem], [data-requirement-stem]")) {
        const preview = event.target.parentElement?.querySelector("[data-formula-preview]");
        if (preview) {
          preview.innerHTML = practiceMarkdown(event.target.value);
          clearTimeout(formulaPreviewTimer);
          formulaPreviewTimer = setTimeout(() => typesetMath(preview), 180);
        }
      }
      if (event.target.matches("[data-subquestion-number]")) {
        const subRow = event.target.closest("[data-subquestion-row]");
        const number = event.target.value || "1";
        subRow?.querySelectorAll("[data-requirement-number]").forEach((select, index) => {
          const suffix = String(select.value || "").split(".").pop() || String(index + 1);
          select.innerHTML = requirementNumberOptions(number, `${number}.${suffix}`, index + 1);
          select.value = `${number}.${suffix}`;
        });
        sortSubquestionRows(event.target.closest("[data-subquestion-list]"));
      }
      if (event.target.matches("[data-requirement-number]")) {
        sortRequirementRows(event.target.closest("[data-requirement-list]"));
      }
      if (
        event.target.matches('[data-question-type-select="parent"]')
        || event.target.matches("[data-question-stem]")
        || event.target.matches("[data-subquestion-type]")
        || event.target.matches("[data-subquestion-stem]")
        || event.target.matches("[data-requirement-type]")
        || event.target.matches("[data-requirement-stem]")
        || event.target.matches("[data-drawing-generation-mode]")
      ) {
        updateExamStructureDrawingRisks(body);
        updateExamStructureCapabilityRisk(body, items);
      }
    };
    const finish = (decision) => {
      if (decision === "confirm") {
        const scoreInputs = Array.from(modal.querySelectorAll("[data-question-score], [data-subquestion-score], [data-requirement-score]"));
        scoreInputs.forEach((input) => input.classList.remove("invalid"));
        const invalid = scoreInputs.find((input) => {
          const value = String(input.value || "").trim();
          const numeric = Number(value);
          return !value || !Number.isFinite(numeric) || numeric < 0;
        });
        if (invalid) {
          invalid.classList.add("invalid");
          invalid.focus();
          platformAlert("请先确认每道题、小问和二级要求的分值；分值会决定后续解析复杂度。", {
            eyebrow: "结构确认",
            title: "仍有分值未填写",
            tone: "warning"
          });
          return;
        }
      }
      const updates = Array.from(modal.querySelectorAll(".exam-structure-review-row[data-question-id]")).map((row) => {
        const parentSelect = row.querySelector('select[data-question-type-select="parent"]');
        return {
          question_id: row.dataset.questionId,
          question_type: parentSelect?.value || "简答题",
          confirmed_score: row.querySelector("[data-question-score]")?.value.trim() || "",
          drawing_generation_mode: normalizeDrawingGenerationMode(row.querySelector("[data-drawing-generation-mode]")?.value || "code"),
          stem: row.querySelector("[data-question-stem]")?.value.trim() || "",
          subquestions: Array.from(row.querySelectorAll("[data-subquestion-row]")).map((subRow, index) => ({
            number: subRow.querySelector("[data-subquestion-number]")?.value.trim() || String(index + 1),
            marker: `(${subRow.querySelector("[data-subquestion-number]")?.value.trim() || String(index + 1)})`,
            stem: subRow.querySelector("[data-subquestion-stem]")?.value.trim() || "",
            question_type: subRow.querySelector("[data-subquestion-type]")?.value || parentSelect?.value || "简答题",
            confirmed_score: subRow.querySelector("[data-subquestion-score]")?.value.trim() || "",
            requirements: Array.from(subRow.querySelectorAll("[data-requirement-row]")).map((reqRow, reqIndex) => ({
              number: reqRow.querySelector("[data-requirement-number]")?.value.trim() || `${subRow.querySelector("[data-subquestion-number]")?.value.trim() || index + 1}.${reqIndex + 1}`,
              marker: reqRow.querySelector("[data-requirement-number]")?.value.trim() || `${subRow.querySelector("[data-subquestion-number]")?.value.trim() || index + 1}.${reqIndex + 1}`,
              stem: reqRow.querySelector("[data-requirement-stem]")?.value.trim() || "",
              question_type: reqRow.querySelector("[data-requirement-type]")?.value || subRow.querySelector("[data-subquestion-type]")?.value || parentSelect?.value || "简答题",
              confirmed_score: reqRow.querySelector("[data-requirement-score]")?.value.trim() || ""
            }))
          }))
        };
      });
      modal.classList.add("hidden");
      examStructureReviewModalOpen = false;
      confirmBtn.removeEventListener("click", onConfirm);
      rejectBtn.removeEventListener("click", onReject);
      body?.removeEventListener("click", onBodyClick);
      body?.removeEventListener("change", onBodyChange);
      body?.removeEventListener("input", onBodyChange);
      resolve({ decision, updates });
    };
    const onConfirm = () => finish("confirm");
    const onReject = () => finish("reject");
    confirmBtn.addEventListener("click", onConfirm);
    rejectBtn.addEventListener("click", onReject);
    body?.addEventListener("click", onBodyClick);
    body?.addEventListener("change", onBodyChange);
    body?.addEventListener("input", onBodyChange);
    updateExamStructureDrawingRisks(body);
    updateExamStructureCapabilityRisk(body, items);
    confirmBtn.focus();
  });
}

function showExamStructureImageZoom(url, title = "原题截图") {
  if (!url) return;
  const existing = document.querySelector(".exam-structure-image-zoom");
  existing?.remove();
  const overlay = document.createElement("div");
  overlay.className = "exam-structure-image-zoom";
  overlay.innerHTML = `
    <div class="exam-structure-image-zoom-card">
      <header>
        <strong>${escapeHtml(title)}</strong>
        <button type="button" aria-label="关闭"><i class="fa-solid fa-xmark"></i></button>
      </header>
      <img src="${escapeHtml(url)}" alt="${escapeHtml(title)}">
    </div>
  `;
  const close = () => overlay.remove();
  overlay.addEventListener("click", (event) => {
    if (event.target === overlay || event.target.closest("button")) close();
  });
  document.body.appendChild(overlay);
}

function showReviewDecisionModal(request) {
  const modal = $("reviewDecisionModal");
  if (!modal) {
    return platformConfirm({
      eyebrow: "质量审查",
      title: request.title || "是否允许审查问题通过",
      message: request.message || "模型回修后仍存在审查问题，请确认是否继续。",
      confirmText: "允许继续",
      tone: "warning"
    });
  }
  const items = request.items || [];
  const stageName = reviewStageText(request.stage);
  const questionCount = new Set(items.map((item) => item.question_id).filter(Boolean)).size;
  $("reviewDecisionTitle").textContent = request.title || "审查问题需要确认";
  $("reviewDecisionSubtitle").textContent = request.message || "模型回修和本地修复后仍存在以下问题，请决定是否允许继续。";
  $("reviewDecisionStage").textContent = stageName;
  $("reviewDecisionStageName").textContent = stageName;
  $("reviewDecisionQuestionCount").textContent = `${questionCount || items.length || 0} 题`;
  $("reviewDecisionIssueCount").textContent = `${items.length || 0} 项`;
  $("reviewDecisionIssues").innerHTML = (items.length ? items : [{ message: "审查未通过，但未返回具体题号。", display: "该问题会写入最终审查报告。" }])
    .map((item, index) => {
      const title = item.question_id ? `题目 ${item.question_id}` : "未定位题号";
      const message = item.message || item.code || "审查未通过";
      const display = item.display || "该问题会保留在最终审查报告中。";
      return `
        <article class="review-decision-issue">
          <span>${index + 1}</span>
          <div>
            <h4>${escapeHtml(title)}</h4>
            <p>${escapeHtml(message)}</p>
            <small><strong>展示影响：</strong>${escapeHtml(display)}</small>
          </div>
        </article>
      `;
    })
    .join("");
  modal.classList.remove("hidden");
  return new Promise((resolve) => {
    const allowBtn = $("reviewDecisionAllowBtn");
    const rejectBtn = $("reviewDecisionRejectBtn");
    const finish = (allowed) => {
      modal.classList.add("hidden");
      allowBtn.removeEventListener("click", onAllow);
      rejectBtn.removeEventListener("click", onReject);
      resolve(allowed);
    };
    const onAllow = () => finish(true);
    const onReject = () => finish(false);
    allowBtn.addEventListener("click", onAllow);
    rejectBtn.addEventListener("click", onReject);
    allowBtn.focus();
  });
}

async function checkReviewDecision(taskId) {
  if (!taskId) return;
  if (currentPage !== "task" || taskId !== activeTaskId) return;
  const data = await api(`/api/tasks/${encodeURIComponent(taskId)}/review-decision`);
  if (!data.pending || !data.request) return;
  const request = data.request;
  const requestId = request.request_id || `${taskId}-${request.stage}`;
  if (handledReviewDecisionRequests.has(requestId)) return;
  handledReviewDecisionRequests.add(requestId);
  const allowed = await showReviewDecisionModal(request);
  await api(`/api/tasks/${encodeURIComponent(taskId)}/review-decision`, {
    method: "POST",
    body: JSON.stringify({
      decision: allowed ? "allow" : "reject",
      note: allowed ? "用户在前端确认允许继续。" : "用户在前端拒绝通过。"
    })
  });
  await loadTasks({ silent: true, includeLiveDetails: true });
}

async function checkExamStructureReview(taskId) {
  if (!taskId) return;
  if (currentPage !== "task" || taskId !== activeTaskId) return;
  const data = await api(`/api/tasks/${encodeURIComponent(taskId)}/exam-structure-review`);
  if (!data.pending || !data.request) return;
  const result = await showExamStructureReviewModal(data.request);
  if (!result) return;
  await api(`/api/tasks/${encodeURIComponent(taskId)}/exam-structure-review`, {
    method: "POST",
    body: JSON.stringify({
      decision: result.decision,
      updates: result.updates,
      note: result.decision === "confirm" ? "用户在前端确认题目结构、题型与分值。" : "用户在前端拒绝题目结构、题型与分值。"
    })
  });
  await loadTasks({ silent: true, includeLiveDetails: true });
}

function maybeOpenActiveReviewDecision(task) {
  if (currentPage !== "task" || task?.task_id !== activeTaskId) return;
  if (isExamStructureReviewTask(task)) {
    checkExamStructureReview(task.task_id).catch((err) => console.warn("Exam structure review check failed", err));
    return;
  }
  if (isReviewDecisionTask(task)) {
    checkReviewDecision(task.task_id).catch((err) => console.warn("Review decision check failed", err));
  }
}

async function hydrateLiveTaskDetails(tasks) {
  const liveTasks = (tasks || []).filter((task) => task?.task_id && isLiveTask(task));
  await Promise.all(liveTasks.map(async (task) => {
    try {
      const data = await api(`/api/tasks/${encodeURIComponent(task.task_id)}`);
      Object.assign(task, data.task || {});
      if (data.current_progress) task.current_progress = data.current_progress;
      if (data.pipeline_status) task.pipeline_status = data.pipeline_status;
      if (data.task?.effective_current_stage) task.effective_current_stage = data.task.effective_current_stage;
    } catch (err) {
      task.manager_refresh_error = String(err).replace(/^Error:\s*/, "");
    }
  }));
}

async function loadTasks(options = {}) {
  const { silent = false, includeLiveDetails = false } = options;
  if (!silent) $("runResult").textContent = "读取任务列表中...";
  try {
    const data = await api("/api/tasks");
    latestTasks = data.tasks || [];
    if (includeLiveDetails) await hydrateLiveTaskDetails(latestTasks);
    updateReviewNotificationBadges(latestTasks);
    renderTasks(latestTasks);
    renderTaskManager(latestTasks);
    const activeTask = latestTasks.find((task) => task.task_id === activeTaskId);
    if (activeTask) updateTaskSummary(activeTask);
    else if (!activeTaskId && latestTasks.length) {
      activeTaskId = latestTasks[0].task_id || "";
      if (activeTaskId) $("taskIdInput").value = activeTaskId;
      updateTaskSummary(latestTasks[0]);
      renderTasks(latestTasks);
    }
    if (!silent) {
      $("runResult").textContent = pretty({ task_count: latestTasks.length });
      setVisual("runVisualResult", "任务列表已刷新", `当前共有 ${latestTasks.length} 个任务。请选择一个任务查看进度或文件。`, "info");
    }
  } catch (err) {
    if (!silent) {
      $("runResult").textContent = String(err);
      setVisual("runVisualResult", "任务列表读取失败", String(err).replace(/^Error:\s*/, ""), "error");
    } else {
      throw err;
    }
  }
}

async function runTask(noModel = false, reuseFragments = false) {
  $("runResult").textContent = "任务已提交...";
  setVisual("runVisualResult", "任务已提交", "平台正在启动生产流程，稍后会自动刷新进度。", "info");
  setProgress("任务启动中，正在等待第一个阶段状态。", 3, "info");
  try {
    const taskId = $("taskIdInput").value.trim();
    const data = await api(`/api/tasks/${encodeURIComponent(taskId)}/run`, {
      method: "POST",
      body: JSON.stringify({ no_model: noModel, reuse_fragments: reuseFragments, render: $("renderCheck").checked })
    });
    activeTaskId = taskId;
    clearTaskDiagnostics();
    $("runResult").textContent = pretty(data);
    startTaskPolling(taskId);
  } catch (err) {
    $("runResult").textContent = String(err);
    setVisual("runVisualResult", "任务启动失败", String(err).replace(/^Error:\s*/, ""), "error");
    setProgress("任务没有启动成功。", 0, "error");
  }
}

function summarizeTaskStatus(data) {
  const task = data.task || {};
  const stages = (data.pipeline_status && data.pipeline_status.stages) || [];
  const lastStage = stages.length ? stages[stages.length - 1] : null;
  return {
    task_id: task.task_id,
    status: task.status,
    current_stage: task.current_stage,
    error: task.error,
    last_stage: lastStage,
    current_progress: data.current_progress,
    model_token_feedback: data.model_token_feedback || [],
    quality_summary: data.quality_summary,
    acceptance_report: data.acceptance_report
  };
}

function stageProgressPercent(stage) {
  const index = Math.max(0, stageOrderIndex(stage));
  if (stage === "completed") return 100;
  return Math.min(95, Math.round(((index + 1) / progressStageOrder.length) * 100));
}

function figureActivityLabel(event) {
  const labels = {
    stage_started: "正在准备图件",
    drawing_code_request_started: "正在生成绘图代码",
    drawing_code_request_succeeded: "绘图代码已返回",
    figure_render_started: "正在渲染图件",
    figure_rendering: "正在绘制图件",
    figure_rendered: "图件已生成",
    figure_render_completed: "图件渲染完成",
    visual_qa_started: "正在视觉审查",
    visual_qa_completed: "视觉审查已完成",
    visual_qa_repair_candidate_started: "正在生成回修候选",
    visual_qa_repair_candidate_audited: "正在复审回修候选",
    visual_qa_repair_candidate_selected: "已选定通过的回修图",
    operation_completed: "当前图件操作已完成",
    operation_failed: "图件操作失败",
    heartbeat: "图件处理仍在进行"
  };
  return labels[String(event || "")] || String(event || "图件处理中");
}

function executionStageProgress(task, current, progress, stages) {
  if (current === "completed" || task.status === "completed") {
    return { percent: 100, label: "本阶段已完成", measurable: true };
  }
  const total = Number(progress?.total || 0);
  const completed = Number(progress?.completed || 0);
  if (total > 0) {
    if (current === "evidence_selection" && progress?.mode === "expansion" && Number(progress?.expansion_total || 0) > 0) {
      const expansionTotal = Number(progress.expansion_total || 0);
      const expansionCompleted = Number(progress.expansion_completed || 0);
      const done = completed + expansionCompleted;
      const all = total + expansionTotal;
      return { percent: Math.round((done / Math.max(1, all)) * 100), label: `已完成 ${done}/${all} 项`, measurable: true };
    }
    return { percent: Math.round((completed / total) * 100), label: `已完成 ${completed}/${total} 题`, measurable: true };
  }
  if (current === "figures" && progress) {
    const operationPercent = { prepare_figures: 30, visual_qa: 62, visual_qa_repair: 82 };
    const percent = Number(operationPercent[progress.operation] ?? 12);
    return { percent, label: figureActivityLabel(progress.active_event || progress.operation), measurable: true };
  }
  const stageRows = (stages || []).filter((row) => row?.stage === current);
  const latest = stageRows[stageRows.length - 1];
  if (latest?.status === "passed") return { percent: 100, label: "本阶段已完成", measurable: true };
  if (task.status === "failed") return { percent: 100, label: "本阶段已停止", measurable: true };
  if (task.status === "paused") return { percent: 0, label: "等待人工确认", measurable: false };
  return { percent: 0, label: "正在执行", measurable: false };
}

function buildTaskExecutionDetail(task, current, progress, stages) {
  const percent = taskProgressPercent(task);
  const detail = {
    badge: stageLabel(current),
    title: stageLabel(current),
    text: "正在等待该阶段返回新的进度。",
    overallPercent: percent,
    stageProgress: executionStageProgress(task, current, progress, stages),
    metrics: [],
    events: []
  };
  const addMetric = (value) => {
    if (value) detail.metrics.push(value);
  };
  const total = Number(progress?.total || 0);
  const completed = Number(progress?.completed || 0);

  if (current === "question_understanding" && total) {
    const active = progress.active || {};
    detail.title = "正在理解题目要求";
    detail.text = "逐题整理题干、图片和表格信息，识别是否需要视觉模型及是否存在作图要求。";
    addMetric(`已处理 ${completed}/${total} 题`);
    addMetric(active.number ? `当前第 ${active.number} 题` : active.question_id ? `当前 ${active.question_id}` : "");
    addMetric(active.phase || "正在提取题面结构");
  } else if (current === "knowledge_planning" && total) {
    detail.title = "正在判断每道题的考查内容";
    detail.text = "模型会为每道题提取知识点和教材检索关键词。";
    addMetric(`已完成 ${completed}/${total} 题`);
    addMetric(progress.parallel_enabled ? `并行 ${progress.max_workers || 1} 路` : "顺序处理");
  } else if (current === "evidence_selection" && total) {
    const expansionTotal = Number(progress.expansion_total || 0);
    const expansionCompleted = Number(progress.expansion_completed || 0);
    detail.title = progress.mode === "expansion" ? "正在补充教材依据" : "正在确认教材依据";
    detail.text = progress.mode === "expansion" ? "对依据不足的题目进行二次检索和确认。" : "模型正在从候选段落中挑选可支撑答案的教材依据。";
    addMetric(`已确认 ${completed}/${total} 题`);
    if (expansionTotal) addMetric(`扩建 ${expansionCompleted}/${expansionTotal}`);
  } else if (current === "answer_generation" && total) {
    const active = progress.active || {};
    detail.title = "正在生成结构化解析";
    detail.text = "模型将教材依据组织为答案、公式和图件说明。";
    addMetric(`已完成 ${completed}/${total} 题`);
    addMetric(active.model || progress.model);
    addMetric(active.strategy ? `策略：${active.strategy}` : "");
    addMetric(progress.elapsed_text ? `耗时 ${progress.elapsed_text}` : "");
  } else if (current === "figures" && progress) {
    const activeEvent = progress.active_event || "stage_started";
    detail.badge = "生成图件";
    detail.title = figureActivityLabel(activeEvent);
    detail.text = "正在生成或审查考试所需图件；图件问题会进入候选回修和复审。";
    addMetric(progress.question_id ? `题目 ${progress.question_id}` : "");
    addMetric(progress.figure_id ? `图件 ${progress.figure_id}` : "");
    addMetric(progress.model ? `模型 ${progress.model}` : "");
    addMetric(progress.elapsed_seconds != null ? `已耗时 ${progress.elapsed_seconds} 秒` : "");
  } else if (task.status === "failed") {
    detail.title = "任务在该阶段停止";
    detail.text = task.error || "请查看问题排查区域中的失败原因。";
  } else if (current === "hybrid_preprocess") {
    detail.title = "正在本机读取题面";
    detail.text = "先在本机完成公式、图片和题目结构提取，再把不依赖 Microsoft Word 的计算交给云端。";
  } else if (current === "hybrid_upload") {
    detail.title = "正在安全上传计算资料";
    detail.text = "上传已预处理题面和教材索引；API Key、Word 文件和本机配置不会放进任务包。";
  } else if (["cloud_queue", "recovered_after_restart"].includes(current)) {
    detail.title = current === "recovered_after_restart" ? "云端正在恢复任务" : "正在等待云端处理";
    detail.text = "任务编号和队列状态已经保存；短暂断网或服务器重启不会丢失任务。";
  } else if (current === "cloud_pipeline") {
    detail.title = "云端正在生成并审查解析";
    detail.text = "模型调用、答案生成、教材依据、图件和内容质量检查正在云端执行。";
  } else if (["awaiting_download", "hybrid_download"].includes(current)) {
    detail.title = "正在取回云端结果";
    detail.text = "下载完整中间结果和诊断证据，校验无误后才会进入本机 Word 阶段。";
  } else if (current === "local_delivery") {
    detail.title = "正在本机生成并检查 Word";
    detail.text = "最终 DOCX、Microsoft Word 渲染、PDF/PNG 和正式验收都在本机完成。";
  } else if (current === "environment") {
    detail.title = "正在检查运行环境";
    detail.text = "检查文档转换、公式写入和渲染工具是否可用。";
  } else if (current === "extract_exam") {
    detail.title = "正在读取并拆分真题";
    detail.text = "提取题干、题号、图片、表格和原始排版信息。";
  } else if (current === "exam_structure_review") {
    detail.title = task.status === "paused" ? "等待确认真题结构" : "正在整理真题结构";
    detail.text = task.status === "paused" ? "请确认题目边界、题型和作图题标记，确认后才会继续解析。" : "正在检查题目边界、题型和附属图片是否对应。";
  } else if (current === "figure_schema_planning") {
    detail.title = "正在规划作图要求";
    detail.text = "确定作图题是否有可复用的专业绘图规则，以及后续采用代码绘图或模型出图。";
  } else if (current === "textbook_index") {
    detail.title = "正在加载教材索引";
    detail.text = "复用已建立的教材片段、页码映射和图片资源索引。";
  } else if (current === "retrieval") {
    detail.title = "正在检索教材候选依据";
    detail.text = "依据每题知识点和关键词，从教材索引中筛选候选段落、表格和图片。";
  } else if (current === "answer_coverage") {
    detail.title = "正在检查解析覆盖情况";
    detail.text = "核对每道题是否有答案、教材依据、计算步骤和必要图件。";
  } else if (current === "content_quality" || current === "content_quality_model_repair" || current === "content_quality_local_repair") {
    detail.title = "正在审查并修复内容";
    detail.text = "检查答案完整性、教材引用、计算过程和作图要求；仅把存在问题的题目送入修复。";
  } else if (current === "docx" || current === "render" || current === "final_acceptance") {
    detail.text = current === "docx" ? "正在写入最终 Word 文档并检查排版。" : current === "render" ? "正在生成 PDF/PNG 并检查渲染结果。" : "正在汇总全部审查结果，判断是否可以交付。";
  } else if (current === "completed") {
    detail.title = "任务已完成";
    detail.text = "全部阶段已完成，可以查看结果和交付文件。";
  }

  const health = task.health || {};
  if (health.health_status) {
    const healthState = String(health.health_status);
    if (Number(health.total_count || 0) > 0) addMetric(`实际进展 ${Number(health.completed_count || 0)}/${Number(health.total_count || 0)}`);
    if (health.active_item) addMetric(`当前 ${health.active_item}`);
    if (health.progress_age_seconds != null) addMetric(`最近进展 ${formatElapsedSeconds(health.progress_age_seconds)}前`);
    if (healthState === "waiting") {
      detail.text = health.current_operation === "正在排队" ? "正在排队，等待可用处理位置。" : "正在等待模型或耗时处理返回，任务仍在继续。";
    } else if (healthState === "warning") {
      detail.title = "等待时间较长";
      detail.text = health.warning_reason || "后台仍在运行，但较长时间没有新的业务进展。";
    } else if (healthState === "error") {
      detail.title = "任务已中断";
      detail.text = health.warning_reason || task.error || "任务已停止，请查看详情后重新运行。";
    }
  }

  const events = Array.isArray(progress?.recent_events) ? progress.recent_events.slice(-4).reverse() : [];
  if (events.length) {
    detail.events = events.map((event) => {
      const action = current === "figures" ? figureActivityLabel(event.event) : displayAttemptStatus(event.status || event.event);
      const meta = [event.question_id ? `题目 ${event.question_id}` : "", event.figure_id ? `图件 ${event.figure_id}` : "", event.model || "", event.error || ""].filter(Boolean).join(" · ");
      return { action, meta, time: event.time || "" };
    });
  } else {
    const last = latestPipelineStage(stages);
    if (last) detail.events = [{ action: `${stageLabel(last.stage)}${last.status === "passed" ? "已完成" : ""}`, meta: "", time: "刚刚" }];
  }
  return detail;
}

function renderTaskExecutionDetail(detail, status) {
  const panel = $("progressPanel");
  if (panel) panel.className = `progress-panel execution-progress result-${status === "failed" ? "error" : status === "completed" ? "ok" : "info"}`;
  const badge = $("executionStageBadge");
  if (badge) badge.textContent = detail.badge;
  setText("executionTitle", detail.title);
  const stageProgress = detail.stageProgress || { percent: 0, label: "正在执行", measurable: false };
  setText("executionProgressText", stageProgress.label);
  const stageBar = $("executionProgressBar");
  if (stageBar) {
    stageBar.style.width = stageProgress.measurable ? `${Math.max(0, Math.min(100, Number(stageProgress.percent) || 0))}%` : "38%";
    stageBar.classList.toggle("indeterminate", !stageProgress.measurable);
  }
  const metrics = $("executionMetrics");
  if (metrics) metrics.innerHTML = detail.metrics.map((item) => `<span>${escapeHtml(item)}</span>`).join("");
  const events = $("executionEvents");
  if (events) {
    events.innerHTML = detail.events.map((event) => `
      <div class="execution-event"><span><strong>${escapeHtml(event.action)}</strong>${event.meta ? `<small>${escapeHtml(event.meta)}</small>` : ""}</span><time>${escapeHtml(event.time)}</time></div>
    `).join("");
  }
}

function renderTaskProgress(data) {
  const task = data.task || {};
  const stages = (data.pipeline_status && data.pipeline_status.stages) || [];
  const current = effectiveCurrentStage(task, stages);
  const progress = data.current_progress || null;
  const status = task.status || "unknown";
  renderTaskStepList(current, status);
  const kind = status === "failed" ? "error" : status === "completed" ? "ok" : status === "running" ? "info" : "warn";
  const detail = buildTaskExecutionDetail({ ...task, current_progress: progress, pipeline_status: { stages } }, current, progress, stages);
  renderTaskExecutionDetail(detail, status);
  setProgress(detail.text, detail.overallPercent, kind);
  renderAnswerProgressDetails(current === "answer_generation" ? progress : null);
}

function renderTaskVisual(data) {
  const task = data.task || {};
  const status = task.status || "";
  const stages = (data.pipeline_status && data.pipeline_status.stages) || [];
  const current = effectiveCurrentStage(task, stages);
  renderTaskProgress(data);
  updateResultMetricsFromTask(data);
  const finalAcceptanceReport = data?.quality_summary?.final_acceptance || null;
  const formallyAccepted = dataFormalAcceptancePassed(finalAcceptanceReport);
  applyExamTaskControls(task, data.quality_summary || {});
  if (status === "completed") {
    setVisual(
      "runVisualResult",
      formallyAccepted ? "任务已完成 · 最终验收通过" : "任务已完成",
      formallyAccepted ? "可以查看解析结果并导出正式交付包。" : "请先查看或执行最终验收，再决定是否导出交付包。",
      formallyAccepted ? "ok" : "warn"
    );
  } else if (status === "completed_with_issues") {
    setVisual("runVisualResult", "任务完成但需要复核", "自动流程已结束，文件可下载并会附带风险报告，但不视为最终验收通过。", "warn");
  } else if (status === "failed") {
    setVisual("runVisualResult", "任务需要处理", `${stageLabel(current)} 阶段失败：${task.error || "请查看质量检查和技术详情。"}`, "error");
  } else if (isExamStructureReviewTask(task)) {
    setVisual("runVisualResult", "等待确认题目结构", "请核对抽取出的题目、题型与分值。确认后，后续解析复杂度会按你确认的分值执行。", "warn");
  } else if (isReviewDecisionTask(task)) {
    setVisual("runVisualResult", "任务等待审批", "审查仍有问题需要确认。请在弹窗中选择允许继续或拒绝通过。", "warn");
  } else if (status === "running") {
    setVisual("runVisualResult", "任务生成中", `正在执行：${stageLabel(current)}。页面会自动刷新。`, "info");
  } else {
    setVisual("runVisualResult", "任务待开始", "点击“开始生成”后，平台会依次完成抽题、检索、生成、审计和交付文件。", "warn");
  }
}

function updateResultMetricsFromTask(data) {
  const coverage = data?.quality_summary?.answer_coverage || null;
  const review = data?.quality_summary?.content_quality || null;
  if (coverage) {
    setText("metricQuestionCount", coverage.question_count ?? "--");
    setText("metricCoveredCount", coverage.covered_count ?? coverage.fragment_count ?? "--");
  }
  if (review) setText("metricReviewCount", review.issue_count ?? "--");
}

function stopTaskManagerPolling() {
  if (taskManagerPollTimer) clearInterval(taskManagerPollTimer);
  taskManagerPollTimer = null;
  taskManagerPollInFlight = false;
}

function startTaskManagerPolling() {
  if (taskManagerPollTimer) return;
  taskManagerPollTimer = setInterval(async () => {
    if (currentPage !== "tasks" || document.hidden || taskManagerPollInFlight) return;
    taskManagerPollInFlight = true;
    try {
      await loadTasks({ silent: true, includeLiveDetails: true });
    } catch (err) {
      console.warn("Task manager refresh failed", err);
    } finally {
      taskManagerPollInFlight = false;
    }
  }, 2500);
}

function startTaskPolling(taskId) {
  if (taskPollTimer) clearInterval(taskPollTimer);
  taskPollTimer = setInterval(async () => {
    try {
      const data = await api(`/api/tasks/${encodeURIComponent(taskId)}`);
      activeTaskId = taskId;
      updateTaskSummary(data.task);
      renderTaskVisual(data);
      $("runResult").textContent = pretty(summarizeTaskStatus(data));
      maybeOpenActiveReviewDecision(data.task);
      if (data.task && ["completed", "completed_with_issues", "failed", "cancelled"].includes(data.task.status)) {
        clearInterval(taskPollTimer);
        taskPollTimer = null;
        await loadTasks();
        await taskFiles();
        if (data.task.status === "failed" || data.task.status === "completed_with_issues") await loadTaskDiagnostics(taskId);
        else clearTaskDiagnostics();
      } else if (data.task?.status === "running" || data.task?.status === "queued" || isActionRequiredTask(data.task)) {
        clearTaskDiagnostics();
      }
    } catch (err) {
      $("runResult").textContent = String(err);
      setVisual("runVisualResult", "进度读取失败", String(err).replace(/^Error:\s*/, ""), "error");
      clearInterval(taskPollTimer);
      taskPollTimer = null;
    }
  }, 2500);
}

async function taskStatus() {
  $("runResult").textContent = "读取中...";
  try {
    const taskId = $("taskIdInput").value.trim();
    const data = await api(`/api/tasks/${encodeURIComponent(taskId)}`);
    activeTaskId = taskId;
    clearTaskDiagnostics();
    updateTaskSummary(data.task);
    renderTaskVisual(data);
    $("runResult").textContent = pretty(summarizeTaskStatus(data));
  } catch (err) {
    $("runResult").textContent = String(err);
    setVisual("runVisualResult", "进度读取失败", String(err).replace(/^Error:\s*/, ""), "error");
  }
}

async function taskQuality() {
  $("runResult").textContent = "读取审计摘要中...";
  try {
    const taskId = $("taskIdInput").value.trim();
    const data = await api(`/api/tasks/${encodeURIComponent(taskId)}`);
    activeTaskId = taskId;
    updateTaskSummary(data.task);
    renderTaskVisual(data);
    const summary = data.quality_summary || {};
    const failed = Object.values(summary).filter((item) => item && item.ok === false).length;
    const passed = Object.values(summary).filter((item) => item && item.ok === true).length;
    setVisual(
      "runVisualResult",
      failed ? "质量检查发现问题" : "质量检查摘要",
      `已通过 ${passed} 项检查${failed ? `，有 ${failed} 项需要处理。` : "，未发现阻断问题。"}`,
      failed ? "error" : "ok"
    );
    $("runResult").textContent = pretty({
      task: data.task,
      quality_summary: data.quality_summary,
      acceptance_report: data.acceptance_report
    });
  } catch (err) {
    $("runResult").textContent = String(err);
    setVisual("runVisualResult", "质量检查读取失败", String(err).replace(/^Error:\s*/, ""), "error");
  }
}

async function taskFiles() {
  $("runResult").textContent = "读取中...";
  try {
    const taskId = $("taskIdInput").value.trim();
    const data = await api(`/api/tasks/${encodeURIComponent(taskId)}/files`);
    const displayCount = renderFiles(data.files || []);
    setVisual("runVisualResult", "文件列表已更新", `当前展示 ${displayCount} 个最终交付文件；其他技术文件保留在交付包中。`, "info");
    $("runResult").textContent = pretty(data);
  } catch (err) {
    $("runResult").textContent = String(err);
    setVisual("runVisualResult", "文件读取失败", String(err).replace(/^Error:\s*/, ""), "error");
  }
}

const finalOutputFileSpecs = [
  {
    label: "最终解析 Word",
    description: "Word 文档",
    icon: "fa-file-word",
    match: (file) => file.kind === "output" && file.name === "answer_book.docx"
  },
  {
    label: "解析 PDF",
    description: "PDF 文档",
    icon: "fa-file-pdf",
    match: (file) => file.kind === "output" && file.name === "answer_book.pdf"
  },
  {
    label: "模型调用汇总",
    description: "Markdown 文档",
    icon: "fa-list-check",
    match: (file) => file.kind === "output" && file.name === "模型调用汇总.md"
  },
  {
    label: "题目依据排查",
    description: "排查表格",
    icon: "fa-table",
    match: (file) => file.name === "题目依据排查.csv"
  },
  {
    label: "审查报告文件",
    description: "Word 文档",
    icon: "fa-clipboard-check",
    match: (file) => file.kind === "output" && file.name === "question_review.docx",
    fallback: (file) => file.name === "question_review.csv"
  },
  {
    label: "作图题全流程图片",
    description: "Word 文档",
    icon: "fa-images",
    match: (file) => file.kind === "output" && file.name === "作图题全流程图片.docx"
  }
];

function finalOutputFiles(files) {
  return finalOutputFileSpecs
    .map((spec) => {
      const file = files.find(spec.match) || (spec.fallback ? files.find(spec.fallback) : null);
      return file ? { ...spec, file } : null;
    })
    .filter(Boolean);
}

function createFinalFileRow(entry) {
  const row = document.createElement("a");
  row.className = "file-row final-file-row";
  row.href = entry.file.download_url;
  row.title = entry.file.path || "";
  row.download = entry.file.name || entry.label;
  row.innerHTML = `
    <span class="final-file-icon"><i class="fas ${entry.icon}"></i></span>
    <span class="final-file-meta">
      <strong>${escapeHtml(entry.label)}</strong>
      <small>预览文件（非正式交付包） · ${escapeHtml(entry.description)} · ${formatBytes(entry.file.size || 0)}</small>
    </span>
  `;
  return row;
}

function renderFiles(files) {
  const list = $("fileList");
  const resultList = $("resultFileList");
  if (list) list.innerHTML = "";
  if (resultList) resultList.innerHTML = "";
  const visibleFiles = finalOutputFiles(files || []);
  if (!visibleFiles.length) {
    if (list) list.textContent = "暂无最终输出文件";
    if (resultList) resultList.textContent = "暂无最终输出文件";
    setText("metricFileCount", "0");
    const hint = $("finalResultHint");
    if (hint) {
      hint.className = "result-card muted-card";
      hint.innerHTML = "<strong>暂未读取到最终输出</strong><p>任务完成后会显示最终解析 Word、解析 PDF、模型调用汇总、题目依据排查、审查报告和作图题全流程图片。</p>";
    }
    return 0;
  }
  for (const entry of visibleFiles) {
    const row = createFinalFileRow(entry);
    if (list) list.appendChild(row.cloneNode(true));
    if (resultList) resultList.appendChild(row);
  }
  setText("metricFileCount", String(visibleFiles.length));
  const hint = $("finalResultHint");
  if (hint) {
    hint.className = "result-card result-info";
    hint.innerHTML = `<strong>预览文件已读取</strong><p>这些单文件用于查看和复核，不代表正式交付。确认验收状态后，请使用“导出正式交付包”获取统一交付结果。</p>`;
  }
  return visibleFiles.length;
}

function syncResultFiles() {
  const files = Array.from(document.querySelectorAll("#fileList .file-row")).map((node) => {
    const clone = node.cloneNode(true);
    return clone;
  });
  const resultList = $("resultFileList");
  if (!resultList) return;
  resultList.innerHTML = "";
  if (!files.length) {
    resultList.textContent = "还没有读取文件。任务完成后点击“查看文件”。";
    return;
  }
  for (const file of files) resultList.appendChild(file);
  setText("metricFileCount", String(files.length));
}

function resultBlock(questions, label) {
  return (questions.blocks || []).find((block) => String(block.label || "").includes(label));
}

function checkpointStatusMeta(status) {
  if (status === "reusable") return { label: "断点复用", className: "checkpoint-reusable" };
  if (status === "redrive") return { label: "待重跑", className: "checkpoint-redrive" };
  return null;
}

function isCalculationQuestion(question) {
  if (question?.type === "计算题") return true;
  return (question?.subquestions || []).some((sub) => sub?.question_type === "计算题" || sub?.confirmed_question_type === "计算题");
}

function isShortAnswerQuestion(question) {
  return question?.type === "简答题";
}

function isTermExplanationQuestion(question) {
  return question?.type === "名词解释";
}

function shouldHideTopAnswer(question) {
  return isCalculationQuestion(question) || isShortAnswerQuestion(question) || isTermExplanationQuestion(question);
}

function renderResultQuestionList(questions) {
  const list = $("resultQuestionList");
  if (!list) return;
  list.innerHTML = "";
  if (!questions.length) {
    list.textContent = "暂无题目";
    return;
  }
  for (const question of questions) {
    const checkpoint = checkpointStatusMeta(question.checkpoint_status);
    const button = document.createElement("button");
    button.type = "button";
    button.className = "result-question-item";
    button.classList.toggle("active", question.question_id === activeResultQuestionId);
    button.innerHTML = `
      <span>
        <strong>第 ${escapeHtml(question.number || question.index)} 题</strong>
        <small>${practiceMarkdown((question.stem || "").slice(0, 18))}${(question.stem || "").length > 18 ? "..." : ""}</small>
      </span>
      <div class="result-question-meta">
        <em>${escapeHtml(question.type || "题目")}</em>
        ${checkpoint ? `<em class="checkpoint-status ${checkpoint.className}">${checkpoint.label}</em>` : ""}
      </div>
    `;
    button.addEventListener("click", () => {
      activeResultQuestionId = question.question_id;
      renderTaskResultView(resultViewData);
    });
    list.appendChild(button);
  }
  requestAnimationFrame(() => typesetMath(list));
}

function renderTagList(items, fallback = "暂无") {
  const values = (items || []).filter(Boolean).slice(0, 8);
  if (!values.length) return `<p class="empty-inline">${escapeHtml(fallback)}</p>`;
  return `<div class="knowledge-tags">${values.map((item) => `<span>${escapeHtml(item)}</span>`).join("")}</div>`;
}

function renderQuestionDetail(question) {
  const detail = $("resultQuestionDetail");
  if (!detail) return;
  if (!question) {
    detail.className = "question-detail-empty";
    detail.textContent = "暂无解析内容。";
    return;
  }
  detail.className = "question-detail-content";
  const evidence = resultBlock(question, "教材依据");
  const analysis = resultBlock(question, "解析");
  const solution = resultBlock(question, "解题步骤");
  const optionAnalysis = resultBlock(question, "选项分析");
  const tips = resultBlock(question, "易错");
  const issueRows = (question.quality_issues || []).slice(0, 6);
  const checkpoint = checkpointStatusMeta(question.checkpoint_status);
  const hideTopAnswer = shouldHideTopAnswer(question);
  const isTermExplanation = isTermExplanationQuestion(question);
  const analysisTitle = isShortAnswerQuestion(question) ? "答案" : "解析";
  const directAnswerHtml = `<section class="answer-section"><h4>答案</h4><p>${practiceMarkdown(question.answer_summary || question.answer || "暂无答案")}</p></section>`;
  detail.innerHTML = `
    <div class="question-detail-header">
      <h3>第 ${escapeHtml(question.number || question.index)} 题：${escapeHtml(question.type || "题目")}</h3>
      <div class="question-detail-meta">
        ${checkpoint ? `<span class="checkpoint-status ${checkpoint.className}">${checkpoint.label}</span>` : ""}
        ${question.score ? `<span>分值：${escapeHtml(question.score)} 分</span>` : ""}
        <button class="text-button" type="button" data-result-question-feedback="${escapeHtml(question.question_id || "")}"><i class="fas fa-bug"></i>反馈此题</button>
      </div>
    </div>
    <section class="original-question-block">
      <h4>题目</h4>
      <p>${practiceMarkdown(question.stem || "暂无原题内容")}</p>
    </section>
    ${hideTopAnswer ? "" : `<section class="answer-section">
      <h4>答案</h4>
      <p>${practiceMarkdown(question.answer_summary || question.answer || "暂无答案")}</p>
    </section>`}
    <section class="knowledge-section">
      <h4><i class="fas fa-lightbulb"></i>考查知识点</h4>
      ${renderTagList(question.knowledge_points || question.key_terms, "未提取到知识点")}
    </section>
    <section class="evidence-section">
      <h4><i class="fas fa-book-open"></i>教材引用</h4>
      <p>${practiceMarkdown(evidence?.text || (question.evidence_ids || []).join("、") || "暂无教材引用")}</p>
    </section>
    ${isTermExplanation ? directAnswerHtml : `<section class="analysis-section">
      <h4>${analysisTitle}</h4>
      <p>${practiceMarkdown(analysis?.text || "暂无解析内容")}</p>
    </section>`}
    ${!isTermExplanation && isCalculationQuestion(question) && solution?.text ? `<section class="analysis-section"><h4>答案</h4><p>${practiceMarkdown(solution.text)}</p></section>` : ""}
    ${!isTermExplanation && optionAnalysis?.text ? `<section class="analysis-section"><h4>选项分析</h4><p>${practiceMarkdown(optionAnalysis.text)}</p></section>` : ""}
    ${!isTermExplanation && tips?.text ? `<section class="analysis-section"><h4>易错点及注意事项</h4><p>${practiceMarkdown(tips.text)}</p></section>` : ""}
    ${!isTermExplanation && (question.formulas || []).length ? `<section class="formula-section"><h4>相关公式</h4>${(question.formulas || []).slice(0, 8).map((formula) => `<div><span class="practice-math">\\(${escapeHtml(formula.latex || "")}\\)</span><span>${escapeHtml(formula.source_note || "")}</span></div>`).join("")}</section>` : ""}
    ${issueRows.length ? `<section class="quality-inline-section"><h4>质量提示</h4>${issueRows.map((issue) => `<p class="${issue.severity === "warning" ? "warn" : "issue"}">${escapeHtml(issue.message || "")}</p>`).join("")}</section>` : ""}
  `;
  detail.querySelector("[data-result-question-feedback]")?.addEventListener("click", (event) => {
    submitSupportFeedback("question", { question_id: question.question_id || "", task_id: activeTaskId || "" }, event.currentTarget);
  });
  requestAnimationFrame(() => typesetMath(detail));
}

function renderTaskResultView(data) {
  resultViewData = data;
  const questions = data?.questions || [];
  if (!activeResultQuestionId || !questions.some((q) => q.question_id === activeResultQuestionId)) {
    activeResultQuestionId = questions[0]?.question_id || "";
  }
  const metrics = data?.metrics || {};
  const checkpointReusableCount = Number(metrics.checkpoint_reusable_count || 0);
  const checkpointRedriveCount = Number(metrics.checkpoint_redrive_count || 0);
  setText("metricQuestionCount", metrics.question_count ?? "--");
  setText("metricCoveredCount", metrics.covered_count ?? metrics.answered_count ?? "--");
  setText("metricReviewCount", Number(metrics.issue_count || 0) + Number(metrics.warning_count || 0));
  const title = $("page-result")?.querySelector(".page-title h2");
  const subtitle = $("page-result")?.querySelector(".page-title p");
  if (title) title.textContent = `${shortName(data?.task?.exam_path || "解析结果")} - 解析结果`;
  if (subtitle) {
    const checkpointSummary = checkpointReusableCount + checkpointRedriveCount
      ? ` · 断点复用 ${checkpointReusableCount} · 待重跑 ${checkpointRedriveCount}`
      : "";
    subtitle.textContent = `共解析 ${metrics.question_count || 0} 道题目${checkpointSummary}`;
  }
  renderResultQuestionList(questions);
  renderQuestionDetail(questions.find((q) => q.question_id === activeResultQuestionId));
}

async function loadTaskResultView(taskId = activeTaskId) {
  const data = await api(`/api/tasks/${encodeURIComponent(taskId)}/result-view`);
  renderTaskResultView(data);
  return data;
}

async function hydrateResultPage() {
  const taskId = ($("taskIdInput")?.value || activeTaskId || "").trim();
  if (!taskId) {
    syncResultFiles();
    return;
  }
  activeTaskId = taskId;
  $("taskIdInput").value = taskId;
  const data = await api(`/api/tasks/${encodeURIComponent(taskId)}`);
  updateTaskSummary(data.task);
  renderTaskVisual(data);
  renderFinalAcceptanceSummary(data.task, data.quality_summary?.final_acceptance || null);
  $("runResult").textContent = pretty(summarizeTaskStatus(data));
  await loadTaskResultView(taskId);
  await taskFiles();
  await loadReview();
}

function formatBytes(size) {
  const n = Number(size || 0);
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
  return `${(n / 1024 / 1024).toFixed(1)} MB`;
}

async function finalAcceptance() {
  $("runResult").textContent = "执行最终验收中...";
  setVisual("runVisualResult", "正在执行最终验收", "平台会检查答案覆盖、公式、Word 文件和渲染结果。", "info");
  try {
    const taskId = $("taskIdInput").value.trim();
    const data = await api(`/api/tasks/${encodeURIComponent(taskId)}/final-acceptance`);
    const formallyAccepted = dataFormalAcceptancePassed(data);
    const deliveryReady = dataDeliveryReady(data);
    setVisual(
      "runVisualResult",
      formallyAccepted ? "最终验收通过" : (deliveryReady ? "完成待复核" : "最终验收未通过"),
      formallyAccepted ? "可以导出交付包。" : (deliveryReady ? "可下载含风险报告的交付包，但不视为验收通过。" : `发现 ${(data.issues || []).length} 个阻断问题，请先处理。`),
      formallyAccepted ? "ok" : (deliveryReady ? "warn" : "error")
    );
    renderFinalAcceptanceSummary({ status: formallyAccepted ? "completed" : "completed_with_issues" }, data);
    if (activeTaskId) {
      const taskData = await api(`/api/tasks/${encodeURIComponent(activeTaskId)}`);
      updateTaskSummary(taskData.task);
      applyExamTaskControls(taskData.task, taskData.quality_summary || {});
    }
    $("runResult").textContent = pretty(data);
  } catch (err) {
    $("runResult").textContent = String(err);
    setVisual("runVisualResult", "最终验收失败", String(err).replace(/^Error:\s*/, ""), "error");
  }
}

async function deliveryPackage() {
  $("runResult").textContent = "导出交付包中...";
  setVisual("runVisualResult", "正在导出交付包", "平台正在打包最终 Word/PDF/PNG 和审计文件。", "info");
  try {
    const taskId = $("taskIdInput").value.trim();
    const data = await api(`/api/tasks/${encodeURIComponent(taskId)}/delivery-package`, {
      method: "POST",
      body: JSON.stringify({})
    });
    $("runResult").textContent = pretty(data);
    const completedWithIssues = data.ok && data.status === "completed_with_issues";
    setVisual(
      "runVisualResult",
      data.ok ? (completedWithIssues ? "交付包已导出 · 待复核" : "交付包已导出") : "交付包导出未通过",
      data.ok ? (completedWithIssues ? "交付包已附带图片风险和验收报告；可下载复核，但不视为最终验收通过。" : "正式交付物已通过无人值守门禁，请在文件列表中下载。") : "请先处理验收问题。",
      data.ok ? (completedWithIssues ? "warn" : "ok") : "error"
    );
    await taskFiles();
  } catch (err) {
    $("runResult").textContent = String(err);
    setVisual("runVisualResult", "交付包导出失败", String(err).replace(/^Error:\s*/, ""), "error");
  }
}

async function pageMap() {
  $("pageMapResult").textContent = "读取页码表中...";
  try {
    const taskId = $("taskIdInput").value.trim();
    const data = await api(`/api/tasks/${encodeURIComponent(taskId)}/page-map`);
    $("pageMapResult").textContent = pretty(data);
    $("pageMapInput").value = pretty(data.manual_rows_data || []);
  } catch (err) {
    $("pageMapResult").textContent = String(err);
  }
}

async function savePageMap() {
  $("pageMapResult").textContent = "保存中...";
  try {
    const taskId = $("taskIdInput").value.trim();
    const rows = JSON.parse($("pageMapInput").value || "[]");
    if (!Array.isArray(rows)) throw new Error("页码校准内容必须是 JSON 数组");
    const data = await api(`/api/tasks/${encodeURIComponent(taskId)}/page-map`, {
      method: "POST",
      body: JSON.stringify({ rows })
    });
    $("pageMapResult").textContent = pretty(data);
  } catch (err) {
    $("pageMapResult").textContent = String(err);
  }
}

async function loadFragments() {
  $("fragmentsResult").textContent = "读取结构化答案中...";
  try {
    const taskId = $("taskIdInput").value.trim();
    const data = await api(`/api/tasks/${encodeURIComponent(taskId)}/answer-fragments`);
    $("fragmentsResult").textContent = pretty({
      exists: data.exists,
      ok: data.ok,
      path: data.path,
      issues: data.issues
    });
    if (data.data) $("fragmentsInput").value = pretty(data.data);
  } catch (err) {
    $("fragmentsResult").textContent = String(err);
  }
}

async function saveFragments() {
  $("fragmentsResult").textContent = "保存并校验中...";
  try {
    const taskId = $("taskIdInput").value.trim();
    const data = JSON.parse($("fragmentsInput").value || "{}");
    const result = await api(`/api/tasks/${encodeURIComponent(taskId)}/answer-fragments`, {
      method: "POST",
      body: JSON.stringify({ data })
    });
    $("fragmentsResult").textContent = pretty(result);
  } catch (err) {
    $("fragmentsResult").textContent = String(err);
  }
}

function renderReviewRows(rows) {
  const list = $("reviewList");
  list.innerHTML = "";
  if (!(rows || []).length) {
    list.innerHTML = `<div class="review-empty">暂无审查报告映射。任务完成后点击“读取审查报告映射”。</div>`;
    return;
  }
  for (const row of rows || []) {
    const item = document.createElement("details");
    item.className = "review-item";
    if ((row.notes || []).length) item.classList.add("has-notes");
    if (row.evidence_binding_strategy === "program_top_evidence") item.classList.add("auto-evidence");
    const summary = document.createElement("summary");
    const title = [row.section, row.number || row.question_id].filter(Boolean).join(" ");
    const status = (row.notes || []).length ? "需复核" : "未发现明显问题";
    summary.innerHTML = `
      <span>${escapeHtml(title || row.question_id || "未命名题目")}</span>
      <strong class="${(row.notes || []).length ? "review-status-warn" : "review-status-ok"}">${status}</strong>
    `;
    item.appendChild(summary);
    const body = document.createElement("div");
    body.className = "review-body";
    const notes = (row.notes || []).map((x) => `<li>${escapeHtml(String(x))}</li>`).join("");
    const evidenceStatus = Number(row.evidence_id_count || 0) > 0
      ? `已绑定 ${Number(row.evidence_id_count || 0)} 条教材依据`
      : "未绑定教材依据";
    body.innerHTML = `
      <div class="review-map-grid">
        <p><strong>题目 ID</strong><span>${escapeHtml(row.question_id || "")}</span></p>
        <p><strong>题型题号</strong><span>${escapeHtml(title || "-")}</span></p>
        <p><strong>答案</strong><span>${practiceMarkdown(row.answer || "未生成答案")}</span></p>
        <p><strong>教材依据状态</strong><span>${escapeHtml(evidenceStatus)}</span></p>
      </div>
      <p><strong>原题</strong>${practiceMarkdown((row.stem || "未读取到原题内容").slice(0, 800))}</p>
      <div class="review-notes">
        <strong>审查提示</strong>
        ${notes ? `<ul>${notes}</ul>` : "<p>未发现需要人工处理的问题。</p>"}
      </div>
    `;
    item.appendChild(body);
    list.appendChild(item);
  }
  requestAnimationFrame(() => typesetMath(list));
}

function escapeHtml(text) {
  return String(text)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;");
}

async function loadReview() {
  $("reviewResult").textContent = "读取审查报告映射中...";
  try {
    const taskId = $("taskIdInput").value.trim();
    const data = await api(`/api/tasks/${encodeURIComponent(taskId)}/review`);
    renderReviewRows(data.review_rows || []);
    $("reviewResult").textContent = pretty({
      ok: data.ok,
      question_count: data.question_count,
      auto_evidence_count: data.auto_evidence_count,
      review_note_count: data.review_note_count,
      coverage: data.coverage
    });
  } catch (err) {
    $("reviewResult").textContent = String(err);
  }
}

async function exportReview() {
  $("reviewResult").textContent = "导出中...";
  try {
    const taskId = $("taskIdInput").value.trim();
    const data = await api(`/api/tasks/${encodeURIComponent(taskId)}/review-export`, {
      method: "POST",
      body: JSON.stringify({})
    });
    $("reviewResult").textContent = pretty(data);
  } catch (err) {
    $("reviewResult").textContent = String(err);
  }
}

function seedPageMap() {
  $("pageMapInput").value = pretty([
    {
      textbook: "物理化学第6版下3",
      citation_textbook: "物理化学第6版下",
      pdf_page_idx: "12",
      printed_page: "210",
      page_source: "manual",
      verified: "true",
      confidence: "high",
      notes: "manual checked"
    }
  ]);
}

async function validateFragment() {
  $("fragmentResult").textContent = "校验中...";
  try {
    const body = JSON.parse($("fragmentInput").value);
    const data = await api("/api/validate-answer-fragment", {
      method: "POST",
      body: JSON.stringify(body)
    });
    $("fragmentResult").textContent = pretty(data);
  } catch (err) {
    $("fragmentResult").textContent = String(err);
  }
}

function seedFragment() {
  $("fragmentInput").value = pretty({
    schema_version: "answer_book.answer_fragment.v4",
    question_id: "demo_01",
    answer: "A",
    evidence_ids: ["ev_demo_01"],
    blocks: [
      {
        label: "解析",
        segments: [
          { type: "text", text: "根据教材证据，该关系式可用于判断反应自发方向。" },
          { type: "formula_ref", formula_id: "f_demo_01" }
        ]
      }
    ],
    formulas: [
      {
        formula_id: "f_demo_01",
        latex: "\\\\Delta_r G_m=-nFE",
        role: "relation",
        display: true,
        source_note: "示例公式"
      }
    ],
    warnings: []
  });
}

Object.assign(window, {
  startWizard,
  goToPage,
  switchTextbookTab,
  switchExamTab,
  switchResultTab,
  clearTextbookSelection,
  prepareTextbookIndex,
  prepareUploadedTextbookIndex,
  loadTasks,
  taskFiles,
  loadReview,
  loadSystemStatus
});

$("refreshBtn")?.addEventListener("click", async () => {
  await refresh();
  if (currentPage === "env") await loadEnvironmentStatus();
});
$("practiceRecoveryOpenBtn")?.addEventListener("click", async (event) => {
  const button = event.currentTarget;
  button.disabled = true;
  try {
    await openPracticeRecoveryNoticeJob();
  } catch (error) {
    await platformAlert(String(error).replace(/^Error:\s*/, ""), {
      title: "无法打开恢复任务",
      tone: "danger",
    });
  } finally {
    button.disabled = false;
  }
});
$("practiceRecoveryStayBtn")?.addEventListener("click", () => {
  hidePracticeRecoveryNotice({ dismiss: true });
});
$("prepareTextbookIndexBtn").addEventListener("click", prepareTextbookIndex);
$("saveSharedLibraryBtn")?.addEventListener("click", () => {
  saveSharedLibrarySettings().catch((err) => setVisual("libraryVisualResult", "教材库连接失败", String(err).replace(/^Error:\s*/, ""), "error"));
});
$("refreshSharedLibraryBtn")?.addEventListener("click", () => refreshSharedLibraryCatalog());
$("publishSharedLibraryBtn")?.addEventListener("click", () => {
  publishSharedLibrary().catch((err) => setVisual("libraryVisualResult", "共享教材发布失败", String(err).replace(/^Error:\s*/, ""), "error"));
});
$("createTaskBtn").addEventListener("click", createTask);
$("runTaskBtn").addEventListener("click", (event) => runTask(false, event.currentTarget.dataset.runMode === "retry"));
$("runTaskNoModelBtn").addEventListener("click", () => runTask(true));
$("runTaskReuseBtn").addEventListener("click", () => runTask(false, true));
$("taskStatusBtn").addEventListener("click", taskStatus);
$("taskQualityBtn").addEventListener("click", taskQuality);
$("finalAcceptanceBtn").addEventListener("click", finalAcceptance);
$("taskFilesBtn").addEventListener("click", taskFiles);
$("deliveryPackageBtn").addEventListener("click", deliveryPackage);
$("loadTasksBtn").addEventListener("click", async () => {
  await loadTasks();
  if (currentPage === "monitor") await loadSystemStatus();
});
$("taskSortSelect")?.addEventListener("change", (event) => {
  activeTaskSort = event.target.value || "smart";
  renderTaskManager();
});
$("taskBulkModeBtn")?.addEventListener("click", () => setTaskBulkMode(true));
$("taskDismissFailedFeedbackBtn")?.addEventListener("click", dismissFailedTaskFeedback);
$("taskBulkCancelBtn")?.addEventListener("click", () => setTaskBulkMode(false));
$("taskBulkDeleteBtn")?.addEventListener("click", deleteSelectedTasks);
$("taskBulkSelectAllBtn")?.addEventListener("click", () => {
  document.querySelectorAll("#taskManagerList [data-task-select]").forEach((input) => selectedTaskIds.add(input.dataset.taskSelect));
  renderTaskManager();
  updateTaskBulkControls();
});
$("refreshSystemBtn")?.addEventListener("click", loadSystemStatus);
$("copyLanAccessBtn")?.addEventListener("click", () => copyLanAccessInfo().catch(() => {}));
$("hybridExecutionEnabled")?.addEventListener("change", () => saveHybridExecutionSetting());
$("pageMapBtn").addEventListener("click", pageMap);
$("savePageMapBtn").addEventListener("click", savePageMap);
$("seedPageMapBtn").addEventListener("click", seedPageMap);
$("loadFragmentsBtn").addEventListener("click", loadFragments);
$("saveFragmentsBtn").addEventListener("click", saveFragments);
$("loadReviewBtn").addEventListener("click", loadReview);
$("exportReviewBtn").addEventListener("click", exportReview);
$("validateFragmentBtn").addEventListener("click", validateFragment);
$("uploadExamBtn").addEventListener("click", async () => {
  $("taskResult").textContent = "上传真题中...";
  setVisual("taskVisualResult", "正在上传真题", "上传完成后会自动切回已有真题列表。", "info");
  try {
    const uploaded = await uploadLibraryFiles("exam");
    $("taskResult").textContent = "真题已上传，已切回已有真题列表。";
    setVisual("taskVisualResult", "真题已上传", `已上传 ${uploaded.length} 个文件，并选中新上传的真题。`, "ok");
  } catch (err) {
    const message = String(err).replace(/^Error:\s*/, "");
    $("taskResult").textContent = `上传失败：${message}`;
    setVisual("taskVisualResult", "真题上传失败", message, "error");
  }
});
$("uploadTextbooksBtn").addEventListener("click", async () => {
  $("libraryResult").textContent = "上传教材中...";
  setVisual("libraryVisualResult", "正在上传教材", "上传完成后可点击右下角“去建立索引”。", "info");
  try {
    const uploaded = await uploadLibraryFiles("textbook");
    setVisual("libraryVisualResult", "教材已上传", `已上传 ${uploaded.length} 个文件。请点击右下角“去建立索引”。`, "ok");
  } catch (err) {
    const message = String(err).replace(/^Error:\s*/, "");
    $("libraryResult").textContent = `上传失败：${message}`;
    setVisual("libraryVisualResult", "教材上传失败", message, "error");
  }
});
setupUploadInput("exam");
setupUploadInput("textbook");
$("examSelect").addEventListener("change", () => {
  selectExamFile($("examSelect").value || "");
});
$("examModelPresetSelect")?.addEventListener("change", (event) => {
  applyExamModelPreset(event.target.value || "custom");
});
$("providerSelect").addEventListener("change", () => {
  updateModelControls();
  updateProviderSummary(providerConfigs);
  updatePracticeModelSummary();
  markExamModelPresetCustom();
});
$("modelSelect").addEventListener("change", () => {
  updateTextRoleModelControls();
  syncVisionModelFromAnswerModel();
  updatePracticeModelSummary();
  markExamModelPresetCustom();
});
$("modelInput").addEventListener("input", () => {
  updateTextRoleModelControls();
  syncVisionModelFromAnswerModel();
  updatePracticeModelSummary();
  markExamModelPresetCustom();
});
for (const roleKey of Object.keys(textModelRoles)) {
  const role = textModelRoles[roleKey];
  $(role.providerId)?.addEventListener("change", () => {
    populateTextRoleModelSelect(roleKey);
    renderQuestionTypeModelCards();
    switchQuestionTypeTab(modelQuestionTypeTab);
    updatePracticeModelSummary();
    markExamModelPresetCustom();
  });
  $(role.modelSelectId)?.addEventListener("change", () => {
    updateTextRoleHint(roleKey);
    renderQuestionTypeModelCards();
    switchQuestionTypeTab(modelQuestionTypeTab);
    updatePracticeModelSummary();
    markExamModelPresetCustom();
  });
  $(role.modelInputId)?.addEventListener("input", () => {
    updateTextRoleHint(roleKey);
    renderQuestionTypeModelCards();
    switchQuestionTypeTab(modelQuestionTypeTab);
    updatePracticeModelSummary();
    markExamModelPresetCustom();
  });
}
$("visionProviderSelect").addEventListener("change", () => {
  populateVisionModelSelect();
  updateModelCapabilityRisk();
  renderQuestionTypeModelCards();
  switchQuestionTypeTab(modelQuestionTypeTab);
  updatePracticeModelSummary();
  markExamModelPresetCustom();
});
$("visionModelSelect").addEventListener("change", () => {
  updateCapabilityModelHints();
  updateModelCapabilityRisk();
  renderQuestionTypeModelCards();
  switchQuestionTypeTab(modelQuestionTypeTab);
  updatePracticeModelSummary();
  markExamModelPresetCustom();
});
$("visionModelInput").addEventListener("input", () => {
  updateCapabilityModelHints();
  updateModelCapabilityRisk();
  renderQuestionTypeModelCards();
  switchQuestionTypeTab(modelQuestionTypeTab);
  updatePracticeModelSummary();
  markExamModelPresetCustom();
});
for (const profile of ["practice", "knowledge"]) {
  for (const kind of ["text", "vision"]) {
    const ids = taskModelControlIds(profile, kind);
    $(ids.provider)?.addEventListener("change", () => {
      populateTaskModelControl(profile, kind);
      saveTaskModelSetting(profile, kind);
      updateTaskModelSummary(profile);
    });
    $(ids.model)?.addEventListener("change", () => {
      saveTaskModelSetting(profile, kind);
      updateTaskModelSummary(profile);
    });
    $(ids.input)?.addEventListener("input", () => {
      saveTaskModelSetting(profile, kind);
      updateTaskModelSummary(profile);
    });
  }
}
$("imageProviderSelect").addEventListener("change", () => {
  populateImageModelControls();
  updateModelCapabilityRisk();
  renderQuestionTypeModelCards();
  switchQuestionTypeTab(modelQuestionTypeTab);
  markExamModelPresetCustom();
});
$("imageModelSelect")?.addEventListener("change", () => {
  updateCapabilityModelHints();
  updateModelCapabilityRisk();
  renderQuestionTypeModelCards();
  switchQuestionTypeTab(modelQuestionTypeTab);
  markExamModelPresetCustom();
});
$("imageModelInput").addEventListener("input", () => {
  updateCapabilityModelHints();
  updateModelCapabilityRisk();
  renderQuestionTypeModelCards();
  switchQuestionTypeTab(modelQuestionTypeTab);
  markExamModelPresetCustom();
});
$("thinkingModeSelect")?.addEventListener("change", markExamModelPresetCustom);
document.addEventListener("visibilitychange", () => {
  if (document.hidden) stopSystemMonitorPolling();
  if (!document.hidden) resumeRememberedPracticeJob().catch(() => {});
  if (!document.hidden) resumeRememberedPracticeWordExports().catch(() => {});
  if (!document.hidden && currentPage === "tasks") {
    loadTasks({ silent: true, includeLiveDetails: true }).catch(() => {});
  }
  if (!document.hidden && currentPage === "monitor") {
    loadSystemStatus().catch(() => {});
    startSystemMonitorPolling();
  }
});
$("practiceForm")?.addEventListener("submit", planPractice);
$("knowledgeForm")?.addEventListener("submit", planKnowledgePractice);
$("knowledgeCount")?.addEventListener("change", updateKnowledgeDifficultyControl);
document.querySelectorAll('input[name="knowledgeQuestionType"]').forEach((input) => input.addEventListener("change", enforceKnowledgeQuestionTypeMode));
updateKnowledgeDifficultyControl();
$("knowledgeFileInput")?.addEventListener("change", (event) => {
  readKnowledgeFiles(event.target.files).catch((error) => {
    showUploadFeedback("knowledge", String(error).replace(/^Error:\s*/, ""));
  }).finally(() => { event.target.value = ""; });
});
$("knowledgeTextInput")?.addEventListener("paste", (event) => {
  pasteKnowledgeImages(event).catch((error) => {
    showUploadFeedback("knowledge", String(error).replace(/^Error:\s*/, ""));
  });
});
$("practiceFile")?.addEventListener("change", (event) => {
  readPracticeFiles(event.target.files).catch((error) => {
    showUploadFeedback("practice", String(error).replace(/^Error:\s*/, ""));
  }).finally(() => { event.target.value = ""; });
});
$("practiceQuestionText")?.addEventListener("paste", (event) => {
  pastePracticeImages(event).catch((error) => {
    showUploadFeedback("practice", String(error).replace(/^Error:\s*/, ""));
  });
});
$("practiceQuestionText")?.addEventListener("input", syncPracticeSubmitAvailability);
$("practiceForm")?.addEventListener("input", () => schedulePracticeWorkspaceDraftSave(currentPracticeSourceMode));
$("practiceForm")?.addEventListener("change", () => schedulePracticeWorkspaceDraftSave(currentPracticeSourceMode));
$("knowledgeForm")?.addEventListener("input", () => schedulePracticeWorkspaceDraftSave("knowledge"));
$("knowledgeForm")?.addEventListener("change", () => schedulePracticeWorkspaceDraftSave("knowledge"));
$("practiceScopeDrawer")?.addEventListener("input", () => schedulePracticeWorkspaceDraftSave(currentPracticeSourceMode));
$("practiceScopeDrawer")?.addEventListener("change", () => schedulePracticeWorkspaceDraftSave(currentPracticeSourceMode));
$("practicePlanReview")?.addEventListener("input", () => schedulePracticeWorkspaceDraftSave(currentPracticeSourceMode));
$("practicePlanReview")?.addEventListener("change", () => schedulePracticeWorkspaceDraftSave(currentPracticeSourceMode));
$("practiceWorkspaceDraftClear")?.addEventListener("click", () => {
  clearAndStartFreshPracticeWorkspace(currentPracticeSourceMode).catch(() => {});
});
$("practiceWorkspaceDraftClearActive")?.addEventListener("click", () => {
  clearAndStartFreshPracticeWorkspace(currentPracticeSourceMode).catch(() => {});
});
$("knowledgeWorkspaceDraftClear")?.addEventListener("click", () => {
  clearAndStartFreshPracticeWorkspace("knowledge", true).catch(() => {});
});
$("practicePlanBackBtn")?.addEventListener("click", () => {
  $("practicePlanReview")?.classList.add("hidden");
  if (latestPracticeSourceScope) {
    renderPracticeSourceSelection({ source_scope: latestPracticeSourceScope, source_analysis: latestPracticeSourceAnalysis });
    return;
  }
  $("practiceEmpty")?.classList.remove("hidden");
  setPracticeStage("submit");
  setText("practiceSourceStatus", "可调整材料");
});
$("practiceWorkflowBackBtn")?.addEventListener("click", handlePracticeWorkflowBack);
$("practiceWorkflowPrimaryBtn")?.addEventListener("click", handlePracticeWorkflowPrimary);
$("practiceSourceBackBtn")?.addEventListener("click", returnToPracticeSourceInput);
$("practiceSourceConfirmBtn")?.addEventListener("click", planSelectedSourceQuestions);
$("practiceSourceQuestionList")?.addEventListener("change", updatePracticeStrategySettings);
$("practiceScopeGranularity")?.addEventListener("change", (event) => {
  const scope = latestPracticeSourceScope || {};
  scope.granularity = event.target.value;
  latestPracticeSourceScope = scope;
  renderPracticeScopeQuestionList(scope.questions || []);
  updatePracticeStrategySettings();
});
$("practiceAddSourceUnit")?.addEventListener("click", addPracticeSourceUnit);
$("practiceSourceQuestionList")?.addEventListener("click", (event) => {
  const editBtn = event.target.closest("[data-edit-source]");
  if (editBtn) {
    editPracticeSourceUnit(editBtn.getAttribute("data-edit-source"));
    return;
  }
  const splitBtn = event.target.closest("[data-split-source]");
  if (splitBtn) {
    splitPracticeSourceUnit(splitBtn.getAttribute("data-split-source"));
    return;
  }
  const mergeBtn = event.target.closest("[data-merge-source]");
  if (mergeBtn) {
    const unitId = mergeBtn.getAttribute("data-merge-source");
    const wasSelected = practiceScopeMergeSelection.includes(unitId);
    if (wasSelected) {
      practiceScopeMergeSelection = practiceScopeMergeSelection.filter((id) => id !== unitId);
      mergeBtn.classList.toggle("active", false);
    } else {
      practiceScopeMergeSelection.push(unitId);
      mergeBtn.classList.toggle("active", true);
    }
    if (practiceScopeMergeSelection.length >= 2) {
      mergePracticeSourceUnits();
    }
    return;
  }
});
document.querySelectorAll('input[name="practiceSetStrategy"]').forEach((input) => {
  input.addEventListener("change", updatePracticeStrategySettings);
});
$("practiceTargetedCount")?.addEventListener("input", updatePracticeStrategySettings);
$("practiceKnowledgePerCount")?.addEventListener("input", updatePracticeStrategySettings);
$("practiceVariantsPerQuestion")?.addEventListener("change", updatePracticeStrategySettings);
["practiceDifficultyBasicCount", "practiceDifficultyIntermediateCount", "practiceDifficultyChallengeCount"].forEach((id) => {
  $(id)?.addEventListener("input", () => {
    nextPracticePreferenceOrder("difficulty");
    updatePracticeScopePreview();
  });
});
$("practiceBlueprintReviewEnabled")?.addEventListener("change", (event) => {
  syncPracticeBlueprintPath(event.target.checked);
  updatePracticeStrategySettings();
  updatePracticeScopePreview();
});
$("knowledgeBlueprintReviewEnabled")?.addEventListener("change", (event) => {
  if (currentPracticeSourceMode === "knowledge") syncPracticeBlueprintPath(event.target.checked);
  updatePracticeStrategySettings();
  updatePracticeScopePreview();
});
$("practiceSelectAllSources")?.addEventListener("click", () => {
  document.querySelectorAll('input[name="practiceSourceQuestion"]').forEach((input) => { input.checked = true; });
  updatePracticeStrategySettings();
});
$("practiceClearSources")?.addEventListener("click", () => {
  document.querySelectorAll('input[name="practiceSourceQuestion"]').forEach((input) => { input.checked = false; });
  updatePracticeStrategySettings();
});
$("practicePlanConfirmBtn")?.addEventListener("click", generatePracticeFromPlan);
$("practicePlanRegenerateBtn")?.addEventListener("click", regeneratePracticePlan);
$("practicePlanAdoptCandidateBtn")?.addEventListener("click", adoptPracticePlanCandidate);
$("practicePlanKeepOriginalBtn")?.addEventListener("click", keepOriginalPracticePlan);
$("practicePlanList")?.addEventListener("click", (event) => {
  const moveBtn = event.target.closest("[data-plan-move]");
  if (moveBtn) {
    const items = latestPracticePlan?.blueprint?.exercise_plan || [];
    const index = Number(moveBtn.dataset.planItemIndex);
    const targetIndex = moveBtn.dataset.planMove === "up" ? index - 1 : index + 1;
    if (targetIndex >= 0 && targetIndex < items.length) {
      [items[index], items[targetIndex]] = [items[targetIndex], items[index]];
      renumberPracticePlanItems(items);
      auditAndRenderPracticePlan();
    }
    return;
  }
  const deleteBtn = event.target.closest("[data-plan-delete]");
  if (deleteBtn) {
    const items = latestPracticePlan?.blueprint?.exercise_plan || [];
    if (items.length > 1) {
      const [removed] = items.splice(Number(deleteBtn.dataset.planDelete), 1);
      if (removed?.plan_item_id) {
        delete practicePlanDrafts[removed.plan_item_id];
        delete practicePlanRevisionReceipts[removed.plan_item_id];
      }
      renumberPracticePlanItems(items);
      auditAndRenderPracticePlan();
    }
    return;
  }
  const regenerateBtn = event.target.closest("[data-plan-item-regenerate]");
  if (regenerateBtn) {
    regeneratePlanItem(Number(regenerateBtn.getAttribute("data-plan-item-regenerate")), regenerateBtn);
    return;
  }
  const draftBtn = event.target.closest("[data-plan-draft]");
  if (draftBtn) {
    generatePlanItemDraft(Number(draftBtn.getAttribute("data-plan-draft")), draftBtn);
    return;
  }
  const adoptBtn = event.target.closest("[data-plan-draft-adopt]");
  if (adoptBtn) {
    togglePlanItemDraftAdopt(adoptBtn.getAttribute("data-plan-draft-adopt"), adoptBtn);
    return;
  }
  const clearBtn = event.target.closest("[data-plan-draft-clear]");
  if (clearBtn) {
    clearPlanItemDraft(clearBtn.getAttribute("data-plan-draft-clear"), clearBtn);
    return;
  }
});
$("practiceEditor")?.addEventListener("input", (event) => {
  if (PRACTICE_EDITOR_FIELD_IDS.includes(event.target?.id)) schedulePracticeEditorDraftSave();
});
$("practiceEditor")?.addEventListener("change", (event) => {
  if (PRACTICE_EDITOR_FIELD_IDS.includes(event.target?.id)) schedulePracticeEditorDraftSave();
});
$("practiceEditor")?.addEventListener("close", () => {
  if (practiceEditorDraftTimer) {
    clearTimeout(practiceEditorDraftTimer);
    practiceEditorDraftTimer = null;
    persistPracticeEditorDraft(practiceEditorDraftSource);
  }
});
$("practiceEditorDiscardDraft")?.addEventListener("click", () => {
  clearPracticeEditorDraft();
  const currentItem = latestPracticeSet?.exercises?.[practiceEditingIndex];
  if (currentItem) populatePracticeEditor(currentItem);
  $("practiceEditorError").textContent = "未保存草稿已放弃，当前显示服务器中的题目。";
  $("practiceEditorError").classList.remove("hidden");
});
window.addEventListener("beforeunload", () => {
  if (practiceEditorDraftTimer) {
    clearTimeout(practiceEditorDraftTimer);
    practiceEditorDraftTimer = null;
    persistPracticeEditorDraft(practiceEditorDraftSource);
  }
});
$("practicePlanAddBtn")?.addEventListener("click", addPracticePlanItem);
$("practicePlanExpandAllBtn")?.addEventListener("click", () => {
  $("practicePlanList")?.querySelectorAll("details").forEach((row) => { row.open = true; });
});
$("practicePlanCollapseAllBtn")?.addEventListener("click", () => {
  $("practicePlanList")?.querySelectorAll("details").forEach((row) => { row.open = false; });
});
$("practiceUndoBtn")?.addEventListener("click", undoPracticeChange);
$("practiceEditorSave")?.addEventListener("click", applyPracticeEditor);
$("practiceSelectAllBtn")?.addEventListener("click", () => {
  (latestPracticeSet?.exercises || []).forEach((_item, index) => selectedPracticeExerciseIndexes.add(index));
  document.querySelectorAll("[data-practice-select]").forEach((input) => { input.checked = true; });
  updatePracticeSelectionActions();
});
$("practiceDownloadSelectedBtn")?.addEventListener("click", () => {
  const data = selectedPracticeSet();
  if (!data) return;
  prepareOrDownloadPracticeWord(data, $("practiceDownloadSelectedBtn"), `专项练习-已选${data.exercises.length}题.docx`).catch((error) => {
    platformAlert(String(error).replace(/^Error:\s*/, ""), { title: "题目 Word 生成失败", tone: "danger" });
  });
});
$("practiceClearSelectedBtn")?.addEventListener("click", () => {
  selectedPracticeExerciseIndexes.clear();
  document.querySelectorAll("[data-practice-select]").forEach((input) => { input.checked = false; });
  updatePracticeSelectionActions();
});
$("practiceConfigCard")?.addEventListener("toggle", syncPracticeConfigState);
$("practiceSidebarCollapse")?.addEventListener("click", () => togglePracticeSidebar(true));
$("practiceSidebarExpand")?.addEventListener("click", () => togglePracticeSidebar(false));
$("practiceRailInput")?.addEventListener("click", () => openPracticeRailTarget("input"));
$("practiceRailPresets")?.addEventListener("click", () => openPracticeRailTarget("presets"));
$("practiceRailConfig")?.addEventListener("click", () => openPracticeRailTarget("config"));
$("practiceRailHistory")?.addEventListener("click", () => openPracticeRailTarget("history"));
document.querySelectorAll(".practice-preset").forEach((btn) => {
  btn.addEventListener("click", () => applyPracticePreset(btn.dataset.practicePreset || ""));
});
["practiceCount", "practiceDifficulty"].forEach((id) => $(id)?.addEventListener("change", updatePracticeConfigSummary));
document.querySelectorAll('input[name="practiceQuestionType"]').forEach((input) => {
  input.addEventListener("change", updatePracticeConfigSummary);
});
$("practiceFocus")?.addEventListener("input", updatePracticeConfigSummary);
practiceSourceContentToggleIds.forEach((toggleId) => {
  $(toggleId)?.addEventListener("change", (event) => {
    syncPracticeSourceContentPreference(event.target.checked, toggleId);
    updatePracticeStrategySettings();
  });
});
$("practiceScopeCloseBtn")?.addEventListener("click", closePracticeScopeDrawer);
$("practiceScopeResumeBtn")?.addEventListener("click", openPracticeScopeDrawer);
$("practiceScopeResumeBackBtn")?.addEventListener("click", returnToPracticeSourceInput);
$("practiceScopeDrawer")?.querySelector("[data-practice-scope-scrim]")?.addEventListener("click", () => {
  closePracticeScopeDrawer();
});
$("practiceScopeDrawer")?.addEventListener("keydown", (event) => {
  if (event.key === "Escape") {
    event.preventDefault();
    closePracticeScopeDrawer();
  }
});
$("practiceBackToPlanBtn")?.addEventListener("click", () => {
  if (latestPracticeRequest?.blueprint_review_enabled === false) {
    if (latestPracticeSourceScope) renderPracticeSourceSelection({ source_scope: latestPracticeSourceScope, source_analysis: latestPracticeSourceAnalysis });
    return;
  }
  if (!latestPracticePlan) return;
  $("practiceResults")?.classList.add("hidden");
  $("practiceEmpty")?.classList.add("hidden");
  renderPracticePlan(latestPracticePlan);
  setPracticeStage("plan");
  setPracticeStageDescription("回到训练蓝图，可调整后再生成。");
});
$("practiceRegenerateSetBtn")?.addEventListener("click", () => regenerateSelectedPracticeQuestions($("practiceRegenerateSetBtn")));
$("practiceShowAllHistory")?.addEventListener("click", async () => {
  $("practiceHistoryList")?.classList.remove("hidden");
  await loadPracticeHistory().catch((error) => {
    $("practiceError").textContent = String(error).replace(/^Error:\s*/, "");
    $("practiceError").classList.remove("hidden");
  });
});
// 初始化工作台默认状态
restorePracticeSidebarState();
syncPracticeConfigState();
updatePracticeConfigSummary();
setPracticeStage("submit");
setPracticeStageDescription("提交题目后，平台会先判断是单题还是题目集；单题将直接进入蓝图，题目集需要先选择训练范围。");
setPracticeStatusBanner("就绪");
setPracticeExportButtonsEnabled(false);
window.lucide?.createIcons();
document.querySelectorAll(".step-pill").forEach((button) => {
  button.addEventListener("click", () => goToPage(button.dataset.page || "home"));
});

wrapTechnicalDetails();
renderTaskStepList();
seedFragment();
refresh().catch((err) => {
  $("environmentBox").textContent = String(err);
});
loadTasks().catch(() => {});

/* ============================================================
   全站视觉增强 · 滚动揭示 / 导航滚动模糊 / 交错入场
   ============================================================ */

// 为带 stagger 的容器子项设置序号变量
function assignStaggerIndices(root = document) {
  root.querySelectorAll(".stagger").forEach((container) => {
    Array.from(container.children).forEach((child, index) => {
      child.style.setProperty("--si", index);
    });
  });
}

// 给各页面的主要区块自动加上 reveal 类，让滚动揭示覆盖全站
function autoMarkReveals(root = document) {
  const selectors = [
    ".home-hero",
    ".platform-home-header",
    ".business-domain-card",
    ".platform-strengths",
    ".platform-strength-grid article",
    ".feature-grid article",
    ".flow-preview",
    ".page-title",
    ".reference-title",
    ".panel-card",
    ".reference-card",
    ".progress-card",
    ".metric-grid > article",
    ".task-grid > article",
    ".compact-section",
    ".task-stat-grid > article",
    ".system-monitor-panel",
    ".practice-header",
    ".practice-progress",
    ".practice-workspace > *",
    ".practice-exercise",
    ".practice-empty",
    ".practice-plan-review",
    ".practice-source-selection",
    ".review-decision-card",
    ".advanced-tool-grid > article",
    ".result-panel",
    ".tab-row",
    // 固定在视口底部的操作栏不能参与滚动揭示，否则在部分缩放比例下会停在 opacity: 0。
    ".page-actions:not(.sticky-page-actions)",
    ".task-filter-row",
    ".shared-library-panel"
  ];
  selectors.forEach((sel) => {
    root.querySelectorAll(sel).forEach((el, index) => {
      if (el.classList.contains("reveal")) return;
      // 同级多卡片用 scale 揭示，标题/hero 用默认上浮
      if (sel.includes("article") || sel.includes("exercise")) {
        el.classList.add("reveal-scale");
      } else {
        el.classList.add("reveal");
      }
      el.style.setProperty("--si", index);
    });
  });
}

// 初始化 IntersectionObserver 实现滚动揭示
let revealObserver = null;
function initRevealObserver() {
  if (revealObserver) revealObserver.disconnect();
  if (!("IntersectionObserver" in window)) {
    document.querySelectorAll(".reveal,.reveal-left,.reveal-right,.reveal-scale").forEach((el) => el.classList.add("visible"));
    return;
  }
  revealObserver = new IntersectionObserver(
    (entries) => {
      entries.forEach((entry) => {
        if (entry.isIntersecting) {
          entry.target.classList.add("visible");
          revealObserver.unobserve(entry.target);
        }
      });
    },
    { threshold: 0.12, rootMargin: "0px 0px -8% 0px" }
  );
  document.querySelectorAll(".reveal,.reveal-left,.reveal-right,.reveal-scale").forEach((el) => {
    if (!el.classList.contains("visible")) revealObserver.observe(el);
  });
}

// 切换页面时重新标记并观察新区块
function refreshPageAnimations() {
  const active = document.querySelector(".page.active");
  if (!active) return;
  active.querySelectorAll(".sticky-page-actions").forEach((el) => {
    el.classList.remove("reveal", "reveal-left", "reveal-right", "reveal-scale");
    el.classList.add("visible");
  });
  autoMarkReveals(active);
  assignStaggerIndices(active);
  // 立即显示首屏可见元素，避免初始空白
  requestAnimationFrame(() => {
    active.querySelectorAll(".reveal,.reveal-left,.reveal-right,.reveal-scale").forEach((el) => {
      const rect = el.getBoundingClientRect();
      if (rect.top < window.innerHeight * 0.92) {
        el.classList.add("visible");
      }
    });
    if (revealObserver) {
      active.querySelectorAll(".reveal:not(.visible),.reveal-scale:not(.visible)").forEach((el) => revealObserver.observe(el));
    }
  });
}

// 导航栏滚动模糊
function initNavScrollBlur() {
  const nav = document.querySelector(".app-nav");
  if (!nav) return;
  const update = () => {
    const scrolled = window.scrollY > 12;
    nav.classList.toggle("scrolled", scrolled);
  };
  update();
  window.addEventListener("scroll", update, { passive: true });
}

// Web 端近似系统级 Liquid Glass：光源跟随指针，保持幅度克制。
function initLiquidGlassLight() {
  const glassModules = document.querySelectorAll(".app-nav, .step-indicator");
  if (!glassModules.length || window.matchMedia("(prefers-reduced-motion: reduce)").matches) return;

  glassModules.forEach((glass) => {
    let frame = 0;
    glass.addEventListener("pointermove", (event) => {
      if (frame) cancelAnimationFrame(frame);
      frame = requestAnimationFrame(() => {
        const rect = glass.getBoundingClientRect();
        const x = Math.max(0, Math.min(100, ((event.clientX - rect.left) / rect.width) * 100));
        const y = Math.max(0, Math.min(100, ((event.clientY - rect.top) / rect.height) * 100));
        glass.style.setProperty("--glass-light-x", `${x.toFixed(1)}%`);
        glass.style.setProperty("--glass-light-y", `${y.toFixed(1)}%`);
      });
    });
    glass.addEventListener("pointerleave", () => {
      glass.style.setProperty("--glass-light-x", "28%");
      glass.style.setProperty("--glass-light-y", "0%");
    });
  });
}

const platformDialogQueue = [];
let platformDialogActive = null;

function platformDialogElements() {
  return {
    overlay: $("platformDialog"),
    card: document.querySelector("#platformDialog .platform-dialog-card"),
    icon: $("platformDialogIcon"),
    eyebrow: $("platformDialogEyebrow"),
    title: $("platformDialogTitle"),
    message: $("platformDialogMessage"),
    inputWrap: $("platformDialogInputWrap"),
    inputLabel: $("platformDialogInputLabel"),
    input: $("platformDialogInput"),
    close: $("platformDialogClose"),
    cancel: $("platformDialogCancel"),
    confirm: $("platformDialogConfirm")
  };
}

function renderNextPlatformDialog() {
  if (platformDialogActive || !platformDialogQueue.length) return;
  const item = platformDialogQueue.shift();
  const elements = platformDialogElements();
  if (!elements.overlay) {
    item.resolve(item.kind === "prompt" ? null : item.kind === "confirm" ? false : true);
    renderNextPlatformDialog();
    return;
  }
  platformDialogActive = { ...item, previousFocus: document.activeElement };
  const tone = ["danger", "warning", "success"].includes(item.options.tone) ? item.options.tone : "info";
  const icon = tone === "danger" ? "fa-triangle-exclamation"
    : tone === "warning" ? "fa-circle-exclamation"
      : tone === "success" ? "fa-circle-check"
        : "fa-circle-info";
  elements.overlay.dataset.tone = tone;
  elements.icon.innerHTML = `<i class="fas ${icon}"></i>`;
  elements.eyebrow.textContent = item.options.eyebrow || (tone === "danger" ? "请谨慎操作" : "系统提示");
  elements.title.textContent = item.options.title || (item.kind === "alert" ? "操作提示" : "请确认");
  elements.message.textContent = item.options.message || "";
  elements.confirm.textContent = item.options.confirmText || "确定";
  elements.cancel.textContent = item.options.cancelText || "取消";
  elements.cancel.classList.toggle("hidden", item.kind === "alert");
  elements.inputWrap.classList.toggle("hidden", item.kind !== "prompt");
  if (item.kind === "prompt") {
    elements.inputLabel.textContent = item.options.inputLabel || "补充说明";
    elements.input.placeholder = item.options.placeholder || "";
    elements.input.value = item.options.defaultValue || "";
  }
  elements.overlay.classList.remove("hidden");
  document.body.classList.add("platform-dialog-open");
  requestAnimationFrame(() => {
    if (item.kind === "prompt") elements.input.focus();
    else elements.confirm.focus();
  });
}

function finishPlatformDialog(confirmed) {
  if (!platformDialogActive) return;
  const current = platformDialogActive;
  const elements = platformDialogElements();
  const result = current.kind === "prompt"
    ? (confirmed ? elements.input.value : null)
    : current.kind === "confirm"
      ? Boolean(confirmed)
      : true;
  elements.overlay?.classList.add("hidden");
  document.body.classList.remove("platform-dialog-open");
  platformDialogActive = null;
  current.resolve(result);
  if (current.previousFocus?.isConnected) current.previousFocus.focus({ preventScroll: true });
  renderNextPlatformDialog();
}

function showPlatformDialog(kind, options = {}) {
  return new Promise((resolve) => {
    platformDialogQueue.push({ kind, options, resolve });
    renderNextPlatformDialog();
  });
}

function platformAlert(message, options = {}) {
  return showPlatformDialog("alert", { ...options, message });
}

function platformConfirm(options = {}) {
  return showPlatformDialog("confirm", typeof options === "string" ? { message: options } : options);
}

function platformPrompt(options = {}) {
  return showPlatformDialog("prompt", typeof options === "string" ? { message: options } : options);
}

function initPlatformDialog() {
  const elements = platformDialogElements();
  if (!elements.overlay || elements.overlay.dataset.ready === "true") return;
  elements.overlay.dataset.ready = "true";
  elements.confirm.addEventListener("click", () => finishPlatformDialog(true));
  elements.cancel.addEventListener("click", () => finishPlatformDialog(false));
  elements.close.addEventListener("click", () => finishPlatformDialog(false));
  elements.overlay.addEventListener("click", (event) => {
    if (event.target === elements.overlay && platformDialogActive?.kind === "alert") finishPlatformDialog(false);
  });
  document.addEventListener("keydown", (event) => {
    if (!platformDialogActive) return;
    if (event.key === "Escape") {
      event.preventDefault();
      finishPlatformDialog(false);
      return;
    }
    if (event.key === "Enter" && platformDialogActive.kind !== "prompt" && !event.shiftKey) {
      event.preventDefault();
      finishPlatformDialog(true);
      return;
    }
    if (event.key !== "Tab") return;
    const focusable = [elements.close, elements.cancel, elements.confirm, elements.input]
      .filter((element) => element && !element.classList.contains("hidden") && !element.closest(".hidden"));
    if (!focusable.length) return;
    const first = focusable[0];
    const last = focusable[focusable.length - 1];
    if (event.shiftKey && document.activeElement === first) {
      event.preventDefault();
      last.focus();
    } else if (!event.shiftKey && document.activeElement === last) {
      event.preventDefault();
      first.focus();
    }
  });
}

const platformSelectStates = new Set();
let openPlatformSelect = null;

function closePlatformSelect(state = openPlatformSelect, restoreFocus = false) {
  if (!state) return;
  state.list.classList.add("hidden");
  state.button.setAttribute("aria-expanded", "false");
  state.wrapper.classList.remove("open");
  if (openPlatformSelect === state) openPlatformSelect = null;
  if (restoreFocus) state.button.focus({ preventScroll: true });
}

function positionPlatformSelect(state) {
  const rect = state.button.getBoundingClientRect();
  const gap = 8;
  const availableBelow = window.innerHeight - rect.bottom - gap;
  const listHeight = Math.min(state.list.scrollHeight || 280, 320);
  const openAbove = availableBelow < Math.min(listHeight, 220) && rect.top > availableBelow;
  state.list.style.left = `${Math.max(12, Math.min(rect.left, window.innerWidth - rect.width - 12))}px`;
  state.list.style.width = `${rect.width}px`;
  state.list.style.top = openAbove
    ? `${Math.max(12, rect.top - listHeight - gap)}px`
    : `${Math.min(window.innerHeight - listHeight - 12, rect.bottom + gap)}px`;
}

function syncPlatformSelect(state) {
  const { select, button, list } = state;
  if (!select.isConnected) {
    list.remove();
    platformSelectStates.delete(state);
    return;
  }
  const options = Array.from(select.options);
  const selected = options.find((option) => option.selected) || options[0];
  button.querySelector(".platform-select-label").textContent = selected?.textContent?.trim() || "请选择";
  button.disabled = select.disabled;
  list.innerHTML = options.map((option, index) => `
    <button type="button" role="option" data-option-index="${index}" aria-selected="${option.selected ? "true" : "false"}" ${option.disabled ? "disabled" : ""}>
      <span>${escapeHtml(option.textContent?.trim() || "")}</span>
      <i class="fas fa-check"></i>
    </button>
  `).join("");
}

function syncPlatformSelectElement(select) {
  if (!select) return;
  const state = Array.from(platformSelectStates).find((item) => item.select === select);
  if (state) syncPlatformSelect(state);
}

function openCustomSelect(state) {
  if (state.select.disabled) return;
  if (openPlatformSelect && openPlatformSelect !== state) closePlatformSelect(openPlatformSelect);
  syncPlatformSelect(state);
  state.list.classList.remove("hidden");
  state.button.setAttribute("aria-expanded", "true");
  state.wrapper.classList.add("open");
  openPlatformSelect = state;
  positionPlatformSelect(state);
  const selected = state.list.querySelector('[aria-selected="true"]') || state.list.querySelector("button:not(:disabled)");
  selected?.scrollIntoView({ block: "nearest" });
}

function choosePlatformSelectOption(state, index) {
  const option = state.select.options[index];
  if (!option || option.disabled) return;
  state.select.value = option.value;
  state.select.dispatchEvent(new Event("change", { bubbles: true }));
  syncPlatformSelect(state);
  closePlatformSelect(state, true);
}

function enhancePlatformSelect(select) {
  if (!select || select.multiple || select.dataset.nativeSelect !== undefined || select.dataset.platformSelect === "ready" || select.classList.contains("visually-hidden")) return;
  select.dataset.platformSelect = "ready";
  const wrapper = document.createElement("span");
  wrapper.className = "platform-select";
  const button = document.createElement("button");
  button.type = "button";
  button.className = "platform-select-trigger";
  button.setAttribute("aria-haspopup", "listbox");
  button.setAttribute("aria-expanded", "false");
  const wrappingLabel = select.closest("label");
  const labelText = wrappingLabel
    ? Array.from(wrappingLabel.childNodes).filter((node) => node.nodeType === 3).map((node) => node.textContent.trim()).filter(Boolean).join(" ")
    : "";
  button.setAttribute("aria-label", select.getAttribute("aria-label") || labelText || "选择");
  button.innerHTML = '<span class="platform-select-label"></span><i class="fas fa-chevron-down"></i>';
  const list = document.createElement("div");
  list.className = "platform-select-menu hidden";
  list.setAttribute("role", "listbox");
  const parent = select.parentNode;
  parent.insertBefore(wrapper, select);
  wrapper.append(select, button);
  select.tabIndex = -1;
  select.setAttribute("aria-hidden", "true");
  document.body.appendChild(list);
  const state = { select, wrapper, button, list, observer: null };
  platformSelectStates.add(state);
  state.observer = new MutationObserver(() => syncPlatformSelect(state));
  state.observer.observe(select, { childList: true, subtree: true, attributes: true, attributeFilter: ["disabled"] });
  select.addEventListener("change", () => syncPlatformSelect(state));
  button.addEventListener("click", () => {
    if (openPlatformSelect === state) closePlatformSelect(state);
    else openCustomSelect(state);
  });
  button.addEventListener("keydown", (event) => {
    const enabled = Array.from(select.options).map((option, index) => ({ option, index })).filter(({ option }) => !option.disabled);
    const focusedOption = list.querySelector(".keyboard-focus");
    const focusedIndex = focusedOption ? Number(focusedOption.dataset.optionIndex) : -1;
    const currentIndex = enabled.findIndex(({ option, index }) => focusedIndex >= 0 ? index === focusedIndex : option.selected);
    if (event.key === "Escape") {
      closePlatformSelect(state, true);
      return;
    }
    if (event.key === "Enter" || event.key === " ") {
      event.preventDefault();
      if (openPlatformSelect === state) {
        const focused = list.querySelector(".keyboard-focus") || list.querySelector('[aria-selected="true"]');
        if (focused) choosePlatformSelectOption(state, Number(focused.dataset.optionIndex));
      } else {
        openCustomSelect(state);
      }
      return;
    }
    if (!["ArrowDown", "ArrowUp", "Home", "End"].includes(event.key) || !enabled.length) return;
    event.preventDefault();
    if (openPlatformSelect !== state) openCustomSelect(state);
    let next = currentIndex < 0 ? 0 : currentIndex;
    if (event.key === "ArrowDown") next = Math.min(enabled.length - 1, next + 1);
    if (event.key === "ArrowUp") next = Math.max(0, next - 1);
    if (event.key === "Home") next = 0;
    if (event.key === "End") next = enabled.length - 1;
    list.querySelectorAll(".keyboard-focus").forEach((item) => item.classList.remove("keyboard-focus"));
    const target = list.querySelector(`[data-option-index="${enabled[next].index}"]`);
    target?.classList.add("keyboard-focus");
    target?.scrollIntoView({ block: "nearest" });
  });
  list.addEventListener("click", (event) => {
    const optionButton = event.target.closest("[data-option-index]");
    if (optionButton) choosePlatformSelectOption(state, Number(optionButton.dataset.optionIndex));
  });
  syncPlatformSelect(state);
}

function initPlatformSelects() {
  document.querySelectorAll("select").forEach(enhancePlatformSelect);
  if (document.body.dataset.platformSelectObserver === "ready") return;
  document.body.dataset.platformSelectObserver = "ready";
  const observer = new MutationObserver((mutations) => {
    mutations.forEach((mutation) => {
      mutation.addedNodes.forEach((node) => {
        if (!(node instanceof Element)) return;
        applyIconAccessibility(node);
        if (node.matches("select")) enhancePlatformSelect(node);
        node.querySelectorAll?.("select").forEach(enhancePlatformSelect);
      });
    });
    platformSelectStates.forEach((state) => {
      if (!state.select.isConnected) {
        state.observer.disconnect();
        state.list.remove();
        platformSelectStates.delete(state);
      }
    });
  });
  observer.observe(document.body, { childList: true, subtree: true });
  document.addEventListener("click", (event) => {
    if (openPlatformSelect && !openPlatformSelect.wrapper.contains(event.target) && !openPlatformSelect.list.contains(event.target)) {
      closePlatformSelect(openPlatformSelect);
    }
  });
  window.addEventListener("resize", () => {
    if (openPlatformSelect) positionPlatformSelect(openPlatformSelect);
  });
  window.addEventListener("scroll", (event) => {
    if (!openPlatformSelect || openPlatformSelect.list.contains(event.target)) return;
    closePlatformSelect(openPlatformSelect);
  }, true);
}

// 初始化
function initSiteEnhancements() {
  // 核心交互控件优先初始化；动画或装饰层异常不能阻断全局控件接管。
  initPlatformDialog();
  applyIconAccessibility();
  initPracticeActionMenus();
  normalizePracticeInlineLayout();
  initPlatformSelects();
  const initialQuery = new URLSearchParams(window.location.search);
  const initialPage = initialQuery.get("page");
  if (initialPage && pageOrder.includes(initialPage)) {
    if (initialPage === "tasks") filterTaskKind(initialQuery.get("kind") || "all");
    goToPage(initialPage);
  }
  document.body.dataset.activePage = currentPage || document.querySelector(".page.active")?.id?.replace("page-", "") || "home";
  document.querySelectorAll(".sticky-page-actions").forEach((el) => {
    el.classList.remove("reveal", "reveal-left", "reveal-right", "reveal-scale");
    el.classList.add("visible");
  });
  autoMarkReveals();
  assignStaggerIndices();
  initRevealObserver();
  initNavScrollBlur();
  initLiquidGlassLight();
  // Restore a durable practice job after refresh. The browser only observes
  // the job; it does not own or cancel the backend worker.
  resumeRememberedPracticeJob().catch(() => {});
  // Word export recovery is independent from practice generation recovery.
  // Only minimal job pointers live in browser storage; the server owns state.
  resumeRememberedPracticeWordExports().catch(() => {});
  // 首屏立即显示
  requestAnimationFrame(() => {
    document.querySelectorAll(".page.active .reveal,.page.active .reveal-scale").forEach((el) => {
      const rect = el.getBoundingClientRect();
      if (rect.top < window.innerHeight * 0.92) el.classList.add("visible");
    });
  });
}

// 页面切换后刷新动画（劫持 goToPage 的尾部行为）
const _originalGoToPage = goToPage;
goToPage = function (page) {
  _originalGoToPage(page);
  // 等待新页面 display 切换完成
  requestAnimationFrame(() => requestAnimationFrame(refreshPageAnimations));
};

if (document.readyState === "loading") {
  document.addEventListener("DOMContentLoaded", initSiteEnhancements);
} else {
  initSiteEnhancements();
}
