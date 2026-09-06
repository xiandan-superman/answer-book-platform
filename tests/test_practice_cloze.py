from __future__ import annotations

import copy
import json

import pytest

from app.exercise_generation import (
    _normalize_plan,
    audit_practice_blueprint,
    normalize_practice_set,
    recompute_practice_quality,
    validate_practice_mode_contract,
)
from app.practice_cloze import cloze_issue, compile_cloze, compile_requirements, expected_stem


def fixture(mode="exam"):
    focus = "仅在原句挖空，保持原文；不超过40字。"
    source = {
        "source_question_id": "s1",
        "source_content": "共晶转变：一种液相在恒温下同时结晶出两种固相的反应。",
        "knowledge_points": ["共晶转变"],
    }
    item = {
        "plan_item_id": "p1",
        "number": 1,
        "question_type": "填空题",
        "source_refs": ["s1"],
        "source_question_id": "s1",
        "required_knowledge_points": ["共晶转变"],
    }
    req = compile_requirements(
        {
            "wording": "strict_verbatim",
            "wording_quote": "仅在原句挖空，保持原文",
            "max_stem_characters": 40,
            "length_quote": "不超过40字",
            "cross_source": "default",
        },
        focus,
    )
    raw = {
        "source_id": "s1",
        "source_field": "source_content",
        "source_quote": source["source_content"],
        "blanks": [{"text": "恒温", "occurrence": 1}],
    }
    item["cloze_mapping"] = compile_cloze(raw, item, [source], {})
    item["cloze_expected_stem"] = expected_stem(item["cloze_mapping"], [source])
    exercise = {
        "plan_item_id": "p1",
        "number": 1,
        "question_type": "填空题",
        "stem": item["cloze_expected_stem"],
        "knowledge_points": ["共晶转变"],
        "source_question_id": "s1",
        "source_refs": ["s1"],
        "cloze_literal": True,
    }
    return {
        "focus": focus,
        "source_mode": mode,
        "requested_count": 1,
        "selected_source_questions": [source],
        "blueprint": {
            "generation_strategy": "knowledge_overall" if mode == "knowledge" else "targeted_set",
            "requirements_contract": req,
            "exercise_plan": [item],
        },
        "exercises": [exercise],
    }, raw


@pytest.mark.parametrize("mode", ["exam", "knowledge"])
def test_exact_evidence_survives_normalize_storage_restore_and_export(tmp_path, monkeypatch, mode):
    from app import practice_store
    from app.practice_export import build_practice_question_docx, validate_docx_output, validate_practice_export

    practice, _ = fixture(mode)
    normalized = normalize_practice_set(practice, requested_count=1, subject="材料", planned_plan_ids=["p1"])
    practice["exercises"] = normalized["exercises"]
    assert not cloze_issue(practice["exercises"][0], practice["blueprint"]["exercise_plan"][0], practice)
    monkeypatch.setattr(practice_store, "PRACTICE_HISTORY_DIR", tmp_path)
    stored = practice_store.save_practice_record(practice, request={"focus": practice["focus"]})
    recovered = practice_store.load_practice_record(stored["history_id"])["data"]
    assert recovered["blueprint"]["exercise_plan"][0]["cloze_mapping"] == practice["blueprint"]["exercise_plan"][0]["cloze_mapping"]
    assert recompute_practice_quality(recovered)["release_level"] != "blocked"
    assert validate_practice_export(recovered)["ok"]
    content = build_practice_question_docx(recovered)
    assert validate_docx_output(content, recovered)["ok"]
    import io
    import zipfile

    from lxml import etree

    root = etree.fromstring(zipfile.ZipFile(io.BytesIO(content)).read("word/document.xml"))
    visible = "".join(root.itertext())
    assert "恒温" not in visible
    # The durable mapping stores no new answer-bearing text.
    assert "恒温" not in json.dumps(recovered["blueprint"]["exercise_plan"][0]["cloze_mapping"], ensure_ascii=False)
    recovered["exercises"][0]["stem"] = recovered["exercises"][0]["stem"].replace("共晶转变：", "共晶转变是指")
    assert recompute_practice_quality(recovered)["release_level"] == "blocked"
    assert not validate_practice_export(recovered)["ok"]


@pytest.mark.parametrize("mutation", ["paraphrase", "missing", "source_changed", "extra_resource", "wrong_source", "bad_span"])
def test_strict_cloze_rejects_drift(mutation):
    practice, _ = fixture()
    item = practice["blueprint"]["exercise_plan"][0]
    exercise = practice["exercises"][0]
    if mutation == "paraphrase":
        exercise["stem"] = exercise["stem"].replace("：", "是指")
    if mutation == "missing":
        item.pop("cloze_mapping")
    if mutation == "source_changed":
        practice["selected_source_questions"][0]["source_content"] += "修改"
    if mutation == "extra_resource":
        exercise["formulas"] = [{"latex": "恒温", "role": "given"}]
    if mutation == "wrong_source":
        item["source_refs"] = ["s2"]
    if mutation == "bad_span":
        item["cloze_mapping"]["blank_spans"] = [[1, 999]]
    assert cloze_issue(exercise, item, practice)["code"] == "cloze_provenance_invalid"


def test_minimal_edit_and_history_are_not_upgraded_to_strict():
    practice, _ = fixture()
    item = practice["blueprint"]["exercise_plan"][0]
    item.pop("cloze_mapping")
    practice["blueprint"].pop("requirements_contract")
    assert cloze_issue(practice["exercises"][0], item, practice) is None
    contract = compile_requirements({"wording": "minimal_edit", "wording_quote": "不要过多改变", "cross_source": "default"}, "不要过多改变")
    practice["blueprint"]["requirements_contract"] = contract
    assert cloze_issue(practice["exercises"][0], item, practice) is None
    with pytest.raises(ValueError, match="缺少用户要求"):
        compile_requirements(None, "只在原句挖空")


def test_occurrences_and_unicode_are_deterministic():
    catalog = [{"source_question_id": "s", "source_content": "😀甲和甲。😀甲和甲。"}]
    item = {"source_refs": ["s"]}
    mapping = compile_cloze(
        {
            "source_id": "s",
            "source_field": "source_content",
            "source_quote": "😀甲和甲。",
            "quote_occurrence": 2,
            "blanks": [{"text": "甲", "occurrence": 2}],
        },
        item,
        catalog,
        {},
    )
    assert mapping["quote_start"] == 5
    assert expected_stem(mapping, catalog) == "😀甲和______。"
    with pytest.raises(ValueError, match="重叠"):
        compile_cloze(
            {
                "source_id": "s",
                "source_field": "source_content",
                "source_quote": "😀甲和甲。",
                "blanks": [{"text": "甲和甲"}, {"text": "甲"}],
            },
            item,
            catalog,
            {},
        )


def test_source_summary_and_math_projection_cannot_claim_verbatim():
    practice, raw = fixture()
    item = practice["blueprint"]["exercise_plan"][0]
    catalog = practice["selected_source_questions"]
    with pytest.raises(ValueError, match="source_content"):
        compile_cloze({**raw, "source_field": "recognized_content"}, item, catalog, {})
    with pytest.raises(ValueError, match="公式序列化"):
        compile_cloze({**raw, "source_quote": "$x$"}, item, catalog, {})


def test_default_comprehensive_items_do_not_require_cross_source_and_explicit_conflict_stays():
    practice, _ = fixture()
    practice["selected_source_questions"].append({"source_question_id": "s2"})
    assert not validate_practice_mode_contract(practice)["errors"]
    mixed = copy.deepcopy(practice)
    mixed["blueprint"]["exercise_plan"].append({"number": 2, "question_type": "简答题", "source_refs": ["s2"], "coverage_role": "铺垫"})
    assert not validate_practice_mode_contract(mixed)["errors"]
    practice["blueprint"]["requirements_contract"]["cross_source"] = "explicit"
    assert validate_practice_mode_contract(practice)["errors"]


def test_strict_fill_uses_the_selected_span_not_the_entire_source_scope():
    practice, _ = fixture()
    practice["blueprint"]["generation_strategy"] = "parallel_exam"
    source = practice["selected_source_questions"][0]
    source["knowledge_points"] = ["共晶转变", "层片间距与强化"]
    plan_item = practice["blueprint"]["exercise_plan"][0]
    # The source sentence only defines the first point. The second point is
    # available to another item, but cannot be made a hidden requirement for
    # this literal span.
    plan_item["required_knowledge_points"] = ["共晶转变"]
    audit = audit_practice_blueprint(practice)
    assert not any("必考知识点" in error for error in audit["errors"])
    assert plan_item["cloze_expected_stem"] == expected_stem(plan_item["cloze_mapping"], [source])


@pytest.mark.parametrize("refs", [["S2"], ["S2", "S1"], ["unknown"], []])
def test_fresh_source_bindings_never_fabricated(refs):
    row = {"source_refs": refs, "target_skill": "保留原始目标", "required_constraints": {}}
    raw = {"blueprint": {"exercise_plan": [row]}}
    before = copy.deepcopy(raw)
    kwargs = dict(
        count=1,
        planned_types=["简答题"],
        difficulty="基础",
        selected_types=["简答题"],
        source_files=[],
        selected_source_questions=[{"source_question_id": "s1"}, {"source_question_id": "s2"}],
        generation_strategy="targeted_set",
        fresh_planning=True,
    )
    if not refs or "unknown" in refs:
        with pytest.raises(ValueError, match="来源缺失或未知"):
            _normalize_plan(raw, **kwargs)
    else:
        plan = _normalize_plan(raw, **kwargs)
        assert plan["blueprint"]["exercise_plan"][0]["source_refs"] == [ref.replace("S", "s") for ref in refs]
        assert plan["blueprint"]["exercise_plan"][0]["target_skill"] == row["target_skill"]
    assert raw == before


@pytest.mark.parametrize("mode", ["exam", "knowledge"])
def test_positional_formula_key_is_preserved_without_changing_input(mode):
    from app.exercise_generation import _normalize_formulas

    raw = [{"f1": r"x^2", "role": "given"}, {"f2": r"y^2", "location": "stem"}]
    saved = copy.deepcopy(raw)
    assert [item["latex"] for item in _normalize_formulas(raw)] == [r"x^2", r"y^2"]
    assert raw == saved
    with pytest.raises(ValueError, match="原引用位置"):
        _normalize_formulas([{}, {"f1": "x"}])


def test_literal_source_numbering_is_not_rewritten_by_word():
    import io
    import zipfile

    from lxml import etree

    from app.practice_export import build_practice_question_docx, validate_docx_output
    practice, raw = fixture()
    source = practice["selected_source_questions"][0]
    source["source_content"] = "2、" + source["source_content"]
    item = practice["blueprint"]["exercise_plan"][0]
    raw["source_quote"] = source["source_content"]
    item["cloze_mapping"] = compile_cloze(raw, item, [source], {})
    practice["exercises"][0]["stem"] = expected_stem(item["cloze_mapping"], [source])
    content = build_practice_question_docx(practice)
    assert validate_docx_output(content, practice)["ok"]
    archive = zipfile.ZipFile(io.BytesIO(content))
    xml = archive.read("word/document.xml")
    assert "2、共晶" in "".join(etree.fromstring(xml).itertext())
    mutated = io.BytesIO()
    with zipfile.ZipFile(mutated, "w") as output:
        for name in archive.namelist():
            value = archive.read(name)
            if name == "word/document.xml":
                value = value.replace("2、共晶".encode(), "(1) 共晶".encode())
            output.writestr(name, value)
    assert not validate_docx_output(mutated.getvalue(), practice)["ok"]


def test_preview_literal_guard_precedes_editorial_normalization():
    from pathlib import Path
    source = (Path(__file__).resolve().parents[1] / "web" / "app.js").read_text()
    body = source.split("function normalizePracticeMarkdownTables(item) {", 1)[1].split("function ensurePracticeMathJax", 1)[0]
    assert 'if (item.cloze_literal === true) return { ...item, stem: String(item.stem ?? "") };' in body
    assert body.index("item.cloze_literal") < body.index("extractPracticeMarkdownTables(item.stem)")
    assert "normalizePracticeQuestionText(extracted.stem)" in body
