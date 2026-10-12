"""System tray icon (StatusNotifierItem via Qt) with the fast-connect menu."""

from __future__ import annotations

from PyQt6.QtCore import QTimer
from PyQt6.QtGui import QAction
from PyQt6.QtWidgets import QMenu, QSystemTrayIcon

from .connection import State
from .icons import icon_busy, icon_busy_dim, icon_connected, icon_idle


class Tray(QSystemTrayIcon):
    def __init__(self, controller):
        super().__init__(icon_idle())
        self.controller = controller
        self.setToolTip("hypr-ovpn — disconnected")

        self.menu = QMenu()
        self.setContextMenu(self.menu)
        self.activated.connect(self._on_activated)

        self._blink = QTimer(self)
        self._blink.setInterval(600)
        self._blink.timeout.connect(self._blink_tick)
        self._blink_on = False

        self.rebuild_menu()

    # ------------------------------------------------------------------ menu

    def rebuild_menu(self) -> None:
        c = self.controller
        self.menu.clear()

        for line in c.status_text().splitlines():
            status = QAction(line, self.menu)
            status.setEnabled(False)
            self.menu.addAction(status)

        if c.connections:
            label = "Disconnect all" if len(c.connections) > 1 else "Disconnect"
            disconnect = QAction(label, self.menu)
            disconnect.triggered.connect(c.disconnect_all)
            self.menu.addAction(disconnect)

        self.menu.addSeparator()

        if not c.store.profiles:
            empty = QAction("No VPNs configured — open Settings", self.menu)
            empty.triggered.connect(c.show_settings)
            self.menu.addAction(empty)
        for p in c.store.profiles:
            action = QAction(p.name, self.menu)
            action.setCheckable(True)
            action.setChecked(c.connection_for(p) is not None)
            action.triggered.connect(lambda _c, prof=p: c.toggle_profile(prof))
            self.menu.addAction(action)

        self.menu.addSeparator()
        settings = QAction("Settings…", self.menu)
        settings.triggered.connect(c.show_settings)
        self.menu.addAction(settings)
        log = QAction("Log…", self.menu)
        log.triggered.connect(c.show_log)
        self.menu.addAction(log)
        self.menu.addSeparator()
        quit_action = QAction("Quit", self.menu)
        quit_action.triggered.connect(c.quit)
        self.menu.addAction(quit_action)

    # ----------------------------------------------------------------- state

    def update_state(self, state: State, tooltip: str) -> None:
        self.setToolTip(tooltip)
        if state in (State.STARTING, State.CONNECTING, State.RECONNECTING, State.DISCONNECTING):
            if not self._blink.isActive():
                self._blink.start()
            self.setIcon(icon_busy())
        else:
            self._blink.stop()
            self.setIcon(icon_connected() if state == State.CONNECTED else icon_idle())
        self.rebuild_menu()

    def _blink_tick(self) -> None:
        self._blink_on = not self._blink_on
        self.setIcon(icon_busy() if self._blink_on else icon_busy_dim())

    def _on_activated(self, reason: QSystemTrayIcon.ActivationReason) -> None:
        if reason in (QSystemTrayIcon.ActivationReason.Trigger,
                      QSystemTrayIcon.ActivationReason.MiddleClick):
            self.controller.show_settings()
