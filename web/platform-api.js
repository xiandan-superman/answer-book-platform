(function () {
  "use strict";

  const delay = (milliseconds) => new Promise((resolve) => setTimeout(resolve, milliseconds));

  function requestId() {
    if (globalThis.crypto?.randomUUID) return `R-${globalThis.crypto.randomUUID()}`;
    return `R-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 10)}`;
  }

  function recordNetworkFailure(method, path, correlationId, startedAt) {
    window.SupportTelemetry?.record("request_failed", {
      method,
      path: String(path).split("?", 1)[0],
      request_id: correlationId,
      status: "network_error",
      duration_ms: Math.round(performance.now() - startedAt)
    });
  }

  function networkError(method) {
    return new Error(
      method === "GET"
        ? "无法连接本地服务。请确认平台仍在运行，然后刷新页面重试。"
        : "请求没有送达本地服务。请确认平台仍在运行；原页面内容会保留，可稍后重试。"
    );
  }

  async function parseResponse(response) {
    try {
      return await response.json();
    } catch (error) {
      const invalid = new Error("本地服务返回了无法识别的响应，请刷新页面后重试。");
      invalid.code = "invalid_response";
      invalid.status = response.status;
      throw invalid;
    }
  }

  async function request(path, options = {}) {
    const correlationId = requestId();
    const startedAt = performance.now();
    const requestOptions = {
      ...options,
      headers: {
        "Content-Type": "application/json",
        "X-Request-ID": correlationId,
        ...(options.headers || {})
      }
    };
    const method = String(requestOptions.method || "GET").toUpperCase();
    window.SupportTelemetry?.record("request_started", {
      method,
      path: String(path).split("?", 1)[0],
      request_id: correlationId,
      status: "started"
    });
    let response;
    try {
      response = await fetch(path, requestOptions);
    } catch (error) {
      recordNetworkFailure(method, path, correlationId, startedAt);
      if (method !== "GET") throw networkError(method);
      try {
        await delay(350);
        response = await fetch(path, requestOptions);
      } catch (retryError) {
        recordNetworkFailure(method, path, correlationId, startedAt);
        throw networkError(method);
      }
    }
    const data = await parseResponse(response);
    if (!response.ok) {
      const error = new Error(data.error || response.statusText);
      error.code = data.error_code || "request_failed";
      error.suggestedAction = data.suggested_action || "";
      error.supportId = data.support_id || "";
      error.recoveryAction = data.recovery_action || "";
      error.status = response.status;
      error.issues = Array.isArray(data.issues) ? data.issues.map((item) => String(item)).filter(Boolean) : [];
      const issueDetail = error.issues.length ? `：${error.issues.slice(0, 3).join("；")}` : "";
      error.userMessage = [
        `${data.error || response.statusText}${issueDetail}`,
        data.suggested_action,
        data.support_id ? `诊断编号：${data.support_id}` : ""
      ].filter(Boolean).join("\n");
      error.message = error.userMessage;
      window.SupportTelemetry?.record("request_failed", {
        method, path: String(path).split("?", 1)[0], request_id: correlationId,
        status: response.status, duration_ms: Math.round(performance.now() - startedAt),
        error_code: error.code, support_id: error.supportId,
        message: `${data.error || response.statusText}${issueDetail}`
      });
      throw error;
    }
    window.SupportTelemetry?.record("request_completed", {
      method, path: String(path).split("?", 1)[0], request_id: correlationId,
      status: response.status, duration_ms: Math.round(performance.now() - startedAt)
    });
    return data;
  }

  window.PlatformApi = { request };
}());
