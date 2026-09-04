#!/bin/zsh
cd "$(dirname "$0")"
PYTHON_BIN="$(command -v python3.11 || command -v python3)"
if [[ -z "$PYTHON_BIN" ]] || ! "$PYTHON_BIN" -c 'import sys; raise SystemExit(0 if sys.version_info[:2] == (3, 11) else 1)' >/dev/null 2>&1; then
  osascript -e 'display dialog "未检测到 Python 3.11。其他 Python 版本可以保留；请并排安装 Python 3.11，然后再次双击启动。" buttons {"打开下载页面", "退出"} default button "打开下载页面" with title "真题解析与生题平台"' >/dev/null 2>&1
  if [[ $? -eq 0 ]]; then
    open "https://www.python.org/downloads/macos/"
  fi
  exit 2
fi
nohup "$PYTHON_BIN" scripts/source_launcher_gui.py >/dev/null 2>&1 &
exit 0
