from __future__ import annotations

import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


class StructuredAnswerTokenTests(unittest.TestCase):
    def test_structured_answer_generation_uses_configured_token_floor(self) -> None:
        from app.answer_generation import structured_answer_max_tokens
        from app.settings import DEFAULT_MODEL_MAX_TOKENS, ProviderConfig, STRUCTURED_ANSWER_MAX_TOKENS

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

        self.assertEqual(49152, STRUCTURED_ANSWER_MAX_TOKENS)
        self.assertEqual(STRUCTURED_ANSWER_MAX_TOKENS, structured_answer_max_tokens(provider))


if __name__ == "__main__":
    unittest.main()
