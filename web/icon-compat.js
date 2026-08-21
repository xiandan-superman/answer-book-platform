(function () {
  "use strict";

  const aliases = {
    "align-left": "align-left",
    "arrows-rotate": "refresh-cw",
    "book-open-reader": "book-open-check",
    "box-archive": "archive",
    "bullseye": "target",
    "check-circle": "circle-check",
    "check-square": "square-check-big",
    "circle-exclamation": "circle-alert",
    "circle-notch": "loader-circle",
    "circle-question": "circle-help",
    "circle-xmark": "circle-x",
    "cloud-arrow-up": "cloud-upload",
    "cloud-upload-alt": "cloud-upload",
    "code-branch": "git-branch",
    "compress-alt": "minimize-2",
    "exclamation-circle": "circle-alert",
    "file-alt": "file-text",
    "file-arrow-up": "file-up",
    "floppy-disk": "save",
    "grid-2": "grid-2x2",
    "circle-info": "info",
    "clone": "copy",
    "diagram-project": "workflow",
    "file-lines": "file-text",
    "file-pdf": "file-text",
    "file-word": "file-text",
    "hashtag": "hash",
    "hourglass-half": "hourglass",
    "info-circle": "info",
    "layer-group": "layers-3",
    "magnifying-glass-chart": "scan-search",
    "minus-circle": "circle-minus",
    "network-wired": "network",
    "octagon-xmark": "x-octagon",
    "paste": "clipboard-paste",
    "pen-ruler": "pencil-ruler",
    "pen-to-square": "square-pen",
    "rotate": "rotate-cw",
    "rotate-left": "undo-2",
    "screwdriver-wrench": "wrench",
    "sort-amount-down": "arrow-down-wide-narrow",
    "share-nodes": "share-2",
    "shield-halved": "shield-half",
    "spinner": "loader-circle",
    "sync-alt": "refresh-cw",
    "table-cells": "table-2",
    "tasks": "list-checks",
    "times": "x",
    "times-circle": "circle-x",
    "triangle-exclamation": "triangle-alert",
    "wand-magic-sparkles": "wand-sparkles",
    "wave-square": "audio-waveform",
    "xmark": "x"
  };

  let renderFrame = 0;

  function iconKey(name) {
    return String(name || "")
      .split("-")
      .filter(Boolean)
      .map((part) => part.charAt(0).toUpperCase() + part.slice(1))
      .join("");
  }

  function supportedIcon(name) {
    const icons = window.lucide?.icons;
    return !icons || Boolean(icons[iconKey(name)]);
  }

  function iconName(node) {
    const source = Array.from(node.classList).find((name) => name.startsWith("fa-") && name !== "fa-spin");
    if (!source) return "";
    const raw = source.slice(3);
    const candidate = aliases[raw] || raw;
    if (supportedIcon(candidate)) return candidate;
    if (raw.startsWith("file-") && supportedIcon("file-text")) return "file-text";
    return supportedIcon("circle-help") ? "circle-help" : candidate;
  }

  function renderSoon() {
    if (renderFrame || !window.lucide?.createIcons) return;
    renderFrame = requestAnimationFrame(() => {
      renderFrame = 0;
      window.lucide.createIcons();
    });
  }

  function convert(root) {
    const nodes = [];
    if (root instanceof Element && root.matches("i.fas, i.far, i.fab")) nodes.push(root);
    root.querySelectorAll?.("i.fas, i.far, i.fab").forEach((node) => nodes.push(node));
    nodes.forEach((node) => {
      const name = iconName(node);
      if (!name) return;
      node.dataset.lucide = name;
      node.setAttribute("aria-hidden", "true");
      node.classList.add("font-icon-compat");
    });
    if (nodes.length) renderSoon();
  }

  function start() {
    convert(document);
    const observer = new MutationObserver((mutations) => {
      mutations.forEach((mutation) => mutation.addedNodes.forEach((node) => {
        if (node instanceof Element) convert(node);
      }));
    });
    observer.observe(document.body, { childList: true, subtree: true });
  }

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", start);
  else start();
}());
