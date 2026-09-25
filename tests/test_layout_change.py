"""Layout-change detection after a settings save.

Context: the plugin runs on the Herdr server, but Herdr draws the sidebar on
the CLIENT. Saving settings updates the server's config automatically; a
remote client keeps its own config and shows nothing new until that block is
updated too. These tests pin the honest half of that: we detect a change in
OUR token set, and never claim to know a remote client's state.
"""

import json
import os
import tempfile
import unittest

from herdr_usage import config, sidebar


class TestTokensIn(unittest.TestCase):
    def test_finds_tokens_with_and_without_dollar(self):
        self.assertEqual(
            sidebar.tokens_in('["$uq_model", { token = "uq_g5h_ok" }]'),
            {"uq_model", "uq_g5h_ok"},
        )

    def test_ignores_non_uq_tokens(self):
        found = sidebar.tokens_in('["state_icon", "agent", "$uq_model"]')
        self.assertEqual(found, {"uq_model"})

    def test_empty_block_has_no_tokens(self):
        self.assertEqual(sidebar.tokens_in(""), set())

    def test_generated_block_exposes_enabled_extras(self):
        cfg = config._merge(config.DEFAULTS, {"display": {"show_plan": True}})
        self.assertIn("uq_plan", sidebar.tokens_in(sidebar.generate(cfg)))

    def test_disabled_extra_is_absent(self):
        cfg = config._merge(config.DEFAULTS, {"display": {"show_plan": False}})
        self.assertNotIn("uq_plan", sidebar.tokens_in(sidebar.generate(cfg)))


class TestLayoutChanged(unittest.TestCase):
    """state_dir() is env-driven, so each test gets an isolated one."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._prev = os.environ.get("HERDR_PLUGIN_STATE_DIR")
        os.environ["HERDR_PLUGIN_STATE_DIR"] = self._tmp.name

    def tearDown(self):
        if self._prev is None:
            os.environ.pop("HERDR_PLUGIN_STATE_DIR", None)
        else:
            os.environ["HERDR_PLUGIN_STATE_DIR"] = self._prev
        self._tmp.cleanup()

    def test_first_run_does_not_report_a_change(self):
        # Nothing to compare against, and install() has just written the
        # block anyway -- warning here would cry wolf on every fresh setup.
        self.assertFalse(sidebar.layout_changed(config.DEFAULTS))

    def test_first_run_records_the_baseline(self):
        sidebar.layout_changed(config.DEFAULTS)
        stamp = sidebar.state_dir() / "layout-tokens.json"
        self.assertTrue(stamp.exists())
        self.assertIn("uq_model", json.loads(stamp.read_text()))

    def test_unchanged_layout_reports_no_change(self):
        sidebar.layout_changed(config.DEFAULTS)
        self.assertFalse(sidebar.layout_changed(config.DEFAULTS))

    def test_enabling_an_extra_reports_a_change(self):
        sidebar.layout_changed(config.DEFAULTS)
        cfg = config._merge(config.DEFAULTS, {"display": {"show_plan": True}})
        self.assertTrue(sidebar.layout_changed(cfg))

    def test_change_is_reported_once_not_repeatedly(self):
        sidebar.layout_changed(config.DEFAULTS)
        cfg = config._merge(config.DEFAULTS, {"display": {"show_plan": True}})
        self.assertTrue(sidebar.layout_changed(cfg))
        self.assertFalse(sidebar.layout_changed(cfg))

    def test_reverting_reports_a_change_again(self):
        sidebar.layout_changed(config.DEFAULTS)
        cfg = config._merge(config.DEFAULTS, {"display": {"show_plan": True}})
        sidebar.layout_changed(cfg)
        self.assertTrue(sidebar.layout_changed(config.DEFAULTS))

    def test_adding_a_gauge_reports_a_change(self):
        sidebar.layout_changed(config.DEFAULTS)
        cfg = config._merge(config.DEFAULTS, {"display": {"gauges": ["5h", "wk", "mo"]}})
        self.assertTrue(sidebar.layout_changed(cfg))

    def test_non_layout_setting_reports_no_change(self):
        # Thresholds only recolour existing tokens: the token NAMES are the
        # same, so no client needs a new block.
        sidebar.layout_changed(config.DEFAULTS)
        cfg = config._merge(config.DEFAULTS, {"thresholds": {"warn": 50}})
        self.assertFalse(sidebar.layout_changed(cfg))

    def test_bar_style_reports_no_change(self):
        # Bar glyphs live inside the token VALUE, not its name.
        sidebar.layout_changed(config.DEFAULTS)
        cfg = config._merge(config.DEFAULTS, {"display": {"bar_set": "dots"}})
        self.assertFalse(sidebar.layout_changed(cfg))

    def test_corrupt_stamp_is_treated_as_first_run(self):
        stamp = sidebar.state_dir() / "layout-tokens.json"
        stamp.parent.mkdir(parents=True, exist_ok=True)
        stamp.write_text("{not json", encoding="utf-8")
        self.assertFalse(sidebar.layout_changed(config.DEFAULTS))
        self.assertIn("uq_model", json.loads(stamp.read_text()))


if __name__ == "__main__":
    unittest.main()
