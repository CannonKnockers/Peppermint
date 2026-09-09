#!/usr/bin/env bash
# Install Peppermint for the current user. This script needs no root.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VENV="$ROOT/.venv"
PY=/usr/bin/python3

say() { printf '\n\033[1m%s\033[0m\n' "$*"; }

say "1. Check the system packages"
missing=()
for typelib in Gtk-3.0 Notify-0.7; do
    $PY - "$typelib" <<'EOF' || missing+=("$typelib")
import sys, gi
name, version = sys.argv[1].split("-")
gi.require_version(name, version)
EOF
done
if ! "$PY" -c 'import gi; gi.require_foreign("cairo")' >/dev/null 2>&1; then
    missing+=("python3-gi-cairo")
fi
if [ ${#missing[@]} -gt 0 ]; then
    echo "These packages are missing: ${missing[*]}"
    echo "Install them with: sudo apt install python3-gi python3-gi-cairo gir1.2-gtk-3.0 gir1.2-notify-0.7"
    exit 1
fi
echo "GTK 3, Cairo and libnotify are ready."

say "2. Make the virtual environment"
# The system python must be used. It owns the `gi` module.
[ -d "$VENV" ] || $PY -m venv --system-site-packages "$VENV"
"$VENV/bin/pip" install --quiet --upgrade pip
"$VENV/bin/pip" install --quiet -e "$ROOT"
echo "Installed in $VENV"

say "3. Check Ollama"
if ! curl -sf --max-time 5 http://127.0.0.1:11434/api/version >/dev/null; then
    echo "Ollama does not answer. Run scripts/setup-ollama.sh first."
    exit 1
fi
echo "Ollama answers."

say "4. Install the daemon service"
mkdir -p "$HOME/.config/systemd/user"
sed "s|@PROJECT_ROOT@|$ROOT|g" "$ROOT/data/peppermint-daemon.service" \
    > "$HOME/.config/systemd/user/peppermint-daemon.service"
systemctl --user daemon-reload
systemctl --user enable --now peppermint-daemon.service
echo "The daemon starts at login."

say "5. Install the menu entry"
mkdir -p "$HOME/.local/share/applications"
sed "s|@PROJECT_ROOT@|$ROOT|g" "$ROOT/data/peppermint.desktop" \
    > "$HOME/.local/share/applications/peppermint.desktop"
update-desktop-database "$HOME/.local/share/applications" 2>/dev/null || true

say "6. Install the panel icon"
mkdir -p "$HOME/.config/autostart"
sed "s|@PROJECT_ROOT@|$ROOT|g" "$ROOT/data/peppermint-applet.desktop" \
    > "$HOME/.config/autostart/peppermint-applet.desktop"
if $PY -c "import gi; gi.require_version('XApp','1.0')" 2>/dev/null; then
    echo "The icon uses XApp, like the other Mint indicators."
else
    echo "XApp is missing, so the icon uses the old interface."
    echo "For the Mint look, install it with: sudo apt install gir1.2-xapp-1.0"
fi
"$VENV/bin/peppermint-window" --background >/dev/null 2>&1 &
echo "The icon appears at login, and it is on the panel now."

say "7. Install the keyboard shortcut"
"$VENV/bin/python" - "$ROOT" <<'EOF'
import sys
from peppermint import config
from peppermint.daemon import keybinding
slot = keybinding.install("Peppermint", f"{sys.argv[1]}/.venv/bin/peppermint toggle", config.HOTKEY)
print(f"{config.HOTKEY} opens Peppermint (slot {slot}).")
EOF

say "Peppermint is ready."
cat <<EOF
Press ${PEPPERMINT_HOTKEY:-Super+Space} to open the window.
From a terminal:
    $VENV/bin/peppermint "sort my Downloads folder"
    $VENV/bin/peppermint list
    $VENV/bin/peppermint health

Put this in your ~/.bashrc to type just \`peppermint\`:
    export PATH="$VENV/bin:\$PATH"
EOF
