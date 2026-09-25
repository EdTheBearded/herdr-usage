"""T1, T2, T4 — bar rendering, durations/ETA, token dict."""

import unittest

from herdr_usage.model import Gauge
from herdr_usage.render import (
    BAR_SETS,
    NO_DATA,
    bar,
    build_tokens,
    fmt_duration,
    gauge_text,
)


class TestBar(unittest.TestCase):
    """T1 — the fill rule that makes 'full bar == exhausted' honest."""

    def test_empty_and_full(self):
        self.assertEqual(bar(0, 10), "░" * 10)
        self.assertEqual(bar(100, 10), "█" * 10)
        self.assertEqual(bar(100.0, 10), "█" * 10)

    def test_tiny_nonzero_shows_one_cell(self):
        # never round a real, non-zero usage down to an empty bar
        self.assertEqual(bar(0.4, 10).count("█"), 1)
        self.assertEqual(bar(0.01, 10).count("█"), 1)
        self.assertEqual(bar(1, 10).count("█"), 1)

    def test_99_9_is_not_full(self):
        # THE assertion: only 100% may render a full bar
        self.assertEqual(bar(99.9, 10).count("█"), 9)
        self.assertEqual(bar(99, 10).count("█"), 9)
        self.assertNotEqual(bar(99.9, 10), bar(100, 10))

    def test_midrange(self):
        self.assertEqual(bar(31, 10).count("█"), 4)
        self.assertEqual(bar(82, 10).count("█"), 9)
        self.assertEqual(bar(50, 10).count("█"), 5)

    def test_every_set_has_exact_width(self):
        for name in BAR_SETS:
            for pct in (0, 0.4, 31, 82, 99.9, 100):
                self.assertEqual(
                    len(bar(pct, 10, name)), 10, f"{name} @ {pct}"
                )

    def test_bar_cells_respected(self):
        for cells in (4, 6, 10, 16, 24):
            self.assertEqual(len(bar(55, cells)), cells)

    def test_out_of_range_clamped(self):
        self.assertEqual(bar(-5, 10), "░" * 10)
        self.assertEqual(bar(150, 10), "█" * 10)

    def test_unknown_set_falls_back(self):
        self.assertEqual(len(bar(50, 10, "nope")), 10)


class TestGaugeText(unittest.TestCase):
    """T1 — None is 'no data', never a 0% bar."""

    def test_none_pct_is_no_data(self):
        g = Gauge(key="5h", label="5h", pct=None)
        txt = gauge_text(g)
        self.assertIn(NO_DATA, txt)
        self.assertNotIn("█", txt)
        self.assertNotIn("0%", txt)

    def test_normal(self):
        g = Gauge(key="5h", label="5h", pct=31)
        self.assertEqual(gauge_text(g), "5h ████░░░░░░  31%")

    def test_blocked_shows_marker_not_percent(self):
        g = Gauge(key="5h", label="5h", pct=100, blocked=True)
        txt = gauge_text(g)
        # Bare U+26D4, no VS16: the emoji form is absent from FiraCode Nerd
        # Font and took an extra cell in a live pane, wrapping the row.
        self.assertIn("⛔", txt)
        self.assertNotIn("\ufe0f", txt)
        self.assertEqual(txt.count("█"), 10)

    def test_stale_marker_appended(self):
        g = Gauge(key="5h", label="5h", pct=31)
        self.assertTrue(gauge_text(g, stale=True).endswith(" ~"))
        self.assertFalse(gauge_text(g, stale=False).endswith(" ~"))

    def test_scoped_label_included(self):
        g = Gauge(key="scoped:Fable", label="wk Fable", pct=0)
        self.assertTrue(gauge_text(g).startswith("wk Fable "))

    def test_never_exceeds_80_chars(self):
        g = Gauge(key="scoped:" + "X" * 60, label="wk " + "X" * 60, pct=99)
        self.assertLessEqual(len(gauge_text(g, stale=True)), 80)


class TestDuration(unittest.TestCase):
    """T2"""

    def test_formats(self):
        self.assertEqual(fmt_duration(74), "1m")
        self.assertEqual(fmt_duration(4940), "1h22m")
        self.assertEqual(fmt_duration(90000), "1d1h")
        self.assertEqual(fmt_duration(59), "0m")

    def test_negative_clamps(self):
        self.assertEqual(fmt_duration(-10), "0m")

    def test_none(self):
        self.assertIsNone(fmt_duration(None))


class TestBuildTokens(unittest.TestCase):
    """T4 — exactly one severity variant per gauge, rest cleared."""

    def setUp(self):
        self.gauges = [
            Gauge(key="5h", label="5h", pct=31),
            Gauge(key="wk", label="wk", pct=82),
        ]

    def test_one_variant_set_others_cleared(self):
        tokens, clear = build_tokens(self.gauges)
        self.assertIn("uq_g5h_ok", tokens)
        for suffix in ("warn", "crit", "none"):
            self.assertNotIn(f"uq_g5h_{suffix}", tokens)
            self.assertIn(f"uq_g5h_{suffix}", clear)

    def test_band_selection(self):
        tokens, _ = build_tokens([Gauge(key="5h", label="5h", pct=82)])
        self.assertIn("uq_g5h_warn", tokens)
        tokens, _ = build_tokens([Gauge(key="5h", label="5h", pct=95)])
        self.assertIn("uq_g5h_crit", tokens)
        tokens, _ = build_tokens([Gauge(key="5h", label="5h", pct=None)])
        self.assertIn("uq_g5h_none", tokens)

    def test_key_slugified_and_valid(self):
        import re

        tokens, clear = build_tokens(
            [Gauge(key="scoped:Fable", label="wk Fable", pct=0)]
        )
        for name in list(tokens) + clear:
            self.assertRegex(name, r"^[A-Za-z0-9_-]{1,32}$", name)
        self.assertIn("uq_gscoped_Fable_ok", tokens)

    def test_long_keys_bounded_and_collision_free(self):
        """Herdr rejects token names over 32 chars; two long keys must not
        collapse into the same token."""
        import re

        a, _ = build_tokens([Gauge(key="scoped:" + "a" * 60, label="x", pct=5)])
        b, _ = build_tokens(
            [Gauge(key="scoped:" + "a" * 60 + "OTHER", label="x", pct=5)]
        )
        na, nb = list(a)[0], list(b)[0]
        self.assertRegex(na, r"^[A-Za-z0-9_-]{1,32}$")
        self.assertRegex(nb, r"^[A-Za-z0-9_-]{1,32}$")
        self.assertNotEqual(na, nb)

    def test_all_values_within_80_chars(self):
        tokens, _ = build_tokens(
            self.gauges, extras={"model": "Opus", "plan": "team"}
        )
        for k, v in tokens.items():
            self.assertLessEqual(len(v), 80, k)

    def test_extras_absent_when_off(self):
        tokens, _ = build_tokens(self.gauges, extras={})
        self.assertNotIn("uq_model", tokens)
        self.assertNotIn("uq_plan", tokens)

    def test_extras_present_when_on(self):
        tokens, _ = build_tokens(
            self.gauges,
            extras={"model": "Opus", "burn": "⚡ 1h20m · ↻ 2h14m", "extra": "★3"},
        )
        self.assertEqual(tokens["uq_model"], "Opus")
        self.assertIn("uq_burn", tokens)
        self.assertIn("uq_extra", tokens)

    def test_token_budget(self):
        many = [Gauge(key=f"g{i}", label=f"g{i}", pct=10 * i) for i in range(4)]
        tokens, clear = build_tokens(
            many, extras={"model": "Opus", "plan": "team", "burn": "x", "extra": "y"}
        )
        self.assertLessEqual(len(tokens) + len(clear), 32)


if __name__ == "__main__":
    unittest.main()
