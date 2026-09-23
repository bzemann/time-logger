"""Unit tests for worktime.notify.

These tests must never trigger a real desktop notification: subprocess.run
is always mocked wherever it could plausibly be reached.
"""

import contextlib
import io
import os
import subprocess
import unittest
from unittest.mock import patch, MagicMock

from worktime import notify


class BuildCommandDarwinTests(unittest.TestCase):
    def test_darwin_command_structure(self):
        with patch.object(notify.shutil, "which", return_value="/usr/bin/osascript"):
            cmd = notify.build_command("darwin", "Title Here", "Body Here")
        self.assertTrue(cmd[0].endswith("osascript"))
        self.assertIn("on run argv", cmd)
        self.assertIn(
            "display notification (item 2 of argv) with title (item 1 of argv)",
            cmd,
        )
        self.assertIn("end run", cmd)
        self.assertEqual(cmd[-2], "Title Here")
        self.assertEqual(cmd[-1], "Body Here")

    def test_darwin_command_uses_which(self):
        with patch.object(notify.shutil, "which", return_value="/opt/homebrew/bin/osascript"):
            cmd = notify.build_command("darwin", "t", "b")
        self.assertEqual(cmd[0], "/opt/homebrew/bin/osascript")

    def test_darwin_command_falls_back_when_which_none(self):
        with patch.object(notify.shutil, "which", return_value=None):
            cmd = notify.build_command("darwin", "t", "b")
        self.assertEqual(cmd[0], "/usr/bin/osascript")

    def test_darwin_special_characters_passed_through_unchanged(self):
        title = 'He said "hi"'
        body = "it's 5 o'clock \\ ok"
        with patch.object(notify.shutil, "which", return_value="/usr/bin/osascript"):
            cmd = notify.build_command("darwin", title, body)
        # Title and body appear byte-identical as the last two arguments.
        self.assertEqual(cmd[-2], title)
        self.assertEqual(cmd[-1], body)
        # They must not be embedded inside any of the -e script arguments.
        script_args = [
            cmd[i + 1] for i, item in enumerate(cmd) if item == "-e"
        ]
        for script in script_args:
            self.assertNotIn(title, script)
            self.assertNotIn(body, script)


class BuildCommandLinuxTests(unittest.TestCase):
    def test_linux_command_when_notify_send_present(self):
        with patch.object(notify.shutil, "which", return_value="/usr/bin/notify-send"):
            cmd = notify.build_command("linux", "Title", "Body")
        self.assertEqual(
            cmd,
            ["/usr/bin/notify-send", "-a", "WorkTime", "-t", "5000", "Title", "Body"],
        )

    def test_linux_command_none_when_notify_send_missing(self):
        with patch.object(notify.shutil, "which", return_value=None):
            cmd = notify.build_command("linux", "Title", "Body")
        self.assertIsNone(cmd)


class BuildCommandOtherPlatformTests(unittest.TestCase):
    def test_win32_returns_none(self):
        self.assertIsNone(notify.build_command("win32", "t", "b"))

    def test_freebsd_returns_none(self):
        self.assertIsNone(notify.build_command("freebsd", "t", "b"))


class NotifyDarwinTests(unittest.TestCase):
    @patch.dict(os.environ, {}, clear=False)
    def test_notify_darwin_success(self):
        os.environ.pop("WORKTIME_NO_NOTIFY", None)
        fake_result = MagicMock(returncode=0)
        with patch.object(notify.shutil, "which", return_value="/usr/bin/osascript"), \
                patch.object(notify.subprocess, "run", return_value=fake_result) as mock_run:
            stderr = io.StringIO()
            with contextlib.redirect_stderr(stderr):
                result = notify.notify("Title", "Body", platform="darwin")

        self.assertTrue(result)
        self.assertEqual(stderr.getvalue(), "")
        mock_run.assert_called_once()
        args, kwargs = mock_run.call_args
        expected_cmd = notify.build_command("darwin", "Title", "Body")
        self.assertEqual(args[0], expected_cmd)
        self.assertEqual(kwargs.get("check"), False)
        self.assertEqual(kwargs.get("capture_output"), True)
        self.assertEqual(kwargs.get("timeout"), 5)


class NotifyFailureTests(unittest.TestCase):
    @patch.dict(os.environ, {}, clear=False)
    def test_notify_nonzero_returncode_falls_back(self):
        os.environ.pop("WORKTIME_NO_NOTIFY", None)
        fake_result = MagicMock(returncode=1)
        with patch.object(notify.shutil, "which", return_value="/usr/bin/osascript"), \
                patch.object(notify.subprocess, "run", return_value=fake_result):
            stderr = io.StringIO()
            with contextlib.redirect_stderr(stderr):
                result = notify.notify("WorkTime", "body text", platform="darwin")

        self.assertFalse(result)
        self.assertIn("WorkTime: body text", stderr.getvalue())

    @patch.dict(os.environ, {}, clear=False)
    def test_notify_filenotfounderror_falls_back(self):
        os.environ.pop("WORKTIME_NO_NOTIFY", None)
        with patch.object(notify.shutil, "which", return_value="/usr/bin/osascript"), \
                patch.object(notify.subprocess, "run", side_effect=FileNotFoundError()):
            stderr = io.StringIO()
            with contextlib.redirect_stderr(stderr):
                result = notify.notify("WorkTime", "body text", platform="darwin")

        self.assertFalse(result)
        self.assertIn("WorkTime: body text", stderr.getvalue())

    @patch.dict(os.environ, {}, clear=False)
    def test_notify_oserror_falls_back(self):
        os.environ.pop("WORKTIME_NO_NOTIFY", None)
        with patch.object(notify.shutil, "which", return_value="/usr/bin/osascript"), \
                patch.object(notify.subprocess, "run", side_effect=OSError()):
            stderr = io.StringIO()
            with contextlib.redirect_stderr(stderr):
                result = notify.notify("WorkTime", "body text", platform="darwin")

        self.assertFalse(result)
        self.assertIn("WorkTime: body text", stderr.getvalue())

    @patch.dict(os.environ, {}, clear=False)
    def test_notify_timeout_falls_back(self):
        os.environ.pop("WORKTIME_NO_NOTIFY", None)
        cmd = ["osascript"]
        with patch.object(notify.shutil, "which", return_value="/usr/bin/osascript"), \
                patch.object(
                    notify.subprocess,
                    "run",
                    side_effect=subprocess.TimeoutExpired(cmd, 5),
                ):
            stderr = io.StringIO()
            with contextlib.redirect_stderr(stderr):
                result = notify.notify("WorkTime", "body text", platform="darwin")

        self.assertFalse(result)
        self.assertIn("WorkTime: body text", stderr.getvalue())

    @patch.dict(os.environ, {}, clear=False)
    def test_notify_linux_without_notify_send(self):
        os.environ.pop("WORKTIME_NO_NOTIFY", None)
        with patch.object(notify.shutil, "which", return_value=None), \
                patch.object(notify.subprocess, "run") as mock_run:
            stderr = io.StringIO()
            with contextlib.redirect_stderr(stderr):
                result = notify.notify("WorkTime", "body text", platform="linux")

        self.assertFalse(result)
        self.assertIn("WorkTime: body text", stderr.getvalue())
        mock_run.assert_not_called()

    @patch.dict(os.environ, {}, clear=False)
    def test_notify_unknown_platform(self):
        os.environ.pop("WORKTIME_NO_NOTIFY", None)
        with patch.object(notify.subprocess, "run") as mock_run:
            stderr = io.StringIO()
            with contextlib.redirect_stderr(stderr):
                result = notify.notify("WorkTime", "body text", platform="win32")

        self.assertFalse(result)
        self.assertIn("WorkTime: body text", stderr.getvalue())
        mock_run.assert_not_called()


class NotifyDisabledTests(unittest.TestCase):
    def _assert_disabled(self, value):
        with patch.dict(os.environ, {"WORKTIME_NO_NOTIFY": value}, clear=False):
            with patch.object(notify.subprocess, "run") as mock_run:
                stderr = io.StringIO()
                with contextlib.redirect_stderr(stderr):
                    result = notify.notify("Title", "Body", platform="darwin")
            self.assertFalse(result)
            mock_run.assert_not_called()
            self.assertEqual(stderr.getvalue(), "")

    def test_disabled_1(self):
        self._assert_disabled("1")

    def test_disabled_true(self):
        self._assert_disabled("true")

    def test_disabled_yes_uppercase(self):
        self._assert_disabled("YES")

    def test_disabled_yes_with_whitespace(self):
        self._assert_disabled(" yes ")

    def test_not_disabled_0(self):
        with patch.dict(os.environ, {"WORKTIME_NO_NOTIFY": "0"}, clear=False):
            fake_result = MagicMock(returncode=0)
            with patch.object(notify.shutil, "which", return_value="/usr/bin/osascript"), \
                    patch.object(notify.subprocess, "run", return_value=fake_result) as mock_run:
                notify.notify("Title", "Body", platform="darwin")
            mock_run.assert_called_once()

    def test_not_disabled_empty(self):
        with patch.dict(os.environ, {"WORKTIME_NO_NOTIFY": ""}, clear=False):
            fake_result = MagicMock(returncode=0)
            with patch.object(notify.shutil, "which", return_value="/usr/bin/osascript"), \
                    patch.object(notify.subprocess, "run", return_value=fake_result) as mock_run:
                notify.notify("Title", "Body", platform="darwin")
            mock_run.assert_called_once()


class NotifyDefaultPlatformTests(unittest.TestCase):
    @patch.dict(os.environ, {}, clear=False)
    def test_notify_uses_sys_platform_when_none(self):
        os.environ.pop("WORKTIME_NO_NOTIFY", None)
        with patch.object(notify.sys, "platform", "linux"), \
                patch.object(notify.shutil, "which", return_value=None), \
                patch.object(notify.subprocess, "run") as mock_run:
            stderr = io.StringIO()
            with contextlib.redirect_stderr(stderr):
                result = notify.notify("WorkTime", "body text")

        self.assertFalse(result)
        self.assertIn("WorkTime: body text", stderr.getvalue())
        mock_run.assert_not_called()


class NotificationsDisabledFunctionTests(unittest.TestCase):
    def test_true_values(self):
        for value in ["1", "true", "TRUE", "True", "yes", "YES", " yes "]:
            with patch.dict(os.environ, {"WORKTIME_NO_NOTIFY": value}, clear=False):
                self.assertTrue(notify.notifications_disabled())

    def test_false_values(self):
        for value in ["0", "", "no", "false", "2"]:
            with patch.dict(os.environ, {"WORKTIME_NO_NOTIFY": value}, clear=False):
                self.assertFalse(notify.notifications_disabled())

    def test_absent(self):
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("WORKTIME_NO_NOTIFY", None)
            self.assertFalse(notify.notifications_disabled())


if __name__ == "__main__":
    unittest.main()
