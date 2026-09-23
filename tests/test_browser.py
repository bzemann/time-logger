"""Tests for worktime.browser."""

from __future__ import annotations

import subprocess
import unittest
from unittest.mock import patch

from worktime import browser


class BrowserDisabledTests(unittest.TestCase):
    def test_default_enabled(self):
        with patch.dict("os.environ", {}, clear=False):
            import os

            os.environ.pop("WORKTIME_NO_BROWSER", None)
            self.assertFalse(browser.browser_disabled())

    def test_true_values(self):
        for value in ["1", "true", "yes", "TRUE", " 1 ", "Yes"]:
            with patch.dict("os.environ", {"WORKTIME_NO_BROWSER": value}):
                self.assertTrue(browser.browser_disabled(), value)

    def test_false_values(self):
        for value in ["0", "false", "no", ""]:
            with patch.dict("os.environ", {"WORKTIME_NO_BROWSER": value}):
                self.assertFalse(browser.browser_disabled(), value)


class BuildOpenCommandTests(unittest.TestCase):
    def test_darwin_uses_open(self):
        with patch("worktime.browser.shutil.which", return_value="/usr/bin/open"):
            cmd = browser.build_open_command("darwin", "http://x")
        self.assertEqual(cmd, ["/usr/bin/open", "http://x"])

    def test_darwin_fallback_path(self):
        with patch("worktime.browser.shutil.which", return_value=None):
            cmd = browser.build_open_command("darwin23", "http://x")
        self.assertEqual(cmd, ["/usr/bin/open", "http://x"])

    def test_linux_with_xdg_open(self):
        with patch("worktime.browser.shutil.which", return_value="/usr/bin/xdg-open"):
            cmd = browser.build_open_command("linux", "http://x")
        self.assertEqual(cmd, ["/usr/bin/xdg-open", "http://x"])

    def test_linux_without_xdg_open(self):
        with patch("worktime.browser.shutil.which", return_value=None):
            cmd = browser.build_open_command("linux", "http://x")
        self.assertIsNone(cmd)

    def test_other_platform(self):
        cmd = browser.build_open_command("win32", "http://x")
        self.assertIsNone(cmd)


class OpenUrlTests(unittest.TestCase):
    def test_success_does_not_call_webbrowser(self):
        with patch.dict("os.environ", {"WORKTIME_NO_BROWSER": "0"}):
            with patch(
                "worktime.browser.build_open_command",
                return_value=["open", "http://x"],
            ), patch(
                "worktime.browser.subprocess.run",
                return_value=subprocess.CompletedProcess(args=[], returncode=0),
            ) as mock_run, patch(
                "worktime.browser.webbrowser.open"
            ) as mock_wb:
                result = browser.open_url("http://x", platform="darwin")

        self.assertTrue(result)
        mock_run.assert_called_once()
        mock_wb.assert_not_called()

    def test_nonzero_returncode_falls_back_to_webbrowser(self):
        with patch.dict("os.environ", {"WORKTIME_NO_BROWSER": "0"}):
            with patch(
                "worktime.browser.build_open_command",
                return_value=["open", "http://x"],
            ), patch(
                "worktime.browser.subprocess.run",
                return_value=subprocess.CompletedProcess(args=[], returncode=1),
            ), patch(
                "worktime.browser.webbrowser.open", return_value=True
            ) as mock_wb:
                result = browser.open_url("http://x", platform="darwin")

        self.assertTrue(result)
        mock_wb.assert_called_once_with("http://x")

    def test_oserror_falls_back_to_webbrowser(self):
        with patch.dict("os.environ", {"WORKTIME_NO_BROWSER": "0"}):
            with patch(
                "worktime.browser.build_open_command",
                return_value=["open", "http://x"],
            ), patch(
                "worktime.browser.subprocess.run", side_effect=OSError("boom")
            ), patch(
                "worktime.browser.webbrowser.open", return_value=True
            ) as mock_wb:
                result = browser.open_url("http://x", platform="darwin")

        self.assertTrue(result)
        mock_wb.assert_called_once_with("http://x")

    def test_timeout_falls_back_to_webbrowser(self):
        with patch.dict("os.environ", {"WORKTIME_NO_BROWSER": "0"}):
            with patch(
                "worktime.browser.build_open_command",
                return_value=["open", "http://x"],
            ), patch(
                "worktime.browser.subprocess.run",
                side_effect=subprocess.TimeoutExpired(cmd="open", timeout=10),
            ), patch(
                "worktime.browser.webbrowser.open", return_value=True
            ) as mock_wb:
                result = browser.open_url("http://x", platform="darwin")

        self.assertTrue(result)
        mock_wb.assert_called_once_with("http://x")

    def test_webbrowser_raising_returns_false(self):
        with patch.dict("os.environ", {"WORKTIME_NO_BROWSER": "0"}):
            with patch(
                "worktime.browser.build_open_command", return_value=None
            ), patch(
                "worktime.browser.webbrowser.open", side_effect=Exception("boom")
            ):
                result = browser.open_url("http://x", platform="win32")

        self.assertFalse(result)

    def test_disabled_does_nothing(self):
        with patch.dict("os.environ", {"WORKTIME_NO_BROWSER": "1"}):
            with patch("worktime.browser.subprocess.run") as mock_run, patch(
                "worktime.browser.webbrowser.open"
            ) as mock_wb:
                result = browser.open_url("http://x", platform="darwin")

        self.assertFalse(result)
        mock_run.assert_not_called()
        mock_wb.assert_not_called()


if __name__ == "__main__":
    unittest.main()
