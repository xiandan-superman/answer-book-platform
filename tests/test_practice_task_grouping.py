import json

from app.server import _practice_job_task_row, _practice_task_row
from app import practice_store


def test_practice_task_rows_carry_batch_id_without_name_error():
    history = _practice_task_row(
        {
            "history_id": "practice_20260801230000_abcdefgh",
            "task_kind": "knowledge",
            "title": "相平衡",
            "request": {"practice_batch_id": "batch-demo"},
            "data": {"exercises": [{"stem": "题干"}]},
            "generation_phases": [
                {"operation": "analyze", "label": "范围解析", "status": "completed"},
                {"operation": "plan", "label": "蓝图设计", "status": "completed"},
                {"operation": "generate_from_plan", "label": "题目生成", "status": "completed"},
            ],
        }
    )
    job = _practice_job_task_row(
        {
            "job_id": "generation_20260801230000_abcdefgh",
            "task_kind": "knowledge",
            "practice_batch_id": "batch-demo",
            "operation": "plan",
            "status": "completed",
            "current_stage": "completed",
        }
    )
    assert history["practice_batch_id"] == "batch-demo"
    assert job["practice_batch_id"] == "batch-demo"
    assert history["operation"] == "generate_from_plan"
    assert [phase["label"] for phase in history["generation_phases"]] == ["范围解析", "蓝图设计", "题目生成"]


def test_practice_history_list_preserves_generation_phases(tmp_path, monkeypatch):
    monkeypatch.setattr(practice_store, "PRACTICE_HISTORY_DIR", tmp_path)
    path = tmp_path / "practice_20260802010000_abcdefgh.json"
    phases = [
        {"operation": "analyze", "label": "范围解析", "status": "completed"},
        {"operation": "plan", "label": "蓝图设计", "status": "completed"},
        {"operation": "generate_from_plan", "label": "题目生成", "status": "completed"},
    ]
    path.write_text(json.dumps({
        "history_id": path.stem,
        "title": "热力学",
        "created_at": "2026-08-02T01:00:00+08:00",
        "updated_at": "2026-08-02T01:01:00+08:00",
        "request": {"source_mode": "knowledge", "practice_batch_id": "batch-test"},
        "generation_phases": phases,
        "data": {"exercises": [{"stem": "题干"}]},
    }, ensure_ascii=False), encoding="utf-8")
    listed = practice_store.list_practice_records()
    assert listed[0]["generation_phases"] == phases
    row = _practice_task_row(listed[0])
    assert [phase["label"] for phase in row["generation_phases"]] == ["范围解析", "蓝图设计", "题目生成"]


def test_practice_history_list_excludes_repair_and_candidate_artifacts(tmp_path, monkeypatch):
    monkeypatch.setattr(practice_store, "PRACTICE_HISTORY_DIR", tmp_path)
    history_id = "practice_20260802010000_abcdefgh"
    canonical = {
        "history_id": history_id,
        "title": "规范记录",
        "request": {"source_mode": "knowledge"},
        "data": {
            "quality": {"status": "passed"},
            "exercises": [{"stem": "题干", "knowledge_points": ["知识点"], "verification_note": "条件充分。"}],
        },
    }
    (tmp_path / f"{history_id}.json").write_text(json.dumps(canonical, ensure_ascii=False), encoding="utf-8")
    repaired = {**canonical, "data": {"quality": {"status": "warning"}, "exercises": [{"stem": "修复候选"}]}}
    (tmp_path / f"{history_id}_repaired.json").write_text(json.dumps(repaired, ensure_ascii=False), encoding="utf-8")
    candidate_id = f"{history_id}_semantic_candidate"
    candidate = {**canonical, "history_id": candidate_id}
    (tmp_path / f"{candidate_id}.json").write_text(json.dumps(candidate, ensure_ascii=False), encoding="utf-8")

    listed = practice_store.list_practice_records()

    assert [row["history_id"] for row in listed] == [history_id]
    assert listed[0]["quality"]["status"] == "passed"
