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
  gives a non-recursive `(name, is_dir, size)` listing for the browser. Both the
  walk and the browser listing treat **symlinks (and unknown types) as files**,
  not skipped - seedboxes often expose completed downloads as symlinks, and RETR
  follows them server-side.
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
- `deluge.py` - `DelugeClient`, a Deluge **Web UI** JSON-RPC client (stdlib
  urllib + cookiejar, no extra deps). Configured with the full Web UI **URL**, so
  it handles seedboxes that serve Deluge over HTTPS behind a reverse proxy at a
  subpath (e.g. `https://you.example.com/deluge/`) as well as a plain local
  install (`http://10.0.0.50:8112`); the JSON endpoint is the URL + `/json`.
  `login()` authenticates and attaches to a daemon; `get_torrents()` returns
  name/progress/state/is_finished; `incomplete_names()` is the set of unfinished
  torrent names. Complete = `is_finished`, `progress >= 100`, or `state ==
  "Seeding"`.
- `updater.py` - GitHub-release self-update. `UpdateChecker` reads
  `releases/latest`; `UpdateDownloader` pulls the `Trawl.exe` asset via the asset
  API url with `Accept: application/octet-stream` (with a redirect handler that
  drops the auth header on the storage redirect). Token is **optional** (only
  needed for a private repo or to dodge the unauthenticated rate limit) and comes
  from keyring. `apply_update_and_restart()` writes a detached batch that waits
  on this PID, deletes the old exe, moves the new one in and relaunches - frozen
  Windows only.
- `version.py` - `__version__` (currently `1.1.1`). Bump and tag to release.
- `mainwindow.py` - `MainWindow` + `DARK_QSS`. Four tabs (Dashboard, Connection,
  Settings, Log), system tray, repeating `QTimer` scheduler + 1 Hz countdown
  label. The Settings tab is wrapped in a `QScrollArea` and now also has the
  **Deluge** and **Updates** groups. Owns the thread wiring for the sync, test,
  Deluge-test, update-check and update-download workers; the **Pick a file...**
  button; run-on-startup via `winreg`; a launch-time silent update check; and
  minimise-to-tray `closeEvent`. A `_browser_open` flag blocks scheduled syncs
  while the browser is open.
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
Deluge: `deluge_enabled, deluge_url, deluge_verify_tls` + Deluge Web UI password
in keyring (account `deluge`). The URL is the full Web UI address (handles
HTTPS + reverse-proxy subpaths and custom ports).
Updates: `check_updates_on_launch` + GitHub token in keyring (`github_token`).

## Status

**v1.1.1.** Adds self-update, the Deluge completion check, and a browser/sync
fix so symlinked files show and download. All modules compile; the FTP
parsing/path/dedup, version-compare, Deluge completion and name-matching logic
are unit-tested; the full `MainWindow` (with the new Deluge + Updates UI and the
update-result flows) constructs and runs cleanly headless. Not testable from the
build sandbox: live FTP (egress blocked) and the live GitHub API (the shared
sandbox IP is rate-limited) - verify a real transfer and an update check on the
actual machine. The repo is now **public**, so updates work without a token.

## Possible next steps (not yet built)

- File-type include/exclude filters (e.g. only video, skip `.nfo`/sample).
- Follow directory symlinks in the recursive walk (currently a dir-symlink would
  be attempted as a file and fail; file-symlinks work).
- Bandwidth throttle; parallel downloads (mind seedbox connection caps).
- Per-folder profiles / multiple sync targets.
- Single-instance guard (QLocalServer / QSharedMemory).
- Post-download hook (e.g. kick off MKVKiller / Name'd).
- Match Deluge torrents to files by save_path as well as by name.

## Conventions in force

- Complete drop-in files only - never diffs or find/replace snippets.
- PyInstaller one-file mode, invoked as `python -m PyInstaller`.
- GitHub Desktop only - no `git tag` / `git push` CLI in any docs.
- No `GITHUB_SETUP.txt` in the repo or structure listings.
- Placeholders use `HawaiizFynest`, never `YOUR_USERNAME`.
- Author credit `Written by LJ "HawaiizFynest" Eblacas` on applicable files.
