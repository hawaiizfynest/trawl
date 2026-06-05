"""
Trawl - configuration and secret storage.

Non-sensitive settings live as JSON in %APPDATA%\\Trawl\\config.json. The FTP
password is stored in the OS credential store via keyring (Windows Credential
Manager). All paths fall back sensibly on non-Windows systems for development.

Written by LJ "HawaiizFynest" Eblacas
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, asdict

APP_NAME = "Trawl"

try:
    import keyring
    _KEYRING_OK = True
except Exception:  # pragma: no cover - keyring missing in some dev envs
    keyring = None
    _KEYRING_OK = False


def app_data_dir() -> str:
    base = os.environ.get("APPDATA") or os.path.join(os.path.expanduser("~"), ".config")
    path = os.path.join(base, APP_NAME)
    os.makedirs(path, exist_ok=True)
    return path


def log_dir() -> str:
    path = os.path.join(app_data_dir(), "logs")
    os.makedirs(path, exist_ok=True)
    return path


def config_path() -> str:
    return os.path.join(app_data_dir(), "config.json")


def database_path() -> str:
    return os.path.join(app_data_dir(), "state.db")


def keyring_available() -> bool:
    return _KEYRING_OK


@dataclass
class Config:
    # --- Connection ---
    host: str = ""
    port: int = 21
    username: str = ""
    mode: str = "ftps_explicit"   # "ftp" | "ftps_explicit" | "ftps_implicit"
    passive: bool = True
    verify_tls: bool = False
    timeout: int = 30

    # --- Sync ---
    remote_dir: str = "/"
    local_dir: str = ""
    recursive: bool = True
    preserve_structure: bool = True
    min_file_age_minutes: int = 5
    interval_hours: float = 6.0
    delete_remote_after: bool = False

    # --- App behaviour ---
    schedule_enabled: bool = False
    autosync_on_launch: bool = False
    minimize_to_tray: bool = True
    start_minimized: bool = False
    run_on_startup: bool = False
    notify_on_complete: bool = True

    # --- Deluge completion check (optional) ---
    deluge_enabled: bool = False
    deluge_url: str = ""
    deluge_verify_tls: bool = False

    # --- Updates ---
    check_updates_on_launch: bool = True

    # ---- password (keyring) ----
    def _secret_account(self) -> str:
        return f"{self.host}:{self.port}:{self.username}"

    def get_password(self) -> str:
        if not _KEYRING_OK or not self.host or not self.username:
            return ""
        try:
            return keyring.get_password(APP_NAME, self._secret_account()) or ""
        except Exception:
            return ""

    def set_password(self, password: str) -> bool:
        if not _KEYRING_OK or not self.host or not self.username:
            return False
        try:
            keyring.set_password(APP_NAME, self._secret_account(), password or "")
            return True
        except Exception:
            return False

    # ---- Deluge password (keyring) ----
    def _deluge_account(self) -> str:
        return "deluge"

    def get_deluge_password(self) -> str:
        if not _KEYRING_OK or not self.deluge_url:
            return ""
        try:
            return keyring.get_password(APP_NAME, self._deluge_account()) or ""
        except Exception:
            return ""

    def set_deluge_password(self, password: str) -> bool:
        if not _KEYRING_OK or not self.deluge_url:
            return False
        try:
            keyring.set_password(APP_NAME, self._deluge_account(), password or "")
            return True
        except Exception:
            return False

    # ---- GitHub token for updates (keyring) ----
    def get_github_token(self) -> str:
        if not _KEYRING_OK:
            return ""
        try:
            return keyring.get_password(APP_NAME, "github_token") or ""
        except Exception:
            return ""

    def set_github_token(self, token: str) -> bool:
        if not _KEYRING_OK:
            return False
        try:
            keyring.set_password(APP_NAME, "github_token", token or "")
            return True
        except Exception:
            return False

    # ---- load / save ----
    @classmethod
    def load(cls) -> "Config":
        p = config_path()
        if not os.path.exists(p):
            return cls()
        try:
            with open(p, "r", encoding="utf-8") as f:
                data = json.load(f)
            cfg = cls()
            for k, v in data.items():
                if hasattr(cfg, k):
                    setattr(cfg, k, v)
            return cfg
        except Exception:
            return cls()

    def save(self) -> None:
        with open(config_path(), "w", encoding="utf-8") as f:
            json.dump(asdict(self), f, indent=2)
