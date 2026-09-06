"""Read the same original user requirements at every practice boundary.

Do not summarize, truncate, reinterpret conditional instructions, or synthesize
missing historical requirements here. Context capacity belongs to the existing
request budget, not to a character slice hidden in a stage-specific cleaner.
"""
from __future__ import annotations

from typing import Any


def practice_user_focus(*sources: dict[str, Any]) -> str:
    """Current nonempty request wins; a persisted plan/result is the fallback.

    This retains the existing empty-request recovery behavior. Exact text is
    preserved, including line breaks, so storage and all consumers agree.
    """
    for source in sources:
        value = str(source.get("focus") or "")
        if value.strip():
            return value
    return ""


PRACTICE_REQUIREMENT_PRIORITY = """用户原始要求优先于平台默认的变式、多样性与难度设计建议。
- 必须输出requirements_contract，逐项区分用户原文与平台默认；“不改变或者不要过多改变”允许少量改写，不能解释为严格逐字。引用存在只证明引用真实，仍须核对解释是否遗漏或改变要求。
- 严格原句填空必须输出cloze_source并从source_content原文选句/空位；不得拿摘要、识图改写或required_constraints定义当作原句。若来源不支持，明确报告冲突，不能声称符合。
- 用户要求原句挖空时，填空题应从绑定材料中选取完整、可独立作答的原句，仅替换被考查的内容为空格；不强制改情境、认知操作或公式链，不把简答指令句改作填空。
- 明确的字数限制作用于包含全部小问的完整题干；规划时选择能在限制内完整表达的考查目标，不先堆叠多问再删掉必要条件。
- 综合模式在整套内分配必考知识点，每题只绑定本题实际考查的知识点及相关约束；无相关公式时显式返回空列表，不把来源的所有公式当成本题必考内容。
- 来源的适用边界必须遵守，但不必每题逐字复述无关定义。若已确认的范围、题型、难度与用户要求确实不可兼容，明确报告具体冲突，不宣称满足，也不静默删改已确认要求。"""
