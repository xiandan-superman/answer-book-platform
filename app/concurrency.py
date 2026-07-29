from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Callable, Iterable, TypeVar


T = TypeVar("T")
R = TypeVar("R")


def run_limited_concurrent(
    items: Iterable[T],
    worker: Callable[[T], R],
    *,
    max_workers: int = 1,
    on_complete: Callable[[int, T, R], None] | None = None,
) -> list[R]:
    values = list(items)
    if not values:
        return []
    workers = max(1, min(int(max_workers or 1), len(values)))
    if workers == 1:
        results = []
        for index, item in enumerate(values):
            result = worker(item)
            if on_complete:
                on_complete(index, item, result)
            results.append(result)
        return results

    results: list[R | None] = [None] * len(values)
    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = {
            executor.submit(worker, item): (index, item)
            for index, item in enumerate(values)
        }
        for future in as_completed(futures):
            index, item = futures[future]
            result = future.result()
            results[index] = result
            if on_complete:
                on_complete(index, item, result)
    return [result for result in results if result is not None]
