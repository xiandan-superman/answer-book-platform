from __future__ import annotations

from ..contracts import CapabilityManifest, KeywordRule

CORE_FIGURE_SCHEMAS = (
    {
        "schema_id": "source_image_overlay.v1",
        "kind": "source_image_overlay",
        "name": "原图保留标注",
        "disciplines": ["general"],
        "description": "逐像素保留题目原图，仅叠加线、箭头、框、圆、点和文字标注。",
        "required_fields": ["kind", "caption", "source_image", "source_image_sha256", "annotations"],
        "optional_fields": ["required_labels"],
        "renderer": "draw_source_image_overlay",
    },
    {
        "schema_id": "generic_axis_curve.v1",
        "kind": "generic_axis_curve",
        "name": "通用坐标曲线",
        "disciplines": ["general"],
        "description": "用于任意学科中的单曲线坐标关系图。",
        "required_fields": ["kind", "caption", "x_label", "y_label", "points"],
        "optional_fields": ["title", "annotations"],
        "renderer": "draw_generic_axis_curve",
    },
    {
        "schema_id": "multi_curve_axis_plot.v1",
        "kind": "multi_curve_axis_plot",
        "name": "多曲线坐标对比图",
        "disciplines": ["general"],
        "description": "用于任意学科中不同条件下的多曲线对比。",
        "required_fields": ["kind", "caption", "x_label", "y_label", "series"],
        "optional_fields": ["title", "annotations", "legend_title"],
        "renderer": "draw_multi_curve_axis_plot",
    },
    {
        "schema_id": "process_flow_diagram.v1",
        "kind": "process_flow_diagram",
        "name": "流程图",
        "disciplines": ["general"],
        "description": "用于过程、操作、制备、实验或决策步骤的流程示意。",
        "required_fields": ["kind", "caption", "steps"],
        "optional_fields": ["arrows", "conditions"],
        "renderer": "draw_process_flow_diagram",
    },
)


CORE_FIGURES_CAPABILITY = CapabilityManifest(
    capability_id="core.figures",
    version="1.0",
    name="通用结构化图形",
    description="不依赖具体学科的坐标曲线、对比曲线和流程图能力。",
    schemas=CORE_FIGURE_SCHEMAS,
    keyword_rules=(
        KeywordRule("process_flow_diagram", ("流程图", "工艺流程", "制备流程")),
        KeywordRule("multi_curve_axis_plot", ("多曲线", "对比曲线", "不同温度", "不同成分", "不同时间")),
        KeywordRule("generic_axis_curve", ("曲线", "坐标图", "关系图", "变化图"), confidence=0.75),
    ),
    prompt_context="优先使用通用图形表达学科无关的曲线、比较关系和步骤流程。",
)
