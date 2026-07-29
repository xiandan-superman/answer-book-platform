const $ = (id) => document.getElementById(id);
let taskPollTimer = null;
let taskManagerPollTimer = null;
let taskManagerPollInFlight = false;
let providerConfigs = {};
let libraryFiles = { exams: [], textbooks: [], exams_root: "", textbooks_root: "" };
let activeTaskId = "";
let currentPage = "home";
let selectedTextbookPaths = new Set();
let textbookSelectionInitialized = false;
let activeTextbookGroups = {};
let disabledTextbookGroupKeys = new Set();
let latestTasks = [];
let activeTaskFilter = "all";
let activeTaskSort = "smart";
let resultViewData = null;
let activeResultQuestionId = "";
let modelQuestionTypeTab = "text";
let modelConnectionTests = {};
let practiceSourceFiles = [];
let latestPracticeSourceScope = null;
let latestPracticePlan = null;
let latestPracticeRequest = null;
let latestPracticeSet = null;
let practiceEditingIndex = -1;
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
  }
};

const pageOrder = ["home", "practice", "env", "textbook", "exam", "task", "result", "tasks", "monitor"];
const workflowStepPages = ["env", "exam", "task", "result"];
const taskStageGroups = [
  { key: "prepare", title: "准备真题", summary: "读取题目并确认题型和分值", stages: ["environment", "extract_exam", "exam_structure_review", "question_understanding", "figure_schema_planning"] },
  { key: "evidence", title: "检索教材依据", summary: "判断考点并核对教材依据", stages: ["textbook_index", "knowledge_planning", "retrieval", "evidence_selection"] },
  { key: "answer", title: "生成解析", summary: "组织答案并检查覆盖范围", stages: ["answer_generation", "answer_coverage"] },
  { key: "figures", title: "生成图件", summary: "绘制、审查和必要时回修图件", stages: ["figures"] },
  { key: "quality", title: "质量审查", summary: "检查内容完整性与专业表达", stages: ["content_quality", "content_quality_model_repair", "figures_after_content_quality_model_repair", "content_quality_local_repair"] },
  { key: "delivery", title: "生成交付物", summary: "生成 Word、渲染复核并最终验收", stages: ["docx", "docx_model_repair", "docx_repair", "docx_user_allowed_candidate", "docx_placeholder", "question_review", "render", "acceptance", "final_acceptance", "completed"] }
];
const stageProgressMilestones = {
  environment: 3, extract_exam: 6, exam_structure_review: 10, question_understanding: 13, figure_schema_planning: 16,
  textbook_index: 19, knowledge_planning: 25, retrieval: 31, evidence_selection: 40, answer_generation: 55,
  answer_coverage: 59, figures: 73, content_quality: 81, content_quality_model_repair: 83,
  figures_after_content_quality_model_repair: 85, content_quality_local_repair: 86, docx: 91,
  docx_model_repair: 92, docx_repair: 93, docx_user_allowed_candidate: 93, docx_placeholder: 93,
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
  "docx_user_allowed_candidate",
  "docx_placeholder",
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
  currentPage = page;
  if (page !== "tasks") stopTaskManagerPolling();
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
  }
  if (page === "result") {
    hydrateResultPage().catch(() => syncResultFiles());
  }
  window.scrollTo({ top: 0, behavior: "smooth" });
}

function updateStepIndicator(page) {
  const currentIndex = workflowStepPages.indexOf(page);
  $("practiceSidebarCollapse")?.addEventListener("click", togglePracticeSidebar);
$("practiceSidebarExpand")?.addEventListener("click", togglePracticeSidebar);
restorePracticeSidebarState();
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

const stageLabels = {
  environment: "检查本机环境",
  extract_exam: "读取真题题目",
  exam_structure_review: "确认题目与题型",
  question_understanding: "理解题目要求",
  figure_schema_planning: "规划所需图件",
  textbook_index: "加载教材检索表",
  knowledge_planning: "判断题目考查内容",
  retrieval: "匹配教材依据",
  evidence_selection: "模型教材引用确认",
  answer_generation: "生成结构化答案",
  answer_coverage: "检查答案覆盖",
  content_quality: "内容质量审计",
  content_quality_model_repair: "质量审查模型回修",
  figures_after_content_quality_model_repair: "回修后生成配图",
  content_quality_local_repair: "质量审查程序修复",
  figures: "生成配图",
  docx: "生成 Word 文档",
  docx_model_repair: "Word 文档模型回修",
  docx_repair: "Word 文档程序修复",
  docx_user_allowed_candidate: "生成允许通过候选文档",
  docx_placeholder: "生成待复核占位文档",
  question_review: "生成存疑审查文档",
  render: "生成 PDF/PNG 复核图",
  acceptance: "验收结果",
  final_acceptance: "最终验收",
  completed: "已完成"
};

function stageLabel(stage) {
  return stageLabels[stage] || stage || "待开始";
}

function statusLabel(status) {
  const labels = {
    completed: "已完成",
    failed: "需要处理",
    running: "生成中",
    paused: "已暂停",
    cancelled: "已取消",
    pending: "待开始",
    created: "待开始"
  };
  return labels[status] || status || "未知";
}

function taskFilterStatus(status, currentStage = "") {
  if (status === "completed" || currentStage === "completed") return "completed";
  if (status === "failed") return "failed";
  if (status === "cancelled") return "failed";
  if (status === "running") return "running";
  if (status === "paused") return "running";
  return "queued";
}

function isReviewDecisionTask(task) {
  if (Object.prototype.hasOwnProperty.call(task || {}, "review_decision_pending")) {
    return Boolean(task.review_decision_pending);
  }
  return task?.status === "paused" && task?.current_stage === "review_decision";
}

function isExamStructureReviewTask(task) {
  if (Object.prototype.hasOwnProperty.call(task || {}, "exam_structure_review_pending")) {
    return Boolean(task.exam_structure_review_pending);
  }
  return task?.status === "paused" && task?.current_stage === "exam_structure_review";
}

function isActionRequiredTask(task) {
  return isReviewDecisionTask(task) || isExamStructureReviewTask(task);
}

function reviewBadgeText(count) {
  return count > 99 ? "99+条信息" : `${count}条信息`;
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
    docx_user_allowed_candidate: "docx",
    docx_placeholder: "docx",
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
    failed: { icon: "fas fa-times", label: "失败" }
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
    openai: "OPENAI_API_KEY",
    deepseek: "DEEPSEEK_API_KEY",
    ark: "ARK_API_KEY",
    zhipu: "ZHIPU_API_KEY",
    bailian: "DASHSCOPE_API_KEY",
    yunwu: "YUNWU_API_KEY"
  };
  return map[name] || "";
}

function displayProviderName(name) {
  const labels = {
    openai: "OpenAI",
    deepseek: "DeepSeek",
    ark: "火山方舟",
    zhipu: "智谱 GLM",
    bailian: "阿里云百炼",
    yunwu: "云雾 API"
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

function setEnvNextEnabled(enabled) {
  const button = $("envNextBtn");
  if (!button) return;
  button.disabled = !enabled;
  button.classList.toggle("disabled", !enabled);
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
  const networkReady = Boolean(env?.network?.ok);
  const ready = runtimeReady && toolsReady && networkReady;
  setEnvNextEnabled(ready);
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
      ? '<i class="fas fa-check-circle"></i><strong>环境就绪，所有检查项均已通过</strong>'
      : hasNetworkCheck
        ? '<i class="fas fa-exclamation-circle"></i><strong>环境还不完整，请查看技术详情</strong>'
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
  const ok = window.confirm(`是否执行“${action.title}”？\n\n${action.description || ""}\n${action.impact || ""}`);
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
}

async function api(path, options = {}) {
  const res = await fetch(path, {
    ...options,
    headers: {
      "Content-Type": "application/json",
      ...(options.headers || {})
    }
  });
  const data = await res.json();
  if (!res.ok) throw new Error(data.error || res.statusText);
  return data;
}

async function refresh() {
  const version = await api("/api/version");
  $("versionBox").textContent = `平台版本 ${version.version} · 发布清单 ${version.release_manifest_exists ? "已存在" : "仅源码运行"}`;
  const providers = await api("/api/providers");
  providerConfigs = providers;
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
  updateProviderSummary(providers);
  await Promise.all([loadLibraryFiles(), loadPracticeHistory()]);
}

function practiceFileLabel(file) {
  if (!file) return "未选择文件";
  return `${file.name} · ${(file.size / 1024).toFixed(file.size > 1024 * 1024 ? 0 : 1)} KB`;
}

async function fileAsDataUrl(file) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(String(reader.result || ""));
    reader.onerror = () => reject(new Error(`${file.name} 读取失败。`));
    reader.readAsDataURL(file);
  });
}

function renderPracticeFilePreview() {
  const preview = $("practiceFilePreview");
  if (!preview) return;
  if (!practiceSourceFiles.length) {
    preview.innerHTML = '<i class="fas fa-file-circle-plus"></i><span>未选择文件，也可以直接粘贴截图</span>';
    return;
  }
  preview.innerHTML = practiceSourceFiles.map((file, index) => `
    <div class="practice-source-file">
      ${String(file.type || "").startsWith("image/")
        ? `<img src="${file.data_url}" alt="">`
        : `<i class="fas ${file.type === "application/pdf" ? "fa-file-pdf" : file.name.endsWith(".docx") ? "fa-file-word" : "fa-file-lines"}"></i>`}
      <span><strong>${escapeHtml(file.name)}</strong><small>${(Number(file.size || 0) / 1024).toFixed(1)} KB</small></span>
      <button type="button" data-practice-file-up="${index}" title="上移"><i class="fas fa-arrow-up"></i></button>
      <button type="button" data-practice-file-down="${index}" title="下移"><i class="fas fa-arrow-down"></i></button>
      <button type="button" data-practice-file-remove="${index}" title="删除"><i class="fas fa-xmark"></i></button>
    </div>
  `).join("");
  preview.querySelectorAll("[data-practice-file-remove]").forEach((button) => {
    button.addEventListener("click", () => {
      practiceSourceFiles.splice(Number(button.dataset.practiceFileRemove), 1);
      renderPracticeFilePreview();
      setText("practiceSourceStatus", practiceSourceFiles.length ? `已读取 ${practiceSourceFiles.length} 个文件` : "等待输入");
    });
  });
  const move = (index, offset) => {
    const target = index + offset;
    if (target < 0 || target >= practiceSourceFiles.length) return;
    [practiceSourceFiles[index], practiceSourceFiles[target]] = [practiceSourceFiles[target], practiceSourceFiles[index]];
    renderPracticeFilePreview();
  };
  preview.querySelectorAll("[data-practice-file-up]").forEach((button) => {
    button.addEventListener("click", () => move(Number(button.dataset.practiceFileUp), -1));
  });
  preview.querySelectorAll("[data-practice-file-down]").forEach((button) => {
    button.addEventListener("click", () => move(Number(button.dataset.practiceFileDown), 1));
  });
}

async function readPracticeFiles(fileList) {
  const files = Array.from(fileList || []);
  if (!files.length) {
    renderPracticeFilePreview();
    return;
  }
  if (files.length + practiceSourceFiles.length > 12) throw new Error("一次最多上传 12 个文件。");
  const total = files.reduce((sum, file) => sum + file.size, practiceSourceFiles.reduce((sum, file) => sum + Number(file.size || 0), 0));
  if (total > 36 * 1024 * 1024) throw new Error("上传文件总大小不能超过 36 MB。");
  for (const file of files) {
    if (file.size > 12 * 1024 * 1024) throw new Error(`${file.name} 超过 12 MB。`);
    practiceSourceFiles.push({
      name: file.name,
      type: file.type || "application/octet-stream",
      size: file.size,
      data_url: await fileAsDataUrl(file)
    });
  }
  renderPracticeFilePreview();
  setText("practiceSourceStatus", `已读取 ${practiceSourceFiles.length} 个文件`);
}

async function pastePracticeImages(event) {
  const items = Array.from(event.clipboardData?.items || []);
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
  await readPracticeFiles(imageFiles);
  setText("practiceSourceStatus", `已粘贴 ${imageFiles.length} 张截图`);
}

function practicePlainText(data) {
  const lines = [
    `专项训练目标：${data.blueprint?.training_goal || ""}`,
    `原题诊断：${data.source_analysis?.question_type || ""} · ${data.source_analysis?.difficulty || ""}`,
    ""
  ];
  for (const item of data.exercises || []) {
    lines.push(`${item.number}. [${item.difficulty}] ${item.stem}`);
    for (const option of item.options || []) lines.push(`${option.label}. ${option.text}`);
    lines.push(`答案：${item.answer}`);
    if (item.solution_steps?.length) lines.push(`解析：${item.solution_steps.join("；")}`);
    lines.push("");
  }
  return lines.join("\n");
}

let practiceMathJaxPromise = null;

function practiceMarkdown(value) {
  return escapeHtml(String(value || ""))
    .replace(/\*\*([^*\n]+)\*\*/g, "<strong>$1</strong>")
    .replace(/`([^`\n]+)`/g, "<code>$1</code>")
    .replace(/\n/g, "<br>");
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
    script.src = "https://cdn.jsdelivr.net/npm/mathjax@3/es5/tex-mml-chtml.js";
    script.async = true;
    script.onload = () => resolve(window.MathJax);
    script.onerror = () => reject(new Error("公式渲染组件加载失败"));
    document.head.appendChild(script);
  });
  return practiceMathJaxPromise;
}

async function typesetPracticeMath() {
  const container = $("practiceResults");
  if (!container) return;
  try {
    const mathJax = await ensurePracticeMathJax();
    mathJax.typesetClear?.([container]);
    await mathJax.typesetPromise([container]);
  } catch {
    // 网络不可用时保留可读的 LaTeX 原文，不影响题目和导出。
  }
}

function practiceExtrasHtml(item, location) {
  const formulas = (item.formulas || []).filter((row) => String(row.location || "stem").includes(location));
  const tables = (item.tables || []).filter((row) => String(row.location || "stem").includes(location));
  const figures = (item.figures || []).filter((row) => String(row.location || "stem").includes(location));
  return [
    ...formulas.map((formula) => `
      <figure class="practice-formula">
        ${formula.caption ? `<figcaption>${escapeHtml(formula.caption)}</figcaption>` : ""}
        <div class="practice-math">\\[${escapeHtml(formula.latex)}\\]</div>
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
    ...figures.map((figure) => figure.series?.some((series) => series.points?.length)
      ? `<figure class="practice-generated-chart">
          ${figure.title ? `<figcaption>${escapeHtml(figure.title)}</figcaption>` : ""}
          <canvas data-practice-figure="${escapeHtml(figure.figure_id)}" aria-label="${escapeHtml(figure.title || "题目图表")}"></canvas>
          ${figure.description ? `<p>${escapeHtml(figure.description)}</p>` : ""}
        </figure>`
      : `<figure class="practice-diagram-spec">
          <figcaption>${escapeHtml(figure.title || "图示")}</figcaption>
          <p>${escapeHtml(figure.description || "请根据题意完成图示。")}</p>
        </figure>`
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
    const points = (figure.series || []).flatMap((series) => series.points || []);
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
    ctx.fillStyle = "#64748b"; ctx.font = "12px sans-serif";
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
const PRACTICE_FAVORITES_KEY = "practiceFavoritesV1";
let practiceFavorites = new Set();
let practiceDrawerWasSkipped = false;

function loadPracticeFavorites() {
  try {
    const raw = localStorage.getItem(PRACTICE_FAVORITES_KEY);
    const arr = raw ? JSON.parse(raw) : [];
    practiceFavorites = new Set(Array.isArray(arr) ? arr.map(String) : []);
  } catch (e) {
    practiceFavorites = new Set();
  }
}

function savePracticeFavorites() {
  try {
    localStorage.setItem(PRACTICE_FAVORITES_KEY, JSON.stringify(Array.from(practiceFavorites)));
  } catch (e) { /* localStorage 不可用时忽略 */ }
}

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

function togglePracticeConfig(forceOpen) {
  const card = $("practiceConfigCard");
  if (!card) return;
  const willOpen = forceOpen !== undefined ? !!forceOpen : card.classList.contains("practice-config-card--collapsed");
  card.classList.toggle("practice-config-card--collapsed", !willOpen);
  $("practiceConfigToggle")?.setAttribute("aria-expanded", String(willOpen));
}

function updatePracticeConfigSummary() {
  const count = $("practiceCount")?.value || "--";
  const difficulty = $("practiceDifficulty")?.value || "--";
  const types = Array.from(document.querySelectorAll('input[name="practiceQuestionType"]:checked')).map((i) => i.value);
  const typeLabel = types.length === 0 ? "题型随机" : types.join("+");
  setText("practiceConfigSummary", `${count} 题 · ${difficulty} · ${typeLabel}`);
}

function openPracticeScopeDrawer() {
  const drawer = $("practiceScopeDrawer");
  if (!drawer) return;
  drawer.classList.remove("hidden");
  drawer.classList.add("practice-scope-drawer--open");
  drawer.setAttribute("aria-hidden", "false");
}

function closePracticeScopeDrawer() {
  const drawer = $("practiceScopeDrawer");
  if (!drawer) return;
  drawer.classList.add("hidden");
  drawer.classList.remove("practice-scope-drawer--open");
  drawer.setAttribute("aria-hidden", "true");
}

function updatePracticeScopePreview() {
  const strategy = document.querySelector('input[name="practiceSetStrategy"]:checked')?.value || "";
  const selectedCount = document.querySelectorAll('input[name="practiceSourceQuestion"]:checked').length;
  const targeted = Number($("practiceTargetedCount")?.value || 10);
  const variants = Number($("practiceVariantsPerQuestion")?.value || 1);
  let n = 0;
  if (strategy === "targeted_set") n = Math.min(targeted, 30);
  else if (strategy === "parallel_exam") n = selectedCount;
  else if (strategy === "per_question") n = Math.min(30, selectedCount * variants);
  setText("practiceScopePreviewCount", `${n} 题`);
  const confirmBtn = $("practiceSourceConfirmBtn");
  if (confirmBtn) confirmBtn.disabled = !strategy || selectedCount === 0;
}

function setPracticeStage(stage) {
  const order = ["submit", "analyze", "scope", "plan", "generate"];
  const activeIndex = order.indexOf(stage);
  document.querySelectorAll(".practice-step").forEach((el) => {
    const idx = order.indexOf(el.dataset.stage);
    el.classList.remove("practice-step--active", "practice-step--done", "practice-step--skipped");
    if (idx === -1) return;
    if (idx < activeIndex) el.classList.add("practice-step--done");
    else if (idx === activeIndex) el.classList.add("practice-step--active");
  });
}

function setPracticeStageSkipped(stage) {
  const el = document.querySelector(`.practice-step[data-stage="${stage}"]`);
  if (!el) return;
  el.classList.remove("practice-step--active", "practice-step--done");
  el.classList.add("practice-step--skipped");
}

function markAllPracticeStagesDone() {
  document.querySelectorAll(".practice-step").forEach((el) => {
    el.classList.remove("practice-step--active", "practice-step--skipped");
    el.classList.add("practice-step--done");
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
  const warnings = data.quality?.warnings || [];
  setText("practiceSummaryQuality", warnings.length ? "需复核" : "通过");
  setText("practiceSummaryStatus", warnings.length ? "已生成 · 需复核" : "已生成 · 通过");
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

function renderPracticeFilters(data) {
  const exercises = data.exercises || [];
  const types = new Set();
  const difficulties = new Set();
  const tags = new Set();
  exercises.forEach((item) => {
    if (item.question_type) types.add(item.question_type);
    if (item.difficulty) difficulties.add(item.difficulty);
    (item.knowledge_points || []).forEach((t) => tags.add(t));
  });
  $("practiceTypeFilterChips").innerHTML = Array.from(types).map((t) => `
    <button type="button" class="practice-filter-chip" data-filter-type="${escapeHtml(t)}">${escapeHtml(t)}</button>
  `).join("");
  $("practiceDifficultyFilterChips").innerHTML = Array.from(difficulties).map((t) => `
    <button type="button" class="practice-filter-chip" data-filter-difficulty="${escapeHtml(t)}">${escapeHtml(t)}</button>
  `).join("");
  $("practiceTagFilterChips").innerHTML = Array.from(tags).map((t) => `
    <button type="button" class="practice-filter-chip" data-filter-tag="${escapeHtml(t)}">${escapeHtml(t)}</button>
  `).join("");
  PRACTICE_FILTERS.type.clear();
  PRACTICE_FILTERS.difficulty.clear();
  PRACTICE_FILTERS.tag.clear();
  document.querySelectorAll(".practice-filter-chip").forEach((c) => c.classList.remove("active"));
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

function togglePracticeFavorite(index, button) {
  const key = String(index);
  if (practiceFavorites.has(key)) practiceFavorites.delete(key);
  else practiceFavorites.add(key);
  savePracticeFavorites();
  const article = document.querySelector(`.practice-exercise[data-exercise-index="${index}"]`);
  if (article) article.classList.toggle("practice-exercise--favorited", practiceFavorites.has(key));
  if (button) {
    button.classList.toggle("active", practiceFavorites.has(key));
    const icon = button.querySelector("i");
    if (icon) icon.className = practiceFavorites.has(key) ? "fas fa-star" : "far fa-star";
  }
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
        <small>${row.question_count || 0} 题 · ${escapeHtml(String(row.updated_at || "").replace("T", " ").slice(0, 16))}</small>
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
          $("practiceCount").value = String(record.request.count || $("practiceCount").value || 5);
          if (record.request.difficulty) $("practiceDifficulty").value = record.request.difficulty;
          $("practiceFocus").value = record.request.focus || "";
          document.querySelectorAll('input[name="practiceQuestionType"]').forEach((input) => {
            input.checked = (record.request.question_types || []).includes(input.value);
          });
          updatePracticeConfigSummary();
        }
        latestPracticeRequest = record.request || null;
        latestPracticePlan = null;
        renderPracticeResults(record.data);
        setText("practiceSourceStatus", "已复用历史记录");
      } catch (error) {
        $("practiceError").textContent = String(error).replace(/^Error:\s*/, "");
        $("practiceError").classList.remove("hidden");
      }
    });
  });
}

function togglePracticeSidebar() {
  const wb = document.querySelector(".practice-workbench");
  if (!wb) return;
  wb.classList.toggle("sidebar-collapsed");
  const collapsed = wb.classList.contains("sidebar-collapsed");
  try { localStorage.setItem("practiceSidebarCollapsed", collapsed ? "1" : "0"); } catch (e) {}
}

function restorePracticeSidebarState() {
  try {
    if (localStorage.getItem("practiceSidebarCollapsed") === "1") {
      document.querySelector(".practice-workbench")?.classList.add("sidebar-collapsed");
    }
  } catch (e) {}
}

function renderPracticeResults(data) {
  latestPracticeSet = data;
  $("practiceEmpty")?.classList.add("hidden");
  $("practiceLoading")?.classList.add("hidden");
  $("practicePlanReview")?.classList.add("hidden");
  $("practiceResults")?.classList.remove("hidden");
  closePracticeScopeDrawer();
  markAllPracticeStagesDone();
  setPracticeStageDescription("练习题已生成完毕，可继续下载、编辑或回到蓝图调整。");
  const strategyLabels = {
    single: "单题专项练习已生成",
    targeted_set: "整套专项补强已生成",
    parallel_exam: "平行试卷已生成",
    per_question: "逐题变式已生成"
  };
  setText("practiceResultMode", strategyLabels[data.generation_strategy] || "专项练习已生成");
  setText("practiceTrainingGoal", data.blueprint?.training_goal || "专项练习");
  const analysis = data.source_analysis || {};
  setText("practiceAnalysisTitle", [analysis.subject, analysis.question_type, analysis.difficulty].filter(Boolean).join(" · "));
  setText("practiceAnalysisMeta", [analysis.question_type, analysis.difficulty].filter(Boolean).join(" · "));
  const analysisTags = [...(analysis.knowledge_points || []), ...(analysis.skills || [])];
  $("practiceKnowledgeTags").innerHTML = analysisTags.map((tag) => `<span>${escapeHtml(tag)}</span>`).join("");
  $("practiceStrategy").innerHTML = (analysis.solution_strategy || []).map((item) => `<li>${escapeHtml(item)}</li>`).join("");
  const warnings = data.quality?.warnings || [];
  const isPassed = warnings.length === 0;
  $("practiceQuality").className = `practice-quality ${isPassed ? "passed" : "warning"}`;
  $("practiceQuality").innerHTML = isPassed
    ? `<i class="fas fa-circle-check"></i><span><strong>结构检查通过</strong>已生成 ${data.quality?.generated_count || 0} 题，题干、答案和解析字段完整。</span>`
    : `<i class="fas fa-triangle-exclamation"></i><span><strong>已生成 ${data.quality.generated_count} 题，建议复核</strong>${escapeHtml(warnings.join("；"))}</span>`;
  const badge = $("practiceQualityBadge");
  if (badge) {
    badge.className = `practice-quality-badge ${isPassed ? "passed" : "warning"}`;
    badge.innerHTML = isPassed
      ? '<i class="fas fa-circle-check"></i>结构检查通过'
      : '<i class="fas fa-triangle-exclamation"></i>需复核';
  }
  setPracticeStatusBanner(isPassed
    ? `已生成 ${data.exercises?.length || 0} 题 · 结构检查通过`
    : `已生成 ${data.exercises?.length || 0} 题 · 建议复核`,
    isPassed ? "done" : "error");
  renderPracticeBlueprintSummary(data);
  renderPracticeFilters(data);
  const practiceSourceLookup = new Map((data.selected_source_questions || []).map((item) => [String(item.source_question_id), item]));
  $("practiceExerciseList").innerHTML = (data.exercises || []).map((item, idx) => {
    const sourceQuestion = practiceSourceLookup.get(String(item.source_question_id || ""));
    const isFavorited = practiceFavorites.has(String(idx));
    const tagsArr = item.knowledge_points || [];
    return `
    <article class="practice-exercise${isFavorited ? " practice-exercise--favorited" : ""}" data-exercise-index="${idx}" data-exercise-type="${escapeHtml(item.question_type || "")}" data-exercise-difficulty="${escapeHtml(item.difficulty || "")}" data-exercise-tags="${escapeHtml(tagsArr.join("|"))}">
      ${sourceQuestion ? `<div class="practice-source-link"><i class="fas fa-link"></i>来源：原题 ${escapeHtml(sourceQuestion.number || "")} · ${escapeHtml(sourceQuestion.title || "")}</div>` : ""}
      <header>
        <div><b>${item.number}</b><span>${escapeHtml(item.question_type)}</span></div>
        <div>
          <small>${escapeHtml(item.target_skill || "核心能力训练")}</small>
          <em class="${item.difficulty === "挑战" ? "hard" : item.difficulty === "基础" ? "easy" : ""}">${escapeHtml(item.difficulty)}</em>
          <div class="practice-exercise__actions">
            <button type="button" data-practice-edit="${idx}" title="编辑本题"><i class="fas fa-pen"></i></button>
            <button type="button" data-practice-regenerate="${idx}" title="重新生成本题"><i class="fas fa-rotate"></i></button>
            <button type="button" data-practice-favorite="${idx}" class="${isFavorited ? "active" : ""}" title="收藏本题"><i class="fa${isFavorited ? "s" : "r"} fa-star"></i></button>
          </div>
        </div>
      </header>
      <div class="practice-stem">${practiceMarkdown(item.stem)}</div>
      ${practiceExtrasHtml(item, "stem")}
      ${item.options?.length ? `<div class="practice-options">${item.options.map((option) => `<p><b>${escapeHtml(option.label)}</b>${escapeHtml(option.text)}</p>`).join("")}</div>` : ""}
      ${tagsArr.length ? `<div class="practice-exercise-tags" style="padding:0 16px 8px;display:flex;flex-wrap:wrap;gap:4px;">${tagsArr.map((t) => `<span>${escapeHtml(t)}</span>`).join("")}</div>` : ""}
      <details>
        <summary>查看答案与解析</summary>
        <div class="practice-answer"><strong>答案</strong><p>${practiceMarkdown(item.answer)}</p></div>
        ${practiceExtrasHtml(item, "solution")}
        <ol>${(item.solution_steps || []).map((step) => `<li>${escapeHtml(step)}</li>`).join("")}</ol>
      </details>
    </article>
  `;
  }).join("");
  updatePracticeAsideSummary(data);
  $("practiceWordBtn")?.removeAttribute("disabled");
  $("practiceCopyBtn")?.removeAttribute("disabled");
  $("practiceSaveBtn")?.removeAttribute("disabled");
  requestAnimationFrame(() => {
    drawPracticeCharts(data);
    typesetPracticeMath();
    applyExerciseFilters();
  });
  document.querySelectorAll("[data-practice-edit]").forEach((button) => {
    button.addEventListener("click", () => openPracticeEditor(Number(button.dataset.practiceEdit)));
  });
  document.querySelectorAll("[data-practice-regenerate]").forEach((button) => {
    button.addEventListener("click", () => regeneratePracticeQuestion(Number(button.dataset.practiceRegenerate), button));
  });
  document.querySelectorAll("[data-practice-favorite]").forEach((button) => {
    button.addEventListener("click", () => togglePracticeFavorite(Number(button.dataset.practiceFavorite), button));
  });
  document.querySelectorAll(".practice-filter-chip").forEach((chip) => {
    chip.addEventListener("click", () => {
      const group = chip.dataset.filterType ? "type" : chip.dataset.filterDifficulty ? "difficulty" : chip.dataset.filterTag ? "tag" : null;
      if (!group) return;
      const value = chip.dataset.filterType || chip.dataset.filterDifficulty || chip.dataset.filterTag;
      togglePracticeFilter(group, value, chip);
    });
  });
}

function practiceRequestPayload() {
  return {
    question_text: $("practiceQuestionText").value.trim(),
    source_files: practiceSourceFiles,
    count: Number($("practiceCount").value),
    difficulty: $("practiceDifficulty").value,
    question_types: Array.from(document.querySelectorAll('input[name="practiceQuestionType"]:checked')).map((input) => input.value),
    focus: $("practiceFocus").value.trim(),
    provider: $("answerProviderSelect")?.value || $("providerSelect")?.value || "",
    model: selectedTextRoleModel("answer") || selectedModel() || "",
    vision_provider: $("visionProviderSelect")?.value || "",
    vision_model: selectedVisionModel(),
    thinking: selectedThinkingMode()
  };
}

function showPracticeLoading(title) {
  $("practiceEmpty")?.classList.add("hidden");
  $("practiceResults")?.classList.add("hidden");
  $("practiceSourceSelection")?.classList.add("hidden");
  $("practicePlanReview")?.classList.add("hidden");
  $("practiceLoading")?.classList.remove("hidden");
  setText("practiceLoadingTitle", title);
}

function renderPracticeSourceSelection(data) {
  latestPracticeSourceScope = data.source_scope || null;
  latestPracticePlan = null;
  $("practiceLoading")?.classList.add("hidden");
  $("practiceEmpty")?.classList.add("hidden");
  $("practiceResults")?.classList.add("hidden");
  $("practicePlanReview")?.classList.add("hidden");
  setPracticeStage("scope");
  setPracticeStageDescription("题目集已识别为多道原题；请先选择生成方式与参与训练的原题范围。");
  setPracticeStatusBanner("等待选择生成方式", "loading");
  const questions = data.source_scope?.questions || [];
  setText("practiceSourceSetTitle", data.source_scope?.title || "请选择要生成专项练习的原题");
  setText("practiceSourceCount", `${questions.length} 道原题`);
  $("practiceSourceQuestionList").innerHTML = questions.map((item) => `
    <label>
      <input type="checkbox" name="practiceSourceQuestion" value="${escapeHtml(item.source_question_id)}" checked>
      <span>
        <b>${escapeHtml(item.number || item.source_question_id)}</b>
        <div><strong>${escapeHtml(item.title || "未命名题目")}</strong><small>${escapeHtml([item.question_type, ...(item.knowledge_points || [])].filter(Boolean).join(" · "))}</small><p>${escapeHtml(item.stem_excerpt || "")}</p></div>
      </span>
    </label>
  `).join("");
  document.querySelectorAll('input[name="practiceSetStrategy"]').forEach((input) => { input.checked = false; });
  $("practiceStrategySettings")?.classList.add("hidden");
  $("practiceSourceSelectionError")?.classList.add("hidden");
  updatePracticeScopePreview();
  openPracticeScopeDrawer();
  setText("practiceSourceStatus", "请选择原题");
}

function updatePracticeStrategySettings() {
  const strategy = document.querySelector('input[name="practiceSetStrategy"]:checked')?.value || "";
  const settings = $("practiceStrategySettings");
  settings?.classList.toggle("hidden", !strategy);
  $("practiceTargetedCountRow")?.classList.toggle("hidden", strategy !== "targeted_set");
  $("practiceVariantsRow")?.classList.toggle("hidden", strategy !== "per_question");
  const selectedCount = document.querySelectorAll('input[name="practiceSourceQuestion"]:checked').length;
  const variants = Number($("practiceVariantsPerQuestion")?.value || 1);
  if (strategy === "targeted_set") {
    setText("practiceStrategyHint", `将在选中的 ${selectedCount} 道原题范围内，按考点权重生成总共 ${Number($("practiceTargetedCount")?.value || 10)} 道练习。`);
  } else if (strategy === "parallel_exam") {
    setText("practiceStrategyHint", `将生成 ${selectedCount} 道平行题，每道选中原题对应一道。`);
  } else if (strategy === "per_question") {
    const total = Math.min(30, selectedCount * variants);
    setText("practiceStrategyHint", `预计生成 ${total} 道变式；为保证单次生成稳定，总量最多 30 道。`);
  } else {
    setText("practiceStrategyHint", "");
  }
  updatePracticeScopePreview();
}

async function planSelectedSourceQuestions() {
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
  latestPracticeRequest = {
    ...latestPracticeRequest,
    source_scope: latestPracticeSourceScope,
    selected_source_questions: selected,
    generation_strategy: strategy,
    strategy_count: Number($("practiceTargetedCount")?.value || 10),
    variants_per_question: Number($("practiceVariantsPerQuestion")?.value || 1)
  };
  const button = $("practiceSourceConfirmBtn");
  const original = button.innerHTML;
  button.disabled = true;
  button.innerHTML = '<i class="fas fa-circle-notch fa-spin"></i>正在设计蓝图';
  showPracticeLoading("正在围绕选中的原题设计训练蓝图");
  try {
    const plan = await api("/api/practice/plan", {
      method: "POST",
      body: JSON.stringify(latestPracticeRequest)
    });
    if (plan.requires_source_selection) throw new Error("题目范围尚未确认，请重新选择。");
    renderPracticePlan(plan);
  } catch (error) {
    renderPracticeSourceSelection({ source_scope: latestPracticeSourceScope });
    errorBox.textContent = String(error).replace(/^Error:\s*/, "");
    errorBox.classList.remove("hidden");
  } finally {
    button.disabled = false;
    button.innerHTML = original;
  }
}

function renderPracticePlan(plan) {
  latestPracticePlan = plan;
  $("practiceLoading")?.classList.add("hidden");
  $("practiceEmpty")?.classList.add("hidden");
  $("practiceResults")?.classList.add("hidden");
  $("practiceSourceSelection")?.classList.add("hidden");
  $("practicePlanReview")?.classList.remove("hidden");
  setText("practicePlanGoal", plan.blueprint?.training_goal || "训练蓝图");
  const analysis = plan.source_analysis || {};
  $("practicePlanAnalysis").innerHTML = `
    <strong>${escapeHtml([analysis.subject, analysis.question_type, analysis.difficulty].filter(Boolean).join(" · "))}</strong>
    <p>${escapeHtml([...(analysis.knowledge_points || []), ...(analysis.skills || [])].join("、"))}</p>
  `;
  $("practicePlanList").innerHTML = (plan.blueprint?.exercise_plan || []).map((item) => `
    <article>
      <b>${item.number}</b>
      <div><strong>${escapeHtml(item.question_type)} · ${escapeHtml(item.difficulty)}</strong><span>${escapeHtml(item.target_skill || "核心能力")}</span></div>
      <p>${escapeHtml(item.variation_type || "")}${item.design_intent ? `：${escapeHtml(item.design_intent)}` : ""}</p>
    </article>
  `).join("");
  setPracticeStage("plan");
  setPracticeStageDescription("训练蓝图已生成；确认后将按此蓝图生成具体练习题。");
  setText("practiceSourceStatus", "蓝图待确认");
}

async function planPractice(event) {
  event.preventDefault();
  const request = practiceRequestPayload();
  const errorBox = $("practiceError");
  if (!request.question_text && !request.source_files.length) {
    errorBox.textContent = "请粘贴题目文字或上传题目文件。";
    errorBox.classList.remove("hidden");
    return;
  }
  errorBox.classList.add("hidden");
  showPracticeLoading("正在解析原题并设计训练蓝图");
  setPracticeStage("analyze");
  setPracticeStageDescription("正在解析原题考点；若是题目集将弹出范围确认抽屉。");
  setPracticeStatusBanner("解析中", "loading");
  const button = $("practiceGenerateBtn");
  button.disabled = true;
  button.innerHTML = '<i class="fas fa-circle-notch fa-spin"></i>正在设计蓝图';
  try {
    latestPracticeRequest = request;
    const plan = await api("/api/practice/plan", {
      method: "POST",
      body: JSON.stringify(request)
    });
    if (plan.requires_source_selection) renderPracticeSourceSelection(plan);
    else renderPracticePlan(plan);
  } catch (error) {
    $("practiceLoading")?.classList.add("hidden");
    $("practiceEmpty")?.classList.remove("hidden");
    errorBox.textContent = String(error).replace(/^Error:\s*/, "");
    errorBox.classList.remove("hidden");
  } finally {
    button.disabled = false;
    button.innerHTML = '<i class="fas fa-diagram-project"></i>先解析并生成训练蓝图';
  }
}

async function generatePracticeFromPlan() {
  if (!latestPracticePlan || !latestPracticeRequest) return;
  const button = $("practicePlanConfirmBtn");
  const original = button.innerHTML;
  button.disabled = true;
  button.innerHTML = '<i class="fas fa-circle-notch fa-spin"></i>正在生成';
  showPracticeLoading("蓝图已确认，正在生成完整练习");
  try {
    const data = await api("/api/practice/generate-from-plan", {
      method: "POST",
      body: JSON.stringify({ ...latestPracticeRequest, plan: latestPracticePlan })
    });
    renderPracticeResults(data);
    markAllPracticeStagesDone();
    setPracticeStageDescription("练习题已生成完毕，可继续下载、编辑或回到蓝图调整。");
    setText("practiceSourceStatus", "已生成并保存");
    await loadPracticeHistory();
  } catch (error) {
    $("practiceLoading")?.classList.add("hidden");
    renderPracticePlan(latestPracticePlan);
    $("practiceError").textContent = String(error).replace(/^Error:\s*/, "");
    $("practiceError").classList.remove("hidden");
  } finally {
    button.disabled = false;
    button.innerHTML = original;
  }
}

async function saveCurrentPractice(showFeedback = true) {
  if (!latestPracticeSet) return;
  const record = await api("/api/practice/history", {
    method: "POST",
    body: JSON.stringify({ data: latestPracticeSet })
  });
  latestPracticeSet = record.data;
  if (showFeedback) {
    const button = $("practiceSaveBtn");
    const original = button.innerHTML;
    button.innerHTML = '<i class="fas fa-check"></i>已保存';
    setTimeout(() => { button.innerHTML = original; }, 1400);
  }
  await loadPracticeHistory();
}

async function loadPracticeHistory() {
  const container = $("practiceHistoryList");
  if (!container) return;
  const data = await api("/api/practice/history");
  const rows = data.records || [];
  container.innerHTML = rows.length ? rows.map((row) => `
    <button type="button" data-practice-history="${escapeHtml(row.history_id)}">
      <strong>${escapeHtml(row.title || "研究生专项练习")}</strong>
      <span>${row.question_count || 0} 题 · ${escapeHtml(String(row.updated_at || "").replace("T", " ").slice(0, 16))}</span>
    </button>
  `).join("") : "<p>暂无历史记录</p>";
  container.querySelectorAll("[data-practice-history]").forEach((button) => {
    button.addEventListener("click", async () => {
      try {
        const record = await api(`/api/practice/history/${encodeURIComponent(button.dataset.practiceHistory)}`);
        latestPracticeRequest = record.request || null;
        latestPracticePlan = null;
        renderPracticeResults(record.data);
        setText("practiceSourceStatus", "已载入历史记录");
      } catch (error) {
        $("practiceError").textContent = String(error).replace(/^Error:\s*/, "");
        $("practiceError").classList.remove("hidden");
      }
    });
  });
  renderPracticeRecentHistory(rows);
}

function openPracticeEditor(index) {
  const item = latestPracticeSet?.exercises?.[index];
  if (!item) return;
  practiceEditingIndex = index;
  const typeSelect = $("practiceEditType");
  typeSelect.innerHTML = ["单选题", "多选题", "判断题", "填空题", "简答题", "计算题", "作图题", "综合题"]
    .map((type) => `<option${type === item.question_type ? " selected" : ""}>${type}</option>`).join("");
  $("practiceEditDifficulty").value = item.difficulty || "进阶";
  $("practiceEditSkill").value = item.target_skill || "";
  $("practiceEditStem").value = item.stem || "";
  $("practiceEditOptions").value = (item.options || []).map((option) => option.text || "").join("\n");
  $("practiceEditAnswer").value = item.answer || "";
  $("practiceEditSteps").value = (item.solution_steps || []).join("\n");
  $("practiceEditFormulas").value = (item.formulas || []).map((row) => `${row.location || "stem"} | ${row.latex || ""} | ${row.caption || ""}`).join("\n");
  $("practiceEditTables").value = JSON.stringify(item.tables || [], null, 2);
  $("practiceEditFigures").value = JSON.stringify(item.figures || [], null, 2);
  $("practiceEditorError").classList.add("hidden");
  setText("practiceEditorTitle", `编辑第 ${index + 1} 题`);
  $("practiceEditor").showModal();
}

function applyPracticeEditor(event) {
  event.preventDefault();
  const item = latestPracticeSet?.exercises?.[practiceEditingIndex];
  if (!item) return;
  try {
    const tables = JSON.parse($("practiceEditTables").value || "[]");
    const figures = JSON.parse($("practiceEditFigures").value || "[]");
    const options = $("practiceEditOptions").value.split("\n").map((text) => text.trim()).filter(Boolean)
      .map((text, index) => ({ label: String.fromCharCode(65 + index), text }));
    const formulas = $("practiceEditFormulas").value.split("\n").map((line, index) => {
      const [location, latex, caption] = line.split("|").map((part) => part.trim());
      return { formula_id: `f${index + 1}`, location: location || "stem", latex, caption: caption || "", display: true };
    }).filter((row) => row.latex);
    Object.assign(item, {
      question_type: $("practiceEditType").value,
      difficulty: $("practiceEditDifficulty").value,
      target_skill: $("practiceEditSkill").value.trim(),
      stem: $("practiceEditStem").value.trim(),
      options,
      answer: $("practiceEditAnswer").value.trim(),
      solution_steps: $("practiceEditSteps").value.split("\n").map((step) => step.trim()).filter(Boolean),
      formulas,
      tables: Array.isArray(tables) ? tables : [],
      figures: Array.isArray(figures) ? figures : []
    });
    $("practiceEditor").close();
    renderPracticeResults(latestPracticeSet);
    saveCurrentPractice(false).catch(() => {});
  } catch (error) {
    $("practiceEditorError").textContent = `结构数据格式错误：${String(error).replace(/^Error:\s*/, "")}`;
    $("practiceEditorError").classList.remove("hidden");
  }
}

async function regeneratePracticeQuestion(index, button) {
  if (!latestPracticeSet) return;
  const instruction = window.prompt("可填写本次重新生成要求；留空则保持训练目标换一种变式。", "") ?? null;
  if (instruction === null) return;
  const original = button.innerHTML;
  button.disabled = true;
  button.innerHTML = '<i class="fas fa-circle-notch fa-spin"></i>';
  try {
    const response = await api("/api/practice/regenerate", {
      method: "POST",
      body: JSON.stringify({
        practice: latestPracticeSet,
        exercise_index: index,
        instruction,
        provider: $("answerProviderSelect")?.value || $("providerSelect")?.value || "",
        model: selectedTextRoleModel("answer") || selectedModel() || "",
        thinking: selectedThinkingMode()
      })
    });
    latestPracticeSet.exercises[index] = response.exercise;
    renderPracticeResults(latestPracticeSet);
    await saveCurrentPractice(false);
  } catch (error) {
    $("practiceError").textContent = String(error).replace(/^Error:\s*/, "");
    $("practiceError").classList.remove("hidden");
  } finally {
    button.disabled = false;
    button.innerHTML = original;
  }
}

async function downloadPracticeWord() {
  if (!latestPracticeSet) return;
  const button = $("practiceWordBtn");
  const original = button.innerHTML;
  button.disabled = true;
  button.innerHTML = '<i class="fas fa-circle-notch fa-spin"></i>正在生成';
  const response = await fetch("/api/practice/export", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(latestPracticeSet)
  });
  if (!response.ok) {
    const data = await response.json().catch(() => ({}));
    button.disabled = false;
    button.innerHTML = original;
    throw new Error(data.error || "Word 生成失败。");
  }
  const blob = await response.blob();
  const link = document.createElement("a");
  link.href = URL.createObjectURL(blob);
  link.download = "研究生专项练习.docx";
  link.click();
  URL.revokeObjectURL(link.href);
  button.disabled = false;
  button.innerHTML = original;
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
  examSelect.innerHTML = "";
  textbookChecklist.innerHTML = "";
  if (taskTextbookChecklist) taskTextbookChecklist.innerHTML = "";
  if (examCardList) examCardList.innerHTML = "";
  $("textbooksDir").value = libraryFiles.textbooks_root || "";
  const textbookPaths = (libraryFiles.textbooks || []).map((file) => file.path);
  if (!textbookSelectionInitialized && textbookPaths.length) {
    selectedTextbookPaths = new Set(textbookPaths);
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
    for (const [index, file] of (libraryFiles.exams || []).entries()) {
      const option = document.createElement("option");
      option.value = file.path;
      option.textContent = fileLabel(file);
      examSelect.appendChild(option);
      if (examCardList) {
        const card = document.createElement("button");
        card.type = "button";
        card.className = `exam-card ${index === 0 ? "selected" : ""}`;
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
    $("examPath").value = examSelect.value || "";
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
  const selected = selectedTextbooks();
  const box = $("selectedTextbookBar");
  const current = $("currentTextbookDisplay");
  const displayNames = selectedTextbookDisplayNames();
  const seen = new Set();
  const names = selected
    .map((path) => displayNames[path] || displayBookName((libraryFiles.textbooks || []).find((file) => file.path === path)?.name || shortName(path)))
    .filter(Boolean)
    .filter((name) => {
      if (seen.has(name)) return false;
      seen.add(name);
      return true;
    });
  const preview = names.slice(0, 3).join("、");
  const detail = names.length > 3 ? `${preview} 等 ${names.length} 本` : preview;
  const text = names.length ? `已选择 ${names.length} 本教材` : "未勾选教材，将默认使用教材库中的全部教材。";
  if (box) {
    box.classList.toggle("hidden", !names.length);
    box.innerHTML = `<span><i class="fas fa-check-circle"></i><strong>${escapeHtml(text)}</strong><small>${escapeHtml(detail)}</small></span><button class="text-button" type="button" onclick="clearTextbookSelection()">清空选择</button>`;
  }
  if (current) current.textContent = names.length ? `当前教材：${text}（${detail}）` : "当前教材：未单独选择，默认使用教材库全部教材。";
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
}

async function deleteLibraryFile(kind, paths, label) {
  const pathList = Array.isArray(paths) ? paths : [paths];
  const validPaths = pathList.filter(Boolean);
  if (!validPaths.length) return;
  const message = kind === "exam"
    ? `确定删除真题“${label}”吗？`
    : `确定删除教材“${label}”吗？${validPaths.length > 1 ? `这会删除 ${validPaths.length} 个文件。` : ""}`;
  if (!window.confirm(message)) return;
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
  input.addEventListener("change", () => renderUploadSelection(kind));
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
  if (hasImages && !providerHasVision(visionCfg)) {
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

function rememberModelConnectionTest(providerName, modelName, ok, error = "") {
  const key = modelConnectionTestKey(providerName, modelName);
  if (!key) return;
  modelConnectionTests[key] = {
    ok: Boolean(ok),
    error: String(error || "").slice(0, 300),
    testedAt: new Date().toISOString()
  };
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
  setModelRoleStatus("visionRoleStatus", routeConnectionStatus(visionRoute()));
  setModelRoleStatus("imageRoleStatus", routeConnectionStatus(imageRoute()));
}

function questionTypeModelCards() {
  const reasoning = textRoleRoute("reasoning", "文本推理模型");
  const answer = textRoleRoute("answer", "结构化解析模型");
  const vision = visionRoute();
  const image = imageRoute();
  return [
    {
      key: "text",
      icon: "fa-file-lines",
      title: "普通文本题",
      desc: "纯文字题目，不涉及图片",
      configHint: "普通文本题只需要文本推理与结构化解析两个阶段。",
      stages: ["reasoning", "answer"],
      routes: [reasoning, answer]
    },
    {
      key: "vision_calc",
      icon: "fa-image",
      title: "有图计算题",
      desc: "含图片/表格，需要读图",
      configHint: "有图计算题会先读图，再进行文本推理与结构化解析。",
      stages: ["reasoning", "answer", "vision"],
      routes: [reasoning, answer, vision]
    },
    {
      key: "drawing",
      icon: "fa-pen-ruler",
      title: "作图题",
      desc: "需要生成专业图形",
      configHint: "作图题会生成结构化解析，并在规则绘图失败时调用生图模型兜底。",
      stages: ["reasoning", "answer", "image"],
      routes: [reasoning, answer, image]
    },
    {
      key: "vision_drawing",
      icon: "fa-icons",
      title: "有图且需作图题",
      desc: "含图片并需要生成图形",
      configHint: "该题型同时启用读图、文本解析与生图兜底，是最完整的模型组合。",
      stages: ["reasoning", "answer", "vision", "image"],
      routes: [reasoning, answer, vision, image]
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
  updateProviderKeyHint();
  if (thinkingSelect) thinkingSelect.value = cfg.thinking_mode || "auto";
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

function updateProviderKeyHint() {
  const cfg = currentProviderConfig();
  const input = $("providerKeyInput");
  const hint = $("providerKeyHint");
  if (!hint) return;
  if (input?.value.trim()) {
    hint.innerHTML = '<i class="fas fa-info-circle"></i>本次测试会优先使用新输入的 API Key；创建生产任务前建议保存为本机 Key。';
    return;
  }
  hint.innerHTML = cfg.api_key_set
    ? '<i class="fas fa-info-circle"></i>默认使用本机已保存的 API Key；也可以输入新 Key 后重新测试。'
    : '<i class="fas fa-info-circle"></i>API Key 仅保存在当前平台服务主机，不会进入交付包。';
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
  return ["auto", "enabled", "disabled"].includes(value) ? value : "auto";
}

function displayThinkingMode(value) {
  if (value === "enabled") return "开启 thinking";
  if (value === "disabled") return "关闭 thinking";
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

async function testProvider() {
  $("providerResult").textContent = "测试中...";
  setVisual("providerVisualResult", "正在测试模型连接", "平台正在确认模型能否返回固定格式结果。", "info");
  const requestedProvider = $("providerSelect").value;
  const requestedModel = requireSelectedModel();
  try {
    const data = await api("/api/provider-test", {
      method: "POST",
      body: JSON.stringify({
        provider: requestedProvider,
        model: requestedModel,
        thinking_mode: selectedThinkingMode(),
        api_key: $("providerKeyInput")?.value.trim() || undefined
      })
    });
    rememberModelConnectionTest(requestedProvider, requestedModel, true);
    rememberModelConnectionTest(data.provider, data.model, true);
    $("providerResult").textContent = pretty(data);
    setText("providerSummary", `${data.provider} 已通过测试`);
    setVisual("providerVisualResult", "连接成功！模型响应正常", `${displayProviderName(data.provider)} / ${data.model} / ${displayThinkingMode(data.thinking_mode || selectedThinkingMode())}`, "ok");
  } catch (err) {
    const message = String(err).replace(/^Error:\s*/, "");
    rememberModelConnectionTest(requestedProvider, requestedModel, false, message);
    const advice = providerErrorAdvice(message);
    $("providerResult").textContent = `测试失败：${message}`;
    setVisual("providerVisualResult", advice.title, advice.body, "error");
  } finally {
    updateModelRoleCards();
    renderQuestionTypeModelCards();
  }
}

async function saveProviderKeys() {
  $("providerResult").textContent = "保存中...";
  setVisual("providerVisualResult", "正在保存 API Key", "Key 只保存在当前平台服务主机的本地环境文件，不会进入交付包。", "info");
  try {
    const keys = {};
    const envKey = providerEnvKey($("providerSelect").value);
    const providerKey = $("providerKeyInput").value.trim();
    if (envKey && providerKey) keys[envKey] = providerKey;
    if (!Object.keys(keys).length) throw new Error("没有输入需要保存的 API Key");
    const data = await api("/api/providers/local-keys", {
      method: "POST",
      body: JSON.stringify({ keys })
    });
    $("providerKeyInput").value = "";
    $("providerResult").textContent = pretty(data);
    setVisual("providerVisualResult", "API Key 已保存", "后续生产任务默认使用本机保存的 Key。", "ok");
    await refresh();
  } catch (err) {
    const message = String(err).replace(/^Error:\s*/, "");
    $("providerResult").textContent = `保存失败：${message}`;
    setVisual("providerVisualResult", "API Key 保存失败", message, "error");
  }
}

async function createTask() {
  $("taskResult").textContent = "创建中...";
  setVisual("taskVisualResult", "正在创建真题项目", "平台会检查所选教材是否已有可复用索引。", "info");
  try {
    const examPath = $("examSelect").value || $("examPath").value.trim();
    if (!examPath) throw new Error("请先选择或上传一个真题 DOCX");
    await requirePreparedTextbookIndex();
    const envKey = providerEnvKey($("providerSelect").value);
    const providerKey = $("providerKeyInput")?.value.trim() || "";
    if (envKey && providerKey) {
      await api("/api/providers/local-keys", {
        method: "POST",
        body: JSON.stringify({ keys: { [envKey]: providerKey } })
      });
      providerConfigs = await api("/api/providers");
      $("providerKeyInput").value = "";
      updateProviderKeyHint();
    }
    const data = await api("/api/tasks", {
      method: "POST",
      body: JSON.stringify({
        exam_path: examPath,
        textbooks_dir: $("textbooksDir").value.trim() || libraryFiles.textbooks_root,
        selected_textbooks: selectedTextbooks(),
        textbook_display_names: selectedTextbookDisplayNames(),
        provider: $("providerSelect").value,
        model: requireSelectedModel(),
        reasoning_provider: $("reasoningProviderSelect")?.value || $("providerSelect").value,
        reasoning_model: selectedTextRoleModel("reasoning") || requireSelectedModel(),
        answer_provider: $("answerProviderSelect")?.value || $("providerSelect").value,
        answer_model: selectedTextRoleModel("answer") || requireSelectedModel(),
        vision_provider: $("visionProviderSelect")?.value || "",
        vision_model: selectedVisionModel(),
        image_provider: $("imageProviderSelect")?.value || "",
        image_model: selectedImageModel(),
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
  if (!tasks || !tasks.length) {
    list.textContent = "暂无任务";
    return;
  }
  for (const task of tasks) {
    const row = document.createElement("button");
    row.type = "button";
    row.className = "task-row";
    row.classList.toggle("selected", task.task_id === activeTaskId);
    const title = document.createElement("strong");
    title.textContent = shortName(task.exam_path);
    const meta = document.createElement("span");
    meta.textContent = `${statusLabel(task.status)} · ${task.current_stage || "待开始"} · ${task.updated_at || ""}`;
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
  const progress = task.current_progress || null;
  const current = effectiveCurrentStage(task, (task.pipeline_status && task.pipeline_status.stages) || []);
  if (task.status === "completed" || current === "completed") return 100;
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
  if (task?.status === "paused") return 1;
  if (normalized === "running") return 1;
  if (normalized === "failed") return 2;
  if (normalized === "queued") return 3;
  if (normalized === "completed") return 4;
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
  const counts = {
    total: tasks.length,
    running: tasks.filter((task) => taskFilterStatus(task.status, task.current_stage) === "running").length,
    queued: tasks.filter((task) => taskFilterStatus(task.status, task.current_stage) === "queued").length,
    completed: tasks.filter((task) => taskFilterStatus(task.status, task.current_stage) === "completed").length
  };
  setText("taskStatTotal", counts.total);
  setText("taskStatRunning", counts.running);
  setText("taskStatQueued", counts.queued);
  setText("taskStatCompleted", counts.completed);
}

function renderTaskManager(tasks = latestTasks) {
  const list = $("taskManagerList");
  const empty = $("taskManagerEmpty");
  if (!list) return;
  updateTaskManagerStats(tasks);
  const visible = sortedTasks(tasks.filter((task) => activeTaskFilter === "all" || taskFilterStatus(task.status, task.current_stage) === activeTaskFilter));
  list.innerHTML = "";
  if (empty) empty.classList.toggle("hidden", visible.length > 0);
  for (const [index, task] of visible.entries()) {
    const normalized = taskFilterStatus(task.status, task.current_stage);
    const actionStatus = task.status === "paused" ? "paused" : normalized;
    const reviewPending = isActionRequiredTask(task);
    const approvalText = isExamStructureReviewTask(task) ? "需确认 · 1条信息" : "需审批 · 1条信息";
    const meta = taskStatusMeta(task.status, task.current_stage);
    const progress = taskProgressSummary(task);
    const percent = progress.percent;
    const title = shortName(task.exam_path);
    const stageText = progress.label;
    const taskId = task.task_id || "";
    const item = document.createElement("article");
    item.className = `task-manager-item task-manager-${normalized}`;
    item.dataset.status = normalized;
    item.innerHTML = `
      <div class="task-manager-main">
        <div class="task-manager-icon task-color-${index % 6}"><i class="fas fa-file-alt"></i></div>
        <div class="task-manager-copy">
          <h3>${escapeHtml(title)}</h3>
          <p>教材：${escapeHtml(shortName(task.textbooks_dir || "教材库"))}</p>
          <div class="task-manager-meta">
            <span class="task-status-chip status-${normalized}"><i class="${meta.icon}"></i>${escapeHtml(meta.label)}</span>
            <button class="task-id-copy" type="button" data-action="copy-task-id" data-task-id="${escapeHtml(taskId)}" title="复制完整ID"><i class="fas fa-hashtag"></i><strong>${escapeHtml(compactTaskId(taskId).replace(/^#/, ""))}</strong><i class="far fa-copy task-id-copy-icon"></i></button>
            <span><i class="far fa-clock"></i>${escapeHtml(task.updated_at || "暂无时间")}</span>
            <span><i class="fas fa-hourglass-half"></i>运行 ${escapeHtml(taskDurationText(task))}</span>
            <span><i class="fas fa-layer-group"></i>${escapeHtml(progress.meta || stageText)}</span>
          </div>
        </div>
      </div>
      <div class="task-manager-side">
        ${reviewPending ? `<span class="task-approval-badge"><i class="fas fa-bell"></i>${approvalText}</span>` : ""}
        <div class="task-manager-actions">${taskManagerActions(actionStatus, reviewPending)}</div>
      </div>
      <div class="task-manager-progress">
        <div>
          <span>${normalized === "completed" ? "完成进度" : normalized === "queued" ? "等待执行" : "当前进度"}</span>
          <strong>${percent}%</strong>
        </div>
        <div class="manager-progress-track"><span style="width: ${percent}%"></span></div>
        <p>${escapeHtml(normalized === "queued" ? "等待开始" : stageText)}</p>
      </div>
    `;
    item.addEventListener("click", (event) => {
      const button = event.target.closest("button");
      if (button) {
        handleTaskManagerAction(task, button.dataset.action || "detail", button);
        return;
      }
      if (normalized === "completed") openTaskResult(task);
      else openTaskDetail(task);
    });
    list.appendChild(item);
  }
}

function formatLogTime(value) {
  const text = String(value || "");
  return text.includes(" ") ? text.split(" ").slice(1).join(" ") : text || "-";
}

function renderSystemStatus(data) {
  const host = data?.host || {};
  const counts = data?.tasks?.counts || {};
  const logs = data?.runtime_logs || [];
  const events = data?.task_events || [];
  const issueCount = Number(counts.failed || 0) + Number(counts.cancelled || 0);

  setText("systemHostName", host.name || "当前服务电脑");
  setText("systemPid", host.pid ? `PID ${host.pid}` : "-");
  setText("systemTime", data?.time || "-");
  setText("systemIssueCount", issueCount);
  setText(
    "systemMonitorSubtitle",
    host.access_host
      ? `当前监控地址：${host.access_host}，展示的是这台服务电脑的真实运行记录`
      : "显示当前打开服务所在电脑的运行状态"
  );

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
    loadLanAccessInfo().catch(() => null)
  ]);
  renderSystemStatus(data);
  return data;
}

let latestLanAccessInfo = null;

async function loadLanAccessInfo() {
  const data = await api("/api/lan/access");
  latestLanAccessInfo = data;
  const firstUrl = data?.urls?.[0] || "";
  setText("lanAccessUrl", firstUrl || "未检测到局域网地址");
  setText("lanAccessUsername", data?.username || "monitor");
  setText("lanAccessPassword", data?.password || "已启用密码保护");
  setText(
    "lanAccessHint",
    firstUrl
      ? "同一局域网内的电脑可通过下方地址查看运行状态和日志。"
      : "未检测到可用的局域网 IPv4 地址，请检查网络连接。"
  );
  $("lanAccessPasswordRow")?.classList.toggle("remote-secret-hidden", !data?.password);
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

function taskManagerActions(status, reviewPending = false) {
  if (reviewPending) {
    return '<button type="button" class="task-card-button red-action" data-action="detail"><i class="fas fa-bell"></i>去处理</button>';
  }
  const actions = {
    running: [
      ["detail", "blue-action", "fas fa-eye", "查看详情"],
      ["pause", "yellow-action", "fas fa-pause", "暂停"],
      ["cancel", "red-action", "fas fa-times", "取消"]
    ],
    queued: [
      ["move-up", "gray-action", "fas fa-arrow-up", "上移"],
      ["log", "gray-action", "fas fa-file-alt", "日志"],
      ["cancel", "red-action", "fas fa-times", "取消"]
    ],
    completed: [
      ["result", "blue-action", "fas fa-eye", "查看结果"],
      ["download", "green-action", "fas fa-download", "下载"],
      ["delete", "gray-action", "fas fa-trash", "删除"]
    ],
    failed: [
      ["detail", "blue-action", "fas fa-eye", "查看详情"],
      ["log", "gray-action", "fas fa-file-alt", "日志"],
      ["delete", "red-action", "fas fa-trash", "删除"]
    ],
    paused: [
      ["detail", "blue-action", "fas fa-eye", "查看详情"],
      ["resume", "green-action", "fas fa-play", "继续"],
      ["cancel", "red-action", "fas fa-times", "取消"]
    ]
  };
  return (actions[status] || actions.queued)
    .map(([action, color, icon, label]) => `<button type="button" class="task-card-button ${color}" data-action="${action}"><i class="${icon}"></i>${label}</button>`)
    .join("");
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
  if (["pause", "resume", "cancel", "move-up"].includes(action)) {
    await controlTask(task.task_id, action);
    return;
  }
  if (action === "delete") {
    await deleteTaskFromManager(task);
  }
}

async function controlTask(taskId, action) {
  try {
    const data = await api(`/api/tasks/${encodeURIComponent(taskId)}/control`, {
      method: "POST",
      body: JSON.stringify({ action })
    });
    if (data.message) window.alert(data.message);
    await loadTasks();
  } catch (err) {
    window.alert(String(err).replace(/^Error:\s*/, ""));
  }
}

async function deleteTaskFromManager(task) {
  if (!window.confirm(`确定删除任务“${shortName(task.exam_path)}”及其输出文件吗？`)) return;
  try {
    const data = await api(`/api/tasks/${encodeURIComponent(task.task_id)}/delete`, {
      method: "POST",
      body: JSON.stringify({})
    });
    if (data.message) window.alert(data.message);
    if (activeTaskId === task.task_id) activeTaskId = "";
    await loadTasks();
  } catch (err) {
    window.alert(String(err).replace(/^Error:\s*/, ""));
  }
}

function filterTasks(filter) {
  activeTaskFilter = filter;
  document.querySelectorAll(".task-filter-btn").forEach((button) => {
    button.classList.toggle("active", button.dataset.filter === filter);
  });
  renderTaskManager();
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
    const preferred = files.find((file) => /\.zip$/i.test(file.name || ""))
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
    window.alert(String(err).replace(/^Error:\s*/, ""));
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
    const onBodyChange = (event) => {
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
          window.alert("请先确认每道题、小问和二级要求的分值；分值会决定后续解析复杂度。");
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
  if (!modal) return Promise.resolve(window.confirm(request.message || "是否允许审查问题通过？"));
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
  if (status === "completed") {
    setVisual("runVisualResult", "任务已完成", "可以进入第 5 步做最终验收和导出交付包。", "ok");
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
      if (data.task && ["completed", "failed"].includes(data.task.status)) {
        clearInterval(taskPollTimer);
        taskPollTimer = null;
        await loadTasks();
        await taskFiles();
        if (data.task.status === "failed") await loadTaskDiagnostics(taskId);
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
      <small>${escapeHtml(entry.description)} · ${formatBytes(entry.file.size || 0)}</small>
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
    hint.innerHTML = `<strong>输出文件已读取</strong><p>当前仅展示 ${visibleFiles.length} 个最终文件：最终解析 Word、解析 PDF、模型调用汇总、题目依据排查、审查报告、作图题全流程图片。其他技术文件只在导出交付包中保留。</p>`;
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
    const button = document.createElement("button");
    button.type = "button";
    button.className = "result-question-item";
    button.classList.toggle("active", question.question_id === activeResultQuestionId);
    button.innerHTML = `
      <span>
        <strong>第 ${escapeHtml(question.number || question.index)} 题</strong>
        <small>${escapeHtml((question.stem || "").slice(0, 18))}${(question.stem || "").length > 18 ? "..." : ""}</small>
      </span>
      <em>${escapeHtml(question.type || "题目")}</em>
    `;
    button.addEventListener("click", () => {
      activeResultQuestionId = question.question_id;
      renderTaskResultView(resultViewData);
    });
    list.appendChild(button);
  }
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
  const hideTopAnswer = shouldHideTopAnswer(question);
  const isTermExplanation = isTermExplanationQuestion(question);
  const analysisTitle = isShortAnswerQuestion(question) ? "答案" : "解析";
  const directAnswerHtml = `<section class="answer-section"><h4>答案</h4><p>${escapeHtml(question.answer_summary || question.answer || "暂无答案")}</p></section>`;
  detail.innerHTML = `
    <div class="question-detail-header">
      <h3>第 ${escapeHtml(question.number || question.index)} 题：${escapeHtml(question.type || "题目")}</h3>
      ${question.score ? `<span>分值：${escapeHtml(question.score)} 分</span>` : ""}
    </div>
    <section class="original-question-block">
      <h4>题目</h4>
      <p>${escapeHtml(question.stem || "暂无原题内容")}</p>
    </section>
    ${hideTopAnswer ? "" : `<section class="answer-section">
      <h4>答案</h4>
      <p>${escapeHtml(question.answer_summary || question.answer || "暂无答案")}</p>
    </section>`}
    <section class="knowledge-section">
      <h4><i class="fas fa-lightbulb"></i>考查知识点</h4>
      ${renderTagList(question.knowledge_points || question.key_terms, "未提取到知识点")}
    </section>
    <section class="evidence-section">
      <h4><i class="fas fa-book-open"></i>教材引用</h4>
      <p>${escapeHtml(evidence?.text || (question.evidence_ids || []).join("、") || "暂无教材引用")}</p>
    </section>
    ${isTermExplanation ? directAnswerHtml : `<section class="analysis-section">
      <h4>${analysisTitle}</h4>
      <p>${escapeHtml(analysis?.text || "暂无解析内容")}</p>
    </section>`}
    ${!isTermExplanation && isCalculationQuestion(question) && solution?.text ? `<section class="analysis-section"><h4>答案</h4><p>${escapeHtml(solution.text)}</p></section>` : ""}
    ${!isTermExplanation && optionAnalysis?.text ? `<section class="analysis-section"><h4>选项分析</h4><p>${escapeHtml(optionAnalysis.text)}</p></section>` : ""}
    ${!isTermExplanation && tips?.text ? `<section class="analysis-section"><h4>易错点及注意事项</h4><p>${escapeHtml(tips.text)}</p></section>` : ""}
    ${!isTermExplanation && (question.formulas || []).length ? `<section class="formula-section"><h4>相关公式</h4>${(question.formulas || []).slice(0, 8).map((formula) => `<div><code>${escapeHtml(formula.latex || "")}</code><span>${escapeHtml(formula.source_note || "")}</span></div>`).join("")}</section>` : ""}
    ${issueRows.length ? `<section class="quality-inline-section"><h4>质量提示</h4>${issueRows.map((issue) => `<p class="${issue.severity === "warning" ? "warn" : "issue"}">${escapeHtml(issue.message || "")}</p>`).join("")}</section>` : ""}
  `;
}

function renderTaskResultView(data) {
  resultViewData = data;
  const questions = data?.questions || [];
  if (!activeResultQuestionId || !questions.some((q) => q.question_id === activeResultQuestionId)) {
    activeResultQuestionId = questions[0]?.question_id || "";
  }
  const metrics = data?.metrics || {};
  setText("metricQuestionCount", metrics.question_count ?? "--");
  setText("metricCoveredCount", metrics.covered_count ?? metrics.answered_count ?? "--");
  setText("metricReviewCount", Number(metrics.issue_count || 0) + Number(metrics.warning_count || 0));
  const title = $("page-result")?.querySelector(".page-title h2");
  const subtitle = $("page-result")?.querySelector(".page-title p");
  if (title) title.textContent = `${shortName(data?.task?.exam_path || "解析结果")} - 解析结果`;
  if (subtitle) subtitle.textContent = `共解析 ${metrics.question_count || 0} 道题目`;
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
    setVisual(
      "runVisualResult",
      data.ok ? "最终验收通过" : "最终验收未通过",
      data.ok ? "可以导出交付包。" : `发现 ${(data.issues || []).length} 个问题，请先处理后再导出。`,
      data.ok ? "ok" : "error"
    );
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
    let data = await api(`/api/tasks/${encodeURIComponent(taskId)}/delivery-package`, {
      method: "POST",
      body: JSON.stringify({})
    });
    if (!data.ok && (data.status === "review_ack_required" || data.status === "review_decision_required")) {
      const ack = data.review_acknowledgement || {};
      const message = [
        "当前任务存在待复核或质量审查项。",
        `待复核题目：${ack.pending_question_count || 0} 题`,
        `审查文档题目：${ack.review_question_count || 0} 题`,
        "",
        "点击“确定”：允许使用待复核前的模型解析候选版生成交付包。",
        "点击“取消”：不允许使用候选解析，仍导出当前待复核占位版交付包。",
        "两种方式都会保留待复核审查文件，便于后续排查。"
      ].join("\n");
      setVisual("runVisualResult", "需要用户评估", "存在待复核内容，请决定导出候选解析版或待复核占位版。", "warn");
      const allowed = window.confirm(message);
      data = await api(`/api/tasks/${encodeURIComponent(taskId)}/delivery-package`, {
        method: "POST",
        body: JSON.stringify(
          allowed
            ? { review_policy: "use_candidate", allow_review_acknowledgement: true }
            : { review_policy: "keep_pending" }
        )
      });
    }
    $("runResult").textContent = pretty(data);
    const policyText = data.review_policy === "use_candidate" ? "已按候选解析版导出，并保留待复核审查文件。" : data.review_policy === "keep_pending" ? "已按待复核占位版导出，并保留待复核审查文件。" : "请在文件列表中下载交付包。";
    setVisual("runVisualResult", data.ok ? "交付包已导出" : "交付包导出未通过", data.ok ? policyText : "请先处理验收问题。", data.ok ? "ok" : "error");
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
        <p><strong>答案</strong><span>${escapeHtml(row.answer || "未生成答案")}</span></p>
        <p><strong>教材依据状态</strong><span>${escapeHtml(evidenceStatus)}</span></p>
      </div>
      <p><strong>原题</strong>${escapeHtml((row.stem || "未读取到原题内容").slice(0, 800))}</p>
      <div class="review-notes">
        <strong>审查提示</strong>
        ${notes ? `<ul>${notes}</ul>` : "<p>未发现需要人工处理的问题。</p>"}
      </div>
    `;
    item.appendChild(body);
    list.appendChild(item);
  }
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
$("testProviderBtn").addEventListener("click", testProvider);
$("saveProviderKeysBtn").addEventListener("click", saveProviderKeys);
$("prepareTextbookIndexBtn").addEventListener("click", prepareTextbookIndex);
$("saveSharedLibraryBtn")?.addEventListener("click", () => {
  saveSharedLibrarySettings().catch((err) => setVisual("libraryVisualResult", "教材库连接失败", String(err).replace(/^Error:\s*/, ""), "error"));
});
$("refreshSharedLibraryBtn")?.addEventListener("click", () => refreshSharedLibraryCatalog());
$("publishSharedLibraryBtn")?.addEventListener("click", () => {
  publishSharedLibrary().catch((err) => setVisual("libraryVisualResult", "共享教材发布失败", String(err).replace(/^Error:\s*/, ""), "error"));
});
$("createTaskBtn").addEventListener("click", createTask);
$("runTaskBtn").addEventListener("click", () => runTask(false));
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
$("refreshSystemBtn")?.addEventListener("click", loadSystemStatus);
$("copyLanAccessBtn")?.addEventListener("click", () => copyLanAccessInfo().catch(() => {}));
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
$("providerSelect").addEventListener("change", () => {
  $("providerKeyInput").value = "";
  updateModelControls();
  updateProviderSummary(providerConfigs);
});
$("modelSelect").addEventListener("change", () => {
  updateTextRoleModelControls();
  syncVisionModelFromAnswerModel();
});
$("modelInput").addEventListener("input", () => {
  updateTextRoleModelControls();
  syncVisionModelFromAnswerModel();
});
for (const roleKey of Object.keys(textModelRoles)) {
  const role = textModelRoles[roleKey];
  $(role.providerId)?.addEventListener("change", () => {
    populateTextRoleModelSelect(roleKey);
    renderQuestionTypeModelCards();
    switchQuestionTypeTab(modelQuestionTypeTab);
  });
  $(role.modelSelectId)?.addEventListener("change", () => {
    updateTextRoleHint(roleKey);
    renderQuestionTypeModelCards();
    switchQuestionTypeTab(modelQuestionTypeTab);
  });
  $(role.modelInputId)?.addEventListener("input", () => {
    updateTextRoleHint(roleKey);
    renderQuestionTypeModelCards();
    switchQuestionTypeTab(modelQuestionTypeTab);
  });
}
$("visionProviderSelect").addEventListener("change", () => {
  populateVisionModelSelect();
  updateModelCapabilityRisk();
  renderQuestionTypeModelCards();
  switchQuestionTypeTab(modelQuestionTypeTab);
});
$("visionModelSelect").addEventListener("change", () => {
  updateCapabilityModelHints();
  updateModelCapabilityRisk();
  renderQuestionTypeModelCards();
  switchQuestionTypeTab(modelQuestionTypeTab);
});
$("visionModelInput").addEventListener("input", () => {
  updateCapabilityModelHints();
  updateModelCapabilityRisk();
  renderQuestionTypeModelCards();
  switchQuestionTypeTab(modelQuestionTypeTab);
});
$("imageProviderSelect").addEventListener("change", () => {
  populateImageModelControls();
  updateModelCapabilityRisk();
  renderQuestionTypeModelCards();
  switchQuestionTypeTab(modelQuestionTypeTab);
});
$("imageModelSelect")?.addEventListener("change", () => {
  updateCapabilityModelHints();
  updateModelCapabilityRisk();
  renderQuestionTypeModelCards();
  switchQuestionTypeTab(modelQuestionTypeTab);
});
$("imageModelInput").addEventListener("input", () => {
  updateCapabilityModelHints();
  updateModelCapabilityRisk();
  renderQuestionTypeModelCards();
  switchQuestionTypeTab(modelQuestionTypeTab);
});
$("providerKeyInput").addEventListener("input", updateProviderKeyHint);
document.addEventListener("visibilitychange", () => {
  if (!document.hidden && currentPage === "tasks") {
    loadTasks({ silent: true, includeLiveDetails: true }).catch(() => {});
  }
});
$("toggleProviderKeyBtn").addEventListener("click", () => {
  const input = $("providerKeyInput");
  const icon = $("toggleProviderKeyBtn")?.querySelector("i");
  if (!input) return;
  input.type = input.type === "password" ? "text" : "password";
  if (icon) icon.className = input.type === "password" ? "fas fa-eye" : "fas fa-eye-slash";
});
$("practiceForm")?.addEventListener("submit", planPractice);
$("practiceFile")?.addEventListener("change", (event) => {
  readPracticeFiles(event.target.files).catch((error) => {
    $("practiceError").textContent = String(error).replace(/^Error:\s*/, "");
    $("practiceError").classList.remove("hidden");
  }).finally(() => { event.target.value = ""; });
});
$("practiceQuestionText")?.addEventListener("paste", (event) => {
  pastePracticeImages(event).catch((error) => {
    $("practiceError").textContent = String(error).replace(/^Error:\s*/, "");
    $("practiceError").classList.remove("hidden");
  });
});
$("practicePlanBackBtn")?.addEventListener("click", () => {
  $("practicePlanReview")?.classList.add("hidden");
  $("practiceEmpty")?.classList.remove("hidden");
  setText("practiceSourceStatus", "可调整参数");
});
$("practiceSourceBackBtn")?.addEventListener("click", () => {
  closePracticeScopeDrawer();
  $("practiceEmpty")?.classList.remove("hidden");
  setPracticeStage("analyze");
  setPracticeStageDescription("已识别为题目集；可重新调整输入或再次发起解析。");
  setText("practiceSourceStatus", "可调整文件或参数");
});
$("practiceSourceConfirmBtn")?.addEventListener("click", planSelectedSourceQuestions);
$("practiceSourceQuestionList")?.addEventListener("change", updatePracticeStrategySettings);
document.querySelectorAll('input[name="practiceSetStrategy"]').forEach((input) => {
  input.addEventListener("change", updatePracticeStrategySettings);
});
$("practiceTargetedCount")?.addEventListener("input", updatePracticeStrategySettings);
$("practiceVariantsPerQuestion")?.addEventListener("change", updatePracticeStrategySettings);
$("practiceSelectAllSources")?.addEventListener("click", () => {
  document.querySelectorAll('input[name="practiceSourceQuestion"]').forEach((input) => { input.checked = true; });
  updatePracticeStrategySettings();
});
$("practiceClearSources")?.addEventListener("click", () => {
  document.querySelectorAll('input[name="practiceSourceQuestion"]').forEach((input) => { input.checked = false; });
  updatePracticeStrategySettings();
});
$("practicePlanConfirmBtn")?.addEventListener("click", generatePracticeFromPlan);
$("practiceSaveBtn")?.addEventListener("click", () => {
  saveCurrentPractice().catch((error) => {
    $("practiceError").textContent = String(error).replace(/^Error:\s*/, "");
    $("practiceError").classList.remove("hidden");
  });
});
$("practiceEditorSave")?.addEventListener("click", applyPracticeEditor);
$("practiceCopyBtn")?.addEventListener("click", async () => {
  if (!latestPracticeSet) return;
  await navigator.clipboard.writeText(practicePlainText(latestPracticeSet));
  $("practiceCopyBtn").innerHTML = '<i class="fas fa-check"></i>已复制';
  setTimeout(() => { $("practiceCopyBtn").innerHTML = '<i class="fas fa-copy"></i>复制全部'; }, 1600);
});
$("practiceWordBtn")?.addEventListener("click", () => {
  downloadPracticeWord().catch((error) => {
    $("practiceError").textContent = String(error).replace(/^Error:\s*/, "");
    $("practiceError").classList.remove("hidden");
  });
});
$("practiceConfigToggle")?.addEventListener("click", () => togglePracticeConfig());
document.querySelectorAll(".practice-preset").forEach((btn) => {
  btn.addEventListener("click", () => applyPracticePreset(btn.dataset.practicePreset || ""));
});
["practiceCount", "practiceDifficulty"].forEach((id) => $(id)?.addEventListener("change", updatePracticeConfigSummary));
document.querySelectorAll('input[name="practiceQuestionType"]').forEach((input) => {
  input.addEventListener("change", updatePracticeConfigSummary);
});
$("practiceFocus")?.addEventListener("input", updatePracticeConfigSummary);
$("practiceScopeDrawer")?.querySelector("[data-practice-scope-scrim]")?.addEventListener("click", () => {
  // 抽屉遮罩仅用于拦截背景点击，不主动关闭
});
$("practiceBackToPlanBtn")?.addEventListener("click", () => {
  if (!latestPracticePlan) return;
  $("practiceResults")?.classList.add("hidden");
  $("practiceEmpty")?.classList.add("hidden");
  renderPracticePlan(latestPracticePlan);
  setPracticeStage("plan");
  setPracticeStageDescription("回到训练蓝图，可调整后再生成。");
});
$("practiceShowAllHistory")?.addEventListener("click", () => {
  document.querySelector(".practice-history-archive")?.open || (document.querySelector(".practice-history-archive")?.setAttribute("open", ""));
});
// 初始化工作台默认状态
loadPracticeFavorites();
updatePracticeConfigSummary();
setPracticeStage("submit");
setPracticeStageDescription("提交题目后，平台会先判断是单题还是题目集；单题将直接进入蓝图，题目集需要先选择训练范围。");
setPracticeStatusBanner("就绪");
$("practiceWordBtn")?.setAttribute("disabled", "disabled");
$("practiceCopyBtn")?.setAttribute("disabled", "disabled");
$("practiceSaveBtn")?.setAttribute("disabled", "disabled");
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
    ".page-actions",
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
  const update = () => nav.classList.toggle("scrolled", window.scrollY > 12);
  update();
  window.addEventListener("scroll", update, { passive: true });
}

// 初始化
function initSiteEnhancements() {
  autoMarkReveals();
  assignStaggerIndices();
  initRevealObserver();
  initNavScrollBlur();
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
