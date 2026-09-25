"""The curses-free settings menu.

Windows CPython ships no `_curses`, so the popup needs a line-mode fallback
or it cannot run there at all.
"""

import unittest

from herdr_usage import config, settings_tui


class TestPlainSettingsFallback(unittest.TestCase):
    """Same reducer as curses, so only the I/O shell needs covering."""

    def _run(self, keys):
        import io

        from herdr_usage import config

        return settings_tui.run_plain(
            config.DEFAULTS, stdin=io.StringIO(keys), stdout=io.StringIO()
        )

    def test_quit_returns_none(self):
        self.assertIsNone(self._run("q\n"))

    def test_save_returns_a_config(self):
        self.assertIsInstance(self._run("s\n"), dict)

    def test_eof_cancels_rather_than_saving(self):
        # A closed pipe must never be read as "the user pressed save".
        self.assertIsNone(self._run(""))

    def test_navigation_then_save_applies_the_edit(self):
        # j moves to "Bar width", space steps it, s saves.
        out = self._run("j \ns\n")
        self.assertIsInstance(out, dict)

if __name__ == "__main__":
    unittest.main()
