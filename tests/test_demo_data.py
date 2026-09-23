"""Tests for tools/make_demo_data.py (loaded via importlib since tools/ is not a package)."""

from __future__ import annotations

import importlib.util
import sys
import tempfile
import unittest
from datetime import date, datetime, time, timedelta
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]

_spec = importlib.util.spec_from_file_location(
    "make_demo_data", REPO_ROOT / "tools" / "make_demo_data.py"
)
make_demo_data = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(make_demo_data)

sys.path.insert(0, str(REPO_ROOT))
from worktime.store import read_entries  # noqa: E402


class GenerateTests(unittest.TestCase):
    def test_deterministic_same_seed(self):
        now = datetime(2026, 9, 23, 15, 0)
        a = make_demo_data.generate(120, 1, now)
        b = make_demo_data.generate(120, 1, now)
        self.assertEqual(a, b)

    def test_different_seed_differs(self):
        now = datetime(2026, 9, 23, 15, 0)
        a = make_demo_data.generate(120, 1, now)
        b = make_demo_data.generate(120, 2, now)
        self.assertNotEqual(a, b)

    def test_date_range_and_overlaps(self):
        now = datetime(2026, 9, 23, 15, 0)
        entries = make_demo_data.generate(120, 1, now)

        min_allowed = now.date() - timedelta(days=119)
        for e in entries:
            self.assertGreaterEqual(e.date, min_allowed)

        for prev, cur in zip(entries, entries[1:]):
            self.assertIsNotNone(prev.end)
            self.assertGreaterEqual(cur.start, prev.end)

    def test_only_last_entry_running(self):
        now = datetime(2026, 9, 23, 15, 0)
        entries = make_demo_data.generate(120, 1, now)

        running = [e for e in entries if e.running]
        self.assertEqual(len(running), 1)
        self.assertIs(entries[-1], running[0])

        run = running[0]
        self.assertEqual(run.date, date(2026, 9, 23))
        self.assertGreaterEqual(run.start.time(), time(7, 0))

    def test_finished_entries_end_same_day_before_2359(self):
        now = datetime(2026, 9, 23, 15, 0)
        entries = make_demo_data.generate(120, 1, now)

        for e in entries:
            if e.running:
                continue
            self.assertEqual(e.end.date(), e.start.date())
            self.assertLessEqual(e.end.time(), time(23, 59, 0))

    def test_no_running_flag(self):
        now = datetime(2026, 9, 23, 15, 0)
        entries = make_demo_data.generate(120, 1, now, running=False)
        self.assertFalse(any(e.running for e in entries))

    def test_now_before_9am_no_running_entry(self):
        now = datetime(2026, 9, 23, 8, 0)
        entries = make_demo_data.generate(120, 1, now)
        self.assertFalse(any(e.running for e in entries))

    def test_workday_totals_plausible(self):
        now = datetime(2026, 9, 23, 15, 0)
        entries = make_demo_data.generate(200, 3, now)

        totals: dict[date, timedelta] = {}
        for e in entries:
            if e.running:
                continue
            if e.start.weekday() > 4:
                continue
            totals.setdefault(e.date, timedelta())
            totals[e.date] += e.duration

        self.assertTrue(totals)
        for d, total in totals.items():
            self.assertGreaterEqual(total, timedelta(hours=4), msg=f"{d}: {total}")
            self.assertLessEqual(total, timedelta(hours=11), msg=f"{d}: {total}")


class MainTests(unittest.TestCase):
    def test_main_writes_readable_csv(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "demo.csv"
            rc = make_demo_data.main(
                [str(path), "--days", "30", "--seed", "1", "--now", "2026-09-23T15:00"]
            )
            self.assertEqual(rc, 0)
            self.assertTrue(path.exists())

            entries = read_entries(path)
            self.assertTrue(entries)

    def test_main_refuses_overwrite_without_force(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "demo.csv"
            rc1 = make_demo_data.main(
                [str(path), "--days", "10", "--seed", "1", "--now", "2026-09-23T15:00"]
            )
            self.assertEqual(rc1, 0)
            before = path.read_bytes()

            rc2 = make_demo_data.main(
                [str(path), "--days", "10", "--seed", "2", "--now", "2026-09-23T15:00"]
            )
            self.assertEqual(rc2, 1)
            after = path.read_bytes()
            self.assertEqual(before, after)

    def test_main_force_overwrites(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "demo.csv"
            rc1 = make_demo_data.main(
                [str(path), "--days", "10", "--seed", "1", "--now", "2026-09-23T15:00"]
            )
            self.assertEqual(rc1, 0)

            rc2 = make_demo_data.main(
                [
                    str(path),
                    "--days",
                    "10",
                    "--seed",
                    "2",
                    "--now",
                    "2026-09-23T15:00",
                    "--force",
                ]
            )
            self.assertEqual(rc2, 0)


if __name__ == "__main__":
    unittest.main()
