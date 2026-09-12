import shutil
import subprocess
from pathlib import Path

import pytest


def test_exam_upload_state_and_selection() -> None:
    node = shutil.which("node")
    if not node:
        pytest.skip("Node unavailable")
    script = r'''
const fs=require('fs'), vm=require('vm'), assert=require('assert');
const src=fs.readFileSync('web/app.js','utf8');
const fields={};
function el(id) {return fields[id] ||= {value:'',files:[],disabled:false,innerHTML:'',
 classList:{toggle(){}},setAttribute(){},closest(){return null},
 querySelector(){return {addEventListener:(_event,fn)=>fields.remove=fn}}};}
let finish, uploads=0, reloadFails=false, confirm=true, deleteFails=false;
const ctx={$:el,document:{querySelectorAll:()=>[]},currentExamAnalysisProfile:'question_only',
 taskNavigationVersion:0,selectedTextbooks:()=>[],escapeHtml:String,formatBytes:String,setVisual(){},
 platformConfirm:async()=>confirm,api:async()=>{if(deleteFails)throw Error('in use')},
 loadLibraryFiles:async()=>{if(reloadFails)throw Error('offline');ctx.selectExamFile(el('examPath').value)},
 uploadFileWithProgress:async()=>{uploads++;return new Promise(resolve=>finish=resolve)}};
vm.createContext(ctx);
for(const [start,end] of [
 ['let examUploadState =','async function prepareTextbookIndex('],
 ['async function deleteLibraryFile(','function uploadFileWithProgress('],
 ['function uploadedFilePath(','function setupUploadInput(']
]) vm.runInContext(src.slice(src.indexOf(start),src.indexOf(end)),ctx);
const state=()=>vm.runInContext('examUploadState',ctx);
async function begin(name='new.docx') {el('examUploadInput').files=[{name,size:10}];return ctx.autoUploadExam()}
(async()=>{
 ctx.selectExamFile('/old.docx');
 const pending=begin();assert.equal(el('examPath').value,'');assert(el('createTaskBtn').disabled);
 await ctx.autoUploadExam();assert.equal(uploads,1);
 finish({path:'/new.docx'});await pending;
 assert.equal(el('examPath').value,'/new.docx');assert(!el('createTaskBtn').disabled);
 assert(el('examUploadList').innerHTML.includes('已选中'));assert.equal(el('examUploadInput').value,'');
 confirm=false;await fields.remove();assert.equal(state().path,'/new.docx');assert(!el('createTaskBtn').disabled);
 confirm=true;deleteFails=true;await fields.remove();assert.equal(state().path,'/new.docx');
 deleteFails=false;await fields.remove();assert.equal(state(),null);assert.equal(el('examPath').value,'');assert(el('createTaskBtn').disabled);
 await begin('bad.pdf');assert.equal(state().status,'error');assert.equal(uploads,1);assert(el('createTaskBtn').disabled);
 reloadFails=true;const second=begin();finish('/second.docx');await second;
 assert.equal(el('examPath').value,'/second.docx');assert.equal(state().status,'done');
 reloadFails=false;const third=begin();ctx.selectExamFile('/other.docx');finish('/third.docx');await third;
 assert.equal(el('examPath').value,'/other.docx');
 const fourth=begin();ctx.taskNavigationVersion++;finish('/fourth.docx');await fourth;assert.equal(el('examPath').value,'');
 const fifth=begin();finish({});await fifth;assert.equal(state().status,'error');assert(el('createTaskBtn').disabled);
})().catch(e=>{console.error(e);process.exit(1)});
'''
    subprocess.run([node, "-e", script], cwd=Path(__file__).resolve().parents[1], check=True)


def test_exam_upload_entry_contract() -> None:
    root = Path(__file__).resolve().parents[1]
    html = (root / "web/index.html").read_text()
    js = (root / "web/app.js").read_text()
    assert 'id="uploadExamBtn"' not in html
    setup = js.split("function setupUploadInput(", 1)[1].split("async function uploadLibraryFiles(", 1)[0]
    assert setup.count("autoUploadExam()") == 2
    assert "if (input.disabled) return" in setup
    auto = js.split("async function autoUploadExam(", 1)[1].split("function uploadFileWithProgress(", 1)[0]
    assert "switchExamTab" not in auto
    assert 'selectExamFile(state.path)' in auto
    create = js.split("async function createTask(", 1)[1].split("async function ", 1)[0]
    assert 'examUploadState?.status === "uploading"' in create
    assert 'exam_path: examPath' in create
