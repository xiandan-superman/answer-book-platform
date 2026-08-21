from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType

DOCUMENT_CONTRACT_VERSION = "answer_book.current_compatibility.v1"
HEADER_TEXT = "航研学考研 丨 专注北航考研 丨 材料考研 954467835"
FOOTER_TEXT = "愿每一位考研学子，以梦为马，不负韶华；披荆斩棘，终达彼岸！"


@dataclass(frozen=True)
class PageContract:
    width_cm: float
    height_cm: float
    margin_cm: float
    header_distance_cm: float
    footer_distance_cm: float


@dataclass(frozen=True)
class TextContract:
    east_asia_font: str
    east_asia_fallback_font: str
    latin_font: str
    body_size_pt: float
    title_size_pt: float
    line_spacing: float
    answer_first_line_indent_cm: float
    note_line_spacing: float


@dataclass(frozen=True)
class HeaderFooterContract:
    header_font: str
    header_fallback_font: str
    header_size_pt: float
    footer_font: str
    footer_fallback_font: str
    footer_size_pt: float


PAGE_CONTRACT = PageContract(
    width_cm=18.2,
    height_cm=25.7,
    margin_cm=1.70,
    header_distance_cm=1.30,
    footer_distance_cm=1.30,
)

TEXT_CONTRACT = TextContract(
    east_asia_font="宋体",
    east_asia_fallback_font="STSong",
    latin_font="Times New Roman",
    body_size_pt=11,
    title_size_pt=16,
    line_spacing=1.5,
    answer_first_line_indent_cm=0.74,
    note_line_spacing=1.25,
)

HEADER_FOOTER_CONTRACT = HeaderFooterContract(
    header_font="黑体",
    header_fallback_font="Heiti SC",
    header_size_pt=12,
    footer_font="华文新魏",
    footer_fallback_font="Weibei SC",
    footer_size_pt=10.5,
)

# These tuples describe the existing product behavior.  They are compatibility
# contracts, not a prompt suggestion: changing them requires an explicit
# document-format migration and matching regression-test updates.
QUESTION_STRUCTURE_CONTRACT = MappingProxyType(
    {
        "名词解释": ("题号+教材依据", "答案标题", "缩进答案正文"),
        "作图题": ("题号+教材依据", "答案标题", "缩进答案正文", "图示", "解析", "易错点及注意事项"),
        "简答题": ("题号+教材依据", "答案标题", "缩进答案正文", "其余答案区块（原顺序）"),
        "计算题": ("题号+教材依据", "解析/小问内容", "图示（归属小问）", "解题步骤", "易错点及注意事项"),
        "其他题型": ("题号、答案", "必要时答案摘要", "其余答案区块（原顺序）"),
    }
)

NUMBERING_CONTRACT = MappingProxyType(
    {
        "question": "{number}、",
        "subquestion": "({number})",
        "requirement_1_to_20": "①、…⑳、",
    }
)
