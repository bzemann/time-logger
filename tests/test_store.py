"""Unit tests for worktime.store."""

from __future__ import annotations

import os
import tempfile
import time
import threading
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from worktime.store import (
    HEADER,
    MAX_DURATION,
    Entry,
    StoreError,
    entry_to_row,
    locked,
    read_entries,
    row_to_entry,
    running_entry,
    update_entries,
    write_entries,
)

FIXTURES = Path(__file__).parent / "fixtures"
SAMPLE_CSV = FIXTURES / "sample.csv"


class TempDirTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp_path = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)

    def csv_path(self, name: str = "worktime.csv") -> Path:
        return self.tmp_path / name


class SampleFixtureTests(TempDirTestCase):
    def test_sample_fixture_parses(self) -> None:
        entries = read_entries(SAMPLE_CSV)
        self.assertEqual(len(entries), 3)

        second = entries[1]
        self.assertEqual(second.date.isoformat(), "2026-09-22")
        self.assertEqual(second.end, datetime(2026, 9, 23, 0, 45, 0))
        self.assertEqual(second.duration_min, 75)

        third = entries[2]
        self.assertTrue(third.running)
        self.assertIsNone(third.end)
        self.assertIsNone(third.duration_min)

        self.assertIs(running_entry(entries), third)

    def test_round_trip_equal_entries(self) -> None:
        entries = read_entries(SAMPLE_CSV)
        out_path = self.csv_path()
        write_entries(out_path, entries)
        reread = read_entries(out_path)
        self.assertEqual(entries, reread)

    def test_round_trip_reproduces_fixture_bytes(self) -> None:
        entries = read_entries(SAMPLE_CSV)
        out_path = self.csv_path()
        write_entries(out_path, entries)
        self.assertEqual(out_path.read_bytes(), SAMPLE_CSV.read_bytes())


class ExactBytesTests(TempDirTestCase):
    def test_no_crlf_and_header_and_trailing_newline(self) -> None:
        path = self.csv_path()
        write_entries(path, [Entry(start=datetime(2026, 1, 1, 8, 0, 0))])
        data = path.read_bytes()
        self.assertNotIn(b"\r", data)
        self.assertTrue(data.startswith(b"date,start,end,duration_min\n"))
        self.assertTrue(data.endswith(b"\n"))


class DurationRoundingTests(unittest.TestCase):
    def _duration_min(self, seconds: int) -> int | None:
        start = datetime(2026, 1, 1, 0, 0, 0)
        end = start + timedelta(seconds=seconds)
        return Entry(start=start, end=end).duration_min

    def test_29_seconds_rounds_to_0(self) -> None:
        self.assertEqual(self._duration_min(29), 0)

    def test_30_seconds_rounds_to_1(self) -> None:
        self.assertEqual(self._duration_min(30), 1)

    def test_89_seconds_rounds_to_1(self) -> None:
        self.assertEqual(self._duration_min(89), 1)

    def test_90_seconds_rounds_to_2(self) -> None:
        self.assertEqual(self._duration_min(90), 2)

    def test_microseconds_dropped(self) -> None:
        start = datetime(2026, 1, 1, 8, 0, 0, 123456)
        end = datetime(2026, 1, 1, 9, 0, 0, 654321)
        entry = Entry(start=start, end=end)
        self.assertEqual(entry.start.microsecond, 0)
        self.assertEqual(entry.end.microsecond, 0)


class EntryValidationTests(unittest.TestCase):
    def test_end_before_start_raises(self) -> None:
        start = datetime(2026, 1, 1, 10, 0, 0)
        end = datetime(2026, 1, 1, 9, 0, 0)
        with self.assertRaises(ValueError):
            Entry(start=start, end=end)

    def test_24h_duration_raises(self) -> None:
        start = datetime(2026, 1, 1, 10, 0, 0)
        end = start + MAX_DURATION
        with self.assertRaises(ValueError):
            Entry(start=start, end=end)

    def test_23_59_59_ok_and_round_trips_via_midnight(self) -> None:
        start = datetime(2026, 1, 1, 10, 0, 0)
        end = start + timedelta(hours=23, minutes=59, seconds=59)
        entry = Entry(start=start, end=end)
        row = entry_to_row(entry)
        self.assertEqual(row[0], "2026-01-01")
        self.assertEqual(row[1], "10:00:00")
        self.assertEqual(row[2], "09:59:59")
        reparsed = row_to_entry(row, 2)
        self.assertEqual(reparsed, entry)

    def test_tz_aware_start_raises(self) -> None:
        start = datetime(2026, 1, 1, 10, 0, 0, tzinfo=timezone.utc)
        with self.assertRaises(ValueError):
            Entry(start=start)

    def test_tz_aware_end_raises(self) -> None:
        start = datetime(2026, 1, 1, 10, 0, 0)
        end = datetime(2026, 1, 1, 11, 0, 0, tzinfo=timezone.utc)
        with self.assertRaises(ValueError):
            Entry(start=start, end=end)


class ReadEmptyMissingTests(TempDirTestCase):
    def test_missing_file_returns_empty(self) -> None:
        self.assertEqual(read_entries(self.csv_path("nope.csv")), [])

    def test_empty_file_returns_empty(self) -> None:
        path = self.csv_path()
        path.write_text("", encoding="utf-8")
        self.assertEqual(read_entries(path), [])

    def test_header_only_returns_empty(self) -> None:
        path = self.csv_path()
        path.write_text("date,start,end,duration_min\n", encoding="utf-8")
        self.assertEqual(read_entries(path), [])


class BlankLinesAndLineNumbersTests(TempDirTestCase):
    def test_blank_lines_skipped_and_line_numbers_correct(self) -> None:
        path = self.csv_path()
        path.write_text(
            "date,start,end,duration_min\n"
            "\n"
            "2026-01-01,8:07:00,,\n",
            encoding="utf-8",
        )
        with self.assertRaises(StoreError) as ctx:
            read_entries(path)
        self.assertIn("line 3", str(ctx.exception))


class ReadErrorTests(TempDirTestCase):
    def _write(self, content: str) -> Path:
        path = self.csv_path()
        path.write_text(content, encoding="utf-8")
        return path

    def assert_store_error_line(self, content: str, line: int) -> None:
        path = self._write(content)
        with self.assertRaises(StoreError) as ctx:
            read_entries(path)
        self.assertIn(f"line {line}", str(ctx.exception))

    def test_wrong_header(self) -> None:
        self.assert_store_error_line("wrong,header,here,x\n", 1)

    def test_wrong_field_count(self) -> None:
        self.assert_store_error_line(
            "date,start,end,duration_min\n2026-01-01,08:00:00,09:00:00\n", 2
        )

    def test_bad_date(self) -> None:
        self.assert_store_error_line(
            "date,start,end,duration_min\n2026-9-22,08:00:00,,\n", 2
        )

    def test_bad_time_format(self) -> None:
        self.assert_store_error_line(
            "date,start,end,duration_min\n2026-01-01,8:07:00,,\n", 2
        )

    def test_invalid_time_value(self) -> None:
        self.assert_store_error_line(
            "date,start,end,duration_min\n2026-01-01,25:00:00,,\n", 2
        )

    def test_end_present_duration_empty(self) -> None:
        self.assert_store_error_line(
            "date,start,end,duration_min\n2026-01-01,08:00:00,09:00:00,\n", 2
        )

    def test_duration_present_end_empty(self) -> None:
        self.assert_store_error_line(
            "date,start,end,duration_min\n2026-01-01,08:00:00,,60\n", 2
        )

    def test_duration_negative(self) -> None:
        self.assert_store_error_line(
            "date,start,end,duration_min\n2026-01-01,08:00:00,09:00:00,-5\n", 2
        )

    def test_duration_non_numeric(self) -> None:
        self.assert_store_error_line(
            "date,start,end,duration_min\n2026-01-01,08:00:00,09:00:00,abc\n", 2
        )

    def test_running_row_not_last(self) -> None:
        self.assert_store_error_line(
            "date,start,end,duration_min\n"
            "2026-01-01,08:00:00,,\n"
            "2026-01-02,08:00:00,09:00:00,60\n",
            3,
        )


class WriteEntriesTests(TempDirTestCase):
    def test_sorts_by_start(self) -> None:
        path = self.csv_path()
        e1 = Entry(start=datetime(2026, 1, 2, 8, 0, 0), end=datetime(2026, 1, 2, 9, 0, 0))
        e2 = Entry(start=datetime(2026, 1, 1, 8, 0, 0), end=datetime(2026, 1, 1, 9, 0, 0))
        write_entries(path, [e1, e2])
        entries = read_entries(path)
        self.assertEqual(entries, [e2, e1])

    def test_two_running_raises(self) -> None:
        e1 = Entry(start=datetime(2026, 1, 1, 8, 0, 0))
        e2 = Entry(start=datetime(2026, 1, 2, 8, 0, 0))
        with self.assertRaises(ValueError):
            write_entries(self.csv_path(), [e1, e2])

    def test_running_not_last_after_sort_raises(self) -> None:
        running = Entry(start=datetime(2026, 1, 1, 8, 0, 0))
        later = Entry(
            start=datetime(2026, 1, 2, 8, 0, 0), end=datetime(2026, 1, 2, 9, 0, 0)
        )
        with self.assertRaises(ValueError):
            write_entries(self.csv_path(), [running, later])

    def test_creates_missing_parent_dirs(self) -> None:
        path = self.tmp_path / "nested" / "dir" / "worktime.csv"
        write_entries(path, [])
        self.assertTrue(path.exists())

    def test_no_tmp_files_left_behind(self) -> None:
        path = self.csv_path()
        write_entries(path, [Entry(start=datetime(2026, 1, 1, 8, 0, 0))])
        leftovers = list(self.tmp_path.glob("*.tmp"))
        self.assertEqual(leftovers, [])

    def test_file_mode_0600(self) -> None:
        path = self.csv_path()
        write_entries(path, [])
        mode = path.stat().st_mode & 0o777
        self.assertEqual(mode, 0o600)

    def test_failure_removes_tmp_and_keeps_original(self) -> None:
        path = self.csv_path()
        original_entry = Entry(start=datetime(2026, 1, 1, 8, 0, 0), end=datetime(2026, 1, 1, 9, 0, 0))
        write_entries(path, [original_entry])
        original_bytes = path.read_bytes()

        import worktime.store as store_mod

        real_replace = os.replace

        def failing_replace(*args, **kwargs):
            raise OSError("boom")

        store_mod.os.replace = failing_replace
        try:
            with self.assertRaises(OSError):
                write_entries(
                    path,
                    [Entry(start=datetime(2026, 1, 2, 8, 0, 0), end=datetime(2026, 1, 2, 9, 0, 0))],
                )
        finally:
            store_mod.os.replace = real_replace

        self.assertEqual(path.read_bytes(), original_bytes)
        leftovers = list(self.tmp_path.glob("*.tmp"))
        self.assertEqual(leftovers, [])


class LockedTests(TempDirTestCase):
    def test_second_lock_times_out(self) -> None:
        path = self.csv_path()
        with locked(path, timeout=5.0):
            start = time.monotonic()
            with self.assertRaises(StoreError):
                with locked(path, timeout=0.2):
                    pass
            elapsed = time.monotonic() - start
            self.assertGreaterEqual(elapsed, 0.2)

    def test_lock_succeeds_after_release(self) -> None:
        path = self.csv_path()
        with locked(path, timeout=1.0):
            pass
        with locked(path, timeout=1.0):
            pass  # should not raise / should not block significantly

    def test_lock_blocks_concurrent_thread(self) -> None:
        path = self.csv_path()
        acquired_order = []
        lock_released = threading.Event()

        def worker():
            with locked(path, timeout=5.0):
                acquired_order.append("thread")

        with locked(path, timeout=5.0):
            acquired_order.append("main")
            t = threading.Thread(target=worker)
            t.start()
            time.sleep(0.1)
            # thread should still be blocked waiting for the lock
            self.assertEqual(acquired_order, ["main"])
        t.join(timeout=5.0)
        self.assertEqual(acquired_order, ["main", "thread"])


class UpdateEntriesTests(TempDirTestCase):
    def test_appends_entry_and_persists(self) -> None:
        path = self.csv_path()
        write_entries(path, [])

        def add_entry(entries: list[Entry]) -> list[Entry]:
            entries.append(Entry(start=datetime(2026, 1, 1, 8, 0, 0)))
            return entries

        result = update_entries(path, add_entry)
        self.assertEqual(len(result), 1)
        self.assertTrue(result[0].running)

        reread = read_entries(path)
        self.assertEqual(reread, result)

    def test_fn_raising_leaves_file_unchanged(self) -> None:
        path = self.csv_path()
        original = [Entry(start=datetime(2026, 1, 1, 8, 0, 0), end=datetime(2026, 1, 1, 9, 0, 0))]
        write_entries(path, original)
        original_bytes = path.read_bytes()

        def boom(entries: list[Entry]) -> list[Entry]:
            raise RuntimeError("nope")

        with self.assertRaises(RuntimeError):
            update_entries(path, boom)

        self.assertEqual(path.read_bytes(), original_bytes)


if __name__ == "__main__":
    unittest.main()
