from __future__ import annotations

import json

from docx import Document

from app.docx_v4 import build_docx_from_fragments


def test_graphic_question_renders_evidence_before_answer(tmp_path) -> None:
    fragments = {
        "schema_version": "answer_book.answer_fragments.v4",
        "fragments": [
            {
                "schema_version": "answer_book.answer_fragment.v4",
                "question_id": "graphic_s01_01",
                "section": "一、作图题",
                "question_type": "作图题",
                "number": "1",
                "answer": "见解析",
                "answer_summary": "应画出 Frank-Read 位错源增殖过程。",
                "evidence_ids": ["ev1"],
                "formulas": [],
                "blocks": [
                    {"label": "教材依据", "segments": [{"type": "text", "text": "教材中的位错源增殖机制。"}]},
                    {"label": "图示", "segments": [{"type": "text", "text": "Frank-Read 位错源示意图"}]},
                    {"label": "解析", "segments": [{"type": "text", "text": "解析说明。"}]},
                    {"label": "易错点及注意事项", "segments": [{"type": "text", "text": "注意钉扎点。"}]},
                    {"label": "待复核公式", "segments": [{"type": "text", "text": "不应进入作图题正式格式。"}]},
                ],
            }
        ],
    }
    source = tmp_path / "fragments.json"
    output = tmp_path / "answer.docx"
    source.write_text(json.dumps(fragments, ensure_ascii=False), encoding="utf-8")

    build_docx_from_fragments(source, output)

    doc = Document(output)
    paragraphs = [p.text for p in doc.paragraphs if p.text.strip()]
    assert paragraphs[1:8] == [
        "一、作图题",
        "1、教材依据：教材中的位错源增殖机制。",
        "答案：",
        "应画出 Frank-Read 位错源增殖过程。",
        "图示：Frank-Read 位错源示意图",
        "解析：解析说明。",
        "易错点及注意事项：注意钉扎点。",
    ]
    answer_body = next(p for p in doc.paragraphs if p.text == "应画出 Frank-Read 位错源增殖过程。")
    assert answer_body.paragraph_format.first_line_indent is not None
    assert "易错点及注意事项：注意钉扎点。" in paragraphs
    assert not any("待复核公式" in text for text in paragraphs)
    assert not any(text == "1、见解析" for text in paragraphs)


def test_short_answer_renders_answer_and_analysis_separately(tmp_path) -> None:
    fragments = {
        "schema_version": "answer_book.answer_fragments.v4",
        "fragments": [
            {
                "schema_version": "answer_book.answer_fragment.v4",
                "question_id": "qa_s01_06_02",
                "section": "六、简答题",
                "question_type": "简答题",
                "number": "2",
                "answer": "扩散系数随温度升高而增大，表面扩散最快。",
                "answer_summary": "扩散系数随温度升高而增大，表面扩散最快。",
                "evidence_ids": ["ev1"],
                "formulas": [],
                "blocks": [
                    {"label": "教材依据", "segments": [{"type": "text", "text": "教材扩散章节。"}]},
                    {"label": "解析", "segments": [{"type": "text", "text": "同温下看曲线高低，表面高于晶界和晶内。"}]},
                    {"label": "易错点及注意事项", "segments": [{"type": "text", "text": "不能把横轴直接当线性温度。"}]},
                ],
            }
        ],
    }
    source = tmp_path / "fragments.json"
    output = tmp_path / "answer.docx"
    source.write_text(json.dumps(fragments, ensure_ascii=False), encoding="utf-8")

    build_docx_from_fragments(source, output)

    doc = Document(output)
    paragraphs = [p.text for p in doc.paragraphs if p.text.strip()]
    assert paragraphs[1:7] == [
        "六、简答题",
        "2、教材依据：教材扩散章节。",
        "答案：",
        "扩散系数随温度升高而增大，表面扩散最快。",
        "解析：同温下看曲线高低，表面高于晶界和晶内。",
        "易错点及注意事项：不能把横轴直接当线性温度。",
    ]
    answer_body = next(p for p in doc.paragraphs if p.text == "扩散系数随温度升高而增大，表面扩散最快。")
    assert answer_body.paragraph_format.first_line_indent is not None


def test_calculation_renders_number_and_evidence_in_same_paragraph(tmp_path) -> None:
    fragments = {
        "schema_version": "answer_book.answer_fragments.v4",
        "fragments": [
            {
                "schema_version": "answer_book.answer_fragment.v4",
                "question_id": "calc_s01_01",
                "section": "五、计算题",
                "question_type": "计算题",
                "number": "1",
                "answer": "计算结果见解题步骤。",
                "answer_summary": "计算结果见解题步骤。",
                "evidence_ids": ["ev1"],
                "formulas": [],
                "blocks": [
                    {"label": "教材依据", "segments": [{"type": "text", "text": "教材扩散定律章节。"}]},
                    {"label": "解析", "segments": [{"type": "text", "text": "先统一单位。"}]},
                    {"label": "解题步骤", "segments": [{"type": "text", "text": "代入已知条件计算。"}]},
                ],
            }
        ],
    }
    source = tmp_path / "fragments.json"
    output = tmp_path / "answer.docx"
    source.write_text(json.dumps(fragments, ensure_ascii=False), encoding="utf-8")

    build_docx_from_fragments(source, output)

    paragraphs = [p.text for p in Document(output).paragraphs if p.text.strip()]
    assert "1、教材依据：教材扩散定律章节。" in paragraphs
    assert "1、" not in paragraphs
    assert sum("教材依据：" in text for text in paragraphs) == 1
    assert "解析：先统一单位。" in paragraphs


def test_docx_normalizes_saved_subquestion_and_requirement_markers(tmp_path) -> None:
    fragments = {
        "schema_version": "answer_book.answer_fragments.v4",
        "fragments": [
            {
                "schema_version": "answer_book.answer_fragment.v4",
                "question_id": "nested_s01_01",
                "section": "六、简答题",
                "question_type": "简答题",
                "number": "1",
                "answer": "(1)、第一问答案。\n①、第一项要求。",
                "answer_summary": "(1)、第一问答案。\n①、第一项要求。",
                "evidence_ids": ["ev1"],
                "formulas": [],
                "blocks": [
                    {"label": "教材依据", "segments": [{"type": "text", "text": "教材相关章节。"}]},
                    {
                        "label": "解析",
                        "segments": [{"type": "text", "text": "(1)、第一小问\n(2) 第二小问\n①、第一项要求\n②、第二项要求"}],
                    },
                ],
            }
        ],
    }
    source = tmp_path / "fragments.json"
    output = tmp_path / "answer.docx"
    source.write_text(json.dumps(fragments, ensure_ascii=False), encoding="utf-8")

    build_docx_from_fragments(source, output)

    paragraphs = [p.text for p in Document(output).paragraphs if p.text.strip()]
    joined = "\n".join(paragraphs)
    assert "(1)第一小问" in joined
    assert "(2)第二小问" in joined
    assert "①、第一项要求" in joined
    assert "②、第二项要求" in joined
    assert "(1)、" not in joined
