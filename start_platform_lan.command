#!/bin/zsh
cd "$(dirname "$0")"
if ! command -v python3 >/dev/null 2>&1; then
  osascript -e 'display dialog "未检测到 Python 3.9 或更高版本。请先安装 Python，然后再次双击启动。" buttons {"打开下载页面", "退出"} default button "打开下载页面" with title "真题解析与生题平台"' >/dev/null 2>&1
  if [[ $? -eq 0 ]]; then
    open "https://www.python.org/downloads/macos/"
  fi
  exit 2
fi
nohup python3 scripts/source_launcher_gui.py --mode lan --autostart >/dev/null 2>&1 &
exit 0
