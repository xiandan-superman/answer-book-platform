from __future__ import annotations

import base64
import hashlib
import json
import math
import mimetypes
import os
import re
import shutil
import textwrap
from io import BytesIO
from pathlib import Path
from typing import Any

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib import font_manager
from matplotlib.patches import Arc, Circle, Ellipse, FancyArrowPatch, Polygon, Rectangle
from PIL import Image, ImageDraw, ImageFont, ImageStat

from .capabilities.catalog import (
    apply_capability_policy_transforms,
    capability_policy_contributions,
    get_schema,
    registry_snapshot,
)
from .capabilities.figure_semantics import (
    FigureRenderDecision,
    RenderStrategy,
    audit_figure_render_outcome,
    semantic_contract_from_mapping,
)
from .capabilities.rendering import RendererRegistry, assemble_renderer_registry
from .concurrency import model_request_slot, run_limited_concurrent
from .drawing_code import (
    drawing_domain_quality_rules,
    generate_drawing_code_spec,
    parse_drawing_code_model_response,
    question_drawing_mode,
    run_drawing_code,
    validate_drawing_code,
)
from .llm_client import OpenAICompatibleClient
from .question_requirements import answer_figure_required
from .settings import FIGURE_AUXILIARY_MAX_TOKENS, provider_supports_image_generation

BUNDLED_FONT_DIR = Path(__file__).resolve().parents[1] / "assets" / "fonts"


def audit_figure_image_integrity(path: Path) -> list[str]:
    """Check only cheap, objective image facts before semantic visual QA."""

    if not path.exists() or path.stat().st_size <= 0:
        return ["figure_image_missing_or_empty"]
    try:
        with Image.open(path) as raw:
            raw.load()
            width, height = raw.size
            if "A" in raw.getbands():
                rgba = raw.convert("RGBA")
                base = Image.new("RGBA", rgba.size, "white")
                base.alpha_composite(rgba)
                image = base.convert("RGB")
            else:
                image = raw.convert("RGB")
            gray = image.convert("L")
            stats = ImageStat.Stat(gray)
            extrema = gray.getextrema()
            histogram = gray.histogram()
    except Exception as exc:
        return [f"figure_image_unreadable:{type(exc).__name__}"]

    issues: list[str] = []
    if width < 96 or height < 96:
        issues.append(f"figure_image_dimensions_too_small:{width}x{height}")
    total = max(width * height, 1)
    nonwhite_ratio = sum(histogram[:250]) / total
    variation = int(extrema[1]) - int(extrema[0])
    standard_deviation = float(stats.stddev[0]) if stats.stddev else 0.0
    if variation < 4 or standard_deviation < 0.8 or nonwhite_ratio < 0.0001:
        issues.append("figure_image_blank_or_nearly_uniform")
    return issues


def figure_model_worker_count() -> int:
    raw = os.environ.get("FIGURE_MODEL_MAX_WORKERS", "6")
    try:
        return max(1, min(6, int(raw)))
    except ValueError:
        return 6


def figure_visual_audit_worker_count() -> int:
    """Keep vision review below common provider burst-concurrency limits."""

    raw = os.environ.get("FIGURE_VISUAL_AUDIT_MAX_WORKERS", "2")
    try:
        return max(1, min(3, int(raw)))
    except ValueError:
        return 2


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


_UNICODE_SUBSCRIPT_DIGITS = str.maketrans("₀₁₂₃₄₅₆₇₈₉", "0123456789")


def _matplotlib_scientific_text(value: Any) -> str:
    """Render common scientific subscripts without relying on CJK glyph coverage."""

    text = str(value or "")
    if not text or "$" in text:
        return text
    text = re.sub(
        r"[₀₁₂₃₄₅₆₇₈₉]+",
        lambda match: f"$_{{{match.group(0).translate(_UNICODE_SUBSCRIPT_DIGITS)}}}$",
        text,
    )
    text = re.sub(
        r"_([A-Za-z0-9]+)",
        lambda match: f"$_{{\\mathrm{{{match.group(1)}}}}}$",
        text,
    )
    return text


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
    parsed_points = [_point_xy(point) for point in points]
    parsed_points = [point for point in parsed_points if point is not None]
    if not parsed_points:
        raise ValueError("line chart has no valid numeric points")
    xs = [point[0] for point in parsed_points]
    ys = [point[1] for point in parsed_points]
    fig, ax = plt.subplots(figsize=(5.8, 3.6), dpi=180)
    ax.plot(xs, ys, marker="o", color="#111", lw=1.8)
    ax.set_xlabel(_matplotlib_scientific_text(spec.get("x_label", "x")))
    ax.set_ylabel(_matplotlib_scientific_text(spec.get("y_label", "y")))
    # The caption is rendered by Word.  Repeating it inside the plot wastes
    # vertical space and was consistently rejected by visual QA.
    if spec.get("render_title_inside") is True and str(spec.get("title") or "").strip():
        ax.set_title(str(spec.get("title")), fontsize=11)
    annotations = list(spec.get("annotations")) if isinstance(spec.get("annotations"), list) else []
    # Labeled points carry semantic content such as phase/region names.  The
    # old generic renderer plotted the points but silently dropped their labels.
    # Merge adjacent duplicate labels into one centered annotation to avoid
    # clutter on plateaus while retaining every distinct requested label.
    point_label_groups: list[dict[str, Any]] = []
    for raw_point in points:
        if not isinstance(raw_point, dict):
            continue
        xy = _point_xy(raw_point)
        label = str(raw_point.get("label") or "").strip()
        if xy is None or not label:
            continue
        if point_label_groups and point_label_groups[-1]["text"] == label:
            point_label_groups[-1]["coordinates"].append(xy)
        else:
            point_label_groups.append({"text": label, "coordinates": [xy]})
    explicit_texts = {
        str(item.get("text") or item.get("label") or "").strip()
        for item in annotations
        if isinstance(item, dict)
    }
    explicit_texts.update(str(item).strip() for item in annotations if isinstance(item, str))
    for group_index, group in enumerate(point_label_groups):
        if group["text"] in explicit_texts:
            continue
        coordinates = group["coordinates"]
        annotations.append(
            {
                "text": group["text"],
                "x": sum(point[0] for point in coordinates) / len(coordinates),
                "y": sum(point[1] for point in coordinates) / len(coordinates),
                "dx": 7 if group_index % 2 == 0 else -7,
                "dy": 13 + (group_index % 3) * 8,
            }
        )
    plateaus = [
        ((left[0] + right[0]) / 2.0, left[1])
        for left, right in zip(parsed_points, parsed_points[1:])
        if math.isclose(left[1], right[1], rel_tol=1e-9, abs_tol=1e-9)
    ]
    generic_index = 0
    for annotation_index, annotation in enumerate(annotations):
        if isinstance(annotation, str):
            text = annotation.strip()
            if not text:
                continue
            temperature_match = re.search(r"(?<![\d.])(-?\d+(?:\.\d+)?)\s*℃", text)
            semantic_offset: tuple[float, float] | None = None
            if temperature_match:
                temperature = float(temperature_match.group(1))
                x, y = min(parsed_points, key=lambda point: abs(point[1] - temperature))
            elif "共晶" in text and plateaus:
                x, y = max(plateaus, key=lambda point: point[1])
                text = f"{text}（{y:g} ℃）"
                semantic_offset = (14, 34)
            elif ("共析" in text or "低温" in text) and plateaus:
                x, y = min(plateaus, key=lambda point: point[1])
                text = f"{text}（{y:g} ℃）"
                semantic_offset = (14, 34)
            elif any(token in text.lower() for token in ("液相线", "liquidus")):
                x, y = parsed_points[min(2, len(parsed_points) - 1)]
                semantic_offset = (-18, 34)
            else:
                candidate_index = min(
                    len(parsed_points) - 1,
                    round((generic_index + 1) * (len(parsed_points) - 1) / (len(annotations) + 1)),
                )
                x, y = parsed_points[candidate_index]
                generic_index += 1
            annotation = {"text": text, "x": x, "y": y}
            if semantic_offset is not None:
                annotation["dx"], annotation["dy"] = semantic_offset
        if not isinstance(annotation, dict):
            continue
        try:
            x = float(annotation.get("x"))
            y = float(annotation.get("y"))
        except (TypeError, ValueError):
            continue
        text = str(annotation.get("text") or annotation.get("label") or "").strip()
        if not text:
            continue
        dx = float(annotation.get("dx") or (8 if annotation_index % 2 == 0 else -8))
        dy = float(annotation.get("dy") or (12 + (annotation_index % 3) * 7))
        ax.annotate(
            _matplotlib_scientific_text(text),
            xy=(x, y),
            xytext=(dx, dy),
            textcoords="offset points",
            fontsize=8,
            ha="left" if dx >= 0 else "right",
            arrowprops={"arrowstyle": "-", "lw": 0.7, "color": "#444"},
            bbox={"boxstyle": "round,pad=0.12", "facecolor": "white", "edgecolor": "none", "alpha": 0.9},
        )
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


def _overlay_point(value: Any, width: int, height: int) -> tuple[int, int]:
    if not isinstance(value, (list, tuple)) or len(value) != 2:
        raise ValueError("overlay point must contain two normalized coordinates")
    try:
        x, y = float(value[0]), float(value[1])
    except (TypeError, ValueError) as exc:
        raise ValueError("overlay coordinates must be numeric") from exc
    if not (0.0 <= x <= 1.0 and 0.0 <= y <= 1.0):
        raise ValueError("overlay coordinates must be normalized to [0,1]")
    return round(x * max(0, width - 1)), round(y * max(0, height - 1))


def _overlay_font(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    candidates = (
        BUNDLED_FONT_DIR / "dolbydu-font" / "unicode" / "Microsoft Yahei.ttf",
        BUNDLED_FONT_DIR / "dolbydu-font" / "unicode" / "SimHei.ttf",
    )
    for candidate in candidates:
        if candidate.exists():
            try:
                return ImageFont.truetype(str(candidate), size=max(10, size))
            except OSError:
                continue
    return ImageFont.load_default()


def validate_source_image_overlay_spec(spec: dict[str, Any]) -> list[str]:
    issues: list[str] = []
    source = Path(str(spec.get("source_image") or ""))
    expected_hash = str(spec.get("source_image_sha256") or "").strip().lower()
    if not source.exists() or not source.is_file():
        issues.append("source_image_overlay: source image is missing")
    elif not expected_hash:
        issues.append("source_image_overlay: source image hash is missing")
    elif hashlib.sha256(source.read_bytes()).hexdigest() != expected_hash:
        issues.append("source_image_overlay: source image hash mismatch")
    annotations = spec.get("annotations")
    if not isinstance(annotations, list) or not annotations:
        issues.append("source_image_overlay: annotations must be a non-empty list")
        return issues
    allowed = {"line", "arrow", "rectangle", "ellipse", "point", "text"}
    labels: set[str] = set()
    for index, annotation in enumerate(annotations):
        if not isinstance(annotation, dict):
            issues.append(f"source_image_overlay: annotations[{index}] must be an object")
            continue
        kind = str(annotation.get("type") or "").strip()
        if kind not in allowed:
            issues.append(f"source_image_overlay: annotations[{index}].type is invalid")
            continue
        labels.update(
            str(annotation.get(key) or "").strip()
            for key in ("text", "label")
            if str(annotation.get(key) or "").strip()
        )
        points = ("xy",) if kind in {"point", "text"} else ("start", "end")
        for field in points:
            value = annotation.get(field)
            if not isinstance(value, (list, tuple)) or len(value) != 2:
                issues.append(f"source_image_overlay: annotations[{index}].{field} is required")
                continue
            try:
                coordinates = [float(item) for item in value]
            except (TypeError, ValueError):
                issues.append(f"source_image_overlay: annotations[{index}].{field} must be numeric")
                continue
            if any(item < 0.0 or item > 1.0 for item in coordinates):
                issues.append(f"source_image_overlay: annotations[{index}].{field} must use [0,1] coordinates")
        if kind == "text" and not str(annotation.get("text") or "").strip():
            issues.append(f"source_image_overlay: annotations[{index}].text is required")
    for required in spec.get("required_labels") or []:
        required_text = str(required or "").strip()
        if required_text and not any(required_text in label for label in labels):
            issues.append(f"source_image_overlay: missing required label: {required_text}")
    return issues


def draw_source_image_overlay(spec: dict[str, Any], output: Path) -> None:
    issues = validate_source_image_overlay_spec(spec)
    if issues:
        raise ValueError("; ".join(issues))
    source = Path(str(spec["source_image"]))
    with Image.open(source) as raw:
        image = raw.convert("RGB")
    draw = ImageDraw.Draw(image)
    width, height = image.size
    base_width = max(2, round(min(width, height) * 0.006))
    for annotation in spec.get("annotations") or []:
        kind = str(annotation.get("type") or "")
        color = str(annotation.get("color") or "#c00000")
        line_width = max(1, int(annotation.get("width") or base_width))
        if kind in {"line", "arrow", "rectangle", "ellipse"}:
            start = _overlay_point(annotation.get("start"), width, height)
            end = _overlay_point(annotation.get("end"), width, height)
            box = [min(start[0], end[0]), min(start[1], end[1]), max(start[0], end[0]), max(start[1], end[1])]
            if kind == "line":
                draw.line([start, end], fill=color, width=line_width)
            elif kind == "arrow":
                draw.line([start, end], fill=color, width=line_width)
                angle = math.atan2(end[1] - start[1], end[0] - start[0])
                arrow_size = max(8, line_width * 4)
                wing_a = (
                    round(end[0] - arrow_size * math.cos(angle - math.pi / 6)),
                    round(end[1] - arrow_size * math.sin(angle - math.pi / 6)),
                )
                wing_b = (
                    round(end[0] - arrow_size * math.cos(angle + math.pi / 6)),
                    round(end[1] - arrow_size * math.sin(angle + math.pi / 6)),
                )
                draw.polygon([end, wing_a, wing_b], fill=color)
            elif kind == "rectangle":
                draw.rectangle(box, outline=color, width=line_width)
            else:
                draw.ellipse(box, outline=color, width=line_width)
            label = str(annotation.get("label") or "").strip()
            if label:
                font = _overlay_font(int(annotation.get("font_size") or max(16, round(min(width, height) * 0.035))))
                label_xy = (round((start[0] + end[0]) / 2), round((start[1] + end[1]) / 2))
                draw.text(label_xy, label, fill=color, font=font, stroke_width=max(1, line_width // 2), stroke_fill="white")
        elif kind == "point":
            xy = _overlay_point(annotation.get("xy"), width, height)
            radius = max(3, int(annotation.get("radius") or line_width * 2))
            draw.ellipse([xy[0] - radius, xy[1] - radius, xy[0] + radius, xy[1] + radius], fill=color)
        elif kind == "text":
            xy = _overlay_point(annotation.get("xy"), width, height)
            font = _overlay_font(int(annotation.get("font_size") or max(16, round(min(width, height) * 0.035))))
            text = str(annotation.get("text") or "")
            draw.text(xy, text, fill=color, font=font, stroke_width=max(1, line_width // 2), stroke_fill="white")
    output.parent.mkdir(parents=True, exist_ok=True)
    image.save(output, format="PNG")


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


def draw_generic_axis_curve(spec: dict[str, Any], output: Path) -> None:
    if not spec.get("points"):
        raise ValueError("generic_axis_curve: points are required; refusing to invent curve data")
    draw_line_chart(spec, output)


def draw_multi_curve_axis_plot(spec: dict[str, Any], output: Path) -> None:
    series = spec.get("series") if isinstance(spec.get("series"), list) else []
    if not series:
        raise ValueError("multi_curve_axis_plot: series are required; refusing to invent comparison data")
    fig, ax = plt.subplots(figsize=(5.8, 3.6), dpi=180)
    rendered_series = 0
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
        rendered_series += 1
    if not rendered_series:
        plt.close(fig)
        raise ValueError("multi_curve_axis_plot: no series contains valid numeric points")
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
        raise ValueError("binary_phase_diagram: curves are required; refusing to invent phase boundaries")
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
        plt.close(fig)
        raise ValueError("binary_phase_diagram: phase_regions are required; refusing to invent phase labels")
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
    if not structure:
        raise ValueError("crystal_unit_cell: structure is required")
    if structure in {"fcc", "face_centered_cubic", "面心立方"}:
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
    planes = spec.get("planes") if isinstance(spec.get("planes"), list) else []
    directions = spec.get("directions") if isinstance(spec.get("directions"), list) else []
    if not planes or not directions:
        raise ValueError("crystal_plane_direction: planes and directions are required")

    def item_label(item: Any) -> str:
        if isinstance(item, dict):
            return str(item.get("label") or item.get("index") or item.get("name") or "").strip()
        return str(item or "").strip()

    plane_label = item_label(planes[0])
    direction_label = item_label(directions[0])
    if not plane_label or not direction_label:
        raise ValueError("crystal_plane_direction: plane and direction labels are required")
    custom = {
        **spec,
        "kind": "custom_diagram",
        "elements": [
            {"type": "line", "start": [0, 0], "end": [1, 0], "label": "a"},
            {"type": "line", "start": [0, 0], "end": [0, 1], "label": "b"},
            {"type": "line", "start": [1, 0], "end": [1, 1]},
            {"type": "line", "start": [0, 1], "end": [1, 1]},
            {"type": "line", "start": [0.15, 0.8], "end": [0.85, 0.2], "label": plane_label, "linewidth": 2},
            {"type": "arrow", "start": [0.15, 0.15], "end": [0.85, 0.75], "label": direction_label},
        ],
    }
    draw_custom_diagram(custom, output)


def _passes_cubic_extinction(h: int, k: int, l_index: int, lattice: str) -> bool:
    lattice = _normalize_lattice_name(lattice)
    if lattice in {"bcc", "body_centered_cubic"}:
        return (h + k + l_index) % 2 == 0
    if lattice in {"fcc", "face_centered_cubic"}:
        return h % 2 == k % 2 == l_index % 2
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
    # The materials prompt contract requires crystallographic negative
    # indices to use overbars. Accept that representation at the renderer
    # boundary instead of requiring the legacy ``1-10`` form.
    text = re.sub(r"\\(?:bar|overline)\{\s*([+-]?\d+)\s*\}", r"-\1", text)
    text = re.sub(r"(\d)\u0305", r"-\1", text)
    text = re.sub(r"[\u00af\u203e]\s*(\d)", r"-\1", text)
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


def _format_hkl_label(h: int, k: int, l_index: int) -> str:
    return f"({h} {k} {l_index})"


def _format_hkl_plot_label(h: int, k: int, l_index: int) -> str:
    """Use crystallographic overbars for negative indices in plotted labels."""

    parts = [rf"\overline{{{abs(value)}}}" if value < 0 else str(value) for value in (h, k, l_index)]
    return r"$(" + r"\ ".join(parts) + r")$"


def _zone_axis_plot_title(spec: dict[str, Any], u: int, v: int, w: int) -> str:
    """Return a safe plot title instead of treating a prose caption as math text."""

    explicit = str(spec.get("title") or "").strip()
    if explicit and "\\" not in explicit:
        return explicit
    lattice = _normalize_lattice_name(spec.get("lattice") or "generic_cubic")
    lattice_label = {
        "bcc": "体心立方点阵",
        "body_centered_cubic": "体心立方点阵",
        "fcc": "面心立方点阵",
        "face_centered_cubic": "面心立方点阵",
    }.get(lattice, "立方点阵")
    return f"{lattice_label} [{u} {v} {w}] 带轴电子衍射花样"


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
    if kind in {"generic_axis_curve", "multi_curve_axis_plot"}:
        defaulted_fields: list[str] = []
        defaults = {
            "caption": normalized.get("title") or "坐标关系图",
            "x_label": "x",
            "y_label": "y",
        }
        for field, fallback in defaults.items():
            if str(normalized.get(field) or "").strip():
                continue
            normalized[field] = fallback
            defaulted_fields.append(field)
        if defaulted_fields:
            normalized["schema_defaulted_fields"] = list(
                dict.fromkeys(
                    [
                        *(
                            str(field)
                            for field in normalized.get("schema_defaulted_fields", []) or []
                            if str(field).strip()
                        ),
                        *defaulted_fields,
                    ]
                )
            )
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
            if not peak.get("label") and peak.get("hkl") not in (None, ""):
                peak["label"] = peak.get("hkl")
            if peak.get("label"):
                peak["label"] = _normalize_peak_label(peak.get("label"))
            style_aliases = {
                "solid": "-",
                "dashed": "--",
                "dotted": ":",
                "dashdot": "-.",
                "dash-dot": "-.",
            }
            raw_style = str(peak.get("style") or peak.get("linestyle") or "").strip().lower()
            if raw_style in style_aliases:
                peak["style"] = style_aliases[raw_style]
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
    spec = normalize_figure_spec(spec)
    kind = str(spec.get("kind") or "").strip()
    issues: list[str] = []
    registry_entry = get_schema(kind)
    # Every registered renderer honours the same contract, regardless of
    # whether the spec came from a model, a restored task, or internal code.
    # Source-dependent validation previously let legacy calls manufacture
    # plausible-looking professional figures from renderer defaults.
    if registry_entry:
        for field in registry_entry.get("required_fields", []) or []:
            value = spec.get(field)
            if value in (None, "", []):
                issues.append(f"{kind}: required field {field} is missing")
        if issues:
            return issues
    if kind == "source_image_overlay":
        binding_issue = str(spec.get("overlay_binding_issue") or "").strip()
        return [binding_issue] if binding_issue else validate_source_image_overlay_spec(spec)
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
        for h, k, l_index in non_origin_labels:
            if h * u + k * v + l_index * w != 0:
                issues.append(f"zone_axis_diffraction: labelled index {_format_hkl_label(h, k, l_index)} does not satisfy zone-axis law")
            if apply_extinction and not _passes_cubic_extinction(h, k, l_index, lattice):
                issues.append(f"zone_axis_diffraction: labelled index {_format_hkl_label(h, k, l_index)} violates cubic extinction rule")
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
            for l_index in range(-max_index, max_index + 1):
                if h == k == l_index == 0 or h * u + k * v + l_index * w == 0:
                    if not apply_extinction or _passes_cubic_extinction(h, k, l_index, lattice):
                        points.append((h, k, l_index))
    if (0, 0, 0) not in points:
        points.append((0, 0, 0))
    if abs(u) == abs(v) and w == 0 and u and v:
        def project(h: int, k: int, l_index: int) -> tuple[float, float]:
            return ((h - k) / math.sqrt(2), float(l_index))

        basis_labels = ("", "")
    else:
        b1 = (v, -u, 0) if (u or v) else (1, 0, 0)
        b2 = (w * u, w * v, -(u * u + v * v)) if (u or v) else (0, 1, 0)
        if b2 == (0, 0, 0):
            b2 = (0, 0, 1)

        def project(h: int, k: int, l_index: int) -> tuple[float, float]:
            return (
                float(h * b1[0] + k * b1[1] + l_index * b1[2]),
                float(-(h * b2[0] + k * b2[1] + l_index * b2[2])),
            )

        basis_labels = ("g1*", "g2*")
    fig, ax = plt.subplots(figsize=(5.2, 4.8), dpi=200)
    projected_points = [(h, k, l_index, *project(h, k, l_index)) for h, k, l_index in points]
    xs = [item[3] for item in projected_points] or [-1, 1]
    ys = [item[4] for item in projected_points] or [-1, 1]
    x_center = (min(xs) + max(xs)) / 2
    y_center = (min(ys) + max(ys)) / 2
    for h, k, l_index in points:
        x, y = project(h, k, l_index)
        base_size = float(spec.get("spot_size") or 42)
        size = base_size * 1.45 if (h, k, l_index) == (0, 0, 0) else base_size
        ax.scatter([x], [y], s=size, c="#111")
        if (h, k, l_index) in labels or (h, k, l_index) == (0, 0, 0):
            x_offset = -0.10 if x > x_center else 0.10
            y_offset = -0.10 if y > y_center else 0.10
            ax.text(
                x + x_offset,
                y + y_offset,
                _format_hkl_plot_label(h, k, l_index),
                fontsize=8,
                ha="right" if x_offset < 0 else "left",
                va="top" if y_offset < 0 else "bottom",
                clip_on=True,
            )
    xpad = max(0.8, (max(xs) - min(xs)) * 0.12)
    ypad = max(0.8, (max(ys) - min(ys)) * 0.12)
    xmin, xmax = min(xs) - xpad, max(xs) + xpad
    ymin, ymax = min(ys) - ypad, max(ys) + ypad
    if bool(spec.get("show_basis_axes", False)):
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
        f"带轴 [{u} {v} {w}]",
        fontsize=8,
        color="#555",
        ha="left",
        va="top",
    )
    ax.set_xlim(xmin, xmax)
    ax.set_ylim(ymin, ymax)
    ax.set_aspect("equal", adjustable="box")
    ax.axis("off")
    title = _zone_axis_plot_title(spec, u, v, w)
    ax.set_title(_wrap_plot_title(title, width=28), fontsize=11)
    fig.tight_layout()
    output.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(output)
    plt.close(fig)


def draw_xrd_pattern(spec: dict[str, Any], output: Path) -> None:
    spec = normalize_figure_spec(spec)
    peaks = spec.get("peaks") if isinstance(spec.get("peaks"), list) else []
    if not peaks:
        raise ValueError("xrd_pattern: peaks are required; refusing to invent diffraction data")
    fig, ax = plt.subplots(figsize=(6.0, 3.4), dpi=180)
    xs: list[float] = []
    legend_seen: set[str] = set()
    pattern_labels = list(
        dict.fromkeys(
            str(peak.get("pattern_label") or peak.get("pattern") or "").strip()
            for peak in peaks
            if str(peak.get("pattern_label") or peak.get("pattern") or "").strip()
        )
    )
    pattern_rows = {label: float(index) for index, label in enumerate(pattern_labels)}
    for peak in peaks:
        position = _normalized_peak_position(peak)
        if position is None:
            continue
        x = float(position)
        xs.append(x)
        height = float(peak.get("intensity") or peak.get("height") or 0.5)
        pattern_label = str(peak.get("pattern_label") or peak.get("pattern") or "").strip()
        baseline = pattern_rows.get(pattern_label, 0.0) if pattern_rows else 0.0
        y = baseline + (height * 0.72 if pattern_rows else height)
        style = str(peak.get("style") or peak.get("linestyle") or "-")
        is_super = style in {"--", ":", "-."}
        color = "#111"
        raw_legend = peak.get("phase_label") or ("新增超结构峰" if is_super else "原有峰")
        legend_label = str(raw_legend) if str(raw_legend) not in legend_seen else None
        if legend_label:
            legend_seen.add(str(raw_legend))
        ax.vlines(x, baseline, y, color=color, lw=2.0, linestyles=style, label=legend_label)
        if peak.get("label"):
            ax.text(x, y + 0.035, str(peak.get("label")), fontsize=7.5, ha="center", color=color)
    ax.set_xlabel(spec.get("x_label", "相对峰位"))
    if pattern_rows:
        ax.set_ylabel(spec.get("y_label", "状态"))
        ax.set_yticks([baseline + 0.26 for baseline in pattern_rows.values()], labels=list(pattern_rows))
        for baseline in pattern_rows.values():
            ax.axhline(baseline, color="#777", lw=0.7)
        ax.set_ylim(-0.08, max(pattern_rows.values()) + 0.98)
    else:
        ax.set_ylabel(spec.get("y_label", "Intensity / a.u."))
        ax.set_ylim(0, max(float(p.get("intensity") or p.get("height") or 0.5) for p in peaks) * 1.25)
    if xs:
        span = max(xs) - min(xs)
        pad = max(0.05, span * 0.08)
        ax.set_xlim(min(xs) - pad, max(xs) + pad)
    if spec.get("title"):
        ax.set_title(_wrap_plot_title(spec.get("title")), fontsize=11, pad=10)
    ax.grid(True, axis="y", alpha=0.18)
    if legend_seen:
        ax.legend(fontsize=8, frameon=False, loc="lower right")
    fig.tight_layout()
    output.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(output)
    plt.close(fig)


def draw_microstructure_schematic(spec: dict[str, Any], output: Path) -> None:
    fig, ax = plt.subplots(figsize=(6.2, 4.2), dpi=180)
    ax.set_aspect("equal")
    ax.axis("off")
    field = Rectangle((0.0, 0.0), 1.0, 0.78, facecolor="#fafafa", edgecolor="#111", linewidth=1.25, zorder=1)
    ax.add_patch(field)
    raw_features = spec.get("features") if isinstance(spec.get("features"), list) else []
    features = [item if isinstance(item, dict) else {"label": str(item)} for item in raw_features]
    if not features:
        plt.close(fig)
        raise ValueError("microstructure_schematic: features are required; refusing to invent constituents")

    def morphology_for(item: dict[str, Any]) -> str:
        explicit = str(item.get("morphology") or item.get("shape") or "").strip().lower()
        aliases = {
            "grain_boundary_network": "boundary_network",
            "blocky": "island",
            "block": "island",
            "base": "matrix",
        }
        if explicit:
            normalized = aliases.get(explicit, explicit)
            if normalized in {
                "matrix",
                "grain",
                "boundary_network",
                "particles",
                "island",
                "dendrite",
                "lamellar_colony",
                "lamellar",
                "eutectic",
            }:
                return normalized
            # Figure specs are model-authored and commonly use descriptive
            # natural language rather than the registry enum. Normalize those
            # phrases before routing; otherwise valid morphology silently
            # falls through to the generic particle renderer.
            phrase_rules = (
                (("枝晶", "dendrit"), "dendrite"),
                (("层片", "片层", "lamell", "eutectic", "共晶"), "lamellar_colony"),
                (("晶界", "边界网", "boundary", "network"), "boundary_network"),
                (("等轴", "晶粒", "grain"), "grain"),
                (("基体", "matrix", "background"), "matrix"),
                (("岛", "块状", "blocky", "island"), "island"),
                (("颗粒", "粒子", "析出物", "particle", "precipitate"), "particles"),
            )
            for tokens, canonical in phrase_rules:
                if any(token in normalized for token in tokens):
                    return canonical
        label = str(item.get("label") or "")
        if any(token in label for token in ("晶界", "网状", "二次渗碳体", "Fe3C_II", "Fe₃C_II")):
            return "boundary_network"
        if any(token in label for token in ("层片", "共晶", "珠光体", "莱氏体", "Ld", "Le")):
            return "lamellar_colony"
        if any(token in label for token in ("初生", "基体", "晶粒", "树枝")):
            return "island"
        return "particles"

    def distribution_for(item: dict[str, Any]) -> str:
        distribution = str(item.get("distribution") or "").strip().lower()
        spatial_role = str(item.get("spatial_role") or "").strip().lower()
        return " ".join(value for value in (spatial_role, distribution) if value)

    def is_matrix_feature(item: dict[str, Any]) -> bool:
        distribution = distribution_for(item)
        return morphology_for(item) == "matrix" or any(
            token in distribution
            for token in ("基体", "占据大部分", "填满", "整个背景", "连续", "matrix", "surrounding", "background")
        )

    def is_boundary_feature(item: dict[str, Any]) -> bool:
        morphology = morphology_for(item)
        distribution = distribution_for(item)
        return morphology in {"boundary_network", "boundary", "network"} or any(
            token in distribution for token in ("沿晶界", "晶界网", "grain boundary network", "boundary_network")
        )

    def is_interstitial_feature(item: dict[str, Any]) -> bool:
        distribution = distribution_for(item)
        return any(token in distribution for token in ("晶界或枝晶间", "晶粒之间", "枝晶间", "interdendritic", "intergranular"))

    def add_clipped_line(xs: list[float], ys: list[float], **kwargs: Any) -> Any:
        lines = ax.plot(xs, ys, clip_on=True, **kwargs)
        for line in lines:
            line.set_clip_path(field)
        return lines[0] if lines else None

    def add_oriented_region(
        start: tuple[float, float],
        end: tuple[float, float],
        width: float,
        *,
        facecolor: str,
        zorder: int,
        lamellae: bool = False,
    ) -> Polygon:
        x0, y0 = start
        x1, y1 = end
        length = math.hypot(x1 - x0, y1 - y0) or 1.0
        nx = -(y1 - y0) / length * width / 2
        ny = (x1 - x0) / length * width / 2
        region = Polygon(
            [(x0 + nx, y0 + ny), (x1 + nx, y1 + ny), (x1 - nx, y1 - ny), (x0 - nx, y0 - ny)],
            closed=True,
            facecolor=facecolor,
            edgecolor="#111",
            linewidth=1.05,
            zorder=zorder,
        )
        region.set_clip_path(field)
        ax.add_patch(region)
        if lamellae:
            angle = math.atan2(y1 - y0, x1 - x0)
            normal = angle + math.pi / 2
            # Explicit alternating dark lamellae are more informative than a
            # decorative hatch: they visibly describe a two-constituent colony.
            for fraction in (0.10, 0.22, 0.34, 0.46, 0.58, 0.70, 0.82, 0.94):
                cx = x0 + (x1 - x0) * fraction
                cy = y0 + (y1 - y0) * fraction
                dx = width * 0.47 * math.cos(normal)
                dy = width * 0.47 * math.sin(normal)
                line = add_clipped_line(
                    [cx - dx, cx + dx],
                    [cy - dy, cy + dy],
                    color="#222",
                    linewidth=1.05,
                    zorder=zorder + 1,
                )
                line.set_clip_path(region)
        return region

    label_anchors: list[tuple[str, tuple[float, float]]] = []
    seen_labels: set[str] = set()

    def remember_label(label: str, anchor: tuple[float, float]) -> None:
        clean = str(label or "").strip()
        if clean and clean not in seen_labels:
            seen_labels.add(clean)
            label_anchors.append((clean, anchor))

    ordered_features = sorted(enumerate(features), key=lambda pair: 0 if is_matrix_feature(pair[1]) else 1)
    for index, item in ordered_features:
        xy = _xy(item.get("xy"), (0.5, 0.4))
        morphology = morphology_for(item)
        distribution = distribution_for(item)
        label = str(item.get("label") or "").strip()
        is_matrix = is_matrix_feature(item)

        if is_matrix and morphology == "matrix":
            # A eutectic/ledeburite matrix is itself a two-constituent field.
            # Fine stippling communicates that fact at textbook overview scale
            # without competing with the dark proeutectic constituent.
            matrix_is_composite = any(
                token in f"{label} {distribution}".lower()
                for token in ("莱氏体", "ledebur", "eutectic", "共晶")
            )
            matrix_patch = Rectangle(
                (0.005, 0.005),
                0.99,
                0.77,
                facecolor="#f1f1f1",
                edgecolor="#888",
                linewidth=0.45,
                hatch="////" if matrix_is_composite else "..",
                zorder=2,
            )
            ax.add_patch(matrix_patch)
            remember_label(label, (0.86, 0.66))
            continue

        if is_matrix and morphology in {"grain", "island"}:
            # A grain matrix is a continuous field partition, not a set of
            # floating ellipses.  The deterministic cellular network works for
            # metals, ceramics, geology, and biological tissue schematics.
            grain_polygons = [
                [(0.01, 0.01), (0.34, 0.01), (0.29, 0.26), (0.05, 0.35)],
                [(0.34, 0.01), (0.66, 0.01), (0.58, 0.28), (0.29, 0.26)],
                [(0.66, 0.01), (0.99, 0.01), (0.98, 0.31), (0.73, 0.38), (0.58, 0.28)],
                [(0.05, 0.35), (0.29, 0.26), (0.48, 0.48), (0.27, 0.77), (0.01, 0.77)],
                [(0.29, 0.26), (0.58, 0.28), (0.73, 0.38), (0.60, 0.77), (0.27, 0.77), (0.48, 0.48)],
                [(0.73, 0.38), (0.98, 0.31), (0.99, 0.77), (0.60, 0.77)],
            ]
            for polygon in grain_polygons:
                ax.add_patch(Polygon(polygon, closed=True, facecolor="#fafafa", edgecolor="#555", linewidth=1.0, zorder=3))
            remember_label(label, (0.16, 0.52))
            continue

        if is_boundary_feature(item):
            network = [
                ([0.02, 0.28, 0.48, 0.72, 0.98], [0.12, 0.31, 0.16, 0.36, 0.22]),
                ([0.05, 0.30, 0.56, 0.78, 0.97], [0.67, 0.50, 0.69, 0.51, 0.65]),
                ([0.28, 0.30, 0.48, 0.56, 0.72, 0.78], [0.31, 0.50, 0.16, 0.69, 0.36, 0.51]),
            ]
            for xs, ys in network:
                add_clipped_line(xs, ys, color="#111", linewidth=1.5, solid_capstyle="round", zorder=6)
            remember_label(label, (0.48, 0.16))
            continue

        if morphology == "dendrite" or "树枝" in distribution:
            main_anchor = (0.46, 0.40) if is_matrix else (0.26, 0.29)

            def add_oriented_lamellar_region(
                start: tuple[float, float],
                end: tuple[float, float],
                width: float,
                *,
                zorder: int,
            ) -> None:
                """Draw one filled part of a dendritic colony with real lamellae.

                The filled region establishes the dark dendritic constituent at
                overview scale. Fine light transverse strokes hint at its
                lamellar substructure without changing the constituent boundary.
                """

                x0, y0 = start
                x1, y1 = end
                region = add_oriented_region(
                    start,
                    end,
                    width,
                    facecolor="#cfcfcf",
                    zorder=zorder,
                )
                angle = math.atan2(y1 - y0, x1 - x0)
                normal = angle + math.pi / 2
                for fraction in (0.18, 0.34, 0.50, 0.66, 0.82):
                    cx = x0 + (x1 - x0) * fraction
                    cy = y0 + (y1 - y0) * fraction
                    dx = width * 0.34 * math.cos(normal)
                    dy = width * 0.34 * math.sin(normal)
                    line = add_clipped_line(
                        [cx - dx, cx + dx],
                        [cy - dy, cy + dy],
                        color="#222222",
                        linewidth=0.65,
                        zorder=zorder + 2,
                    )
                    line.set_clip_path(region)

            stems = (
                (
                    (0.03, 0.60, -0.30, 0.46),
                    (0.25, 0.13, 0.46, 0.52),
                    (0.58, 0.58, -0.40, 0.49),
                )
                if is_matrix
                else (
                    # Distribute isolated dendritic colonies through the field
                    # instead of lining them up along the lower margin.  The
                    # three orientations remain deterministic and leave enough
                    # surrounding matrix visible for a clear two-constituent
                    # textbook schematic.
                    (0.04, 0.60, -0.28, 0.40),
                    (0.29, 0.14, 0.43, 0.42),
                    (0.59, 0.62, -0.42, 0.40),
                )
            )
            for stem_index, (x0, y0, angle, length) in enumerate(stems):
                x1 = x0 + length * math.cos(angle)
                y1 = y0 + length * math.sin(angle)
                lamellar_dendrite = morphology in {"lamellar_colony", "lamellar", "eutectic"}
                if lamellar_dendrite:
                    add_oriented_lamellar_region((x0, y0), (x1, y1), 0.072, zorder=7)
                elif is_matrix:
                    add_clipped_line(
                        [x0, x1], [y0, y1], color="#111", linewidth=22, solid_capstyle="round", zorder=4
                    )
                    add_clipped_line(
                        [x0, x1], [y0, y1], color="#dedede", linewidth=17, solid_capstyle="round", zorder=5
                    )
                else:
                    # An isolated/dispersed dendrite is still a constituent
                    # region, not a one-pixel skeleton. Draw a bounded filled
                    # arm so it remains legible against an interstitial matrix.
                    add_clipped_line(
                        [x0, x1], [y0, y1], color="#111", linewidth=13, solid_capstyle="round", zorder=6
                    )
                    add_clipped_line(
                        [x0, x1], [y0, y1], color="#e2e2e2", linewidth=9, solid_capstyle="round", zorder=7
                    )
                for branch_index, fraction in enumerate((0.25, 0.48, 0.70, 0.86)):
                    bx = x0 + (x1 - x0) * fraction
                    by = y0 + (y1 - y0) * fraction
                    direction = -1 if (branch_index + stem_index) % 2 else 1
                    branch_angle = angle + direction * 1.05
                    dx = 0.06 * math.cos(branch_angle)
                    dy = 0.06 * math.sin(branch_angle)
                    if lamellar_dendrite:
                        add_oriented_lamellar_region((bx, by), (bx + dx, by + dy), 0.043, zorder=8)
                    elif is_matrix:
                        branch_length = 0.13
                        dx = branch_length * math.cos(branch_angle)
                        dy = branch_length * math.sin(branch_angle)
                        add_clipped_line(
                            [bx, bx + dx],
                            [by, by + dy],
                            color="#111",
                            linewidth=14,
                            solid_capstyle="round",
                            zorder=4,
                        )
                        add_clipped_line(
                            [bx, bx + dx],
                            [by, by + dy],
                            color="#dedede",
                            linewidth=10,
                            solid_capstyle="round",
                            zorder=5,
                        )
                    else:
                        add_clipped_line(
                            [bx, bx + dx], [by, by + dy], color="#111", linewidth=8, solid_capstyle="round", zorder=6
                        )
                        add_clipped_line(
                            [bx, bx + dx], [by, by + dy], color="#e2e2e2", linewidth=5, solid_capstyle="round", zorder=7
                        )
            remember_label(label, main_anchor)
            continue

        if morphology in {"lamellar_colony", "lamellar", "eutectic"}:
            if is_interstitial_feature(item):
                # Intergranular/interdendritic eutectic is the continuous
                # residual field around the primary framework, not a handful
                # of floating colonies.  Draw it below the primary phase so
                # the primary dendrites remain visible while every remaining
                # interdendritic area carries a clear two-phase lamellar
                # texture.  This interpretation is discipline-neutral: the
                # same spatial contract applies to any matrix/framework plus
                # interstitial constituent schematic.
                interstitial_field = Rectangle(
                    (0.005, 0.005),
                    0.99,
                    0.77,
                    facecolor="#fbfbfb",
                    edgecolor="#777",
                    linewidth=0.45,
                    hatch="||||",
                    zorder=2,
                )
                interstitial_field.set_clip_path(field)
                ax.add_patch(interstitial_field)
                remember_label(label, (0.84, 0.18))
            elif is_matrix:
                lamellar_matrix = Rectangle((0.005, 0.005), 0.99, 0.77, facecolor="#f5f5f5", edgecolor="#777", linewidth=0.5, hatch="////", zorder=2)
                ax.add_patch(lamellar_matrix)
                remember_label(label, (0.86, 0.12))
            else:
                centers = [(0.28, 0.32), (0.66, 0.53)] if any(token in distribution for token in ("分布", "块状", "colony")) else [xy]
                for colony_index, center in enumerate(centers):
                    colony = Ellipse(center, 0.27, 0.18, angle=25 + colony_index * 55, facecolor="#f5f5f5", edgecolor="#111", linewidth=1.0, hatch="////", zorder=5)
                    colony.set_clip_path(field)
                    ax.add_patch(colony)
                remember_label(label, centers[0])
            continue

        if morphology in {"grain", "island"}:
            centers = [(0.24, 0.25), (0.55, 0.56), (0.81, 0.27)] if any(token in distribution for token in ("分散", "多个", "块状")) else [xy]
            for grain_index, center in enumerate(centers):
                island = Ellipse(
                    center,
                    0.25,
                    0.15,
                    angle=(index * 29 + grain_index * 37) % 110,
                    facecolor="white",
                    edgecolor="#111",
                    linewidth=1.15,
                    hatch=".." if morphology == "island" else None,
                    zorder=5,
                )
                island.set_clip_path(field)
                ax.add_patch(island)
            remember_label(label, centers[0])
            continue

        count = max(8, min(24, int(item.get("count") or 14)))
        if any(token in distribution for token in ("弥散", "分散", "throughout", "within", "分布于", "内部")):
            # Keep intragranular particles away from the intergranular bands so
            # the intended containment relation remains visible.
            px = [0.10 + ((n * 37) % 78) / 100 for n in range(count)]
            py = [0.09 + ((n * 53) % 62) / 100 for n in range(count)]
        else:
            px = [xy[0] + 0.08 * math.cos(2 * math.pi * n / count) for n in range(count)]
            py = [xy[1] + 0.055 * math.sin(2 * math.pi * n / count) for n in range(count)]
        scatter = ax.scatter(px, py, s=13, color="#111", marker="o", zorder=8, clip_on=True)
        scatter.set_clip_path(field)
        middle = len(px) // 2
        remember_label(label, (px[middle], py[middle]))

    matrix_label = str(spec.get("matrix_label") or "").strip()
    if matrix_label and matrix_label not in seen_labels:
        remember_label(matrix_label, (0.88, 0.10))

    # Labels are laid out in dedicated margins and point to actual rendered
    # geometry.  This prevents model-provided placeholder coordinates from
    # creating overlaps or pointing every label at the background.
    # Keep labels below a stable top safety margin. Matplotlib's tight layout
    # can otherwise crop CJK glyph ascenders when annotations sit close to the
    # axes limit, especially for wide scientific labels.
    top_slots = [(0.03, 0.86), (0.38, 0.86), (0.73, 0.86)]
    bottom_slots = [(0.03, -0.09), (0.38, -0.09), (0.73, -0.09)]
    available_top = list(top_slots)
    assigned_top: list[tuple[float, float]] = []
    for _, anchor in label_anchors[:3]:
        nearest = min(available_top, key=lambda slot: abs(slot[0] - anchor[0]))
        assigned_top.append(nearest)
        available_top.remove(nearest)
    for label_index, (label, anchor) in enumerate(label_anchors[:6]):
        slot = assigned_top[label_index] if label_index < 3 else bottom_slots[label_index - 3]
        ax.annotate(
            _matplotlib_scientific_text(label),
            xy=anchor,
            xytext=slot,
            fontsize=8,
            ha="left",
            va="center",
            color="#111",
            arrowprops={"arrowstyle": "-", "lw": 0.7, "color": "#444"},
            bbox={"boxstyle": "round,pad=0.12", "facecolor": "white", "edgecolor": "none", "alpha": 0.94},
            zorder=10,
        )
    ax.set_xlim(-0.06, 1.06)
    ax.set_ylim(-0.14, 1.00)
    if str(spec.get("title") or "").strip():
        ax.set_title(str(spec.get("title")), fontsize=11)
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
    start_points = [point for point in (_point_xy(item) for item in spec.get("start_curve") or []) if point is not None]
    finish_points = [point for point in (_point_xy(item) for item in spec.get("finish_curve") or []) if point is not None]
    if not start_points or not finish_points:
        raise ValueError("ttt_diagram: start_curve and finish_curve are required")
    fig, ax = plt.subplots(figsize=(5.8, 3.8), dpi=180)
    ax.semilogx([point[0] for point in start_points], [point[1] for point in start_points], color="#111", lw=1.8, label="开始")
    ax.semilogx([point[0] for point in finish_points], [point[1] for point in finish_points], color="#111", lw=1.8, ls="--", label="终了")
    if spec.get("ms_temperature") not in (None, ""):
        ms_temperature = float(spec["ms_temperature"])
        ax.axhline(ms_temperature, color="#666", lw=1.2)
        ax.text(min(point[0] for point in start_points), ms_temperature, "Ms", fontsize=8, color="#555")
    for region in spec.get("regions") or []:
        if not isinstance(region, dict):
            continue
        point = _point_xy(region.get("xy"))
        label = str(region.get("label") or "").strip()
        if point is not None and label:
            ax.text(point[0], point[1], label, fontsize=8)
    ax.set_xlabel(spec.get("x_label") or "t / s")
    ax.set_ylabel(spec.get("y_label") or "T / °C")
    ax.set_title(spec.get("title") or spec.get("caption") or "TTT 等温转变曲线", fontsize=11)
    ax.grid(True, alpha=0.18)
    ax.legend(fontsize=8)
    fig.tight_layout()
    output.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(output)
    plt.close(fig)


def draw_stress_strain_curve(spec: dict[str, Any], output: Path) -> None:
    points = [point for point in (_point_xy(item) for item in spec.get("points") or []) if point is not None]
    if not points:
        raise ValueError("stress_strain_curve: points are required; refusing to invent material behavior")
    fig, ax = plt.subplots(figsize=(5.8, 3.6), dpi=180)
    xs = [point[0] for point in points]
    ys = [point[1] for point in points]
    ax.plot(xs, ys, color="#111", lw=1.9)
    for annotation in spec.get("annotations") or spec.get("labels") or []:
        if not isinstance(annotation, dict):
            continue
        point = _point_xy(annotation.get("xy") or [annotation.get("x"), annotation.get("y")])
        label = str(annotation.get("text") or annotation.get("label") or "").strip()
        if point is not None and label:
            ax.text(point[0], point[1], label, fontsize=8)
    ax.set_xlabel(spec.get("x_label") or "ε")
    ax.set_ylabel(spec.get("y_label") or "σ")
    ax.set_title(spec.get("title") or spec.get("caption") or "应力-应变曲线", fontsize=11)
    ax.grid(True, alpha=0.18)
    fig.tight_layout()
    output.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(output)
    plt.close(fig)


def draw_dsc_curve(spec: dict[str, Any], output: Path) -> None:
    points = [point for point in (_point_xy(item) for item in spec.get("points") or []) if point is not None]
    if not points:
        raise ValueError("dsc_curve: points are required; refusing to invent thermal events")
    fig, ax = plt.subplots(figsize=(6.0, 3.4), dpi=180)
    xs = [point[0] for point in points]
    ys = [point[1] for point in points]
    ax.plot(xs, ys, color="#111", lw=1.8)
    for field, label in (("tg", "Tg"), ("tc", "Tc"), ("tm", "Tm")):
        if spec.get(field) in (None, ""):
            continue
        x = float(spec[field])
        ax.axvline(x, color="#666", ls="--", lw=0.9)
        ax.text(x, max(ys), label, fontsize=8)
    ax.set_xlabel(spec.get("x_label") or "T / °C")
    ax.set_ylabel(spec.get("y_label") or "Heat flow")
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
    if not str(spec.get("structure") or "").strip():
        raise ValueError("ceramic_crystal_structure: structure is required")
    draw_crystal_unit_cell(spec, output)


def draw_sintering_microstructure_evolution(spec: dict[str, Any], output: Path) -> None:
    titles = spec.get("stages") if isinstance(spec.get("stages"), list) else []
    if len(titles) != 3:
        raise ValueError("sintering_microstructure_evolution: exactly three named stages are required")
    fig, axes = plt.subplots(1, 3, figsize=(7.2, 2.8), dpi=180)
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
    logx: bool = False,
) -> None:
    raw_points = spec.get("points") if isinstance(spec.get("points"), list) else []
    parsed_points = [_point_xy(point) for point in raw_points]
    parsed_points = [point for point in parsed_points if point is not None]
    if not parsed_points:
        raise ValueError(f"{str(spec.get('kind') or 'profile_curve')}: points are required; refusing to invent curve data")
    if logx and any(point[0] <= 0 for point in parsed_points):
        raise ValueError(f"{str(spec.get('kind') or 'profile_curve')}: logarithmic x values must be positive")
    xs = [point[0] for point in parsed_points]
    ys = [point[1] for point in parsed_points]
    fig, ax = plt.subplots(figsize=(5.8, 3.6), dpi=180)
    if logx:
        ax.semilogx(xs, ys, color="#111", lw=1.8, marker="o")
    else:
        ax.plot(xs, ys, color="#111", lw=1.8, marker="o")
    annotations = spec.get("annotations") or spec.get("stage_labels") or []
    for annotation in annotations if isinstance(annotations, list) else []:
        if not isinstance(annotation, dict):
            continue
        point = _point_xy(annotation.get("xy") or [annotation.get("x"), annotation.get("y")])
        label = str(annotation.get("text") or annotation.get("label") or "").strip()
        if point is not None and label:
            ax.text(point[0], point[1], _matplotlib_scientific_text(label), fontsize=8)
    ax.set_xlabel(spec.get("x_label", x_label))
    ax.set_ylabel(spec.get("y_label", y_label))
    ax.set_title(spec.get("title") or spec.get("caption") or title, fontsize=11)
    ax.grid(True, alpha=0.18)
    fig.tight_layout()
    output.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(output)
    plt.close(fig)


def _semantic_labels(value: Any) -> list[str]:
    labels: list[str] = []

    def collect(item: Any) -> None:
        if isinstance(item, str):
            if item.strip():
                labels.append(item.strip())
            return
        if isinstance(item, dict):
            label = str(item.get("label") or item.get("name") or item.get("type") or "").strip()
            if label:
                labels.append(label)
            return
        if isinstance(item, (list, tuple)):
            for child in item:
                collect(child)

    collect(value)
    return list(dict.fromkeys(labels))


def _draw_simple_schematic(spec: dict[str, Any], output: Path, *, title: str, labels: list[str]) -> None:
    labels = [str(label).strip() for label in labels if str(label).strip()]
    if not labels:
        raise ValueError(f"{str(spec.get('kind') or 'schematic')}: semantic labels are required")
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
    components = spec.get("components") if isinstance(spec.get("components"), list) else []
    if len(components) < 3:
        raise ValueError("ternary_phase_diagram: three components are required")
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
        plt.close(fig)
        raise ValueError("ternary_phase_diagram: phase_regions are required; refusing to invent phase labels")
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
    steps = spec.get("steps") if isinstance(spec.get("steps"), list) else []
    if not steps:
        raise ValueError("process_flow_diagram: steps are required; refusing to invent process stages")
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
    start_curve = spec.get("start_curve") if isinstance(spec.get("start_curve"), list) else []
    finish_curve = spec.get("finish_curve") if isinstance(spec.get("finish_curve"), list) else []
    cooling_curves = spec.get("cooling_curves") if isinstance(spec.get("cooling_curves"), list) else []
    start_points = [point for point in (_point_xy(item) for item in start_curve) if point is not None]
    finish_points = [point for point in (_point_xy(item) for item in finish_curve) if point is not None]
    if not start_points or not finish_points or not cooling_curves:
        raise ValueError("cct_diagram: start_curve, finish_curve, and cooling_curves are required")
    fig, ax = plt.subplots(figsize=(5.8, 3.8), dpi=180)
    ax.semilogx([point[0] for point in start_points], [point[1] for point in start_points], color="#111", lw=1.7, label="转变开始")
    ax.semilogx([point[0] for point in finish_points], [point[1] for point in finish_points], color="#111", lw=1.7, ls="--", label="转变终了")
    rendered_cooling = 0
    for idx, curve in enumerate(cooling_curves):
        if not isinstance(curve, dict):
            continue
        points = [point for point in (_point_xy(item) for item in curve.get("points") or []) if point is not None]
        if not points:
            continue
        ax.semilogx([point[0] for point in points], [point[1] for point in points], lw=1.1, label=str(curve.get("label") or f"冷却曲线{idx + 1}"))
        rendered_cooling += 1
    if not rendered_cooling:
        plt.close(fig)
        raise ValueError("cct_diagram: cooling_curves contain no valid numeric points")
    if spec.get("ms_temperature") not in (None, ""):
        ms_temperature = float(spec["ms_temperature"])
        ax.axhline(ms_temperature, color="#666", lw=1.1)
        ax.text(min(point[0] for point in start_points), ms_temperature, "Ms", fontsize=8, color="#555")
    ax.set_xlabel(spec.get("x_label") or "t / s")
    ax.set_ylabel(spec.get("y_label") or "T / °C")
    ax.set_title(spec.get("title") or spec.get("caption") or "CCT 连续冷却转变曲线", fontsize=11)
    ax.grid(True, alpha=0.18)
    ax.legend(fontsize=7)
    fig.tight_layout()
    output.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(output)
    plt.close(fig)


def draw_heat_treatment_curve(spec: dict[str, Any], output: Path) -> None:
    _draw_profile_curve(spec, output, title="热处理温度-时间曲线", x_label="t", y_label="T / °C")


def draw_creep_curve(spec: dict[str, Any], output: Path) -> None:
    _draw_profile_curve(spec, output, title="蠕变三阶段曲线", x_label="t", y_label="ε")


def draw_fatigue_sn_curve(spec: dict[str, Any], output: Path) -> None:
    _draw_profile_curve(spec, output, title="S-N 疲劳曲线", x_label="N", y_label="σa", logx=True)


def draw_precipitation_aging_curve(spec: dict[str, Any], output: Path) -> None:
    _draw_profile_curve(spec, output, title="时效强化曲线", x_label="时效时间", y_label="硬度/强度")


def draw_corrosion_polarization_curve(spec: dict[str, Any], output: Path) -> None:
    _draw_profile_curve(spec, output, title="腐蚀极化曲线", x_label="log i", y_label="E")


def draw_welding_thermal_cycle(spec: dict[str, Any], output: Path) -> None:
    _draw_profile_curve(spec, output, title="焊接热循环曲线", x_label="t", y_label="T / °C")


def draw_tga_curve(spec: dict[str, Any], output: Path) -> None:
    _draw_profile_curve(spec, output, title="TGA 热重曲线", x_label="T / °C", y_label="质量保留率 / %")


def draw_dma_curve(spec: dict[str, Any], output: Path) -> None:
    draw_multi_curve_axis_plot({**spec, "x_label": spec.get("x_label") or "T / °C", "y_label": spec.get("y_label") or "相对值", "caption": spec.get("caption") or "DMA 曲线"}, output)


def draw_viscoelastic_creep_curve(spec: dict[str, Any], output: Path) -> None:
    _draw_profile_curve(spec, output, title="黏弹性蠕变-回复曲线", x_label="t", y_label="ε")


def draw_stress_relaxation_curve(spec: dict[str, Any], output: Path) -> None:
    _draw_profile_curve(spec, output, title="应力松弛曲线", x_label="t", y_label="σ")


def draw_time_temperature_superposition(spec: dict[str, Any], output: Path) -> None:
    draw_multi_curve_axis_plot({**spec, "x_label": spec.get("x_label") or "约化时间", "y_label": spec.get("y_label") or "模量", "caption": spec.get("caption") or "时温等效主曲线"}, output)


def draw_polymer_stress_strain_curve(spec: dict[str, Any], output: Path) -> None:
    series = spec.get("series")
    if not series and spec.get("points"):
        series = [{"label": str(spec.get("material_type") or "材料"), "points": spec["points"]}]
    draw_multi_curve_axis_plot({**spec, "series": series, "x_label": spec.get("x_label") or "ε", "y_label": spec.get("y_label") or "σ", "caption": spec.get("caption") or "高分子应力-应变曲线"}, output)


def draw_molecular_weight_distribution(spec: dict[str, Any], output: Path) -> None:
    _draw_profile_curve(spec, output, title="分子量分布曲线", x_label="log M", y_label="频率")


def draw_polymer_blend_phase_diagram(spec: dict[str, Any], output: Path) -> None:
    _draw_profile_curve(spec, output, title="高分子共混相图", x_label="组分 B 体积分数", y_label="T")


def draw_rheology_flow_curve(spec: dict[str, Any], output: Path) -> None:
    _draw_profile_curve(spec, output, title="流变流动曲线", x_label="剪切速率", y_label="黏度", logx=True)


def draw_sintering_densification_curve(spec: dict[str, Any], output: Path) -> None:
    _draw_profile_curve(spec, output, title="烧结致密化曲线", x_label="烧结时间/温度", y_label="相对密度")


def draw_ionic_conductivity_arrhenius(spec: dict[str, Any], output: Path) -> None:
    _draw_profile_curve(spec, output, title="离子电导 Arrhenius 曲线", x_label="1000/T", y_label="log(σT)")


def draw_dielectric_temperature_curve(spec: dict[str, Any], output: Path) -> None:
    _draw_profile_curve(spec, output, title="介电常数-温度曲线", x_label="T / °C", y_label="εr")


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
    _draw_simple_schematic(spec, output, title="晶体缺陷结构示意图", labels=_semantic_labels(spec.get("defects")))


def draw_dislocation_schematic(spec: dict[str, Any], output: Path) -> None:
    dislocation_type = str(spec.get("dislocation_type") or "").strip()
    if not dislocation_type:
        raise ValueError("dislocation_schematic: dislocation_type is required")
    burgers_vector = str(spec.get("burgers_vector") or "b").strip()
    custom = {**spec, "kind": "custom_diagram", "elements": [
        {"type": "line", "start": [0, 0.3], "end": [1, 0.3], "label": "滑移面"},
        {"type": "line", "start": [0.5, 0.0], "end": [0.5, 0.8], "label": "半原子面", "color": "#2563eb"},
        {"type": "arrow", "start": [0.35, 0.18], "end": [0.7, 0.18], "label": burgers_vector},
        {"type": "text", "xy": [0.38, 0.86], "text": dislocation_type},
    ]}
    draw_custom_diagram(custom, output)


def draw_slip_system_schematic(spec: dict[str, Any], output: Path) -> None:
    plane = spec.get("plane")
    direction = spec.get("direction")
    if plane in (None, "") or direction in (None, ""):
        raise ValueError("slip_system_schematic: plane and direction are required")
    draw_crystal_plane_direction(
        {
            **spec,
            "cell": spec.get("cell") or "schematic",
            "planes": [plane],
            "directions": [direction],
            "caption": spec.get("caption") or "滑移系示意图",
        },
        output,
    )


def draw_recrystallization_grain_growth(spec: dict[str, Any], output: Path) -> None:
    _draw_simple_schematic(spec, output, title="回复-再结晶-晶粒长大示意图", labels=_semantic_labels(spec.get("stages")))


def draw_polymer_configuration_conformation(spec: dict[str, Any], output: Path) -> None:
    _draw_simple_schematic(
        spec,
        output,
        title="高分子构型/构象示意图",
        labels=_semantic_labels([spec.get("configuration"), spec.get("side_groups")]),
    )


def draw_polymer_crystalline_morphology(spec: dict[str, Any], output: Path) -> None:
    _draw_simple_schematic(spec, output, title="高分子晶态形貌示意图", labels=_semantic_labels(spec.get("crystalline_regions")))


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
    _draw_simple_schematic(
        spec,
        output,
        title="硅酸盐结构示意图",
        labels=_semantic_labels([spec.get("structure_type"), spec.get("tetrahedra")]),
    )


def draw_glass_network_structure(spec: dict[str, Any], output: Path) -> None:
    _draw_simple_schematic(
        spec,
        output,
        title="玻璃网络结构示意图",
        labels=_semantic_labels([spec.get("network_formers"), spec.get("modifiers")]),
    )


def draw_ceramic_phase_diagram(spec: dict[str, Any], output: Path) -> None:
    if str(spec.get("diagram_type") or "").lower() == "ternary":
        draw_ternary_phase_diagram(spec, output)
    else:
        draw_binary_phase_diagram({**spec, "caption": spec.get("caption") or "陶瓷相图示意图"}, output)


def draw_porous_ceramic_microstructure(spec: dict[str, Any], output: Path) -> None:
    _draw_simple_schematic(spec, output, title="多孔陶瓷组织示意图", labels=_semantic_labels(spec.get("pore_labels")))


def draw_defect_chemistry_diagram(spec: dict[str, Any], output: Path) -> None:
    _draw_simple_schematic(spec, output, title="陶瓷缺陷化学示意图", labels=_semantic_labels(spec.get("defects")))


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
    return answer_figure_required(question)


def _planned_render_strategy(question: dict[str, Any]) -> str:
    raw_plan = question.get("figure_schema_plan")
    plan: dict[str, Any] = raw_plan if isinstance(raw_plan, dict) else {}
    raw_decision = plan.get("render_decision")
    decision: dict[str, Any] = raw_decision if isinstance(raw_decision, dict) else {}
    return str(decision.get("strategy") or "").strip()


def _planned_fallback_allowed(question: dict[str, Any]) -> bool:
    raw_plan = question.get("figure_schema_plan")
    plan: dict[str, Any] = raw_plan if isinstance(raw_plan, dict) else {}
    raw_decision = plan.get("render_decision")
    decision: dict[str, Any] = raw_decision if isinstance(raw_decision, dict) else {}
    return bool(decision.get("fallback_allowed", True))


def _image_model_fallback_allowed_for_question(question: dict[str, Any]) -> bool:
    """Allow raster fallback only when no deterministic renderer contract exists."""

    strategy = _planned_render_strategy(question)
    return strategy not in {"unavailable", "programmatic_renderer"} and _planned_fallback_allowed(question)


def _planned_schema_kind(question: dict[str, Any]) -> str:
    raw_plan = question.get("figure_schema_plan")
    plan: dict[str, Any] = raw_plan if isinstance(raw_plan, dict) else {}
    raw_resolution = plan.get("schema_resolution")
    resolution: dict[str, Any] = raw_resolution if isinstance(raw_resolution, dict) else {}
    raw_decision = plan.get("render_decision")
    decision: dict[str, Any] = raw_decision if isinstance(raw_decision, dict) else {}
    return str(decision.get("schema_kind") or resolution.get("kind") or resolution.get("proposed_kind") or "").strip()


def _planned_figure_metadata(question: dict[str, Any]) -> dict[str, Any]:
    raw_plan = question.get("figure_schema_plan")
    plan: dict[str, Any] = raw_plan if isinstance(raw_plan, dict) else {}
    raw_contract = plan.get("figure_semantic_contract")
    contract: dict[str, Any] = raw_contract if isinstance(raw_contract, dict) else {}
    raw_decision = plan.get("render_decision")
    decision: dict[str, Any] = raw_decision if isinstance(raw_decision, dict) else {}
    raw_resolution = plan.get("schema_resolution")
    resolution: dict[str, Any] = raw_resolution if isinstance(raw_resolution, dict) else {}
    planned_units: list[dict[str, str]] = []
    for unit in plan.get("figure_units", []) or []:
        if not isinstance(unit, dict):
            continue
        unit_resolution = unit.get("schema_resolution") if isinstance(unit.get("schema_resolution"), dict) else {}
        unit_decision = unit.get("render_decision") if isinstance(unit.get("render_decision"), dict) else {}
        unit_kind = str(unit_decision.get("schema_kind") or unit_resolution.get("kind") or "").strip()
        unit_number = str(unit.get("answer_unit_number") or "").strip()
        if unit_kind and unit_number:
            planned_units.append({"answer_unit_number": unit_number, "schema_kind": unit_kind})
    return {
        "semantic_contract_id": str(contract.get("contract_id") or decision.get("semantic_contract_id") or "").strip(),
        "planned_render_strategy": str(decision.get("strategy") or "").strip(),
        "planned_schema_kind": str(
            decision.get("schema_kind") or resolution.get("kind") or resolution.get("proposed_kind") or ""
        ).strip(),
        "figure_semantic_contract": contract,
        "figure_render_decision": decision,
        "planned_figure_units": planned_units,
    }


def _bind_composite_plan_coverage(question: dict[str, Any], spec: dict[str, Any]) -> None:
    """Record when one deterministic figure completely realizes a composite plan.

    A multi-unit XRD answer is often clearer as one plot containing several
    labelled states.  It is compatible only when every planned unit has the
    same XRD schema and the rendered spec contains at least one distinct state
    for every unit.
    """

    raw_plan = question.get("figure_schema_plan")
    plan = raw_plan if isinstance(raw_plan, dict) else {}
    units: list[dict[str, str]] = []
    for unit in plan.get("figure_units", []) or []:
        if not isinstance(unit, dict):
            continue
        resolution = unit.get("schema_resolution") if isinstance(unit.get("schema_resolution"), dict) else {}
        decision = unit.get("render_decision") if isinstance(unit.get("render_decision"), dict) else {}
        number = str(unit.get("answer_unit_number") or "").strip()
        kind = str(decision.get("schema_kind") or resolution.get("kind") or "").strip()
        if number and kind:
            units.append({"answer_unit_number": number, "schema_kind": kind})
    if len(units) < 2:
        return

    actual_kind = str(spec.get("kind") or "").strip()
    coverage = {
        "status": "incomplete",
        "actual_schema_kind": actual_kind,
        "planned_unit_count": len(units),
        "covered_unit_count": 0,
        "planned_units": units,
        "evidence": "",
    }
    spec["covered_answer_unit_numbers"] = []
    spec["composite_plan_coverage"] = coverage
    if actual_kind != "xrd_pattern" or any(unit["schema_kind"] != actual_kind for unit in units):
        coverage["evidence"] = "composite child schemas are not all represented by the actual schema"
        return

    peaks = spec.get("peaks") if isinstance(spec.get("peaks"), list) else []
    state_labels = list(
        dict.fromkeys(
            str(peak.get("pattern_label") or peak.get("pattern") or "").strip()
            for peak in peaks
            if isinstance(peak, dict) and str(peak.get("pattern_label") or peak.get("pattern") or "").strip()
        )
    )
    if len(state_labels) < len(units):
        coverage["evidence"] = f"xrd state count {len(state_labels)} is below planned unit count {len(units)}"
        return

    covered = [unit["answer_unit_number"] for unit in units]
    spec["covered_answer_unit_numbers"] = covered
    coverage.update(
        {
            "status": "complete",
            "covered_unit_count": len(covered),
            "evidence": "distinct_xrd_pattern_labels",
            "state_labels": state_labels,
        }
    )


def _composite_plan_coverage_complete(spec: dict[str, Any]) -> bool:
    coverage = spec.get("composite_plan_coverage")
    if not isinstance(coverage, dict) or coverage.get("status") != "complete":
        return False
    return int(coverage.get("covered_unit_count") or 0) == int(coverage.get("planned_unit_count") or -1)


def _question_context_for_figure_spec(question: dict[str, Any], spec: dict[str, Any]) -> dict[str, Any]:
    """Bind a figure to the semantic plan of its originating answer unit."""

    unit_number = str(spec.get("answer_unit_number") or "").strip()
    raw_plan = question.get("figure_schema_plan")
    plan = raw_plan if isinstance(raw_plan, dict) else {}
    if not unit_number:
        return question
    for unit_plan in plan.get("figure_units", []) or []:
        if not isinstance(unit_plan, dict):
            continue
        if str(unit_plan.get("answer_unit_number") or "").strip() != unit_number:
            continue
        scoped = dict(question)
        scoped["figure_schema_plan"] = unit_plan
        scoped["stem"] = str(unit_plan.get("answer_unit_stem") or scoped.get("stem") or "")
        return scoped
    return question


def _semantic_route_issues(spec: dict[str, Any], *, generation_method: str) -> list[str]:
    raw_contract = spec.get("figure_semantic_contract")
    raw_decision = spec.get("figure_render_decision")
    planned = bool(
        spec.get("semantic_contract_id")
        or spec.get("planned_render_strategy")
        or raw_contract
        or raw_decision
    )
    if not planned:
        return []
    if not isinstance(raw_contract, dict) or not isinstance(raw_decision, dict):
        return ["semantic_contract_not_bound_to_figure_spec"]
    contract = semantic_contract_from_mapping(raw_contract)
    metadata_contract_id = str(spec.get("semantic_contract_id") or "").strip()
    issues = (
        ["semantic_contract_metadata_mismatch"]
        if metadata_contract_id and metadata_contract_id != contract.contract_id
        else []
    )
    try:
        strategy = RenderStrategy(str(raw_decision.get("strategy") or ""))
    except ValueError:
        return [*issues, "invalid_planned_render_strategy"]
    decision = FigureRenderDecision(
        strategy=strategy,
        reason=str(raw_decision.get("reason") or ""),
        semantic_contract_id=str(raw_decision.get("semantic_contract_id") or ""),
        schema_kind=str(raw_decision.get("schema_kind") or ""),
        renderer=str(raw_decision.get("renderer") or ""),
        fallback_allowed=bool(raw_decision.get("fallback_allowed", True)),
    )
    route_issues = [
        *issues,
        *audit_figure_render_outcome(
            contract,
            decision,
            actual_kind=str(spec.get("kind") or ""),
            generation_method=generation_method,
        ),
    ]
    if _composite_plan_coverage_complete(spec):
        route_issues = [issue for issue in route_issues if issue != "actual_schema_kind_differs_from_plan"]
    return route_issues


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
    understanding = question.get("question_understanding") if isinstance(question.get("question_understanding"), dict) else {}
    understanding_text = json.dumps(
        {
            "question_requirements": understanding.get("question_requirements") or [],
            "images": understanding.get("images") or [],
            "tables": understanding.get("tables") or [],
            "uncertainties": understanding.get("uncertainties") or [],
        },
        ensure_ascii=False,
    )[:3500]
    schema_plan_text = json.dumps(question.get("figure_schema_plan") or {}, ensure_ascii=False)[:2500]
    return "\n".join(
        [
            "请直接生成一张可插入真题解析册的学术作图图片。",
            "要求：白底，清晰黑白线稿为主，必要时使用少量低饱和颜色；中文、符号、箭头和标签必须清楚可读；不要生成照片风格、装饰背景、水印、Logo 或无关文字。",
            "图必须严格服务题目要求。若题目要求曲线、组织示意、相图、晶胞、衍射花样、弯曲液面、电池结构等，请把关键对象、方向、坐标轴、标签和图注画完整。",
            "如果有多个小图，请使用整齐的多面板布局，每个小图要有清楚标签。",
            f"题目：{stem}",
            f"答案要点：{answer}" if answer else "",
            f"解析上下文：{analysis}" if analysis else "",
            f"题面视觉理解：{understanding_text}" if understanding else "",
            f"图件语义合同与 Schema：{schema_plan_text}" if schema_plan_text else "",
            f"已有结构化作图要求：{spec_text}" if spec_text else "",
            "必须满足图件语义合同中的 required_elements、required_labels 和 relationship_constraints；不得添加 forbidden_assumptions 中禁止的内容。",
            "最终图片中不要出现 figure_specs、占位符、JSON、代码、内部字段或题目无关说明。",
        ]
    ).strip()


def _direct_model_figure_id(qid: str) -> str:
    return f"{qid}_model_fig_01"


def _bind_source_image_overlay_spec(spec: dict[str, Any], question: dict[str, Any]) -> dict[str, Any]:
    bound = dict(spec)
    refs = [Path(str(raw)) for raw in question.get("image_refs") or []]
    try:
        source_index = int(bound.get("source_image_index") or 1)
    except (TypeError, ValueError):
        source_index = 0
    bound.pop("source_image", None)
    bound.pop("source_image_sha256", None)
    if len(refs) > 1 and "source_image_index" not in bound:
        bound["overlay_binding_issue"] = "multiple source images require an explicit source_image_index"
        return bound
    if source_index < 1 or source_index > len(refs):
        bound["overlay_binding_issue"] = "source_image_index does not identify an attached source image"
        return bound
    source = refs[source_index - 1]
    if not source.exists() or not source.is_file():
        bound["overlay_binding_issue"] = "selected source image is missing"
        return bound
    bound["source_image_index"] = source_index
    bound["source_image"] = str(source)
    bound["source_image_sha256"] = hashlib.sha256(source.read_bytes()).hexdigest()
    bound.pop("overlay_binding_issue", None)
    return bound


def _explicit_figure_specs(
    fragment: dict[str, Any],
    qid: str,
    question: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
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
        spec.setdefault("source", "answer_unit" if spec.get("answer_unit_number") else "answer_draft")
        if question is not None and _planned_render_strategy(question) == "source_image_overlay":
            spec["kind"] = "source_image_overlay"
        if spec.get("kind") == "source_image_overlay" and question is not None:
            spec = _bind_source_image_overlay_spec(spec, question)
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

    # Durable fragments from older runs may contain both the question-level
    # mirror and the answer-unit copy.  A semantic drawing contract represents
    # one required final figure, so collapse those copies at this execution
    # boundary as well as at draft normalization.
    # First collapse the legacy question-level mirror when its semantic body is
    # identical to one answer-unit copy. Two explicitly different units remain
    # independent even if they happen to request similar figures.
    mirror_deduplicated: list[dict[str, Any]] = []
    mirror_positions: dict[str, int] = {}

    def mirror_key(spec: dict[str, Any]) -> str:
        ignored = {
            "figure_id", "question_id", "source", "answer_unit_number",
            "schema_id", "renderer", "schema_status",
        }
        payload = {key: value for key, value in spec.items() if key not in ignored}
        return hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()

    for spec in specs:
        key = mirror_key(spec)
        previous_index = mirror_positions.get(key)
        if previous_index is not None:
            previous = mirror_deduplicated[previous_index]
            previous_unit = str(previous.get("answer_unit_number") or "").strip()
            current_unit = str(spec.get("answer_unit_number") or "").strip()
            if bool(previous_unit) != bool(current_unit):
                # Prefer the unit-bound copy because downstream ordering can
                # place it precisely.
                if current_unit:
                    mirror_deduplicated[previous_index] = spec
                continue
        mirror_positions.setdefault(key, len(mirror_deduplicated))
        mirror_deduplicated.append(spec)

    deduplicated: list[dict[str, Any]] = []
    positions: dict[tuple[str, str], int] = {}

    def semantic_key(spec: dict[str, Any]) -> tuple[str, str] | None:
        contract = str(spec.get("semantic_contract_id") or "").strip()
        if not contract and isinstance(spec.get("figure_semantic_contract"), dict):
            contract = str(spec["figure_semantic_contract"].get("contract_id") or "").strip()
        unit = str(spec.get("answer_unit_number") or "").strip()
        identity = contract or unit
        kind = str(spec.get("kind") or "").strip()
        return (identity, kind) if identity and kind else None

    def richness(spec: dict[str, Any]) -> tuple[int, int, int]:
        preferred_source = int(str(spec.get("source") or "").startswith("visual_qa_"))
        structural = sum(
            len(spec.get(key) or []) if isinstance(spec.get(key), list) else int(bool(spec.get(key)))
            for key in ("required_labels", "features", "annotations", "points", "regions")
        )
        return preferred_source, structural, len(json.dumps(spec, ensure_ascii=False, sort_keys=True))

    for spec in mirror_deduplicated:
        key = semantic_key(spec)
        if key is not None and key in positions:
            index = positions[key]
            if richness(spec) > richness(deduplicated[index]):
                deduplicated[index] = spec
            continue
        if key is not None:
            positions[key] = len(deduplicated)
        deduplicated.append(spec)
    return deduplicated


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
    """Ask the selected capability pack for a machine-verifiable proposal."""

    raw_plan = question.get("figure_schema_plan")
    plan = raw_plan if isinstance(raw_plan, dict) else {}
    planned_kinds = {_planned_schema_kind(question)}
    planned_kinds.update(
        str((unit.get("schema_resolution") or {}).get("kind") or (unit.get("render_decision") or {}).get("schema_kind") or "").strip()
        for unit in plan.get("figure_units", []) or []
        if isinstance(unit, dict)
    )
    text = "\n".join(str(question.get(key) or "") for key in ("stem", "section", "section_raw"))
    contributions = capability_policy_contributions(
        "deterministic_figure_spec",
        {"question": question, "planned_kinds": sorted(planned_kinds)},
        text=text,
    )
    for proposal in contributions:
        if isinstance(proposal, dict) and proposal.get("kind"):
            return dict(proposal)
    return None


def _hydrate_explicit_figure_spec(question: dict[str, Any], spec: dict[str, Any]) -> dict[str, Any]:
    """Fill only missing fields that a capability can derive from explicit text.

    The schema plan and model-authored figure remain authoritative. Capability
    packs may complete a required field such as ``structure=fcc`` only when the
    question states it explicitly; existing model values are never overwritten.
    """

    planned_kinds = {_planned_schema_kind(question), str(spec.get("kind") or "").strip()}
    text = "\n".join(str(question.get(key) or "") for key in ("stem", "section", "section_raw"))
    contributions = capability_policy_contributions(
        "deterministic_figure_spec",
        {
            "question": question,
            "planned_kinds": sorted(kind for kind in planned_kinds if kind),
            "purpose": "hydrate_explicit_spec",
            "candidate_spec": spec,
        },
        text=text,
    )
    hydrated = dict(spec)
    for proposal in contributions:
        if not isinstance(proposal, dict) or str(proposal.get("kind") or "") != str(spec.get("kind") or ""):
            continue
        filled_fields: list[str] = []
        for key, value in proposal.items():
            if key in {
                "question_id",
                "figure_id",
                "kind",
                "caption",
                "source",
                "capability_id",
                "generation_basis",
            }:
                continue
            if hydrated.get(key) in (None, "", [], {}):
                hydrated[key] = value
                filled_fields.append(key)
        if filled_fields:
            hydrated["deterministic_hydration"] = {
                "capability_id": str(proposal.get("capability_id") or ""),
                "generation_basis": str(proposal.get("generation_basis") or ""),
                "filled_fields": sorted(filled_fields),
            }
        break
    return hydrated


def _insert_figure_block(fragment: dict[str, Any], spec: dict[str, Any]) -> None:
    figure_id = str(spec.get("figure_id", ""))
    rel_path = f"figures/{figure_id}.png"
    answer_unit_number = str(spec.get("answer_unit_number") or "").strip()
    new_segments = [
        {
            "type": "image_ref",
            "image_id": figure_id,
            "path": rel_path,
            "role": "answer_generated_figure",
            **({"answer_unit_number": answer_unit_number} if answer_unit_number else {}),
        },
        {
            "type": "text",
            "text": str(spec.get("caption") or "题目图示"),
            "figure_caption_for": figure_id,
            **({"answer_unit_number": answer_unit_number} if answer_unit_number else {}),
        },
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

    try:
        previous_specs_data = json.loads(specs_json.read_text(encoding="utf-8")) if specs_json.exists() else {}
    except (OSError, json.JSONDecodeError):
        previous_specs_data = {}
    previous_specs = [
        item
        for item in previous_specs_data.get("figures", [])
        if isinstance(item, dict)
    ]
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
        "reused": [],
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
    initial_code_targets = [
        (str(question.get("question_id") or "").strip(), question, fragments_by_id[str(question.get("question_id") or "").strip()])
        for question in structured_exam.get("items", [])
        if isinstance(question, dict)
        and str(question.get("question_id") or "").strip() in fragments_by_id
        and _figure_needed(question)
        and question_drawing_mode(question) == "code"
        and _planned_render_strategy(question) != "unavailable"
        and code_client is not None
    ]

    def request_drawing_code(target: tuple[str, dict[str, Any], dict[str, Any]]) -> tuple[str, dict[str, Any] | None, str]:
        qid, question, fragment = target
        try:
            drawing_client = OpenAICompatibleClient(code_provider)
            with model_request_slot(code_provider):
                spec = generate_drawing_code_spec(
                    drawing_client,
                    question,
                    fragment,
                    model=str(code_report["model"]),
                    previous_issues=[],
                )
            return qid, spec, ""
        except Exception as exc:
            return qid, None, str(exc)

    initial_code_results = run_limited_concurrent(
        initial_code_targets,
        request_drawing_code,
        max_workers=figure_model_worker_count(),
    )
    initial_code_by_qid = {qid: (spec, error) for qid, spec, error in initial_code_results}
    needed_question_ids: set[str] = set()
    for question in structured_exam.get("items", []):
        qid = str(question.get("question_id", "")).strip()
        fragment = fragments_by_id.get(qid)
        if not fragment:
            continue
        needs_figure = _figure_needed(question)
        planned_strategy = _planned_render_strategy(question)
        mode = question_drawing_mode(question)
        question_specs: list[dict[str, Any]] = []
        if needs_figure and planned_strategy == "unavailable":
            code_report["skipped"].append(
                {
                    "question_id": qid,
                    "reason": "semantic contract forbids replacement and no compatible renderer is available",
                }
            )
            needed_question_ids.add(qid)
            continue
        if needs_figure and mode == "code":
            if code_client is not None:
                try:
                    report("drawing_code_request_started", question_id=qid, model=code_report["model"], phase="initial")
                    code_spec, code_error = initial_code_by_qid.get(qid, (None, "未获得独立作图代码结果"))
                    if code_spec is None:
                        raise RuntimeError(code_error)
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
            # A deterministic, machine-verifiable professional-diagram
            # contract is authoritative when available. Model-authored specs
            # remain the fallback for open-ended diagrams.
            inferred_spec = _figure_spec_for_question(question) if mode == "figure_specs" else None
            if inferred_spec:
                question_specs = [inferred_spec]
            else:
                question_specs = _explicit_figure_specs(fragment, qid, question)
                question_specs = [
                    _hydrate_explicit_figure_spec(_question_context_for_figure_spec(question, spec), spec)
                    for spec in question_specs
                ]
                filtered_specs: list[dict[str, Any]] = []
                for spec in question_specs:
                    planned_kind = _planned_schema_kind(_question_context_for_figure_spec(question, spec))
                    if planned_kind in {"xrd_pattern", "zone_axis_diffraction"} and str(spec.get("kind") or "") != planned_kind:
                        continue
                    filtered_specs.append(spec)
                question_specs = filtered_specs
        if needs_figure:
            needed_question_ids.add(qid)
        for spec in question_specs:
            if not spec:
                continue
            spec = spec if str(spec.get("kind") or "") == "model_drawing_code" else normalize_figure_spec(spec)
            figure_question = _question_context_for_figure_spec(question, spec)
            spec["drawing_generation_mode"] = mode
            spec.update({key: value for key, value in _planned_figure_metadata(figure_question).items() if value})
            _bind_composite_plan_coverage(figure_question, spec)
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
    retry_code_targets: list[tuple[str, dict[str, Any], dict[str, Any], list[str]]] = []
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

        retry_code_targets.append((qid, question, fragment, previous_issues[:12]))

    def request_retry_drawing_code(target: tuple[str, dict[str, Any], dict[str, Any], list[str]]) -> tuple[str, dict[str, Any] | None, str]:
        qid, question, fragment, previous_issues = target
        try:
            drawing_client = OpenAICompatibleClient(code_provider)
            with model_request_slot(code_provider):
                retry_spec = generate_drawing_code_spec(
                    drawing_client,
                    question,
                    fragment,
                    model=str(code_report["model"]),
                    previous_issues=previous_issues,
                )
            retry_spec.update({key: value for key, value in _planned_figure_metadata(question).items() if value})
            _bind_composite_plan_coverage(question, retry_spec)
            return qid, retry_spec, ""
        except Exception as exc:
            return qid, None, str(exc)

    for qid, _question, _fragment, _issues in retry_code_targets:
        report("drawing_code_request_started", question_id=qid, model=code_report["model"], phase="retry")
    retry_code_results = run_limited_concurrent(retry_code_targets, request_retry_drawing_code, max_workers=figure_model_worker_count())
    retry_code_specs: list[dict[str, Any]] = []
    for qid, retry_spec, error in retry_code_results:
        if retry_spec is not None:
            figure_id = re.sub(r"[^0-9A-Za-z_.-]+", "_", str(retry_spec.get("figure_id") or "").strip()).strip("._")
            retry_spec["figure_id"] = figure_id or f"{qid}_code_fig_01"
            retry_spec["source"] = "model_retry"
            retry_spec["drawing_generation_mode"] = "code"
            retry_code_specs.append(retry_spec)
            fragments_by_figure_id[str(retry_spec.get("figure_id", ""))] = fragments_by_id[qid]
            code_report["generated"].append(
                {
                    "question_id": qid,
                    "figure_id": retry_spec.get("figure_id"),
                    "model": code_report["model"],
                    "reason": "initial drawing code missing or failed",
                }
            )
            report("drawing_code_request_succeeded", question_id=qid, figure_id=retry_spec.get("figure_id"), model=code_report["model"], phase="retry")
            continue
        code_report["failed"].append({"question_id": qid, "error": error[:700]})
        report("drawing_code_request_failed", question_id=qid, model=code_report["model"], phase="retry", error=error[:300])
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
    direct_targets = [
        (qid, questions_by_id[qid], fragments_by_id[qid])
        for qid in sorted(needed_question_ids - covered_qids)
        if questions_by_id.get(qid) is not None and fragments_by_id.get(qid) is not None
        # A registered deterministic schema is a quality contract. If its
        # spec fails validation or rendering, preserve that failure for the
        # hard gate instead of silently replacing it with an image model.
        and _image_model_fallback_allowed_for_question(questions_by_id[qid])
    ]
    previous_fallbacks = {
        str(spec.get("question_id") or "").strip(): spec
        for spec in previous_specs
        if str(spec.get("kind") or "").strip() == "model_generated_image"
    }
    reusable_fallback_specs: list[dict[str, Any]] = []
    pending_direct_targets: list[tuple[str, dict[str, Any], dict[str, Any]]] = []
    for qid, question, fragment in direct_targets:
        previous = previous_fallbacks.get(qid)
        figure_id = _direct_model_figure_id(qid)
        output = output_dir / f"{figure_id}.png"
        current_prompt = _direct_figure_prompt(question, fragment, _explicit_figure_specs(fragment, qid, question))
        reusable = bool(
            previous
            and str(previous.get("figure_id") or "") == figure_id
            and str(previous.get("prompt") or "") == current_prompt
            and str(previous.get("provider") or "") == str(getattr(provider, "name", "") or "")
            and str(previous.get("model") or "") == str(getattr(provider, "image_model", "") or "")
            and str(previous.get("image_size") or "") == str(getattr(provider, "image_size", "") or "")
            and not audit_figure_image_integrity(output)
        )
        if not reusable:
            pending_direct_targets.append((qid, question, fragment))
            continue
        reused_spec = dict(previous)
        reused_spec["path"] = str(output)
        reused_spec.update(_planned_figure_metadata(question))
        _bind_composite_plan_coverage(question, reused_spec)
        reusable_fallback_specs.append(reused_spec)
        fragments_by_figure_id[figure_id] = fragment
        generated.append(output)
        generated_ids.add(figure_id)
        direct_report["reused"].append(
            {"question_id": qid, "figure_id": figure_id, "model": reused_spec.get("model", ""), "path": str(output)}
        )
        report("image_fallback_reused", question_id=qid, figure_id=figure_id, model=reused_spec.get("model", ""))
    direct_targets = pending_direct_targets
    if direct_client is None:
        direct_report["skipped"].extend(
            {
                "question_id": qid,
                "reason": "program/code figure generation could not render and image model is not configured or provider API key is missing",
            }
            for qid, _question, _fragment in direct_targets
        )

    def generate_image_fallback(target: tuple[str, dict[str, Any], dict[str, Any]]) -> tuple[str, dict[str, Any] | None, str]:
        qid, question, fragment = target
        figure_id = _direct_model_figure_id(qid)
        output = output_dir / f"{figure_id}.png"
        prompt = _direct_figure_prompt(question, fragment, _explicit_figure_specs(fragment, qid, question))
        try:
            image_client = OpenAICompatibleClient(provider)
            with model_request_slot(provider):
                image_result = image_client.generate_image(
                    prompt,
                    output,
                    model=getattr(provider, "image_model", ""),
                    size=getattr(provider, "image_size", "1024x1024"),
                )
            output = image_result.path
            if not output.exists() or output.stat().st_size <= 0:
                raise RuntimeError("image provider returned success but no image file was written")
            return qid, {
                "figure_id": figure_id,
                "question_id": qid,
                "kind": "model_generated_image",
                "caption": "题目图示",
                "prompt": prompt,
                "provider": image_result.provider,
                "model": image_result.model,
                "image_size": str(getattr(provider, "image_size", "") or ""),
                "path": str(output),
                **_planned_figure_metadata(question),
            }, ""
        except Exception as exc:
            return qid, None, str(exc)

    if direct_client is not None:
        for qid, _question, _fragment in direct_targets:
            report("image_fallback_started", question_id=qid, figure_id=_direct_model_figure_id(qid), model=getattr(provider, "image_model", ""))
        direct_results = run_limited_concurrent(direct_targets, generate_image_fallback, max_workers=figure_model_worker_count())
    else:
        direct_results = []
    direct_generated_specs: list[dict[str, Any]] = []
    for qid, spec, error in direct_results:
        figure_id = _direct_model_figure_id(qid)
        if spec is None:
            direct_report["failed"].append({"question_id": qid, "figure_id": figure_id, "error": error[:500], "fallback": "none"})
            report("image_fallback_failed", question_id=qid, figure_id=figure_id, error=error[:300])
            continue
        output = Path(str(spec["path"]))
        direct_generated_specs.append(spec)
        fragments_by_figure_id[figure_id] = fragments_by_id[qid]
        generated.append(output)
        generated_ids.add(figure_id)
        direct_report["generated"].append({"question_id": qid, "figure_id": figure_id, "model": spec["model"], "path": str(output)})
        report("image_fallback_succeeded", question_id=qid, figure_id=figure_id, model=spec["model"])
    accepted_fallback_specs = [*reusable_fallback_specs, *direct_generated_specs]
    if accepted_fallback_specs:
        specs.extend(accepted_fallback_specs)
    if direct_generated_specs:
        _archive_generated_stage_images(
            specs_json.parent,
            [Path(str(spec.get("path") or "")) for spec in direct_generated_specs],
            specs,
            "image_model_fallback",
        )
    if accepted_fallback_specs:
        active_specs_data = {"figures": specs}
        if _prune_failed_primary_specs_with_generated_fallback(active_specs_data, output_dir):
            specs = [spec for spec in active_specs_data["figures"] if isinstance(spec, dict)]
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
        semantic_contract_id = str(spec.get("semantic_contract_id") or "")
        planned_render_strategy = str(spec.get("planned_render_strategy") or "")
        planned_schema_kind = str(spec.get("planned_schema_kind") or "")
        registry_entry = get_schema(kind)
        program_issues = program_check_figure_spec(spec)
        rendered = figure_id in generated_ids
        output_path = output_dir / f"{figure_id}.png"
        if rendered:
            image_issues = audit_figure_image_integrity(output_path)
            if image_issues:
                program_issues.extend(image_issues)
                generated_ids.discard(figure_id)
                generated = [path for path in generated if path.stem != figure_id]
                output_path.unlink(missing_ok=True)
                rendered = False
        if kind == "model_drawing_code":
            code_issues = validate_drawing_code(str(spec.get("code") or ""))
            run_issues = ((spec.get("run_result") or {}) if isinstance(spec.get("run_result"), dict) else {}).get("issues") or []
            program_issues = [*program_issues, *code_issues, *[str(issue) for issue in run_issues]]
            risk_notes = [] if rendered and not program_issues else list(program_issues or ["模型代码绘图未能生成有效图片。"])
            semantic_route_issues = _semantic_route_issues(
                spec,
                generation_method="model_code_renderer" if rendered else "none",
            )
            program_issues.extend(semantic_route_issues)
            risk_notes.extend(issue for issue in semantic_route_issues if issue not in risk_notes)
            audit_items.append(
                {
                    "question_id": qid,
                    "figure_id": figure_id,
                    "diagram_type": kind,
                    "schema_status": "model_drawing_code",
                    "schema_id": "",
                    "renderer": "model_code_drawer",
                    "generation_method": "model_code_renderer" if rendered else "none",
                    "semantic_contract_id": semantic_contract_id,
                    "planned_render_strategy": planned_render_strategy,
                    "planned_schema_kind": planned_schema_kind,
                    "needs_manual_review": bool(risk_notes),
                    "program_check_issues": program_issues,
                    "risk_notes": risk_notes,
                    "code_path": spec.get("code_path", ""),
                }
            )
            continue
        if kind == "model_generated_image":
            semantic_route_issues = _semantic_route_issues(spec, generation_method="image_model")
            program_issues.extend(semantic_route_issues)
            audit_items.append(
                {
                    "question_id": qid,
                    "figure_id": figure_id,
                    "diagram_type": kind,
                    "schema_status": "image_model_fallback",
                    "schema_id": "",
                    "renderer": "",
                    "generation_method": "image_model",
                    "semantic_contract_id": semantic_contract_id,
                    "planned_render_strategy": planned_render_strategy,
                    "planned_schema_kind": planned_schema_kind,
                    "needs_manual_review": True,
                    "program_check_issues": program_issues,
                    "risk_notes": [
                        "未命中可渲染 schema 或程序绘图失败，已使用生图模型兜底，已标记为高风险并交由自动视觉质检处理。",
                        *semantic_route_issues,
                    ],
                }
            )
            continue
        risk_notes = list(program_issues)
        defaulted_fields = [
            str(field)
            for field in spec.get("schema_defaulted_fields", []) or []
            if str(field).strip()
        ]
        if defaulted_fields:
            risk_notes.append(
                "结构化图缺少 " + "、".join(defaulted_fields) + "，已使用安全默认值完成渲染；轴语义需结合题面复核。"
            )
        if not rendered:
            risk_notes.append("程序绘图未能生成有效图片，且生图模型未配置、失败或跳过；已作为自动质量阻断与 renderer 能力缺口记录。")
        schema_status = "schema_found" if registry_entry else str(spec.get("schema_status") or "legacy_programmatic")
        generation_method = (
            "source_image_overlay"
            if rendered and kind == "source_image_overlay"
            else ("programmatic_renderer" if rendered else "none")
        )
        if not rendered and not registry_entry:
            schema_status = "render_failed"
        semantic_route_issues = _semantic_route_issues(spec, generation_method=generation_method)
        program_issues.extend(semantic_route_issues)
        risk_notes.extend(issue for issue in semantic_route_issues if issue not in risk_notes)
        audit_items.append(
            {
                "question_id": qid,
                "figure_id": figure_id,
                "diagram_type": kind,
                "schema_status": schema_status,
                "schema_id": str(spec.get("schema_id") or (registry_entry or {}).get("schema_id") or ""),
                "renderer": str(spec.get("renderer") or (registry_entry or {}).get("renderer") or ""),
                "generation_method": generation_method,
                "semantic_contract_id": semantic_contract_id,
                "planned_render_strategy": planned_render_strategy,
                "planned_schema_kind": planned_schema_kind,
                "planned_figure_units": spec.get("planned_figure_units") or [],
                "covered_answer_unit_numbers": spec.get("covered_answer_unit_numbers") or [],
                "composite_plan_coverage": spec.get("composite_plan_coverage") or {},
                "needs_manual_review": bool(risk_notes),
                "program_check_issues": program_issues,
                "risk_notes": risk_notes,
            }
        )
    for qid in sorted(needed_question_ids - audited_qids):
        question = questions_by_id.get(qid) or {}
        planned_metadata = _planned_figure_metadata(question)
        planned_strategy = _planned_render_strategy(question)
        replacement_forbidden = not _planned_fallback_allowed(question)
        unavailable = planned_strategy == "unavailable" or replacement_forbidden
        audit_items.append(
            {
                "question_id": qid,
                "figure_id": _direct_model_figure_id(qid),
                "diagram_type": "",
                "schema_status": "semantic_contract_unrenderable" if unavailable else "image_model_fallback",
                "schema_id": "",
                "renderer": "",
                "generation_method": "none" if unavailable else "image_model",
                **planned_metadata,
                "needs_manual_review": True,
                "program_check_issues": [],
                "risk_notes": [
                    "题目要求保留原图并在其上标注，但未获得通过结构与原图哈希校验的标注规格；为避免错误替换原图，已禁止生图兜底。"
                    if unavailable
                    else "未获得可渲染程序作图输出，已记录为自动质量阻断与 renderer 能力缺口。"
                ],
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


def _model_code_renderer(spec: dict[str, Any], output: Path) -> None:
    code = str(spec.get("code") or "").strip()
    code_path = output.with_suffix(".py")
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
        raise RuntimeError("; ".join(result.issues or ["drawing code execution failed"]))


def _figure_renderer_registry() -> RendererRegistry:
    """Assemble capability renderers behind one dispatch contract.

    Renderer implementations stay in this module during the compatibility phase;
    capability packs declare implementation names and are wired automatically.
    Stored legacy kinds remain explicit until their old task data is retired.
    """

    implementations = {
        name: value
        for name, value in globals().items()
        if name.startswith("draw_") and callable(value)
    }
    return assemble_renderer_registry(
        registry_snapshot(),
        implementations,
        compatibility_renderers={
            "phase_diagram": draw_phase_diagram,
            "line_chart": draw_line_chart,
            "diffraction_pattern": draw_diffraction_pattern,
            "fcc_cell": draw_fcc_cell,
            "model_drawing_code": _model_code_renderer,
            "curved_liquid_surface": draw_curved_liquid_surface,
            "custom_diagram": draw_custom_diagram,
        },
    )


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
    renderers = _figure_renderer_registry()
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
        output.unlink(missing_ok=True)
        try:
            rendered = renderers.render(kind, spec, output)
        except (RuntimeError, ValueError) as exc:
            spec["validation_issues"] = [str(exc)]
            if callable(progress_callback):
                progress_callback("figure_render_failed", {"figure_id": figure_id, "question_id": spec.get("question_id", ""), "error": str(exc)})
            continue
        if not rendered:
            continue
        integrity_issues = audit_figure_image_integrity(output)
        if integrity_issues:
            output.unlink(missing_ok=True)
            spec["validation_issues"] = integrity_issues
            if callable(progress_callback):
                progress_callback(
                    "figure_render_failed",
                    {
                        "figure_id": figure_id,
                        "question_id": spec.get("question_id", ""),
                        "error": "; ".join(integrity_issues),
                    },
                )
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
        "question_understanding": question.get("question_understanding") if isinstance(question.get("question_understanding"), dict) else {},
        "figure_schema_plan": question.get("figure_schema_plan") if isinstance(question.get("figure_schema_plan"), dict) else {},
    }


def _compact_visual_qa(qa: dict[str, Any]) -> dict[str, Any]:
    return {
        "ok": qa.get("ok"),
        "summary": _short_text(qa.get("summary") or qa.get("error"), 700),
        "missing_requirements": _compact_list(qa.get("missing_requirements")),
        "label_issues": _compact_list(qa.get("label_issues")),
        "visual_issues": _compact_list(qa.get("visual_issues")),
    }


def _declared_figure_labels(spec: dict[str, Any]) -> list[str]:
    labels: list[str] = []
    for value in spec.get("required_labels") or []:
        if str(value or "").strip():
            labels.append(str(value).strip())
    for item in spec.get("features") or []:
        if isinstance(item, dict) and str(item.get("label") or "").strip():
            labels.append(str(item["label"]).strip())
    for item in spec.get("annotations") or []:
        if isinstance(item, dict):
            value = item.get("text") or item.get("label")
            if str(value or "").strip():
                labels.append(str(value).strip())
    for field in ("x_label", "y_label", "matrix_label"):
        if str(spec.get(field) or "").strip():
            labels.append(str(spec[field]).strip())
    return list(dict.fromkeys(labels))


def _ground_visual_qa_to_figure_spec(qa: dict[str, Any], spec: dict[str, Any]) -> dict[str, Any]:
    """Keep raster review inside its remit and retain deterministic text truth."""

    if not isinstance(qa, dict):
        return qa
    nested = qa.get("output_schema")
    if "ok" not in qa and isinstance(nested, dict) and "ok" in nested:
        qa = dict(nested)
    if qa.get("error"):
        return qa

    grounded = dict(qa)
    declared_labels = _declared_figure_labels(spec)
    source_text_is_exact = str(spec.get("kind") or "") not in {
        "direct_model_image",
        "model_drawing_code",
        "source_image_overlay",
    }
    suppressed: list[dict[str, str]] = []

    def mentions_declared_label(issue: str) -> bool:
        compact_issue = re.sub(r"\s+", "", issue).replace("₍", "(").replace("₎", ")")
        for label in declared_labels:
            compact_label = re.sub(r"\s+", "", label)
            variants = {
                compact_label,
                re.sub(r"[_₀₁₂₃₄₅₆₇₈₉]+", "", compact_label),
            }
            # A reviewer often quotes only the scientific token inside a
            # longer deterministic annotation (for example Fe₃C inside
            # "共晶转变 L→γ+Fe₃C").  Treat that token as a declared
            # label fragment when it includes an explicit numeric subscript.
            for token in re.findall(r"[A-Z][a-z]?[₀-₉0-9]+[A-Za-z₀-₉0-9]*", compact_label):
                variants.add(token)
                variants.add(token.translate(_UNICODE_SUBSCRIPT_DIGITS))
            if any(variant and variant in compact_issue for variant in variants):
                return True
        return False

    missing_values = qa.get("missing_requirements") if isinstance(qa.get("missing_requirements"), list) else []
    kept_missing: list[Any] = []
    for value in missing_values:
        issue = str(value or "")
        quoted = re.findall(r"['‘’\"“”]([^'‘’\"“”]{1,40})['‘’\"“”]", issue)
        quoted_is_declared = any(
            any(re.sub(r"\s+", "", quote) in re.sub(r"\s+", "", label) or re.sub(r"\s+", "", label) in re.sub(r"\s+", "", quote) for label in declared_labels)
            for quote in quoted
        )
        missing_scope_expansion = (
            any(token in issue for token in ("缺失", "未展示", "未标注", "不包括", "未包含"))
            and not mentions_declared_label(issue)
            and not any(
                token in issue
                for token in (
                    "坐标轴",
                    "单位",
                    "刻度",
                    "方向",
                    "箭头",
                    "图例",
                    "标题",
                    "边界",
                    "平台",
                    "转折",
                )
            )
        )
        if source_text_is_exact and mentions_declared_label(issue) and any(token in issue for token in ("标签", "文字", "错别字")):
            suppressed.append({"field": "missing_requirements", "issue": issue, "reason": "deterministic_label_source"})
        elif (quoted and not quoted_is_declared and any(token in issue for token in ("缺失", "未展示", "未清晰展示"))) or missing_scope_expansion:
            suppressed.append({"field": "missing_requirements", "issue": issue, "reason": "undeclared_scope_expansion"})
        else:
            kept_missing.append(value)
    grounded["missing_requirements"] = kept_missing

    label_values = qa.get("label_issues") if isinstance(qa.get("label_issues"), list) else []
    kept_labels: list[Any] = []
    for value in label_values:
        issue = str(value or "")
        if source_text_is_exact and mentions_declared_label(issue) and any(
            token in issue for token in ("错别字", "文字错误", "缺少 required label", "缺少required label", "标签缺失")
        ):
            suppressed.append({"field": "label_issues", "issue": issue, "reason": "deterministic_label_source"})
        else:
            kept_labels.append(value)
    grounded["label_issues"] = kept_labels
    visual_values = qa.get("visual_issues") if isinstance(qa.get("visual_issues"), list) else []
    kept_visual: list[Any] = []
    for value in visual_values:
        issue = str(value or "")
        missing_clause = re.search(
            r"(缺少|缺失|未包含|未体现|完全未体现|未完整|应为|还应增加|且缺)([^。；;]{1,80})",
            issue,
        )
        missing_clause_mentions_declared = bool(
            missing_clause and mentions_declared_label(missing_clause.group(2))
        )
        introduces_undeclared_subject = False
        if any(token in issue for token in ("缺少", "缺失", "未体现", "未完整", "应为", "还应", "且缺")):
            if missing_clause is None:
                introduces_undeclared_subject = not mentions_declared_label(issue)
            else:
                marker = missing_clause.group(1)
                prefix_mentions_declared = mentions_declared_label(issue[: missing_clause.start()])
                # In "declared label + 未体现/应为 + morphology", the omitted
                # clause describes a property of an already-declared object.  It is
                # therefore a real raster defect, not a request to invent a new
                # constituent.  Additive wording such as "缺少 X" remains scoped
                # to the clause itself so mixed issues cannot smuggle in X.
                property_marker = marker in {"未体现", "完全未体现", "未完整", "应为"}
                introduces_undeclared_subject = not (
                    missing_clause_mentions_declared
                    or (property_marker and prefix_mentions_declared)
                )
        deterministic_text_ocr = (
            source_text_is_exact
            and mentions_declared_label(issue)
            and any(token in issue for token in ("文字", "文本", "错别字", "缺字", "OCR", "下标", "上标", "化学式"))
            and not any(token in issue for token in ("重叠", "遮挡", "裁切", "超出", "过小"))
        )
        if introduces_undeclared_subject:
            suppressed.append({"field": "visual_issues", "issue": issue, "reason": "undeclared_scope_expansion"})
        elif deterministic_text_ocr:
            suppressed.append({"field": "visual_issues", "issue": issue, "reason": "deterministic_label_source"})
        else:
            kept_visual.append(value)
    grounded["visual_issues"] = kept_visual
    if suppressed:
        grounded["figure_spec_grounding"] = {
            "applied": True,
            "declared_labels": declared_labels,
            "suppressed_issues": suppressed,
        }
    if not grounded["missing_requirements"] and not grounded["label_issues"] and not grounded["visual_issues"]:
        grounded["ok"] = True
        if suppressed:
            grounded["summary"] = "未发现超出规格文本真值之外的可确认视觉问题。"
    return grounded


def _figure_candidate_scope_issues(
    current_spec: dict[str, Any],
    repaired_spec: dict[str, Any],
    *,
    grounded_labels: set[str] | None = None,
) -> list[str]:
    """Reject semantic scope expansion performed only by a raster reviewer.

    Visual repair may change geometry, morphology, spacing and rendering hints.
    Adding a new named peer object is different: it changes the answer's
    scientific/disciplinary meaning and must first be justified in the answer
    and evidence stages.  Keeping this check local and deterministic avoids an
    extra model round while preventing visually plausible semantic drift.
    """

    current_labels = set(_declared_figure_labels(current_spec))
    repaired_labels = set(_declared_figure_labels(repaired_spec))
    grounded = {str(label or "").strip() for label in (grounded_labels or set()) if str(label or "").strip()}
    grounded |= {re.sub(r"\s+", "", label) for label in grounded}
    added_labels = sorted(
        label
        for label in repaired_labels - current_labels
        if label and label not in grounded and re.sub(r"\s+", "", label) not in grounded
    )
    issues = [f"figure_candidate_scope_expansion: added undeclared label {label}" for label in added_labels]
    current_kind = str(current_spec.get("kind") or "").strip()
    repaired_kind = str(repaired_spec.get("kind") or "").strip()
    if current_kind and repaired_kind and current_kind != repaired_kind:
        issues.append(
            f"figure_candidate_scope_expansion: kind changed from {current_kind} to {repaired_kind}"
        )
    return issues


def _confirmed_figure_repair_labels(structured_exam: dict[str, Any], question_id: str) -> set[str]:
    """Return peer labels explicitly confirmed from the source question image."""

    question = next(
        (
            item
            for item in structured_exam.get("items", []) or []
            if isinstance(item, dict) and str(item.get("question_id") or "").strip() == question_id
        ),
        {},
    )
    understanding = question.get("question_understanding") if isinstance(question, dict) else {}
    labels: set[str] = set()
    for image in understanding.get("images", []) if isinstance(understanding, dict) else []:
        if not isinstance(image, dict):
            continue
        labels.update(str(value or "").strip() for value in image.get("detected_labels", []) or [])
        for path in image.get("fixed_condition_phase_paths", []) or []:
            if not isinstance(path, dict):
                continue
            for region in path.get("ordered_regions", []) or []:
                if isinstance(region, dict):
                    labels.add(str(region.get("phase_or_region") or "").strip())
            terminals = path.get("terminal_regions") if isinstance(path.get("terminal_regions"), dict) else {}
            for terminal in terminals.values():
                if isinstance(terminal, dict):
                    labels.add(str(terminal.get("phase_or_region") or "").strip())
    return {label for label in labels if label}


def _figure_policy_text(question: dict[str, Any], spec: dict[str, Any]) -> str:
    return " ".join(
        [
            str(question.get("stem") or ""),
            " ".join(
                str(item.get("stem") or "")
                for item in question.get("subquestions") or []
                if isinstance(item, dict)
            ),
            str(spec.get("caption") or ""),
            str(spec.get("notes") or ""),
        ]
    ).lower()


def _uses_crystallographic_index_whitelist(question: dict[str, Any], spec: dict[str, Any]) -> bool:
    text = _figure_policy_text(question, spec)
    return any(
        contribution.get("crystallographic_index_whitelist") is True
        for contribution in capability_policy_contributions(
            "visual_qa",
            {"question": question, "spec": spec, "text": text},
            text=text,
        )
        if isinstance(contribution, dict)
    )


def _apply_crystallographic_index_whitelist(
    qa: dict[str, Any],
    question: dict[str, Any],
    spec: dict[str, Any],
) -> dict[str, Any]:
    """Keep visual QA from treating raster hkl/direction OCR as a physics oracle."""
    if not isinstance(qa, dict):
        return qa
    text = _figure_policy_text(question, spec)
    return apply_capability_policy_transforms(
        "filter_visual_qa",
        qa,
        {
            "question": question,
            "spec": spec,
            "text": text,
            "deterministic_validation_passed": not program_check_figure_spec(spec),
        },
        text=text,
    )


def _drawing_code_repair_constraints(question: dict[str, Any], spec: dict[str, Any]) -> list[str]:
    text = _figure_policy_text(question, spec)
    constraints = [
        "把 visual_qa 当作问题线索，不要照抄其中可能不存在的元素；必须以题干、当前图像要求和代码自身为准。",
        "优先修正错误指数、缺失图形、排版重叠和可读性问题；不要添加题目没有要求的解释性装饰。",
        "不要使用过大的图内标题；如果需要标题，字号应小于主体标签，且不得挤压图形主体。",
        "所有标签必须与点、线、箭头、其他标签保持清晰间距；宁可减少非必要标签，也不要重叠。",
    ]
    for contribution in capability_policy_contributions(
        "drawing_repair",
        {"question": question, "spec": spec, "text": text},
        text=text,
    ):
        if isinstance(contribution, dict):
            constraints.extend(
                str(item) for item in contribution.get("constraints", []) if str(item).strip()
            )
    return list(dict.fromkeys(constraints))


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
            "可修改已声明对象的形态、空间关系、位置与排版；不得新增 current_spec 未声明的同级标签、组成或对象。",
            "若视觉审查暗示答案语义本身缺项，本轮不能由图像修复器自行扩展；应保留原语义边界，仅修形态和可读性。",
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
            "图中解释性文字使用中文；题目要求的标准专业术语和符号可保留原始写法。",
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
        # Provider/transport failures mean that no visual judgement exists.
        # Rewriting the figure cannot repair an unavailable reviewer and only
        # repeats the same failing service call.
        if str(qa.get("error") or "").strip():
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


def _figure_visual_repair_fingerprint(
    *,
    structured_exam: dict[str, Any],
    specs_data: dict[str, Any],
    qa_report: dict[str, Any],
    output_dir: Path,
    repair_provider: str,
    repair_model: str,
    vision_provider: str,
    vision_model: str,
    max_rounds: int,
    max_candidates_per_target: int,
) -> str:
    image_hashes: dict[str, str] = {}
    for spec in specs_data.get("figures", []) or []:
        if not isinstance(spec, dict):
            continue
        figure_id = str(spec.get("figure_id") or "").strip()
        image = output_dir / f"{figure_id}.png"
        if figure_id and image.is_file():
            image_hashes[figure_id] = hashlib.sha256(image.read_bytes()).hexdigest()
    payload = {
        "version": "answer_book.figure_visual_qa_repair.v3",
        "structured_exam": structured_exam,
        "specs": specs_data,
        "qa_report": qa_report,
        "image_hashes": image_hashes,
        "repair_provider": repair_provider,
        "repair_model": repair_model,
        "vision_provider": vision_provider,
        "vision_model": vision_model,
        "max_rounds": max(0, int(max_rounds)),
        "max_candidates_per_target": max(1, min(2, int(max_candidates_per_target))),
    }
    return hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()


def _reusable_figure_visual_repair_report(path: Path, fingerprint: str) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    try:
        existing = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return None
    if not isinstance(existing, dict) or existing.get("input_fingerprint") != fingerprint:
        return None
    # A provider/runtime error must be retried. Only a fully evaluated result is
    # reusable; otherwise a transient outage would suppress future repairs.
    candidates = [
        candidate
        for round_item in existing.get("rounds", []) or []
        if isinstance(round_item, dict)
        for target in round_item.get("targets", []) or []
        if isinstance(target, dict)
        for candidate in target.get("candidates", []) or []
        if isinstance(candidate, dict)
    ]
    if any(str(candidate.get("status") or "") == "error" for candidate in candidates):
        return None
    cached = dict(existing)
    cached["cache"] = {"hit": True, "content_addressed": True}
    cached["remote_model_calls_this_run"] = 0
    return cached


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
    active_ids = {
        str(spec.get("figure_id") or "").strip()
        for spec in specs
        if str(spec.get("figure_id") or "").strip()
        and (output_dir / f"{str(spec.get('figure_id') or '').strip()}.png").exists()
    }
    # Specs are the authoritative lifecycle registry. Remove answer-generated
    # images that were superseded, pruned, or failed to render, while always
    # preserving original question images.
    for fragment in fragments_by_id.values():
        for block in fragment.get("blocks", []) or []:
            if not isinstance(block, dict) or not isinstance(block.get("segments"), list):
                continue
            before_segments = block["segments"]
            kept_segments: list[dict[str, Any]] = []
            index = 0
            while index < len(before_segments):
                segment = before_segments[index]
                index += 1
                if not isinstance(segment, dict):
                    continue
                if segment.get("type") == "image_ref":
                    role = str(segment.get("role") or "").strip()
                    path = str(segment.get("path") or "").strip().replace("\\", "/")
                    image_id = str(segment.get("image_id") or Path(path).stem).strip()
                    generated_ref = role == "answer_generated_figure" or path.startswith("figures/")
                    if role != "source_question_image" and generated_ref and image_id not in active_ids:
                        changed = True
                        # Legacy fragments stored an untagged caption directly
                        # after each generated image. Remove that paired caption
                        # only inside the dedicated figure block.
                        if str(block.get("label") or "").strip() == "图示" and index < len(before_segments):
                            following = before_segments[index]
                            if (
                                isinstance(following, dict)
                                and following.get("type") == "text"
                                and not following.get("figure_caption_for")
                            ):
                                index += 1
                        continue
                caption_for = str(segment.get("figure_caption_for") or "").strip()
                if caption_for and caption_for not in active_ids:
                    changed = True
                    continue
                kept_segments.append(segment)
            if len(kept_segments) != len(before_segments):
                block["segments"] = kept_segments
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


def _prune_failed_primary_specs_with_generated_fallback(specs_data: dict[str, Any], output_dir: Path) -> bool:
    """Make a successful fallback authoritative over missing primary artifacts."""

    specs = [spec for spec in specs_data.get("figures", []) or [] if isinstance(spec, dict)]
    fallback_qids = {
        str(spec.get("question_id") or "").strip()
        for spec in specs
        if str(spec.get("kind") or "").strip() == "model_generated_image"
        and str(spec.get("question_id") or "").strip()
        and str(spec.get("figure_id") or "").strip()
        and (output_dir / f"{str(spec.get('figure_id') or '').strip()}.png").exists()
    }
    if not fallback_qids:
        return False
    pruned: list[dict[str, Any]] = []
    changed = False
    for spec in specs:
        qid = str(spec.get("question_id") or "").strip()
        kind = str(spec.get("kind") or "").strip()
        figure_id = str(spec.get("figure_id") or "").strip()
        missing_primary = kind != "model_generated_image" and (
            not figure_id or not (output_dir / f"{figure_id}.png").exists()
        )
        if qid in fallback_qids and missing_primary:
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
    max_candidates_per_target: int = 2,
    progress_callback: Any | None = None,
) -> dict[str, Any]:
    """Create and visually validate independent repair candidates before promotion.

    The answer model and the vision reviewer receive the same failed source spec. A
    candidate may replace the primary figure only after the vision reviewer accepts
    its rendered PNG. This prevents a failed repair from overwriting the last known
    figure merely because its code passed the static validator.
    """
    report: dict[str, Any] = {
        "schema_version": "answer_book.figure_visual_qa_repair.v3",
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
        "budget": {
            "max_rounds": max(0, int(max_rounds)),
            "max_candidates_per_target": max(1, min(2, int(max_candidates_per_target))),
        },
        "cache": {"hit": False, "content_addressed": True},
    }
    if not report["enabled"]:
        report["skipped_reason"] = "repair or vision provider is not configured"
        repair_report_json.parent.mkdir(parents=True, exist_ok=True)
        repair_report_json.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        return report
    if qa_report is None:
        qa_report = json.loads(visual_qa_json.read_text(encoding="utf-8")) if visual_qa_json.exists() else {}
    specs_data_for_fingerprint = (
        json.loads(specs_json.read_text(encoding="utf-8")) if specs_json.exists() else {"figures": []}
    )
    fingerprint = _figure_visual_repair_fingerprint(
        structured_exam=structured_exam,
        specs_data=specs_data_for_fingerprint,
        qa_report=qa_report,
        output_dir=output_dir,
        repair_provider=str(report["repair_model"]["provider"]),
        repair_model=str(report["repair_model"]["model"]),
        vision_provider=str(report["vision_model"]["provider"]),
        vision_model=str(report["vision_model"]["model"]),
        max_rounds=max_rounds,
        max_candidates_per_target=max_candidates_per_target,
    )
    report["input_fingerprint"] = fingerprint
    cached_report = _reusable_figure_visual_repair_report(repair_report_json, fingerprint)
    if cached_report is not None:
        return cached_report
    if max_rounds <= 0:
        report["skipped_reason"] = "figure repair budget is zero"
        repair_report_json.parent.mkdir(parents=True, exist_ok=True)
        repair_report_json.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        return report
    latest_qa = qa_report
    repair_model = str(report["repair_model"]["model"])
    reviewer_model = str(report["vision_model"]["model"])
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
        if kind == "source_image_overlay":
            repaired["kind"] = kind
            for key in (
                "source_image_index",
                "source_image",
                "source_image_sha256",
                "semantic_contract_id",
                "planned_render_strategy",
                "planned_schema_kind",
                "figure_semantic_contract",
                "figure_render_decision",
            ):
                if key in current_spec:
                    repaired[key] = current_spec[key]
        repaired.setdefault("caption", current_spec.get("caption") or "题目图示")
        repaired["source"] = f"visual_qa_{strategy}_candidate"
        if kind == "model_drawing_code":
            validation_issues = validate_drawing_code(str(repaired.get("code") or ""))
        else:
            repaired = normalize_figure_spec(repaired)
            validation_issues = [
                *program_check_figure_spec(repaired),
                *_figure_candidate_scope_issues(
                    current_spec,
                    repaired,
                    grounded_labels=_confirmed_figure_repair_labels(
                        structured_exam, str(current_spec.get("question_id") or "").strip()
                    ),
                ),
            ]
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

    for round_index in range(1, int(max_rounds) + 1):
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

        def repair_target(
            target: dict[str, Any],
            *,
            generation_by_figure: dict[str, dict[str, Any]] = generation_by_figure,
            round_index: int = round_index,
            specs_by_id: dict[str, int] = specs_by_id,
            specs: list[dict[str, Any]] = specs,
            round_report: dict[str, Any] = round_report,
        ) -> tuple[dict[str, Any], list[dict[str, Any]]]:
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
            raw_candidate_configs = [
                ("original_model", provider, repair_model),
                ("vision_reviewer", vision_provider, reviewer_model),
            ]
            # When one visual provider owns both image repair and re-audit, do
            # not send the same request twice under two strategy names. Prefer
            # the vision-aware strategy because it receives the source raster.
            candidate_by_route: dict[tuple[str, str], tuple[str, Any, str]] = {}
            for config in raw_candidate_configs:
                strategy, candidate_provider, candidate_model = config
                route = (str(getattr(candidate_provider, "name", "")), str(candidate_model or ""))
                if route not in candidate_by_route or strategy == "vision_reviewer":
                    candidate_by_route[route] = config
            candidate_configs = list(candidate_by_route.values())[: max(1, min(2, int(max_candidates_per_target)))]
            def repair_candidate(config: tuple[str, Any, str]) -> tuple[dict[str, Any], dict[str, Any] | None]:
                strategy, candidate_provider, candidate_model = config
                candidate_client = OpenAICompatibleClient(candidate_provider)
                candidate_report: dict[str, Any] = {"strategy": strategy, "provider": getattr(candidate_provider, "name", ""), "model": candidate_model}
                emit("visual_qa_repair_candidate_started", {"figure_id": figure_id, "question_id": target_report["question_id"], "strategy": strategy, "model": candidate_model})
                passing_candidate: dict[str, Any] | None = None
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
                            passing_candidate = {
                                "strategy": strategy,
                                "spec": repaired,
                                "qa": candidate_qa,
                                "image": candidate_image,
                                "target_report": target_report,
                            }
                except Exception as exc:
                    candidate_report["status"] = "error"
                    candidate_report["error"] = str(exc)[:700]
                return candidate_report, passing_candidate

            candidate_results = run_limited_concurrent(
                candidate_configs,
                repair_candidate,
                max_workers=min(len(candidate_configs), figure_model_worker_count()),
            )
            target_report["candidates"] = [item[0] for item in candidate_results]
            target_passing = [item[1] for item in candidate_results if isinstance(item[1], dict)]
            passing = target_passing
            promoted: list[dict[str, Any]] = []
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
                promoted = [selected]
            return target_report, promoted

        target_results = run_limited_concurrent(
            targets,
            repair_target,
            max_workers=min(len(targets), figure_model_worker_count()),
        )
        for target_report, target_passing in target_results:
            round_report["targets"].append(target_report)
            selected_candidates.extend(target_passing)
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
            pruned = _prune_failed_primary_specs_with_generated_fallback(refreshed_specs_data, output_dir) or pruned
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
    report["remote_model_calls_this_run"] = sum(
        1
        for round_item in report.get("rounds", [])
        if isinstance(round_item, dict)
        for target in round_item.get("targets", []) or []
        if isinstance(target, dict)
        for candidate in target.get("candidates", []) or []
        if isinstance(candidate, dict)
    )
    repair_report_json.parent.mkdir(parents=True, exist_ok=True)
    repair_report_json.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return report


def _audit_figures_with_vision_serial(
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
        question = _question_context_for_figure_spec(questions_by_id.get(qid, {}), spec)
        integrity_issues = audit_figure_image_integrity(path)
        if integrity_issues:
            qa = {
                "ok": False,
                "deterministic": True,
                "error": "; ".join(integrity_issues),
                "missing_requirements": ["generated figure is not a usable image"],
                "label_issues": [],
                "visual_issues": integrity_issues,
                "summary": "The generated figure failed local image-integrity checks.",
            }
            report["items"].append(
                {
                    "question_id": qid,
                    "figure_id": figure_id,
                    "path": str(path),
                    "vision_input": None,
                    "qa": qa,
                }
            )
            if callable(progress_callback):
                progress_callback(
                    "visual_qa_completed",
                    {"figure_id": figure_id, "question_id": qid, "ok": False, "error": qa["error"]},
                )
            continue
        kind = str(spec.get("kind") or "").strip()
        domain_rules: list[str] = drawing_domain_quality_rules(question, str(spec.get("caption") or spec.get("title") or ""))
        policy_text = _figure_policy_text(question, spec)
        visual_qa_policy_rules: list[str] = []
        for contribution in capability_policy_contributions(
            "visual_qa",
            {"question": question, "spec": spec, "text": policy_text},
            text=policy_text,
        ):
            if isinstance(contribution, dict):
                visual_qa_policy_rules.extend(
                    str(rule) for rule in contribution.get("hard_rules", []) if str(rule).strip()
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
                "Treat question.figure_schema_plan.figure_semantic_contract as the semantic source of truth: every required element, label, and relationship must be visibly satisfied, and forbidden assumptions must not be introduced.",
                "Audit only the current figure_spec and its caption. If the same question has multiple figure_specs, do not require this single figure to cover requirements assigned to other figures.",
                "Do not require extra phases, constituents, labels, or quantitative proportions that are absent from the current figure semantic contract and figure_spec; those belong to the content-correctness gate.",
                "Do not infer that a plateau length, region area, particle count, or schematic size represents a quantitative fraction unless the current figure_spec explicitly declares that relationship.",
                "Focus on missing labels, wrong directions, wrong axes, unreadable text, and irrelevant decorative content.",
                "Report only visible problems that you can directly verify from the image. Do not mention elements that are not visible in the current image.",
                "Treat label overlap, oversized titles, cramped legends, or text covering plotted marks as visual_issues.",
                "Keep each issue concise; do not include long derivations.",
                *visual_qa_policy_rules,
                *domain_rules,
            ],
        }
        try:
            if callable(progress_callback):
                progress_callback("visual_qa_started", {"figure_id": figure_id, "question_id": qid, "model": report["vision_model"]})
            with model_request_slot(provider):
                qa = client.chat_json_object(
                    [
                        {"role": "system", "content": "你是真题解析册插图质量审查器，只输出 JSON。"},
                        {"role": "user", "content": [{"type": "text", "text": json.dumps(payload, ensure_ascii=False)}, {"type": "image_url", "image_url": {"url": image_url}}]},
                    ],
                    model=str(report["vision_model"]),
                    max_tokens=min(FIGURE_AUXILIARY_MAX_TOKENS, 2048),
                    timeout=120,
                    attempts=1,
                )
        except Exception as exc:
            qa = {"ok": False, "error": str(exc)[:500]}
        qa = _ground_visual_qa_to_figure_spec(qa, spec)
        qa = _apply_crystallographic_index_whitelist(qa, question, spec)
        if callable(progress_callback):
            progress_callback("visual_qa_completed", {"figure_id": figure_id, "question_id": qid, "ok": qa.get("ok") is True, "error": str(qa.get("error") or "")[:300]})
        report["items"].append({"question_id": qid, "figure_id": figure_id, "path": str(path), "vision_input": image_input, "qa": qa})
    report_json.parent.mkdir(parents=True, exist_ok=True)
    report_json.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return report


def audit_figures_with_vision(
    structured_exam: dict[str, Any],
    specs_json: Path,
    output_dir: Path,
    report_json: Path,
    *,
    provider: Any | None = None,
    model: str = "",
    reuse_unchanged: bool = True,
    progress_callback: Any | None = None,
) -> dict[str, Any]:
    """Audit independent figure PNGs concurrently and persist one stable report."""
    specs_data = json.loads(specs_json.read_text(encoding="utf-8")) if specs_json.exists() else {"figures": []}
    enabled = bool(provider is not None and getattr(provider, "api_key", "") and getattr(provider, "supports_vision", False) and getattr(provider, "vision_model", ""))
    if not enabled:
        return _audit_figures_with_vision_serial(
            structured_exam, specs_json, output_dir, report_json, provider=provider, model=model, progress_callback=progress_callback,
        )
    specs = [item for item in specs_data.get("figures", []) or [] if isinstance(item, dict)]
    questions = {
        str(item.get("question_id") or "").strip(): item
        for item in structured_exam.get("items", []) or []
        if isinstance(item, dict) and str(item.get("question_id") or "").strip()
    }
    existing_report: dict[str, Any] = {}
    if reuse_unchanged and report_json.exists():
        try:
            loaded = json.loads(report_json.read_text(encoding="utf-8"))
            existing_report = loaded if isinstance(loaded, dict) else {}
        except (OSError, json.JSONDecodeError):
            existing_report = {}
    existing_items = {
        str(item.get("figure_id") or "").strip(): item
        for item in existing_report.get("items", []) or []
        if isinstance(item, dict) and str(item.get("figure_id") or "").strip()
    }
    audit_specs: list[dict[str, Any]] = []
    reused_items: list[dict[str, Any]] = []
    fingerprints: dict[str, str] = {}
    skipped: list[dict[str, Any]] = []
    for spec in specs:
        figure_id = str(spec.get("figure_id") or "").strip()
        qid = str(spec.get("question_id") or "").strip()
        image_path = output_dir / f"{figure_id}.png"
        if not figure_id or not image_path.exists():
            skipped.append({"question_id": qid, "figure_id": figure_id, "reason": "figure image missing"})
        else:
            fingerprint_payload = {
                "image_sha256": hashlib.sha256(image_path.read_bytes()).hexdigest(),
                "spec": _compact_figure_spec_for_visual_qa(spec),
                "question": _minimal_question_for_figure_repair(questions.get(qid, {}), qid),
                "provider": str(getattr(provider, "name", "") or ""),
                "model": str(model or getattr(provider, "vision_model", "") or ""),
                # Cached raw reviewer output is only reusable under the same
                # deterministic grounding policy.  Otherwise an obsolete OCR
                # false positive can remain sticky after the program learns
                # how to adjudicate it.
                "grounding_policy_version": "figure_spec_grounding.v2",
            }
            fingerprint = hashlib.sha256(
                json.dumps(fingerprint_payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
            ).hexdigest()
            fingerprints[figure_id] = fingerprint
            existing = existing_items.get(figure_id)
            if isinstance(existing, dict) and existing.get("audit_fingerprint") == fingerprint:
                reused_items.append({**existing, "path": str(image_path), "reused": True})
            else:
                audit_specs.append(spec)
    max_workers = figure_visual_audit_worker_count() if len(audit_specs) > 1 else 1
    worker_root = report_json.parent / "figure_visual_qa_workers"

    def audit_one(spec: dict[str, Any]) -> dict[str, Any]:
        figure_id = str(spec.get("figure_id") or "").strip()
        worker_dir = worker_root / figure_id
        worker_specs = worker_dir / "figure_specs.json"
        worker_report = worker_dir / "figure_visual_qa.json"
        worker_dir.mkdir(parents=True, exist_ok=True)
        worker_specs.write_text(json.dumps({"figures": [spec]}, ensure_ascii=False, indent=2), encoding="utf-8")
        return _audit_figures_with_vision_serial(
            structured_exam,
            worker_specs,
            output_dir,
            worker_report,
            provider=provider,
            model=model,
            progress_callback=None,
        )

    def completed(_index: int, spec: dict[str, Any], result: dict[str, Any]) -> None:
        if not callable(progress_callback):
            return
        item = next((row for row in result.get("items", []) if isinstance(row, dict)), {})
        qa = item.get("qa") if isinstance(item.get("qa"), dict) else {}
        progress_callback(
            "visual_qa_completed",
            {
                "figure_id": str(spec.get("figure_id") or ""),
                "question_id": str(spec.get("question_id") or ""),
                "ok": qa.get("ok") is True,
                "error": str(qa.get("error") or "")[:300],
            },
        )

    if callable(progress_callback):
        for spec in audit_specs:
            progress_callback("visual_qa_started", {"figure_id": str(spec.get("figure_id") or ""), "question_id": str(spec.get("question_id") or ""), "model": str(model or getattr(provider, "vision_model", "") or "")})
        for item in reused_items:
            progress_callback("visual_qa_reused", {"figure_id": item.get("figure_id"), "question_id": item.get("question_id")})
        for item in skipped:
            progress_callback("visual_qa_skipped", item)
    worker_reports = run_limited_concurrent(audit_specs, audit_one, max_workers=max_workers, on_complete=completed)
    items = [
        {
            **item,
            "audit_fingerprint": fingerprints.get(str(item.get("figure_id") or "").strip(), ""),
            "reused": False,
        }
        for worker_report in worker_reports
        for item in worker_report.get("items", [])
        if isinstance(item, dict)
    ] + reused_items
    report = {
        "schema_version": "answer_book.figure_visual_qa.v1",
        "enabled": True,
        "provider": getattr(provider, "name", ""),
        "vision_model": str(model or getattr(provider, "vision_model", "") or ""),
        "items": items,
        "skipped": skipped,
        "cache": {
            "content_addressed": True,
            "reused_count": len(reused_items),
            "audited_count": len(audit_specs),
        },
        "concurrency": {
            "max_workers": max_workers,
            "parallel_enabled": max_workers > 1 and len(audit_specs) > 1,
        },
    }
    report_json.parent.mkdir(parents=True, exist_ok=True)
    report_json.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return report
