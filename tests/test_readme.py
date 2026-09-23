"""Consistency checks between README.md and the actual code.

These tests don't check prose quality; they check that facts stated in the
README (subcommands, options, environment variables, config keys) actually
match the code, so the README can't silently go stale.
"""

from __future__ import annotations

import re
import unittest
from pathlib import Path

import csv
import io

from worktime.cli import _build_parser
from worktime.config import _ALLOWED_KEYS
from worktime.store import row_to_entry

REPO_ROOT = Path(__file__).resolve().parents[1]
README_PATH = REPO_ROOT / "README.md"

ENV_VAR_RE = re.compile(r"WORKTIME_[A-Z_]+")

HEADINGS = [
    "# WorkTime Logger",
    "Requirements",
    "Installation",
    "Daily use",
    "Command reference",
    "Dashboard",
    "Reports",
    "Configuration",
    "How the balance is calculated",
    "Your data",
    "Troubleshooting",
    "Updating",
    "Development",
]

# Directories to scan for WORKTIME_* environment variable usage.
ENV_SCAN_DIRS = ["worktime", "bin", "platform", "tools"]


def _iter_text_files(root: Path):
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        if "__pycache__" in path.parts:
            continue
        try:
            data = path.read_bytes()
        except OSError:
            continue
        # Skip binaries (e.g. compiled icons): a NUL byte means "not text".
        if b"\x00" in data:
            continue
        try:
            yield path, data.decode("utf-8")
        except UnicodeDecodeError:
            continue


def _find_env_vars() -> set[str]:
    found: set[str] = set()
    for dirname in ENV_SCAN_DIRS:
        root = REPO_ROOT / dirname
        if not root.exists():
            continue
        for _path, text in _iter_text_files(root):
            found.update(ENV_VAR_RE.findall(text))
    return found


class TestReadmeExists(unittest.TestCase):
    def setUp(self):
        self.assertTrue(README_PATH.exists(), "README.md is missing")
        self.text = README_PATH.read_text(encoding="utf-8")


class TestSubcommands(TestReadmeExists):
    def test_all_subcommands_documented(self):
        parser = _build_parser()
        subparsers_action = parser._subparsers._group_actions[0]
        names = list(subparsers_action.choices.keys())
        self.assertTrue(names, "no subcommands found on the real parser")
        for name in names:
            with self.subTest(command=name):
                self.assertIn(
                    f"worktime {name}",
                    self.text,
                    f"README.md does not mention 'worktime {name}'",
                )

    def test_report_kinds_documented(self):
        parser = _build_parser()
        subparsers_action = parser._subparsers._group_actions[0]
        report_parser = subparsers_action.choices["report"]
        kind_action = next(
            a for a in report_parser._actions if a.dest == "kind"
        )
        self.assertTrue(kind_action.choices)
        for kind in kind_action.choices:
            with self.subTest(kind=kind):
                self.assertIn(
                    f"worktime report {kind}",
                    self.text,
                    f"README.md does not mention 'worktime report {kind}'",
                )


class TestOptions(TestReadmeExists):
    def test_options_documented(self):
        for opt in ("--at", "--last", "--date", "--no-open", "--port", "--version"):
            with self.subTest(option=opt):
                self.assertIn(opt, self.text, f"README.md does not mention {opt!r}")


class TestEnvVars(TestReadmeExists):
    def test_all_env_vars_documented(self):
        env_vars = _find_env_vars()
        self.assertTrue(env_vars, "no WORKTIME_* env vars found in the source tree")
        for var in sorted(env_vars):
            with self.subTest(var=var):
                self.assertIn(
                    var, self.text, f"README.md does not mention {var!r}"
                )

    def test_no_stale_env_vars_in_readme(self):
        real_vars = _find_env_vars()
        mentioned = set(ENV_VAR_RE.findall(self.text))
        stale = mentioned - real_vars
        self.assertEqual(
            stale,
            set(),
            f"README.md mentions WORKTIME_* variables not found in the code: {stale}",
        )


class TestConfigKeys(TestReadmeExists):
    def test_all_config_keys_documented(self):
        self.assertTrue(_ALLOWED_KEYS)
        for key in sorted(_ALLOWED_KEYS):
            with self.subTest(key=key):
                self.assertIn(key, self.text, f"README.md does not mention config key {key!r}")


class TestLinks(TestReadmeExists):
    def test_no_cdn_links(self):
        forbidden = ["cdn.jsdelivr", "unpkg.com", "cdnjs.cloudflare"]
        for token in forbidden:
            with self.subTest(token=token):
                self.assertNotIn(
                    token, self.text, f"README.md links to a CDN ({token})"
                )

    def test_links_are_to_known_hosts(self):
        urls = re.findall(r"https?://[^\s)>\]\"']+", self.text)
        allowed_prefixes = (
            "https://github.com/bzemann/time-logger",
            "https://github.com/nikitabobko",
        )
        # Local addresses (e.g. "http://127.0.0.1:<port>/", the dashboard's
        # own URL) aren't external links, so they're exempt.
        local_prefixes = ("http://127.0.0.1", "http://localhost")
        for url in urls:
            if url.startswith(local_prefixes):
                continue
            with self.subTest(url=url):
                self.assertTrue(
                    url.startswith(allowed_prefixes),
                    f"unexpected link in README.md: {url}",
                )


class TestExamples(TestReadmeExists):
    def test_no_zero_padded_balance_hours(self):
        # fmt_balance (worktime/report.py, mirrored in web/app.js) never
        # zero-pads the hours part, e.g. "+3:40", never "+03:40"/"+04:40".
        self.assertNotIn("+04:40", self.text)
        padded = re.findall(r"[+\u2212]0\d:\d{2}", self.text)
        self.assertEqual(
            padded,
            [],
            f"README.md contains zero-padded balance(s), which fmt_balance never "
            f"produces: {padded}",
        )

    def test_csv_example_rows_are_internally_consistent(self):
        blocks = re.findall(r"```csv\n(.*?)```", self.text, re.DOTALL)
        self.assertTrue(blocks, "README.md has no ```csv example block")

        checked = 0
        for block in blocks:
            reader = csv.reader(io.StringIO(block))
            rows = list(reader)
            self.assertTrue(rows, "empty csv block in README.md")
            self.assertEqual(rows[0], ["date", "start", "end", "duration_min"])

            for row in rows[1:]:
                if not row:
                    continue
                date_s, start_s, end_s, dur_s = row
                if not end_s:
                    # A running session (empty end/duration_min) has nothing
                    # to recompute.
                    continue
                entry = row_to_entry(row, 1)
                with self.subTest(row=row):
                    self.assertEqual(
                        str(entry.duration_min),
                        dur_s,
                        f"README.md CSV example row {row!r} has duration_min "
                        f"{dur_s!r}, but recomputing from start/end gives "
                        f"{entry.duration_min}",
                    )
                    checked += 1
        self.assertGreater(checked, 0, "no CSV example row with an end time to check")


class TestHeadings(TestReadmeExists):
    def test_all_section_headings_present(self):
        for heading in HEADINGS:
            with self.subTest(heading=heading):
                self.assertIn(heading, self.text, f"README.md is missing heading {heading!r}")


if __name__ == "__main__":
    unittest.main()
