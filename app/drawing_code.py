from __future__ import annotations

import ast
import base64
import importlib.util
import json
import mimetypes
import os
import re
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .capabilities.catalog import capability_policy_contributions
from .llm_client import OpenAICompatibleClient, parse_json_content
from .prompt_registry import prompt_contract
from .question_types import explicit_question_type, iter_leaf_question_parts
from .settings import DRAWING_CODE_MAX_TOKENS

ALLOWED_IMPORT_ROOTS = {"math", "numpy", "matplotlib", "textwrap"}
AUTO_INSTALLABLE_IMPORTS = {"numpy": "numpy", "matplotlib": "matplotlib"}
DENIED_CALL_NAMES = {"open", "input", "eval", "exec", "compile", "__import__", "breakpoint"}
DENIED_ATTRS = {
    "system",
    "popen",
    "spawn",
    "fork",
    "remove",
    "unlink",
    "rmdir",
    "removedirs",
    "rename",
    "replace",
    "chmod",
    "chown",
    "kill",
    "walk",
}
DENIED_NAMES = {"os", "sys", "subprocess", "socket", "pathlib", "shutil", "requests", "urllib", "http", "builtins", "importlib"}
TEXT_METHODS = {"set_title", "set_xlabel", "set_ylabel", "text", "annotate", "suptitle"}
TEXT_KEYWORDS = {"label", "title", "xlabel", "ylabel"}


TAGGED_JSON_RE = re.compile(r"<JSON>\s*(.*?)\s*</JSON>", re.IGNORECASE | re.DOTALL)
TAGGED_FILE_RE = re.compile(
    r"<FILE\s+name=[\"']([^\"']+)[\"']\s*>\s*(.*?)\s*</FILE>",
    re.IGNORECASE | re.DOTALL,
)
COLOR_KEYWORDS = {"c", "color", "colors", "edgecolor", "edgecolors", "facecolor", "facecolors", "markeredgecolor", "markerfacecolor"}
CMAP_KEYWORDS = {"cmap", "colormap"}
ALLOWED_COLOR_NAMES = {
    "black",
    "k",
    "white",
    "w",
    "gray",
    "grey",
    "lightgray",
    "lightgrey",
    "darkgray",
    "darkgrey",
    "dimgray",
    "dimgrey",
    "silver",
    "gainsboro",
    "none",
    "transparent",
}
ALLOWED_CMAP_NAMES = {"gray", "grey", "greys", "binary"}
ALLOWED_TEXT_TOKENS = {
    "x",
    "y",
    "z",
    "d",
    "i",
    "n",
    "h",
    "k",
    "l",
    "hkl",
    "uvw",
    "xrd",
    "xrf",
    "tem",
    "sem",
    "bcc",
    "fcc",
    "hcp",
    "sc",
    "cscl",
    "nacl",
    "2theta",
    "theta",
    "sin",
    "cos",
    "tan",
    "log",
    "ln",
    "exp",
    "au",
    "a.u",
    "nm",
    "um",
    "mm",
    "cm",
    "m",
    "pa",
    "kpa",
    "mpa",
    "gpa",
    "ev",
    "kev",
    "mev",
    "hz",
    "khz",
    "mhz",
    "ghz",
    "mol",
    "wt",
    "at",
    "ph",
}
DENIED_ENGLISH_WORDS = {
    "ordered",
    "disordered",
    "fundamental",
    "superlattice",
    "intensity",
    "relative",
    "peak",
    "peaks",
    "position",
    "positions",
    "zone",
    "axis",
    "diffraction",
    "pattern",
    "lattice",
    "structure",
    "original",
    "new",
    "added",
    "curve",
    "point",
    "points",
    "temperature",
    "stress",
    "strain",
    "time",
}
MATH_TEXT_COMMANDS = {
    "alpha",
    "beta",
    "gamma",
    "delta",
    "epsilon",
    "eta",
    "theta",
    "lambda",
    "mu",
    "nu",
    "pi",
    "rho",
    "sigma",
    "tau",
    "phi",
    "chi",
    "psi",
    "omega",
    "bar",
    "overline",
    "underline",
    "hat",
    "tilde",
    "vec",
    "mathrm",
    "mathit",
    "mathbf",
    "mathsf",
    "pm",
    "mp",
    "times",
    "cdot",
    "circ",
    "degree",
    "sqrt",
    "frac",
    "sum",
    "Delta",
}
GENERIC_FONT_FAMILIES = {"serif", "sans-serif", "sans", "monospace", "cursive", "fantasy", "system-ui"}
PROJECT_FONT_CACHE_DIR = Path(__file__).resolve().parents[1] / "assets" / "fonts"
USER_FONT_CACHE_DIR = Path.home() / ".cache" / "answer_book_platform" / "fonts"
CJK_FONT_FALLBACK_ORDER = [
    "Noto Sans CJK SC",
    "Source Han Sans SC",
    "Arial Unicode MS",
    "Songti SC",
    "PingFang SC",
    "SimSong",
    "Microsoft YaHei",
    "SimHei",
    "STHeiti",
]
OPEN_FONT_DOWNLOAD_URLS = {
    "Noto Sans CJK SC": "https://github.com/notofonts/noto-cjk/raw/main/Sans/OTF/SimplifiedChinese/NotoSansCJKsc-Regular.otf",
}
DEFAULT_FONT_MAX_BYTES = 80 * 1024 * 1024


def _literal_font_strings(node: ast.AST) -> list[str]:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return [node.value]
    if isinstance(node, (ast.List, ast.Tuple, ast.Set)):
        out: list[str] = []
        for item in node.elts:
            out.extend(_literal_font_strings(item))
        return out
    return []


def _requested_font_families(code: str) -> list[str]:
    try:
        tree = ast.parse(code)
    except SyntaxError:
        return []
    requested: list[str] = []

    def add(values: list[str]) -> None:
        for value in values:
            name = str(value or "").strip()
            if not name or name.lower() in GENERIC_FONT_FAMILIES:
                continue
            if name not in requested:
                requested.append(name)

    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Subscript):
                    key = target.slice
                    if isinstance(key, ast.Constant) and key.value in {"font.sans-serif", "font.family"}:
                        add(_literal_font_strings(node.value))
        elif isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "update":
            if not node.args or not isinstance(node.args[0], ast.Dict):
                continue
            for key, value in zip(node.args[0].keys, node.args[0].values):
                if isinstance(key, ast.Constant) and key.value in {"font.sans-serif", "font.family"}:
                    add(_literal_font_strings(value))
    return requested


def _parse_font_url_config(value: str) -> dict[str, str]:
    text = str(value or "").strip()
    if not text:
        return {}
    try:
        raw = json.loads(text)
        if isinstance(raw, dict):
            return {str(k).strip(): str(v).strip() for k, v in raw.items() if str(k).strip() and str(v).strip()}
    except json.JSONDecodeError:
        pass
    out: dict[str, str] = {}
    for item in re.split(r"[;\n]", text):
        if not item.strip() or "=" not in item:
            continue
        key, url = item.split("=", 1)
        if key.strip() and url.strip():
            out[key.strip()] = url.strip()
    return out


def _font_download_urls() -> dict[str, str]:
    urls: dict[str, str] = {}
    urls.update(_parse_font_url_config(os.environ.get("ANSWER_BOOK_FONT_URLS_JSON", "")))
    urls.update(_parse_font_url_config(os.environ.get("ANSWER_BOOK_FONT_URLS", "")))
    return urls


def _font_sha256_config() -> dict[str, str]:
    values: dict[str, str] = {}
    values.update(_parse_font_url_config(os.environ.get("ANSWER_BOOK_FONT_SHA256_JSON", "")))
    values.update(_parse_font_url_config(os.environ.get("ANSWER_BOOK_FONT_SHA256", "")))
    cleaned: dict[str, str] = {}
    for name, digest in values.items():
        text = str(digest or "").strip().lower()
        if text.startswith("sha256:"):
            text = text.split(":", 1)[1].strip()
        if re.fullmatch(r"[0-9a-f]{64}", text):
            cleaned[name] = text
    return cleaned


def _env_flag(name: str) -> bool:
    return str(os.environ.get(name, "")).strip().lower() in {"1", "true", "yes", "on"}


def _font_max_bytes() -> int:
    try:
        value = int(os.environ.get("ANSWER_BOOK_FONT_MAX_BYTES", str(DEFAULT_FONT_MAX_BYTES)))
    except ValueError:
        return DEFAULT_FONT_MAX_BYTES
    return max(1024 * 1024, min(value, 200 * 1024 * 1024))


def _ensure_allowed_python_dependencies(code: str) -> list[str]:
    if _env_flag("ANSWER_BOOK_DISABLE_AUTO_PACKAGE_INSTALL"):
        return []
    issues: list[str] = []
    for root in _import_roots_in_code(code):
        if root not in ALLOWED_IMPORT_ROOTS or root not in AUTO_INSTALLABLE_IMPORTS:
            continue
        if importlib.util.find_spec(root) is not None:
            continue
        cmd = [sys.executable, "-m", "pip", "install", "--disable-pip-version-check", "--no-input"]
        index_url = os.environ.get("ANSWER_BOOK_PIP_INDEX_URL", "").strip()
        extra_index_url = os.environ.get("ANSWER_BOOK_PIP_EXTRA_INDEX_URL", "").strip()
        trusted_host = os.environ.get("ANSWER_BOOK_PIP_TRUSTED_HOST", "").strip()
        if index_url:
            cmd.extend(["--index-url", index_url])
        if extra_index_url:
            cmd.extend(["--extra-index-url", extra_index_url])
        if trusted_host:
            cmd.extend(["--trusted-host", trusted_host])
        cmd.append(AUTO_INSTALLABLE_IMPORTS[root])
        try:
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=180)
        except Exception as exc:
            issues.append(f"failed to install required package {root}: {exc.__class__.__name__}")
            continue
        if proc.returncode != 0 or importlib.util.find_spec(root) is None:
            tail = ((proc.stderr or "") + "\n" + (proc.stdout or "")).strip()[-500:]
            issues.append(f"failed to install required package {root}: {tail}")
    return issues


def _import_roots_in_code(code: str) -> list[str]:
    try:
        tree = ast.parse(code)
    except SyntaxError:
        return []
    roots: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names = [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom):
            names = [node.module or ""]
        else:
            continue
        for name in names:
            root = str(name or "").split(".", 1)[0]
            if root and root not in roots:
                roots.append(root)
    return roots


def drawing_domain_quality_rules(question: dict[str, Any], caption: str = "") -> list[str]:
    stem = str(question.get("stem") or "")
    subquestions = " ".join(str(item.get("stem") or "") for item in question.get("subquestions") or [] if isinstance(item, dict))
    text = f"{stem}\n{subquestions}\n{caption}".lower()
    rules: list[str] = []
    rules.extend(
        [
            "Use compact publication-style layout: no oversized in-figure title; prefer the caption field for the title.",
            "Keep labels away from markers, axes, arrows, and neighboring labels; use offsets or smaller fonts to prevent overlap.",
            "Do not add explanatory arrows, axes, legends, or equations unless they are needed to answer the drawing question.",
        ]
    )
    for contribution in capability_policy_contributions(
        "drawing_quality",
        {"question": question, "caption": caption, "text": text},
        text=text,
    ):
        if isinstance(contribution, dict):
            rules.extend(str(rule) for rule in contribution.get("rules", []) if str(rule).strip())
    if question.get("subquestions"):
        rules.append("If the question has multiple drawing subquestions, the figure must cover every drawing subquestion with separate panels, rows, or clearly separated regions.")
    return list(dict.fromkeys(rules))


def _short_drawing_text(value: Any, limit: int = 900) -> str:
    text = str(value or "").strip()
    if len(text) <= limit:
        return text
    return text[:limit].rstrip() + "..."


def _compact_drawing_answer_context(fragment: dict[str, Any]) -> dict[str, Any]:
    useful_blocks: list[dict[str, str]] = []
    for block in fragment.get("blocks") or []:
        if not isinstance(block, dict):
            continue
        label = str(block.get("label") or "").strip()
        if label not in {"解析", "作图要点", "图示", "答案"}:
            continue
        texts: list[str] = []
        for segment in block.get("segments") or []:
            if isinstance(segment, dict) and segment.get("type") == "text":
                value = str(segment.get("text") or "").strip()
                if value:
                    texts.append(value)
        if texts:
            useful_blocks.append({"label": label, "text": _short_drawing_text(" ".join(texts), 900)})
        if len(useful_blocks) >= 3:
            break
    return {
        "answer_summary": _short_drawing_text(fragment.get("answer_summary") or fragment.get("answer") or "", 1200),
        "drawing_relevant_blocks": useful_blocks,
    }


def _compact_question_understanding(question: dict[str, Any]) -> dict[str, Any]:
    understanding = question.get("question_understanding") if isinstance(question.get("question_understanding"), dict) else {}
    return {
        "question_requirements": understanding.get("question_requirements") or [],
        "tables": understanding.get("tables") or [],
        "images": [
            {
                "image_id": item.get("image_id"),
                "ocr_text": _short_drawing_text(item.get("ocr_text"), 600),
                "visual_description": _short_drawing_text(item.get("visual_description"), 1000),
                "detected_labels": item.get("detected_labels") or [],
                "axes": item.get("axes") or {},
                "curves": item.get("curves") or [],
                "data_points": item.get("data_points") or [],
                "answer_relevant_observations": item.get("answer_relevant_observations") or [],
                "uncertainties": item.get("uncertainties") or [],
            }
            for item in (understanding.get("images") or [])[:4]
            if isinstance(item, dict)
        ],
        "uncertainties": understanding.get("uncertainties") or [],
    }


def _drawing_prompt_question(question: dict[str, Any]) -> dict[str, Any]:
    """Remove non-drawing siblings before requesting independent drawing code."""

    drawing_parts = [
        part
        for part in iter_leaf_question_parts(question)
        if explicit_question_type(part) == "作图题"
    ]
    if not drawing_parts:
        return question
    scoped = dict(question)
    scoped["stem"] = "\n".join(
        f"{str(part.get('marker') or part.get('number') or '').strip()} {str(part.get('stem') or '').strip()}".strip()
        for part in drawing_parts
        if str(part.get("stem") or "").strip()
    )
    scoped["subquestions"] = drawing_parts
    scoped.pop("requirements", None)
    return scoped


def _drawing_image_parts(question: dict[str, Any]) -> list[dict[str, Any]]:
    parts: list[dict[str, Any]] = []
    for raw in (question.get("image_refs") or [])[:4]:
        path = Path(str(raw))
        if not path.exists() or not path.is_file():
            continue
        mime = mimetypes.guess_type(path.name)[0] or "image/png"
        data_url = f"data:{mime};base64,{base64.b64encode(path.read_bytes()).decode('ascii')}"
        parts.append({"type": "image_url", "image_url": {"url": data_url}})
    return parts


RUNNER_TEMPLATE = r'''
import json
try:
    import resource
except ImportError:
    resource = None

# resource is a Unix-only module. Windows runs the isolated renderer without
# POSIX rlimits; process timeout remains enforced by the parent process.
if resource is not None:
    try:
        cpu_soft, cpu_hard = resource.getrlimit(resource.RLIMIT_CPU)
        new_cpu = min({cpu_seconds}, cpu_hard if cpu_hard > 0 else {cpu_seconds})
        resource.setrlimit(resource.RLIMIT_CPU, (new_cpu, cpu_hard))

        mem_soft, mem_hard = resource.getrlimit(resource.RLIMIT_AS)
        if mem_hard > 0:
            new_mem = min({memory_bytes}, mem_hard)
            resource.setrlimit(resource.RLIMIT_AS, (new_mem, mem_hard))
    except (ValueError, OSError):
        pass

import matplotlib
matplotlib.use("Agg")
import hashlib
import re
import urllib.parse
import urllib.request
from matplotlib import font_manager
from matplotlib.ft2font import FT2Font
from pathlib import Path
REQUESTED_FONT_NAMES = {requested_fonts_json}
FONT_CACHE_DIRS = [Path(item) for item in {font_cache_dirs_json}]
CONFIGURED_FONT_URLS = {font_download_urls_json}
FONT_SHA256 = {font_sha256_json}
OPEN_FONT_DOWNLOAD_URLS = {open_font_download_urls_json}
ALLOW_INSECURE_FONT_URLS = {allow_insecure_font_urls_python}
REQUIRE_FONT_SHA256 = {require_font_sha256_python}
FONT_MAX_BYTES = {font_max_bytes}
fallback_fonts = {fallback_fonts_json}
FONT_DOWNLOAD_LOG = []
font_file_candidates = [
    "/System/Library/Fonts/Supplemental/Arial Unicode.ttf",
    "/System/Library/Fonts/Supplemental/Songti.ttc",
    "/System/Library/Fonts/PingFang.ttc",
    "/Library/Fonts/Arial Unicode.ttf",
    "C:/Windows/Fonts/msyh.ttc",
    "C:/Windows/Fonts/simhei.ttf",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/opentype/noto/NotoSansCJKsc-Regular.otf",
    "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
]


def _safe_font_filename(font_name, url):
    parsed = urllib.parse.urlparse(str(url or ""))
    suffix = Path(parsed.path).suffix.lower()
    if suffix not in {{".ttf", ".otf", ".ttc"}}:
        return ""
    safe = re.sub(r"[^0-9A-Za-z_.-]+", "_", str(font_name or "font")).strip("._") or "font"
    return safe + suffix


def _log_font_download(font_name, status, detail=""):
    try:
        FONT_DOWNLOAD_LOG.append({{"font": str(font_name or ""), "status": str(status or ""), "detail": str(detail or "")[:160]}})
    except Exception:
        pass


def _font_files_in_cache():
    files = []
    for directory in FONT_CACHE_DIRS:
        try:
            if not directory.exists():
                continue
            for pattern in ("*.ttf", "*.otf", "*.ttc"):
                files.extend(directory.glob(pattern))
        except Exception:
            pass
    return files


def _register_font_file(path):
    try:
        if Path(path).exists():
            font_manager.fontManager.addfont(str(path))
            return True
    except Exception:
        return False
    return False


def _sha256_file(path):
    hasher = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            hasher.update(chunk)
    return hasher.hexdigest()


def _copy_or_download_to_tmp(url, tmp_path):
    parsed = urllib.parse.urlparse(str(url or ""))
    total = 0
    hasher = hashlib.sha256()
    if parsed.scheme == "file":
        source = Path(urllib.request.url2pathname(parsed.path))
        with source.open("rb") as src, Path(tmp_path).open("wb") as dst:
            for chunk in iter(lambda: src.read(1024 * 1024), b""):
                total += len(chunk)
                if total > FONT_MAX_BYTES:
                    raise RuntimeError("font file exceeds size limit")
                hasher.update(chunk)
                dst.write(chunk)
        return hasher.hexdigest()

    request = urllib.request.Request(str(url), headers={{"User-Agent": "answer-book-platform/1.0"}})
    with urllib.request.urlopen(request, timeout=30) as response, Path(tmp_path).open("wb") as dst:
        for chunk in iter(lambda: response.read(1024 * 1024), b""):
            if not chunk:
                break
            total += len(chunk)
            if total > FONT_MAX_BYTES:
                raise RuntimeError("font file exceeds size limit")
            hasher.update(chunk)
            dst.write(chunk)
    return hasher.hexdigest()


def _available_font_names():
    return {{font.name for font in font_manager.fontManager.ttflist}}


def _font_supports_cjk(font_name):
    try:
        path = font_manager.findfont(font_manager.FontProperties(family=font_name), fallback_to_default=False)
        if not path:
            return False
        charmap = FT2Font(path).get_charmap()
        return any(ord(ch) in charmap for ch in "中文强度无序有序体")
    except Exception:
        return False


def _download_font(font_name, url):
    if not FONT_CACHE_DIRS or not url:
        return ""
    target_dir = FONT_CACHE_DIRS[0]
    try:
        parsed = urllib.parse.urlparse(str(url or ""))
        if parsed.scheme not in {{"https", "file"}} and not (ALLOW_INSECURE_FONT_URLS and parsed.scheme == "http"):
            _log_font_download(font_name, "rejected", "unsupported or insecure URL scheme")
            return ""
        filename = _safe_font_filename(font_name, url)
        if not filename:
            _log_font_download(font_name, "rejected", "font URL must end with .ttf, .otf, or .ttc")
            return ""
        expected_sha = str(FONT_SHA256.get(font_name, "") or "").strip().lower()
        if REQUIRE_FONT_SHA256 and not expected_sha:
            _log_font_download(font_name, "rejected", "sha256 is required for this font source")
            return ""
        target_dir.mkdir(parents=True, exist_ok=True)
        target = target_dir / filename
        if target.exists() and target.stat().st_size > 0:
            if expected_sha and _sha256_file(target) != expected_sha:
                target.unlink(missing_ok=True)
                _log_font_download(font_name, "removed", "cached font sha256 mismatch")
            elif _register_font_file(target):
                _log_font_download(font_name, "cached", str(target))
                return str(target)
        tmp_target = target.with_suffix(target.suffix + ".part")
        actual_sha = _copy_or_download_to_tmp(url, tmp_target)
        if expected_sha and actual_sha != expected_sha:
            tmp_target.unlink(missing_ok=True)
            _log_font_download(font_name, "rejected", "downloaded font sha256 mismatch")
            return ""
        tmp_target.replace(target)
        if _register_font_file(target):
            _log_font_download(font_name, "downloaded", str(target))
            return str(target)
        _log_font_download(font_name, "rejected", "downloaded file could not be registered as a font")
    except Exception as exc:
        _log_font_download(font_name, "failed", exc.__class__.__name__)
        return ""
    return ""


for font_file in font_file_candidates:
    try:
        if Path(font_file).exists():
            font_manager.fontManager.addfont(font_file)
    except Exception:
        pass
for font_file in _font_files_in_cache():
    _register_font_file(font_file)
for font_name in REQUESTED_FONT_NAMES:
    if font_name not in _available_font_names():
        _download_font(font_name, CONFIGURED_FONT_URLS.get(font_name, ""))
if not any(font_name in _available_font_names() and _font_supports_cjk(font_name) for font_name in REQUESTED_FONT_NAMES + fallback_fonts):
    for font_name, url in OPEN_FONT_DOWNLOAD_URLS.items():
        _download_font(font_name, url)
available_fonts = _available_font_names()
CJK_FONT_NAME = ""
preferred_fonts = []
for font_name in REQUESTED_FONT_NAMES + fallback_fonts:
    if font_name and font_name not in preferred_fonts:
        preferred_fonts.append(font_name)
for font_name in preferred_fonts:
    if font_name in available_fonts and _font_supports_cjk(font_name):
        CJK_FONT_NAME = font_name
        matplotlib.rcParams["font.family"] = "sans-serif"
        matplotlib.rcParams["font.sans-serif"] = [font_name, "DejaVu Sans"]
        break
matplotlib.rcParams["axes.unicode_minus"] = False


def _answer_book_force_cjk_font():
    if not CJK_FONT_NAME:
        return
    matplotlib.rcParams["font.family"] = "sans-serif"
    matplotlib.rcParams["font.sans-serif"] = [CJK_FONT_NAME, "DejaVu Sans"]
    matplotlib.rcParams["axes.unicode_minus"] = False
    try:
        import matplotlib.pyplot as _plt
        from matplotlib.text import Text as _Text
        for number in _plt.get_fignums():
            fig = _plt.figure(number)
            for text_obj in fig.findobj(match=_Text):
                try:
                    if re.search(r"[\u4e00-\u9fff]", text_obj.get_text() or ""):
                        text_obj.set_fontfamily(CJK_FONT_NAME)
                except Exception:
                    pass
    except Exception:
        pass


_answer_book_force_cjk_font()
try:
    import matplotlib.pyplot as _answer_book_plt
    import matplotlib.figure as _answer_book_figure
    _answer_book_original_figure_savefig = _answer_book_figure.Figure.savefig
    _answer_book_original_figure_tight_layout = _answer_book_figure.Figure.tight_layout
    _answer_book_original_pyplot_savefig = _answer_book_plt.savefig
    _answer_book_original_pyplot_tight_layout = _answer_book_plt.tight_layout

    def _answer_book_patched_figure_savefig(self, *args, **kwargs):
        _answer_book_force_cjk_font()
        return _answer_book_original_figure_savefig(self, *args, **kwargs)

    def _answer_book_patched_figure_tight_layout(self, *args, **kwargs):
        _answer_book_force_cjk_font()
        return _answer_book_original_figure_tight_layout(self, *args, **kwargs)

    def _answer_book_patched_pyplot_savefig(*args, **kwargs):
        _answer_book_force_cjk_font()
        return _answer_book_original_pyplot_savefig(*args, **kwargs)

    def _answer_book_patched_pyplot_tight_layout(*args, **kwargs):
        _answer_book_force_cjk_font()
        return _answer_book_original_pyplot_tight_layout(*args, **kwargs)

    _answer_book_figure.Figure.savefig = _answer_book_patched_figure_savefig
    _answer_book_figure.Figure.tight_layout = _answer_book_patched_figure_tight_layout
    _answer_book_plt.savefig = _answer_book_patched_pyplot_savefig
    _answer_book_plt.tight_layout = _answer_book_patched_pyplot_tight_layout
except Exception:
    pass

USER_CODE = {code_json}
namespace = {{}}
exec(USER_CODE, namespace)
draw = namespace.get("draw")
if not callable(draw):
    raise RuntimeError("draw(output_path) function missing")
draw({output_json})
print(json.dumps({{"ok": True, "output": {output_json}, "font": CJK_FONT_NAME, "requested_fonts": REQUESTED_FONT_NAMES, "font_downloads": FONT_DOWNLOAD_LOG}}, ensure_ascii=False))
'''


@dataclass(frozen=True)
class DrawingCodeRunResult:
    ok: bool
    output_path: str
    code_path: str
    issues: list[str]
    stdout: str = ""
    stderr: str = ""
    returncode: int | None = None


def normalize_drawing_mode(value: Any) -> str:
    text = str(value or "").strip().lower()
    if text in {"figure_specs", "figure-specs", "spec", "specs", "schema", "renderer", "规则化", "规则绘图"}:
        return "figure_specs"
    return "code"


def question_drawing_mode(question: dict[str, Any]) -> str:
    plan = question.get("figure_schema_plan") if isinstance(question.get("figure_schema_plan"), dict) else {}
    decision = plan.get("render_decision") if isinstance(plan.get("render_decision"), dict) else {}
    strategy = str(decision.get("strategy") or "").strip()
    if strategy in {"programmatic_renderer", "source_image_overlay"}:
        return "figure_specs"
    if strategy == "model_code_renderer":
        return "code"
    return normalize_drawing_mode(question.get("drawing_generation_mode") or question.get("drawing_mode") or question.get("figure_generation_mode") or "code")


def _attr_name(func: ast.AST) -> str:
    if isinstance(func, ast.Attribute):
        return func.attr
    if isinstance(func, ast.Name):
        return func.id
    return ""


def _literal_strings(
    node: ast.AST,
    assignments: dict[str, ast.AST],
    resolving: set[str] | None = None,
) -> list[str]:
    """Resolve only statically knowable strings used by the style checker.

    Generated plotting code commonly reassigns numeric arrays (for example
    ``x = x.flatten()``). Those expressions are unrelated to text or colors and
    must not be followed as variable aliases.
    """
    resolving = resolving or set()
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return [node.value]
    if isinstance(node, ast.Name):
        if node.id not in assignments or node.id in resolving:
            return []
        return _literal_strings(assignments[node.id], assignments, resolving | {node.id})
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        return _literal_strings(node.left, assignments, resolving) + _literal_strings(node.right, assignments, resolving)
    if isinstance(node, (ast.List, ast.Tuple, ast.Set)):
        return [text for child in node.elts for text in _literal_strings(child, assignments, resolving)]
    if isinstance(node, ast.Dict):
        return [text for child in node.values for text in _literal_strings(child, assignments, resolving)]
    if isinstance(node, ast.JoinedStr):
        return [str(value.value) for value in node.values if isinstance(value, ast.Constant) and isinstance(value.value, str)]
    return []


def _collect_assignments(tree: ast.AST) -> dict[str, ast.AST]:
    assignments: dict[str, ast.AST] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    assignments[target.id] = node.value
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            assignments[node.target.id] = node.value
    return assignments


def _is_gray_hex(value: str) -> bool:
    text = value.strip()
    if not re.fullmatch(r"#[0-9a-fA-F]{3}([0-9a-fA-F]{3})?([0-9a-fA-F]{2})?", text):
        return False
    if len(text) == 4:
        r, g, b = (int(ch * 2, 16) for ch in text[1:4])
    else:
        r, g, b = (int(text[i : i + 2], 16) for i in (1, 3, 5))
    return max(r, g, b) - min(r, g, b) <= 8


def _is_gray_color(value: str) -> bool:
    text = str(value or "").strip().lower()
    if not text:
        return True
    if text in ALLOWED_COLOR_NAMES:
        return True
    if _is_gray_hex(text):
        return True
    if re.fullmatch(r"0?(\.\d+)?|1(\.0+)?", text):
        return True
    return False


def _format_string_has_bad_color(value: str) -> bool:
    text = str(value or "").strip()
    if not text or len(text) > 8:
        return False
    if not any(mark in text for mark in ("-", ":", ".", "o", "s", "^", "v", "x", "+", "*")):
        return False
    return bool(re.search(r"(^|[^A-Za-z])[bgrcmy]([^A-Za-z]|$)", text))


def _format_string_has_gray_color(value: str) -> bool:
    text = str(value or "").strip()
    if not text or len(text) > 8:
        return False
    return bool(re.search(r"(^|[^A-Za-z])[kw]([^A-Za-z]|$)", text))


def _has_cjk(value: str) -> bool:
    return bool(re.search(r"[\u4e00-\u9fff]", value))


def _is_standard_mathtext_notation(value: str) -> bool:
    """Allow compact scientific labels while keeping prose labels subject to CJK checks."""
    text = str(value or "").strip()
    if len(text) < 3 or not (text.startswith("$") and text.endswith("$")):
        return False
    body = text[1:-1].strip()
    if not body or not re.fullmatch(r"[A-Za-z0-9\s\\{}\[\]().,+\-*/^_=<>|:;]+", body):
        return False
    commands = re.findall(r"\\([A-Za-z]+)", body)
    if any(command not in MATH_TEXT_COMMANDS for command in commands):
        return False
    without_commands = re.sub(r"\\[A-Za-z]+", " ", body)
    tokens = [token.lower() for token in re.findall(r"[A-Za-z]+", without_commands)]
    return all(len(token) == 1 or token in ALLOWED_TEXT_TOKENS for token in tokens)


def _text_requires_chinese(value: str) -> bool:
    text = str(value or "").strip()
    if not text or _has_cjk(text) or _is_standard_mathtext_notation(text):
        return False
    # Compact point/phase/axis labels are conventional scientific notation, not
    # English prose. Requiring them to contain CJK rejects valid node, vector,
    # variable, and symbolic labels. Keep the exemption deliberately short so
    # actual English labels still go through the prose policy below.
    compact = re.sub(r"[\s_{}()\[\]+\-*/^=,.:'′″]", "", text)
    if (
        compact
        and not re.search(r"\s", text)
        and len(text) <= 16
        and re.fullmatch(r"[A-Za-z0-9α-ωΑ-Ωθλμσγδ₀-₉Ⅰ-Ⅻ]+", compact)
    ):
        return False
    if re.fullmatch(r"[\s\d\W_+\-*/^=()[\]{}.,;:°θλμÅ]+", text):
        return False
    tokens = [token.lower().strip(".") for token in re.findall(r"[A-Za-z][A-Za-z.]*", text)]
    if not tokens:
        return False
    if any(token in DENIED_ENGLISH_WORDS for token in tokens):
        return True
    return any(token not in ALLOWED_TEXT_TOKENS for token in tokens)


def validate_drawing_code(code: str) -> list[str]:
    issues: list[str] = []
    try:
        tree = ast.parse(code)
    except SyntaxError as exc:
        return [f"syntax error: {exc}"]
    function_defs = [node for node in tree.body if isinstance(node, ast.FunctionDef)]
    if len(function_defs) != 1 or function_defs[0].name != "draw":
        issues.append("code must define exactly one top-level function named draw")
    assignments = _collect_assignments(tree)
    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            names = [alias.name for alias in node.names] if isinstance(node, ast.Import) else [node.module or ""]
            for name in names:
                root = name.split(".", 1)[0]
                if root not in ALLOWED_IMPORT_ROOTS:
                    issues.append(f"import not allowed: {name}")
                elif importlib.util.find_spec(root) is None:
                    issues.append(f"required python package is not installed: {root}")
        elif isinstance(node, ast.Call):
            if isinstance(node.func, ast.Name) and node.func.id in DENIED_CALL_NAMES:
                issues.append(f"call not allowed: {node.func.id}")
            if isinstance(node.func, ast.Attribute) and node.func.attr in DENIED_ATTRS:
                issues.append(f"attribute call not allowed: {node.func.attr}")
            func_name = _attr_name(node.func)
            if func_name in TEXT_METHODS and node.args:
                for text in _literal_strings(node.args[0], assignments):
                    if _text_requires_chinese(text):
                        issues.append(f"figure text should be Chinese or standard notation: {text!r}")
            has_explicit_monochrome = False
            if func_name in {"plot", "scatter", "vlines", "hlines", "bar", "errorbar"}:
                for arg in node.args:
                    for text in _literal_strings(arg, assignments):
                        if _format_string_has_bad_color(text):
                            issues.append(f"do not use color-coded matplotlib format strings: {text!r}")
                        if _format_string_has_gray_color(text):
                            has_explicit_monochrome = True
            for keyword in node.keywords:
                key = keyword.arg or ""
                if key in TEXT_KEYWORDS:
                    for text in _literal_strings(keyword.value, assignments):
                        if _text_requires_chinese(text):
                            issues.append(f"figure text should be Chinese or standard notation: {text!r}")
                if key in COLOR_KEYWORDS:
                    for color in _literal_strings(keyword.value, assignments):
                        if not _is_gray_color(color):
                            issues.append(f"non-monochrome color is not allowed for key {key}: {color!r}")
                        else:
                            has_explicit_monochrome = True
                if key in CMAP_KEYWORDS:
                    for cmap in _literal_strings(keyword.value, assignments):
                        if str(cmap or "").strip().lower() not in ALLOWED_CMAP_NAMES:
                            issues.append(f"non-monochrome colormap is not allowed: {cmap!r}")
                        else:
                            has_explicit_monochrome = True
            if func_name in {"plot", "scatter", "vlines", "hlines", "bar", "errorbar"} and not has_explicit_monochrome:
                issues.append(
                    f"{func_name} must explicitly use a black/white/gray color or format string; "
                    "for example define BLACK = '#111111' and pass color=BLACK"
                )
        elif isinstance(node, ast.Name) and node.id in DENIED_NAMES:
            issues.append(f"name not allowed: {node.id}")
        elif isinstance(node, ast.Attribute) and node.attr.startswith("__"):
            issues.append(f"dunder attribute not allowed: {node.attr}")
        elif isinstance(node, (ast.Global, ast.Nonlocal)):
            issues.append("global/nonlocal statements are not allowed")
    return sorted(set(issues))


def _colorful_visible_ratio(path: Path) -> float:
    try:
        from PIL import Image
    except Exception:
        return 0.0
    img = Image.open(path).convert("RGB")
    width, height = img.size
    sampled = img.resize((min(width, 240), min(height, 240)))
    colorful_pixels = 0
    visible_pixels = 0
    for r, g, b in sampled.getdata():
        if max(r, g, b) > 245 and min(r, g, b) > 235:
            continue
        visible_pixels += 1
        if max(r, g, b) - min(r, g, b) > 35:
            colorful_pixels += 1
    return colorful_pixels / max(1, visible_pixels)


def _visible_content_ratio(path: Path) -> float:
    try:
        from PIL import Image
    except Exception:
        return 1.0
    try:
        img = Image.open(path).convert("RGB")
    except Exception:
        return 0.0
    width, height = img.size
    sampled = img.resize((min(width, 240), min(height, 240)))
    visible_pixels = 0
    for r, g, b in sampled.getdata():
        if max(r, g, b) > 245 and min(r, g, b) > 235:
            continue
        visible_pixels += 1
    return visible_pixels / max(1, sampled.size[0] * sampled.size[1])


def _runtime_stderr_issues(stderr: str) -> list[str]:
    text = str(stderr or "")
    issues: list[str] = []
    if re.search(r"Glyph\s+\d+.*missing from font", text, re.IGNORECASE):
        issues.append("matplotlib could not render some text glyphs; generated image may contain square boxes")
    if re.search(r"findfont:.*Generic family.*not found", text, re.IGNORECASE):
        issues.append("matplotlib could not find the configured font family")
    return issues


def run_drawing_code(code: str, output_path: Path, code_path: Path, *, timeout_seconds: int = 20, cpu_seconds: int = 10, memory_mb: int = 512) -> DrawingCodeRunResult:
    output_path = output_path.resolve()
    code_path = code_path.resolve()
    dependency_issues = _ensure_allowed_python_dependencies(code)
    issues = validate_drawing_code(code)
    issues.extend(dependency_issues)
    if issues:
        return DrawingCodeRunResult(False, str(output_path), str(code_path), issues)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    code_path.parent.mkdir(parents=True, exist_ok=True)
    code_path.write_text(code, encoding="utf-8")
    with tempfile.TemporaryDirectory(prefix="answer_book_drawing_code_") as raw_tmp:
        tmp = Path(raw_tmp)
        runner = tmp / "runner.py"
        runner.write_text(
            RUNNER_TEMPLATE.format(
                code_json=json.dumps(code),
                output_json=json.dumps(str(output_path)),
                requested_fonts_json=json.dumps(_requested_font_families(code)),
                font_cache_dirs_json=json.dumps([str(PROJECT_FONT_CACHE_DIR), str(USER_FONT_CACHE_DIR)]),
                font_download_urls_json=json.dumps(_font_download_urls(), ensure_ascii=False),
                font_sha256_json=json.dumps(_font_sha256_config(), ensure_ascii=False),
                open_font_download_urls_json=json.dumps(OPEN_FONT_DOWNLOAD_URLS, ensure_ascii=False),
                allow_insecure_font_urls_python=repr(_env_flag("ANSWER_BOOK_ALLOW_INSECURE_FONT_URLS")),
                require_font_sha256_python=repr(_env_flag("ANSWER_BOOK_REQUIRE_FONT_SHA256")),
                font_max_bytes=_font_max_bytes(),
                fallback_fonts_json=json.dumps(CJK_FONT_FALLBACK_ORDER, ensure_ascii=False),
                cpu_seconds=int(cpu_seconds),
                memory_bytes=int(memory_mb) * 1024 * 1024,
            ),
            encoding="utf-8",
        )
        env = {
            "MPLBACKEND": "Agg",
            "PATH": os.environ.get("PATH", ""),
            "HOME": os.environ.get("HOME", str(tmp)),
        }
        proc = subprocess.run([sys.executable, str(runner)], cwd=str(tmp), env=env, capture_output=True, text=True, timeout=timeout_seconds)
    run_issues: list[str] = []
    if proc.returncode != 0:
        run_issues.append("drawing subprocess failed")
    run_issues.extend(_runtime_stderr_issues(proc.stderr or ""))
    if not output_path.exists() or output_path.stat().st_size <= 0:
        run_issues.append("drawing code did not write output image")
    if output_path.exists():
        if _visible_content_ratio(output_path) < 0.0005:
            run_issues.append("output image appears blank or nearly blank")
        if _colorful_visible_ratio(output_path) > 0.02:
            run_issues.append("output image uses saturated colors; black-and-white safe output required")
    return DrawingCodeRunResult(
        ok=not run_issues,
        output_path=str(output_path),
        code_path=str(code_path),
        issues=run_issues,
        stdout=(proc.stdout or "")[-4000:],
        stderr=(proc.stderr or "")[-4000:],
        returncode=proc.returncode,
    )


def _tagged_json_and_files(content: str) -> tuple[dict[str, Any], dict[str, str]]:
    text = str(content or "").strip()
    json_match = TAGGED_JSON_RE.search(text)
    files = {name.strip(): body.strip() for name, body in TAGGED_FILE_RE.findall(text) if name.strip()}
    if not json_match:
        return {}, files
    meta = parse_json_content(json_match.group(1))
    return meta, files


def _unwrap_drawing_code_spec(data: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
    if isinstance(data.get("drawing_code_spec"), dict):
        notes = data.get("repair_notes") if isinstance(data.get("repair_notes"), list) else []
        return dict(data["drawing_code_spec"]), [str(note) for note in notes]
    notes = data.get("repair_notes") if isinstance(data.get("repair_notes"), list) else []
    return dict(data), [str(note) for note in notes]


def parse_drawing_code_model_response(content: str) -> tuple[dict[str, Any], list[str]]:
    meta, files = _tagged_json_and_files(content)
    if meta:
        spec, notes = _unwrap_drawing_code_spec(meta)
        code_ref = str(spec.get("code_ref") or spec.get("file") or spec.get("filename") or "").strip()
        code = str(spec.get("code") or "").strip()
        if not code and code_ref and code_ref in files:
            code = files[code_ref]
        if not code and len(files) == 1:
            code = next(iter(files.values()))
        spec["code"] = code
        spec.setdefault("source_protocol", "json_file_blocks")
        return spec, notes
    data = parse_json_content(content)
    spec, notes = _unwrap_drawing_code_spec(data)
    spec.setdefault("source_protocol", "json")
    return spec, notes


def build_drawing_code_prompt(
    question: dict[str, Any],
    fragment: dict[str, Any],
    *,
    previous_issues: list[str] | None = None,
    include_images: bool = False,
) -> list[dict[str, Any]]:
    question = _drawing_prompt_question(question)
    domain_rules = drawing_domain_quality_rules(question)
    payload = {
        "task": "generate_python_matplotlib_drawing_code",
        "question": {
            "question_id": question.get("question_id", ""),
            "number": question.get("number", ""),
            "stem": question.get("stem", ""),
            "subquestions": question.get("subquestions", []),
            "question_understanding": _compact_question_understanding(question),
            "figure_schema_plan": question.get("figure_schema_plan") or {},
        },
        "answer_context": _compact_drawing_answer_context(fragment),
        "previous_issues": previous_issues or [],
        "domain_quality_rules": domain_rules,
        "output_schema": {
            "figure_id": "可选；不填则程序按题号生成",
            "caption": "中文图题",
            "code_ref": "代码文件名，例如 figure.py；代码不要放进 JSON 字符串",
            "notes": "简短说明假设",
        },
        "output_protocol": [
            "Return exactly two blocks and no extra prose.",
            "<JSON>{\"figure_id\":\"...\",\"caption\":\"...\",\"code_ref\":\"figure.py\",\"notes\":\"...\"}</JSON>",
            "<FILE name=\"figure.py\">Python/Matplotlib code defining draw(output_path: str) -> None</FILE>",
            "Do not put Python code inside the JSON block. Put all code only inside the FILE block.",
        ],
        "monochrome_code_template": """BLACK = '#111111'\nGRAY = '#666666'\nax.plot(x, y, color=BLACK, linestyle='-', marker='o')\nax.scatter(x, y, color=BLACK, marker='o')\nax.annotate('标注', xy=(x, y), arrowprops={'color': BLACK})""",
        "hard_rules": [
            "Return the required <JSON> and <FILE> blocks exactly; no markdown fences.",
            "Code must define exactly one function: draw(output_path: str) -> None.",
            "Use matplotlib Agg backend compatible code. You may import matplotlib.pyplot, numpy, math, textwrap.",
            "Do not read files, use network, shell, subprocess, OS APIs, eval, exec, or open.",
            "Use Chinese for explanatory figure text: titles, axis names, legends, and annotations.",
            "Keep variables, units, indices, and other standard scientific notation in their conventional form; do not translate established symbols into prose.",
            "Do not use color as an information channel. The figure must be readable after black-and-white printing.",
            "Use black, white, and gray only; distinguish categories with line styles, markers, hatch patterns, direct labels, vertical offsets, or subplots.",
            "Use the monochrome_code_template pattern: define BLACK/GRAY and pass color=BLACK or color=GRAY explicitly to every plot, scatter, vlines, hlines, bar, errorbar, and annotation arrow. Do not rely on matplotlib default colors.",
            "Legend labels must describe line style or marker meaning in Chinese, not color names.",
            "The drawing must answer the question directly; no decorative background, watermark, JSON text, or code text in the image.",
            "Prefer complete, exam-answer-quality diagrams over minimum viable sketches.",
            "Treat figure_schema_plan.figure_semantic_contract as mandatory: include every required element, label, and relationship, and do not invent forbidden assumptions.",
            "Use question_understanding as the authoritative description of labels, axes, curves, and visible relationships from the original question.",
            "If source_image_policy is preserve_and_overlay, reproduce the original base geometry and add only the requested answer marks; do not replace it with an unrelated generic diagram.",
            *domain_rules,
        ],
    }
    user_content: Any = json.dumps(payload, ensure_ascii=False)
    image_parts = _drawing_image_parts(question) if include_images else []
    if image_parts:
        user_content = [{"type": "text", "text": user_content}, *image_parts]
    return [
        {"role": "system", "content": "你是专业考试作图代码生成器，按 <JSON> 元数据 + <FILE> 源码块协议输出。"},
        {"role": "user", "content": user_content},
    ]


def generate_drawing_code_spec(client: OpenAICompatibleClient, question: dict[str, Any], fragment: dict[str, Any], *, model: str, previous_issues: list[str] | None = None) -> dict[str, Any]:
    messages = build_drawing_code_prompt(
        question,
        fragment,
        previous_issues=previous_issues,
        include_images=bool(getattr(getattr(client, "config", None), "supports_vision", False)),
    )
    with prompt_contract("figure.drawing_code"):
        if hasattr(client, "chat_text"):
            result = client.chat_text(
                messages,
                model=model,
                max_tokens=DRAWING_CODE_MAX_TOKENS,
                timeout=90,
                thinking="disabled",
                task_stage="drawing_code",
                item_ids=[str(question.get("question_id") or fragment.get("question_id") or "")],
                enforce_context_budget=True,
            )
            data, _notes = parse_drawing_code_model_response(result.content)
        else:
            data = client.chat_json_object(
                messages,
                model=model,
                max_tokens=DRAWING_CODE_MAX_TOKENS,
                timeout=90,
                attempts=1,
                thinking="disabled",
                task_stage="drawing_code",
                item_ids=[str(question.get("question_id") or fragment.get("question_id") or "")],
                enforce_context_budget=True,
            )
    code = str(data.get("code") or "").strip()
    if not code:
        raise ValueError("model did not return drawing code")
    qid = str(question.get("question_id") or fragment.get("question_id") or "").strip()
    return {
        "figure_id": str(data.get("figure_id") or f"{qid}_code_fig_01").strip(),
        "question_id": qid,
        "kind": "model_drawing_code",
        "caption": str(data.get("caption") or "题目图示").strip(),
        "code": code,
        "notes": str(data.get("notes") or "").strip(),
        "source": "model_retry",
    }
