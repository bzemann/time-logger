# WorkTime Logger

WorkTime Logger is a personal, shortcut-driven time tracker for a single
activity — there are no projects, tags or categories. A keyboard shortcut
starts a session, another shows how long it has run as a desktop
notification, and a third stops it. Every session lands as one row in a
plain CSV file; a local web dashboard and on-demand weekly/monthly HTML
reports read from that same file. It runs on macOS and Linux (Debian), uses
only the Python 3 standard library, and needs no account, server or cloud
service.

- Start / stop / show running time with a keyboard shortcut (Aerospace on
  macOS, i3 on Linux) or a Spotlight / rofi entry
- Desktop notifications for started, running and stopped sessions
- A local-only web dashboard (charts, range filter, recent sessions)
- Self-contained weekly/monthly HTML reports, generated automatically
- One CSV file per machine, human-readable and easy to edit by hand
- No accounts, no network calls, no dependencies to install

## Requirements

| | Requirement |
|---|---|
| Both OSes | Python 3.11 or newer |
| macOS | [Aerospace](https://github.com/nikitabobko/AeroSpace) for keyboard shortcuts (optional — Spotlight launchers work without it) |
| Debian | `libnotify-bin` (for `notify-send`) and a notification daemon such as `dunst`; `xdg-utils` (for opening the browser); i3 and rofi for shortcuts/launcher |

Nothing needs to be installed with `pip`: WorkTime Logger only uses the
Python standard library, and the dashboard's chart library (Chart.js,
vendored under `web/vendor/`, MIT licensed) ships with the repository —
there is no CDN and no build step.

## Installation

### macOS

```sh
platform/macos/install.sh
```

This creates 5 Spotlight-searchable apps ("WorkTime Start/Stop/Dashboard/
Weekly Report/Monthly Report") and a `~/.local/bin/worktime` symlink, and
prints the Aerospace keybinding lines to add. Paste them under
`[mode.main.binding]` in `~/.config/aerospace/aerospace.toml`, then reload
Aerospace's config (`alt-shift-c`). Uninstall with
`platform/macos/install.sh --uninstall`. Details:
[`platform/macos/README.md`](platform/macos/README.md).

### Debian (Linux)

The repository is private, so the Debian machine needs GitHub access first.

1. **Prerequisites:**

   ```sh
   sudo apt install git python3 libnotify-bin dunst xdg-utils
   ```

2. **GitHub access and clone** (pick one):

   ```sh
   # a) GitHub CLI (easiest)
   sudo apt install gh && gh auth login
   gh repo clone bzemann/time-logger ~/code/time-logger

   # b) SSH key: add the printed key on GitHub → Settings → SSH and GPG keys
   ssh-keygen -t ed25519 && cat ~/.ssh/id_ed25519.pub
   git clone git@github.com:bzemann/time-logger.git ~/code/time-logger
   ```

3. **Run the installer:**

   ```sh
   cd ~/code/time-logger && platform/linux/install.sh
   ```

   It creates 5 `.desktop` launchers (with a WorkTime icon) for your rofi app
   launcher and a `~/.local/bin/worktime` symlink, and prints three things to
   paste: the i3 keybindings, the polybar module, and your `modules-right`
   line with `worktime` added. It never edits your configs itself.

4. **i3:** paste the four printed `bindsym $mod+shift+…` lines into
   `~/.config/i3/config` and reload i3 with `$mod+shift+c`.

5. **polybar:** paste the printed `[module/worktime]` block into
   `~/.config/polybar/config.ini` and replace your `modules-right = …` line
   with the printed one. Then restart polybar with
   `~/.config/polybar/launch.sh` (or restart i3 with `$mod+shift+r`) — a plain
   i3 reload does not re-run polybar's `exec_always` launcher.

6. **Check:** follow the checklist in
   [`platform/linux/README.md`](platform/linux/README.md) (keys, rofi entries,
   dashboard, status bar).

Uninstall with `platform/linux/install.sh --uninstall` (then remove the
pasted i3 lines and polybar module by hand).

## Daily use

| Action | macOS (Aerospace) | Debian (i3) | Spotlight / rofi entry |
|---|---|---|---|
| Start / show running time | `alt-shift-t` | `$mod+shift+t` | WorkTime Start |
| Stop | `alt-shift-x` | `$mod+shift+u` | WorkTime Stop |
| Dashboard | `alt-shift-d` | `$mod+shift+d` | WorkTime Dashboard |
| Weekly report (this week so far) | `alt-shift-r` | `$mod+shift+w` | WorkTime Weekly Report |
| Monthly report | — | — | WorkTime Monthly Report |

On Debian, stop uses `u` instead of `x` because `$mod+shift+x` is commonly
bound to the lock screen, and the weekly report uses `w` instead of `r`
because `$mod+shift+r` restarts i3 by default.

There's no keyboard shortcut for the monthly report on either OS; use the
rofi entry or `worktime report month` from a terminal.

### Status bar (polybar)

The Linux installer prints a ready-to-paste polybar module,
`[module/worktime]`, that runs `<repo>/bin/worktime status --short` every
15 seconds and shows the running time (e.g. `1h 24m`); the module is empty
when nothing is running, so it disappears from the bar. If the CSV or
config is broken, it shows `err` — run `worktime status` in a terminal to
see the actual error. Left-clicking the module opens the dashboard. The
installer also prints your `modules-right` line with `worktime` inserted,
ready to paste into your polybar config.

Notifications:

- **Start** (nothing running): `Started at HH:MM`.
- **Start** (already running): `Currently running: 1h 24m`, with a second
  line `Since HH:MM · Today: …`.
- **Stop**: `Stopped: 3h 02m`, with a second line `Today: …`.
- **Stop** with nothing running: `No session running` (and the command
  exits with status 1).

### Forgotten start or stop: `--at HH:MM`

If you forgot to press start or stop in time, pass the real time
afterwards:

```sh
worktime start --at 08:15
worktime stop --at 17:30
```

`--at HH:MM` resolves to the most recent time matching `HH:MM` that isn't
in the future — so `worktime stop --at 23:50` run just after midnight
still means "yesterday 23:50", not a future time today. `start --at`
refuses a time that overlaps the previous session, and refuses while a
session is already running. `stop --at` refuses a time before the running
session's start. A session forgotten for 24 hours or more can't be closed
this way (sessions must stay under 24h) — see
[Your data](#your-data) for fixing it by editing the CSV directly.

## Command reference

All commands start with `bin/worktime` (or just `worktime`, once the
install script has symlinked it onto your `PATH`).

| Command | Description | Example |
|---|---|---|
| `worktime --version` | Print the version and exit | `worktime --version` |
| `worktime config` | Show the effective configuration | `worktime config` |
| `worktime start [--at HH:MM]` | Start a session, or show the running one | `worktime start` |
| `worktime stop [--at HH:MM]` | Stop the running session | `worktime stop` |
| `worktime status [--short]` | Print the current state (no notification); `--short` prints only the running time (e.g. `1h 24m`) or an empty line, and `err` on a broken CSV/config — for status bars like polybar | `worktime status --short` |
| `worktime dashboard` | Open the dashboard (starts the server if needed) | `worktime dashboard` |
| `worktime serve [--port N]` | Run the dashboard server in the foreground | `worktime serve --port 8765` |
| `worktime stop-server` | Stop the background dashboard server | `worktime stop-server` |
| `worktime report week\|month [--last \| --date YYYY-MM-DD] [--no-open]` | Generate a report for the current period so far, the previous complete period (`--last`), or the period containing a given date (`--date`) | `worktime report week --last` |
| `worktime report catch-up` | Generate any due weekly/monthly reports (used internally; no options) | `worktime report catch-up` |

Notes:

- `worktime` with no subcommand prints help to stderr and exits 2.
- `stop` with nothing running exits 1 (after still printing/notifying
  "No session running").
- `report week` / `report month` always overwrite the target file and open
  it in the browser unless `--no-open` is given.
- `--last` and `--date` are mutually exclusive.
- `report catch-up` takes no options; passing `--last`, `--date` or
  `--no-open` with it is an error.
- `ConfigError`, `StoreError`, `SessionError` and `ControlError` print
  `worktime: …` to stderr and exit 1; `start`, `stop`, `dashboard` and
  `report` also send a "WorkTime error" notification for these, since
  stderr isn't visible when triggered from a shortcut.

## Dashboard

`worktime dashboard` starts the local server if it isn't already running
and opens the dashboard in your browser at `http://127.0.0.1:<port>/`
(default port 8765). It only listens on `127.0.0.1`, never on your network.

It shows:

- A header with the running status ("Running · 1h 24m" / "Not running")
  and a "Generated" timestamp.
- Cards for Today, This Week, This Month and Total (worked time and
  balance against the target).
- A range filter (7d / 30d / 90d / this year / all / custom dates), default
  30 days, remembered per browser.
- An "Hours per day" bar chart with a dashed target line.
- An "Actual vs. target" trend chart with a Weekly/Monthly toggle
  (remembered per browser), showing worked and target hours plus balance
  and cumulative balance in the tooltip.
- A "Recent sessions" table of the latest 50 sessions overall — this table
  is **not** filtered by the selected range.

The page refreshes every 60 seconds (and on focus), and its dark mode
follows your OS setting.

After a `git pull`, run `worktime stop-server` once — a server started
before the update keeps serving the old code until it is restarted; the
next `worktime dashboard` starts a fresh instance.

## Reports

Reports are generated with `worktime report week` or `worktime report
month`:

```sh
worktime report week                       # current week, so far
worktime report month --last               # previous complete month
worktime report week --date 2026-09-23     # the week containing that date
worktime report month --no-open            # write it without opening a browser
```

They are also generated **automatically**: after `start` or `dashboard`,
WorkTime Logger checks whether the last complete week and/or month is
missing a final report (for example, the first `start` on a Monday morning
triggers last week's report; the first workday of a new month triggers last
month's). This runs in the background so the shortcut stays instant;
disable it with `WORKTIME_NO_AUTO_REPORTS=1`.

Reports are written to `reports_dir` (by default a `reports/` folder next
to the CSV), one self-contained HTML file per period:
`week-2026-W39.html`, `month-2026-09.html`. A report generated while its
period is still ongoing is marked "in progress" and can be regenerated
later; once the period is over, the report is marked "final" and is never
overwritten automatically (only `worktime report week|month` run manually
overwrites it). Regenerate a report manually after fixing an entry in the
CSV. Reports have no PDF export built in — use your browser's "Print to
PDF" (an A4 print stylesheet is included).

## Configuration

The config file is TOML. Its location, in order of precedence:

1. `$WORKTIME_CONFIG` (a path to the file)
2. `$XDG_CONFIG_HOME/worktime/config.toml`, if `XDG_CONFIG_HOME` is set
3. `~/.config/worktime/config.toml`

If the file doesn't exist, all settings use their defaults. See
[`config.example.toml`](config.example.toml) for a copy-pasteable template.
Run `worktime config` to see the effective values (including where the
file was or wasn't found).

```toml
# Where sessions are stored (CSV).
data_file = "~/.local/share/worktime/worktime.csv"

# Where generated weekly/monthly reports are written. Defaults to a
# "reports" directory next to data_file.
reports_dir = "~/.local/share/worktime/reports"

# Daily work time target, as "H:MM" or a number of hours (e.g. 8.5).
daily_target = "8:30"

# Days that count towards the daily target: mon, tue, wed, thu, fri, sat, sun.
workdays = ["mon", "tue", "wed", "thu", "fri"]

# Days off (vacation, public holidays): the daily target is 0 on these days.
# Single dates "YYYY-MM-DD" or inclusive ranges "YYYY-MM-DD..YYYY-MM-DD"
# (up to 366 days per range).
days_off = []
# days_off = ["2026-12-24..2026-12-31", "2027-01-02"]

# Port the local dashboard server listens on (127.0.0.1 only).
port = 8765
```

| Key | Default | Meaning |
|---|---|---|
| `data_file` | `~/.local/share/worktime/worktime.csv` | Path to the CSV file |
| `reports_dir` | `reports/` next to `data_file` | Where HTML reports are written |
| `daily_target` | `"8:30"` | Target worked time per workday, as `"H:MM"` or a number of hours |
| `workdays` | `["mon", "tue", "wed", "thu", "fri"]` | Weekdays that carry a target |
| `days_off` | `[]` | Dates or inclusive date ranges (`"YYYY-MM-DD..YYYY-MM-DD"`, max 366 days) with a target of 0 |
| `port` | `8765` | Port for the local dashboard server (127.0.0.1 only) |

Any key that isn't one of the above makes the config file invalid
(`worktime` exits with an error naming the unknown key).

### Environment variables

| Variable | Effect |
|---|---|
| `WORKTIME_CONFIG` | Path to the config file, overriding the default location |
| `WORKTIME_PYTHON` | Force `bin/worktime` to use this Python interpreter (must be ≥ 3.11) |
| `WORKTIME_NO_NOTIFY` | `1`/`true`/`yes` disables desktop notifications |
| `WORKTIME_NO_BROWSER` | `1`/`true`/`yes` disables opening the browser |
| `WORKTIME_NO_AUTO_REPORTS` | `1`/`true`/`yes` disables the automatic report catch-up after `start`/`dashboard` |
| `WORKTIME_INSTALL_NO_REGISTER` | (testing only) skips `lsregister`/`mdimport` on macOS, or `update-desktop-database` on Linux, during install |
| `WORKTIME_INSTALL_ALLOW_ANY_OS` | (testing only) lets `platform/linux/install.sh` run on a non-Linux OS |

## How the balance is calculated

- Every configured **workday** that is not a configured **day off** has a
  target of `daily_target`; weekends (unless made a workday) and days off
  have a target of 0.
- **Worked time** on a day is the sum of the elapsed time of every session
  that *started* that day — a session that crosses midnight counts
  entirely for its start date, and a still-running session counts with its
  time so far.
- Counting only starts at your **first ever entry**: days before tracking
  started never contribute to the target, even if they'd otherwise be
  workdays. **Future days** don't count either. **Today** counts with its
  full target, even before the day is over.
- **Balance** = worked − target. Positive means ahead, negative means
  behind.

Example: a week with 28h 52m worked against an 8:24 target over 3 tracked
days (25h 12m target) gives a balance of +3:40.

Balances are always shown signed: `+3:40` / `−1:15` / `±0:00` (never
zero-padded hours), colored green or red so they don't rely on color
alone. Plain durations (worked/target time) look like `7h 09m` or `45m`.

## Your data

Sessions live in one CSV file (default
`~/.local/share/worktime/worktime.csv`, see `worktime config` for the
actual path). Example content:

```csv
date,start,end,duration_min
2026-09-22,08:12:05,12:30:40,259
2026-09-22,23:30:00,00:45:00,75
2026-09-23,15:01:00,,
```

- `date` is the local calendar day the session **started** on
  (`YYYY-MM-DD`).
- `start` and `end` are local 24h times (`HH:MM:SS`). `end` is empty for a
  currently running session (as is `duration_min`).
- A session where `end` is an earlier clock time than `start` crossed
  midnight — e.g. `23:30:00` → `00:45:00` above is a 75-minute session
  that ends the next calendar day, still filed under `2026-09-22`.
- `duration_min` is a courtesy column for humans/spreadsheets. It's only
  syntax-checked on read; the real duration is always recomputed from
  `start`/`end`, rounded half-up to the minute.

Parsing is strict: a malformed row raises an error naming the file and the
line number. Only the **last** row in the file may be a running session
(empty `end`).

If you edit the file by hand:

- Timestamps are the source of truth — fix `start`/`end`. `duration_min` is
  ignored when reading (the duration is always computed from `start`/`end`)
  and is rewritten correctly the next time the tool writes the file.
- Only the last row may have an empty `end`.
- Sessions must stay under 24 hours (`end`/`start` further apart than that
  raises an error).

Each machine keeps its **own** CSV file — nothing is synced automatically.
The format is identical everywhere, so a file can be copied between
machines (e.g. to merge history) at any time the tool isn't running.

Writes are atomic (a temp file, then an atomic replace) and serialized with
a lock file, so two `worktime` commands never race. To back up your data,
just copy the CSV file. Other files that live next to it:

- `reports/` — generated HTML reports
- `<file>.lock` — the write lock (not user-editable)
- `server.pid` — the running dashboard server's PID
- `server.log` — the dashboard server's stdout/stderr
- `report.log` — stderr of the background report catch-up
- `error.log` — tracebacks from unexpected errors in shortcut-triggered
  commands

## Troubleshooting

- **No notification appears (macOS):** notifications are sent via
  `osascript`, which Notification Center attributes to "Script Editor".
  Check System Settings → Notifications → Script Editor and allow
  notifications there.
- **No notification appears (Debian):** is a notification daemon running
  (e.g. `dunst`)? Test directly with `notify-send test hi`.
- **Spotlight says "SystemIntents does not have permission to open
  (null)":** the app's ad-hoc signature is missing or stale — re-run
  `platform/macos/install.sh` to re-sign it.
- **"Port … is used by another program":** another process is using the
  configured port — set a different `port` in your config file.
- **The dashboard shows old behaviour after an update:** run `worktime
  stop-server`, then open the dashboard again.
- **A session was forgotten for more than 24 hours:** `stop --at` can't
  close it (sessions must stay under 24h) — edit the last row of the CSV
  file by hand instead (see [Your data](#your-data)).
- **A CSV error mentions a line number:** open the file and fix that
  specific line; the parser is strict about the format.
- **Logs:** next to the CSV file — `error.log` (unexpected errors from
  shortcut commands), `report.log` (background report catch-up),
  `server.log` (the dashboard server).
- **"no Python >= 3.11 found":** install Python 3.11+, or point
  `WORKTIME_PYTHON` at one.
- **polybar shows `err`:** the CSV or config is broken; run `worktime
  status` in a terminal to see the actual error.

## Updating

```sh
git pull
worktime stop-server
```

If the repository was moved or renamed, re-run the platform install script
(`platform/macos/install.sh` or `platform/linux/install.sh`) so the
launchers pick up the new absolute path.

## Development

```
 platform/macos (Aerospace, Spotlight .app)   platform/linux (i3, rofi .desktop)
                 └───────────── bin/worktime <cmd> ─────────────┘
                                     │
   worktime/cli.py ── session.py ── store.py ──► worktime.csv
        │               └── notify.py (osascript | notify-send)
        ├── server.py (http.server, 127.0.0.1) ── /api/* + serves web/
        └── report.py ── stats.py ──► reports/*.html
```

Project layout:

```
bin/worktime            POSIX sh launcher (finds Python >= 3.11, execs `python -m worktime`)
worktime/
  cli.py                 argparse CLI, subcommands, error handling
  config.py               TOML config loading
  store.py                CSV read/write, locking, Entry
  session.py               start/stop/status logic
  notify.py                 desktop notifications (macOS/Linux)
  browser.py                open a URL in the default browser
  stats.py                  aggregation: daily/period totals, target, balance
  server.py                  dashboard HTTP server + JSON API
  control.py                  background server lifecycle (start/stop/probe)
  report.py                   weekly/monthly HTML reports with SVG charts
web/                     static dashboard (index.html, style.css, app.js, vendor/chart.umd.js)
platform/
  macos/                  install.sh, aerospace-bindings.toml, README.md
  linux/                  install.sh, i3-bindings.conf, polybar-module.ini, README.md
    icons/                  WorkTime icon for the .desktop launchers
tools/
  make_demo_data.py       reproducible demo CSV generator
tests/                   unit tests (unittest, stdlib)
  fixtures/                test fixtures (e.g. for the Linux installer tests)
docs/                    reference-dashboard.jpeg (design reference)
```

Run the tests:

```sh
python3 -m unittest discover -s tests
```

Try the dashboard or reports with demo data instead of your real CSV:

```sh
python3 tools/make_demo_data.py /tmp/wt-demo/wt.csv
cat > /tmp/wt-demo/c.toml <<'EOF'
data_file = "/tmp/wt-demo/wt.csv"
port = 8799
EOF
WORKTIME_CONFIG=/tmp/wt-demo/c.toml bin/worktime dashboard
```

Optional JS syntax check (needs `node`):

```sh
node --check web/app.js
```

The project is developed in a Planner/Executor workflow, described in
[`CLAUDE.md`](CLAUDE.md).
