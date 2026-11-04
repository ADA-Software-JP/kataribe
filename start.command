#!/bin/bash
# Starts Kataribe (macOS). Closing the terminal ends the app. (Kataribe を起動する)
cd "$(dirname "$0")" || exit 1
if ! command -v python3 >/dev/null 2>&1; then
  echo "python3 was not found. Install it from https://www.python.org/ (python3 が見つかりません)"
  read -r -p "Press Enter to exit (Enter で終了)"
  exit 1
fi
exec python3 server.py
