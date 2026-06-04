"""
Trawl - seedbox to desktop file puller.

Entry point: builds the QApplication, installs a global crash logger, applies
the dark theme and launches the main window.

Written by LJ "HawaiizFynest" Eblacas
"""
from __future__ import annotations

import datetime
import os
import sys
import traceback

from PyQt6.QtWidgets import QApplication, QMessageBox

from config import Config, log_dir
from mainwindow import DARK_QSS, MainWindow


def install_excepthook() -> None:
    def hook(exc_type, exc, tb):
        text = "".join(traceback.format_exception(exc_type, exc, tb))
        try:
            with open(os.path.join(log_dir(), "crash.log"), "a", encoding="utf-8") as f:
                f.write(f"\n=== {datetime.datetime.now():%Y-%m-%d %H:%M:%S} ===\n{text}\n")
        except Exception:
            pass
        try:
            QMessageBox.critical(
                None, "Trawl - unexpected error",
                f"An unexpected error occurred:\n\n{exc_type.__name__}: {exc}\n\n"
                f"Details were written to the log folder.",
            )
        except Exception:
            pass
        sys.__excepthook__(exc_type, exc, tb)

    sys.excepthook = hook


def main() -> None:
    install_excepthook()
    app = QApplication(sys.argv)
    app.setApplicationName("Trawl")
    app.setQuitOnLastWindowClosed(False)  # tray keeps the app alive
    app.setStyleSheet(DARK_QSS)

    cfg = Config.load()
    win = MainWindow(cfg)

    if cfg.start_minimized and cfg.minimize_to_tray:
        win.hide()
    else:
        win.show()

    if cfg.autosync_on_launch:
        win.start_sync()

    sys.exit(app.exec())


if __name__ == "__main__":
    main()
