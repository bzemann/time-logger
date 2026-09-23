"""Tests for platform/macos/install.sh and aerospace-bindings.toml."""

from __future__ import annotations

import csv
import os
import plistlib
import shutil
import stat
import subprocess
import sys
import tempfile
import tomllib
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
MACOS_DIR = REPO_ROOT / "platform" / "macos"
INSTALL_SH = MACOS_DIR / "install.sh"
TEMPLATE = MACOS_DIR / "aerospace-bindings.toml"
WT = REPO_ROOT / "bin" / "worktime"

# name -> (bundle id suffix, launcher args)
APPS = {
    "WorkTime Start": ("start", "start"),
    "WorkTime Stop": ("stop", "stop"),
    "WorkTime Dashboard": ("dashboard", "dashboard"),
    "WorkTime Weekly Report": ("report-week", "report week"),
    "WorkTime Monthly Report": ("report-month", "report month"),
}


def run_install(args, home, extra_env=None):
    env = os.environ.copy()
    env["HOME"] = str(home)
    env["WORKTIME_INSTALL_NO_REGISTER"] = "1"
    env["WORKTIME_NO_NOTIFY"] = "1"
    env["WORKTIME_NO_AUTO_REPORTS"] = "1"
    if extra_env:
        env.update(extra_env)
    return subprocess.run(
        ["/bin/sh", str(INSTALL_SH), *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=env,
    )


def app_dir(home, name):
    return Path(home) / "Applications" / f"{name}.app"


@unittest.skipUnless(sys.platform == "darwin", "macOS only")
class InstallScriptTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.home = Path(self._tmp.name)

    def test_install_creates_apps(self):
        result = run_install([], self.home)
        self.assertEqual(result.returncode, 0, result.stderr)

        for name, (bundle_id, args) in APPS.items():
            d = app_dir(self.home, name)
            self.assertTrue(d.is_dir(), f"{d} missing")

            plist_path = d / "Contents" / "Info.plist"
            with open(plist_path, "rb") as f:
                plist = plistlib.load(f)
            self.assertEqual(plist["CFBundleExecutable"], "worktime-launcher")
            self.assertEqual(plist["CFBundleIdentifier"], f"local.worktime.{bundle_id}")
            self.assertEqual(plist["CFBundleName"], name)
            self.assertIs(plist["LSUIElement"], True)
            self.assertEqual(plist["CFBundleInfoDictionaryVersion"], "6.0")

            lint = subprocess.run(
                ["plutil", "-lint", str(plist_path)], capture_output=True, text=True
            )
            self.assertEqual(lint.returncode, 0, lint.stdout + lint.stderr)

            codesign_check = subprocess.run(
                ["codesign", "-v", str(d)], capture_output=True, text=True
            )
            self.assertEqual(
                codesign_check.returncode, 0, codesign_check.stdout + codesign_check.stderr
            )

            launcher = d / "Contents" / "MacOS" / "worktime-launcher"
            self.assertTrue(launcher.is_file())
            mode = launcher.stat().st_mode
            self.assertTrue(mode & stat.S_IXUSR, "launcher not executable")
            content = launcher.read_text(encoding="utf-8")
            self.assertIn(f'exec "{WT}" {args}', content)

    def test_symlink_created(self):
        result = run_install([], self.home)
        self.assertEqual(result.returncode, 0, result.stderr)

        link = self.home / ".local" / "bin" / "worktime"
        self.assertTrue(link.is_symlink())
        self.assertEqual(os.readlink(link), str(WT))

    def test_aerospace_lines_and_notification_notice(self):
        result = run_install([], self.home)
        self.assertEqual(result.returncode, 0, result.stderr)

        expected = [
            f'alt-shift-t = \'exec-and-forget "{WT}" start\'',
            f'alt-shift-x = \'exec-and-forget "{WT}" stop\'',
            f'alt-shift-d = \'exec-and-forget "{WT}" dashboard\'',
            f'alt-shift-r = \'exec-and-forget "{WT}" report week\'',
        ]
        for line in expected:
            self.assertIn(line, result.stdout)
        self.assertIn("Script Editor", result.stdout)

    def test_path_hint_shown_when_not_on_path(self):
        result = run_install([], self.home, extra_env={"PATH": "/usr/bin:/bin"})
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Add this to ~/.zshrc", result.stdout)
        self.assertIn(f'export PATH="{self.home}/.local/bin:$PATH"', result.stdout)

    def test_install_twice_is_idempotent(self):
        result1 = run_install([], self.home)
        self.assertEqual(result1.returncode, 0, result1.stderr)
        result2 = run_install([], self.home)
        self.assertEqual(result2.returncode, 0, result2.stderr)

        for name in APPS:
            d = app_dir(self.home, name)
            self.assertTrue(d.is_dir())
            codesign_check = subprocess.run(
                ["codesign", "-v", str(d)], capture_output=True, text=True
            )
            self.assertEqual(
                codesign_check.returncode, 0, codesign_check.stdout + codesign_check.stderr
            )
        link = self.home / ".local" / "bin" / "worktime"
        self.assertTrue(link.is_symlink())
        self.assertEqual(os.readlink(link), str(WT))

    def test_foreign_app_left_untouched(self):
        foreign_dir = app_dir(self.home, "WorkTime Start")
        (foreign_dir / "Contents").mkdir(parents=True)
        plist_path = foreign_dir / "Contents" / "Info.plist"
        foreign_content = (
            "<?xml version=\"1.0\" encoding=\"UTF-8\"?>\n"
            "<plist><dict><key>CFBundleIdentifier</key>"
            "<string>com.example.foreign</string></dict></plist>\n"
        )
        plist_path.write_text(foreign_content, encoding="utf-8")

        result = run_install([], self.home)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Skipping WorkTime Start.app", result.stderr)
        self.assertEqual(plist_path.read_text(encoding="utf-8"), foreign_content)

        result_uninstall = run_install(["--uninstall"], self.home)
        self.assertEqual(result_uninstall.returncode, 0, result_uninstall.stderr)
        self.assertTrue(foreign_dir.is_dir())
        self.assertEqual(plist_path.read_text(encoding="utf-8"), foreign_content)

    def test_existing_regular_file_at_symlink_path(self):
        local_bin = self.home / ".local" / "bin"
        local_bin.mkdir(parents=True)
        link_path = local_bin / "worktime"
        link_path.write_text("not a symlink\n", encoding="utf-8")

        result = run_install([], self.home)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("a file already exists there", result.stderr)
        self.assertFalse(link_path.is_symlink())
        self.assertEqual(link_path.read_text(encoding="utf-8"), "not a symlink\n")

    def test_conflict_warning_and_already_configured(self):
        aerospace_dir = self.home / ".config" / "aerospace"
        aerospace_dir.mkdir(parents=True)
        (aerospace_dir / "aerospace.toml").write_text(
            "[mode.main.binding]\n"
            "alt-shift-t = 'layout tiles'\n"
            "alt-shift-x = 'exec-and-forget \"/x/bin/worktime\" stop'\n",
            encoding="utf-8",
        )

        result = run_install([], self.home)
        self.assertEqual(result.returncode, 0, result.stderr)
        output = result.stdout + result.stderr
        self.assertIn("Warning: alt-shift-t is already bound", output)
        self.assertIn("alt-shift-x: already configured", output)

    def test_uninstall_removes_apps_and_symlink(self):
        result = run_install([], self.home)
        self.assertEqual(result.returncode, 0, result.stderr)

        result_uninstall = run_install(["--uninstall"], self.home)
        self.assertEqual(result_uninstall.returncode, 0, result_uninstall.stderr)

        for name in APPS:
            self.assertFalse(app_dir(self.home, name).exists())
        link = self.home / ".local" / "bin" / "worktime"
        self.assertFalse(link.exists())
        self.assertFalse(link.is_symlink())

        result_uninstall2 = run_install(["--uninstall"], self.home)
        self.assertEqual(result_uninstall2.returncode, 0, result_uninstall2.stderr)

    def test_unknown_option_exits_2(self):
        result = run_install(["--bogus"], self.home)
        self.assertEqual(result.returncode, 2)

    def test_help_exits_0(self):
        result = run_install(["--help"], self.home)
        self.assertEqual(result.returncode, 0, result.stderr)
        result_h = run_install(["-h"], self.home)
        self.assertEqual(result_h.returncode, 0, result_h.stderr)

    def test_end_to_end_launcher(self):
        result = run_install([], self.home)
        self.assertEqual(result.returncode, 0, result.stderr)

        csv_path = self.home / "wt.csv"
        cfg_path = self.home / "c.toml"
        cfg_path.write_text(f'data_file = "{csv_path}"\n', encoding="utf-8")

        env = os.environ.copy()
        env["WORKTIME_CONFIG"] = str(cfg_path)
        env["WORKTIME_NO_NOTIFY"] = "1"
        env["WORKTIME_NO_AUTO_REPORTS"] = "1"

        start_launcher = app_dir(self.home, "WorkTime Start") / "Contents" / "MacOS" / "worktime-launcher"
        start_result = subprocess.run(
            [str(start_launcher)], capture_output=True, text=True, encoding="utf-8", env=env
        )
        self.assertEqual(start_result.returncode, 0, start_result.stderr)
        self.assertTrue(start_result.stdout.startswith("Started at"), start_result.stdout)

        with open(csv_path, newline="", encoding="utf-8") as f:
            rows = list(csv.DictReader(f))
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["end"], "")

        stop_launcher = app_dir(self.home, "WorkTime Stop") / "Contents" / "MacOS" / "worktime-launcher"
        stop_result = subprocess.run(
            [str(stop_launcher)], capture_output=True, text=True, encoding="utf-8", env=env
        )
        self.assertEqual(stop_result.returncode, 0, stop_result.stderr)
        self.assertIn("Stopped:", stop_result.stdout)


class InstallScriptSyntaxTests(unittest.TestCase):
    def test_sh_n(self):
        result = subprocess.run(["sh", "-n", str(INSTALL_SH)], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_dash_n(self):
        dash = shutil.which("dash")
        if not dash:
            self.skipTest("dash not available")
        result = subprocess.run([dash, "-n", str(INSTALL_SH)], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)


class AerospaceTemplateTests(unittest.TestCase):
    def test_template_is_valid_toml_with_expected_bindings(self):
        text = TEMPLATE.read_text(encoding="utf-8")
        data = tomllib.loads(text)

        self.assertEqual(set(data.keys()), {"alt-shift-t", "alt-shift-x", "alt-shift-d", "alt-shift-r"})

        endings = {
            "alt-shift-t": "start",
            "alt-shift-x": "stop",
            "alt-shift-d": "dashboard",
            "alt-shift-r": "report week",
        }
        for key, value in data.items():
            self.assertTrue(value.startswith('exec-and-forget "@WORKTIME@" '), value)
            self.assertTrue(value.endswith(endings[key]), value)


if __name__ == "__main__":
    unittest.main()
