"""
Trawl - main window.

A clean dark PyQt6 interface with four tabs (Dashboard, Connection, Settings,
Log), a system-tray presence, an interval scheduler, and a background sync
worker running on a QThread.

Written by LJ "HawaiizFynest" Eblacas
"""
from __future__ import annotations

import datetime
import os
import sys

from PyQt6.QtCore import Qt, QThread, QTimer, pyqtSlot
from PyQt6.QtGui import QAction, QIcon
from PyQt6.QtWidgets import (
    QApplication, QCheckBox, QComboBox, QDoubleSpinBox, QFileDialog, QFormLayout,
    QGroupBox, QHBoxLayout, QHeaderView, QLabel, QLineEdit, QMainWindow, QMenu,
    QMessageBox, QPlainTextEdit, QProgressBar, QPushButton, QSpinBox, QStyle,
    QSystemTrayIcon, QTableWidget, QTableWidgetItem, QTabWidget, QVBoxLayout,
    QWidget,
)

from config import Config, log_dir
from database import Database
from remotebrowser import RemoteBrowserDialog
from worker import SyncWorker, TestWorker, human_size

ACCENT = "#06b6d4"

DARK_QSS = f"""
* {{ font-family: "Segoe UI", "Inter", sans-serif; font-size: 13px; }}
QWidget {{ background: #18181b; color: #e4e4e7; }}
QMainWindow, QDialog {{ background: #18181b; }}
QLabel#Title {{ font-size: 20px; font-weight: 700; color: #fafafa; }}
QLabel#Subtle {{ color: #a1a1aa; }}
QLabel#StatusDot {{ font-size: 16px; }}

QTabWidget::pane {{ border: 1px solid #27272a; border-radius: 10px; top: -1px; background: #1c1c1f; }}
QTabBar::tab {{
    background: transparent; color: #a1a1aa; padding: 9px 18px; margin-right: 4px;
    border: 1px solid transparent; border-top-left-radius: 8px; border-top-right-radius: 8px;
}}
QTabBar::tab:selected {{ color: #fafafa; background: #1c1c1f; border-color: #27272a; border-bottom-color: #1c1c1f; }}
QTabBar::tab:hover {{ color: #e4e4e7; }}

QGroupBox {{
    border: 1px solid #27272a; border-radius: 10px; margin-top: 14px; padding: 14px 12px 12px 12px;
    background: #202024;
}}
QGroupBox::title {{ subcontrol-origin: margin; left: 12px; padding: 0 6px; color: #d4d4d8; font-weight: 600; }}

QLineEdit, QSpinBox, QDoubleSpinBox, QComboBox, QPlainTextEdit {{
    background: #111113; border: 1px solid #3f3f46; border-radius: 8px; padding: 7px 10px; color: #f4f4f5;
    selection-background-color: {ACCENT};
}}
QLineEdit:focus, QSpinBox:focus, QDoubleSpinBox:focus, QComboBox:focus, QPlainTextEdit:focus {{ border-color: {ACCENT}; }}
QComboBox::drop-down {{ border: none; width: 22px; }}
QComboBox QAbstractItemView {{ background: #111113; border: 1px solid #3f3f46; selection-background-color: {ACCENT}; }}

QPushButton {{
    background: #27272a; border: 1px solid #3f3f46; border-radius: 8px; padding: 8px 16px; color: #f4f4f5; font-weight: 600;
}}
QPushButton:hover {{ background: #323237; border-color: #52525b; }}
QPushButton:disabled {{ color: #71717a; background: #1f1f22; border-color: #2a2a2e; }}
QPushButton#Primary {{ background: {ACCENT}; border-color: {ACCENT}; color: #06121a; }}
QPushButton#Primary:hover {{ background: #22d3ee; border-color: #22d3ee; }}
QPushButton#Danger:hover {{ background: #3a1d1d; border-color: #7f1d1d; color: #fecaca; }}

QProgressBar {{
    background: #111113; border: 1px solid #3f3f46; border-radius: 8px; height: 18px; text-align: center; color: #d4d4d8;
}}
QProgressBar::chunk {{ background: {ACCENT}; border-radius: 6px; }}

QTableWidget {{ background: #111113; border: 1px solid #27272a; border-radius: 8px; gridline-color: #27272a; }}
QHeaderView::section {{ background: #202024; color: #a1a1aa; border: none; border-bottom: 1px solid #27272a; padding: 7px; font-weight: 600; }}
QTableWidget::item {{ padding: 4px; }}

QCheckBox {{ spacing: 8px; }}
QCheckBox::indicator {{ width: 18px; height: 18px; border: 1px solid #3f3f46; border-radius: 5px; background: #111113; }}
QCheckBox::indicator:checked {{ background: {ACCENT}; border-color: {ACCENT}; }}

QPlainTextEdit {{ font-family: "Cascadia Mono", "JetBrains Mono", Consolas, monospace; font-size: 12px; }}
QStatusBar {{ color: #a1a1aa; }}
"""


class MainWindow(QMainWindow):
    def __init__(self, cfg: Config):
        super().__init__()
        self.cfg = cfg
        self.db = Database()
        self.sync_thread = None
        self.sync_worker = None
        self.test_thread = None
        self.test_worker = None
        self._really_quit = False
        self._browser_open = False
        self.next_run = None

        self.setWindowTitle("Trawl")
        self.resize(880, 660)
        self.setWindowIcon(self._app_icon())

        central = QWidget()
        self.setCentralWidget(central)
        root = QVBoxLayout(central)
        root.setContentsMargins(16, 14, 16, 8)
        root.setSpacing(12)

        root.addLayout(self._build_header())

        self.tabs = QTabWidget()
        self.tabs.addTab(self._build_dashboard_tab(), "Dashboard")
        self.tabs.addTab(self._build_connection_tab(), "Connection")
        self.tabs.addTab(self._build_settings_tab(), "Settings")
        self.tabs.addTab(self._build_log_tab(), "Log")
        root.addWidget(self.tabs)

        self.statusBar().showMessage("Ready.")

        self._build_tray()
        self._load_form()
        self._load_recent()

        # repeating scheduler timer
        self.schedule_timer = QTimer(self)
        self.schedule_timer.timeout.connect(self._on_schedule_tick)

        # 1 Hz display timer for the countdown label
        self.display_timer = QTimer(self)
        self.display_timer.timeout.connect(self._update_next_run_label)
        self.display_timer.start(1000)

        if self.cfg.schedule_enabled:
            self._start_schedule(run_now=False)

    # ---------- icon ----------
    def _app_icon(self) -> QIcon:
        return self.style().standardIcon(QStyle.StandardPixmap.SP_DriveNetIcon)

    # ---------- header ----------
    def _build_header(self) -> QHBoxLayout:
        row = QHBoxLayout()
        title = QLabel("Trawl")
        title.setObjectName("Title")
        sub = QLabel("Seedbox -> Desktop")
        sub.setObjectName("Subtle")
        col = QVBoxLayout()
        col.setSpacing(0)
        col.addWidget(title)
        col.addWidget(sub)
        row.addLayout(col)
        row.addStretch(1)
        self.lbl_conn = QLabel("Not configured")
        self.lbl_conn.setObjectName("Subtle")
        self.dot = QLabel("\u25CF")
        self.dot.setObjectName("StatusDot")
        self.dot.setStyleSheet("color:#52525b;")
        row.addWidget(self.dot)
        row.addWidget(self.lbl_conn)
        return row

    # ---------- dashboard ----------
    def _build_dashboard_tab(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setSpacing(12)

        controls = QHBoxLayout()
        self.btn_sync = QPushButton("Sync now")
        self.btn_sync.setObjectName("Primary")
        self.btn_sync.clicked.connect(lambda: self.start_sync())
        self.btn_stop = QPushButton("Stop")
        self.btn_stop.setObjectName("Danger")
        self.btn_stop.setEnabled(False)
        self.btn_stop.clicked.connect(self.stop_sync)
        self.btn_browse = QPushButton("Pick a file...")
        self.btn_browse.setToolTip("Browse the seedbox and download one chosen file - good for a quick test.")
        self.btn_browse.clicked.connect(self.open_remote_browser)
        self.chk_schedule = QCheckBox("Run on a schedule")
        self.chk_schedule.toggled.connect(self._toggle_schedule)
        controls.addWidget(self.btn_sync)
        controls.addWidget(self.btn_stop)
        controls.addWidget(self.btn_browse)
        controls.addStretch(1)
        controls.addWidget(self.chk_schedule)
        lay.addLayout(controls)

        prog = QGroupBox("Activity")
        pl = QVBoxLayout(prog)
        self.lbl_status = QLabel("Idle.")
        self.lbl_status.setObjectName("Subtle")
        pl.addWidget(self.lbl_status)
        self.bar_current = QProgressBar()
        self.bar_current.setValue(0)
        pl.addWidget(self.bar_current)
        self.lbl_overall = QLabel("0 / 0 files")
        self.lbl_overall.setObjectName("Subtle")
        pl.addWidget(self.lbl_overall)
        self.bar_overall = QProgressBar()
        self.bar_overall.setValue(0)
        pl.addWidget(self.bar_overall)
        self.lbl_next = QLabel("Schedule off.")
        self.lbl_next.setObjectName("Subtle")
        pl.addWidget(self.lbl_next)
        lay.addWidget(prog)

        recent = QGroupBox("Recent transfers")
        rl = QVBoxLayout(recent)
        self.tbl = QTableWidget(0, 4)
        self.tbl.setHorizontalHeaderLabels(["File", "Size", "Status", "When"])
        self.tbl.verticalHeader().setVisible(False)
        self.tbl.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.tbl.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        hh = self.tbl.horizontalHeader()
        hh.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        for c in (1, 2, 3):
            hh.setSectionResizeMode(c, QHeaderView.ResizeMode.ResizeToContents)
        rl.addWidget(self.tbl)
        lay.addWidget(recent, 1)
        return w

    # ---------- connection ----------
    def _build_connection_tab(self) -> QWidget:
        w = QWidget()
        outer = QVBoxLayout(w)
        box = QGroupBox("Seedbox connection")
        form = QFormLayout(box)
        form.setSpacing(10)

        self.in_host = QLineEdit()
        self.in_host.setPlaceholderText("e.g. yourbox.seedhost.example")
        self.in_port = QSpinBox()
        self.in_port.setRange(1, 65535)
        self.in_user = QLineEdit()
        self.in_pass = QLineEdit()
        self.in_pass.setEchoMode(QLineEdit.EchoMode.Password)
        self.btn_showpass = QPushButton("Show")
        self.btn_showpass.setCheckable(True)
        self.btn_showpass.setFixedWidth(70)
        self.btn_showpass.toggled.connect(self._toggle_password_echo)
        pass_row = QHBoxLayout()
        pass_row.addWidget(self.in_pass, 1)
        pass_row.addWidget(self.btn_showpass)
        pass_wrap = QWidget()
        pass_wrap.setLayout(pass_row)

        self.in_mode = QComboBox()
        self.in_mode.addItem("FTPS - explicit (AUTH TLS)", "ftps_explicit")
        self.in_mode.addItem("FTPS - implicit (port 990)", "ftps_implicit")
        self.in_mode.addItem("FTP - plain (no encryption)", "ftp")

        self.chk_passive = QCheckBox("Passive mode (recommended)")
        self.chk_verify = QCheckBox("Verify TLS certificate")
        self.chk_verify.setToolTip(
            "Leave off if your seedbox uses a self-signed certificate (common). "
            "Turn on only if the provider has a valid public certificate."
        )
        self.in_timeout = QSpinBox()
        self.in_timeout.setRange(5, 300)
        self.in_timeout.setSuffix(" s")

        form.addRow("Host", self.in_host)
        form.addRow("Port", self.in_port)
        form.addRow("Username", self.in_user)
        form.addRow("Password", pass_wrap)
        form.addRow("Mode", self.in_mode)
        form.addRow("", self.chk_passive)
        form.addRow("", self.chk_verify)
        form.addRow("Timeout", self.in_timeout)
        outer.addWidget(box)

        btns = QHBoxLayout()
        self.btn_test = QPushButton("Test connection")
        self.btn_test.clicked.connect(self.test_connection)
        btn_save = QPushButton("Save")
        btn_save.setObjectName("Primary")
        btn_save.clicked.connect(self._save_from_form)
        btns.addStretch(1)
        btns.addWidget(self.btn_test)
        btns.addWidget(btn_save)
        outer.addLayout(btns)
        outer.addStretch(1)
        return w

    # ---------- settings ----------
    def _build_settings_tab(self) -> QWidget:
        w = QWidget()
        outer = QVBoxLayout(w)

        sbox = QGroupBox("What to pull")
        sf = QFormLayout(sbox)
        sf.setSpacing(10)
        self.in_remote = QLineEdit()
        self.in_remote.setPlaceholderText("/downloads")
        self.in_local = QLineEdit()
        btn_browse = QPushButton("Browse...")
        btn_browse.clicked.connect(self._browse_local)
        local_row = QHBoxLayout()
        local_row.addWidget(self.in_local, 1)
        local_row.addWidget(btn_browse)
        local_wrap = QWidget()
        local_wrap.setLayout(local_row)
        self.chk_recursive = QCheckBox("Scan subfolders")
        self.chk_preserve = QCheckBox("Recreate folder structure on the desktop")
        self.in_minage = QSpinBox()
        self.in_minage.setRange(0, 1440)
        self.in_minage.setSuffix(" min")
        self.in_minage.setToolTip("Skip files modified more recently than this, so still-downloading torrents are left alone.")
        sf.addRow("Remote folder", self.in_remote)
        sf.addRow("Destination", local_wrap)
        sf.addRow("", self.chk_recursive)
        sf.addRow("", self.chk_preserve)
        sf.addRow("Min file age", self.in_minage)
        outer.addWidget(sbox)

        tbox = QGroupBox("Schedule")
        tf = QFormLayout(tbox)
        tf.setSpacing(10)
        self.in_interval = QDoubleSpinBox()
        self.in_interval.setRange(0.1, 168.0)
        self.in_interval.setSingleStep(0.5)
        self.in_interval.setDecimals(1)
        self.in_interval.setSuffix(" hours")
        self.chk_autosync = QCheckBox("Run one sync as soon as Trawl launches")
        tf.addRow("Check every", self.in_interval)
        tf.addRow("", self.chk_autosync)
        outer.addWidget(tbox)

        abox = QGroupBox("Behaviour")
        af = QFormLayout(abox)
        af.setSpacing(10)
        self.chk_tray = QCheckBox("Keep running in the system tray when the window is closed")
        self.chk_startmin = QCheckBox("Start minimised to the tray")
        self.chk_startup = QCheckBox("Launch Trawl when Windows starts")
        self.chk_notify = QCheckBox("Show a tray notification when a sync finishes")
        self.chk_delete = QCheckBox("Delete files from the seedbox after a successful download")
        self.chk_delete.setToolTip("Use with caution - removed files cannot be recovered from the seedbox.")
        af.addRow("", self.chk_tray)
        af.addRow("", self.chk_startmin)
        af.addRow("", self.chk_startup)
        af.addRow("", self.chk_notify)
        af.addRow("", self.chk_delete)
        outer.addWidget(abox)

        btns = QHBoxLayout()
        btn_save = QPushButton("Save")
        btn_save.setObjectName("Primary")
        btn_save.clicked.connect(self._save_from_form)
        btns.addStretch(1)
        btns.addWidget(btn_save)
        outer.addLayout(btns)
        outer.addStretch(1)
        return w

    # ---------- log ----------
    def _build_log_tab(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        self.txt_log = QPlainTextEdit()
        self.txt_log.setReadOnly(True)
        self.txt_log.setMaximumBlockCount(5000)
        lay.addWidget(self.txt_log)
        row = QHBoxLayout()
        row.addStretch(1)
        btn_open = QPushButton("Open log folder")
        btn_open.clicked.connect(lambda: self._open_path(log_dir()))
        btn_clear = QPushButton("Clear")
        btn_clear.clicked.connect(self.txt_log.clear)
        row.addWidget(btn_open)
        row.addWidget(btn_clear)
        lay.addLayout(row)
        return w

    # ---------- tray ----------
    def _build_tray(self) -> None:
        self.tray = QSystemTrayIcon(self._app_icon(), self)
        self.tray.setToolTip("Trawl")
        menu = QMenu()
        act_show = QAction("Show Trawl", self)
        act_show.triggered.connect(self._show_window)
        act_sync = QAction("Sync now", self)
        act_sync.triggered.connect(lambda: self.start_sync())
        act_quit = QAction("Quit", self)
        act_quit.triggered.connect(self.quit_app)
        menu.addAction(act_show)
        menu.addAction(act_sync)
        menu.addSeparator()
        menu.addAction(act_quit)
        self.tray.setContextMenu(menu)
        self.tray.activated.connect(self._on_tray_activated)
        self.tray.show()

    def _on_tray_activated(self, reason) -> None:
        if reason in (QSystemTrayIcon.ActivationReason.Trigger,
                      QSystemTrayIcon.ActivationReason.DoubleClick):
            self._show_window()

    def _show_window(self) -> None:
        self.showNormal()
        self.raise_()
        self.activateWindow()

    # ---------- form load/save ----------
    def _load_form(self) -> None:
        c = self.cfg
        self.in_host.setText(c.host)
        self.in_port.setValue(c.port)
        self.in_user.setText(c.username)
        self.in_pass.setText(c.get_password())
        i = self.in_mode.findData(c.mode)
        self.in_mode.setCurrentIndex(i if i >= 0 else 0)
        self.chk_passive.setChecked(c.passive)
        self.chk_verify.setChecked(c.verify_tls)
        self.in_timeout.setValue(c.timeout)

        self.in_remote.setText(c.remote_dir)
        self.in_local.setText(c.local_dir)
        self.chk_recursive.setChecked(c.recursive)
        self.chk_preserve.setChecked(c.preserve_structure)
        self.in_minage.setValue(c.min_file_age_minutes)
        self.in_interval.setValue(c.interval_hours)
        self.chk_autosync.setChecked(c.autosync_on_launch)

        self.chk_tray.setChecked(c.minimize_to_tray)
        self.chk_startmin.setChecked(c.start_minimized)
        self.chk_startup.setChecked(c.run_on_startup)
        self.chk_notify.setChecked(c.notify_on_complete)
        self.chk_delete.setChecked(c.delete_remote_after)

        self.chk_schedule.blockSignals(True)
        self.chk_schedule.setChecked(c.schedule_enabled)
        self.chk_schedule.blockSignals(False)
        self._refresh_conn_label()

    def _gather_into_config(self) -> None:
        c = self.cfg
        c.host = self.in_host.text().strip()
        c.port = self.in_port.value()
        c.username = self.in_user.text().strip()
        c.mode = self.in_mode.currentData()
        c.passive = self.chk_passive.isChecked()
        c.verify_tls = self.chk_verify.isChecked()
        c.timeout = self.in_timeout.value()

        c.remote_dir = self.in_remote.text().strip() or "/"
        c.local_dir = self.in_local.text().strip()
        c.recursive = self.chk_recursive.isChecked()
        c.preserve_structure = self.chk_preserve.isChecked()
        c.min_file_age_minutes = self.in_minage.value()
        c.interval_hours = self.in_interval.value()
        c.autosync_on_launch = self.chk_autosync.isChecked()

        c.minimize_to_tray = self.chk_tray.isChecked()
        c.start_minimized = self.chk_startmin.isChecked()
        c.notify_on_complete = self.chk_notify.isChecked()
        c.delete_remote_after = self.chk_delete.isChecked()
        c.run_on_startup = self.chk_startup.isChecked()

    def _save_from_form(self) -> None:
        self._gather_into_config()
        self.cfg.set_password(self.in_pass.text())
        self.cfg.save()
        self._apply_run_on_startup(self.cfg.run_on_startup)
        self._refresh_conn_label()
        self.append_log("info", "Settings saved.")
        self.statusBar().showMessage("Settings saved.", 4000)

    def _refresh_conn_label(self) -> None:
        if self.cfg.host and self.cfg.username:
            self.lbl_conn.setText(f"{self.cfg.username}@{self.cfg.host}")
            self.dot.setStyleSheet(f"color:{ACCENT};")
        else:
            self.lbl_conn.setText("Not configured")
            self.dot.setStyleSheet("color:#52525b;")

    # ---------- small UI helpers ----------
    def _toggle_password_echo(self, shown: bool) -> None:
        self.in_pass.setEchoMode(
            QLineEdit.EchoMode.Normal if shown else QLineEdit.EchoMode.Password
        )
        self.btn_showpass.setText("Hide" if shown else "Show")

    def _browse_local(self) -> None:
        start = self.in_local.text().strip() or os.path.expanduser("~")
        path = QFileDialog.getExistingDirectory(self, "Choose destination folder", start)
        if path:
            self.in_local.setText(path)

    def _open_path(self, path: str) -> None:
        try:
            if sys.platform.startswith("win"):
                os.startfile(path)  # type: ignore[attr-defined]
            elif sys.platform == "darwin":
                os.system(f'open "{path}"')
            else:
                os.system(f'xdg-open "{path}"')
        except Exception:
            QMessageBox.information(self, "Trawl", f"Folder: {path}")

    def _apply_run_on_startup(self, enable: bool) -> None:
        if not sys.platform.startswith("win"):
            return
        try:
            import winreg
            key = winreg.OpenKey(
                winreg.HKEY_CURRENT_USER,
                r"Software\Microsoft\Windows\CurrentVersion\Run",
                0, winreg.KEY_SET_VALUE,
            )
            if enable:
                winreg.SetValueEx(key, "Trawl", 0, winreg.REG_SZ, f'"{sys.executable}"')
            else:
                try:
                    winreg.DeleteValue(key, "Trawl")
                except FileNotFoundError:
                    pass
            winreg.CloseKey(key)
        except Exception as e:
            self.append_log("warn", f"Could not update startup setting: {e}")

    # ---------- logging + table ----------
    def append_log(self, level: str, message: str) -> None:
        ts = datetime.datetime.now().strftime("%H:%M:%S")
        line = f"[{ts}] {level.upper():5} {message}"
        self.txt_log.appendPlainText(line)
        try:
            with open(os.path.join(log_dir(), "trawl.log"), "a", encoding="utf-8") as f:
                f.write(line + "\n")
        except Exception:
            pass

    def _load_recent(self) -> None:
        for row in self.db.recent():
            name = os.path.basename(row["local_path"] or row["remote_path"])
            when = datetime.datetime.fromtimestamp(row["updated_at"]).strftime("%Y-%m-%d %H:%M")
            self._append_row(name, int(row["size"]), row["status"], when)

    def _append_row(self, name: str, size: int, status: str, when: str) -> None:
        r = self.tbl.rowCount()
        self.tbl.insertRow(r)
        self.tbl.setItem(r, 0, QTableWidgetItem(name))
        self.tbl.setItem(r, 1, QTableWidgetItem(human_size(size)))
        self.tbl.setItem(r, 2, QTableWidgetItem(status))
        self.tbl.setItem(r, 3, QTableWidgetItem(when))

    @pyqtSlot(str, int, str)
    def _on_transfer(self, name: str, size: int, status: str) -> None:
        self.tbl.insertRow(0)
        self.tbl.setItem(0, 0, QTableWidgetItem(name))
        self.tbl.setItem(0, 1, QTableWidgetItem(human_size(size)))
        self.tbl.setItem(0, 2, QTableWidgetItem(status))
        self.tbl.setItem(0, 3, QTableWidgetItem(datetime.datetime.now().strftime("%Y-%m-%d %H:%M")))

    # ---------- progress slots ----------
    @pyqtSlot(int, int)
    def _on_file_progress(self, done: int, total: int) -> None:
        if total > 0:
            self.bar_current.setRange(0, total)
            self.bar_current.setValue(done)
        else:
            self.bar_current.setRange(0, 0)

    @pyqtSlot(int, int)
    def _on_overall(self, done: int, total: int) -> None:
        self.lbl_overall.setText(f"{done} / {total} files")
        self.bar_overall.setRange(0, max(total, 1))
        self.bar_overall.setValue(done)

    # ---------- sync lifecycle ----------
    def start_sync(self) -> None:
        if self.sync_thread is not None:
            return
        self._gather_into_config()
        if not (self.cfg.host and self.cfg.username):
            QMessageBox.warning(self, "Trawl", "Set the seedbox host and username on the Connection tab first.")
            self.tabs.setCurrentIndex(1)
            return
        if not self.cfg.local_dir:
            QMessageBox.warning(self, "Trawl", "Choose a destination folder on the Settings tab first.")
            self.tabs.setCurrentIndex(2)
            return

        self.btn_sync.setEnabled(False)
        self.btn_stop.setEnabled(True)
        self.bar_current.setValue(0)
        self.bar_overall.setValue(0)
        self.lbl_status.setText("Starting...")

        self.sync_thread = QThread(self)
        self.sync_worker = SyncWorker(self.cfg)
        self.sync_worker.moveToThread(self.sync_thread)
        self.sync_thread.started.connect(self.sync_worker.run)
        self.sync_worker.log.connect(self.append_log)
        self.sync_worker.status.connect(self.lbl_status.setText)
        self.sync_worker.file_progress.connect(self._on_file_progress)
        self.sync_worker.overall.connect(self._on_overall)
        self.sync_worker.transfer.connect(self._on_transfer)
        self.sync_worker.finished.connect(self._on_sync_finished)
        self.sync_worker.finished.connect(self.sync_thread.quit)
        self.sync_worker.finished.connect(self.sync_worker.deleteLater)
        self.sync_thread.finished.connect(self.sync_thread.deleteLater)
        self.sync_thread.finished.connect(self._clear_sync_refs)
        self.sync_thread.start()

    def stop_sync(self) -> None:
        if self.sync_worker is not None:
            self.sync_worker.request_stop()
            self.lbl_status.setText("Stopping...")
            self.btn_stop.setEnabled(False)

    @pyqtSlot(dict)
    def _on_sync_finished(self, summary: dict) -> None:
        self.btn_sync.setEnabled(True)
        self.btn_stop.setEnabled(False)
        self.bar_current.setRange(0, 100)
        self.bar_current.setValue(0)
        if summary.get("error"):
            self.lbl_status.setText(f"Error: {summary['error']}")
            msg = f"Sync failed: {summary['error']}"
        else:
            d, s, f = summary["downloaded"], summary["skipped"], summary["failed"]
            self.lbl_status.setText(
                f"Done - {d} downloaded, {s} skipped, {f} failed "
                f"({human_size(summary['bytes'])})."
            )
            msg = f"{d} downloaded, {s} skipped, {f} failed."
        stamp = datetime.datetime.now().strftime("%H:%M:%S")
        self.statusBar().showMessage(f"Last sync {stamp} - {msg}")
        if self.cfg.notify_on_complete and self.tray.isVisible():
            self.tray.showMessage("Trawl - sync finished", msg,
                                  QSystemTrayIcon.MessageIcon.Information, 5000)
        if self.cfg.schedule_enabled:
            self.next_run = datetime.datetime.now() + datetime.timedelta(hours=self.cfg.interval_hours)

    def _clear_sync_refs(self) -> None:
        self.sync_thread = None
        self.sync_worker = None

    # ---------- remote browser ----------
    def open_remote_browser(self) -> None:
        self._gather_into_config()
        self.cfg.set_password(self.in_pass.text())
        if not (self.cfg.host and self.cfg.username):
            QMessageBox.warning(self, "Trawl", "Set the seedbox host and username on the Connection tab first.")
            self.tabs.setCurrentIndex(1)
            return
        if not self.cfg.local_dir:
            QMessageBox.warning(self, "Trawl", "Choose a destination folder on the Settings tab first.")
            self.tabs.setCurrentIndex(2)
            return
        self._browser_open = True
        try:
            dlg = RemoteBrowserDialog(self.cfg, self)
            dlg.exec()
        finally:
            self._browser_open = False
        self._load_recent_refresh()

    def _load_recent_refresh(self) -> None:
        # Rebuild the recent-transfers table after a browser session so any
        # files grabbed there show up on the dashboard.
        self.tbl.setRowCount(0)
        self._load_recent()

    # ---------- test connection ----------
    def test_connection(self) -> None:
        if self.test_thread is not None:
            return
        self._gather_into_config()
        self.cfg.set_password(self.in_pass.text())
        if not (self.cfg.host and self.cfg.username):
            QMessageBox.warning(self, "Trawl", "Enter a host and username first.")
            return
        self.btn_test.setEnabled(False)
        self.btn_test.setText("Testing...")
        self.test_thread = QThread(self)
        self.test_worker = TestWorker(self.cfg)
        self.test_worker.moveToThread(self.test_thread)
        self.test_thread.started.connect(self.test_worker.run)
        self.test_worker.finished.connect(self._on_test_finished)
        self.test_worker.finished.connect(self.test_thread.quit)
        self.test_worker.finished.connect(self.test_worker.deleteLater)
        self.test_thread.finished.connect(self.test_thread.deleteLater)
        self.test_thread.finished.connect(self._clear_test_refs)
        self.test_thread.start()

    @pyqtSlot(bool, str)
    def _on_test_finished(self, ok: bool, message: str) -> None:
        self.btn_test.setEnabled(True)
        self.btn_test.setText("Test connection")
        self.append_log("info" if ok else "error", message.replace("\n", " "))
        if ok:
            QMessageBox.information(self, "Trawl - connection OK", message)
        else:
            QMessageBox.critical(
                self, "Trawl - connection failed",
                message + "\n\nIf this is a TLS error, try turning off "
                          "'Verify TLS certificate' on the Connection tab.",
            )

    def _clear_test_refs(self) -> None:
        self.test_thread = None
        self.test_worker = None

    # ---------- scheduling ----------
    def _toggle_schedule(self, on: bool) -> None:
        self.cfg.schedule_enabled = on
        self.cfg.save()
        if on:
            self._start_schedule(run_now=True)
        else:
            self.schedule_timer.stop()
            self.next_run = None
            self.append_log("info", "Schedule disabled.")

    def _start_schedule(self, run_now: bool) -> None:
        interval_ms = max(int(self.cfg.interval_hours * 3600 * 1000), 60000)
        self.schedule_timer.start(interval_ms)
        self.next_run = datetime.datetime.now() + datetime.timedelta(milliseconds=interval_ms)
        self.append_log("info", f"Schedule enabled - every {self.cfg.interval_hours} hour(s).")
        if run_now:
            self.start_sync()

    def _on_schedule_tick(self) -> None:
        self.next_run = datetime.datetime.now() + datetime.timedelta(hours=self.cfg.interval_hours)
        if self._browser_open:
            self.append_log("warn", "Scheduled sync skipped - the remote browser is open.")
        elif self.sync_thread is None:
            self.append_log("info", "Scheduled sync starting.")
            self.start_sync()
        else:
            self.append_log("warn", "Scheduled sync skipped - a sync is already running.")

    def _update_next_run_label(self) -> None:
        if self.cfg.schedule_enabled and self.next_run is not None:
            remaining = (self.next_run - datetime.datetime.now()).total_seconds()
            if remaining < 0:
                remaining = 0
            h = int(remaining // 3600)
            m = int((remaining % 3600) // 60)
            s = int(remaining % 60)
            self.lbl_next.setText(f"Next sync in {h:d}:{m:02d}:{s:02d}")
        else:
            self.lbl_next.setText("Schedule off.")

    # ---------- close / quit ----------
    def quit_app(self) -> None:
        self._really_quit = True
        if self.sync_worker is not None:
            self.sync_worker.request_stop()
        if self.sync_thread is not None:
            self.sync_thread.wait(3000)
        self.cfg.save()
        self.db.close()
        QApplication.instance().quit()

    def closeEvent(self, event) -> None:
        if (not self._really_quit and self.cfg.minimize_to_tray and self.tray.isVisible()):
            event.ignore()
            self.hide()
            self.tray.showMessage(
                "Trawl", "Trawl is still running in the system tray.",
                QSystemTrayIcon.MessageIcon.Information, 3000,
            )
        else:
            if self.sync_worker is not None:
                self.sync_worker.request_stop()
            if self.sync_thread is not None:
                self.sync_thread.wait(3000)
            self.cfg.save()
            self.db.close()
            event.accept()
            QApplication.instance().quit()
