from __future__ import annotations

from app.content_quality_audit import audit_content_quality


def _fragment() -> dict:
    return {
        "question_id": "q1",
        "answer": "状态函数的改变量只取决于始末状态。",
        "answer_summary": "状态函数与途径无关。",
        "evidence_ids": ["ev1"],
        "blocks": [
            {
                "label": "解析",
                "segments": [
                    {
                        "type": "text",
                        "text": "状态函数是状态的单值函数，其改变量只取决于系统的始态和末态。",
                    }
                ],
            },
            {
                "label": "教材依据",
                "segments": [{"type": "text", "text": "教材原文说明状态函数与途径无关。"}],
            },
        ],
        "formulas": [],
    }


def test_reused_legacy_checkpoint_without_draft_is_diagnostic_not_answer_warning() -> None:
    structured_exam = {
        "items": [
            {
                "question_id": "q1",
                "number": "1",
                "question_type": "简答题",
                "stem": "说明状态函数的特点。",
            }
        ]
    }

    report = audit_content_quality(
        structured_exam,
        {"fragments": [_fragment()]},
        {"drafts": []},
        draft_optional_question_ids={"q1"},
    )

    assert "missing_draft" not in {
        item["code"] for item in [*report["issues"], *report["warnings"]]
    }
    assert report["diagnostic_count"] == 1
    assert report["diagnostics"][0]["code"] == "checkpoint_draft_unavailable"


def test_newly_generated_fragment_without_draft_remains_a_quality_issue() -> None:
    structured_exam = {
        "items": [
            {
                "question_id": "q1",
                "number": "1",
                "question_type": "简答题",
                "stem": "说明状态函数的特点。",
            }
        ]
    }

    report = audit_content_quality(
        structured_exam,
        {"fragments": [_fragment()]},
        {"drafts": []},
    )

    assert "missing_draft" in {item["code"] for item in report["issues"]}


def test_unresolved_high_risk_correctness_flag_is_a_content_issue() -> None:
    structured_exam = {
        "items": [
            {
                "question_id": "q1",
                "number": "1",
                "question_type": "简答题",
                "stem": "说明状态函数的特点。",
            }
        ]
    }
    fragment = _fragment()
    fragment["_review_flags"] = [
        {
            "code": "high_risk_correctness_unresolved",
            "message": "高风险正确性修复未通过。",
        }
    ]

    report = audit_content_quality(
        structured_exam,
        {"fragments": [fragment]},
        {"drafts": [{"question_id": "q1", "answer": fragment["answer"], "analysis": "状态函数与路径无关。"}]},
    )

    assert "high_risk_correctness_unresolved" in {
        item["code"] for item in report["issues"]
    }


def test_xrd_text_is_checked_against_active_final_figure_contract() -> None:
    structured_exam = {
        "items": [
            {
                "question_id": "xrd1",
                "number": "1",
                "question_type": "作图题",
                "stem": "画出体心立方有序化前后的X射线粉末衍射峰。",
            }
        ]
    }
    fragment = {
        "question_id": "xrd1",
        "answer": "见解析。",
        "answer_summary": "基本峰(110)、(321)。",
        "evidence_ids": [],
        "formulas": [],
        "blocks": [
            {
                "label": "解析",
                "segments": [
                    {"type": "text", "text": "峰间距随2θ增大而逐渐增大。"}
                ],
            }
        ],
    }
    draft = {"question_id": "xrd1", "answer": "见解析。", "analysis": "衍射峰相对位置。"}
    active_specs = {
        "figures": [
            {
                "question_id": "xrd1",
                "kind": "xrd_pattern",
                "peaks": [{"label": "(110)", "two_theta": 2}],
            }
        ]
    }

    report = audit_content_quality(
        structured_exam,
        {"fragments": [fragment]},
        {"drafts": [draft]},
        active_figure_specs_data=active_specs,
    )

    codes = {item["code"] for item in report["issues"]}
    assert "xrd_figure_text_label_mismatch" in codes
    assert "xrd_unsupported_peak_spacing_trend" in codes


def test_calculation_steps_can_fulfil_reasoning_role_without_duplicate_analysis_label() -> None:
    structured_exam = {
        "items": [
            {
                "question_id": "calc1",
                "number": "1",
                "question_type": "计算题",
                "stem": "计算一段路程。",
            }
        ]
    }
    formulas = [{"formula_id": "f1", "latex": "s=1+1=2", "role": "result", "display": False}]
    draft = {
        "question_id": "calc1",
        "answer": "2 m",
        "formulas": formulas,
        "steps": [
            {
                "text": "先建立路程的加法关系，再代入两个分段路程。",
                "relation_formula_indices": [1],
                "substitution_formula_indices": [1],
                "result_formula_indices": [1],
                "result_text": "总路程为2 m。",
            }
        ],
        "mistake_notes": ["相加前应统一长度单位。"],
    }
    fragment = {
        "question_id": "calc1",
        "answer": "2 m",
        "answer_summary": "总路程为2 m。",
        "evidence_ids": [],
        "formulas": formulas,
        "blocks": [
            {
                "label": "解题步骤",
                "segments": [
                    {"type": "text", "text": "先建立路程的加法关系，再代入两个分段路程，计算并核对最终结果。"},
                    {"type": "formula_ref", "formula_id": "f1"},
                ],
            },
            {"label": "易错点及注意事项", "segments": [{"type": "text", "text": "相加前应统一长度单位。"}]},
        ],
    }

    report = audit_content_quality(
        structured_exam,
        {"fragments": [fragment]},
        {"drafts": [draft]},
    )

    assert "missing_analysis" not in {item["code"] for item in report["issues"]}
    assert "analysis_satisfied_by_calculation_steps" in {item["code"] for item in report["diagnostics"]}


def test_calculation_steps_do_not_hide_numeric_inconsistency() -> None:
    structured_exam = {
        "items": [{"question_id": "calc1", "number": "1", "question_type": "计算题", "stem": "计算质量。"}]
    }
    formulas = [{"formula_id": "f1", "latex": "m=2+2=5", "role": "result", "display": False}]
    draft = {
        "question_id": "calc1",
        "answer": "5 kg",
        "formulas": formulas,
        "steps": [
            {
                "text": "建立质量加和关系并代入。",
                "relation_formula_indices": [1],
                "substitution_formula_indices": [1],
                "result_formula_indices": [1],
                "result_text": "总质量为5 kg。",
            }
        ],
        "mistake_notes": ["检查加法结果。"],
    }
    fragment = {
        "question_id": "calc1",
        "answer": "5 kg",
        "answer_summary": "总质量为5 kg。",
        "evidence_ids": [],
        "formulas": formulas,
        "blocks": [
            {
                "label": "解题步骤",
                "segments": [
                    {"type": "text", "text": "建立质量加和关系，代入两个分量并检查最终计算结果。"},
                    {"type": "formula_ref", "formula_id": "f1"},
                ],
            },
            {"label": "易错点及注意事项", "segments": [{"type": "text", "text": "检查加法结果。"}]},
        ],
    }

    report = audit_content_quality(
        structured_exam,
        {"fragments": [fragment]},
        {"drafts": [draft]},
    )

    assert "calculation_internal_inconsistency" in {item["code"] for item in report["issues"]}
