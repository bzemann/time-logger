# Changelog

All notable changes to WorkTime Logger, for people running it. Newest
version first.

## 0.2.0 — 2026-09-24

### New

- Per-date targets (`target_overrides`) for half days and other special
  days: a config table mapping a date or inclusive date range to a target
  that replaces the normal one for that date, even on weekends. Applies
  retroactively to balances; `worktime config` shows a summary line.
- `worktime status --short` for status bars, and a ready-to-paste polybar
  module (Debian) that shows the running time and opens the dashboard on
  click.
- Debian: keys `$mod+shift+t/u/d/w`, a rofi icon for the launcher entries,
  and a printed hint for opening the rofi app launcher.

### Changed

- The dashboard server restarts itself automatically after an update
  (when its version no longer matches the code on disk), instead of
  requiring `worktime stop-server` after every `git pull`.
- Both install scripts now frame everything meant to be pasted between
  `──── paste into … ────` / `──── replace this line in … ────` and
  `──── end ────` lines, so it's unambiguous what to copy.

### What you need to do

- macOS: `git pull`, then re-run `platform/macos/install.sh` (nothing new
  to paste unless it says so).
- Debian: `git pull`, then re-run `platform/linux/install.sh`; if you use
  polybar, paste the printed module and `modules-right` line, then
  restart it with `~/.config/polybar/launch.sh`.

## 0.1.0 — 2026-09-23

First version: shortcut-driven start/stop/status with desktop
notifications, a local-only web dashboard with charts and a range filter,
self-contained weekly/monthly HTML reports with automatic catch-up, and
launcher layers for both macOS (Aerospace, Spotlight) and Debian (i3,
rofi).
