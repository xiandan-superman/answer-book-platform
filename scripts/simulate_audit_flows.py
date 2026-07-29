from __future__ import annotations

import copy
import json
import shutil
import sys
import tempfile
import time
import zipfile
from pathlib import Path
from typing import Any

from docx import Document

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.audit_review_gate import auto_allow_audit_report
from app.content_quality_audit import audit_content_quality
from app.docx_audit import audit_docx_v4
from app.docx_v4 import build_docx_from_fragments
from app.final_acceptance import audit_ok
from app.fragment_repair import repair_answer_fragments_for_docx
from app.pipeline import (
    CONTENT_QUALITY_MODEL_REPAIR_CODES,
    build_and_audit_docx_with_repair,
    build_user_allowed_docx_candidate,
    build_user_allowed_docx_placeholder,
    _docx_issue_code,
    _filter_audit_report_for_model_repair,
    _filter_docx_issues_for_model_repair,
)


REPORT_PATH = ROOT / "docs" / "audit_flow_simulation_20260705.md"
WORK_ROOT = ROOT / "tmp" / "audit_flow_simulation_20260705"


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def base_question(qid: str, *, section: str = "简答题", stem: str = "说明本题的核心判断依据。") -> dict[str, Any]:
    return {
        "question_id": qid,
        "section": section,
        "question_type": section,
        "number": qid.replace("q_", ""),
        "stem": stem,
    }


def text_seg(text: str) -> dict[str, str]:
    return {"type": "text", "text": text}


def formula_seg(fid: str) -> dict[str, str]:
    return {"type": "formula_ref", "formula_id": fid}


def image_seg(qid: str) -> dict[str, str]:
    return {"type": "image_ref", "image_id": f"{qid}_fig_01", "path": "figures/missing.png"}


def base_fragment(
    qid: str,
    *,
    section: str = "简答题",
    answer: str = "正确结论",
    analysis: str = "本题先识别题干给出的关键条件，再用对应概念判断适用范围，最后得到结论。",
    evidence_ids: list[str] | None = None,
    formulas: list[dict[str, Any]] | None = None,
    blocks: list[dict[str, Any]] | None = None,
    answer_summary: str = "正确结论",
    warnings: list[str] | None = None,
    review_flags: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    return {
        "schema_version": "answer_book.answer_fragment.v4",
        "question_id": qid,
        "section": section,
        "number": qid.replace("q_", ""),
        "answer": answer,
        "answer_summary": answer_summary,
        "evidence_ids": evidence_ids if evidence_ids is not None else ["ev_ok"],
        "formulas": formulas if formulas is not None else [],
        "blocks": blocks
        if blocks is not None
        else [{"label": "解析", "segments": [text_seg(analysis)]}],
        "warnings": warnings or [],
        "_review_flags": review_flags or [],
    }


def base_draft(
    qid: str,
    *,
    answer: str = "正确结论",
    analysis: str = "本题先识别题干给出的关键条件，再用对应概念判断适用范围，最后得到结论。",
    formulas: list[dict[str, Any]] | None = None,
    steps: list[dict[str, Any]] | None = None,
    mistake_notes: list[str] | None = None,
    option_analysis: dict[str, str] | None = None,
) -> dict[str, Any]:
    return {
        "question_id": qid,
        "answer": answer,
        "analysis": analysis,
        "formulas": formulas if formulas is not None else [],
        "steps": steps if steps is not None else [],
        "mistake_notes": mistake_notes if mistake_notes is not None else [],
        "option_analysis": option_analysis if option_analysis is not None else {},
    }


def base_selection(qid: str, *, selected: list[str] | None = None, rejected: list[str] | None = None) -> dict[str, Any]:
    return {
        "question_id": qid,
        "knowledge_points": [
            {
                "knowledge_point": "模拟考点",
                "selected_evidence_ids": selected if selected is not None else ["ev_ok"],
                "rejected_evidence_ids": rejected if rejected is not None else [],
            }
        ],
    }


def calc_fixture(qid: str) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any], dict[str, Any]]:
    formulas = [
        {"formula_id": "f1", "latex": "n=m/M", "display": True, "role": "relation"},
        {"formula_id": "f2", "latex": "n=10/2", "display": True, "role": "substitution"},
        {"formula_id": "f3", "latex": "n=5\\ \\mathrm{mol}", "display": True, "role": "result"},
    ]
    steps = [
        {
            "text": "由物质的量定义计算样品物质的量。",
            "relation_formula_indices": [1],
            "substitution_formula_indices": [2],
            "result_formula_indices": [3],
            "result_text": "样品物质的量为 5 mol。",
        }
    ]
    question = base_question(qid, section="计算题", stem="计算样品物质的量。")
    fragment = base_fragment(
        qid,
        section="计算题",
        answer="见解析",
        answer_summary="样品物质的量为 5 mol",
        formulas=formulas,
        blocks=[
            {"label": "解析", "segments": [text_seg("本题先确定物理量关系，再代入题干数值并检查单位。")]},
            {
                "label": "解题步骤",
                "segments": [
                    text_seg("由物质的量定义计算样品物质的量。"),
                    formula_seg("f1"),
                    text_seg("带入数值："),
                    formula_seg("f2"),
                    text_seg("求得："),
                    formula_seg("f3"),
                    text_seg("样品物质的量为 5 mol。"),
                ],
            },
            {"label": "易错点及注意事项", "segments": [text_seg("注意质量与摩尔质量单位要先统一。")]},
        ],
    )
    draft = base_draft(qid, answer="见解析", formulas=formulas, steps=steps, mistake_notes=["注意质量与摩尔质量单位要先统一。"])
    return question, fragment, draft, base_selection(qid)


def content_scenarios() -> list[dict[str, Any]]:
    scenarios: list[dict[str, Any]] = []

    qid = "q_missing_fragment"
    scenarios.append(
        {
            "target_code": "missing_fragment",
            "expect_kind": "issues",
            "questions": [base_question(qid)],
            "fragments": [],
            "drafts": [base_draft(qid)],
            "selections": [base_selection(qid)],
        }
    )

    qid = "q_missing_draft"
    scenarios.append(
        {
            "target_code": "missing_draft",
            "expect_kind": "issues",
            "questions": [base_question(qid)],
            "fragments": [base_fragment(qid)],
            "drafts": [],
            "selections": [base_selection(qid)],
        }
    )

    qid = "q_missing_answer"
    scenarios.append(
        {
            "target_code": "missing_answer",
            "expect_kind": "issues",
            "questions": [base_question(qid)],
            "fragments": [base_fragment(qid, answer="待复核")],
            "drafts": [base_draft(qid, answer="待复核")],
            "selections": [base_selection(qid)],
        }
    )

    qid = "q_missing_analysis"
    scenarios.append(
        {
            "target_code": "missing_analysis",
            "expect_kind": "issues",
            "questions": [base_question(qid)],
            "fragments": [base_fragment(qid, blocks=[])],
            "drafts": [base_draft(qid)],
            "selections": [base_selection(qid)],
        }
    )

    qid = "q_short_analysis"
    scenarios.append(
        {
            "target_code": "short_analysis",
            "expect_kind": "warnings",
            "questions": [base_question(qid)],
            "fragments": [base_fragment(qid, analysis="过短。")],
            "drafts": [base_draft(qid)],
            "selections": [base_selection(qid)],
        }
    )

    qid = "q_forbidden_process"
    scenarios.append(
        {
            "target_code": "forbidden_process_text",
            "expect_kind": "issues",
            "questions": [base_question(qid)],
            "fragments": [base_fragment(qid, analysis="本题需要人工复核，后续再确认答案是否完整。")],
            "drafts": [base_draft(qid, analysis="本题需要人工复核。")],
            "selections": [base_selection(qid)],
        }
    )

    qid = "q_generic_phrase"
    scenarios.append(
        {
            "target_code": "generic_analysis_phrase",
            "expect_kind": "warnings",
            "questions": [base_question(qid)],
            "fragments": [base_fragment(qid, analysis="根据教材可知，本题应先识别关键条件，再判断适用概念，最后得到结论。")],
            "drafts": [base_draft(qid)],
            "selections": [base_selection(qid)],
        }
    )

    qid = "q_formula_placeholder"
    scenarios.append(
        {
            "target_code": "unresolved_formula_placeholder",
            "expect_kind": "issues",
            "questions": [base_question(qid)],
            "fragments": [base_fragment(qid, analysis="本题使用 {f1} 判断符号关系，并据此得到结论。")],
            "drafts": [base_draft(qid)],
            "selections": [base_selection(qid)],
        }
    )

    qid = "q_citation_leak"
    scenarios.append(
        {
            "target_code": "citation_leaked_into_answer",
            "expect_kind": "issues",
            "questions": [base_question(qid)],
            "fragments": [base_fragment(qid, analysis="教材依据：课本-p12 说明该概念，因此得到本题结论。")],
            "drafts": [base_draft(qid)],
            "selections": [base_selection(qid)],
        }
    )

    qid = "q_missing_confirmed_evidence"
    scenarios.append(
        {
            "target_code": "missing_confirmed_evidence",
            "expect_kind": "issues",
            "questions": [base_question(qid)],
            "fragments": [base_fragment(qid, evidence_ids=["ev_other"])],
            "drafts": [base_draft(qid)],
            "selections": [base_selection(qid, selected=["ev_ok"])],
        }
    )

    qid = "q_uses_rejected"
    scenarios.append(
        {
            "target_code": "uses_rejected_evidence",
            "expect_kind": "issues",
            "questions": [base_question(qid)],
            "fragments": [base_fragment(qid, evidence_ids=["ev_bad"])],
            "drafts": [base_draft(qid)],
            "selections": [base_selection(qid, selected=[], rejected=["ev_bad"])],
        }
    )

    qid = "q_choice_no_option"
    scenarios.append(
        {
            "target_code": "choice_missing_option_analysis",
            "expect_kind": "issues",
            "questions": [base_question(qid, section="选择题", stem="下列说法正确的是（ ）。")],
            "fragments": [base_fragment(qid, section="选择题", answer="A")],
            "drafts": [base_draft(qid, answer="A")],
            "selections": [base_selection(qid)],
        }
    )

    q, f, d, s = calc_fixture("q_calc_no_summary")
    f["answer_summary"] = ""
    d["answer"] = ""
    scenarios.append({"target_code": "missing_answer_summary", "expect_kind": "issues", "questions": [q], "fragments": [f], "drafts": [d], "selections": [s]})

    q, f, d, s = calc_fixture("q_calc_no_formula")
    f["formulas"] = []
    d["formulas"] = []
    scenarios.append({"target_code": "calculation_missing_formula", "expect_kind": "issues", "questions": [q], "fragments": [f], "drafts": [d], "selections": [s]})

    q, f, d, s = calc_fixture("q_formula_absence_allowed")
    f["formulas"] = []
    d["formulas"] = []
    f["_review_flags"] = [{"code": "formula_absence_after_retry", "message": "模型二次生成仍未给出公式。"}]
    scenarios.append({"target_code": "formula_absence_after_retry", "expect_kind": "warnings", "questions": [q], "fragments": [f], "drafts": [d], "selections": [s]})

    q, f, d, s = calc_fixture("q_calc_no_steps")
    f["blocks"] = [block for block in f["blocks"] if block["label"] != "解题步骤"]
    d["steps"] = []
    scenarios.append({"target_code": "calculation_missing_steps", "expect_kind": "issues", "questions": [q], "fragments": [f], "drafts": [d], "selections": [s]})

    q, f, d, s = calc_fixture("q_calc_steps_no_formula_refs")
    for block in f["blocks"]:
        if block["label"] == "解题步骤":
            block["segments"] = [text_seg("由物质的量定义列式、代入并求出结果。")]
    scenarios.append({"target_code": "calculation_steps_missing_formula_refs", "expect_kind": "issues", "questions": [q], "fragments": [f], "drafts": [d], "selections": [s]})

    q, f, d, s = calc_fixture("q_calc_formula_dumped_analysis")
    f["blocks"] = [
        {
            "label": "解析",
            "segments": [
                text_seg("本题把公式集中写在解析里。"),
                formula_seg("f1"),
                formula_seg("f2"),
                formula_seg("f3"),
            ],
        },
        {"label": "解题步骤", "segments": [text_seg("由定义完成计算。")]},
        {"label": "易错点及注意事项", "segments": [text_seg("注意单位统一。")]},
    ]
    scenarios.append({"target_code": "calculation_formula_dumped_in_analysis", "expect_kind": "issues", "questions": [q], "fragments": [f], "drafts": [d], "selections": [s]})

    q, f, d, s = calc_fixture("q_calc_formula_dumped_steps")
    for block in f["blocks"]:
        if block["label"] == "解题步骤":
            block["segments"] = [text_seg("关系式与代入：n=m/M，n=10/2=5 mol。")]
    scenarios.append({"target_code": "calculation_formula_dumped_in_steps", "expect_kind": "issues", "questions": [q], "fragments": [f], "drafts": [d], "selections": [s]})

    q, f, d, s = calc_fixture("q_calc_no_substitution")
    d["steps"][0].pop("substitution_formula_indices", None)
    scenarios.append({"target_code": "calculation_missing_substitution", "expect_kind": "issues", "questions": [q], "fragments": [f], "drafts": [d], "selections": [s]})

    q, f, d, s = calc_fixture("q_calc_not_sequential")
    d["steps"][0].pop("relation_formula_indices", None)
    d["steps"][0].pop("formula_indices", None)
    scenarios.append({"target_code": "calculation_steps_not_sequential", "expect_kind": "issues", "questions": [q], "fragments": [f], "drafts": [d], "selections": [s]})

    q, f, d, s = calc_fixture("q_calc_no_mistake")
    f["blocks"] = [block for block in f["blocks"] if block["label"] != "易错点及注意事项"]
    d["mistake_notes"] = []
    scenarios.append({"target_code": "calculation_missing_mistake_notes", "expect_kind": "issues", "questions": [q], "fragments": [f], "drafts": [d], "selections": [s]})

    q, f, d, s = calc_fixture("q_calc_answer_no_unit")
    f["answer"] = "5"
    f["answer_summary"] = "5"
    d["answer"] = "5"
    scenarios.append({"target_code": "calculation_answer_missing_unit", "expect_kind": "warnings", "questions": [q], "fragments": [f], "drafts": [d], "selections": [s]})

    qid = "q_noncalc_summary"
    scenarios.append(
        {
            "target_code": "missing_answer_summary",
            "expect_kind": "warnings",
            "questions": [base_question(qid)],
            "fragments": [base_fragment(qid, answer="见解析", answer_summary="")],
            "drafts": [base_draft(qid, answer="")],
            "selections": [base_selection(qid)],
        }
    )

    qid = "q_noncalc_unintegrated_formula"
    scenarios.append(
        {
            "target_code": "noncalculation_unintegrated_formulas",
            "expect_kind": "warnings",
            "questions": [base_question(qid)],
            "fragments": [
                base_fragment(
                    qid,
                    blocks=[
                        {"label": "解析", "segments": [text_seg("本题依据判据判断，结论如答案。")]},
                        {"label": "待复核公式", "segments": [text_seg("公式未自然融入正文。")]},
                    ],
                )
            ],
            "drafts": [base_draft(qid)],
            "selections": [base_selection(qid)],
        }
    )

    qid = "q_missing_figure"
    scenarios.append(
        {
            "target_code": "missing_required_figure",
            "expect_kind": "issues",
            "questions": [base_question(qid, section="作图题", stem="请绘制晶胞示意图并标出关键位置。")],
            "fragments": [base_fragment(qid, section="作图题")],
            "drafts": [base_draft(qid)],
            "selections": [base_selection(qid)],
        }
    )

    qid1, qid2 = "q_dup_note_1", "q_dup_note_2"
    repeated = "注意审题并区分相近概念。"
    scenarios.append(
        {
            "target_code": "duplicated_mistake_note",
            "expect_kind": "warnings",
            "questions": [base_question(qid1), base_question(qid2)],
            "fragments": [
                base_fragment(qid1, blocks=[{"label": "解析", "segments": [text_seg("本题通过概念边界判断，答案与题干条件一致。")]}, {"label": "易错点及注意事项", "segments": [text_seg(repeated)]}]),
                base_fragment(qid2, blocks=[{"label": "解析", "segments": [text_seg("本题通过概念边界判断，答案与题干条件一致。")]}, {"label": "易错点及注意事项", "segments": [text_seg(repeated)]}]),
            ],
            "drafts": [base_draft(qid1), base_draft(qid2)],
            "selections": [base_selection(qid1), base_selection(qid2)],
        }
    )

    return scenarios


def run_content_scenario(case: dict[str, Any], case_dir: Path) -> dict[str, Any]:
    fragments_path = case_dir / "answer_fragments.json"
    report_path = case_dir / "content_quality_audit.json"
    fragments_data = {"fragments": copy.deepcopy(case["fragments"])}
    drafts_data = {"drafts": copy.deepcopy(case["drafts"])}
    selection_data = {"selections": copy.deepcopy(case["selections"])}
    structured_exam = {"items": copy.deepcopy(case["questions"])}
    write_json(fragments_path, fragments_data)

    report = audit_content_quality(structured_exam, fragments_data, drafts_data, selection_data, report_path)
    target_code = case["target_code"]
    kind = case["expect_kind"]
    bucket = report.get(kind, [])
    target_hits = [item for item in bucket if isinstance(item, dict) and item.get("code") == target_code]
    all_codes = sorted(
        {
            str(item.get("code"))
            for item in report.get("issues", []) + report.get("warnings", [])
            if isinstance(item, dict) and item.get("code")
        }
    )
    repair_targets = _filter_audit_report_for_model_repair(report, CONTENT_QUALITY_MODEL_REPAIR_CODES)

    local_repair = {"ok": None, "changed": False, "skipped": True}
    after_local = report
    if case["fragments"]:
        local_repair = repair_answer_fragments_for_docx(fragments_path, case_dir / "answer_fragments.before_content_quality_local_repair.json")
        repaired_fragments = json.loads(fragments_path.read_text(encoding="utf-8"))
        after_local = audit_content_quality(structured_exam, repaired_fragments, drafts_data, selection_data, case_dir / "content_quality_after_local_repair.json")

    final_report = after_local
    if not after_local.get("ok"):
        final_report = auto_allow_audit_report(
            case_dir,
            "content_quality",
            after_local,
            title="质量审查仍有问题",
            output_json=case_dir / "content_quality_after_auto_allow.json",
        )

    docx_path = case_dir / "answer_book_from_current_fragments.docx"
    docx_generation = {"attempted": bool(case["fragments"]), "exists": False, "error": ""}
    if case["fragments"]:
        try:
            build_docx_from_fragments(fragments_path, docx_path)
            docx_generation["exists"] = docx_path.exists()
        except Exception as exc:
            docx_generation["error"] = str(exc)

    gate_ok, gate_issues, gate_warnings = audit_ok("content_quality", final_report, require_render=False)
    return {
        "target_code": target_code,
        "expected_bucket": kind,
        "detected": bool(target_hits),
        "initial_ok": report.get("ok"),
        "initial_issue_count": report.get("issue_count"),
        "initial_warning_count": report.get("warning_count"),
        "all_detected_codes": all_codes,
        "model_repair_target": bool(repair_targets.get("issues") or repair_targets.get("warnings")),
        "model_repair_codes": sorted({item.get("code") for item in repair_targets.get("issues", []) + repair_targets.get("warnings", []) if isinstance(item, dict)}),
        "local_repair": local_repair,
        "after_local_ok": after_local.get("ok"),
        "final_auto_allowed": bool(final_report.get("auto_allowed")),
        "final_ok": final_report.get("ok"),
        "final_issue_count": final_report.get("issue_count"),
        "final_warning_count": final_report.get("warning_count"),
        "final_acceptance_gate_ok": gate_ok,
        "final_acceptance_gate_issue_count": len(gate_issues),
        "final_acceptance_gate_warning_count": len(gate_warnings),
        "docx_generation": docx_generation,
        "case_dir": str(case_dir),
    }


def create_docx_with_document_xml(path: Path, body_xml: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        base = tmp_path / "base.docx"
        Document().save(base)
        unpacked = tmp_path / "unzipped"
        with zipfile.ZipFile(base) as zf:
            zf.extractall(unpacked)
        document_xml = f'''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"
  xmlns:m="http://schemas.openxmlformats.org/officeDocument/2006/math">
  <w:body>
    {body_xml}
    <w:sectPr/>
  </w:body>
</w:document>'''
        (unpacked / "word" / "document.xml").write_text(document_xml, encoding="utf-8")
        with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
            for file in unpacked.rglob("*"):
                if file.is_file():
                    zf.write(file, file.relative_to(unpacked).as_posix())
    return path


def docx_audit_cases() -> list[dict[str, Any]]:
    p = lambda text: f"<w:p><w:r><w:t>{text}</w:t></w:r></w:p>"
    math = lambda text: f"<w:p><m:oMath><m:r><m:t>{text}</m:t></m:r></m:oMath></w:p>"
    math_italic = lambda text: f'<w:p><m:oMath><m:r><m:rPr><m:sty m:val="i"/></m:rPr><m:t>{text}</m:t></m:r></m:oMath></w:p>'
    return [
        {
            "target": "omml_formula_count_below_expected",
            "body": p("普通段落"),
            "min_formulas": 1,
            "expect_substring": "OMML formula count 0 below expected minimum 1",
        },
        {
            "target": "math_object_empty",
            "body": math(""),
            "min_formulas": 0,
            "expect_substring": "math object 1 is empty",
        },
        {
            "target": "math_object_raw_latex",
            "body": math("\\frac{x}{y}"),
            "min_formulas": 0,
            "expect_substring": "math object 1 contains raw latex marker",
        },
        {
            "target": "math_run_not_italic",
            "body": math("x"),
            "min_formulas": 0,
            "expect_substring": "math object 1 run 1 is not italic",
        },
        {
            "target": "paragraph_formula_placeholder",
            "body": p("这里残留 {f1} 占位符"),
            "min_formulas": 0,
            "expect_substring": "unresolved formula placeholder",
        },
        {
            "target": "paragraph_raw_latex_marker",
            "body": p("\\frac{x}{y}"),
            "min_formulas": 0,
            "expect_substring": "raw latex marker",
        },
        {
            "target": "paragraph_raw_latex_word",
            "body": p("alpha beta"),
            "min_formulas": 0,
            "expect_substring": "raw latex command word",
        },
        {
            "target": "paragraph_raw_radical",
            "body": p("结果为 √x"),
            "min_formulas": 0,
            "expect_substring": "raw radical",
        },
        {
            "target": "paragraph_raw_subscript",
            "body": p("成分为 C_0"),
            "min_formulas": 0,
            "expect_substring": "raw subscript marker",
        },
        {
            "target": "valid_italic_math_control",
            "body": math_italic("x"),
            "min_formulas": 1,
            "expect_substring": "",
        },
    ]


def run_docx_audit_case(case: dict[str, Any], case_dir: Path) -> dict[str, Any]:
    docx = create_docx_with_document_xml(case_dir / f"{case['target']}.docx", case["body"])
    issues = audit_docx_v4(docx, min_formulas=case["min_formulas"])
    detected = not case["expect_substring"] or any(case["expect_substring"] in issue for issue in issues)
    codes = sorted({_docx_issue_code(issue) for issue in issues})
    return {
        "target": case["target"],
        "detected": detected,
        "issue_count": len(issues),
        "issues": issues,
        "classified_codes": codes,
        "model_repair_eligible_issues": _filter_docx_issues_for_model_repair(issues),
        "docx": str(docx),
    }


def pipeline_mark_collector(path: Path):
    rows: list[dict[str, Any]] = []

    def mark(stage: str, status: str, detail: Any = None) -> None:
        rows.append({"stage": stage, "status": status, "detail": detail or {}})
        write_json(path, {"stages": rows})

    return mark, rows


def run_docx_flow_cases(flow_dir: Path) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []

    # A normal-text formula issue that the local fragment repair can convert into formula_ref.
    qid = "q_docx_local_repair"
    fragment = base_fragment(
        qid,
        analysis="本题成分记为 C_0，再根据定义判断结论。",
        blocks=[{"label": "解析", "segments": [text_seg("本题成分记为 C_0，再根据定义判断结论。")]}],
    )
    fragments_path = flow_dir / "local_repair" / "answer_fragments.json"
    write_json(fragments_path, {"fragments": [fragment]})
    mark, marks = pipeline_mark_collector(flow_dir / "local_repair" / "pipeline_status.json")
    docx_path = flow_dir / "local_repair" / "answer_book.docx"
    result = build_and_audit_docx_with_repair("audit_flow_simulation", fragments_path, docx_path, flow_dir / "local_repair", mark, use_model=False)
    results.append(
        {
            "case": "docx_local_repair",
            "description": "DOCX 审核发现普通文本中的下标公式，模型回修不可用时进入程序自修。",
            "result_ok": result.get("ok"),
            "final_docx_exists": docx_path.exists(),
            "issues": result.get("issues", []),
            "repair": result.get("repair", {}),
            "marks": marks,
            "fallback_placeholder": False,
        }
    )

    # A build failure caused by a missing formula reference. Auto-allow then candidate also fails, so placeholder is used.
    qid = "q_docx_placeholder"
    bad_fragment = base_fragment(
        qid,
        blocks=[{"label": "解析", "segments": [text_seg("本题缺少公式对象。"), formula_seg("missing_formula")]}],
    )
    bad_dir = flow_dir / "placeholder"
    bad_fragments = bad_dir / "answer_fragments.json"
    write_json(bad_fragments, {"fragments": [bad_fragment]})
    mark, marks = pipeline_mark_collector(bad_dir / "pipeline_status.json")
    bad_docx = bad_dir / "answer_book.docx"
    result = build_and_audit_docx_with_repair("audit_flow_simulation", bad_fragments, bad_docx, bad_dir, mark, use_model=False)
    docx_report = {"ok": not result.get("issues"), "issues": result.get("issues", []), "warnings": []}
    allowed = auto_allow_audit_report(bad_dir, "docx", docx_report, title="DOCX 审计仍未通过", output_json=bad_dir / "docx_audit.json")
    candidate = None
    placeholder = None
    if not bad_docx.exists() and allowed.get("auto_allowed"):
        candidate = build_user_allowed_docx_candidate(bad_fragments, bad_docx, bad_dir, "模拟 DOCX 审计自动放行后正式候选版重建。")
        marks.append({"stage": "docx_user_allowed_candidate", "status": "applied" if candidate.get("ok") else "failed", "detail": candidate})
        if not candidate.get("ok"):
            placeholder = build_user_allowed_docx_placeholder(bad_fragments, bad_docx, bad_dir, "模拟正式总版 Word 重建仍失败。")
            marks.append({"stage": "docx_placeholder", "status": "applied", "detail": placeholder})
    gate_ok, gate_issues, gate_warnings = audit_ok("docx", allowed, require_render=False)
    results.append(
        {
            "case": "docx_placeholder_after_candidate_failure",
            "description": "DOCX 构建失败且自动放行后，正式候选版仍失败，最终生成待复核占位版。",
            "initial_result_ok": result.get("ok"),
            "auto_allowed": bool(allowed.get("auto_allowed")),
            "candidate": candidate,
            "placeholder": placeholder,
            "final_docx_exists": bad_docx.exists(),
            "final_acceptance_gate_ok": gate_ok,
            "final_acceptance_gate_issue_count": len(gate_issues),
            "final_acceptance_gate_warning_count": len(gate_warnings),
            "marks": marks,
            "fallback_placeholder": bool(placeholder and placeholder.get("ok")),
        }
    )

    # Directly exercise candidate regeneration success when the current fragments are valid.
    qid = "q_docx_candidate"
    good_dir = flow_dir / "candidate_success"
    good_fragments = good_dir / "answer_fragments.json"
    write_json(good_fragments, {"fragments": [base_fragment(qid)]})
    good_docx = good_dir / "answer_book.docx"
    candidate = build_user_allowed_docx_candidate(good_fragments, good_docx, good_dir, "模拟自动放行后正式候选版重建。")
    results.append(
        {
            "case": "docx_user_allowed_candidate_success",
            "description": "DOCX 已被允许继续但目标文件缺失时，使用当前完整解析内容重建正式候选版。",
            "candidate": candidate,
            "final_docx_exists": good_docx.exists(),
            "fallback_placeholder": False,
        }
    )

    return results


def render_report(content_results: list[dict[str, Any]], docx_audit_results: list[dict[str, Any]], docx_flow_results: list[dict[str, Any]]) -> str:
    now = time.strftime("%Y-%m-%d %H:%M:%S")
    lines: list[str] = [
        "# 审查问题模拟流程测试报告",
        "",
        f"- 生成时间：{now}",
        f"- 工作目录：`{ROOT}`",
        f"- 模拟输出目录：`{WORK_ROOT}`",
        f"- 内容质量审查模拟项：{len(content_results)}",
        f"- DOCX 审核原子问题模拟项：{len(docx_audit_results)}",
        f"- DOCX 后续流程模拟项：{len(docx_flow_results)}",
        "",
        "## 结论",
        "",
        "1. 内容质量审查的 issue 不会直接阻止最终文件继续生成；模型回修和程序自修后仍存在的问题会被 `auto_allow_audit_report` 转为 warning，并写入审查记录。",
        "2. 内容质量审查放行后，最终 Word 通常仍按当前 `answer_fragments.json` 生成；这不是占位低质量版，但对应题目可能存在缺图、缺解析、缺公式或引用异常等质量风险。",
        "3. DOCX 审核失败后会先尝试模型回修或程序自修；如果仍失败并被允许继续，只要 `answer_book.docx` 已存在，就沿用当前可生成文件并保留审查 warning。",
        "4. 如果 DOCX 被允许继续但 `answer_book.docx` 不存在，系统先重建正式候选版；候选版仍失败时才生成“待复核版”占位 Word，这是低质量兜底版本。",
        "",
        "## 内容质量审查逐项模拟",
        "",
        "| 目标问题 | 触发 | 初始 issue/warning | 模型回修目标 | 程序自修后通过 | 自动放行 | 最终 gate | DOCX 可生成 | 观察到的代码 |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---|",
    ]
    for row in content_results:
        initial_count = row["initial_issue_count"] if row["expected_bucket"] == "issues" else row["initial_warning_count"]
        lines.append(
            "| {code} | {detected} | {count} | {model} | {local_ok} | {auto} | {gate} | {docx} | `{codes}` |".format(
                code=row["target_code"],
                detected="是" if row["detected"] else "否",
                count=initial_count,
                model="是" if row["model_repair_target"] else "否",
                local_ok="是" if row["after_local_ok"] else "否",
                auto="是" if row["final_auto_allowed"] else "否",
                gate="通过" if row["final_acceptance_gate_ok"] else "失败",
                docx="是" if row["docx_generation"].get("exists") else ("未尝试" if not row["docx_generation"].get("attempted") else "否"),
                codes=", ".join(row["all_detected_codes"]),
            )
        )

    missing_content = [row["target_code"] for row in content_results if not row["detected"]]
    lines.extend(
        [
            "",
            "### 内容质量审查覆盖结果",
            "",
            f"- 未触发目标代码：{', '.join(missing_content) if missing_content else '无'}",
            f"- 模型回修白名单：`{', '.join(sorted(CONTENT_QUALITY_MODEL_REPAIR_CODES))}`",
            "- 非白名单 issue 会跳过模型回修，直接进入程序自修；程序自修主要处理公式占位/文本公式对象化，不会补全真实答案质量。",
            "- 自动放行不是修复内容，而是把阻断性 issue 记录为 warning，供最终审查报告和存疑题目文档提示。",
            "",
            "## DOCX 审核原子问题模拟",
            "",
            "| 目标问题 | 触发 | issue 数 | 分类代码 | 可模型回修 issue |",
            "|---|---:|---:|---|---|",
        ]
    )
    for row in docx_audit_results:
        lines.append(
            "| {target} | {detected} | {count} | `{codes}` | `{eligible}` |".format(
                target=row["target"],
                detected="是" if row["detected"] else "否",
                count=row["issue_count"],
                codes=", ".join(row["classified_codes"]),
                eligible="; ".join(row["model_repair_eligible_issues"]),
            )
        )
    missing_docx = [row["target"] for row in docx_audit_results if not row["detected"]]
    lines.extend(
        [
            "",
            "### DOCX 审核覆盖结果",
            "",
            f"- 未触发目标问题：{', '.join(missing_docx) if missing_docx else '无'}",
            "- 当前 DOCX 模型回修白名单只有 `formula_like_normal_text`；raw LaTeX、下标、根号、OMML 数量不足等会走程序自修或自动放行。",
            "",
            "## DOCX 后续流程模拟",
            "",
            "| 场景 | 结果 | 最终 Word | 是否占位低质量版 | 关键后续流程 |",
            "|---|---|---:|---:|---|",
        ]
    )
    for row in docx_flow_results:
        if row["case"] == "docx_local_repair":
            flow = "initial 审核失败 -> docx_model_repair skipped -> docx_repair applied -> after_repair 重新审核"
            result = "通过" if row["result_ok"] else "仍失败"
        elif row["case"] == "docx_placeholder_after_candidate_failure":
            flow = "initial 构建失败 -> docx_repair skipped -> auto_allow -> candidate failed -> placeholder applied"
            result = "占位兜底" if row["fallback_placeholder"] else "未兜底"
        else:
            flow = "auto_allow 后发现 docx 缺失 -> user_allowed_candidate applied"
            result = "候选版生成"
        lines.append(
            f"| {row['case']} | {result} | {'是' if row.get('final_docx_exists') else '否'} | {'是' if row.get('fallback_placeholder') else '否'} | {flow} |"
        )

    lines.extend(
        [
            "",
            "## 对最终文件的影响判断",
            "",
            "- 内容质量 issue：默认最终仍可生成正式 `answer_book.docx`，但质量风险会转为 warning；不会自动生成低质量占位版。",
            "- DOCX 审核 issue 且 `answer_book.docx` 已存在：最终文件继续存在，风险写入 `docx_audit.json` 和最终审查报告；不会生成占位版。",
            "- DOCX 构建/审核后 `answer_book.docx` 不存在：先尝试 `docx_user_allowed_candidate` 重建正式候选版；只有候选版失败，才生成 `docx_placeholder` 待复核版。",
            "- 待复核占位版的内容只保留题号、答案、warning 和 review flag，不包含完整解析排版，因此属于低质量兜底交付物。",
            "",
            "## 机器可复查产物",
            "",
            f"- 内容质量逐项 JSON：`{WORK_ROOT / 'content_results.json'}`",
            f"- DOCX 审核逐项 JSON：`{WORK_ROOT / 'docx_audit_results.json'}`",
            f"- DOCX 流程 JSON：`{WORK_ROOT / 'docx_flow_results.json'}`",
        ]
    )
    return "\n".join(lines) + "\n"


def main() -> int:
    if WORK_ROOT.exists():
        shutil.rmtree(WORK_ROOT)
    WORK_ROOT.mkdir(parents=True, exist_ok=True)

    content_results = []
    for index, case in enumerate(content_scenarios(), start=1):
        case_dir = WORK_ROOT / "content_quality" / f"{index:02d}_{case['target_code']}"
        content_results.append(run_content_scenario(case, case_dir))

    docx_audit_results = []
    for index, case in enumerate(docx_audit_cases(), start=1):
        case_dir = WORK_ROOT / "docx_audit" / f"{index:02d}_{case['target']}"
        docx_audit_results.append(run_docx_audit_case(case, case_dir))

    docx_flow_results = run_docx_flow_cases(WORK_ROOT / "docx_flow")

    write_json(WORK_ROOT / "content_results.json", content_results)
    write_json(WORK_ROOT / "docx_audit_results.json", docx_audit_results)
    write_json(WORK_ROOT / "docx_flow_results.json", docx_flow_results)

    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text(render_report(content_results, docx_audit_results, docx_flow_results), encoding="utf-8")

    failures = [
        f"content:{row['target_code']}"
        for row in content_results
        if not row["detected"] or not row["final_acceptance_gate_ok"]
    ]
    failures.extend(f"docx:{row['target']}" for row in docx_audit_results if not row["detected"])
    if failures:
        print(json.dumps({"ok": False, "report": str(REPORT_PATH), "failures": failures}, ensure_ascii=False, indent=2))
        return 1
    print(json.dumps({"ok": True, "report": str(REPORT_PATH), "work_dir": str(WORK_ROOT)}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
