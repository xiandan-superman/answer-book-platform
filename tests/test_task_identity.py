import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from app.task_identity import public_task_number, resolve_task_number


def test_all_task_kinds_have_same_stable_number_format():
    for value in ("exam_20260912", "practice_abc", "word_format_abc", "generation_xyz"):
        row = {"task_id": value}
        number = public_task_number(row)
        assert re.fullmatch(r"RW-(?:[0-9A-F]{4}-){3}[0-9A-F]{4}", number)
        assert number == public_task_number({**row, "run_id": "new-run", "status": "failed"})
    assert public_task_number({}) == ""
    assert public_task_number({"task_id": "a"}) != public_task_number({"task_id": "b"})


def test_practice_phases_and_history_share_number():
    rows = [{"task_id": "batch-a", "job_id": "generation_1", "practice_batch_id": "batch-a"},
            {"task_id": "batch-a", "job_id": "generation_2", "practice_batch_id": "batch-a"},
            {"history_id": "practice_1", "request": {"practice_batch_id": "batch-a"}}]
    assert len({public_task_number(row) for row in rows}) == 1


def test_reverse_lookup_includes_jobs_older_than_list_limit(monkeypatch, tmp_path):
    from app import practice_jobs, practice_store, task_store, word_format_tasks

    monkeypatch.setattr(practice_jobs, "PRACTICE_JOB_DIR", tmp_path)
    monkeypatch.setattr(task_store, "list_tasks", lambda: [])
    monkeypatch.setattr(word_format_tasks, "list_word_format_tasks", lambda: [])
    history = {"history_id": "practice_1", "request": {"practice_batch_id": "batch-a"}}
    monkeypatch.setattr(practice_store, "_canonical_history_records", lambda: [(tmp_path / "history", history)])
    for i in range(105):
        row = {"job_id": f"generation_{i:03}", "task_id": "batch-a", "practice_batch_id": "batch-a"}
        (tmp_path / f"generation_{i:03}.json").write_text(json.dumps(row))
    result = resolve_task_number(public_task_number(history).lower())
    assert result["found"]
    assert len(result["matches"]) == 106
    assert any(row["job_id"] == "generation_000" for row in result["matches"])
    with pytest.raises(ValueError):
        resolve_task_number("../../unknown")


def test_frontend_feedback_keeps_real_resource_ids_and_copies_public_number():
    node = shutil.which("node")
    if not node:
        pytest.skip("Node unavailable")
    script = r'''
const fs=require('fs'),vm=require('vm'),assert=require('assert');
const src=fs.readFileSync('web/app.js','utf8');
const extract=(a,b)=>src.slice(src.indexOf(a),src.indexOf(b,src.indexOf(a)));
let copied=''; const ctx={shortTaskModelName:()=>'',copyTextToClipboard:async v=>copied=v};
vm.createContext(ctx);
vm.runInContext(extract('function taskSupportContext(', 'async function submitTaskSupportFeedback('),ctx);
vm.runInContext(extract('async function copyTaskId(', 'async function handleTaskManagerAction('),ctx);
(async()=>{
const task={task_id:'batch',job_id:'generation_1',history_id:'practice_1',is_generation_job:true,is_generation_task:true,public_task_id:'RW-AAAA-BBBB-CCCC-DDDD'};
const payload=ctx.taskSupportContext(task);
assert.equal(payload.task_id,'generation_1');assert.equal(payload.job_id,'generation_1');
assert.equal(payload.history_id,'practice_1');assert.equal(payload.stable_task_id,'batch');
assert.equal(payload.public_task_id,task.public_task_id);
await ctx.copyTaskId(task);assert.equal(copied,task.public_task_id);
})().catch(e=>{console.error(e);process.exitCode=1});
'''
    subprocess.run([node, "-e", script], check=True, cwd=Path(__file__).resolve().parents[1], capture_output=True, text=True)
