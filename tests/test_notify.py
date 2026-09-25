"""Toast wrapping and machine enumeration.

A toast is drawn by the client, so the server has to guess its width from the
layout snapshot. Getting this wrong clips the message, which is what the user
reported.
"""

import unittest
from unittest import mock

from herdr_usage import herdrctl

LONG = (
    "Local sidebar updated. A remote client needs its own block: run "
    "`herdr plugin action invoke herdr-usage.print-sidebar` and paste "
    "the output into that machine's config.toml."
)


class TestWrapBody(unittest.TestCase):
    def test_no_line_exceeds_the_budget(self):
        for width in (30, 40, 60, 73, 120):
            out = herdrctl.wrap_body(LONG, width)
            for line in out.splitlines():
                self.assertLessEqual(len(line), width, f"width={width}: {line!r}")

    def test_short_text_is_untouched(self):
        self.assertEqual(herdrctl.wrap_body("short", 80), "short")

    def test_wraps_rather_than_truncating_when_it_fits(self):
        out = herdrctl.wrap_body(LONG, 73)
        self.assertGreater(len(out.splitlines()), 1)
        self.assertNotIn("…", out)  # fits within max_lines, nothing dropped

    def test_caps_line_count_with_an_ellipsis(self):
        out = herdrctl.wrap_body(LONG * 4, 40, max_lines=3)
        self.assertEqual(len(out.splitlines()), 3)
        self.assertTrue(out.endswith("…"))

    def test_existing_newlines_are_preserved_as_paragraphs(self):
        out = herdrctl.wrap_body("alpha\nbeta", 80)
        self.assertEqual(out, "alpha\nbeta")

    def test_absurdly_narrow_width_still_produces_lines(self):
        out = herdrctl.wrap_body(LONG, 4)
        self.assertTrue(out)
        for line in out.splitlines():
            self.assertLessEqual(len(line), 20)

    def test_empty_body_is_safe(self):
        self.assertEqual(herdrctl.wrap_body("", 80), "")


class TestClientWidth(unittest.TestCase):
    def _snapshot(self, widths):
        layouts = [{"area": {"width": w, "height": 40}} for w in widths]
        return mock.Mock(stdout='{"result": {"snapshot": {"layouts": %s}}}' % (
            str(layouts).replace("'", '"')))

    def test_uses_the_narrowest_layout(self):
        with mock.patch.object(herdrctl, "_run", return_value=self._snapshot([120, 73, 90])):
            self.assertEqual(herdrctl.client_width(), 73)

    def test_falls_back_when_snapshot_is_unparseable(self):
        with mock.patch.object(herdrctl, "_run", return_value=mock.Mock(stdout="not json")):
            self.assertEqual(herdrctl.client_width(default=55), 55)

    def test_falls_back_when_there_are_no_layouts(self):
        with mock.patch.object(herdrctl, "_run",
                               return_value=mock.Mock(stdout='{"result":{"snapshot":{"layouts":[]}}}')):
            self.assertEqual(herdrctl.client_width(default=55), 55)

    def test_survives_a_missing_herdr_binary(self):
        with mock.patch.object(herdrctl, "_run", side_effect=OSError):
            self.assertEqual(herdrctl.client_width(default=44), 44)


class TestMachines(unittest.TestCase):
    def test_parses_a_bare_list(self):
        out = '[{"id": "abc", "label": "Work linux PC"}]'
        with mock.patch.object(herdrctl, "_run", return_value=mock.Mock(stdout=out)):
            self.assertEqual(herdrctl.machines()[0]["label"], "Work linux PC")

    def test_empty_list_means_no_peers(self):
        with mock.patch.object(herdrctl, "_run", return_value=mock.Mock(stdout="[]")):
            self.assertEqual(herdrctl.machines(), [])

    def test_bad_json_is_not_an_error(self):
        with mock.patch.object(herdrctl, "_run", return_value=mock.Mock(stdout="boom")):
            self.assertEqual(herdrctl.machines(), [])

    def test_missing_binary_is_not_an_error(self):
        with mock.patch.object(herdrctl, "_run", side_effect=OSError):
            self.assertEqual(herdrctl.machines(), [])


class TestNotifyWraps(unittest.TestCase):
    def test_body_is_wrapped_before_being_passed_to_herdr(self):
        seen = {}

        def fake_run(args, check=True):
            seen["args"] = args
            return mock.Mock(returncode=0)

        with mock.patch.object(herdrctl, "_run", side_effect=fake_run):
            herdrctl.notify("title", LONG, width=40)
        body = seen["args"][seen["args"].index("--body") + 1]
        self.assertIn("\n", body)
        for line in body.splitlines():
            self.assertLessEqual(len(line), 40)

    def test_no_body_flag_when_body_is_empty(self):
        seen = {}

        def fake_run(args, check=True):
            seen["args"] = args
            return mock.Mock(returncode=0)

        with mock.patch.object(herdrctl, "_run", side_effect=fake_run):
            herdrctl.notify("title")
        self.assertNotIn("--body", seen["args"])


if __name__ == "__main__":
    unittest.main()
