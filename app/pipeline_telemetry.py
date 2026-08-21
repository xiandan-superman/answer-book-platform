from __future__ import annotations

import json
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any

from .task_store import append_event, load_task, update_task_health

PIPELINE_TELEMETRY_VERSION = "answer_book.pipeline_telemetry.v1"


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


class PipelineRunTelemetry:
    """Own pipeline timing, status persistence and the task-health heartbeat."""

    def __init__(
        self,
        *,
        task_id: str,
        status_path: Path,
        quality_governance: dict[str, Any],
        heartbeat_seconds: int = 10,
    ) -> None:
        self.task_id = task_id
        self.status_path = status_path
        self.heartbeat_seconds = max(1, int(heartbeat_seconds))
        self.started_at = datetime.now().astimezone().isoformat(timespec="seconds")
        self._started_monotonic = time.monotonic()
        self._stage_started: dict[str, float] = {}
        self._heartbeat_finished = threading.Event()
        self.summary: dict[str, Any] = {
            "task_id": task_id,
            "telemetry_version": PIPELINE_TELEMETRY_VERSION,
            "started_at": self.started_at,
            "updated_at": self.started_at,
            "elapsed_seconds": 0.0,
            "stages": [],
            "quality_governance": quality_governance,
        }

    def start_heartbeat(self) -> None:
        threading.Thread(
            target=self._heartbeat,
            name=f"pipeline-heartbeat-{self.task_id}",
            daemon=True,
        ).start()

    def stop(self) -> None:
        self._heartbeat_finished.set()
        ended_at = datetime.now().astimezone().isoformat(timespec="seconds")
        self.summary["ended_at"] = ended_at
        self.summary["updated_at"] = ended_at
        self.summary["elapsed_seconds"] = round(
            max(0.0, time.monotonic() - self._started_monotonic),
            3,
        )
        _write_json(self.status_path, self.summary)

    def _heartbeat(self) -> None:
        while not self._heartbeat_finished.wait(self.heartbeat_seconds):
            try:
                current = load_task(self.task_id)
                if current.status != "running":
                    return
                update_task_health(
                    self.task_id,
                    current_operation=current.current_stage or "正在处理",
                )
            except Exception:
                return

    def mark(self, stage: str, status: str, detail: Any = None) -> None:
        now_monotonic = time.monotonic()
        recorded_at = datetime.now().astimezone().isoformat(timespec="seconds")
        if status == "started":
            self._stage_started[stage] = now_monotonic
        rows = self.summary["stages"]
        row: dict[str, Any] = {
            "event_index": len(rows) + 1,
            "stage": stage,
            "status": status,
            "recorded_at": recorded_at,
            "pipeline_elapsed_seconds": round(
                max(0.0, now_monotonic - self._started_monotonic),
                3,
            ),
            "detail": detail or {},
        }
        started = self._stage_started.get(stage)
        if status != "started" and started is not None:
            row["stage_elapsed_seconds"] = round(max(0.0, now_monotonic - started), 3)
            self._stage_started.pop(stage, None)
        rows.append(row)
        self.summary["updated_at"] = recorded_at
        self.summary["elapsed_seconds"] = row["pipeline_elapsed_seconds"]
        append_event(self.task_id, f"{stage}_{status}", row)
        _write_json(self.status_path, self.summary)
        self._sync_task_health(stage, status, detail)

    def _sync_task_health(self, stage: str, status: str, detail: Any) -> None:
        payload = detail if isinstance(detail, dict) else {}
        # Only the explicit task-progress contract may change the task counter.
        # Domain metrics such as question_count/generated_count can describe
        # different populations (for example questions versus optional figures)
        # and therefore must never be combined implicitly.
        total = payload.get("total")
        completed = payload.get("completed")
        if status not in {"passed", "reused", "completed"} and total is None and completed is None:
            return
        try:
            update_task_health(
                self.task_id,
                current_operation=stage,
                total_count=int(total) if total is not None else None,
                completed_count=int(completed) if completed is not None else None,
                progress=True,
            )
        except Exception:
            pass
