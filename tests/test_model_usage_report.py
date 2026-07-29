from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path


class ModelUsageReportTests(unittest.TestCase):
    def test_build_report_lists_final_models_and_figure_source(self) -> None:
        from app.model_usage_report import MODEL_USAGE_REPORT_NAME, build_model_usage_report

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            stage = root / "stage_outputs"
            output = root / "outputs"
            stage.mkdir()

            (stage / "structured_exam.json").write_text(
                json.dumps(
                    {
                        "items": [
                            {
                                "question_id": "q1",
                                "number": "1",
                                "confirmed_question_type": "简答题",
                                "confirmed_score": 6,
                                "stem": "说明相律的基本含义。",
                            },
                            {
                                "question_id": "q2",
                                "number": "2",
                                "confirmed_question_type": "作图题",
                                "confirmed_score": 10,
                                "stem": "画出示意图。",
                                "needs_figure": True,
                            },
                        ]
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            for name, model in [
                ("knowledge_plans.json", "deepseek-v4-pro"),
                ("evidence_selection.json", "deepseek-v4-pro"),
                ("answer_fragments.json", "deepseek-v4-flash"),
            ]:
                prompt_tokens = 100 if name == "knowledge_plans.json" else (200 if name == "evidence_selection.json" else 300)
                completion_tokens = 10 if name == "knowledge_plans.json" else (20 if name == "evidence_selection.json" else 30)
                (stage / name).write_text(
                    json.dumps(
                        {
                            "provider": "deepseek",
                            "model": model,
                            "model_token_feedback": [
                                {
                                    "question_id": "q1",
                                    "ok": True,
                                    "attempts": [
                                        {
                                            "strategy": "primary",
                                            "model": model,
                                            "max_tokens": 12288,
                                            "prompt_tokens": prompt_tokens,
                                            "completion_tokens": completion_tokens,
                                            "reasoning_tokens": 3,
                                        }
                                    ],
                                },
                                {
                                    "question_id": "q2",
                                    "ok": True,
                                    "attempts": [
                                        {
                                            "strategy": "primary",
                                            "model": model,
                                            "max_tokens": 12288,
                                            "prompt_tokens": prompt_tokens,
                                            "completion_tokens": completion_tokens,
                                            "reasoning_tokens": 3,
                                        }
                                    ],
                                },
                            ],
                        },
                        ensure_ascii=False,
                    ),
                    encoding="utf-8",
                )
            (stage / "answer_generation_progress.json").write_text(
                json.dumps({"status": "completed", "completed": 2, "total": 2}, ensure_ascii=False),
                encoding="utf-8",
            )
            (stage / "drawing_code_generation.json").write_text(
                json.dumps(
                    {
                        "provider": "deepseek",
                        "model": "deepseek-v4-pro",
                        "generated": [{"question_id": "q2", "model": "deepseek-v4-pro"}],
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            (stage / "direct_model_figures.json").write_text(
                json.dumps({"provider": "bailian", "image_model": "qwen-image-2.0-pro", "generated": []}, ensure_ascii=False),
                encoding="utf-8",
            )
            (stage / "figure_generation_audit.json").write_text(
                json.dumps(
                    {
                        "items": [
                            {
                                "question_id": "q2",
                                "generation_method": "model_code_renderer",
                                "needs_manual_review": False,
                            }
                        ]
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )

            report = build_model_usage_report(stage, output, "demo_task")
            self.assertEqual(report.name, MODEL_USAGE_REPORT_NAME)
            text = report.read_text(encoding="utf-8")
            self.assertIn("# 模型调用汇总", text)
            self.assertIn("deepseek/deepseek-v4-flash", text)
            self.assertIn("模型代码+程序渲染：deepseek/deepseek-v4-pro", text)
            self.assertIn("tokens：输入 300；输出 30；推理 3；本次合计 330", text)
            self.assertIn("总计 token", text)
            self.assertIn("可统计 660", text)
            self.assertIn("未返回 usage：作图/图片", text)
            self.assertIn("q1", text)
            self.assertIn("q2", text)


if __name__ == "__main__":
    unittest.main()
