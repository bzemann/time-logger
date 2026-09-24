"""Unit tests for worktime/stats.py."""

from __future__ import annotations

import types
import unittest
from datetime import date, datetime, timedelta

from worktime.store import Entry
from worktime import stats


def td(hours=0, minutes=0, seconds=0):
    return timedelta(hours=hours, minutes=minutes, seconds=seconds)


class TargetTests(unittest.TestCase):
    def test_for_day_workday(self):
        target = stats.Target(td(8), frozenset({0, 1, 2, 3, 4}))
        self.assertEqual(target.for_day(date(2026, 9, 21)), td(8))  # Monday

    def test_for_day_weekend(self):
        target = stats.Target(td(8), frozenset({0, 1, 2, 3, 4}))
        self.assertEqual(target.for_day(date(2026, 9, 26)), timedelta(0))  # Saturday

    def test_for_day_day_off_on_workday(self):
        target = stats.Target(
            td(8), frozenset({0, 1, 2, 3, 4}), frozenset({date(2026, 9, 23)})
        )
        self.assertEqual(target.for_day(date(2026, 9, 23)), timedelta(0))  # Wed, off
        self.assertEqual(target.for_day(date(2026, 9, 21)), td(8))  # Mon, not off

    def test_from_config(self):
        cfg = types.SimpleNamespace(
            daily_target_min=510,
            workdays=frozenset({0, 1, 2, 3, 4}),
            days_off=frozenset(),
        )
        target = stats.Target.from_config(cfg)
        self.assertEqual(target.daily, td(8, 30))
        self.assertEqual(target.workdays, frozenset({0, 1, 2, 3, 4}))
        self.assertEqual(target.days_off, frozenset())
        self.assertEqual(target.overrides, {})

    def test_from_config_with_target_overrides(self):
        cfg = types.SimpleNamespace(
            daily_target_min=510,
            workdays=frozenset({0, 1, 2, 3, 4}),
            days_off=frozenset(),
            target_overrides={date(2026, 12, 24): 255},
        )
        target = stats.Target.from_config(cfg)
        self.assertEqual(target.overrides, {date(2026, 12, 24): td(4, 15)})
        self.assertEqual(target.for_day(date(2026, 12, 24)), td(4, 15))

    def test_for_day_override_on_workday(self):
        target = stats.Target(
            td(8),
            frozenset({0, 1, 2, 3, 4}),
            overrides={date(2026, 9, 23): td(4, 15)},  # Wednesday
        )
        self.assertEqual(target.for_day(date(2026, 9, 23)), td(4, 15))

    def test_for_day_override_on_weekend_applies(self):
        target = stats.Target(
            td(8),
            frozenset({0, 1, 2, 3, 4}),
            overrides={date(2026, 9, 26): td(6)},  # Saturday
        )
        self.assertEqual(target.for_day(date(2026, 9, 26)), td(6))

    def test_for_day_override_zero_acts_like_day_off(self):
        target = stats.Target(
            td(8),
            frozenset({0, 1, 2, 3, 4}),
            overrides={date(2026, 9, 21): timedelta(0)},  # Monday
        )
        self.assertEqual(target.for_day(date(2026, 9, 21)), timedelta(0))


class TrackingStartTests(unittest.TestCase):
    def test_empty(self):
        self.assertIsNone(stats.tracking_start([]))

    def test_min_date(self):
        entries = [
            Entry(datetime(2026, 9, 23, 8, 0, 0), datetime(2026, 9, 23, 9, 0, 0)),
            Entry(datetime(2026, 9, 21, 8, 0, 0), datetime(2026, 9, 21, 9, 0, 0)),
        ]
        self.assertEqual(stats.tracking_start(entries), date(2026, 9, 21))


class DailyTotalsTests(unittest.TestCase):
    def test_midnight_crossing_counts_for_start_date(self):
        entries = [
            Entry(datetime(2026, 9, 22, 23, 30, 0), datetime(2026, 9, 23, 0, 45, 0))
        ]
        now = datetime(2026, 9, 23, 12, 0, 0)
        totals = stats.daily_totals(entries, now, date(2026, 9, 22), date(2026, 9, 23))
        self.assertEqual(totals[date(2026, 9, 22)], td(1, 15))
        self.assertEqual(totals[date(2026, 9, 23)], timedelta(0))

    def test_includes_zero_days_ascending_inclusive(self):
        now = datetime(2026, 9, 10, 12, 0, 0)
        totals = stats.daily_totals([], now, date(2026, 9, 1), date(2026, 9, 5))
        self.assertEqual(
            list(totals.keys()),
            [date(2026, 9, d) for d in range(1, 6)],
        )
        self.assertTrue(all(v == timedelta(0) for v in totals.values()))

    def test_start_after_end_raises(self):
        now = datetime(2026, 9, 10, 12, 0, 0)
        with self.assertRaises(ValueError):
            stats.daily_totals([], now, date(2026, 9, 5), date(2026, 9, 1))


class DailyTargetsTests(unittest.TestCase):
    def test_start_after_end_raises(self):
        target = stats.Target(td(8), frozenset({0, 1, 2, 3, 4}))
        now = datetime(2026, 9, 10, 12, 0, 0)
        with self.assertRaises(ValueError):
            stats.daily_targets([], now, date(2026, 9, 5), date(2026, 9, 1), target)

    def test_no_entries_all_zero(self):
        target = stats.Target(td(8), frozenset({0, 1, 2, 3, 4}))
        now = datetime(2026, 9, 10, 12, 0, 0)
        result = stats.daily_targets([], now, date(2026, 9, 1), date(2026, 9, 5), target)
        self.assertEqual(list(result.keys()), [date(2026, 9, d) for d in range(1, 6)])
        self.assertTrue(all(v == timedelta(0) for v in result.values()))

    def test_before_first_entry_is_zero(self):
        entries = [
            Entry(datetime(2026, 9, 23, 8, 0, 0), datetime(2026, 9, 23, 12, 0, 0))
        ]
        now = datetime(2026, 9, 23, 18, 0, 0)
        target = stats.Target(td(8), frozenset({0, 1, 2, 3, 4}))
        result = stats.daily_targets(entries, now, date(2026, 9, 21), date(2026, 9, 23), target)
        self.assertEqual(result[date(2026, 9, 21)], timedelta(0))  # Monday, before tracking
        self.assertEqual(result[date(2026, 9, 22)], timedelta(0))  # Tuesday, before tracking
        self.assertEqual(result[date(2026, 9, 23)], td(8))  # Wednesday, first entry day

    def test_future_is_zero(self):
        entries = [
            Entry(datetime(2026, 9, 21, 8, 0, 0), datetime(2026, 9, 21, 12, 0, 0))
        ]
        now = datetime(2026, 9, 23, 18, 0, 0)
        target = stats.Target(td(8), frozenset({0, 1, 2, 3, 4}))
        result = stats.daily_targets(entries, now, date(2026, 9, 23), date(2026, 9, 25), target)
        self.assertEqual(result[date(2026, 9, 23)], td(8))  # Wednesday, today
        self.assertEqual(result[date(2026, 9, 24)], timedelta(0))  # future
        self.assertEqual(result[date(2026, 9, 25)], timedelta(0))  # future

    def test_weekend_is_zero(self):
        entries = [
            Entry(datetime(2026, 9, 21, 8, 0, 0), datetime(2026, 9, 21, 12, 0, 0))
        ]
        now = datetime(2026, 9, 27, 18, 0, 0)  # Sunday
        target = stats.Target(td(8), frozenset({0, 1, 2, 3, 4}))
        result = stats.daily_targets(entries, now, date(2026, 9, 26), date(2026, 9, 27), target)
        self.assertEqual(result[date(2026, 9, 26)], timedelta(0))  # Saturday
        self.assertEqual(result[date(2026, 9, 27)], timedelta(0))  # Sunday

    def test_day_off_is_zero(self):
        entries = [
            Entry(datetime(2026, 9, 21, 8, 0, 0), datetime(2026, 9, 21, 12, 0, 0))
        ]
        now = datetime(2026, 9, 23, 18, 0, 0)
        target = stats.Target(
            td(8), frozenset({0, 1, 2, 3, 4}), frozenset({date(2026, 9, 23)})
        )
        result = stats.daily_targets(entries, now, date(2026, 9, 22), date(2026, 9, 23), target)
        self.assertEqual(result[date(2026, 9, 22)], td(8))  # Tuesday
        self.assertEqual(result[date(2026, 9, 23)], timedelta(0))  # Wednesday, day off

    def test_consistency_with_summarize(self):
        entries = [
            Entry(datetime(2026, 9, 21, 8, 0, 0), datetime(2026, 9, 21, 18, 0, 0)),
            Entry(datetime(2026, 9, 22, 7, 0, 0), datetime(2026, 9, 22, 18, 43, 0)),
            Entry(datetime(2026, 9, 23, 8, 45, 0), datetime(2026, 9, 23, 12, 1, 0)),
        ]
        now = datetime(2026, 9, 23, 17, 3, 0)
        target = stats.Target(td(8, 24), frozenset({0, 1, 2, 3, 4}))
        ranges = [
            (date(2026, 9, 1), date(2026, 9, 23)),
            (date(2026, 9, 21), date(2026, 9, 27)),
            (date(2026, 9, 23), date(2026, 9, 23)),
            (date(2026, 9, 1), date(2026, 9, 30)),
        ]
        for start, end in ranges:
            targets = stats.daily_targets(entries, now, start, end, target)
            summary = stats.summarize(entries, now, start, end, target)
            self.assertEqual(sum(targets.values(), timedelta(0)), summary.target)


class TargetOverridesIntegrationTests(unittest.TestCase):
    def test_override_zero_not_counted_in_target_days(self):
        entries = [
            Entry(datetime(2026, 9, 21, 8, 0, 0), datetime(2026, 9, 21, 12, 0, 0)),
        ]
        now = datetime(2026, 9, 21, 18, 0, 0)
        target = stats.Target(
            td(8),
            frozenset({0, 1, 2, 3, 4}),
            overrides={date(2026, 9, 21): timedelta(0)},
        )
        summary = stats.summarize(entries, now, date(2026, 9, 21), date(2026, 9, 21), target)
        self.assertEqual(summary.target, timedelta(0))
        self.assertEqual(summary.target_days, 0)

    def test_summarize_week_with_one_half_day_override(self):
        # Mon-Fri workweek, Wed has a half-day override of 4:15 instead
        # of the normal 8:30 target.
        entries = [
            Entry(datetime(2026, 9, 21, 8, 0, 0), datetime(2026, 9, 21, 16, 30, 0)),
            Entry(datetime(2026, 9, 22, 8, 0, 0), datetime(2026, 9, 22, 16, 30, 0)),
            Entry(datetime(2026, 9, 23, 8, 0, 0), datetime(2026, 9, 23, 12, 15, 0)),
            Entry(datetime(2026, 9, 24, 8, 0, 0), datetime(2026, 9, 24, 16, 30, 0)),
            Entry(datetime(2026, 9, 25, 8, 0, 0), datetime(2026, 9, 25, 16, 30, 0)),
        ]
        now = datetime(2026, 9, 25, 18, 0, 0)
        target = stats.Target(
            td(8, 30),
            frozenset({0, 1, 2, 3, 4}),
            overrides={date(2026, 9, 23): td(4, 15)},
        )
        summary = stats.summarize(entries, now, date(2026, 9, 21), date(2026, 9, 27), target)
        expected_target = td(8, 30) * 4 + td(4, 15)
        self.assertEqual(summary.target, expected_target)
        self.assertEqual(summary.target_days, 5)

    def test_override_before_first_entry_gives_zero_via_window(self):
        entries = [
            Entry(datetime(2026, 9, 23, 8, 0, 0), datetime(2026, 9, 23, 12, 0, 0))
        ]
        now = datetime(2026, 9, 23, 18, 0, 0)
        target = stats.Target(
            td(8),
            frozenset({0, 1, 2, 3, 4}),
            overrides={date(2026, 9, 21): td(4)},  # before tracking started
        )
        result = stats.daily_targets(
            entries, now, date(2026, 9, 21), date(2026, 9, 23), target
        )
        self.assertEqual(result[date(2026, 9, 21)], timedelta(0))

    def test_override_in_future_gives_zero_via_window(self):
        entries = [
            Entry(datetime(2026, 9, 21, 8, 0, 0), datetime(2026, 9, 21, 12, 0, 0))
        ]
        now = datetime(2026, 9, 23, 18, 0, 0)
        target = stats.Target(
            td(8),
            frozenset({0, 1, 2, 3, 4}),
            overrides={date(2026, 9, 25): td(4)},  # in the future
        )
        result = stats.daily_targets(
            entries, now, date(2026, 9, 23), date(2026, 9, 25), target
        )
        self.assertEqual(result[date(2026, 9, 25)], timedelta(0))


class SummarizeTests(unittest.TestCase):
    def test_start_after_end_raises(self):
        target = stats.Target(td(8), frozenset({0, 1, 2, 3, 4}))
        now = datetime(2026, 9, 10, 12, 0, 0)
        with self.assertRaises(ValueError):
            stats.summarize([], now, date(2026, 9, 5), date(2026, 9, 1), target)

    def test_target_window_before_first_entry(self):
        # Range starts long before tracking began; target should only be
        # counted from the first entry's date onward.
        entries = [
            Entry(datetime(2026, 9, 23, 8, 0, 0), datetime(2026, 9, 23, 12, 0, 0))
        ]
        now = datetime(2026, 9, 23, 18, 0, 0)
        target = stats.Target(td(8), frozenset({0, 1, 2, 3, 4}))
        summary = stats.summarize(entries, now, date(2026, 9, 1), date(2026, 9, 23), target)
        self.assertEqual(summary.worked, td(4))
        self.assertEqual(summary.target, td(8))
        self.assertEqual(summary.target_days, 1)
        self.assertEqual(summary.balance, td(-4))

    def test_target_window_clips_to_today(self):
        entries = [
            Entry(datetime(2026, 9, 23, 8, 0, 0), datetime(2026, 9, 23, 12, 0, 0))
        ]
        now = datetime(2026, 9, 23, 18, 0, 0)
        target = stats.Target(td(8), frozenset({0, 1, 2, 3, 4}))
        summary = stats.summarize(entries, now, date(2026, 9, 23), date(2026, 9, 30), target)
        self.assertEqual(summary.target, td(8))
        self.assertEqual(summary.target_days, 1)

    def test_no_entries_gives_zero_target(self):
        now = datetime(2026, 9, 10, 12, 0, 0)
        target = stats.Target(td(8), frozenset({0, 1, 2, 3, 4}))
        summary = stats.summarize([], now, date(2026, 9, 1), date(2026, 9, 10), target)
        self.assertEqual(summary.worked, timedelta(0))
        self.assertEqual(summary.target, timedelta(0))
        self.assertEqual(summary.target_days, 0)
        self.assertEqual(summary.balance, timedelta(0))
        self.assertEqual(summary.sessions, 0)
        self.assertEqual(summary.days_worked, 0)
        self.assertIsNone(summary.longest)

    def test_weekend_work_counts_but_no_target(self):
        entries = [
            Entry(datetime(2026, 9, 26, 10, 0, 0), datetime(2026, 9, 26, 12, 0, 0))
        ]  # Saturday
        now = datetime(2026, 9, 26, 23, 0, 0)
        target = stats.Target(td(8), frozenset({0, 1, 2, 3, 4}))
        summary = stats.summarize(entries, now, date(2026, 9, 26), date(2026, 9, 26), target)
        self.assertEqual(summary.worked, td(2))
        self.assertEqual(summary.target, timedelta(0))
        self.assertEqual(summary.balance, td(2))

    def test_day_off_work_counts_but_no_target(self):
        entries = [
            Entry(datetime(2026, 9, 23, 10, 0, 0), datetime(2026, 9, 23, 12, 0, 0))
        ]  # Wednesday, but a day off
        now = datetime(2026, 9, 23, 23, 0, 0)
        target = stats.Target(
            td(8), frozenset({0, 1, 2, 3, 4}), frozenset({date(2026, 9, 23)})
        )
        summary = stats.summarize(entries, now, date(2026, 9, 23), date(2026, 9, 23), target)
        self.assertEqual(summary.worked, td(2))
        self.assertEqual(summary.target, timedelta(0))
        self.assertEqual(summary.balance, td(2))

    def test_averages_rounding(self):
        entries = [
            Entry(datetime(2026, 3, 2, 8, 0, 0), datetime(2026, 3, 2, 8, 0, 40)),  # Mon 40s
            Entry(datetime(2026, 3, 3, 8, 0, 0), datetime(2026, 3, 3, 8, 0, 35)),  # Tue 35s
            Entry(datetime(2026, 3, 4, 8, 0, 0), datetime(2026, 3, 4, 8, 0, 36)),  # Wed 36s
            Entry(datetime(2026, 3, 7, 8, 0, 0), datetime(2026, 3, 7, 8, 0, 10)),  # Sat 10s
        ]
        now = datetime(2026, 3, 7, 23, 0, 0)
        target = stats.Target(td(8), frozenset({0, 1, 2, 3, 4}))
        summary = stats.summarize(entries, now, date(2026, 3, 2), date(2026, 3, 7), target)
        self.assertEqual(summary.days_worked, 4)
        # Window Mon 3/2..Sat 3/7 has 5 workdays (Mon-Fri), regardless of
        # whether each workday has an entry.
        self.assertEqual(summary.target_days, 5)
        self.assertEqual(summary.worked, td(seconds=121))
        self.assertEqual(summary.avg_per_day_worked, td(seconds=30))  # round(121/4)
        self.assertEqual(summary.avg_per_target_day, td(seconds=24))  # round(121/5)

    def test_averages_zero_division(self):
        now = datetime(2026, 9, 10, 12, 0, 0)
        target = stats.Target(td(8), frozenset({0, 1, 2, 3, 4}))
        summary = stats.summarize([], now, date(2026, 9, 1), date(2026, 9, 10), target)
        self.assertEqual(summary.avg_per_day_worked, timedelta(0))
        self.assertEqual(summary.avg_per_target_day, timedelta(0))

    def test_avg_per_target_day_zero_when_no_target_days(self):
        entries = [
            Entry(datetime(2026, 9, 26, 10, 0, 0), datetime(2026, 9, 26, 12, 0, 0))
        ]  # Saturday only, not a workday
        now = datetime(2026, 9, 26, 23, 0, 0)
        target = stats.Target(td(8), frozenset({0, 1, 2, 3, 4}))
        summary = stats.summarize(entries, now, date(2026, 9, 26), date(2026, 9, 26), target)
        self.assertEqual(summary.days_worked, 1)
        self.assertEqual(summary.target_days, 0)
        self.assertEqual(summary.avg_per_day_worked, td(2))
        self.assertEqual(summary.avg_per_target_day, timedelta(0))

    def test_longest_ignores_running_entry(self):
        entries = [
            Entry(datetime(2026, 9, 23, 8, 0, 0), datetime(2026, 9, 23, 8, 30, 0)),  # 30m
            Entry(datetime(2026, 9, 23, 12, 0, 0)),  # running, elapsed 5h by now
        ]
        now = datetime(2026, 9, 23, 17, 0, 0)
        target = stats.Target(td(8), frozenset({0, 1, 2, 3, 4}))
        summary = stats.summarize(entries, now, date(2026, 9, 23), date(2026, 9, 23), target)
        self.assertEqual(summary.longest, entries[0])

    def test_longest_tie_breaks_on_earliest_start(self):
        earlier = Entry(datetime(2026, 9, 23, 8, 0, 0), datetime(2026, 9, 23, 9, 0, 0))
        later = Entry(datetime(2026, 9, 23, 10, 0, 0), datetime(2026, 9, 23, 11, 0, 0))
        now = datetime(2026, 9, 23, 17, 0, 0)
        target = stats.Target(td(8), frozenset({0, 1, 2, 3, 4}))
        summary = stats.summarize(
            [later, earlier], now, date(2026, 9, 23), date(2026, 9, 23), target
        )
        self.assertEqual(summary.longest, earlier)

    def test_longest_none_when_no_finished_entries(self):
        entries = [Entry(datetime(2026, 9, 23, 8, 0, 0))]  # running only
        now = datetime(2026, 9, 23, 17, 0, 0)
        target = stats.Target(td(8), frozenset({0, 1, 2, 3, 4}))
        summary = stats.summarize(entries, now, date(2026, 9, 23), date(2026, 9, 23), target)
        self.assertIsNone(summary.longest)


class ScreenshotScenarioTests(unittest.TestCase):
    """The scenario from example-dashboard.jpeg / the roadmap spec."""

    def setUp(self):
        self.entries = [
            Entry(datetime(2026, 9, 21, 8, 0, 0), datetime(2026, 9, 21, 18, 0, 0)),
            Entry(datetime(2026, 9, 22, 7, 0, 0), datetime(2026, 9, 22, 18, 43, 0)),
            Entry(datetime(2026, 9, 23, 8, 45, 0), datetime(2026, 9, 23, 12, 1, 0)),
            Entry(datetime(2026, 9, 23, 13, 10, 0), datetime(2026, 9, 23, 13, 52, 0)),
            Entry(datetime(2026, 9, 23, 13, 52, 0), datetime(2026, 9, 23, 15, 1, 0)),
            Entry(datetime(2026, 9, 23, 15, 1, 0)),
        ]
        self.now = datetime(2026, 9, 23, 17, 3, 0)
        self.target = stats.Target(td(8, 24), frozenset({0, 1, 2, 3, 4}))

    def test_overview(self):
        ov = stats.overview(self.entries, self.now, self.target)

        today = ov["today"]
        self.assertEqual(today.worked, td(7, 9))
        self.assertEqual(today.target, td(8, 24))
        self.assertEqual(today.balance, -td(1, 15))

        week = ov["week"]
        self.assertEqual(week.worked, td(28, 52))
        self.assertEqual(week.target, td(25, 12))
        self.assertEqual(week.balance, td(3, 40))
        self.assertEqual(week.sessions, 6)
        self.assertEqual(week.days_worked, 3)
        self.assertEqual(week.longest, self.entries[1])  # Tuesday entry

        month = ov["month"]
        self.assertEqual(month.worked, week.worked)
        self.assertEqual(month.target, week.target)
        self.assertEqual(month.balance, week.balance)

        total = ov["total"]
        self.assertEqual(total.worked, week.worked)
        self.assertEqual(total.target, week.target)
        self.assertEqual(total.balance, week.balance)

    def test_overview_key_order(self):
        ov = stats.overview(self.entries, self.now, self.target)
        self.assertEqual(list(ov.keys()), ["today", "week", "month", "total"])


class OverviewEdgeCaseTests(unittest.TestCase):
    def test_overview_on_sunday(self):
        now = datetime(2026, 9, 27, 10, 0, 0)  # Sunday
        target = stats.Target(td(8), frozenset({0, 1, 2, 3, 4}))
        ov = stats.overview([], now, target)
        self.assertEqual(ov["week"].start, date(2026, 9, 21))  # Monday
        self.assertEqual(ov["week"].end, date(2026, 9, 27))  # that Sunday

    def test_overview_month_bounds_leap_february(self):
        now = datetime(2028, 2, 15, 12, 0, 0)
        target = stats.Target(td(8), frozenset({0, 1, 2, 3, 4}))
        ov = stats.overview([], now, target)
        self.assertEqual(ov["month"].start, date(2028, 2, 1))
        self.assertEqual(ov["month"].end, date(2028, 2, 29))

    def test_overview_total_no_entries_uses_today(self):
        now = datetime(2026, 9, 23, 12, 0, 0)
        target = stats.Target(td(8), frozenset({0, 1, 2, 3, 4}))
        ov = stats.overview([], now, target)
        self.assertEqual(ov["total"].start, date(2026, 9, 23))
        self.assertEqual(ov["total"].end, date(2026, 9, 23))


class BucketsTests(unittest.TestCase):
    def setUp(self):
        self.target = stats.Target(td(8), frozenset({0, 1, 2, 3, 4}))

    def test_invalid_unit_raises(self):
        now = datetime(2026, 9, 23, 12, 0, 0)
        with self.assertRaises(ValueError):
            stats.buckets([], now, date(2026, 9, 1), date(2026, 9, 10), self.target, "day")

    def test_start_after_end_raises(self):
        now = datetime(2026, 9, 23, 12, 0, 0)
        with self.assertRaises(ValueError):
            stats.buckets([], now, date(2026, 9, 10), date(2026, 9, 1), self.target, "week")

    def test_week_buckets(self):
        now = datetime(2026, 10, 1, 12, 0, 0)
        entries = [
            Entry(datetime(2026, 9, 16, 8, 0, 0), datetime(2026, 9, 16, 9, 0, 0)),
            Entry(datetime(2026, 9, 21, 8, 0, 0), datetime(2026, 9, 21, 9, 0, 0)),
            Entry(datetime(2026, 9, 28, 8, 0, 0), datetime(2026, 9, 28, 9, 0, 0)),
        ]
        result = stats.buckets(
            entries, now, date(2026, 9, 16), date(2026, 9, 30), self.target, "week"
        )
        self.assertEqual(len(result), 3)

        self.assertEqual(result[0].label, "2026-W38")
        self.assertEqual(result[0].start, date(2026, 9, 16))
        self.assertEqual(result[0].end, date(2026, 9, 20))

        self.assertEqual(result[1].label, "2026-W39")
        self.assertEqual(result[1].start, date(2026, 9, 21))
        self.assertEqual(result[1].end, date(2026, 9, 27))

        self.assertEqual(result[2].label, "2026-W40")
        self.assertEqual(result[2].start, date(2026, 9, 28))
        self.assertEqual(result[2].end, date(2026, 9, 30))

        expected_cumulative = (
            result[0].balance,
            result[0].balance + result[1].balance,
            result[0].balance + result[1].balance + result[2].balance,
        )
        self.assertEqual(
            (result[0].cumulative_balance, result[1].cumulative_balance, result[2].cumulative_balance),
            expected_cumulative,
        )

    def test_week_bucket_iso_year_edge(self):
        now = datetime(2027, 1, 10, 12, 0, 0)
        start, end = date(2026, 12, 28), date(2027, 1, 3)
        result = stats.buckets([], now, start, end, self.target, "week")
        self.assertEqual(len(result), 1)
        iso_year, iso_week, _ = start.isocalendar()
        self.assertEqual(result[0].label, f"{iso_year}-W{iso_week:02d}")
        self.assertEqual(result[0].label, "2026-W53")
        self.assertEqual(result[0].start, start)
        self.assertEqual(result[0].end, end)

    def test_month_buckets(self):
        now = datetime(2026, 4, 1, 12, 0, 0)
        result = stats.buckets(
            [], now, date(2026, 1, 15), date(2026, 3, 10), self.target, "month"
        )
        self.assertEqual(len(result), 3)

        self.assertEqual(result[0].label, "2026-01")
        self.assertEqual(result[0].start, date(2026, 1, 15))
        self.assertEqual(result[0].end, date(2026, 1, 31))

        self.assertEqual(result[1].label, "2026-02")
        self.assertEqual(result[1].start, date(2026, 2, 1))
        self.assertEqual(result[1].end, date(2026, 2, 28))

        self.assertEqual(result[2].label, "2026-03")
        self.assertEqual(result[2].start, date(2026, 3, 1))
        self.assertEqual(result[2].end, date(2026, 3, 10))

    def test_future_buckets_have_zero_worked_and_target(self):
        now = datetime(2026, 1, 1, 12, 0, 0)
        result = stats.buckets(
            [], now, date(2026, 6, 1), date(2026, 6, 30), self.target, "month"
        )
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].worked, timedelta(0))
        self.assertEqual(result[0].target, timedelta(0))


class ResolveRangeTests(unittest.TestCase):
    def setUp(self):
        self.now = datetime(2026, 9, 23, 12, 0, 0)  # Wednesday

    def test_7d(self):
        self.assertEqual(
            stats.resolve_range("7d", self.now, []),
            (date(2026, 9, 17), date(2026, 9, 23)),
        )

    def test_30d(self):
        self.assertEqual(
            stats.resolve_range("30d", self.now, []),
            (date(2026, 8, 25), date(2026, 9, 23)),
        )

    def test_90d(self):
        self.assertEqual(
            stats.resolve_range("90d", self.now, []),
            (date(2026, 6, 26), date(2026, 9, 23)),
        )

    def test_year(self):
        self.assertEqual(
            stats.resolve_range("year", self.now, []),
            (date(2026, 1, 1), date(2026, 9, 23)),
        )

    def test_all_with_entries(self):
        entries = [
            Entry(datetime(2026, 9, 5, 8, 0, 0), datetime(2026, 9, 5, 9, 0, 0)),
        ]
        self.assertEqual(
            stats.resolve_range("all", self.now, entries),
            (date(2026, 9, 5), date(2026, 9, 23)),
        )

    def test_all_without_entries(self):
        self.assertEqual(
            stats.resolve_range("all", self.now, []),
            (date(2026, 9, 23), date(2026, 9, 23)),
        )

    def test_unknown_name_raises(self):
        with self.assertRaises(ValueError):
            stats.resolve_range("bogus", self.now, [])


if __name__ == "__main__":
    unittest.main()
