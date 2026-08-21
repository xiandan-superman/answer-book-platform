(function () {
  "use strict";

  const delay = (milliseconds) => new Promise((resolve) => setTimeout(resolve, milliseconds));

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
    const requestOptions = {
      ...options,
      headers: {
        "Content-Type": "application/json",
        ...(options.headers || {})
      }
    };
    const method = String(requestOptions.method || "GET").toUpperCase();
    let response;
    try {
      response = await fetch(path, requestOptions);
    } catch (error) {
      if (method !== "GET") throw networkError(method);
      try {
        await delay(350);
        response = await fetch(path, requestOptions);
      } catch (retryError) {
        throw networkError(method);
      }
    }
    const data = await parseResponse(response);
    if (!response.ok) {
      const error = new Error(data.error || response.statusText);
      error.code = data.error_code || "request_failed";
      error.suggestedAction = data.suggested_action || "";
      error.supportId = data.support_id || "";
      error.status = response.status;
      error.userMessage = [
        data.error || response.statusText,
        data.suggested_action,
        data.support_id ? `诊断编号：${data.support_id}` : ""
      ].filter(Boolean).join("\n");
      error.message = error.userMessage;
      throw error;
    }
    return data;
  }

  window.PlatformApi = { request };
}());
