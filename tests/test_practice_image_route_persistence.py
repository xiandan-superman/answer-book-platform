from __future__ import annotations

import json


def test_compact_practice_request_preserves_atomic_image_route():
    from app.practice_store import _compact_request

    compact = _compact_request(
        {
            "provider": "lingsuan_google",
            "model": "gemini-3.7-flash-medium",
            "image_orchestration": "main_model_tool_loop",
            "image_provider": "lingsuan_image",
            "image_model": "gpt-image-2",
        }
    )

    assert compact["image_orchestration"] == "main_model_tool_loop"
    assert compact["image_provider"] == "lingsuan_image"
    assert compact["image_model"] == "gpt-image-2"


def test_tool_loop_initialization_error_is_not_mislabeled_as_model_output():
    from app.exercise_generation import _generation_error_detail
    from app.model_tool_loop import ModelToolLoopUnavailableError

    detail = _generation_error_detail(
        ModelToolLoopUnavailableError("所选主模型不支持自主生图工具回路。")
    )

    assert detail["code"] == "model_tool_loop_unsupported"
    assert detail["title"] == "主模型不支持自主生图"
    assert detail["retryable"] is False
    assert detail["requires_configuration"] is False


def test_missing_image_route_is_reported_as_configuration_error():
    from app.exercise_generation import _generation_error_detail
    from app.model_tool_loop import ModelToolLoopUnavailableError

    detail = _generation_error_detail(
        ModelToolLoopUnavailableError("缺少生图服务配置。", requires_configuration=True)
    )

    assert detail["code"] == "image_tool_configuration_missing"
    assert detail["requires_configuration"] is True
    assert detail["retryable"] is False


def test_legacy_history_recovers_image_route_from_its_completed_job(tmp_path, monkeypatch):
    from app import practice_store

    job_dir = tmp_path / "jobs"
    job_dir.mkdir()
    monkeypatch.setattr(practice_store, "PRACTICE_JOB_DIR", job_dir)
    (job_dir / "generation_1.json").write_text(
        json.dumps(
            {
                "history_id": "practice_legacy",
                "payload": {
                    "image_orchestration": "main_model_tool_loop",
                    "image_provider": "lingsuan_image",
                    "image_model": "gpt-image-2",
                },
            }
        ),
        encoding="utf-8",
    )

    recovered = practice_store._recover_legacy_image_route(
        "practice_legacy",
        {"image_orchestration": "main_model_tool_loop"},
    )

    assert recovered["image_provider"] == "lingsuan_image"
    assert recovered["image_model"] == "gpt-image-2"
    assert recovered["image_route_recovered_from_job"] is True
