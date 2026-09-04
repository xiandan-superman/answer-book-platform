from __future__ import annotations

import hashlib
import json
import os
import platform
import re
import shutil
import stat
import subprocess
import tempfile
import threading
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterable

from .document_contracts import FOOTER_TEXT, HEADER_FOOTER_CONTRACT, HEADER_TEXT, PAGE_CONTRACT, TEXT_CONTRACT
from .document_tool import DocumentToolFailure
from .paths import CACHE_DIR, CONFIG_DIR, LOCAL_CONFIG_DIR
from .practice_document_contracts import PRACTICE_PAGE_CONTRACT, PRACTICE_TEXT_CONTRACT

OFFICECLI_UPSTREAM = "https://github.com/iOfficeAI/OfficeCLI"
OFFICECLI_VERSION = "1.0.147"
OFFICECLI_RELEASE_TAG = f"v{OFFICECLI_VERSION}"
OFFICECLI_RELEASE_BASE = f"{OFFICECLI_UPSTREAM}/releases/download/{OFFICECLI_RELEASE_TAG}"
OFFICECLI_MIRROR_BASE = f"https://d.officecli.ai/releases/download/{OFFICECLI_RELEASE_TAG}"

_ASSET_SHA256 = {
    "officecli-mac-arm64": "55569d8a7430c1d8d7872c1661ff8cfea2eeef03ffc4fa8dbee437a4c91ee1ed",
    "officecli-mac-x64": "9f957b9439b922916360189bedfb780defc471b95ab8670f2a5a9630e7c9c253",
    "officecli-win-arm64.exe": "7ff0195c32405bac9cf6a32589d984fa7a863adfabbc6e42dfef47a7839264cf",
    "officecli-win-x64.exe": "724056e5ff079c3585df79c8afc386f08ef7d5f956cf4e2723534e129aab6e80",
}
_INLINE_MATH_RE = re.compile(r"(?<!\\)\$\$(.+?)(?<!\\)\$\$|(?<!\\)\$(.+?)(?<!\\)\$", re.DOTALL)


def _officecli_latex(value: str) -> str:
    """Preserve the platform rule that every visible Word-math run is italic."""
    latex = str(value or "").strip()
    return rf"\mathit{{{latex}}}" if latex else ""


def selected_word_tool_variant(value: str | None = None) -> str:
    configured: Any = value
    if configured is None:
        configured = os.environ.get("ANSWER_BOOK_WORD_TOOL_VARIANT")
    if configured is None:
        for path in (LOCAL_CONFIG_DIR / "word_tool.json", CONFIG_DIR / "word_tool.json"):
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except (FileNotFoundError, OSError, json.JSONDecodeError):
                continue
            if isinstance(payload, dict) and payload.get("variant") is not None:
                configured = payload["variant"]
                break
    raw = str(configured if configured is not None else "B").strip().upper()
    if raw not in {"A", "B"}:
        raise DocumentToolFailure(
            code="WORD_TOOL_VARIANT_INVALID",
            message=f"未知的 Word 工具版本：{raw or '<empty>'}",
            suggestion="设置 ANSWER_BOOK_WORD_TOOL_VARIANT=A 或 B；默认值为 B。",
            responsibility="configuration",
        )
    return raw


def _officecli_asset() -> str:
    machine = platform.machine().lower()
    if sys_platform := platform.system().lower():
        if sys_platform == "darwin":
            return "officecli-mac-arm64" if machine in {"arm64", "aarch64"} else "officecli-mac-x64"
        if sys_platform == "windows":
            return "officecli-win-arm64.exe" if machine in {"arm64", "aarch64"} else "officecli-win-x64.exe"
    raise DocumentToolFailure(
        code="OFFICECLI_PLATFORM_UNSUPPORTED",
        message=f"OfficeCLI B 版尚不支持当前平台：{platform.system()} {platform.machine()}",
        suggestion="在受支持的 macOS/Windows 主机运行，或明确配置 ANSWER_BOOK_WORD_TOOL_VARIANT=A。",
        responsibility="delivery_environment",
    )


def _download_with_retry(urls: Iterable[str], target: Path) -> None:
    last_error = ""
    for url in urls:
        for attempt in range(3):
            try:
                request = urllib.request.Request(url, headers={"User-Agent": "answer-book-platform-officecli"})
                with urllib.request.urlopen(request, timeout=300) as response, target.open("wb") as handle:
                    shutil.copyfileobj(response, handle)
                return
            except (OSError, urllib.error.URLError) as exc:
                last_error = str(exc)
                target.unlink(missing_ok=True)
                if attempt < 2:
                    time.sleep(0.5 * (2**attempt))
    raise DocumentToolFailure(
        code="OFFICECLI_DOWNLOAD_FAILED",
        message=f"OfficeCLI B 版运行时下载失败：{last_error or 'unknown error'}",
        suggestion="检查网络后重试，或通过 OFFICECLI_BINARY 指向已校验的 OfficeCLI 1.0.147。",
        responsibility="delivery_environment",
        retryable=True,
    )


def resolve_officecli_binary() -> Path:
    override = str(os.environ.get("OFFICECLI_BINARY") or "").strip()
    if override:
        binary = Path(override).expanduser().resolve()
        if binary.is_file():
            return binary
        raise DocumentToolFailure(
            code="OFFICECLI_BINARY_MISSING",
            message=f"配置的 OfficeCLI 不存在：{binary}",
            suggestion="修正 OFFICECLI_BINARY，或删除该配置让平台安装受控版本。",
            responsibility="configuration",
        )
    asset = _officecli_asset()
    binary_dir = CACHE_DIR / "officecli" / OFFICECLI_VERSION
    binary = binary_dir / ("officecli.exe" if asset.endswith(".exe") else "officecli")
    expected = _ASSET_SHA256[asset]
    if binary.is_file() and hashlib.sha256(binary.read_bytes()).hexdigest() == expected:
        return binary
    binary_dir.mkdir(parents=True, exist_ok=True)
    staged = binary.with_name(f".{binary.name}.{os.getpid()}.{threading.get_ident()}.download")
    _download_with_retry(
        (f"{OFFICECLI_MIRROR_BASE}/{asset}", f"{OFFICECLI_RELEASE_BASE}/{asset}"),
        staged,
    )
    actual = hashlib.sha256(staged.read_bytes()).hexdigest()
    if actual != expected:
        staged.unlink(missing_ok=True)
        raise DocumentToolFailure(
            code="OFFICECLI_CHECKSUM_MISMATCH",
            message="OfficeCLI B 版运行时校验失败。",
            suggestion="不要使用该下载结果；检查下载链路后重试。",
            responsibility="delivery_environment",
        )
    staged.chmod(staged.stat().st_mode | stat.S_IXUSR)
    os.replace(staged, binary)
    return binary


def _json_envelope(result: subprocess.CompletedProcess[str]) -> dict[str, Any]:
    text = (result.stdout or result.stderr or "").strip()
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        payload = {"success": result.returncode == 0, "message": text}
    return payload if isinstance(payload, dict) else {"success": False, "message": text}


def _run_officecli(binary: Path, *args: str, timeout: int = 300) -> dict[str, Any]:
    result = subprocess.run(
        [str(binary), *args],
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )
    payload = _json_envelope(result)
    if result.returncode != 0 or payload.get("success") is False:
        detail = str(payload.get("message") or payload.get("error") or result.stderr or result.stdout).strip()
        raise DocumentToolFailure(
            code="OFFICECLI_COMMAND_FAILED",
            message=f"OfficeCLI 执行失败：{detail or 'unknown error'}",
            suggestion="保留 B 版命令计划与候选件，修正文档命令或输入后重试；不要静默切回 A。",
            details={"args": list(args[:2]), "exit_code": result.returncode},
        )
    return payload


@dataclass
class OfficeCliPlan:
    commands: list[dict[str, Any]]
    paragraph_count: int = 0
    table_count: int = 0
    font: str = TEXT_CONTRACT.latin_font
    east_asia_font: str = TEXT_CONTRACT.east_asia_font
    body_size: float = TEXT_CONTRACT.body_size_pt
    line_spacing: float = TEXT_CONTRACT.line_spacing

    @classmethod
    def new(cls, *, practice: bool = False) -> "OfficeCliPlan":
        if practice:
            page = PRACTICE_PAGE_CONTRACT
            text = PRACTICE_TEXT_CONTRACT
            props = {
                "pageWidth": f"{page.width_inches}in",
                "pageHeight": f"{page.height_inches}in",
                "marginTop": f"{page.top_bottom_margin_inches}in",
                "marginBottom": f"{page.top_bottom_margin_inches}in",
                "marginLeft": f"{page.left_right_margin_inches}in",
                "marginRight": f"{page.left_right_margin_inches}in",
                "marginHeader": f"{page.header_footer_distance_inches}in",
                "marginFooter": f"{page.header_footer_distance_inches}in",
                "docDefaults.font": text.latin_font,
                "docDefaults.font.eastAsia": text.east_asia_font,
                "docDefaults.fontSize": str(text.body_size_pt),
                "docDefaults.lineSpacing": f"{text.line_spacing}x",
            }
        else:
            props = {
                "pageWidth": f"{PAGE_CONTRACT.width_cm}cm",
                "pageHeight": f"{PAGE_CONTRACT.height_cm}cm",
                "marginTop": f"{PAGE_CONTRACT.margin_cm}cm",
                "marginBottom": f"{PAGE_CONTRACT.margin_cm}cm",
                "marginLeft": f"{PAGE_CONTRACT.margin_cm}cm",
                "marginRight": f"{PAGE_CONTRACT.margin_cm}cm",
                "marginHeader": f"{PAGE_CONTRACT.header_distance_cm}cm",
                "marginFooter": f"{PAGE_CONTRACT.footer_distance_cm}cm",
                "docDefaults.font": TEXT_CONTRACT.latin_font,
                "docDefaults.font.eastAsia": TEXT_CONTRACT.east_asia_font,
                "docDefaults.fontSize": str(TEXT_CONTRACT.body_size_pt),
                "docDefaults.lineSpacing": f"{TEXT_CONTRACT.line_spacing}x",
            }
        return cls(
            [{"command": "set", "path": "/", "props": props}],
            font=text.latin_font if practice else TEXT_CONTRACT.latin_font,
            east_asia_font=text.east_asia_font if practice else TEXT_CONTRACT.east_asia_font,
            body_size=text.body_size_pt if practice else TEXT_CONTRACT.body_size_pt,
            line_spacing=text.line_spacing if practice else TEXT_CONTRACT.line_spacing,
        )

    def paragraph(self, text: str = "", **props: Any) -> str:
        self.paragraph_count += 1
        paragraph_props = {
            "text": text,
            "font": self.font,
            "font.ea": self.east_asia_font,
            "size": f"{self.body_size}pt",
            "color": "000000",
            "spaceBefore": "0pt",
            "spaceAfter": "0pt",
            "lineSpacing": f"{self.line_spacing}x",
            **props,
        }
        self.commands.append({"command": "add", "parent": "/body", "type": "paragraph", "props": paragraph_props})
        return f"/body/p[{self.paragraph_count}]"

    def rich_paragraph(self, pieces: list[tuple[str, str]], *, label: str = "", **props: Any) -> str:
        path = self.paragraph("", **props)
        if label:
            self.commands.append({"command": "add", "parent": path, "type": "run", "props": {"text": f"{label}：", "bold": "true", "font": self.font, "font.ea": self.east_asia_font, "size": f"{self.body_size}pt", "color": "000000"}})
        for kind, value in pieces:
            if not value:
                continue
            if kind == "equation":
                self.commands.append({"command": "add", "parent": path, "type": "equation", "props": {"formula": _officecli_latex(value), "mode": "inline"}})
            else:
                self.commands.append({"command": "add", "parent": path, "type": "run", "props": {"text": value, "font": self.font, "font.ea": self.east_asia_font, "size": f"{self.body_size}pt", "color": "000000"}})
        return path

    def equation(self, latex: str) -> None:
        self.commands.append({"command": "add", "parent": "/body", "type": "equation", "props": {"formula": _officecli_latex(latex)}})

    def picture(self, source: Path, *, title: str = "", description: str = "") -> None:
        path = self.paragraph("", align="center", keepNext="true")
        self.commands.append({
            "command": "add",
            "parent": path,
            "type": "picture",
            "props": {"src": str(source), "width": "15cm", "alt": description or title or source.stem},
        })

    def table(self, headers: list[Any], rows: list[list[Any]], *, title: str = "") -> None:
        if title:
            self.paragraph(str(title), keepNext="true")
        columns = max(len(headers), max((len(row) for row in rows), default=0))
        if not columns:
            return
        matrix = ([headers] if headers else []) + rows
        self.table_count += 1
        props: dict[str, Any] = {"rows": len(matrix), "cols": columns, "width": "100%", "layout": "fixed", "border.all": "single;4;000000"}
        self.commands.append({"command": "add", "parent": "/body", "type": "table", "props": props})
        for row_index, row in enumerate(matrix, start=1):
            row_props = {f"c{index}": str(row[index - 1]) if index <= len(row) else "" for index in range(1, columns + 1)}
            if headers and row_index == 1:
                row_props["header"] = "true"
            self.commands.append({"command": "set", "path": f"/body/tbl[{self.table_count}]/tr[{row_index}]", "props": row_props})


def _inline_pieces(text: Any) -> list[tuple[str, str]]:
    raw = str(text or "").replace("**", "")
    pieces: list[tuple[str, str]] = []
    cursor = 0
    for match in _INLINE_MATH_RE.finditer(raw):
        if match.start() > cursor:
            pieces.append(("text", raw[cursor:match.start()]))
        pieces.append(("equation", str(match.group(1) or match.group(2) or "").strip()))
        cursor = match.end()
    if cursor < len(raw):
        pieces.append(("text", raw[cursor:]))
    return pieces or [("text", raw)]


def _segment_pieces(segments: Any, formulas: dict[str, dict[str, Any]]) -> list[tuple[str, str]]:
    pieces: list[tuple[str, str]] = []
    for segment in segments if isinstance(segments, list) else []:
        if not isinstance(segment, dict):
            continue
        kind = str(segment.get("type") or "text")
        if kind == "formula_ref":
            formula = formulas.get(str(segment.get("formula_id") or ""), {})
            latex = str(formula.get("latex") or formula.get("linear") or "").strip()
            if not latex:
                raise DocumentToolFailure(
                    code="OFFICECLI_FORMULA_REFERENCE_MISSING",
                    message=f"B 版公式引用缺失：{segment.get('formula_id') or '<empty>'}",
                    suggestion="恢复公式对象与 formula_ref 的一一对应后重试。",
                )
            pieces.append(("equation", latex))
        elif kind == "text":
            pieces.extend(_inline_pieces(segment.get("text")))
        else:
            raise DocumentToolFailure(
                code="OFFICECLI_SEGMENT_TYPE_UNSUPPORTED",
                message=f"B 版尚不支持答案片段类型：{kind}",
                suggestion="为该结构增加明确的 OfficeCLI 命令映射，不能静默丢弃。",
            )
    return pieces


def _execute_plan(plan: OfficeCliPlan, output: Path) -> Path:
    binary = resolve_officecli_binary()
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="answer-book-officecli-") as raw_tmp:
        command_file = Path(raw_tmp) / "commands.json"
        command_file.write_text(json.dumps(plan.commands, ensure_ascii=False), encoding="utf-8")
        _run_officecli(binary, "create", str(output), "--force", "--locale", "zh-CN", "--json")
        try:
            _run_officecli(binary, "batch", str(output), "--input", str(command_file), "--stop-on-error", "--json")
            _run_officecli(binary, "save", str(output), "--json")
            _run_officecli(binary, "validate", str(output), "--json")
        except Exception:
            output.unlink(missing_ok=True)
            raise
        finally:
            subprocess.run(
                [str(binary), "close", str(output), "--json"],
                capture_output=True,
                text=True,
                timeout=30,
                check=False,
            )
    return output


def build_answer_book_with_officecli(fragments_json: Path, output_docx: Path) -> Path:
    data = json.loads(fragments_json.read_text(encoding="utf-8"))
    plan = OfficeCliPlan.new()
    plan.commands.extend([
        {"command": "add", "parent": "/", "type": "header", "props": {"type": "default", "text": HEADER_TEXT, "align": "center", "font": HEADER_FOOTER_CONTRACT.header_font, "size": f"{HEADER_FOOTER_CONTRACT.header_size_pt}pt", "bold": "true"}},
        {"command": "add", "parent": "/", "type": "footer", "props": {"type": "default", "text": FOOTER_TEXT, "align": "center", "font": HEADER_FOOTER_CONTRACT.footer_font, "size": f"{HEADER_FOOTER_CONTRACT.footer_size_pt}pt"}},
        {"command": "add", "parent": "/footer[1]", "type": "paragraph", "props": {"text": "第 ", "align": "center", "font": TEXT_CONTRACT.east_asia_font, "size": "10.5pt"}},
        {"command": "add", "parent": "/footer[1]/p[2]", "type": "field", "props": {"fieldType": "page"}},
        {"command": "add", "parent": "/footer[1]/p[2]", "type": "run", "props": {"text": " 页", "font": TEXT_CONTRACT.east_asia_font, "size": "10.5pt"}},
    ])
    plan.paragraph(str(data.get("document_title") or "真题答案解析"), style="Title", align="center", bold="true", size=f"{TEXT_CONTRACT.title_size_pt}pt")
    current_section = ""
    for index, fragment in enumerate(data.get("fragments") or [], start=1):
        if not isinstance(fragment, dict):
            continue
        section = str(fragment.get("section") or "")
        if section and section != current_section:
            current_section = section
            plan.paragraph(section, style="Heading1", bold="true", keepNext="true")
        number = str(fragment.get("display_number") or fragment.get("number") or fragment.get("question_id") or index).replace("_", "-")
        formulas = {str(item.get("formula_id") or ""): item for item in fragment.get("formulas") or [] if isinstance(item, dict)}
        plan.paragraph(f"{number}、", style="Heading2", bold="true", keepNext="true")
        summary_segments = fragment.get("answer_summary_segments")
        if isinstance(summary_segments, list):
            answer_pieces = _segment_pieces(summary_segments, formulas)
        else:
            answer_pieces = _inline_pieces(fragment.get("answer_summary") or fragment.get("answer") or "待复核")
        plan.rich_paragraph(answer_pieces, label="答案", firstLineIndent=f"{TEXT_CONTRACT.answer_first_line_indent_cm}cm", lineSpacing=f"{TEXT_CONTRACT.line_spacing}x")
        for block in fragment.get("blocks") or []:
            if not isinstance(block, dict) or str(block.get("label") or "") == "教材依据":
                continue
            label = "补充公式" if str(block.get("label") or "") == "待复核公式" else str(block.get("label") or "")
            text_pieces: list[tuple[str, str]] = []
            for segment in block.get("segments") or []:
                if not isinstance(segment, dict):
                    continue
                if str(segment.get("type") or "") == "image_ref":
                    image = Path(str(segment.get("path") or ""))
                    if not image.is_absolute():
                        image = fragments_json.parent / image
                    if not image.is_file():
                        raise DocumentToolFailure(
                            code="OFFICECLI_REQUIRED_IMAGE_MISSING",
                            message=f"B 版所需图片不存在：{segment.get('image_id') or image.name}",
                            suggestion="恢复对应图片资产后重试。",
                        )
                    if text_pieces:
                        plan.rich_paragraph(text_pieces, label=label, lineSpacing=f"{TEXT_CONTRACT.line_spacing}x")
                        text_pieces = []
                        label = ""
                    plan.picture(image, title=str(segment.get("caption") or ""), description=str(segment.get("alt") or ""))
                else:
                    text_pieces.extend(_segment_pieces([segment], formulas))
            if text_pieces:
                plan.rich_paragraph(
                    text_pieces,
                    label=label,
                    lineSpacing=f"{TEXT_CONTRACT.note_line_spacing if label == '易错点及注意事项' else TEXT_CONTRACT.line_spacing}x",
                )
    return _execute_plan(plan, output_docx)


def _practice_title(plan: OfficeCliPlan, data: dict[str, Any], kind: str) -> None:
    title = "专项练习题目卷" if kind == "questions" else "专项练习"
    plan.paragraph(title, style="Title", align="center", bold="true", size="16pt")
    subject = str((data.get("source_analysis") or {}).get("subject") or "").strip()
    goal = str((data.get("blueprint") or {}).get("training_goal") or "").strip()
    if subject:
        plan.paragraph(f"学科：{subject}", align="center", size="9.5pt")
    if goal:
        plan.paragraph(f"训练目标：{goal}", align="center", size="9.5pt")


def _practice_assets(plan: OfficeCliPlan, item: dict[str, Any], location: str, asset_dir: Path) -> None:
    for formula in item.get("formulas") or []:
        if not isinstance(formula, dict) or location not in str(formula.get("location") or "stem"):
            continue
        if location == "stem" and str(formula.get("role") or "relation").lower() != "given":
            continue
        caption = str(formula.get("caption") or "").strip()
        if caption:
            plan.paragraph(caption, keepNext="true")
        latex = str(formula.get("latex") or "").strip()
        if latex:
            plan.equation(latex)
    for table in item.get("tables") or []:
        if isinstance(table, dict) and location in str(table.get("location") or "stem"):
            plan.table(list(table.get("headers") or []), [list(row) for row in table.get("rows") or [] if isinstance(row, list)], title=str(table.get("title") or ""))
    for figure_index, figure in enumerate(item.get("figures") or [], start=1):
        if not isinstance(figure, dict) or location not in str(figure.get("location") or "stem"):
            continue
        path = Path(str(figure.get("image_path") or figure.get("path") or ""))
        if not path.is_file():
            from .practice_export import _chart_png

            chart = _chart_png(figure)
            if chart is not None:
                path = asset_dir / f"figure-{figure_index}.png"
                path.write_bytes(chart.getvalue())
        if path.is_file():
            plan.picture(path, title=str(figure.get("title") or ""), description=str(figure.get("description") or ""))
        else:
            raise DocumentToolFailure(
                code="OFFICECLI_PRACTICE_FIGURE_UNRENDERABLE",
                message=f"第 {item.get('number') or '?'} 题图无法生成真实图片。",
                suggestion="恢复已绑定图片或可绘制的结构化图形数据后重试。",
            )


def _practice_questions(
    plan: OfficeCliPlan,
    data: dict[str, Any],
    progress_callback: Callable[[int, int], None] | None,
    asset_dir: Path,
) -> None:
    plan.paragraph("练习题", style="Heading1", bold="true")
    exercises = data.get("exercises") or []
    total = len(exercises)
    for index, item in enumerate(exercises, start=1):
        if not isinstance(item, dict):
            continue
        plan.paragraph(f"第 {index} 题", style="Heading2", bold="true", keepNext="true")
        for part in str(item.get("stem") or "").splitlines() or [""]:
            if part.strip():
                plan.rich_paragraph(_inline_pieces(part.strip()), firstLineIndent=f"{PRACTICE_TEXT_CONTRACT.first_line_indent_pt}pt", lineSpacing=f"{PRACTICE_TEXT_CONTRACT.line_spacing}x", align="justify")
        _practice_assets(plan, item, "stem", asset_dir)
        for option_index, option in enumerate(item.get("options") or []):
            if isinstance(option, dict):
                plan.rich_paragraph(
                    _inline_pieces(f"{chr(65 + option_index)}. {str(option.get('text') or '').replace('**', '')}"),
                    indent=f"{PRACTICE_TEXT_CONTRACT.list_left_indent_pt}pt",
                    hangingIndent=f"{PRACTICE_TEXT_CONTRACT.list_hanging_indent_pt}pt",
                )
        if progress_callback is not None:
            progress_callback(index, total)


def _practice_solutions(plan: OfficeCliPlan, data: dict[str, Any], asset_dir: Path) -> None:
    plan.paragraph("参考答案与解析", style="Heading1", bold="true")
    for index, item in enumerate(data.get("exercises") or [], start=1):
        if not isinstance(item, dict):
            continue
        plan.paragraph(f"第 {index} 题", style="Heading2", bold="true", keepNext="true")
        plan.rich_paragraph(_inline_pieces(item.get("answer") or ""), label="参考答案", lineSpacing=f"{PRACTICE_TEXT_CONTRACT.line_spacing}x")
        _practice_assets(plan, item, "solution", asset_dir)
        steps = item.get("solution_steps") or []
        if steps:
            plan.paragraph("解析", style="Heading3", bold="true", keepNext="true")
            for step_index, step in enumerate(steps, start=1):
                plan.rich_paragraph(
                    _inline_pieces(f"{step_index}. {step}"),
                    indent=f"{PRACTICE_TEXT_CONTRACT.list_left_indent_pt}pt",
                    hangingIndent=f"{PRACTICE_TEXT_CONTRACT.list_hanging_indent_pt}pt",
                )
        points = [str(value).strip() for value in item.get("knowledge_points") or [] if str(value).strip()]
        if points:
            plan.paragraph("涉及知识点：" + "、".join(points), size=f"{PRACTICE_TEXT_CONTRACT.auxiliary_size_pt}pt", color="64748B")


def build_practice_with_officecli(
    data: dict[str, Any],
    *,
    document_kind: str,
    progress_callback: Callable[[int, int], None] | None = None,
) -> bytes:
    if not isinstance(data, dict) or not isinstance(data.get("exercises"), list) or not data["exercises"]:
        raise ValueError("没有可导出的专项练习。")
    with tempfile.TemporaryDirectory(prefix="answer-book-officecli-practice-") as raw_tmp:
        asset_dir = Path(raw_tmp) / "assets"
        asset_dir.mkdir()
        plan = OfficeCliPlan.new(practice=True)
        _practice_title(plan, data, document_kind)
        if document_kind in {"questions", "combined"}:
            _practice_questions(plan, data, progress_callback, asset_dir)
        if document_kind == "combined":
            plan.commands.append({"command": "add", "parent": "/", "type": "section", "props": {"type": "nextPage"}})
        if document_kind in {"solutions", "combined"}:
            _practice_solutions(plan, data, asset_dir)
        plan.commands.extend([
            {"command": "add", "parent": "/", "type": "footer", "props": {"type": "default", "text": "第 ", "field": "page", "align": "right", "size": "9pt"}},
            {"command": "add", "parent": "/footer[1]/p[1]", "type": "run", "props": {"text": " 页", "size": "9pt"}},
        ])
        output = Path(raw_tmp) / "practice.docx"
        _execute_plan(plan, output)
        return output.read_bytes()


def officecli_runtime_info() -> dict[str, str]:
    binary = resolve_officecli_binary()
    result = subprocess.run([str(binary), "--version"], capture_output=True, text=True, timeout=30, check=False)
    return {
        "variant": "B",
        "engine": "iOfficeAI/OfficeCLI",
        "expected_version": OFFICECLI_VERSION,
        "actual_version": (result.stdout or result.stderr).strip(),
        "binary_sha256": hashlib.sha256(binary.read_bytes()).hexdigest(),
    }
