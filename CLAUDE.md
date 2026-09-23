# WorkTime Logger

## Project description

WorkTime Logger is a personal, shortcut-driven work time tracker for a **single activity** (no projects, tags or project filtering). Keyboard shortcuts start a session, show the running time as a desktop notification, and stop the session. Every session is stored in one CSV file. A localhost web dashboard and on-demand weekly/monthly HTML reports read from that CSV.

It runs on **macOS** (development machine; Aerospace + Spotlight) and **Linux Debian** (main daily use; i3 + rofi). Each machine keeps **its own CSV file**, and nothing is synced. The file format is identical on both OSes, so a CSV can be copied between machines. No accounts, no cloud: Python 3 standard library only.

## Architecture and how it works

```
 platform/macos (Aerospace, Spotlight .app)   platform/linux (i3, rofi .desktop)
                 └───────────── bin/worktime <cmd> ─────────────┘
                                     │
   worktime/cli.py ── session.py ── store.py ──► worktime.csv
        │               └── notify.py (osascript | notify-send)
        ├── server.py (http.server, 127.0.0.1) ── /api/* + serves web/
        └── report.py ── stats.py ──► reports/*.html
```

- **Language / deps:** Python 3.11+ standard library only (`csv`, `datetime`, `http.server`, `tomllib`, `fcntl`, `unittest`). Chart.js is vendored in `web/vendor/`, with no CDN, so everything works offline.
- **`bin/worktime`:** POSIX sh launcher. It follows symlinks, picks the first Python ≥ 3.11 (`$WORKTIME_PYTHON`, `python3` on PATH, `/opt/homebrew/bin`, `/usr/local/bin`, `/usr/bin`) and runs `python -m worktime`. It exists because shortcut launchers run with a minimal PATH, where macOS `/usr/bin/python3` is 3.9 (no `tomllib`).
- **`worktime/store.py`:** CSV read/write. Columns: `date,start,end,duration_min` (e.g. `2026-09-23,08:12:05,12:30:40,258`).
  - Local time, `YYYY-MM-DD` / `HH:MM:SS`, UTF-8, `\n` line endings, header row.
  - A **running session** is the row with an empty `end` and `duration_min`. It is the only state, so there is no separate state file.
  - Writes are atomic (temp file + `os.replace`) and guarded by an `fcntl` lock file.
  - A session crossing midnight belongs to its start date. `end < start` means the end is on the next day. So sessions must be under 24h (`Entry` raises `ValueError` otherwise).
  - Timestamps are the truth. `duration_min` is written rounded half-up, but on read it is only syntax-checked, then recomputed.
  - Strict reading: any malformed row raises `StoreError` with the line number. Only the last row may be running.
  - API: `Entry(start, end=None)` (with `date`, `running`, `duration`, `duration_min`, `elapsed(now)`), `read_entries`, `write_entries` (sorts, atomic, mode 0600), `running_entry`, `locked(path, timeout)` (`fcntl.flock` on `<csv>.lock`), `update_entries(path, fn)` (lock + read + fn + write).
- **`worktime/session.py`:** start/stop/status logic.
  - It never prints or notifies anything. Functions take `now` as a parameter and return `StartResult` / `StopResult` / `StatusResult`. User-facing errors raise `SessionError`.
  - `start` and `stop` each do exactly one `store.update_entries` call, so the whole read–decide–write step runs under the lock.
  - `start` with nothing running starts a session and notifies "Started at HH:MM".
  - `start` while a session is running changes nothing. It notifies "Currently running: 1h 24m", with "Since HH:MM · Today: …" on the second line.
  - `stop` closes the session and notifies "Stopped: 3h 02m", with "Today: …" on the second line. With nothing running it notifies "No session running" and exits 1.
  - `--at HH:MM` (for both `start` and `stop`) resolves to the most recent non-future occurrence of that time. `start --at` refuses overlaps with the previous session and refuses while a session is running. `stop --at` refuses times before the session start.
  - A forgotten session of 24h or more: `stop` refuses and suggests `stop --at HH:MM`. If that still gives 24h or more, the error says to edit the last CSV row manually.
  - `format_duration` rounds down to minutes: `45m`, `1h 04m`, `25h 03m`. `today_total` counts sessions whose start date is today, including the running one.
- **`worktime/notify.py`:** The only OS-specific part of the core. `notify(title, body)` never raises.
  - macOS: `osascript` with the title and body passed as argv, not interpolated into the script. Notifications appear as coming from "Script Editor".
  - Linux: `notify-send -a WorkTime -t 5000`.
  - A missing tool, a failure or an unknown OS falls back to stderr. `WORKTIME_NO_NOTIFY=1` disables notifications (the tests use this).
- **`worktime/config.py`:** TOML config, loaded into a frozen `Config(data_file, reports_dir, daily_target_min, workdays, port)`. Invalid values raise `ConfigError`.
  - Location: `$WORKTIME_CONFIG`, else `$XDG_CONFIG_HOME/worktime/config.toml`, else `~/.config/worktime/config.toml`. A missing file means defaults. See `config.example.toml`.
  - Keys: `data_file`, `reports_dir`, `daily_target` (`"H:MM"` or number of hours, **default 8:30**), `workdays` (default mon–fri; stored as 0=Mon), `port` (default 8765). Unknown keys are an error.
  - Default data path on both OSes: `~/.local/share/worktime/worktime.csv`. Reports go to `reports/` next to it.
- **`worktime/stats.py`:** All aggregation (per day/week/month, target, balance, averages, longest session, session counts). The API and the reports both use it, so their numbers are consistent.
  - Daily balance = today's worked time − target. The target is 0 on days that aren't workdays.
- **`worktime/server.py`:** `http.server` bound to `127.0.0.1`. It re-reads the CSV on every request.
  - `worktime dashboard` starts the server in the background if it isn't running, then opens the browser (`open` / `xdg-open`).
- **`web/`:** The static dashboard (reference: `example-dashboard.jpeg`, minus all project elements).
  - Header with Running status and generated timestamp.
  - Summary cards (today/week/month/total + daily balance).
  - Range filter (7d/30d/90d/this year/all/custom).
  - Daily bar chart with a target line.
  - Weekly/monthly trend of actual vs. target, with tooltips.
  - Recent-entries table with a running flag.
- **`worktime/report.py`:** Weekly and monthly reports as self-contained HTML with inline SVG charts drawn by Python. It includes total hours, average per day, sessions, longest session and over/under target.
- **`platform/macos/`, `platform/linux/`:** Thin launcher layer: keybinding snippets (Aerospace / i3), Spotlight `.app` bundles in `~/Applications` / rofi `.desktop` entries, and install scripts that **print** snippets rather than editing WM configs.
- **CLI** (`worktime/cli.py`, argparse, subcommands via `set_defaults(func=...)`): implemented: `--version`, `config`, `start [--at HH:MM]`, `stop [--at HH:MM]`, `status` (prints only, no notification). Planned: `dashboard | serve | report week|month [--date YYYY-MM-DD]`.
  - Errors: `ConfigError`, `StoreError` and `SessionError` print `worktime: …` to stderr and exit 1. For `start` and `stop` they also send a "WorkTime error" notification, since stderr isn't visible when triggered from a shortcut.

## Workflow

The Planner (Opus 5.5, `~/.claude/agents/planner.md`) plans, dispatches and reviews. Executors (`~/.claude/agents/executor.md`, Sonnet 5 or Haiku 4.5, chosen per subagent by the Planner with Basil's approval) implement. Basil has the final word on every plan and every model choice. Tasks run via `/task`.

## Workflow settings

- Max executors: 3
- GitHub: private
- Remote: https://github.com/bzemann/time-logger (SSH: git@github.com:bzemann/time-logger.git)

## Current status

- Implemented: tasks 1–2 (launcher, config, CSV store, start/stop/status with `--at`, desktop notifications). 117 unit tests pass. The macOS notification was confirmed visually by Basil.
- Open: roadmap items 3–10.
- To do in task 8/9: unexpected exceptions (e.g. an unwritable data folder) currently produce only a traceback and no notification. Add a catch-all error notification for shortcut-triggered commands.
- Known limitations: naive local time, so a session spanning a DST change is off by 1h (accepted). Sessions of 24h or more can't be represented. `stop` refuses them (see session.py), so a session forgotten for over a day has to be fixed in the CSV by hand.
- Notes: the reference dashboard screenshot is `example-dashboard.jpeg` (repo root). Design notes from it for tasks 5–6:
  - Light theme with white rounded cards; the original UI labels are German.
  - The "Läuft" status pill is green, with the generated timestamp next to it.
  - Cards (Heute/Woche/Monat/Total) show the **balance vs. target** big (e.g. `+03:40`) and the worked time small below it. The "Heute" card shows worked time big and the balance small.
  - The target line in the example is about 8.4h (42h/week), so the target must be configurable in hours and minutes.
  - Weekly trend labels use ISO calendar weeks (KW39).
  - Drop everything project-related: the project filter chips, the donut chart, stacked bars and the project column.
  - Open question for task 5: UI language (German vs. English).

## Roadmap

1. ✅ **Skeleton, config and CSV store** (done 2026-09-23): package layout, `bin/worktime`, `config.py`, `store.py` (format, atomic write, lock, midnight rule), unit tests.
2. ✅ **Sessions, CLI and notifications** (done 2026-09-23): `start` (status notification while running), `stop`, `status`, `notify.py` for macOS and Linux, unit tests with notifications mocked.
3. **Stats module:** day/week/month aggregation, target and balance, averages, longest session, session counts, unit tests.
4. **Web server and JSON API:** `serve`, `dashboard` (auto-start + open browser), endpoints for status, summary and entries by range.
5. **Dashboard part 1** *(reference: `example-dashboard.jpeg`)*: layout, header/status/timestamp, summary cards with daily balance, range filter, daily bar chart with target line.
6. **Dashboard part 2:** weekly/monthly actual-vs-target trend chart with tooltips, recent-entries table with running flag.
7. **Reports:** `report week|month [--date]` as self-contained HTML with stats and SVG charts (daily bars, trend line), saved to `reports/`.
8. **macOS launcher layer:** Aerospace keybinding snippet, Spotlight `.app` bundles, install script.
9. **Linux launcher layer:** i3 keybinding snippet, rofi `.desktop` entries (optional rofi menu), install script.
10. **README and end-to-end check:** `README.md` describing installation, configuration and daily usage on **both macOS and Linux Debian** (shortcuts, dashboard, reports, CSV format, troubleshooting), plus an end-to-end check on both OSes.

## Setup and run

- Requirements: Python 3.11+ (macOS: Homebrew `python3`; Debian 12+: system `python3`). Linux notifications: `notify-send` (`libnotify-bin`) + a notification daemon (e.g. dunst).
- Show effective config: `bin/worktime config` (`bin/worktime --version`)
- Track time: `bin/worktime start [--at HH:MM]`, `bin/worktime stop [--at HH:MM]`, `bin/worktime status`
- Quiet mode for development: `WORKTIME_NO_NOTIFY=1 bin/worktime …`
- Dashboard (after task 4): `bin/worktime dashboard`
- Tests: `python3 -m unittest discover -s tests`

## Changelog

- 2026-09-23: Project setup: Git (`main`), `.gitignore`, `.claude/settings.json` (Planner agent), `CLAUDE.md` with architecture and roadmap, private GitHub repo.
- 2026-09-23: Task 1: `bin/worktime` launcher (finds Python ≥ 3.11 under a minimal PATH), CLI skeleton, TOML config (default target 8:30), strict CSV store with atomic writes and `flock` locking, 64 unit tests.
- 2026-09-23: Task 2: `start`/`stop`/`status` with `--at HH:MM`, 24h and overlap protection, desktop notifications (`osascript` / `notify-send`, stderr fallback, `WORKTIME_NO_NOTIFY`), error notifications for shortcut commands. 117 tests.
