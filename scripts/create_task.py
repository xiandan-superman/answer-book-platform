#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.settings import get_provider, resolve_provider_model
from app.task_store import create_task


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--exam", required=True, help="Absolute path to exam DOCX")
    parser.add_argument("--textbooks", required=True, help="Absolute path to textbooks directory")
    parser.add_argument("--provider", default="openai", choices=["openai", "deepseek", "ark", "zhipu"])
    parser.add_argument("--model", default="")
    args = parser.parse_args()
    provider = get_provider(args.provider)
    record = create_task(args.exam, args.textbooks, provider.name, resolve_provider_model(provider, args.model))
    print(json.dumps(record.__dict__, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
