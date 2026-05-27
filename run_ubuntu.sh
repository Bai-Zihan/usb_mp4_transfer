#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
APP_SCRIPT="$SCRIPT_DIR/usb_mp4_transfer_app.py"

if ! command -v python3 >/dev/null 2>&1; then
  echo "python3 not found. Please install Python 3 first."
  echo "Example: sudo apt update && sudo apt install -y python3 python3-tk"
  exit 1
fi

if [ ! -f "$APP_SCRIPT" ]; then
  echo "Cannot find app script: $APP_SCRIPT"
  exit 1
fi

if ! python3 - <<'PY' >/dev/null 2>&1
import tkinter
PY
then
  echo "python3-tk is missing. Install it with:"
  echo "sudo apt update && sudo apt install -y python3-tk"
  exit 1
fi

cd "$SCRIPT_DIR"
exec python3 "$APP_SCRIPT"
