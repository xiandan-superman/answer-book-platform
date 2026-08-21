(function () {
  "use strict";

  const STORAGE_KEY = "answerBook.supportTelemetry.v1";
  const MAX_EVENTS = 240;
  const MAX_STORAGE_CHARS = 180000;
  const sessionId = `S-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 10)}`;
  let events = [];

  function cleanText(value, limit = 300) {
    return String(value || "")
      .replace(/sk-[A-Za-z0-9_-]{8,}/g, "***")
      .replace(/Bearer\s+[^\s,;]+/gi, "Bearer ***")
      .slice(0, limit);
  }

  function loadPrevious() {
    try {
      const parsed = JSON.parse(localStorage.getItem(STORAGE_KEY) || "[]");
      if (Array.isArray(parsed)) events = parsed.slice(-MAX_EVENTS);
    } catch (error) {
      events = [];
    }
  }

  function persist() {
    try {
      let selected = events.slice(-MAX_EVENTS);
      let raw = JSON.stringify(selected);
      while (raw.length > MAX_STORAGE_CHARS && selected.length > 20) {
        selected = selected.slice(Math.ceil(selected.length / 5));
        raw = JSON.stringify(selected);
      }
      localStorage.setItem(STORAGE_KEY, raw);
      events = selected;
    } catch (error) {}
  }

  function record(kind, values = {}) {
    const row = {
      time: new Date().toISOString(),
      kind: cleanText(kind, 60),
      page: cleanText(document.body?.dataset?.activePage || "", 80)
    };
    const allowed = [
      "action", "target", "method", "path", "request_id", "status", "duration_ms",
      "error_code", "support_id", "message", "task_id", "question_id", "exercise_index", "history_id"
    ];
    allowed.forEach((key) => {
      if (values[key] !== undefined && values[key] !== null && values[key] !== "") {
        row[key] = typeof values[key] === "number" ? values[key] : cleanText(values[key], key === "message" ? 1000 : 300);
      }
    });
    if (row.path) row.path = row.path.split("?", 1)[0];
    events.push(row);
    persist();
    return row;
  }

  function targetName(element) {
    if (!(element instanceof Element)) return "";
    const target = element.closest("button, a, [role='button']");
    if (!target) return "";
    return cleanText(
      target.id
        || target.getAttribute("data-action")
        || target.getAttribute("aria-label")
        || target.getAttribute("title")
        || target.textContent,
      160
    );
  }

  function snapshot() {
    return events.slice(-MAX_EVENTS).map((item) => ({ ...item }));
  }

  function selectedText() {
    try {
      return cleanText(window.getSelection()?.toString() || "", 2000);
    } catch (error) {
      return "";
    }
  }

  loadPrevious();
  record("session_started", { action: "page_loaded", path: location.pathname });

  document.addEventListener("click", (event) => {
    const target = targetName(event.target);
    if (target) record("user_action", { action: "click", target });
  }, true);

  window.addEventListener("error", (event) => {
    record("frontend_error", {
      action: "window_error",
      message: event.message || event.error?.message || "脚本错误",
      path: event.filename || location.pathname,
      status: "error"
    });
  });

  window.addEventListener("unhandledrejection", (event) => {
    record("frontend_error", {
      action: "unhandled_rejection",
      message: event.reason?.message || String(event.reason || "Promise rejected"),
      status: "error"
    });
  });

  window.SupportTelemetry = { sessionId, record, snapshot, selectedText };
}());
