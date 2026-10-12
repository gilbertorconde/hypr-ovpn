"""One OpenVPN connection, driven through the management interface.

openvpn runs as root via pkexec; the app stays unprivileged and talks to it
over a password-protected management socket on 127.0.0.1. Credentials are fed
through the management channel (never on the command line), state changes are
streamed back, and disconnect is a clean SIGTERM sent through the same channel
(so no second root prompt is needed to stop the VPN).
"""

from __future__ import annotations

import os
import secrets as pysecrets
import shutil
import tempfile
from enum import Enum
from pathlib import Path

from PyQt6.QtCore import QObject, QProcess, QTimer, pyqtSignal
from PyQt6.QtNetwork import QTcpServer, QTcpSocket, QHostAddress

from .profiles import Profile

OPENVPN_BIN = shutil.which("openvpn") or "/usr/bin/openvpn"
PKEXEC_BIN = shutil.which("pkexec") or "/usr/bin/pkexec"


class State(Enum):
    IDLE = "idle"
    STARTING = "starting"          # pkexec prompt / process spawn
    CONNECTING = "connecting"
    CONNECTED = "connected"
    RECONNECTING = "reconnecting"
    DISCONNECTING = "disconnecting"


# OpenVPN >STATE names -> our states
_STATE_MAP = {
    "CONNECTING": State.CONNECTING,
    "WAIT": State.CONNECTING,
    "AUTH": State.CONNECTING,
    "GET_CONFIG": State.CONNECTING,
    "ASSIGN_IP": State.CONNECTING,
    "ADD_ROUTES": State.CONNECTING,
    "RESOLVE": State.CONNECTING,
    "TCP_CONNECT": State.CONNECTING,
    "CONNECTED": State.CONNECTED,
    "RECONNECTING": State.RECONNECTING,
    "EXITING": State.DISCONNECTING,
}


def _mgmt_quote(value: str) -> str:
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


class VpnConnection(QObject):
    state_changed = pyqtSignal(object, str)        # State, detail text
    log_line = pyqtSignal(str)
    finished = pyqtSignal(bool, str)               # user_requested, reason
    # need ("Auth" / "Private Key" / ...), wants_username, is_retry
    credentials_needed = pyqtSignal(str, bool, bool)
    connected_info = pyqtSignal(str)               # local VPN IP (may be "")
    bytecount = pyqtSignal(int, int)               # rx, tx

    def __init__(self, profile: Profile, parent: QObject | None = None):
        super().__init__(parent)
        self.profile = profile
        self.state = State.IDLE
        self.detail = "Disconnected"
        self.rx = self.tx = 0
        self.local_ip = ""
        self.username = profile.username
        self.password: str | None = None
        self._auth_failed_once = False
        self._user_requested_stop = False
        self._exit_reason = ""
        self._buffer = b""
        self._mgmt_password = pysecrets.token_urlsafe(24)
        self._pwfile: str | None = None
        self._proc: QProcess | None = None
        self._sock: QTcpSocket | None = None
        self._mgmt_port = 0
        self._retry_timer = QTimer(self)
        self._retry_timer.setInterval(300)
        self._retry_timer.timeout.connect(self._try_mgmt_connect)
        self._connect_attempts = 0

    # ---------------------------------------------------------------- start

    def start(self) -> None:
        self._set_state(State.STARTING, "Waiting for authorization…")
        self._mgmt_port = self._pick_free_port()
        fd, self._pwfile = tempfile.mkstemp(prefix="hypr-ovpn-mgmt-")
        with os.fdopen(fd, "w") as f:
            f.write(self._mgmt_password + "\n")
        os.chmod(self._pwfile, 0o600)

        args = [
            OPENVPN_BIN,
            "--cd", self.profile.cd_dir,
            "--config", self.profile.config_path,
            "--management", "127.0.0.1", str(self._mgmt_port), self._pwfile,
            "--management-query-passwords",
            "--management-hold",
            "--auth-retry", "interact",
            "--verb", "3",
        ]
        # Force an auth prompt when the profile has a username but the config
        # itself never asks for one.
        if self.username and not self.profile.has_auth_directive:
            args.append("--auth-user-pass")

        self._proc = QProcess(self)
        self._proc.setProcessChannelMode(QProcess.ProcessChannelMode.MergedChannels)
        self._proc.readyReadStandardOutput.connect(self._on_proc_output)
        self._proc.finished.connect(self._on_proc_finished)
        self._proc.errorOccurred.connect(self._on_proc_error)
        self.log_line.emit(f"[hypr-ovpn] launching: pkexec openvpn --config {self.profile.config_path}")
        self._proc.start(PKEXEC_BIN, args)
        self._connect_attempts = 0
        self._retry_timer.start()

    @staticmethod
    def _pick_free_port() -> int:
        server = QTcpServer()
        server.listen(QHostAddress.SpecialAddress.LocalHost, 0)
        port = server.serverPort()
        server.close()
        return port

    # ------------------------------------------------------------ mgmt link

    def _try_mgmt_connect(self) -> None:
        if self._proc is None or self._proc.state() == QProcess.ProcessState.NotRunning:
            self._retry_timer.stop()
            return
        self._connect_attempts += 1
        if self._connect_attempts > 600:  # ~3 min: pkexec dialog abandoned
            self._retry_timer.stop()
            self.log_line.emit("[hypr-ovpn] gave up waiting for the management interface")
            self.stop()
            return
        if self._sock is not None:
            return
        sock = QTcpSocket(self)
        sock.connected.connect(lambda: self._on_mgmt_connected(sock))
        sock.readyRead.connect(lambda: self._on_mgmt_data(sock))
        sock.errorOccurred.connect(lambda _e: self._on_mgmt_socket_error(sock))
        sock.connectToHost(QHostAddress.SpecialAddress.LocalHost, self._mgmt_port)
        self._sock = sock

    def _on_mgmt_socket_error(self, sock: QTcpSocket) -> None:
        # openvpn not listening yet — drop and let the retry timer try again.
        if sock is self._sock and self.state == State.STARTING:
            sock.deleteLater()
            self._sock = None

    def _on_mgmt_connected(self, sock: QTcpSocket) -> None:
        self._retry_timer.stop()
        self.log_line.emit("[hypr-ovpn] management interface attached")
        self._send(self._mgmt_password)          # answer the password prompt
        self._send("state on")
        self._send("log on")
        self._send("bytecount 5")
        self._send("hold release")
        if self.state == State.STARTING:
            self._set_state(State.CONNECTING, "Connecting…")

    def _send(self, line: str) -> None:
        if self._sock and self._sock.state() == QTcpSocket.SocketState.ConnectedState:
            self._sock.write((line + "\n").encode())

    # ---------------------------------------------------------- mgmt parsing

    def _on_mgmt_data(self, sock: QTcpSocket) -> None:
        if sock is not self._sock:
            return
        self._buffer += bytes(sock.readAll())
        while b"\n" in self._buffer:
            raw, self._buffer = self._buffer.split(b"\n", 1)
            self._handle_mgmt_line(raw.decode(errors="replace").rstrip("\r"))

    def _handle_mgmt_line(self, line: str) -> None:
        if not line:
            return
        if line.startswith(">STATE:"):
            self._handle_state(line[len(">STATE:"):])
        elif line.startswith(">LOG:"):
            parts = line[len(">LOG:"):].split(",", 2)
            self.log_line.emit(parts[2] if len(parts) == 3 else line)
        elif line.startswith(">PASSWORD:"):
            self._handle_password(line[len(">PASSWORD:"):])
        elif line.startswith(">BYTECOUNT:"):
            try:
                rx, tx = line[len(">BYTECOUNT:"):].split(",")
                self.rx, self.tx = int(rx), int(tx)
                self.bytecount.emit(self.rx, self.tx)
            except ValueError:
                pass
        elif line.startswith(">HOLD:"):
            self._send("hold release")
        elif line.startswith(">FATAL:"):
            self._exit_reason = line[len(">FATAL:"):]
            self.log_line.emit(line)
        elif line.startswith((">INFO:", "SUCCESS:")):
            pass
        elif line.startswith("ERROR:"):
            self.log_line.emit(f"[mgmt] {line}")

    def _handle_state(self, payload: str) -> None:
        # ts,STATE,desc,local_ip,remote_ip,...
        fields = payload.split(",")
        name = fields[1] if len(fields) > 1 else ""
        desc = fields[2] if len(fields) > 2 else ""
        state = _STATE_MAP.get(name)
        if state is None:
            return
        if state == State.CONNECTED:
            self.local_ip = fields[3] if len(fields) > 3 else ""
            self.connected_info.emit(self.local_ip)
            self._set_state(state, f"Connected ({self.local_ip})" if self.local_ip else "Connected")
        elif state == State.RECONNECTING:
            self._set_state(state, desc or "Reconnecting…")
        elif state == State.DISCONNECTING:
            if desc and not self._exit_reason:
                self._exit_reason = desc
            self._set_state(state, "Disconnecting…")
        else:
            self._set_state(state, desc or name.capitalize())

    def _handle_password(self, payload: str) -> None:
        if payload.startswith("Verification Failed"):
            self._auth_failed_once = True
            self.password = None
            self.log_line.emit(f"[hypr-ovpn] authentication failed: {payload}")
            return
        if payload.startswith("Need "):
            # Need 'Auth' username/password   |   Need 'Private Key' password
            try:
                need = payload.split("'")[1]
            except IndexError:
                need = "Auth"
            wants_username = "username" in payload
            if (need == "Auth" and self.username and self.password is not None
                    and not self._auth_failed_once):
                self._supply(need, wants_username)
            else:
                self.credentials_needed.emit(need, wants_username, self._auth_failed_once)

    def supply_credentials(self, need: str, username: str, password: str) -> None:
        """Called by the UI after prompting the user."""
        if username:
            self.username = username
        self.password = password
        self._auth_failed_once = False
        self._supply(need, wants_username=need == "Auth" and bool(self.username))

    def cancel_credentials(self) -> None:
        self.log_line.emit("[hypr-ovpn] credential prompt cancelled — stopping")
        self.stop()

    def _supply(self, need: str, wants_username: bool) -> None:
        if wants_username:
            self._send(f"username {_mgmt_quote(need)} {_mgmt_quote(self.username)}")
        self._send(f"password {_mgmt_quote(need)} {_mgmt_quote(self.password or '')}")

    # ----------------------------------------------------------------- stop

    def stop(self) -> None:
        """User-requested disconnect."""
        self._user_requested_stop = True
        if self.state not in (State.IDLE, State.DISCONNECTING):
            self._set_state(State.DISCONNECTING, "Disconnecting…")
        if self._sock and self._sock.state() == QTcpSocket.SocketState.ConnectedState:
            self._send("signal SIGTERM")
            # Belt and braces: if openvpn ignores us, escalate shortly.
            QTimer.singleShot(5000, self._force_kill)
        else:
            self._force_kill()

    def _force_kill(self) -> None:
        if self._proc and self._proc.state() != QProcess.ProcessState.NotRunning:
            self._proc.terminate()
            QTimer.singleShot(3000, lambda: (
                self._proc.kill() if self._proc and self._proc.state() != QProcess.ProcessState.NotRunning else None
            ))

    # ------------------------------------------------------------- process

    def _on_proc_output(self) -> None:
        if self._proc is None:
            return
        data = bytes(self._proc.readAllStandardOutput()).decode(errors="replace")
        for line in data.splitlines():
            if line.strip():
                self.log_line.emit(line)
                if "AUTH_FAILED" in line and not self._exit_reason:
                    self._exit_reason = "auth-failure"

    def _on_proc_error(self, error: QProcess.ProcessError) -> None:
        if error == QProcess.ProcessError.FailedToStart:
            self._exit_reason = "failed to start pkexec/openvpn"
            self.log_line.emit("[hypr-ovpn] failed to start pkexec/openvpn")

    def _on_proc_finished(self, exit_code: int, _status) -> None:
        self._retry_timer.stop()
        if self._sock:
            self._sock.abort()
            self._sock.deleteLater()
            self._sock = None
        if self._pwfile:
            Path(self._pwfile).unlink(missing_ok=True)
            self._pwfile = None
        reason = self._exit_reason
        if not reason:
            if exit_code == 126 or exit_code == 127:
                reason = "authorization was cancelled"
            elif exit_code != 0 and not self._user_requested_stop:
                reason = f"openvpn exited with code {exit_code}"
        self.log_line.emit(f"[hypr-ovpn] process finished (code {exit_code})")
        self._set_state(State.IDLE, "Disconnected")
        self.finished.emit(self._user_requested_stop, reason)

    # ------------------------------------------------------------------ misc

    def _set_state(self, state: State, detail: str) -> None:
        if state == self.state and detail == self.detail:
            return
        self.state = state
        self.detail = detail
        if state == State.IDLE:
            self.rx = self.tx = 0
        self.state_changed.emit(state, detail)
