"""WorkTime Logger configuration.

The configuration file is a TOML file. Its location is determined by (in
order): the ``WORKTIME_CONFIG`` environment variable; otherwise
``$XDG_CONFIG_HOME/worktime/config.toml`` if ``XDG_CONFIG_HOME`` is set;
otherwise ``~/.config/worktime/config.toml``. If the file does not exist,
all settings fall back to their defaults. See ``config.example.toml`` in
the repository root for the recognised keys and their format.
"""

from __future__ import annotations

import math
import os
import re
import tomllib
from dataclasses import dataclass
from pathlib import Path

DEFAULT_DATA_FILE = "~/.local/share/worktime/worktime.csv"
DEFAULT_DAILY_TARGET_MIN = 510  # 8:30
DEFAULT_WORKDAYS = frozenset({0, 1, 2, 3, 4})
DEFAULT_PORT = 8765

_WEEKDAY_NAMES = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")
_HMM_RE = re.compile(r"^\d{1,2}:[0-5]\d$")

_ALLOWED_KEYS = {"data_file", "reports_dir", "daily_target", "workdays", "port"}


class ConfigError(Exception):
    """Raised when the configuration file is missing, malformed or invalid."""


@dataclass(frozen=True)
class Config:
    data_file: Path
    reports_dir: Path
    daily_target_min: int
    workdays: frozenset[int]
    port: int


def config_path() -> Path:
    """Return the path of the config file (may or may not exist)."""
    env_path = os.environ.get("WORKTIME_CONFIG")
    if env_path:
        return Path(os.path.abspath(os.path.expanduser(env_path)))

    xdg = os.environ.get("XDG_CONFIG_HOME")
    if xdg:
        return Path(os.path.abspath(os.path.expanduser(xdg))) / "worktime" / "config.toml"

    return Path(os.path.abspath(os.path.expanduser("~/.config/worktime/config.toml")))


def _abspath(s: str) -> Path:
    return Path(os.path.abspath(os.path.expanduser(s)))


def parse_daily_target(value) -> int:
    """Parse a daily target value into a number of minutes (0..1440)."""
    if isinstance(value, str):
        if not _HMM_RE.match(value):
            raise ConfigError(
                f"daily_target must be 'H:MM' or a number of hours, got {value!r}"
            )
        hours_str, minutes_str = value.split(":")
        minutes = int(hours_str) * 60 + int(minutes_str)
    elif isinstance(value, bool):
        raise ConfigError(
            f"daily_target must be 'H:MM' or a number of hours, got {value!r}"
        )
    elif isinstance(value, (int, float)):
        if isinstance(value, float) and not math.isfinite(value):
            raise ConfigError(
                f"daily_target must be 'H:MM' or a number of hours, got {value!r}"
            )
        minutes = round(value * 60)
    else:
        raise ConfigError(
            f"daily_target must be 'H:MM' or a number of hours, got {value!r}"
        )

    if not (0 <= minutes <= 1440):
        raise ConfigError(
            f"daily_target must be 'H:MM' or a number of hours, got {value!r}"
        )
    return minutes


def _parse_workdays(value) -> frozenset[int]:
    if not isinstance(value, list):
        raise ConfigError(f"workdays must be a list of day names, got {value!r}")

    days: set[int] = set()
    for item in value:
        if not isinstance(item, str):
            raise ConfigError(f"workdays must be a list of day names, got {item!r}")
        name = item.strip().lower()
        if name not in _WEEKDAY_NAMES:
            raise ConfigError(f"unknown weekday name: {item!r}")
        days.add(_WEEKDAY_NAMES.index(name))
    return frozenset(days)


def _parse_port(value) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ConfigError(f"port must be an integer between 1 and 65535, got {value!r}")
    if not (1 <= value <= 65535):
        raise ConfigError(f"port must be an integer between 1 and 65535, got {value!r}")
    return value


def load_config(path: Path | None = None) -> Config:
    """Load the configuration from ``path`` (or the default location)."""
    if path is None:
        path = config_path()

    if not path.exists():
        data_file = _abspath(DEFAULT_DATA_FILE)
        return Config(
            data_file=data_file,
            reports_dir=data_file.parent / "reports",
            daily_target_min=DEFAULT_DAILY_TARGET_MIN,
            workdays=DEFAULT_WORKDAYS,
            port=DEFAULT_PORT,
        )

    try:
        with path.open("rb") as f:
            raw = tomllib.load(f)
    except tomllib.TOMLDecodeError as e:
        raise ConfigError(f"{path}: invalid TOML: {e}") from e

    unknown = sorted(set(raw) - _ALLOWED_KEYS)
    if unknown:
        raise ConfigError(f"{path}: unknown key '{unknown[0]}'")

    try:
        if "data_file" in raw:
            if not isinstance(raw["data_file"], str) or not raw["data_file"]:
                raise ConfigError(f"data_file must be a non-empty string, got {raw['data_file']!r}")
            data_file = _abspath(raw["data_file"])
        else:
            data_file = _abspath(DEFAULT_DATA_FILE)

        if "reports_dir" in raw:
            if not isinstance(raw["reports_dir"], str) or not raw["reports_dir"]:
                raise ConfigError(f"reports_dir must be a non-empty string, got {raw['reports_dir']!r}")
            reports_dir = _abspath(raw["reports_dir"])
        else:
            reports_dir = data_file.parent / "reports"

        if "daily_target" in raw:
            daily_target_min = parse_daily_target(raw["daily_target"])
        else:
            daily_target_min = DEFAULT_DAILY_TARGET_MIN

        if "workdays" in raw:
            workdays = _parse_workdays(raw["workdays"])
        else:
            workdays = DEFAULT_WORKDAYS

        if "port" in raw:
            port = _parse_port(raw["port"])
        else:
            port = DEFAULT_PORT
    except ConfigError as e:
        raise ConfigError(f"{path}: {e}") from e

    return Config(
        data_file=data_file,
        reports_dir=reports_dir,
        daily_target_min=daily_target_min,
        workdays=workdays,
        port=port,
    )
