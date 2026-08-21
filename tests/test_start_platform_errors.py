from __future__ import annotations

import sys

from app.process_lock import ProcessLockUnavailable
from scripts import start_platform


def _ready_environment() -> dict:
    return {"formula_conversion": {"preferred_chain_ready": True}}


def test_duplicate_start_has_plain_chinese_error(monkeypatch, capsys) -> None:
    monkeypatch.setattr(start_platform, "check_environment", _ready_environment)
    monkeypatch.setattr(
        start_platform,
        "run",
        lambda *_args: (_ for _ in ()).throw(ProcessLockUnavailable("平台已有运行实例。为避免重复任务和模型费用，本次启动已停止。")),
    )
    monkeypatch.setattr(sys, "argv", ["start_platform.py"])

    assert start_platform.main() == 2
    assert "避免重复任务和模型费用" in capsys.readouterr().err


def test_port_conflict_says_no_task_recovery_was_started(monkeypatch, capsys) -> None:
    monkeypatch.setattr(start_platform, "check_environment", _ready_environment)

    def fail_bind(*_args):
        error = OSError("address already in use")
        error.errno = 48
        raise error

    monkeypatch.setattr(start_platform, "run", fail_bind)
    monkeypatch.setattr(sys, "argv", ["start_platform.py", "--port", "18766"])

    assert start_platform.main() == 2
    message = capsys.readouterr().err
    assert "端口 18766 已被占用" in message
    assert "没有恢复任务或启动后台工作器" in message


def test_keyboard_interrupt_is_a_clean_shutdown(monkeypatch, capsys) -> None:
    monkeypatch.setattr(start_platform, "check_environment", _ready_environment)
    monkeypatch.setattr(
        start_platform,
        "run",
        lambda *_args: (_ for _ in ()).throw(KeyboardInterrupt()),
    )
    monkeypatch.setattr(sys, "argv", ["start_platform.py"])

    assert start_platform.main() == 0
    assert "平台已停止" in capsys.readouterr().out
