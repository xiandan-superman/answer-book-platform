from __future__ import annotations

import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
INDEX = (ROOT / "web/index.html").read_text(encoding="utf-8")


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


def test_gsap_motion_runtime_loads_before_application_code() -> None:
    assert INDEX.index('src="/vendor/gsap.min.js"') < INDEX.index('src="/motion.js')
    assert INDEX.index('src="/motion.js') < INDEX.index('src="/app.js')
