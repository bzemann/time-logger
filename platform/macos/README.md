# WorkTime Logger — macOS launcher layer

Thin launcher layer on top of `bin/worktime`: Spotlight-searchable apps and
an Aerospace keybinding snippet. Nothing here changes `worktime`'s
behaviour, it only adds ways to trigger it.

## What gets installed

Running `platform/macos/install.sh` creates:

- 5 tiny `.app` bundles in `~/Applications`, each a local shell launcher.
  The installer ad-hoc signs each app locally (`codesign --sign -`, no
  Apple account, no network), which newer macOS Spotlight requires to open
  them:
  - `WorkTime Start.app`
  - `WorkTime Stop.app`
  - `WorkTime Dashboard.app`
  - `WorkTime Weekly Report.app`
  - `WorkTime Monthly Report.app`
- a `worktime` symlink in `~/.local/bin`, pointing at `bin/worktime`, for
  terminal use.
- printed Aerospace keybinding lines (see below) — the script never edits
  your `aerospace.toml` automatically. Paste the printed lines in yourself.

## Install / uninstall

```sh
platform/macos/install.sh              # install (safe to run again)
platform/macos/install.sh --uninstall  # remove the apps and the symlink
platform/macos/install.sh --help
```

The install is idempotent: running it twice produces the same result. It
never overwrites an app or symlink it did not create itself — if something
with the same name already exists and wasn't made by this script, it is
skipped with a message instead.

If `~/.local/bin` is not already on your `PATH`, the script prints the line
to add to `~/.zshrc`.

## Aerospace shortcuts

| Shortcut       | Action                          |
|----------------|----------------------------------|
| `alt-shift-t`  | start / show running time        |
| `alt-shift-x`  | stop                             |
| `alt-shift-d`  | open the dashboard                |
| `alt-shift-r`  | weekly report (this week so far) |

Paste the lines printed by `install.sh` under `[mode.main.binding]` in
`~/.config/aerospace/aerospace.toml` (or `~/.aerospace.toml`), then reload
the config (`alt-shift-c`). A template with the `@WORKTIME@` placeholder is
in `platform/macos/aerospace-bindings.toml`.

There is no shortcut for the monthly report — use Spotlight (see below) or
run `worktime report month` from the terminal.

## Spotlight

Press `cmd-space` and type `worktime` — all 5 apps show up (e.g. "WorkTime
Dashboard", "WorkTime Monthly Report").

## Notification permission

Notifications are sent via `osascript`, so on the first run macOS may ask
you to allow notifications for "Script Editor" (that's how `osascript`
identifies itself). If nothing appears, check System Settings →
Notifications → Script Editor.

## Troubleshooting

If Spotlight shows "SystemIntents does not have permission to open" when
you try to launch one of the apps, its signature is missing or stale —
re-run `platform/macos/install.sh` to re-sign it.

## After moving the repository

The launchers hard-code the absolute path to `bin/worktime` at install
time. If you move or rename the repository, re-run `install.sh` to
regenerate them.

## Logs

Logs live next to the CSV data file (see `worktime config` for the path):
`error.log`, `report.log` and `server.log`.
