(function attachPlatformUpdateNotes(root, factory) {
  const api = factory();
  if (typeof module === "object" && module.exports) module.exports = api;
  else root.PlatformUpdateNotes = api;
})(typeof globalThis !== "undefined" ? globalThis : this, function createPlatformUpdateNotes() {
  "use strict";

  function cleanMarkdownText(value) {
    return String(value || "")
      .replace(/!\[([^\]]*)\]\([^)]*\)/g, "$1")
      .replace(/\[([^\]]+)\]\([^)]*\)/g, "$1")
      .replace(/[`*_~]/g, "")
      .replace(/<[^>]+>/g, "")
      .replace(/\s+/g, " ")
      .trim();
  }

  function shorten(value, maxLength) {
    const text = cleanMarkdownText(value).replace(/[；。]+$/, "");
    if (text.length <= maxLength) return text;
    return `${text.slice(0, Math.max(1, maxLength - 1)).trimEnd()}…`;
  }

  function sectionSummary(heading) {
    const text = cleanMarkdownText(heading);
    if (/验证|测试|安全|升级|注意|说明/.test(text)) return { skip: true, text: "" };
    if (/模型|接口|兼容/.test(text)) return { skip: false, text: "优化模型调用兼容性" };
    if (/生题/.test(text) && /稳定|重试|等待/.test(text)) {
      return { skip: false, text: "提升生题等待与重试稳定性" };
    }
    if (/Word/i.test(text) && /下载|公式|导出|文档/.test(text)) {
      return { skip: false, text: "修复 Word 下载和公式显示问题" };
    }
    return { skip: false, text: "" };
  }

  function summarizeReleaseNotes(markdown, options = {}) {
    const maxItems = Math.max(1, Number(options.maxItems) || 4);
    const maxLength = Math.max(24, Number(options.maxLength) || 78);
    const summaries = [];
    const seen = new Set();
    let currentSection = { skip: false, text: "" };
    let sectionHasItem = false;

    const add = (value) => {
      const text = shorten(value, maxLength);
      if (!text || seen.has(text) || summaries.length >= maxItems) return;
      seen.add(text);
      summaries.push(text);
    };

    for (const rawLine of String(markdown || "").split(/\r?\n/)) {
      const line = rawLine.trim();
      const headingMatch = line.match(/^#{3,6}\s+(.+)$/);
      if (headingMatch) {
        currentSection = sectionSummary(headingMatch[1]);
        sectionHasItem = false;
        if (currentSection.text) {
          add(currentSection.text);
          sectionHasItem = true;
        }
        continue;
      }
      if (/^#{1,2}\s+/.test(line) || currentSection.skip || sectionHasItem) continue;
      const bulletMatch = line.match(/^[-*+]\s+(.+)$/);
      if (bulletMatch) {
        add(bulletMatch[1]);
        sectionHasItem = true;
      }
    }

    if (!summaries.length) {
      for (const rawLine of String(markdown || "").split(/\r?\n/)) {
        const bulletMatch = rawLine.trim().match(/^[-*+]\s+(.+)$/);
        if (bulletMatch) add(bulletMatch[1]);
      }
    }
    return summaries;
  }

  function formatReleaseSummary(markdown, options = {}) {
    const items = summarizeReleaseNotes(markdown, options);
    if (!items.length) return "本次更新包含体验和稳定性改进。";
    return `本次更新：\n${items.map((item) => `• ${item}`).join("\n")}`;
  }

  return { cleanMarkdownText, summarizeReleaseNotes, formatReleaseSummary };
});
