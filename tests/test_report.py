import dataclasses
import re
import tempfile
import unittest
import xml.etree.ElementTree as ET
from datetime import date, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

from worktime import report
from worktime.store import Entry, write_entries

WORKDAYS = frozenset({0, 1, 2, 3, 4})


def make_cfg(tmpdir, daily_target_min=510, workdays=WORKDAYS, days_off=frozenset()):
    tmpdir = Path(tmpdir)
    return SimpleNamespace(
        data_file=tmpdir / "wt.csv",
        reports_dir=tmpdir / "reports",
        daily_target_min=daily_target_min,
        workdays=workdays,
        days_off=days_off,
    )


class PeriodForTests(unittest.TestCase):
    def test_week(self):
        p = report.period_for("week", date(2026, 9, 23))
        self.assertEqual(p.start, date(2026, 9, 21))
        self.assertEqual(p.end, date(2026, 9, 27))
        self.assertEqual(p.title, "Week 39, 2026")
        self.assertEqual(p.stem, "week-2026-W39")
        self.assertEqual(p.kind, "week")

    def test_week_iso_edge(self):
        d1 = date(2026, 12, 31)
        iso_year, iso_week, _ = d1.isocalendar()
        p1 = report.period_for("week", d1)
        self.assertEqual(p1.stem, f"week-{iso_year}-W{iso_week:02d}")
        self.assertEqual(p1.title, f"Week {iso_week}, {iso_year}")

        d2 = date(2027, 1, 1)
        p2 = report.period_for("week", d2)
        self.assertEqual(p1, p2)

    def test_month(self):
        p = report.period_for("month", date(2026, 2, 10))
        self.assertEqual(p.start, date(2026, 2, 1))
        self.assertEqual(p.end, date(2026, 2, 28))
        self.assertEqual(p.title, "February 2026")
        self.assertEqual(p.stem, "month-2026-02")

    def test_unknown_kind(self):
        with self.assertRaises(ValueError):
            report.period_for("year", date(2026, 1, 1))


class PreviousPeriodTests(unittest.TestCase):
    def test_week(self):
        p = report.previous_period("week", date(2026, 9, 28))
        self.assertEqual(p.stem, "week-2026-W39")

    def test_month(self):
        p = report.previous_period("month", date(2026, 1, 15))
        self.assertEqual(p.title, "December 2025")


SCREENSHOT_ENTRIES = [
    Entry(datetime(2026, 9, 21, 8, 0), datetime(2026, 9, 21, 18, 0)),
    Entry(datetime(2026, 9, 22, 7, 0), datetime(2026, 9, 22, 18, 43)),
    Entry(datetime(2026, 9, 23, 8, 45), datetime(2026, 9, 23, 12, 1)),
    Entry(datetime(2026, 9, 23, 13, 10), datetime(2026, 9, 23, 13, 52)),
    Entry(datetime(2026, 9, 23, 13, 52), datetime(2026, 9, 23, 15, 1)),
    Entry(datetime(2026, 9, 23, 15, 1)),  # running
]

NOW = datetime(2026, 9, 23, 17, 3)


class BuildReportTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.cfg = make_cfg(self.tmp.name, daily_target_min=504)
        self.period = report.period_for("week", date(2026, 9, 23))

    def test_screenshot_scenario(self):
        r = report.build_report(SCREENSHOT_ENTRIES, NOW, self.cfg, self.period)

        self.assertEqual(r.summary.balance, timedelta(hours=3, minutes=40))
        self.assertFalse(r.final)
        self.assertIsNotNone(r.running)
        self.assertEqual(len(r.days), 7)

        future_dates = {d.date for d in r.days if d.future}
        self.assertEqual(
            future_dates,
            {date(2026, 9, 24), date(2026, 9, 25), date(2026, 9, 26), date(2026, 9, 27)},
        )

        self.assertEqual(len(r.cumulative), 3)
        last = r.cumulative[-1]
        self.assertEqual(last[0], date(2026, 9, 23))
        self.assertEqual(last[1], timedelta(hours=28, minutes=52))
        self.assertEqual(last[2], timedelta(hours=25, minutes=12))


class RenderHtmlTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.cfg = make_cfg(self.tmp.name, daily_target_min=504)
        self.period = report.period_for("week", date(2026, 9, 23))

    def _assert_svgs_parse(self, html_text):
        fragments = re.findall(r"<svg.*?</svg>", html_text, re.DOTALL)
        self.assertTrue(fragments, "expected at least one svg fragment")
        for frag in fragments:
            ET.fromstring(frag)  # raises on invalid XML

    def test_in_progress(self):
        r = report.build_report(SCREENSHOT_ENTRIES, NOW, self.cfg, self.period)
        html_text = report.render_html(r)

        self.assertIn("Week 39, 2026", html_text)
        self.assertIn("+3:40", html_text)
        self.assertIn('content="in-progress"', html_text[:4096])
        self.assertIn("In progress", html_text)
        self.assertIn("Includes the running session", html_text)
        self.assertNotIn("<script", html_text)

        http_occurrences = re.findall(r"http[^\s\"']*", html_text)
        for occ in http_occurrences:
            self.assertEqual(occ.rstrip('"'), "http://www.w3.org/2000/svg")

        self._assert_svgs_parse(html_text)
        self.assertIn("Total", html_text)
        self.assertIn("<table", html_text)

    def test_final(self):
        entries = list(SCREENSHOT_ENTRIES[:-1]) + [
            Entry(datetime(2026, 9, 23, 15, 1), datetime(2026, 9, 23, 16, 25))
        ]
        now = datetime(2026, 9, 28, 10, 0)
        r = report.build_report(entries, now, self.cfg, self.period)
        html_text = report.render_html(r)

        self.assertIn('content="final"', html_text[:4096])
        self.assertNotIn("In progress", html_text)

    def test_escaping(self):
        r = report.build_report(SCREENSHOT_ENTRIES, NOW, self.cfg, self.period)
        evil_period = dataclasses.replace(r.period, title="<b>x&y</b>")
        r2 = dataclasses.replace(r, period=evil_period)
        html_text = report.render_html(r2)

        self.assertIn("&lt;b&gt;x&amp;y&lt;/b&gt;", html_text)
        self.assertNotIn("<b>x&y</b>", html_text)

    def test_empty_period(self):
        period = report.period_for("week", date(2026, 1, 5))
        r = report.build_report([], NOW, self.cfg, period)
        html_text = report.render_html(r)

        self.assertIn("No sessions in this period.", html_text)
        self.assertNotIn("<svg", html_text)


class NiceScaleTests(unittest.TestCase):
    def test_cases(self):
        self.assertEqual(report.nice_scale(7.2), (8, 2))
        self.assertEqual(report.nice_scale(10.5), (12, 4))
        self.assertEqual(report.nice_scale(0), (1.0, 0.5))
        self.assertEqual(report.nice_scale(42.5), (50, 10))


class FormatTests(unittest.TestCase):
    def test_fmt_balance(self):
        self.assertEqual(report.fmt_balance(timedelta(0)), "±0:00")
        self.assertEqual(report.fmt_balance(timedelta(seconds=13200)), "+3:40")
        self.assertEqual(report.fmt_balance(timedelta(seconds=-4500)), "−1:15")

    def test_fmt_duration(self):
        self.assertEqual(report.fmt_duration(timedelta(seconds=25740)), "7h 09m")
        self.assertEqual(report.fmt_duration(timedelta(seconds=0)), "0m")
        self.assertEqual(report.fmt_duration(timedelta(seconds=-100)), "0m")


class WriteReportTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.cfg = make_cfg(self.tmp.name, daily_target_min=504)
        write_entries(self.cfg.data_file, SCREENSHOT_ENTRIES)
        self.period = report.period_for("week", date(2026, 9, 23))

    def test_write_and_status(self):
        path = report.write_report(self.cfg, self.period, NOW)
        self.assertEqual(path, self.cfg.reports_dir / "week-2026-W39.html")
        self.assertTrue(path.exists())

        tmp_files = list(self.cfg.reports_dir.glob("*.tmp"))
        self.assertEqual(tmp_files, [])

        self.assertEqual(report.read_status(path), "in-progress")

    def test_overwrite(self):
        report.write_report(self.cfg, self.period, NOW)
        later = datetime(2026, 9, 28, 10, 0)
        path = report.write_report(self.cfg, self.period, later)
        self.assertEqual(report.read_status(path), "final")

        tmp_files = list(self.cfg.reports_dir.glob("*.tmp"))
        self.assertEqual(tmp_files, [])


class ReadStatusTests(unittest.TestCase):
    def test_missing(self):
        self.assertIsNone(report.read_status(Path("/nonexistent/path/x.html")))

    def test_junk(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        p = Path(tmp.name) / "junk.html"
        p.write_text("not a worktime report at all", encoding="utf-8")
        self.assertIsNone(report.read_status(p))


class DueReportsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.cfg = make_cfg(self.tmp.name, daily_target_min=504)
        self.now = datetime(2026, 9, 28, 9, 0)

    def test_no_entries(self):
        write_entries(self.cfg.data_file, [])
        self.assertEqual(report.due_reports(self.cfg, self.now), [])

    def test_due_and_lifecycle(self):
        entries = [Entry(datetime(2026, 7, 1, 9, 0), datetime(2026, 7, 1, 17, 0))]
        write_entries(self.cfg.data_file, entries)

        due = report.due_reports(self.cfg, self.now)
        self.assertEqual(len(due), 2)
        self.assertEqual(due[0].stem, "week-2026-W39")
        self.assertEqual(due[1].stem, "month-2026-08")

        # Write a final report for W39 (now after the period).
        week_period = due[0]
        report.write_report(self.cfg, week_period, datetime(2026, 9, 28, 10, 0))

        due2 = report.due_reports(self.cfg, self.now)
        self.assertEqual([p.stem for p in due2], ["month-2026-08"])

        # In-progress report for August: still due.
        month_period = due[1]
        report.write_report(self.cfg, month_period, datetime(2026, 8, 20, 12, 0))

        due3 = report.due_reports(self.cfg, self.now)
        self.assertEqual([p.stem for p in due3], ["month-2026-08"])

    def test_excluded_before_tracking_start(self):
        entries = [Entry(datetime(2026, 9, 28, 9, 0), datetime(2026, 9, 28, 10, 0))]
        write_entries(self.cfg.data_file, entries)

        due = report.due_reports(self.cfg, self.now)
        self.assertEqual(due, [])


if __name__ == "__main__":
    unittest.main()
