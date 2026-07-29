from __future__ import annotations

import os
import platform
import sys
from pathlib import Path


SOURCE_ROOT = Path(__file__).resolve().parents[1]
IS_FROZEN = bool(getattr(sys, "frozen", False))
RESOURCE_ROOT = Path(getattr(sys, "_MEIPASS", SOURCE_ROOT)).resolve()


def _default_data_root() -> Path:
    configured = str(os.environ.get("ANSWER_BOOK_DATA_DIR") or "").strip()
    if configured:
        return Path(configured).expanduser().resolve()
    if not IS_FROZEN:
        return SOURCE_ROOT
    system = platform.system()
    if system == "Darwin":
        return Path.home() / "Library" / "Application Support" / "Answer Book Platform"
    if system == "Windows":
        base = Path(os.environ.get("LOCALAPPDATA") or (Path.home() / "AppData" / "Local"))
        return base / "Answer Book Platform"
    return Path(os.environ.get("XDG_DATA_HOME") or (Path.home() / ".local" / "share")) / "answer-book-platform"


# PROJECT_ROOT remains the read-only application resource root for compatibility.
# Runtime/user content is always stored under DATA_ROOT in a frozen desktop build.
PROJECT_ROOT = RESOURCE_ROOT
DATA_ROOT = _default_data_root()
CONFIG_DIR = RESOURCE_ROOT / "config"
LOCAL_CONFIG_DIR = DATA_ROOT / "config"
WEB_DIR = RESOURCE_ROOT / "web"
TASKS_DIR = DATA_ROOT / "tasks"
OUTPUTS_DIR = DATA_ROOT / "outputs"
TEXTBOOKS_DIR = DATA_ROOT / "textbooks"
EXAMS_DIR = DATA_ROOT / "exams"
LOGS_DIR = DATA_ROOT / "logs"
CACHE_DIR = DATA_ROOT / "cache"
SHARED_TEXTBOOK_LIBRARY_DIR = CACHE_DIR / "shared_textbook_library"


def ensure_project_dirs() -> None:
    for path in (
        DATA_ROOT,
        LOCAL_CONFIG_DIR,
        TASKS_DIR,
        OUTPUTS_DIR,
        TEXTBOOKS_DIR,
        EXAMS_DIR,
        LOGS_DIR,
        CACHE_DIR,
        SHARED_TEXTBOOK_LIBRARY_DIR,
    ):
        path.mkdir(parents=True, exist_ok=True)
