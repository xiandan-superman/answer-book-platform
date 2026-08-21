from app.question_requirements import answer_figure_required, delivery_figure_required, figure_requirement_summary, source_image_required


def test_source_image_is_not_mistaken_for_answer_drawing() -> None:
    question = {"stem": "根据下图回答问题", "image_refs": ["source.png"], "needs_figure": True}
    assert source_image_required(question) is True
    assert answer_figure_required(question) is False
    assert delivery_figure_required(question) is True


def test_drawing_command_requires_generated_answer_figure_without_type_label() -> None:
    question = {"stem": "请画出 FCC 晶胞，并标出原子位置。"}
    assert answer_figure_required(question) is True
    assert source_image_required(question) is False


def test_legacy_needs_figure_without_source_image_means_answer_figure() -> None:
    assert answer_figure_required({"stem": "完成本题", "needs_figure": True}) is True


def test_topic_word_alone_does_not_trigger_drawing() -> None:
    assert figure_requirement_summary({"stem": "讨论该晶胞结构及其配位数。"}) == {
        "answer_figure_required": False,
        "source_image_required": False,
        "delivery_figure_required": False,
    }


def test_nested_requirement_can_trigger_answer_figure() -> None:
    question = {"stem": "回答下列问题", "subquestions": [{"stem": "用图表示温度变化时的相区变化"}]}
    assert answer_figure_required(question) is True


def test_source_figure_noun_does_not_trigger_answer_redrawing() -> None:
    question = {
        "stem": "碳的相图（示意图）如题四图所示，请根据图中信息回答。",
        "image_refs": ["carbon_phase_diagram.png"],
        "needs_figure": True,
        "answer_figure_required": True,
        "question_understanding": {"needs_figure": True},
        "figure_schema_plan": {"diagram_intent": {"needs_figure": True}},
    }
    assert source_image_required(question) is True
    assert answer_figure_required(question) is False


def test_explicit_request_to_draw_schematic_still_triggers_answer_figure() -> None:
    assert answer_figure_required({"stem": "请画出对应的相图示意图并标出相区。"}) is True


def test_cross_subject_source_images_remain_reference_only() -> None:
    examples = [
        "如图所示，已知三角形 ABC，求角 A。",
        "下图为植物细胞示意图，说明结构 2 的功能。",
        "根据图中的温度—时间曲线判断物态变化。",
    ]
    for stem in examples:
        assert answer_figure_required({"stem": stem, "image_refs": ["source.png"], "needs_figure": True}) is False


def test_annotation_on_supplied_image_is_an_answer_drawing_requirement() -> None:
    question = {"stem": "请在下图中标出入射光线和反射角。", "image_refs": ["optics.png"]}
    assert source_image_required(question) is True
    assert answer_figure_required(question) is True
