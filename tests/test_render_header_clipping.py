from __future__ import annotations

from PIL import Image, ImageDraw

from app.render_audit import audit_rendered_pages, audit_rendered_pages_report, inspect_header_clipping


def _page(path, *, header_top: int, header_height: int = 18, rule_y: int | None = None) -> None:
    image = Image.new("RGB", (800, 1100), "white")
    draw = ImageDraw.Draw(image)
    draw.rectangle((220, header_top, 310, header_top + header_height - 1), fill="black")
    draw.rectangle((360, header_top, 450, header_top + header_height - 1), fill="black")
    if rule_y is not None:
        draw.line((100, rule_y, 700, rule_y), fill="black", width=1)
    draw.rectangle((100, 220, 650, 235), fill="black")
    image.save(path)
    image.close()


def test_header_clipping_gate_accepts_header_with_clear_top_margin(tmp_path) -> None:
    for number in range(1, 4):
        _page(tmp_path / f"page-{number}.png", header_top=55)

    report = inspect_header_clipping(sorted(tmp_path.glob("page-*.png")))

    assert report["ok"] is True
    assert audit_rendered_pages(tmp_path) == []
    detailed = audit_rendered_pages_report(tmp_path)
    assert detailed["page_count"] == 3
    assert detailed["header_clipping"]["ok"] is True


def test_header_clipping_gate_blocks_ink_cut_by_physical_page_edge(tmp_path) -> None:
    _page(tmp_path / "page-1.png", header_top=0)

    issues = audit_rendered_pages(tmp_path)

    assert any("physical top edge" in issue for issue in issues)


def test_header_clipping_gate_blocks_text_colliding_with_header_rule(tmp_path) -> None:
    _page(tmp_path / "page-1.png", header_top=40, header_height=20, rule_y=50)

    issues = audit_rendered_pages(tmp_path)

    assert any("horizontal rule" in issue for issue in issues)


def test_header_clipping_gate_ignores_chart_border_below_header(tmp_path) -> None:
    path = tmp_path / "page-1.png"
    _page(path, header_top=40, header_height=20)
    with Image.open(path) as source:
        image = source.copy()
    draw = ImageDraw.Draw(image)
    draw.line((100, 78, 700, 78), fill="black", width=1)
    for x in range(120, 681, 60):
        draw.line((x, 74, x, 90), fill="black", width=1)
    image.save(path)
    image.close()

    report = inspect_header_clipping([path])

    assert report["ok"] is True


def test_header_clipping_gate_blocks_collapsed_repeated_header_band(tmp_path) -> None:
    _page(tmp_path / "page-1.png", header_top=50, header_height=20)
    _page(tmp_path / "page-2.png", header_top=50, header_height=6)
    _page(tmp_path / "page-3.png", header_top=50, header_height=20)

    issues = audit_rendered_pages(tmp_path)

    assert any("repeated header band height" in issue for issue in issues)


def test_render_gate_reports_corrupt_page_without_crashing(tmp_path) -> None:
    (tmp_path / "page-1.png").write_bytes(b"not-a-png")

    issues = audit_rendered_pages(tmp_path)

    assert any("could not be inspected" in issue for issue in issues)
    assert any("header clipping inspection failed" in issue for issue in issues)
