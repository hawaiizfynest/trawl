"""
Trawl - Deluge Web UI client (JSON-RPC).

Talks to the Deluge Web UI (deluge-web, default port 8112) over its JSON-RPC
endpoint so a sync can find out which torrents have finished downloading and
avoid grabbing files that are still in progress.

This targets the Web UI (the thing you log into in a browser with a password),
not the raw daemon RPC on port 58846.

Written by LJ "HawaiizFynest" Eblacas
"""
from __future__ import annotations

import http.cookiejar
import json
import ssl
import urllib.request
from typing import List


class DelugeError(Exception):
    pass


class DelugeClient:
    def __init__(self, host: str, port: int = 8112, password: str = "",
                 use_https: bool = False, verify_tls: bool = False, timeout: int = 20):
        self.host = host
        self.port = int(port)
        self.password = password
        self.scheme = "https" if use_https else "http"
        self.verify_tls = verify_tls
        self.timeout = int(timeout)
        self._id = 0

        handlers = [urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar())]
        if use_https:
            ctx = ssl.create_default_context()
            if not verify_tls:
                ctx.check_hostname = False
                ctx.verify_mode = ssl.CERT_NONE
            handlers.append(urllib.request.HTTPSHandler(context=ctx))
        self._opener = urllib.request.build_opener(*handlers)

    @property
    def _url(self) -> str:
        return f"{self.scheme}://{self.host}:{self.port}/json"

    def _call(self, method: str, params=None):
        self._id += 1
        body = json.dumps({"method": method, "params": params or [], "id": self._id}).encode("utf-8")
        req = urllib.request.Request(self._url, data=body, headers={
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": "Trawl",
        })
        with self._opener.open(req, timeout=self.timeout) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        if data.get("error"):
            raise DelugeError(str(data["error"]))
        return data.get("result")

    def login(self) -> bool:
        ok = self._call("auth.login", [self.password])
        if not ok:
            raise DelugeError("Login failed - check the Deluge Web UI password.")
        # Make sure the web client is attached to a daemon.
        try:
            if not self._call("web.connected", []):
                hosts = self._call("web.get_hosts", []) or []
                if hosts:
                    self._call("web.connect", [hosts[0][0]])
        except DelugeError:
            pass  # some setups manage this themselves
        return True

    def get_torrents(self) -> List[dict]:
        fields = ["name", "progress", "state", "is_finished", "save_path"]
        result = self._call("core.get_torrents_status", [{}, fields]) or {}
        out = []
        for thash, info in result.items():
            out.append({
                "hash": thash,
                "name": info.get("name", "") or "",
                "progress": float(info.get("progress", 0.0) or 0.0),
                "state": info.get("state", "") or "",
                "is_finished": bool(info.get("is_finished", False)),
                "save_path": info.get("save_path", "") or "",
            })
        return out

    @staticmethod
    def is_complete(t: dict) -> bool:
        return bool(t.get("is_finished")) or float(t.get("progress", 0.0)) >= 100.0 \
            or t.get("state") == "Seeding"

    def incomplete_names(self) -> set:
        """Names of torrents that are NOT finished downloading."""
        return {t["name"] for t in self.get_torrents()
                if t["name"] and not self.is_complete(t)}
