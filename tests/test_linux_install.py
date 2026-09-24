"""Tests for platform/linux/install.sh, i3-bindings.conf and polybar-module.ini.

tests/fixtures/i3-config-debian and tests/fixtures/polybar-config.ini are
local-only, byte-identical copies of Basil's real i3 and polybar configs
(from new-conf-linux-debian/). Both are personal configuration and are
gitignored, so they only exist on this machine — on any other checkout
(e.g. Debian, or a fresh clone from GitHub) they are missing. The tests
that read them (RealDebianConfigTests, FixtureTests) skip cleanly when
that's the case. Every behaviour they exercise against the real configs
(the lock-screen key being left alone, the rofi key-combo hint, the
modules-right rebuild) is also covered by fixture-independent tests using
small inline configs, further down in InstallScriptTests, so the
underlying logic is tested everywhere regardless of the fixtures'
presence.
"""

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
import xml.etree.ElementTree as ET
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
LINUX_DIR = REPO_ROOT / "platform" / "linux"
INSTALL_SH = LINUX_DIR / "install.sh"
TEMPLATE = LINUX_DIR / "i3-bindings.conf"
POLYBAR_TEMPLATE = LINUX_DIR / "polybar-module.ini"
ICON = LINUX_DIR / "icons" / "worktime.svg"
WT = REPO_ROOT / "bin" / "worktime"

FIXTURES_DIR = REPO_ROOT / "tests" / "fixtures"
I3_CONFIG_DEBIAN = FIXTURES_DIR / "i3-config-debian"
POLYBAR_CONFIG = FIXTURES_DIR / "polybar-config.ini"

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
    "awk",
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
            self.assertEqual(section["Icon"], str(ICON))
            self.assertTrue(Path(section["Icon"]).is_file())

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
            f"bindsym $mod+shift+t exec --no-startup-id {WT} start",
            f"bindsym $mod+shift+u exec --no-startup-id {WT} stop",
            f"bindsym $mod+shift+d exec --no-startup-id {WT} dashboard",
            f"bindsym $mod+shift+w exec --no-startup-id {WT} report week",
        ]
        for line in expected:
            self.assertIn(line, result.stdout)
        self.assertIn("rofi", result.stdout)
        self.assertNotIn("+x exec", result.stdout)
        self.assertIn("──── paste into ~/.config/i3/config ────", result.stdout)
        self.assertIn("──── end ────", result.stdout)

    def test_polybar_lines_and_generic_hint_without_config(self):
        result = run_install([], self.home)
        self.assertEqual(result.returncode, 0, result.stderr)

        self.assertIn("[module/worktime]", result.stdout)
        self.assertIn(f"exec = {WT} status --short", result.stdout)
        self.assertIn(f"click-left = {WT} dashboard", result.stdout)
        self.assertIn("interval = 15", result.stdout)
        self.assertIn("If you use polybar", result.stdout)
        self.assertIn("──── paste into ~/.config/polybar/config.ini ────", result.stdout)
        self.assertIn(
            "polybar: then restart it: ~/.config/polybar/launch.sh (or restart i3 with $mod+shift+r).",
            result.stdout,
        )

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
            "bindsym $mod+shift+d exec dmenu_run\n"
            "bindsym Mod4+Shift+u exec --no-startup-id /x/bin/worktime stop\n"
            "bindsym $mod+shift+tab focus left\n",
            encoding="utf-8",
        )

        result = run_install([], self.home)
        self.assertEqual(result.returncode, 0, result.stderr)
        output = result.stdout + result.stderr
        self.assertIn("Warning: $mod+shift+d is already bound", output)
        self.assertIn("$mod+shift+u: already configured", output)
        self.assertNotIn("$mod+shift+t is already bound", output)
        self.assertNotIn("$mod+shift+t: already configured", output)

    def test_i3_config_fallback_used(self):
        i3_dir = self.home / ".i3"
        i3_dir.mkdir(parents=True)
        (i3_dir / "config").write_text(
            "bindsym $mod+shift+w exec --no-startup-id /x/bin/worktime report week\n",
            encoding="utf-8",
        )

        result = run_install([], self.home)
        self.assertEqual(result.returncode, 0, result.stderr)
        output = result.stdout + result.stderr
        self.assertIn("$mod+shift+w: already configured", output)

    def test_conflict_check_is_case_insensitive(self):
        i3_dir = self.home / ".config" / "i3"
        i3_dir.mkdir(parents=True)
        (i3_dir / "config").write_text(
            "bindsym $mod+shift+t exec foo\n"
            "bindsym Mod4+SHIFT+u exec foo\n"
            "bindsym --release $mod+Shift+w exec foo\n"
            "# bindsym $mod+shift+d exec foo\n"
            "bindsym $mod+shift+tab focus left\n",
            encoding="utf-8",
        )

        result = run_install([], self.home)
        self.assertEqual(result.returncode, 0, result.stderr)
        output = result.stdout + result.stderr
        self.assertIn("Warning: $mod+shift+t is already bound", output)
        self.assertIn("Warning: $mod+shift+u is already bound", output)
        self.assertIn("Warning: $mod+shift+w is already bound", output)
        self.assertNotIn("$mod+shift+d is already bound", output)
        self.assertNotIn("$mod+shift+d: already configured", output)

    def test_conflict_check_tab_alone_is_not_t(self):
        i3_dir = self.home / ".config" / "i3"
        i3_dir.mkdir(parents=True)
        (i3_dir / "config").write_text(
            "bindsym $mod+shift+tab focus left\n",
            encoding="utf-8",
        )

        result = run_install([], self.home)
        self.assertEqual(result.returncode, 0, result.stderr)
        output = result.stdout + result.stderr
        self.assertNotIn("$mod+shift+t is already bound", output)
        self.assertNotIn("$mod+shift+t: already configured", output)

    def test_rofi_hint_fallback_without_rofi_line(self):
        i3_dir = self.home / ".config" / "i3"
        i3_dir.mkdir(parents=True)
        (i3_dir / "config").write_text(
            "bindsym $mod+d exec dmenu_run\n",
            encoding="utf-8",
        )

        result = run_install([], self.home)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn(
            "rofi: open your app launcher (rofi -show drun) and type 'worktime'.",
            result.stdout,
        )

    def test_polybar_module_already_configured(self):
        polybar_dir = self.home / ".config" / "polybar"
        polybar_dir.mkdir(parents=True)
        (polybar_dir / "config.ini").write_text(
            "[module/worktime]\ntype = custom/script\n",
            encoding="utf-8",
        )

        result = run_install([], self.home)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("polybar: worktime module already configured", result.stdout)
        self.assertNotIn("──── paste into ~/.config/polybar/config.ini ────", result.stdout)
        # No modules-right line in this config at all: the generic hint is
        # printed, and since nothing needed pasting, no restart line.
        self.assertIn(
            "polybar: add 'worktime' to one of your modules-left/center/right lines.",
            result.stdout,
        )
        self.assertNotIn(
            "polybar: then restart it: ~/.config/polybar/launch.sh (or restart i3 with $mod+shift+r).",
            result.stdout,
        )

    def test_polybar_module_present_but_modules_right_needs_worktime(self):
        # The module was pasted in already, but 'worktime' was never added
        # to modules-right: the modules-right check must still run and
        # suggest the replace frame, independently of the module's state.
        polybar_dir = self.home / ".config" / "polybar"
        polybar_dir.mkdir(parents=True)
        (polybar_dir / "config.ini").write_text(
            "[module/worktime]\ntype = custom/script\n"
            "[bar/main]\nmodules-right = shot wifi memory disk volume time powerbtn\n",
            encoding="utf-8",
        )

        result = run_install([], self.home)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("polybar: worktime module already configured", result.stdout)
        self.assertNotIn("──── paste into ~/.config/polybar/config.ini ────", result.stdout)
        self.assertIn(
            "polybar: in [bar/main], REPLACE your existing modules-right line with:",
            result.stdout,
        )
        self.assertIn("──── replace this line in ~/.config/polybar/config.ini ────", result.stdout)
        self.assertIn(
            "modules-right = shot wifi memory disk volume worktime time powerbtn",
            result.stdout,
        )
        self.assertIn(
            "polybar: then restart it: ~/.config/polybar/launch.sh (or restart i3 with $mod+shift+r).",
            result.stdout,
        )

    def test_polybar_module_and_modules_right_both_already_configured(self):
        polybar_dir = self.home / ".config" / "polybar"
        polybar_dir.mkdir(parents=True)
        (polybar_dir / "config.ini").write_text(
            "[module/worktime]\ntype = custom/script\n"
            "[bar/main]\nmodules-right = shot worktime time powerbtn\n",
            encoding="utf-8",
        )

        result = run_install([], self.home)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("polybar: worktime module already configured", result.stdout)
        self.assertIn("polybar: 'worktime' is already in modules-right", result.stdout)
        self.assertNotIn("──── paste into ~/.config/polybar/config.ini ────", result.stdout)
        self.assertNotIn("──── replace this line in ~/.config/polybar/config.ini ────", result.stdout)
        self.assertNotIn(
            "polybar: then restart it: ~/.config/polybar/launch.sh (or restart i3 with $mod+shift+r).",
            result.stdout,
        )

    def test_polybar_modules_right_already_has_worktime(self):
        polybar_dir = self.home / ".config" / "polybar"
        polybar_dir.mkdir(parents=True)
        (polybar_dir / "config.ini").write_text(
            "[bar/main]\nmodules-right = a worktime b\n",
            encoding="utf-8",
        )

        result = run_install([], self.home)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("polybar: 'worktime' is already in modules-right", result.stdout)
        self.assertNotIn("──── replace this line in ~/.config/polybar/config.ini ────", result.stdout)
        # The module itself is still missing, so its frame (and the
        # restart line, since something needs pasting) must still appear.
        self.assertIn("──── paste into ~/.config/polybar/config.ini ────", result.stdout)
        self.assertIn(
            "polybar: then restart it: ~/.config/polybar/launch.sh (or restart i3 with $mod+shift+r).",
            result.stdout,
        )

    def test_polybar_modules_right_without_time_appends_at_end(self):
        polybar_dir = self.home / ".config" / "polybar"
        polybar_dir.mkdir(parents=True)
        (polybar_dir / "config.ini").write_text(
            "[bar/main]\nmodules-right = a b c\n",
            encoding="utf-8",
        )

        result = run_install([], self.home)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("modules-right = a b c worktime", result.stdout)
        self.assertIn(
            "polybar: in [bar/main], REPLACE your existing modules-right line with:",
            result.stdout,
        )
        self.assertIn("──── replace this line in ~/.config/polybar/config.ini ────", result.stdout)

    def test_polybar_modules_right_rebuild_realistic_inline(self):
        # Fixture-independent version of RealDebianConfigTests.test_real_configs_install_cleanly's
        # modules-right assertion, using an inline config with the same tokens.
        polybar_dir = self.home / ".config" / "polybar"
        polybar_dir.mkdir(parents=True)
        (polybar_dir / "config.ini").write_text(
            "[bar/main]\nmodules-right = shot wifi memory disk volume time powerbtn\n",
            encoding="utf-8",
        )

        result = run_install([], self.home)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn(
            "modules-right = shot wifi memory disk volume worktime time powerbtn",
            result.stdout,
        )

    def test_lock_screen_binding_never_suggested_inline(self):
        # Fixture-independent version of RealDebianConfigTests.test_real_i3_config_never_suggests_binding_x.
        i3_dir = self.home / ".config" / "i3"
        i3_dir.mkdir(parents=True)
        (i3_dir / "config").write_text(
            "bindsym $mod+shift+x exec betterlockscreen -l dimblur\n",
            encoding="utf-8",
        )

        result = run_install([], self.home)
        self.assertEqual(result.returncode, 0, result.stderr)
        output = result.stdout + result.stderr
        self.assertNotIn("+x ", output)

    def test_rofi_hint_with_app_launcher_key_inline(self):
        # Fixture-independent version of RealDebianConfigTests.test_real_configs_install_cleanly's
        # rofi-hint assertion, using an inline config with the same app-launcher binding.
        i3_dir = self.home / ".config" / "i3"
        i3_dir.mkdir(parents=True)
        (i3_dir / "config").write_text(
            "bindsym $mod+space exec --no-startup-id ~/.config/rofi/app-launcher/launch.sh\n",
            encoding="utf-8",
        )

        result = run_install([], self.home)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn(
            "rofi: open your app launcher ($mod+space) and type 'worktime'.",
            result.stdout,
        )

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

    def test_uninstall_message_mentions_polybar_and_i3(self):
        result_uninstall = run_install(["--uninstall"], self.home)
        self.assertEqual(result_uninstall.returncode, 0, result_uninstall.stderr)
        output = result_uninstall.stdout + result_uninstall.stderr
        self.assertIn("$mod+shift+t/u/d/w", output)
        self.assertIn("module/worktime", output)
        self.assertIn("polybar", output)

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


def _extract_frames(text):
    """Return a list of (header_line, [content_lines]) for each
    "──── ... ────" / "──── end ────" block in install.sh's output."""
    lines = text.splitlines()
    frames = []
    i = 0
    while i < len(lines):
        line = lines[i]
        if line.startswith("──── ") and line.endswith(" ────") and line != "──── end ────":
            header = line
            content = []
            i += 1
            while i < len(lines) and lines[i] != "──── end ────":
                content.append(lines[i])
                i += 1
            frames.append((header, content))
        i += 1
    return frames


class FrameFormatTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.home = Path(self._tmp.name)

    def test_frames_are_well_formed(self):
        polybar_dir = self.home / ".config" / "polybar"
        polybar_dir.mkdir(parents=True)
        (polybar_dir / "config.ini").write_text(
            "[bar/main]\nmodules-right = shot wifi memory disk volume time powerbtn\n",
            encoding="utf-8",
        )

        result = run_install([], self.home)
        self.assertEqual(result.returncode, 0, result.stderr)
        frames = _extract_frames(result.stdout)
        self.assertTrue(frames, "no frames found in install output")

        for header, content in frames:
            self.assertTrue(content, f"empty frame: {header}")
            for line in content:
                for prefix in ("polybar:", "i3:", "Aerospace:", "Warning:"):
                    self.assertFalse(
                        line.startswith(prefix),
                        f"instruction line inside frame: {line!r}",
                    )
                self.assertNotIn("e.g.", line, f"'e.g.' found inside frame: {line!r}")

        # The number of frame starts equals the number of "──── end ────" lines.
        self.assertEqual(result.stdout.count("──── end ────"), len(frames))

        module_frame = next(
            content for header, content in frames if "paste into ~/.config/polybar/config.ini" in header
        )
        self.assertFalse(
            any(line.startswith("modules-right") for line in module_frame),
            "the polybar module frame must not contain a modules-right line",
        )

        replace_frame = next(
            content
            for header, content in frames
            if "replace this line in ~/.config/polybar/config.ini" in header
        )
        modules_right_lines = [line for line in replace_frame if line.startswith("modules-right =")]
        self.assertEqual(
            len(modules_right_lines),
            1,
            f"the replace frame must contain exactly one modules-right line, got {replace_frame!r}",
        )


@unittest.skipUnless(
    I3_CONFIG_DEBIAN.exists() and POLYBAR_CONFIG.exists(),
    "local-only fixture (personal config, gitignored)",
)
class RealDebianConfigTests(unittest.TestCase):
    """Installer behaviour against Basil's real i3 + polybar configs
    (tests/fixtures/i3-config-debian, tests/fixtures/polybar-config.ini —
    exact copies of new-conf-linux-debian/, which is gitignored). Skipped
    on checkouts where the fixtures aren't present (see module docstring);
    the same behaviour is also covered by inline tests in
    InstallScriptTests that don't depend on the fixtures."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.home = Path(self._tmp.name)
        i3_dir = self.home / ".config" / "i3"
        i3_dir.mkdir(parents=True)
        shutil.copyfile(I3_CONFIG_DEBIAN, i3_dir / "config")
        polybar_dir = self.home / ".config" / "polybar"
        polybar_dir.mkdir(parents=True)
        shutil.copyfile(POLYBAR_CONFIG, polybar_dir / "config.ini")

    def test_real_configs_install_cleanly(self):
        result = run_install([], self.home)
        self.assertEqual(result.returncode, 0, result.stderr)
        output = result.stdout + result.stderr

        self.assertNotIn("Warning:", output)
        self.assertIn(
            "rofi: open your app launcher ($mod+space) and type 'worktime'.",
            result.stdout,
        )
        self.assertIn(
            "modules-right = shot wifi memory disk volume worktime time powerbtn",
            result.stdout,
        )
        self.assertIn(f"exec = {WT} status --short", result.stdout)
        self.assertIn(f"click-left = {WT} dashboard", result.stdout)

    def test_real_i3_config_never_suggests_binding_x(self):
        result = run_install([], self.home)
        self.assertEqual(result.returncode, 0, result.stderr)
        output = result.stdout + result.stderr
        self.assertNotIn("+x ", output)


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
            r"^bindsym \$mod\+shift\+[tudw] exec --no-startup-id @WORKTIME@ (start|stop|dashboard|report week)$"
        )
        for line in lines:
            self.assertRegex(line, pattern)

        # No bindsym line for x: it's commonly the lock screen key on Basil's
        # setup (a comment mentions this, which is fine).
        for line in text.splitlines():
            if line.strip().startswith("bindsym"):
                self.assertNotIn("shift+x", line.lower())


class PolybarTemplateTests(unittest.TestCase):
    def test_template_lines(self):
        text = POLYBAR_TEMPLATE.read_text(encoding="utf-8")
        lines = [line for line in text.splitlines() if line.strip() and not line.strip().startswith(";")]
        self.assertGreaterEqual(len(lines), 1)
        self.assertEqual(lines[0], "[module/worktime]")

    def test_template_parses_as_ini(self):
        cp = configparser.RawConfigParser(comment_prefixes=(";", "#"), interpolation=None)
        cp.read(POLYBAR_TEMPLATE, encoding="utf-8")
        self.assertIn("module/worktime", cp.sections())
        section = cp["module/worktime"]
        self.assertEqual(section["type"], "custom/script")
        self.assertTrue(section["exec"].endswith("status --short"))
        self.assertEqual(section["interval"], "15")
        self.assertTrue(section["click-left"].endswith("dashboard"))


class IconTests(unittest.TestCase):
    def test_icon_is_valid_svg(self):
        self.assertTrue(ICON.is_file())
        self.assertLess(ICON.stat().st_size, 1024)
        tree = ET.parse(ICON)
        root = tree.getroot()
        self.assertTrue(root.tag.endswith("svg"))


@unittest.skipUnless(
    I3_CONFIG_DEBIAN.exists() and POLYBAR_CONFIG.exists(),
    "local-only fixture (personal config, gitignored)",
)
class FixtureTests(unittest.TestCase):
    def test_fixtures_exist(self):
        self.assertTrue(I3_CONFIG_DEBIAN.is_file())
        self.assertTrue(POLYBAR_CONFIG.is_file())


if __name__ == "__main__":
    unittest.main()
