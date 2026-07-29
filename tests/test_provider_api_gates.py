from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


class ProviderApiGateTests(unittest.TestCase):
    def test_pipeline_disallows_demo_without_key_by_default(self) -> None:
        from app.pipeline import PipelineOptions

        self.assertTrue(PipelineOptions().use_model)
        self.assertFalse(PipelineOptions().allow_demo_without_key)

    def test_task_creation_reports_missing_role_provider_key(self) -> None:
        from app.server import _provider_key_validation_errors

        errors = _provider_key_validation_errors(
            [
                (
                    "答案生成模型",
                    SimpleNamespace(name="yunwu", api_key="", api_key_env="YUNWU_API_KEY"),
                )
            ]
        )

        self.assertEqual(1, len(errors))
        self.assertIn("答案生成模型 yunwu 未配置 API Key", errors[0])
        self.assertIn("YUNWU_API_KEY", errors[0])

    def test_final_acceptance_blocks_demo_answer_fragments(self) -> None:
        from app.final_acceptance import answer_fragment_blocking_findings

        with tempfile.TemporaryDirectory() as raw_tmp:
            stage_dir = Path(raw_tmp)
            (stage_dir / "answer_fragments.json").write_text(
                json.dumps(
                    {
                        "provider": "demo",
                        "model": "demo",
                        "fragments": [
                            {
                                "question_id": "q1",
                                "answer": "待复核",
                                "_review_flags": [
                                    {
                                        "code": "answer_generation_failed",
                                        "message": "demo fragment; configure provider API key for real generation",
                                    }
                                ],
                            }
                        ],
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )

            issues = answer_fragment_blocking_findings(stage_dir)

        self.assertTrue(any("demo 占位流程" in issue for issue in issues))
        self.assertTrue(any("q1 答案仍为待复核" in issue for issue in issues))
        self.assertTrue(any("答案生成失败" in issue for issue in issues))


if __name__ == "__main__":
    unittest.main()
