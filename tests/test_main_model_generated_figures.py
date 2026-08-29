from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


class MainModelGeneratedFigureTests(unittest.TestCase):
    def test_non_drawing_question_can_use_only_main_model_accepted_asset(self):
        from app.figures import _main_model_generated_image_specs

        with tempfile.TemporaryDirectory() as tmp:
            image = Path(tmp) / "accepted.png"
            Image.new("RGB", (160, 120), "white").save(image)
            fragment = {
                "question_id": "q1",
                "question_type": "简答题",
                "generated_images": [
                    {"asset_id": "img_sha256_abc", "caption": "解释性示意图"}
                ],
                "_meta": {
                    "image_tool_loop": {
                        "generated_artifacts": [
                            {
                                "asset_id": "img_sha256_abc",
                                "path": str(image),
                                "provider": "p",
                                "model": "image-model",
                            }
                        ]
                    }
                },
            }

            specs = _main_model_generated_image_specs(fragment, "q1")

            self.assertEqual(1, len(specs))
            self.assertEqual("model_generated_image", specs[0]["kind"])
            self.assertEqual("main_model_tool_loop", specs[0]["source"])

    def test_accepted_asset_is_copied_by_figure_stage_without_regeneration(self):
        from app.figures import generate_figures

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "accepted.png"
            image = Image.new("RGB", (160, 120), "white")
            for x in range(20, 140):
                image.putpixel((x, 60), (0, 0, 0))
            for y in range(20, 100):
                image.putpixel((80, y), (0, 0, 0))
            image.save(source)
            specs_json = root / "specs.json"
            specs_json.write_text(
                json.dumps(
                    {
                        "figures": [
                            {
                                "figure_id": "q1_agent_img_01",
                                "question_id": "q1",
                                "kind": "model_generated_image",
                                "path": str(source),
                                "caption": "解释性示意图",
                                "source": "main_model_tool_loop",
                            }
                        ]
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            output_dir = root / "figures"

            generated = generate_figures(specs_json, output_dir)

            self.assertEqual([output_dir / "q1_agent_img_01.png"], generated)
            self.assertTrue(generated[0].exists())

    def test_main_model_accepted_asset_suppresses_uninspected_program_figure(self):
        from app.figures import prepare_figures_for_fragments

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "accepted.png"
            image = Image.new("RGB", (160, 120), "white")
            for x in range(20, 140):
                image.putpixel((x, 60), (0, 0, 0))
            for y in range(20, 100):
                image.putpixel((80, y), (0, 0, 0))
            image.save(source)
            fragments_json = root / "answer_fragments.json"
            fragments_json.write_text(
                json.dumps(
                    {
                        "image_generation_orchestration": "main_model_tool_loop",
                        "fragments": [
                            {
                                "question_id": "q1",
                                "question_type": "作图题",
                                "generated_images": [
                                    {"asset_id": "img_sha256_checked", "caption": "主模型检查后的图"}
                                ],
                                "figure_specs": [
                                    {
                                        "kind": "custom_diagram",
                                        "caption": "不应再渲染的程序图",
                                        "elements": [
                                            {"type": "line", "start": [0, 0], "end": [1, 1], "label": "旧路径"}
                                        ],
                                    }
                                ],
                                "blocks": [],
                                "_meta": {
                                    "image_tool_loop": {
                                        "generated_artifacts": [
                                            {
                                                "asset_id": "img_sha256_checked",
                                                "path": str(source),
                                                "provider": "image-provider",
                                                "model": "image-model",
                                            }
                                        ]
                                    }
                                },
                            }
                        ],
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            specs_json = root / "figure_specs.json"
            output_dir = root / "figures"

            generated = prepare_figures_for_fragments(
                {
                    "items": [
                        {
                            "question_id": "q1",
                            "question_type": "作图题",
                            "section": "作图题",
                            "stem": "画出示意图",
                            "drawing_generation_mode": "figure_specs",
                        }
                    ]
                },
                fragments_json,
                specs_json,
                output_dir,
            )
            specs = json.loads(specs_json.read_text(encoding="utf-8"))["figures"]

            self.assertEqual([output_dir / "q1_agent_img_01.png"], generated)
            self.assertEqual(1, len(specs))
            self.assertEqual("main_model_tool_loop", specs[0]["source"])

    def test_main_model_mode_never_falls_back_to_unaccepted_program_specs(self):
        from app.figures import prepare_figures_for_fragments

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            fragments_json = root / "answer_fragments.json"
            fragments_json.write_text(
                json.dumps({
                    "image_generation_orchestration": "main_model_tool_loop",
                    "fragments": [{
                        "question_id": "q1",
                        "question_type": "作图题",
                        "generated_images": [],
                        "figure_specs": [{
                            "kind": "custom_diagram",
                            "caption": "不得跨链路采用",
                            "elements": [{"type": "line", "start": [0, 0], "end": [1, 1]}],
                        }],
                        "blocks": [],
                    }],
                }, ensure_ascii=False),
                encoding="utf-8",
            )
            specs_json = root / "figure_specs.json"

            generated = prepare_figures_for_fragments(
                {"items": [{
                    "question_id": "q1",
                    "question_type": "作图题",
                    "section": "作图题",
                    "stem": "画出示意图",
                    "drawing_generation_mode": "figure_specs",
                }]},
                fragments_json,
                specs_json,
                root / "figures",
            )
            report = json.loads(specs_json.read_text(encoding="utf-8"))

            self.assertEqual([], generated)
            self.assertEqual([], report["figures"])
            self.assertFalse(report["drawing_code_generation"]["enabled"])


if __name__ == "__main__":
    unittest.main()
