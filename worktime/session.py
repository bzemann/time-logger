"""Start/stop/status logic for WorkTime Logger.

This module contains the pure business logic for starting, stopping and
inspecting the current work session. It never prints anything and never
sends a desktop notification — that is the CLI's job (``worktime/cli.py``),
which turns the results and :class:`SessionError` messages returned/raised
here into printed output and notifications.

For testability, the current time is never read implicitly (e.g. via
``datetime.now()``) inside the logic functions below; it is always passed
in explicitly as the ``now`` parameter. Only :func:`current_time` (used by
the CLI to obtain ``now`` once per invocation) touches the real clock.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, time as dtime, timedelta
from pathlib import Path
from typing import Sequence

from worktime import store
from worktime.store import Entry

_AT_RE = re.compile(r"^(\d{1,2}):(\d{2})$")


class SessionError(Exception):
    """User-facing error (message is shown verbatim in a notification)."""


@dataclass(frozen=True)
class StartResult:
    started: bool
    entry: Entry
    elapsed: timedelta
    today: timedelta


@dataclass(frozen=True)
class StopResult:
    stopped: bool
    entry: Entry | None
    today: timedelta


@dataclass(frozen=True)
class StatusResult:
    entry: Entry | None
    elapsed: timedelta
    today: timedelta


def current_time() -> datetime:
    """Return the current local time, truncated to whole seconds."""
    return datetime.now().replace(microsecond=0)


def fmt_time(dt: datetime, now: datetime) -> str:
    """Format ``dt`` as ``HH:MM`` if it is on ``now``'s date, else ``"Mon HH:MM"``."""
    if dt.date() == now.date():
        return f"{dt:%H:%M}"
    return f"{dt:%a %H:%M}"


def format_duration(td: timedelta) -> str:
    total_min = max(0, int(td.total_seconds()) // 60)
    h, m = divmod(total_min, 60)
    if h > 0:
        return f"{h}h {m:02d}m"
    return f"{m}m"


def today_total(entries: Sequence[Entry], now: datetime) -> timedelta:
    total = timedelta(0)
    for e in entries:
        if e.date == now.date():
            total += e.elapsed(now)
    return total


def resolve_at(hhmm: str, now: datetime) -> datetime:
    """Resolve ``hhmm`` (``HH:MM``) to the most recent non-future occurrence."""
    m = _AT_RE.match(hhmm)
    if m:
        h, mnt = int(m.group(1)), int(m.group(2))
        if 0 <= h <= 23 and 0 <= mnt <= 59:
            candidate = datetime.combine(now.date(), dtime(h, mnt))
            if candidate > now:
                candidate -= timedelta(days=1)
            return candidate
    raise SessionError(f"invalid time '{hhmm}', expected HH:MM")


def start(path: Path, now: datetime, at: str | None = None) -> StartResult:
    outcome: dict = {}

    def fn(entries: list[Entry]) -> list[Entry]:
        running = store.running_entry(entries)
        if running is not None:
            if at is not None:
                raise SessionError(
                    f"A session is already running since "
                    f"{fmt_time(running.start, now)}"
                )
            outcome["started"] = False
            outcome["entry"] = running
            return entries

        start_dt = resolve_at(at, now) if at else now
        if entries and entries[-1].end > start_dt:
            raise SessionError(
                f"Start {start_dt:%H:%M} overlaps the previous session "
                f"(ended {fmt_time(entries[-1].end, now)})"
            )
        new_entry = Entry(start_dt)
        outcome["started"] = True
        outcome["entry"] = new_entry
        return entries + [new_entry]

    new_entries = store.update_entries(path, fn)
    entry = outcome["entry"]
    elapsed = entry.elapsed(now)
    today = today_total(new_entries, now)
    return StartResult(
        started=outcome["started"], entry=entry, elapsed=elapsed, today=today
    )


def stop(path: Path, now: datetime, at: str | None = None) -> StopResult:
    outcome: dict = {}

    def fn(entries: list[Entry]) -> list[Entry]:
        running = store.running_entry(entries)
        if running is None:
            outcome["stopped"] = False
            outcome["entry"] = None
            return entries

        if at:
            end = resolve_at(at, now)
            if end < running.start:
                raise SessionError(
                    f"--at {at} is before the session start "
                    f"({fmt_time(running.start, now)})"
                )
        else:
            end = now
            if end < running.start:
                raise SessionError(
                    f"System clock ({fmt_time(now, now)}) is before the "
                    f"session start ({fmt_time(running.start, now)})"
                )

        if end - running.start >= store.MAX_DURATION:
            if not at:
                raise SessionError(
                    f"Running since {running.start:%a %H:%M} "
                    f"({format_duration(now - running.start)}). "
                    f"Stop it with: worktime stop --at HH:MM"
                )
            raise SessionError(
                f"--at {at} gives a session of "
                f"{format_duration(end - running.start)}; sessions must be "
                f"shorter than 24h. Fix the last row of {path} manually."
            )

        closed = Entry(running.start, end)
        outcome["stopped"] = True
        outcome["entry"] = closed
        return entries[:-1] + [closed]

    new_entries = store.update_entries(path, fn)
    today = today_total(new_entries, now)
    return StopResult(stopped=outcome["stopped"], entry=outcome["entry"], today=today)


def status(path: Path, now: datetime) -> StatusResult:
    entries = store.read_entries(path)
    entry = store.running_entry(entries)
    elapsed = entry.elapsed(now) if entry else timedelta(0)
    today = today_total(entries, now)
    return StatusResult(entry=entry, elapsed=elapsed, today=today)
