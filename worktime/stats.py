"""Aggregation and reporting numbers for WorkTime Logger.

This module turns the raw list of :class:`~worktime.store.Entry` rows into
the numbers shown on the dashboard and in reports: how much was worked on a
day/week/month/custom range, what the target for that range was, and the
balance (worked minus target).

Plain-language rules:

- **Target per day**: the configured daily target on days that are a
  configured workday and are not a configured day off, otherwise zero
  (weekends and days off never carry a target).
- **Worked time per day**: the sum of the elapsed time of every session that
  *started* on that day. A session that crosses midnight counts entirely for
  its start date, and a still-running session counts with the time elapsed
  so far (as of "now").
- **Target of a period**: the sum of the daily targets, but only for the
  days that actually fall within tracking, i.e. the overlap between the
  requested range and ``[first entry's date, today]``. Days before
  tracking started or in the future never contribute to the target, even if
  they would otherwise be workdays. If there are no entries yet, the target
  of any period is zero.
- **Balance**: worked minus target. Positive means ahead of target,
  negative means behind.
- All numbers are exact ``timedelta`` values down to the second; the only
  rounding is for the "average per day" figures, which are rounded to the
  nearest whole second.
"""

from __future__ import annotations

import calendar
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Sequence

from worktime.store import Entry


@dataclass(frozen=True)
class Target:
    """A daily target plus the calendar rules that decide when it applies."""

    daily: timedelta
    workdays: frozenset[int]  # 0 = Monday
    days_off: frozenset[date] = frozenset()

    def for_day(self, d: date) -> timedelta:
        if d.weekday() in self.workdays and d not in self.days_off:
            return self.daily
        return timedelta(0)

    @classmethod
    def from_config(cls, cfg) -> "Target":
        return cls(timedelta(minutes=cfg.daily_target_min), cfg.workdays, cfg.days_off)


@dataclass(frozen=True)
class PeriodSummary:
    start: date
    end: date
    worked: timedelta
    target: timedelta
    balance: timedelta
    sessions: int
    days_worked: int
    target_days: int
    avg_per_day_worked: timedelta
    avg_per_target_day: timedelta
    longest: Entry | None


@dataclass(frozen=True)
class Bucket:
    label: str
    start: date
    end: date
    worked: timedelta
    target: timedelta
    balance: timedelta
    cumulative_balance: timedelta


def tracking_start(entries: Sequence[Entry]) -> date | None:
    if not entries:
        return None
    return min(e.date for e in entries)


def daily_totals(
    entries: Sequence[Entry], now: datetime, start: date, end: date
) -> dict[date, timedelta]:
    if start > end:
        raise ValueError("start must be <= end")

    totals: dict[date, timedelta] = {}
    d = start
    while d <= end:
        totals[d] = timedelta(0)
        d += timedelta(days=1)

    for e in entries:
        if e.date in totals:
            totals[e.date] += e.elapsed(now)

    return totals


def _target_window(
    entries: Sequence[Entry], now: datetime, start: date, end: date, target: Target
) -> tuple[timedelta, int]:
    ts = tracking_start(entries)
    if ts is None:
        return timedelta(0), 0

    window_start = max(start, ts)
    window_end = min(end, now.date())
    if window_start > window_end:
        return timedelta(0), 0

    total = timedelta(0)
    target_days = 0
    d = window_start
    while d <= window_end:
        t = target.for_day(d)
        total += t
        if t > timedelta(0):
            target_days += 1
        d += timedelta(days=1)

    return total, target_days


def summarize(
    entries: Sequence[Entry],
    now: datetime,
    start: date,
    end: date,
    target: Target,
) -> PeriodSummary:
    if start > end:
        raise ValueError("start must be <= end")

    totals = daily_totals(entries, now, start, end)
    worked = sum(totals.values(), timedelta(0))
    days_worked = sum(1 for v in totals.values() if v > timedelta(0))

    sessions = sum(1 for e in entries if start <= e.date <= end)

    target_total, target_days = _target_window(entries, now, start, end, target)
    balance = worked - target_total

    if days_worked == 0:
        avg_per_day_worked = timedelta(0)
    else:
        avg_per_day_worked = timedelta(
            seconds=round(worked.total_seconds() / days_worked)
        )

    if target_days == 0:
        avg_per_target_day = timedelta(0)
    else:
        avg_per_target_day = timedelta(
            seconds=round(worked.total_seconds() / target_days)
        )

    longest: Entry | None = None
    for e in entries:
        if e.running:
            continue
        if not (start <= e.date <= end):
            continue
        if longest is None:
            longest = e
        elif e.duration > longest.duration:
            longest = e
        elif e.duration == longest.duration and e.start < longest.start:
            longest = e

    return PeriodSummary(
        start=start,
        end=end,
        worked=worked,
        target=target_total,
        balance=balance,
        sessions=sessions,
        days_worked=days_worked,
        target_days=target_days,
        avg_per_day_worked=avg_per_day_worked,
        avg_per_target_day=avg_per_target_day,
        longest=longest,
    )


def overview(
    entries: Sequence[Entry], now: datetime, target: Target
) -> dict[str, PeriodSummary]:
    today = now.date()

    week_start = today - timedelta(days=today.weekday())
    week_end = week_start + timedelta(days=6)

    month_start = today.replace(day=1)
    _, last_day = calendar.monthrange(today.year, today.month)
    month_end = today.replace(day=last_day)

    ts = tracking_start(entries)
    total_start = ts if ts is not None else today

    return {
        "today": summarize(entries, now, today, today, target),
        "week": summarize(entries, now, week_start, week_end, target),
        "month": summarize(entries, now, month_start, month_end, target),
        "total": summarize(entries, now, total_start, today, target),
    }


def buckets(
    entries: Sequence[Entry],
    now: datetime,
    start: date,
    end: date,
    target: Target,
    unit: str,
) -> list[Bucket]:
    if unit not in ("week", "month"):
        raise ValueError(f"unknown unit '{unit}'")
    if start > end:
        raise ValueError("start must be <= end")

    result: list[Bucket] = []
    cumulative = timedelta(0)
    cur = start
    while cur <= end:
        if unit == "week":
            iso_year, iso_week, iso_weekday = cur.isocalendar()
            natural_end = cur + timedelta(days=7 - iso_weekday)
            label = f"{iso_year}-W{iso_week:02d}"
        else:
            _, last_day = calendar.monthrange(cur.year, cur.month)
            natural_end = cur.replace(day=last_day)
            label = f"{cur.year}-{cur.month:02d}"

        bucket_end = min(natural_end, end)
        summary = summarize(entries, now, cur, bucket_end, target)
        cumulative += summary.balance

        result.append(
            Bucket(
                label=label,
                start=cur,
                end=bucket_end,
                worked=summary.worked,
                target=summary.target,
                balance=summary.balance,
                cumulative_balance=cumulative,
            )
        )

        cur = bucket_end + timedelta(days=1)

    return result


def resolve_range(name: str, now: datetime, entries: Sequence[Entry]) -> tuple[date, date]:
    today = now.date()

    if name == "7d":
        return today - timedelta(days=6), today
    if name == "30d":
        return today - timedelta(days=29), today
    if name == "90d":
        return today - timedelta(days=89), today
    if name == "year":
        return date(today.year, 1, 1), today
    if name == "all":
        ts = tracking_start(entries)
        start = ts if ts is not None else today
        return start, today

    raise ValueError(f"unknown range '{name}'")
