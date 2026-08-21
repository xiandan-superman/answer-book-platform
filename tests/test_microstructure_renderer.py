from __future__ import annotations

from pathlib import Path


def test_microstructure_renderer_draws_field_relations_not_only_isolated_ellipses(tmp_path: Path) -> None:
    from app.figures import draw_microstructure_schematic

    output = tmp_path / "microstructure.png"
    draw_microstructure_schematic(
        {
            "kind": "microstructure_schematic",
            "caption": "test",
            "matrix_label": "matrix",
            "features": [
                {"label": "matrix", "morphology": "matrix", "distribution": "matrix"},
                {"label": "dendrite", "morphology": "dendrite", "distribution": "dendritic"},
                {"label": "particles", "morphology": "particles", "distribution": "dispersed throughout"},
            ],
        },
        output,
    )

    from PIL import Image

    with Image.open(output) as image:
        assert image.width >= 700
        assert image.height >= 500
        assert len(image.convert("L").getcolors(maxcolors=256 * 256) or []) > 8


def test_dendritic_distribution_overrides_generic_lamellar_shape(tmp_path: Path, monkeypatch) -> None:
    import matplotlib.axes

    from app.figures import draw_microstructure_schematic

    filled_regions: list[float] = []
    original_add_patch = matplotlib.axes.Axes.add_patch

    def record_patch(self, patch):
        import matplotlib.patches

        if isinstance(patch, matplotlib.patches.Polygon):
            filled_regions.append(float(patch.get_linewidth()))
        return original_add_patch(self, patch)

    monkeypatch.setattr(matplotlib.axes.Axes, "add_patch", record_patch)
    draw_microstructure_schematic(
        {
            "features": [
                {
                    "label": "珠光体",
                    "morphology": "lamellar_colony",
                    "distribution": "树枝状分布",
                }
            ]
        },
        tmp_path / "dendritic.png",
    )

    # Three main stems and their branches are dark two-dimensional constituent
    # regions. This preserves a dendritic overview with light internal lamellae
    # rather than turning the morphology into a bare tree skeleton.
    assert len(filled_regions) == 15


def test_visual_qa_is_grounded_to_declared_spec_labels() -> None:
    from app.figures import _ground_visual_qa_to_figure_spec

    grounded = _ground_visual_qa_to_figure_spec(
        {
            "output_schema": {
                "ok": False,
                "missing_requirements": ["缺失对'二次渗碳体'的示意或标注。"],
                "label_issues": ["标签'球光体'存在错别字，正确应为'珠光体'。"],
                "visual_issues": [],
                "summary": "OCR 与题外推断导致失败。",
            }
        },
        {
            "kind": "microstructure_schematic",
            "features": [{"label": "珠光体", "morphology": "dendrite", "spatial_role": "dispersed"}],
        },
    )

    assert grounded["ok"] is True
    assert grounded["missing_requirements"] == []
    assert grounded["label_issues"] == []
    assert len(grounded["figure_spec_grounding"]["suppressed_issues"]) == 2


def test_visual_qa_grounding_suppresses_unquoted_missing_object_outside_spec() -> None:
    from app.figures import _ground_visual_qa_to_figure_spec

    grounded = _ground_visual_qa_to_figure_spec(
        {
            "ok": False,
            "missing_requirements": ["图中缺失二次渗碳体的标注或区分。"],
            "label_issues": [],
            "visual_issues": [],
        },
        {
            "kind": "microstructure_schematic",
            "features": [{"label": "珠光体"}, {"label": "变态莱氏体"}],
        },
    )

    assert grounded["ok"] is True
    assert grounded["missing_requirements"] == []


def test_visual_qa_grounding_keeps_visible_layout_problems() -> None:
    from app.figures import _ground_visual_qa_to_figure_spec

    grounded = _ground_visual_qa_to_figure_spec(
        {"ok": False, "missing_requirements": [], "label_issues": [], "visual_issues": ["标签引线相互交叉。"]},
        {"kind": "microstructure_schematic", "features": [{"label": "基体"}]},
    )

    assert grounded["ok"] is False
    assert grounded["visual_issues"] == ["标签引线相互交叉。"]


def test_visual_qa_grounding_suppresses_deterministic_subscript_ocr_false_positive() -> None:
    from app.figures import _ground_visual_qa_to_figure_spec

    grounded = _ground_visual_qa_to_figure_spec(
        {
            "ok": False,
            "missing_requirements": [],
            "label_issues": [],
            "visual_issues": ["标注文本中化学式Fe₃C的下标未正确渲染，显示为普通文本Fe3C。"],
        },
        {
            "kind": "generic_axis_curve",
            "annotations": [{"text": "共晶转变 L→γ+Fe₃C"}],
        },
    )

    assert grounded["ok"] is True
    assert grounded["visual_issues"] == []
    assert grounded["figure_spec_grounding"]["suppressed_issues"][0]["reason"] == "deterministic_label_source"


def test_visual_qa_grounding_rejects_undeclared_constituent_scope_expansion() -> None:
    from app.figures import _ground_visual_qa_to_figure_spec

    grounded = _ground_visual_qa_to_figure_spec(
        {
            "ok": False,
            "missing_requirements": [],
            "label_issues": [],
            "visual_issues": ["图中还应增加并标出未在规格中声明的第三种组织成分。"],
        },
        {
            "kind": "microstructure_schematic",
            "features": [{"label": "珠光体"}, {"label": "变态莱氏体"}],
        },
    )

    assert grounded["ok"] is True
    assert grounded["visual_issues"] == []


def test_visual_qa_grounding_suppresses_only_missing_undeclared_clause_in_mixed_issue() -> None:
    from app.figures import _ground_visual_qa_to_figure_spec

    grounded = _ground_visual_qa_to_figure_spec(
        {
            "ok": False,
            "missing_requirements": [],
            "label_issues": [],
            "visual_issues": ["本图已有珠光体和变态莱氏体，但缺少未在规格声明的二次相。"],
        },
        {"kind": "microstructure_schematic", "features": [{"label": "珠光体"}, {"label": "变态莱氏体"}]},
    )

    assert grounded["ok"] is True


def test_visual_qa_grounding_keeps_declared_morphology_problem() -> None:
    from app.figures import _ground_visual_qa_to_figure_spec

    issue = "共晶体(α+β)未画成规格要求的层片状，而是孤立块状。"
    grounded = _ground_visual_qa_to_figure_spec(
        {"ok": False, "missing_requirements": [], "label_issues": [], "visual_issues": [issue]},
        {"kind": "microstructure_schematic", "features": [{"label": "共晶体(α+β)", "morphology": "lamellar_colony"}]},
    )

    assert grounded["ok"] is False
    assert grounded["visual_issues"] == [issue]


def test_visual_qa_grounding_keeps_declared_subject_property_problem() -> None:
    from app.figures import _ground_visual_qa_to_figure_spec

    issue = "“变态莱氏体”未体现规格要求的连续基体和珠光体岛特征。"
    grounded = _ground_visual_qa_to_figure_spec(
        {"ok": False, "missing_requirements": [], "label_issues": [], "visual_issues": [issue]},
        {"kind": "microstructure_schematic", "features": [{"label": "变态莱氏体", "morphology": "matrix"}]},
    )

    assert grounded["ok"] is False
    assert grounded["visual_issues"] == [issue]


def test_figure_repair_scope_rejects_new_peer_label_but_allows_morphology_change() -> None:
    from app.figures import _figure_candidate_scope_issues

    current = {
        "kind": "microstructure_schematic",
        "features": [{"label": "珠光体", "morphology": "lamellar_colony"}, {"label": "变态莱氏体", "morphology": "matrix"}],
    }
    morphology_only = {
        "kind": "microstructure_schematic",
        "features": [{"label": "珠光体", "morphology": "dendrite"}, {"label": "变态莱氏体", "morphology": "matrix"}],
    }
    expanded = {
        **morphology_only,
        "features": [*morphology_only["features"], {"label": "二次渗碳体", "morphology": "network"}],
    }

    assert _figure_candidate_scope_issues(current, morphology_only) == []
    assert _figure_candidate_scope_issues(current, expanded) == [
        "figure_candidate_scope_expansion: added undeclared label 二次渗碳体"
    ]
    assert _figure_candidate_scope_issues(
        current, expanded, grounded_labels={"二次渗碳体"}
    ) == []


def test_confirmed_figure_repair_labels_come_only_from_source_image_understanding() -> None:
    from app.figures import _confirmed_figure_repair_labels

    exam = {
        "items": [
            {
                "question_id": "q1",
                "question_understanding": {
                    "images": [
                        {
                            "detected_labels": ["bcc", "FeTi"],
                            "fixed_condition_phase_paths": [
                                {
                                    "ordered_regions": [{"phase_or_region": "bcc + fcc"}],
                                    "terminal_regions": {"x_max": {"phase_or_region": "bcc"}},
                                }
                            ],
                        }
                    ]
                },
            }
        ]
    }

    assert _confirmed_figure_repair_labels(exam, "q1") == {"bcc", "FeTi", "bcc + fcc"}


def test_visual_qa_grounding_suppresses_deterministic_label_ocr_but_not_overlap() -> None:
    from app.figures import _ground_visual_qa_to_figure_spec

    grounded = _ground_visual_qa_to_figure_spec(
        {
            "ok": False,
            "missing_requirements": [],
            "label_issues": [],
            "visual_issues": ["液相线文字疑似缺字。", "液相线文字与共晶标签重叠。"],
        },
        {"kind": "generic_axis_curve", "annotations": [{"text": "液相线"}]},
    )

    assert grounded["ok"] is False
    assert grounded["visual_issues"] == ["液相线文字与共晶标签重叠。"]


def test_string_curve_annotations_are_rendered_and_caption_title_is_not_repeated(tmp_path: Path, monkeypatch) -> None:
    import matplotlib.axes

    from app.figures import draw_line_chart

    rendered_labels: list[str] = []
    rendered_titles: list[str] = []
    original_annotate = matplotlib.axes.Axes.annotate
    original_set_title = matplotlib.axes.Axes.set_title

    def record_annotation(self, text, *args, **kwargs):
        rendered_labels.append(str(text))
        return original_annotate(self, text, *args, **kwargs)

    def record_title(self, label, *args, **kwargs):
        rendered_titles.append(str(label))
        return original_set_title(self, label, *args, **kwargs)

    monkeypatch.setattr(matplotlib.axes.Axes, "annotate", record_annotation)
    monkeypatch.setattr(matplotlib.axes.Axes, "set_title", record_title)
    draw_line_chart(
        {
            "caption": "冷却曲线",
            "title": "冷却曲线",
            "points": [[0, 1400], [1, 1148], [2, 1148], [3, 727], [4, 727], [5, 20]],
            "annotations": ["液相线", "共晶转变", "共析转变"],
        },
        tmp_path / "curve.png",
    )

    assert rendered_labels[0] == "液相线"
    assert rendered_labels[1] == "共晶转变（1148 ℃）"
    assert rendered_labels[2] == "共析转变（727 ℃）"
    assert "冷却曲线" not in rendered_titles


def test_thermal_curve_preserves_supplied_points_and_annotation_coordinates(tmp_path: Path, monkeypatch) -> None:
    import matplotlib.axes

    from app.figures import draw_generic_axis_curve

    anchors: dict[str, tuple[float, float]] = {}
    original_annotate = matplotlib.axes.Axes.annotate

    def record_annotation(self, text, *args, **kwargs):
        anchors[str(text)] = kwargs.get("xy")
        return original_annotate(self, text, *args, **kwargs)

    monkeypatch.setattr(matplotlib.axes.Axes, "annotate", record_annotation)
    draw_generic_axis_curve(
        {
            "caption": "合金平衡凝固冷却曲线",
            "x_label": "时间",
            "y_label": "温度",
            "points": [[0, 1400], [1, 1300], [2, 1148], [3, 1148], [4, 900], [5, 727], [6, 727]],
            "annotations": [
                {"x": 99, "y": 1200, "text": "共晶转变"},
                {"x": 99, "y": 800, "text": "共析转变"},
            ],
        },
        tmp_path / "thermal.png",
    )

    assert anchors["共晶转变"] == (99.0, 1200.0)
    assert anchors["共析转变"] == (99.0, 800.0)
    assert "初生相开始析出（转变起点）" not in anchors


def test_generic_axis_curve_renders_point_labels_once_per_adjacent_region(tmp_path: Path, monkeypatch) -> None:
    import matplotlib.axes

    from app.figures import draw_generic_axis_curve

    seen: list[str] = []
    original_annotate = matplotlib.axes.Axes.annotate

    def record_annotation(self, text, *args, **kwargs):
        seen.append(str(text))
        return original_annotate(self, text, *args, **kwargs)

    monkeypatch.setattr(matplotlib.axes.Axes, "annotate", record_annotation)
    draw_generic_axis_curve(
        {
            "points": [
                {"x": 0, "y": 0, "label": "Fe 端"},
                {"x": 0.2, "y": 0.3, "label": "两相区"},
                {"x": 0.4, "y": 0.3, "label": "两相区"},
                {"x": 1, "y": 1, "label": "Ti 端"},
            ],
        },
        tmp_path / "curve.png",
    )

    assert seen.count("两相区") == 1
    assert {"Fe 端", "两相区", "Ti 端"}.issubset(set(seen))


def test_matrix_label_anchor_is_separate_from_dendrite_anchor(tmp_path: Path, monkeypatch) -> None:
    import matplotlib.axes

    from app.figures import draw_microstructure_schematic

    anchors: dict[str, tuple[float, float]] = {}
    original_annotate = matplotlib.axes.Axes.annotate

    def record_annotation(self, text, *args, **kwargs):
        xy = kwargs.get("xy")
        if isinstance(xy, tuple):
            anchors[str(text)] = xy
        return original_annotate(self, text, *args, **kwargs)

    monkeypatch.setattr(matplotlib.axes.Axes, "annotate", record_annotation)
    draw_microstructure_schematic(
        {
            "features": [
                {"label": "珠光体", "morphology": "dendrite", "distribution": "树枝状分布"},
                {"label": "变态莱氏体", "morphology": "matrix", "distribution": "基体分布"},
            ]
        },
        tmp_path / "anchors.png",
    )

    assert anchors["珠光体"][1] < 0.4
    assert anchors["变态莱氏体"][1] > 0.6


def test_dendritic_matrix_and_interdendritic_lamellae_are_two_dimensional(tmp_path: Path, monkeypatch) -> None:
    import matplotlib.axes

    from app.figures import draw_microstructure_schematic

    filled_arms = 0
    original_plot = matplotlib.axes.Axes.plot

    def record_plot(self, *args, **kwargs):
        nonlocal filled_arms
        if kwargs.get("linewidth", 0) >= 10:
            filled_arms += 1
        return original_plot(self, *args, **kwargs)

    monkeypatch.setattr(matplotlib.axes.Axes, "plot", record_plot)
    draw_microstructure_schematic(
        {
            "features": [
                {"label": "primary", "morphology": "dendrite", "distribution": "continuous", "spatial_role": "matrix"},
                {"label": "eutectic", "morphology": "lamellar_colony", "distribution": "dispersed", "spatial_role": "intergranular"},
            ]
        },
        tmp_path / "matrix_and_lamellae.png",
    )

    # Main dendrite stems, branch arms and several explicitly lamellar
    # interdendritic colonies are filled regions, not disconnected skeletons.
    assert filled_arms >= 20


def test_interdendritic_lamellae_fill_residual_field_instead_of_floating_islands(tmp_path: Path, monkeypatch) -> None:
    import matplotlib.axes
    import matplotlib.patches

    from app.figures import draw_microstructure_schematic

    hatched_fields = 0
    floating_colonies = 0
    original_add_patch = matplotlib.axes.Axes.add_patch

    def record_patch(self, patch):
        nonlocal hatched_fields, floating_colonies
        if isinstance(patch, matplotlib.patches.Rectangle) and patch.get_hatch() == "||||":
            hatched_fields += 1
        if isinstance(patch, matplotlib.patches.Ellipse) and patch.get_hatch() == "||||":
            floating_colonies += 1
        return original_add_patch(self, patch)

    monkeypatch.setattr(matplotlib.axes.Axes, "add_patch", record_patch)
    draw_microstructure_schematic(
        {
            "features": [
                {"label": "primary", "morphology": "dendrite", "distribution": "continuous", "spatial_role": "matrix"},
                {
                    "label": "eutectic",
                    "morphology": "lamellar_colony",
                    "distribution": "interdendritic",
                    "spatial_role": "intergranular",
                },
            ]
        },
        tmp_path / "filled_interdendritic_field.png",
    )

    assert hatched_fields == 1
    assert floating_colonies == 0


def test_natural_language_morphologies_route_to_dendrite_and_lamellar_matrix(tmp_path: Path, monkeypatch) -> None:
    import matplotlib.axes
    import matplotlib.patches

    from app.figures import draw_microstructure_schematic

    lamellar_fields = 0
    filled_dendrite_lines = 0
    original_add_patch = matplotlib.axes.Axes.add_patch
    original_plot = matplotlib.axes.Axes.plot

    def record_patch(self, patch):
        nonlocal lamellar_fields
        if isinstance(patch, matplotlib.patches.Rectangle) and patch.get_hatch() == "////":
            lamellar_fields += 1
        return original_add_patch(self, patch)

    def record_plot(self, *args, **kwargs):
        nonlocal filled_dendrite_lines
        if kwargs.get("linewidth", 0) >= 8:
            filled_dendrite_lines += 1
        return original_plot(self, *args, **kwargs)

    monkeypatch.setattr(matplotlib.axes.Axes, "add_patch", record_patch)
    monkeypatch.setattr(matplotlib.axes.Axes, "plot", record_plot)
    draw_microstructure_schematic(
        {
            "features": [
                {
                    "label": "初生α相",
                    "morphology": "粗大枝晶状或等轴状",
                    "distribution": "孤立分布于基体中",
                    "spatial_role": "isolated",
                },
                {
                    "label": "共晶体(α+β)",
                    "morphology": "细小层片状交替分布",
                    "distribution": "填充于初生α相之间",
                    "spatial_role": "matrix",
                },
            ]
        },
        tmp_path / "natural_language_morphology.png",
    )

    assert lamellar_fields == 1
    assert filled_dendrite_lines >= 20


def test_microstructure_top_labels_keep_rendering_safety_margin(tmp_path: Path, monkeypatch) -> None:
    import matplotlib.axes

    from app.figures import draw_microstructure_schematic

    label_positions = []
    original_annotate = matplotlib.axes.Axes.annotate

    def record_annotation(self, text, *args, **kwargs):
        label_positions.append(kwargs.get("xytext"))
        return original_annotate(self, text, *args, **kwargs)

    monkeypatch.setattr(matplotlib.axes.Axes, "annotate", record_annotation)
    draw_microstructure_schematic(
        {
            "features": [
                {"label": "较长的初生相科学标签", "morphology": "粗大枝晶状", "spatial_role": "isolated"},
                {"label": "较长的共晶体科学标签", "morphology": "细小层片状", "spatial_role": "matrix"},
            ]
        },
        tmp_path / "label_margin.png",
    )

    assert label_positions
    assert all(position is not None and position[1] <= 0.86 for position in label_positions)


def test_visual_audit_worker_count_defaults_below_generation_concurrency(monkeypatch) -> None:
    from app.figures import figure_visual_audit_worker_count

    monkeypatch.delenv("FIGURE_VISUAL_AUDIT_MAX_WORKERS", raising=False)
    assert figure_visual_audit_worker_count() == 2
    monkeypatch.setenv("FIGURE_VISUAL_AUDIT_MAX_WORKERS", "99")
    assert figure_visual_audit_worker_count() == 3
