from __future__ import annotations

import os
import sys
from pathlib import Path


def _program_root() -> Path:
    frozen_root = getattr(sys, "_MEIPASS", "") if getattr(sys, "frozen", False) else ""
    return Path(frozen_root).resolve() if frozen_root else Path(__file__).resolve().parents[1]


def _default_user_data_root(*, platform_name: str | None = None, home: Path | None = None) -> Path:
    platform_name = platform_name or sys.platform
    home = home or Path.home()
    if platform_name == "darwin":
        return home / "Library" / "Application Support" / "Answer Book Platform"
    if platform_name.startswith("win"):
        local_app_data = str(os.environ.get("LOCALAPPDATA") or "").strip()
        return (Path(local_app_data) if local_app_data else home / "AppData" / "Local") / "Answer Book Platform"
    xdg_data_home = str(os.environ.get("XDG_DATA_HOME") or "").strip()
    return (Path(xdg_data_home) if xdg_data_home else home / ".local" / "share") / "answer-book-platform"


def _data_root(project_root: Path) -> Path:
    override = str(os.environ.get("ANSWER_BOOK_DATA_DIR") or "").strip()
    if override:
        return Path(override).expanduser().resolve()
    if getattr(sys, "frozen", False):
        return _default_user_data_root().resolve()
    return project_root


PROJECT_ROOT = _program_root()
CONFIG_DIR = PROJECT_ROOT / "config"
DATA_ROOT = _data_root(PROJECT_ROOT)
LOCAL_CONFIG_DIR = DATA_ROOT / "config"
WEB_DIR = PROJECT_ROOT / "web"
TASKS_DIR = DATA_ROOT / "tasks"
OUTPUTS_DIR = DATA_ROOT / "outputs"
TEXTBOOKS_DIR = DATA_ROOT / "textbooks"
EXAMS_DIR = DATA_ROOT / "exams"
LOGS_DIR = DATA_ROOT / "logs"
CACHE_DIR = DATA_ROOT / "cache"
SHARED_TEXTBOOK_LIBRARY_DIR = CACHE_DIR / "shared_textbook_library"


def ensure_project_dirs() -> None:
    for path in (DATA_ROOT, LOCAL_CONFIG_DIR, TASKS_DIR, OUTPUTS_DIR, TEXTBOOKS_DIR, EXAMS_DIR, LOGS_DIR, CACHE_DIR, SHARED_TEXTBOOK_LIBRARY_DIR):
        path.mkdir(parents=True, exist_ok=True)
