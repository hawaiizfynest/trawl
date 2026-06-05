"""
Trawl - background workers.

SyncWorker performs one full sync pass (connect, scan, download eligible files)
on a worker thread and reports progress through Qt signals. TestWorker performs
a one-shot connection test. Both are designed to run via QThread.moveToThread.

Written by LJ "HawaiizFynest" Eblacas
"""
from __future__ import annotations

import os
import re
import shutil
import time

from PyQt6.QtCore import QObject, pyqtSignal, pyqtSlot

from config import Config
from database import Database
from deluge import DelugeClient
from ftpclient import FtpClient, RemoteFile

_INVALID = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


def human_size(n: float) -> str:
    units = ("B", "KB", "MB", "GB", "TB")
    for unit in units:
        if n < 1024 or unit == "TB":
            return f"{int(n)} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} TB"


def _sanitize_component(name: str) -> str:
    cleaned = _INVALID.sub("_", name).rstrip(" .")
    return cleaned or "_"


def _client_from_config(cfg: Config) -> FtpClient:
    return FtpClient(
        host=cfg.host, port=cfg.port, username=cfg.username,
        password=cfg.get_password(), mode=cfg.mode, passive=cfg.passive,
        verify_tls=cfg.verify_tls, timeout=cfg.timeout,
    )


class TestWorker(QObject):
    finished = pyqtSignal(bool, str)

    def __init__(self, cfg: Config):
        super().__init__()
        self.cfg = cfg

    @pyqtSlot()
    def run(self) -> None:
        client = _client_from_config(self.cfg)
        try:
            welcome = client.connect()
            try:
                count = len(client._list_dir(self.cfg.remote_dir or "/"))
            except Exception:
                count = -1
            client.quit()
            msg = (welcome or "Connected.").strip()
            if count >= 0:
                msg += f"\nListed {count} item(s) in {self.cfg.remote_dir or '/'}."
            self.finished.emit(True, msg)
        except Exception as e:
            self.finished.emit(False, f"{type(e).__name__}: {e}")


class DelugeTestWorker(QObject):
    finished = pyqtSignal(bool, str)

    def __init__(self, cfg: Config):
        super().__init__()
        self.cfg = cfg

    @pyqtSlot()
    def run(self) -> None:
        try:
            client = DelugeClient(
                self.cfg.deluge_url, self.cfg.get_deluge_password(),
                self.cfg.deluge_verify_tls)
            client.login()
            torrents = client.get_torrents()
            done = sum(1 for t in torrents if DelugeClient.is_complete(t))
            self.finished.emit(
                True, f"Connected to Deluge. {len(torrents)} torrent(s): "
                      f"{done} complete, {len(torrents) - done} still downloading.")
        except Exception as e:
            self.finished.emit(False, f"{type(e).__name__}: {e}")


class SyncWorker(QObject):
    log = pyqtSignal(str, str)                 # level, message
    status = pyqtSignal(str)                    # current operation line
    file_progress = pyqtSignal(int, int)        # bytes_done, bytes_total
    overall = pyqtSignal(int, int)              # files_done, files_total
    transfer = pyqtSignal(str, int, str)        # name, size, status
    finished = pyqtSignal(dict)                 # summary

    def __init__(self, cfg: Config):
        super().__init__()
        self.cfg = cfg
        self._stop = False

    def request_stop(self) -> None:
        self._stop = True

    def _deluge_incomplete(self):
        """Set of lowercased torrent names that are NOT finished, or None to
        skip Deluge gating (disabled, or the check failed)."""
        if not self.cfg.deluge_enabled or not self.cfg.deluge_url:
            return None
        try:
            client = DelugeClient(
                self.cfg.deluge_url, self.cfg.get_deluge_password(),
                self.cfg.deluge_verify_tls)
            client.login()
            names = {n.lower() for n in client.incomplete_names()}
            self.log.emit("info", f"Deluge: {len(names)} torrent(s) still downloading.")
            return names
        except Exception as e:
            self.log.emit("warn", f"Deluge check skipped ({type(e).__name__}: {e}); "
                                  f"using the file-age check instead.")
            return None

    @staticmethod
    def _belongs_to_incomplete(remote_path: str, incomplete: set) -> bool:
        parts = [p.lower() for p in remote_path.split("/") if p]
        if not parts:
            return False
        base = parts[-1]
        partset = set(parts)
        for name in incomplete:
            if name in partset or name == base:
                return True
        return False

    def _local_path_for(self, rf: RemoteFile, root: str) -> str:
        rel = rf.path[len(root):] if rf.path.startswith(root) else os.path.basename(rf.path)
        parts = [p for p in rel.split("/") if p]
        if not self.cfg.preserve_structure:
            parts = [parts[-1]] if parts else [os.path.basename(rf.path)]
        safe = [_sanitize_component(p) for p in parts] or ["_"]
        return os.path.join(self.cfg.local_dir, *safe)

    @pyqtSlot()
    def run(self) -> None:
        summary = {"downloaded": 0, "skipped": 0, "failed": 0, "bytes": 0, "error": None}
        if not self.cfg.local_dir:
            summary["error"] = "No destination folder set."
            self.finished.emit(summary)
            return
        os.makedirs(self.cfg.local_dir, exist_ok=True)

        db = Database()
        client = _client_from_config(self.cfg)
        root = (self.cfg.remote_dir or "/").rstrip("/") or "/"

        try:
            self.status.emit("Connecting...")
            welcome = client.connect()
            self.log.emit("info", (welcome or "Connected.").strip())

            self.status.emit("Scanning remote directory...")
            all_files = list(client.walk(root, recursive=self.cfg.recursive))
            self.log.emit("info", f"Found {len(all_files)} file(s) on the server.")

            now = time.time()
            min_age = self.cfg.min_file_age_minutes * 60
            incomplete = self._deluge_incomplete()
            eligible = []
            for rf in all_files:
                if incomplete is not None and self._belongs_to_incomplete(rf.path, incomplete):
                    summary["skipped"] += 1
                    self.log.emit("info", f"Skipping {os.path.basename(rf.path)} - "
                                          f"torrent not finished.")
                    continue
                if min_age and rf.modify_epoch and (now - rf.modify_epoch) < min_age:
                    summary["skipped"] += 1
                    continue
                if db.is_completed(rf.path, rf.size):
                    summary["skipped"] += 1
                    continue
                eligible.append(rf)

            total = len(eligible)
            self.overall.emit(0, total)
            self.log.emit("info", f"{total} new file(s) to download.")

            for index, rf in enumerate(eligible):
                if self._stop:
                    self.log.emit("warn", "Stopped by user.")
                    break

                local_path = self._local_path_for(rf, root)
                name = os.path.basename(local_path)

                try:
                    free = shutil.disk_usage(self.cfg.local_dir).free
                    if rf.size and free < rf.size:
                        self.log.emit("error", f"Not enough disk space for {name} "
                                               f"({human_size(rf.size)} needed). Skipping.")
                        self.transfer.emit(name, rf.size, "no space")
                        summary["failed"] += 1
                        self.overall.emit(index + 1, total)
                        continue
                except Exception:
                    pass

                start = time.time()

                def progress_cb(done: int, tot: int, _name=name, _start=start) -> None:
                    elapsed = max(time.time() - _start, 0.001)
                    speed = done / elapsed
                    pct = (done / tot * 100) if tot else 0
                    eta = (tot - done) / speed if (tot and speed > 0) else 0
                    self.file_progress.emit(done, tot)
                    self.status.emit(
                        f"{_name} - {pct:0.0f}% - {human_size(speed)}/s - "
                        f"ETA {int(eta // 60)}:{int(eta % 60):02d}"
                    )

                self.log.emit("info", f"Downloading {name} ({human_size(rf.size)})...")
                result = client.download(rf, local_path, progress_cb, lambda: self._stop)

                if result == "completed":
                    db.record(rf.path, rf.size, rf.modify_epoch, local_path, "completed")
                    summary["downloaded"] += 1
                    summary["bytes"] += rf.size
                    self.transfer.emit(name, rf.size, "completed")
                    self.log.emit("info", f"Saved {name}.")
                    if self.cfg.delete_remote_after:
                        if client.delete(rf.path):
                            self.log.emit("warn", f"Deleted {rf.path} from server.")
                        else:
                            self.log.emit("error", f"Could not delete {rf.path} from server.")
                elif result == "stopped":
                    self.log.emit("warn", "Stopped by user.")
                    break
                elif result == "size_mismatch":
                    summary["failed"] += 1
                    self.transfer.emit(name, rf.size, "incomplete")
                    self.log.emit("error", f"{name} did not match expected size; will retry next run.")
                else:
                    summary["failed"] += 1
                    self.transfer.emit(name, rf.size, "error")
                    self.log.emit("error", f"Failed to download {name}.")

                self.overall.emit(index + 1, total)

            client.quit()
        except Exception as e:
            summary["error"] = f"{type(e).__name__}: {e}"
            self.log.emit("error", summary["error"])
            try:
                client.quit()
            except Exception:
                pass
        finally:
            db.close()

        self.finished.emit(summary)
