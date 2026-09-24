"""Tests for the bin/worktime CLI wrapper."""

from __future__ import annotations

import http.server
import json
import os
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from datetime import date, datetime, timedelta
from pathlib import Path
from unittest import mock

from worktime import cli, control

REPO_ROOT = Path(__file__).resolve().parents[1]
WORKTIME_BIN = REPO_ROOT / "bin" / "worktime"
SERVER_PY = REPO_ROOT / "worktime" / "server.py"


def _free_port() -> int:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def run(args, **kwargs):
    return subprocess.run(
        [str(WORKTIME_BIN), *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        **kwargs,
    )


class VersionTests(unittest.TestCase):
    def test_version(self):
        result = run(["--version"])
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout.strip(), "worktime 0.1.0")


class ConfigCommandTests(unittest.TestCase):
    def test_config_with_valid_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            cfg_path = tmp_path / "config.toml"
            data_file = tmp_path / "data" / "worktime.csv"
            cfg_path.write_text(
                f'daily_target = "8:24"\n'
                f'data_file = "{data_file}"\n',
                encoding="utf-8",
            )
            env = dict(os.environ)
            env["WORKTIME_CONFIG"] = str(cfg_path)

            result = run(["config"], cwd=str(tmp_path), env=env)

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("daily target: 8:24", result.stdout)
        self.assertIn(str(data_file), result.stdout)

    def test_config_with_invalid_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg_path = Path(tmp) / "config.toml"
            cfg_path.write_text("not valid toml [[[", encoding="utf-8")
            env = dict(os.environ)
            env["WORKTIME_CONFIG"] = str(cfg_path)

            result = run(["config"], env=env)

        self.assertEqual(result.returncode, 1)
        self.assertTrue(result.stderr.startswith("worktime: "), result.stderr)

    def test_config_with_no_days_off(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            cfg_path = tmp_path / "config.toml"
            cfg_path.write_text("", encoding="utf-8")
            env = dict(os.environ)
            env["WORKTIME_CONFIG"] = str(cfg_path)

            result = run(["config"], cwd=str(tmp_path), env=env)

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("days off:     (none)", result.stdout)

    def test_config_with_days_off_range(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            cfg_path = tmp_path / "config.toml"
            cfg_path.write_text(
                'days_off = ["2026-12-24..2027-01-02"]\n',
                encoding="utf-8",
            )
            env = dict(os.environ)
            env["WORKTIME_CONFIG"] = str(cfg_path)

            result = run(["config"], cwd=str(tmp_path), env=env)

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("days off:     10 days (2026-12-24 … 2027-01-02)", result.stdout)

    def test_config_with_single_days_off(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            cfg_path = tmp_path / "config.toml"
            cfg_path.write_text(
                'days_off = ["2027-01-02"]\n',
                encoding="utf-8",
            )
            env = dict(os.environ)
            env["WORKTIME_CONFIG"] = str(cfg_path)

            result = run(["config"], cwd=str(tmp_path), env=env)

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("days off:     1 day (2027-01-02)", result.stdout)


class NoCommandTests(unittest.TestCase):
    def test_no_command_exits_2(self):
        result = run([])
        self.assertEqual(result.returncode, 2)


class SymlinkInvocationTests(unittest.TestCase):
    def test_invoked_through_symlink(self):
        with tempfile.TemporaryDirectory() as tmp:
            link = Path(tmp) / "worktime-link"
            link.symlink_to(WORKTIME_BIN)
            result = subprocess.run(
                [str(link), "--version"], capture_output=True, text=True
            )
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout.strip(), "worktime 0.1.0")


class MinimalPathTests(unittest.TestCase):
    def test_minimal_path_finds_python(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg_path = Path(tmp) / "config.toml"
            env = {
                "HOME": tmp,
                "PATH": "/usr/bin:/bin",
                "WORKTIME_CONFIG": str(cfg_path),
            }
            result = subprocess.run(
                [str(WORKTIME_BIN), "--version"],
                capture_output=True,
                text=True,
                env=env,
            )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), "worktime 0.1.0")


class SessionCommandTests(unittest.TestCase):
    def _env(self, tmp_path: Path):
        cfg_path = tmp_path / "config.toml"
        data_file = tmp_path / "data" / "worktime.csv"
        cfg_path.write_text(f'data_file = "{data_file}"\n', encoding="utf-8")
        env = dict(os.environ)
        env["WORKTIME_CONFIG"] = str(cfg_path)
        env["WORKTIME_NO_NOTIFY"] = "1"
        env["WORKTIME_NO_AUTO_REPORTS"] = "1"
        return env, data_file

    def test_start_then_start_again(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            env, data_file = self._env(tmp_path)

            result = run(["start"], env=env)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertTrue(result.stdout.startswith("Started at"), result.stdout)
            self.assertTrue(data_file.exists())

            result = run(["start"], env=env)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertTrue(
                result.stdout.startswith("Currently running: 0m"), result.stdout
            )

    def test_status_running(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            env, data_file = self._env(tmp_path)

            run(["start"], env=env)
            result = run(["status"], env=env)

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertTrue(result.stdout.startswith("Running since"), result.stdout)
            self.assertIn("Target: 8:30", result.stdout)

    def test_status_not_running(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            env, data_file = self._env(tmp_path)

            result = run(["status"], env=env)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertTrue(result.stdout.startswith("Not running."), result.stdout)

    def test_stop_then_stop_again(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            env, data_file = self._env(tmp_path)

            run(["start"], env=env)
            result = run(["stop"], env=env)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertTrue(result.stdout.startswith("Stopped: 0m"), result.stdout)

            result = run(["stop"], env=env)
            self.assertEqual(result.returncode, 1)
            self.assertTrue(
                result.stdout.startswith("No session running"), result.stdout
            )

    def test_start_with_broken_csv_header(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            env, data_file = self._env(tmp_path)
            data_file.parent.mkdir(parents=True, exist_ok=True)
            data_file.write_text("not,the,right,header\nfoo\n", encoding="utf-8")

            result = run(["start"], env=env)
            self.assertEqual(result.returncode, 1)
            self.assertTrue(result.stderr.startswith("worktime: "), result.stderr)
            self.assertIn("line 1", result.stderr)

    def test_start_at_invalid_time(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            env, data_file = self._env(tmp_path)

            result = run(["start", "--at", "25:00"], env=env)
            self.assertEqual(result.returncode, 1)
            self.assertIn("invalid time", result.stderr)


class StatusShortTests(unittest.TestCase):
    """Tests for `worktime status --short` (machine-readable, for status bars)."""

    def _env(self, tmp_path: Path):
        cfg_path = tmp_path / "config.toml"
        data_file = tmp_path / "data" / "worktime.csv"
        cfg_path.write_text(f'data_file = "{data_file}"\n', encoding="utf-8")
        env = dict(os.environ)
        env["WORKTIME_CONFIG"] = str(cfg_path)
        env["WORKTIME_NO_NOTIFY"] = "1"
        env["WORKTIME_NO_AUTO_REPORTS"] = "1"
        return env, data_file

    def test_short_no_csv(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            env, data_file = self._env(tmp_path)

            result = run(["status", "--short"], env=env)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stdout, "\n")

    def test_short_running(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            env, data_file = self._env(tmp_path)
            data_file.parent.mkdir(parents=True, exist_ok=True)

            now = datetime.now()
            start = now - timedelta(minutes=90)
            if start.date() != now.date():
                # Avoid crossing midnight, which would change the expected
                # elapsed formatting; fall back to a shorter offset.
                start = now - timedelta(minutes=30)
                expected_re = r"^(29|30|31)m\n$"
            else:
                expected_re = r"^1h (29|30|31)m\n$"
            data_file.write_text(
                "date,start,end,duration_min\n"
                f"{start.date().isoformat()},{start:%H:%M:%S},,\n",
                encoding="utf-8",
            )

            result = run(["status", "--short"], env=env)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertRegex(result.stdout, expected_re)

    def test_short_finished_session_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            env, data_file = self._env(tmp_path)
            data_file.parent.mkdir(parents=True, exist_ok=True)
            data_file.write_text(
                "date,start,end,duration_min\n"
                "2026-01-01,08:00:00,09:00:00,60\n",
                encoding="utf-8",
            )

            result = run(["status", "--short"], env=env)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stdout, "\n")

    def test_short_broken_csv(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            env, data_file = self._env(tmp_path)
            data_file.parent.mkdir(parents=True, exist_ok=True)
            data_file.write_text("not,the,right,header\nfoo\n", encoding="utf-8")

            result = run(["status", "--short"], env=env)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stdout, "err\n")
            self.assertTrue(result.stderr.startswith("worktime: "), result.stderr)
            self.assertIn("line 1", result.stderr)

    def test_short_invalid_config(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            cfg_path = tmp_path / "config.toml"
            cfg_path.write_text("bogus_key = 1\n", encoding="utf-8")
            env = dict(os.environ)
            env["WORKTIME_CONFIG"] = str(cfg_path)
            env["WORKTIME_NO_NOTIFY"] = "1"
            env["WORKTIME_NO_AUTO_REPORTS"] = "1"

            result = run(["status", "--short"], env=env)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stdout, "err\n")

    def test_plain_status_unaffected_by_short_flag_presence(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            env, data_file = self._env(tmp_path)

            result = run(["status"], env=env)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertTrue(result.stdout.startswith("Not running."), result.stdout)


class AutoCatchUpUnitTests(unittest.TestCase):
    """Unit tests for cli._maybe_start_catch_up, run in-process.

    ``worktime.report.due_reports`` is patched directly (rather than
    injecting a fake module via ``sys.modules``) so these tests are
    unaffected by import order: once any other test module has done
    ``from worktime import report``, that import gets cached as an
    attribute on the ``worktime`` package, which a ``sys.modules`` patch
    alone would not override.
    """

    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmpdir.cleanup)
        tmp_path = Path(self._tmpdir.name)
        self.cfg = mock.Mock()
        self.cfg.data_file = tmp_path / "data" / "worktime.csv"
        self.now = mock.Mock()

    def test_disabled_env_skips_everything(self):
        with mock.patch.dict(
            os.environ, {"WORKTIME_NO_AUTO_REPORTS": "1"}
        ), mock.patch(
            "worktime.report.due_reports", return_value=[mock.Mock()]
        ) as due_reports, mock.patch(
            "worktime.cli.subprocess.Popen"
        ) as popen:
            cli._maybe_start_catch_up(self.cfg, self.now)

        popen.assert_not_called()
        due_reports.assert_not_called()

    def test_no_due_reports_skips_popen(self):
        with mock.patch.dict(
            os.environ, {"WORKTIME_NO_AUTO_REPORTS": "0"}
        ), mock.patch(
            "worktime.report.due_reports", return_value=[]
        ) as due_reports, mock.patch(
            "worktime.cli.subprocess.Popen"
        ) as popen:
            cli._maybe_start_catch_up(self.cfg, self.now)

        popen.assert_not_called()
        due_reports.assert_called_once_with(self.cfg, self.now)

    def test_due_reports_error_is_swallowed(self):
        with mock.patch.dict(
            os.environ, {"WORKTIME_NO_AUTO_REPORTS": "0"}
        ), mock.patch(
            "worktime.report.due_reports", side_effect=RuntimeError("boom")
        ), mock.patch(
            "worktime.cli.subprocess.Popen"
        ) as popen:
            cli._maybe_start_catch_up(self.cfg, self.now)  # must not raise

        popen.assert_not_called()

    def test_due_reports_spawns_detached_catch_up(self):
        with mock.patch.dict(
            os.environ, {"WORKTIME_NO_AUTO_REPORTS": "0"}
        ), mock.patch(
            "worktime.report.due_reports", return_value=[mock.Mock()]
        ), mock.patch(
            "worktime.cli.subprocess.Popen"
        ) as popen:
            cli._maybe_start_catch_up(self.cfg, self.now)

        popen.assert_called_once()
        args, kwargs = popen.call_args
        self.assertEqual(
            args[0], [sys.executable, "-m", "worktime", "report", "catch-up"]
        )
        self.assertTrue(kwargs["start_new_session"])
        self.assertEqual(kwargs["stdin"], subprocess.DEVNULL)
        self.assertTrue(
            kwargs["env"]["PYTHONPATH"].startswith(str(control.REPO_ROOT))
        )

    def test_popen_oserror_is_swallowed(self):
        with mock.patch.dict(
            os.environ, {"WORKTIME_NO_AUTO_REPORTS": "0"}
        ), mock.patch(
            "worktime.report.due_reports", return_value=[mock.Mock()]
        ), mock.patch(
            "worktime.cli.subprocess.Popen", side_effect=OSError("no fork")
        ):
            cli._maybe_start_catch_up(self.cfg, self.now)  # must not raise


class DashboardServerTests(unittest.TestCase):
    def _env(self, tmp_path: Path, port: int):
        cfg_path = tmp_path / "config.toml"
        data_file = tmp_path / "data" / "worktime.csv"
        cfg_path.write_text(
            f'data_file = "{data_file}"\nport = {port}\n', encoding="utf-8"
        )
        env = dict(os.environ)
        env["WORKTIME_CONFIG"] = str(cfg_path)
        env["WORKTIME_NO_NOTIFY"] = "1"
        env["WORKTIME_NO_BROWSER"] = "1"
        env["WORKTIME_NO_AUTO_REPORTS"] = "1"
        return env, data_file

    def _kill_leftover_pid(self, data_file: Path):
        pid_file = data_file.parent / "server.pid"
        if pid_file.exists():
            try:
                pid = int(pid_file.read_text().strip())
                os.kill(pid, signal.SIGTERM)
            except (ValueError, OSError):
                pass

    def test_stop_server_when_nothing_running(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            port = _free_port()
            env, data_file = self._env(tmp_path, port)

            result = run(["stop-server"], env=env)

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stdout.strip(), "Server is not running.")

    def test_dashboard_start_reuse_and_stop(self):
        if not SERVER_PY.exists():
            self.skipTest("worktime/server.py does not exist yet")

        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            port = _free_port()
            env, data_file = self._env(tmp_path, port)

            try:
                result = run(["dashboard"], env=env, timeout=30)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn(f"http://127.0.0.1:{port}/", result.stdout)

                pid_file = data_file.parent / "server.pid"
                self.assertTrue(pid_file.exists())

                with urllib.request.urlopen(
                    f"http://127.0.0.1:{port}/api/health", timeout=5
                ) as resp:
                    data = json.loads(resp.read())
                self.assertEqual(data.get("app"), "worktime")

                # Calling dashboard again should reuse the running server.
                result = run(["dashboard"], env=env, timeout=30)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn(f"http://127.0.0.1:{port}/", result.stdout)

                result = run(["stop-server"], env=env, timeout=10)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(result.stdout.strip(), "Server stopped.")

                self.assertFalse(pid_file.exists())

                with self.assertRaises(urllib.error.URLError):
                    urllib.request.urlopen(
                        f"http://127.0.0.1:{port}/api/health", timeout=5
                    )
            finally:
                self._kill_leftover_pid(data_file)

    def test_dashboard_port_used_by_other_program(self):
        port = _free_port()

        class NotFoundHandler(http.server.BaseHTTPRequestHandler):
            def log_message(self, format, *args):
                pass

            def do_GET(self):
                self.send_response(404)
                self.end_headers()

        httpd = http.server.HTTPServer(("127.0.0.1", port), NotFoundHandler)
        thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        thread.start()

        try:
            with tempfile.TemporaryDirectory() as tmp:
                tmp_path = Path(tmp)
                env, data_file = self._env(tmp_path, port)

                result = run(["dashboard"], env=env, timeout=30)

                self.assertEqual(result.returncode, 1)
                self.assertIn("used by another program", result.stderr)
        finally:
            httpd.shutdown()
            httpd.server_close()
            thread.join(timeout=5)


def _write_workday_csv(data_file: Path) -> None:
    """Write finished 08:00-16:30 sessions for every weekday of the last 70 days.

    This gives the previous complete week and the previous complete month
    (and the current, in-progress week) real data to report on.
    """
    data_file.parent.mkdir(parents=True, exist_ok=True)
    lines = ["date,start,end,duration_min\n"]
    today = date.today()
    for i in range(70, 0, -1):
        day = today - timedelta(days=i)
        if day.weekday() < 5:
            lines.append(f"{day.isoformat()},08:00:00,16:30:00,510\n")
    data_file.write_text("".join(lines), encoding="utf-8")


class ReportCommandTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from worktime import report

        cls.report = report

    def _env(self, tmp_path: Path, *, auto_reports_disabled: bool = True):
        cfg_path = tmp_path / "config.toml"
        data_file = tmp_path / "data" / "worktime.csv"
        cfg_path.write_text(f'data_file = "{data_file}"\n', encoding="utf-8")
        env = dict(os.environ)
        env["WORKTIME_CONFIG"] = str(cfg_path)
        env["WORKTIME_NO_NOTIFY"] = "1"
        env["WORKTIME_NO_BROWSER"] = "1"
        if auto_reports_disabled:
            env["WORKTIME_NO_AUTO_REPORTS"] = "1"
        reports_dir = data_file.parent / "reports"
        return env, data_file, reports_dir

    def test_report_week_current(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            env, data_file, reports_dir = self._env(tmp_path)
            _write_workday_csv(data_file)

            period = self.report.period_for("week", date.today())
            result = run(["report", "week", "--no-open"], env=env)

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertTrue(
                result.stdout.strip().endswith(f"{period.stem}.html"),
                result.stdout,
            )
            self.assertTrue((reports_dir / f"{period.stem}.html").exists())

    def test_report_month_last(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            env, data_file, reports_dir = self._env(tmp_path)
            _write_workday_csv(data_file)

            period = self.report.previous_period("month", date.today())
            result = run(["report", "month", "--last", "--no-open"], env=env)

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertTrue(
                result.stdout.strip().endswith(f"{period.stem}.html"),
                result.stdout,
            )
            self.assertTrue((reports_dir / f"{period.stem}.html").exists())

    def test_report_week_with_date(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            env, data_file, reports_dir = self._env(tmp_path)
            _write_workday_csv(data_file)

            result = run(
                ["report", "week", "--date", "2026-09-23", "--no-open"], env=env
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            expected = reports_dir / "week-2026-W39.html"
            self.assertTrue(result.stdout.strip().endswith("week-2026-W39.html"))
            self.assertTrue(expected.exists())

    def test_report_last_and_date_mutually_exclusive(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            env, data_file, reports_dir = self._env(tmp_path)
            _write_workday_csv(data_file)

            result = run(["report", "week", "--last", "--date", "2026-09-23"], env=env)
            self.assertEqual(result.returncode, 2)

    def test_report_invalid_date_format(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            env, data_file, reports_dir = self._env(tmp_path)
            _write_workday_csv(data_file)

            result = run(["report", "week", "--date", "2026-9-3"], env=env)
            self.assertEqual(result.returncode, 2)

    def test_report_catch_up(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            env, data_file, reports_dir = self._env(tmp_path)
            _write_workday_csv(data_file)

            week_period = self.report.previous_period("week", date.today())
            month_period = self.report.previous_period("month", date.today())

            result = run(["report", "catch-up"], env=env)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertTrue((reports_dir / f"{week_period.stem}.html").exists())
            self.assertTrue((reports_dir / f"{month_period.stem}.html").exists())

            result = run(["report", "catch-up"], env=env)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stdout.strip(), "No reports due.")

    def test_report_catch_up_rejects_options(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            env, data_file, reports_dir = self._env(tmp_path)
            _write_workday_csv(data_file)

            result = run(["report", "catch-up", "--no-open"], env=env)
            self.assertEqual(result.returncode, 1)
            self.assertIn("takes no options", result.stderr)

    def test_auto_catch_up_on_start(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            env, data_file, reports_dir = self._env(
                tmp_path, auto_reports_disabled=False
            )
            _write_workday_csv(data_file)

            week_period = self.report.previous_period("week", date.today())
            month_period = self.report.previous_period("month", date.today())
            week_file = reports_dir / f"{week_period.stem}.html"
            month_file = reports_dir / f"{month_period.stem}.html"

            try:
                result = run(["start"], env=env)
                self.assertEqual(result.returncode, 0, result.stderr)

                deadline = time.monotonic() + 15
                while time.monotonic() < deadline:
                    if week_file.exists() and month_file.exists():
                        break
                    time.sleep(0.2)

                self.assertTrue(week_file.exists())
                self.assertTrue(month_file.exists())
            finally:
                run(["stop"], env=env)

    def test_auto_catch_up_disabled(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            env, data_file, reports_dir = self._env(
                tmp_path, auto_reports_disabled=True
            )
            _write_workday_csv(data_file)

            try:
                result = run(["start"], env=env)
                self.assertEqual(result.returncode, 0, result.stderr)

                time.sleep(2)

                html_files = (
                    list(reports_dir.glob("*.html")) if reports_dir.exists() else []
                )
                self.assertEqual(html_files, [])
            finally:
                run(["stop"], env=env)


class CatchAllErrorTests(unittest.TestCase):
    """In-process tests for the unexpected-exception catch-all in cli.main."""

    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmpdir.cleanup)
        tmp_path = Path(self._tmpdir.name)
        self.home = tmp_path / "home"
        self.home.mkdir()
        self.data_dir = tmp_path / "data"
        self.data_file = self.data_dir / "worktime.csv"
        self.cfg_path = tmp_path / "config.toml"
        self.cfg_path.write_text(f'data_file = "{self.data_file}"\n', encoding="utf-8")
        self.env = {
            "WORKTIME_CONFIG": str(self.cfg_path),
            "WORKTIME_NO_AUTO_REPORTS": "1",
            "HOME": str(self.home),
        }

    def test_start_unexpected_error_notifies_and_logs(self):
        notify_mock = mock.Mock()
        with mock.patch.dict(os.environ, self.env, clear=False), mock.patch(
            "worktime.cli.notify", notify_mock
        ), mock.patch("worktime.session.start", side_effect=RuntimeError("boom")):
            rc = cli.main(["start"])

        self.assertEqual(rc, 1)
        notify_mock.assert_called_once()
        title, body = notify_mock.call_args.args
        self.assertEqual(title, "WorkTime error")
        self.assertIn("RuntimeError: boom", body)
        self.assertIn("error.log", body)

        log_path = self.data_dir / "error.log"
        self.assertTrue(log_path.exists())
        content = log_path.read_text(encoding="utf-8")
        self.assertIn("=== ", content)
        self.assertIn("worktime start", content)
        self.assertIn("Traceback", content)
        self.assertIn("RuntimeError: boom", content)

    def test_dashboard_unexpected_error_notifies(self):
        notify_mock = mock.Mock()
        with mock.patch.dict(os.environ, self.env, clear=False), mock.patch(
            "worktime.cli.notify", notify_mock
        ), mock.patch("worktime.control.probe", side_effect=RuntimeError("boom")):
            rc = cli.main(["dashboard"])

        self.assertEqual(rc, 1)
        notify_mock.assert_called_once()

    def test_non_shortcut_command_reraises_and_does_not_notify(self):
        notify_mock = mock.Mock()
        with mock.patch.dict(os.environ, self.env, clear=False), mock.patch(
            "worktime.cli.notify", notify_mock
        ), mock.patch("worktime.session.status", side_effect=RuntimeError("boom")):
            with self.assertRaises(RuntimeError):
                cli.main(["status"])

        notify_mock.assert_not_called()

    def test_expected_store_error_unchanged(self):
        from worktime.store import StoreError

        notify_mock = mock.Mock()
        with mock.patch.dict(os.environ, self.env, clear=False), mock.patch(
            "worktime.cli.notify", notify_mock
        ), mock.patch(
            "worktime.session.start", side_effect=StoreError("bad csv")
        ):
            rc = cli.main(["start"])

        self.assertEqual(rc, 1)
        notify_mock.assert_called_once_with("WorkTime error", "bad csv")
        log_path = self.data_dir / "error.log"
        self.assertFalse(log_path.exists())

    def test_broken_config_falls_back_to_default_log_dir(self):
        notify_mock = mock.Mock()
        with mock.patch.dict(os.environ, self.env, clear=False), mock.patch(
            "worktime.cli.notify", notify_mock
        ), mock.patch(
            "worktime.cli.load_config", side_effect=RuntimeError("cfg boom")
        ):
            rc = cli.main(["start"])

        self.assertEqual(rc, 1)
        log_path = self.home / ".local" / "share" / "worktime" / "error.log"
        self.assertTrue(log_path.exists())
        self.assertIn("cfg boom", log_path.read_text(encoding="utf-8"))

    def test_long_message_is_truncated(self):
        notify_mock = mock.Mock()
        with mock.patch.dict(os.environ, self.env, clear=False), mock.patch(
            "worktime.cli.notify", notify_mock
        ), mock.patch(
            "worktime.session.start", side_effect=RuntimeError("x" * 500)
        ):
            rc = cli.main(["start"])

        self.assertEqual(rc, 1)
        notify_mock.assert_called_once()
        _, body = notify_mock.call_args.args
        self.assertRegex(body, r"RuntimeError: x+\.\.\.")
        self.assertNotIn("x" * 190, body)
        detail = body.split("Unexpected problem (", 1)[1].split(")", 1)[0]
        self.assertLessEqual(len(detail), 200)

    def test_unwritable_log_falls_back_to_message(self):
        notify_mock = mock.Mock()
        with mock.patch.dict(os.environ, self.env, clear=False), mock.patch(
            "worktime.cli.notify", notify_mock
        ), mock.patch(
            "worktime.session.start", side_effect=RuntimeError("boom")
        ), mock.patch("worktime.cli._write_error_log", return_value=None):
            rc = cli.main(["start"])

        self.assertEqual(rc, 1)
        notify_mock.assert_called_once()
        _, body = notify_mock.call_args.args
        self.assertIn("Could not write error.log", body)

    def test_readonly_data_folder_falls_back_to_default_log_dir(self):
        if os.geteuid() == 0:
            self.skipTest("root ignores directory permissions")

        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.data_dir.chmod(0o555)
        self.addCleanup(self.data_dir.chmod, 0o755)

        notify_mock = mock.Mock()
        with mock.patch.dict(os.environ, self.env, clear=False), mock.patch(
            "worktime.cli.notify", notify_mock
        ):
            # No mocking here: the read-only data folder makes the real
            # session.start -> store.locked() lock-file creation raise a
            # genuine PermissionError, which is what the catch-all (and
            # this test) is meant to survive.
            rc = cli.main(["start"])

        self.assertEqual(rc, 1)
        log_path = self.home / ".local" / "share" / "worktime" / "error.log"
        self.assertTrue(log_path.exists())
        self.assertIn("PermissionError", log_path.read_text(encoding="utf-8"))
        notify_mock.assert_called_once()
        _, body = notify_mock.call_args.args
        self.assertIn(str(log_path), body)


@unittest.skipIf(sys.platform == "win32", "POSIX sh wrapper")
class WrapperNoPythonFallbackTests(unittest.TestCase):
    """Subprocess tests for bin/worktime's no-Python-found notification."""

    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmpdir.cleanup)
        tmp_path = Path(self._tmpdir.name)

        # A copy of bin/worktime with the absolute fallback candidates
        # replaced by a nonexistent path, so no Python is ever found.
        bin_dir = tmp_path / "bin"
        bin_dir.mkdir()
        self.wrapper = bin_dir / "worktime"
        original = WORKTIME_BIN.read_text(encoding="utf-8")
        target_line = (
            "    for cand in /opt/homebrew/bin/python3 /usr/local/bin/python3 "
            "/usr/bin/python3; do\n"
        )
        self.assertIn(target_line, original, "fallback candidates line not found")
        patched = original.replace(
            target_line, "    for cand in /nonexistent/python3; do\n"
        )
        self.wrapper.write_text(patched, encoding="utf-8")
        self.wrapper.chmod(0o755)

        # A minimal PATH with only the tools the wrapper itself needs, plus
        # a fake osascript that records what it was called with, and no
        # python3 at all.
        fakebin = tmp_path / "fakebin"
        fakebin.mkdir()
        for tool in ("dirname", "readlink", "tr"):
            real = shutil.which(tool)
            self.assertIsNotNone(real, f"{tool} not found on PATH")
            (fakebin / tool).symlink_to(real)

        self.notified_file = tmp_path / "notified.txt"
        osascript = fakebin / "osascript"
        osascript.write_text(
            "#!/bin/sh\n"
            f'for a in "$@"; do printf \'%s\\n\' "$a" >> "{self.notified_file}"; done\n',
            encoding="utf-8",
        )
        osascript.chmod(0o755)

        self.home = tmp_path / "home"
        self.home.mkdir()
        self.env = {"PATH": str(fakebin), "HOME": str(self.home)}

    def test_no_python_found_sends_notification(self):
        result = subprocess.run(
            [str(self.wrapper), "--version"],
            capture_output=True,
            text=True,
            env=self.env,
        )

        self.assertEqual(result.returncode, 1)
        self.assertIn("no Python >= 3.11 found", result.stderr)
        self.assertTrue(self.notified_file.exists())
        content = self.notified_file.read_text(encoding="utf-8")
        self.assertIn("WorkTime error", content)
        self.assertIn("No Python >= 3.11 found", content)

    def test_no_python_found_respects_no_notify(self):
        env = dict(self.env)
        env["WORKTIME_NO_NOTIFY"] = "1"

        result = subprocess.run(
            [str(self.wrapper), "--version"],
            capture_output=True,
            text=True,
            env=env,
        )

        self.assertEqual(result.returncode, 1)
        self.assertFalse(self.notified_file.exists())


if __name__ == "__main__":
    unittest.main()
