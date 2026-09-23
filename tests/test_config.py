"""Tests for worktime.config."""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from datetime import date

from worktime.config import (
    DEFAULT_DAILY_TARGET_MIN,
    DEFAULT_DATA_FILE,
    DEFAULT_PORT,
    DEFAULT_WORKDAYS,
    ConfigError,
    config_path,
    load_config,
    parse_daily_target,
    parse_days_off,
)


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


class LoadConfigDefaultsTests(unittest.TestCase):
    def test_missing_file_uses_defaults(self):
        with tempfile.TemporaryDirectory() as tmp:
            missing = Path(tmp) / "does-not-exist" / "config.toml"
            cfg = load_config(missing)

        expected_data_file = Path(os.path.abspath(os.path.expanduser(DEFAULT_DATA_FILE)))
        self.assertEqual(cfg.data_file, expected_data_file)
        self.assertEqual(cfg.reports_dir, expected_data_file.parent / "reports")
        self.assertEqual(cfg.daily_target_min, DEFAULT_DAILY_TARGET_MIN)
        self.assertEqual(cfg.workdays, DEFAULT_WORKDAYS)
        self.assertEqual(cfg.port, DEFAULT_PORT)


class LoadConfigFullFileTests(unittest.TestCase):
    def test_full_file_parsed_correctly(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg_path = Path(tmp) / "config.toml"
            data_dir = Path(tmp) / "data"
            reports_dir = Path(tmp) / "reports"
            _write(
                cfg_path,
                f"""
                data_file = "{data_dir}/worktime.csv"
                reports_dir = "{reports_dir}"
                daily_target = "7:15"
                workdays = ["mon", "wed", "fri"]
                port = 9000
                """,
            )
            cfg = load_config(cfg_path)

        self.assertEqual(cfg.data_file, data_dir / "worktime.csv")
        self.assertEqual(cfg.reports_dir, reports_dir)
        self.assertEqual(cfg.daily_target_min, 7 * 60 + 15)
        self.assertEqual(cfg.workdays, frozenset({0, 2, 4}))
        self.assertEqual(cfg.port, 9000)

    def test_reports_dir_defaults_next_to_data_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg_path = Path(tmp) / "config.toml"
            data_dir = Path(tmp) / "data"
            _write(cfg_path, f'data_file = "{data_dir}/worktime.csv"\n')
            cfg = load_config(cfg_path)

        self.assertEqual(cfg.reports_dir, data_dir / "reports")


class ParseDailyTargetTests(unittest.TestCase):
    def test_hmm_strings(self):
        self.assertEqual(parse_daily_target("8:24"), 504)
        self.assertEqual(parse_daily_target("8:30"), 510)
        self.assertEqual(parse_daily_target("0:00"), 0)

    def test_numeric_hours(self):
        self.assertEqual(parse_daily_target(8.5), 510)
        self.assertEqual(parse_daily_target(8), 480)

    def test_invalid_values_raise(self):
        for bad in (
            "8:7",
            "25:00",
            "abc",
            True,
            -1,
            float("nan"),
            float("inf"),
            float("-inf"),
        ):
            with self.subTest(bad=bad):
                with self.assertRaises(ConfigError):
                    parse_daily_target(bad)


class LoadConfigInvalidTests(unittest.TestCase):
    def test_invalid_daily_target_in_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg_path = Path(tmp) / "config.toml"
            _write(cfg_path, 'daily_target = "abc"\n')
            with self.assertRaises(ConfigError):
                load_config(cfg_path)

    def test_daily_target_nan_in_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg_path = Path(tmp) / "config.toml"
            _write(cfg_path, "daily_target = nan\n")
            with self.assertRaises(ConfigError):
                load_config(cfg_path)

    def test_invalid_weekday_name(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg_path = Path(tmp) / "config.toml"
            _write(cfg_path, 'workdays = ["monday"]\n')
            with self.assertRaises(ConfigError):
                load_config(cfg_path)

    def test_workdays_not_a_list(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg_path = Path(tmp) / "config.toml"
            _write(cfg_path, 'workdays = "mon"\n')
            with self.assertRaises(ConfigError):
                load_config(cfg_path)

    def test_unknown_key(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg_path = Path(tmp) / "config.toml"
            _write(cfg_path, 'daly_target = "8:30"\n')
            with self.assertRaisesRegex(ConfigError, "daly_target"):
                load_config(cfg_path)

    def test_invalid_toml(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg_path = Path(tmp) / "config.toml"
            _write(cfg_path, "this is not valid toml [[[")
            with self.assertRaises(ConfigError):
                load_config(cfg_path)

    def test_port_out_of_range_and_wrong_type(self):
        for bad in ("port = 0", "port = 70000", 'port = "8765"'):
            with tempfile.TemporaryDirectory() as tmp:
                cfg_path = Path(tmp) / "config.toml"
                _write(cfg_path, bad + "\n")
                with self.assertRaises(ConfigError):
                    load_config(cfg_path)

    def test_error_message_includes_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg_path = Path(tmp) / "config.toml"
            _write(cfg_path, 'daily_target = "abc"\n')
            with self.assertRaisesRegex(ConfigError, str(cfg_path)):
                load_config(cfg_path)


class TildeExpansionTests(unittest.TestCase):
    def test_data_file_tilde_expansion(self):
        with tempfile.TemporaryDirectory() as fake_home:
            with tempfile.TemporaryDirectory() as tmp:
                cfg_path = Path(tmp) / "config.toml"
                _write(cfg_path, 'data_file = "~/mydata/worktime.csv"\n')
                with patch.dict(os.environ, {"HOME": fake_home}, clear=False):
                    cfg = load_config(cfg_path)

        self.assertEqual(
            cfg.data_file, Path(fake_home) / "mydata" / "worktime.csv"
        )


class ConfigPathTests(unittest.TestCase):
    def test_worktime_config_env_wins(self):
        with tempfile.TemporaryDirectory() as tmp:
            explicit = str(Path(tmp) / "explicit.toml")
            env = {
                "WORKTIME_CONFIG": explicit,
                "XDG_CONFIG_HOME": str(Path(tmp) / "xdg"),
            }
            with patch.dict(os.environ, env, clear=False):
                self.assertEqual(config_path(), Path(explicit))

    def test_xdg_config_home_used_when_no_worktime_config(self):
        with tempfile.TemporaryDirectory() as tmp:
            xdg = str(Path(tmp) / "xdg")
            env = {"XDG_CONFIG_HOME": xdg}
            with patch.dict(os.environ, env, clear=False):
                os.environ.pop("WORKTIME_CONFIG", None)
                self.assertEqual(
                    config_path(), Path(xdg) / "worktime" / "config.toml"
                )

    def test_default_home_config_dir(self):
        with tempfile.TemporaryDirectory() as fake_home:
            with patch.dict(os.environ, {"HOME": fake_home}, clear=False):
                os.environ.pop("WORKTIME_CONFIG", None)
                os.environ.pop("XDG_CONFIG_HOME", None)
                self.assertEqual(
                    config_path(),
                    Path(fake_home) / ".config" / "worktime" / "config.toml",
                )


class ParseDaysOffTests(unittest.TestCase):
    def test_single_date(self):
        result = parse_days_off(["2026-12-24"])
        self.assertEqual(result, frozenset({date(2026, 12, 24)}))

    def test_empty_list(self):
        result = parse_days_off([])
        self.assertEqual(result, frozenset())

    def test_date_range_inclusive(self):
        result = parse_days_off(["2026-12-24..2026-12-31"])
        # Should be 8 days: 24, 25, 26, 27, 28, 29, 30, 31
        self.assertEqual(len(result), 8)
        self.assertIn(date(2026, 12, 24), result)
        self.assertIn(date(2026, 12, 31), result)
        self.assertNotIn(date(2027, 1, 1), result)

    def test_multiple_dates_and_ranges(self):
        result = parse_days_off(["2026-12-24..2026-12-31", "2027-01-02"])
        # Should be 9 days: 8 from range + 1 single date
        self.assertEqual(len(result), 9)
        self.assertIn(date(2026, 12, 24), result)
        self.assertIn(date(2026, 12, 31), result)
        self.assertIn(date(2027, 1, 2), result)
        self.assertNotIn(date(2027, 1, 1), result)

    def test_overlapping_dates_merged(self):
        result = parse_days_off(["2026-12-24", "2026-12-24", "2026-12-25"])
        self.assertEqual(result, frozenset({date(2026, 12, 24), date(2026, 12, 25)}))

    def test_spaces_around_dates(self):
        result = parse_days_off([" 2027-01-02 "])
        self.assertEqual(result, frozenset({date(2027, 1, 2)}))

    def test_spaces_in_range(self):
        result = parse_days_off([" 2026-12-24 .. 2026-12-31 "])
        self.assertEqual(len(result), 8)
        self.assertIn(date(2026, 12, 24), result)

    def test_not_a_list(self):
        with self.assertRaisesRegex(ConfigError, "days_off must be a list of dates"):
            parse_days_off("2026-12-24")

    def test_non_string_item(self):
        with self.assertRaisesRegex(ConfigError, "days_off entries must be strings"):
            parse_days_off([20261224])

    def test_invalid_date_format(self):
        with self.assertRaisesRegex(ConfigError, "invalid date"):
            parse_days_off(["2026-1-5"])

    def test_invalid_date_value(self):
        with self.assertRaisesRegex(ConfigError, "invalid date"):
            parse_days_off(["2026-13-01"])

    def test_range_start_after_end(self):
        with self.assertRaisesRegex(ConfigError, "has start after end"):
            parse_days_off(["2026-12-31..2026-12-24"])

    def test_range_too_long(self):
        with self.assertRaisesRegex(ConfigError, "longer than 366 days"):
            parse_days_off(["2026-01-01..2027-01-02"])

    def test_incomplete_range(self):
        with self.assertRaisesRegex(ConfigError, "invalid date"):
            parse_days_off(["2026-12-24.."])

    def test_invalid_date_string(self):
        with self.assertRaisesRegex(ConfigError, "invalid date"):
            parse_days_off(["abc"])


class LoadConfigDaysOffTests(unittest.TestCase):
    def test_missing_file_defaults_to_empty(self):
        with tempfile.TemporaryDirectory() as tmp:
            missing = Path(tmp) / "does-not-exist" / "config.toml"
            cfg = load_config(missing)

        self.assertEqual(cfg.days_off, frozenset())

    def test_empty_days_off_list(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg_path = Path(tmp) / "config.toml"
            _write(cfg_path, "days_off = []\n")
            cfg = load_config(cfg_path)

        self.assertEqual(cfg.days_off, frozenset())

    def test_days_off_with_range_and_single_dates(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg_path = Path(tmp) / "config.toml"
            _write(cfg_path, 'days_off = ["2026-12-24..2026-12-31", "2027-01-02"]\n')
            cfg = load_config(cfg_path)

        self.assertEqual(len(cfg.days_off), 9)
        self.assertIn(date(2026, 12, 24), cfg.days_off)
        self.assertIn(date(2026, 12, 31), cfg.days_off)
        self.assertIn(date(2027, 1, 2), cfg.days_off)

    def test_invalid_days_off_not_a_list(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg_path = Path(tmp) / "config.toml"
            _write(cfg_path, 'days_off = "2026-12-24"\n')
            with self.assertRaisesRegex(ConfigError, str(cfg_path)):
                load_config(cfg_path)

    def test_invalid_days_off_error_includes_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg_path = Path(tmp) / "config.toml"
            _write(cfg_path, 'days_off = ["2026-13-01"]\n')
            with self.assertRaisesRegex(ConfigError, str(cfg_path)):
                load_config(cfg_path)


if __name__ == "__main__":
    unittest.main()
