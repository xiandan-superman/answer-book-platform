from app.ocr_corrections import apply_declared_ocr_corrections


def test_model_declared_coordinate_correction_is_pending_without_source_span() -> None:
    fragment = {
        "warnings": ["题目中坐标(12,12,12)应为(1/2,1/2,1/2)的OCR错误，解析按正确坐标处理。"],
        "blocks": [{"label": "解析", "segments": [{"type": "text", "text": "B 位于（12,12,12）位置。"}]}],
        "subquestions": [{"stem": "B 位于(12,12,12)位置。"}],
    }

    corrections = apply_declared_ocr_corrections(fragment)

    assert corrections == []
    assert fragment["blocks"][0]["segments"][0]["text"] == "B 位于（12,12,12）位置。"
    assert fragment["subquestions"][0]["stem"] == "B 位于(12,12,12)位置。"
    assert fragment["_meta"]["declared_ocr_corrections_pending"] == [
        {"source": "12,12,12", "target": "1/2,1/2,1/2"}
    ]


def test_verified_coordinate_correction_changes_only_the_bound_span() -> None:
    text = "B 位于（12,12,12）位置；不要改写说明中的12,12,12。"
    start = text.index("12,12,12")
    fragment = {
        "warnings": ["题目中坐标(12,12,12)应为(1/2,1/2,1/2)的OCR错误。"],
        "blocks": [{"segments": [{"type": "text", "text": text}]}],
        "_meta": {
            "verified_ocr_corrections": [
                {
                    "source": "12,12,12",
                    "target": "1/2,1/2,1/2",
                    "path": ["blocks", 0, "segments", 0, "text"],
                    "start": start,
                    "end": start + len("12,12,12"),
                }
            ]
        },
    }

    corrections = apply_declared_ocr_corrections(fragment)

    assert corrections == [{"source": "12,12,12", "target": "1/2,1/2,1/2"}]
    assert fragment["blocks"][0]["segments"][0]["text"] == "B 位于（1/2,1/2,1/2）位置；不要改写说明中的12,12,12。"


def test_no_declared_correction_means_no_mutation() -> None:
    fragment = {"blocks": [{"segments": [{"type": "text", "text": "坐标(12,12,12)"}]}]}
    assert apply_declared_ocr_corrections(fragment) == []
    assert fragment["blocks"][0]["segments"][0]["text"] == "坐标(12,12,12)"
