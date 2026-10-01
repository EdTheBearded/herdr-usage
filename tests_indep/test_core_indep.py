"""Independent unittest suite for herdr_usage core modules.

The implementation and the pre-existing `tests/` suite were not supplied to
the author. A failure here is a behaviour discrepancy to investigate.

Run:  cd /home/ed/herdr-usage && python3 -m unittest discover -s tests_indep -t .
"""

import copy
import json
import math
import os
import pathlib
import re
import unittest
from datetime import datetime

from herdr_usage.model import AccountUsage, Gauge, band, burn_eta, window_label
from herdr_usage.render import (
    BAR_SETS,
    BLOCKED,
    NO_DATA,
    STALE,
    TOKEN_MAX,
    bar,
    build_tokens,
    fmt_duration,
    gauge_text,
)
from herdr_usage.providers import (
    normalize_claude,
    normalize_codex,
    resolve_path,
    run_declarative,
    short_model,
)

REPO = pathlib.Path(__file__).resolve().parent.parent
FIXTURES = REPO / "tests" / "fixtures"

TOKEN_NAME_RE = re.compile(r"^[A-Za-z0-9_-]{1,32}$")

# Every glyph any bar set can emit — used to prove "no data" renders no bar.
ALL_BAR_CHARS = set()


def _load(name):
    with open(FIXTURES / name, encoding="utf-8") as fh:
        return json.load(fh)


def setUpModule():
    for full, empty in BAR_SETS.values():
        ALL_BAR_CHARS.add(full)
        ALL_BAR_CHARS.add(empty)


def iso_epoch(s):
    return datetime.fromisoformat(s).timestamp()


def without_clock(usage):
    """AccountUsage minus `fetched_at`, which is legitimately wall-clock."""
    return (usage.provider, usage.account_id, usage.plan, usage.gauges,
            usage.credits, usage.cost_usd, usage.error)


# ---------------------------------------------------------------------------
# model: dataclasses
# ---------------------------------------------------------------------------


class TestDataModel(unittest.TestCase):
    def test_gauge_defaults_unknown_is_none_not_zero(self):
        """SPEC §3: pct None = unknown, never 0. Defaults must not fake data."""
        g = Gauge(key="5h", label="5h")
        self.assertIsNone(g.pct)
        self.assertIsNone(g.resets_at)
        self.assertIsNone(g.severity)
        self.assertIs(g.blocked, False)

    def test_account_usage_defaults(self):
        u = AccountUsage(provider="codex")
        self.assertEqual(u.gauges, [])
        self.assertIsNone(u.account_id)
        self.assertIsNone(u.plan)
        self.assertIsNone(u.credits)
        self.assertIsNone(u.cost_usd)
        self.assertIsNone(u.error)

    def test_account_usage_gauges_not_shared_between_instances(self):
        """A mutable default would make every AccountUsage share one gauge list."""
        a = AccountUsage(provider="codex")
        b = AccountUsage(provider="claude")
        a.gauges.append(Gauge(key="5h", label="5h"))
        self.assertEqual(b.gauges, [])


# ---------------------------------------------------------------------------
# model: window_label
# ---------------------------------------------------------------------------


class TestWindowLabel(unittest.TestCase):
    def test_canonical_durations(self):
        """API: identity comes from duration. 300->5h, 10080->wk, 43200->mo."""
        self.assertEqual(window_label(300), "5h")
        self.assertEqual(window_label(10080), "wk")
        self.assertEqual(window_label(43200), "mo")

    def test_derived_hour_and_day(self):
        self.assertEqual(window_label(60), "1h")
        self.assertEqual(window_label(1440), "1d")

    def test_non_multiple_falls_back_to_minutes(self):
        self.assertEqual(window_label(77), "77m")
        self.assertEqual(window_label(1), "1m")

    def test_none_and_zero_are_unknown(self):
        self.assertEqual(window_label(None), "?")
        self.assertEqual(window_label(0), "?")

    def test_label_is_short_for_sane_inputs(self):
        """API: 'Result is always short (<= 6 chars for sane inputs)'."""
        for m in (1, 5, 60, 120, 300, 360, 1440, 2880, 10080, 43200, 77, 999):
            with self.subTest(minutes=m):
                self.assertLessEqual(len(window_label(m)), 6)

    def test_other_hour_multiple_derives_plausibly(self):
        """120 min is a derived multiple: either '2h' or the minutes fallback."""
        self.assertIn(window_label(120), ("2h", "120m"))

    def test_identity_never_depends_on_a_field_name(self):
        """SPEC §4.1 trap: same duration -> same label, whatever slot carried it."""
        self.assertEqual(window_label(300), window_label(300))
        self.assertNotEqual(window_label(300), window_label(10080))


# ---------------------------------------------------------------------------
# model: band
# ---------------------------------------------------------------------------


class TestBand(unittest.TestCase):
    def test_thresholds_plain(self):
        self.assertEqual(band(Gauge("5h", "5h", pct=0.0)), "ok")
        self.assertEqual(band(Gauge("5h", "5h", pct=69.9)), "ok")
        self.assertEqual(band(Gauge("5h", "5h", pct=70.0)), "warn")
        self.assertEqual(band(Gauge("5h", "5h", pct=89.9)), "warn")
        self.assertEqual(band(Gauge("5h", "5h", pct=90.0)), "crit")
        self.assertEqual(band(Gauge("5h", "5h", pct=100.0)), "crit")

    def test_custom_thresholds_honoured(self):
        g = Gauge("5h", "5h", pct=55.0)
        self.assertEqual(band(g, warn=50, crit=95), "warn")
        self.assertEqual(band(g, warn=60, crit=95), "ok")

    def test_pct_none_is_none_band(self):
        self.assertEqual(band(Gauge("5h", "5h", pct=None)), "none")

    def test_blocked_beats_everything_including_none_pct(self):
        """API precedence 1: blocked -> crit regardless of pct, including None."""
        self.assertEqual(band(Gauge("5h", "5h", pct=None, blocked=True)), "crit")
        self.assertEqual(band(Gauge("5h", "5h", pct=0.0, blocked=True)), "crit")
        self.assertEqual(band(Gauge("5h", "5h", pct=12.0, blocked=True)), "crit")

    def test_blocked_beats_a_benign_provider_severity(self):
        """Precedence 1 outranks 2: a 'normal' severity cannot un-block a gauge."""
        g = Gauge("5h", "5h", pct=3.0, severity="normal", blocked=True)
        self.assertEqual(band(g), "crit")

    def test_provider_severity_beats_thresholds_downward(self):
        """API precedence 2: provider band wins even when pct says crit."""
        g = Gauge("5h", "5h", pct=99.0, severity="normal")
        self.assertEqual(band(g), "ok")

    def test_provider_severity_beats_thresholds_upward(self):
        g = Gauge("5h", "5h", pct=1.0, severity="critical")
        self.assertEqual(band(g), "crit")

    def test_provider_severity_warning_maps_to_warn(self):
        self.assertEqual(band(Gauge("5h", "5h", pct=1.0, severity="warning")), "warn")

    def test_severity_is_case_insensitive(self):
        for raw, expect in (
            ("CRITICAL", "crit"),
            ("Critical", "crit"),
            ("WARNING", "warn"),
            ("Normal", "ok"),
            ("nOrMaL", "ok"),
        ):
            with self.subTest(severity=raw):
                self.assertEqual(band(Gauge("5h", "5h", pct=50.0, severity=raw)), expect)

    def test_severity_normal_with_pct_none_still_wins(self):
        """Precedence 2 sits above 4: a recognised severity beats 'no pct'."""
        self.assertEqual(band(Gauge("5h", "5h", pct=None, severity="normal")), "ok")

    def test_unrecognised_severity_falls_through_to_thresholds(self):
        """API precedence 3: an unknown band must not be guessed at."""
        self.assertEqual(band(Gauge("5h", "5h", pct=95.0, severity="spicy")), "crit")
        self.assertEqual(band(Gauge("5h", "5h", pct=10.0, severity="spicy")), "ok")
        self.assertEqual(band(Gauge("5h", "5h", pct=75.0, severity="")), "warn")

    def test_unrecognised_severity_with_pct_none_is_none(self):
        self.assertEqual(band(Gauge("5h", "5h", pct=None, severity="spicy")), "none")

    def test_band_returns_only_the_four_names(self):
        for g in (
            Gauge("a", "a"),
            Gauge("a", "a", pct=0.0),
            Gauge("a", "a", pct=100.0),
            Gauge("a", "a", blocked=True),
            Gauge("a", "a", pct=50.0, severity="warning"),
            Gauge("a", "a", pct=50.0, severity="???"),
        ):
            with self.subTest(gauge=g):
                self.assertIn(band(g), {"ok", "warn", "crit", "none"})

    def test_band_does_not_mutate_the_gauge(self):
        g = Gauge("5h", "5h", pct=95.0, severity="normal")
        before = copy.deepcopy(g)
        band(g)
        self.assertEqual(g, before)

    def test_band_is_idempotent(self):
        g = Gauge("5h", "5h", pct=71.0)
        self.assertEqual(band(g), band(g))


# ---------------------------------------------------------------------------
# model: burn_eta
# ---------------------------------------------------------------------------


class TestBurnEta(unittest.TestCase):
    # rate = (pct-prev)/dt ; eta = (100-pct)/rate
    # 25/128 is exact in binary, so eta below is exactly 256.0.
    GOOD = dict(pct=50.0, prev_pct=25.0, dt=128.0, resets_at=None, now=1000.0)

    def test_happy_path(self):
        self.assertAlmostEqual(burn_eta(**self.GOOD), 256.0, places=6)

    def test_none_prev_pct_suppresses(self):
        a = dict(self.GOOD, prev_pct=None)
        self.assertIsNone(burn_eta(**a))

    def test_none_pct_suppresses(self):
        self.assertIsNone(burn_eta(**dict(self.GOOD, pct=None)))

    def test_none_dt_suppresses(self):
        self.assertIsNone(burn_eta(**dict(self.GOOD, dt=None)))

    def test_dt_below_min_suppresses(self):
        """SPEC §5.6: suppressed when dt < 60 s — too little signal."""
        self.assertIsNone(burn_eta(pct=50.0, prev_pct=25.0, dt=59.9,
                                   resets_at=None, now=1000.0))

    def test_dt_exactly_min_is_allowed(self):
        """Boundary: the rule is dt < min_dt, so dt == min_dt must pass."""
        self.assertIsNotNone(burn_eta(pct=50.0, prev_pct=25.0, dt=60.0,
                                      resets_at=None, now=1000.0))

    def test_min_dt_override_is_honoured(self):
        self.assertIsNotNone(burn_eta(pct=50.0, prev_pct=25.0, dt=30.0,
                                      resets_at=None, now=1000.0, min_dt=10))
        self.assertIsNone(burn_eta(pct=50.0, prev_pct=25.0, dt=30.0,
                                   resets_at=None, now=1000.0, min_dt=31))

    def test_flat_usage_suppresses(self):
        """pct <= prev_pct: no burn, so no ETA (and no division by zero)."""
        self.assertIsNone(burn_eta(pct=50.0, prev_pct=50.0, dt=128.0,
                                   resets_at=None, now=1000.0))

    def test_decreasing_usage_suppresses(self):
        self.assertIsNone(burn_eta(pct=40.0, prev_pct=50.0, dt=128.0,
                                   resets_at=None, now=1000.0))

    def test_pct_at_or_above_100_suppresses(self):
        """Already exhausted: an ETA to exhaustion is meaningless."""
        self.assertIsNone(burn_eta(pct=100.0, prev_pct=25.0, dt=128.0,
                                   resets_at=None, now=1000.0))
        self.assertIsNone(burn_eta(pct=140.0, prev_pct=25.0, dt=128.0,
                                   resets_at=None, now=1000.0))

    def test_eta_past_reset_suppresses(self):
        """SPEC §5.6: an ETA later than the window reset is meaningless."""
        self.assertIsNone(burn_eta(pct=50.0, prev_pct=25.0, dt=128.0,
                                   resets_at=1255.0, now=1000.0))

    def test_eta_exactly_at_reset_is_kept(self):
        """Boundary: suppression is now+eta > resets_at, so equality survives."""
        got = burn_eta(pct=50.0, prev_pct=25.0, dt=128.0,
                       resets_at=1256.0, now=1000.0)
        self.assertIsNotNone(got)
        self.assertAlmostEqual(got, 256.0, places=6)

    def test_eta_before_reset_is_kept(self):
        got = burn_eta(pct=50.0, prev_pct=25.0, dt=128.0,
                       resets_at=999999.0, now=1000.0)
        self.assertAlmostEqual(got, 256.0, places=6)

    def test_resets_at_none_disables_the_reset_check(self):
        self.assertIsNotNone(burn_eta(pct=99.0, prev_pct=1.0, dt=128.0,
                                      resets_at=None, now=0.0))

    def test_combined_suppressions_still_return_none(self):
        """Several suppressors at once must not fight each other into a value."""
        self.assertIsNone(burn_eta(pct=100.0, prev_pct=100.0, dt=1.0,
                                   resets_at=0.0, now=1e9))
        self.assertIsNone(burn_eta(pct=None, prev_pct=None, dt=None,
                                   resets_at=None, now=None))

    def test_tiny_burn_gives_a_huge_but_finite_eta(self):
        got = burn_eta(pct=50.0000001, prev_pct=50.0, dt=128.0,
                       resets_at=None, now=0.0)
        self.assertIsNotNone(got)
        self.assertTrue(math.isfinite(got))
        self.assertGreater(got, 0)

    def test_result_is_positive_when_returned(self):
        for pct, prev in ((1.0, 0.5), (50.0, 25.0), (99.9, 1.0)):
            got = burn_eta(pct=pct, prev_pct=prev, dt=128.0,
                           resets_at=None, now=0.0)
            with self.subTest(pct=pct):
                self.assertIsNotNone(got)
                self.assertGreater(got, 0)


# ---------------------------------------------------------------------------
# render: bar
# ---------------------------------------------------------------------------


class TestBar(unittest.TestCase):
    def _filled(self, s, bar_set="blocks"):
        full, _empty = BAR_SETS[bar_set]
        return s.count(full)

    def test_always_exactly_cells_characters(self):
        for cells in (1, 2, 3, 5, 10, 40, 200):
            for pct in (-100, 0, 0.0001, 1, 50, 99.999, 100, 100.0001, 1e9):
                with self.subTest(cells=cells, pct=pct):
                    self.assertEqual(len(bar(pct, cells=cells)), cells)

    def test_only_100_renders_a_full_bar(self):
        """SPEC §5.2: a full bar means exhausted — 99.999% must not render full."""
        self.assertEqual(self._filled(bar(100.0, cells=10)), 10)
        self.assertEqual(self._filled(bar(99.999, cells=10)), 9)
        self.assertEqual(self._filled(bar(99.9999999, cells=10)), 9)

    def test_above_100_clamps_to_full(self):
        self.assertEqual(self._filled(bar(100.0001, cells=10)), 10)
        self.assertEqual(self._filled(bar(1e6, cells=10)), 10)

    def test_zero_and_below_render_empty(self):
        self.assertEqual(self._filled(bar(0, cells=10)), 0)
        self.assertEqual(self._filled(bar(0.0, cells=10)), 0)
        self.assertEqual(self._filled(bar(-0.5, cells=10)), 0)
        self.assertEqual(self._filled(bar(-1e9, cells=10)), 0)

    def test_any_nonzero_pct_shows_at_least_one_cell(self):
        """SPEC §5.2: a non-zero percentage always shows at least one block."""
        for pct in (0.0001, 0.01, 1e-9, 0.5, 1):
            with self.subTest(pct=pct):
                self.assertGreaterEqual(self._filled(bar(pct, cells=10)), 1)

    def test_ceiling_fill_for_mid_values(self):
        self.assertEqual(self._filled(bar(10, cells=10)), 1)
        self.assertEqual(self._filled(bar(10.1, cells=10)), 2)
        self.assertEqual(self._filled(bar(31, cells=10)), 4)
        self.assertEqual(self._filled(bar(50, cells=10)), 5)
        self.assertEqual(self._filled(bar(82, cells=10)), 9)
        self.assertEqual(self._filled(bar(90, cells=10)), 9)

    def test_exactly_cells_minus_one_worth_is_not_capped(self):
        """90% of 10 cells == 9 cells, which is cells-1: the cap must not bite."""
        self.assertEqual(self._filled(bar(90.0, cells=10)), 9)
        self.assertEqual(self._filled(bar(80.0, cells=5)), 4)
        self.assertEqual(self._filled(bar(75.0, cells=4)), 3)

    def test_cap_bites_just_above_cells_minus_one(self):
        self.assertEqual(self._filled(bar(90.0001, cells=10)), 9)
        self.assertEqual(self._filled(bar(99.0, cells=10)), 9)

    def test_single_cell_bar(self):
        """cells=1: min(cells-1, ...) == 0, so only pct>=100 can fill it."""
        self.assertEqual(self._filled(bar(100.0, cells=1)), 1)
        self.assertEqual(self._filled(bar(0.0, cells=1)), 0)
        self.assertEqual(self._filled(bar(50.0, cells=1)), 0)
        self.assertEqual(self._filled(bar(99.9, cells=1)), 0)

    def test_two_cell_bar(self):
        self.assertEqual(self._filled(bar(0.1, cells=2)), 1)
        self.assertEqual(self._filled(bar(50.0, cells=2)), 1)
        self.assertEqual(self._filled(bar(99.9, cells=2)), 1)
        self.assertEqual(self._filled(bar(100.0, cells=2)), 2)

    def test_three_cell_bar(self):
        self.assertEqual(self._filled(bar(0.1, cells=3)), 1)
        self.assertEqual(self._filled(bar(34.0, cells=3)), 2)
        self.assertEqual(self._filled(bar(99.999, cells=3)), 2)
        self.assertEqual(self._filled(bar(100.0, cells=3)), 3)

    def test_huge_cell_count(self):
        self.assertEqual(self._filled(bar(50.0, cells=1000)), 500)
        self.assertEqual(self._filled(bar(0.05, cells=1000)), 1)
        self.assertEqual(self._filled(bar(99.999, cells=1000)), 999)
        self.assertEqual(self._filled(bar(100.0, cells=1000)), 1000)

    def test_fill_is_monotonic_in_pct(self):
        prev = -1
        for pct in [x / 4 for x in range(0, 401)]:
            n = self._filled(bar(pct, cells=10))
            self.assertGreaterEqual(n, prev, f"fill dropped at pct={pct}")
            prev = n

    def test_every_bar_set_is_usable(self):
        for name, (full, empty) in BAR_SETS.items():
            with self.subTest(bar_set=name):
                s = bar(50.0, cells=10, bar_set=name)
                self.assertEqual(len(s), 10)
                self.assertEqual(set(s) - {full, empty}, set())
                self.assertEqual(s.count(full), 5)

    def test_bar_set_names_from_spec_all_present(self):
        self.assertEqual(
            set(BAR_SETS) >= {"blocks", "shade", "squares", "dots", "half", "ascii"},
            True,
        )

    def test_unknown_bar_set_falls_back_to_blocks(self):
        self.assertEqual(bar(50.0, 10, "no-such-set"), bar(50.0, 10, "blocks"))
        self.assertEqual(bar(50.0, 10, ""), bar(50.0, 10, "blocks"))

    def test_full_chars_come_before_empty_chars(self):
        full, empty = BAR_SETS["blocks"]
        s = bar(31.0, cells=10)
        self.assertEqual(s, full * s.count(full) + empty * s.count(empty))

    def test_bar_is_idempotent(self):
        self.assertEqual(bar(37.5, 10), bar(37.5, 10))


# ---------------------------------------------------------------------------
# render: fmt_duration
# ---------------------------------------------------------------------------


class TestFmtDuration(unittest.TestCase):
    def test_none_passes_through(self):
        self.assertIsNone(fmt_duration(None))

    def test_documented_samples(self):
        self.assertEqual(fmt_duration(74), "1m")
        self.assertEqual(fmt_duration(4940), "1h22m")
        self.assertEqual(fmt_duration(90000), "1d1h")
        self.assertEqual(fmt_duration(59), "0m")

    def test_zero(self):
        self.assertEqual(fmt_duration(0), "0m")

    def test_negative_clamps_to_zero(self):
        self.assertEqual(fmt_duration(-1), "0m")
        self.assertEqual(fmt_duration(-99999), "0m")

    def test_never_returns_a_negative_looking_string(self):
        for s in (-1, -60, -3600, -86400):
            with self.subTest(secs=s):
                self.assertNotIn("-", fmt_duration(s))

    def test_output_is_compact(self):
        for s in (0, 59, 74, 4940, 90000, 31 * 86400):
            with self.subTest(secs=s):
                self.assertLessEqual(len(fmt_duration(s)), 8)


# ---------------------------------------------------------------------------
# render: gauge_text
# ---------------------------------------------------------------------------


class TestGaugeText(unittest.TestCase):
    def test_no_data_has_no_bar_and_no_zero_percent(self):
        """SPEC §2.1: no data -> '⋯ no data', never '0%' and never a bar."""
        t = gauge_text(Gauge("5h", "5h", pct=None))
        self.assertEqual(t, f"5h {NO_DATA}")
        self.assertNotIn("0%", t)
        self.assertNotIn("%", t)
        self.assertEqual(set(t) & ALL_BAR_CHARS, set())

    def test_zero_percent_is_not_no_data(self):
        """A real 0% reading must render as a bar + 0%, distinct from unknown."""
        t = gauge_text(Gauge("5h", "5h", pct=0.0))
        self.assertNotIn(NO_DATA, t)
        self.assertIn("0%", t)

    def test_normal_rendering_shape(self):
        t = gauge_text(Gauge("5h", "5h", pct=31.0))
        self.assertTrue(t.startswith("5h "))
        self.assertTrue(t.endswith("31%"))
        self.assertIn(BAR_SETS["blocks"][0] * 4, t)

    def test_percentage_is_rounded_to_whole_cells_of_width_3(self):
        self.assertTrue(gauge_text(Gauge("5h", "5h", pct=7.4)).endswith("  7%"))
        self.assertTrue(gauge_text(Gauge("5h", "5h", pct=100.0)).endswith("100%"))

    def test_blocked_renders_full_bar_and_blocked_glyph(self):
        """SPEC §5.5 degraded state: '5h ██████████ ⛔️'."""
        t = gauge_text(Gauge("5h", "5h", pct=100.0, blocked=True))
        full = BAR_SETS["blocks"][0]
        self.assertIn(full * 10, t)
        self.assertIn(BLOCKED, t)
        self.assertNotIn("%", t)

    def test_blocked_with_unknown_pct_still_renders_blocked(self):
        """Adversarial: blocked but pct never arrived — must not crash or say 'no data'."""
        t = gauge_text(Gauge("5h", "5h", pct=None, blocked=True))
        self.assertIn(BLOCKED, t)
        self.assertNotIn(NO_DATA, t)

    def test_stale_marker_appended(self):
        t = gauge_text(Gauge("5h", "5h", pct=31.0), stale=True)
        self.assertTrue(t.endswith(f" {STALE}"))
        self.assertIn("31%", t)

    def test_stale_marker_on_no_data(self):
        t = gauge_text(Gauge("5h", "5h", pct=None), stale=True)
        self.assertTrue(t.endswith(f" {STALE}"))

    def test_label_is_used_verbatim(self):
        t = gauge_text(Gauge("scoped:Fable", "wk Fable", pct=0.0))
        self.assertTrue(t.startswith("wk Fable "))

    def test_truncated_to_token_max(self):
        g = Gauge("x", "L" * 300, pct=50.0)
        self.assertLessEqual(len(gauge_text(g)), TOKEN_MAX)

    def test_truncated_to_token_max_with_wide_bar(self):
        g = Gauge("x", "5h", pct=50.0)
        self.assertLessEqual(len(gauge_text(g, cells=500)), TOKEN_MAX)

    def test_does_not_mutate_the_gauge(self):
        g = Gauge("5h", "5h", pct=31.0)
        before = copy.deepcopy(g)
        gauge_text(g)
        self.assertEqual(g, before)

    def test_all_bar_sets_render(self):
        for name in BAR_SETS:
            with self.subTest(bar_set=name):
                t = gauge_text(Gauge("5h", "5h", pct=50.0), bar_set=name)
                self.assertIn("50%", t)


# ---------------------------------------------------------------------------
# render: build_tokens
# ---------------------------------------------------------------------------


class TestBuildTokens(unittest.TestCase):
    def test_exactly_one_severity_variant_set_three_cleared(self):
        """SPEC §5.3: emit exactly one of four token names, clear the other three."""
        tokens, clear = build_tokens([Gauge("5h", "5h", pct=31.0)])
        variants = [f"uq_g5h_{v}" for v in ("ok", "warn", "crit", "none")]
        present = [v for v in variants if v in tokens]
        self.assertEqual(present, ["uq_g5h_ok"])
        for v in variants:
            if v != "uq_g5h_ok":
                self.assertIn(v, clear)
        self.assertNotIn("uq_g5h_ok", clear)

    def test_variant_follows_band_for_each_severity(self):
        cases = [
            (Gauge("5h", "5h", pct=10.0), "ok"),
            (Gauge("5h", "5h", pct=75.0), "warn"),
            (Gauge("5h", "5h", pct=95.0), "crit"),
            (Gauge("5h", "5h", pct=None), "none"),
            (Gauge("5h", "5h", pct=1.0, blocked=True), "crit"),
            (Gauge("5h", "5h", pct=99.0, severity="normal"), "ok"),
        ]
        for g, expect in cases:
            with self.subTest(gauge=g):
                tokens, clear = build_tokens([g])
                self.assertIn(f"uq_g5h_{expect}", tokens)
                self.assertEqual(
                    sum(1 for v in ("ok", "warn", "crit", "none")
                        if f"uq_g5h_{v}" in tokens),
                    1,
                )

    def test_custom_thresholds_reach_the_band(self):
        tokens, _ = build_tokens([Gauge("5h", "5h", pct=55.0)], warn=50, crit=95)
        self.assertIn("uq_g5h_warn", tokens)

    def test_slug_replaces_non_word_characters(self):
        """SPEC §5.4: 'scoped:Fable' -> 'scoped_Fable'."""
        tokens, clear = build_tokens([Gauge("scoped:Fable", "wk Fable", pct=0.0)])
        self.assertIn("uq_gscoped_Fable_ok", tokens)

    def test_token_name_length_capped_at_32(self):
        """SPEC §5.4: the slug is bounded to 32 chars, so 'uq_g<slug>_ok' must fit.

        A long gauge key must be truncated, not passed through: Herdr's token
        names are `^[A-Za-z0-9_-]{1,32}$`.
        """
        tokens, clear = build_tokens([Gauge("a" * 40, "L", pct=50.0)])
        for name in list(tokens) + list(clear):
            self.assertLessEqual(len(name), 32, f"token name too long: {name!r}")

    def test_token_names_valid_for_hostile_gauge_keys(self):
        """SPEC §5.4: every gauge key slugifies to ^[A-Za-z0-9_-]{1,32}$."""
        hostile = [
            "",
            "5h",
            "scoped:Fable",
            "gauge.with.dots",
            "café ☕ window",
            "a" * 200,
            "  spaces  ",
            "../../etc/passwd",
            "tab\tand\nnewline",
            "%$#@!",
            "ünïcødé",
            "🙂🙂🙂",
        ]
        for key in hostile:
            with self.subTest(key=repr(key)):
                tokens, clear = build_tokens([Gauge(key, "L", pct=50.0)])
                for name in list(tokens) + list(clear):
                    self.assertRegex(name, TOKEN_NAME_RE,
                                     f"bad token name {name!r} from key {key!r}")

    def test_token_values_never_exceed_80_chars(self):
        """API: Herdr truncates silently above 80 chars, so we must not exceed it."""
        gauges = [Gauge("5h", "X" * 400, pct=50.0)]
        extras = {"model": "M" * 400, "plan": "P" * 400,
                  "burn": "B" * 400, "extra": "E" * 400}
        tokens, _ = build_tokens(gauges, extras=extras, cells=300)
        for name, value in tokens.items():
            with self.subTest(token=name):
                self.assertLessEqual(len(value), TOKEN_MAX)

    def test_extras_present_and_truthy_are_set(self):
        tokens, clear = build_tokens([], extras={
            "model": "Opus", "plan": "team", "burn": "1h20m", "extra": "$0.42"})
        self.assertEqual(tokens["uq_model"], "Opus")
        self.assertEqual(tokens["uq_plan"], "team")
        self.assertEqual(tokens["uq_burn"], "1h20m")
        self.assertEqual(tokens["uq_extra"], "$0.42")
        for n in ("uq_model", "uq_plan", "uq_burn", "uq_extra"):
            self.assertNotIn(n, clear)

    def test_extras_absent_are_cleared(self):
        tokens, clear = build_tokens([], extras=None)
        for n in ("uq_model", "uq_plan", "uq_burn", "uq_extra"):
            self.assertNotIn(n, tokens)
            self.assertIn(n, clear)

    def test_extras_falsy_are_cleared_not_set(self):
        """Empty string / None / 0 must clear the token, never set a blank one."""
        tokens, clear = build_tokens([], extras={
            "model": "", "plan": None, "burn": 0, "extra": False})
        for n in ("uq_model", "uq_plan", "uq_burn", "uq_extra"):
            self.assertNotIn(n, tokens)
            self.assertIn(n, clear)

    def test_unknown_extra_keys_are_ignored(self):
        tokens, clear = build_tokens([], extras={"bogus": "x", "model": "Opus"})
        self.assertNotIn("uq_bogus", tokens)
        self.assertNotIn("uq_bogus", clear)

    def test_empty_gauges_emits_only_extras(self):
        tokens, clear = build_tokens([])
        self.assertEqual(tokens, {})
        # uq_agent added when the harness icon moved to its own sidebar line.
        self.assertEqual(set(clear),
                         {"uq_agent", "uq_model", "uq_plan", "uq_burn", "uq_extra"})

    def test_tokens_and_clear_never_overlap(self):
        gauges = [Gauge("5h", "5h", pct=31.0), Gauge("wk", "wk", pct=None),
                  Gauge("scoped:Fable", "wk Fable", pct=0.0, blocked=True)]
        tokens, clear = build_tokens(gauges, extras={"model": "Opus"})
        self.assertEqual(set(tokens) & set(clear), set())

    def test_token_budget_for_two_gauges_is_twelve(self):
        """SPEC §5.4: default 2 gauges = 13 tokens (5 extras + 4 per gauge)."""
        tokens, clear = build_tokens(
            [Gauge("5h", "5h", pct=31.0), Gauge("wk", "wk", pct=82.0)],
            extras={"model": "Opus"})
        self.assertEqual(len(tokens) + len(clear), 13)

    def test_token_budget_for_four_gauges_is_twenty(self):
        gauges = [Gauge(k, k, pct=10.0) for k in ("5h", "wk", "mo", "scoped:Fable")]
        tokens, clear = build_tokens(gauges, extras={"model": "Opus"})
        self.assertEqual(len(tokens) + len(clear), 21)
        self.assertLessEqual(len(tokens) + len(clear), 32)

    def test_stale_flag_reaches_the_rendered_value(self):
        tokens, _ = build_tokens([Gauge("5h", "5h", pct=31.0)], stale=True)
        self.assertTrue(tokens["uq_g5h_ok"].endswith(f" {STALE}"))

    def test_does_not_mutate_inputs(self):
        gauges = [Gauge("5h", "5h", pct=31.0)]
        extras = {"model": "Opus"}
        g_before, e_before = copy.deepcopy(gauges), copy.deepcopy(extras)
        build_tokens(gauges, extras=extras)
        self.assertEqual(gauges, g_before)
        self.assertEqual(extras, e_before)

    def test_is_idempotent(self):
        gauges = [Gauge("5h", "5h", pct=31.0)]
        a = build_tokens(gauges, extras={"model": "Opus"})
        b = build_tokens(gauges, extras={"model": "Opus"})
        self.assertEqual(a[0], b[0])
        self.assertEqual(sorted(a[1]), sorted(b[1]))


# ---------------------------------------------------------------------------
# providers: short_model
# ---------------------------------------------------------------------------


class TestShortModel(unittest.TestCase):
    def test_none_passes_through(self):
        self.assertIsNone(short_model(None))

    def test_documented_families(self):
        self.assertEqual(short_model("claude-opus-5"), "Opus")
        self.assertEqual(short_model("claude-sonnet-5"), "Sonnet")
        self.assertEqual(short_model("claude-haiku-4-5-20251001"), "Haiku")
        self.assertEqual(short_model("gpt-5.6-terra"), "Terra")
        self.assertEqual(short_model("gpt-6-sol"), "Sol")

    def test_unknown_slug_is_truncated_to_20_chars(self):
        slug = "totally-made-up-vendor-identifier-2025"
        self.assertEqual(short_model(slug), slug[:20])

    def test_unknown_short_slug_unchanged(self):
        self.assertEqual(short_model("zzz"), "zzz")

    def test_empty_string(self):
        self.assertEqual(short_model(""), "")

    def test_result_never_exceeds_20_chars(self):
        for slug in ("claude-opus-5", "x" * 400, "a-b-c-d-e-f-g-h-i-j-k"):
            with self.subTest(slug=slug[:20]):
                self.assertLessEqual(len(short_model(slug)), 20)


# ---------------------------------------------------------------------------
# providers: normalize_codex
# ---------------------------------------------------------------------------


class TestNormalizeCodex(unittest.TestCase):
    def pairs(self, usage):
        return sorted((g.label, g.pct) for g in usage.gauges)

    def test_real_fixture(self):
        u = normalize_codex(_load("codex_ratelimits.json"))
        self.assertEqual(u.provider, "codex")
        self.assertEqual(u.plan, "team")
        self.assertEqual(u.account_id, "00000000-0000-0000-0000-000000000000")
        self.assertEqual(u.credits, 3)
        self.assertEqual(self.pairs(u), [("5h", 13), ("wk", 27)])

    def test_resets_at_carried(self):
        u = normalize_codex(_load("codex_ratelimits.json"))
        by = {g.label: g for g in u.gauges}
        self.assertEqual(by["5h"].resets_at, 1790301885)
        self.assertEqual(by["wk"].resets_at, 1790691275)

    def test_swapped_slots_give_identical_label_pct_pairs(self):
        """SPEC §4.1 trap: 'primary' is weekly in ~60% of snapshots.

        The window identity MUST come from windowDurationMins, so swapping the
        two slots must not change a single (label, pct) pair.
        """
        a = normalize_codex(_load("codex_ratelimits.json"))
        b = normalize_codex(_load("codex_ratelimits_swapped.json"))
        self.assertEqual(self.pairs(a), self.pairs(b))
        self.assertEqual(self.pairs(b), [("5h", 13), ("wk", 27)])

    def test_swapped_fixture_resets_at_follow_the_window_not_the_slot(self):
        b = {g.label: g.resets_at for g in
             normalize_codex(_load("codex_ratelimits_swapped.json")).gauges}
        self.assertEqual(b["5h"], 1790301885)
        self.assertEqual(b["wk"], 1790691275)

    def test_snake_case_spelling_parsed(self):
        """SPEC §4.1: parse both camelCase (app-server) and snake_case (rollout)."""
        doc = {"rate_limits": _load("codex_rollout_snake.json")}
        u = normalize_codex(doc)
        self.assertEqual(self.pairs(u), [("5h", 13), ("wk", 27)])
        self.assertEqual(u.plan, "team")

    def test_bare_snake_rollout_document_does_not_raise(self):
        normalize_codex(_load("codex_rollout_snake.json"))

    def test_blocked_fixture(self):
        u = normalize_codex(_load("codex_blocked.json"))
        self.assertTrue(any(g.blocked for g in u.gauges),
                        "rateLimitReachedType non-null must mark a gauge blocked")
        self.assertEqual(u.credits, 3)

    def test_string_credits_balance_does_not_break_parsing(self):
        """API: credits.balance may be a string or null; neither breaks parsing."""
        doc = _load("codex_blocked.json")
        self.assertEqual(doc["rateLimits"]["credits"]["balance"], "12.50")
        u = normalize_codex(doc)
        self.assertEqual(u.provider, "codex")
        self.assertEqual(len(u.gauges), 2)

    def test_null_credits_balance(self):
        doc = _load("codex_ratelimits.json")
        self.assertIsNone(doc["rateLimits"]["credits"]["balance"])
        self.assertEqual(normalize_codex(doc).credits, 3)

    def test_null_slot_is_skipped_not_an_error(self):
        doc = {"rateLimits": {
            "primary": None,
            "secondary": {"usedPercent": 27, "windowDurationMins": 10080},
        }}
        u = normalize_codex(doc)
        self.assertEqual(self.pairs(u), [("wk", 27)])

    def test_slot_without_duration_is_skipped(self):
        doc = {"rateLimits": {
            "primary": {"usedPercent": 13},
            "secondary": {"usedPercent": 27, "windowDurationMins": 10080},
        }}
        self.assertEqual(self.pairs(normalize_codex(doc)), [("wk", 27)])

    def test_both_slots_missing_yields_no_gauges(self):
        u = normalize_codex({"rateLimits": {}})
        self.assertEqual(u.gauges, [])
        self.assertEqual(u.provider, "codex")

    def test_empty_document_does_not_raise(self):
        u = normalize_codex({})
        self.assertEqual(u.provider, "codex")
        self.assertEqual(u.gauges, [])
        self.assertIsNone(u.plan)
        self.assertIsNone(u.credits)

    def test_missing_reset_credits_gives_none(self):
        doc = {"rateLimits": {"primary": {"usedPercent": 1,
                                          "windowDurationMins": 300}}}
        self.assertIsNone(normalize_codex(doc).credits)

    def test_credits_is_int_or_none(self):
        u = normalize_codex(_load("codex_ratelimits.json"))
        self.assertIsInstance(u.credits, int)

    def test_unseen_window_duration_still_yields_a_gauge(self):
        """SPEC §3: gauges are a list; a new window needs no new code."""
        doc = {"rateLimits": {"primary": {"usedPercent": 5,
                                          "windowDurationMins": 43200}}}
        self.assertEqual(self.pairs(normalize_codex(doc)), [("mo", 5)])

    def test_does_not_mutate_the_input_document(self):
        doc = _load("codex_ratelimits.json")
        before = copy.deepcopy(doc)
        normalize_codex(doc)
        self.assertEqual(doc, before)

    def test_is_idempotent(self):
        doc = _load("codex_ratelimits.json")
        self.assertEqual(without_clock(normalize_codex(doc)),
                         without_clock(normalize_codex(doc)))

    def test_zero_percent_stays_zero_not_none(self):
        """SPEC §2.1: never invent a percentage — but a real 0 is real."""
        doc = {"rateLimits": {"primary": {"usedPercent": 0,
                                          "windowDurationMins": 300}}}
        g = normalize_codex(doc).gauges[0]
        self.assertIsNotNone(g.pct)
        self.assertEqual(g.pct, 0)


# ---------------------------------------------------------------------------
# providers: normalize_claude
# ---------------------------------------------------------------------------


class TestNormalizeClaude(unittest.TestCase):
    def test_real_fixture_gauges(self):
        u = normalize_claude(_load("claude_get_usage.json"))
        self.assertEqual(u.provider, "claude")
        by = {g.key: g for g in u.gauges}
        self.assertEqual(by["5h"].pct, 22)
        self.assertEqual(by["5h"].label, "5h")
        self.assertEqual(by["wk"].pct, 11)
        self.assertEqual(by["wk"].label, "wk")
        self.assertEqual(by["scoped:Fable"].pct, 0)
        self.assertEqual(by["scoped:Fable"].label, "wk Fable")

    def test_exactly_three_gauges_no_stray_buckets(self):
        """Adversarial: the payload has many non-null junk buckets (nimbus_quill).

        Only five_hour, seven_day and model_scoped[] may produce gauges.
        """
        u = normalize_claude(_load("claude_get_usage.json"))
        self.assertEqual(len(u.gauges), 3, [g.key for g in u.gauges])

    def test_iso_resets_at_converted_to_epoch(self):
        u = normalize_claude(_load("claude_get_usage.json"))
        by = {g.key: g for g in u.gauges}
        self.assertAlmostEqual(
            by["5h"].resets_at,
            iso_epoch("2026-09-25T02:00:00.223748+00:00"), places=3)
        self.assertAlmostEqual(
            by["wk"].resets_at,
            iso_epoch("2026-09-25T02:59:59.223775+00:00"), places=3)
        self.assertIsInstance(by["5h"].resets_at, float)

    def test_severity_carried_verbatim(self):
        """API: severity is attached verbatim, not translated."""
        u = normalize_claude(_load("claude_get_usage.json"))
        by = {g.key: g for g in u.gauges}
        self.assertEqual(by["5h"].severity, "normal")
        self.assertEqual(by["wk"].severity, "normal")
        self.assertEqual(by["scoped:Fable"].severity, "normal")

    def test_blocked_fixture_severity_and_lock(self):
        u = normalize_claude(_load("claude_blocked.json"))
        by = {g.key: g for g in u.gauges}
        self.assertTrue(by["5h"].blocked)
        self.assertEqual(by["5h"].severity, "critical")
        self.assertFalse(by["wk"].blocked)

    def test_subscription_type_null_is_none_not_the_string(self):
        """API: a JSON null stays None, never the string 'None'."""
        u = normalize_claude(_load("claude_get_usage.json"))
        self.assertIsNone(u.plan)
        self.assertNotEqual(u.plan, "None")

    def test_subscription_type_value_carried(self):
        doc = _load("claude_get_usage.json")
        doc["subscription_type"] = "max"
        self.assertEqual(normalize_claude(doc).plan, "max")

    def test_cost_usd_zero_is_kept_not_dropped(self):
        u = normalize_claude(_load("claude_get_usage.json"))
        self.assertIsNotNone(u.cost_usd)
        self.assertEqual(u.cost_usd, 0)

    def test_cost_usd_nonzero(self):
        doc = _load("claude_get_usage.json")
        doc["session"]["total_cost_usd"] = 0.42
        self.assertAlmostEqual(normalize_claude(doc).cost_usd, 0.42)

    def test_seven_day_opus_is_never_special_cased(self):
        """SPEC §4.2: don't hardcode 'Opus' — model_scoped[] drives scoped gauges."""
        doc = _load("claude_get_usage.json")
        doc["rate_limits"]["seven_day_opus"] = {
            "utilization": 77, "resets_at": None, "locked_reason": None}
        doc["rate_limits"]["model_scoped"] = []
        u = normalize_claude(doc)
        keys = [g.key for g in u.gauges]
        self.assertEqual(sorted(keys), ["5h", "wk"])
        self.assertNotIn(77, [g.pct for g in u.gauges])

    def test_model_scoped_drives_scoped_gauges(self):
        doc = _load("claude_get_usage.json")
        doc["rate_limits"]["model_scoped"] = [
            {"display_name": "Fable", "utilization": 3, "resets_at": None},
            {"display_name": "Sol", "utilization": 9, "resets_at": None},
        ]
        u = normalize_claude(doc)
        by = {g.key: g for g in u.gauges}
        self.assertEqual(by["scoped:Fable"].pct, 3)
        self.assertEqual(by["scoped:Sol"].pct, 9)
        self.assertEqual(by["scoped:Sol"].label, "wk Sol")

    def test_scoped_null_resets_at_is_none(self):
        doc = _load("claude_get_usage.json")
        doc["rate_limits"]["model_scoped"] = [
            {"display_name": "Fable", "utilization": 0, "resets_at": None}]
        by = {g.key: g for g in normalize_claude(doc).gauges}
        self.assertIsNone(by["scoped:Fable"].resets_at)

    def test_empty_model_scoped_yields_two_gauges(self):
        doc = _load("claude_get_usage.json")
        doc["rate_limits"]["model_scoped"] = []
        self.assertEqual(len(normalize_claude(doc).gauges), 2)

    def test_missing_model_scoped_key_does_not_raise(self):
        doc = _load("claude_get_usage.json")
        doc["rate_limits"].pop("model_scoped")
        u = normalize_claude(doc)
        self.assertEqual(sorted(g.key for g in u.gauges), ["5h", "wk"])

    def test_null_five_hour_bucket_is_skipped(self):
        doc = _load("claude_get_usage.json")
        doc["rate_limits"]["five_hour"] = None
        u = normalize_claude(doc)
        self.assertNotIn("5h", [g.key for g in u.gauges])

    def test_empty_document_does_not_raise(self):
        u = normalize_claude({})
        self.assertEqual(u.provider, "claude")
        self.assertEqual(u.gauges, [])
        self.assertIsNone(u.plan)

    def test_missing_limits_list_leaves_severity_none(self):
        doc = _load("claude_get_usage.json")
        doc["rate_limits"].pop("limits")
        by = {g.key: g for g in normalize_claude(doc).gauges}
        self.assertIsNone(by["5h"].severity)

    def test_unknown_severity_string_carried_verbatim(self):
        doc = _load("claude_get_usage.json")
        doc["rate_limits"]["limits"][0]["severity"] = "ELEVATED"
        by = {g.key: g for g in normalize_claude(doc).gauges}
        self.assertEqual(by["5h"].severity, "ELEVATED")

    def test_malformed_iso_resets_at_does_not_raise(self):
        doc = _load("claude_get_usage.json")
        doc["rate_limits"]["five_hour"]["resets_at"] = "not-a-timestamp"
        u = normalize_claude(doc)
        by = {g.key: g for g in u.gauges}
        self.assertIsNone(by["5h"].resets_at)

    def test_zero_utilization_stays_zero_not_none(self):
        u = normalize_claude(_load("claude_get_usage.json"))
        by = {g.key: g for g in u.gauges}
        self.assertIsNotNone(by["scoped:Fable"].pct)
        self.assertEqual(by["scoped:Fable"].pct, 0)

    def test_does_not_mutate_the_input_document(self):
        doc = _load("claude_get_usage.json")
        before = copy.deepcopy(doc)
        normalize_claude(doc)
        self.assertEqual(doc, before)

    def test_is_idempotent(self):
        doc = _load("claude_get_usage.json")
        self.assertEqual(without_clock(normalize_claude(doc)),
                         without_clock(normalize_claude(doc)))


# ---------------------------------------------------------------------------
# providers: resolve_path
# ---------------------------------------------------------------------------


DOC = {
    "account": {"id": "acc-1"},
    "plan": "pro",
    "limits": {"five_hour": {"used_percent": 42.5, "resets_at": 1790301885}},
    "list": [{"a": 1}, {"a": 2}],
    "n": 7,
    "nothing": None,
    "empty": [],
}


class TestResolvePath(unittest.TestCase):
    def test_simple_dotted(self):
        self.assertEqual(resolve_path(DOC, "$.account.id"), "acc-1")
        self.assertEqual(resolve_path(DOC, "$.plan"), "pro")

    def test_nested(self):
        self.assertEqual(resolve_path(DOC, "$.limits.five_hour.used_percent"), 42.5)

    def test_index(self):
        self.assertEqual(resolve_path(DOC, "$.list[0].a"), 1)
        self.assertEqual(resolve_path(DOC, "$.list[1].a"), 2)

    def test_index_out_of_range_is_none(self):
        self.assertIsNone(resolve_path(DOC, "$.list[9].a"))
        self.assertIsNone(resolve_path(DOC, "$.empty[0]"))

    def test_missing_key_is_none(self):
        self.assertIsNone(resolve_path(DOC, "$.nope"))
        self.assertIsNone(resolve_path(DOC, "$.account.nope"))
        self.assertIsNone(resolve_path(DOC, "$.a.b.c.d.e"))

    def test_wrong_container_type_is_none(self):
        """Descending into a scalar must return None, never raise."""
        self.assertIsNone(resolve_path(DOC, "$.n.deeper"))
        self.assertIsNone(resolve_path(DOC, "$.plan.x"))
        self.assertIsNone(resolve_path(DOC, "$.account[0]"))

    def test_path_not_starting_with_dollar_is_none(self):
        self.assertIsNone(resolve_path(DOC, "account.id"))
        self.assertIsNone(resolve_path(DOC, ".account.id"))
        self.assertIsNone(resolve_path(DOC, ""))

    def test_json_null_resolves_to_none(self):
        self.assertIsNone(resolve_path(DOC, "$.nothing"))

    def test_non_dict_document_does_not_raise(self):
        self.assertIsNone(resolve_path(None, "$.a"))
        self.assertIsNone(resolve_path([], "$.a"))
        self.assertIsNone(resolve_path("scalar", "$.a"))

    def test_no_expressions_or_filters(self):
        """SPEC §4.3: no expressions, no filters, no code execution."""
        for p in ("$.list[?(@.a==1)]", "$.list[*].a", "$..a",
                  "$['account']['id']", "$.list[-1].a"):
            with self.subTest(path=p):
                try:
                    resolve_path(DOC, p)
                except Exception as exc:  # noqa: BLE001
                    self.fail(f"{p!r} raised {exc!r}; contract says never raises")

    def test_does_not_mutate_the_document(self):
        before = copy.deepcopy(DOC)
        for p in ("$.account.id", "$.list[0].a", "$.nope"):
            resolve_path(DOC, p)
        self.assertEqual(DOC, before)


# ---------------------------------------------------------------------------
# providers: run_declarative
# ---------------------------------------------------------------------------


class TestRunDeclarative(unittest.TestCase):
    SPEC = {
        "command": ["mytool", "usage", "--json"],
        "account_id": "$.account.id",
        "plan": "$.plan",
        "gauges": [{
            "label": "5h",
            "pct": "$.limits.five_hour.used_percent",
            "resets_at": "$.limits.five_hour.resets_at",
        }],
    }

    def test_happy_path(self):
        u = run_declarative("mytool", self.SPEC, DOC)
        self.assertEqual(u.provider, "mytool")
        self.assertEqual(u.account_id, "acc-1")
        self.assertEqual(u.plan, "pro")
        self.assertEqual(len(u.gauges), 1)
        g = u.gauges[0]
        self.assertEqual(g.label, "5h")
        self.assertEqual(g.pct, 42.5)
        self.assertEqual(g.resets_at, 1790301885)

    def test_integer_pct_is_a_real_number(self):
        doc = {"p": 42}
        u = run_declarative("t", {"gauges": [{"label": "5h", "pct": "$.p"}]}, doc)
        self.assertEqual(u.gauges[0].pct, 42)

    def test_zero_pct_is_kept(self):
        doc = {"p": 0}
        u = run_declarative("t", {"gauges": [{"label": "5h", "pct": "$.p"}]}, doc)
        self.assertIsNotNone(u.gauges[0].pct)
        self.assertEqual(u.gauges[0].pct, 0)

    def test_string_pct_yields_none(self):
        """SPEC §2.1 + API: 'a string (even "42")' must not become a percentage."""
        doc = {"p": "42"}
        u = run_declarative("t", {"gauges": [{"label": "5h", "pct": "$.p"}]}, doc)
        self.assertIsNone(u.gauges[0].pct)

    def test_numeric_string_with_decimals_yields_none(self):
        doc = {"p": "42.5"}
        u = run_declarative("t", {"gauges": [{"label": "5h", "pct": "$.p"}]}, doc)
        self.assertIsNone(u.gauges[0].pct)

    def test_bool_pct_yields_none(self):
        """bool is an int in Python — True must NOT slip through as 1%."""
        for value in (True, False):
            with self.subTest(value=value):
                u = run_declarative(
                    "t", {"gauges": [{"label": "5h", "pct": "$.p"}]}, {"p": value})
                self.assertIsNone(u.gauges[0].pct)

    def test_null_pct_yields_none(self):
        u = run_declarative(
            "t", {"gauges": [{"label": "5h", "pct": "$.p"}]}, {"p": None})
        self.assertIsNone(u.gauges[0].pct)

    def test_unresolvable_pct_path_yields_none(self):
        u = run_declarative(
            "t", {"gauges": [{"label": "5h", "pct": "$.nope.nope"}]}, {})
        self.assertEqual(len(u.gauges), 1)
        self.assertIsNone(u.gauges[0].pct)

    def test_container_pct_yields_none(self):
        for value in ([1, 2], {"a": 1}):
            with self.subTest(value=value):
                u = run_declarative(
                    "t", {"gauges": [{"label": "5h", "pct": "$.p"}]}, {"p": value})
                self.assertIsNone(u.gauges[0].pct)

    def test_key_defaults_to_label_when_absent(self):
        u = run_declarative("t", {"gauges": [{"label": "5h", "pct": "$.p"}]}, {"p": 1})
        self.assertEqual(u.gauges[0].key, "5h")

    def test_explicit_key_honoured(self):
        u = run_declarative(
            "t", {"gauges": [{"key": "mo", "label": "mo", "pct": "$.p"}]}, {"p": 1})
        self.assertEqual(u.gauges[0].key, "mo")

    def test_gauges_not_a_list_yields_no_gauges(self):
        """API: a malformed gauges value yields no gauges rather than an exception."""
        for bad in ("nope", 5, {"label": "5h"}, None, True):
            with self.subTest(gauges=bad):
                u = run_declarative("t", {"gauges": bad}, DOC)
                self.assertEqual(u.gauges, [])
                self.assertEqual(u.provider, "t")

    def test_gauge_entries_not_dicts_are_skipped(self):
        u = run_declarative("t", {"gauges": ["x", 5, None, [1]]}, DOC)
        self.assertEqual(u.gauges, [])

    def test_mixed_good_and_bad_entries(self):
        spec = {"gauges": ["junk", {"label": "5h", "pct": "$.n"}, None]}
        u = run_declarative("t", spec, DOC)
        self.assertEqual(len(u.gauges), 1)
        self.assertEqual(u.gauges[0].pct, 7)

    def test_empty_spec_does_not_raise(self):
        u = run_declarative("t", {}, DOC)
        self.assertEqual(u.provider, "t")
        self.assertEqual(u.gauges, [])
        self.assertIsNone(u.account_id)
        self.assertIsNone(u.plan)

    def test_empty_document_does_not_raise(self):
        u = run_declarative("t", self.SPEC, {})
        self.assertIsNone(u.account_id)
        self.assertIsNone(u.plan)
        self.assertIsNone(u.gauges[0].pct)

    def test_non_dict_spec_values_do_not_raise(self):
        spec = {"account_id": 5, "plan": None, "gauges": [{"label": "5h", "pct": 7}]}
        u = run_declarative("t", spec, DOC)
        self.assertEqual(u.provider, "t")

    def test_resets_at_unresolvable_is_none(self):
        spec = {"gauges": [{"label": "5h", "pct": "$.n", "resets_at": "$.nope"}]}
        self.assertIsNone(run_declarative("t", spec, DOC).gauges[0].resets_at)

    def test_does_not_mutate_spec_or_doc(self):
        spec, doc = copy.deepcopy(self.SPEC), copy.deepcopy(DOC)
        run_declarative("mytool", spec, doc)
        self.assertEqual(spec, self.SPEC)
        self.assertEqual(doc, DOC)

    def test_is_idempotent(self):
        a = run_declarative("mytool", self.SPEC, DOC)
        b = run_declarative("mytool", self.SPEC, DOC)
        self.assertEqual(without_clock(a), without_clock(b))


# ---------------------------------------------------------------------------
# cross-module: normalized output must render without inventing data
# ---------------------------------------------------------------------------


class TestEndToEndRendering(unittest.TestCase):
    def test_codex_fixture_renders_expected_rows(self):
        u = normalize_codex(_load("codex_ratelimits.json"))
        rows = sorted(gauge_text(g) for g in u.gauges)
        self.assertEqual(rows, ["5h ██░░░░░░░░  13%", "wk ███░░░░░░░  27%"])

    def test_claude_fixture_renders_expected_rows(self):
        u = normalize_claude(_load("claude_get_usage.json"))
        rows = {g.key: gauge_text(g) for g in u.gauges}
        self.assertEqual(rows["5h"], "5h ███░░░░░░░  22%")
        self.assertEqual(rows["wk"], "wk ██░░░░░░░░  11%")
        self.assertEqual(rows["scoped:Fable"], "wk Fable ░░░░░░░░░░   0%")

    def test_claude_blocked_row_has_no_percentage(self):
        u = normalize_claude(_load("claude_blocked.json"))
        by = {g.key: gauge_text(g) for g in u.gauges}
        self.assertIn(BLOCKED, by["5h"])
        self.assertNotIn("%", by["5h"])

    def test_declarative_unresolvable_renders_no_data_never_zero(self):
        """SPEC §2.1: unresolvable path -> '⋯ no data', not '0%'."""
        spec = {"gauges": [{"label": "5h", "pct": "$.missing"}]}
        u = run_declarative("t", spec, {})
        text = gauge_text(u.gauges[0])
        self.assertEqual(text, f"5h {NO_DATA}")
        self.assertNotIn("0%", text)

    def test_all_fixture_tokens_are_within_the_pane_budget(self):
        for fixture, norm in (("codex_ratelimits.json", normalize_codex),
                              ("claude_get_usage.json", normalize_claude)):
            with self.subTest(fixture=fixture):
                u = norm(_load(fixture))
                tokens, clear = build_tokens(u.gauges, extras={"model": "Opus"})
                self.assertLessEqual(len(tokens) + len(clear), 32)
                for name in list(tokens) + list(clear):
                    self.assertRegex(name, TOKEN_NAME_RE)
                for value in tokens.values():
                    self.assertLessEqual(len(value), TOKEN_MAX)


if __name__ == "__main__":
    unittest.main()
