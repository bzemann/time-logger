"""Command-line interface for WorkTime Logger."""

from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
from datetime import date

from worktime import __version__, browser, control, session
from worktime.config import ConfigError, load_config, config_path
from worktime.control import ControlError
from worktime.notify import notify
from worktime.session import SessionError
from worktime.store import StoreError

_WEEKDAY_NAMES = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")
_TITLE = "WorkTime"
_TRUE_VALUES = {"1", "true", "yes"}
_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _format_hmm(minutes: int) -> str:
    return f"{minutes // 60}:{minutes % 60:02d}"


def _format_days_off(days_off) -> str:
    """Format days_off frozenset for display."""
    if not days_off:
        return "(none)"

    sorted_dates = sorted(days_off)
    n = len(sorted_dates)

    if n == 1:
        return f"1 day ({sorted_dates[0]})"
    else:
        first = sorted_dates[0]
        last = sorted_dates[-1]
        return f"{n} days ({first} … {last})"


def _cmd_config(args: argparse.Namespace) -> int:
    path = config_path()
    found = "found" if path.exists() else "not found, using defaults"
    cfg = load_config(path)

    workdays = ", ".join(_WEEKDAY_NAMES[d] for d in sorted(cfg.workdays))
    if not workdays:
        workdays = "(none)"

    print(f"config file:  {path} ({found})")
    print(f"data file:    {cfg.data_file}")
    print(f"reports dir:  {cfg.reports_dir}")
    print(f"daily target: {_format_hmm(cfg.daily_target_min)}")
    print(f"workdays:     {workdays}")
    print(f"days off:     {_format_days_off(cfg.days_off)}")
    print(f"port:         {cfg.port}")
    return 0


def _parse_date(value: str) -> date:
    """Parse a strict YYYY-MM-DD date for argparse's ``--date``."""
    if not _DATE_RE.match(value):
        raise argparse.ArgumentTypeError(
            f"invalid date: {value!r} (expected YYYY-MM-DD)"
        )
    try:
        return date.fromisoformat(value)
    except ValueError as e:
        raise argparse.ArgumentTypeError(
            f"invalid date: {value!r} (expected YYYY-MM-DD)"
        ) from e


def _auto_reports_disabled() -> bool:
    """Return True if the WORKTIME_NO_AUTO_REPORTS env var disables auto catch-up."""
    value = os.environ.get("WORKTIME_NO_AUTO_REPORTS", "")
    return value.strip().lower() in _TRUE_VALUES


def _maybe_start_catch_up(cfg, now) -> None:
    """Spawn a detached `worktime report catch-up` if any report is due.

    Reports for the last complete week/month should appear at the first
    `start`/`dashboard` invocation after the period ends. This runs in the
    background (fire-and-forget, like `control.start_background` for the
    server) so the shortcut that triggered it stays instant. Never raises:
    any failure here must not break `start` or `dashboard`.
    """
    if _auto_reports_disabled():
        return

    try:
        from worktime import report

        due = report.due_reports(cfg, now)
    except Exception:
        return

    if not due:
        return

    log_path = cfg.data_file.parent / "report.log"
    env = os.environ.copy()
    old_pythonpath = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = str(control.REPO_ROOT) + (
        os.pathsep + old_pythonpath if old_pythonpath else ""
    )

    try:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        with open(log_path, "ab") as logf:
            subprocess.Popen(
                [sys.executable, "-m", "worktime", "report", "catch-up"],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=logf,
                start_new_session=True,
                env=env,
                cwd=str(control.REPO_ROOT),
                close_fds=True,
            )
    except OSError:
        return


def _cmd_start(args: argparse.Namespace) -> int:
    cfg = load_config()
    now = session.current_time()
    result = session.start(cfg.data_file, now, at=args.at)

    if result.started:
        body = f"Started at {session.fmt_time(result.entry.start, now)}"
    else:
        body = (
            f"Currently running: {session.format_duration(result.elapsed)}\n"
            f"Since {session.fmt_time(result.entry.start, now)} · "
            f"Today: {session.format_duration(result.today)}"
        )
    notify(_TITLE, body)
    print(body)
    _maybe_start_catch_up(cfg, now)
    return 0


def _cmd_stop(args: argparse.Namespace) -> int:
    cfg = load_config()
    now = session.current_time()
    result = session.stop(cfg.data_file, now, at=args.at)

    if result.stopped:
        body = (
            f"Stopped: {session.format_duration(result.entry.duration)}\n"
            f"Today: {session.format_duration(result.today)}"
        )
        notify(_TITLE, body)
        print(body)
        return 0

    body = f"No session running\nToday: {session.format_duration(result.today)}"
    notify(_TITLE, body)
    print(body)
    return 1


def _cmd_status(args: argparse.Namespace) -> int:
    cfg = load_config()
    now = session.current_time()
    result = session.status(cfg.data_file, now)
    target = _format_hmm(cfg.daily_target_min)

    if result.entry is not None:
        print(
            f"Running since {session.fmt_time(result.entry.start, now)} "
            f"({session.format_duration(result.elapsed)}). "
            f"Today: {session.format_duration(result.today)}. Target: {target}."
        )
    else:
        print(
            f"Not running. Today: {session.format_duration(result.today)}. "
            f"Target: {target}."
        )
    return 0


def _cmd_serve(args: argparse.Namespace) -> int:
    from worktime import server

    cfg = load_config()
    port = args.port or cfg.port
    try:
        server.run("127.0.0.1", port, server.pid_path(cfg))
    except OSError as e:
        raise ControlError(f"cannot listen on 127.0.0.1:{port}: {e}") from e
    return 0


def _cmd_dashboard(args: argparse.Namespace) -> int:
    cfg = load_config()
    url = f"http://127.0.0.1:{cfg.port}/"
    state = control.probe(cfg.port)

    if state == "other":
        raise ControlError(
            f"Port {cfg.port} is used by another program. "
            f"Set a different 'port' in {config_path()}."
        )
    if state == "free":
        control.start_background(cfg)

    opened = browser.open_url(url)
    print(url if opened else f"Open {url} in your browser")
    now = session.current_time()
    _maybe_start_catch_up(cfg, now)
    return 0


def _cmd_stop_server(args: argparse.Namespace) -> int:
    cfg = load_config()
    print(control.stop_background(cfg))
    return 0


def _cmd_report(args: argparse.Namespace) -> int:
    from worktime import report

    cfg = load_config()
    now = session.current_time()

    if args.kind == "catch-up":
        if args.last or args.date or args.no_open:
            raise ControlError("report catch-up takes no options")
        due = report.due_reports(cfg, now)
        if not due:
            print("No reports due.")
            return 0
        for period in due:
            path = report.write_report(cfg, period, now)
            kind_label = "Weekly" if period.kind == "week" else "Monthly"
            notify(_TITLE, f"{kind_label} report saved: {path.name}")
            print(path)
        return 0

    if args.date:
        period = report.period_for(args.kind, args.date)
    elif args.last:
        period = report.previous_period(args.kind, now.date())
    else:
        period = report.period_for(args.kind, now.date())

    path = report.write_report(cfg, period, now)
    kind_label = "Weekly" if period.kind == "week" else "Monthly"
    notify(_TITLE, f"{kind_label} report saved: {path.name}")
    print(path)
    if not args.no_open:
        browser.open_url(path.as_uri())
    return 0


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="worktime",
        description="WorkTime Logger — shortcut-driven work time tracking.",
    )
    parser.add_argument(
        "--version", action="version", version=f"worktime {__version__}"
    )

    subparsers = parser.add_subparsers(dest="command")

    config_parser = subparsers.add_parser(
        "config", help="Show the effective configuration"
    )
    config_parser.set_defaults(func=_cmd_config, notify_errors=False)

    start_parser = subparsers.add_parser(
        "start", help="Start a session (or show the running one)"
    )
    start_parser.add_argument("--at", help="Start time as HH:MM")
    start_parser.set_defaults(func=_cmd_start, notify_errors=True)

    stop_parser = subparsers.add_parser("stop", help="Stop the running session")
    stop_parser.add_argument("--at", help="Stop time as HH:MM")
    stop_parser.set_defaults(func=_cmd_stop, notify_errors=True)

    status_parser = subparsers.add_parser("status", help="Print the current state")
    status_parser.set_defaults(func=_cmd_status, notify_errors=False)

    serve_parser = subparsers.add_parser(
        "serve", help="Run the dashboard server in the foreground"
    )
    serve_parser.add_argument("--port", type=int, help="Port to listen on")
    serve_parser.set_defaults(func=_cmd_serve, notify_errors=False)

    dashboard_parser = subparsers.add_parser(
        "dashboard", help="Open the dashboard (starts the server if needed)"
    )
    dashboard_parser.set_defaults(func=_cmd_dashboard, notify_errors=True)

    stop_server_parser = subparsers.add_parser(
        "stop-server", help="Stop the background dashboard server"
    )
    stop_server_parser.set_defaults(func=_cmd_stop_server, notify_errors=False)

    report_parser = subparsers.add_parser(
        "report", help="Generate a weekly or monthly HTML report"
    )
    report_parser.add_argument("kind", choices=["week", "month", "catch-up"])
    report_group = report_parser.add_mutually_exclusive_group()
    report_group.add_argument(
        "--last", action="store_true", help="Previous complete period"
    )
    report_group.add_argument(
        "--date", type=_parse_date, help="Period containing this date"
    )
    report_parser.add_argument(
        "--no-open", action="store_true", help="Do not open the report in the browser"
    )
    report_parser.set_defaults(func=_cmd_report, notify_errors=True)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    if not getattr(args, "command", None):
        parser.print_help(sys.stderr)
        return 2

    try:
        return args.func(args)
    except (ConfigError, StoreError, SessionError, ControlError) as e:
        print(f"worktime: {e}", file=sys.stderr)
        if getattr(args, "notify_errors", False):
            notify("WorkTime error", str(e))
        return 1
