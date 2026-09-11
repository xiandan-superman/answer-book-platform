import json
import os

import pytest

from app.output_checkpoints import content_sha256, save_output_checkpoint


def test_checkpoint_preserves_exact_versions_and_cannot_publish(tmp_path):
    candidate = {"question_id": "q", "answer": "original"}
    first = save_output_checkpoint(
        tmp_path, stage="generate", object_id="q", source={"answer": "raw"},
        candidate=candidate, diagnostics={"issues": []},
    )
    original_bytes = first.read_bytes()
    candidate["answer"] = "latest"
    second = save_output_checkpoint(
        tmp_path, stage="generate", object_id="q", source=None,
        candidate=candidate, diagnostics={"issues": ["failed"]},
    )
    assert first != second
    assert first.read_bytes() == original_bytes
    payload = json.loads(second.read_text())
    assert payload["candidate_sha256"] == content_sha256(candidate)
    assert payload["delivery_status"] == "not_evaluated"
    assert json.loads((second.parent / "latest.json").read_text())["snapshot"] == second.name


def test_ids_are_not_paths_and_tasks_stages_are_isolated(tmp_path):
    paths = [save_output_checkpoint(
        tmp_path / task, stage=stage, object_id="../../outside", source={},
        candidate={"a": 1}, diagnostics={},
    ) for task, stage in [("t1", "generation"), ("t1", "repair"), ("t2", "generation")]]
    assert len(set(paths)) == 3
    assert all(p.is_relative_to(tmp_path) for p in paths)
    assert not (tmp_path / "outside").exists()


def test_failed_commit_preserves_previous_pointer(tmp_path, monkeypatch):
    import app.output_checkpoints as checkpoints
    args = dict(stage_dir=tmp_path, stage="generation", object_id="q", source={}, diagnostics={})
    first = save_output_checkpoint(**args, candidate={"version": 1})
    pointer = first.parent / "latest.json"
    previous = pointer.read_bytes()
    real_write = checkpoints.atomic_write_json

    def fail_pointer(path, payload):
        if path.name == "latest.json":
            raise OSError("disk unavailable")
        real_write(path, payload)

    monkeypatch.setattr(checkpoints, "atomic_write_json", fail_pointer)
    with pytest.raises(OSError):
        save_output_checkpoint(**args, candidate={"version": 2})
    assert pointer.read_bytes() == previous
    assert first.exists()


def test_checkpoint_rejects_corrupted_existing_snapshot(tmp_path):
    args = dict(stage_dir=tmp_path, stage="generation", object_id="q", source={}, candidate={}, diagnostics={})
    path = save_output_checkpoint(**args)
    path.write_text('{}')
    with pytest.raises(ValueError, match="integrity"):
        save_output_checkpoint(**args)


def test_checkpoint_returns_long_path_aware_path(tmp_path, monkeypatch):
    import app.output_checkpoints as checkpoints

    observed = []

    def fake_long_path(value):
        observed.append(value)
        return os.path.abspath(str(value))

    monkeypatch.setattr(checkpoints, "long_path", fake_long_path)
    returned = save_output_checkpoint(
        tmp_path,
        stage="generation",
        object_id="q",
        source={},
        candidate={},
        diagnostics={},
    )

    assert observed
    assert returned.is_absolute()
    assert returned.exists()
