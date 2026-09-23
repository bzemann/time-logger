"""Tests for the bin/worktime CLI wrapper."""

from __future__ import annotations

import os
import subprocess
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
WORKTIME_BIN = REPO_ROOT / "bin" / "worktime"


def run(args, **kwargs):
    return subprocess.run(
        [str(WORKTIME_BIN), *args],
        capture_output=True,
        text=True,
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


if __name__ == "__main__":
    unittest.main()
