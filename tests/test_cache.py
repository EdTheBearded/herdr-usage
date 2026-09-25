"""T9 regression — the cache's previous-sample bookkeeping.

Caught in live use, not by the unit suites: re-saving the same reading (what
every cache-hit refresh does) copied pct into prev_pct, flattening the burn
slope to zero forever.
"""

import pathlib
import tempfile
import unittest

from herdr_usage import cache


def sample(pct, fetched_at):
    return {
        "codex": {
            "provider": "codex",
            "fetched_at": fetched_at,
            "gauges": [{"key": "5h", "label": "5h", "pct": pct}],
        }
    }


class TestPreviousSample(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = pathlib.Path(self.tmp.name) / "usage.json"

    def gauge(self):
        return cache.load(self.path)["codex"]["gauges"][0]

    def test_resaving_the_same_reading_does_not_flatten_the_slope(self):
        cache.save(self.path, sample(10.0, 100.0))
        cache.save(self.path, sample(30.0, 400.0))
        self.assertEqual(self.gauge()["prev_pct"], 10.0)

        # a cache-hit refresh re-saves the identical entry
        cache.save(self.path, sample(30.0, 400.0))
        g = self.gauge()
        self.assertEqual(g["pct"], 30.0)
        self.assertEqual(g["prev_pct"], 10.0,
                         "re-saving the same reading destroyed the burn slope")
        self.assertEqual(g["prev_fetched_at"], 100.0)

    def test_a_new_reading_still_shifts_prev(self):
        cache.save(self.path, sample(10.0, 100.0))
        cache.save(self.path, sample(30.0, 400.0))
        cache.save(self.path, sample(55.0, 700.0))
        g = self.gauge()
        self.assertEqual(g["prev_pct"], 30.0)
        self.assertEqual(g["prev_fetched_at"], 400.0)

    def test_burn_eta_is_computable_after_a_cache_hit(self):
        from herdr_usage.model import burn_eta

        cache.save(self.path, sample(10.0, 100.0))
        cache.save(self.path, sample(30.0, 400.0))
        cache.save(self.path, sample(30.0, 400.0))  # cache hit
        g = self.gauge()
        eta = burn_eta(g["pct"], g["prev_pct"],
                       400.0 - g["prev_fetched_at"], None, 400.0)
        self.assertIsNotNone(eta, "burn ETA died after a cache-hit re-save")


if __name__ == "__main__":
    unittest.main()
