# WorkTime Logger — Linux (Debian, i3, rofi) launcher layer

Thin launcher layer on top of `bin/worktime`: rofi-searchable `.desktop`
entries and an i3 keybinding snippet. Nothing here changes `worktime`'s
behaviour, it only adds ways to trigger it.

## Getting it onto Debian

The repository is private, so cloning it needs GitHub access on the
Debian machine first:

```sh
sudo apt install git python3 libnotify-bin dunst xdg-utils

# access to the private GitHub repo — pick one:
sudo apt install gh && gh auth login                          # a) GitHub CLI (easiest)
ssh-keygen -t ed25519 && cat ~/.ssh/id_ed25519.pub             # b) SSH key: add it on GitHub -> Settings -> SSH and GPG keys

git clone git@github.com:bzemann/time-logger.git ~/code/time-logger
# (with gh instead: gh repo clone bzemann/time-logger ~/code/time-logger)

cd ~/code/time-logger && platform/linux/install.sh
```

Debian 12 ships Python 3.11, which is what `worktime` requires (3.11+).
Each machine keeps its own CSV data file — the CSV on a freshly set up
machine starts empty. The file format is identical on both OSes, so a CSV
can be copied between machines if you ever want to merge history.

## What gets installed

Running `platform/linux/install.sh` creates:

- 5 `.desktop` launcher files in `$XDG_DATA_HOME/applications` (default
  `~/.local/share/applications`), picked up by rofi's `drun` mode and any
  other `.desktop`-aware launcher or application menu:
  - `worktime-start.desktop` — WorkTime Start
  - `worktime-stop.desktop` — WorkTime Stop
  - `worktime-dashboard.desktop` — WorkTime Dashboard
  - `worktime-report-week.desktop` — WorkTime Weekly Report
  - `worktime-report-month.desktop` — WorkTime Monthly Report
- a `worktime` symlink in `~/.local/bin`, pointing at `bin/worktime`, for
  terminal use.
- printed i3 keybinding lines (see below) — the script never edits your
  i3 config automatically. Paste the printed lines in yourself.

## Install / uninstall

```sh
platform/linux/install.sh              # install (safe to run again)
platform/linux/install.sh --uninstall  # remove the .desktop entries and the symlink
platform/linux/install.sh --help
```

The install is idempotent: running it twice produces the same result. It
never overwrites a `.desktop` file or symlink it did not create itself —
if something with the same name already exists and wasn't made by this
script, it is skipped with a message instead.

If `~/.local/bin` is not already on your `PATH`, the script prints the
line to add to `~/.profile` (or `~/.bashrc`).

## i3 shortcuts

| Shortcut         | Action                            |
|-------------------|------------------------------------|
| `$mod+Shift+t`    | start / show running time         |
| `$mod+Shift+x`    | stop                               |
| `$mod+Shift+d`    | open the dashboard                 |
| `$mod+Shift+w`    | weekly report (this week so far)   |

There is no `$mod+Shift+r` shortcut here on purpose: in i3's default
config `$mod+Shift+r` restarts i3, so `$mod+Shift+w` is used instead.

Paste the lines printed by `install.sh` into `~/.config/i3/config` (or
`~/.i3/config`), then reload i3 (`$mod+Shift+c`). A template with the
`@WORKTIME@` placeholder is in `platform/linux/i3-bindings.conf`.

There is no shortcut for the monthly report — use rofi (see below) with
"WorkTime Monthly Report", or run `worktime report month` from the
terminal.

## rofi

Open rofi's app launcher (`rofi -show drun`, often bound to `$mod+d`) and
type "worktime" — all 5 entries show up (e.g. "WorkTime Dashboard",
"WorkTime Monthly Report").

## Debian checklist

Run through this after installing on a Debian machine:

1. `worktime --version` — prints the version (confirms `bin/worktime`
   found Python >= 3.11).
2. `worktime config` — shows `~/.local/share/worktime/worktime.csv` as
   the data file.
3. Paste the printed i3 lines into your i3 config, then reload i3
   (`$mod+Shift+c`).
4. `$mod+Shift+t` — a "Started at …" notification appears.
5. `$mod+Shift+t` again — a "Currently running: 0m …" notification
   appears (nothing new is started).
6. `$mod+Shift+d` — the dashboard opens in the browser and shows
   "Running".
7. `$mod+Shift+w` — the weekly report opens in the browser.
8. `$mod+Shift+x` — a "Stopped: …" notification appears.
9. rofi (`rofi -show drun`, type "worktime") — each of the 5 entries
   works.
10. `python3 -m unittest discover -s tests` passes (the macOS-only
    installer tests are skipped on Linux).

## Troubleshooting

- **No notification appears:** is a notification daemon running (e.g.
  `dunst`)? Try `notify-send test hello` directly to check.
- **The browser doesn't open:** check the default browser with
  `xdg-settings get default-web-browser`.
- **Logs:** next to the CSV data file (see `worktime config` for the
  path): `error.log` (unexpected errors), `report.log` (background
  report catch-up), `server.log` (dashboard server).
- **After moving the repository:** the `.desktop` files and the symlink
  hard-code the absolute path to `bin/worktime` at install time —
  re-run `platform/linux/install.sh`.
- **After `git pull`:** run `worktime stop-server` once, so the
  dashboard server picks up the new code on its next start.
