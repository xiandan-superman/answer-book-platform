import json
import zipfile

from PIL import Image
from lxml import etree

from app.docx_v4 import build_docx_from_fragments
from app.pipeline import attach_source_images_to_fragments


def _fragment() -> dict:
    return {"schema_version": "answer_book.answer_fragment.v4", "question_id": "q1", "answer": "A", "blocks": [{"label": "教材依据", "segments": [{"type": "text", "text": "依据"}]}], "formulas": [], "evidence_ids": []}


def test_original_question_image_is_attached_once(tmp_path) -> None:
    image = tmp_path / "source.png"
    image.write_bytes(b"not-rendered-in-this-unit-test")
    fragments_path = tmp_path / "answer_fragments.json"
    fragments_path.write_text(json.dumps({"fragments": [_fragment()]}), encoding="utf-8")
    exam = {"items": [{"question_id": "q1", "image_refs": [str(image)]}]}
    first = attach_source_images_to_fragments(exam, fragments_path)
    second = attach_source_images_to_fragments(exam, fragments_path)
    data = json.loads(fragments_path.read_text(encoding="utf-8"))
    images = [segment for block in data["fragments"][0]["blocks"] for segment in block["segments"] if segment.get("type") == "image_ref"]
    assert first["attached_question_ids"] == ["q1"]
    assert second["attached_question_ids"] == ["q1"]
    assert len(images) == 1 and images[0]["role"] == "source_question_image"


def test_source_image_can_be_restored_after_full_fragment_repair(tmp_path) -> None:
    image = tmp_path / "source.png"
    image.write_bytes(b"source")
    fragments_path = tmp_path / "answer_fragments.json"
    fragments_path.write_text(json.dumps({"fragments": [_fragment()]}), encoding="utf-8")
    exam = {"items": [{"question_id": "q1", "image_refs": [str(image)]}]}

    attach_source_images_to_fragments(exam, fragments_path)
    repaired = json.loads(fragments_path.read_text(encoding="utf-8"))
    repaired["fragments"][0] = _fragment()  # model repair returned a complete replacement
    fragments_path.write_text(json.dumps(repaired), encoding="utf-8")

    attach_source_images_to_fragments(exam, fragments_path)
    restored = json.loads(fragments_path.read_text(encoding="utf-8"))["fragments"][0]
    source_images = [
        segment
        for block in restored["blocks"]
        for segment in block["segments"]
        if segment.get("role") == "source_question_image"
    ]

    assert len(source_images) == 1
    assert source_images[0]["path"] == str(image)


def test_recovered_source_image_clears_only_stale_missing_image_warning(tmp_path) -> None:
    image = tmp_path / "source.png"
    image.write_bytes(b"source")
    fragment = _fragment()
    fragment["warnings"] = [
        "原题未抽取到图片，按题干文字生成图示规格。",
        "仍需核对材料牌号。",
    ]
    fragments_path = tmp_path / "answer_fragments.json"
    fragments_path.write_text(json.dumps({"fragments": [fragment]}), encoding="utf-8")

    delivery = attach_source_images_to_fragments(
        {"items": [{"question_id": "q1", "image_refs": [str(image)]}]},
        fragments_path,
    )
    restored = json.loads(fragments_path.read_text(encoding="utf-8"))["fragments"][0]

    assert restored["warnings"] == ["仍需核对材料牌号。"]
    assert delivery["reconciled_warning_question_ids"] == ["q1"]


def test_missing_source_image_does_not_clear_missing_image_warning(tmp_path) -> None:
    image = tmp_path / "missing.png"
    fragment = _fragment()
    fragment["warnings"] = ["原题未抽取到图片，按题干文字生成图示规格。"]
    fragments_path = tmp_path / "answer_fragments.json"
    fragments_path.write_text(json.dumps({"fragments": [fragment]}), encoding="utf-8")

    delivery = attach_source_images_to_fragments(
        {"items": [{"question_id": "q1", "image_refs": [str(image)]}]},
        fragments_path,
    )
    restored = json.loads(fragments_path.read_text(encoding="utf-8"))["fragments"][0]

    assert restored["warnings"] == ["原题未抽取到图片，按题干文字生成图示规格。"]
    assert delivery["reconciled_warning_question_ids"] == []


def test_attached_source_image_is_embedded_in_graphic_question_word(tmp_path) -> None:
    image = tmp_path / "source.png"
    Image.new("RGB", (320, 180), "white").save(image)
    fragment = _fragment()
    fragment.update({"question_type": "作图题", "section": "作图题", "number": "1"})
    fragments_path = tmp_path / "answer_fragments.json"
    fragments_path.write_text(json.dumps({"fragments": [fragment]}), encoding="utf-8")
    attach_source_images_to_fragments({"items": [{"question_id": "q1", "image_refs": [str(image)]}]}, fragments_path)
    output = tmp_path / "answer.docx"
    build_docx_from_fragments(fragments_path, output)
    with zipfile.ZipFile(output) as archive:
        root = etree.fromstring(archive.read("word/document.xml"))
    assert len(root.xpath(".//w:drawing", namespaces={"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"})) == 1
