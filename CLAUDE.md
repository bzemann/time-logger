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
- **`worktime/store.py`:** CSV read/write. Columns: `date,start,end,duration_min` (e.g. `2026-09-23,08:12:05,12:30:40,258`).
  - Local time, `YYYY-MM-DD` / `HH:MM:SS`, UTF-8, `\n` line endings, header row.
  - A **running session** is the row with an empty `end` and `duration_min`. It is the only state, so there is no separate state file.
  - Writes are atomic (temp file + `os.replace`) and guarded by an `fcntl` lock file.
  - A session crossing midnight belongs to its start date. `end < start` means the end is on the next day.
- **`worktime/session.py`:** start/stop/status logic.
  - `start` with nothing running starts a session and notifies "Started at HH:MM".
  - `start` while a session is running only notifies "Currently running: 1h 24m".
  - `stop` closes the session and notifies the session duration and today's total.
- **`worktime/notify.py`:** The only OS-specific part of the core. It uses `osascript` (macOS Notification Center) or `notify-send` (Linux/dunst).
- **`worktime/config.py`:** TOML config: data path, daily target hours (default 8), workdays that count for the target (default Mon–Fri), server port.
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
- **CLI:** `worktime start | stop | status | dashboard | serve | report week|month [--date YYYY-MM-DD]`.

## Workflow

The Planner (Opus 5.5, `~/.claude/agents/planner.md`) plans, dispatches and reviews. Executors (`~/.claude/agents/executor.md`, Sonnet 5 or Haiku 4.5, chosen per subagent by the Planner with Basil's approval) implement. Basil has the final word on every plan and every model choice. Tasks run via `/task`.

## Workflow settings

- Max executors: 3
- GitHub: private
- Remote: https://github.com/bzemann/time-logger (SSH: git@github.com:bzemann/time-logger.git)

## Current status

- Implemented: nothing yet (project setup only).
- Open: all roadmap items.
- Known issues / notes: the reference dashboard screenshot is `example-dashboard.jpeg` (repo root). Design notes from it for tasks 5–6:
  - Light theme with white rounded cards; the original UI labels are German.
  - The "Läuft" status pill is green, with the generated timestamp next to it.
  - Cards (Heute/Woche/Monat/Total) show the **balance vs. target** big (e.g. `+03:40`) and the worked time small below it. The "Heute" card shows worked time big and the balance small.
  - The target line in the example is about 8.4h (42h/week), so the target must be configurable in hours and minutes.
  - Weekly trend labels use ISO calendar weeks (KW39).
  - Drop everything project-related: the project filter chips, the donut chart, stacked bars and the project column.
  - Open question for task 5: UI language (German vs. English).

## Roadmap

1. **Skeleton, config and CSV store:** package layout, `bin/worktime`, `config.py`, `store.py` (format, atomic write, lock, midnight rule), unit tests.
2. **Sessions, CLI and notifications:** `start` (status notification while running), `stop`, `status`, `notify.py` for macOS and Linux, unit tests with notifications mocked.
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
- Run (once task 1–2 exist): `bin/worktime start | stop | status`
- Dashboard (after task 4): `bin/worktime dashboard`
- Tests: `python3 -m unittest discover -s tests`

## Changelog

- 2026-09-23: Project setup: Git (`main`), `.gitignore`, `.claude/settings.json` (Planner agent), `CLAUDE.md` with architecture and roadmap, private GitHub repo.
