"""Application controller: owns the profile store, the active connections
(any number at once, one per profile), the tray icon, and the windows."""

from __future__ import annotations

from PyQt6.QtCore import QObject, QTimer
from PyQt6.QtWidgets import QApplication, QSystemTrayIcon

from . import secrets_store
from .connection import State, VpnConnection
from .profiles import Profile, ProfileStore
from .tray import Tray
from .windows import CredentialsDialog, LogWindow, SettingsWindow


def _human_bytes(n: int) -> str:
    for unit in ("B", "KiB", "MiB", "GiB"):
        if n < 1024 or unit == "GiB":
            return f"{n:.1f} {unit}" if unit != "B" else f"{n} B"
        n /= 1024
    return f"{n:.1f} GiB"


class Controller(QObject):
    def __init__(self, app: QApplication):
        super().__init__()
        self.app = app
        self.store = ProfileStore()
        self.connections: dict[str, VpnConnection] = {}   # profile id -> connection
        self._log_lines: list[str] = []
        self._quitting = False

        self.tray = Tray(self)
        self.tray.show()
        self.settings_window: SettingsWindow | None = None
        self.log_window: LogWindow | None = None

        QTimer.singleShot(500, self._autoconnect)

    # ------------------------------------------------------------- lifecycle

    def _autoconnect(self) -> None:
        for p in self.store.profiles:
            if p.autoconnect:
                self.connect_profile(p)

    def quit(self) -> None:
        if self.connections and not self._quitting:
            self._quitting = True
            self.notify("Disconnecting before exit…")
            self.disconnect_all()
            QTimer.singleShot(8000, self.app.quit)  # safety net
            return
        self.app.quit()

    # ------------------------------------------------------------ connecting

    def connection_for(self, profile: Profile) -> VpnConnection | None:
        return self.connections.get(profile.id)

    def toggle_profile(self, profile: Profile) -> None:
        if profile.id in self.connections:
            self.disconnect_profile(profile)
        else:
            self.connect_profile(profile)

    def connect_profile(self, profile: Profile) -> None:
        if profile.id in self.connections:
            return  # already up (or on its way up / down)
        conn = VpnConnection(profile, self)
        self.connections[profile.id] = conn
        if profile.has_saved_password:
            saved = secrets_store.lookup(profile.id)
            if saved is not None:
                conn.password = saved
        conn.state_changed.connect(lambda state, _d, c=conn: self._on_state_changed(c, state))
        conn.log_line.connect(lambda line, c=conn: self._on_log_line(c, line))
        conn.credentials_needed.connect(
            lambda need, wants, retry, c=conn: self._on_credentials_needed(c, need, wants, retry))
        conn.finished.connect(lambda user, reason, c=conn: self._on_finished(c, user, reason))
        conn.bytecount.connect(lambda _rx, _tx: self.tray.setToolTip(f"hypr-ovpn\n{self.status_text()}"))
        conn.start()

    def disconnect_profile(self, profile: Profile) -> None:
        conn = self.connections.get(profile.id)
        if conn is not None:
            conn.stop()

    def disconnect_all(self) -> None:
        for conn in list(self.connections.values()):
            conn.stop()

    # --------------------------------------------------------------- signals

    def _on_state_changed(self, conn: VpnConnection, state: State) -> None:
        if state == State.CONNECTED:
            self.notify(f"Connected to {conn.profile.name}"
                        + (f" ({conn.local_ip})" if conn.local_ip else ""))
        self._refresh_ui()

    def _on_credentials_needed(self, conn: VpnConnection, need: str,
                               wants_username: bool, is_retry: bool) -> None:
        if self.connections.get(conn.profile.id) is not conn:
            return
        if is_retry:
            self.notify(f"{conn.profile.name}: authentication failed", error=True)
        dlg = CredentialsDialog(
            None, conn.profile.name, need, wants_username, conn.username, is_retry)
        if dlg.exec():
            username = dlg.user_edit.text().strip()
            password = dlg.pass_edit.text()
            conn.supply_credentials(need, username, password)
            if need == "Auth":
                profile = conn.profile
                changed = False
                if username and username != profile.username:
                    profile.username = username
                    changed = True
                if dlg.save_check.isChecked() and password:
                    if secrets_store.store(profile.id, password):
                        profile.has_saved_password = True
                        changed = True
                if changed:
                    self.store.save()
                    self.profiles_changed()
        else:
            conn.cancel_credentials()

    def _on_finished(self, conn: VpnConnection, user_requested: bool, reason: str) -> None:
        if self.connections.get(conn.profile.id) is conn:
            del self.connections[conn.profile.id]
        name = conn.profile.name
        if self._quitting:
            if not self.connections:
                self.app.quit()
            return
        if user_requested:
            self.notify(f"Disconnected from {name}")
        elif reason == "auth-failure":
            self.notify(f"{name}: authentication failed", error=True)
        elif reason:
            self.notify(f"{name}: disconnected ({reason})", error=True)
        else:
            self.notify(f"{name}: disconnected")
        self._refresh_ui()

    def _on_log_line(self, conn: VpnConnection, line: str) -> None:
        if len(self.connections) > 1:
            line = f"[{conn.profile.name}] {line}"
        self._log_lines.append(line)
        if len(self._log_lines) > 5000:
            del self._log_lines[:1000]
        if self.log_window is not None and self.log_window.isVisible():
            self.log_window.append(line)

    # ------------------------------------------------------------------- ui

    def connection_status(self, conn: VpnConnection) -> str:
        text = f"{conn.profile.name}: {conn.detail}"
        if conn.state == State.CONNECTED and (conn.rx or conn.tx):
            text += f"  ↓{_human_bytes(conn.rx)} ↑{_human_bytes(conn.tx)}"
        return text

    def status_text(self) -> str:
        if not self.connections:
            return "Disconnected"
        return "\n".join(self.connection_status(c) for c in self.connections.values())

    def overall_state(self) -> State:
        """Busy if any connection is in transition, else connected if any is up."""
        states = {c.state for c in self.connections.values()}
        for busy in (State.STARTING, State.CONNECTING, State.RECONNECTING, State.DISCONNECTING):
            if busy in states:
                return busy
        return State.CONNECTED if State.CONNECTED in states else State.IDLE

    def _refresh_ui(self) -> None:
        self.tray.update_state(self.overall_state(), f"hypr-ovpn\n{self.status_text()}")
        if self.settings_window is not None and self.settings_window.isVisible():
            self.settings_window.refresh()

    def profiles_changed(self) -> None:
        self.tray.rebuild_menu()
        if self.settings_window is not None and self.settings_window.isVisible():
            self.settings_window.refresh()

    def show_settings(self) -> None:
        if self.settings_window is None:
            self.settings_window = SettingsWindow(self)
        self.settings_window.refresh()
        self.settings_window.show()
        self.settings_window.raise_()
        self.settings_window.activateWindow()

    def show_log(self) -> None:
        if self.log_window is None:
            self.log_window = LogWindow(self)
        self.log_window.set_lines(self._log_lines)
        self.log_window.show()
        self.log_window.raise_()
        self.log_window.activateWindow()

    def notify(self, message: str, error: bool = False) -> None:
        icon = (QSystemTrayIcon.MessageIcon.Critical if error
                else QSystemTrayIcon.MessageIcon.Information)
        self.tray.showMessage("hypr-ovpn", message, icon, 5000)

    # -------------------------------------------------------------- profiles

    def apply_password_change(self, profile: Profile, new_password: str | None,
                              clear: bool) -> None:
        if new_password is not None:
            if secrets_store.store(profile.id, new_password):
                profile.has_saved_password = True
        elif clear and profile.has_saved_password:
            secrets_store.clear(profile.id)
            profile.has_saved_password = False

    def remove_profile(self, profile: Profile) -> None:
        secrets_store.clear(profile.id)
        self.store.remove(profile)
        self.profiles_changed()

    # ------------------------------------------------------------------- cli

    def handle_command(self, command: str) -> str:
        parts = command.strip().split(None, 1)
        if not parts:
            return "ERR empty command"
        verb, arg = parts[0], (parts[1].strip() if len(parts) > 1 else "")
        if verb == "show":
            self.show_settings()
            return "OK"
        if verb == "disconnect":
            if not arg:
                self.disconnect_all()
                return "OK"
            profile = self.store.find_by_name(arg)
            if profile is None:
                return f"ERR no profile named '{arg}'"
            self.disconnect_profile(profile)
            return "OK"
        if verb == "status":
            return self.status_text()
        if verb in ("connect", "toggle"):
            profile = self.store.find_by_name(arg)
            if profile is None:
                return f"ERR no profile named '{arg}'"
            self.toggle_profile(profile) if verb == "toggle" else self.connect_profile(profile)
            return "OK"
        return f"ERR unknown command '{verb}'"
