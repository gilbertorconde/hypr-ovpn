"""Password storage in the system keyring via libsecret's `secret-tool`.

Falls back to a 0600 file under the config dir if no Secret Service is
available (base64-obfuscated only — the keyring is strongly preferred and
used automatically when present).
"""

from __future__ import annotations

import base64
import json
import shutil
import subprocess

from .profiles import CONFIG_DIR

_ATTRS = ["application", "hypr-ovpn", "profile"]
_FALLBACK_FILE = CONFIG_DIR / "secrets.json"

SECRET_TOOL = shutil.which("secret-tool")


def backend_name() -> str:
    return "system keyring" if SECRET_TOOL else "local file (install libsecret + a keyring for secure storage)"


def store(profile_id: str, password: str) -> bool:
    if SECRET_TOOL:
        proc = subprocess.run(
            [SECRET_TOOL, "store", f"--label=hypr-ovpn VPN password ({profile_id})",
             *_pairs(profile_id)],
            input=password.encode(), capture_output=True,
        )
        return proc.returncode == 0
    data = _read_fallback()
    data[profile_id] = base64.b64encode(password.encode()).decode()
    _write_fallback(data)
    return True


def lookup(profile_id: str) -> str | None:
    if SECRET_TOOL:
        proc = subprocess.run(
            [SECRET_TOOL, "lookup", *_pairs(profile_id)], capture_output=True,
        )
        if proc.returncode == 0:
            return proc.stdout.decode()
        return None
    raw = _read_fallback().get(profile_id)
    return base64.b64decode(raw).decode() if raw else None


def clear(profile_id: str) -> None:
    if SECRET_TOOL:
        subprocess.run([SECRET_TOOL, "clear", *_pairs(profile_id)], capture_output=True)
        return
    data = _read_fallback()
    if data.pop(profile_id, None) is not None:
        _write_fallback(data)


def _pairs(profile_id: str) -> list[str]:
    return ["application", "hypr-ovpn", "profile", profile_id]


def _read_fallback() -> dict:
    try:
        return json.loads(_FALLBACK_FILE.read_text())
    except (OSError, json.JSONDecodeError):
        return {}


def _write_fallback(data: dict) -> None:
    _FALLBACK_FILE.touch(mode=0o600, exist_ok=True)
    _FALLBACK_FILE.chmod(0o600)
    _FALLBACK_FILE.write_text(json.dumps(data))
