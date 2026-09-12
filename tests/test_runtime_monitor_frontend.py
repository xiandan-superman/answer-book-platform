from __future__ import annotations

import unittest
import shutil
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class RuntimeMonitorFrontendTests(unittest.TestCase):
    def test_stem_editor_toggles_without_losing_input(self) -> None:
        node = shutil.which("node")
        if not node:
            self.skipTest("Node.js required for frontend runtime regression")
        script = r'''
const fs=require('fs'), vm=require('vm'), assert=require('assert');
const source=fs.readFileSync('web/app.js','utf8');
const code=source.slice(source.indexOf('function examStemEditorHtml('), source.indexOf('function scoreInputValue('));
let rendered=0, focused=0;
const ctx={practiceMarkdown:v=>v, escapeHtml:v=>v, typesetMath:()=>rendered++};
vm.createContext(ctx); vm.runInContext(code,ctx);
for(const attr of ['data-question-stem','data-subquestion-stem','data-requirement-stem']) {
  const html=ctx.examStemEditorHtml('原题',attr);
  assert(html.includes('<textarea hidden'));
  assert(html.includes(attr));
}
const input={hidden:true,value:'原题',focus:()=>focused++}, preview={hidden:false};
const button={parentElement:{querySelector:s=>s==='textarea'?input:preview},setAttribute:()=>{}};
ctx.toggleExamStemEditor(button);
assert.equal(input.hidden,false); assert.equal(preview.hidden,true); assert.equal(focused,1);
input.value='修改后 $x^2$';
ctx.toggleExamStemEditor(button);
assert.equal(input.hidden,true); assert.equal(preview.hidden,false);
assert.equal(preview.innerHTML,input.value); assert.equal(rendered,1);
ctx.toggleExamStemEditor(button); assert.equal(input.value,'修改后 $x^2$');
'''
        subprocess.run([node, "-e", script], cwd=ROOT, check=True, capture_output=True, text=True)

    def test_execution_detail_runs_without_global_health(self) -> None:
        node = shutil.which("node")
        if not node:
            self.skipTest("Node.js required for frontend runtime regression")
        script = r'''
const fs = require("fs"), vm = require("vm"), assert = require("assert");
const source = fs.readFileSync("web/app.js", "utf8");
const code = source.slice(source.indexOf("function executionStageProgress("), source.indexOf("function renderTaskExecutionDetail("));
const ctx = { activeTaskAnalysisProfile: "evidence_backed", taskProgressPercent: () => 10,
  stageLabel: value => value, visibleStepStage: value => value,
  latestPipelineStage: () => null, formatElapsedSeconds: value => String(value) };
vm.createContext(ctx);
vm.runInContext(code, ctx);
for (const health of [undefined, {}, {health_status:"waiting", current_operation:"等待用户确认"}]) {
  const detail = ctx.buildTaskExecutionDetail({status:"paused", health}, "exam_structure_review", null, []);
  assert.equal(detail.title, "等待确认真题结构");
  assert.equal(detail.stageProgress.label, "等待人工确认");
}
const detail = ctx.buildTaskExecutionDetail({status:"running", health:{current_operation:"正在准备运行时"}}, "extract_exam", null, []);
assert.equal(detail.title, "正在准备运行时");
for (const stage of ["environment", "retrieval", "content_quality", "docx", "final_acceptance"]) {
  assert(ctx.buildTaskExecutionDetail({status:"running"}, stage, null, []).stageProgress);
}
'''
        subprocess.run([node, "-e", script], cwd=ROOT, check=True, capture_output=True, text=True)

    def test_monitor_has_user_facing_health_regions_and_ten_second_refresh(self) -> None:
        html = (ROOT / "web" / "index.html").read_text(encoding="utf-8")
        script = (ROOT / "web" / "app.js").read_text(encoding="utf-8")
        self.assertIn('id="systemHealthOverview"', html)
        self.assertIn('id="systemModelHealthLabel"', html)
        self.assertIn('id="systemProviderRoutes"', html)
        self.assertIn('id="systemRunningTasks"', html)
        self.assertIn("function startSystemMonitorPolling()", script)
        self.assertIn("}, 10000);", script)
        self.assertIn("if (document.hidden) stopSystemMonitorPolling();", script)
        self.assertIn("models.provider_gates", script)
        self.assertIn("cooldown_remaining_seconds", script)
        self.assertIn("运行 ${Number(gate.active || 0)}/${Number(gate.limit || 0)} · 等待 ${Number(gate.waiting || 0)}", script)

    def test_task_manager_maps_health_to_four_user_states(self) -> None:
        script = (ROOT / "web" / "app.js").read_text(encoding="utf-8")
        for label in ("正在处理", "正在等待", "等待时间较长", "任务已中断"):
            self.assertIn(label, script)
