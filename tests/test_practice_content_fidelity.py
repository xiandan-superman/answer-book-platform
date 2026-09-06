from copy import deepcopy
from io import BytesIO
from zipfile import ZipFile

import pytest
from lxml import etree

from app.practice_export import build_practice_question_docx, build_practice_solution_docx, validate_docx_output


def _change_xml(content, change):
    output = BytesIO()
    with ZipFile(BytesIO(content)) as source, ZipFile(output, "w") as target:
        for member in source.infolist():
            raw = source.read(member.filename)
            if member.filename == "word/document.xml":
                root = etree.fromstring(raw)
                change(root)
                raw = etree.tostring(root)
            target.writestr(member, raw)
    return output.getvalue()


@pytest.mark.parametrize("source_mode", ["question", "knowledge"])
@pytest.mark.parametrize("field", ["stem", "option", "math", "math_structure"])
def test_same_counts_do_not_hide_changed_question_content(source_mode, field):
    data = {"source_mode": source_mode, "exercises": [{
        "stem": "原题条件：计算 $x^{2}$。",
        "options": [{"text": "保持体积不变"}, {"text": "保持压强不变"}],
    }]}
    content = build_practice_question_docx(data)
    assert validate_docx_output(content, data)["ok"]

    def change(root):
        for node in root.iter():
            if field == "stem" and node.text and "原题条件" in node.text:
                node.text = node.text.replace("原题条件", "无关文字")
            elif field == "option" and node.text and "保持体积" in node.text:
                node.text = node.text.replace("保持体积", "保持温度")
            elif field == "math" and etree.QName(node).localname == "t" and node.text == "2":
                node.text = "3"
            elif field == "math_structure" and etree.QName(node).localname in {"sSup", "sup"}:
                node.tag = node.tag.replace("sSup", "sSub").replace("}sup", "}sub")

    report = validate_docx_output(_change_xml(content, change), data)
    assert not report["ok"]
    assert any("与当前结果不一致" in issue for issue in report["issues"])
    assert report["question_heading_count"] == 1
    assert report["office_math_count"] == 1


def test_other_question_cannot_supply_missing_content():
    data = {"exercises": [{"stem": "甲题完整条件"}, {"stem": "乙题完整条件"}]}
    content = build_practice_question_docx(data)

    def change(root):
        for node in root.iter():
            if node.text == "甲题完整条件":
                node.text = "乙题完整条件"
            elif node.text == "乙题完整条件":
                node.text = "甲题完整条件"

    report = validate_docx_output(_change_xml(content, change), data)
    assert len([issue for issue in report["issues"] if "与当前结果不一致" in issue]) == 2


def test_selected_original_number_and_rich_multiline_content_are_preserved():
    data = {"exercises": [{"_export_original_number": 7,
        "stem": "已知 **重要条件**。\n(1) 计算 $\\frac{a}{b}$。\n(2) 写出结论。",
        "options": [{"text": "A. 保持 **体积** 不变"}],
    }]}
    assert validate_docx_output(build_practice_question_docx(data), data)["ok"]
    revised = deepcopy(data)
    revised["exercises"][0]["stem"] += "新增条件。"
    report = validate_docx_output(build_practice_question_docx(data), revised)
    assert any("第 7 题题干" in issue for issue in report["issues"])


def test_solution_assets_are_not_required_on_question_sheet():
    data = {"exercises": [{"stem": "说明实验步骤。", "tables": [
        {"location": "solution", "headers": ["答案"], "rows": [["内容"]]},
    ], "figures": [{"location": "solution", "image_path": "unused-answer-figure.png"}]}]}
    report = validate_docx_output(build_practice_question_docx(data), data)
    assert report["ok"], report["issues"]
    assert report["expected_table_count"] == report["expected_figure_count"] == 0


def test_solution_requires_explicit_scope_and_checks_answer_content():
    data = {"exercises": [{"stem": "求解方程。", "answer": "$x=2$",
                           "solution_steps": ["移项后求解。"]}]}
    content = build_practice_solution_docx(data)
    assert not validate_docx_output(content, data)["ok"]
    assert validate_docx_output(content, data, document_kind="solutions")["ok"]
    revised = deepcopy(data)
    revised["exercises"][0]["answer"] = "$x=3$"
    report = validate_docx_output(content, revised, document_kind="solutions")
    assert any("参考答案与当前结果不一致" in issue for issue in report["issues"])


def test_option_labels_are_part_of_the_question_contract():
    data = {"exercises": [{"stem": "选择正确叙述。", "options": [{"text": "保持温度"}]}]}
    def change(root):
        for node in root.iter():
            if node.text == "A. ":
                node.text = "B. "
    report = validate_docx_output(_change_xml(build_practice_question_docx(data), change), data)
    assert any("选项 A与当前结果不一致" in issue for issue in report["issues"])
