from __future__ import annotations

from app.http_errors import public_error_payload


def test_server_get_handler_has_the_same_structured_error_boundary_as_post() -> None:
    from pathlib import Path

    server = (Path(__file__).resolve().parents[1] / "app" / "server.py").read_text(encoding="utf-8")
    get_start = server.index("    def do_GET(self) -> None:")
    get_end = server.index("    def _do_GET(self) -> None:", get_start)
    wrapper = server[get_start:get_end]

    assert "except FileNotFoundError as exc:" in wrapper
    assert "except ValueError as exc:" in wrapper
    assert "public_error_payload(exc, status=404" in wrapper
    assert "public_error_payload(exc, status=400" in wrapper
    assert "public_error_payload(exc, status=500" in wrapper


def test_internal_format_error_is_not_returned_to_user() -> None:
    payload = public_error_payload(
        ValueError("Invalid format specifier 'x' for object of type 'str'"),
        status=500,
        path="/api/practice",
    )

    assert payload["error_code"] == "internal_error"
    assert "Invalid format specifier" not in payload["error"]
    assert payload["support_id"]


def test_safe_chinese_validation_message_remains_actionable() -> None:
    payload = public_error_payload(ValueError("请至少选择一本教材。"), status=400, path="/api/tasks")

    assert payload["error_code"] == "invalid_request"
    assert payload["error"] == "请至少选择一本教材。"
