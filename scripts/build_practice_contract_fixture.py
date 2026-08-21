from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.practice_export import build_practice_question_docx, build_practice_solution_docx, validate_docx_output
from app.render_word import export_docx_to_pdf, render_pdf_to_png


def _fixture() -> dict:
    return {
        "source_analysis": {"subject": "跨学科契约校准", "question_type": "单选题", "difficulty": "进阶"},
        "blueprint": {"training_goal": "校准生题 Word 的结构与样式"},
        "exercises": [
            {
                "number": 1,
                "question_type": "单选题",
                "difficulty": "进阶",
                "stem": r"已知标准态关系 $\Delta G^{\theta}=\Delta H^{\theta}-T\Delta S^{\theta}$，下列判断正确的是？",
                "options": [
                    {"text": "A. 当 ΔGθ<0 时，过程在给定条件下具有自发趋势"},
                    {"text": "B. 当 ΔGθ>0 时，过程必然自发"},
                    {"text": "C. 温度不可能影响 ΔGθ"},
                ],
                "answer": "A。",
                "solution_steps": [
                    "先识别判据所对应的温度、压力等适用条件。",
                    "在给定条件下，ΔGθ<0 表示正向过程具有自发趋势，故选 A。",
                ],
                "knowledge_points": ["热力学判据", "标准态符号"],
                "formulas": [],
                "tables": [],
                "figures": [],
            },
            {
                "number": 2,
                "question_type": "计算题",
                "difficulty": "进阶",
                "stem": "某过程满足一阶动力学，写出由初值 c₀ 计算时刻 t 浓度 c 的关系。",
                "options": [],
                "answer": r"$c=c_0\exp(-kt)$。",
                "solution_steps": [
                    "建立微分关系并分离变量。",
                    "由初值积分，得到浓度随时间指数衰减。",
                ],
                "knowledge_points": ["一阶动力学"],
                "formulas": [
                    {"location": "stem", "latex": r"\frac{dc}{dt}=-kc", "display": True},
                    {"location": "solution", "latex": r"c=c_0\exp(-kt)", "display": True},
                ],
                "tables": [],
                "figures": [],
            },
            {
                "number": 3,
                "question_type": "简答题",
                "difficulty": "基础",
                "stem": "写出氢气燃烧反应 2H₂+O₂→2H₂O，并说明参比电极（Hg₂Cl₂(s)|Hg(l)|KCl(aq)）的竖线含义。",
                "options": [],
                "answer": "反应式配平正确；单竖线表示相界面。",
                "solution_steps": [
                    "按元素守恒检查反应物与生成物的计量系数。",
                    "识别电极表示式中各相及相界面，单竖线用于分隔相界面。",
                ],
                "knowledge_points": ["化学反应计量", "电极表示式"],
                "formulas": [],
                "tables": [],
                "figures": [],
            },
        ],
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--render", action="store_true")
    args = parser.parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)
    data = _fixture()
    for kind, builder in (("questions", build_practice_question_docx), ("solutions", build_practice_solution_docx)):
        content = builder(data)
        report = validate_docx_output(content, data)
        if not report["ok"]:
            raise RuntimeError(f"{kind} fixture failed contract audit: {report['issues']}")
        docx = args.output_dir / f"practice_contract_{kind}.docx"
        docx.write_bytes(content)
        if args.render:
            pdf = args.output_dir / f"practice_contract_{kind}.pdf"
            export_docx_to_pdf(docx, pdf)
            render_pdf_to_png(pdf, args.output_dir / kind, prefix="page")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
