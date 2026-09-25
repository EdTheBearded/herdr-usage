"""Baseline retention for the burn-rate ETA.

The reported bug: burn never appeared. Root cause was not burn_eta -- its
gates are correct -- but the cache advancing the prev_* baseline on every
forced refresh. A settings save forces one, so two saves a few seconds apart
pinned the sample window under burn_eta's 60s floor, permanently.
"""

import unittest

from herdr_usage import cache
from herdr_usage.model import burn_eta


def entry(at, pct, prev_pct=None, prev_at=None):
    g = {"key": "5h", "pct": pct, "resets_at": None}
    if prev_pct is not None:
        g["prev_pct"] = prev_pct
    if prev_at is not None:
        g["prev_fetched_at"] = prev_at
    return {"claude": {"provider": "claude", "fetched_at": at, "gauges": [g]}}


def gauge(data):
    return data["claude"]["gauges"][0]


class TestBaselineRetention(unittest.TestCase):
    def test_first_save_has_no_baseline(self):
        out = cache._with_previous({}, entry(100.0, 10.0))
        self.assertIsNone(gauge(out)["prev_pct"])

    def test_old_baseline_is_replaced(self):
        old = entry(100.0, 10.0, prev_pct=5.0, prev_at=0.0)
        out = cache._with_previous(old, entry(400.0, 30.0))
        self.assertEqual(gauge(out)["prev_pct"], 10.0)
        self.assertEqual(gauge(out)["prev_fetched_at"], 100.0)

    def test_young_baseline_is_kept(self):
        """Two forced refreshes 20s apart must not destroy a usable slope."""
        old = entry(100.0, 10.0, prev_pct=5.0, prev_at=40.0)
        out = cache._with_previous(old, entry(120.0, 12.0))
        self.assertEqual(gauge(out)["prev_pct"], 5.0)
        self.assertEqual(gauge(out)["prev_fetched_at"], 40.0)

    def test_kept_baseline_still_yields_an_eta(self):
        old = entry(100.0, 10.0, prev_pct=5.0, prev_at=40.0)
        out = cache._with_previous(old, entry(120.0, 12.0))
        g = out["claude"]["gauges"][0]
        eta = burn_eta(g["pct"], g["prev_pct"],
                       120.0 - g["prev_fetched_at"], None, 120.0)
        self.assertIsNotNone(eta, "a retained baseline must produce an ETA")

    def test_advancing_baseline_would_have_killed_the_eta(self):
        """Pins the regression: the old behaviour yields dt below the floor."""
        dt = 20.0
        self.assertIsNone(burn_eta(12.0, 10.0, dt, None, 120.0),
                          "20s sample must not produce an ETA")

    def test_repeated_forced_saves_do_not_drift_the_baseline(self):
        data = entry(100.0, 10.0)
        for i in range(1, 6):  # five saves, 10s apart
            data = cache._with_previous(data, entry(100.0 + i * 10, 10.0 + i))
        g = gauge(data)
        self.assertEqual(g["prev_fetched_at"], 100.0,
                         "baseline drifted despite each step being <60s")

    def test_baseline_advances_once_the_span_is_long_enough(self):
        data = entry(100.0, 10.0)
        data = cache._with_previous(data, entry(130.0, 11.0))   # +30s, keep
        data = cache._with_previous(data, entry(200.0, 13.0))   # 100s, replace
        self.assertEqual(gauge(data)["prev_fetched_at"], 130.0)

    def test_cache_hit_still_keeps_prev(self):
        old = entry(100.0, 10.0, prev_pct=5.0, prev_at=0.0)
        out = cache._with_previous(old, entry(100.0, 10.0))
        self.assertEqual(gauge(out)["prev_pct"], 5.0)

    def test_missing_fetched_at_does_not_crash(self):
        old = entry(100.0, 10.0, prev_pct=5.0, prev_at=40.0)
        broken = {"claude": {"gauges": [{"key": "5h", "pct": 12.0}]}}
        self.assertIsInstance(cache._with_previous(old, broken), dict)

    def test_bool_timestamps_are_not_treated_as_numbers(self):
        old = entry(100.0, 10.0, prev_pct=5.0, prev_at=True)
        out = cache._with_previous(old, entry(120.0, 12.0))
        self.assertIsInstance(out, dict)


if __name__ == "__main__":
    unittest.main()
