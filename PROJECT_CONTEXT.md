# Trawl - Project Context / Handoff Brief

This file is a self-contained briefing so work on Trawl can resume in a fresh
conversation without re-deriving anything. Paste it (or point to it) at the
start of the next session.

Repo: `https://github.com/HawaiizFynest/Trawl` (private)
Author credit on all files: `Written by LJ "HawaiizFynest" Eblacas`

---

## What Trawl is

A Windows desktop app (PyQt6) that pulls new files from a seedbox over FTP/FTPS
on a schedule and saves them to a local folder. It tracks what it has already
downloaded so each run fetches only new files. Tray-resident, with an interval
scheduler and on-demand sync.

Decided up front with the user:
- Transport: **FTP / FTPS** (not SFTP).
- Delivery: **desktop app** (PyQt6 .exe), because the destination is the desktop
  itself - a NAS-hosted web app cannot cleanly push files into a desktop.
- Target OS: **Windows 11**.

## Stack and key decisions

- **PyQt6** GUI. Background work runs on a **QThread** with a worker moved to it
  (`moveToThread`), reporting via signals. Deliberately **not qasync** - `ftplib`
  is blocking, so a thread is the correct tool here (unlike Directrix/SPWarden
  which use qasync for async httpx/Graph calls).
- **ftplib** (stdlib) for FTP, explicit FTPS (`FTP_TLS` + `auth()` + `prot_p()`),
  and implicit FTPS (custom `_ImplicitFTP_TLS` subclass that TLS-wraps the
  control socket immediately, default port 990).
- **keyring** for the seedbox password (Windows Credential Manager), matching the
  Directrix/SPWarden convention. Service name `Trawl`, account `host:port:user`.
- **SQLite** for download history at `%APPDATA%\Trawl\state.db` (WAL mode; each
  thread opens its own connection).
- **Dark theme** via QSS using the ridgeport palette (zinc neutrals, cyan-500
  `#06b6d4` accent, Segoe UI / Cascadia Mono).
- No external deps beyond `PyQt6` and `keyring`; everything else is stdlib.

## File-by-file

- `trawl.py` - entry point. Installs a global `sys.excepthook` that logs crashes
  to `%APPDATA%\Trawl\logs\crash.log`, applies `DARK_QSS`, sets
  `setQuitOnLastWindowClosed(False)` so the tray keeps the app alive, then shows
  `MainWindow`. Honours `start_minimized` and `autosync_on_launch`.
- `config.py` - `Config` dataclass (all settings). JSON at
  `%APPDATA%\Trawl\config.json`. Password via keyring (`get_password` /
  `set_password`, not stored in JSON). Path helpers: `app_data_dir`, `log_dir`,
  `config_path`, `database_path`.
- `database.py` - `Database` class. Table `downloads(remote_path PK, size,
  modify_epoch, local_path, status, updated_at)`. `is_completed(path, size)` is
  the dedup check (path present, status='completed', size matches).
- `ftpclient.py` - `FtpClient` and `RemoteFile`. `connect()` handles all three
  modes and honours `verify_tls` (default False -> `CERT_NONE`, since seedbox
  certs are usually self-signed). `walk(root, recursive)` uses **MLSD** first
  (reliable type/size/modify), falling back to a tolerant Unix **LIST** parser
  (`_LIST_RE`). `download()` resumes via `.part` + FTP `REST`, falls back to a
  clean restart if `REST` is refused, verifies final size, then renames. Returns
  `completed` / `stopped` / `size_mismatch` / `error`. `list_dir_entries(path)`
  gives a non-recursive `(name, is_dir, size)` listing for the browser.
- `worker.py` - `SyncWorker` (one full sync pass) and `TestWorker` (connection
  test). Signals: `log, status, file_progress, overall, transfer, finished`.
  Eligibility = not too new (min file age) AND not already completed. Mirrors
  remote structure under the destination (`preserve_structure`), sanitises path
  components for Windows, guards free disk space, optional delete-after.
  `human_size()` lives here.
- `remotebrowser.py` - `RemoteBrowserDialog` + `RemoteSession`. Lets the user
  browse the seedbox and download chosen file(s) immediately, bypassing the scan
  and scheduler (the "select a single file" feature). `RemoteSession` owns one
  FtpClient connection on its own QThread and serves list/download requests
  serially via queued signals, so the connection is never used from two threads.
  Picked files download (flattened) into `local_dir` and are recorded in the DB.
  After an aborted transfer the session reconnects (unless it is closing).
- `mainwindow.py` - `MainWindow` + `DARK_QSS`. Four tabs (Dashboard, Connection,
  Settings, Log), system tray, repeating `QTimer` scheduler + 1 Hz countdown
  label. Owns the thread wiring for both workers, the **Pick a file...** button
  that opens `RemoteBrowserDialog` (`open_remote_browser`), run-on-startup via
  `winreg`, and minimise-to-tray `closeEvent`. A `_browser_open` flag stops a
  scheduled sync from firing while the browser dialog is open.
- `trawl.spec` - PyInstaller **one-file** build. Uses `collect_submodules` for
  `keyring` and `win32ctypes` so the credential backend bundles correctly; picks
  up `trawl.ico` if present. Build: `python -m PyInstaller trawl.spec`.
- `.github/workflows/build.yml` - GitHub Actions. On a `v*` tag push it builds
  `Trawl.exe` on `windows-latest` (PyInstaller one-file) and attaches it to a
  GitHub Release, matching the tag-push release pattern used across the other
  tools. UPX is not on the runner, so the `upx=True` flag in the spec is simply
  skipped - harmless.

## Settings (Config fields)

Connection: `host, port, username, mode (ftp|ftps_explicit|ftps_implicit),
passive, verify_tls, timeout`.
Sync: `remote_dir, local_dir, recursive, preserve_structure,
min_file_age_minutes, interval_hours, delete_remote_after`.
Behaviour: `schedule_enabled, autosync_on_launch, minimize_to_tray,
start_minimized, run_on_startup, notify_on_complete`.

## Status

**v1.0.0 - feature complete and validated, plus a remote file browser.** All
modules syntax-check; the FTP parsing/path/dedup logic is unit-tested (LIST
parser, MLSD UTC time parse, path mirroring, sanitisation, config roundtrip,
database); the full `MainWindow` and the `RemoteBrowserDialog` (including its
session-thread start/stop lifecycle) construct and run cleanly headless
(offscreen). Live FTP transfer was not testable from the build sandbox (FTP
egress blocked there) - verify a real transfer against the actual seedbox on
first run, which is exactly what the **Pick a file...** browser is good for.

## Possible next steps (not yet built)

- Per-folder profiles / multiple sync targets.
- File-type include/exclude filters (e.g. only video, skip `.nfo`/sample).
- Bandwidth throttle.
- Parallel downloads (note: many seedboxes cap concurrent connections).
- Single-instance guard (QLocalServer / QSharedMemory).
- Optional post-download hook (e.g. kick off MKVKiller / Name'd).
- Windows toast via a real `.ico` and richer notifications.

## Conventions in force

- Complete drop-in files only - never diffs or find/replace snippets.
- PyInstaller one-file mode, invoked as `python -m PyInstaller`.
- GitHub Desktop only - no `git tag` / `git push` CLI in any docs.
- No `GITHUB_SETUP.txt` in the repo or structure listings.
- Placeholders use `HawaiizFynest`, never `YOUR_USERNAME`.
- Author credit `Written by LJ "HawaiizFynest" Eblacas` on applicable files.
