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


class _LocalWriteError(Exception):
    """Wraps a local filesystem error so it can be told apart from a transfer
    error (both surface as OSError otherwise)."""
    def __init__(self, original: Exception):
        super().__init__(str(original))
        self.original = original


class _ReuseFTP_TLS(ftplib.FTP_TLS):
    """FTP_TLS that reuses the control connection's TLS session on the data
    connection. Many FTPS servers (and most seedboxes) require this, and it is
    the usual fix for 'EOF occurred in violation of protocol' during transfers."""

    def ntransfercmd(self, cmd, rest=None):
        conn, size = ftplib.FTP.ntransfercmd(self, cmd, rest)
        if self._prot_p:
            try:
                session = self.sock.session
            except AttributeError:
                session = None
            conn = self.context.wrap_socket(
                conn, server_hostname=self.host, session=session)
        return conn, size


class _ImplicitReuseFTP_TLS(_ReuseFTP_TLS):
    """Implicit FTPS (TLS from the first byte, typically port 990) with the same
    data-channel session reuse."""

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
                 verify_tls: bool = False, timeout: int = 30,
                 encrypt_data: bool = True):
        self.host = host
        self.port = int(port)
        self.username = username
        self.password = password
        self.mode = mode
        self.passive = passive
        self.verify_tls = verify_tls
        self.timeout = int(timeout)
        # When False, FTPS connections use a clear data channel (prot_c): the
        # login stays TLS-encrypted but file bytes transfer unencrypted. This
        # sidesteps SSL data-channel errors (EOF / BAD_LENGTH) on servers that
        # don't transfer file data cleanly over TLS.
        self.encrypt_data = encrypt_data
        self.ftp: Optional[ftplib.FTP] = None

    # ---- TLS context ----
    def _ssl_context(self) -> ssl.SSLContext:
        ctx = ssl.create_default_context()
        # Pin to TLS 1.2. Many FTPS servers mishandle TLS 1.3 on the data
        # connection, which shows up as 'BAD_LENGTH' or other SSL record errors
        # mid-transfer. TLS 1.2 session reuse is the reliable FTPS combination.
        try:
            ctx.maximum_version = ssl.TLSVersion.TLSv1_2
        except (ValueError, AttributeError):
            pass
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
            ftp = _ImplicitReuseFTP_TLS(context=self._ssl_context(), timeout=self.timeout)
            ftp.connect(self.host, self.port or 990)
            ftp.login(self.username, self.password)
            if self.encrypt_data:
                ftp.prot_p()
            else:
                ftp.prot_c()
        else:  # ftps_explicit (default)
            ftp = _ReuseFTP_TLS(context=self._ssl_context(), timeout=self.timeout)
            ftp.connect(self.host, self.port or 21)
            ftp.auth()
            ftp.login(self.username, self.password)
            if self.encrypt_data:
                ftp.prot_p()
            else:
                ftp.prot_c()
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
        folders first then files, alphabetically. Anything that is not clearly a
        directory (including symlinks and entries with no type) is shown as a
        selectable file - many seedboxes expose completed downloads as symlinks."""
        out: List[Tuple[str, bool, int]] = []
        for name, facts in self._list_dir(path or "/"):
            ftype = (facts.get("type") or "").lower()
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
                else:
                    # file, symlink, or unknown -> treat as a downloadable file.
                    # RETR follows symlinks on the server side, so completed
                    # downloads exposed as symlinks are fetched normally.
                    try:
                        size = int(facts.get("size") or 0)
                    except (TypeError, ValueError):
                        size = 0
                    yield RemoteFile(full, size, self._parse_modify(facts))

    # ---- download ----
    def _retrieve(self, cmd: str, callback, rest, should_stop) -> None:
        """RETR over the data connection, tolerating servers that close the TLS
        data channel without a clean shutdown (the cause of
        'EOF occurred in violation of protocol'). Integrity is verified by the
        caller via the expected file size."""
        conn, _ = self.ftp.ntransfercmd(cmd, rest)
        try:
            while True:
                if should_stop():
                    raise AbortDownload()
                try:
                    data = conn.recv(65536)
                except ssl.SSLEOFError:
                    break
                except ssl.SSLError as e:
                    if "EOF" in str(e).upper() or "UNEXPECTED" in str(e).upper():
                        break
                    raise
                if not data:
                    break
                callback(data)
        finally:
            try:
                conn.close()
            except Exception:
                pass
        try:
            self.ftp.voidresp()
        except ftplib.all_errors:
            pass

    def download(self, rf: RemoteFile, local_path: str,
                 progress_cb: Callable[[int, int], None],
                 should_stop: Callable[[], bool]):
        """Returns (status, detail). status is one of:
        'completed', 'stopped', 'size_mismatch', 'write_error', 'error'.
        detail carries the underlying message on failure (empty on success)."""
        part = local_path + ".part"
        try:
            os.makedirs(os.path.dirname(local_path) or ".", exist_ok=True)
            offset = 0
            if os.path.exists(part):
                existing = os.path.getsize(part)
                if rf.size and existing == rf.size:
                    os.replace(part, local_path)
                    return ("completed", "")
                if rf.size and existing > rf.size:
                    os.remove(part)
                else:
                    offset = existing
        except OSError as e:
            return ("write_error", str(e))

        written = [offset]

        def fetch(start: int, file_mode: str) -> None:
            try:
                f = open(part, file_mode)
            except OSError as e:
                raise _LocalWriteError(e)
            try:
                def cb(data: bytes) -> None:
                    try:
                        f.write(data)
                    except OSError as e:
                        raise _LocalWriteError(e)
                    written[0] += len(data)
                    progress_cb(written[0], rf.size)
                self._retrieve(f"RETR {rf.path}", cb,
                               start if start > 0 else None, should_stop)
            finally:
                f.close()

        try:
            try:
                fetch(offset, "ab" if offset else "wb")
            except (ftplib.error_perm, ftplib.error_temp):
                # Server likely refused REST/resume - restart from scratch.
                if offset:
                    offset = 0
                    written[0] = 0
                    fetch(0, "wb")
                else:
                    raise
        except AbortDownload:
            return ("stopped", "")
        except _LocalWriteError as e:
            return ("write_error", str(e.original))
        except (ssl.SSLError, ftplib.Error, OSError, EOFError) as e:
            # connection / transfer problems (local file errors are tagged above)
            return ("error", str(e))

        try:
            if rf.size and os.path.getsize(part) != rf.size:
                return ("size_mismatch",
                        f"expected {rf.size} bytes, got {os.path.getsize(part)}")
            os.replace(part, local_path)
        except OSError as e:
            return ("write_error", str(e))
        return ("completed", "")

    def delete(self, remote_path: str) -> bool:
        try:
            self.ftp.delete(remote_path)
            return True
        except ftplib.all_errors:
            return False
