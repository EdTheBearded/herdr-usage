"""T3 — severity band, burn-rate ETA, window labels."""

import unittest

from herdr_usage.model import Gauge, band, burn_eta, window_label


class TestBand(unittest.TestCase):
    def test_thresholds(self):
        for pct, expect in ((0, "ok"), (69, "ok"), (70, "warn"), (89, "warn"),
                            (90, "crit"), (100, "crit")):
            self.assertEqual(band(Gauge("5h", "5h", pct)), expect, pct)

    def test_custom_thresholds(self):
        g = Gauge("5h", "5h", 55)
        self.assertEqual(band(g, warn=50, crit=80), "warn")
        self.assertEqual(band(g, warn=60, crit=80), "ok")

    def test_blocked_is_always_crit(self):
        self.assertEqual(band(Gauge("5h", "5h", 3, blocked=True)), "crit")
        self.assertEqual(band(Gauge("5h", "5h", None, blocked=True)), "crit")

    def test_none_pct_is_none_band(self):
        self.assertEqual(band(Gauge("5h", "5h", None)), "none")

    def test_provider_severity_overrides_thresholds(self):
        # Claude sends its own band; trust it over our numbers
        g = Gauge("5h", "5h", 12, severity="critical")
        self.assertEqual(band(g), "crit")
        g = Gauge("5h", "5h", 95, severity="normal")
        self.assertEqual(band(g), "ok")
        g = Gauge("5h", "5h", 5, severity="warning")
        self.assertEqual(band(g), "warn")

    def test_unknown_severity_falls_back_to_thresholds(self):
        self.assertEqual(band(Gauge("5h", "5h", 95, severity="banana")), "crit")


class TestBurnEta(unittest.TestCase):
    def test_normal_case(self):
        # 31% -> 41% in 600s => 10%/600s => 59% left => ~3540s
        eta = burn_eta(pct=41, prev_pct=31, dt=600, resets_at=None, now=0)
        self.assertAlmostEqual(eta, 3540, delta=5)

    def test_suppressed_when_dt_too_small(self):
        self.assertIsNone(burn_eta(pct=41, prev_pct=31, dt=30, resets_at=None, now=0))

    def test_suppressed_when_not_increasing(self):
        self.assertIsNone(burn_eta(pct=31, prev_pct=31, dt=600, resets_at=None, now=0))
        self.assertIsNone(burn_eta(pct=20, prev_pct=31, dt=600, resets_at=None, now=0))

    def test_suppressed_when_eta_beyond_reset(self):
        # window resets in 100s; running out in ~3540s is meaningless
        self.assertIsNone(
            burn_eta(pct=41, prev_pct=31, dt=600, resets_at=100, now=0)
        )

    def test_kept_when_eta_before_reset(self):
        eta = burn_eta(pct=41, prev_pct=31, dt=600, resets_at=99999, now=0)
        self.assertIsNotNone(eta)

    def test_missing_previous_sample(self):
        self.assertIsNone(burn_eta(pct=41, prev_pct=None, dt=600, resets_at=None, now=0))

    def test_already_full(self):
        self.assertIsNone(burn_eta(pct=100, prev_pct=90, dt=600, resets_at=None, now=0))


class TestWindowLabel(unittest.TestCase):
    def test_known_windows(self):
        self.assertEqual(window_label(300), "5h")
        self.assertEqual(window_label(10080), "wk")
        self.assertEqual(window_label(43200), "mo")

    def test_derived_window(self):
        # unknown duration must still produce something short, not crash
        self.assertEqual(window_label(60), "1h")
        self.assertEqual(window_label(1440), "1d")
        self.assertTrue(0 < len(window_label(77)) <= 6)

    def test_none(self):
        self.assertEqual(window_label(None), "?")


if __name__ == "__main__":
    unittest.main()
