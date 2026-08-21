from __future__ import annotations

import json
import os
import platform
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import BinaryIO, Iterator

from .paths import CACHE_DIR

PLATFORM_INSTANCE_LOCK = CACHE_DIR / "platform_instance.lock"


class ProcessLockUnavailable(RuntimeError):
    pass


def _lock_file(handle: BinaryIO) -> None:
    if os.name == "nt":
        import msvcrt

        handle.seek(0)
        if handle.read(1) == b"":
            handle.seek(0)
            handle.write(b"0")
            handle.flush()
        handle.seek(0)
        msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        return
    import fcntl

    fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)


def _unlock_file(handle: BinaryIO) -> None:
    if os.name == "nt":
        import msvcrt

        handle.seek(0)
        msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        return
    import fcntl

    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def _holder_description(path: Path) -> str:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return ""
    pid = str(data.get("pid") or "").strip()
    purpose = str(data.get("purpose") or "").strip()
    started_at = str(data.get("started_at") or "").strip()
    parts = [value for value in (f"PID {pid}" if pid else "", purpose, started_at) if value]
    return "，".join(parts)


@contextmanager
def platform_process_lock(
    *,
    purpose: str,
    path: Path | None = None,
) -> Iterator[Path]:
    """Prevent supported platform entrypoints from running in two processes."""
    configured_path = str(os.environ.get("PLATFORM_INSTANCE_LOCK_PATH") or "").strip()
    lock_path = Path(path or configured_path or PLATFORM_INSTANCE_LOCK)
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    handle = lock_path.open("a+b")
    try:
        try:
            _lock_file(handle)
        except (BlockingIOError, OSError) as exc:
            holder = _holder_description(lock_path)
            detail = f"（{holder}）" if holder else ""
            raise ProcessLockUnavailable(
                f"平台已有运行实例{detail}。为避免重复任务和模型费用，本次启动已停止。"
            ) from exc
        metadata = {
            "schema_version": 1,
            "pid": os.getpid(),
            "purpose": str(purpose or "platform"),
            "started_at": datetime.now().astimezone().isoformat(timespec="seconds"),
            "host": platform.node(),
        }
        handle.seek(0)
        handle.truncate()
        handle.write(json.dumps(metadata, ensure_ascii=False).encode("utf-8"))
        handle.flush()
        os.fsync(handle.fileno())
        yield lock_path
    finally:
        try:
            _unlock_file(handle)
        except OSError:
            pass
        handle.close()
