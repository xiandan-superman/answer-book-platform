import shutil
import subprocess
from pathlib import Path

import pytest


def test_original_frontend_functions_keep_identity_and_terminal_state() -> None:
    node = shutil.which("node")
    if not node:
        pytest.skip("Node.js unavailable")
    script = r'''
const fs=require('fs'), vm=require('vm'), assert=require('assert');
const source=fs.readFileSync('web/app.js','utf8');
const slice=(start,end)=>source.slice(source.indexOf(start),source.indexOf(end,source.indexOf(start)));
const calls=[];
const ctx={structuredClone, examUploadState:null, practiceSessionVersion:1, latestPracticeSet:{history_id:'A',exercises:[{_edit_version:'a1'}]},
 currentPracticeHistoryId:'A',latestPracticeRequest:{},currentPracticeRevisionCount:0,
 api:async(url,options)=>{calls.push([url,JSON.parse(options.body)]);return {history_id:'A',data:{history_id:'A'},revision_count:1}},
 activePracticeJobId:'A',showPracticeLoadingTaskId:()=>{},rememberPracticeJob:()=>{},practiceJobDelay:()=>{throw Error('unexpected retry')},
};
vm.createContext(ctx);
vm.runInContext(slice('function practiceEditContext(', 'async function regeneratePracticeQuestion('),ctx);
vm.runInContext(slice('async function waitForPracticeJob(', 'async function submitPracticeJob('),ctx);
vm.runInContext(slice('async function regeneratePlanItem(', 'async function generatePlanItemDraft('),ctx);
vm.runInContext(slice('function populatePracticeEditor(', 'function openPracticeEditor('),ctx);
vm.runInContext(slice('async function regenerateSelectedPracticeQuestions(', 'async function undoPracticeChange('),ctx);
vm.runInContext(slice('async function runTask(', 'function summarizeTaskStatus('),ctx);
vm.runInContext(slice('async function taskStatus(', 'async function taskFiles('),ctx);
vm.runInContext(slice('async function createTask(', 'function renderTasks('),ctx);
vm.runInContext(slice('async function applyPracticeEditor(', 'function practiceRegenerationPayload('),ctx);
(async()=>{
 const context=ctx.practiceEditContext(0);
 ctx.latestPracticeSet={history_id:'B',exercises:[{_edit_version:'b1'}]};ctx.currentPracticeHistoryId='B';ctx.practiceSessionVersion++;
 await ctx.saveRegeneratedPracticeExercise(0,{stem:'candidate'},'regenerate',null,null,context);
 assert(calls[0][0].includes('/A/exercise'));assert.equal(calls[0][1].expected_edit_version,'a1');
 assert.equal(ctx.latestPracticeSet.history_id,'B');
 const drafts=new Map();ctx.PRACTICE_EDITOR_DRAFT_PREFIX='draft.';ctx.crypto={randomUUID:()=> 'test-candidate'};
 ctx.practiceExerciseExportId=()=> 'question-1';ctx.localStorage={setItem:(key,value)=>drafts.set(key,value)};
 ctx.retainPracticeRegenerationCandidate(context,{stem:'late candidate',options:[{label:'A',text:'one\ntwo'}]});
 const retained=JSON.parse([...drafts.values()][0]);
 assert.equal(retained.history_id,'A');assert.equal(retained.base_edit_version,'a1');
 assert.equal(retained.values.practiceEditStem,'late candidate');assert.equal(ctx.latestPracticeSet.history_id,'B');
 for(const status of ['failed','cancelled','paused']) {
  let gets=0;ctx.api=async()=>{gets++;return {status,error:'network 连接失败'}};
  await assert.rejects(ctx.waitForPracticeJob('A'),e=>e.practiceJob.status===status);assert.equal(gets,1);
 }
 const fields=new Map();ctx.$=id=>{if(!fields.has(id))fields.set(id,{value:'',classList:{add(){},remove(){}},close(){}});return fields.get(id)};
 ctx.syncPlatformSelectElement=()=>{};
 const item={options:[{label:'A',text:'line1\nline2'},{label:'B',text:'other'}],formulas:[{formula_id:'custom',latex:'x^2',display:false}]};
 ctx.populatePracticeEditor(item);
 assert.deepEqual(JSON.parse(ctx.$('practiceEditOptions').value),item.options);
 assert.deepEqual(JSON.parse(ctx.$('practiceEditFormulas').value),item.formulas);
 ctx.latestPracticeSet={history_id:'B',exercises:[item]};ctx.practiceEditingIndex=0;
 ctx.practiceEditorTargetContext=ctx.practiceEditContext(0);ctx.practiceEditorDraftStale=false;ctx.practiceEditorDraftBaseVersion='';ctx.practiceEditorMergeInProgress=false;
 ctx.clearPracticeEditorDraft=()=>{};ctx.renderPracticeResults=()=>{};ctx.setPracticeStatusBanner=()=>{};ctx.loadPracticeHistory=async()=>{};
 let editorSaved;const originalSave=ctx.saveRegeneratedPracticeExercise;
 ctx.saveRegeneratedPracticeExercise=async(index,exercise)=>editorSaved=exercise;
 await ctx.applyPracticeEditor({preventDefault(){}});
 assert.deepEqual(editorSaved.options,item.options);assert.deepEqual(editorSaved.formulas,item.formulas);
 ctx.practiceEditorDraftBaseVersion='old';ctx.practiceEditorServerVersion='latest';ctx.practiceEditorMergeInProgress=true;
 let savedVersion;ctx.saveRegeneratedPracticeExercise=async(index,exercise,reason,review,updates,context)=>savedVersion=context.version;
 await ctx.applyPracticeEditor({preventDefault(){}});assert.equal(savedVersion,'latest');assert.equal(ctx.practiceEditorDraftBaseVersion,'old');
 ctx.saveRegeneratedPracticeExercise=originalSave;
 let release;ctx.latestPracticePlan={blueprint:{exercise_plan:[{plan_item_id:'A',target_skill:'old'}]}};
 ctx.requestPlanRevisionSpec=async()=>({note:'test'});ctx.api=()=>new Promise(resolve=>release=resolve);
 ctx.platformAlert=()=>{throw Error('must not interrupt new view')};ctx.renderPracticePlan=()=>{throw Error('must not render stale plan')};
 const pending=ctx.regeneratePlanItem(0,{innerHTML:'edit',disabled:false});await new Promise(setImmediate);
 const planB={blueprint:{exercise_plan:[{plan_item_id:'B'}]}};ctx.latestPracticePlan=planB;ctx.practiceSessionVersion++;
 release({plan_item:{plan_item_id:'A',target_skill:'new'}});await pending;
 assert.equal(ctx.latestPracticePlan.blueprint.exercise_plan[0].plan_item_id,'B');
 // Batch continuation must not read the newly opened task for later items.
 ctx.latestPracticeSet={history_id:'A',exercises:[{_edit_version:'a1'},{_edit_version:'a2'}]};ctx.currentPracticeHistoryId='A';
 ctx.selectedPracticeExerciseIndexes=new Set([0,1]);ctx.practiceRegenerationInProgress=false;
 ctx.platformPrompt=async()=>'';ctx.setPracticeRegenerationBusy=()=>{};ctx.setPracticeStatusBanner=()=>{};
 ctx.practiceRegenerationPayload=(index)=>({practice:ctx.latestPracticeSet,index});
 const generated=[],saved=[];
 ctx.regeneratePracticeExercise=async(index,instruction,payload)=>{
   generated.push(payload.practice.history_id);
   ctx.latestPracticeSet={history_id:'B',exercises:[]};ctx.currentPracticeHistoryId='B';ctx.practiceSessionVersion++;
   return {exercise:{stem:'candidate'}};
 };
 ctx.saveRegeneratedPracticeExercise=async(index,exercise,reason,review,updates,context)=>saved.push([context.historyId,context.version]);
 ctx.updatePracticeSelectionActions=()=>{throw Error('must not refresh new selection')};
 await ctx.regenerateSelectedPracticeQuestions({innerHTML:'regenerate'});
 assert.deepEqual(generated,['A','A']);assert.deepEqual(saved,[['A','a1'],['A','a2']]);
 assert.equal(ctx.latestPracticeSet.history_id,'B');
 for (const operation of ['runTask','taskStatus','taskQuality']) {
  for (const fails of [false,true]) {
   ctx.taskNavigationVersion=1;ctx.$('taskIdInput').value='A';ctx.activeTaskId='A';
   ctx.setVisual=()=>{};ctx.setProgress=()=>{};
   let resolve,reject;ctx.api=()=>new Promise((yes,no)=>{resolve=yes;reject=no});
   const pending=ctx[operation]();
   ctx.taskNavigationVersion++;ctx.$('taskIdInput').value='B';ctx.activeTaskId='B';ctx.$('runResult').textContent='B';
   ctx.setVisual=()=>{throw Error('old response changed new view')};
   if(fails)reject(Error('old failure'));else resolve({task:{task_id:'A'}});
   await pending;
   assert.equal(ctx.activeTaskId,'B');assert.equal(ctx.$('runResult').textContent,'B');
  }
 }
 let selected='A',submitted;
 ctx.currentExamAnalysisProfile='question_only';ctx.$('examSelect').value='exam.docx';
 ctx.selectedTextbooks=()=>[];ctx.selectedTextbookNames=()=>[];ctx.selectedTextbookDisplayNames=()=>({});
 ctx.selectedImageProviderConfig=()=>({api_key_set:true});ctx.selectedImageModel=()=>selected;
 ctx.imageOrchestrationMode=()=> 'main_model_tool_loop';ctx.selectedTextRoleModel=()=>selected;
 ctx.selectedRoleProtocol=()=> selected==='A'?'chat_completions':'responses';ctx.selectedRoleThinkingMode=()=> 'auto';
 ctx.textRoleRoute=()=>({provider:'test',model:selected});ctx.selectedVisionModel=()=>selected;ctx.examTaskPreflightRoutes=()=>[{model:selected}];ctx.shortName=x=>x;
 ctx.setVisual=()=>{};ctx.loadTasks=async()=>{};
 ctx.platformConfirm=async()=>{selected='B';return true};
 ctx.preflightTaskModelRoutes=async routes=>{assert.equal(routes[0].model,'A');selected='C';return true};
 ctx.api=async(url,options)=>{submitted=JSON.parse(options.body);return {}};
 await ctx.createTask();
 assert.equal(submitted.model,'A');assert.equal(submitted.answer_model,'A');assert.equal(submitted.reasoning_model,'A');
 assert.equal(submitted.api_protocol,'chat_completions');
})().catch(e=>{console.error(e);process.exitCode=1});
'''
    subprocess.run([node, "-e", script], cwd=Path(__file__).resolve().parents[1], check=True, capture_output=True, text=True)
