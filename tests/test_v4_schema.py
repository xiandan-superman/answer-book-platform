from __future__ import annotations

import unittest

from app.v4_schema import AnswerFragmentV4, validate_v4_answer_fragment


def valid_fragment() -> dict:
    return {
        "schema_version": "answer_book.answer_fragment.v4",
        "question_id": "q1",
        "answer": "见解析",
        "blocks": [{"label": "解析", "segments": [{"type": "text", "text": "根据题意可得结论。"}]}],
        "formulas": [],
        "evidence_ids": ["ev_q1_01"],
    }


class V4SchemaTests(unittest.TestCase):
    def test_valid_fragment_has_a_typed_model_and_no_issues(self) -> None:
        fragment = valid_fragment()

        model = AnswerFragmentV4.model_validate(fragment)

        self.assertEqual("q1", model.question_id)
        self.assertEqual([], validate_v4_answer_fragment(fragment))

    def test_strict_nested_types_are_rejected(self) -> None:
        fragment = valid_fragment()
        fragment["evidence_ids"] = [123]

        issues = validate_v4_answer_fragment(fragment)

        self.assertTrue(any("schema type error at evidence_ids.0" in issue for issue in issues), issues)

    def test_semantic_formula_guards_remain_active(self) -> None:
        fragment = valid_fragment()
        fragment["blocks"][0]["segments"] = [{"type": "formula_ref", "formula_id": "f_missing"}]

        issues = validate_v4_answer_fragment(fragment)

        self.assertIn("formula_ref points to missing formula_id: f_missing", issues)

    def test_short_academic_formula_is_not_treated_as_empty(self) -> None:
        fragment = valid_fragment()
        fragment["formulas"] = [
            {"formula_id": "f_pv", "latex": "pV", "role": "relation", "display": False}
        ]
        fragment["blocks"][0]["segments"] = [
            {"type": "formula_ref", "formula_id": "f_pv", "inline": True}
        ]

        self.assertEqual([], validate_v4_answer_fragment(fragment))


if __name__ == "__main__":
    unittest.main()
