(function () {
  "use strict";

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
    question_review: "生成存疑审查文档",
    render: "生成 PDF/PNG 复核图",
    acceptance: "验收结果",
    final_acceptance: "最终验收",
    planning: "解析并设计蓝图",
    analyzing: "解析材料范围",
    generating: "生成练习题",
    recovering: "从检查点恢复",
    failed: "执行失败",
    completed: "已完成"
  };

  const statusLabels = {
    completed: "已完成",
    completed_with_issues: "完成待复核",
    failed: "执行失败",
    running: "执行中",
    paused: "已暂停",
    cancelled: "已取消",
    needs_input: "等待你的确认",
    queued: "等待执行",
    pending: "待开始",
    created: "待开始"
  };

  function isReviewDecisionTask(task) {
    if (Object.prototype.hasOwnProperty.call(task || {}, "review_decision_pending")) return Boolean(task.review_decision_pending);
    return task?.status === "paused" && task?.current_stage === "review_decision";
  }

  function isExamStructureReviewTask(task) {
    if (Object.prototype.hasOwnProperty.call(task || {}, "exam_structure_review_pending")) return Boolean(task.exam_structure_review_pending);
    return task?.status === "paused" && task?.current_stage === "exam_structure_review";
  }

  function isActionRequiredTask(task) {
    return task?.status === "needs_input" || isReviewDecisionTask(task) || isExamStructureReviewTask(task);
  }

  function filterStatus(status, currentStage = "") {
    if (status === "completed_with_issues") return "completed_with_issues";
    if (status === "completed" || currentStage === "completed") return "completed";
    if (status === "failed") return "failed";
    if (status === "cancelled") return "cancelled";
    if (status === "needs_input") return "needs_input";
    if (status === "running") return "running";
    if (status === "paused") return "paused";
    return "queued";
  }

  function displayStatus(task = {}) {
    if (isActionRequiredTask(task)) return "needs_input";
    if (task.is_generation_task && task.status === "completed_with_issues" && !task.generation?.partial_success) return "completed";
    return filterStatus(task.status, task.current_stage);
  }

  window.TaskContractUI = {
    stageLabels,
    stageLabel: (stage) => stageLabels[stage] || stage || "待开始",
    statusLabel: (status) => statusLabels[status] || status || "未知",
    filterStatus,
    displayStatus,
    isReviewDecisionTask,
    isExamStructureReviewTask,
    isActionRequiredTask
  };
}());
