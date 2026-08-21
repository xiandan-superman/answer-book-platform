from __future__ import annotations

import pytest

from app import pipeline, task_store
from app.resource_ids import bounded_resource_path, validate_resource_id


@pytest.mark.parametrize(
    "value",
    ["../outside", "..\\outside", ".", "..", "task/child", "task\\child", "task\x00id", "x" * 161],
)
def test_resource_id_rejects_path_syntax(value: str) -> None:
    with pytest.raises(ValueError, match="编号无效"):
        validate_resource_id(value)


@pytest.mark.parametrize("value", ["task-1", "exam_contract_test", "材料科学真题_20260820_120000"])
def test_resource_id_preserves_existing_task_id_forms(value: str) -> None:
    assert validate_resource_id(value) == value


def test_all_exam_task_roots_reject_traversal(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    monkeypatch.setattr(task_store, "TASKS_DIR", tmp_path / "tasks")
    monkeypatch.setattr(pipeline, "OUTPUTS_DIR", tmp_path / "outputs")

    with pytest.raises(ValueError):
        task_store.task_dir("../outside")
    with pytest.raises(ValueError):
        pipeline.stage_dir("../outside")
    with pytest.raises(ValueError):
        pipeline.output_dir("../outside")


def test_resource_path_rejects_existing_symlink_escape(tmp_path) -> None:
    root = tmp_path / "tasks"
    outside = tmp_path / "outside"
    root.mkdir()
    outside.mkdir()
    (root / "task-1").symlink_to(outside, target_is_directory=True)

    with pytest.raises(ValueError, match="编号无效"):
        bounded_resource_path(root, "task-1")
