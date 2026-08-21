from __future__ import annotations

import sys

from scripts import run_platform


def test_keyboard_interrupt_is_a_clean_shutdown(monkeypatch, capsys) -> None:
    monkeypatch.setattr(
        run_platform,
        "run",
        lambda *_args: (_ for _ in ()).throw(KeyboardInterrupt()),
    )
    monkeypatch.setattr(sys, "argv", ["run_platform.py"])

    assert run_platform.main() == 0
    assert "平台已停止" in capsys.readouterr().out
