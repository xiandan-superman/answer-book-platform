import shutil
import subprocess
from pathlib import Path

import pytest


def test_score_prefill_and_shortcuts_execute_original_functions() -> None:
    node = shutil.which("node")
    if not node:
        pytest.skip("Node unavailable")
    script = r'''
const fs=require('fs'),vm=require('vm'),assert=require('assert');
const src=fs.readFileSync('web/app.js','utf8');
const ctx={escapeHtml:String,Event:class {constructor(type){this.type=type}}};vm.createContext(ctx);
vm.runInContext(src.slice(src.indexOf('function scoreInputValue('),src.indexOf('const DRAWING_GENERATION_MODES')),ctx);
assert.equal(ctx.scoreInputValue({confirmed_score:'',score:'10',suggested_score:'10'}),'10');
assert.equal(ctx.scoreInputValue({confirmed_score:'5',score:'10'}),'5');
assert.equal(ctx.scoreInputValue({confirmed_score:'',score:'',suggested_score:'20'}),'20');
assert.equal(ctx.scoreInputValue({}),'');assert.equal(ctx.scoreInputValue({score:0}),'0');
assert.equal(ctx.scoreInputValue({score:'garbage'}),'');
for(const attr of ['data-question-score','data-subquestion-score','data-requirement-score']) {
 const html=ctx.scoreFieldHtml({},'分值',attr);assert(html.includes(`${attr} type="number"`));
 assert(html.includes('value=""'));for(const n of [5,10,20])assert(html.includes(`data-score-shortcut="${n}"`));
}
const events=[],input={classList:{remove:x=>assert.equal(x,'invalid')},dispatchEvent:e=>events.push(e.type),focus(){this.focused=true}};
ctx.applyScoreShortcut({dataset:{scoreShortcut:'20'},closest:()=>({querySelector:()=>input})});
assert.equal(input.value,'20');assert.deepEqual(events,['input','change']);assert(input.focused);
'''
    subprocess.run([node, "-e", script], cwd=Path(__file__).resolve().parents[1], check=True)
