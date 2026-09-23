"""Unit and HTTP tests for worktime/server.py."""

from __future__ import annotations

import http.client
import json
import os
import signal
import socket
import subprocess
import sys
import tempfile
import threading
import time
import types
import unittest
from datetime import datetime
from pathlib import Path
from unittest import mock

from worktime import __version__, server
from worktime.store import Entry

REPO_ROOT = Path(__file__).resolve().parents[1]


def _cfg(daily_target_min=504, workdays=frozenset({0, 1, 2, 3, 4}), days_off=frozenset()):
    return types.SimpleNamespace(
        daily_target_min=daily_target_min,
        workdays=workdays,
        days_off=days_off,
    )


def _screenshot_entries():
    return [
        Entry(datetime(2026, 9, 21, 8, 0, 0), datetime(2026, 9, 21, 18, 0, 0)),
        Entry(datetime(2026, 9, 22, 7, 0, 0), datetime(2026, 9, 22, 18, 43, 0)),
        Entry(datetime(2026, 9, 23, 8, 45, 0), datetime(2026, 9, 23, 12, 1, 0)),
        Entry(datetime(2026, 9, 23, 13, 10, 0), datetime(2026, 9, 23, 13, 52, 0)),
        Entry(datetime(2026, 9, 23, 13, 52, 0), datetime(2026, 9, 23, 15, 1, 0)),
        Entry(datetime(2026, 9, 23, 15, 1, 0)),
    ]


class BuildDashboardTests(unittest.TestCase):
    def setUp(self):
        self.cfg = _cfg()
        self.entries = _screenshot_entries()
        self.now = datetime(2026, 9, 23, 17, 3, 0)

    def _days_by_date(self, result):
        return {d["date"]: d for d in result["days"]}

    def test_screenshot_scenario_default_range(self):
        result = server.build_dashboard(self.entries, self.now, self.cfg, {})

        self.assertEqual(result["overview"]["week"]["balance_s"], 13200)
        self.assertEqual(result["overview"]["today"]["balance_s"], -4500)
        self.assertEqual(result["overview"]["today"]["worked_s"], 25740)

        self.assertTrue(result["status"]["running"])
        self.assertEqual(result["status"]["elapsed_s"], 7320)

        self.assertIsNone(result["entries"][0]["end"])
        self.assertTrue(result["entries"][0]["running"])
        self.assertEqual(len(result["entries"]), 6)

        self.assertEqual(result["range"]["name"], "30d")
        self.assertEqual(len(result["days"]), 30)
        self.assertEqual(result["days"][-1]["date"], "2026-09-23")

        by_date = self._days_by_date(result)
        self.assertEqual(by_date["2026-09-20"]["target_s"], 0)  # Sunday, before tracking
        self.assertEqual(by_date["2026-09-21"]["target_s"], 30240)  # Mon, 8:24

        self.assertTrue(result["trend"][-1]["label"].endswith("2026-W39"))

    def test_limit(self):
        result = server.build_dashboard(self.entries, self.now, self.cfg, {"limit": "2"})
        self.assertEqual(len(result["entries"]), 2)

    def test_custom_range(self):
        result = server.build_dashboard(
            self.entries, self.now, self.cfg, {"start": "2026-09-01", "end": "2026-09-23"}
        )
        self.assertEqual(result["range"]["name"], "custom")
        self.assertEqual(result["range"]["start"], "2026-09-01")
        self.assertEqual(result["range"]["end"], "2026-09-23")

    def test_only_start_given_is_error(self):
        with self.assertRaises(server.ApiError) as ctx:
            server.build_dashboard(self.entries, self.now, self.cfg, {"start": "2026-09-01"})
        self.assertEqual(ctx.exception.status, 400)

    def test_only_end_given_is_error(self):
        with self.assertRaises(server.ApiError) as ctx:
            server.build_dashboard(self.entries, self.now, self.cfg, {"end": "2026-09-01"})
        self.assertEqual(ctx.exception.status, 400)

    def test_bad_date_is_error(self):
        with self.assertRaises(server.ApiError) as ctx:
            server.build_dashboard(
                self.entries, self.now, self.cfg, {"start": "bad", "end": "2026-09-23"}
            )
        self.assertEqual(ctx.exception.status, 400)

    def test_start_after_end_is_error(self):
        with self.assertRaises(server.ApiError) as ctx:
            server.build_dashboard(
                self.entries,
                self.now,
                self.cfg,
                {"start": "2026-09-23", "end": "2026-09-01"},
            )
        self.assertEqual(ctx.exception.status, 400)

    def test_bad_range_is_error(self):
        with self.assertRaises(server.ApiError) as ctx:
            server.build_dashboard(self.entries, self.now, self.cfg, {"range": "5y"})
        self.assertEqual(ctx.exception.status, 400)

    def test_bad_unit_is_error(self):
        with self.assertRaises(server.ApiError) as ctx:
            server.build_dashboard(self.entries, self.now, self.cfg, {"unit": "day"})
        self.assertEqual(ctx.exception.status, 400)

    def test_bad_limit_zero(self):
        with self.assertRaises(server.ApiError) as ctx:
            server.build_dashboard(self.entries, self.now, self.cfg, {"limit": "0"})
        self.assertEqual(ctx.exception.status, 400)

    def test_bad_limit_too_large(self):
        with self.assertRaises(server.ApiError) as ctx:
            server.build_dashboard(self.entries, self.now, self.cfg, {"limit": "501"})
        self.assertEqual(ctx.exception.status, 400)

    def test_bad_limit_not_a_number(self):
        with self.assertRaises(server.ApiError) as ctx:
            server.build_dashboard(self.entries, self.now, self.cfg, {"limit": "x"})
        self.assertEqual(ctx.exception.status, 400)

    def test_json_serializable(self):
        result = server.build_dashboard(self.entries, self.now, self.cfg, {})
        json.dumps(result)  # must not raise


class DashboardHttpTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._tmpdir = tempfile.TemporaryDirectory()
        cls.tmp_path = Path(cls._tmpdir.name)
        cls.cfg_path = cls.tmp_path / "config.toml"
        cls.data_file = cls.tmp_path / "data" / "worktime.csv"
        cls.data_file.parent.mkdir(parents=True, exist_ok=True)

        cls.web_root = cls.tmp_path / "web"
        cls.web_root.mkdir()
        (cls.web_root / "index.html").write_text(
            "<html><body>INDEXCONTENT</body></html>", encoding="utf-8"
        )
        (cls.web_root / "style.css").write_text("body { color: red; }", encoding="utf-8")

        # Sits just outside web_root, for path-traversal tests.
        (cls.tmp_path / "secret.txt").write_text("TOPSECRET", encoding="utf-8")

        cls._env_patcher = mock.patch.dict(os.environ, {"WORKTIME_CONFIG": str(cls.cfg_path)})
        cls._env_patcher.start()

    @classmethod
    def tearDownClass(cls):
        cls._env_patcher.stop()
        cls._tmpdir.cleanup()

    @classmethod
    def _write_config(cls, target="8:24"):
        cls.cfg_path.write_text(
            f'daily_target = "{target}"\n'
            f'data_file = "{cls.data_file}"\n',
            encoding="utf-8",
        )

    @classmethod
    def _write_good_csv(cls):
        cls.data_file.write_text(
            "date,start,end,duration_min\n"
            "2026-09-23,08:00:00,12:00:00,240\n",
            encoding="utf-8",
        )

    def setUp(self):
        self._write_config()
        self._write_good_csv()
        self.srv = server.make_server("127.0.0.1", 0, self.web_root)
        self.port = self.srv.server_address[1]
        self.thread = threading.Thread(target=self.srv.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.srv.shutdown()
        self.srv.server_close()
        self.thread.join(timeout=5)

    def _request(self, method, path, host=None):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        try:
            if host is None:
                conn.request(method, path)
            else:
                conn.putrequest(method, path, skip_host=True)
                conn.putheader("Host", host)
                conn.endheaders()
            resp = conn.getresponse()
            body = resp.read()
            headers = dict(resp.getheaders())
            return resp.status, headers, body
        finally:
            conn.close()

    def test_health(self):
        status, headers, body = self._request("GET", "/api/health")
        self.assertEqual(status, 200)
        self.assertEqual(
            json.loads(body), {"app": "worktime", "version": __version__}
        )
        self.assertIn("application/json", headers.get("Content-Type", ""))

    def test_dashboard_ok(self):
        status, headers, body = self._request("GET", "/api/dashboard")
        self.assertEqual(status, 200)
        self.assertIn("application/json", headers.get("Content-Type", ""))
        self.assertEqual(headers.get("Cache-Control"), "no-store")
        obj = json.loads(body)
        self.assertIn("overview", obj)

    def test_dashboard_bad_range(self):
        status, headers, body = self._request("GET", "/api/dashboard?range=bad")
        self.assertEqual(status, 400)
        self.assertIn("error", json.loads(body))

    def test_unknown_api_path_is_404(self):
        status, _, _ = self._request("GET", "/api/nope")
        self.assertEqual(status, 404)

    def test_index(self):
        status, headers, body = self._request("GET", "/")
        self.assertEqual(status, 200)
        self.assertIn("INDEXCONTENT", body.decode("utf-8"))
        self.assertIn("text/html", headers.get("Content-Type", ""))
        self.assertIn("charset=utf-8", headers.get("Content-Type", ""))

    def test_style_css(self):
        status, headers, body = self._request("GET", "/style.css")
        self.assertEqual(status, 200)
        self.assertIn("text/css", headers.get("Content-Type", ""))

    def test_path_traversal_blocked(self):
        for path in ("/../secret.txt", "/%2e%2e/secret.txt", "/..%2fsecret.txt"):
            with self.subTest(path=path):
                status, _, body = self._request("GET", path)
                self.assertEqual(status, 404)
                self.assertNotIn(b"TOPSECRET", body)

    def test_host_header_forbidden(self):
        status, _, _ = self._request("GET", "/api/health", host=f"evil.example:{self.port}")
        self.assertEqual(status, 403)

    def test_host_header_localhost_allowed(self):
        status, _, _ = self._request("GET", "/api/health", host=f"localhost:{self.port}")
        self.assertEqual(status, 200)

    def test_broken_csv_returns_500(self):
        self.data_file.write_text("not,a,valid,header\n", encoding="utf-8")
        try:
            status, _, body = self._request("GET", "/api/dashboard")
            self.assertEqual(status, 500)
            self.assertIn("line 1", json.loads(body)["error"])
        finally:
            self._write_good_csv()

    def test_config_reread_per_request(self):
        self._write_config(target="4:00")
        try:
            status, _, body = self._request("GET", "/api/dashboard")
            self.assertEqual(status, 200)
            self.assertEqual(json.loads(body)["settings"]["daily_target_s"], 4 * 3600)
        finally:
            self._write_config()

    def test_post_not_allowed(self):
        status, _, _ = self._request("POST", "/api/dashboard")
        self.assertNotEqual(status, 200)


class RunSubprocessTests(unittest.TestCase):
    def test_run_serves_writes_pid_and_stops_on_sigterm(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            cfg_path = tmp_path / "config.toml"
            data_file = tmp_path / "data" / "worktime.csv"
            cfg_path.write_text(f'data_file = "{data_file}"\n', encoding="utf-8")
            pid_file = tmp_path / "server.pid"

            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.bind(("127.0.0.1", 0))
            port = s.getsockname()[1]
            s.close()

            env = dict(os.environ)
            env["PYTHONPATH"] = str(REPO_ROOT)
            env["WORKTIME_CONFIG"] = str(cfg_path)

            code = (
                "from pathlib import Path\n"
                "from worktime.server import run\n"
                f"run('127.0.0.1', {port}, Path({str(pid_file)!r}))\n"
            )
            proc = subprocess.Popen(
                [sys.executable, "-c", code],
                cwd=str(REPO_ROOT),
                env=env,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            try:
                deadline = time.monotonic() + 5
                healthy = False
                while time.monotonic() < deadline and not healthy:
                    try:
                        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=0.5)
                        conn.request("GET", "/api/health")
                        resp = conn.getresponse()
                        healthy = resp.status == 200
                        resp.read()
                        conn.close()
                    except OSError:
                        time.sleep(0.1)
                self.assertTrue(healthy, "server did not become healthy in time")

                self.assertEqual(pid_file.read_text().strip(), str(proc.pid))

                proc.send_signal(signal.SIGTERM)
                returncode = proc.wait(timeout=5)
                self.assertEqual(returncode, 0)
                self.assertFalse(pid_file.exists())
            finally:
                if proc.poll() is None:
                    proc.kill()
                    proc.wait(timeout=5)
                if proc.stdout:
                    proc.stdout.close()
                if proc.stderr:
                    proc.stderr.close()


if __name__ == "__main__":
    unittest.main()
