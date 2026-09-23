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
- **`worktime/notify.py`:** One of the two OS-specific parts of the core (the other is `browser.py`). `notify(title, body)` never raises.
  - macOS: `osascript` with the title and body passed as argv, not interpolated into the script. Notifications appear as coming from "Script Editor".
  - Linux: `notify-send -a WorkTime -t 5000`.
  - A missing tool, a failure or an unknown OS falls back to stderr. `WORKTIME_NO_NOTIFY=1` disables notifications (the tests use this).
- **`worktime/config.py`:** TOML config, loaded into a frozen `Config(data_file, reports_dir, daily_target_min, workdays, port)`. Invalid values raise `ConfigError`.
  - Location: `$WORKTIME_CONFIG`, else `$XDG_CONFIG_HOME/worktime/config.toml`, else `~/.config/worktime/config.toml`. A missing file means defaults. See `config.example.toml`.
  - Keys: `data_file`, `reports_dir`, `daily_target` (`"H:MM"` or number of hours, **default 8:30**), `workdays` (default mon–fri; stored as 0=Mon), `days_off` (list of `"YYYY-MM-DD"` or inclusive `"YYYY-MM-DD..YYYY-MM-DD"` ranges up to 366 days; stored as `frozenset[date]`; default empty), `port` (default 8765). Unknown keys are an error.
  - Default data path on both OSes: `~/.local/share/worktime/worktime.csv`. Reports go to `reports/` next to it.
- **`worktime/stats.py`:** All aggregation. Pure functions with no I/O. The API (task 4) and the reports (task 7) both use it, so their numbers are consistent.
  - Rules:
    - The target per day is `daily_target` on workdays that aren't in `days_off`, else 0.
    - Worked time per day is the sum of `elapsed(now)` for sessions *starting* that day (a running session counts with its time so far).
    - The target of a period covers only days in `[max(start, first entry date), min(end, today)]`. Today counts with its full target; days before tracking started or in the future count 0.
    - Balance = worked − target.
    - Everything is exact-second `timedelta`; rounding happens at display time only.
    - This reproduces the screenshot: a week with 28h 52m worked at a 8:24 target over 3 days gives +03:40.
  - API:
    - `Target(daily, workdays, days_off)` with `.for_day(d)` and `.from_config(cfg)`.
    - `tracking_start(entries)`.
    - `daily_totals(entries, now, start, end)` covers every day, zeros included.
    - `summarize(...) -> PeriodSummary(start, end, worked, target, balance, sessions, days_worked, target_days, avg_per_day_worked, avg_per_target_day, longest)`. `longest` is finished sessions only.
    - `overview(entries, now, target)` returns the keys today, week (ISO Mon–Sun), month and total.
    - `buckets(..., unit="week"|"month")` returns `Bucket(label, start, end, worked, target, balance, cumulative_balance)`. Labels are `2026-W39` / `2026-09`, and edge buckets are clipped to the range.
    - `resolve_range("7d"|"30d"|"90d"|"year"|"all", now, entries)`.
- **`worktime/server.py`:** `ThreadingHTTPServer`, bound to `127.0.0.1` only. Config and CSV are re-read on **every request**, so `days_off` and other config edits apply without a restart. Only `port` needs one.
  - Security:
    - GET only, no write endpoints.
    - The `Host` header must be `127.0.0.1:<port>` or `localhost:<port>`, else 403 (protects against DNS rebinding).
    - Static files come only from `web/`; resolved paths that leave it give 404.
    - Every response sets `Cache-Control: no-store`.
  - Routes:
    - `/api/health` returns `{"app": "worktime", "version"}`.
    - `/api/dashboard` takes `?range=7d|30d|90d|year|all` (default `30d`), or `?start=&end=` (both, YYYY-MM-DD), plus `&unit=week|month` (default week) and `&limit=1..500` (default 50). Bad parameters give 400, a broken CSV or config gives 500; both return `{"error"}`.
    - Anything else is a static file from `web/`.
  - The `/api/dashboard` JSON is built by `build_dashboard(entries, now, cfg, params)`, a pure function. All durations are **integer seconds** (`*_s`).
    - `version`, `generated` (ISO), `status {running, since, elapsed_s}`, `settings {daily_target_s, workdays}`.
    - `overview {today, week, month, total}` and `range_summary`: each a summary with `start, end, worked_s, target_s, balance_s, sessions, days_worked, target_days, avg_per_day_worked_s, avg_per_target_day_s, longest {date, start, end, duration_s} | null`.
    - `range {name, start, end, unit}`.
    - `days [{date, worked_s, target_s}]` (every day of the range; the target follows the window rule via `stats.daily_targets`).
    - `trend [{label, start, end, worked_s, target_s, balance_s, cumulative_balance_s}]`.
    - `entries [{date, start, end|null, duration_s, running}]`: the **50 most recent sessions overall, newest first, not filtered by the range** (Basil's decision).
  - `run(host, port, pid_file)` writes `server.pid`, handles SIGTERM and Ctrl-C, and removes the PID file on exit. There is no access log; errors go to stderr.
  - `pid_path(cfg)` and `log_path(cfg)` give `server.pid` and `server.log` next to the CSV.
- **`worktime/control.py`:** background server lifecycle.
  - `probe(port)` returns "ours", "free" or "other", based on `/api/health`.
  - `start_background(cfg)` spawns `python -m worktime serve` detached (`start_new_session`, output to `server.log`) and waits up to 5 s for it to answer.
  - `stop_background(cfg)` reads the PID file, checks the server is ours, sends SIGTERM and waits. It also cleans up stale or invalid PID files.
  - **After code updates, run `worktime stop-server` once**, because a running server keeps serving the old code.
- **`worktime/browser.py`:** `open_url(url)` uses `open` on macOS and `xdg-open` on Linux, falling back to `webbrowser`. It never raises. `WORKTIME_NO_BROWSER=1` disables it (the tests use this).
- **`web/`:** The static dashboard (reference: `example-dashboard.jpeg`, minus all project elements). It fetches `/api/dashboard`.
  - Files:
    - `index.html`: structure, with a CSP meta tag and no external URLs.
    - `style.css`: all colors are CSS variables. Light theme like the screenshot; dark mode via `prefers-color-scheme`, with its own chosen values.
    - `app.js`: plain JS in one IIFE, in 11 commented sections: formatting → theme tokens → range + trend-unit state → fetch → header → cards → range → daily chart → trend chart → sessions table → refresh/init.
    - `vendor/chart.umd.js`: Chart.js **4.4.1**, vendored, with an MIT `LICENSE-chartjs.md`.
  - UI language **English**. Dates are Swiss style `23.09.2026` (`Mon 21.09.` in the chart). Durations look like `7h 09m`. Balances look like `+3:40` / `−1:15` / `±0:00`, colored green or red but **always signed**, so they don't rely on color alone.
  - The browser **only formats** and never computes stats. Data is inserted via `textContent` only; `tests/test_web.py` enforces no `innerHTML` and no external URLs.
  - Part 1 (task 5):
    - Header: "WorkTime", a status pill ("Running · 1h 24m" / "Not running") and the "Generated" timestamp.
    - Cards: TODAY (big = worked, sub = balance + target), THIS WEEK / THIS MONTH / TOTAL (big = balance, sub = worked + target; TOTAL adds "since").
    - Range card: presets 7d/30d/90d/year/all plus custom dates, with a range summary line. **Default 30 days, remembered in `localStorage`** (`worktime.range.v1`).
    - "Hours per day" bar chart: single blue series, no legend, ≤24px bars with rounded tops. A custom `targetLine` plugin draws the dashed "Target 8:30" line. The tooltip shows worked / target / balance, plus "(running)" for today.
  - Behavior:
    - It refreshes every 60 s and on focus or visibility.
    - Stale responses are ignored by sequence number.
    - While loading, the previous render stays visible (dimmed).
    - Errors show in a banner.
    - The chart instance is updated in place, never recreated.
  - Part 2 (task 6):
    - "Actual vs. target" trend chart, covering the selected range:
      - Chart.js line chart: worked hours as a blue area (2px line, `--series-wash` fill, 8px dots with a surface ring) and target hours as a dashed gray line. HTML legend "Worked / Target".
      - **Weekly | Monthly** toggle in the card header (default Weekly, remembered in `localStorage` as `worktime.trend.v1`). It re-fetches with `unit=`.
      - A `crosshair` plugin draws a vertical hairline at the hovered period.
      - Tooltip: title "W39 · 21.09.–27.09.2026" or "September 2026" (clipped edge periods show their dates), then Worked / Target / Balance / Cumulative. X-axis labels "W39" / "Sep 26".
      - The cumulative balance is deliberately **only in the tooltip**, not drawn as a third line (Basil's decision).
    - "Recent sessions (latest 50)" table, deliberately **not range-filtered**:
      - Columns Date / Start / End / Duration, with `tabular-nums`.
      - The running row is highlighted and shows a "● Running" badge.
      - A session ending after midnight shows "00:20 (+1)".
      - Empty state: "No sessions yet…".
- **`tools/make_demo_data.py PATH [--days 120] [--seed 1] [--now …] [--no-running] [--force]`:** a reproducible, realistic demo CSV (written via `store.write_entries`). It refuses to overwrite without `--force`. Use it with a temporary config pointed at by `WORKTIME_CONFIG` to try the dashboard or reports without touching real data.
  - Header with Running status and generated timestamp.
  - Summary cards (today/week/month/total + daily balance).
  - Range filter (7d/30d/90d/this year/all/custom).
  - Daily bar chart with a target line.
  - Weekly/monthly trend of actual vs. target, with tooltips.
  - Recent-entries table with a running flag.
- **`worktime/report.py`:** Weekly and monthly reports as **one self-contained HTML file each**. There is no PDF (Basil's decision); print to PDF from the browser, since an A4 print stylesheet is included.
  - The HTML has no JavaScript and no external resources. SVG charts are drawn by Python, and all text is escaped.
  - Periods: `Period(kind, start, end, title, stem)`. A week is ISO Mon–Sun (`week-2026-W39`, "Week 39, 2026"); a month is a calendar month (`month-2026-09`, "September 2026").
    - `period_for(kind, ref_date)` and `previous_period(kind, today)` compute them.
    - `report_path(cfg, p)` is `reports_dir/<stem>.html`.
  - Three layers:
    1. `build_report(entries, now, cfg, period) -> Report`: pure data from `stats` (summary, `DayRow`s, cumulative series, running entry).
    2. `render_html(report)`: header with an "In progress" note, 8 tiles (worked, target, balance, sessions, Ø per day worked, Ø per target day, days worked, longest session), then "Hours per day" (`svg_daily_bars`, with target line), "Cumulative worked vs. target" (`svg_cumulative`, two lines with end labels) and the "Daily breakdown" table with a Total row. An empty period shows "No sessions in this period".
    3. SVG helpers: a 720×240 viewBox, `nice_scale` for the hour ticks, and `<title>` hover values on every bar and point.
    - `write_report(cfg, period, now)` reads the CSV and writes atomically. It never notifies or opens anything.
  - Each file carries `<meta name="worktime:status" content="final|in-progress">` (final = the period is over), read by `read_status(path)`.
  - `due_reports(cfg, now)` returns the last complete week and month whose report is missing or not final, skipping periods that end before the first entry. There is no backfill of older periods.
- **Automatic reports (catch-up):** after `start` and `dashboard`, `cli._maybe_start_catch_up` calls `due_reports`. If anything is due, it spawns a detached `python -m worktime report catch-up` (stderr goes to `report.log` next to the CSV), so the shortcut stays instant.
  - It never breaks `start` or `dashboard`, and `WORKTIME_NO_AUTO_REPORTS=1` disables it (the tests use this).
  - `report catch-up` writes the due reports, sends one notification each and never opens a browser. Final reports are never overwritten automatically.
- **`platform/macos/`** (done, task 8): `install.sh [--uninstall|--help]` (POSIX sh), `aerospace-bindings.toml` (template with `@WORKTIME@`), `README.md`.
  - Creates 5 Spotlight launchers in `~/Applications`: "WorkTime Start / Stop / Dashboard / Weekly Report / Monthly Report".
    - Each is a minimal `.app`: `Info.plist` with `CFBundleIdentifier local.worktime.<id>`, `LSUIElement` (no Dock icon) and `CFBundleInfoDictionaryVersion 6.0`, plus `Contents/MacOS/worktime-launcher`, a 2-line sh script that execs the absolute path to `bin/worktime`.
  - **The apps are ad-hoc code signed** (`codesign --force --sign -`). Unsigned bundles made macOS 27 Spotlight fail intermittently with "SystemIntents does not have permission to open (null)".
  - It also symlinks `~/.local/bin/worktime` and **prints** the Aerospace lines, with a conflict / "already configured" check. It never edits the Aerospace config itself.
  - It never overwrites foreign apps or a real file at the symlink path. Uninstall removes only its own files. It is idempotent.
  - `WORKTIME_INSTALL_NO_REGISTER=1` skips `lsregister`/`mdimport` (used by the tests).
  - Re-run it after moving the repo (the launchers contain the absolute path).
  - Keys:
    - `alt-shift-t`: start, or show the running time
    - `alt-shift-x`: stop
    - `alt-shift-d`: dashboard
    - `alt-shift-r`: weekly report (this week so far)
    - The monthly report is only via Spotlight or the CLI.
  - On Basil's Mac: installed. The 4 lines were added to `~/.config/aerospace/aerospace.toml` after the `alt-shift-a` line (backup `aerospace.toml.bak`).
- **`platform/linux/`** (implemented in task 9; **Debian check pending**): `install.sh [--uninstall|--help]` (POSIX sh, dash-safe), `i3-bindings.conf` (template with `@WORKTIME@`), `README.md` (getting the private repo onto Debian, plus a Debian checklist).
  - Creates 5 `.desktop` files in `${XDG_DATA_HOME:-~/.local/share}/applications` (`worktime-start`, `-stop`, `-dashboard`, `-report-week`, `-report-month`). rofi `drun` lists them.
    - Each has the marker `X-WorkTime-Launcher=true`; only files with the marker are touched.
    - `Exec=<abs path>/bin/worktime <args>`, unquoted.
  - Runs `update-desktop-database` best-effort.
  - Symlinks `~/.local/bin/worktime`.
  - Prints hints for missing `notify-send` / `xdg-open` (`sudo apt install libnotify-bin xdg-utils`, plus dunst).
  - Prints the i3 lines with a conflict check against `~/.config/i3/config` / `~/.i3/config`. It matches `$mod`/`Mod1`/`Mod4` and both key orders, with a word boundary so `$mod+Shift+tab` isn't `t`. It never edits the i3 config.
  - Keys:
    - `$mod+Shift+t`: start, or show the running time
    - `$mod+Shift+x`: stop
    - `$mod+Shift+d`: dashboard
    - `$mod+Shift+w`: weekly report (**not `r`**: that is i3 restart by default)
    - The monthly report is only via rofi or the CLI.
  - The repo path must match `[A-Za-z0-9._/-]`, since i3 `exec` and `.desktop` `Exec` are written unquoted.
  - `WORKTIME_INSTALL_ALLOW_ANY_OS=1` is a test-only hook so the tests run on macOS. `WORKTIME_INSTALL_NO_REGISTER=1` skips `update-desktop-database`.
- **Unexpected errors:**
  - In `cli.main`, any other `Exception` from a shortcut command (`notify_errors=True`) writes its traceback to `error.log`, then sends a "WorkTime error: Unexpected problem (…). Details in <path>" notification and exits 1.
    - It tries the data folder first, then `~/.local/share/worktime/`. The detail text is capped at 200 characters.
  - Terminal commands (`status`, `config`, `serve`, …) re-raise, so their traceback stays visible.
  - `bin/worktime` itself notifies (via `osascript` or `notify-send`, respecting `WORKTIME_NO_NOTIFY`) when no Python ≥ 3.11 is found.
- **CLI** (`worktime/cli.py`, argparse, subcommands via `set_defaults(func=...)`): implemented: `--version`, `config`, `start [--at HH:MM]`, `stop [--at HH:MM]`, `status` (prints only, no notification). `serve [--port N]` (foreground), `dashboard` (probes the port: if ours, it opens the browser; if free, it starts the server in the background, then opens it; if another program has the port, it errors), `stop-server`. `report week|month [--last | --date YYYY-MM-DD] [--no-open]` (default: the current period so far; always overwrites; notifies and opens the browser), `report catch-up`.
  - Errors: `ConfigError`, `StoreError`, `SessionError` and `ControlError` print `worktime: …` to stderr and exit 1. For `start`, `stop`, `dashboard` and `report` they also send a "WorkTime error" notification, since stderr isn't visible when triggered from a shortcut.

## Workflow

The Planner (Opus 5.5, `~/.claude/agents/planner.md`) plans, dispatches and reviews. Executors (`~/.claude/agents/executor.md`, Sonnet 5 or Haiku 4.5, chosen per subagent by the Planner with Basil's approval) implement. Basil has the final word on every plan and every model choice. Tasks run via `/task`.

## Workflow settings

- Max executors: 3
- GitHub: private
- Remote: https://github.com/bzemann/time-logger (SSH: git@github.com:bzemann/time-logger.git)

## Current status

- Implemented: tasks 1–8 (launcher, config incl. `days_off`, CSV store, start/stop/status with `--at`, desktop notifications, stats module, local web server with JSON API, `dashboard`/`serve`/`stop-server`, the complete dashboard, demo data tool, HTML reports with automatic catch-up, the macOS launcher layer, the unexpected-error catch-all). 321 unit tests pass. Basil confirmed on his Mac: notifications, dashboard, reports, all 4 Aerospace shortcuts and all Spotlight launchers.
- Basil's real CSV was reset to header-only on 2026-09-23 (test sessions removed; backup in the session scratchpad only). Real tracking starts from there.
- Task 9 (Linux layer) is implemented and tested on macOS in test mode (342 tests pass), but **not yet verified on Basil's Debian machine**. The repo isn't cloned there yet. Next step there: follow `platform/linux/README.md` ("Getting it onto Debian", then the checklist). Fix any Debian findings in a follow-up `/task`.
- Open: roadmap item 10 (and the Debian check of item 9).
- Test-writing note for executors: never put "wait for another executor's file" loops or skips into test files. Waiting belongs only in the executor's own work session.
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
3. ✅ **Stats module** (done 2026-09-23): day/week/month aggregation, target and balance, averages, longest session, session counts, unit tests.
4. ✅ **Web server and JSON API** (done 2026-09-23): `serve`, `dashboard` (auto-start + open browser), endpoints for status, summary and entries by range.
5. ✅ **Dashboard part 1** (done 2026-09-23; reference: `example-dashboard.jpeg`): layout, header/status/timestamp, summary cards with daily balance, range filter, daily bar chart with target line.
6. ✅ **Dashboard part 2** (done 2026-09-23): weekly/monthly actual-vs-target trend chart with tooltips, recent-entries table with running flag.
7. ✅ **Reports** (done 2026-09-23): `report week|month [--last|--date] [--no-open]` as self-contained HTML with stats, SVG charts (daily bars, cumulative worked vs. target) and a daily table in `reports/`. Automatic catch-up for the last complete week and month on `start` / `dashboard`. HTML only, no PDF.
8. ✅ **macOS launcher layer** (done 2026-09-23): Aerospace keybinding snippet, Spotlight `.app` bundles, install script.
9. ✅ **Linux launcher layer** (implemented 2026-09-23, Debian check pending): i3 keybinding snippet, rofi `.desktop` entries (optional rofi menu), install script.
10. **README and end-to-end check:** `README.md` describing installation, configuration and daily usage on **both macOS and Linux Debian** (shortcuts, dashboard, reports, CSV format, troubleshooting), plus an end-to-end check on both OSes.

## Setup and run

- Requirements: Python 3.11+ (macOS: Homebrew `python3`; Debian 12+: system `python3`). Linux notifications: `notify-send` (`libnotify-bin`) + a notification daemon (e.g. dunst).
- Reports: `bin/worktime report week` (this week so far), `bin/worktime report month --last`, `bin/worktime report week --date 2026-09-23`. They are created automatically after `start` / `dashboard` (disable with `WORKTIME_NO_AUTO_REPORTS=1`).
- macOS setup: `platform/macos/install.sh`, then paste the printed Aerospace lines and reload (`alt-shift-c`). Remove it with `platform/macos/install.sh --uninstall`.
- Debian setup: see `platform/linux/README.md` (clone the private repo via `gh auth login` or an SSH key, then `platform/linux/install.sh`, paste the printed i3 lines, reload with `$mod+Shift+c`).
- Logs (next to the CSV): `error.log` (unexpected errors from shortcuts), `report.log` (background report catch-up), `server.log` (dashboard server).
- Show effective config: `bin/worktime config` (`bin/worktime --version`)
- Track time: `bin/worktime start [--at HH:MM]`, `bin/worktime stop [--at HH:MM]`, `bin/worktime status`
- Quiet mode for development: `WORKTIME_NO_NOTIFY=1 bin/worktime …`
- Dashboard: `bin/worktime dashboard` (starts the server in the background and opens the browser). Stop it: `bin/worktime stop-server`. Foreground for debugging: `bin/worktime serve [--port N]`
- Quiet mode for tests: `WORKTIME_NO_NOTIFY=1 WORKTIME_NO_BROWSER=1`
- Try with demo data: `python3 tools/make_demo_data.py /tmp/wt-demo/wt.csv`, then write `/tmp/wt-demo/c.toml` with `data_file = "/tmp/wt-demo/wt.csv"` and `port = 8799`, then run `WORKTIME_CONFIG=/tmp/wt-demo/c.toml bin/worktime dashboard`
- JS syntax check (dev only, needs node): `node --check web/app.js`
- Tests: `python3 -m unittest discover -s tests`

## Changelog

- 2026-09-23: Project setup: Git (`main`), `.gitignore`, `.claude/settings.json` (Planner agent), `CLAUDE.md` with architecture and roadmap, private GitHub repo.
- 2026-09-23: Task 1: `bin/worktime` launcher (finds Python ≥ 3.11 under a minimal PATH), CLI skeleton, TOML config (default target 8:30), strict CSV store with atomic writes and `flock` locking, 64 unit tests.
- 2026-09-23: Task 2: `start`/`stop`/`status` with `--at HH:MM`, 24h and overlap protection, desktop notifications (`osascript` / `notify-send`, stderr fallback, `WORKTIME_NO_NOTIFY`), error notifications for shortcut commands. 117 tests.
- 2026-09-23: Task 3: `worktime/stats.py` (daily totals, period summaries, dashboard overview, week/month trend buckets, range presets) with the screenshot's balance semantics. New config key `days_off` for vacation and holidays. 179 tests.
- 2026-09-23: Task 4: local web server (`server.py`, 127.0.0.1 only, Host check, safe static serving), `/api/health` and `/api/dashboard` JSON (all durations in seconds), `stats.daily_targets`, background lifecycle (`control.py`: probe, detached start, PID file, stop), `browser.py`, CLI `serve`/`dashboard`/`stop-server`, placeholder `web/index.html`. 245 tests.
- 2026-09-23: Task 5: dashboard part 1 (English UI): header with live status, balance cards, remembered range filter with summary, "Hours per day" bar chart with a target line and tooltips, dark mode, 60 s refresh, error banner. Chart.js 4.4.1 vendored. `tools/make_demo_data.py`. Static-asset, offline and no-`innerHTML` tests. 261 tests.
- 2026-09-23: Task 6: dashboard part 2: "Actual vs. target" trend chart (area plus dashed target line, Weekly/Monthly toggle remembered, crosshair, tooltip with balance and cumulative) and the "Recent sessions (latest 50)" table (running badge, next-day marker). The dashboard is complete.
- 2026-09-23: Task 7: `worktime/report.py` (self-contained HTML reports with SVG charts, tiles and a daily table, A4 print stylesheet, final/in-progress marker), CLI `report week|month|catch-up`, automatic background catch-up after `start`/`dashboard`. 296 tests.
- 2026-09-23: Task 8: macOS launcher layer (`platform/macos/install.sh`: 5 ad-hoc signed Spotlight launchers, a `~/.local/bin` symlink, printed Aerospace bindings alt-shift-t/x/d/r), a catch-all notification plus `error.log` for unexpected errors in shortcut commands, and a `bin/worktime` notification when no Python is found. Installed and verified on Basil's Mac. Real CSV reset. 321 tests.
- 2026-09-23: Task 9: Linux launcher layer (`platform/linux/install.sh`: 5 rofi `.desktop` entries, a `~/.local/bin` symlink, apt hints, printed i3 bindings $mod+Shift+t/x/d/w with a conflict check; README with Debian setup and checklist). Tested on macOS in test mode; the Debian check is pending. 342 tests.
