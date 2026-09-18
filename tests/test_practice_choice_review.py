from copy import deepcopy
from types import SimpleNamespace

import pytest

from app.exercise_generation import recompute_practice_quality
from app.practice_choice_review import review_choices, review_issue
from app.practice_export import validate_practice_export
from app.practice_store import strip_practice_answer_content


def question():
    return {"question_type": "单选题", "stem": "3x-2=x+4，正确的是（      ）",
            "options": [{"text": "x=3"}, {"text": "两边除以x不改变解"}],
            "knowledge_points": ["方程"], "verification_note": "已检查"}


@pytest.mark.parametrize("verdicts,ambiguous,passed", [([True, False], False, True), ([True, True], False, False), ([False, False], False, False), ([True, False], True, False), ([True, None], False, False)])
def test_each_option_and_uniqueness_control_delivery(verdicts, ambiguous, passed):
    item = question()
    calls = []

    def respond(*args, **kwargs):
        calls.append(kwargs)
        return {"options": [{"index": i, "satisfies": value, "reason": "按实际条件核对"} for i, value in enumerate(verdicts, 1)], "ambiguous": ambiguous}

    data = {"exercises": [item]}
    review_choices(data, SimpleNamespace(chat_json_object=respond), "test", "low")
    assert len(calls) == 1 and calls[0]["attempts"] == 1
    assert (not review_issue(item)) is passed
    assert (recompute_practice_quality(data)["status"] == "passed") is passed
    public = strip_practice_answer_content(data)
    assert public["exercises"][0]["choice_review"] == item["choice_review"]
    assert "options" not in item["choice_review"]  # no answer-label leakage
    if not passed:
        assert validate_practice_export(public)["ok"] is False
    else:
        changed = deepcopy(item)
        changed["options"][0]["text"] = "x=4"
        assert review_issue(changed)
        assert validate_practice_export({"exercises": [changed]})["ok"] is False


@pytest.mark.parametrize("response", [{}, {"options": [{"index": 1, "satisfies": True, "reason": "ok"}]*2, "ambiguous": False}])
def test_incomplete_review_cannot_pass(response):
    item = question()
    review_choices({"exercises": [item]}, SimpleNamespace(chat_json_object=lambda *a, **k: response), "test", None)
    assert review_issue(item)


def test_failure_and_visual_content_do_not_fabricate_pass():
    def fail(*args, **kwargs):
        raise TimeoutError("test")
    item = question()
    review_choices({"exercises": [item]}, SimpleNamespace(chat_json_object=fail), "test", None)
    assert item["choice_review"]["status"] == "unverified"
    item["figures"] = [{"figure_id": "f1"}]
    review_choices({"exercises": [item]}, None, "test", None)
    assert review_issue(item)


def test_historical_unreviewed_choice_is_not_formally_certified():
    assert recompute_practice_quality({"exercises": [question()]})["release_level"] == "review_candidate"


def test_cancelled_review_is_not_swallowed():
    from app.practice_runtime import PracticeGenerationStopped

    def stopped(*args, **kwargs):
        raise PracticeGenerationStopped("cancelled")

    with pytest.raises(PracticeGenerationStopped):
        review_choices({"exercises": [question()]}, SimpleNamespace(chat_json_object=stopped), "test", None)


def test_valid_review_is_reused_without_paid_call():
    data = {"exercises": [question()]}
    response = {"options": [{"index": 1, "satisfies": True, "reason": "ok"}, {"index": 2, "satisfies": False, "reason": "no"}], "ambiguous": False}
    review_choices(data, SimpleNamespace(chat_json_object=lambda *a, **k: response), "test", None)
    review_choices(data, None, "test", None)
    assert not review_issue(data["exercises"][0])
