#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.settings import get_provider, list_providers, resolve_provider_model  # noqa: E402
from app.task_store import TaskRecord, create_task, save_task  # noqa: E402
from app.textbook_index import discover_textbooks  # noqa: E402
from app.textbook_index_cache import prepare_textbook_index_cache  # noqa: E402


def prepare_cli_textbooks(textbooks_dir: str) -> tuple[str, list[str], dict[str, Any]]:
    root = Path(textbooks_dir).expanduser().resolve()
    if not root.is_dir():
        raise ValueError(f"Textbooks directory not found: {root}")
    selected = [str(path.resolve()) for path in discover_textbooks(root)]
    if not selected:
        raise ValueError(f"No supported textbook files found in: {root}")
    status = prepare_textbook_index_cache(selected)
    if not status.get("page_map_ok", True):
        issues = status.get("page_map_issues") or []
        raise ValueError(f"Textbook page map is invalid: {issues[:5]}")
    return str(root), selected, status


def bind_cli_textbooks(record: TaskRecord, textbooks_dir: str, selected: list[str]) -> TaskRecord:
    record.textbooks_dir = textbooks_dir
    record.selected_textbooks = selected
    record.textbook_display_names = {}
    save_task(record)
    return record


def main() -> int:
    providers = list_providers()
    default_provider = get_provider().name
    parser = argparse.ArgumentParser()
    parser.add_argument("--exam", required=True, help="Absolute path to exam DOCX")
    parser.add_argument("--textbooks", required=True, help="Absolute path to textbooks directory")
    parser.add_argument("--provider", default=default_provider, choices=sorted(providers))
    parser.add_argument("--model", default="")
    parser.add_argument("--correctness-provider", default="", choices=["", *sorted(providers)])
    parser.add_argument("--correctness-model", default="")
    args = parser.parse_args()
    provider = get_provider(args.provider)
    textbooks_dir, selected, index_status = prepare_cli_textbooks(args.textbooks)
    model = resolve_provider_model(provider, args.model)
    correctness_provider = get_provider(args.correctness_provider or provider.name)
    correctness_model = resolve_provider_model(
        correctness_provider,
        args.correctness_model or (model if correctness_provider.name == provider.name else None),
    )
    record = create_task(
        args.exam,
        textbooks_dir,
        provider.name,
        model,
        correctness_provider=correctness_provider.name,
        correctness_model=correctness_model,
    )
    record = bind_cli_textbooks(record, textbooks_dir, selected)
    print(
        json.dumps(
            {**record.__dict__, "textbook_index": index_status},
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
