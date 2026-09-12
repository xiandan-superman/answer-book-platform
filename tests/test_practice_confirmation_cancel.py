from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from app import practice_jobs
from app.task_read_model import build_practice_runs


@pytest.mark.parametrize("source_mode", ["exam", "knowledge"])
@pytest.mark.parametrize("operation", ["analyze", "plan"])
def test_cancel_waiting_confirmation_preserves_content_and_survives_recovery(tmp_path, monkeypatch, source_mode, operation):
    monkeypatch.setattr(practice_jobs, "PRACTICE_JOB_DIR", tmp_path / "jobs")
    created = practice_jobs.create_practice_job(operation, {"source_mode": source_mode, "practice_batch_id": "test-batch"})
    saved_result = {"source_analysis": {"subject": "材料"}, "blueprint": {"exercise_plan": [{"number": 1}]}}
    practice_jobs.update_practice_job(created["job_id"], status="completed", result=saved_result)
    before = practice_jobs.load_practice_job(created["job_id"])
    row = build_practice_runs([before], [])[0]
    assert row["status"] == "needs_input"
    assert row["capabilities"]["cancel"] is True
    assert row["capabilities"]["view_result"] is True

    assert practice_jobs.cancel_practice_job(created["job_id"])["ok"] is True
    cancelled = practice_jobs.load_practice_job(created["job_id"])
    assert cancelled["payload"] == before["payload"]
    assert cancelled["result"] == saved_result
    assert cancelled["control_epoch"] == before.get("control_epoch", 0) + 1
    # Duplicate clicks are harmless, and stale workers cannot revive the job.
    assert practice_jobs.cancel_practice_job(created["job_id"])["ok"] is True
    practice_jobs.update_practice_job(created["job_id"], status="completed", result={"late": True})
    assert practice_jobs.recover_practice_jobs(fail_interrupted=False) == []
    assert practice_jobs.load_practice_job(created["job_id"]) == cancelled
    refreshed = build_practice_runs(practice_jobs.list_practice_jobs(), [])[0]
    assert refreshed["status"] == "cancelled"
    assert refreshed["duration_text"] == "已取消"
    assert refreshed["capabilities"]["cancel"] is False
    assert len(list((tmp_path / "jobs").glob("*.json"))) == 1


@pytest.mark.parametrize("operation", ["generate_from_plan", "generate_from_contract"])
def test_finished_generation_still_cannot_be_cancelled(tmp_path, monkeypatch, operation):
    monkeypatch.setattr(practice_jobs, "PRACTICE_JOB_DIR", tmp_path / "jobs")
    created = practice_jobs.create_practice_job(operation, {"source_mode": "exam"})
    practice_jobs.update_practice_job(created["job_id"], status="completed", result={"exercises": []})
    before = practice_jobs.load_practice_job(created["job_id"])
    assert practice_jobs.cancel_practice_job(created["job_id"])["ok"] is False
    assert practice_jobs.load_practice_job(created["job_id"]) == before


def test_frontend_confirmation_cancel_action_and_failure_feedback():
    node = shutil.which("node")
    if not node:
        pytest.skip("Node is needed to execute frontend action contracts")
    root = Path(__file__).resolve().parents[1]
    rows = []
    for kind in ("practice", "knowledge"):
        for operation in ("analyze", "plan"):
            rows.extend(build_practice_runs([{
                "job_id": "test-job", "practice_batch_id": "test-batch",
                "task_kind": kind, "operation": operation, "status": "completed",
            }], []))
    script = r"""
const fs = require('fs'), vm = require('vm'), assert = require('assert');
const source = fs.readFileSync('web/app.js', 'utf8');
const extract = (start, end) => source.slice(source.indexOf(start), source.indexOf(end, source.indexOf(start)));
let accepted = true, response = {ok:true}, calls = [], alerts = [], refreshes = 0;
const ctx = {
  practiceErrorNeedsConfiguration: () => false, taskDisplayStatus: t => t.status,
  taskResourceId: t => t.job_id,
  platformConfirm: async options => { assert(options.message.includes('不会删除')); return accepted; },
  api: async (url, options) => { calls.push([url, options]); if(response instanceof Error) throw response; return response; },
  platformAlert: async (...args) => alerts.push(args), loadTasks: async () => {refreshes++;},
};
vm.createContext(ctx);
vm.runInContext(extract('function taskManagerActions(', 'function generationTaskManagerActions('),ctx);
vm.runInContext(extract('async function cancelGenerationJob(', 'async function retryExamTask('),ctx);
(async () => {
  for (const task of ROWS) {
    const html = ctx.taskManagerActions(task, true);
    assert(html.includes('data-action="job-result"'));
    assert(html.includes('data-action="job-cancel"'));
    assert(!html.includes('data-action="delete"'));
    accepted = false; calls = []; await ctx.cancelGenerationJob(task); assert.equal(calls.length,0);
    accepted = true; response = {ok:true}; await ctx.cancelGenerationJob(task);
    assert.equal(calls[0][0], '/api/practice/jobs/test-job/cancel');
    assert.equal(calls[0][1].method, 'POST');
    response = {ok:false, message:'当前出题任务已经结束，不能取消。'};
    await ctx.cancelGenerationJob(task); assert.equal(alerts.at(-1)[0], response.message);
    response = new Error('网络失败'); await ctx.cancelGenerationJob(task);
    assert.equal(alerts.at(-1)[0], '网络失败');
  }
  assert.equal(refreshes, ROWS.length * 2);
})().catch(error => {console.error(error); process.exitCode = 1;});
""".replace("ROWS", json.dumps(rows, ensure_ascii=False))
    subprocess.run([node, "-e", script], cwd=root, check=True, capture_output=True, text=True)
