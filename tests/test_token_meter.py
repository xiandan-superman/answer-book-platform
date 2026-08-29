from __future__ import annotations

import json

from app.token_meter import compact_non_core_history, measure_request_tokens


def test_token_meter_includes_tools_images_structure_and_provider_calibration(tmp_path) -> None:
    ledger = tmp_path / "model_calls.jsonl"
    rows = [
        {
            "provider": "p",
            "model": "m",
            "usage_source": "provider_reported",
            "prompt_tokens": 200,
            "estimated_prompt_tokens": 100,
        }
        for _ in range(3)
    ]
    ledger.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")
    measurement = measure_request_tokens(
        [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "题目"},
                    {"type": "image_url", "image_url": {"url": "https://example.invalid/image.png"}},
                ],
            }
        ],
        provider="p",
        model="m",
        tools=[{"type": "function", "name": "generate_image", "parameters": {"type": "object"}}],
        ledger_path=ledger,
    )

    assert measurement.image_tokens > 0
    assert measurement.tool_schema_tokens > 0
    assert measurement.structural_tokens > 0
    assert measurement.calibration_factor == 2.0
    assert measurement.provider_usage_calibrated is True


def test_compaction_only_rewrites_old_failed_tool_results_and_preserves_core_history() -> None:
    failure = json.dumps({"ok": False, "error": {"code": "FAILED", "message": "x" * 2000}})
    success = json.dumps({"ok": True, "asset": {"asset_id": "img_core"}, "pixels": "x" * 2000})
    messages = [
        {"role": "system", "content": "不可压缩的答案约束"},
        {"role": "user", "content": "不可压缩的题干与确认教材证据"},
        {"role": "tool", "tool_call_id": "old", "content": failure},
        {"role": "tool", "tool_call_id": "success", "content": success},
        {"role": "tool", "tool_call_id": "recent-1", "content": failure},
        {"role": "tool", "tool_call_id": "recent-2", "content": failure},
    ]

    compacted, observation = compact_non_core_history(messages, retain_recent_failures=2)

    assert observation["compacted_result_count"] == 1
    assert observation["core_history_changed"] is False
    assert compacted[0] == messages[0]
    assert compacted[1] == messages[1]
    assert compacted[3] == messages[3]
    assert compacted[4] == messages[4]
    assert compacted[5] == messages[5]
    assert compacted[2]["tool_call_id"] == "old"
    assert "original_sha256" in compacted[2]["content"]


def test_compaction_preserves_responses_result_with_pixels() -> None:
    failure = json.dumps({"ok": False, "error": {"code": "FAILED", "message": "x" * 2000}})
    messages = [
        {
            "type": "function_call_output",
            "call_id": "image",
            "output": [{"type": "input_text", "text": failure}, {"type": "input_image", "image_url": "data:image/png;base64,AA=="}],
        },
        {"type": "function_call_output", "call_id": "a", "output": [{"type": "input_text", "text": failure}]},
        {"type": "function_call_output", "call_id": "b", "output": [{"type": "input_text", "text": failure}]},
        {"type": "function_call_output", "call_id": "c", "output": [{"type": "input_text", "text": failure}]},
    ]

    compacted, _ = compact_non_core_history(messages, retain_recent_failures=2)

    assert compacted[0] == messages[0]
    assert compacted[1]["call_id"] == "a"
    assert "original_sha256" in compacted[1]["output"][0]["text"]
