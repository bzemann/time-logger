"""Permanent end-to-end test of the whole daily flow through the real
``bin/worktime`` launcher.

This exercises the whole stack (CLI subprocess -> session/store -> server
-> report) against a temporary config and a reproducible demo CSV, in one
ordered scenario: status, start, start-again, dashboard (server autostart),
the JSON API and the static dashboard over real HTTP, stop, stop-again, a
current-period report, catch-up reports for the previous week/month, and
stop-server. It never touches Basil's real data file.
"""

from __future__ import annotations

import json
import os
import signal
import socket
import subprocess
import sys
import tempfile
import time
import unittest
import urllib.error
import urllib.request
from datetime import date
from pathlib import Path

from worktime import report, stats, store

REPO_ROOT = Path(__file__).resolve().parents[1]
WORKTIME_BIN = REPO_ROOT / "bin" / "worktime"
MAKE_DEMO_DATA = REPO_ROOT / "tools" / "make_demo_data.py"


def _free_port() -> int:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


class DailyFlowEndToEnd(unittest.TestCase):
    """One ordered scenario covering the whole daily flow end-to-end."""

    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmpdir.cleanup)
        self.tmp_path = Path(self._tmpdir.name)

        self.port = _free_port()
        self.data_file = self.tmp_path / "data" / "worktime.csv"
        self.reports_dir = self.data_file.parent / "reports"
        self.pid_file = self.data_file.parent / "server.pid"

        cfg_path = self.tmp_path / "config.toml"
        cfg_path.write_text(
            f'data_file = "{self.data_file}"\n'
            f"port = {self.port}\n"
            f'daily_target = "8:30"\n',
            encoding="utf-8",
        )

        # Reproducible demo data: 45 days of history, no running session, so
        # the previous week/month both have real data for the catch-up step.
        gen = subprocess.run(
            [
                sys.executable,
                str(MAKE_DEMO_DATA),
                str(self.data_file),
                "--days",
                "45",
                "--no-running",
                "--seed",
                "7",
            ],
            capture_output=True,
            text=True,
            timeout=30,
        )
        self.assertEqual(
            gen.returncode,
            0,
            f"make_demo_data.py failed\nstdout:\n{gen.stdout}\nstderr:\n{gen.stderr}",
        )

        self.env = os.environ.copy()
        self.env["WORKTIME_CONFIG"] = str(cfg_path)
        self.env["WORKTIME_NO_NOTIFY"] = "1"
        self.env["WORKTIME_NO_BROWSER"] = "1"
        self.env["WORKTIME_NO_AUTO_REPORTS"] = "1"

        # Never leave a server running, even if the test fails partway.
        self.addCleanup(self._kill_server_if_running)

        # The test must never modify Basil's real data file.
        self.real_data_path = Path(
            os.path.expanduser("~/.local/share/worktime/worktime.csv")
        )
        self.real_data_existed = self.real_data_path.exists()
        self.real_data_mtime = (
            self.real_data_path.stat().st_mtime if self.real_data_existed else None
        )

    def _kill_server_if_running(self):
        if self.pid_file.exists():
            try:
                pid = int(self.pid_file.read_text().strip())
            except (ValueError, OSError):
                return
            try:
                os.kill(pid, signal.SIGTERM)
            except ProcessLookupError:
                pass

    # -- helpers ----------------------------------------------------------

    def run_cli(self, *args):
        return subprocess.run(
            [str(WORKTIME_BIN), *args],
            env=self.env,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=30,
        )

    def _ctx(self, r, label):
        return f"{label} failed\nstdout:\n{r.stdout}\nstderr:\n{r.stderr}"

    def _assert_rc(self, r, expected, label):
        self.assertEqual(r.returncode, expected, self._ctx(r, label))

    # -- the scenario -------------------------------------------------------

    def test_full_daily_flow(self):
        # 1. status: nothing running yet.
        r = self.run_cli("status")
        self._assert_rc(r, 0, "status")
        self.assertTrue(
            r.stdout.startswith("Not running."), self._ctx(r, "status")
        )

        # 2. start: begins a session.
        r = self.run_cli("start")
        self._assert_rc(r, 0, "start")
        self.assertTrue(r.stdout.startswith("Started at"), self._ctx(r, "start"))
        entries = store.read_entries(self.data_file)
        self.assertTrue(entries, "no entries after start")
        self.assertTrue(entries[-1].running, "last entry should be running after start")

        # 3. start again: reports the running session instead of starting a new one.
        r = self.run_cli("start")
        self._assert_rc(r, 0, "start (again)")
        self.assertTrue(
            r.stdout.startswith("Currently running:"), self._ctx(r, "start (again)")
        )

        # 4. dashboard: starts the background server and prints its URL.
        r = self.run_cli("dashboard")
        self._assert_rc(r, 0, "dashboard")
        self.assertIn(
            f"http://127.0.0.1:{self.port}/", r.stdout, self._ctx(r, "dashboard")
        )
        self.assertTrue(
            self.pid_file.exists(), "server.pid missing after dashboard start"
        )

        # 5. the running server, over real HTTP.
        health_url = f"http://127.0.0.1:{self.port}/api/health"
        with urllib.request.urlopen(health_url, timeout=5) as resp:
            health = json_load(resp)
        self.assertEqual(health.get("app"), "worktime", health)

        dash_url = (
            f"http://127.0.0.1:{self.port}/api/dashboard?range=30d&unit=week"
        )
        with urllib.request.urlopen(dash_url, timeout=5) as resp:
            dash = json_load(resp)
        self.assertTrue(dash["status"]["running"], dash["status"])
        self.assertTrue(dash["entries"], "no entries in dashboard JSON")
        self.assertTrue(dash["entries"][0]["running"], dash["entries"][0])
        self.assertEqual(len(dash["days"]), 30, dash["days"])
        for key in ("today", "week", "month", "total"):
            self.assertIn(key, dash["overview"], dash["overview"].keys())

        root_url = f"http://127.0.0.1:{self.port}/"
        with urllib.request.urlopen(root_url, timeout=5) as resp:
            self.assertEqual(resp.status, 200)
            html = resp.read().decode("utf-8")
        self.assertIn("WorkTime", html)

        # 6. stop: ends the session; stopping again reports nothing running.
        r = self.run_cli("stop")
        self._assert_rc(r, 0, "stop")
        self.assertTrue(r.stdout.startswith("Stopped:"), self._ctx(r, "stop"))
        entries = store.read_entries(self.data_file)
        self.assertFalse(entries[-1].running, "last entry still running after stop")

        r = self.run_cli("stop")
        self._assert_rc(r, 1, "stop (again)")
        self.assertIn("No session running", r.stdout, self._ctx(r, "stop (again)"))

        # 7. the dashboard JSON reflects the stopped session.
        with urllib.request.urlopen(dash_url, timeout=5) as resp:
            dash = json_load(resp)
        self.assertFalse(dash["status"]["running"], dash["status"])

        # 8. report week (current, in-progress period).
        r = self.run_cli("report", "week", "--no-open")
        self._assert_rc(r, 0, "report week")
        current_week = report.period_for("week", date.today())
        expected_name = f"{current_week.stem}.html"
        self.assertTrue(
            r.stdout.strip().endswith(expected_name), self._ctx(r, "report week")
        )
        current_week_path = self.reports_dir / expected_name
        self.assertTrue(current_week_path.exists())
        # The current period is never final: today is never after its own
        # end date, so `report week` (no --last/--date) always yields
        # "in-progress" here.
        self.assertEqual(report.read_status(current_week_path), "in-progress")

        # 9. report catch-up: the previous complete week/month.
        all_entries = store.read_entries(self.data_file)
        tracking_start = stats.tracking_start(all_entries)
        self.assertIsNotNone(tracking_start, "no entries to compute tracking_start")

        week_prev = report.previous_period("week", date.today())
        month_prev = report.previous_period("month", date.today())
        # 45 days of demo history easily covers the previous complete week;
        # the previous complete month is only covered if today isn't very
        # early in the current month (in which case the 45-day window
        # wouldn't reach back into it). Assert what the demo data actually
        # guarantees, and skip the month check otherwise rather than assume.
        self.assertGreaterEqual(
            week_prev.end,
            tracking_start,
            "demo data (45 days) should cover the previous complete week",
        )
        check_month = month_prev.end >= tracking_start

        r = self.run_cli("report", "catch-up")
        self._assert_rc(r, 0, "report catch-up")

        week_prev_path = self.reports_dir / f"{week_prev.stem}.html"
        self.assertTrue(week_prev_path.exists(), self._ctx(r, "report catch-up (week)"))
        self.assertEqual(report.read_status(week_prev_path), "final")

        if check_month:
            month_prev_path = self.reports_dir / f"{month_prev.stem}.html"
            self.assertTrue(
                month_prev_path.exists(), self._ctx(r, "report catch-up (month)")
            )
            self.assertEqual(report.read_status(month_prev_path), "final")
        else:
            month_prev_path = None

        r = self.run_cli("report", "catch-up")
        self._assert_rc(r, 0, "report catch-up (second run)")
        self.assertEqual(r.stdout.strip(), "No reports due.")

        # 10. report HTML sanity: no inline script, contains the period title.
        sanity_paths = [
            (current_week_path, current_week.title),
            (week_prev_path, week_prev.title),
        ]
        if check_month:
            sanity_paths.append((month_prev_path, month_prev.title))
        for path, title in sanity_paths:
            content = path.read_text(encoding="utf-8")
            self.assertNotIn("<script", content, path)
            self.assertIn(title, content, path)

        # 11. stop-server: stops the background server.
        r = self.run_cli("stop-server")
        self._assert_rc(r, 0, "stop-server")
        self.assertEqual(r.stdout.strip(), "Server stopped.")
        self.assertFalse(self.pid_file.exists(), "server.pid still present after stop")

        with self.assertRaises(urllib.error.URLError):
            urllib.request.urlopen(health_url, timeout=5)

        # Finally: the real data file was never touched by any of this.
        if self.real_data_existed:
            self.assertTrue(self.real_data_path.exists())
            self.assertEqual(
                self.real_data_path.stat().st_mtime,
                self.real_data_mtime,
                "the real worktime.csv was modified by the test",
            )
        else:
            self.assertFalse(
                self.real_data_path.exists(),
                "the real worktime.csv was created by the test",
            )


def json_load(resp):
    return json.loads(resp.read())


if __name__ == "__main__":
    unittest.main()
