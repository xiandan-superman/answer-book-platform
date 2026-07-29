from app.answer_generation import fragment_from_analysis_draft, normalize_formula_reference_base


def _segment_formula_ids(block):
    return [segment.get("formula_id") for segment in block.get("segments", []) if segment.get("type") == "formula_ref"]


def test_zero_based_formula_placeholders_are_normalized_before_fragment_conversion():
    draft = {
        "question_id": "q_zero_formula",
        "answer": "A",
        "analysis_segments": [
            {"text": "第一处为 {f0}，第二处为 {f1}。", "formula_indices": [0, 1]},
        ],
        "option_analysis": {
            "A": "使用 {f0}。",
            "B": "使用 {f1}。",
        },
        "formulas": [
            {"latex": "a=b", "meaning": "第一个公式", "role": "relation", "display": True},
            {"latex": "c=d", "meaning": "第二个公式", "role": "relation", "display": True},
        ],
    }

    fragment = fragment_from_analysis_draft(
        draft,
        {"question_id": "q_zero_formula", "question_type": "选择题", "stem": "选择正确项。"},
        [],
    )

    analysis = next(block for block in fragment["blocks"] if block["label"] == "解析")
    option = next(block for block in fragment["blocks"] if block["label"] == "选项分析")
    assert _segment_formula_ids(analysis) == ["f_q_zero_formula_01", "f_q_zero_formula_02"]
    assert _segment_formula_ids(option) == ["f_q_zero_formula_01", "f_q_zero_formula_02"]
    assert "{f0}" not in "".join(segment.get("text", "") for block in fragment["blocks"] for segment in block.get("segments", []))
    assert fragment["_meta"]["formula_reference_normalization"] == "zero_based_to_one_based"


def test_one_based_formula_placeholders_are_left_unchanged():
    draft = {
        "question_id": "q_one_formula",
        "analysis_segments": [{"text": "引用 {f1}。", "formula_indices": [1]}],
        "formulas": [{"latex": "a=b", "meaning": "第一个公式"}],
    }

    normalized, changed = normalize_formula_reference_base(draft)

    assert changed is False
    assert normalized is draft


def test_uppercase_formula_placeholders_are_recognized():
    draft = {
        "question_id": "q_upper_formula",
        "analysis_segments": [{"text": "由关系 {F1} 可知结论。"}],
        "formulas": [{"latex": "h+k+l=2n", "meaning": "衍射条件", "role": "relation", "display": True}],
    }

    fragment = fragment_from_analysis_draft(
        draft,
        {"question_id": "q_upper_formula", "question_type": "简答题", "stem": "说明衍射条件。"},
        [],
    )

    analysis = next(block for block in fragment["blocks"] if block["label"] == "解析")
    assert _segment_formula_ids(analysis) == ["f_q_upper_formula_01"]
    assert "{F1}" not in "".join(segment.get("text", "") for block in fragment["blocks"] for segment in block.get("segments", []))
