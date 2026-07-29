# Nontechnical Production Wizard Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Reframe the existing local Web UI as a nontechnical, step-by-step answer-book production wizard.

**Architecture:** Keep the current HTTP API and task pipeline unchanged. Rewrite the static HTML/CSS layout and adapt `web/app.js` presentation helpers so environment, provider, textbook preparation, task creation, production progress, review, and delivery are shown as clear workflow steps with technical JSON relegated to detail panels.

**Tech Stack:** Static HTML, CSS, vanilla JavaScript, existing Python HTTP server and quality gates.

---

### Task 1: Add UI Structure Regression

**Files:**
- Modify: `scripts/selftest.py`

- [ ] **Step 1: Write the failing test**

Add assertions that `web/index.html` contains these visible workflow labels:

```python
index_html = (ROOT / "web" / "index.html").read_text(encoding="utf-8")
required_ui_labels = ["开工前检查", "准备教材库", "创建真题项目", "生成与复核", "验收与交付"]
result["workflow_ui_labels"] = {label: (label in index_html) for label in required_ui_labels}
if not all(result["workflow_ui_labels"].values()):
    return 1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 scripts/selftest.py`
Expected: FAIL because the current page still uses technical section labels.

- [ ] **Step 3: Implement the minimal UI structure**

Rewrite `web/index.html` into the five workflow sections while preserving the IDs used by `web/app.js`.

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 scripts/selftest.py`
Expected: PASS.

### Task 2: Add Nontechnical Styling and Feedback

**Files:**
- Modify: `web/styles.css`
- Modify: `web/app.js`

- [ ] **Step 1: Update presentation helpers**

Add small helpers in `web/app.js` for status labels, human-readable provider summaries, and task cards while keeping raw JSON in existing `<pre>` detail regions.

- [ ] **Step 2: Restyle page**

Replace the old two-column technical panel look with a guided production layout: readiness strip, numbered step cards, visible status badges, calm forms, and grouped actions.

- [ ] **Step 3: Verify in browser**

Open `http://127.0.0.1:8765`, confirm the page loads, DeepSeek model selector still works, no console warnings/errors appear, and primary workflow labels are visible.

### Task 3: Full Verification

**Files:**
- Existing quality gate scripts.

- [ ] **Step 1: Run quality gates**

Run: `python3 scripts/run_quality_gates.py`
Expected: all checks pass and release zip is regenerated.

- [ ] **Step 2: Smoke-test API-backed UI**

Use the browser to verify provider selection, model dropdown, task list rendering, and page readability.
