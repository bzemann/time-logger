"""Tests for worktime/session.py (start/stop/status logic)."""

from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path

from worktime import session
from worktime.session import SessionError
from worktime.store import Entry, write_entries


class TempDirTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp_path = Path(self._tmp.name)
        self.csv_path = self.tmp_path / "worktime.csv"

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def read_bytes(self) -> bytes:
        return self.csv_path.read_bytes()


class FormatDurationTests(unittest.TestCase):
    def test_examples(self):
        cases = [
            (timedelta(seconds=0), "0m"),
            (timedelta(seconds=59), "0m"),
            (timedelta(seconds=60), "1m"),
            (timedelta(minutes=45), "45m"),
            (timedelta(hours=1), "1h 00m"),
            (timedelta(hours=1, minutes=24, seconds=50), "1h 24m"),
            (timedelta(hours=25, minutes=3), "25h 03m"),
        ]
        for td, expected in cases:
            with self.subTest(td=td):
                self.assertEqual(session.format_duration(td), expected)

    def test_never_negative(self):
        self.assertEqual(session.format_duration(timedelta(seconds=-30)), "0m")


class ResolveAtTests(unittest.TestCase):
    def setUp(self) -> None:
        self.now = datetime(2026, 9, 23, 19, 0, 0)  # Wednesday

    def test_earlier_today(self):
        result = session.resolve_at("17:30", self.now)
        self.assertEqual(result, datetime(2026, 9, 23, 17, 30))

    def test_later_falls_back_to_yesterday(self):
        result = session.resolve_at("20:00", self.now)
        self.assertEqual(result, datetime(2026, 9, 22, 20, 0))

    def test_exact_now_time(self):
        result = session.resolve_at("19:00", self.now)
        self.assertEqual(result, datetime(2026, 9, 23, 19, 0))

    def test_single_digit_hour(self):
        result = session.resolve_at("7:05", self.now)
        self.assertEqual(result, datetime(2026, 9, 23, 7, 5))

    def test_invalid_formats(self):
        for bad in ["24:00", "12:60", "abc", "1730"]:
            with self.subTest(bad=bad):
                with self.assertRaises(SessionError):
                    session.resolve_at(bad, self.now)


class TodayTotalTests(unittest.TestCase):
    def test_mixed_entries(self):
        now = datetime(2026, 9, 23, 12, 0, 0)
        entries = [
            # finished session today: 09:00-10:30 = 1h30m
            Entry(datetime(2026, 9, 23, 9, 0), datetime(2026, 9, 23, 10, 30)),
            # session started yesterday 23:30, ends today 00:45 -> belongs to
            # yesterday, must NOT count toward today
            Entry(datetime(2026, 9, 22, 23, 30), datetime(2026, 9, 23, 0, 45)),
            # running session today, started 11:00, now is 12:00 -> 1h
            Entry(datetime(2026, 9, 23, 11, 0)),
        ]
        total = session.today_total(entries, now)
        self.assertEqual(total, timedelta(hours=2, minutes=30))


class StartTests(TempDirTestCase):
    def test_start_on_empty_file(self):
        now = datetime(2026, 9, 23, 9, 0, 0)
        result = session.start(self.csv_path, now)
        self.assertTrue(result.started)
        self.assertEqual(result.entry.start, now)
        self.assertIsNone(result.entry.end)

        from worktime.store import read_entries

        entries = read_entries(self.csv_path)
        self.assertEqual(len(entries), 1)
        self.assertTrue(entries[0].running)
        self.assertEqual(entries[0].start, now)

    def test_start_again_does_not_change_csv(self):
        now = datetime(2026, 9, 23, 9, 0, 0)
        session.start(self.csv_path, now)
        before = self.read_bytes()

        later = now + timedelta(hours=1, minutes=24)
        result = session.start(self.csv_path, later)
        self.assertFalse(result.started)
        self.assertEqual(result.elapsed, timedelta(hours=1, minutes=24))
        self.assertEqual(self.read_bytes(), before)

    def test_start_at_with_empty_file(self):
        now = datetime(2026, 9, 23, 9, 0, 0)
        result = session.start(self.csv_path, now, at="08:00")
        self.assertTrue(result.started)
        self.assertEqual(result.entry.start, datetime(2026, 9, 23, 8, 0))

    def test_start_at_overlap_raises_and_csv_unchanged(self):
        now = datetime(2026, 9, 23, 9, 0, 0)
        write_entries(
            self.csv_path,
            [Entry(datetime(2026, 9, 23, 7, 0), datetime(2026, 9, 23, 8, 30))],
        )
        before = self.read_bytes()

        with self.assertRaises(SessionError):
            session.start(self.csv_path, now, at="08:00")
        self.assertEqual(self.read_bytes(), before)

    def test_start_at_while_running_raises(self):
        now = datetime(2026, 9, 23, 9, 0, 0)
        write_entries(self.csv_path, [Entry(datetime(2026, 9, 23, 8, 0))])
        with self.assertRaises(SessionError):
            session.start(self.csv_path, now, at="08:30")


class StopTests(TempDirTestCase):
    def test_stop_running_session(self):
        start_time = datetime(2026, 9, 23, 9, 0, 0)
        session.start(self.csv_path, start_time)

        stop_time = start_time + timedelta(hours=2)
        result = session.stop(self.csv_path, stop_time)
        self.assertTrue(result.stopped)
        self.assertEqual(result.entry.duration, timedelta(hours=2))

        from worktime.store import read_entries

        entries = read_entries(self.csv_path)
        self.assertEqual(len(entries), 1)
        self.assertFalse(entries[0].running)

    def test_stop_again_does_not_change_csv(self):
        start_time = datetime(2026, 9, 23, 9, 0, 0)
        session.start(self.csv_path, start_time)
        session.stop(self.csv_path, start_time + timedelta(hours=2))
        before = self.read_bytes()

        result = session.stop(self.csv_path, start_time + timedelta(hours=3))
        self.assertFalse(result.stopped)
        self.assertIsNone(result.entry)
        self.assertEqual(self.read_bytes(), before)

    def test_stop_after_25h_raises_and_csv_unchanged(self):
        start_time = datetime(2026, 9, 23, 9, 0, 0)
        write_entries(self.csv_path, [Entry(start_time)])
        before = self.read_bytes()

        now = start_time + timedelta(hours=25)
        with self.assertRaises(SessionError) as ctx:
            session.stop(self.csv_path, now)
        self.assertIn("worktime stop --at HH:MM", str(ctx.exception))
        self.assertEqual(self.read_bytes(), before)

    def test_stop_at_resolves_to_previous_day(self):
        start_time = datetime(2026, 9, 22, 9, 0, 0)  # yesterday
        write_entries(self.csv_path, [Entry(start_time)])
        now = datetime(2026, 9, 23, 10, 0, 0)

        result = session.stop(self.csv_path, now, at="17:30")
        self.assertTrue(result.stopped)
        self.assertEqual(result.entry.end, datetime(2026, 9, 22, 17, 30))
        self.assertEqual(result.entry.duration, timedelta(hours=8, minutes=30))

    def test_stop_at_before_start_raises(self):
        start_time = datetime(2026, 9, 23, 9, 0, 0)
        write_entries(self.csv_path, [Entry(start_time)])
        now = datetime(2026, 9, 23, 10, 0, 0)

        with self.assertRaises(SessionError):
            session.stop(self.csv_path, now, at="08:00")

    def test_stop_at_over_24h_raises(self):
        # Started Monday 09:00, now Wednesday 10:00, --at 17:30 resolves to
        # Tuesday 17:30 -> a session of 1 day 8h30m (>= 24h).
        start_time = datetime(2026, 9, 21, 9, 0, 0)  # Monday
        write_entries(self.csv_path, [Entry(start_time)])
        now = datetime(2026, 9, 23, 10, 0, 0)  # Wednesday

        with self.assertRaises(SessionError) as ctx:
            session.stop(self.csv_path, now, at="17:30")
        self.assertIn("shorter than 24h", str(ctx.exception))

    def test_system_clock_before_start_raises(self):
        start_time = datetime(2026, 9, 23, 12, 0, 0)
        write_entries(self.csv_path, [Entry(start_time)])
        now = datetime(2026, 9, 23, 11, 0, 0)  # before the session start

        with self.assertRaises(SessionError):
            session.stop(self.csv_path, now)


class StatusTests(TempDirTestCase):
    def test_status_running(self):
        start_time = datetime(2026, 9, 23, 9, 0, 0)
        write_entries(self.csv_path, [Entry(start_time)])
        now = start_time + timedelta(minutes=30)

        result = session.status(self.csv_path, now)
        self.assertIsNotNone(result.entry)
        self.assertEqual(result.elapsed, timedelta(minutes=30))
        self.assertEqual(result.today, timedelta(minutes=30))

    def test_status_not_running(self):
        now = datetime(2026, 9, 23, 9, 0, 0)
        result = session.status(self.csv_path, now)
        self.assertIsNone(result.entry)
        self.assertEqual(result.elapsed, timedelta(0))
        self.assertEqual(result.today, timedelta(0))


if __name__ == "__main__":
    unittest.main()
