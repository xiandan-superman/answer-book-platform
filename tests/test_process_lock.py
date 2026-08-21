from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from app.process_lock import ProcessLockUnavailable, platform_process_lock

_CHILD_CODE = """
import sys
from pathlib import Path
from app.process_lock import ProcessLockUnavailable, platform_process_lock
try:
    with platform_process_lock(purpose='child', path=Path(sys.argv[1])):
        print('acquired')
except ProcessLockUnavailable as exc:
    print(str(exc), file=sys.stderr)
    raise SystemExit(3)
"""

_HOLDING_CHILD_CODE = """
import sys, time
from pathlib import Path
from app.process_lock import platform_process_lock
with platform_process_lock(purpose='crash-child', path=Path(sys.argv[1])):
    print('acquired', flush=True)
    time.sleep(30)
"""


def _child_attempt(path: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-c", _CHILD_CODE, str(path)],
        check=False,
        capture_output=True,
        text=True,
        timeout=5,
    )


def test_second_process_is_rejected_and_lock_releases_after_owner_exits(tmp_path) -> None:
    lock_path = tmp_path / "platform.lock"
    with platform_process_lock(purpose="test-parent", path=lock_path):
        blocked = _child_attempt(lock_path)
        assert blocked.returncode == 3
        assert "避免重复任务和模型费用" in blocked.stderr
        assert "test-parent" in blocked.stderr

    acquired = _child_attempt(lock_path)
    assert acquired.returncode == 0
    assert acquired.stdout.strip() == "acquired"


def test_same_process_second_lock_is_also_rejected(tmp_path) -> None:
    lock_path = tmp_path / "same-process.lock"
    with platform_process_lock(purpose="first", path=lock_path):
        with pytest.raises(ProcessLockUnavailable):
            with platform_process_lock(purpose="second", path=lock_path):
                pass


def test_abrupt_process_exit_releases_lock_for_restart(tmp_path) -> None:
    lock_path = tmp_path / "crash-release.lock"
    child = subprocess.Popen(
        [sys.executable, "-c", _HOLDING_CHILD_CODE, str(lock_path)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        assert child.stdout is not None
        assert child.stdout.readline().strip() == "acquired"
        child.kill()
        child.wait(timeout=5)
        with platform_process_lock(purpose="restarted-server", path=lock_path):
            pass
    finally:
        if child.poll() is None:
            child.kill()
            child.wait(timeout=5)


def test_official_cli_stops_before_loading_task_when_server_lock_is_held(tmp_path) -> None:
    lock_path = tmp_path / "cli.lock"
    environment = {**os.environ, "PLATFORM_INSTANCE_LOCK_PATH": str(lock_path)}
    with platform_process_lock(purpose="web-server", path=lock_path):
        result = subprocess.run(
            [sys.executable, "scripts/run_task.py", "task-that-must-not-be-loaded"],
            check=False,
            capture_output=True,
            text=True,
            timeout=10,
            env=environment,
        )

    assert result.returncode == 2
    assert "避免重复任务和模型费用" in result.stderr
    assert "task-that-must-not-be-loaded" not in result.stderr
