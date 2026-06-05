# Trawl

A small Windows desktop app that pulls new files from your seedbox over
FTP/FTPS on a schedule and drops them straight onto your computer. It remembers
what it has already fetched, so each run only grabs what is new.

Written by LJ "HawaiizFynest" Eblacas

---

## What it does

- Connects to a seedbox via **plain FTP**, **explicit FTPS (AUTH TLS)** or **implicit FTPS (port 990)**
- Recursively scans a remote folder and downloads anything it has not seen before
- Runs on an interval you set (every *N* hours), or on demand with **Sync now**
- Lives quietly in the **system tray** and can launch with Windows
- **Resumes** interrupted downloads instead of starting over
- Skips files that are still being written on the server (configurable minimum file age)
- Optionally checks **Deluge** and only pulls files whose torrent has finished
- **Updates itself** from the latest GitHub release
- Recreates the remote folder structure on your desktop (optional)
- Keeps a transfer history and a live log

## Requirements

- **Windows 10/11** to run the built app
- **Python 3.10+** only if you are building it yourself

## Run from source

```
pip install -r requirements.txt
python trawl.py
```

## Build the executable

Trawl builds to a single `Trawl.exe` with PyInstaller (one-file mode):

```
pip install -r requirements.txt pyinstaller
python -m PyInstaller trawl.spec
```

The finished `Trawl.exe` lands in the `dist` folder. To give it a custom icon,
drop a `trawl.ico` next to `trawl.spec` before building and it will be picked up
automatically.

### Automated builds and releases

A GitHub Actions workflow (`.github/workflows/build.yml`) builds the executable
for you. When you push a version tag, it spins up a Windows runner, builds
`Trawl.exe`, and attaches it to a GitHub Release for that tag (the `.exe` is also
saved as a run artifact).

To cut a release from GitHub Desktop: open **History**, right-click the commit
and choose **Create Tag**, name it `v1.0.0` (any `v*` tag triggers a build),
then **Push origin**. The build runs on its own and the finished `Trawl.exe`
shows up under the repository's **Releases**. The build sets the app's version from the tag automatically, so you never edit a version file - tag `v1.1.5` (or `1.1.5`) and that's the version the app reports.

## First-run setup

1. Open the **Connection** tab, enter your seedbox host, port, username and
   password, pick the mode, and click **Test connection**.
2. Open the **Settings** tab, set the **remote folder** to pull from and choose
   a **destination folder** on your desktop.
3. Set how often to check under **Schedule**, then tick **Run on a schedule**
   on the Dashboard (or just use **Sync now**).

## Pick a single file (quick test)

If you just want to test the connection and a transfer without pulling a whole
folder, use **Pick a file...** on the Dashboard. It opens a browser of your
seedbox: double-click folders to navigate, select one (or more) files, then
click **Download selected**. Those files download straight to your destination
folder and nothing else is touched - the scheduler and the full scan stay out of
the way. It is the quickest way to confirm everything works before you turn on
scheduled syncing. Completed downloads exposed as symlinks (common on seedboxes)
appear as selectable files too.

## How it works

- **New-file detection.** Every completed download is recorded in a small SQLite
  database keyed by its remote path and size. A file is re-fetched only if its
  path is new or its size changed, so reruns are cheap.
- **Still-downloading files.** The **minimum file age** setting skips anything
  modified more recently than the chosen number of minutes, which keeps Trawl
  from grabbing a torrent that is still being written on the box.
- **Resume.** Files download to a `.part` file and are renamed on success. If a
  transfer is interrupted, the next run resumes from where it stopped (using the
  FTP `REST` command, falling back to a clean restart if the server refuses it).
- **Verification.** A download is only marked complete when the local size
  matches the size reported by the server.

## Connection modes and TLS

Most seedboxes use **explicit FTPS**, which is the default. If your provider's
certificate is self-signed (very common), leave **Verify TLS certificate**
unchecked. Turn it on only if the provider has a valid public certificate. Plain
FTP is available but sends your password in the clear and should be avoided.

For FTPS, Trawl reuses the login's TLS session on the data connection and
tolerates servers that close that connection without a clean TLS shutdown -
this resolves the common seedbox transfer error "EOF occurred in violation of
protocol". It also pins TLS 1.2, which avoids data-channel errors such as
"BAD_LENGTH" that some servers throw under TLS 1.3.

## Deluge completion check (optional)

If your files come from torrents, Trawl can ask Deluge whether a torrent has
actually finished before pulling its files. Enable it under **Settings →
Deluge completion check**, paste your Deluge **Web UI URL** and the Web UI
password, and click **Test Deluge**.

The URL is simply whatever you open the Deluge Web UI at in a browser. That
covers a seedbox serving Deluge over HTTPS behind a reverse proxy at a subpath,
e.g. `https://you.example.com/deluge/`, as well as a plain local install like
`http://10.0.0.50:8112`. Leave **Verify TLS certificate** off if needed (it's
fine either way for a host with a valid certificate). If the URL uses a
self-hosted certificate, keep it off.

When enabled, before each sync Trawl fetches the list of torrents and their
state, and skips any file whose torrent is still downloading (matched by the
torrent name against the file's folder or filename). A finished torrent, or a
file that matches no torrent at all, is allowed through. If Deluge can't be
reached, Trawl logs a warning and falls back to the minimum-file-age check
rather than halting, so a Deluge outage never stops syncing.

This talks to the Deluge **Web UI** (deluge-web), the same thing you log into in
a browser - not the raw daemon on port 58846, so make sure the Web UI is
running.

## Automatic updates

Trawl can update itself from your GitHub releases. Under **Settings →
Updates** you'll see the current version, an optional GitHub token field, a
**Check for updates when Trawl starts** toggle, and a **Check for updates now**
button. It looks at the **most recently published release** (by date, not by
version number) that has a `Trawl.exe` attached, and if that release's tag
differs from the version you're running, it offers to update. Accept and it
downloads the new `Trawl.exe`, then closes, swaps itself out and relaunches.

Because it compares release dates, version numbers don't have to keep going up -
whatever you published last is what gets offered. (Keeping them increasing still
makes the version label easier to read.)

Because the release is on a public repo, no token is needed. The token field is
only there if you make the repo private again, or if the unauthenticated GitHub
rate limit gets in the way - paste a fine-grained token with read access and
it's stored in Windows Credential Manager. Self-replacement only happens in the
built `Trawl.exe`; running from source, you just pull and rebuild.

## Where your data lives

- Settings: `%APPDATA%\Trawl\config.json`
- Download history: `%APPDATA%\Trawl\state.db`
- Logs and crash reports: `%APPDATA%\Trawl\logs\`
- Your seedbox password is stored in **Windows Credential Manager** via keyring,
  never in plain text.

## Running in the background

With **Keep running in the system tray** enabled, closing the window hides Trawl
to the tray instead of quitting. Double-click the tray icon to bring it back, or
right-click for **Sync now** and **Quit**. **Launch Trawl when Windows starts**
adds it to your per-user startup, and **Start minimised** sends it straight to
the tray on launch.

## Deleting from the seedbox

There is an optional **Delete files from the seedbox after a successful
download** setting. It is off by default. Removed files cannot be recovered from
the seedbox, so only enable it if that is genuinely what you want.

## Troubleshooting

- **Connection fails with a TLS/certificate error** — uncheck *Verify TLS
  certificate* on the Connection tab.
- **Connection hangs or lists nothing** — make sure *Passive mode* is on; most
  seedboxes require it.
- **A file keeps re-downloading** — it is likely still being written on the
  server; raise the *minimum file age*.
- **Nothing is found** — confirm the *remote folder* path is correct and that
  *Scan subfolders* is on if your files live in subdirectories.

## If downloads or updates fail with "Permission denied" (errno 13)

This is Windows blocking Trawl from writing files, and it is **not** fixed by
running as administrator. The usual cause is **Controlled Folder Access**
(Windows ransomware protection) blocking an unsigned app from writing to
protected folders like Desktop, Documents, Pictures or Downloads - or the
destination/exe sitting in a folder your account can't write to.

Fixes, easiest first:

- Point the **destination folder** (and keep `Trawl.exe`) somewhere plain and
  writable, e.g. `C:\Trawl` or `D:\Seedbox`, not under Desktop/Documents.
- If you want to keep using a protected folder, allow Trawl through Controlled
  Folder Access: **Windows Security -> Virus & threat protection -> Ransomware
  protection -> Allow an app through Controlled folder access -> Add `Trawl.exe`**.
- The log now names the exact path it failed to write (Settings -> Log, or
  `%APPDATA%\Trawl\logs\trawl.log`), which tells you which folder is blocked.

Updates download to your temp folder (always writable) and only swap the exe on
restart, so the destination-folder fix above usually clears update errors too.

## Project structure

```
Trawl/
  trawl.py          entry point + crash logging + dark theme
  mainwindow.py     UI (tabs, tray, scheduler) + background-thread wiring
  worker.py         SyncWorker / TestWorker / DelugeTestWorker (QThread)
  remotebrowser.py  remote file browser dialog for picking single files
  ftpclient.py      FTP/FTPS connect, recursive walk, resumable download
  deluge.py         Deluge Web UI JSON-RPC client (completion check)
  updater.py        GitHub-release self-update (download + swap + restart)
  version.py        version string
  database.py       SQLite download history
  config.py         settings (JSON) + secrets (keyring) + paths
  requirements.txt
  trawl.spec        PyInstaller one-file build
  .github/workflows/build.yml   builds Trawl.exe on a v* tag
  README.md
  PROJECT_CONTEXT.md
```
