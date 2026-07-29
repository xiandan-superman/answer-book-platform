from __future__ import annotations

import json
import base64
from io import BytesIO
import mimetypes
import math
import re
import shutil
import textwrap
from pathlib import Path
from typing import Any

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import Arc, Circle, Ellipse, FancyArrowPatch
from matplotlib import font_manager
from PIL import Image

from .figure_schema_registry import get_schema
from .llm_client import OpenAICompatibleClient
from .question_types import question_has_type
from .settings import FIGURE_AUXILIARY_MAX_TOKENS, provider_supports_image_generation
from .drawing_code import drawing_domain_quality_rules, generate_drawing_code_spec, parse_drawing_code_model_response, question_drawing_mode, run_drawing_code, validate_drawing_code

BUNDLED_FONT_DIR = Path(__file__).resolve().parents[1] / "assets" / "fonts"


def configure_fonts() -> None:
    compatibility_aliases = {
        "Kaiti.ttf": "Kaiti",
        "Microsoft Yahei.ttf": "Microsoft YaHei",
    }
    for pattern in ("*.ttf", "*.otf", "*.ttc", "*.woff", "*.woff2"):
        for font_file in BUNDLED_FONT_DIR.rglob(pattern):
            try:
                font_manager.fontManager.addfont(str(font_file))
                alias = compatibility_aliases.get(font_file.name) if font_file.parent.name == "matplotlib-compatible" else None
                if alias:
                    font_manager.fontManager.ttflist.append(
                        font_manager.FontEntry(
                            fname=str(font_file),
                            name=alias,
                            style="normal",
                            variant="normal",
                            weight="normal",
                            stretch="normal",
                            size="scalable",
                        )
                    )
            except Exception:
                pass
    preferred = [
        "PingFang SC",
        "Songti SC",
        "SimSong",
        "Microsoft YaHei",
        "SimHei",
        "Arial Unicode MS",
        "Lantinghei SC",
        "STHeiti",
    ]
    available = {f.name for f in font_manager.fontManager.ttflist}
    for name in preferred:
        if name in available:
            plt.rcParams["font.sans-serif"] = [name, "DejaVu Sans"]
            plt.rcParams["axes.unicode_minus"] = False
            return
    plt.rcParams["axes.unicode_minus"] = False


configure_fonts()


def _wrap_plot_title(value: Any, width: int = 34) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    return "\n".join(textwrap.wrap(text, width=width, break_long_words=True, replace_whitespace=False))


def draw_phase_diagram(spec: dict[str, Any], output: Path) -> None:
    eutectic_x = float(spec.get("eutectic_x", 0.5))
    eutectic_t = float(spec.get("eutectic_t", 0))
    left_t = float(spec.get("left_melting_t", 30))
    right_t = float(spec.get("right_melting_t", 50))
    fig, ax = plt.subplots(figsize=(5.8, 3.6), dpi=180)
    ax.plot([0, eutectic_x], [left_t, eutectic_t], color="#111", lw=1.8)
    ax.plot([eutectic_x, 1], [eutectic_t, right_t], color="#111", lw=1.8)
    ax.axhline(eutectic_t, color="#555", lw=1.0, ls="--")
    ax.text(0.03, left_t + 1, "A", fontsize=10)
    ax.text(0.94, right_t + 1, "B", fontsize=10)
    ax.text(eutectic_x + 0.02, eutectic_t + 1, "E", fontsize=10)
    ax.set_xlabel("$x_B$")
    ax.set_ylabel("T / °C")
    ax.set_xlim(0, 1)
    ax.set_ylim(min(eutectic_t, 0) - 5, max(left_t, right_t) + 8)
    ax.grid(True, alpha=0.18)
    ax.set_title(spec.get("title") or spec.get("caption") or "Phase diagram", fontsize=11)
    fig.tight_layout()
    output.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(output)
    plt.close(fig)


def draw_line_chart(spec: dict[str, Any], output: Path) -> None:
    points = spec.get("points") or []
    xs = [float(p[0]) for p in points]
    ys = [float(p[1]) for p in points]
    fig, ax = plt.subplots(figsize=(5.8, 3.6), dpi=180)
    ax.plot(xs, ys, marker="o", color="#111", lw=1.8)
    ax.set_xlabel(spec.get("x_label", "x"))
    ax.set_ylabel(spec.get("y_label", "y"))
    ax.set_title(spec.get("title") or spec.get("caption") or "Line chart", fontsize=11)
    ax.grid(True, alpha=0.2)
    fig.tight_layout()
    output.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(output)
    plt.close(fig)


def draw_curved_liquid_surface(spec: dict[str, Any], output: Path) -> None:
    fig, axes = plt.subplots(1, 2, figsize=(7.2, 3.6), dpi=180)
    blue = "#2563eb"
    red = "#dc2626"
    green = "#16a34a"
    gray = "#475569"

    for ax in axes:
        ax.set_aspect("equal", adjustable="box")
        ax.set_xlim(-1.35, 1.35)
        ax.set_ylim(-1.2, 1.25)
        ax.axis("off")

    left = axes[0]
    left.set_title("凸液面（液滴）", fontsize=11)
    left.add_patch(Circle((0, 0), 0.82, fill=False, edgecolor="#111", linewidth=1.8))
    left.add_patch(FancyArrowPatch((-0.18, 0.82), (-0.62, 0.82), arrowstyle="->", mutation_scale=12, color=green, linewidth=1.3))
    left.add_patch(FancyArrowPatch((0.18, 0.82), (0.62, 0.82), arrowstyle="->", mutation_scale=12, color=green, linewidth=1.3))
    left.add_patch(FancyArrowPatch((0, 0.92), (0, 0.12), arrowstyle="->", mutation_scale=14, color=red, linewidth=1.6))
    left.add_patch(FancyArrowPatch((0, 0), (0.58, 0.58), arrowstyle="->", mutation_scale=12, color=blue, linewidth=1.4))
    left.plot([0, 0.82], [0, 0], color=gray, linestyle="--", linewidth=1.0)
    left.text(0.08, 0.15, "附加压力\n指向曲率中心", color=red, fontsize=8)
    left.text(-1.05, 0.92, "表面张力\n切线方向", color=green, fontsize=8)
    left.text(0.32, 0.34, "r", color=blue, fontsize=10)
    left.text(-0.28, -0.12, "曲率中心", fontsize=8)
    left.text(0.56, -0.18, "曲率半径", color=gray, fontsize=8)

    right = axes[1]
    right.set_title("凹液面（气泡）", fontsize=11)
    right.add_patch(Arc((0, 0), 1.65, 1.65, theta1=35, theta2=325, edgecolor="#111", linewidth=1.8))
    right.add_patch(FancyArrowPatch((0.44, 0.69), (0.73, 0.42), arrowstyle="->", mutation_scale=12, color=green, linewidth=1.3))
    right.add_patch(FancyArrowPatch((0.44, -0.69), (0.73, -0.42), arrowstyle="->", mutation_scale=12, color=green, linewidth=1.3))
    right.add_patch(FancyArrowPatch((0.82, 0), (0.12, 0), arrowstyle="->", mutation_scale=14, color=red, linewidth=1.6))
    right.add_patch(FancyArrowPatch((0, 0), (0.58, 0.58), arrowstyle="->", mutation_scale=12, color=blue, linewidth=1.4))
    right.plot([0, 0.82], [0, 0], color=gray, linestyle="--", linewidth=1.0)
    right.text(0.08, -0.34, "附加压力\n指向曲率中心", color=red, fontsize=8)
    right.text(0.62, 0.78, "表面张力\n切线方向", color=green, fontsize=8)
    right.text(0.32, 0.34, "r", color=blue, fontsize=10)
    right.text(-0.28, -0.12, "曲率中心", fontsize=8)
    right.text(0.58, -0.18, "曲率半径", color=gray, fontsize=8)

    fig.suptitle(spec.get("title") or spec.get("caption") or "弯曲液面附加压力示意图", fontsize=12)
    fig.tight_layout()
    output.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(output)
    plt.close(fig)


def _xy(value: Any, fallback: tuple[float, float] = (0.0, 0.0)) -> tuple[float, float]:
    if isinstance(value, (list, tuple)) and len(value) >= 2:
        try:
            return float(value[0]), float(value[1])
        except (TypeError, ValueError):
            return fallback
    return fallback


def _style_kwargs(element: dict[str, Any]) -> dict[str, Any]:
    style = str(element.get("style") or "solid").strip().lower()
    return {
        "color": str(element.get("color") or "#111111"),
        "linewidth": float(element.get("linewidth") or element.get("lw") or 1.5),
        "linestyle": "--" if style == "dashed" else ":" if style == "dotted" else "-",
    }


def _label_element(ax: Any, element: dict[str, Any], default_xy: tuple[float, float]) -> None:
    label = str(element.get("label") or "").strip()
    if not label:
        return
    x, y = _xy(element.get("label_xy"), (default_xy[0] + 0.04, default_xy[1] + 0.04))
    ax.text(x, y, label, fontsize=float(element.get("label_size") or 8), color=str(element.get("label_color") or element.get("color") or "#111111"))


def validate_custom_diagram_spec(spec: dict[str, Any]) -> list[str]:
    issues: list[str] = []
    elements = spec.get("elements")
    if not isinstance(elements, list) or not elements:
        return ["custom_diagram.elements must be a non-empty list"]
    labels = {str(element.get("label") or "").strip() for element in elements if isinstance(element, dict)}
    labels.update(str(element.get("text") or "").strip() for element in elements if isinstance(element, dict) and element.get("type") == "text")
    for required in spec.get("required_labels") or []:
        required_text = str(required or "").strip()
        if required_text and not any(required_text in label for label in labels):
            issues.append(f"missing required label: {required_text}")
    allowed = {"line", "arrow", "circle", "ellipse", "arc", "text", "point"}
    for idx, element in enumerate(elements):
        if not isinstance(element, dict):
            issues.append(f"elements[{idx}] must be object")
            continue
        etype = str(element.get("type") or "").strip()
        if etype not in allowed:
            issues.append(f"elements[{idx}].type invalid: {etype}")
        if etype in {"line", "arrow"} and ("start" not in element or "end" not in element):
            issues.append(f"elements[{idx}] {etype} requires start and end")
        if etype in {"circle", "ellipse", "arc"} and "center" not in element:
            issues.append(f"elements[{idx}] {etype} requires center")
        if etype == "circle" and "radius" not in element:
            issues.append(f"elements[{idx}] circle requires radius")
        if etype in {"ellipse", "arc"} and ("width" not in element or "height" not in element):
            issues.append(f"elements[{idx}] {etype} requires width and height")
        if etype == "text" and ("xy" not in element or not str(element.get("text") or "").strip()):
            issues.append(f"elements[{idx}] text requires xy and text")
    return issues


def draw_custom_diagram(spec: dict[str, Any], output: Path) -> None:
    issues = validate_custom_diagram_spec(spec)
    if issues:
        raise ValueError("; ".join(issues))
    fig, ax = plt.subplots(figsize=(6.4, 4.0), dpi=180)
    ax.set_aspect("equal", adjustable="box")
    ax.axis("off")
    x_values: list[float] = []
    y_values: list[float] = []
    for element in spec.get("elements") or []:
        etype = str(element.get("type") or "").strip()
        kwargs = _style_kwargs(element)
        if etype in {"line", "arrow"}:
            start = _xy(element.get("start"))
            end = _xy(element.get("end"))
            x_values.extend([start[0], end[0]])
            y_values.extend([start[1], end[1]])
            if etype == "arrow":
                ax.add_patch(FancyArrowPatch(start, end, arrowstyle=str(element.get("arrowstyle") or "->"), mutation_scale=float(element.get("mutation_scale") or 13), **kwargs))
            else:
                ax.plot([start[0], end[0]], [start[1], end[1]], **kwargs)
            _label_element(ax, element, ((start[0] + end[0]) / 2, (start[1] + end[1]) / 2))
        elif etype == "circle":
            center = _xy(element.get("center"))
            radius = float(element.get("radius") or 0.5)
            x_values.extend([center[0] - radius, center[0] + radius])
            y_values.extend([center[1] - radius, center[1] + radius])
            ax.add_patch(Circle(center, radius, fill=bool(element.get("fill", False)), edgecolor=kwargs["color"], facecolor=str(element.get("fill_color") or "none"), linewidth=kwargs["linewidth"], linestyle=kwargs["linestyle"]))
            _label_element(ax, element, (center[0], center[1] + radius))
        elif etype == "ellipse":
            center = _xy(element.get("center"))
            width = float(element.get("width") or 1.0)
            height = float(element.get("height") or 0.6)
            x_values.extend([center[0] - width / 2, center[0] + width / 2])
            y_values.extend([center[1] - height / 2, center[1] + height / 2])
            ax.add_patch(Ellipse(center, width, height, angle=float(element.get("angle") or 0), fill=bool(element.get("fill", False)), edgecolor=kwargs["color"], facecolor=str(element.get("fill_color") or "none"), linewidth=kwargs["linewidth"], linestyle=kwargs["linestyle"]))
            _label_element(ax, element, (center[0], center[1] + height / 2))
        elif etype == "arc":
            center = _xy(element.get("center"))
            width = float(element.get("width") or 1.0)
            height = float(element.get("height") or 1.0)
            x_values.extend([center[0] - width / 2, center[0] + width / 2])
            y_values.extend([center[1] - height / 2, center[1] + height / 2])
            ax.add_patch(Arc(center, width, height, angle=float(element.get("angle") or 0), theta1=float(element.get("theta1") or 0), theta2=float(element.get("theta2") or 180), **kwargs))
            _label_element(ax, element, (center[0], center[1] + height / 2))
        elif etype == "point":
            xy = _xy(element.get("xy"))
            x_values.append(xy[0])
            y_values.append(xy[1])
            ax.scatter([xy[0]], [xy[1]], s=float(element.get("size") or 32), color=kwargs["color"])
            _label_element(ax, element, xy)
        elif etype == "text":
            xy = _xy(element.get("xy"))
            x_values.append(xy[0])
            y_values.append(xy[1])
            ax.text(xy[0], xy[1], str(element.get("text") or ""), fontsize=float(element.get("font_size") or 9), color=str(element.get("color") or "#111111"))
    if x_values and y_values:
        margin = float(spec.get("margin") or 0.25)
        ax.set_xlim(min(x_values) - margin, max(x_values) + margin)
        ax.set_ylim(min(y_values) - margin, max(y_values) + margin)
    title = str(spec.get("title") or spec.get("caption") or "").strip()
    if title:
        ax.set_title(title, fontsize=12)
    fig.tight_layout()
    output.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(output)
    plt.close(fig)


def draw_diffraction_pattern(spec: dict[str, Any], output: Path) -> None:
    points = spec.get("points") or []
    fig, ax = plt.subplots(figsize=(4.8, 4.8), dpi=180)
    for p in points:
        xy = p.get("xy", [0, 0])
        label = p.get("label", "")
        size = float(p.get("size", 36))
        ax.scatter([float(xy[0])], [float(xy[1])], s=size, c="#111")
        if label:
            ax.text(float(xy[0]) + 0.05, float(xy[1]) + 0.05, label, fontsize=8)
    ax.axhline(0, color="#ddd", lw=0.8)
    ax.axvline(0, color="#ddd", lw=0.8)
    ax.set_aspect("equal", adjustable="box")
    ax.set_xlabel("$g_1$")
    ax.set_ylabel("$g_2$")
    ax.set_title(spec.get("title") or spec.get("caption") or "Diffraction pattern", fontsize=11)
    ax.grid(True, alpha=0.16)
    fig.tight_layout()
    output.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(output)
    plt.close(fig)


def draw_fcc_cell(spec: dict[str, Any], output: Path) -> None:
    fig, ax = plt.subplots(figsize=(4.8, 4.2), dpi=180)
    front = [(0, 0), (1, 0), (1, 1), (0, 1), (0, 0)]
    back = [(0.35, 0.28), (1.35, 0.28), (1.35, 1.28), (0.35, 1.28), (0.35, 0.28)]
    ax.plot([p[0] for p in front], [p[1] for p in front], color="#111", lw=1.4)
    ax.plot([p[0] for p in back], [p[1] for p in back], color="#111", lw=1.4)
    for a, b in zip(front[:-1], back[:-1]):
        ax.plot([a[0], b[0]], [a[1], b[1]], color="#111", lw=1.4)
    corners = front[:-1] + back[:-1]
    face_centers = [(0.5, 0.5), (0.85, 0.78), (0.675, 0.14), (0.675, 1.14), (0.175, 0.64), (1.175, 0.64)]
    for x, y in corners:
        ax.scatter([x], [y], s=180, color="#ffffff", edgecolors="#111", linewidths=1.2, zorder=3)
    for x, y in face_centers:
        ax.scatter([x], [y], s=130, color="#2f6f9f", edgecolors="#111", linewidths=0.8, zorder=4)
    ax.text(0.05, -0.14, "顶点原子", fontsize=9)
    ax.text(0.78, -0.14, "面心原子", fontsize=9, color="#2f6f9f")
    ax.set_title(spec.get("title") or spec.get("caption") or "面心立方晶胞示意图", fontsize=11)
    ax.set_aspect("equal", adjustable="box")
    ax.axis("off")
    fig.tight_layout()
    output.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(output)
    plt.close(fig)


def _default_points() -> list[list[float]]:
    return [[0, 0], [1, 0.35], [2, 0.9], [3, 1.15], [4, 1.05]]


def draw_generic_axis_curve(spec: dict[str, Any], output: Path) -> None:
    if not spec.get("points"):
        spec = {**spec, "points": _default_points()}
    draw_line_chart(spec, output)


def draw_multi_curve_axis_plot(spec: dict[str, Any], output: Path) -> None:
    series = spec.get("series") if isinstance(spec.get("series"), list) else []
    if not series:
        series = [
            {"label": "条件 A", "points": [[0, 0.15], [1, 0.4], [2, 0.75], [3, 0.95]]},
            {"label": "条件 B", "points": [[0, 0.1], [1, 0.25], [2, 0.52], [3, 0.8]]},
        ]
    fig, ax = plt.subplots(figsize=(5.8, 3.6), dpi=180)
    for item in series:
        if not isinstance(item, dict):
            continue
        points = item.get("points") or []
        if not points:
            continue
        parsed_points = [_point_xy(point) for point in points]
        parsed_points = [point for point in parsed_points if point is not None]
        if not parsed_points:
            continue
        xs = [point[0] for point in parsed_points]
        ys = [point[1] for point in parsed_points]
        ax.plot(xs, ys, marker="o", lw=1.7, label=str(item.get("label") or "曲线"))
    ax.set_xlabel(spec.get("x_label", "x"))
    ax.set_ylabel(spec.get("y_label", "y"))
    ax.set_title(spec.get("title") or spec.get("caption") or "多曲线对比图", fontsize=11)
    ax.grid(True, alpha=0.2)
    ax.legend(fontsize=8)
    fig.tight_layout()
    output.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(output)
    plt.close(fig)


def draw_binary_phase_diagram(spec: dict[str, Any], output: Path) -> None:
    curves = spec.get("curves") if isinstance(spec.get("curves"), list) else []
    if not curves:
        eutectic_x = float(spec.get("eutectic_x", 0.45))
        eutectic_t = float(spec.get("eutectic_t", 577))
        curves = [
            {"label": "液相线", "points": [[0, 660], [eutectic_x, eutectic_t], [1, 760]]},
            {"label": "共晶线", "points": [[0, eutectic_t], [1, eutectic_t]], "style": "dashed"},
        ]
    fig, ax = plt.subplots(figsize=(6.0, 3.8), dpi=180)
    for curve in curves:
        points = curve.get("points") if isinstance(curve, dict) else []
        if not points:
            continue
        xs = [float(p[0]) for p in points]
        ys = [float(p[1]) for p in points]
        ax.plot(xs, ys, lw=1.8, linestyle="--" if curve.get("style") == "dashed" else "-", label=str(curve.get("label") or "相界线"))
    regions = spec.get("phase_regions") if isinstance(spec.get("phase_regions"), list) else []
    if not regions:
        regions = [
            {"xy": [0.5, 720], "label": "L"},
            {"xy": [0.2, 610], "label": "α + L"},
            {"xy": [0.72, 610], "label": "β + L"},
            {"xy": [0.5, 520], "label": "α + β"},
        ]
    for region in regions:
        if isinstance(region, dict):
            x, y = _xy(region.get("xy"), (0.5, 0.5))
            ax.text(x, y, str(region.get("label") or ""), fontsize=9, ha="center")
    ax.set_xlabel(spec.get("x_label", "成分 / mole fraction"))
    ax.set_ylabel(spec.get("y_label", "T / °C"))
    ax.set_xlim(0, 1)
    ax.grid(True, alpha=0.16)
    ax.legend(fontsize=8, loc="best")
    ax.set_title(spec.get("title") or spec.get("caption") or "二元相图示意图", fontsize=11)
    fig.tight_layout()
    output.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(output)
    plt.close(fig)


def draw_crystal_unit_cell(spec: dict[str, Any], output: Path) -> None:
    structure = str(spec.get("structure") or "").lower()
    if structure in {"fcc", "face_centered_cubic", "面心立方", ""}:
        draw_fcc_cell(spec, output)
        return
    fig, ax = plt.subplots(figsize=(4.8, 4.2), dpi=180)
    front = [(0, 0), (1, 0), (1, 1), (0, 1), (0, 0)]
    back = [(0.35, 0.28), (1.35, 0.28), (1.35, 1.28), (0.35, 1.28), (0.35, 0.28)]
    ax.plot([p[0] for p in front], [p[1] for p in front], color="#111", lw=1.3)
    ax.plot([p[0] for p in back], [p[1] for p in back], color="#111", lw=1.3)
    for a, b in zip(front[:-1], back[:-1]):
        ax.plot([a[0], b[0]], [a[1], b[1]], color="#111", lw=1.3)
    corners = front[:-1] + back[:-1]
    for x, y in corners:
        ax.scatter([x], [y], s=160, color="#fff", edgecolors="#111", linewidths=1.1, zorder=3)
    if structure in {"bcc", "body_centered_cubic", "体心立方"}:
        ax.scatter([0.675], [0.64], s=160, color="#2563eb", edgecolors="#111", linewidths=0.8, zorder=4)
        ax.text(0.72, 0.66, "体心原子", fontsize=8, color="#2563eb")
    elif structure in {"nacl", "cscl", "perovskite", "钙钛矿", "陶瓷晶体"}:
        for x, y in [(0.5, 0.5), (0.85, 0.78), (0.675, 1.14)]:
            ax.scatter([x], [y], s=120, color="#dc2626", edgecolors="#111", linewidths=0.8, zorder=4)
        ax.text(0.75, -0.12, "阴/阳离子占位", fontsize=8, color="#dc2626")
    ax.set_title(spec.get("title") or spec.get("caption") or "晶胞结构示意图", fontsize=11)
    ax.set_aspect("equal", adjustable="box")
    ax.axis("off")
    fig.tight_layout()
    output.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(output)
    plt.close(fig)


def draw_crystal_plane_direction(spec: dict[str, Any], output: Path) -> None:
    custom = {
        **spec,
        "kind": "custom_diagram",
        "elements": spec.get("elements") or [
            {"type": "line", "start": [0, 0], "end": [1, 0], "label": "a"},
            {"type": "line", "start": [0, 0], "end": [0, 1], "label": "b"},
            {"type": "line", "start": [1, 0], "end": [1, 1]},
            {"type": "line", "start": [0, 1], "end": [1, 1]},
            {"type": "line", "start": [0.15, 0.8], "end": [0.85, 0.2], "label": "(hkl)晶面", "color": "#2563eb", "linewidth": 2},
            {"type": "arrow", "start": [0.15, 0.15], "end": [0.85, 0.75], "label": "[uvw]晶向", "color": "#dc2626"},
        ],
    }
    draw_custom_diagram(custom, output)


def _passes_cubic_extinction(h: int, k: int, l: int, lattice: str) -> bool:
    lattice = _normalize_lattice_name(lattice)
    if lattice in {"bcc", "body_centered_cubic"}:
        return (h + k + l) % 2 == 0
    if lattice in {"fcc", "face_centered_cubic"}:
        return (h % 2 == k % 2 == l % 2)
    return True


def _normalize_lattice_name(value: Any) -> str:
    text = str(value or "").strip().lower()
    compact = re.sub(r"[\s_\-]+", "", text)
    if compact in {"bcc", "bodycenteredcubic", "bodycentredcubic"} or "体心" in text:
        return "bcc"
    if compact in {"fcc", "facecenteredcubic", "facecentredcubic"} or "面心" in text:
        return "fcc"
    return text or "generic_cubic"


def _parse_hkl_index(value: Any) -> list[int] | None:
    if isinstance(value, (list, tuple)) and len(value) >= 3:
        try:
            return [int(value[0]), int(value[1]), int(value[2])]
        except (TypeError, ValueError):
            return None
    text = str(value or "").strip()
    if not text:
        return None
    text = text.strip("()[]{}")
    text = text.replace(",", " ")
    if " " in text:
        parts = [part for part in text.split() if part]
        if len(parts) >= 3:
            try:
                return [int(parts[0]), int(parts[1]), int(parts[2])]
            except ValueError:
                return None
    compact = re.sub(r"\s+", "", text)
    if not compact:
        return None
    values: list[int] = []
    i = 0
    while i < len(compact):
        sign = 1
        if compact[i] in "+-":
            sign = -1 if compact[i] == "-" else 1
            i += 1
        if i >= len(compact) or not compact[i].isdigit():
            return None
        values.append(sign * int(compact[i]))
        i += 1
    return values if len(values) == 3 else None


def _format_hkl_label(h: int, k: int, l: int) -> str:
    return f"({h} {k} {l})"


def _normalized_peak_position(peak: dict[str, Any]) -> float | None:
    for key in ("two_theta", "2theta", "2θ", "angle", "position", "x", "relative_position", "n"):
        if key not in peak:
            continue
        try:
            return float(peak[key])
        except (TypeError, ValueError):
            continue
    return None


def _normalize_peak_label(label: Any) -> str:
    text = str(label or "").strip()
    if not text:
        return ""
    if text.startswith("(") and text.endswith(")"):
        return text
    match = re.fullmatch(r"(\([-+]?\d+\s*[-+]?\d+\s*[-+]?\d+\))(.*)", text)
    if match:
        return f"{match.group(1)}{match.group(2).strip()}"
    return f"({text})"


def _is_odd_hkl_label(label: Any) -> bool:
    text = str(label or "")
    matches = re.findall(r"\(([-+]?\d+)\s*([-+]?\d+)\s*([-+]?\d+)\)", text)
    for h_text, k_text, l_text in matches:
        try:
            if (int(h_text) + int(k_text) + int(l_text)) % 2:
                return True
        except ValueError:
            continue
    parsed = _parse_hkl_index(text)
    return bool(parsed and sum(parsed) % 2)


def _point_xy(point: Any) -> tuple[float, float] | None:
    if isinstance(point, dict):
        x_value = point.get("x", point.get("two_theta", point.get("position", point.get("angle"))))
        y_value = point.get("y", point.get("intensity", point.get("height", 1.0)))
        try:
            return float(x_value), float(y_value)
        except (TypeError, ValueError):
            return None
    if isinstance(point, (list, tuple)) and len(point) >= 2:
        try:
            return float(point[0]), float(point[1])
        except (TypeError, ValueError):
            return None
    return None


def _point_label(point: Any) -> str:
    if isinstance(point, dict):
        return str(point.get("label") or point.get("hkl") or "").strip()
    return ""


def _looks_like_xrd_series(spec: dict[str, Any]) -> bool:
    text = " ".join(
        str(value or "")
        for value in (spec.get("kind"), spec.get("caption"), spec.get("title"), spec.get("x_label"), spec.get("y_label"))
    )
    if any(token in text for token in ("X射线", "衍射", "XRD", "2θ", "sin²θ", "晶面指数", "超结构", "超点阵")):
        return True
    for series in spec.get("series") or []:
        if not isinstance(series, dict):
            continue
        for point in series.get("points") or []:
            label = _point_label(point)
            if re.fullmatch(r"\(?[-+]?\d+\s*[-+]?\d+\s*[-+]?\d+\)?", label.replace(",", " ")):
                return True
    return False


def _normalize_xrd_from_series_spec(spec: dict[str, Any]) -> dict[str, Any]:
    peaks: list[dict[str, Any]] = []
    seen_base_positions: set[float] = set()
    for series_index, series in enumerate(spec.get("series") or []):
        if not isinstance(series, dict):
            continue
        series_label = str(series.get("label") or "")
        ordered_series = any(token in series_label for token in ("有序", "超结构", "超点阵", "ordered", "super"))
        for point in series.get("points") or []:
            xy = _point_xy(point)
            if xy is None:
                continue
            x, y = xy
            rounded_x = round(x, 6)
            label = _normalize_peak_label(_point_label(point))
            fractional_position = abs(x - round(x)) > 1e-6
            is_super = ordered_series and (fractional_position or rounded_x not in seen_base_positions)
            if ordered_series and not is_super:
                continue
            if not ordered_series:
                seen_base_positions.add(rounded_x)
            peaks.append(
                {
                    "two_theta": x,
                    "intensity": y,
                    "label": label,
                    "phase": "ordered" if is_super else "disordered",
                    "style": "--" if is_super else "-",
                    "phase_label": "新增超结构峰" if is_super else "原有峰",
                }
            )
        if series_index == 0 and not seen_base_positions:
            seen_base_positions.update(round(float(peak["two_theta"]), 6) for peak in peaks)
    return {
        **{key: value for key, value in spec.items() if key not in {"series", "annotations", "kind"}},
        "kind": "xrd_pattern",
        "caption": spec.get("caption") or spec.get("title") or "XRD 衍射峰示意图",
        "x_label": spec.get("x_label") or "相对峰位",
        "y_label": spec.get("y_label") or "相对强度",
        "peaks": peaks,
        "phase_labels": ["原有峰", "新增超结构峰"],
    }


def normalize_figure_spec(spec: dict[str, Any]) -> dict[str, Any]:
    normalized = dict(spec)
    kind = str(normalized.get("kind") or "").strip()
    if kind == "multi_curve_axis_plot" and _looks_like_xrd_series(normalized):
        normalized = _normalize_xrd_from_series_spec(normalized)
        kind = "xrd_pattern"
    if kind == "zone_axis_diffraction":
        parsed_axis = _parse_hkl_index(normalized.get("zone_axis"))
        if parsed_axis is not None:
            normalized["zone_axis"] = parsed_axis
        if normalized.get("lattice"):
            normalized["lattice"] = _normalize_lattice_name(normalized.get("lattice"))
        labels: list[list[int]] = []
        for item in normalized.get("label_indices") or []:
            parsed = _parse_hkl_index(item)
            if parsed is not None:
                labels.append(parsed)
        normalized["label_indices"] = labels
        try:
            spot_size = float(normalized.get("spot_size") or 0)
        except (TypeError, ValueError):
            spot_size = 0
        if spot_size < 30:
            normalized["spot_size"] = 42
        try:
            max_index = int(normalized.get("max_index") or 0)
        except (TypeError, ValueError):
            max_index = 0
        if max_index < 2:
            normalized["max_index"] = 3
        return normalized
    if kind == "xrd_pattern":
        peaks: list[dict[str, Any]] = []
        for raw_peak in normalized.get("peaks") or []:
            if not isinstance(raw_peak, dict):
                continue
            peak = dict(raw_peak)
            position = _normalized_peak_position(peak)
            if position is not None:
                peak["two_theta"] = position
            if "intensity" not in peak and "height" not in peak:
                peak["intensity"] = 1.0
            if peak.get("label"):
                peak["label"] = _normalize_peak_label(peak.get("label"))
            phase = peak.get("phase")
            phase_index = peak.get("phase_index", peak.get("phaseIndex"))
            phase_text = str(phase or "").strip().lower()
            label_text = str(peak.get("label") or "")
            phase_label_text = str(peak.get("phase_label") or peak.get("phaseLabel") or "")
            base_phase = (
                phase in (None, "", 0, "0")
                or phase_index in (0, "0")
                or phase_text in {"base", "basic", "fundamental", "原有峰", "基本峰", "disordered", "bcc", "matrix"}
                or any(token in phase_label_text for token in ("原有", "基本", "无序"))
            )
            super_phase = (
                "超" in label_text
                or "★" in label_text
                or "*" in label_text
                or _is_odd_hkl_label(label_text)
                or phase_text in {"super", "superlattice", "新增峰", "超结构峰", "超点阵峰"}
                or any(token in phase_label_text for token in ("新增", "超结构", "超点阵"))
            )
            if super_phase:
                peak["style"] = "--"
                peak["phase_label"] = "新增超结构峰"
            elif base_phase:
                peak["style"] = "-"
                peak.setdefault("phase_label", "原有峰")
            elif "style" not in peak and "linestyle" not in peak:
                peak["style"] = "--"
            peaks.append(peak)
        normalized["peaks"] = peaks
        return normalized
    return normalized


def program_check_figure_spec(spec: dict[str, Any]) -> list[str]:
    kind = str(spec.get("kind") or "").strip()
    issues: list[str] = []
    if kind == "zone_axis_diffraction":
        zone_axis = spec.get("zone_axis") or [1, 1, 0]
        parsed_axis = _parse_hkl_index(zone_axis)
        if parsed_axis is not None:
            zone_axis = parsed_axis
        try:
            u, v, w = [int(x) for x in zone_axis[:3]]
        except (TypeError, ValueError):
            issues.append("zone_axis_diffraction: invalid zone_axis")
            return issues
        labels = [_parse_hkl_index(item) for item in spec.get("label_indices") or []]
        labels = [item for item in labels if item is not None]
        non_origin_labels = [item for item in labels if item != [0, 0, 0]]
        if len(non_origin_labels) < 2:
            issues.append("zone_axis_diffraction: at least two non-origin labelled indices are required")
        try:
            if float(spec.get("spot_size") or 0) < 30:
                issues.append("zone_axis_diffraction: spot_size is too small for final document rendering")
        except (TypeError, ValueError):
            issues.append("zone_axis_diffraction: invalid spot_size")
        lattice = _normalize_lattice_name(spec.get("lattice") or "generic_cubic")
        apply_extinction = bool(spec.get("apply_extinction", False))
        for h, k, l in non_origin_labels:
            if h * u + k * v + l * w != 0:
                issues.append(f"zone_axis_diffraction: labelled index {_format_hkl_label(h, k, l)} does not satisfy zone-axis law")
            if apply_extinction and not _passes_cubic_extinction(h, k, l, lattice):
                issues.append(f"zone_axis_diffraction: labelled index {_format_hkl_label(h, k, l)} violates cubic extinction rule")
        return issues
    if kind == "xrd_pattern":
        peaks = spec.get("peaks") if isinstance(spec.get("peaks"), list) else []
        if not peaks:
            issues.append("xrd_pattern: at least one peak is required")
            return issues
        positions = [_normalized_peak_position(peak) for peak in peaks if isinstance(peak, dict)]
        if len(positions) != len(peaks) or any(position is None for position in positions):
            issues.append("xrd_pattern: peak positions are missing")
        numeric_positions = [float(position) for position in positions if position is not None]
        if len(numeric_positions) > 1 and len(set(round(position, 6) for position in numeric_positions)) < 2:
            issues.append("xrd_pattern: peak positions are collapsed")
        caption = str(spec.get("caption") or spec.get("title") or "")
        expects_superlattice = any(token in caption for token in ("有序", "超结构", "超点阵", "新增"))
        styles = [str((peak.get("style") or peak.get("linestyle") or "") if isinstance(peak, dict) else "") for peak in peaks]
        if expects_superlattice and not any(style in {"--", ":", "-."} for style in styles):
            issues.append("xrd_pattern: ordered/superlattice pattern needs a distinct dashed peak style")
        return issues
    return issues


def draw_zone_axis_diffraction(spec: dict[str, Any], output: Path) -> None:
    spec = normalize_figure_spec(spec)
    u, v, w = [int(x) for x in (spec.get("zone_axis") or [1, 1, 0])[:3]]
    max_index = int(spec.get("max_index") or 3)
    lattice = _normalize_lattice_name(spec.get("lattice") or "generic_cubic")
    apply_extinction = bool(spec.get("apply_extinction", False))
    labels = {tuple(int(x) for x in item[:3]) for item in (spec.get("label_indices") or []) if isinstance(item, (list, tuple)) and len(item) >= 3}
    points: list[tuple[int, int, int]] = []
    for h in range(-max_index, max_index + 1):
        for k in range(-max_index, max_index + 1):
            for l in range(-max_index, max_index + 1):
                if h == k == l == 0 or h * u + k * v + l * w == 0:
                    if not apply_extinction or _passes_cubic_extinction(h, k, l, lattice):
                        points.append((h, k, l))
    if (0, 0, 0) not in points:
        points.append((0, 0, 0))
    if abs(u) == abs(v) and w == 0 and u and v:
        def project(h: int, k: int, l: int) -> tuple[float, float]:
            return ((h - k) / math.sqrt(2), float(l))

        basis_labels = ("", "")
    else:
        b1 = (v, -u, 0) if (u or v) else (1, 0, 0)
        b2 = (w * u, w * v, -(u * u + v * v)) if (u or v) else (0, 1, 0)
        if b2 == (0, 0, 0):
            b2 = (0, 0, 1)

        def project(h: int, k: int, l: int) -> tuple[float, float]:
            return (float(h * b1[0] + k * b1[1] + l * b1[2]), float(-(h * b2[0] + k * b2[1] + l * b2[2])))

        basis_labels = ("g1*", "g2*")
    fig, ax = plt.subplots(figsize=(5.2, 4.8), dpi=200)
    projected_points = [(h, k, l, *project(h, k, l)) for h, k, l in points]
    for h, k, l in points:
        x, y = project(h, k, l)
        base_size = float(spec.get("spot_size") or 42)
        size = base_size * 1.45 if (h, k, l) == (0, 0, 0) else base_size
        ax.scatter([x], [y], s=size, c="#111")
        if (h, k, l) in labels or (h, k, l) == (0, 0, 0):
            ax.text(x + 0.08, y + 0.08, _format_hkl_label(h, k, l), fontsize=8)
    xs = [item[3] for item in projected_points] or [-1, 1]
    ys = [item[4] for item in projected_points] or [-1, 1]
    xpad = max(0.8, (max(xs) - min(xs)) * 0.12)
    ypad = max(0.8, (max(ys) - min(ys)) * 0.12)
    xmin, xmax = min(xs) - xpad, max(xs) + xpad
    ymin, ymax = min(ys) - ypad, max(ys) + ypad
    ax.axhline(0, color="#ddd", lw=0.8)
    ax.axvline(0, color="#ddd", lw=0.8)
    ax.annotate("", xy=(xmax - 0.15, 0), xytext=(xmax - 1.0, 0), arrowprops={"arrowstyle": "->", "lw": 1.0, "color": "#666"})
    ax.annotate("", xy=(0, ymax - 0.15), xytext=(0, ymax - 1.0), arrowprops={"arrowstyle": "->", "lw": 1.0, "color": "#666"})
    if basis_labels[0]:
        ax.text(xmax - 0.95, 0.16, basis_labels[0], fontsize=8, color="#555")
    if basis_labels[1]:
        ax.text(0.16, ymax - 0.85, basis_labels[1], fontsize=8, color="#555")
    ax.text(
        xmin + 0.12,
        ymax - 0.38,
        f"zone axis [{u} {v} {w}]",
        fontsize=8,
        color="#555",
        ha="left",
        va="top",
    )
    ax.set_xlim(xmin, xmax)
    ax.set_ylim(ymin, ymax)
    ax.set_aspect("equal", adjustable="box")
    ax.axis("off")
    title = spec.get("title") or spec.get("caption") or f"[{u} {v} {w}] 带轴电子衍射花样"
    ax.set_title(_wrap_plot_title(title, width=28), fontsize=11)
    fig.tight_layout()
    output.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(output)
    plt.close(fig)


def draw_xrd_pattern(spec: dict[str, Any], output: Path) -> None:
    spec = normalize_figure_spec(spec)
    peaks = spec.get("peaks") if isinstance(spec.get("peaks"), list) else []
    if not peaks:
        peaks = [
            {"two_theta": 32, "intensity": 0.45, "label": "(111)"},
            {"two_theta": 45, "intensity": 1.0, "label": "(200)"},
            {"two_theta": 56, "intensity": 0.6, "label": "(220)"},
            {"two_theta": 75, "intensity": 0.35, "label": "(311)"},
        ]
    fig, ax = plt.subplots(figsize=(6.0, 3.4), dpi=180)
    xs: list[float] = []
    legend_seen: set[str] = set()
    for peak in peaks:
        position = _normalized_peak_position(peak)
        if position is None:
            continue
        x = float(position)
        xs.append(x)
        y = float(peak.get("intensity") or peak.get("height") or 0.5)
        style = str(peak.get("style") or peak.get("linestyle") or "-")
        is_super = style in {"--", ":", "-."}
        color = str(peak.get("color") or ("#b45309" if is_super else "#111"))
        raw_legend = peak.get("phase_label") or ("新增超结构峰" if is_super else "原有峰")
        legend_label = str(raw_legend) if str(raw_legend) not in legend_seen else None
        if legend_label:
            legend_seen.add(str(raw_legend))
        ax.vlines(x, 0, y, color=color, lw=2.0, linestyles=style, label=legend_label)
        if peak.get("label"):
            ax.text(x, y + 0.04, str(peak.get("label")), fontsize=8, ha="center", color=color)
    ax.set_xlabel(spec.get("x_label", "2θ / °"))
    ax.set_ylabel(spec.get("y_label", "Intensity / a.u."))
    ax.set_ylim(0, max(float(p.get("intensity") or p.get("height") or 0.5) for p in peaks) * 1.25)
    if xs:
        span = max(xs) - min(xs)
        pad = max(3.0, span * 0.08)
        ax.set_xlim(min(xs) - pad, max(xs) + pad)
    ax.set_title(_wrap_plot_title(spec.get("title") or spec.get("caption") or "XRD 衍射峰示意图"), fontsize=11, pad=10)
    ax.grid(True, axis="y", alpha=0.18)
    if legend_seen:
        ax.legend(fontsize=8, frameon=False, loc="lower right")
    fig.tight_layout()
    output.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(output)
    plt.close(fig)


def draw_microstructure_schematic(spec: dict[str, Any], output: Path) -> None:
    fig, ax = plt.subplots(figsize=(5.2, 3.8), dpi=180)
    ax.set_aspect("equal")
    ax.axis("off")
    grain_centers = [(0.1, 0.2), (0.45, 0.22), (0.82, 0.2), (0.25, 0.62), (0.65, 0.62)]
    for idx, (x, y) in enumerate(grain_centers):
        ax.add_patch(Ellipse((x, y), 0.42, 0.32, angle=idx * 25, fill=False, edgecolor="#111", linewidth=1.4))
    features = spec.get("features") if isinstance(spec.get("features"), list) else []
    if not features:
        features = [{"label": "第二相/析出物", "xy": [0.5, 0.5]}, {"label": "晶界", "xy": [0.18, 0.44]}]
    for item in features:
        xy = _xy(item.get("xy"), (0.5, 0.5)) if isinstance(item, dict) else (0.5, 0.5)
        ax.scatter([xy[0]], [xy[1]], s=36, color="#2563eb")
        label = str(item.get("label") or "") if isinstance(item, dict) else ""
        if label:
            ax.text(xy[0] + 0.03, xy[1] + 0.03, label, fontsize=8, color="#2563eb")
    ax.text(0.5, -0.08, str(spec.get("matrix_label") or "基体晶粒"), fontsize=9, ha="center")
    ax.set_xlim(-0.15, 1.15)
    ax.set_ylim(-0.15, 1.0)
    ax.set_title(spec.get("title") or spec.get("caption") or "显微组织示意图", fontsize=11)
    fig.tight_layout()
    output.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(output)
    plt.close(fig)


def draw_fe_c_phase_diagram(spec: dict[str, Any], output: Path) -> None:
    fig, ax = plt.subplots(figsize=(6.2, 4.0), dpi=180)
    ax.plot([0, 4.3, 6.67], [1538, 1147, 1227], color="#111", lw=1.7, label="液相线")
    ax.plot([0, 0.77, 2.11, 4.3, 6.67], [912, 727, 1147, 1147, 727], color="#111", lw=1.5)
    ax.axhline(727, color="#666", ls="--", lw=1.0)
    ax.text(0.35, 820, "α + γ", fontsize=8)
    ax.text(1.2, 980, "γ", fontsize=9)
    ax.text(3.0, 1220, "L + γ", fontsize=8)
    ax.text(4.3, 1165, "E", fontsize=9)
    ax.text(0.77, 750, "S", fontsize=9)
    ax.text(5.1, 900, "Fe3C", fontsize=8)
    ax.set_xlim(0, 6.67)
    ax.set_ylim(600, 1600)
    ax.set_xlabel("w(C) / %")
    ax.set_ylabel("T / °C")
    ax.set_title(spec.get("title") or spec.get("caption") or "Fe-C 相图示意图", fontsize=11)
    ax.grid(True, alpha=0.16)
    fig.tight_layout()
    output.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(output)
    plt.close(fig)


def draw_ttt_diagram(spec: dict[str, Any], output: Path) -> None:
    fig, ax = plt.subplots(figsize=(5.8, 3.8), dpi=180)
    y = [720, 650, 560, 460, 360, 300]
    start_x = [0.8, 1.2, 2.2, 4.5, 8, 14]
    finish_x = [3, 5, 9, 18, 40, 80]
    ax.semilogx(start_x, y, color="#111", lw=1.8, label="开始")
    ax.semilogx(finish_x, y, color="#111", lw=1.8, ls="--", label="终了")
    ax.axhline(float(spec.get("ms_temperature") or 230), color="#dc2626", lw=1.2)
    ax.text(1.1, 690, "珠光体区", fontsize=8)
    ax.text(10, 410, "贝氏体区", fontsize=8)
    ax.text(0.12, float(spec.get("ms_temperature") or 230) + 8, "Ms", fontsize=8, color="#dc2626")
    ax.set_xlabel("t / s")
    ax.set_ylabel("T / °C")
    ax.set_title(spec.get("title") or spec.get("caption") or "TTT 等温转变曲线", fontsize=11)
    ax.grid(True, alpha=0.18)
    ax.legend(fontsize=8)
    fig.tight_layout()
    output.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(output)
    plt.close(fig)


def draw_stress_strain_curve(spec: dict[str, Any], output: Path) -> None:
    points = spec.get("points") or [[0, 0], [0.01, 120], [0.04, 180], [0.16, 330], [0.25, 360], [0.34, 310]]
    fig, ax = plt.subplots(figsize=(5.8, 3.6), dpi=180)
    xs = [float(p[0]) for p in points]
    ys = [float(p[1]) for p in points]
    ax.plot(xs, ys, color="#111", lw=1.9)
    for label, idx in [("弹性段", 1), ("屈服/强化", 2), ("抗拉强度", 4), ("断裂", 5)]:
        if idx < len(points):
            ax.text(xs[idx], ys[idx] + 14, label, fontsize=8)
    ax.set_xlabel("ε")
    ax.set_ylabel("σ / MPa")
    ax.set_title(spec.get("title") or spec.get("caption") or "应力-应变曲线", fontsize=11)
    ax.grid(True, alpha=0.18)
    fig.tight_layout()
    output.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(output)
    plt.close(fig)


def draw_dsc_curve(spec: dict[str, Any], output: Path) -> None:
    fig, ax = plt.subplots(figsize=(6.0, 3.4), dpi=180)
    xs = [20, 50, 80, 110, 140, 170, 200, 230]
    ys = [0.0, 0.02, -0.05, -0.08, 0.28, 0.05, -0.42, 0.0]
    ax.plot(xs, ys, color="#111", lw=1.8)
    tg = float(spec.get("tg") or 85)
    tc = float(spec.get("tc") or 145)
    tm = float(spec.get("tm") or 205)
    for x, label in [(tg, "Tg"), (tc, "Tc"), (tm, "Tm")]:
        ax.axvline(x, color="#666", ls="--", lw=0.9)
        ax.text(x + 2, 0.32 if label != "Tm" else -0.34, label, fontsize=8)
    ax.set_xlabel("T / °C")
    ax.set_ylabel("Heat flow")
    ax.set_title(spec.get("title") or spec.get("caption") or "DSC 曲线示意图", fontsize=11)
    ax.grid(True, alpha=0.16)
    fig.tight_layout()
    output.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(output)
    plt.close(fig)


def draw_polymer_chain_structure(spec: dict[str, Any], output: Path) -> None:
    chain_type = str(spec.get("chain_type") or "linear").lower()
    fig, ax = plt.subplots(figsize=(5.8, 3.4), dpi=180)
    ax.axis("off")
    xs = [i * 0.16 for i in range(24)]
    ys = [0.5 + 0.08 * ((i % 4) - 1.5) for i in range(24)]
    ax.plot(xs, ys, color="#111", lw=1.8)
    for x, y in zip(xs[::2], ys[::2]):
        ax.scatter([x], [y], s=24, color="#2563eb")
    if chain_type in {"branched", "branch", "支化", "crosslinked", "交联", "network"}:
        for idx in (6, 12, 18):
            ax.plot([xs[idx], xs[idx] + 0.18], [ys[idx], ys[idx] + 0.32], color="#111", lw=1.4)
            ax.scatter([xs[idx] + 0.18], [ys[idx] + 0.32], s=24, color="#2563eb")
    if chain_type in {"crosslinked", "交联", "network", "网状"}:
        ax.plot([xs[7], xs[15]], [ys[7] + 0.25, ys[15] - 0.18], color="#dc2626", lw=1.3)
        ax.text(1.55, 0.18, "交联键", fontsize=8, color="#dc2626")
    ax.set_title(spec.get("title") or spec.get("caption") or "高分子链结构示意图", fontsize=11)
    fig.tight_layout()
    output.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(output)
    plt.close(fig)


def draw_ceramic_crystal_structure(spec: dict[str, Any], output: Path) -> None:
    draw_crystal_unit_cell({**spec, "structure": spec.get("structure") or "陶瓷晶体"}, output)


def draw_sintering_microstructure_evolution(spec: dict[str, Any], output: Path) -> None:
    fig, axes = plt.subplots(1, 3, figsize=(7.2, 2.8), dpi=180)
    titles = spec.get("stages") if isinstance(spec.get("stages"), list) else ["粉末接触", "烧结颈形成", "致密化/晶粒长大"]
    for idx, ax in enumerate(axes):
        ax.set_aspect("equal")
        ax.axis("off")
        ax.set_title(str(titles[idx] if idx < len(titles) else f"阶段{idx + 1}"), fontsize=9)
        ax.add_patch(Circle((-0.28, 0), 0.32, fill=False, edgecolor="#111", linewidth=1.5))
        ax.add_patch(Circle((0.28, 0), 0.32, fill=False, edgecolor="#111", linewidth=1.5))
        if idx >= 1:
            ax.add_patch(Ellipse((0, 0), 0.24 + idx * 0.12, 0.18 + idx * 0.08, fill=False, edgecolor="#2563eb", linewidth=1.6))
            ax.text(-0.15, 0.42, "烧结颈", fontsize=7, color="#2563eb")
        if idx == 2:
            ax.add_patch(Circle((0.0, -0.28), 0.08, fill=False, edgecolor="#dc2626", linewidth=1.2))
            ax.text(-0.18, -0.52, "残余孔隙", fontsize=7, color="#dc2626")
        ax.set_xlim(-0.75, 0.75)
        ax.set_ylim(-0.7, 0.7)
    fig.suptitle(spec.get("title") or spec.get("caption") or "烧结组织演化示意图", fontsize=11)
    fig.tight_layout()
    output.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(output)
    plt.close(fig)


def _draw_profile_curve(
    spec: dict[str, Any],
    output: Path,
    *,
    title: str,
    x_label: str,
    y_label: str,
    points: list[list[float]],
    labels: list[tuple[float, float, str]] | None = None,
    logx: bool = False,
) -> None:
    raw_points = spec.get("points") if isinstance(spec.get("points"), list) and spec.get("points") else points
    xs = [float(p[0]) for p in raw_points]
    ys = [float(p[1]) for p in raw_points]
    fig, ax = plt.subplots(figsize=(5.8, 3.6), dpi=180)
    if logx:
        ax.semilogx(xs, ys, color="#111", lw=1.8, marker="o")
    else:
        ax.plot(xs, ys, color="#111", lw=1.8, marker="o")
    for x, y, label in labels or []:
        ax.text(x, y, label, fontsize=8)
    ax.set_xlabel(spec.get("x_label", x_label))
    ax.set_ylabel(spec.get("y_label", y_label))
    ax.set_title(spec.get("title") or spec.get("caption") or title, fontsize=11)
    ax.grid(True, alpha=0.18)
    fig.tight_layout()
    output.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(output)
    plt.close(fig)


def _draw_simple_schematic(spec: dict[str, Any], output: Path, *, title: str, labels: list[str]) -> None:
    fig, ax = plt.subplots(figsize=(5.8, 3.6), dpi=180)
    ax.set_aspect("equal")
    ax.axis("off")
    for idx, label in enumerate(labels):
        x = 0.18 + 0.22 * (idx % 4)
        y = 0.72 - 0.28 * (idx // 4)
        ax.add_patch(Ellipse((x, y), 0.18, 0.12, angle=idx * 18, fill=False, edgecolor="#111", linewidth=1.4))
        ax.text(x, y, label, fontsize=8, ha="center", va="center")
    if len(labels) >= 2:
        for idx in range(min(len(labels) - 1, 3)):
            x0 = 0.18 + 0.22 * idx
            x1 = 0.18 + 0.22 * (idx + 1)
            ax.add_patch(FancyArrowPatch((x0 + 0.1, 0.72), (x1 - 0.1, 0.72), arrowstyle="->", mutation_scale=12, color="#2563eb", linewidth=1.1))
    ax.set_xlim(0, 1.05)
    ax.set_ylim(0.15, 0.95)
    ax.set_title(spec.get("title") or spec.get("caption") or title, fontsize=11)
    fig.tight_layout()
    output.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(output)
    plt.close(fig)


def draw_ternary_phase_diagram(spec: dict[str, Any], output: Path) -> None:
    components = spec.get("components") if isinstance(spec.get("components"), list) and len(spec.get("components")) >= 3 else ["A", "B", "C"]
    fig, ax = plt.subplots(figsize=(4.8, 4.4), dpi=180)
    triangle = [(0, 0), (1, 0), (0.5, 0.866), (0, 0)]
    ax.plot([p[0] for p in triangle], [p[1] for p in triangle], color="#111", lw=1.6)
    for frac in (0.25, 0.5, 0.75):
        ax.plot([frac, 0.5 + frac / 2], [0, 0.866 * (1 - frac)], color="#ddd", lw=0.8)
        ax.plot([1 - frac, 0.5 - frac / 2], [0, 0.866 * (1 - frac)], color="#ddd", lw=0.8)
        ax.plot([frac / 2, 1 - frac / 2], [0.866 * frac, 0.866 * frac], color="#ddd", lw=0.8)
    ax.text(-0.05, -0.06, str(components[0]), fontsize=10)
    ax.text(1.02, -0.06, str(components[1]), fontsize=10)
    ax.text(0.48, 0.92, str(components[2]), fontsize=10)
    regions = spec.get("phase_regions") if isinstance(spec.get("phase_regions"), list) else []
    if not regions:
        regions = [{"xy": [0.5, 0.48], "label": "单相区"}, {"xy": [0.28, 0.18], "label": "两相区"}, {"xy": [0.68, 0.2], "label": "三相区"}]
    for region in regions:
        if isinstance(region, dict):
            x, y = _xy(region.get("xy"), (0.5, 0.45))
            ax.text(x, y, str(region.get("label") or ""), fontsize=8, ha="center")
    ax.set_aspect("equal")
    ax.axis("off")
    ax.set_title(spec.get("title") or spec.get("caption") or "三元相图示意图", fontsize=11)
    fig.tight_layout()
    output.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(output)
    plt.close(fig)


def draw_process_flow_diagram(spec: dict[str, Any], output: Path) -> None:
    steps = spec.get("steps") if isinstance(spec.get("steps"), list) and spec.get("steps") else ["原料", "混合/成形", "热处理", "性能测试"]
    fig, ax = plt.subplots(figsize=(6.4, 2.4), dpi=180)
    ax.axis("off")
    xs = [0.12 + i * (0.76 / max(1, len(steps) - 1)) for i in range(len(steps))]
    for idx, (x, step) in enumerate(zip(xs, steps)):
        ax.text(x, 0.5, str(step), fontsize=9, ha="center", va="center", bbox={"boxstyle": "round,pad=0.25", "facecolor": "#f8fafc", "edgecolor": "#111", "linewidth": 1.0})
        if idx < len(steps) - 1:
            ax.add_patch(FancyArrowPatch((x + 0.07, 0.5), (xs[idx + 1] - 0.07, 0.5), arrowstyle="->", mutation_scale=12, color="#111", linewidth=1.2))
    ax.set_title(spec.get("title") or spec.get("caption") or "材料工艺流程图", fontsize=11)
    fig.tight_layout()
    output.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(output)
    plt.close(fig)


def draw_cct_diagram(spec: dict[str, Any], output: Path) -> None:
    fig, ax = plt.subplots(figsize=(5.8, 3.8), dpi=180)
    y = [720, 650, 560, 460, 360]
    ax.semilogx([1, 2, 5, 15, 50], y, color="#111", lw=1.7, label="转变开始")
    ax.semilogx([4, 8, 20, 70, 180], y, color="#111", lw=1.7, ls="--", label="转变终了")
    for idx, factor in enumerate([0.8, 2.2, 6.0]):
        ax.semilogx([factor, factor * 4, factor * 16, factor * 50], [760, 620, 450, 260], lw=1.1, label=f"冷却曲线{idx + 1}")
    ax.axhline(float(spec.get("ms_temperature") or 230), color="#dc2626", lw=1.1)
    ax.text(1.0, 240, "Ms", fontsize=8, color="#dc2626")
    ax.set_xlabel("t / s")
    ax.set_ylabel("T / °C")
    ax.set_title(spec.get("title") or spec.get("caption") or "CCT 连续冷却转变曲线", fontsize=11)
    ax.grid(True, alpha=0.18)
    ax.legend(fontsize=7)
    fig.tight_layout()
    output.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(output)
    plt.close(fig)


def draw_heat_treatment_curve(spec: dict[str, Any], output: Path) -> None:
    _draw_profile_curve(spec, output, title="热处理温度-时间曲线", x_label="t", y_label="T / °C", points=[[0, 25], [1, 850], [3, 850], [3.4, 60], [5, 200], [6.5, 200], [7, 25]], labels=[(1.5, 880, "保温"), (3.3, 120, "淬火"), (5.2, 230, "回火")])


def draw_creep_curve(spec: dict[str, Any], output: Path) -> None:
    _draw_profile_curve(spec, output, title="蠕变三阶段曲线", x_label="t", y_label="ε", points=[[0, 0], [1, 0.22], [3, 0.34], [5, 0.48], [6, 0.78], [6.6, 1.1]], labels=[(0.4, 0.16, "初始蠕变"), (2.8, 0.39, "稳态蠕变"), (5.35, 0.84, "加速蠕变")])


def draw_fatigue_sn_curve(spec: dict[str, Any], output: Path) -> None:
    _draw_profile_curve(spec, output, title="S-N 疲劳曲线", x_label="N", y_label="σa", points=[[1e3, 520], [1e4, 420], [1e5, 330], [1e6, 270], [1e7, 250]], labels=[(2e6, 260, "疲劳极限")], logx=True)


def draw_precipitation_aging_curve(spec: dict[str, Any], output: Path) -> None:
    _draw_profile_curve(spec, output, title="时效强化曲线", x_label="时效时间", y_label="硬度/强度", points=[[0, 0.2], [1, 0.55], [2.5, 0.95], [4, 0.82], [6, 0.55]], labels=[(0.5, 0.42, "欠时效"), (2.3, 1.0, "峰时效"), (4.2, 0.72, "过时效")])


def draw_corrosion_polarization_curve(spec: dict[str, Any], output: Path) -> None:
    _draw_profile_curve(spec, output, title="腐蚀极化曲线", x_label="log i", y_label="E", points=[[-3, -0.45], [-2, -0.28], [-1, -0.18], [0, -0.18], [1, 0.25]], labels=[(-2.4, -0.26, "Ecorr"), (0.05, -0.12, "钝化区")])


def draw_welding_thermal_cycle(spec: dict[str, Any], output: Path) -> None:
    _draw_profile_curve(spec, output, title="焊接热循环曲线", x_label="t", y_label="T / °C", points=[[0, 25], [0.6, 1350], [1.2, 900], [2.5, 500], [5, 150]], labels=[(0.58, 1390, "峰值温度"), (1.5, 700, "冷却阶段")])


def draw_tga_curve(spec: dict[str, Any], output: Path) -> None:
    _draw_profile_curve(spec, output, title="TGA 热重曲线", x_label="T / °C", y_label="质量保留率 / %", points=[[30, 100], [180, 98], [280, 84], [390, 38], [600, 22]], labels=[(300, 78, "分解阶段"), (520, 28, "残炭/残余")])


def draw_dma_curve(spec: dict[str, Any], output: Path) -> None:
    series = [
        {"label": "储能模量", "points": [[-80, 1.0], [-20, 0.85], [40, 0.28], [120, 0.12]]},
        {"label": "tanδ", "points": [[-80, 0.05], [-20, 0.12], [40, 0.65], [120, 0.16]]},
    ]
    draw_multi_curve_axis_plot({**spec, "series": spec.get("series") or series, "x_label": "T / °C", "y_label": "相对值", "caption": spec.get("caption") or "DMA 曲线"}, output)


def draw_viscoelastic_creep_curve(spec: dict[str, Any], output: Path) -> None:
    _draw_profile_curve(spec, output, title="黏弹性蠕变-回复曲线", x_label="t", y_label="ε", points=[[0, 0], [1, 0.42], [3, 0.62], [3.2, 0.32], [5, 0.18]], labels=[(1.2, 0.55, "加载蠕变"), (3.35, 0.3, "卸载回复"), (4.2, 0.2, "残余形变")])


def draw_stress_relaxation_curve(spec: dict[str, Any], output: Path) -> None:
    _draw_profile_curve(spec, output, title="应力松弛曲线", x_label="t", y_label="σ", points=[[0, 1.0], [1, 0.68], [2, 0.49], [4, 0.32], [8, 0.22]], labels=[(1.2, 0.7, "恒定应变")])


def draw_time_temperature_superposition(spec: dict[str, Any], output: Path) -> None:
    series = [
        {"label": "低温", "points": [[0.01, 0.85], [0.1, 0.7], [1, 0.48]]},
        {"label": "参考温度", "points": [[0.1, 0.85], [1, 0.7], [10, 0.48]]},
        {"label": "高温", "points": [[1, 0.85], [10, 0.7], [100, 0.48]]},
    ]
    draw_multi_curve_axis_plot({**spec, "series": spec.get("series") or series, "x_label": "约化时间", "y_label": "模量", "caption": spec.get("caption") or "时温等效主曲线"}, output)


def draw_polymer_stress_strain_curve(spec: dict[str, Any], output: Path) -> None:
    series = [
        {"label": "塑料", "points": [[0, 0], [0.05, 0.6], [0.25, 0.8], [0.6, 0.72]]},
        {"label": "橡胶", "points": [[0, 0], [0.8, 0.18], [2.0, 0.45], [4.0, 1.0]]},
        {"label": "纤维", "points": [[0, 0], [0.03, 1.05]]},
    ]
    draw_multi_curve_axis_plot({**spec, "series": spec.get("series") or series, "x_label": "ε", "y_label": "σ", "caption": spec.get("caption") or "高分子应力-应变曲线"}, output)


def draw_molecular_weight_distribution(spec: dict[str, Any], output: Path) -> None:
    points = [[1, 0.02], [2, 0.16], [3, 0.58], [4, 1.0], [5, 0.62], [6, 0.22], [7, 0.05]]
    _draw_profile_curve(spec, output, title="分子量分布曲线", x_label="log M", y_label="频率", points=points, labels=[(3.1, 0.72, "Mn"), (4.4, 0.86, "Mw")])


def draw_polymer_blend_phase_diagram(spec: dict[str, Any], output: Path) -> None:
    _draw_profile_curve(spec, output, title="高分子共混相图", x_label="组分 B 体积分数", y_label="T", points=[[0.05, 0.85], [0.25, 0.55], [0.5, 0.35], [0.75, 0.55], [0.95, 0.85]], labels=[(0.5, 0.72, "单相区"), (0.5, 0.25, "两相区")])


def draw_rheology_flow_curve(spec: dict[str, Any], output: Path) -> None:
    _draw_profile_curve(spec, output, title="流变流动曲线", x_label="剪切速率", y_label="黏度", points=[[0.1, 1.0], [1, 0.72], [10, 0.42], [100, 0.25]], labels=[(2, 0.65, "剪切变稀")], logx=True)


def draw_sintering_densification_curve(spec: dict[str, Any], output: Path) -> None:
    _draw_profile_curve(spec, output, title="烧结致密化曲线", x_label="烧结时间/温度", y_label="相对密度", points=[[0, 0.55], [1, 0.68], [2, 0.82], [4, 0.93], [6, 0.97]], labels=[(0.5, 0.64, "初期"), (2.0, 0.86, "中期"), (4.5, 0.96, "后期")])


def draw_ionic_conductivity_arrhenius(spec: dict[str, Any], output: Path) -> None:
    _draw_profile_curve(spec, output, title="离子电导 Arrhenius 曲线", x_label="1000/T", y_label="log(σT)", points=[[0.9, -0.8], [1.1, -1.1], [1.3, -1.45], [1.5, -1.82], [1.7, -2.1]], labels=[(1.25, -1.25, "斜率对应Ea")])


def draw_dielectric_temperature_curve(spec: dict[str, Any], output: Path) -> None:
    _draw_profile_curve(spec, output, title="介电常数-温度曲线", x_label="T / °C", y_label="εr", points=[[0, 120], [50, 180], [100, 850], [150, 220], [220, 130]], labels=[(100, 900, "Tc")])


def draw_hysteresis_loop(spec: dict[str, Any], output: Path, *, title: str, x_label: str, y_label: str, coercive_label: str, remanent_label: str) -> None:
    import math
    xs = [i / 50 for i in range(-150, 151)]
    upper = [math.tanh(2.2 * (x + 0.35)) for x in xs]
    lower = [math.tanh(2.2 * (x - 0.35)) for x in reversed(xs)]
    loop_x = xs + list(reversed(xs))
    loop_y = upper + lower
    fig, ax = plt.subplots(figsize=(4.8, 4.0), dpi=180)
    ax.plot(loop_x, loop_y, color="#111", lw=1.8)
    ax.axhline(0, color="#ddd", lw=0.8)
    ax.axvline(0, color="#ddd", lw=0.8)
    ax.text(0.42, 0.08, coercive_label, fontsize=8)
    ax.text(0.05, 0.58, remanent_label, fontsize=8)
    ax.set_xlabel(x_label)
    ax.set_ylabel(y_label)
    ax.set_title(spec.get("title") or spec.get("caption") or title, fontsize=11)
    ax.grid(True, alpha=0.16)
    fig.tight_layout()
    output.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(output)
    plt.close(fig)


def draw_ferroelectric_hysteresis_loop(spec: dict[str, Any], output: Path) -> None:
    draw_hysteresis_loop(spec, output, title="铁电 P-E 电滞回线", x_label="E", y_label="P", coercive_label="Ec", remanent_label="Pr")


def draw_magnetic_hysteresis_loop(spec: dict[str, Any], output: Path) -> None:
    draw_hysteresis_loop(spec, output, title="磁滞回线", x_label="H", y_label="M", coercive_label="Hc", remanent_label="Mr")


def draw_defect_structure_schematic(spec: dict[str, Any], output: Path) -> None:
    _draw_simple_schematic(spec, output, title="晶体缺陷结构示意图", labels=["空位", "间隙原子", "置换原子", "晶界"])


def draw_dislocation_schematic(spec: dict[str, Any], output: Path) -> None:
    custom = {**spec, "kind": "custom_diagram", "elements": [
        {"type": "line", "start": [0, 0.3], "end": [1, 0.3], "label": "滑移面"},
        {"type": "line", "start": [0.5, 0.0], "end": [0.5, 0.8], "label": "半原子面", "color": "#2563eb"},
        {"type": "arrow", "start": [0.35, 0.18], "end": [0.7, 0.18], "label": "b", "color": "#dc2626"},
        {"type": "text", "xy": [0.38, 0.86], "text": "刃型位错"},
    ]}
    draw_custom_diagram(custom, output)


def draw_slip_system_schematic(spec: dict[str, Any], output: Path) -> None:
    draw_crystal_plane_direction({**spec, "caption": spec.get("caption") or "滑移系示意图"}, output)


def draw_recrystallization_grain_growth(spec: dict[str, Any], output: Path) -> None:
    _draw_simple_schematic(spec, output, title="回复-再结晶-晶粒长大示意图", labels=["冷变形组织", "回复", "再结晶形核", "晶粒长大"])


def draw_polymer_configuration_conformation(spec: dict[str, Any], output: Path) -> None:
    _draw_simple_schematic(spec, output, title="高分子构型/构象示意图", labels=["等规", "间规", "无规", "链段构象"])


def draw_polymer_crystalline_morphology(spec: dict[str, Any], output: Path) -> None:
    _draw_simple_schematic(spec, output, title="高分子晶态形貌示意图", labels=["晶区", "非晶区", "片晶", "折叠链"])


def draw_spherulite_schematic(spec: dict[str, Any], output: Path) -> None:
    fig, ax = plt.subplots(figsize=(4.4, 4.0), dpi=180)
    ax.set_aspect("equal")
    ax.axis("off")
    ax.add_patch(Circle((0, 0), 1.0, fill=False, edgecolor="#111", linewidth=1.5))
    ax.axhline(0, color="#111", lw=1.0)
    ax.axvline(0, color="#111", lw=1.0)
    for angle in range(0, 180, 20):
        import math
        x = math.cos(math.radians(angle))
        y = math.sin(math.radians(angle))
        ax.plot([-x, x], [-y, y], color="#2563eb", lw=0.8, alpha=0.75)
    ax.scatter([0], [0], s=35, color="#dc2626")
    ax.text(0.06, 0.06, "晶核", fontsize=8, color="#dc2626")
    ax.text(0.35, 0.7, "径向片晶", fontsize=8, color="#2563eb")
    ax.set_title(spec.get("title") or spec.get("caption") or "高分子球晶示意图", fontsize=11)
    fig.tight_layout()
    output.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(output)
    plt.close(fig)


def draw_silicate_structure_schematic(spec: dict[str, Any], output: Path) -> None:
    _draw_simple_schematic(spec, output, title="硅酸盐结构示意图", labels=["硅氧四面体", "链状", "层状", "架状"])


def draw_glass_network_structure(spec: dict[str, Any], output: Path) -> None:
    _draw_simple_schematic(spec, output, title="玻璃网络结构示意图", labels=["网络形成体", "桥氧", "修饰体", "非桥氧"])


def draw_ceramic_phase_diagram(spec: dict[str, Any], output: Path) -> None:
    if str(spec.get("diagram_type") or "").lower() == "ternary":
        draw_ternary_phase_diagram(spec, output)
    else:
        draw_binary_phase_diagram({**spec, "caption": spec.get("caption") or "陶瓷相图示意图"}, output)


def draw_porous_ceramic_microstructure(spec: dict[str, Any], output: Path) -> None:
    _draw_simple_schematic(spec, output, title="多孔陶瓷组织示意图", labels=["晶粒", "连通孔", "闭口孔", "晶界"])


def draw_defect_chemistry_diagram(spec: dict[str, Any], output: Path) -> None:
    _draw_simple_schematic(spec, output, title="陶瓷缺陷化学示意图", labels=["氧空位", "阳离子空位", "间隙离子", "缺陷反应"])


def draw_fracture_toughness_schematic(spec: dict[str, Any], output: Path) -> None:
    custom = {**spec, "kind": "custom_diagram", "elements": [
        {"type": "line", "start": [0, 0.5], "end": [0.25, 0.5], "label": "裂纹", "linewidth": 2},
        {"type": "line", "start": [0.25, 0.5], "end": [0.42, 0.65], "linewidth": 2},
        {"type": "line", "start": [0.42, 0.65], "end": [0.65, 0.38], "label": "裂纹偏转", "linewidth": 2},
        {"type": "ellipse", "center": [0.72, 0.45], "width": 0.25, "height": 0.13, "label": "桥联/增韧相", "color": "#2563eb"},
        {"type": "arrow", "start": [0.05, 0.76], "end": [0.28, 0.56], "label": "扩展方向", "color": "#dc2626"},
    ]}
    draw_custom_diagram(custom, output)


def _figure_needed(question: dict[str, Any]) -> bool:
    return question_has_type(question, "作图题")


def _block_plain_text(fragment: dict[str, Any], labels: set[str] | None = None, max_chars: int = 1200) -> str:
    parts: list[str] = []
    for block in fragment.get("blocks", []):
        label = str(block.get("label", "")).strip()
        if labels is not None and label not in labels:
            continue
        for seg in block.get("segments", []):
            if isinstance(seg, dict) and seg.get("type") == "text":
                text = str(seg.get("text", "")).strip()
                if text:
                    parts.append(text)
    return "\n".join(parts)[:max_chars]


def _direct_figure_prompt(question: dict[str, Any], fragment: dict[str, Any], specs: list[dict[str, Any]]) -> str:
    stem = str(question.get("stem") or "").strip()
    answer = str(fragment.get("answer_summary") or fragment.get("answer") or "").strip()
    analysis = _block_plain_text(fragment, {"解析", "解题步骤", "易错点及注意事项"}, max_chars=1200)
    spec_text = json.dumps(specs[:2], ensure_ascii=False)[:1200] if specs else ""
    return "\n".join(
        [
            "请直接生成一张可插入真题解析册的学术作图图片。",
            "要求：白底，清晰黑白线稿为主，必要时使用少量低饱和颜色；中文、符号、箭头和标签必须清楚可读；不要生成照片风格、装饰背景、水印、Logo 或无关文字。",
            "图必须严格服务题目要求。若题目要求曲线、组织示意、相图、晶胞、衍射花样、弯曲液面、电池结构等，请把关键对象、方向、坐标轴、标签和图注画完整。",
            "如果有多个小图，请使用整齐的多面板布局，每个小图要有清楚标签。",
            f"题目：{stem}",
            f"答案要点：{answer}" if answer else "",
            f"解析上下文：{analysis}" if analysis else "",
            f"已有结构化作图要求：{spec_text}" if spec_text else "",
            "最终图片中不要出现 figure_specs、占位符、JSON、代码、内部字段或题目无关说明。",
        ]
    ).strip()


def _direct_model_figure_id(qid: str) -> str:
    return f"{qid}_model_fig_01"


def _explicit_figure_specs(fragment: dict[str, Any], qid: str) -> list[dict[str, Any]]:
    draft = fragment.get("_draft") if isinstance(fragment.get("_draft"), dict) else {}
    raw_specs = draft.get("figure_specs") or draft.get("diagram_specs") or fragment.get("figure_specs") or []
    if isinstance(raw_specs, dict):
        raw_specs = [raw_specs]
    if not isinstance(raw_specs, list):
        return []
    specs: list[dict[str, Any]] = []
    for index, raw in enumerate(raw_specs, start=1):
        if not isinstance(raw, dict):
            continue
        spec = dict(raw)
        spec.setdefault("question_id", qid)
        spec.setdefault("figure_id", f"{qid}_fig_{index:02d}")
        registry_entry = get_schema(str(spec.get("kind") or ""))
        if registry_entry:
            spec.setdefault("schema_id", registry_entry["schema_id"])
            spec.setdefault("renderer", registry_entry["renderer"])
            spec.setdefault("schema_status", "schema_found")
        if spec.get("kind") == "custom_diagram":
            issues = validate_custom_diagram_spec(spec)
            if issues:
                spec["validation_issues"] = issues
        specs.append(spec)
    return specs


def _explicit_drawing_code_specs(fragment: dict[str, Any], qid: str) -> list[dict[str, Any]]:
    draft = fragment.get("_draft") if isinstance(fragment.get("_draft"), dict) else {}
    raw_specs = (
        draft.get("drawing_code_specs")
        or draft.get("drawing_codes")
        or draft.get("drawing_code")
        or fragment.get("drawing_code_specs")
        or fragment.get("drawing_codes")
        or fragment.get("drawing_code")
        or []
    )
    if isinstance(raw_specs, str):
        raw_specs = [{"code": raw_specs}]
    if isinstance(raw_specs, dict):
        raw_specs = [raw_specs]
    if not isinstance(raw_specs, list):
        return []
    specs: list[dict[str, Any]] = []
    for index, raw in enumerate(raw_specs, start=1):
        if not isinstance(raw, dict):
            continue
        code = str(raw.get("code") or "").strip()
        if not code:
            continue
        spec = dict(raw)
        if not str(spec.get("question_id") or "").strip():
            spec["question_id"] = qid
        if not str(spec.get("figure_id") or "").strip():
            spec["figure_id"] = f"{qid}_code_fig_{index:02d}"
        spec["figure_id"] = re.sub(r"[^0-9A-Za-z_.-]+", "_", str(spec.get("figure_id") or "").strip()).strip("._")
        if not str(spec.get("figure_id") or "").strip():
            spec["figure_id"] = f"{qid}_code_fig_{index:02d}"
        spec.setdefault("kind", "model_drawing_code")
        spec.setdefault("caption", "题目图示")
        spec.setdefault("source", "answer_draft")
        specs.append(spec)
    return specs


def _figure_spec_for_question(question: dict[str, Any]) -> dict[str, Any] | None:
    if not _figure_needed(question):
        return None
    qid = str(question.get("question_id", "")).strip()
    if not qid:
        return None
    stem = str(question.get("stem", ""))
    figure_id = f"{qid}_fig_01"
    if "面心立方" in stem or "fcc" in stem.lower() or "晶胞" in stem:
        return {
            "figure_id": figure_id,
            "question_id": qid,
            "kind": "fcc_cell",
            "caption": "面心立方晶胞示意图",
        }
    if "倒易" in stem or "衍射" in stem:
        return {
            "figure_id": figure_id,
            "question_id": qid,
            "kind": "diffraction_pattern",
            "caption": "倒易点阵/衍射花样示意图",
            "points": [
                {"xy": [0, 0], "label": "000", "size": 40},
                {"xy": [1, 0], "label": "200", "size": 44},
                {"xy": [0, 1], "label": "020", "size": 44},
                {"xy": [1, 1], "label": "220", "size": 44},
                {"xy": [1.5, 0.5], "label": "311", "size": 36},
            ],
        }
    if (
        "弯曲液面" in stem
        or "附加压力" in stem
        or ("表面张力" in stem and "曲率" in stem)
        or "Laplace" in stem
        or "拉普拉斯" in stem
    ):
        return {
            "figure_id": figure_id,
            "question_id": qid,
            "kind": "curved_liquid_surface",
            "caption": "弯曲液面附加压力示意图",
        }
    return None


def _insert_figure_block(fragment: dict[str, Any], spec: dict[str, Any]) -> None:
    figure_id = str(spec.get("figure_id", ""))
    rel_path = f"figures/{figure_id}.png"
    new_segments = [
        {"type": "image_ref", "image_id": figure_id, "path": rel_path},
        {"type": "text", "text": str(spec.get("caption") or "题目图示")},
    ]
    blocks = list(fragment.get("blocks", []))
    for block in blocks:
        if str(block.get("label", "")).strip() == "图示":
            segments = block.get("segments", []) if isinstance(block.get("segments"), list) else []
            filtered: list[dict[str, Any]] = []
            idx = 0
            while idx < len(segments):
                segment = segments[idx]
                if isinstance(segment, dict) and segment.get("type") == "image_ref" and str(segment.get("image_id") or "") == figure_id:
                    idx += 1
                    if idx < len(segments):
                        next_segment = segments[idx]
                        if isinstance(next_segment, dict) and next_segment.get("type") == "text":
                            idx += 1
                    continue
                if isinstance(segment, dict):
                    filtered.append(segment)
                idx += 1
            block["segments"] = filtered + new_segments
            fragment["blocks"] = blocks
            return
    insert_at = 0
    for idx, block in enumerate(blocks):
        if str(block.get("label", "")).strip() == "教材依据":
            insert_at = idx + 1
            break
    blocks.insert(
        insert_at,
        {
            "label": "图示",
            "segments": new_segments,
        },
    )
    fragment["blocks"] = blocks


def _archive_generated_stage_images(stage_dir: Path, image_paths: list[Path], specs: list[dict[str, Any]], stage_label: str) -> None:
    if not image_paths:
        return
    specs_by_id = {
        str(spec.get("figure_id") or "").strip(): spec
        for spec in specs
        if isinstance(spec, dict) and str(spec.get("figure_id") or "").strip()
    }
    manifest_path = stage_dir / "figure_stage_images.json"
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8")) if manifest_path.exists() else {}
    except Exception:
        manifest = {}
    entries = [entry for entry in manifest.get("items", []) if isinstance(entry, dict)] if isinstance(manifest, dict) else []
    archive_dir_name = re.sub(r"[^0-9A-Za-z_.-]+", "_", str(stage_label or "stage")).strip("._") or "stage"
    archive_root = stage_dir / "figure_stage_images" / archive_dir_name
    archive_root.mkdir(parents=True, exist_ok=True)
    for image in image_paths:
        if not image.exists():
            continue
        figure_id = image.stem
        spec = specs_by_id.get(figure_id, {})
        sequence = len(entries) + 1
        archived = archive_root / f"{sequence:03d}_{figure_id}.png"
        while archived.exists():
            sequence += 1
            archived = archive_root / f"{sequence:03d}_{figure_id}.png"
        shutil.copy2(image, archived)
        entries.append(
            {
                "stage": stage_label,
                "question_id": str(spec.get("question_id") or "").strip(),
                "figure_id": figure_id,
                "kind": str(spec.get("kind") or "").strip(),
                "source": str(spec.get("source") or "").strip(),
                "original_path": str(image),
                "path": str(archived),
            }
        )
    manifest_path.write_text(
        json.dumps({"schema_version": "answer_book.figure_stage_images.v1", "items": entries}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def prepare_figures_for_fragments(
    structured_exam: dict[str, Any],
    fragments_json: Path,
    specs_json: Path,
    output_dir: Path,
    provider: Any | None = None,
    model: str = "",
    code_provider: Any | None = None,
    code_model: str = "",
    progress_callback: Any | None = None,
) -> list[Path]:
    def report(event: str, **detail: Any) -> None:
        if callable(progress_callback):
            progress_callback(event, detail)
    def synced_specs() -> list[dict[str, Any]]:
        try:
            current = json.loads(specs_json.read_text(encoding="utf-8"))
        except Exception:
            return specs
        return [item for item in current.get("figures", []) if isinstance(item, dict)]

    fragments_data = json.loads(fragments_json.read_text(encoding="utf-8"))
    fragments_by_id = {
        str(fragment.get("question_id", "")).strip(): fragment
        for fragment in fragments_data.get("fragments", [])
        if str(fragment.get("question_id", "")).strip()
    }
    specs: list[dict[str, Any]] = []
    fragments_by_figure_id: dict[str, dict[str, Any]] = {}
    direct_report: dict[str, Any] = {
        "enabled": bool(provider is not None and getattr(provider, "api_key", "") and provider_supports_image_generation(provider)),
        "provider": getattr(provider, "name", "") if provider is not None else "",
        "image_model": getattr(provider, "image_model", "") if provider is not None else "",
        "image_size": getattr(provider, "image_size", "") if provider is not None else "",
        "generated": [],
        "failed": [],
        "skipped": [],
    }
    code_report: dict[str, Any] = {
        "enabled": bool(code_provider is not None and getattr(code_provider, "api_key", "")),
        "provider": getattr(code_provider, "name", "") if code_provider is not None else "",
        "model": str(code_model or getattr(code_provider, "default_model", "") or "") if code_provider is not None else "",
        "generated": [],
        "failed": [],
        "skipped": [],
    }
    direct_client = OpenAICompatibleClient(provider) if direct_report["enabled"] else None
    code_client = OpenAICompatibleClient(code_provider) if code_report["enabled"] else None
    questions_by_id = {
        str(question.get("question_id", "")).strip(): question
        for question in structured_exam.get("items", [])
        if str(question.get("question_id", "")).strip()
    }
    needed_question_ids: set[str] = set()
    for question in structured_exam.get("items", []):
        qid = str(question.get("question_id", "")).strip()
        fragment = fragments_by_id.get(qid)
        if not fragment:
            continue
        needs_figure = _figure_needed(question)
        mode = question_drawing_mode(question)
        question_specs: list[dict[str, Any]] = []
        if needs_figure and mode == "code":
            if code_client is not None:
                try:
                    report("drawing_code_request_started", question_id=qid, model=code_report["model"], phase="initial")
                    code_spec = generate_drawing_code_spec(
                        code_client,
                        question,
                        fragment,
                        model=str(code_report["model"]),
                        previous_issues=[],
                    )
                    figure_id = re.sub(r"[^0-9A-Za-z_.-]+", "_", str(code_spec.get("figure_id") or "").strip()).strip("._")
                    code_spec["figure_id"] = figure_id or f"{qid}_code_fig_01"
                    code_spec["question_id"] = qid
                    code_spec["kind"] = "model_drawing_code"
                    code_spec["source"] = "independent_code_generator"
                    question_specs = [code_spec]
                    code_report["generated"].append(
                        {
                            "question_id": qid,
                            "figure_id": code_spec.get("figure_id"),
                            "model": code_report["model"],
                            "reason": "default independent drawing code generation",
                        }
                    )
                    report("drawing_code_request_succeeded", question_id=qid, figure_id=code_spec.get("figure_id"), model=code_report["model"], phase="initial")
                except Exception as exc:
                    code_report["failed"].append(
                        {
                            "question_id": qid,
                            "error": str(exc)[:700],
                            "stage": "initial_independent_code_generation",
                        }
                    )
                    question_specs = _explicit_drawing_code_specs(fragment, qid)
                    for spec in question_specs:
                        spec["source"] = "answer_draft_fallback_after_code_generation_failure"
                    report("drawing_code_request_failed", question_id=qid, model=code_report["model"], phase="initial", error=str(exc)[:300])
            else:
                question_specs = _explicit_drawing_code_specs(fragment, qid)
                if question_specs:
                    code_report["skipped"].append(
                        {
                            "question_id": qid,
                            "reason": "independent drawing code provider is not configured; using answer_draft drawing_code_specs fallback",
                        }
                    )
        elif needs_figure:
            question_specs = _explicit_figure_specs(fragment, qid)
        if needs_figure and mode == "figure_specs" and not question_specs:
            spec = _figure_spec_for_question(question)
            question_specs = [spec] if spec else []
        if needs_figure:
            needed_question_ids.add(qid)
        for spec in question_specs:
            if not spec:
                continue
            spec = spec if str(spec.get("kind") or "") == "model_drawing_code" else normalize_figure_spec(spec)
            spec["drawing_generation_mode"] = mode
            specs.append(spec)
            fragments_by_figure_id[str(spec.get("figure_id", ""))] = fragment
    specs_json.parent.mkdir(parents=True, exist_ok=True)
    specs_json.write_text(json.dumps({"figures": specs, "direct_model_generation": direct_report, "drawing_code_generation": code_report}, ensure_ascii=False, indent=2), encoding="utf-8")
    report("figure_render_started", figure_count=len(specs), phase="initial")
    generated = generate_figures(specs_json, output_dir, progress_callback=progress_callback)
    _archive_generated_stage_images(specs_json.parent, generated, synced_specs(), "initial_render")
    report("figure_render_completed", generated_count=len(generated), phase="initial")
    refreshed_specs_data = json.loads(specs_json.read_text(encoding="utf-8")) if specs_json.exists() else {"figures": []}
    if _prune_stale_failed_code_specs(refreshed_specs_data, output_dir):
        specs_json.write_text(json.dumps(refreshed_specs_data, ensure_ascii=False, indent=2), encoding="utf-8")
    specs = synced_specs()
    generated_ids = {path.stem for path in generated}
    covered_qids = {
        str(spec.get("question_id") or "").strip()
        for spec in specs
        if str(spec.get("figure_id") or "").strip() in generated_ids
    }
    retry_code_specs: list[dict[str, Any]] = []
    for qid in sorted(needed_question_ids - covered_qids):
        question = questions_by_id.get(qid)
        fragment = fragments_by_id.get(qid)
        if not question or not fragment or question_drawing_mode(question) != "code":
            continue
        previous_issues: list[str] = []
        for spec in specs:
            if str(spec.get("question_id") or "") != qid:
                continue
            if str(spec.get("kind") or "") == "model_drawing_code":
                previous_issues.extend(validate_drawing_code(str(spec.get("code") or "")))
                run_issues = ((spec.get("run_result") or {}) if isinstance(spec.get("run_result"), dict) else {}).get("issues") or []
                previous_issues.extend(str(issue) for issue in run_issues)
        if code_client is None:
            code_report["skipped"].append({"question_id": qid, "reason": "drawing code was missing or failed, and text model provider is not configured for code retry"})
            continue
        try:
            report("drawing_code_request_started", question_id=qid, model=code_report["model"], phase="retry")
            retry_spec = generate_drawing_code_spec(
                code_client,
                question,
                fragment,
                model=str(code_report["model"]),
                previous_issues=previous_issues[:12],
            )
            figure_id = re.sub(r"[^0-9A-Za-z_.-]+", "_", str(retry_spec.get("figure_id") or "").strip()).strip("._")
            retry_spec["figure_id"] = figure_id or f"{qid}_code_fig_01"
            retry_spec["source"] = "model_retry"
            retry_spec["drawing_generation_mode"] = "code"
            retry_code_specs.append(retry_spec)
            fragments_by_figure_id[str(retry_spec.get("figure_id", ""))] = fragment
            code_report["generated"].append(
                {
                    "question_id": qid,
                    "figure_id": retry_spec.get("figure_id"),
                    "model": code_report["model"],
                    "reason": "initial drawing code missing or failed",
                }
            )
            report("drawing_code_request_succeeded", question_id=qid, figure_id=retry_spec.get("figure_id"), model=code_report["model"], phase="retry")
        except Exception as exc:
            code_report["failed"].append({"question_id": qid, "error": str(exc)[:700]})
            report("drawing_code_request_failed", question_id=qid, model=code_report["model"], phase="retry", error=str(exc)[:300])
    if retry_code_specs:
        specs.extend(retry_code_specs)
        specs_json.write_text(json.dumps({"figures": specs, "direct_model_generation": direct_report, "drawing_code_generation": code_report}, ensure_ascii=False, indent=2), encoding="utf-8")
        report("figure_render_started", figure_count=len(retry_code_specs), phase="retry")
        generated = generate_figures(specs_json, output_dir, progress_callback=progress_callback)
        _archive_generated_stage_images(specs_json.parent, generated, synced_specs(), "retry_render")
        report("figure_render_completed", generated_count=len(generated), phase="retry")
        refreshed_specs_data = json.loads(specs_json.read_text(encoding="utf-8")) if specs_json.exists() else {"figures": []}
        if _prune_stale_failed_code_specs(refreshed_specs_data, output_dir):
            specs_json.write_text(json.dumps(refreshed_specs_data, ensure_ascii=False, indent=2), encoding="utf-8")
        specs = synced_specs()
        generated_ids = {path.stem for path in generated}
        covered_qids = {
            str(spec.get("question_id") or "").strip()
            for spec in specs
            if str(spec.get("figure_id") or "").strip() in generated_ids
        }
    direct_generated_specs: list[dict[str, Any]] = []
    for qid in sorted(needed_question_ids - covered_qids):
        question = questions_by_id.get(qid)
        fragment = fragments_by_id.get(qid)
        if not question or not fragment:
            continue
        if direct_client is None:
            direct_report["skipped"].append(
                {
                    "question_id": qid,
                    "reason": "program/code figure generation could not render and image model is not configured or provider API key is missing",
                }
            )
            continue
        figure_id = _direct_model_figure_id(qid)
        output = output_dir / f"{figure_id}.png"
        prompt = _direct_figure_prompt(question, fragment, _explicit_figure_specs(fragment, qid))
        try:
            report("image_fallback_started", question_id=qid, figure_id=figure_id, model=getattr(provider, "image_model", ""))
            image_result = direct_client.generate_image(
                prompt,
                output,
                model=getattr(provider, "image_model", ""),
                size=getattr(provider, "image_size", "1024x1024"),
            )
            output = image_result.path
            if not output.exists() or output.stat().st_size <= 0:
                raise RuntimeError("image provider returned success but no image file was written")
            spec = {
                "figure_id": figure_id,
                "question_id": qid,
                "kind": "model_generated_image",
                "caption": "题目图示",
                "prompt": prompt,
                "provider": image_result.provider,
                "model": image_result.model,
                "path": str(output),
            }
            direct_generated_specs.append(spec)
            fragments_by_figure_id[figure_id] = fragment
            generated.append(output)
            generated_ids.add(figure_id)
            direct_report["generated"].append(
                {
                    "question_id": qid,
                    "figure_id": figure_id,
                    "model": image_result.model,
                    "path": str(output),
                }
            )
            report("image_fallback_succeeded", question_id=qid, figure_id=figure_id, model=image_result.model)
        except Exception as exc:
            direct_report["failed"].append(
                {
                    "question_id": qid,
                    "figure_id": figure_id,
                    "error": str(exc)[:500],
                    "fallback": "none",
                }
            )
            report("image_fallback_failed", question_id=qid, figure_id=figure_id, error=str(exc)[:300])
    if direct_generated_specs:
        specs.extend(direct_generated_specs)
        _archive_generated_stage_images(
            specs_json.parent,
            [Path(str(spec.get("path") or "")) for spec in direct_generated_specs],
            specs,
            "image_model_fallback",
        )
    specs_json.write_text(json.dumps({"figures": specs, "direct_model_generation": direct_report, "drawing_code_generation": code_report}, ensure_ascii=False, indent=2), encoding="utf-8")
    (specs_json.parent / "direct_model_figures.json").write_text(json.dumps(direct_report, ensure_ascii=False, indent=2), encoding="utf-8")
    (specs_json.parent / "drawing_code_generation.json").write_text(json.dumps(code_report, ensure_ascii=False, indent=2), encoding="utf-8")
    generated_ids = {path.stem for path in generated}
    audit_items: list[dict[str, Any]] = []
    audited_qids: set[str] = set()
    for spec in specs:
        qid = str(spec.get("question_id") or "").strip()
        figure_id = str(spec.get("figure_id") or "").strip()
        if not qid or not figure_id:
            continue
        audited_qids.add(qid)
        kind = str(spec.get("kind") or "")
        registry_entry = get_schema(kind)
        program_issues = program_check_figure_spec(spec)
        rendered = figure_id in generated_ids
        if kind == "model_drawing_code":
            code_issues = validate_drawing_code(str(spec.get("code") or ""))
            run_issues = ((spec.get("run_result") or {}) if isinstance(spec.get("run_result"), dict) else {}).get("issues") or []
            program_issues = [*program_issues, *code_issues, *[str(issue) for issue in run_issues]]
            risk_notes = [] if rendered and not program_issues else list(program_issues or ["模型代码绘图未能生成有效图片。"])
            audit_items.append(
                {
                    "question_id": qid,
                    "figure_id": figure_id,
                    "diagram_type": kind,
                    "schema_status": "model_drawing_code",
                    "schema_id": "",
                    "renderer": "model_code_drawer",
                    "generation_method": "model_code_renderer" if rendered else "none",
                    "needs_manual_review": bool(risk_notes),
                    "program_check_issues": program_issues,
                    "risk_notes": risk_notes,
                    "code_path": spec.get("code_path", ""),
                }
            )
            continue
        if kind == "model_generated_image":
            audit_items.append(
                {
                    "question_id": qid,
                    "figure_id": figure_id,
                    "diagram_type": kind,
                    "schema_status": "image_model_fallback",
                    "schema_id": "",
                    "renderer": "",
                    "generation_method": "image_model",
                    "needs_manual_review": True,
                    "program_check_issues": program_issues,
                    "risk_notes": ["未命中可渲染 schema 或程序绘图失败，已使用生图模型兜底，专业准确性需复核。"],
                }
            )
            continue
        risk_notes = list(program_issues)
        if not rendered:
            risk_notes.append("程序绘图未能生成有效图片，且生图模型未配置、失败或跳过；需要人工复核、修复绘图代码或新增 renderer。")
        schema_status = "schema_found" if registry_entry else str(spec.get("schema_status") or "legacy_programmatic")
        generation_method = "programmatic_renderer" if rendered else "none"
        if not rendered and not registry_entry:
            schema_status = "image_model_fallback"
            generation_method = "image_model"
        audit_items.append(
            {
                "question_id": qid,
                "figure_id": figure_id,
                "diagram_type": kind,
                "schema_status": schema_status,
                "schema_id": str(spec.get("schema_id") or (registry_entry or {}).get("schema_id") or ""),
                "renderer": str(spec.get("renderer") or (registry_entry or {}).get("renderer") or ""),
                "generation_method": generation_method,
                "needs_manual_review": bool(risk_notes),
                "program_check_issues": program_issues,
                "risk_notes": risk_notes,
            }
        )
    for qid in sorted(needed_question_ids - audited_qids):
        audit_items.append(
            {
                "question_id": qid,
                "figure_id": _direct_model_figure_id(qid),
                "diagram_type": "",
                "schema_status": "image_model_fallback",
                "schema_id": "",
                "renderer": "",
                "generation_method": "image_model",
                "needs_manual_review": True,
                "program_check_issues": [],
                "risk_notes": ["未获得可渲染程序作图输出，需走生图模型或人工补图。"],
            }
        )
    generation_audit = {
        "schema_version": "answer_book.figure_generation_audit.v1",
        "items": audit_items,
        "direct_model_generation": direct_report,
        "drawing_code_generation": code_report,
    }
    (specs_json.parent / "figure_generation_audit.json").write_text(json.dumps(generation_audit, ensure_ascii=False, indent=2), encoding="utf-8")
    for spec in specs:
        figure_id = str(spec.get("figure_id", ""))
        if figure_id in generated_ids and figure_id in fragments_by_figure_id:
            _insert_figure_block(fragments_by_figure_id[figure_id], spec)
    fragments_json.write_text(json.dumps(fragments_data, ensure_ascii=False, indent=2), encoding="utf-8")
    return generated


def generate_figures(specs_json: Path, output_dir: Path, progress_callback: Any | None = None) -> list[Path]:
    if not specs_json.exists():
        return []
    data = json.loads(specs_json.read_text(encoding="utf-8"))
    normalized_figures: list[dict[str, Any]] = []
    for raw_spec in data.get("figures", []):
        if isinstance(raw_spec, dict):
            spec = normalize_figure_spec(raw_spec)
            figure_id = re.sub(r"[^0-9A-Za-z_.-]+", "_", str(spec.get("figure_id") or "").strip()).strip("._")
            if not figure_id:
                continue
            spec["figure_id"] = figure_id
            normalized_figures.append(spec)
    data["figures"] = normalized_figures
    specs_json.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    generated: list[Path] = []
    for spec in normalized_figures:
        figure_id = str(spec.get("figure_id", "")).strip()
        if not figure_id:
            continue
        output = output_dir / f"{figure_id}.png"
        kind = str(spec.get("kind", "")).strip()
        validation_issues = program_check_figure_spec(spec)
        if validation_issues:
            spec["validation_issues"] = validation_issues
            if callable(progress_callback):
                progress_callback("figure_render_failed", {"figure_id": figure_id, "question_id": spec.get("question_id", ""), "error": "; ".join(validation_issues[:3])})
            continue
        spec.pop("validation_issues", None)
        if callable(progress_callback):
            progress_callback("figure_rendering", {"figure_id": figure_id, "question_id": spec.get("question_id", ""), "kind": kind})
        if kind == "phase_diagram":
            draw_phase_diagram(spec, output)
        elif kind == "line_chart":
            draw_line_chart(spec, output)
        elif kind == "generic_axis_curve":
            draw_generic_axis_curve(spec, output)
        elif kind == "multi_curve_axis_plot":
            draw_multi_curve_axis_plot(spec, output)
        elif kind == "binary_phase_diagram":
            draw_binary_phase_diagram(spec, output)
        elif kind == "ternary_phase_diagram":
            draw_ternary_phase_diagram(spec, output)
        elif kind == "diffraction_pattern":
            draw_diffraction_pattern(spec, output)
        elif kind == "fcc_cell":
            draw_fcc_cell(spec, output)
        elif kind == "crystal_unit_cell":
            draw_crystal_unit_cell(spec, output)
        elif kind == "crystal_plane_direction":
            draw_crystal_plane_direction(spec, output)
        elif kind == "zone_axis_diffraction":
            draw_zone_axis_diffraction(spec, output)
        elif kind == "xrd_pattern":
            draw_xrd_pattern(spec, output)
        elif kind == "model_drawing_code":
            code = str(spec.get("code") or "").strip()
            code_path = output_dir / f"{figure_id}.py"
            result = run_drawing_code(code, output, code_path)
            spec["code_path"] = result.code_path
            spec["run_result"] = {
                "ok": result.ok,
                "issues": result.issues,
                "stdout": result.stdout,
                "stderr": result.stderr,
                "returncode": result.returncode,
            }
            if not result.ok:
                spec["validation_issues"] = result.issues or ["drawing code execution failed"]
                if callable(progress_callback):
                    progress_callback("figure_render_failed", {"figure_id": figure_id, "question_id": spec.get("question_id", ""), "error": "; ".join((result.issues or ["drawing code execution failed"])[:3])})
                continue
        elif kind == "microstructure_schematic":
            draw_microstructure_schematic(spec, output)
        elif kind == "defect_structure_schematic":
            draw_defect_structure_schematic(spec, output)
        elif kind == "process_flow_diagram":
            draw_process_flow_diagram(spec, output)
        elif kind == "fe_c_phase_diagram":
            draw_fe_c_phase_diagram(spec, output)
        elif kind == "ttt_diagram":
            draw_ttt_diagram(spec, output)
        elif kind == "cct_diagram":
            draw_cct_diagram(spec, output)
        elif kind == "heat_treatment_curve":
            draw_heat_treatment_curve(spec, output)
        elif kind == "stress_strain_curve":
            draw_stress_strain_curve(spec, output)
        elif kind == "creep_curve":
            draw_creep_curve(spec, output)
        elif kind == "fatigue_sn_curve":
            draw_fatigue_sn_curve(spec, output)
        elif kind == "dislocation_schematic":
            draw_dislocation_schematic(spec, output)
        elif kind == "slip_system_schematic":
            draw_slip_system_schematic(spec, output)
        elif kind == "precipitation_aging_curve":
            draw_precipitation_aging_curve(spec, output)
        elif kind == "recrystallization_grain_growth":
            draw_recrystallization_grain_growth(spec, output)
        elif kind == "corrosion_polarization_curve":
            draw_corrosion_polarization_curve(spec, output)
        elif kind == "welding_thermal_cycle":
            draw_welding_thermal_cycle(spec, output)
        elif kind == "dsc_curve":
            draw_dsc_curve(spec, output)
        elif kind == "polymer_chain_structure":
            draw_polymer_chain_structure(spec, output)
        elif kind == "polymer_configuration_conformation":
            draw_polymer_configuration_conformation(spec, output)
        elif kind == "polymer_crystalline_morphology":
            draw_polymer_crystalline_morphology(spec, output)
        elif kind == "spherulite_schematic":
            draw_spherulite_schematic(spec, output)
        elif kind == "tga_curve":
            draw_tga_curve(spec, output)
        elif kind == "dma_curve":
            draw_dma_curve(spec, output)
        elif kind == "viscoelastic_creep_curve":
            draw_viscoelastic_creep_curve(spec, output)
        elif kind == "stress_relaxation_curve":
            draw_stress_relaxation_curve(spec, output)
        elif kind == "time_temperature_superposition":
            draw_time_temperature_superposition(spec, output)
        elif kind == "polymer_stress_strain_curve":
            draw_polymer_stress_strain_curve(spec, output)
        elif kind == "molecular_weight_distribution":
            draw_molecular_weight_distribution(spec, output)
        elif kind == "polymer_blend_phase_diagram":
            draw_polymer_blend_phase_diagram(spec, output)
        elif kind == "rheology_flow_curve":
            draw_rheology_flow_curve(spec, output)
        elif kind == "ceramic_crystal_structure":
            draw_ceramic_crystal_structure(spec, output)
        elif kind == "silicate_structure_schematic":
            draw_silicate_structure_schematic(spec, output)
        elif kind == "glass_network_structure":
            draw_glass_network_structure(spec, output)
        elif kind == "ceramic_phase_diagram":
            draw_ceramic_phase_diagram(spec, output)
        elif kind == "sintering_densification_curve":
            draw_sintering_densification_curve(spec, output)
        elif kind == "sintering_microstructure_evolution":
            draw_sintering_microstructure_evolution(spec, output)
        elif kind == "porous_ceramic_microstructure":
            draw_porous_ceramic_microstructure(spec, output)
        elif kind == "defect_chemistry_diagram":
            draw_defect_chemistry_diagram(spec, output)
        elif kind == "ionic_conductivity_arrhenius":
            draw_ionic_conductivity_arrhenius(spec, output)
        elif kind == "dielectric_temperature_curve":
            draw_dielectric_temperature_curve(spec, output)
        elif kind == "ferroelectric_hysteresis_loop":
            draw_ferroelectric_hysteresis_loop(spec, output)
        elif kind == "magnetic_hysteresis_loop":
            draw_magnetic_hysteresis_loop(spec, output)
        elif kind == "fracture_toughness_schematic":
            draw_fracture_toughness_schematic(spec, output)
        elif kind == "curved_liquid_surface":
            draw_curved_liquid_surface(spec, output)
        elif kind == "custom_diagram" and not spec.get("validation_issues"):
            draw_custom_diagram(spec, output)
        else:
            continue
        generated.append(output)
        if callable(progress_callback):
            progress_callback("figure_rendered", {"figure_id": figure_id, "question_id": spec.get("question_id", ""), "generated_count": len(generated)})
    data["figures"] = normalized_figures
    specs_json.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return generated


def _image_data_url(path: Path) -> str:
    mime = mimetypes.guess_type(path.name)[0] or "image/png"
    return f"data:{mime};base64,{base64.b64encode(path.read_bytes()).decode('ascii')}"


VISION_AUDIT_MAX_IMAGE_SIDE = 1536


def _vision_audit_image_data_url(path: Path) -> tuple[str, dict[str, int | bool]]:
    """Reduce visual-QA payload size without modifying the saved figure file."""
    try:
        with Image.open(path) as raw:
            image = raw.convert("RGB")
            original_width, original_height = image.size
            if max(image.size) > VISION_AUDIT_MAX_IMAGE_SIDE:
                image.thumbnail((VISION_AUDIT_MAX_IMAGE_SIDE, VISION_AUDIT_MAX_IMAGE_SIDE), Image.Resampling.LANCZOS)
            processed_width, processed_height = image.size
            buffer = BytesIO()
            image.save(buffer, format="JPEG", quality=92, optimize=True)
        content = buffer.getvalue()
        return (
            f"data:image/jpeg;base64,{base64.b64encode(content).decode('ascii')}",
            {
                "original_width": original_width,
                "original_height": original_height,
                "processed_width": processed_width,
                "processed_height": processed_height,
                "processed_bytes": len(content),
                "downscaled": (processed_width, processed_height) != (original_width, original_height),
            },
        )
    except Exception:
        return _image_data_url(path), {"processed_bytes": path.stat().st_size, "downscaled": False}


def _compact_figure_spec_for_visual_qa(spec: dict[str, Any]) -> dict[str, Any]:
    omitted_keys = {"code", "prompt", "path", "code_path", "run_result", "stdout", "stderr"}

    def compact(value: Any, depth: int = 0) -> Any:
        if depth >= 4:
            return "[omitted: nesting limit]"
        if isinstance(value, str):
            return value if len(value) <= 900 else value[:900] + "..."
        if isinstance(value, list):
            return [compact(item, depth + 1) for item in value[:30]]
        if isinstance(value, dict):
            return {str(key): compact(item, depth + 1) for key, item in value.items() if str(key) not in omitted_keys}
        return value

    return compact(spec)


def _short_text(value: Any, limit: int = 900) -> str:
    text = str(value or "").strip()
    return text if len(text) <= limit else text[:limit] + "..."


def _compact_list(value: Any, limit: int = 8) -> list[Any]:
    if not isinstance(value, list):
        return []
    compact: list[Any] = []
    for item in value[:limit]:
        if isinstance(item, str):
            compact.append(_short_text(item, 500))
        elif isinstance(item, dict):
            compact.append({str(k): _short_text(v, 500) if isinstance(v, str) else v for k, v in item.items()})
        else:
            compact.append(item)
    return compact


def _minimal_question_for_figure_repair(question: dict[str, Any], qid: str) -> dict[str, Any]:
    subquestions: list[dict[str, Any]] = []
    for sub in question.get("subquestions", []) or []:
        if not isinstance(sub, dict):
            continue
        subquestions.append(
            {
                "number": _short_text(sub.get("number"), 80),
                "stem": _short_text(sub.get("stem") or sub.get("text"), 800),
            }
        )
    return {
        "question_id": qid,
        "question_type": _short_text(question.get("question_type"), 80),
        "stem": _short_text(question.get("stem"), 1600),
        "subquestions": subquestions[:10],
    }


def _compact_visual_qa(qa: dict[str, Any]) -> dict[str, Any]:
    return {
        "ok": qa.get("ok"),
        "summary": _short_text(qa.get("summary") or qa.get("error"), 700),
        "missing_requirements": _compact_list(qa.get("missing_requirements")),
        "label_issues": _compact_list(qa.get("label_issues")),
        "visual_issues": _compact_list(qa.get("visual_issues")),
    }


CRYSTALLOGRAPHIC_INDEX_CONTEXT_MARKERS = (
    "晶面指数",
    "晶向",
    "电子衍射",
    "衍射花样",
    "带轴",
    "xrd",
    "x射线衍射",
    "粉末衍射",
    "diffraction",
    "zone axis",
)

CRYSTALLOGRAPHIC_INDEX_JUDGMENT_MARKERS = (
    "非法",
    "消光",
    "晶带",
    "h+k",
    "h + k",
    "h+k+l",
    "h + k + l",
    "反射条件",
    "衍射条件",
    "不应出现",
    "允许反射",
    "指数错误",
    "标签错误",
    "标准指数",
    "上划线",
    "overbar",
)


def _uses_crystallographic_index_whitelist(question: dict[str, Any], spec: dict[str, Any]) -> bool:
    text = " ".join(
        [
            str(question.get("stem") or ""),
            " ".join(str(item.get("stem") or "") for item in question.get("subquestions") or [] if isinstance(item, dict)),
            str(spec.get("caption") or ""),
            str(spec.get("notes") or ""),
        ]
    ).lower()
    return any(marker in text for marker in CRYSTALLOGRAPHIC_INDEX_CONTEXT_MARKERS)


def _apply_crystallographic_index_whitelist(
    qa: dict[str, Any],
    question: dict[str, Any],
    spec: dict[str, Any],
) -> dict[str, Any]:
    """Keep visual QA from treating raster hkl/direction OCR as a physics oracle."""
    if not isinstance(qa, dict) or qa.get("error") or not _uses_crystallographic_index_whitelist(question, spec):
        return qa

    filtered = dict(qa)
    removed: list[dict[str, str]] = []
    remaining_issue_count = 0
    for field in ("missing_requirements", "label_issues", "visual_issues"):
        values = qa.get(field) if isinstance(qa.get(field), list) else []
        kept: list[Any] = []
        for value in values:
            text = str(value or "")
            lowered = text.lower()
            if any(marker in lowered for marker in CRYSTALLOGRAPHIC_INDEX_JUDGMENT_MARKERS):
                removed.append({"field": field, "issue": text})
            else:
                kept.append(value)
        filtered[field] = kept
        remaining_issue_count += len(kept)

    if not removed:
        return filtered
    filtered["crystallographic_index_whitelist"] = {
        "applied": True,
        "reason": "晶面/晶向/电子衍射指数的物理合法性不由视觉 OCR 判定。",
        "suppressed_issues": removed,
    }
    if qa.get("ok") is not True and remaining_issue_count == 0:
        filtered["ok"] = True
        filtered["summary"] = "未发现可由视觉审查直接确认的排版或图像问题；晶体学指数合法性由程序规则校验。"
    return filtered


def _drawing_code_repair_constraints(question: dict[str, Any], spec: dict[str, Any]) -> list[str]:
    text = " ".join(
        [
            str(question.get("stem") or ""),
            " ".join(str(item.get("stem") or "") for item in question.get("subquestions") or [] if isinstance(item, dict)),
            str(spec.get("caption") or ""),
            str(spec.get("notes") or ""),
        ]
    ).lower()
    constraints = [
        "把 visual_qa 当作问题线索，不要照抄其中可能不存在的元素；必须以题干、当前图像要求和代码自身为准。",
        "优先修正错误指数、缺失图形、排版重叠和可读性问题；不要添加题目没有要求的解释性装饰。",
        "不要使用过大的图内标题；如果需要标题，字号应小于主体标签，且不得挤压图形主体。",
        "所有标签必须与点、线、箭头、其他标签保持清晰间距；宁可减少非必要标签，也不要重叠。",
    ]
    if ("[110]" in text or "110" in text) and ("衍射" in text or "带轴" in text or "diffraction" in text):
        constraints.extend(
            [
                "电子衍射带轴图以斑点阵列和指数标注为主体；除非题目要求，不要画粗大的基矢箭头或坐标轴。",
                "只标注满足 h+k=0 的[110]零层倒易点；(110)、(220)这类 h+k != 0 的指数不能作为斑点标签。",
                "5×5 阵列可以只标注中心和若干代表性非中心点；未标注点可保留为黑点以表现周期性。",
            ]
        )
    if ("x射线" in text or "xrd" in text or "粉末衍射" in text) and ("体心" in text or "bcc" in text or "有序" in text):
        constraints.extend(
            [
                "XRD 峰图应避免相邻峰标签重叠；对密集峰可交错上下标注、旋转标签或增加画布宽度。",
                "不能用颜色区分关键含义；用实线/虚线、圆点/菱形、上下分图或直接文字说明区分。",
            ]
        )
    return constraints


def _compact_generation_audit(item: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(item, dict):
        return {}
    return {
        "generation_method": item.get("generation_method"),
        "schema_status": item.get("schema_status"),
        "program_check_issues": _compact_list(item.get("program_check_issues")),
        "risk_notes": _compact_list(item.get("risk_notes")),
    }


def build_figure_spec_repair_payload(
    structured_exam: dict[str, Any],
    spec: dict[str, Any],
    qa_item: dict[str, Any],
    generation_item: dict[str, Any] | None = None,
) -> dict[str, Any]:
    qid = str(spec.get("question_id") or qa_item.get("question_id") or "").strip()
    questions_by_id = {
        str(question.get("question_id") or "").strip(): question
        for question in structured_exam.get("items", []) or []
        if str(question.get("question_id") or "").strip()
    }
    kind = str(spec.get("kind") or "").strip()
    schema = get_schema(kind) or {}
    return {
        "task": "repair_one_failed_figure_spec",
        "strict_scope": [
            "只修复 failed_figure.current_spec 这一张图。",
            "不要改写其他题目、其他 figure_id 或答案正文。",
            "如果题目包含多个小问，只处理该 figure_id 对应的图像要求。",
            "如果同一题有多张图，当前图只需满足 current_spec/caption 对应的那一部分要求；不要把其他图承担的要求合并到当前图。",
            "如果 visual_qa 要求当前图补充其他图已经承担的内容，应优先保持 current_spec 的单图职责，只修正真实的可视化错误。",
            "输出必须是一个 JSON 对象，顶层只允许包含 figure_spec 和 repair_notes。",
        ],
        "question": _minimal_question_for_figure_repair(questions_by_id.get(qid, {}), qid),
        "failed_figure": {
            "question_id": qid,
            "figure_id": str(spec.get("figure_id") or qa_item.get("figure_id") or "").strip(),
            "kind": kind,
            "current_spec": spec,
            "visual_qa": _compact_visual_qa(qa_item.get("qa") if isinstance(qa_item.get("qa"), dict) else {}),
            "generation_audit": _compact_generation_audit(generation_item),
        },
        "schema_hint": {
            "schema_id": schema.get("schema_id", ""),
            "kind": schema.get("kind", kind),
            "description": schema.get("description", ""),
            "required_fields": schema.get("required_fields", []),
            "optional_fields": schema.get("optional_fields", []),
            "renderer": schema.get("renderer", ""),
        },
        "output_schema": {
            "figure_spec": "修复后的单个 figure_spec；保留原 question_id 和 figure_id",
            "repair_notes": ["简短说明改了哪些字段"],
        },
    }


def build_drawing_code_repair_payload(
    structured_exam: dict[str, Any],
    spec: dict[str, Any],
    qa_item: dict[str, Any],
    generation_item: dict[str, Any] | None = None,
) -> dict[str, Any]:
    qid = str(spec.get("question_id") or qa_item.get("question_id") or "").strip()
    questions_by_id = {
        str(question.get("question_id") or "").strip(): question
        for question in structured_exam.get("items", []) or []
        if str(question.get("question_id") or "").strip()
    }
    question = questions_by_id.get(qid, {})
    run_result = spec.get("run_result") if isinstance(spec.get("run_result"), dict) else {}
    domain_rules = drawing_domain_quality_rules(question, str(spec.get("caption") or ""))
    return {
        "task": "repair_one_failed_drawing_code",
        "strict_scope": [
            "只修复 failed_figure.current_code_spec 这一张图。",
            "不要改写其他题目、其他 figure_id、其他图片或答案正文。",
            "如果题目包含多个小问，只处理该 figure_id/caption 对应的图像要求。",
            "输出必须使用 <JSON> 元数据 + <FILE> 源码块协议，顶层 JSON 只允许包含 drawing_code_spec 和 repair_notes。",
        ],
        "question": _minimal_question_for_figure_repair(question, qid),
        "domain_quality_rules": domain_rules,
        "repair_constraints": _drawing_code_repair_constraints(question, spec),
        "failed_figure": {
            "question_id": qid,
            "figure_id": str(spec.get("figure_id") or qa_item.get("figure_id") or "").strip(),
            "caption": spec.get("caption") or "题目图示",
            "current_code_spec": {
                "figure_id": spec.get("figure_id"),
                "question_id": qid,
                "kind": "model_drawing_code",
                "caption": spec.get("caption") or "题目图示",
                "code": spec.get("code") or "",
                "notes": spec.get("notes") or "",
                "validation_issues": spec.get("validation_issues") or [],
                "run_result": {
                    "ok": run_result.get("ok"),
                    "issues": _compact_list(run_result.get("issues")),
                    "stderr": _short_text(run_result.get("stderr"), 1200),
                },
            },
            "visual_qa": _compact_visual_qa(qa_item.get("qa") if isinstance(qa_item.get("qa"), dict) else {}),
            "generation_audit": _compact_generation_audit(generation_item),
        },
        "output_schema": {
            "drawing_code_spec": {
                "figure_id": "必须保留原 figure_id",
                "question_id": "必须保留原 question_id",
                "kind": "model_drawing_code",
                "caption": "中文图注",
                "code_ref": "代码文件名，例如 repaired.py；代码不要放进 JSON 字符串",
                "notes": "简短说明修复内容或作图假设",
            },
            "repair_notes": ["简短说明改了哪些代码或图形表达"],
        },
        "output_protocol": [
            "Return exactly two blocks and no extra prose.",
            "<JSON>{\"drawing_code_spec\":{\"figure_id\":\"...\",\"question_id\":\"...\",\"kind\":\"model_drawing_code\",\"caption\":\"...\",\"code_ref\":\"repaired.py\",\"notes\":\"...\"},\"repair_notes\":[\"...\"]}</JSON>",
            "<FILE name=\"repaired.py\">Python/Matplotlib code defining draw(output_path: str) -> None</FILE>",
            "Do not put Python code inside the JSON block. Put all code only inside the FILE block.",
        ],
        "code_rules": [
            "代码必须定义且只定义一个顶层函数 draw(output_path: str) -> None。",
            "只能使用 matplotlib、numpy、math、textwrap；不得读写除 output_path 之外的文件，不得使用网络、shell、subprocess、OS API、eval、exec、open。",
            "必须保存 PNG 到 output_path。",
            "图中解释性文字使用中文；XRD/BCC/FCC/CsCl/hkl/2θ/a.u./[110]/(110) 等惯用标识可保留英文或符号。",
            "图必须黑白打印可读，只使用黑、白、灰；用线型、点型、填充、直接标注、上下分图或位置区分关键含义，不能靠颜色。",
            "修复目标是考试答案级插图，不是最低可运行示意图。",
            "不要添加题目未要求的大标题、粗箭头、坐标轴、图例或说明文字；这些元素会压缩图形主体并造成重叠。",
            "所有标签必须可读且不重叠；对密集标签使用 staggered offsets、较小字号、旋转、扩大画布或减少非必要标注。",
            *domain_rules,
        ],
    }


def _load_generation_audit_by_figure(specs_json: Path) -> dict[str, dict[str, Any]]:
    audit_path = specs_json.parent / "figure_generation_audit.json"
    if not audit_path.exists():
        return {}
    try:
        audit = json.loads(audit_path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    result: dict[str, dict[str, Any]] = {}
    for item in audit.get("items", []) or []:
        if isinstance(item, dict):
            figure_id = str(item.get("figure_id") or "").strip()
            if figure_id:
                result[figure_id] = item
    return result


def _visual_qa_failed_targets(qa_report: dict[str, Any], specs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    specs_by_id = {str(spec.get("figure_id") or "").strip(): spec for spec in specs if isinstance(spec, dict)}
    targets: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in qa_report.get("items", []) or []:
        if not isinstance(item, dict):
            continue
        qa = item.get("qa") if isinstance(item.get("qa"), dict) else {}
        if qa.get("ok") is True:
            continue
        figure_id = str(item.get("figure_id") or "").strip()
        if figure_id and figure_id in specs_by_id and figure_id not in seen:
            targets.append({"figure_id": figure_id, "qa_item": item, "spec": specs_by_id[figure_id]})
            seen.add(figure_id)
    for item in qa_report.get("skipped", []) or []:
        if not isinstance(item, dict):
            continue
        figure_id = str(item.get("figure_id") or "").strip()
        if not figure_id or figure_id in seen or figure_id not in specs_by_id:
            continue
        qa_item = {
            "question_id": item.get("question_id"),
            "figure_id": figure_id,
            "qa": {
                "ok": False,
                "summary": item.get("reason") or "figure image missing",
                "missing_requirements": ["figure image missing"],
                "label_issues": [],
                "visual_issues": [],
            },
        }
        targets.append({"figure_id": figure_id, "qa_item": qa_item, "spec": specs_by_id[figure_id]})
        seen.add(figure_id)
    return targets


def _figure_visual_qa_issue_count(report: dict[str, Any]) -> int:
    if not isinstance(report, dict) or not report.get("enabled"):
        return 0
    count = len(report.get("failed", []) if isinstance(report.get("failed"), list) else [])
    item_figure_ids: set[str] = set()
    for item in report.get("items", []) or []:
        if not isinstance(item, dict):
            continue
        figure_id = str(item.get("figure_id") or "").strip()
        if figure_id:
            item_figure_ids.add(figure_id)
        qa = item.get("qa") if isinstance(item.get("qa"), dict) else {}
        if qa.get("ok") is not True:
            count += 1
    for item in report.get("skipped", []) or []:
        figure_id = str(item.get("figure_id") or "").strip() if isinstance(item, dict) else ""
        if isinstance(item, dict) and str(item.get("reason") or "") == "figure image missing" and figure_id not in item_figure_ids:
            count += 1
    return count


def _sync_generated_figure_blocks(fragments_json: Path | None, specs: list[dict[str, Any]], output_dir: Path) -> None:
    if fragments_json is None or not fragments_json.exists():
        return
    try:
        fragments_data = json.loads(fragments_json.read_text(encoding="utf-8"))
    except Exception:
        return
    fragments_by_id = {
        str(fragment.get("question_id") or "").strip(): fragment
        for fragment in fragments_data.get("fragments", []) or []
        if isinstance(fragment, dict) and str(fragment.get("question_id") or "").strip()
    }
    changed = False
    for spec in specs:
        figure_id = str(spec.get("figure_id") or "").strip()
        qid = str(spec.get("question_id") or "").strip()
        if not figure_id or not qid or not (output_dir / f"{figure_id}.png").exists():
            continue
        fragment = fragments_by_id.get(qid)
        if fragment is None:
            continue
        before = json.dumps(fragment.get("blocks", []), ensure_ascii=False, sort_keys=True)
        _insert_figure_block(fragment, spec)
        after = json.dumps(fragment.get("blocks", []), ensure_ascii=False, sort_keys=True)
        changed = changed or before != after
    if changed:
        fragments_json.write_text(json.dumps(fragments_data, ensure_ascii=False, indent=2), encoding="utf-8")


def _prune_redundant_model_fallback_specs(specs_data: dict[str, Any], output_dir: Path) -> bool:
    specs = [spec for spec in specs_data.get("figures", []) or [] if isinstance(spec, dict)]
    generated_primary_qids = {
        str(spec.get("question_id") or "").strip()
        for spec in specs
        if str(spec.get("kind") or "").strip() != "model_generated_image"
        and str(spec.get("question_id") or "").strip()
        and (output_dir / f"{str(spec.get('figure_id') or '').strip()}.png").exists()
    }
    if not generated_primary_qids:
        return False
    pruned: list[dict[str, Any]] = []
    changed = False
    for spec in specs:
        qid = str(spec.get("question_id") or "").strip()
        kind = str(spec.get("kind") or "").strip()
        figure_id = str(spec.get("figure_id") or "").strip()
        is_direct_fallback = kind == "model_generated_image" or figure_id.endswith("_model_fig_01")
        if is_direct_fallback and qid in generated_primary_qids:
            changed = True
            continue
        pruned.append(spec)
    if changed:
        specs_data["figures"] = pruned
    return changed


def _prune_stale_failed_code_specs(specs_data: dict[str, Any], output_dir: Path) -> bool:
    specs = [spec for spec in specs_data.get("figures", []) or [] if isinstance(spec, dict)]
    covered_code_qids = {
        str(spec.get("question_id") or "").strip()
        for spec in specs
        if str(spec.get("kind") or "").strip() == "model_drawing_code"
        and str(spec.get("question_id") or "").strip()
        and str(spec.get("figure_id") or "").strip()
        and (output_dir / f"{str(spec.get('figure_id') or '').strip()}.png").exists()
    }
    if not covered_code_qids:
        return False
    pruned: list[dict[str, Any]] = []
    changed = False
    for spec in specs:
        qid = str(spec.get("question_id") or "").strip()
        figure_id = str(spec.get("figure_id") or "").strip()
        kind = str(spec.get("kind") or "").strip()
        if kind == "model_drawing_code" and qid in covered_code_qids and (not figure_id or not (output_dir / f"{figure_id}.png").exists()):
            changed = True
            continue
        pruned.append(spec)
    if changed:
        specs_data["figures"] = pruned
    return changed


def repair_figures_with_model_for_visual_qa(
    structured_exam: dict[str, Any],
    fragments_json: Path | None,
    specs_json: Path,
    output_dir: Path,
    visual_qa_json: Path,
    repair_report_json: Path,
    *,
    qa_report: dict[str, Any] | None = None,
    provider: Any | None = None,
    model: str = "",
    vision_provider: Any | None = None,
    vision_model: str = "",
    max_rounds: int = 1,
    progress_callback: Any | None = None,
) -> dict[str, Any]:
    """Create and visually validate independent repair candidates before promotion.

    The answer model and the vision reviewer receive the same failed source spec. A
    candidate may replace the primary figure only after the vision reviewer accepts
    its rendered PNG. This prevents a failed repair from overwriting the last known
    figure merely because its code passed the static validator.
    """
    report: dict[str, Any] = {
        "schema_version": "answer_book.figure_visual_qa_repair.v2",
        "enabled": bool(
            provider is not None
            and getattr(provider, "api_key", "")
            and vision_provider is not None
            and getattr(vision_provider, "api_key", "")
            and getattr(vision_provider, "supports_vision", False)
        ),
        "repair_model": {
            "provider": getattr(provider, "name", "") if provider is not None else "",
            "model": str(model or getattr(provider, "default_model", "") or "") if provider is not None else "",
        },
        "vision_model": {
            "provider": getattr(vision_provider, "name", "") if vision_provider is not None else "",
            "model": str(vision_model or getattr(vision_provider, "vision_model", "") or "") if vision_provider is not None else "",
        },
        "rounds": [],
        "changed": False,
        "latest_visual_qa": qa_report or {},
    }
    if not report["enabled"]:
        report["skipped_reason"] = "repair or vision provider is not configured"
        repair_report_json.parent.mkdir(parents=True, exist_ok=True)
        repair_report_json.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        return report
    if qa_report is None:
        qa_report = json.loads(visual_qa_json.read_text(encoding="utf-8")) if visual_qa_json.exists() else {}
    latest_qa = qa_report
    repair_model = str(report["repair_model"]["model"])
    reviewer_model = str(report["vision_model"]["model"])
    repair_client = OpenAICompatibleClient(provider)
    reviewer_client = OpenAICompatibleClient(vision_provider)
    candidate_root = output_dir.parent / "figure_visual_qa_candidates"

    def emit(event: str, detail: dict[str, Any]) -> None:
        if callable(progress_callback):
            progress_callback(event, detail)

    def request_candidate(
        *,
        strategy: str,
        candidate_provider: Any,
        candidate_model: str,
        candidate_client: OpenAICompatibleClient,
        current_spec: dict[str, Any],
        qa_item: dict[str, Any],
        generation_item: dict[str, Any] | None,
        source_image: Path,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        kind = str(current_spec.get("kind") or "").strip()
        if kind == "model_drawing_code":
            payload = build_drawing_code_repair_payload(structured_exam, current_spec, qa_item, generation_item)
            system_prompt = "你是专业作图题 Python/Matplotlib 代码修复器。按 <JSON> 元数据 + <FILE> 源码块协议输出，只修复用户给定的一张图。"
            output_key = "drawing_code_spec"
        else:
            payload = build_figure_spec_repair_payload(structured_exam, current_spec, qa_item, generation_item)
            system_prompt = "你是专业作图题 figure_specs 修复器。只输出 JSON，只修复用户给定的一张图。"
            output_key = "figure_spec"
        payload["repair_strategy"] = strategy
        payload["candidate_policy"] = [
            "基于失败前的同一份 current_spec 生成候选，不要假设其他候选已经修改过代码。",
            "修复后会由视觉审查模型再次审查；只有通过视觉审查的候选才会成为正式版本。",
        ]
        messages: list[dict[str, Any]] = [{"role": "system", "content": system_prompt}]
        image_input: dict[str, Any] | None = None
        if strategy == "vision_reviewer" and source_image.exists():
            image_url, image_input = _vision_audit_image_data_url(source_image)
            messages.append(
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": json.dumps(payload, ensure_ascii=False)},
                        {"type": "image_url", "image_url": {"url": image_url}},
                    ],
                }
            )
        else:
            messages.append({"role": "user", "content": json.dumps(payload, ensure_ascii=False)})
        if kind == "model_drawing_code" and hasattr(candidate_client, "chat_text"):
            result = candidate_client.chat_text(
                messages,
                model=candidate_model,
                max_tokens=FIGURE_AUXILIARY_MAX_TOKENS,
                timeout=90,
                thinking="disabled",
            )
            repaired, repair_notes = parse_drawing_code_model_response(result.content)
            response: dict[str, Any] = {"drawing_code_spec": repaired, "repair_notes": repair_notes}
        else:
            response = candidate_client.chat_json_object(
                messages,
                model=candidate_model,
                max_tokens=FIGURE_AUXILIARY_MAX_TOKENS,
                timeout=90,
                attempts=1,
                thinking="disabled" if kind == "model_drawing_code" else None,
            )
            repaired = response.get(output_key) if isinstance(response, dict) else None
        if kind == "model_drawing_code" and not isinstance(repaired, dict) and isinstance(response, dict) and response.get("code"):
            repaired = {"code": response.get("code"), "caption": response.get("caption") or current_spec.get("caption")}
        if not isinstance(repaired, dict):
            raise ValueError(f"{strategy} did not return {output_key} object")
        repaired["figure_id"] = str(current_spec.get("figure_id") or "").strip()
        repaired["question_id"] = str(current_spec.get("question_id") or "").strip()
        repaired["kind"] = "model_drawing_code" if kind == "model_drawing_code" else (repaired.get("kind") or kind)
        repaired.setdefault("caption", current_spec.get("caption") or "题目图示")
        repaired["source"] = f"visual_qa_{strategy}_candidate"
        if kind == "model_drawing_code":
            validation_issues = validate_drawing_code(str(repaired.get("code") or ""))
        else:
            repaired = normalize_figure_spec(repaired)
            validation_issues = program_check_figure_spec(repaired)
        if validation_issues:
            repaired["validation_issues"] = validation_issues
        return repaired, {
            "strategy": strategy,
            "provider": getattr(candidate_provider, "name", ""),
            "model": candidate_model,
            "repair_notes": _compact_list(response.get("repair_notes")) if isinstance(response, dict) else [],
            "vision_input": image_input,
            "validation_issues": validation_issues,
        }

    def audit_candidate(
        candidate_spec: dict[str, Any],
        candidate_dir: Path,
        candidate_specs_json: Path,
    ) -> tuple[dict[str, Any], Path]:
        candidate_specs_json.parent.mkdir(parents=True, exist_ok=True)
        candidate_specs_json.write_text(json.dumps({"figures": [candidate_spec]}, ensure_ascii=False, indent=2), encoding="utf-8")
        generated = generate_figures(candidate_specs_json, candidate_dir)
        candidate_qa_path = candidate_dir / "figure_visual_qa.json"
        candidate_qa = audit_figures_with_vision(
            structured_exam,
            candidate_specs_json,
            candidate_dir,
            candidate_qa_path,
            provider=vision_provider,
            model=reviewer_model,
        )
        return candidate_qa, generated[0] if generated else candidate_dir / f"{candidate_spec.get('figure_id')}.png"

    def candidate_passed(candidate_qa: dict[str, Any], figure_id: str) -> bool:
        return any(
            str(item.get("figure_id") or "") == figure_id
            and isinstance(item.get("qa"), dict)
            and item["qa"].get("ok") is True
            for item in candidate_qa.get("items", []) or []
            if isinstance(item, dict)
        )

    def replace_qa_item(base: dict[str, Any], replacement: dict[str, Any], figure_id: str, final_path: Path) -> dict[str, Any]:
        merged = dict(base)
        items: list[dict[str, Any]] = []
        for item in base.get("items", []) or []:
            if isinstance(item, dict) and str(item.get("figure_id") or "") == figure_id:
                continue
            if isinstance(item, dict):
                items.append(item)
        for item in replacement.get("items", []) or []:
            if not isinstance(item, dict) or str(item.get("figure_id") or "") != figure_id:
                continue
            item = dict(item)
            item["path"] = str(final_path)
            items.append(item)
        merged["items"] = items
        merged["skipped"] = [item for item in base.get("skipped", []) or [] if str(item.get("figure_id") or "") != figure_id]
        return merged

    for round_index in range(1, max(1, int(max_rounds)) + 1):
        specs_data = json.loads(specs_json.read_text(encoding="utf-8")) if specs_json.exists() else {"figures": []}
        specs = [spec for spec in specs_data.get("figures", []) or [] if isinstance(spec, dict)]
        generation_by_figure = _load_generation_audit_by_figure(specs_json)
        targets = _visual_qa_failed_targets(latest_qa, specs)
        round_report: dict[str, Any] = {
            "round": round_index,
            "target_count": len(targets),
            "targets": [],
            "changed": False,
        }
        if not targets:
            round_report["status"] = "no_failed_figures"
            report["rounds"].append(round_report)
            break
        specs_by_id = {str(spec.get("figure_id") or "").strip(): idx for idx, spec in enumerate(specs)}
        selected_candidates: list[dict[str, Any]] = []
        for target in targets:
            figure_id = target["figure_id"]
            current_spec = target["spec"]
            kind = str(current_spec.get("kind") or "").strip()
            target_report: dict[str, Any] = {
                "question_id": str(current_spec.get("question_id") or "").strip(),
                "figure_id": figure_id,
                "kind": kind,
                "changed": False,
                "candidates": [],
            }
            source_image = output_dir / f"{figure_id}.png"
            candidate_configs = [
                ("original_model", provider, repair_model, repair_client),
                ("vision_reviewer", vision_provider, reviewer_model, reviewer_client),
            ]
            for strategy, candidate_provider, candidate_model, candidate_client in candidate_configs:
                candidate_report: dict[str, Any] = {"strategy": strategy, "provider": getattr(candidate_provider, "name", ""), "model": candidate_model}
                emit("visual_qa_repair_candidate_started", {"figure_id": figure_id, "question_id": target_report["question_id"], "strategy": strategy, "model": candidate_model})
                try:
                    repaired, request_report = request_candidate(
                        strategy=strategy,
                        candidate_provider=candidate_provider,
                        candidate_model=candidate_model,
                        candidate_client=candidate_client,
                        current_spec=dict(current_spec),
                        qa_item=target["qa_item"],
                        generation_item=generation_by_figure.get(figure_id),
                        source_image=source_image,
                    )
                    candidate_report.update(request_report)
                    if candidate_report["validation_issues"]:
                        candidate_report["status"] = "validation_failed"
                    else:
                        candidate_dir = candidate_root / figure_id / f"round_{round_index}" / strategy
                        candidate_qa, candidate_image = audit_candidate(repaired, candidate_dir, candidate_dir / "figure_specs.json")
                        candidate_report["path"] = str(candidate_image)
                        candidate_report["qa_path"] = str(candidate_dir / "figure_visual_qa.json")
                        candidate_report["visual_qa"] = candidate_qa
                        candidate_report["passed"] = candidate_passed(candidate_qa, figure_id)
                        candidate_report["status"] = "passed" if candidate_report["passed"] else "visual_qa_failed"
                        emit("visual_qa_repair_candidate_audited", {"figure_id": figure_id, "question_id": target_report["question_id"], "strategy": strategy, "ok": candidate_report["passed"]})
                        if candidate_report["passed"]:
                            selected_candidates.append(
                                {
                                    "strategy": strategy,
                                    "spec": repaired,
                                    "qa": candidate_qa,
                                    "image": candidate_image,
                                    "target_report": target_report,
                                }
                            )
                except Exception as exc:
                    candidate_report["status"] = "error"
                    candidate_report["error"] = str(exc)[:700]
                target_report["candidates"].append(candidate_report)
            passing = [candidate for candidate in selected_candidates if candidate["target_report"] is target_report]
            if passing:
                # Prefer the visual reviewer's own repair when both candidates pass.
                selected = next((candidate for candidate in passing if candidate["strategy"] == "vision_reviewer"), passing[0])
                idx = specs_by_id.get(figure_id)
                if idx is None:
                    raise ValueError("target figure_id no longer exists in figure_specs")
                before = json.dumps(specs[idx], ensure_ascii=False, sort_keys=True)
                specs[idx] = selected["spec"]
                target_report["selected_strategy"] = selected["strategy"]
                target_report["changed"] = before != json.dumps(selected["spec"], ensure_ascii=False, sort_keys=True)
                target_report["selected"] = True
                # A passing re-audit is a valid promotion even if its spec happens
                # to serialize identically to the failed source spec.
                round_report["changed"] = True
            round_report["targets"].append(target_report)
        if round_report["changed"]:
            specs_data["figures"] = specs
            specs_json.write_text(json.dumps(specs_data, ensure_ascii=False, indent=2), encoding="utf-8")
            for selected in selected_candidates:
                figure_id = str(selected["spec"].get("figure_id") or "")
                target_path = output_dir / f"{figure_id}.png"
                if selected["image"].exists():
                    target_path.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(selected["image"], target_path)
                    latest_qa = replace_qa_item(latest_qa, selected["qa"], figure_id, target_path)
                    emit("visual_qa_repair_candidate_selected", {"figure_id": figure_id, "question_id": str(selected["spec"].get("question_id") or ""), "strategy": selected["strategy"]})
            refreshed_specs_data = json.loads(specs_json.read_text(encoding="utf-8")) if specs_json.exists() else {"figures": []}
            pruned = _prune_redundant_model_fallback_specs(refreshed_specs_data, output_dir)
            pruned = _prune_stale_failed_code_specs(refreshed_specs_data, output_dir) or pruned
            if pruned:
                specs_json.write_text(json.dumps(refreshed_specs_data, ensure_ascii=False, indent=2), encoding="utf-8")
            refreshed_specs = [spec for spec in refreshed_specs_data.get("figures", []) or [] if isinstance(spec, dict)]
            _sync_generated_figure_blocks(fragments_json, refreshed_specs, output_dir)
            visual_qa_json.write_text(json.dumps(latest_qa, ensure_ascii=False, indent=2), encoding="utf-8")
            round_report["generated_count"] = len(selected_candidates)
            round_report["visual_qa_issue_count_after"] = _figure_visual_qa_issue_count(latest_qa)
            report["changed"] = True
        else:
            round_report["status"] = "no_spec_changes"
            report["rounds"].append(round_report)
            break
        report["rounds"].append(round_report)
        if _figure_visual_qa_issue_count(latest_qa) == 0:
            break
    report["latest_visual_qa"] = latest_qa
    repair_report_json.parent.mkdir(parents=True, exist_ok=True)
    repair_report_json.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return report


def audit_figures_with_vision(
    structured_exam: dict[str, Any],
    specs_json: Path,
    output_dir: Path,
    report_json: Path,
    *,
    provider: Any | None = None,
    model: str = "",
    progress_callback: Any | None = None,
) -> dict[str, Any]:
    specs_data = json.loads(specs_json.read_text(encoding="utf-8")) if specs_json.exists() else {"figures": []}
    report: dict[str, Any] = {
        "schema_version": "answer_book.figure_visual_qa.v1",
        "enabled": bool(provider is not None and getattr(provider, "api_key", "") and getattr(provider, "supports_vision", False) and getattr(provider, "vision_model", "")),
        "provider": getattr(provider, "name", "") if provider is not None else "",
        "vision_model": str(model or getattr(provider, "vision_model", "") or "") if provider is not None else "",
        "items": [],
        "skipped": [],
    }
    questions_by_id = {
        str(question.get("question_id") or "").strip(): question
        for question in structured_exam.get("items", []) or []
        if str(question.get("question_id") or "").strip()
    }
    if not report["enabled"]:
        report["skipped"].append({"reason": "vision provider is not configured"})
        report_json.parent.mkdir(parents=True, exist_ok=True)
        report_json.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        return report
    client = OpenAICompatibleClient(provider)
    figures_by_qid: dict[str, list[dict[str, Any]]] = {}
    for raw_spec in specs_data.get("figures", []) or []:
        if not isinstance(raw_spec, dict):
            continue
        raw_qid = str(raw_spec.get("question_id") or "").strip()
        if raw_qid:
            figures_by_qid.setdefault(raw_qid, []).append(raw_spec)
    for spec in specs_data.get("figures", []) or []:
        if not isinstance(spec, dict):
            continue
        figure_id = str(spec.get("figure_id") or "").strip()
        qid = str(spec.get("question_id") or "").strip()
        path = output_dir / f"{figure_id}.png"
        if not figure_id or not path.exists():
            missing = {"question_id": qid, "figure_id": figure_id, "reason": "figure image missing"}
            report["skipped"].append(missing)
            if callable(progress_callback):
                progress_callback("visual_qa_skipped", {"figure_id": figure_id, "question_id": qid, "reason": "figure image missing"})
            continue
        question = questions_by_id.get(qid, {})
        kind = str(spec.get("kind") or "").strip()
        crystallographic_whitelist = _uses_crystallographic_index_whitelist(question, spec)
        domain_rules: list[str] = drawing_domain_quality_rules(question, str(spec.get("caption") or spec.get("title") or ""))
        if kind == "zone_axis_diffraction" and not crystallographic_whitelist:
            domain_rules.extend(
                [
                    "For a cubic [110] zone-axis diffraction pattern, the diffraction spots must satisfy h + k = 0.",
                    "In a BCC lattice, reflections with h+k+l even are allowed and h+k+l odd are extinct.",
                    "In a [110] zone-axis pattern, in-plane reciprocal basis directions such as [1 -1 0]* and [0 0 1]* are not the beam/zone axis; do not treat them as contradicting the [110] zone axis.",
                ]
            )
        elif kind == "xrd_pattern" and not crystallographic_whitelist:
            domain_rules.extend(
                [
                    "For disordered BCC powder XRD, allowed fundamental peaks satisfy h+k+l even, such as (110), (200), (211), (220), (310), (222).",
                    "For CsCl-type ordering, new superlattice peaks occur at odd h+k+l positions such as (100), (111), (210), and should be visually distinguished from fundamental peaks.",
                ]
            )
        if kind == "model_drawing_code":
            domain_rules.extend(
                [
                    "This is a model-generated Python drawing. Do not pass it only because it contains some labels; check whether it reaches exam-answer figure quality.",
                    "Reject overly sparse sketches when the required professional figure is normally a pattern, array, curve set, phase diagram, or multi-peak plot.",
                    "Reject figures that introduce arbitrary physical parameters not given by the question when relative positions or qualitative changes are sufficient.",
                ]
            )
        image_url, image_input = _vision_audit_image_data_url(path)
        payload = {
            "task": "audit_generated_answer_figure",
            "question": _minimal_question_for_figure_repair(question, qid),
            "figure_spec": _compact_figure_spec_for_visual_qa(spec),
            "figure_context": {
                "same_question_figure_count": len(figures_by_qid.get(qid, [])),
                "current_figure_caption": spec.get("caption") or spec.get("title") or "",
                "current_figure_only": True,
            },
            "output_schema": {
                "ok": True,
                "missing_requirements": [],
                "label_issues": [],
                "visual_issues": [],
                "summary": "一句话说明图片是否满足题意",
            },
            "hard_rules": [
                "Return exactly one valid JSON object.",
                "Check whether the generated figure satisfies the question and figure_spec.",
                "Audit only the current figure_spec and its caption. If the same question has multiple figure_specs, do not require this single figure to cover requirements assigned to other figures.",
                "Focus on missing labels, wrong directions, wrong axes, unreadable text, and irrelevant decorative content.",
                "Report only visible problems that you can directly verify from the image. Do not mention elements that are not visible in the current image.",
                "Treat label overlap, oversized titles, cramped legends, or text covering plotted marks as visual_issues.",
                "Keep each issue concise; do not include long derivations.",
                *(
                    [
                        "This figure contains crystallographic plane/direction indices or diffraction indexing.",
                        "Do not judge any hkl, uvw, zone-axis, extinction, reflection, or indexing label as physically illegal, wrong, or missing based on the raster image.",
                        "For crystallographic labels, report only directly visible readability defects such as blur, clipping, or overlap. If uncertain, do not report an index issue.",
                    ]
                    if crystallographic_whitelist
                    else []
                ),
                *domain_rules,
            ],
        }
        try:
            if callable(progress_callback):
                progress_callback("visual_qa_started", {"figure_id": figure_id, "question_id": qid, "model": report["vision_model"]})
            qa = client.chat_json_object(
                [
                    {"role": "system", "content": "你是真题解析册插图质量审查器，只输出 JSON。"},
                    {"role": "user", "content": [{"type": "text", "text": json.dumps(payload, ensure_ascii=False)}, {"type": "image_url", "image_url": {"url": image_url}}]},
                ],
                model=str(report["vision_model"]),
                max_tokens=FIGURE_AUXILIARY_MAX_TOKENS,
                timeout=90,
                attempts=1,
            )
        except Exception as exc:
            qa = {"ok": False, "error": str(exc)[:500]}
        qa = _apply_crystallographic_index_whitelist(qa, question, spec)
        if callable(progress_callback):
            progress_callback("visual_qa_completed", {"figure_id": figure_id, "question_id": qid, "ok": qa.get("ok") is True, "error": str(qa.get("error") or "")[:300]})
        report["items"].append({"question_id": qid, "figure_id": figure_id, "path": str(path), "vision_input": image_input, "qa": qa})
    report_json.parent.mkdir(parents=True, exist_ok=True)
    report_json.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return report
