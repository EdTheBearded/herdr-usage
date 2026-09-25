"""Cross-platform paths and the curses-free settings fallback.

Windows is a first-class target now: Herdr uses %APPDATA% there, not XDG, and
Windows CPython ships no `_curses` at all, so the popup needs a line-mode
fallback or it cannot run.
"""

import os
import pathlib
import unittest
from unittest import mock

from herdr_usage import paths, settings_tui


class TestConfigHome(unittest.TestCase):
    def test_windows_uses_appdata(self):
        with mock.patch.object(paths.sys, "platform", "win32"), \
             mock.patch.dict(os.environ, {"APPDATA": r"C:\Users\x\AppData\Roaming"}):
            self.assertEqual(
                paths.config_home(), pathlib.Path(r"C:\Users\x\AppData\Roaming")
            )

    def test_windows_without_appdata_falls_back_under_home(self):
        with mock.patch.object(paths.sys, "platform", "win32"), \
             mock.patch.dict(os.environ, {}, clear=True):
            self.assertEqual(paths.config_home().parts[-2:], ("AppData", "Roaming"))

    def test_macos_uses_application_support(self):
        with mock.patch.object(paths.sys, "platform", "darwin"):
            self.assertEqual(
                paths.config_home().parts[-2:], ("Library", "Application Support")
            )

    def test_linux_honours_xdg(self):
        with mock.patch.object(paths.sys, "platform", "linux"), \
             mock.patch.dict(os.environ, {"XDG_CONFIG_HOME": "/tmp/xdg"}):
            self.assertEqual(paths.config_home(), pathlib.Path("/tmp/xdg"))

    def test_linux_defaults_to_dot_config(self):
        with mock.patch.object(paths.sys, "platform", "linux"), \
             mock.patch.dict(os.environ, {}, clear=True):
            self.assertEqual(paths.config_home().name, ".config")


class TestStateHome(unittest.TestCase):
    def test_windows_uses_localappdata(self):
        with mock.patch.object(paths.sys, "platform", "win32"), \
             mock.patch.dict(os.environ, {"LOCALAPPDATA": r"C:\Users\x\AppData\Local"}):
            self.assertEqual(
                paths.state_home(), pathlib.Path(r"C:\Users\x\AppData\Local")
            )

    def test_linux_honours_xdg_state(self):
        with mock.patch.object(paths.sys, "platform", "linux"), \
             mock.patch.dict(os.environ, {"XDG_STATE_HOME": "/tmp/state"}):
            self.assertEqual(paths.state_home(), pathlib.Path("/tmp/state"))

    def test_state_and_config_differ_on_windows(self):
        # Roaming vs Local: mixing them would sync a cache between machines.
        env = {"APPDATA": r"C:\A\Roaming", "LOCALAPPDATA": r"C:\A\Local"}
        with mock.patch.object(paths.sys, "platform", "win32"), \
             mock.patch.dict(os.environ, env):
            self.assertNotEqual(paths.config_home(), paths.state_home())


class TestHerdrConfig(unittest.TestCase):
    def test_env_override_wins_on_every_platform(self):
        with mock.patch.dict(os.environ, {"HERDR_CONFIG_PATH": "/tmp/h.toml"}):
            self.assertEqual(paths.herdr_config(), pathlib.Path("/tmp/h.toml"))

    def test_windows_path_ends_in_herdr_config(self):
        with mock.patch.object(paths.sys, "platform", "win32"), \
             mock.patch.dict(os.environ, {"APPDATA": r"C:\A"}, clear=True):
            self.assertEqual(paths.herdr_config().parts[-2:], ("herdr", "config.toml"))


if __name__ == "__main__":
    unittest.main()
