#!/usr/bin/env bash
set -euo pipefail
HERE="$(cd "$(dirname "$(readlink -f "$0")")" && pwd)"

BIN_DIR="${HOME}/.local/bin"
APPS_DIR="${HOME}/.local/share/applications"

mkdir -p "$BIN_DIR" "$APPS_DIR"

ln -sf "$HERE/hypr-ovpn" "$BIN_DIR/hypr-ovpn"
echo "installed launcher    -> $BIN_DIR/hypr-ovpn"

sed "s|^Exec=.*|Exec=$BIN_DIR/hypr-ovpn|" "$HERE/hypr-ovpn.desktop" > "$APPS_DIR/hypr-ovpn.desktop"
echo "installed desktop file -> $APPS_DIR/hypr-ovpn.desktop"

if [[ "${1:-}" == "--polkit" ]]; then
    echo "installing polkit policy (asks for sudo) — VPN starts will remember"
    echo "your authentication for a few minutes instead of asking every time"
    sudo install -m 644 "$HERE/polkit/dev.hypr-ovpn.policy" \
        /usr/share/polkit-1/actions/dev.hypr-ovpn.policy
    echo "installed polkit policy -> /usr/share/polkit-1/actions/dev.hypr-ovpn.policy"
else
    echo
    echo "tip: run './install.sh --polkit' to also install the optional polkit"
    echo "     policy (cached authentication when starting VPNs)"
fi

echo
echo "autostart with Hyprland: add to hyprland.conf:"
echo "    exec-once = $BIN_DIR/hypr-ovpn"
