from __future__ import annotations

from pathlib import Path

from app import knowledge_planning
from app.llm_client import LLMError
from app.settings import ProviderConfig


def test_all_transient_knowledge_planning_failures_stop_for_user_retry(tmp_path: Path, monkeypatch) -> None:
    calls: list[dict] = []

    class FailingClient:
        last_json_retry_report = {"attempts": []}

        def __init__(self, _provider) -> None:
            self.last_json_retry_report = {"attempts": []}

        def chat_json_object(self, _messages, **kwargs):
            calls.append(kwargs)
            raise LLMError(
                'Provider HTTP 404: {"error":{"message":"Model \\"gemini-3.7-flash-medium\\" is not supported by any configured account in this group","type":"model_not_found"}}',
                status_code=404,
            )

    monkeypatch.setattr(knowledge_planning, "OpenAICompatibleClient", FailingClient)
    provider = ProviderConfig(
        name="lingsuan_google",
        type="openai_compatible",
        base_url="https://example.test/v1",
        api_key="test-key",
        default_model="gemini-3.7-flash-medium",
        model_options=("gemini-3.7-flash-medium",),
        allow_custom_model=False,
        model_hint="",
        temperature=0.2,
        max_tokens=2048,
    )
    structured = {"items": [{"question_id": "q1", "stem": "题目一"}, {"question_id": "q2", "stem": "题目二"}]}

    result = knowledge_planning.generate_knowledge_plans(
        structured,
        provider,
        provider.default_model,
        tmp_path / "knowledge_plans.json",
    )

    assert result.ok is False
    assert result.failure_state == "service_degraded"
    assert "模型路由暂时不可用" in result.failure_message
    assert len(calls) == 2
    assert all(call["attempts"] == 3 for call in calls)
