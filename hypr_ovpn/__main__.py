"""Entry point: single-instance guard, CLI control channel, Qt setup.

Usage:
    hypr-ovpn                      start the tray app (or focus settings if running)
    hypr-ovpn --settings           open the settings window
    hypr-ovpn --connect NAME       connect to a profile (great for Hyprland binds)
    hypr-ovpn --toggle NAME        connect/disconnect a profile
    hypr-ovpn --disconnect         disconnect the active VPN
    hypr-ovpn --status             print the current status
"""

from __future__ import annotations

import os
import sys

# Route file dialogs through xdg-desktop-portal (native system picker) unless
# the user insists on their own platform theme.
if os.environ.get("HYPR_OVPN_KEEP_PLATFORMTHEME") != "1":
    os.environ["QT_QPA_PLATFORMTHEME"] = "xdgdesktopportal"

from PyQt6.QtCore import QTimer
from PyQt6.QtNetwork import QLocalServer, QLocalSocket
from PyQt6.QtWidgets import QApplication, QSystemTrayIcon

from .icons import icon_connected

SOCKET_NAME = "hypr-ovpn-ctl"


def _args_to_command(argv: list[str]) -> str | None:
    if "--settings" in argv:
        return "show"
    if "--disconnect" in argv:
        return "disconnect"
    if "--status" in argv:
        return "status"
    for flag in ("--connect", "--toggle"):
        if flag in argv:
            i = argv.index(flag)
            if i + 1 >= len(argv):
                print(f"{flag} requires a profile name", file=sys.stderr)
                sys.exit(2)
            return f"{flag.lstrip('-')} {argv[i + 1]}"
    return None


def _send_to_running(command: str) -> str | None:
    """Returns the reply if a running instance handled it, else None."""
    sock = QLocalSocket()
    sock.connectToServer(SOCKET_NAME)
    if not sock.waitForConnected(500):
        return None
    sock.write((command + "\n").encode())
    sock.flush()
    reply = b""
    if sock.waitForReadyRead(3000):
        reply = bytes(sock.readAll())
    sock.disconnectFromServer()
    return reply.decode(errors="replace").strip()


def main() -> int:
    argv = sys.argv[:]
    command = _args_to_command(argv)

    app = QApplication(argv)
    app.setApplicationName("hypr-ovpn")
    app.setDesktopFileName("hypr-ovpn")
    app.setQuitOnLastWindowClosed(False)
    app.setWindowIcon(icon_connected())

    # Single instance: forward the command (or a plain "show") and exit.
    reply = _send_to_running(command or "show")
    if reply is not None:
        if command == "--status" or (command and command.startswith("status")):
            print(reply)
        elif reply.startswith("ERR"):
            print(reply, file=sys.stderr)
            return 1
        return 0

    if command == "status":
        print("hypr-ovpn is not running")
        return 1

    if not QSystemTrayIcon.isSystemTrayAvailable():
        print("warning: no system tray found — the app will still run; "
              "add a tray module (e.g. waybar) to see the icon", file=sys.stderr)

    from .app import Controller
    controller = Controller(app)

    # Control server for later invocations.
    QLocalServer.removeServer(SOCKET_NAME)
    server = QLocalServer()
    server.listen(SOCKET_NAME)

    def on_new_connection() -> None:
        conn = server.nextPendingConnection()

        def on_ready() -> None:
            line = bytes(conn.readAll()).decode(errors="replace").strip()
            reply = controller.handle_command(line) if line else "ERR empty"
            conn.write((reply + "\n").encode())
            conn.flush()
            conn.disconnectFromServer()

        conn.readyRead.connect(on_ready)

    server.newConnection.connect(on_new_connection)

    if command == "show" or "--settings" in argv:
        controller.show_settings()
    elif command:
        QTimer.singleShot(0, lambda: controller.handle_command(command))

    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
