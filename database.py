"""
Trawl - SQLite download history.

Tracks which remote files have already been fetched so they are not
re-downloaded. Each component (GUI thread, worker thread) creates its own
instance; SQLite in WAL mode handles the concurrent access.

Written by LJ "HawaiizFynest" Eblacas
"""
from __future__ import annotations

import os
import sqlite3
import time
from typing import Optional

from config import database_path


class Database:
    def __init__(self, path: Optional[str] = None):
        self.path = path or database_path()
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        self.conn = sqlite3.connect(self.path, check_same_thread=False, timeout=30)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA busy_timeout=30000")
        self._init_schema()

    def _init_schema(self) -> None:
        self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS downloads (
                remote_path   TEXT PRIMARY KEY,
                size          INTEGER NOT NULL,
                modify_epoch  INTEGER,
                local_path    TEXT,
                status        TEXT NOT NULL,
                updated_at    REAL NOT NULL
            )
            """
        )
        self.conn.commit()

    def is_completed(self, remote_path: str, size: int) -> bool:
        row = self.conn.execute(
            "SELECT size, status FROM downloads WHERE remote_path = ?",
            (remote_path,),
        ).fetchone()
        if row is None:
            return False
        return row["status"] == "completed" and int(row["size"]) == int(size)

    def record(self, remote_path: str, size: int, modify_epoch: Optional[int],
               local_path: str, status: str) -> None:
        self.conn.execute(
            """
            INSERT INTO downloads (remote_path, size, modify_epoch, local_path, status, updated_at)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(remote_path) DO UPDATE SET
                size=excluded.size,
                modify_epoch=excluded.modify_epoch,
                local_path=excluded.local_path,
                status=excluded.status,
                updated_at=excluded.updated_at
            """,
            (remote_path, int(size), modify_epoch, local_path, status, time.time()),
        )
        self.conn.commit()

    def recent(self, limit: int = 200):
        return self.conn.execute(
            "SELECT * FROM downloads ORDER BY updated_at DESC LIMIT ?",
            (limit,),
        ).fetchall()

    def count_completed(self) -> int:
        return self.conn.execute(
            "SELECT COUNT(*) AS c FROM downloads WHERE status='completed'"
        ).fetchone()["c"]

    def clear(self) -> None:
        self.conn.execute("DELETE FROM downloads")
        self.conn.commit()

    def close(self) -> None:
        try:
            self.conn.close()
        except Exception:
            pass
