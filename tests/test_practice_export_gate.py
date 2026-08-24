import tempfile
import unittest
from pathlib import Path

from app.practice_export import (
    audit_practice_export_data,
    build_practice_question_docx,
    has_unrenderable_practice_markup,
    normalize_practice_markup,
    normalize_practice_question_text,
    resolve_practice_export_payload,
    validate_docx_output,
)


class PracticeExportGateTests(unittest.TestCase):
    def test_disabled_semantic_review_does_not_downgrade_clean_export(self):
        from app.practice_export import validate_practice_export

        report = validate_practice_export({
            "quality": {"status": "passed"},
            "semantic_review": {"status": "disabled", "triggered": False, "items": []},
            "exercises": [{"number": 1, "stem": "说明晶格常数的物理意义。", "generation_status": "completed"}],
        })

        self.assertTrue(report["ok"])
        self.assertEqual("formal", report["release_level"])
        self.assertEqual([], report["warning_issues"])
    def test_literal_escaped_subquestion_breaks_become_real_paragraphs(self):
        normalized = normalize_practice_question_text(r"材料说明。\n(1) 第一问。\n(2) 第二问。")

        self.assertNotIn(r"\n", normalized)
        self.assertEqual(normalized, "材料说明。\n\n(1) 第一问。\n\n(2) 第二问。")

    def test_literal_newline_repair_does_not_touch_latex_commands(self):
        normalized = normalize_practice_question_text(r"已知 $\nabla f=0$，再判断 $\nu$。")

        self.assertIn(r"\nabla", normalized)
        self.assertIn(r"\nu", normalized)

    def test_fill_in_formula_bank_is_blocked_as_answer_leak(self):
        from app.practice_export import validate_practice_export

        data = {"exercises": [{
            "number": 3,
            "question_type": "填空题",
            "stem": "fcc 密排方向为 ______。",
            "formulas": [{
                "latex": r"\mathrm{fcc}:\langle110\rangle",
                "location": "stem",
                "caption": "fcc 密排方向",
            }],
        }]}

        report = validate_practice_export(data)

        self.assertFalse(report["ok"])
        self.assertTrue(any("题面答案泄漏" in issue for issue in report["blocking_issues"]))

    def test_explicit_given_formula_remains_exportable(self):
        from app.practice_export import validate_practice_export

        data = {"exercises": [{
            "question_type": "填空题",
            "stem": "已知状态方程，待求体积为 ______。",
            "formulas": [{"latex": r"PV=nRT", "location": "stem", "role": "given"}],
        }]}

        self.assertTrue(validate_practice_export(data)["ok"])

    def test_structured_formula_preflight_failure_blocks_formal_export(self):
        from unittest.mock import patch

        from app.practice_export import validate_practice_export

        data = {
            "exercises": [
                {
                    "number": 1,
                    "stem": "计算题。",
                    "formulas": [{"latex": r"E=mc^2", "location": "stem", "display": True}],
                }
            ]
        }
        with patch("app.practice_export.preflight_expression_render", return_value="renderer failed"):
            report = validate_practice_export(data)

        self.assertFalse(report["ok"])
        self.assertTrue(any("无法生成 Word 公式对象" in issue for issue in report["blocking_issues"]))

    def test_description_only_diagram_blocks_practice_export(self):
        data = {
            "exercises": [
                {
                    "stem": "根据示意图作答。",
                    "figures": [
                        {
                            "figure_id": "g1",
                            "location": "stem",
                            "figure_type": "diagram",
                            "title": "示意图",
                            "description": "用文字标注的结构关系示意。",
                            "series": [],
                        }
                    ],
                }
            ]
        }
        from app.practice_export import validate_practice_export
        report = validate_practice_export(data)
        self.assertFalse(report["ok"])
        self.assertTrue(any("无法绘制" in issue for issue in report["blocking_issues"]))

    def test_blocks_raw_markers_and_self_correction_from_formal_export(self):
        data = {"exercises": [{"stem": r"自我纠错：题干含未包裹公式 \frac{1}{2}"}]}
        issues = audit_practice_export_data(data)
        self.assertTrue(any("未渲染" in issue for issue in issues))
        self.assertTrue(any("自我纠错" in issue for issue in issues))
        from app.practice_export import validate_practice_export
        report = validate_practice_export(data)
        self.assertFalse(report["ok"])
        self.assertTrue(any("未渲染" in issue for issue in report["blocking_issues"]))
        self.assertTrue(any("自我纠错" in issue for issue in report["blocking_issues"]))

    def test_allows_renderable_inline_math_and_markdown_bold(self):
        data = {"exercises": [{"stem": r"**已知** $\Delta U = Q + W$，求 $Q$。"}]}
        self.assertEqual(audit_practice_export_data(data), [])
        content = build_practice_question_docx(data)
        self.assertTrue(validate_docx_output(content, data)["ok"])
        with __import__("zipfile").ZipFile(__import__("io").BytesIO(content)) as archive:
            xml = archive.read("word/document.xml").decode("utf-8")
        self.assertNotIn("**", xml)
        self.assertNotIn("$", xml)

    def test_repairs_bare_braced_latex_before_the_export_audit(self):
        source = r"气体 \mathrm{Ar(g)} 与液体 \mathrm{H_2O(l)} 的热容不同。"
        normalized = normalize_practice_markup(source)

        self.assertEqual(normalized, r"气体 $\mathrm{Ar(g)}$ 与液体 $\mathrm{H_2O(l)}$ 的热容不同。")
        self.assertFalse(has_unrenderable_practice_markup(normalized))
        self.assertEqual(audit_practice_export_data({"exercises": [{"stem": normalized}]}), [])

    def test_control_characters_inside_math_are_not_exportable(self):
        self.assertTrue(has_unrenderable_practice_markup("$\beta$"))
        self.assertTrue(has_unrenderable_practice_markup("$\frac{1}{2}$"))
        self.assertFalse(has_unrenderable_practice_markup("$\\beta$\n第二段"))

    def test_blocks_missing_figure_image(self):
        issues = audit_practice_export_data({"exercises": [{"stem": "画图", "figures": [{"location": "stem", "title": "相图"}]}]})
        self.assertEqual(issues, [])

    def test_allows_clean_text_and_embedded_figure(self):
        from PIL import Image

        with tempfile.TemporaryDirectory() as raw:
            image = Path(raw) / "figure.png"
            Image.new("RGB", (320, 180), "white").save(image)
            self.assertEqual(audit_practice_export_data({"exercises": [{"stem": "题干", "figures": [{"location": "stem", "image_path": str(image)}]}]}), [])

            data = {
                "exercises": [{
                    "stem": "根据原始题图作答。",
                    "figures": [{"figure_id": "source-1", "location": "stem", "image_path": str(image)}],
                }],
            }
            from app.practice_export import validate_practice_export

            self.assertTrue(validate_practice_export(data)["ok"])
            content = build_practice_question_docx(data)
            report = validate_docx_output(content, data)
            self.assertTrue(report["ok"])
            self.assertEqual(report["media_count"], 1)

    def test_corrupt_embedded_figure_is_not_formally_exportable(self):
        from app.practice_export import validate_practice_export

        with tempfile.TemporaryDirectory() as raw:
            image = Path(raw) / "broken.png"
            image.write_bytes(b"not-an-image")
            data = {"exercises": [{"stem": "根据题图作答。", "figures": [{"image_path": str(image)}]}]}

            report = validate_practice_export(data)

            self.assertFalse(report["ok"])
            self.assertTrue(any("无法绘制" in issue for issue in report["blocking_issues"]))

    def test_failed_question_blocks_formal_word_export(self):
        data = {
            "exercises": [
                {"stem": "第一题正常题干。", "generation_status": "completed"},
                {
                    "stem": "本题生成失败。",
                    "question_type": "作图题",
                    "generation_status": "failed",
                    "generation_error": {"message": "上游模型响应超时（HTTP 524）。"},
                },
            ]
        }

        from app.practice_export import validate_practice_export
        report = validate_practice_export(data)
        self.assertFalse(report["ok"])
        self.assertIn("第 2 题生成失败，不能进入正式题目卷。", report["blocking_issues"])

    def test_failed_question_message_uses_its_real_number(self):
        from app.practice_export import validate_practice_export

        report = validate_practice_export({
            "exercises": [{"number": 7, "stem": "失败占位。", "generation_status": "failed"}],
        })

        self.assertIn("第 7 题生成失败，不能进入正式题目卷。", report["blocking_issues"])

        from app.exercise_generation import recompute_practice_quality

        quality = recompute_practice_quality({
            "requested_count": 1,
            "exercises": [{"number": 7, "stem": "失败占位。", "generation_status": "failed"}],
        })
        self.assertTrue(any(issue.startswith("第 7 题生成失败") for issue in quality["blocking_issues"]))

    def test_full_export_uses_latest_saved_record_instead_of_stale_page_data(self):
        stale = {
            "history_id": "practice_demo",
            "exercises": [{"plan_item_id": "p1", "number": 1, "stem": "失败占位。", "generation_status": "failed"}],
        }
        latest = {
            "history_id": "practice_demo",
            "exercises": [{"plan_item_id": "p1", "number": 1, "stem": "已重新生成。", "generation_status": "completed"}],
        }

        resolved = resolve_practice_export_payload(stale, latest)

        self.assertEqual(resolved["exercises"][0]["stem"], "已重新生成。")
        from app.practice_export import validate_practice_export
        self.assertTrue(validate_practice_export(resolved)["ok"])

    def test_selected_export_uses_only_requested_stable_question_ids(self):
        latest = {
            "history_id": "practice_demo",
            "quality": {"blocking_issues": ["第 2 题生成失败：旧的整套质量结果。"]},
            "exercises": [
                {"plan_item_id": "p1", "number": 1, "stem": "第一题。", "generation_status": "completed"},
                {"plan_item_id": "p2", "number": 2, "stem": "失败占位。", "generation_status": "failed"},
                {"plan_item_id": "p3", "number": 3, "stem": "第三题。", "generation_status": "completed"},
            ],
        }
        request = {
            "history_id": "practice_demo",
            "export_scope": "selected",
            "selected_exercise_ids": ["p1", "p3"],
            "exercises": latest["exercises"],
        }

        resolved = resolve_practice_export_payload(request, latest)

        self.assertEqual([item["number"] for item in resolved["exercises"]], [1, 3])
        self.assertEqual(resolved["quality"], {})
        from app.practice_export import validate_practice_export
        self.assertTrue(validate_practice_export(resolved)["ok"])

    def test_empty_selected_export_is_blocked(self):
        from app.practice_export import validate_practice_export

        resolved = resolve_practice_export_payload(
            {"export_scope": "selected", "selected_exercise_ids": ["missing"]},
            {"exercises": [{"plan_item_id": "p1", "stem": "第一题。"}]},
        )

        self.assertFalse(validate_practice_export(resolved)["ok"])
        self.assertIn("没有可导出的题目。", validate_practice_export(resolved)["blocking_issues"])


if __name__ == "__main__":
    unittest.main()
