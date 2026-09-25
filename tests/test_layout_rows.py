"""Sidebar line split, harness icon, and the stay-open settings popup."""

import unittest

from herdr_usage import config, main, render, settings_tui, sidebar


class TestHermesIcon(unittest.TestCase):
    def test_hermes_has_an_icon_in_every_set(self):
        for name, icons in render.ICON_SETS.items():
            self.assertIn("hermes", icons, f"{name} set lacks hermes")

    def test_brand_hermes_is_cod_agent(self):
        # Hermes is the agent harness, not the Greek god: cod-agent U+EC67,
        # verified present in FiraCode Nerd Font.
        self.assertEqual(render.icon_for("hermes"), "\uec67")

    def test_geometric_hermes_avoids_missing_glyphs(self):
        # ☉ U+2609 renders as tofu in FiraCode NF.
        self.assertNotEqual(render.ICON_SETS["geometric"]["hermes"], "\u2609")


class TestHarnessVsVendor(unittest.TestCase):
    """A wrapper's harness and its billed vendor are different facts."""

    def _tokens(self, pane, cache=None):
        out = main.refresh(config.DEFAULTS, cache or {}, [pane], {}, now=1000)
        return out["panes"][pane["pane_id"]]["tokens"]

    def test_hermes_pane_shows_harness_and_vendor_separately(self):
        t = self._tokens(
            {"pane_id": "w1:p1", "agent": "hermes", "provider": "claude",
             "model": "Opus"},
            {"claude": {"provider": "claude", "gauges": [], "fetched_at": 1000}},
        )
        self.assertEqual(t["uq_agent"], "\uec67")        # hermes harness
        self.assertEqual(t["uq_model"], "\uec82 Opus")   # anthropic billing

    def test_native_claude_pane_does_not_repeat_the_icon(self):
        # Harness and vendor are the same here; showing it twice is noise.
        t = self._tokens(
            {"pane_id": "w1:p1", "agent": "claude", "provider": "claude",
             "model": "Opus"},
            {"claude": {"provider": "claude", "gauges": [], "fetched_at": 1000}},
        )
        self.assertEqual(t["uq_agent"], "\uec82")
        self.assertEqual(t["uq_model"], "Opus")

    def test_unknown_harness_emits_no_agent_token(self):
        out = main.refresh(
            config.DEFAULTS, {},
            [{"pane_id": "w1:p1", "agent": "somethingelse", "provider": None}],
            {}, now=1000)
        p = out["panes"]["w1:p1"]
        self.assertNotIn("uq_agent", p["tokens"])
        self.assertIn("uq_agent", p["clear"])


class TestSidebarLineSplit(unittest.TestCase):
    def test_model_is_not_on_the_header_row(self):
        rows = sidebar.generate(config.DEFAULTS).splitlines()
        header = next(r for r in rows if "state_icon" in r)
        self.assertNotIn("$uq_model", header)

    def test_header_carries_machine_agent_and_harness_icon(self):
        header = next(r for r in sidebar.generate(config.DEFAULTS).splitlines()
                      if "state_icon" in r)
        for token in ('"machine"', '"agent"', '"$uq_agent"'):
            self.assertIn(token, header)

    def test_model_row_exists_below_the_header(self):
        block = sidebar.generate(config.DEFAULTS)
        rows = block.splitlines()
        header_i = next(i for i, r in enumerate(rows) if "state_icon" in r)
        model_i = next(i for i, r in enumerate(rows) if "$uq_model" in r)
        self.assertGreater(model_i, header_i)

    def test_no_empty_second_row_when_model_and_plan_are_off(self):
        cfg = config._merge(config.DEFAULTS,
                            {"display": {"show_model": False, "show_plan": False}})
        self.assertNotIn("  [],", sidebar.generate(cfg))

    def test_token_budget_stays_under_the_herdr_cap(self):
        # Herdr rejects a pane with more than 32 tokens.
        gauges = [render.Gauge(k, k, pct=10.0) for k in ("5h", "wk", "mo", "scoped:X")]
        tokens, clear = render.build_tokens(
            gauges, extras={"agent": "x", "model": "y", "plan": "z",
                            "burn": "b", "extra": "e"})
        self.assertLessEqual(len(tokens) + len(clear), 32)


class TestControlHintAlwaysVisible(unittest.TestCase):
    """The hint was being pushed off a short popup, hiding the keys."""

    def _state(self, cursor=0):
        s = settings_tui.initial_state(config.DEFAULTS)
        s["cursor"] = cursor
        return s

    def test_hint_is_the_last_line_when_space_is_tight(self):
        for rows in (8, 12, 16, 22):
            out = settings_tui.render_lines(self._state(), rows=rows)
            self.assertIn("save", out[-1], f"rows={rows}: hint missing")

    def test_output_fits_the_window(self):
        for rows in (8, 12, 16, 22):
            out = settings_tui.render_lines(self._state(), rows=rows)
            self.assertLessEqual(len(out), rows, f"rows={rows} overflowed")

    def test_cursor_stays_visible_when_scrolled(self):
        last = len(settings_tui.ROWS) - 1
        label = settings_tui.ROWS[last][0]
        out = settings_tui.render_lines(self._state(cursor=last), rows=10)
        self.assertTrue(any(label in line for line in out),
                        "cursor row scrolled out of view")

    def test_unbounded_render_is_unchanged(self):
        out = settings_tui.render_lines(self._state())
        self.assertEqual(len(out), len(settings_tui.ROWS) + 4)
        self.assertIn("save", out[-1])


class TestSaveKeepsReducerContract(unittest.TestCase):
    """Staying open is a shell decision; the reducer must still report save."""

    def test_reduce_still_marks_save(self):
        state = settings_tui.initial_state(config.DEFAULTS)
        self.assertEqual(settings_tui.reduce(state, "s")["done"], "save")

    def test_reduce_still_marks_cancel(self):
        state = settings_tui.initial_state(config.DEFAULTS)
        self.assertEqual(settings_tui.reduce(state, "q")["done"], "cancel")


class TestApplyToRemoved(unittest.TestCase):
    def test_setting_is_gone_from_defaults(self):
        self.assertNotIn("apply_to", config.DEFAULTS["display"])

    def test_row_is_gone_from_the_popup(self):
        keys = {key for _, key, _, _ in settings_tui.ROWS}
        self.assertNotIn("display.apply_to", keys)


class TestSaveNoteDoesNotGrowTheMenu(unittest.TestCase):
    """The reported bug: save output desynced curses and skewed the menu."""

    def _state(self):
        return settings_tui.initial_state(config.DEFAULTS)

    def test_note_does_not_add_a_line(self):
        plain = settings_tui.render_lines(self._state())
        noted = settings_tui.render_lines(self._state(), note="saved")
        self.assertEqual(len(plain), len(noted))

    def test_note_appears_in_the_header(self):
        out = settings_tui.render_lines(self._state(), note="saved")
        self.assertIn("saved", out[0])

    def test_note_never_displaces_the_hint(self):
        for rows in (8, 12, 22):
            out = settings_tui.render_lines(self._state(), rows=rows, note="saved")
            self.assertIn("save", out[-1])
            self.assertLessEqual(len(out), rows)

    def test_row_count_is_identical_with_and_without_a_note(self):
        # Any difference means the menu shifts under the cursor after a save.
        for rows in (8, 12, 22):
            a = settings_tui.render_lines(self._state(), rows=rows)
            b = settings_tui.render_lines(self._state(), rows=rows, note="saved")
            self.assertEqual(len(a), len(b), f"rows={rows}")

    def test_on_save_output_is_not_written_to_the_screen(self):
        """A callback that prints must not corrupt the display."""
        import io as _io

        def noisy(_cfg):
            print("sidebar block written to /some/path")
            print("press prefix+u to edit the gauges")

        out = _io.StringIO()
        settings_tui.run_plain(
            config.DEFAULTS, stdin=_io.StringIO("s\nq\n"), stdout=out, on_save=noisy
        )
        self.assertNotIn("sidebar block written", out.getvalue())
        self.assertIn("saved", out.getvalue())


if __name__ == "__main__":
    unittest.main()
