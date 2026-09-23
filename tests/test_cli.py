"""Tests for the bin/worktime CLI wrapper."""

from __future__ import annotations

import http.server
import json
import os
import signal
import socket
import subprocess
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from pathlib import Path

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


if __name__ == "__main__":
    unittest.main()
