#!/bin/zsh
set -e
cd "$(dirname "$0")"
python3 scripts/start_platform.py --host 0.0.0.0 --port 8766
