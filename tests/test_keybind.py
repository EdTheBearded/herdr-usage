"""The settings-popup keybinding in the managed block.

The load-bearing rule: never claim a key the user already bound. A silently
hijacked shortcut is worse than no shortcut, because the user's own binding
stops working for a reason they can't see.
"""

import unittest

from herdr_usage import config, sidebar

REAL_CONFIG_EXCERPT = """
prefix = "ctrl+b"
help = "prefix+?"
settings = "prefix+s"
# detach = "prefix+q"
goto = "prefix+g"
new_tab = ["prefix+c", "f2"]
close_tab = ["prefix+shift+x", "f6"]
# edit_scrollback = "prefix+e"
# toggle_sidebar = "prefix+b"

[[keys.command]]
key = "prefix+t"
type = "popup"
command = "exec \\"${SHELL:-sh}\\""

[[keys.command]]
key = "prefix+y"
type = "popup"
command = "bash -ic 'yocto-setup --modal'"
"""


class TestKeyInUse(unittest.TestCase):
    def test_detects_active_action_binding(self):
        self.assertTrue(sidebar.key_in_use(REAL_CONFIG_EXCERPT, "prefix+s"))
        self.assertTrue(sidebar.key_in_use(REAL_CONFIG_EXCERPT, "prefix+g"))

    def test_detects_binding_inside_a_list(self):
        # new_tab = ["prefix+c", "f2"] -- the second element must count too.
        self.assertTrue(sidebar.key_in_use(REAL_CONFIG_EXCERPT, "prefix+c"))
        self.assertTrue(sidebar.key_in_use(REAL_CONFIG_EXCERPT, "f2"))

    def test_detects_user_command_binding(self):
        self.assertTrue(sidebar.key_in_use(REAL_CONFIG_EXCERPT, "prefix+t"))
        self.assertTrue(sidebar.key_in_use(REAL_CONFIG_EXCERPT, "prefix+y"))

    def test_commented_default_counts_as_reserved(self):
        # An unset Herdr default is a reservation: taking it means a future
        # `herdr config reset-keys` silently collides with us.
        self.assertTrue(sidebar.key_in_use(REAL_CONFIG_EXCERPT, "prefix+q"))
        self.assertTrue(sidebar.key_in_use(REAL_CONFIG_EXCERPT, "prefix+b"))

    def test_free_key_is_free(self):
        for key in ("prefix+u", "prefix+a", "prefix+m"):
            self.assertFalse(sidebar.key_in_use(REAL_CONFIG_EXCERPT, key), key)

    def test_no_substring_false_positive(self):
        # "prefix+m" must not match "prefix+minus".
        self.assertFalse(sidebar.key_in_use('split = "prefix+minus"', "prefix+m"))

    def test_our_own_block_is_not_a_collision(self):
        # Reinstalling must be idempotent, not a self-detected conflict.
        text = (
            REAL_CONFIG_EXCERPT
            + f"\n{sidebar.BEGIN}\n"
            + sidebar.keybind({"display": {"settings_key": "prefix+u"}})
            + f"{sidebar.END}\n"
        )
        self.assertFalse(sidebar.key_in_use(text, "prefix+u"))

    def test_empty_key_is_never_in_use(self):
        self.assertFalse(sidebar.key_in_use(REAL_CONFIG_EXCERPT, ""))


class TestKeybindBlock(unittest.TestCase):
    def test_emits_a_plugin_action_binding(self):
        out = sidebar.keybind({"display": {"settings_key": "prefix+u"}})
        self.assertIn('key = "prefix+u"', out)
        self.assertIn('type = "plugin_action"', out)
        self.assertIn('command = "herdr-usage.settings"', out)

    def test_empty_key_emits_nothing(self):
        self.assertEqual(sidebar.keybind({"display": {"settings_key": ""}}), "")
        self.assertEqual(sidebar.keybind({"display": {}}), "")
        self.assertEqual(sidebar.keybind({}), "")

    def test_whitespace_only_key_emits_nothing(self):
        self.assertEqual(sidebar.keybind({"display": {"settings_key": "  "}}), "")

    def test_generate_includes_the_binding(self):
        block = sidebar.generate(config.DEFAULTS)
        self.assertIn("[[keys.command]]", block)
        self.assertIn('key = "prefix+u"', block)

    def test_binding_sits_after_the_sidebar_table(self):
        # TOML arrays-of-tables end the preceding table, so [[keys.command]]
        # must come last or it would swallow the sidebar's own keys.
        block = sidebar.generate(config.DEFAULTS)
        self.assertLess(block.index("[ui.sidebar.agents]"), block.index("[[keys.command]]"))

    def test_undo_removes_the_binding_too(self):
        # One marker pair means one uninstall; the keybind must not survive it.
        text = f"a = 1\n\n{sidebar.BEGIN}\n{sidebar.generate(config.DEFAULTS)}{sidebar.END}\n"
        self.assertIn("[[keys.command]]", text)
        self.assertNotIn("[[keys.command]]", sidebar._strip(text))


class TestDefaultKey(unittest.TestCase):
    def test_default_is_prefix_u(self):
        self.assertEqual(config.DEFAULTS["display"]["settings_key"], "prefix+u")

    def test_default_does_not_collide_with_herdr_defaults(self):
        # Every stock Herdr binding, active or commented, from a real config.
        self.assertFalse(
            sidebar.key_in_use(REAL_CONFIG_EXCERPT,
                               config.DEFAULTS["display"]["settings_key"])
        )


if __name__ == "__main__":
    unittest.main()
