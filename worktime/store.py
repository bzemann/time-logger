"""CSV storage for WorkTime Logger.

File format (UTF-8, ``\\n`` line endings, header row, one row per session)::

    date,start,end,duration_min
    2026-09-22,08:12:05,12:30:40,259
    2026-09-22,23:30:00,00:45:00,75
    2026-09-23,15:01:00,,

- ``date`` (``YYYY-MM-DD``) and ``start``/``end`` (``HH:MM:SS``, 24h) are the
  date and local-time of the *start* of the session. ``end`` is empty for a
  currently running session, and so is ``duration_min``.
- Midnight rule: a session belongs to the calendar day it *started* on. If
  the end clock time is earlier than the start clock time, the end is on the
  next calendar day (e.g. ``23:30:00`` -> ``00:45:00`` is a 75 minute
  session). If ``end == start`` the session is zero-length on the same day.
- Timestamps are the source of truth; ``duration_min`` is only a courtesy
  column for humans/spreadsheets skimming the file. It is always recomputed
  from ``start``/``end`` on read (rounded half-up from exact seconds) and
  its stored value is never trusted, only checked for being a syntactically
  valid non-negative integer.
- At most one entry may be "running" (empty ``end``), and if present it must
  be the last row in the file.

Writes are atomic: a temp file is written in the same directory, fsynced,
chmod'd to 0600, then ``os.replace``'d over the target. Concurrent access is
serialized with an ``fcntl.flock`` on a sibling ``<file>.lock`` file (see
:func:`locked`), so two ``worktime`` commands never race on the CSV.
"""

from __future__ import annotations

import csv
import fcntl
import os
import re
import tempfile
import time
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import date, datetime, time as dtime, timedelta
from pathlib import Path
from typing import Callable, Iterable, Iterator, Sequence

HEADER = ["date", "start", "end", "duration_min"]
MAX_DURATION = timedelta(hours=24)

_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_TIME_RE = re.compile(r"^\d{2}:\d{2}:\d{2}$")
_DURATION_RE = re.compile(r"^\d+$")


class StoreError(Exception):
    """Raised for CSV format errors and locking failures."""


@dataclass(frozen=True)
class Entry:
    """One work session. ``end is None`` means the session is running."""

    start: datetime
    end: datetime | None = None

    def __post_init__(self) -> None:
        if self.start.tzinfo is not None:
            raise ValueError("start must be a naive (local) datetime")
        object.__setattr__(self, "start", self.start.replace(microsecond=0))
        if self.end is not None:
            if self.end.tzinfo is not None:
                raise ValueError("end must be a naive (local) datetime")
            end = self.end.replace(microsecond=0)
            if end < self.start:
                raise ValueError("end is before start")
            if end - self.start >= MAX_DURATION:
                raise ValueError("session duration must be less than 24 hours")
            object.__setattr__(self, "end", end)

    @property
    def date(self) -> date:
        return self.start.date()

    @property
    def running(self) -> bool:
        return self.end is None

    @property
    def duration(self) -> timedelta | None:
        if self.end is None:
            return None
        return self.end - self.start

    @property
    def duration_min(self) -> int | None:
        d = self.duration
        if d is None:
            return None
        return (int(d.total_seconds()) + 30) // 60

    def elapsed(self, now: datetime) -> timedelta:
        end = self.end if self.end is not None else now
        delta = end - self.start
        return max(delta, timedelta(0))


def entry_to_row(entry: Entry) -> list[str]:
    date_str = entry.start.date().isoformat()
    start_str = entry.start.strftime("%H:%M:%S")
    if entry.end is None:
        return [date_str, start_str, "", ""]
    end_str = entry.end.strftime("%H:%M:%S")
    return [date_str, start_str, end_str, str(entry.duration_min)]


def row_to_entry(row: list[str], line: int) -> Entry:
    if len(row) != 4:
        raise StoreError(f"line {line}: expected 4 fields, got {len(row)}")
    date_s, start_s, end_s, dur_s = row

    if not _DATE_RE.match(date_s):
        raise StoreError(f"line {line}: invalid date '{date_s}'")
    try:
        d = date.fromisoformat(date_s)
    except ValueError:
        raise StoreError(f"line {line}: invalid date '{date_s}'")

    if not _TIME_RE.match(start_s):
        raise StoreError(f"line {line}: invalid start time '{start_s}'")
    try:
        t_start = dtime.fromisoformat(start_s)
    except ValueError:
        raise StoreError(f"line {line}: invalid start time '{start_s}'")

    if bool(end_s) != bool(dur_s):
        raise StoreError(
            f"line {line}: end and duration_min must both be present or both empty"
        )

    start_dt = datetime.combine(d, t_start)

    if not end_s:
        end_dt = None
    else:
        if not _TIME_RE.match(end_s):
            raise StoreError(f"line {line}: invalid end time '{end_s}'")
        try:
            t_end = dtime.fromisoformat(end_s)
        except ValueError:
            raise StoreError(f"line {line}: invalid end time '{end_s}'")
        if not _DURATION_RE.match(dur_s):
            raise StoreError(f"line {line}: invalid duration_min '{dur_s}'")
        end_date = d if t_end >= t_start else d + timedelta(days=1)
        end_dt = datetime.combine(end_date, t_end)

    try:
        return Entry(start=start_dt, end=end_dt)
    except ValueError as e:
        raise StoreError(f"line {line}: {e}") from e


def read_entries(path: Path) -> list[Entry]:
    path = Path(path)
    if not path.exists():
        return []
    if path.stat().st_size == 0:
        return []

    entries: list[Entry] = []
    with path.open("r", encoding="utf-8", newline="") as f:
        reader = csv.reader(f)
        try:
            header = next(reader)
        except StopIteration:
            return []
        if header != HEADER:
            raise StoreError(
                f"{path}: line 1: expected header "
                f"'date,start,end,duration_min', got '{','.join(header)}'"
            )

        prev_running = False
        for row in reader:
            if row == [] or all(field == "" for field in row):
                continue
            if prev_running:
                raise StoreError(
                    f"{path}: line {reader.line_num}: "
                    f"running session is not the last entry"
                )
            try:
                entry = row_to_entry(row, reader.line_num)
            except StoreError as e:
                raise StoreError(f"{path}: {e}") from e
            entries.append(entry)
            prev_running = entry.running

    return entries


def write_entries(path: Path, entries: Iterable[Entry]) -> None:
    path = Path(path)
    sorted_entries = sorted(entries, key=lambda e: e.start)

    running_count = sum(1 for e in sorted_entries if e.running)
    if running_count > 1:
        raise ValueError("at most one running entry is allowed")
    if running_count == 1 and not sorted_entries[-1].running:
        raise ValueError("the running entry must be the last entry")

    path.parent.mkdir(parents=True, exist_ok=True)

    fd, tmp_path = tempfile.mkstemp(
        dir=path.parent, prefix="." + path.name + ".", suffix=".tmp"
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as f:
            writer = csv.writer(f, lineterminator="\n")
            writer.writerow(HEADER)
            for entry in sorted_entries:
                writer.writerow(entry_to_row(entry))
            f.flush()
            os.fsync(f.fileno())
        os.chmod(tmp_path, 0o600)
        os.replace(tmp_path, path)
    except Exception:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)
        raise

    dir_fd = None
    try:
        dir_fd = os.open(str(path.parent), os.O_RDONLY)
        os.fsync(dir_fd)
    except OSError:
        pass
    finally:
        if dir_fd is not None:
            os.close(dir_fd)


def running_entry(entries: Sequence[Entry]) -> Entry | None:
    if entries and entries[-1].running:
        return entries[-1]
    return None


@contextmanager
def locked(path: Path, timeout: float = 5.0) -> Iterator[None]:
    path = Path(path)
    lock_path = path.with_name(path.name + ".lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(str(lock_path), os.O_RDWR | os.O_CREAT, 0o600)
    try:
        deadline = time.monotonic() + timeout
        while True:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except OSError:
                if time.monotonic() >= deadline:
                    raise StoreError(
                        f"could not lock {path} within {timeout}s "
                        f"(another worktime command running?)"
                    )
                time.sleep(0.05)
        try:
            yield
        finally:
            fcntl.flock(fd, fcntl.LOCK_UN)
    finally:
        os.close(fd)


def update_entries(
    path: Path,
    fn: Callable[[list[Entry]], list[Entry]],
    timeout: float = 5.0,
) -> list[Entry]:
    path = Path(path)
    with locked(path, timeout):
        entries = read_entries(path)
        new_entries = fn(list(entries))
        write_entries(path, new_entries)
        return sorted(new_entries, key=lambda e: e.start)
