import shutil
import subprocess
from pathlib import Path

import pytest


def test_protocol_control_is_visible_and_preserves_registered_choice() -> None:
    node = shutil.which("node")
    if not node:
        pytest.skip("Node unavailable")
    script = r'''
const fs=require('fs'),vm=require('vm'),assert=require('assert');
const src=fs.readFileSync('web/app.js','utf8'),fields={select:{value:''},field:{hidden:true}};
let protocols=['responses'];
const ctx={$:id=>fields[id],supportedModelProtocols:()=>protocols,modelRequestProtocol:()=> 'responses',escapeHtml:x=>x,protocolDisplayName:x=>x};
vm.createContext(ctx);vm.runInContext(src.slice(src.indexOf('function populateProtocolControl('),src.indexOf('function selectedProtocolChoice(')),ctx);
ctx.populateProtocolControl('select','field',{},'model');
assert.equal(fields.field.hidden,false);assert.equal(fields.select.disabled,true);assert.equal(fields.select.value,'responses');
protocols=['responses','chat_completions'];
ctx.populateProtocolControl('select','field',{},'model','chat_completions');
assert.equal(fields.select.disabled,false);assert.equal(fields.select.value,'chat_completions');
'''
    subprocess.run([node, "-e", script], cwd=Path(__file__).resolve().parents[1], check=True)


def test_compatibility_settings_keep_legacy_controls_without_duplicate_button() -> None:
    root = Path(__file__).resolve().parents[1]
    html = (root / "web/index.html").read_text()
    assert 'id="modelAdvancedToggleBtn"' not in html
    assert "默认模型（兼容设置）" in html
    assert 'id="providerSelect"' in html
    assert 'id="modelSelect"' in html


def test_new_generation_entry_chooses_models_before_submitting_materials() -> None:
    root = Path(__file__).resolve().parents[1]
    html = (root / "web/index.html").read_text()
    assert 'class="generation-entry-link" type="button" onclick="goToPage(\'practice-models\')"' in html
    assert 'class="generation-entry-link" type="button" onclick="goToPage(\'knowledge-models\')"' in html
    assert html.count('onclick="openPracticeEntry(\'exam\')"') == 1
    assert html.count('onclick="openKnowledgeEntry()"') == 1
    assert "下一步：提交原题" in html
    assert "下一步：提交知识材料" in html


def test_new_exam_default_only_sets_the_two_requested_roles() -> None:
    node = shutil.which("node")
    if not node:
        pytest.skip("Node unavailable")
    script = r'''
const fs=require('fs'),vm=require('vm'),assert=require('assert');
const src=fs.readFileSync('web/app.js','utf8'),calls=[];
const ctx={setExamTextRoleRoute:(role,route)=>calls.push([role,...route]),syncPlatformSelectElement:()=>{},$:()=>({}),
textModelRoles:{reasoning:{},answer:{}},updateModelRoleCards:()=>{},goToPage:p=>assert.equal(p,'env')};
vm.createContext(ctx);vm.runInContext(src.slice(src.indexOf('function startWizard('),src.indexOf('function startQuestionAnalysis(')),ctx);
ctx.startWizard();
assert.deepEqual(calls,[['reasoning','lingsuan_openai','gpt-5.6-sol'],['answer','lingsuan_openai','gpt-5.6-sol']]);
'''
    subprocess.run([node, "-e", script], cwd=Path(__file__).resolve().parents[1], check=True)
