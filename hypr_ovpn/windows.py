"""Settings window, profile editor, credential prompt, and log viewer."""

from __future__ import annotations

from pathlib import Path

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QIcon
from PyQt6.QtWidgets import (
    QCheckBox, QDialog, QDialogButtonBox, QFileDialog, QFormLayout, QHBoxLayout,
    QLabel, QLineEdit, QListWidget, QListWidgetItem, QMainWindow, QMessageBox,
    QPlainTextEdit, QPushButton, QVBoxLayout, QWidget,
)

from . import secrets_store
from .connection import State
from .icons import icon_connected, icon_idle, icon_busy
from .profiles import Profile, analyze_config


def _pick_ovpn_file(parent: QWidget) -> str:
    path, _ = QFileDialog.getOpenFileName(
        parent, "Choose OpenVPN config",
        str(Path.home()),
        "OpenVPN config (*.ovpn *.conf);;All files (*)",
    )
    return path


class ProfileDialog(QDialog):
    """Add / edit a VPN profile — mirrors the Android client's profile form."""

    def __init__(self, parent: QWidget | None, profile: Profile | None = None):
        super().__init__(parent)
        self.setWindowTitle("Edit VPN" if profile else "Add VPN")
        self.setMinimumWidth(460)
        self.profile = profile
        self.result_password: str | None = None       # None = leave as-is
        self.result_clear_password = False
        self.picked_config: str = ""

        form = QFormLayout()

        self.name_edit = QLineEdit(profile.name if profile else "")
        form.addRow("Name", self.name_edit)

        self.config_edit = QLineEdit(profile.config_path if profile else "")
        self.config_edit.setReadOnly(True)
        self.config_edit.setPlaceholderText("Pick a .ovpn file…")
        browse = QPushButton("Browse…")
        browse.clicked.connect(self._browse)
        config_row = QHBoxLayout()
        config_row.addWidget(self.config_edit, 1)
        config_row.addWidget(browse)
        form.addRow("Config file", config_row)

        self.config_note = QLabel("")
        self.config_note.setWordWrap(True)
        self.config_note.setStyleSheet("color: #b58900;")
        self.config_note.hide()
        form.addRow("", self.config_note)

        self.user_edit = QLineEdit(profile.username if profile else "")
        self.user_edit.setPlaceholderText("Leave empty if the VPN needs no username")
        form.addRow("Username", self.user_edit)

        self.pass_edit = QLineEdit()
        self.pass_edit.setEchoMode(QLineEdit.EchoMode.Password)
        if profile and profile.has_saved_password:
            self.pass_edit.setPlaceholderText("(saved — type to replace)")
        else:
            self.pass_edit.setPlaceholderText("Leave empty to be asked on every connect")
        form.addRow("Password", self.pass_edit)

        self.save_pass_check = QCheckBox(f"Save password ({secrets_store.backend_name()})")
        self.save_pass_check.setChecked(bool(profile and profile.has_saved_password))
        self.pass_edit.textEdited.connect(lambda t: self.save_pass_check.setChecked(bool(t)))
        form.addRow("", self.save_pass_check)

        self.autoconnect_check = QCheckBox("Connect automatically when hypr-ovpn starts")
        self.autoconnect_check.setChecked(bool(profile and profile.autoconnect))
        form.addRow("", self.autoconnect_check)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self._validate_accept)
        buttons.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.addLayout(form)
        layout.addWidget(buttons)

        if profile and not profile.imported:
            self._show_external_note(profile.config_path)

    def _browse(self) -> None:
        path = _pick_ovpn_file(self)
        if not path:
            return
        self.picked_config = path
        self.config_edit.setText(path)
        if not self.name_edit.text().strip():
            self.name_edit.setText(Path(path).stem)
        self._show_external_note(path)

    def _show_external_note(self, path: str) -> None:
        try:
            analysis = analyze_config(Path(path))
        except OSError:
            return
        if analysis.self_contained:
            self.config_note.hide()
        else:
            refs = ", ".join(a.split()[0] for a in analysis.external_files)
            self.config_note.setText(
                f"This config references external files ({refs}). It will be used from its "
                f"current location instead of being imported — don't move or delete it.")
            self.config_note.show()

    def _validate_accept(self) -> None:
        if not self.name_edit.text().strip():
            QMessageBox.warning(self, "Missing name", "Give this VPN a name.")
            return
        if not (self.picked_config or (self.profile and self.profile.config_path)):
            QMessageBox.warning(self, "Missing config", "Pick a .ovpn config file.")
            return
        typed = self.pass_edit.text()
        if typed and self.save_pass_check.isChecked():
            self.result_password = typed
        elif not self.save_pass_check.isChecked():
            self.result_clear_password = True
        self.accept()


class CredentialsDialog(QDialog):
    """Prompt shown at connect time when no password is saved (or auth failed)."""

    def __init__(self, parent: QWidget | None, profile_name: str, need: str,
                 wants_username: bool, username: str, is_retry: bool):
        super().__init__(parent)
        self.setWindowTitle(f"{profile_name} — authentication")
        self.setMinimumWidth(380)
        self.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, True)

        layout = QVBoxLayout(self)
        if is_retry:
            warn = QLabel("Authentication failed — check your credentials and try again.")
            warn.setStyleSheet("color: #d9534f;")
            warn.setWordWrap(True)
            layout.addWidget(warn)
        label = "Password" if need == "Auth" else f"{need} password"
        layout.addWidget(QLabel(f"“{profile_name}” needs your {label.lower()} to connect."))

        form = QFormLayout()
        self.user_edit = QLineEdit(username)
        if wants_username:
            form.addRow("Username", self.user_edit)
        self.pass_edit = QLineEdit()
        self.pass_edit.setEchoMode(QLineEdit.EchoMode.Password)
        form.addRow(label, self.pass_edit)
        layout.addLayout(form)

        self.save_check = QCheckBox("Save password for future connections")
        if need == "Auth":
            layout.addWidget(self.save_check)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self.pass_edit.setFocus()


class LogWindow(QMainWindow):
    def __init__(self, controller):
        super().__init__()
        self.controller = controller
        self.setWindowTitle("hypr-ovpn — log")
        self.resize(760, 420)
        self.text = QPlainTextEdit()
        self.text.setReadOnly(True)
        self.text.setMaximumBlockCount(5000)
        self.text.setStyleSheet("font-family: monospace;")
        self.setCentralWidget(self.text)

    def append(self, line: str) -> None:
        self.text.appendPlainText(line)

    def set_lines(self, lines: list[str]) -> None:
        self.text.setPlainText("\n".join(lines))
        self.text.verticalScrollBar().setValue(self.text.verticalScrollBar().maximum())


class SettingsWindow(QMainWindow):
    """The VPN list — add, edit, remove, and connect."""

    def __init__(self, controller):
        super().__init__()
        self.controller = controller
        self.setWindowTitle("hypr-ovpn — VPN profiles")
        self.resize(520, 420)

        central = QWidget()
        layout = QVBoxLayout(central)

        self.list = QListWidget()
        self.list.itemDoubleClicked.connect(self._toggle_selected)
        self.list.itemSelectionChanged.connect(self._update_buttons)
        layout.addWidget(self.list, 1)

        self.status_label = QLabel("")
        self.status_label.setWordWrap(True)
        layout.addWidget(self.status_label)

        row = QHBoxLayout()
        self.add_btn = QPushButton("Add…")
        self.edit_btn = QPushButton("Edit…")
        self.remove_btn = QPushButton("Remove")
        self.connect_btn = QPushButton("Connect")
        self.log_btn = QPushButton("Log")
        row.addWidget(self.add_btn)
        row.addWidget(self.edit_btn)
        row.addWidget(self.remove_btn)
        row.addStretch(1)
        row.addWidget(self.log_btn)
        row.addWidget(self.connect_btn)
        layout.addLayout(row)

        self.add_btn.clicked.connect(self._add)
        self.edit_btn.clicked.connect(self._edit)
        self.remove_btn.clicked.connect(self._remove)
        self.connect_btn.clicked.connect(self._toggle_selected)
        self.log_btn.clicked.connect(lambda: self.controller.show_log())

        self.setCentralWidget(central)
        self.refresh()

    # ------------------------------------------------------------------ ui

    def _selected(self) -> Profile | None:
        item = self.list.currentItem()
        return self.controller.store.get(item.data(Qt.ItemDataRole.UserRole)) if item else None

    def refresh(self) -> None:
        selected_id = None
        if (item := self.list.currentItem()) is not None:
            selected_id = item.data(Qt.ItemDataRole.UserRole)
        self.list.clear()
        for p in self.controller.store.profiles:
            item = QListWidgetItem(self._item_text(p))
            item.setData(Qt.ItemDataRole.UserRole, p.id)
            item.setIcon(self._item_icon(p))
            self.list.addItem(item)
            if p.id == selected_id:
                self.list.setCurrentItem(item)
        if self.list.currentItem() is None and self.list.count():
            self.list.setCurrentRow(0)
        self.status_label.setText(self.controller.status_text())
        self._update_buttons()

    def _item_text(self, p: Profile) -> str:
        bits = [p.name]
        if p.username:
            bits.append(f"({p.username})")
        if conn := self.controller.connection_for(p):
            bits.append("— " + conn.detail)
        return "  ".join(bits)

    def _item_icon(self, p: Profile) -> QIcon:
        if conn := self.controller.connection_for(p):
            return icon_connected() if conn.state == State.CONNECTED else icon_busy()
        return icon_idle()

    def _update_buttons(self) -> None:
        p = self._selected()
        is_active = bool(p and self.controller.connection_for(p))
        self.connect_btn.setText("Disconnect" if is_active else "Connect")
        self.connect_btn.setEnabled(p is not None)
        self.edit_btn.setEnabled(p is not None)
        self.remove_btn.setEnabled(p is not None and not is_active)

    # -------------------------------------------------------------- actions

    def _add(self) -> None:
        dlg = ProfileDialog(self)
        if not dlg.exec():
            return
        if not dlg.picked_config:
            QMessageBox.warning(self, "Missing config", "No config file was chosen.")
            return
        try:
            profile = self.controller.store.create(
                dlg.name_edit.text().strip(), dlg.picked_config, dlg.user_edit.text().strip())
        except OSError as e:
            QMessageBox.critical(self, "Import failed", f"Could not import config:\n{e}")
            return
        profile.autoconnect = dlg.autoconnect_check.isChecked()
        self.controller.apply_password_change(profile, dlg.result_password, dlg.result_clear_password)
        self.controller.store.save()
        self.controller.profiles_changed()

    def _edit(self) -> None:
        profile = self._selected()
        if not profile:
            return
        dlg = ProfileDialog(self, profile)
        if not dlg.exec():
            return
        profile.name = dlg.name_edit.text().strip()
        profile.username = dlg.user_edit.text().strip()
        profile.autoconnect = dlg.autoconnect_check.isChecked()
        if dlg.picked_config:
            try:
                self.controller.store.replace_config(profile, dlg.picked_config)
            except OSError as e:
                QMessageBox.critical(self, "Import failed", f"Could not import config:\n{e}")
        self.controller.apply_password_change(profile, dlg.result_password, dlg.result_clear_password)
        self.controller.store.save()
        self.controller.profiles_changed()

    def _remove(self) -> None:
        profile = self._selected()
        if not profile:
            return
        answer = QMessageBox.question(
            self, "Remove VPN",
            f"Remove “{profile.name}”? Its imported config and saved password will be deleted.")
        if answer != QMessageBox.StandardButton.Yes:
            return
        self.controller.remove_profile(profile)

    def _toggle_selected(self) -> None:
        profile = self._selected()
        if profile:
            self.controller.toggle_profile(profile)
