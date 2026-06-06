"""
Trawl - self-update from GitHub Releases.

Checks the latest release of the repo, downloads the Trawl.exe asset, then hands
off to a small batch script that waits for this process to exit, deletes the old
exe, moves the new one into place and relaunches it. Self-replacement only runs
for the built (frozen) executable on Windows.

The repo is private, so a GitHub token (fine-grained, read access to the repo)
is required - it is stored in Windows Credential Manager via keyring, never in
the build. Public repos work without a token.

Written by LJ "HawaiizFynest" Eblacas
"""
from __future__ import annotations

import json
import os
import ssl
import subprocess
import sys
import tempfile
import urllib.request

from PyQt6.QtCore import QObject, pyqtSignal, pyqtSlot

from version import __version__

GITHUB_OWNER = "HawaiizFynest"
GITHUB_REPO = "Trawl"
API_RELEASES = f"https://api.github.com/repos/{GITHUB_OWNER}/{GITHUB_REPO}/releases?per_page=50"
ASSET_NAME = "Trawl.exe"


def _parse_version(tag: str):
    tag = (tag or "").lstrip("vV").strip()
    parts = []
    for chunk in tag.split("."):
        digits = ""
        for ch in chunk:
            if ch.isdigit():
                digits += ch
            else:
                break
        parts.append(int(digits) if digits else 0)
    return tuple(parts) if parts else (0,)


def is_newer(latest: str, current: str) -> bool:
    return _parse_version(latest) > _parse_version(current)


def is_frozen() -> bool:
    return getattr(sys, "frozen", False)


def current_version() -> str:
    return __version__


def _ssl_ctx() -> ssl.SSLContext:
    return ssl.create_default_context()


def _auth_headers(token: str, accept: str) -> dict:
    headers = {"Accept": accept, "User-Agent": "Trawl-Updater",
               "X-GitHub-Api-Version": "2022-11-28"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


class _StripAuthOnRedirect(urllib.request.HTTPRedirectHandler):
    """When GitHub redirects an asset download to its signed storage URL, the
    Authorization header must not be forwarded or the storage host rejects it."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        new = super().redirect_request(req, fp, code, msg, headers, newurl)
        if new is not None:
            for h in list(new.headers.keys()):
                if h.lower() == "authorization":
                    del new.headers[h]
        return new


class UpdateChecker(QObject):
    # available, latest_version, asset_api_url, notes
    result = pyqtSignal(bool, str, str, str)
    error = pyqtSignal(str)

    def __init__(self, token: str = ""):
        super().__init__()
        self.token = token

    @pyqtSlot()
    def run(self) -> None:
        try:
            req = urllib.request.Request(
                API_RELEASES, headers=_auth_headers(self.token, "application/vnd.github+json"))
            with urllib.request.urlopen(req, timeout=20, context=_ssl_ctx()) as resp:
                releases = json.loads(resp.read().decode("utf-8"))

            candidates = []
            for rel in releases or []:
                if rel.get("draft"):
                    continue
                asset_url = ""
                for asset in rel.get("assets", []) or []:
                    if (asset.get("name", "") or "").lower() == ASSET_NAME.lower():
                        asset_url = asset.get("url", "")  # API url (works for private repos)
                        break
                if not asset_url:
                    continue  # nothing to install from this release
                candidates.append({
                    "tag": (rel.get("tag_name", "") or "").lstrip("vV"),
                    "when": rel.get("published_at") or rel.get("created_at") or "",
                    "url": asset_url,
                    "notes": rel.get("body", "") or "",
                })

            if not candidates:
                self.result.emit(False, current_version(), "", "")
                return

            # Choose the most recently published release, not the highest version
            # number (ISO 8601 timestamps sort chronologically as strings).
            candidates.sort(key=lambda c: c["when"], reverse=True)
            newest = candidates[0]
            running = current_version().lstrip("vV").strip().lower()
            available = newest["tag"].strip().lower() != running
            self.result.emit(available, newest["tag"], newest["url"], newest["notes"])
        except Exception as e:
            self.error.emit(self._friendly(e))

    @staticmethod
    def _friendly(e: Exception) -> str:
        msg = f"{type(e).__name__}: {e}"
        text = str(e)
        if "403" in text or "429" in text:
            msg += ("\n\nThe GitHub API may be rate-limiting unauthenticated "
                    "requests. Adding a GitHub token in the Updates section "
                    "raises the limit.")
        elif "404" in text or "401" in text:
            msg += ("\n\nNo matching release/asset was found. If the repo is "
                    "private, add a GitHub token with read access in the "
                    "Updates section.")
        return msg


class UpdateDownloader(QObject):
    progress = pyqtSignal(int, int)    # done, total
    finished = pyqtSignal(bool, str)   # ok, new_path_or_error

    def __init__(self, asset_api_url: str, token: str = ""):
        super().__init__()
        self.asset_api_url = asset_api_url
        self.token = token
        self._stop = False

    def request_stop(self) -> None:
        self._stop = True

    @pyqtSlot()
    def run(self) -> None:
        try:
            # Download to the temp dir, which is always writable and not subject
            # to Controlled Folder Access; the swap step moves it into place.
            new_path = os.path.join(tempfile.gettempdir(), "Trawl.new.exe")
            opener = urllib.request.build_opener(_StripAuthOnRedirect)
            req = urllib.request.Request(
                self.asset_api_url,
                headers=_auth_headers(self.token, "application/octet-stream"))
            with opener.open(req, timeout=120) as resp:
                total = int(resp.headers.get("Content-Length") or 0)
                done = 0
                with open(new_path, "wb") as f:
                    while True:
                        if self._stop:
                            self.finished.emit(False, "Cancelled.")
                            return
                        chunk = resp.read(65536)
                        if not chunk:
                            break
                        f.write(chunk)
                        done += len(chunk)
                        self.progress.emit(done, total)
            self.finished.emit(True, new_path)
        except Exception as e:
            self.finished.emit(False, f"{type(e).__name__}: {e}")


def apply_update_and_restart(new_exe: str) -> bool:
    """Windows + frozen only. Spawns a detached batch that waits for this
    process to exit, swaps the exe and relaunches. Returns False if it cannot
    run (e.g. launched from source), so the caller can fall back gracefully.

    Robustness notes:
      * Delays use ping, not timeout: a detached process has no console, and
        timeout aborts instantly there, so any timeout-based wait is a no-op.
      * The old exe is renamed aside (not deleted) before the new one is moved
        into place. Windows allows renaming a running/locked .exe but not
        deleting it, so the swap works even while the one-file bootloader is
        still releasing the old file.
      * If anything fails the script relaunches whatever exe is present, so the
        app is never left uninstalled.
      * Every step is logged to %APPDATA%\\Trawl\\logs\\update.log.
    """
    if not is_frozen() or not sys.platform.startswith("win"):
        return False
    current = sys.executable
    pid = os.getpid()
    appdata = os.environ.get("APPDATA") or os.path.expanduser("~")
    log_dir = os.path.join(appdata, "Trawl", "logs")
    try:
        os.makedirs(log_dir, exist_ok=True)
    except Exception:
        pass
    log_path = os.path.join(log_dir, "update.log")
    bat = os.path.join(tempfile.gettempdir(), "trawl_update.bat")
    script = f"""@echo off
setlocal enableextensions
set "PID={pid}"
set "OLD={current}"
set "NEW={new_exe}"
set "LOG={log_path}"
> "%LOG%" echo [update] start pid=%PID%
>> "%LOG%" echo [update] OLD=%OLD%
>> "%LOG%" echo [update] NEW=%NEW%

set /a w=0
:wait
tasklist /FI "PID eq %PID%" 2>NUL | find "%PID%" >NUL
if errorlevel 1 goto gone
set /a w+=1
if %w% geq 30 goto gone
ping -n 2 127.0.0.1 >NUL
goto wait
:gone
>> "%LOG%" echo [update] app exited (waited ~%w%s); settling
ping -n 3 127.0.0.1 >NUL

if exist "%OLD%.old" del "%OLD%.old" >NUL 2>&1
set /a r=0
:rename
move /Y "%OLD%" "%OLD%.old" >> "%LOG%" 2>&1
if not exist "%OLD%" goto renamed
set /a r+=1
if %r% geq 15 goto renamefail
ping -n 2 127.0.0.1 >NUL
goto rename

:renamefail
>> "%LOG%" echo [update] ERROR could not move old exe aside; relaunching existing
start "" "%OLD%"
goto cleanup

:renamed
>> "%LOG%" echo [update] old exe moved aside
move /Y "%NEW%" "%OLD%" >> "%LOG%" 2>&1
if exist "%OLD%" goto launch
>> "%LOG%" echo [update] ERROR new exe not in place; restoring previous
move /Y "%OLD%.old" "%OLD%" >> "%LOG%" 2>&1

:launch
>> "%LOG%" echo [update] launching %OLD%
start "" "%OLD%"

:cleanup
ping -n 2 127.0.0.1 >NUL
del "%OLD%.old" >NUL 2>&1
>> "%LOG%" echo [update] done
del "%~f0" >NUL 2>&1
"""
    with open(bat, "w", encoding="utf-8") as f:
        f.write(script)

    # CREATE_NO_WINDOW hides the console while still giving the batch a (hidden)
    # console so find/ping/tasklist work; CREATE_NEW_PROCESS_GROUP lets it
    # outlive this process. (DETACHED_PROCESS together with CREATE_NO_WINDOW is
    # contradictory and can pop a visible window, so it is not used.)
    CREATE_NEW_PROCESS_GROUP = 0x00000200
    CREATE_NO_WINDOW = 0x08000000
    subprocess.Popen(
        ["cmd", "/c", bat],
        creationflags=CREATE_NEW_PROCESS_GROUP | CREATE_NO_WINDOW,
        close_fds=True,
    )
    return True


def cleanup_stale_update() -> None:
    """Delete a leftover '<exe>.old' left by a previous self-update. Safe to
    call at startup; by then the old image is fully unlocked. Ignores errors."""
    try:
        if not is_frozen() or not sys.platform.startswith("win"):
            return
        old = sys.executable + ".old"
        if os.path.exists(old):
            os.remove(old)
    except Exception:
        pass
