from __future__ import annotations

import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


class EvidenceSelectionTokenTests(unittest.TestCase):
    def test_evidence_selection_uses_default_max_tokens(self) -> None:
        from app.evidence_selection import EVIDENCE_SELECTION_MAX_TOKENS, _select_one
        from app.retrieval import EvidenceCandidate
        from app.settings import DEFAULT_MODEL_MAX_TOKENS, ProviderConfig

        class FakeClient:
            last_json_retry_report = {}

            def __init__(self) -> None:
                self.max_tokens = None

            def chat_json_object(self, messages, **kwargs):
                self.max_tokens = kwargs.get("max_tokens")
                return {
                    "question_id": "q1",
                    "knowledge_points": [
                        {
                            "knowledge_point": "相律",
                            "selected_evidence_ids": ["ev1"],
                            "rejected_evidence_ids": [],
                            "reason": "候选证据直接说明相律。",
                        }
                    ],
                }

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
        client = FakeClient()
        _select_one(
            client,
            provider,
            "test-model",
            {"question_id": "q1", "stem": "判断相律。"},
            {"question_id": "q1", "knowledge_points": ["相律"]},
            [
                EvidenceCandidate(
                    "ev1",
                    "q1",
                    "示例教材",
                    "示例教材",
                    "1",
                    "demo.json",
                    "1",
                    "10",
                    9.0,
                    "相律说明。",
                    True,
                    "相律",
                )
            ],
        )

        self.assertEqual(DEFAULT_MODEL_MAX_TOKENS, EVIDENCE_SELECTION_MAX_TOKENS)
        self.assertEqual(DEFAULT_MODEL_MAX_TOKENS, client.max_tokens)


if __name__ == "__main__":
    unittest.main()
