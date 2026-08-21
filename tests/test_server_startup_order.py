from __future__ import annotations

from contextlib import contextmanager

import pytest

from app import server


def _patch_clean_startup(monkeypatch, events: list[str]) -> None:
    @contextmanager
    def fake_lock(*, purpose):
        events.append(f"lock:{purpose}")
        yield
        events.append("unlock")

    class FakeServer:
        def __init__(self, address, _handler):
            events.append(f"bind:{address[0]}:{address[1]}")

        def serve_forever(self):
            events.append("serve")

        def server_close(self):
            events.append("close")

    monkeypatch.setattr(server, "platform_process_lock", fake_lock)
    monkeypatch.setattr(server, "ThreadingHTTPServer", FakeServer)
    monkeypatch.setattr(server, "ensure_project_dirs", lambda: events.append("dirs"))
    monkeypatch.setattr(server, "recover_interrupted_tasks", lambda _reason: events.append("recover_exam") or [])
    monkeypatch.setattr(server, "cleanup_practice_jobs", lambda: events.append("cleanup") or {"removed_count": 0, "removed_bytes": 0})
    monkeypatch.setattr(server, "recover_practice_export_jobs", lambda: events.append("recover_export") or {"resumed": 0, "completed_from_cache": 0, "failed": 0})
    monkeypatch.setattr(server, "recover_practice_jobs", lambda **_kwargs: events.append("recover_practice") or [])
    monkeypatch.setattr(server, "start_practice_queue_consumer", lambda: events.append("consumer_start"))
    monkeypatch.setattr(server, "stop_practice_queue_consumer", lambda: events.append("consumer_stop"))
    monkeypatch.setattr(server, "append_runtime_log", lambda *_args, **_kwargs: None)


def test_server_binds_before_recovery_or_worker_start(monkeypatch) -> None:
    events: list[str] = []
    _patch_clean_startup(monkeypatch, events)

    server.run("127.0.0.1", 18766)

    assert events.index("bind:127.0.0.1:18766") < events.index("recover_exam")
    assert events.index("bind:127.0.0.1:18766") < events.index("consumer_start")
    assert events[-4:] == ["serve", "consumer_stop", "close", "unlock"]


def test_bind_failure_does_not_recover_or_start_workers(monkeypatch) -> None:
    events: list[str] = []

    @contextmanager
    def fake_lock(*, purpose):
        events.append("lock")
        try:
            yield
        finally:
            events.append("unlock")

    def fail_bind(_address, _handler):
        events.append("bind")
        raise OSError("address already in use")

    monkeypatch.setattr(server, "platform_process_lock", fake_lock)
    monkeypatch.setattr(server, "ThreadingHTTPServer", fail_bind)
    monkeypatch.setattr(server, "ensure_project_dirs", lambda: events.append("dirs"))
    monkeypatch.setattr(server, "recover_interrupted_tasks", lambda _reason: events.append("recover") or [])
    monkeypatch.setattr(server, "start_practice_queue_consumer", lambda: events.append("consumer"))

    with pytest.raises(OSError, match="address already in use"):
        server.run("127.0.0.1", 18766)

    assert events == ["dirs", "lock", "bind", "unlock"]
