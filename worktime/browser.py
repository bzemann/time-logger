"""Open a URL in the default web browser.

This is the second OS-specific module in WorkTime Logger, next to
``notify.py``. Everything else stays plain, portable Python 3 standard
library code; only this module shells out to a platform-specific "open a
URL" command.

- **macOS** (``sys.platform`` starting with ``"darwin"``): uses the ``open``
  command.
- **Linux** (``sys.platform`` starting with ``"linux"``): uses ``xdg-open``
  if it is found on ``PATH``.
- Any other platform, or a missing ``xdg-open`` on Linux, falls back to
  Python's ``webbrowser`` module.

If the platform-specific command fails (missing binary, non-zero exit
code, or timeout), ``open_url`` also falls back to ``webbrowser.open``.
``open_url`` never raises.

Opening the browser can be disabled entirely (e.g. for tests, or when
running headless) by setting the environment variable
``WORKTIME_NO_BROWSER`` to ``1``, ``true`` or ``yes`` (case-insensitive,
surrounding whitespace ignored). When disabled, ``open_url()`` returns
``False`` immediately without running any command or opening a browser.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import webbrowser

_TRUE_VALUES = {"1", "true", "yes"}


def browser_disabled() -> bool:
    """Return True if the WORKTIME_NO_BROWSER env var disables the browser."""
    value = os.environ.get("WORKTIME_NO_BROWSER", "")
    return value.strip().lower() in _TRUE_VALUES


def build_open_command(platform: str, url: str) -> list[str] | None:
    """Build the "open a URL" command for `platform`, or None if unsupported."""
    if platform.startswith("darwin"):
        exe = shutil.which("open") or "/usr/bin/open"
        return [exe, url]
    if platform.startswith("linux"):
        exe = shutil.which("xdg-open")
        if exe is None:
            return None
        return [exe, url]
    return None


def open_url(url: str, *, platform: str | None = None) -> bool:
    """Open `url` in the default web browser.

    Returns True on apparent success, False otherwise (disabled,
    unsupported platform, missing binary, command failed/timed out, or the
    ``webbrowser`` fallback also failed). Never raises.
    """
    if browser_disabled():
        return False

    platform = platform or sys.platform
    cmd = build_open_command(platform, url)
    if cmd is not None:
        try:
            result = subprocess.run(cmd, check=False, capture_output=True, timeout=10)
        except (OSError, subprocess.TimeoutExpired):
            result = None
        if result is not None and result.returncode == 0:
            return True

    try:
        return bool(webbrowser.open(url))
    except Exception:
        return False
