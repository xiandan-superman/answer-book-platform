from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
INDEX = (ROOT / "web/index.html").read_text(encoding="utf-8")
MATHJAX_ROOT = ROOT / "web/vendor/mathjax"


def test_frontend_runtime_assets_are_local() -> None:
    runtime_urls = re.findall(r"(?:src|href)=[\"']([^\"']+)[\"']", INDEX)
    assert not [url for url in runtime_urls if url.startswith(("http://", "https://", "//"))]


def test_required_local_runtime_assets_exist() -> None:
    for relative in (
        "web/vendor/gsap.min.js",
        "web/vendor/lucide.min.js",
        "web/vendor/mathjax/tex-mml-chtml.js",
        "web/icon-compat.js",
        "web/platform-api.js",
        "web/task-contract-ui.js",
        "web/styles/foundation.css",
        "web/motion.js",
    ):
        assert (ROOT / relative).is_file(), relative


def test_mathjax_vendor_tree_contains_only_the_runtime_chain() -> None:
    files = {
        path.relative_to(MATHJAX_ROOT).as_posix()
        for path in MATHJAX_ROOT.rglob("*")
        if path.is_file()
    }
    assert {"LICENSE", "tex-mml-chtml.js"} <= files
    assert "input/tex/extensions/boldsymbol.js" in files
    assert "input/tex/extensions/mhchem.js" in files
    font_paths = {path for path in files if path.startswith("output/chtml/fonts/woff-v2/")}
    assert len(font_paths) == 23
    assert "output/chtml/fonts/woff-v2/MathJax_Zero.woff" in font_paths
    assert all(
        path in {"LICENSE", "tex-mml-chtml.js"}
        or path.startswith("input/")
        or path in font_paths
        for path in files
    )
    assert sum(path.stat().st_size for path in MATHJAX_ROOT.rglob("*") if path.is_file()) < 4_000_000


def test_mathjax_loader_waits_for_async_startup_before_typesetting() -> None:
    app_js = (ROOT / "web/app.js").read_text(encoding="utf-8")
    assert "typeset: false" in app_js
    assert "await window.MathJax?.startup?.promise" in app_js
    assert 'typeof window.MathJax?.typesetPromise !== "function"' in app_js


def test_gsap_motion_runtime_loads_before_application_code() -> None:
    assert INDEX.index('src="/vendor/gsap.min.js"') < INDEX.index('src="/motion.js')
    assert INDEX.index('src="/motion.js') < INDEX.index('src="/app.js')
