#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
APP_NAME="U盘文件夹自动导入"
DESKTOP_FILE="$HOME/.local/share/applications/usb-mp4-transfer.desktop"
RUN_SCRIPT="$SCRIPT_DIR/run_ubuntu.sh"

mkdir -p "$HOME/.local/share/applications"

cat > "$DESKTOP_FILE" <<EOF
[Desktop Entry]
Version=1.0
Type=Application
Name=$APP_NAME
Comment=Import USB folders and replace matching target folders
Exec=$RUN_SCRIPT
Path=$SCRIPT_DIR
Terminal=false
Categories=Utility;
StartupNotify=true
EOF

chmod +x "$RUN_SCRIPT"
chmod +x "$DESKTOP_FILE"

if command -v update-desktop-database >/dev/null 2>&1; then
  update-desktop-database "$HOME/.local/share/applications" >/dev/null 2>&1 || true
fi

echo "Desktop launcher installed:"
echo "$DESKTOP_FILE"
echo
echo "You can now search for \"$APP_NAME\" in Ubuntu app menu and pin it."
