# WorkTime Logger — Linux (Debian, i3, rofi, polybar) launcher layer

Thin launcher layer on top of `bin/worktime`: rofi-searchable `.desktop`
entries, an i3 keybinding snippet and a polybar status-bar module. Nothing
here changes `worktime`'s behaviour, it only adds ways to trigger it and
see it.

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
- printed polybar module lines and a hint for adding `worktime` to
  `modules-right` (or wherever you put it) — the script never edits your
  polybar config automatically either.

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
| `$mod+shift+t`    | start / show running time         |
| `$mod+shift+u`    | stop                               |
| `$mod+shift+d`    | open the dashboard                 |
| `$mod+shift+w`    | weekly report (this week so far)   |

Stop uses `u`, not `x`: on many setups (including Basil's)
`$mod+shift+x` is already bound to the lock screen
(e.g. `betterlockscreen`), so `x` is left alone.

There is no `$mod+shift+r` shortcut here on purpose: in i3's default
config `$mod+shift+r` restarts i3, so `$mod+shift+w` is used instead.

Paste the lines printed by `install.sh` into `~/.config/i3/config` (or
`~/.i3/config`), then reload i3 (`$mod+shift+c`). A template with the
`@WORKTIME@` placeholder is in `platform/linux/i3-bindings.conf`. i3
keybinding modifiers are case-insensitive, and so is the installer's
conflict check.

There is no shortcut for the monthly report — use rofi (see below) with
"WorkTime Monthly Report", or run `worktime report month` from the
terminal.

## rofi

The installer looks at your i3 config for the `bindsym` line that opens
rofi's `drun` mode (or an `app-launcher` script, like Basil's own
`~/.config/rofi/app-launcher/launch.sh`) and prints the key combo it
finds (e.g. `$mod+space`), falling back to a generic `rofi -show drun`
hint if it can't find one. Open your app launcher and type "worktime" —
all 5 entries show up (e.g. "WorkTime Dashboard", "WorkTime Monthly
Report"), each with a WorkTime clock icon
(`platform/linux/icons/worktime.svg`).

The installer only reads your rofi/i3 setup to build this hint — it
never binds or changes the app-launcher key itself.

## Status bar (polybar)

`platform/linux/polybar-module.ini` is a `[module/worktime]` template
(`type = custom/script`) that runs `worktime status --short` every 15
seconds:

- while a session is running, it shows the running time (e.g. `1h 24m`);
- while nothing is running, `status --short` prints nothing, so the
  module's label is empty and it effectively disappears from the bar;
- if the CSV or config is broken, it shows `err` — run `worktime status`
  in a terminal to see the actual problem.
- clicking the module (`click-left`) opens the dashboard, same as
  `$mod+shift+d`.

`install.sh` prints the module's lines (with `@WORKTIME@` filled in) for
you to paste into `~/.config/polybar/config.ini`, under a section
heading. It also looks at that file:

- if `[module/worktime]` is already there, it says so instead of
  printing anything to add;
- otherwise, if a `modules-right` line exists and doesn't already list
  `worktime`, it prints a ready-to-paste version of that line with
  `worktime` inserted (right before `time` if that module is present,
  otherwise at the end);
- if there's no `modules-right` line (or no polybar config at all), it
  prints a generic hint to add `worktime` to one of your
  `modules-left`/`modules-center`/`modules-right` lines.

As with i3, `install.sh` never edits your polybar config automatically —
you paste it in and restart polybar with `~/.config/polybar/launch.sh` (or
restart i3 with `$mod+shift+r`); a plain i3 reload does not re-run polybar's
`exec_always` launcher.

## Debian checklist

Run through this after installing on a Debian machine:

1. `worktime --version` — prints the version (confirms `bin/worktime`
   found Python >= 3.11).
2. `worktime config` — shows `~/.local/share/worktime/worktime.csv` as
   the data file.
3. Paste the printed i3 lines into your i3 config, then reload i3
   (`$mod+shift+c`).
4. `$mod+shift+t` — a "Started at …" notification appears.
5. `$mod+shift+t` again — a "Currently running: 0m …" notification
   appears (nothing new is started).
6. `$mod+shift+d` — the dashboard opens in the browser and shows
   "Running".
7. `$mod+shift+w` — the weekly report opens in the browser.
8. `$mod+shift+u` — a "Stopped: …" notification appears.
9. rofi (open your app launcher, type "worktime") — each of the 5
   entries works, and shows a WorkTime clock icon.
10. Paste the printed polybar module lines into
    `~/.config/polybar/config.ini` (and add `worktime` to
    `modules-right` as hinted), then restart polybar with
    `~/.config/polybar/launch.sh` (or restart i3 with `$mod+shift+r`); a
    plain `$mod+shift+c` reload does not re-run polybar's `exec_always`
    launcher. `$mod+shift+t` shows the running time in the bar within 15
    seconds; `$mod+shift+u` makes it disappear again within 15 seconds.
    Clicking the module opens the dashboard.
11. `python3 -m unittest discover -s tests` passes (the macOS-only
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
