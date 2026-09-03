"""Profile storage: JSON metadata plus imported .ovpn configs.

Layout under $XDG_CONFIG_HOME/hypr-ovpn/:
    profiles.json       list of profile dicts
    configs/<id>.ovpn   imported copies of self-contained configs
"""

from __future__ import annotations

import json
import os
import re
import shutil
import uuid
from dataclasses import dataclass, field, asdict
from pathlib import Path

CONFIG_DIR = Path(os.environ.get("XDG_CONFIG_HOME", "~/.config")).expanduser() / "hypr-ovpn"
CONFIGS_SUBDIR = CONFIG_DIR / "configs"
PROFILES_FILE = CONFIG_DIR / "profiles.json"

# Directives whose (first) argument is an external file on disk. If a config
# uses any of these without an inline <block>, we must keep referencing the
# original file location instead of copying just the .ovpn.
_FILE_DIRECTIVES = {
    "ca", "cert", "key", "tls-auth", "tls-crypt", "tls-crypt-v2",
    "pkcs12", "secret", "dh", "extra-certs", "crl-verify", "askpass",
}

_INLINE_RE = re.compile(r"^\s*<(ca|cert|key|tls-auth|tls-crypt|tls-crypt-v2|pkcs12|secret|dh|extra-certs|crl-verify)>")


@dataclass
class Profile:
    id: str
    name: str
    config_path: str          # path of the .ovpn actually used at connect time
    cd_dir: str               # working dir for openvpn (resolves relative paths)
    username: str = ""
    has_saved_password: bool = False   # actual secret lives in the keyring
    autoconnect: bool = False
    imported: bool = True     # False = config referenced in place (external certs)
    has_auth_directive: bool = False   # config contains its own auth-user-pass line

    def to_dict(self) -> dict:
        return asdict(self)

    @staticmethod
    def from_dict(d: dict) -> "Profile":
        known = {f for f in Profile.__dataclass_fields__}
        return Profile(**{k: v for k, v in d.items() if k in known})


class ConfigAnalysis:
    def __init__(self, external_files: list[str], has_auth_directive: bool):
        self.external_files = external_files
        self.has_auth_directive = has_auth_directive

    @property
    def self_contained(self) -> bool:
        return not self.external_files


def analyze_config(path: Path) -> ConfigAnalysis:
    """Find directives that reference external files (and aren't inlined)."""
    text = path.read_text(errors="replace")
    inline_blocks = set(_INLINE_RE.findall(text))
    external: list[str] = []
    has_auth = False
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith(("#", ";")):
            continue
        parts = line.split()
        directive = parts[0]
        if directive == "auth-user-pass":
            has_auth = True
            if len(parts) > 1:
                external.append(line)
            continue
        if directive in _FILE_DIRECTIVES and len(parts) > 1 and directive not in inline_blocks:
            arg = parts[1].strip("'\"")
            if arg not in ("[inline]",):
                external.append(line)
    return ConfigAnalysis(external, has_auth)


class ProfileStore:
    def __init__(self) -> None:
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        CONFIGS_SUBDIR.mkdir(parents=True, exist_ok=True)
        self.profiles: list[Profile] = []
        self.load()

    def load(self) -> None:
        self.profiles = []
        if PROFILES_FILE.exists():
            try:
                data = json.loads(PROFILES_FILE.read_text())
                self.profiles = [Profile.from_dict(d) for d in data]
            except (json.JSONDecodeError, TypeError):
                # Keep a backup of the corrupt file rather than losing it.
                shutil.copy(PROFILES_FILE, PROFILES_FILE.with_suffix(".json.bad"))

    def save(self) -> None:
        tmp = PROFILES_FILE.with_suffix(".json.tmp")
        tmp.write_text(json.dumps([p.to_dict() for p in self.profiles], indent=2))
        tmp.replace(PROFILES_FILE)

    def get(self, profile_id: str) -> Profile | None:
        return next((p for p in self.profiles if p.id == profile_id), None)

    def find_by_name(self, name: str) -> Profile | None:
        return next((p for p in self.profiles if p.name.lower() == name.lower()), None)

    def create(self, name: str, source_config: str, username: str = "") -> Profile:
        """Register a new profile, importing the config if self-contained."""
        source = Path(source_config).expanduser().resolve()
        analysis = analyze_config(source)
        pid = uuid.uuid4().hex[:12]
        if analysis.self_contained:
            dest = CONFIGS_SUBDIR / f"{pid}.ovpn"
            shutil.copy(source, dest)
            dest.chmod(0o600)
            config_path, cd_dir, imported = str(dest), str(CONFIGS_SUBDIR), True
        else:
            config_path, cd_dir, imported = str(source), str(source.parent), False
        profile = Profile(
            id=pid, name=name, config_path=config_path, cd_dir=cd_dir,
            username=username, imported=imported,
            has_auth_directive=analysis.has_auth_directive,
        )
        self.profiles.append(profile)
        self.save()
        return profile

    def replace_config(self, profile: Profile, source_config: str) -> None:
        """Point an existing profile at a new .ovpn file."""
        source = Path(source_config).expanduser().resolve()
        analysis = analyze_config(source)
        if profile.imported and Path(profile.config_path).exists():
            Path(profile.config_path).unlink(missing_ok=True)
        if analysis.self_contained:
            dest = CONFIGS_SUBDIR / f"{profile.id}.ovpn"
            shutil.copy(source, dest)
            dest.chmod(0o600)
            profile.config_path, profile.cd_dir, profile.imported = str(dest), str(CONFIGS_SUBDIR), True
        else:
            profile.config_path, profile.cd_dir, profile.imported = str(source), str(source.parent), False
        profile.has_auth_directive = analysis.has_auth_directive
        self.save()

    def remove(self, profile: Profile) -> None:
        if profile.imported:
            Path(profile.config_path).unlink(missing_ok=True)
        self.profiles = [p for p in self.profiles if p.id != profile.id]
        self.save()
