import json

from app.final_acceptance import figure_delivery_findings


def _write(path, value) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")


def test_figure_delivery_requires_source_and_generated_answer_images(tmp_path) -> None:
    source = tmp_path / "source.png"
    source.write_bytes(b"image")
    _write(tmp_path / "structured_exam.json", {"items": [{"question_id": "q1", "stem": "根据下图画出对应曲线", "image_refs": [str(source)], "source_image_required": True, "answer_figure_required": True}]})
    _write(tmp_path / "answer_fragments.json", {"fragments": [{"question_id": "q1", "blocks": [{"label": "原题图", "segments": [{"type": "image_ref", "path": str(source), "role": "source_question_image"}]}]}]})
    summary, issues, _ = figure_delivery_findings(tmp_path)
    assert summary["ok"] is False and any("没有独立生成图" in issue for issue in issues)
    data = json.loads((tmp_path / "answer_fragments.json").read_text(encoding="utf-8"))
    data["fragments"][0]["blocks"].append({"label": "图示", "segments": [{"type": "image_ref", "path": str(tmp_path / "answer.png")}]})
    _write(tmp_path / "answer_fragments.json", data)
    summary, issues, _ = figure_delivery_findings(tmp_path)
    assert summary["ok"] is True and issues == []
