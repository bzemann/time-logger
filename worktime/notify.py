"""Desktop notifications for WorkTime Logger.

This module is the ONLY OS-specific part of the core. Everything else in
``worktime`` (session, store, stats, ...) is plain, portable Python 3
standard library code; only this module shells out to a platform-specific
notifier binary.

Two backends are supported:

- **macOS** (``sys.platform`` starting with ``"darwin"``): uses
  ``osascript`` to ask Notification Center to display a notification. The
  title and body are passed as ``argv`` items to a small fixed AppleScript
  snippet (``on run argv`` / ``display notification ... with title ...``)
  instead of being interpolated into the script source. This is what makes
  arbitrary titles/bodies (quotes, backslashes, apostrophes, ...) safe: they
  never touch AppleScript syntax, they are just opaque argument strings.
- **Linux** (``sys.platform`` starting with ``"linux"``): uses
  ``notify-send`` (typically backed by a notification daemon such as
  dunst). If ``notify-send`` is not found on ``PATH``, notifications are
  simply unavailable on that machine.

Any other platform, or any failure while running the backend command
(binary missing, non-zero exit code, timeout, or any other ``OSError``),
falls back to printing ``"{title}: {body}"`` to stderr. ``notify()`` never
raises for these failure modes -- a missing or broken notifier must never
crash the CLI.

Notifications can be disabled entirely (e.g. for tests or quiet hours) by
setting the environment variable ``WORKTIME_NO_NOTIFY`` to ``1``, ``true``
or ``yes`` (case-insensitive, surrounding whitespace ignored). When
disabled, ``notify()`` returns ``False`` immediately without running any
command or printing anything.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys

_TRUE_VALUES = {"1", "true", "yes"}


def notifications_disabled() -> bool:
    """Return True if the WORKTIME_NO_NOTIFY env var disables notifications."""
    value = os.environ.get("WORKTIME_NO_NOTIFY", "")
    return value.strip().lower() in _TRUE_VALUES


def build_command(platform: str, title: str, body: str) -> list[str] | None:
    """Build the notifier command for `platform`, or None if unsupported."""
    if platform.startswith("darwin"):
        osascript = shutil.which("osascript") or "/usr/bin/osascript"
        return [
            osascript,
            "-e",
            "on run argv",
            "-e",
            "display notification (item 2 of argv) with title (item 1 of argv)",
            "-e",
            "end run",
            title,
            body,
        ]
    if platform.startswith("linux"):
        exe = shutil.which("notify-send")
        if exe is None:
            return None
        return [exe, "-a", "WorkTime", "-t", "5000", title, body]
    return None


def notify(title: str, body: str, *, platform: str | None = None) -> bool:
    """Show a desktop notification, falling back to stderr on failure.

    Returns True if the platform notifier ran successfully (exit code 0),
    False otherwise (disabled, unsupported platform, missing binary, or the
    command failed/timed out). Never raises.
    """
    if notifications_disabled():
        return False

    platform = platform or sys.platform
    cmd = build_command(platform, title, body)
    if cmd is None:
        print(f"{title}: {body}", file=sys.stderr)
        return False

    try:
        result = subprocess.run(cmd, check=False, capture_output=True, timeout=5)
    except (OSError, subprocess.TimeoutExpired):
        print(f"{title}: {body}", file=sys.stderr)
        return False

    if result.returncode != 0:
        print(f"{title}: {body}", file=sys.stderr)
        return False

    return True
