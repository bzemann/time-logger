"""Command-line interface for WorkTime Logger."""

from __future__ import annotations

import argparse
import sys

from worktime import __version__
from worktime.config import ConfigError, config_path, load_config

_WEEKDAY_NAMES = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")


def _format_hmm(minutes: int) -> str:
    return f"{minutes // 60}:{minutes % 60:02d}"


def _cmd_config(args: argparse.Namespace) -> int:
    path = config_path()
    found = "found" if path.exists() else "not found, using defaults"
    cfg = load_config(path)

    workdays = ", ".join(_WEEKDAY_NAMES[d] for d in sorted(cfg.workdays))
    if not workdays:
        workdays = "(none)"

    print(f"config file:  {path} ({found})")
    print(f"data file:    {cfg.data_file}")
    print(f"reports dir:  {cfg.reports_dir}")
    print(f"daily target: {_format_hmm(cfg.daily_target_min)}")
    print(f"workdays:     {workdays}")
    print(f"port:         {cfg.port}")
    return 0


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="worktime",
        description="WorkTime Logger — shortcut-driven work time tracking.",
    )
    parser.add_argument(
        "--version", action="version", version=f"worktime {__version__}"
    )

    subparsers = parser.add_subparsers(dest="command")

    config_parser = subparsers.add_parser(
        "config", help="Show the effective configuration"
    )
    config_parser.set_defaults(func=_cmd_config)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    if not getattr(args, "command", None):
        parser.print_help(sys.stderr)
        return 2

    try:
        return args.func(args)
    except ConfigError as e:
        print(f"worktime: {e}", file=sys.stderr)
        return 1
