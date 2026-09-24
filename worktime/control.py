"""Background management of the dashboard server.

``worktime dashboard`` needs to start ``worktime/server.py`` in the
background (unless it is already running), then open a browser tab, and
``worktime stop-server`` needs to stop it again. This module implements
that lifecycle:

- **Probing**: :func:`probe` asks the server's ``/api/health`` endpoint
  whether the configured port is free, already serving *our* server
  (identified by ``{"app": "worktime", ...}``), or occupied by some other
  program.
- **Detached start**: :func:`start_background` launches
  ``python -m worktime serve`` as a *fully detached* subprocess
  (``start_new_session=True``, stdin closed, stdout/stderr redirected to a
  log file) so that it keeps running after the shortcut process (or the
  ``worktime dashboard`` invocation itself) has exited. The server writes
  its own PID file (see ``server.pid_path``) once it is listening.
- **Stopping**: :func:`stop_background` reads the PID file and sends
  ``SIGTERM``, then waits for the health check to stop responding as
  "ours".

Stale server note: if the server's code is updated (e.g. after a `git
pull`) while an old instance is still running in the background, that old
instance keeps serving the previous code until it is restarted. Run
``worktime stop-server`` after updating the code; the next `worktime
dashboard` will start a fresh instance.
"""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

from worktime import __version__
from worktime.config import config_path

REPO_ROOT = Path(__file__).resolve().parents[1]


class ControlError(Exception):
    """Raised when the background server can't be started or stopped."""


def probe(port: int, timeout: float = 1.0) -> str:
    """Probe ``127.0.0.1:port`` and return "ours", "free" or "other".

    Never raises.
    """
    url = f"http://127.0.0.1:{port}/api/health"
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            raw = resp.read()
        data = json.loads(raw)
    except urllib.error.URLError as e:
        if isinstance(e.reason, ConnectionRefusedError):
            return "free"
        return "other"
    except ConnectionRefusedError:
        return "free"
    except Exception:
        return "other"

    if isinstance(data, dict) and data.get("app") == "worktime":
        return "ours"
    return "other"


def server_version(port: int, timeout: float = 1.0) -> str | None:
    """Return the ``version`` reported by our server on ``port``, or None.

    None covers everything that isn't a healthy response from *our* app
    with a string ``version`` field: a refused connection, a non-worktime
    app, bad JSON, a timeout, or an old server that predates the
    ``version`` field. Never raises.
    """
    url = f"http://127.0.0.1:{port}/api/health"
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            raw = resp.read()
        data = json.loads(raw)
    except Exception:
        return None

    if isinstance(data, dict) and data.get("app") == "worktime":
        version = data.get("version")
        if isinstance(version, str):
            return version
    return None


def ensure_current(cfg) -> str:
    """Make sure the dashboard server for ``cfg`` is running and current.

    Returns "started" (nothing was running), "restarted" (an old-code
    instance was replaced) or "running" (already up to date). Raises
    ControlError if the port is used by another program, or if stopping
    or starting fails.
    """
    state = probe(cfg.port)

    if state == "other":
        raise ControlError(
            f"Port {cfg.port} is used by another program. "
            f"Set a different 'port' in {config_path()}."
        )

    if state == "free":
        start_background(cfg)
        return "started"

    version = server_version(cfg.port)
    if version == __version__:
        return "running"

    # After a `git pull`, a server started with older code keeps serving
    # it until restarted. This check replaces the manual `worktime
    # stop-server` step that used to be required after an update.
    stop_background(cfg)
    start_background(cfg)
    return "restarted"


def start_background(cfg, wait: float = 5.0) -> None:
    """Start the dashboard server in the background and wait until it answers.

    Raises ControlError if it doesn't come up within `wait` seconds.
    """
    from worktime import server

    log = server.log_path(cfg)
    log.parent.mkdir(parents=True, exist_ok=True)

    env = os.environ.copy()
    old_pythonpath = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = str(REPO_ROOT) + (os.pathsep + old_pythonpath if old_pythonpath else "")

    with open(log, "ab") as logf:
        subprocess.Popen(
            [sys.executable, "-m", "worktime", "serve"],
            stdin=subprocess.DEVNULL,
            stdout=logf,
            stderr=logf,
            start_new_session=True,
            env=env,
            cwd=str(REPO_ROOT),
            close_fds=True,
        )

    deadline = time.monotonic() + wait
    while time.monotonic() < deadline:
        if probe(cfg.port, timeout=0.5) == "ours":
            return
        time.sleep(0.1)

    raise ControlError(f"Dashboard server did not start within {wait:g}s. See {log}")


def stop_background(cfg, wait: float = 3.0) -> str:
    """Stop the background dashboard server. Returns a human message."""
    from worktime import server

    pidf = server.pid_path(cfg)
    state = probe(cfg.port)

    if not pidf.exists():
        if state == "ours":
            raise ControlError(
                f"Server is running on port {cfg.port} but {pidf} is missing; "
                f"stop it manually."
            )
        return "Server is not running."

    try:
        pid = int(pidf.read_text().strip())
    except ValueError:
        pidf.unlink(missing_ok=True)
        return "Server is not running (removed invalid PID file)."

    if state != "ours":
        pidf.unlink(missing_ok=True)
        return "Server is not running (removed stale PID file)."

    try:
        os.kill(pid, signal.SIGTERM)
    except ProcessLookupError:
        pidf.unlink(missing_ok=True)
        return "Server is not running (removed stale PID file)."

    deadline = time.monotonic() + wait
    while time.monotonic() < deadline:
        if probe(cfg.port) != "ours":
            return "Server stopped."
        time.sleep(0.1)

    raise ControlError(f"Server (PID {pid}) did not stop within {wait:g}s.")
