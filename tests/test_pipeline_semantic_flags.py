from __future__ import annotations

import json

from app.pipeline import _mark_unresolved_correctness_review_flags


def test_only_unresolved_repair_decisions_receive_semantic_review_flag(tmp_path) -> None:
    fragments_json = tmp_path / "answer_fragments.json"
    fragments_json.write_text(
        json.dumps(
            {
                "fragments": [
                    {
                        "question_id": "q1",
                        "answer": "candidate",
                        "_review_flags": [
                            {"code": "high_risk_correctness_unresolved", "message": "stale"}
                        ],
                    },
                    {"question_id": "q2", "answer": "candidate"},
                ]
            }
        ),
        encoding="utf-8",
    )

    flagged = _mark_unresolved_correctness_review_flags(
        fragments_json,
        [
            {"question_id": "q1", "decision": "warn"},
            {"question_id": "q2", "decision": "repair"},
        ],
    )

    data = json.loads(fragments_json.read_text(encoding="utf-8"))
    assert flagged == ["q2"]
    assert "_review_flags" not in data["fragments"][0]
    assert data["fragments"][1]["_review_flags"][0]["code"] == "high_risk_correctness_unresolved"
