"""Tests for platform/linux/install.sh and i3-bindings.conf."""

from __future__ import annotations

import configparser
import csv
import os
import re
import shlex
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
LINUX_DIR = REPO_ROOT / "platform" / "linux"
INSTALL_SH = LINUX_DIR / "install.sh"
TEMPLATE = LINUX_DIR / "i3-bindings.conf"
WT = REPO_ROOT / "bin" / "worktime"

# desktop id -> (Name, Exec args)
APPS = {
    "worktime-start": ("WorkTime Start", "start"),
    "worktime-stop": ("WorkTime Stop", "stop"),
    "worktime-dashboard": ("WorkTime Dashboard", "dashboard"),
    "worktime-report-week": ("WorkTime Weekly Report", "report week"),
    "worktime-report-month": ("WorkTime Monthly Report", "report month"),
}

# Base tools install.sh and bin/worktime need on PATH, used to build a
# minimal, deterministic PATH for the dependency-hint tests (see
# test_dependency_hint_shown_when_missing / test_dependency_hint_absent_when_present).
BASE_TOOLS = [
    "uname",
    "dirname",
    "grep",
    "sed",
    "mkdir",
    "cat",
    "chmod",
    "mv",
    "rm",
    "ln",
    "readlink",
    "head",
    "tr",
    "python3",
]


def run_install(args, home, extra_env=None):
    env = os.environ.copy()
    env["HOME"] = str(home)
    env["WORKTIME_INSTALL_ALLOW_ANY_OS"] = "1"
    env["WORKTIME_INSTALL_NO_REGISTER"] = "1"
    env["WORKTIME_NO_NOTIFY"] = "1"
    env["WORKTIME_NO_AUTO_REPORTS"] = "1"
    env.pop("XDG_DATA_HOME", None)
    if extra_env:
        env.update(extra_env)
    return subprocess.run(
        ["/bin/sh", str(INSTALL_SH), *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=env,
    )


def apps_dir_for(home):
    return Path(home) / ".local" / "share" / "applications"


def read_desktop(path):
    cp = configparser.RawConfigParser()
    cp.optionxform = str
    cp.read(path, encoding="utf-8")
    return cp


def build_min_path(tmp_dir, include_notify_tools):
    """A directory containing symlinks to only the tools install.sh and
    bin/worktime actually need, so the dependency-hint tests are
    deterministic regardless of what happens to be installed on the host
    (this is the "curated PATH" approach; see report)."""
    bindir = Path(tmp_dir) / "bin"
    bindir.mkdir()
    for tool in BASE_TOOLS:
        src = shutil.which(tool)
        if src is None:
            raise unittest.SkipTest(f"required tool not found on host: {tool}")
        os.symlink(src, bindir / tool)
    if include_notify_tools:
        for name in ("notify-send", "xdg-open"):
            p = bindir / name
            p.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
            p.chmod(0o755)
    return str(bindir)


class InstallScriptTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.home = Path(self._tmp.name)

    def test_install_creates_desktop_files(self):
        result = run_install([], self.home)
        self.assertEqual(result.returncode, 0, result.stderr)

        apps_dir = apps_dir_for(self.home)
        for desktop_id, (name, args) in APPS.items():
            f = apps_dir / f"{desktop_id}.desktop"
            self.assertTrue(f.is_file(), f"{f} missing")

            mode = f.stat().st_mode
            self.assertEqual(stat.S_IMODE(mode), 0o644)

            cp = read_desktop(f)
            self.assertIn("Desktop Entry", cp.sections())
            section = cp["Desktop Entry"]
            self.assertEqual(section["Type"], "Application")
            self.assertEqual(section["Name"], name)
            self.assertEqual(section["Exec"], f"{WT} {args}")
            self.assertEqual(section["Terminal"], "false")
            self.assertEqual(section["X-WorkTime-Launcher"], "true")

            if shutil.which("desktop-file-validate"):
                validate = subprocess.run(
                    ["desktop-file-validate", str(f)], capture_output=True, text=True
                )
                self.assertEqual(validate.returncode, 0, validate.stdout + validate.stderr)

    def test_xdg_data_home_override(self):
        with tempfile.TemporaryDirectory() as xdg_tmp:
            result = run_install([], self.home, extra_env={"XDG_DATA_HOME": xdg_tmp})
            self.assertEqual(result.returncode, 0, result.stderr)

            apps_dir = Path(xdg_tmp) / "applications"
            for desktop_id in APPS:
                self.assertTrue((apps_dir / f"{desktop_id}.desktop").is_file())
            self.assertFalse(apps_dir_for(self.home).exists())

    def test_symlink_created(self):
        result = run_install([], self.home)
        self.assertEqual(result.returncode, 0, result.stderr)

        link = self.home / ".local" / "bin" / "worktime"
        self.assertTrue(link.is_symlink())
        self.assertEqual(os.readlink(link), str(WT))

    def test_path_hint_shown_when_not_on_path(self):
        result = run_install([], self.home, extra_env={"PATH": "/usr/bin:/bin"})
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Add this to ~/.profile", result.stdout)
        self.assertIn(f'export PATH="{self.home}/.local/bin:$PATH"', result.stdout)

    def test_i3_lines_and_summary(self):
        result = run_install([], self.home)
        self.assertEqual(result.returncode, 0, result.stderr)

        expected = [
            f"bindsym $mod+Shift+t exec --no-startup-id {WT} start",
            f"bindsym $mod+Shift+x exec --no-startup-id {WT} stop",
            f"bindsym $mod+Shift+d exec --no-startup-id {WT} dashboard",
            f"bindsym $mod+Shift+w exec --no-startup-id {WT} report week",
        ]
        for line in expected:
            self.assertIn(line, result.stdout)
        self.assertIn("rofi", result.stdout)

    def test_dependency_hint_shown_when_missing(self):
        with tempfile.TemporaryDirectory() as tool_tmp:
            path = build_min_path(tool_tmp, include_notify_tools=False)
            result = run_install([], self.home, extra_env={"PATH": path})
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("sudo apt install", result.stderr)
            self.assertIn("libnotify-bin", result.stderr)
            self.assertIn("xdg-utils", result.stderr)

    def test_dependency_hint_absent_when_present(self):
        with tempfile.TemporaryDirectory() as tool_tmp:
            path = build_min_path(tool_tmp, include_notify_tools=True)
            result = run_install([], self.home, extra_env={"PATH": path})
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertNotIn("sudo apt install", result.stderr)

    def test_install_twice_is_idempotent(self):
        result1 = run_install([], self.home)
        self.assertEqual(result1.returncode, 0, result1.stderr)
        apps_dir = apps_dir_for(self.home)
        contents1 = {
            f"{desktop_id}.desktop": (apps_dir / f"{desktop_id}.desktop").read_text(encoding="utf-8")
            for desktop_id in APPS
        }

        result2 = run_install([], self.home)
        self.assertEqual(result2.returncode, 0, result2.stderr)
        for desktop_id in APPS:
            content2 = (apps_dir / f"{desktop_id}.desktop").read_text(encoding="utf-8")
            self.assertEqual(content2, contents1[f"{desktop_id}.desktop"])

        link = self.home / ".local" / "bin" / "worktime"
        self.assertTrue(link.is_symlink())
        self.assertEqual(os.readlink(link), str(WT))

    def test_foreign_desktop_file_left_untouched(self):
        apps_dir = apps_dir_for(self.home)
        apps_dir.mkdir(parents=True)
        f = apps_dir / "worktime-start.desktop"
        foreign_content = (
            "[Desktop Entry]\n"
            "Type=Application\n"
            "Name=Something Else\n"
            "Exec=/usr/bin/something-else\n"
        )
        f.write_text(foreign_content, encoding="utf-8")

        result = run_install([], self.home)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Skipping worktime-start.desktop", result.stderr)
        self.assertEqual(f.read_text(encoding="utf-8"), foreign_content)

        result_uninstall = run_install(["--uninstall"], self.home)
        self.assertEqual(result_uninstall.returncode, 0, result_uninstall.stderr)
        self.assertTrue(f.is_file())
        self.assertEqual(f.read_text(encoding="utf-8"), foreign_content)

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
        i3_dir = self.home / ".config" / "i3"
        i3_dir.mkdir(parents=True)
        (i3_dir / "config").write_text(
            "bindsym $mod+Shift+d exec dmenu_run\n"
            "bindsym Mod4+Shift+x exec --no-startup-id /x/bin/worktime stop\n"
            "bindsym $mod+Shift+tab focus left\n",
            encoding="utf-8",
        )

        result = run_install([], self.home)
        self.assertEqual(result.returncode, 0, result.stderr)
        output = result.stdout + result.stderr
        self.assertIn("Warning: $mod+Shift+d is already bound", output)
        self.assertIn("$mod+Shift+x: already configured", output)
        self.assertNotIn("$mod+Shift+t is already bound", output)
        self.assertNotIn("$mod+Shift+t: already configured", output)

    def test_i3_config_fallback_used(self):
        i3_dir = self.home / ".i3"
        i3_dir.mkdir(parents=True)
        (i3_dir / "config").write_text(
            "bindsym $mod+Shift+w exec --no-startup-id /x/bin/worktime report week\n",
            encoding="utf-8",
        )

        result = run_install([], self.home)
        self.assertEqual(result.returncode, 0, result.stderr)
        output = result.stdout + result.stderr
        self.assertIn("$mod+Shift+w: already configured", output)

    def test_uninstall_removes_files_and_symlink(self):
        result = run_install([], self.home)
        self.assertEqual(result.returncode, 0, result.stderr)

        result_uninstall = run_install(["--uninstall"], self.home)
        self.assertEqual(result_uninstall.returncode, 0, result_uninstall.stderr)

        apps_dir = apps_dir_for(self.home)
        for desktop_id in APPS:
            self.assertFalse((apps_dir / f"{desktop_id}.desktop").exists())
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

    @unittest.skipUnless(sys.platform == "darwin", "OS guard is only exercised on a non-Linux host")
    def test_os_guard_without_allow_any_os(self):
        env = os.environ.copy()
        env["HOME"] = str(self.home)
        env.pop("WORKTIME_INSTALL_ALLOW_ANY_OS", None)
        result = subprocess.run(
            ["/bin/sh", str(INSTALL_SH)],
            capture_output=True,
            text=True,
            encoding="utf-8",
            env=env,
        )
        self.assertEqual(result.returncode, 1)
        self.assertIn("Linux only", result.stderr)

    def test_repo_path_character_check_present(self):
        text = INSTALL_SH.read_text(encoding="utf-8")
        self.assertIn("[!A-Za-z0-9._/-]", text)

    def test_end_to_end_launcher(self):
        result = run_install([], self.home)
        self.assertEqual(result.returncode, 0, result.stderr)

        apps_dir = apps_dir_for(self.home)
        start_desktop = apps_dir / "worktime-start.desktop"
        stop_desktop = apps_dir / "worktime-stop.desktop"

        csv_path = self.home / "wt.csv"
        cfg_path = self.home / "c.toml"
        cfg_path.write_text(f'data_file = "{csv_path}"\n', encoding="utf-8")

        env = os.environ.copy()
        env["WORKTIME_CONFIG"] = str(cfg_path)
        env["WORKTIME_NO_NOTIFY"] = "1"
        env["WORKTIME_NO_AUTO_REPORTS"] = "1"

        start_exec = read_desktop(start_desktop)["Desktop Entry"]["Exec"]
        start_result = subprocess.run(
            shlex.split(start_exec), capture_output=True, text=True, encoding="utf-8", env=env
        )
        self.assertEqual(start_result.returncode, 0, start_result.stderr)
        self.assertTrue(start_result.stdout.startswith("Started at"), start_result.stdout)

        with open(csv_path, newline="", encoding="utf-8") as f:
            rows = list(csv.DictReader(f))
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["end"], "")

        stop_exec = read_desktop(stop_desktop)["Desktop Entry"]["Exec"]
        stop_result = subprocess.run(
            shlex.split(stop_exec), capture_output=True, text=True, encoding="utf-8", env=env
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


class I3TemplateTests(unittest.TestCase):
    def test_template_lines(self):
        text = TEMPLATE.read_text(encoding="utf-8")
        lines = [line for line in text.splitlines() if line.strip() and not line.strip().startswith("#")]
        self.assertEqual(len(lines), 4)

        pattern = re.compile(
            r"^bindsym \$mod\+Shift\+[txdw] exec --no-startup-id @WORKTIME@ (start|stop|dashboard|report week)$"
        )
        for line in lines:
            self.assertRegex(line, pattern)


if __name__ == "__main__":
    unittest.main()
