(function initializeUploadFileSelection(root, factory) {
  const api = factory();
  if (typeof module === "object" && module.exports) module.exports = api;
  root.UploadFileSelection = api;
})(typeof globalThis === "object" ? globalThis : this, function createUploadFileSelection() {
  "use strict";

  const MEBIBYTE = 1024 * 1024;
  const DEFAULT_LIMITS = Object.freeze({ maxFiles: 12, maxFileSize: 12 * MEBIBYTE, maxTotalSize: 36 * MEBIBYTE });
  const ALLOWED_MIME_TYPES = new Set([
    "image/png",
    "image/jpeg",
    "image/webp",
    "application/pdf",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "text/plain",
    "text/markdown"
  ]);
  const ALLOWED_EXTENSIONS = new Set([".png", ".jpg", ".jpeg", ".webp", ".pdf", ".docx", ".txt", ".md"]);
  const MIME_TYPES_BY_EXTENSION = Object.freeze({
    ".png": ["image/png"],
    ".jpg": ["image/jpeg"],
    ".jpeg": ["image/jpeg"],
    ".webp": ["image/webp"],
    ".pdf": ["application/pdf"],
    ".docx": ["application/vnd.openxmlformats-officedocument.wordprocessingml.document"],
    ".txt": ["text/plain"],
    ".md": ["text/plain", "text/markdown"]
  });

  function fileExtension(name) {
    const normalized = String(name || "").trim().toLowerCase();
    const dot = normalized.lastIndexOf(".");
    return dot >= 0 ? normalized.slice(dot) : "";
  }

  function isAllowedFileType(file) {
    const mime = String(file?.type || "").trim().toLowerCase();
    const extension = fileExtension(file?.name);
    const extensionAllowed = ALLOWED_EXTENSIONS.has(extension);
    if (!extension) return ALLOWED_MIME_TYPES.has(mime);
    if (!extensionAllowed) return false;
    if (!mime || mime === "application/octet-stream") return true;
    return (MIME_TYPES_BY_EXTENSION[extension] || []).includes(mime);
  }

  function appendReason(reasonsByIndex, index, reason) {
    const reasons = reasonsByIndex.get(index) || [];
    if (!reasons.includes(reason)) reasons.push(reason);
    reasonsByIndex.set(index, reasons);
  }

  function validateSelection(existingFiles, incomingFiles, limits = DEFAULT_LIMITS) {
    const existing = Array.from(existingFiles || []);
    const incoming = Array.from(incomingFiles || []);
    const effective = { ...DEFAULT_LIMITS, ...(limits || {}) };
    const reasonsByIndex = new Map();

    incoming.forEach((file, index) => {
      if (!isAllowedFileType(file)) {
        appendReason(reasonsByIndex, index, "不支持此文件类型（支持 PNG/JPG/WEBP/PDF/DOCX/TXT/MD）");
      }
      if (Number(file?.size || 0) <= 0) {
        appendReason(reasonsByIndex, index, "文件内容为空");
      }
      if (Number(file?.size || 0) > effective.maxFileSize) {
        appendReason(reasonsByIndex, index, "单文件超过 12 MB");
      }
    });

    if (existing.length + incoming.length > effective.maxFiles) {
      incoming.forEach((_file, index) => appendReason(reasonsByIndex, index, "加入后文件数量将超过 12 个"));
    }
    const totalSize = existing.concat(incoming).reduce((sum, file) => sum + Number(file?.size || 0), 0);
    if (totalSize > effective.maxTotalSize) {
      incoming.forEach((_file, index) => appendReason(reasonsByIndex, index, "加入后文件总大小将超过 36 MB"));
    }

    if (reasonsByIndex.size) {
      incoming.forEach((_file, index) => {
        if (!reasonsByIndex.has(index)) {
          appendReason(reasonsByIndex, index, "同批次有文件未通过校验，本文件也未加入");
        }
      });
    }

    return {
      ok: reasonsByIndex.size === 0,
      rejected: incoming.map((file, index) => ({
        name: String(file?.name || `未命名文件 ${index + 1}`),
        reasons: reasonsByIndex.get(index) || []
      })).filter((item) => item.reasons.length),
      totalCount: existing.length + incoming.length,
      totalSize
    };
  }

  function mergePreparedFiles(existingFiles, preparedFiles) {
    const existing = Array.from(existingFiles || []);
    const prepared = Array.from(preparedFiles || []);
    const merged = existing.slice();
    const seenByHash = new Map();
    const seenByDataUrl = new Map();
    existing.forEach((file) => {
      const hash = String(file?.sha256 || "").trim().toLowerCase();
      if (hash && !seenByHash.has(hash)) seenByHash.set(hash, file);
      const dataUrl = String(file?.data_url || "");
      if (dataUrl && !seenByDataUrl.has(dataUrl)) seenByDataUrl.set(dataUrl, file);
    });
    const added = [];
    const duplicates = [];
    prepared.forEach((file) => {
      const hash = String(file?.sha256 || "").trim().toLowerCase();
      const dataUrl = String(file?.data_url || "");
      const duplicateOf = (hash ? seenByHash.get(hash) : null) || (dataUrl ? seenByDataUrl.get(dataUrl) : null);
      if (duplicateOf) {
        duplicates.push({ file, duplicateOf });
        return;
      }
      merged.push(file);
      added.push(file);
      if (hash) seenByHash.set(hash, file);
      if (dataUrl) seenByDataUrl.set(dataUrl, file);
    });
    return { files: merged, added, duplicates };
  }

  function displayName(file, allFiles) {
    const name = String(file?.name || "未命名文件");
    const files = Array.from(allFiles || []);
    const versions = files.filter((item) => String(item?.name || "") === name);
    if (versions.length < 2) return name;
    const version = Math.max(0, versions.indexOf(file)) + 1;
    return `${name} · 同名版本 ${version}/${versions.length}`;
  }

  function formatValidationError(rejected) {
    const lines = Array.from(rejected || []).map((item) => `- ${item.name}：${item.reasons.join("；")}`);
    return ["本次选择未加入；已选文件保持不变。", ...lines].join("\n");
  }

  return {
    DEFAULT_LIMITS,
    isAllowedFileType,
    validateSelection,
    mergePreparedFiles,
    displayName,
    formatValidationError
  };
});
