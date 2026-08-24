from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCE_RELEASE = (ROOT / ".github" / "workflows" / "source-release.yml").read_text(encoding="utf-8")
QUALITY = (ROOT / ".github" / "workflows" / "quality.yml").read_text(encoding="utf-8")


def test_source_release_requires_full_quality_gate_before_packaging() -> None:
    assert "python scripts/run_quality_gates.py --full" in SOURCE_RELEASE
    assert SOURCE_RELEASE.index("run_quality_gates.py --full") < SOURCE_RELEASE.index("git archive")


def test_source_release_smoke_starts_the_packaged_zip_with_isolated_data() -> None:
    assert "unzip -q" in SOURCE_RELEASE
    assert "ANSWER_BOOK_DATA_DIR" in SOURCE_RELEASE
    assert "/api/version" in SOURCE_RELEASE


def test_quality_matrix_covers_supported_python_profiles_and_browser_smoke() -> None:
    assert 'python-version: ["3.9", "3.11"]' in QUALITY
    assert "constraints-py39.txt" in QUALITY
    assert "constraints-py311.txt" in QUALITY
    assert "playwright install --with-deps chromium" in QUALITY
    assert "tests/e2e/test_platform_smoke.py" in QUALITY
