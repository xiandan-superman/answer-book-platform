from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


class StructuredAnswerTokenTests(unittest.TestCase):
    def test_generation_calls_model_only_for_nonreusable_questions(self) -> None:
        from app.answer_generation import generate_answer_fragments
        from app.settings import ProviderConfig

        provider = ProviderConfig(
            name="test",
            type="openai_compatible",
            base_url="https://example.test/v1",
            api_key="key",
            default_model="test-model",
            model_options=(),
            allow_custom_model=True,
            model_hint="",
            temperature=0.1,
            max_tokens=24576,
        )
        reused = {
            "schema_version": "answer_book.answer_fragment.v4",
            "question_id": "q1",
            "section": "一、简答题",
            "number": "1",
            "answer": "已复用",
            "evidence_ids": [],
            "blocks": [{"label": "解析", "segments": [{"type": "text", "text": "已复用。"}]}],
            "formulas": [],
            "warnings": [],
        }
        generated = {
            **reused,
            "question_id": "q2",
            "number": "2",
            "answer": "新生成",
        }
        calls: list[str] = []

        def fake_generate(_client, _provider, question, _evidence, _model, **_kwargs):
            calls.append(question["question_id"])
            return dict(generated), []

        with tempfile.TemporaryDirectory() as raw_tmp, patch.dict(
            "os.environ", {"ANSWER_GENERATION_BATCH_ENABLED": "0", "ANSWER_GENERATION_MAX_WORKERS": "1"}
        ), patch("app.answer_generation.generate_one_fragment", side_effect=fake_generate):
            output = Path(raw_tmp) / "answer_fragments.json"
            (output.parent / "answer_drafts.json").write_text(
                json.dumps(
                    {
                        "schema_version": "answer_book.answer_drafts.v1",
                        "drafts": [{"question_id": "q1", "answer": "原始草稿"}],
                    }
                ),
                encoding="utf-8",
            )
            result = generate_answer_fragments(
                {"items": [{"question_id": "q1", "number": "1"}, {"question_id": "q2", "number": "2"}]},
                [],
                provider,
                "test-model",
                output,
                reusable_fragments={"q1": reused},
            )
            payload = json.loads(output.read_text(encoding="utf-8"))
            drafts_payload = json.loads(
                (output.parent / "answer_drafts.json").read_text(encoding="utf-8")
            )

        self.assertTrue(result.ok)
        self.assertEqual(1, result.reused_fragment_count)
        self.assertEqual(["q2"], calls)
        self.assertEqual(["q1", "q2"], [item["question_id"] for item in payload["fragments"]])
        self.assertEqual(["q1"], [item["question_id"] for item in drafts_payload["drafts"]])

    def test_structured_answer_generation_uses_adaptive_bounded_budget(self) -> None:
        from app.answer_generation import structured_answer_max_tokens
        from app.settings import DEFAULT_MODEL_MAX_TOKENS, STRUCTURED_ANSWER_MAX_TOKENS, ProviderConfig

        provider = ProviderConfig(
            name="test",
            type="openai_compatible",
            base_url="https://example.test/v1",
            api_key="key",
            default_model="test-model",
            model_options=(),
            allow_custom_model=True,
            model_hint="",
            temperature=0.1,
            max_tokens=DEFAULT_MODEL_MAX_TOKENS,
        )

        self.assertEqual(24576, STRUCTURED_ANSWER_MAX_TOKENS)
        self.assertEqual(STRUCTURED_ANSWER_MAX_TOKENS, structured_answer_max_tokens(provider))
        self.assertEqual(
            10752,
            structured_answer_max_tokens(
                provider,
                {
                    "subquestions": [
                        {"number": "1", "question_type": "简答题"},
                        {"number": "2", "question_type": "简答题"},
                        {"number": "3", "question_type": "简答题"},
                    ]
                },
            ),
        )
        self.assertEqual(
            21504,
            structured_answer_max_tokens(
                provider,
                {"subquestions": [{"number": str(i), "question_type": "简答题"} for i in range(20)]},
            ),
        )

    def test_answer_generation_timeout_is_layered_and_configurable(self) -> None:
        from app.answer_generation import answer_generation_timeout_seconds

        self.assertEqual(180, answer_generation_timeout_seconds({"question_type": "简答题"}, thinking_mode="disabled"))
        self.assertEqual(300, answer_generation_timeout_seconds({"question_type": "计算题"}, thinking_mode="disabled"))
        self.assertEqual(600, answer_generation_timeout_seconds({"question_type": "计算题"}, thinking_mode="high"))
        with patch.dict(
            "os.environ",
            {
                "ANSWER_GENERATION_TIMEOUT_SECONDS": "240",
                "ANSWER_GENERATION_COMPLEX_TIMEOUT_SECONDS": "480",
                "ANSWER_GENERATION_REASONING_TIMEOUT_SECONDS": "900",
            },
        ):
            self.assertEqual(240, answer_generation_timeout_seconds({"question_type": "简答题"}, thinking_mode="disabled"))
            self.assertEqual(480, answer_generation_timeout_seconds({"question_type": "计算题"}, thinking_mode="disabled"))
            self.assertEqual(900, answer_generation_timeout_seconds({"question_type": "计算题"}, thinking_mode="medium"))

    def test_answer_generation_honors_task_thinking_choice(self) -> None:
        from app.answer_generation import answer_generation_attempt_thinking_mode, answer_generation_thinking_mode
        from app.settings import ProviderConfig

        base = dict(
            name="test",
            type="openai_compatible",
            base_url="https://example.test/v1",
            api_key="key",
            default_model="test-model",
            model_options=(),
            allow_custom_model=True,
            model_hint="",
            temperature=0.1,
            max_tokens=24576,
        )
        self.assertEqual("high", answer_generation_thinking_mode(ProviderConfig(**base, thinking_mode="high")))
        self.assertEqual("disabled", answer_generation_thinking_mode(ProviderConfig(**base, thinking_mode="auto")))
        self.assertEqual("disabled", answer_generation_thinking_mode(ProviderConfig(**base, thinking_mode="unexpected")))
        with patch.dict("os.environ", {"ANSWER_GENERATION_AUTO_THINKING_MODE": "low"}):
            self.assertEqual("low", answer_generation_thinking_mode(ProviderConfig(**base, thinking_mode="auto")))
        automatic = ProviderConfig(**base, thinking_mode="auto")
        explicit_disabled = ProviderConfig(**base, thinking_mode="disabled")
        calculation = {"question_type": "计算题"}
        self.assertEqual("disabled", answer_generation_attempt_thinking_mode(automatic, calculation, 0))
        self.assertEqual("disabled", answer_generation_attempt_thinking_mode(automatic, calculation, 1))
        self.assertEqual("disabled", answer_generation_attempt_thinking_mode(explicit_disabled, calculation, 1))

    def test_explicit_reasoning_receives_provider_full_token_allowance(self) -> None:
        from app.llm_client import _effective_max_tokens
        from app.settings import ProviderConfig

        provider = ProviderConfig(
            name="generic-reasoning-provider",
            type="openai_compatible",
            base_url="https://example.test/v1",
            api_key="key",
            default_model="reasoning-model",
            model_options=(),
            allow_custom_model=True,
            model_hint="",
            temperature=0.1,
            max_tokens=24576,
        )
        self.assertEqual(10752, _effective_max_tokens(provider, 10752, "disabled"))
        self.assertEqual(24576, _effective_max_tokens(provider, 10752, "low"))


if __name__ == "__main__":
    unittest.main()
