from __future__ import annotations

import os
import shutil
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
    return _default_user_data_root().resolve()


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


def _copy_tree_without_overwrite(source: Path, target: Path) -> None:
    """Merge legacy user data while preserving every existing destination file."""
    target.mkdir(parents=True, exist_ok=True)
    for root, directory_names, file_names in os.walk(source, followlinks=False):
        root_path = Path(root)
        directory_names[:] = [
            name for name in directory_names if not (root_path / name).is_symlink()
        ]
        relative = root_path.relative_to(source)
        destination_root = target / relative
        destination_root.mkdir(parents=True, exist_ok=True)
        for name in file_names:
            source_file = root_path / name
            destination_file = destination_root / name
            if source_file.is_symlink() or destination_file.exists():
                continue
            shutil.copy2(source_file, destination_file)


def _migrate_legacy_source_data() -> None:
    """Copy pre-0.10 source-checkout data out of the replaceable code tree."""
    if getattr(sys, "frozen", False):
        return
    if os.environ.get("ANSWER_BOOK_DATA_DIR") and os.environ.get("ANSWER_BOOK_LAUNCHED_BY_SUPERVISOR") != "1":
        return
    if DATA_ROOT == PROJECT_ROOT:
        return
    marker = DATA_ROOT / "runtime" / "source_data_migration_v1.done"
    if marker.exists():
        return
    DATA_ROOT.mkdir(parents=True, exist_ok=True)
    for name in (
        "tasks", "outputs", "logs", "cache", "practice_history", "practice_jobs",
        "model_diagnostics", "support_reports", "textbooks", "exams",
    ):
        source = PROJECT_ROOT / name
        target = DATA_ROOT / name
        if source.is_dir():
            _copy_tree_without_overwrite(source, target)
    legacy_config = PROJECT_ROOT / "config"
    target_config = DATA_ROOT / "config"
    target_config.mkdir(parents=True, exist_ok=True)
    for name in (
        "api_keys.json", "providers.local.json", "support_reporting.json",
        "support_cloud.json", "hybrid_cloud.json", "remote_monitor.local.json",
    ):
        source = legacy_config / name
        target = target_config / name
        if source.is_file() and not target.exists():
            shutil.copy2(source, target)
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_text("legacy source data copied; original files retained\n", encoding="utf-8")


def ensure_project_dirs() -> None:
    _migrate_legacy_source_data()
    for path in (DATA_ROOT, LOCAL_CONFIG_DIR, TASKS_DIR, OUTPUTS_DIR, TEXTBOOKS_DIR, EXAMS_DIR, LOGS_DIR, CACHE_DIR, SHARED_TEXTBOOK_LIBRARY_DIR):
        path.mkdir(parents=True, exist_ok=True)
