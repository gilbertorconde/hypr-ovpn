"""Application controller: owns the profile store, the single active
connection, the tray icon, and the windows."""

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
        self.active_connection: VpnConnection | None = None
        self._pending_profile: Profile | None = None
        self.state_detail = "Disconnected"
        self._rx = self._tx = 0
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
                break

    def quit(self) -> None:
        if self.active_connection is not None and not self._quitting:
            self._quitting = True
            self.notify("Disconnecting before exit…")
            self.active_connection.stop()
            QTimer.singleShot(8000, self.app.quit)  # safety net
            return
        self.app.quit()

    # ------------------------------------------------------------ connecting

    def toggle_profile(self, profile: Profile) -> None:
        conn = self.active_connection
        if conn is not None and conn.profile.id == profile.id:
            self.disconnect_active()
        else:
            self.connect_profile(profile)

    def connect_profile(self, profile: Profile) -> None:
        if self.active_connection is not None:
            # One VPN at a time: switch by disconnecting first, then starting.
            self._pending_profile = profile
            self.active_connection.stop()
            return
        conn = VpnConnection(profile, self)
        self.active_connection = conn
        self._rx = self._tx = 0
        if profile.has_saved_password:
            saved = secrets_store.lookup(profile.id)
            if saved is not None:
                conn.password = saved
        conn.state_changed.connect(self._on_state_changed)
        conn.log_line.connect(self._on_log_line)
        conn.credentials_needed.connect(self._on_credentials_needed)
        conn.finished.connect(self._on_finished)
        conn.bytecount.connect(self._on_bytecount)
        conn.start()

    def disconnect_active(self) -> None:
        self._pending_profile = None
        if self.active_connection is not None:
            self.active_connection.stop()

    # --------------------------------------------------------------- signals

    def _on_state_changed(self, state: State, detail: str) -> None:
        self.state_detail = detail
        if state == State.CONNECTED:
            conn = self.active_connection
            self.notify(f"Connected to {conn.profile.name}"
                        + (f" ({conn.local_ip})" if conn.local_ip else ""))
        self._refresh_ui()

    def _on_bytecount(self, rx: int, tx: int) -> None:
        self._rx, self._tx = rx, tx
        self.tray.setToolTip(self.status_text())

    def _on_credentials_needed(self, need: str, wants_username: bool, is_retry: bool) -> None:
        conn = self.active_connection
        if conn is None:
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

    def _on_finished(self, user_requested: bool, reason: str) -> None:
        conn = self.active_connection
        self.active_connection = None
        self.state_detail = "Disconnected"
        self._rx = self._tx = 0
        if conn is not None and not user_requested and not self._quitting:
            name = conn.profile.name
            if reason == "auth-failure":
                self.notify(f"{name}: authentication failed", error=True)
            elif reason:
                self.notify(f"{name}: disconnected ({reason})", error=True)
            else:
                self.notify(f"{name}: disconnected")
        elif user_requested and conn is not None and not self._quitting:
            self.notify(f"Disconnected from {conn.profile.name}")
        self._refresh_ui()
        if self._quitting:
            self.app.quit()
            return
        if self._pending_profile is not None:
            pending, self._pending_profile = self._pending_profile, None
            self.connect_profile(pending)

    def _on_log_line(self, line: str) -> None:
        self._log_lines.append(line)
        if len(self._log_lines) > 5000:
            del self._log_lines[:1000]
        if self.log_window is not None and self.log_window.isVisible():
            self.log_window.append(line)

    # ------------------------------------------------------------------- ui

    def status_text(self) -> str:
        conn = self.active_connection
        if conn is None:
            return "Disconnected"
        text = f"{conn.profile.name}: {self.state_detail}"
        if conn.state == State.CONNECTED and (self._rx or self._tx):
            text += f"  ↓{_human_bytes(self._rx)} ↑{_human_bytes(self._tx)}"
        return text

    def _refresh_ui(self) -> None:
        state = self.active_connection.state if self.active_connection else State.IDLE
        self.tray.update_state(state, f"hypr-ovpn — {self.status_text()}")
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
            self.disconnect_active()
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
