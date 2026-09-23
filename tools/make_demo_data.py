"""Generate a realistic, reproducible demo CSV for WorkTime Logger.

This lets you try the dashboard and reports without touching real tracking
data. Sessions are randomly generated (with a fixed seed by default, so runs
are reproducible) across a range of past days, on a plausible Mon-Fri (with
occasional weekend) work schedule, optionally ending with a still-running
session for "today".

Usage::

    python3 tools/make_demo_data.py /tmp/demo/worktime.csv --days 120

Then point a temporary config at the generated file, e.g.::

    cat > /tmp/demo/config.toml <<'EOF'
    data_file = "/tmp/demo/worktime.csv"
    EOF
    WORKTIME_CONFIG=/tmp/demo/config.toml bin/worktime dashboard

Run ``python3 tools/make_demo_data.py --help`` for all options.
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime, time, timedelta
from pathlib import Path
from random import Random

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from worktime.store import Entry, write_entries  # noqa: E402


def _clamp(value: int, lo: int, hi: int) -> int:
    return max(lo, min(hi, value))


def _split_minutes(total: int, num: int, rng: Random) -> list[int]:
    """Split ``total`` minutes into ``num`` parts, each at least 30 minutes."""
    base = 30
    remaining = max(0, total - base * num)
    if num == 1:
        return [total]
    cuts = sorted(rng.randint(0, remaining) for _ in range(num - 1))
    parts = []
    prev = 0
    for c in cuts:
        parts.append(base + (c - prev))
        prev = c
    parts.append(base + (remaining - prev))
    return parts


def _days_type(value: str) -> int:
    try:
        n = int(value)
    except ValueError:
        raise argparse.ArgumentTypeError(f"invalid int value: '{value}'") from None
    if not (1 <= n <= 3650):
        raise argparse.ArgumentTypeError("--days must be between 1 and 3650")
    return n


def generate(days: int, seed: int, now: datetime, running: bool = True) -> list[Entry]:
    """Generate a deterministic list of demo :class:`Entry` objects.

    Covers ``days`` calendar days ending today: the past ``days - 1`` days
    (each with 0 or more finished sessions), plus, if ``running`` is true and
    ``now`` is at or after 09:00, one running session for today.
    """
    rng = Random(seed)
    entries: list[Entry] = []

    start_date = now.date() - timedelta(days=days - 1)
    end_date = now.date() - timedelta(days=1)

    d = start_date
    while d <= end_date:
        weekday = d.weekday()  # Mon=0 .. Sun=6
        if weekday <= 4:
            prob = 0.92
        elif weekday == 5:
            prob = 0.12
        else:
            prob = 0.04

        works = rng.random() < prob

        if works and weekday <= 4:
            day_start = datetime.combine(d, time(7, 30)) + timedelta(
                minutes=rng.randint(0, 120)
            )
            total = _clamp(round(rng.gauss(510, 60)), 240, 660)
            num = rng.choice([1, 2, 2, 3])
            parts = _split_minutes(total, num, rng)

            day_end = datetime.combine(d, time(23, 59, 0))
            cursor = day_start
            for i, part in enumerate(parts):
                if i > 0:
                    cursor = cursor + timedelta(minutes=rng.randint(20, 60))
                session_start = cursor
                if session_start >= day_end:
                    cursor = session_start
                    continue
                session_end = session_start + timedelta(minutes=part)
                if session_end > day_end:
                    session_end = day_end
                if (session_end - session_start) < timedelta(minutes=10):
                    cursor = session_end
                    continue
                entries.append(Entry(start=session_start, end=session_end))
                cursor = session_end
        elif works:
            day_start = datetime.combine(d, time(9, 0)) + timedelta(
                minutes=rng.randint(0, 180)
            )
            length = rng.randint(60, 240)
            day_end = datetime.combine(d, time(23, 59, 0))
            session_end = day_start + timedelta(minutes=length)
            if session_end > day_end:
                session_end = day_end
            entries.append(Entry(start=day_start, end=session_end))

        d += timedelta(days=1)

    if running and now.time() >= time(9, 0):
        offset = rng.randint(30, 180)
        session_start = now - timedelta(minutes=offset)
        earliest = datetime.combine(now.date(), time(7, 0))
        if session_start < earliest:
            session_start = earliest
        entries.append(Entry(start=session_start))

    entries.sort(key=lambda e: e.start)
    return entries


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Generate a reproducible demo worktime.csv for the dashboard/reports.",
    )
    parser.add_argument("path", type=Path, help="output CSV path")
    parser.add_argument("--days", type=_days_type, default=120, help="number of days (1-3650, default 120)")
    parser.add_argument("--seed", type=int, default=1, help="random seed (default 1)")
    parser.add_argument(
        "--now",
        type=str,
        default=None,
        help="reference 'now' as YYYY-MM-DDTHH:MM (default: current time)",
    )
    parser.add_argument(
        "--no-running",
        action="store_true",
        help="do not add a running session for today",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="overwrite the output file if it already exists",
    )
    args = parser.parse_args(argv)

    if args.now is not None:
        now = datetime.fromisoformat(args.now)
    else:
        now = datetime.now().replace(second=0, microsecond=0)

    path: Path = args.path
    if path.exists() and not args.force:
        print(f"{path} exists; use --force to overwrite", file=sys.stderr)
        return 1

    entries = generate(args.days, args.seed, now, running=not args.no_running)

    path.parent.mkdir(parents=True, exist_ok=True)
    write_entries(path, entries)
    print(f"Wrote {len(entries)} sessions over {args.days} days to {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
