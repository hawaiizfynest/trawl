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
shows up under the repository's **Releases**.

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
scheduled syncing.

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

## Project structure

```
Trawl/
  trawl.py          entry point + crash logging + dark theme
  mainwindow.py     UI (tabs, tray, scheduler) + background-thread wiring
  worker.py         SyncWorker / TestWorker (run on a QThread)
  remotebrowser.py  remote file browser dialog for picking single files
  ftpclient.py      FTP/FTPS connect, recursive walk, resumable download
  database.py       SQLite download history
  config.py         settings (JSON) + password (keyring) + paths
  requirements.txt
  trawl.spec        PyInstaller one-file build
  README.md
  PROJECT_CONTEXT.md
```
