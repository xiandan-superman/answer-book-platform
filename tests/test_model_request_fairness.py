from __future__ import annotations

import json
import os
import threading
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from unittest.mock import patch

from app.concurrency import ModelRequestAborted, model_request_context, model_request_slot, model_request_snapshot
from app.llm_client import LLMError, OpenAICompatibleClient
from app.runtime_monitor import model_call_context
from app.settings import ProviderConfig


@dataclass(frozen=True)
class _ProviderIdentity:
    name: str
    base_url: str


class _Response:
    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self) -> bytes:
        return json.dumps({"choices": [{"message": {"content": "ok"}, "finish_reason": "stop"}]}).encode("utf-8")


def _wait_until(predicate, timeout: float = 2.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.005)
    raise AssertionError("condition was not reached before timeout")


class ModelRequestFairnessTests(unittest.TestCase):
    def test_lingsuan_six_slots_are_shared_across_workflows_and_protocols(self) -> None:
        url = "https://lingsuan-six-shared.invalid/v1"
        providers = [_ProviderIdentity(name, url) for name in ("lingsuan_google", "lingsuan_openai")]
        release = threading.Event()
        entered = [threading.Event() for _ in range(7)]
        owners = ("exam-task", "question-practice-task", "knowledge-practice-task")

        def request(index: int) -> None:
            with model_request_context(owners[index % 3]):
                with model_request_slot(providers[index % 2]):
                    entered[index].set()
                    self.assertTrue(release.wait(5.0))

        def gate_state():
            return next(row for row in model_request_snapshot()["providers"] if row["base_url"] == url)

        with patch.dict(os.environ, {}, clear=True):
            with ThreadPoolExecutor(max_workers=7) as executor:
                futures = []
                try:
                    futures = [executor.submit(request, index) for index in range(6)]
                    for event in entered[:6]:
                        self.assertTrue(event.wait(2.0))
                    futures.append(executor.submit(request, 6))
                    _wait_until(lambda: gate_state()["waiting"] == 1)
                    self.assertEqual(6, gate_state()["active"])
                    self.assertEqual(6, gate_state()["limit"])
                    self.assertFalse(entered[6].is_set())
                finally:
                    release.set()
                for future in futures:
                    future.result(timeout=2.0)
        self.assertTrue(entered[6].is_set())
        self.assertEqual(0, gate_state()["active"])
        self.assertEqual(0, gate_state()["waiting"])

    def test_bigmodel_default_ceiling_is_shared_across_tasks(self) -> None:
        provider = _ProviderIdentity("bigmodel", "https://bigmodel-concurrency.invalid/v1")
        active = 0
        maximum_active = 0
        lock = threading.Lock()

        def request(index: int) -> None:
            nonlocal active, maximum_active
            with model_request_context(f"glm-task-{index}"):
                with model_request_slot(provider):
                    with lock:
                        active += 1
                        maximum_active = max(maximum_active, active)
                    time.sleep(0.025)
                    with lock:
                        active -= 1

        with patch.dict(os.environ, {}, clear=True):
            with ThreadPoolExecutor(max_workers=8) as executor:
                list(executor.map(request, range(8)))

        self.assertEqual(2, maximum_active)

    def test_bigmodel_429_pauses_other_waiting_requests(self) -> None:
        provider = _ProviderIdentity("bigmodel", "https://bigmodel-cooldown.invalid/v1")
        entered = threading.Event()

        with (
            patch.dict(
                os.environ,
                {
                    "BIGMODEL_REQUEST_MAX_CONCURRENCY": "1",
                    "BIGMODEL_RATE_LIMIT_BASE_SECONDS": "0.25",
                    "BIGMODEL_RATE_LIMIT_CAP_SECONDS": "0.25",
                },
                clear=True,
            ),
            patch("app.concurrency.random.uniform", return_value=0.0),
        ):
            with self.assertRaises(LLMError):
                with model_request_slot(provider):
                    raise LLMError("Provider HTTP 429: slow down", status_code=429)

            row = next(
                item
                for item in model_request_snapshot()["providers"]
                if item["base_url"] == provider.base_url
            )
            self.assertEqual(1, row["limit"])
            self.assertEqual(1, row["rate_limited_count"])
            self.assertGreater(row["cooldown_remaining_seconds"], 0)

            def wait_for_slot() -> None:
                with model_request_slot(provider):
                    entered.set()

            started = time.monotonic()
            waiter = threading.Thread(target=wait_for_slot)
            waiter.start()
            time.sleep(0.05)
            self.assertFalse(entered.is_set())
            waiter.join(1.0)
            self.assertTrue(entered.is_set())
            self.assertGreaterEqual(time.monotonic() - started, 0.22)

    def test_bigmodel_quota_429_does_not_start_rate_limit_cooldown(self) -> None:
        provider = _ProviderIdentity("bigmodel", "https://bigmodel-quota.invalid/v1")
        with patch.dict(os.environ, {"BIGMODEL_REQUEST_MAX_CONCURRENCY": "1"}, clear=True):
            with self.assertRaises(LLMError):
                with model_request_slot(provider):
                    raise LLMError(
                        "Provider HTTP 429: insufficient_quota: credit balance is empty",
                        status_code=429,
                    )

        row = next(
            item
            for item in model_request_snapshot()["providers"]
            if item["base_url"] == provider.base_url
        )
        self.assertEqual(0, row["rate_limited_count"])
        self.assertEqual(0, row["cooldown_remaining_seconds"])

    def test_waiting_user_tasks_are_admitted_round_robin(self) -> None:
        provider = _ProviderIdentity("fairness-provider", "https://fairness.invalid/v1")
        first_entered = threading.Event()
        release_first = threading.Event()
        order: list[str] = []
        order_lock = threading.Lock()

        def request(owner: str, first: bool = False) -> None:
            with model_request_context(owner):
                with model_request_slot(provider):
                    with order_lock:
                        order.append(owner)
                    if first:
                        first_entered.set()
                        self.assertTrue(release_first.wait(2.0))
                    else:
                        time.sleep(0.015)

        with patch.dict(os.environ, {"MODEL_REQUEST_MAX_CONCURRENCY": "1"}):
            with ThreadPoolExecutor(max_workers=5) as executor:
                first = executor.submit(request, "exam-task", True)
                self.assertTrue(first_entered.wait(1.0))
                exam_waiters = [executor.submit(request, "exam-task") for _ in range(3)]
                _wait_until(lambda: model_request_snapshot()["waiting"] >= 3)
                practice = executor.submit(request, "practice-task")
                _wait_until(lambda: model_request_snapshot()["waiting"] >= 4)
                release_first.set()
                first.result(timeout=2.0)
                practice.result(timeout=2.0)
                for waiter in exam_waiters:
                    waiter.result(timeout=2.0)

        self.assertEqual("exam-task", order[0])
        self.assertEqual("practice-task", order[2])

    def test_limit_change_does_not_create_a_second_independent_semaphore(self) -> None:
        provider = _ProviderIdentity("dynamic-provider", "https://dynamic.invalid/v1")
        first_entered = threading.Event()
        release_first = threading.Event()
        second_entered = threading.Event()

        def first_request() -> None:
            with model_request_slot(provider):
                first_entered.set()
                self.assertTrue(release_first.wait(2.0))

        def second_request() -> None:
            with model_request_slot(provider):
                second_entered.set()

        with patch.dict(os.environ, {"MODEL_REQUEST_MAX_CONCURRENCY": "2"}):
            first = threading.Thread(target=first_request)
            first.start()
            self.assertTrue(first_entered.wait(1.0))
            with patch.dict(os.environ, {"MODEL_REQUEST_MAX_CONCURRENCY": "1"}):
                second = threading.Thread(target=second_request)
                second.start()
                time.sleep(0.05)
                self.assertFalse(second_entered.is_set())
                release_first.set()
                first.join(1.0)
                second.join(1.0)
                self.assertTrue(second_entered.is_set())

    def test_client_boundary_limits_calls_without_business_layer_guard(self) -> None:
        provider = ProviderConfig(
            name="central-boundary-provider",
            type="openai_compatible",
            base_url="https://central-boundary.invalid/v1",
            api_key="test-key",
            default_model="test-model",
            model_options=("test-model",),
            allow_custom_model=False,
            model_hint="",
            temperature=0.1,
            max_tokens=100,
        )
        active = 0
        maximum_active = 0
        first_wait_claimed = False
        overlap_observed = threading.Event()
        callers_ready = threading.Barrier(8)
        lock = threading.Lock()

        def make_call(index: int) -> str:
            nonlocal active, maximum_active, first_wait_claimed
            client = OpenAICompatibleClient(provider)

            def fake_urlopen(_request, timeout):
                nonlocal active, maximum_active, first_wait_claimed
                wait_for_overlap = False
                with lock:
                    active += 1
                    maximum_active = max(maximum_active, active)
                    if active >= 2:
                        overlap_observed.set()
                    elif not first_wait_claimed:
                        first_wait_claimed = True
                        wait_for_overlap = True
                if wait_for_overlap:
                    overlap_observed.wait(1.0)
                time.sleep(0.005)
                with lock:
                    active -= 1
                return _Response()

            client._urlopen = fake_urlopen
            callers_ready.wait(2.0)
            with model_call_context(task_id=f"task-{index}"):
                return client.chat_text([{"role": "user", "content": "ping"}], timeout=1).content

        with patch.dict(os.environ, {"MODEL_REQUEST_MAX_CONCURRENCY": "2"}):
            with ThreadPoolExecutor(max_workers=8) as executor:
                results = list(executor.map(make_call, range(8)))

        self.assertEqual(["ok"] * 8, results)
        self.assertEqual(2, maximum_active)

    def test_cancelled_task_is_rechecked_after_wait_and_spends_no_model_request(self) -> None:
        provider = _ProviderIdentity("cancel-provider", "https://cancel.invalid/v1")
        holder_entered = threading.Event()
        release_holder = threading.Event()
        cancelled = threading.Event()
        request_body_entered = threading.Event()
        observed: list[type[BaseException]] = []

        def hold_slot() -> None:
            with model_request_context("exam-task"):
                with model_request_slot(provider):
                    holder_entered.set()
                    self.assertTrue(release_holder.wait(2.0))

        def ensure_not_cancelled() -> None:
            if cancelled.is_set():
                raise ModelRequestAborted("用户取消任务")

        def cancelled_waiter() -> None:
            try:
                with model_request_context("practice-task", admission_check=ensure_not_cancelled):
                    with model_request_slot(provider):
                        request_body_entered.set()
            except BaseException as exc:
                observed.append(type(exc))

        with patch.dict(os.environ, {"MODEL_REQUEST_MAX_CONCURRENCY": "1"}):
            holder = threading.Thread(target=hold_slot)
            holder.start()
            self.assertTrue(holder_entered.wait(1.0))
            waiter = threading.Thread(target=cancelled_waiter)
            waiter.start()
            _wait_until(lambda: model_request_snapshot()["waiting"] >= 1)
            cancelled.set()
            release_holder.set()
            holder.join(1.0)
            waiter.join(1.0)

        self.assertEqual([ModelRequestAborted], observed)
        self.assertFalse(request_body_entered.is_set())

    def test_client_guard_is_reentrant_with_existing_business_guard(self) -> None:
        provider = ProviderConfig(
            name="reentrant-provider",
            type="openai_compatible",
            base_url="https://reentrant.invalid/v1",
            api_key="test-key",
            default_model="test-model",
            model_options=("test-model",),
            allow_custom_model=False,
            model_hint="",
            temperature=0.1,
            max_tokens=100,
        )
        client = OpenAICompatibleClient(provider)
        client._urlopen = lambda _request, timeout: _Response()
        with patch.dict(os.environ, {"MODEL_REQUEST_MAX_CONCURRENCY": "1"}):
            with model_request_slot(provider):
                result = client.chat_text([{"role": "user", "content": "ping"}], timeout=1)
        self.assertEqual("ok", result.content)


if __name__ == "__main__":
    unittest.main()
