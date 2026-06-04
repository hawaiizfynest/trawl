"""
Trawl - FTP / FTPS client.

Handles plain FTP, explicit FTPS (AUTH TLS) and implicit FTPS (port 990),
recursive directory walking via MLSD with a LIST-based fallback, and
resumable binary downloads with progress reporting.

Written by LJ "HawaiizFynest" Eblacas
"""
from __future__ import annotations

import calendar
import ftplib
import os
import re
import ssl
import time
from dataclasses import dataclass
from typing import Callable, Iterator, List, Optional, Tuple


class AbortDownload(Exception):
    """Raised inside the retrbinary callback to cancel a transfer."""


@dataclass
class RemoteFile:
    path: str               # full remote path, e.g. /downloads/Show/ep.mkv
    size: int
    modify_epoch: Optional[int]


class _ImplicitFTP_TLS(ftplib.FTP_TLS):
    """FTP_TLS variant that wraps the control socket in TLS immediately
    (implicit FTPS, typically port 990)."""

    def __init__(self, *args, **kwargs):
        self._wrapped_sock = None
        super().__init__(*args, **kwargs)

    @property
    def sock(self):
        return self._wrapped_sock

    @sock.setter
    def sock(self, value):
        if value is not None and not isinstance(value, ssl.SSLSocket):
            value = self.context.wrap_socket(value, server_hostname=self.host)
        self._wrapped_sock = value


# Tolerant Unix LIST parser used only when the server does not support MLSD.
_LIST_RE = re.compile(
    r"^([\-dl])[rwxXsStT\-]{9}\s+\d+\s+\S+\s+\S+\s+(\d+)\s+"
    r"\w{3}\s+\d+\s+(?:\d{4}|\d{1,2}:\d{2})\s+(.+)$"
)


class FtpClient:
    def __init__(self, host: str, port: int, username: str, password: str,
                 mode: str = "ftps_explicit", passive: bool = True,
                 verify_tls: bool = False, timeout: int = 30):
        self.host = host
        self.port = int(port)
        self.username = username
        self.password = password
        self.mode = mode
        self.passive = passive
        self.verify_tls = verify_tls
        self.timeout = int(timeout)
        self.ftp: Optional[ftplib.FTP] = None

    # ---- TLS context ----
    def _ssl_context(self) -> ssl.SSLContext:
        ctx = ssl.create_default_context()
        if not self.verify_tls:
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE
        return ctx

    # ---- connection lifecycle ----
    def connect(self) -> str:
        if self.mode == "ftp":
            ftp = ftplib.FTP(timeout=self.timeout)
            ftp.connect(self.host, self.port or 21)
            ftp.login(self.username, self.password)
        elif self.mode == "ftps_implicit":
            ftp = _ImplicitFTP_TLS(context=self._ssl_context(), timeout=self.timeout)
            ftp.connect(self.host, self.port or 990)
            ftp.login(self.username, self.password)
            ftp.prot_p()
        else:  # ftps_explicit (default)
            ftp = ftplib.FTP_TLS(context=self._ssl_context(), timeout=self.timeout)
            ftp.connect(self.host, self.port or 21)
            ftp.auth()
            ftp.login(self.username, self.password)
            ftp.prot_p()
        ftp.set_pasv(self.passive)
        self.ftp = ftp
        try:
            return ftp.getwelcome() or "Connected."
        except Exception:
            return "Connected."

    def quit(self) -> None:
        if self.ftp is not None:
            try:
                self.ftp.quit()
            except Exception:
                try:
                    self.ftp.close()
                except Exception:
                    pass
            self.ftp = None

    # ---- directory listing ----
    def _mlsd(self, path: str) -> List[Tuple[str, dict]]:
        entries: List[Tuple[str, dict]] = []
        for name, facts in self.ftp.mlsd(path):
            if name in (".", ".."):
                continue
            entries.append((name, facts))
        return entries

    def _list_fallback(self, path: str) -> List[Tuple[str, dict]]:
        lines: List[str] = []
        self.ftp.retrlines(f"LIST {path}", lines.append)
        out: List[Tuple[str, dict]] = []
        for line in lines:
            m = _LIST_RE.match(line.strip())
            if not m:
                continue
            type_char, size, name = m.groups()
            if name in (".", ".."):
                continue
            ftype = "dir" if type_char == "d" else ("link" if type_char == "l" else "file")
            out.append((name, {"type": ftype, "size": size}))
        return out

    def _list_dir(self, path: str) -> List[Tuple[str, dict]]:
        try:
            return self._mlsd(path)
        except (ftplib.error_perm, ftplib.error_proto, ftplib.error_temp):
            return self._list_fallback(path)

    def list_dir_entries(self, path: str) -> List[Tuple[str, bool, int]]:
        """Non-recursive listing for the browser: (name, is_dir, size),
        folders first then files, alphabetically."""
        out: List[Tuple[str, bool, int]] = []
        for name, facts in self._list_dir(path or "/"):
            ftype = (facts.get("type") or "").lower()
            if ftype == "link":
                continue
            is_dir = ftype == "dir"
            try:
                size = int(facts.get("size") or 0)
            except (TypeError, ValueError):
                size = 0
            out.append((name, is_dir, size))
        out.sort(key=lambda e: (not e[1], e[0].lower()))
        return out

    @staticmethod
    def _join(base: str, name: str) -> str:
        return base + name if base.endswith("/") else base + "/" + name

    @staticmethod
    def _parse_modify(facts: dict) -> Optional[int]:
        raw = facts.get("modify")
        if not raw:
            return None
        try:
            t = time.strptime(raw[:14], "%Y%m%d%H%M%S")
            return int(calendar.timegm(t))  # MLSD modify facts are UTC
        except Exception:
            return None

    def walk(self, root: str, recursive: bool = True) -> Iterator[RemoteFile]:
        root = root or "/"
        visited = set()
        stack = [root]
        while stack:
            current = stack.pop()
            norm = current.rstrip("/") or "/"
            if norm in visited:
                continue
            visited.add(norm)
            try:
                entries = self._list_dir(current)
            except ftplib.all_errors:
                continue
            for name, facts in entries:
                ftype = (facts.get("type") or "").lower()
                full = self._join(norm, name)
                if ftype == "dir":
                    if recursive:
                        stack.append(full)
                elif ftype in ("file", ""):
                    try:
                        size = int(facts.get("size") or 0)
                    except (TypeError, ValueError):
                        size = 0
                    yield RemoteFile(full, size, self._parse_modify(facts))
                # links and pseudo-entries (cdir/pdir) are skipped

    # ---- download ----
    def download(self, rf: RemoteFile, local_path: str,
                 progress_cb: Callable[[int, int], None],
                 should_stop: Callable[[], bool]) -> str:
        """Returns one of: 'completed', 'stopped', 'size_mismatch', 'error'."""
        part = local_path + ".part"
        os.makedirs(os.path.dirname(local_path) or ".", exist_ok=True)

        offset = 0
        if os.path.exists(part):
            existing = os.path.getsize(part)
            if rf.size and existing == rf.size:
                os.replace(part, local_path)
                return "completed"
            if rf.size and existing > rf.size:
                os.remove(part)
            else:
                offset = existing

        written = [offset]

        def open_and_fetch(start: int, file_mode: str) -> None:
            with open(part, file_mode) as f:
                def cb(data: bytes) -> None:
                    if should_stop():
                        raise AbortDownload()
                    f.write(data)
                    written[0] += len(data)
                    progress_cb(written[0], rf.size)
                rest = start if start > 0 else None
                self.ftp.retrbinary(f"RETR {rf.path}", cb, blocksize=65536, rest=rest)

        try:
            try:
                open_and_fetch(offset, "ab" if offset else "wb")
            except (ftplib.error_perm, ftplib.error_temp) as e:
                # Server likely refused REST/resume - restart from scratch.
                if offset:
                    offset = 0
                    written[0] = 0
                    open_and_fetch(0, "wb")
                else:
                    raise e
        except AbortDownload:
            return "stopped"
        except ftplib.all_errors:
            return "error"

        if rf.size and os.path.getsize(part) != rf.size:
            return "size_mismatch"   # keep .part so a later run can resume
        os.replace(part, local_path)
        return "completed"

    def delete(self, remote_path: str) -> bool:
        try:
            self.ftp.delete(remote_path)
            return True
        except ftplib.all_errors:
            return False
