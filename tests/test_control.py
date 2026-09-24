"""Tests for worktime.control (all mocked / fast, no real server)."""

from __future__ import annotations

import json
import tempfile
import unittest
import urllib.error
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from worktime import __version__, control
from worktime.control import ControlError


def make_cfg(tmp_path: Path, port: int = 1) -> SimpleNamespace:
    return SimpleNamespace(data_file=tmp_path / "data" / "worktime.csv", port=port)


class ProbeTests(unittest.TestCase):
    def _urlopen_ok(self, payload):
        cm = MagicMock()
        cm.__enter__.return_value.read.return_value = json.dumps(payload).encode()
        cm.__exit__.return_value = False
        return cm

    def test_ours(self):
        with patch(
            "worktime.control.urllib.request.urlopen",
            return_value=self._urlopen_ok({"app": "worktime", "version": __version__}),
        ):
            self.assertEqual(control.probe(1), "ours")

    def test_other_wrong_app(self):
        with patch(
            "worktime.control.urllib.request.urlopen",
            return_value=self._urlopen_ok({"app": "something-else"}),
        ):
            self.assertEqual(control.probe(1), "other")

    def test_other_bad_json(self):
        cm = MagicMock()
        cm.__enter__.return_value.read.return_value = b"not json"
        cm.__exit__.return_value = False
        with patch("worktime.control.urllib.request.urlopen", return_value=cm):
            self.assertEqual(control.probe(1), "other")

    def test_other_http_error(self):
        with patch(
            "worktime.control.urllib.request.urlopen",
            side_effect=urllib.error.HTTPError("url", 500, "err", {}, None),
        ):
            self.assertEqual(control.probe(1), "other")

    def test_other_generic_url_error(self):
        with patch(
            "worktime.control.urllib.request.urlopen",
            side_effect=urllib.error.URLError("timed out"),
        ):
            self.assertEqual(control.probe(1), "other")

    def test_free_connection_refused(self):
        with patch(
            "worktime.control.urllib.request.urlopen",
            side_effect=urllib.error.URLError(ConnectionRefusedError()),
        ):
            self.assertEqual(control.probe(1), "free")

    def test_free_raw_connection_refused(self):
        with patch(
            "worktime.control.urllib.request.urlopen",
            side_effect=ConnectionRefusedError(),
        ):
            self.assertEqual(control.probe(1), "free")


class StartBackgroundTests(unittest.TestCase):
    def test_starts_and_waits_for_ours(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg = make_cfg(Path(tmp))
            probe_calls = {"n": 0}

            def fake_probe(port, timeout=1.0):
                probe_calls["n"] += 1
                return "ours" if probe_calls["n"] >= 2 else "free"

            with patch("worktime.control.probe", side_effect=fake_probe), patch(
                "worktime.control.subprocess.Popen"
            ) as mock_popen, patch("worktime.control.time.sleep"):
                control.start_background(cfg, wait=5.0)

            mock_popen.assert_called_once()
            args, kwargs = mock_popen.call_args
            import sys

            self.assertEqual(args[0], [sys.executable, "-m", "worktime", "serve"])
            self.assertTrue(kwargs["start_new_session"])
            import subprocess as sp

            self.assertEqual(kwargs["stdin"], sp.DEVNULL)
            self.assertTrue(
                kwargs["env"]["PYTHONPATH"].startswith(str(control.REPO_ROOT))
            )

    def test_raises_when_never_ours(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg = make_cfg(Path(tmp))

            times = iter([0, 0.05, 0.4])

            with patch("worktime.control.probe", return_value="free"), patch(
                "worktime.control.subprocess.Popen"
            ), patch("worktime.control.time.sleep"), patch(
                "worktime.control.time.monotonic", side_effect=lambda: next(times, 0.4)
            ):
                with self.assertRaises(ControlError):
                    control.start_background(cfg, wait=0.3)


class StopBackgroundTests(unittest.TestCase):
    def test_no_pid_file_and_free(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg = make_cfg(Path(tmp))
            with patch("worktime.control.probe", return_value="free"):
                msg = control.stop_background(cfg)
        self.assertEqual(msg, "Server is not running.")

    def test_no_pid_file_but_ours_raises(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg = make_cfg(Path(tmp))
            with patch("worktime.control.probe", return_value="ours"):
                with self.assertRaises(ControlError):
                    control.stop_background(cfg)

    def test_invalid_pid_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg = make_cfg(Path(tmp))
            cfg.data_file.parent.mkdir(parents=True, exist_ok=True)
            pidf = cfg.data_file.parent / "server.pid"
            pidf.write_text("not-a-pid")
            with patch("worktime.control.probe", return_value="ours"):
                msg = control.stop_background(cfg)
        self.assertEqual(msg, "Server is not running (removed invalid PID file).")
        self.assertFalse(pidf.exists())

    def test_stale_pid_file_probe_not_ours(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg = make_cfg(Path(tmp))
            cfg.data_file.parent.mkdir(parents=True, exist_ok=True)
            pidf = cfg.data_file.parent / "server.pid"
            pidf.write_text("12345")
            with patch("worktime.control.probe", return_value="free"):
                msg = control.stop_background(cfg)
        self.assertEqual(msg, "Server is not running (removed stale PID file).")
        self.assertFalse(pidf.exists())

    def test_process_lookup_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg = make_cfg(Path(tmp))
            cfg.data_file.parent.mkdir(parents=True, exist_ok=True)
            pidf = cfg.data_file.parent / "server.pid"
            pidf.write_text("12345")
            with patch("worktime.control.probe", return_value="ours"), patch(
                "worktime.control.os.kill", side_effect=ProcessLookupError
            ):
                msg = control.stop_background(cfg)
        self.assertEqual(msg, "Server is not running (removed stale PID file).")
        self.assertFalse(pidf.exists())

    def test_success(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg = make_cfg(Path(tmp))
            cfg.data_file.parent.mkdir(parents=True, exist_ok=True)
            pidf = cfg.data_file.parent / "server.pid"
            pidf.write_text("12345")

            probe_calls = {"n": 0}

            def fake_probe(port, timeout=1.0):
                probe_calls["n"] += 1
                # first call (initial state check) -> ours; then after kill -> free
                return "ours" if probe_calls["n"] == 1 else "free"

            with patch("worktime.control.probe", side_effect=fake_probe), patch(
                "worktime.control.os.kill"
            ) as mock_kill, patch("worktime.control.time.sleep"):
                msg = control.stop_background(cfg, wait=1.0)

            self.assertEqual(msg, "Server stopped.")
            import signal

            mock_kill.assert_called_once_with(12345, signal.SIGTERM)

    def test_never_stops_raises(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg = make_cfg(Path(tmp))
            cfg.data_file.parent.mkdir(parents=True, exist_ok=True)
            pidf = cfg.data_file.parent / "server.pid"
            pidf.write_text("12345")

            times = iter([0, 0.05, 0.4])

            with patch("worktime.control.probe", return_value="ours"), patch(
                "worktime.control.os.kill"
            ), patch("worktime.control.time.sleep"), patch(
                "worktime.control.time.monotonic", side_effect=lambda: next(times, 0.4)
            ):
                with self.assertRaises(ControlError):
                    control.stop_background(cfg, wait=0.3)


class ServerVersionTests(unittest.TestCase):
    def _urlopen_ok(self, payload):
        cm = MagicMock()
        cm.__enter__.return_value.read.return_value = json.dumps(payload).encode()
        cm.__exit__.return_value = False
        return cm

    def test_returns_version_string(self):
        with patch(
            "worktime.control.urllib.request.urlopen",
            return_value=self._urlopen_ok({"app": "worktime", "version": "1.2.3"}),
        ):
            self.assertEqual(control.server_version(1), "1.2.3")

    def test_none_for_wrong_app(self):
        with patch(
            "worktime.control.urllib.request.urlopen",
            return_value=self._urlopen_ok({"app": "something-else", "version": "1.2.3"}),
        ):
            self.assertIsNone(control.server_version(1))

    def test_none_for_missing_version_field(self):
        with patch(
            "worktime.control.urllib.request.urlopen",
            return_value=self._urlopen_ok({"app": "worktime"}),
        ):
            self.assertIsNone(control.server_version(1))

    def test_none_for_non_string_version(self):
        with patch(
            "worktime.control.urllib.request.urlopen",
            return_value=self._urlopen_ok({"app": "worktime", "version": 1}),
        ):
            self.assertIsNone(control.server_version(1))

    def test_none_for_bad_json(self):
        cm = MagicMock()
        cm.__enter__.return_value.read.return_value = b"not json"
        cm.__exit__.return_value = False
        with patch("worktime.control.urllib.request.urlopen", return_value=cm):
            self.assertIsNone(control.server_version(1))

    def test_none_when_refused(self):
        with patch(
            "worktime.control.urllib.request.urlopen",
            side_effect=ConnectionRefusedError(),
        ):
            self.assertIsNone(control.server_version(1))

    def test_none_on_timeout(self):
        with patch(
            "worktime.control.urllib.request.urlopen",
            side_effect=TimeoutError(),
        ):
            self.assertIsNone(control.server_version(1))


class EnsureCurrentTests(unittest.TestCase):
    def test_free_starts_server(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg = make_cfg(Path(tmp), port=1)
            with patch("worktime.control.probe", return_value="free"), patch(
                "worktime.control.start_background"
            ) as mock_start, patch(
                "worktime.control.stop_background"
            ) as mock_stop, patch(
                "worktime.control.server_version"
            ) as mock_version:
                status = control.ensure_current(cfg)

        self.assertEqual(status, "started")
        mock_start.assert_called_once_with(cfg)
        mock_stop.assert_not_called()
        mock_version.assert_not_called()

    def test_ours_same_version_does_nothing(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg = make_cfg(Path(tmp), port=1)
            with patch("worktime.control.probe", return_value="ours"), patch(
                "worktime.control.server_version", return_value=control.__version__
            ), patch("worktime.control.start_background") as mock_start, patch(
                "worktime.control.stop_background"
            ) as mock_stop:
                status = control.ensure_current(cfg)

        self.assertEqual(status, "running")
        mock_start.assert_not_called()
        mock_stop.assert_not_called()

    def test_ours_older_version_restarts_in_order(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg = make_cfg(Path(tmp), port=1)
            calls = []
            with patch("worktime.control.probe", return_value="ours"), patch(
                "worktime.control.server_version", return_value="0.1.0"
            ), patch(
                "worktime.control.start_background",
                side_effect=lambda c: calls.append("start"),
            ) as mock_start, patch(
                "worktime.control.stop_background",
                side_effect=lambda c: calls.append("stop"),
            ) as mock_stop:
                status = control.ensure_current(cfg)

        self.assertEqual(status, "restarted")
        mock_stop.assert_called_once_with(cfg)
        mock_start.assert_called_once_with(cfg)
        self.assertEqual(calls, ["stop", "start"])

    def test_ours_none_version_restarts(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg = make_cfg(Path(tmp), port=1)
            with patch("worktime.control.probe", return_value="ours"), patch(
                "worktime.control.server_version", return_value=None
            ), patch("worktime.control.start_background") as mock_start, patch(
                "worktime.control.stop_background"
            ) as mock_stop:
                status = control.ensure_current(cfg)

        self.assertEqual(status, "restarted")
        mock_stop.assert_called_once_with(cfg)
        mock_start.assert_called_once_with(cfg)

    def test_other_raises_control_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg = make_cfg(Path(tmp), port=1)
            with patch("worktime.control.probe", return_value="other"):
                with self.assertRaises(ControlError) as cm:
                    control.ensure_current(cfg)
        self.assertIn("used by another program", str(cm.exception))

    def test_stop_failure_propagates(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg = make_cfg(Path(tmp), port=1)
            with patch("worktime.control.probe", return_value="ours"), patch(
                "worktime.control.server_version", return_value="0.1.0"
            ), patch(
                "worktime.control.stop_background",
                side_effect=ControlError("did not stop"),
            ), patch("worktime.control.start_background") as mock_start:
                with self.assertRaises(ControlError):
                    control.ensure_current(cfg)
        mock_start.assert_not_called()


if __name__ == "__main__":
    unittest.main()
