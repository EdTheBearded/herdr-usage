"""T5, T6, T7, T11 — provider normalizers, declarative runner, model identity."""

import json
import pathlib
import unittest

from herdr_usage.providers import (
    normalize_claude,
    normalize_codex,
    resolve_path,
    run_declarative,
    short_model,
)

FIX = pathlib.Path(__file__).parent / "fixtures"


def load(name):
    return json.loads((FIX / name).read_text())


class TestCodex(unittest.TestCase):
    """T5"""

    def test_two_gauges_with_right_labels(self):
        u = normalize_codex(load("codex_ratelimits.json"))
        self.assertEqual([g.label for g in u.gauges], ["5h", "wk"])
        self.assertEqual(u.plan, "team")

    def test_swapped_windows_yield_identical_gauges(self):
        """primary is the WEEKLY window in 2242/3703 real snapshots."""
        a = normalize_codex(load("codex_ratelimits.json"))
        b = normalize_codex(load("codex_ratelimits_swapped.json"))
        self.assertEqual(
            {(g.label, g.pct) for g in a.gauges},
            {(g.label, g.pct) for g in b.gauges},
        )

    def test_snake_case_rollout_parses_the_same(self):
        camel = normalize_codex(load("codex_ratelimits.json"))
        snake = normalize_codex({"rateLimits": load("codex_rollout_snake.json")})
        self.assertEqual(
            {(g.label, g.pct) for g in camel.gauges},
            {(g.label, g.pct) for g in snake.gauges},
        )

    def test_blocked(self):
        u = normalize_codex(load("codex_blocked.json"))
        self.assertTrue(any(g.blocked for g in u.gauges))

    def test_credits_balance_string_or_null(self):
        u = normalize_codex(load("codex_blocked.json"))  # balance "12.50"
        self.assertIsNotNone(u.credits)
        u2 = normalize_codex(load("codex_ratelimits.json"))  # balance null
        self.assertIsNotNone(u2.credits)

    def test_reset_credits_count(self):
        u = normalize_codex(load("codex_ratelimits.json"))
        self.assertEqual(u.credits, 3)

    def test_unknown_window_does_not_crash(self):
        d = load("codex_ratelimits.json")
        d["rateLimits"]["primary"]["windowDurationMins"] = 43200
        u = normalize_codex(d)
        self.assertIn("mo", [g.label for g in u.gauges])

    def test_account_id_carried(self):
        u = normalize_codex(load("codex_ratelimits.json"))
        self.assertTrue(u.account_id)

    def test_null_window_skipped_not_crashed(self):
        d = load("codex_ratelimits.json")
        d["rateLimits"]["secondary"] = None
        u = normalize_codex(d)
        self.assertEqual(len(u.gauges), 1)


class TestClaude(unittest.TestCase):
    """T6"""

    def test_five_hour_and_seven_day(self):
        u = normalize_claude(load("claude_get_usage.json"))
        labels = [g.label for g in u.gauges]
        self.assertIn("5h", labels)
        self.assertIn("wk", labels)

    def test_null_seven_day_opus_produces_no_gauge(self):
        """Don't hardcode Opus - this account's scoped row is 'Fable'."""
        u = normalize_claude(load("claude_get_usage.json"))
        self.assertFalse(any("opus" in g.key.lower() for g in u.gauges))

    def test_model_scoped_becomes_a_gauge(self):
        u = normalize_claude(load("claude_get_usage.json"))
        scoped = [g for g in u.gauges if g.key.startswith("scoped:")]
        self.assertEqual(len(scoped), 1)
        self.assertIn("Fable", scoped[0].label)

    def test_severity_carried_through(self):
        u = normalize_claude(load("claude_blocked.json"))
        five = next(g for g in u.gauges if g.label == "5h")
        self.assertEqual(five.severity, "critical")

    def test_locked_reason_is_blocked(self):
        u = normalize_claude(load("claude_blocked.json"))
        five = next(g for g in u.gauges if g.label == "5h")
        self.assertTrue(five.blocked)

    def test_null_subscription_type_is_not_the_string_none(self):
        u = normalize_claude(load("claude_get_usage.json"))
        self.assertIsNone(u.plan)

    def test_cost_carried(self):
        u = normalize_claude(load("claude_get_usage.json"))
        self.assertIsNotNone(u.cost_usd)

    def test_iso_resets_at_becomes_epoch(self):
        u = normalize_claude(load("claude_get_usage.json"))
        five = next(g for g in u.gauges if g.label == "5h")
        self.assertIsInstance(five.resets_at, (int, float))
        self.assertGreater(five.resets_at, 1_600_000_000)


class TestDeclarative(unittest.TestCase):
    """T7"""

    DOC = {"account": {"id": "acc1"}, "plan": "pro",
           "limits": {"five_hour": {"used_percent": 42, "resets_at": 1790301885}},
           "list": [{"c": 7}]}

    def test_dotted_path(self):
        self.assertEqual(resolve_path(self.DOC, "$.account.id"), "acc1")
        self.assertEqual(resolve_path(self.DOC, "$.list[0].c"), 7)

    def test_missing_path_is_none(self):
        self.assertIsNone(resolve_path(self.DOC, "$.nope.deeper"))
        self.assertIsNone(resolve_path(self.DOC, "$.list[9].c"))

    def test_spec(self):
        spec = {
            "account_id": "$.account.id",
            "plan": "$.plan",
            "gauges": [{"label": "5h",
                        "pct": "$.limits.five_hour.used_percent",
                        "resets_at": "$.limits.five_hour.resets_at"}],
        }
        u = run_declarative("mytool", spec, self.DOC)
        self.assertEqual(u.account_id, "acc1")
        self.assertEqual(u.gauges[0].pct, 42)

    def test_unresolvable_pct_is_none_not_crash(self):
        spec = {"gauges": [{"label": "5h", "pct": "$.nothing.here"}]}
        u = run_declarative("mytool", spec, self.DOC)
        self.assertIsNone(u.gauges[0].pct)

    def test_non_numeric_pct_is_none(self):
        spec = {"gauges": [{"label": "5h", "pct": "$.plan"}]}
        u = run_declarative("mytool", spec, self.DOC)
        self.assertIsNone(u.gauges[0].pct)

    def test_numeric_string_pct_is_rejected(self):
        """'never invent a percentage' - a string is not a number.

        Guards against a lax float() coercion that would also turn True into
        1.0 and a version string into a bogus reading.
        """
        doc = {"p": "42", "flag": True}
        spec = {"gauges": [{"label": "5h", "pct": "$.p"}]}
        self.assertIsNone(run_declarative("mytool", spec, doc).gauges[0].pct)
        spec = {"gauges": [{"label": "5h", "pct": "$.flag"}]}
        self.assertIsNone(run_declarative("mytool", spec, doc).gauges[0].pct)

    def test_malformed_spec_yields_no_gauges_not_exception(self):
        u = run_declarative("mytool", {"gauges": "nonsense"}, self.DOC)
        self.assertEqual(u.gauges, [])


class TestShortModel(unittest.TestCase):
    """T11"""

    def test_known(self):
        self.assertEqual(short_model("claude-opus-5"), "Opus")
        self.assertEqual(short_model("claude-sonnet-5"), "Sonnet")
        self.assertEqual(short_model("claude-haiku-4-5-20251001"), "Haiku")
        self.assertEqual(short_model("gpt-5.6-terra"), "Terra")
        self.assertEqual(short_model("gpt-6-sol"), "Sol")

    def test_unknown_slug_returned_truncated(self):
        out = short_model("some-weird-model-name-that-is-long")
        self.assertTrue(out)
        self.assertLessEqual(len(out), 20)

    def test_none(self):
        self.assertIsNone(short_model(None))


if __name__ == "__main__":
    unittest.main()
