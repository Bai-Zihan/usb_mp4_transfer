#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

if ! command -v apt >/dev/null 2>&1; then
  echo "This script is for Ubuntu/Debian systems (apt required)."
  exit 1
fi

echo "[1/4] Installing Python, Tk, codecs, and VLC..."
sudo apt update
sudo apt install -y \
  python3 \
  python3-tk \
  ffmpeg \
  ubuntu-restricted-extras \
  gstreamer1.0-libav \
  gstreamer1.0-plugins-good \
  gstreamer1.0-plugins-bad \
  gstreamer1.0-plugins-ugly \
  vlc

echo "[2/4] Making launcher scripts executable..."
chmod +x "$SCRIPT_DIR/run_ubuntu.sh"
chmod +x "$SCRIPT_DIR/install_ubuntu_desktop_icon.sh"

echo "[3/4] Installing desktop launcher..."
"$SCRIPT_DIR/install_ubuntu_desktop_icon.sh"

echo "[4/4] Done."
echo "Open Ubuntu app menu and search: U盘文件夹自动导入"
echo "Or run directly: $SCRIPT_DIR/run_ubuntu.sh"
