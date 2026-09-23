"""Localhost web server for the WorkTime Logger dashboard.

``run(host, port, pid_file=None)`` starts a threaded ``http.server`` that
serves the JSON API under ``/api/*`` and static files (the dashboard) from
the ``web/`` directory next to the repository root. It is meant to be
started by ``worktime serve`` / ``worktime dashboard`` and left running in
the background; the CLI (task 4b) is responsible for auto-starting it and
opening a browser.

Security measures:

- The server only ever binds to ``127.0.0.1`` (or another address the
  caller explicitly passes in, but the CLI always uses ``127.0.0.1``); it
  is never meant to be reachable from the network.
- Every request's ``Host`` header is checked against ``127.0.0.1:<port>``
  and ``localhost:<port>`` *before* any routing happens, which defeats DNS
  rebinding attacks (a malicious page tricking the browser into sending
  requests with a different ``Host`` to this server).
- Static files are only ever served from inside ``web/`` (resolved to an
  absolute path and checked with ``Path.is_relative_to``), so ``..`` or
  percent-encoded traversal attempts can't escape that directory.
- Only ``GET`` is handled; every other HTTP method falls back to
  ``BaseHTTPRequestHandler``'s default (405/501) behaviour.
- Every response carries ``Cache-Control: no-store`` and
  ``X-Content-Type-Options: nosniff``.

The config file and the CSV data file are both re-read on *every* request
to ``/api/dashboard`` (there is no caching), so the dashboard always
reflects the latest state on disk, including edits made by hand or by a
concurrently running ``worktime start``/``stop``.

JSON layout of ``/api/dashboard`` (see :func:`build_dashboard`): a
top-level object with ``version``, ``generated`` (an ISO timestamp),
``status`` (whether a session is currently running), ``settings`` (the
effective daily target and workdays), ``overview`` (today/week/month/total
summaries), ``range``/``range_summary`` (the requested or default range and
its summary), ``days`` (per-day worked vs. target seconds for the range,
for the bar chart), ``trend`` (weekly or monthly buckets, for the trend
chart) and ``entries`` (the most recent sessions, independent of the
range).
"""

from __future__ import annotations

import json
import mimetypes
import os
import re
import signal
import sys
import threading
import traceback
from datetime import date, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlsplit

from worktime import __version__, stats
from worktime.config import ConfigError, load_config
from worktime.store import Entry, StoreError, read_entries, running_entry

WEB_ROOT = Path(__file__).resolve().parents[1] / "web"

_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_VALID_RANGES = ("7d", "30d", "90d", "year", "all")
_VALID_UNITS = ("week", "month")


class ApiError(Exception):
    """A user-facing API error: HTTP ``status`` plus a JSON ``message``."""

    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.message = message


def pid_path(cfg) -> Path:
    return cfg.data_file.parent / "server.pid"


def log_path(cfg) -> Path:
    return cfg.data_file.parent / "server.log"


def _s(td) -> int:
    return int(td.total_seconds())


def _parse_date_param(params: dict[str, str], key: str) -> date:
    v = params[key]
    if not _DATE_RE.match(v):
        raise ApiError(400, f"invalid date '{v}'")
    try:
        return date.fromisoformat(v)
    except ValueError:
        raise ApiError(400, f"invalid date '{v}'") from None


def _resolve_range_params(entries, now: datetime, params: dict[str, str]) -> tuple[str, date, date]:
    has_start = "start" in params
    has_end = "end" in params

    if has_start or has_end:
        if has_start != has_end:
            raise ApiError(400, "start and end must be given together")
        start = _parse_date_param(params, "start")
        end = _parse_date_param(params, "end")
        if start > end:
            raise ApiError(400, "start is after end")
        return "custom", start, end

    name = params.get("range", "30d")
    if name not in _VALID_RANGES:
        raise ApiError(400, f"unknown range '{name}'")
    start, end = stats.resolve_range(name, now, entries)
    return name, start, end


def _parse_limit(params: dict[str, str]) -> int:
    v = params.get("limit", "50")
    if not re.match(r"^-?\d+$", v):
        raise ApiError(400, f"invalid limit '{v}'")
    n = int(v)
    if not (1 <= n <= 500):
        raise ApiError(400, f"invalid limit '{v}'")
    return n


def _summary_json(summary) -> dict:
    longest = summary.longest
    if longest is None:
        longest_json = None
    else:
        longest_json = {
            "date": longest.date.isoformat(),
            "start": longest.start.strftime("%H:%M:%S"),
            "end": longest.end.strftime("%H:%M:%S"),
            "duration_s": _s(longest.duration),
        }

    return {
        "start": summary.start.isoformat(),
        "end": summary.end.isoformat(),
        "worked_s": _s(summary.worked),
        "target_s": _s(summary.target),
        "balance_s": _s(summary.balance),
        "sessions": summary.sessions,
        "days_worked": summary.days_worked,
        "target_days": summary.target_days,
        "avg_per_day_worked_s": _s(summary.avg_per_day_worked),
        "avg_per_target_day_s": _s(summary.avg_per_target_day),
        "longest": longest_json,
    }


def _entry_json(e: Entry, now: datetime) -> dict:
    return {
        "date": e.date.isoformat(),
        "start": e.start.strftime("%H:%M:%S"),
        "end": e.end.strftime("%H:%M:%S") if e.end is not None else None,
        "duration_s": _s(e.elapsed(now)),
        "running": e.running,
    }


def build_dashboard(entries, now: datetime, cfg, params: dict[str, str]) -> dict:
    range_name, range_start, range_end = _resolve_range_params(entries, now, params)

    unit = params.get("unit", "week")
    if unit not in _VALID_UNITS:
        raise ApiError(400, f"unknown unit '{unit}'")

    limit = _parse_limit(params)

    target = stats.Target.from_config(cfg)

    run = running_entry(entries)
    if run is None:
        status = {"running": False, "since": None, "elapsed_s": 0}
    else:
        status = {
            "running": True,
            "since": run.start.isoformat(),
            "elapsed_s": _s(run.elapsed(now)),
        }

    overview = stats.overview(entries, now, target)
    range_summary = stats.summarize(entries, now, range_start, range_end, target)

    totals = stats.daily_totals(entries, now, range_start, range_end)
    targets = stats.daily_targets(entries, now, range_start, range_end, target)
    days = [
        {"date": d.isoformat(), "worked_s": _s(totals[d]), "target_s": _s(targets[d])}
        for d in sorted(totals)
    ]

    trend_buckets = stats.buckets(entries, now, range_start, range_end, target, unit)
    trend = [
        {
            "label": b.label,
            "start": b.start.isoformat(),
            "end": b.end.isoformat(),
            "worked_s": _s(b.worked),
            "target_s": _s(b.target),
            "balance_s": _s(b.balance),
            "cumulative_balance_s": _s(b.cumulative_balance),
        }
        for b in trend_buckets
    ]

    sorted_entries = sorted(entries, key=lambda e: e.start, reverse=True)[:limit]

    return {
        "version": __version__,
        "generated": now.isoformat(),
        "status": status,
        "settings": {
            "daily_target_s": cfg.daily_target_min * 60,
            "workdays": sorted(cfg.workdays),
        },
        "overview": {k: _summary_json(v) for k, v in overview.items()},
        "range": {
            "name": range_name,
            "start": range_start.isoformat(),
            "end": range_end.isoformat(),
            "unit": unit,
        },
        "range_summary": _summary_json(range_summary),
        "days": days,
        "trend": trend,
        "entries": [_entry_json(e, now) for e in sorted_entries],
    }


class _Handler(BaseHTTPRequestHandler):
    server_version = f"WorkTime/{__version__}"
    sys_version = ""

    def log_message(self, format, *args):  # noqa: A002 - stdlib signature
        pass

    def log_error(self, format, *args):  # noqa: A002 - stdlib signature
        msg = format % args
        sys.stderr.write(f"[{datetime.now():%Y-%m-%d %H:%M:%S}] {msg}\n")

    def _send_json(self, status: int, obj) -> None:
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(body)

    def _send_static(self, candidate: Path) -> None:
        data = candidate.read_bytes()
        content_type, _ = mimetypes.guess_type(str(candidate))
        if content_type is None:
            content_type = "application/octet-stream"
        if (
            content_type.startswith("text/")
            or content_type in ("application/javascript", "application/json")
        ):
            content_type += "; charset=utf-8"
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(data)

    def _check_host(self) -> bool:
        port = self.server.server_address[1]
        allowed = {f"127.0.0.1:{port}", f"localhost:{port}"}
        if self.headers.get("Host", "") not in allowed:
            self._send_json(403, {"error": "forbidden host"})
            return False
        return True

    def do_GET(self) -> None:  # noqa: N802 - stdlib method name
        if not self._check_host():
            return

        parts = urlsplit(self.path)
        path = parts.path

        if path == "/api/health":
            self._send_json(200, {"app": "worktime", "version": __version__})
            return

        if path == "/api/dashboard":
            try:
                params = {k: v[0] for k, v in parse_qs(parts.query).items()}
                cfg = load_config()
                entries = read_entries(cfg.data_file)
                now = datetime.now().replace(microsecond=0)
                result = build_dashboard(entries, now, cfg, params)
                self._send_json(200, result)
            except ApiError as e:
                self._send_json(e.status, {"error": e.message})
            except (ConfigError, StoreError) as e:
                self._send_json(500, {"error": str(e)})
            except Exception as e:
                traceback.print_exc()
                self._send_json(500, {"error": f"internal error: {e}"})
            return

        if path.startswith("/api/"):
            self._send_json(404, {"error": "not found"})
            return

        self._serve_static(path)

    def _serve_static(self, path: str) -> None:
        web_root: Path = self.server.web_root
        rel = unquote(path)
        if rel == "/":
            rel = "/index.html"
        if "\x00" in rel:
            self._send_json(404, {"error": "not found"})
            return

        candidate = (web_root / rel.lstrip("/")).resolve()
        if not (candidate.is_relative_to(web_root) and candidate.is_file()):
            self._send_json(404, {"error": "not found"})
            return

        self._send_static(candidate)


def make_server(host: str = "127.0.0.1", port: int = 8765, web_root: Path = WEB_ROOT) -> ThreadingHTTPServer:
    srv = ThreadingHTTPServer((host, port), _Handler)
    srv.daemon_threads = True
    srv.web_root = web_root.resolve()
    return srv


def run(host: str, port: int, pid_file: Path | None = None) -> None:
    srv = make_server(host, port)

    if pid_file is not None:
        pid_file.parent.mkdir(parents=True, exist_ok=True)
        pid_file.write_text(f"{os.getpid()}\n")

    if threading.current_thread() is threading.main_thread():
        def _handle_sigterm(signum, frame):
            raise SystemExit(0)

        signal.signal(signal.SIGTERM, _handle_sigterm)

    print(f"Serving WorkTime dashboard on http://{host}:{srv.server_address[1]}/", flush=True)

    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        srv.server_close()
        if pid_file is not None and pid_file.exists():
            try:
                if pid_file.read_text().strip() == str(os.getpid()):
                    pid_file.unlink()
            except OSError:
                pass
