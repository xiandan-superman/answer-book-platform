from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType


PRACTICE_DOCUMENT_CONTRACT_VERSION = "practice_word.current_compatibility.v1"


@dataclass(frozen=True)
class PracticePageContract:
    width_inches: float
    height_inches: float
    top_bottom_margin_inches: float
    left_right_margin_inches: float
    header_footer_distance_inches: float


@dataclass(frozen=True)
class PracticeTextContract:
    east_asia_font: str
    latin_font: str
    east_asia_fallback_font: str
    math_font: str
    body_size_pt: float
    auxiliary_size_pt: float
    page_number_size_pt: float
    line_spacing: float
    first_line_indent_pt: float
    list_left_indent_pt: float
    list_hanging_indent_pt: float
    table_line_spacing: float


PRACTICE_PAGE_CONTRACT = PracticePageContract(
    width_inches=8.5,
    height_inches=11,
    top_bottom_margin_inches=0.82,
    left_right_margin_inches=1,
    header_footer_distance_inches=0.42,
)

PRACTICE_TEXT_CONTRACT = PracticeTextContract(
    east_asia_font="宋体",
    latin_font="Times New Roman",
    east_asia_fallback_font="SimSun",
    math_font="Cambria Math",
    body_size_pt=11,
    auxiliary_size_pt=9.5,
    page_number_size_pt=9,
    line_spacing=1.5,
    first_line_indent_pt=22,
    list_left_indent_pt=22,
    list_hanging_indent_pt=22,
    table_line_spacing=1.15,
)

PRACTICE_STRUCTURE_CONTRACT = MappingProxyType(
    {
        "questions": ("专项练习题目卷", "练习题", "第 N 题", "题干", "A. / B. 选项"),
        "solutions": ("专项练习", "参考答案与解析", "第 N 题", "参考答案：", "解析编号步骤", "涉及知识点"),
        "combined": ("专项练习", "练习题", "题目", "分页", "参考答案与解析", "答案与解析"),
    }
)

PRACTICE_NUMBERING_CONTRACT = MappingProxyType(
    {
        "question_heading": "第 {number} 题",
        "option": "A. / B. / …",
        "solution_step": "每题独立的 Word List Number（均从 1. / 2. / … 开始）",
        "page": "第 {PAGE} 页",
    }
)
