"""Vendor icons.

The load-bearing rule: an icon is a claim about which account is being
billed. An unknown provider must get NO icon rather than a default one --
showing the Anthropic mark next to an unrecognised vendor is the same class
of lie as showing someone else's percentage.
"""

import unittest

from herdr_usage import config, main, render


class TestIconFor(unittest.TestCase):
    def test_brand_icons_are_the_verified_codepoints(self):
        # These exact codepoints were confirmed rendering in a live pane on
        # the user's Windows Terminal. Changing them needs re-verification.
        self.assertEqual(render.icon_for("claude"), "\uec82")   # cod-claude
        self.assertEqual(render.icon_for("codex"), "\uec81")    # cod-openai

    def test_provider_aliases_map_to_same_vendor(self):
        self.assertEqual(render.icon_for("claude"), render.icon_for("anthropic"))
        self.assertEqual(render.icon_for("codex"), render.icon_for("openai"))

    def test_case_insensitive(self):
        self.assertEqual(render.icon_for("Claude"), render.icon_for("claude"))

    def test_unknown_provider_gets_no_icon(self):
        self.assertIsNone(render.icon_for("mistral"))
        self.assertIsNone(render.icon_for("some-new-vendor"))

    def test_none_provider_gets_no_icon(self):
        self.assertIsNone(render.icon_for(None))
        self.assertIsNone(render.icon_for(""))

    def test_every_set_covers_every_vendor(self):
        vendors = set(render.ICON_SETS["brand"])
        for name, icons in render.ICON_SETS.items():
            self.assertEqual(set(icons), vendors, f"{name} set is missing a vendor")

    def test_unknown_icon_set_falls_back_to_brand(self):
        self.assertEqual(
            render.icon_for("claude", "no-such-set"), render.icon_for("claude", "brand")
        )

    def test_ascii_set_is_pure_ascii(self):
        # The whole point of this set is terminals without a patched font.
        for glyph in render.ICON_SETS["ascii"].values():
            glyph.encode("ascii")  # raises if not

    def test_geometric_set_excludes_glyphs_missing_from_firacode(self):
        # U+2B22/U+2B21 render as tofu in FiraCode Nerd Font -- a fallback set
        # containing them would defeat its own purpose.
        for glyph in render.ICON_SETS["geometric"].values():
            self.assertNotIn(glyph, ("\u2b22", "\u2b21"))


class TestBlockedGlyph(unittest.TestCase):
    def test_blocked_has_no_variation_selector(self):
        # U+FE0F is absent from FiraCode Nerd Font and cost an extra cell in a
        # live pane, wrapping the row.
        self.assertNotIn("\ufe0f", render.BLOCKED)
        self.assertEqual(render.BLOCKED, "\u26d4")


def _pane(pane_id="w1:p1", **kw):
    p = {"pane_id": pane_id, "agent": "hermes"}
    p.update(kw)
    return p


def _entry(provider):
    return {"provider": provider, "gauges": [], "fetched_at": 1000}


class TestIconInTokens(unittest.TestCase):
    """The icon rides on uq_model, so it inherits that token's plumbing."""

    def _run(self, panes, cache_data, cfg=None):
        cfg = cfg or config.DEFAULTS
        return main.refresh(cfg, cache_data, panes, {}, now=1000)

    def test_icon_prefixes_the_model_token(self):
        out = self._run(
            [_pane(provider="claude", model="Opus")],
            {"claude": _entry("claude")},
        )
        self.assertEqual(out["panes"]["w1:p1"]["tokens"]["uq_model"], "\uec82 Opus")

    def test_icon_renders_without_a_model(self):
        # Model is unknown until the first turn; the vendor is still known.
        out = self._run(
            [_pane(provider="claude")],
            {"claude": _entry("claude")},
        )
        self.assertEqual(out["panes"]["w1:p1"]["tokens"]["uq_model"], "\uec82")

    def test_local_vendor_icon_when_no_quota_provider(self):
        out = self._run(
            [_pane(provider=None, vendor="local", model="Qwen")],
            {},
        )
        self.assertEqual(out["panes"]["w1:p1"]["tokens"]["uq_model"], "\uec20 Qwen")

    def test_no_icon_when_neither_provider_nor_vendor_known(self):
        out = self._run([_pane(provider=None, model="Mystery")], {})
        self.assertEqual(out["panes"]["w1:p1"]["tokens"]["uq_model"], "Mystery")

    def test_show_icon_false_suppresses_it(self):
        cfg = config._merge(config.DEFAULTS, {"display": {"show_icon": False}})
        out = self._run(
            [_pane(provider="claude", model="Opus")],
            {"claude": _entry("claude")},
            cfg,
        )
        self.assertEqual(out["panes"]["w1:p1"]["tokens"]["uq_model"], "Opus")

    def test_icon_set_is_honoured(self):
        cfg = config._merge(config.DEFAULTS, {"display": {"icon_set": "ascii"}})
        out = self._run(
            [_pane(provider="claude", model="Opus")],
            {"claude": _entry("claude")},
            cfg,
        )
        self.assertEqual(out["panes"]["w1:p1"]["tokens"]["uq_model"], "[A] Opus")

    def test_icon_follows_billed_provider_not_agent_name(self):
        # A hermes pane routed to codex must show the OpenAI mark.
        out = self._run(
            [_pane(agent="hermes", provider="codex", model="Terra")],
            {"codex": _entry("codex")},
        )
        self.assertEqual(out["panes"]["w1:p1"]["tokens"]["uq_model"], "\uec81 Terra")


class TestSettingsExposesIcons(unittest.TestCase):
    def test_icon_rows_are_in_the_settings_popup(self):
        from herdr_usage import settings_tui

        keys = {key for _, key, _, _ in settings_tui.ROWS}
        self.assertIn("display.icon_set", keys)
        self.assertIn("display.show_icon", keys)

    def test_icon_set_choices_match_render(self):
        from herdr_usage import settings_tui

        for _, key, _, bounds in settings_tui.ROWS:
            if key == "display.icon_set":
                self.assertEqual(set(bounds), set(render.ICON_SETS))
                return
        self.fail("icon_set row not found")


if __name__ == "__main__":
    unittest.main()
