#!/bin/zsh
set -e

reviewer_dir="$(cd "$(dirname "$0")" && pwd)"
project_dir="$(cd "$reviewer_dir/.." && pwd)"
cd "$project_dir"

reviewer_python="python3"
if ! "$reviewer_python" -c "import docx" >/dev/null 2>&1; then
  echo "缺少 python-docx 依赖，请先在项目根目录执行：python3 -m pip install -r requirements.txt"
  read -r "?按回车键退出…"
  exit 1
fi

(sleep 1; open "http://127.0.0.1:8788") &
exec "$reviewer_python" -m standalone_word_format_reviewer.server --port 8788
