from pathlib import Path
from unittest.mock import patch

import pytest

from app import storage_cleanup
from app.textbook_package import resolve_package_asset


@pytest.mark.parametrize("reference", ["../outside.png", "images/../../outside.png", r"..\outside.png", r"C:\outside.png"])
def test_asset_traversal_is_rejected(tmp_path: Path, reference: str) -> None:
    root = tmp_path / "package"
    root.mkdir()
    with pytest.raises(ValueError, match="越界"):
        resolve_package_asset(root, None, None, reference)


def test_absolute_asset_must_remain_inside_package(tmp_path: Path) -> None:
    root = tmp_path / "package"
    root.mkdir()
    outside = tmp_path / "outside.png"
    outside.write_bytes(b"not-user-data")
    with pytest.raises(ValueError, match="越界"):
        resolve_package_asset(root, None, None, str(outside))
    inside = root / "inside.png"
    inside.write_bytes(b"test")
    assert resolve_package_asset(root, None, None, str(inside)) == inside.resolve()


def test_symlink_asset_escape_is_rejected(tmp_path: Path) -> None:
    root = tmp_path / "package"
    root.mkdir()
    outside = tmp_path / "outside.png"
    outside.write_bytes(b"test")
    link = root / "link.png"
    try:
        link.symlink_to(outside)
    except OSError:
        pytest.skip("symlinks unavailable on this host")
    with pytest.raises(ValueError, match="越界"):
        resolve_package_asset(root, None, None, "link.png")


def test_empty_cleanup_selection_deletes_nothing(tmp_path: Path) -> None:
    with patch.object(storage_cleanup, "ensure_project_dirs"), patch.object(
        storage_cleanup, "storage_overview", return_value={"areas": [{"kind": "textbook_packages", "entries": [
            {"id": "cache", "deletable": True, "size_bytes": 12}]}]}
    ), patch.object(storage_cleanup, "PACKAGE_CACHE_DIR", tmp_path), patch.object(storage_cleanup.shutil, "rmtree") as delete:
        storage_cleanup.cleanup_storage("textbook_packages", [])
        delete.assert_not_called()


def test_historical_package_asset_rechecked_before_read(tmp_path: Path) -> None:
    from app.evidence_selection import _candidate_visual_parts

    root = tmp_path / "package"
    root.mkdir()
    outside = tmp_path / "outside.png"
    outside.write_bytes(b"test")
    with pytest.raises(ValueError, match="越界"):
        _candidate_visual_parts([{"source_type": "figure_block", "source_file": str(root / "book_content_list.json"), "asset_path": str(outside)}])
