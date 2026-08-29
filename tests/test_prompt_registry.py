from __future__ import annotations

import ast
import copy
import json
import threading
import urllib.request
from http.server import ThreadingHTTPServer

from app import runtime_monitor
from app.paths import PROJECT_ROOT
from app.prompt_registry import (
    PROMPT_CONTRACTS,
    build_prompt_registry_report,
    current_prompt_contract_id,
    observe_prompt_request,
    practice_prompt_contract_id,
    prompt_contract,
)


def test_prompt_catalog_has_unique_versioned_contracts_and_task_profiles() -> None:
    assert len(PROMPT_CONTRACTS) >= 20
    assert len(PROMPT_CONTRACTS) == len(set(PROMPT_CONTRACTS))
    for prompt_id, contract in PROMPT_CONTRACTS.items():
        assert contract.prompt_id == prompt_id
        assert contract.version == "1"
        assert contract.task_profiles
        assert contract.section_order
        assert contract.output_contract
        assert contract.consumers
    assert set(PROMPT_CONTRACTS["exam.answer_draft_single"].task_profiles) == {
        "exam",
        "question_only",
    }
    assert "textbook_evidence" in PROMPT_CONTRACTS[
        "exam.answer_draft_single"
    ].section_order
    assert PROMPT_CONTRACTS[
        "exam.answer_draft_single"
    ].disabled_sections_by_profile == (("question_only", ("textbook_evidence",)),)
    assert PROMPT_CONTRACTS["exam.evidence_selection"].task_profiles == ("exam",)


def test_registered_observation_contains_hashes_but_no_prompt_content() -> None:
    payload = {
        "model": "model-a",
        "messages": [
            {"role": "system", "content": "private system instructions"},
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "private question and evidence"},
                    {"type": "image_url", "image_url": {"url": "data:image/png;base64,AAAA"}},
                ],
            },
        ],
        "tools": [{"type": "function", "function": {"name": "generate_image"}}],
        "response_format": {"type": "json_object"},
    }
    original = copy.deepcopy(payload)

    with prompt_contract("exam.answer_draft_single"):
        observation = observe_prompt_request(payload)

    assert payload == original
    assert observation["prompt_id"] == "exam.answer_draft_single"
    assert observation["registered"] is True
    assert observation["version"] == "1"
    assert observation["transport_shape"] == "chat_messages"
    assert observation["message_count"] == 2
    assert observation["tool_schema_count"] == 1
    assert observation["observed_sections"][1]["image_count"] == 1
    assert len(observation["prompt_fingerprint_sha256"]) == 64
    serialized = json.dumps(observation, ensure_ascii=False)
    assert "private system instructions" not in serialized
    assert "private question and evidence" not in serialized
    assert "data:image" not in serialized
    assert observation["behavior_changed"] is False
    assert observation["assembly_order_enforced"] is False


def test_unknown_and_nested_prompt_contracts_are_observed_without_blocking() -> None:
    assert current_prompt_contract_id() == ""
    with prompt_contract("exam.answer_draft_single"):
        assert current_prompt_contract_id() == "exam.answer_draft_single"
        with prompt_contract("tool.image_generation"):
            nested = observe_prompt_request({"prompt": "private generated image request"})
            assert nested["prompt_id"] == "tool.image_generation"
            assert nested["registered"] is True
        assert current_prompt_contract_id() == "exam.answer_draft_single"
    assert current_prompt_contract_id() == ""
    unknown = observe_prompt_request({"messages": [{"role": "user", "content": "secret"}]})
    assert unknown["prompt_id"] == "unregistered"
    assert unknown["registered"] is False


def test_practice_stage_mapping_preserves_intentional_prompt_forks() -> None:
    assert practice_prompt_contract_id("source_analysis") == "practice.source_analysis"
    assert practice_prompt_contract_id("planning") == "practice.planning"
    assert practice_prompt_contract_id("generation") == "practice.generation"
    assert practice_prompt_contract_id("semantic_review") == "practice.semantic_review"
    assert practice_prompt_contract_id("unknown") == "unregistered.practice"


def test_execution_intent_records_prompt_identity_without_content(tmp_path, monkeypatch) -> None:
    ledger = tmp_path / "model_execution_events.jsonl"
    monkeypatch.setattr(runtime_monitor, "MODEL_EXECUTION_EVENT_LEDGER", ledger)
    monkeypatch.setattr(runtime_monitor, "MODEL_CALL_LEDGER", tmp_path / "model_calls.jsonl")
    payload = {
        "model": "model-a",
        "messages": [
            {"role": "system", "content": "private registry system"},
            {"role": "user", "content": "private registry question"},
        ],
    }

    with prompt_contract("exam.answer_draft_single"):
        with runtime_monitor.track_model_call(
            provider="provider-a",
            model="model-a",
            purpose="chat_json",
            timeout=60,
            request_payload=payload,
            protocol="chat_completions",
            endpoint="https://example.invalid/v1/chat/completions",
        ):
            pass

    content = ledger.read_text(encoding="utf-8")
    intent = json.loads(content.splitlines()[0])
    observation = intent["prompt_observation"]
    assert observation["prompt_id"] == "exam.answer_draft_single"
    assert observation["registered"] is True
    assert observation["declared_section_order"]
    assert "private registry system" not in content
    assert "private registry question" not in content


def test_prompt_observer_failure_is_fail_open_for_business_request(tmp_path, monkeypatch) -> None:
    ledger = tmp_path / "model_execution_events.jsonl"
    monkeypatch.setattr(runtime_monitor, "MODEL_EXECUTION_EVENT_LEDGER", ledger)
    monkeypatch.setattr(runtime_monitor, "MODEL_CALL_LEDGER", tmp_path / "model_calls.jsonl")
    monkeypatch.setattr(
        runtime_monitor,
        "observe_prompt_request",
        lambda _payload: (_ for _ in ()).throw(RuntimeError("observer unavailable")),
    )
    entered = False

    with runtime_monitor.track_model_call(
        provider="provider-a",
        model="model-a",
        purpose="chat_json",
        timeout=60,
        request_payload={"messages": []},
    ):
        entered = True

    assert entered is True
    intent = json.loads(ledger.read_text(encoding="utf-8").splitlines()[0])
    assert intent["prompt_observation"]["report_unavailable"] is True
    assert intent["prompt_observation"]["behavior_changed"] is False


def test_prompt_registry_report_separates_legacy_registered_and_unregistered(tmp_path) -> None:
    ledger = tmp_path / "model_execution_events.jsonl"
    rows = [
        {"event_type": "invocation.intent", "task_id": "private-task-legacy"},
        {
            "event_type": "invocation.intent",
            "task_id": "private-task-registered",
            "prompt_observation": {
                "prompt_id": "exam.answer_draft_single",
                "registered": True,
                "transport_shape": "chat_messages",
                "prompt_fingerprint_sha256": "a" * 64,
            },
        },
        {
            "event_type": "invocation.intent",
            "task_id": "private-task-unregistered",
            "prompt_observation": {
                "prompt_id": "unregistered",
                "registered": False,
                "transport_shape": "responses_input",
                "prompt_fingerprint_sha256": "b" * 64,
            },
        },
    ]
    ledger.write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows),
        encoding="utf-8",
    )

    report = build_prompt_registry_report(model_execution_ledger=ledger)

    assert report["execution_intent_count"] == 3
    assert report["prompt_observation_count"] == 2
    assert report["legacy_intent_without_prompt_observation_count"] == 1
    assert report["registered_observation_count"] == 1
    assert report["unregistered_observation_count"] == 1
    assert report["observed_contract_counts"] == {
        "exam.answer_draft_single": 1,
        "unregistered": 1,
    }
    assert report["readiness"]["registry_authoritative"] is False
    assert report["added_model_calls"] == 0
    serialized = json.dumps(report, ensure_ascii=False)
    assert "private-task" not in serialized


def test_prompt_registry_api_is_shadow_only(monkeypatch) -> None:
    from app import server as platform_server

    monkeypatch.setattr(platform_server, "append_runtime_log", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        platform_server,
        "build_prompt_registry_report",
        lambda: {
            "schema_version": "answer_book.prompt_registry.v1",
            "mode": "shadow",
            "authority": "observation_only",
            "enforced": False,
            "behavior_changed": False,
            "catalog_count": 24,
            "added_model_calls": 0,
            "added_tokens": 0,
            "added_network_requests": 0,
        },
    )
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), platform_server.PlatformHandler)
    worker = threading.Thread(target=httpd.serve_forever, daemon=True)
    worker.start()
    try:
        with urllib.request.urlopen(
            f"http://127.0.0.1:{httpd.server_port}/api/quality/prompt-registry"
        ) as response:
            payload = json.loads(response.read().decode("utf-8"))
    finally:
        httpd.shutdown()
        httpd.server_close()
        worker.join(timeout=2)

    assert payload["mode"] == "shadow"
    assert payload["enforced"] is False
    assert payload["behavior_changed"] is False
    assert payload["added_model_calls"] == 0


def test_business_model_entry_points_declare_a_prompt_contract() -> None:
    network_entry_names = {
        "chat_json",
        "chat_text",
        "chat_json_object",
        "run_json",
        "generate_image",
        "edit_image",
    }
    missing: list[str] = []
    for path in sorted((PROJECT_ROOT / "app").rglob("*.py")):
        if path.name == "llm_client.py":
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        parents: dict[ast.AST, ast.AST] = {}
        for parent in ast.walk(tree):
            for child in ast.iter_child_nodes(parent):
                parents[child] = parent
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
                continue
            if node.func.attr not in network_entry_names:
                continue
            current: ast.AST = node
            declared = False
            while current in parents:
                current = parents[current]
                if not isinstance(current, ast.With):
                    continue
                for item in current.items:
                    expression = item.context_expr
                    if not isinstance(expression, ast.Call):
                        continue
                    function = expression.func
                    if (
                        isinstance(function, ast.Name)
                        and function.id == "prompt_contract"
                    ) or (
                        isinstance(function, ast.Attribute)
                        and function.attr == "prompt_contract"
                    ):
                        declared = True
            if not declared:
                missing.append(f"{path.relative_to(PROJECT_ROOT)}:{node.lineno}")
    assert missing == []
