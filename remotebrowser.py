"""
Trawl - remote file browser.

Lets you browse the seedbox and download one or more chosen files immediately,
without scanning or syncing the whole folder. Useful for quick tests and one-off
grabs.

A RemoteSession owns a single FTP connection on a background thread and serves
list/download requests one at a time, so the UI never freezes and the ftplib
connection is never touched from two threads at once.

Written by LJ "HawaiizFynest" Eblacas
"""
from __future__ import annotations

import os

from PyQt6.QtCore import QObject, Qt, QThread, pyqtSignal, pyqtSlot
from PyQt6.QtWidgets import (
    QAbstractItemView, QDialog, QHBoxLayout, QLabel, QListWidget,
    QListWidgetItem, QMessageBox, QProgressBar, QPushButton, QStyle,
    QVBoxLayout,
)

from config import Config
from database import Database
from ftpclient import FtpClient, RemoteFile
from worker import _sanitize_component, human_size


class RemoteSession(QObject):
    connected = pyqtSignal(bool, str)
    listed = pyqtSignal(str, list)            # path, [(name, is_dir, size)]
    list_error = pyqtSignal(str)
    dl_progress = pyqtSignal(str, int, int)   # name, done, total
    dl_file_done = pyqtSignal(str, str)       # name, status
    dl_all_done = pyqtSignal(dict)            # summary
    log = pyqtSignal(str, str)

    def __init__(self, cfg: Config):
        super().__init__()
        self.cfg = cfg
        self.client = None
        self._stop = False
        self._closing = False

    # These two are plain methods, safe to call from the GUI thread because
    # they only flip a boolean that the worker loop reads.
    def request_stop(self) -> None:
        self._stop = True

    def prepare_close(self) -> None:
        self._closing = True
        self._stop = True

    def _new_client(self) -> FtpClient:
        return FtpClient(
            host=self.cfg.host, port=self.cfg.port, username=self.cfg.username,
            password=self.cfg.get_password(), mode=self.cfg.mode,
            passive=self.cfg.passive, verify_tls=self.cfg.verify_tls,
            timeout=self.cfg.timeout,
        )

    @pyqtSlot()
    def open(self) -> None:
        try:
            self.client = self._new_client()
            welcome = self.client.connect()
            self.connected.emit(True, (welcome or "Connected.").strip())
        except Exception as e:
            self.connected.emit(False, f"{type(e).__name__}: {e}")

    def _reconnect(self) -> bool:
        try:
            if self.client:
                self.client.quit()
        except Exception:
            pass
        try:
            self.client = self._new_client()
            self.client.connect()
            return True
        except Exception as e:
            self.log.emit("error", f"Reconnect failed: {e}")
            return False

    @pyqtSlot(str)
    def list_dir(self, path: str) -> None:
        if self.client is None:
            self.list_error.emit("Not connected.")
            return
        try:
            entries = self.client.list_dir_entries(path or "/")
            self.listed.emit(path or "/", entries)
        except Exception as e:
            self.list_error.emit(f"{type(e).__name__}: {e}")

    @pyqtSlot(list)
    def download_files(self, items: list) -> None:
        # items: list of (remote_path, size, name)
        self._stop = False
        summary = {"downloaded": 0, "failed": 0, "bytes": 0, "stopped": False, "error": None}
        if self.client is None:
            summary["error"] = "Not connected."
            self.dl_all_done.emit(summary)
            return
        db = Database()
        try:
            for remote_path, size, name in items:
                if self._stop:
                    summary["stopped"] = True
                    break
                local_path = os.path.join(self.cfg.local_dir, _sanitize_component(name))
                rf = RemoteFile(remote_path, int(size), None)

                def cb(done, total, _n=name):
                    self.dl_progress.emit(_n, done, total)

                self.log.emit("info", f"Downloading {name} ({human_size(size)})...")
                result = self.client.download(rf, local_path, cb, lambda: self._stop)
                if result == "completed":
                    db.record(remote_path, int(size), None, local_path, "completed")
                    summary["downloaded"] += 1
                    summary["bytes"] += int(size)
                    self.dl_file_done.emit(name, "completed")
                elif result == "stopped":
                    summary["stopped"] = True
                    self.dl_file_done.emit(name, "stopped")
                    break
                else:
                    summary["failed"] += 1
                    self.dl_file_done.emit(name, result)
        finally:
            db.close()

        if summary.get("stopped") and not self._closing:
            # control connection can be desynced after an aborted transfer
            self._reconnect()
        self.dl_all_done.emit(summary)

    @pyqtSlot()
    def close(self) -> None:
        if self.client is not None:
            try:
                self.client.quit()
            except Exception:
                pass
            self.client = None


class RemoteBrowserDialog(QDialog):
    # request signals -> session (queued across threads automatically)
    req_open = pyqtSignal()
    req_list = pyqtSignal(str)
    req_download = pyqtSignal(list)
    req_close = pyqtSignal()

    def __init__(self, cfg: Config, parent=None):
        super().__init__(parent)
        self.cfg = cfg
        self.cur_path = (cfg.remote_dir or "/").rstrip("/") or "/"
        self.downloading = False
        self.entries = []   # current (name, is_dir, size)

        self.setWindowTitle("Browse remote files")
        self.resize(640, 540)

        lay = QVBoxLayout(self)

        nav = QHBoxLayout()
        self.btn_up = QPushButton("Up")
        self.btn_up.clicked.connect(self._go_up)
        self.btn_refresh = QPushButton("Refresh")
        self.btn_refresh.clicked.connect(lambda: self._list(self.cur_path))
        self.lbl_path = QLabel(self.cur_path)
        self.lbl_path.setObjectName("Subtle")
        nav.addWidget(self.btn_up)
        nav.addWidget(self.btn_refresh)
        nav.addWidget(self.lbl_path, 1)
        lay.addLayout(nav)

        self.list = QListWidget()
        self.list.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.list.itemDoubleClicked.connect(self._on_double_click)
        lay.addWidget(self.list, 1)

        self.lbl_status = QLabel("Connecting...")
        self.lbl_status.setObjectName("Subtle")
        lay.addWidget(self.lbl_status)
        self.bar = QProgressBar()
        self.bar.setValue(0)
        lay.addWidget(self.bar)

        btns = QHBoxLayout()
        self.btn_download = QPushButton("Download selected")
        self.btn_download.setObjectName("Primary")
        self.btn_download.setEnabled(False)
        self.btn_download.clicked.connect(self._download_selected)
        self.btn_stop = QPushButton("Stop")
        self.btn_stop.setObjectName("Danger")
        self.btn_stop.setEnabled(False)
        self.btn_stop.clicked.connect(self._stop)
        self.btn_close = QPushButton("Close")
        self.btn_close.clicked.connect(self.close)
        btns.addWidget(self.btn_download)
        btns.addWidget(self.btn_stop)
        btns.addStretch(1)
        btns.addWidget(self.btn_close)
        lay.addLayout(btns)

        # session on its own thread
        self.thread = QThread(self)
        self.session = RemoteSession(cfg)
        self.session.moveToThread(self.thread)
        self.req_open.connect(self.session.open)
        self.req_list.connect(self.session.list_dir)
        self.req_download.connect(self.session.download_files)
        self.req_close.connect(self.session.close)
        self.session.connected.connect(self._on_connected)
        self.session.listed.connect(self._on_listed)
        self.session.list_error.connect(self._on_list_error)
        self.session.dl_progress.connect(self._on_dl_progress)
        self.session.dl_file_done.connect(self._on_dl_file_done)
        self.session.dl_all_done.connect(self._on_dl_all_done)
        self.thread.start()
        self.req_open.emit()

    # ---- helpers ----
    def _set_busy(self, busy: bool) -> None:
        self.btn_up.setEnabled(not busy)
        self.btn_refresh.setEnabled(not busy)
        self.list.setEnabled(not busy)

    def _list(self, path: str) -> None:
        self._set_busy(True)
        self.lbl_status.setText(f"Listing {path} ...")
        self.req_list.emit(path)

    def _child_path(self, name: str) -> str:
        base = self.cur_path.rstrip("/")
        return (base + "/" + name) if base else "/" + name

    def _go_up(self) -> None:
        if self.cur_path in ("/", ""):
            return
        parent = self.cur_path.rstrip("/").rsplit("/", 1)[0] or "/"
        self._list(parent)

    # ---- session signal handlers ----
    @pyqtSlot(bool, str)
    def _on_connected(self, ok: bool, message: str) -> None:
        if ok:
            self.lbl_status.setText(message)
            self._list(self.cur_path)
        else:
            self.lbl_status.setText("Connection failed.")
            QMessageBox.critical(
                self, "Trawl", message +
                "\n\nIf this is a TLS error, turn off 'Verify TLS certificate' "
                "on the Connection tab.")

    @pyqtSlot(str, list)
    def _on_listed(self, path: str, entries: list) -> None:
        self.cur_path = path.rstrip("/") or "/"
        self.lbl_path.setText(self.cur_path)
        self.entries = entries
        self.list.clear()
        folder_icon = self.style().standardIcon(QStyle.StandardPixmap.SP_DirIcon)
        file_icon = self.style().standardIcon(QStyle.StandardPixmap.SP_FileIcon)
        for name, is_dir, size in entries:
            label = name if is_dir else f"{name}    ({human_size(size)})"
            item = QListWidgetItem(folder_icon if is_dir else file_icon, label)
            item.setData(Qt.ItemDataRole.UserRole, (name, is_dir, size))
            self.list.addItem(item)
        self._set_busy(False)
        nfiles = sum(1 for e in entries if not e[1])
        self.lbl_status.setText(
            f"{len(entries)} item(s), {nfiles} file(s). "
            f"Double-click a folder to open it; select files and Download.")
        self.btn_download.setEnabled(nfiles > 0)

    @pyqtSlot(str)
    def _on_list_error(self, message: str) -> None:
        self._set_busy(False)
        self.lbl_status.setText("Could not list folder.")
        QMessageBox.warning(self, "Trawl", f"Could not list folder:\n{message}")

    def _on_double_click(self, item: QListWidgetItem) -> None:
        name, is_dir, _size = item.data(Qt.ItemDataRole.UserRole)
        if is_dir:
            self._list(self._child_path(name))

    def _download_selected(self) -> None:
        if not self.cfg.local_dir:
            QMessageBox.warning(self, "Trawl", "Set a destination folder on the Settings tab first.")
            return
        items = []
        for it in self.list.selectedItems():
            name, is_dir, size = it.data(Qt.ItemDataRole.UserRole)
            if is_dir:
                continue
            items.append((self._child_path(name), size, name))
        if not items:
            QMessageBox.information(self, "Trawl", "Select one or more files (not folders).")
            return
        self.downloading = True
        self._set_busy(True)
        self.btn_download.setEnabled(False)
        self.btn_stop.setEnabled(True)
        self.btn_close.setEnabled(False)
        self.bar.setValue(0)
        self.lbl_status.setText(f"Downloading {len(items)} file(s)...")
        self.req_download.emit(items)

    def _stop(self) -> None:
        self.session.request_stop()
        self.btn_stop.setEnabled(False)
        self.lbl_status.setText("Stopping...")

    @pyqtSlot(str, int, int)
    def _on_dl_progress(self, name: str, done: int, total: int) -> None:
        if total > 0:
            self.bar.setRange(0, total)
            self.bar.setValue(done)
        else:
            self.bar.setRange(0, 0)
        pct = (done / total * 100) if total else 0
        self.lbl_status.setText(
            f"{name} - {pct:0.0f}% ({human_size(done)} / {human_size(total)})")

    @pyqtSlot(str, str)
    def _on_dl_file_done(self, name: str, status: str) -> None:
        self.lbl_status.setText(f"{name}: {status}")

    @pyqtSlot(dict)
    def _on_dl_all_done(self, summary: dict) -> None:
        self.downloading = False
        self._set_busy(False)
        self.btn_stop.setEnabled(False)
        self.btn_close.setEnabled(True)
        self.bar.setRange(0, 100)
        self.bar.setValue(0)
        if summary.get("error"):
            self.lbl_status.setText(f"Error: {summary['error']}")
        else:
            d, f = summary["downloaded"], summary["failed"]
            extra = " (stopped)" if summary.get("stopped") else ""
            self.lbl_status.setText(
                f"Done - {d} downloaded, {f} failed{extra} "
                f"({human_size(summary['bytes'])}). Saved to your destination folder.")
        self.btn_download.setEnabled(any(not e[1] for e in self.entries))

    def closeEvent(self, event) -> None:
        self.session.prepare_close()   # abort any transfer, skip reconnect
        self.req_close.emit()          # queue the FTP quit
        self.thread.quit()
        self.thread.wait(4000)
        event.accept()
