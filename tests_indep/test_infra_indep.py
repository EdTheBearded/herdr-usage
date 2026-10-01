"""Independent unit tests for the herdr-usage infrastructure modules.

The implementation and the primary test suite were not supplied to this author.

Scope: herdr_usage.config, .cache, .sidebar, .herdrctl, .main.refresh, and
.settings_tui.reduce.
"""

from __future__ import annotations

import copy
import json
import os
import pathlib
import shlex
import subprocess
import sys
import tempfile
import time
import tomllib
import unittest
from unittest import mock

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

# --------------------------------------------------------------------------
# Import guards -- one flag per module so the suite stays runnable as the
# modules land incrementally.
# --------------------------------------------------------------------------
try:
    from herdr_usage import config as uconfig
    HAVE_CONFIG = True
except ImportError:
    uconfig = None
    HAVE_CONFIG = False

try:
    from herdr_usage import cache as ucache
    HAVE_CACHE = True
except ImportError:
    ucache = None
    HAVE_CACHE = False

try:
    from herdr_usage import sidebar as usidebar
    HAVE_SIDEBAR = True
except ImportError:
    usidebar = None
    HAVE_SIDEBAR = False

try:
    from herdr_usage import herdrctl as uherdrctl
    HAVE_HERDRCTL = True
except ImportError:
    uherdrctl = None
    HAVE_HERDRCTL = False

try:
    from herdr_usage import main as umain
    HAVE_REFRESH = hasattr(umain, "refresh")
except ImportError:
    umain = None
    HAVE_REFRESH = False

try:
    from herdr_usage import settings_tui as utui
    HAVE_TUI = hasattr(utui, "reduce")
except ImportError:
    utui = None
    HAVE_TUI = False

try:
    from herdr_usage import model as umodel
except ImportError:  # pragma: no cover - model is already implemented
    umodel = None

HAVE_IMPL = all(
    (HAVE_CONFIG, HAVE_CACHE, HAVE_SIDEBAR, HAVE_HERDRCTL, HAVE_REFRESH, HAVE_TUI)
)

NOT_YET = "not implemented yet"

# Config schema from SPEC section 9, restated here so these tests do not depend
# on herdr_usage.config having landed.
SPEC_DEFAULTS = {
    "refresh": {"interval_secs": 300, "min_interval_secs": 60},
    "display": {
        "bar_set": "blocks",
        "bar_cells": 10,
        "gauges": ["5h", "wk"],
        "show_model": True,
        "show_plan": False,
        "show_burn": False,
        "show_resets_in": False,
        "show_credits": False,
        "show_cost": False,
        "show_scoped": False,
    },
    "thresholds": {"warn": 70, "crit": 90, "stale_multiplier": 3},
    "providers": {"codex": {"enabled": True}, "claude": {"enabled": True}},
}

BAR_SET_NAMES = {"blocks", "shade", "squares", "dots", "half", "ascii"}


# --------------------------------------------------------------------------
# Shape-tolerant introspection helpers (used by the reducer + cache tests so
# they pin behaviour rather than a particular dict layout).
# --------------------------------------------------------------------------
def _walk(obj, path=()):
    """Yield (path, key, value) for every dict entry nested anywhere in obj."""
    if isinstance(obj, dict):
        for k, v in obj.items():
            here = path + (str(k),)
            yield here, str(k), v
            yield from _walk(v, here)
    elif isinstance(obj, (list, tuple)):
        for i, v in enumerate(obj):
            yield from _walk(v, path + (str(i),))


def _bool_leaves(obj):
    return {p: v for p, _k, v in _walk(obj) if isinstance(v, bool)}


def _number_leaves(obj):
    return {p: v for p, _k, v in _walk(obj)
            if isinstance(v, (int, float)) and not isinstance(v, bool)}


def _find_by_key(obj, names, want=None):
    """First (path, value) whose key matches `names` (and type, if given)."""
    for p, k, v in _walk(obj):
        if k.lower() in names and (want is None or isinstance(v, want)):
            return p, v
    return None, None


CURSOR_KEYS = {"cursor", "index", "idx", "selected", "sel", "row", "pos", "position"}


def _cursor(state):
    p, v = _find_by_key(state, CURSOR_KEYS, int)
    if p is None:
        raise AssertionError(
            "SPEC GAP: reducer state exposes no integer cursor/index key "
            f"(looked for {sorted(CURSOR_KEYS)}); got keys {list(state)}"
        )
    return v


def _bar_set(state):
    _p, v = _find_by_key(state, {"bar_set", "barset", "bars"}, str)
    return v


# --------------------------------------------------------------------------
# herdr_usage.config -- T8
# --------------------------------------------------------------------------
@unittest.skipUnless(HAVE_CONFIG, NOT_YET)
class TestConfigLoad(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = pathlib.Path(self.tmp.name)

    def write(self, text, name="config.toml"):
        p = self.dir / name
        p.write_text(text, encoding="utf-8")
        return p

    def test_missing_file_yields_defaults(self):
        cfg = uconfig.load(self.dir / "does-not-exist.toml")
        self.assertEqual(cfg["display"]["bar_set"], "blocks")
        self.assertEqual(cfg["display"]["bar_cells"], 10)
        self.assertEqual(cfg["display"]["gauges"], ["5h", "wk"])
        self.assertIs(cfg["display"]["show_model"], True)
        self.assertIs(cfg["display"]["show_plan"], False)
        self.assertEqual(cfg["thresholds"]["warn"], 70)
        self.assertEqual(cfg["thresholds"]["crit"], 90)
        self.assertEqual(cfg["thresholds"]["stale_multiplier"], 3)
        self.assertEqual(cfg["refresh"]["interval_secs"], 300)
        self.assertEqual(cfg["refresh"]["min_interval_secs"], 60)
        self.assertIs(cfg["providers"]["codex"]["enabled"], True)
        self.assertIs(cfg["providers"]["claude"]["enabled"], True)

    def test_defaults_constant_matches_spec_schema(self):
        d = uconfig.DEFAULTS
        for table, keys in SPEC_DEFAULTS.items():
            self.assertIn(table, d, f"DEFAULTS missing [{table}]")
            for k, v in keys.items():
                self.assertIn(k, d[table], f"DEFAULTS missing {table}.{k}")
                self.assertEqual(d[table][k], v, f"{table}.{k}")

    def test_load_never_hands_out_the_defaults_object(self):
        """Mutating a loaded config must not poison later loads."""
        a = uconfig.load(self.dir / "nope.toml")
        a["display"]["bar_cells"] = 999
        a["display"]["gauges"].append("mo")
        b = uconfig.load(self.dir / "nope.toml")
        self.assertEqual(b["display"]["bar_cells"], 10)
        self.assertEqual(b["display"]["gauges"], ["5h", "wk"])
        self.assertEqual(uconfig.DEFAULTS["display"]["bar_cells"], 10)

    def test_partial_file_deep_merges_over_defaults(self):
        p = self.write(
            "[display]\n"
            "bar_cells = 4\n"
            "\n"
            "[thresholds]\n"
            "warn = 55\n"
        )
        cfg = uconfig.load(p)
        # specified keys win
        self.assertEqual(cfg["display"]["bar_cells"], 4)
        self.assertEqual(cfg["thresholds"]["warn"], 55)
        # unspecified keys inside a *specified* table keep their defaults
        self.assertEqual(cfg["display"]["bar_set"], "blocks")
        self.assertEqual(cfg["display"]["gauges"], ["5h", "wk"])
        self.assertIs(cfg["display"]["show_model"], True)
        self.assertEqual(cfg["thresholds"]["crit"], 90)
        # untouched tables survive whole
        self.assertEqual(cfg["refresh"]["interval_secs"], 300)
        self.assertIs(cfg["providers"]["claude"]["enabled"], True)

    def test_unknown_keys_and_tables_are_preserved(self):
        p = self.write(
            "[display]\n"
            "mystery = 7\n"
            "\n"
            "[providers.mytool]\n"
            'command = ["mytool", "usage", "--json"]\n'
            'account_id = "$.account.id"\n'
        )
        cfg = uconfig.load(p)
        self.assertEqual(cfg["display"]["mystery"], 7)
        self.assertEqual(cfg["providers"]["mytool"]["command"],
                         ["mytool", "usage", "--json"])
        self.assertEqual(cfg["providers"]["mytool"]["account_id"], "$.account.id")
        # and the built-in providers are still there
        self.assertIn("codex", cfg["providers"])

    def test_interval_below_floor_is_clamped_up(self):
        p = self.write("[refresh]\ninterval_secs = 5\n")
        cfg = uconfig.load(p)
        self.assertEqual(cfg["refresh"]["interval_secs"], 60,
                         "interval_secs must clamp UP to min_interval_secs")

    def test_interval_clamp_uses_the_files_own_floor(self):
        p = self.write("[refresh]\ninterval_secs = 10\nmin_interval_secs = 5\n")
        cfg = uconfig.load(p)
        self.assertEqual(cfg["refresh"]["interval_secs"], 10)

        p2 = self.write("[refresh]\ninterval_secs = 30\nmin_interval_secs = 120\n",
                        name="c2.toml")
        cfg2 = uconfig.load(p2)
        self.assertEqual(cfg2["refresh"]["interval_secs"], 120)

    def test_interval_above_floor_untouched(self):
        p = self.write("[refresh]\ninterval_secs = 900\n")
        self.assertEqual(uconfig.load(p)["refresh"]["interval_secs"], 900)


MESSY_CONFIG = """\
# herdr-usage configuration
# Hand-edited on purpose: these comments MUST survive a surgical write.

[refresh]
interval_secs     = 300   # how often we probe
min_interval_secs = 60    # hard floor, do not lower

# ---- display -------------------------------------------------------------
[display]
bar_set   = "shade"    # blocks|shade|squares|dots|half|ascii
bar_cells = 10
gauges    = [ "5h", "wk" ]   # windows to render
show_model = true

# Extras are off by default; flip them in the settings popup.
[thresholds]
warn = 70
crit = 90          # red band
stale_multiplier = 3

[providers.codex]
enabled = true

[providers.claude]
enabled = true     # trailing comment on the last line
"""


@unittest.skipUnless(HAVE_CONFIG, NOT_YET)
class TestConfigSurgicalWriter(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = pathlib.Path(self.tmp.name) / "config.toml"
        self.path.write_text(MESSY_CONFIG, encoding="utf-8")

    def text(self):
        return self.path.read_text(encoding="utf-8")

    def parsed(self):
        with self.path.open("rb") as fh:
            return tomllib.load(fh)

    def test_write_preserves_every_comment(self):
        uconfig.set_value(self.path, "display.bar_cells", 14)
        out = self.text()
        for comment in (
            "# herdr-usage configuration",
            "# Hand-edited on purpose",
            "# how often we probe",
            "# hard floor, do not lower",
            "# ---- display ---",
            "# blocks|shade|squares|dots|half|ascii",
            "# windows to render",
            "# Extras are off by default",
            "# red band",
            "# trailing comment on the last line",
        ):
            self.assertIn(comment, out, f"comment lost: {comment!r}")

    def test_write_changes_only_the_target_key(self):
        before = self.parsed()
        uconfig.set_value(self.path, "display.bar_cells", 14)
        after = self.parsed()
        self.assertEqual(after["display"]["bar_cells"], 14)
        before["display"]["bar_cells"] = 14
        self.assertEqual(after, before, "an unrelated value changed")

    def test_write_preserves_table_order(self):
        uconfig.set_value(self.path, "thresholds.warn", 80)
        out = self.text()
        order = [out.index(h) for h in
                 ("[refresh]", "[display]", "[thresholds]",
                  "[providers.codex]", "[providers.claude]")]
        self.assertEqual(order, sorted(order), "table order changed")

    def test_write_does_not_duplicate_the_key_or_its_table(self):
        uconfig.set_value(self.path, "display.bar_set", "dots")
        out = self.text()
        self.assertEqual(out.count("[display]"), 1)
        self.assertEqual(len([ln for ln in out.splitlines()
                              if ln.strip().startswith("bar_set")]), 1)
        self.assertEqual(self.parsed()["display"]["bar_set"], "dots")

    def test_appending_a_new_key_lands_under_its_existing_table(self):
        uconfig.set_value(self.path, "display.show_plan", True)
        out = self.text()
        self.assertEqual(out.count("[display]"), 1,
                         "must append into the existing table, not re-open it")
        start = out.index("[display]")
        nxt = out.index("[thresholds]")
        self.assertIn("show_plan", out[start:nxt],
                      "new key must be written inside [display]")
        self.assertIs(self.parsed()["display"]["show_plan"], True)
        # untouched neighbours intact
        self.assertEqual(self.parsed()["display"]["bar_set"], "shade")

    def test_creating_a_missing_table(self):
        uconfig.set_value(self.path, "providers.mytool.enabled", False)
        cfg = self.parsed()
        self.assertIs(cfg["providers"]["mytool"]["enabled"], False)
        self.assertIs(cfg["providers"]["codex"]["enabled"], True)
        self.assertIn("# herdr-usage configuration", self.text())

    def test_output_always_reparses_with_tomllib(self):
        writes = [
            ("display.bar_set", "ascii"),
            ("display.bar_cells", 6),
            ("display.show_cost", True),
            ("thresholds.warn", 65),
            ("thresholds.crit", 95),
            ("refresh.interval_secs", 600),
            ("providers.claude.enabled", False),
        ]
        for key, value in writes:
            uconfig.set_value(self.path, key, value)
            with self.path.open("rb") as fh:
                doc = tomllib.load(fh)  # raises if the writer emitted junk
            table, _, leaf = key.rpartition(".")
            node = doc
            for part in table.split("."):
                node = node[part]
            self.assertEqual(node[leaf], value, f"{key} did not take")
        self.assertIn("# hard floor, do not lower", self.text())

    def test_string_values_are_quoted_not_bare(self):
        uconfig.set_value(self.path, "display.bar_set", "half")
        self.assertEqual(self.parsed()["display"]["bar_set"], "half")

    def test_repeated_writes_do_not_grow_the_file_unboundedly(self):
        uconfig.set_value(self.path, "display.bar_cells", 11)
        once = self.text()
        for _ in range(5):
            uconfig.set_value(self.path, "display.bar_cells", 11)
        self.assertEqual(self.text(), once, "rewriting the same value is not idempotent")

    def test_write_survives_reload_through_load(self):
        uconfig.set_value(self.path, "display.bar_cells", 8)
        self.assertEqual(uconfig.load(self.path)["display"]["bar_cells"], 8)


# --------------------------------------------------------------------------
# herdr_usage.cache -- T9
# --------------------------------------------------------------------------
@unittest.skipUnless(HAVE_CACHE, NOT_YET)
class TestCache(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = pathlib.Path(self.tmp.name)
        self.path = self.dir / "usage.json"

    @staticmethod
    def sample(pct, fetched_at):
        return {
            "codex": {
                "provider": "codex",
                "account_id": "acct-1",
                "fetched_at": fetched_at,
                "gauges": [{"key": "5h", "label": "5h", "pct": pct}],
            }
        }

    def test_missing_file_loads_as_empty_dict(self):
        self.assertEqual(ucache.load(self.dir / "nothing.json"), {})

    def test_round_trip(self):
        data = self.sample(12.5, 1000.0)
        ucache.save(self.path, data)
        got = ucache.load(self.path)
        self.assertEqual(got["codex"]["account_id"], "acct-1")
        self.assertEqual(got["codex"]["gauges"][0]["pct"], 12.5)

    def test_truncated_file_loads_as_empty_dict(self):
        self.path.write_text('{"codex": {"fetched_at": 1', encoding="utf-8")
        self.assertEqual(ucache.load(self.path), {})

    def test_binary_garbage_loads_as_empty_dict(self):
        self.path.write_bytes(b"\x00\xff\xfe not json at all")
        self.assertEqual(ucache.load(self.path), {})

    def test_empty_file_loads_as_empty_dict(self):
        self.path.write_bytes(b"")
        self.assertEqual(ucache.load(self.path), {})

    def test_non_dict_json_loads_as_empty_dict(self):
        self.path.write_text("[1, 2, 3]", encoding="utf-8")
        self.assertEqual(ucache.load(self.path), {})

    def test_save_is_atomic_via_temp_file_plus_os_replace(self):
        """The destination must never be opened for writing in place."""
        ucache.save(self.path, self.sample(10.0, 100.0))
        old_bytes = self.path.read_bytes()

        calls = []
        real_replace = os.replace

        def spy(src, dst, *a, **k):
            dst_p = pathlib.Path(dst)
            calls.append({
                "src": str(src),
                "dst": str(dst),
                "dst_before": dst_p.read_bytes() if dst_p.exists() else None,
            })
            return real_replace(src, dst, *a, **k)

        with mock.patch("os.replace", spy):
            ucache.save(self.path, self.sample(99.0, 200.0))

        self.assertTrue(calls, "save must publish via os.replace (atomic rename)")
        hit = [c for c in calls if os.path.abspath(c["dst"]) == str(self.path)]
        self.assertTrue(hit, f"no os.replace targeted {self.path}: {calls}")
        c = hit[-1]
        self.assertNotEqual(os.path.abspath(c["src"]), str(self.path),
                            "save wrote straight to the destination")
        self.assertEqual(os.path.dirname(os.path.abspath(c["src"])),
                         str(self.dir),
                         "temp file must live in the destination directory "
                         "so the rename is atomic on the same filesystem")
        self.assertEqual(c["dst_before"], old_bytes,
                         "destination was modified before the atomic replace -- "
                         "a partially written file was observable")
        self.assertEqual(ucache.load(self.path)["codex"]["gauges"][0]["pct"], 99.0)

    def test_failed_save_leaves_the_previous_file_intact(self):
        ucache.save(self.path, self.sample(10.0, 100.0))
        good = self.path.read_bytes()

        def boom(*a, **k):
            raise OSError("disk full")

        with mock.patch("os.replace", boom):
            with self.assertRaises(Exception):
                ucache.save(self.path, self.sample(77.0, 300.0))
        self.assertEqual(self.path.read_bytes(), good)
        self.assertEqual(ucache.load(self.path)["codex"]["gauges"][0]["pct"], 10.0)

    def test_save_creates_missing_parent_directory(self):
        nested = self.dir / "a" / "b" / "usage.json"
        ucache.save(nested, self.sample(1.0, 5.0))
        self.assertEqual(ucache.load(nested)["codex"]["gauges"][0]["pct"], 1.0)

    def test_stale_lock_older_than_60s_is_ignored(self):
        old = time.time() - 3600
        for lock in (pathlib.Path(str(self.path) + ".lock"),
                     self.path.with_suffix(".lock")):
            lock.write_text("stale", encoding="utf-8")
            os.utime(lock, (old, old))
        ucache.save(self.path, self.sample(42.0, 500.0))
        self.assertEqual(ucache.load(self.path)["codex"]["gauges"][0]["pct"], 42.0,
                         "a .lock older than 60s must not block the write")

    def test_is_fresh(self):
        now = 1_000_000.0
        self.assertTrue(ucache.is_fresh({"fetched_at": now - 10}, 60, now))
        self.assertTrue(ucache.is_fresh({"fetched_at": now}, 60, now))
        self.assertFalse(ucache.is_fresh({"fetched_at": now - 120}, 60, now))
        self.assertFalse(ucache.is_fresh({}, 60, now))
        self.assertFalse(ucache.is_fresh({"fetched_at": None}, 60, now))

    def test_is_fresh_defaults_now_to_wall_clock(self):
        self.assertTrue(ucache.is_fresh({"fetched_at": time.time()}, 60))
        self.assertFalse(ucache.is_fresh({"fetched_at": time.time() - 999}, 60))

    def test_previous_sample_is_retained_for_burn_rate(self):
        """After a second save the earlier pct must still be reachable."""
        ucache.save(self.path, self.sample(10.0, 100.0))
        ucache.save(self.path, self.sample(30.0, 400.0))
        got = ucache.load(self.path)

        self.assertEqual(got["codex"]["gauges"][0]["pct"], 30.0,
                         "the newest sample must be the current one")

        prev_paths = [(p, v) for p, k, v in _walk(got) if "prev" in k.lower()]
        self.assertTrue(prev_paths,
                        "no prev-* key anywhere in the cache: burn_eta has no "
                        f"prev_pct to work from. Got: {json.dumps(got)[:400]}")
        flat = {p: v for p, _k, v in _walk(got)}
        found_pct = any("prev" in "/".join(p).lower() and v == 10.0
                        for p, v in flat.items())
        self.assertTrue(found_pct,
                        "the previous pct (10.0) was not retained under a prev "
                        f"key. Got: {json.dumps(got)[:400]}")
        found_ts = any("prev" in "/".join(p).lower() and v == 100.0
                       for p, v in flat.items())
        self.assertTrue(found_ts,
                        "the previous fetched_at (100.0) was not retained; "
                        "burn_eta needs dt")

    def test_prev_tracks_the_newest_pair_not_the_first(self):
        ucache.save(self.path, self.sample(10.0, 100.0))
        ucache.save(self.path, self.sample(30.0, 400.0))
        ucache.save(self.path, self.sample(55.0, 700.0))
        flat = {"/".join(p).lower(): v for p, _k, v in _walk(ucache.load(self.path))}
        prevs = {k: v for k, v in flat.items() if "prev" in k}
        self.assertIn(30.0, prevs.values(),
                      f"prev must follow the last-but-one sample; got {prevs}")
        self.assertNotIn(10.0, prevs.values(),
                         f"prev is stuck on the first sample; got {prevs}")

    def test_first_ever_save_has_no_previous_pct(self):
        ucache.save(self.path, self.sample(10.0, 100.0))
        flat = {"/".join(p).lower(): v for p, _k, v in _walk(ucache.load(self.path))}
        for k, v in flat.items():
            if "prev" in k and "pct" in k:
                self.assertIsNone(v, "prev_pct must be None on the very first sample")


# --------------------------------------------------------------------------
# herdr_usage.sidebar -- T10
# --------------------------------------------------------------------------
BASE_CONFIG_FILE = """\
# Herdr configuration -- hand written, must survive us.
[general]
shell = "/usr/bin/zsh"

[ui]
theme = "mocha"

[keys]
prefix = "C-a"
"""


@unittest.skipUnless(HAVE_SIDEBAR, NOT_YET)
class TestSidebarGenerate(unittest.TestCase):
    def test_generate_returns_a_sidebar_agents_block(self):
        block = usidebar.generate(copy.deepcopy(SPEC_DEFAULTS))
        self.assertIsInstance(block, str)
        self.assertIn("[ui.sidebar.agents]", block)

    def test_generate_references_the_enabled_gauges(self):
        block = usidebar.generate(copy.deepcopy(SPEC_DEFAULTS))
        for base in ("uq_g5h", "uq_gwk"):
            for variant in ("ok", "warn", "crit", "none"):
                self.assertIn(f"{base}_{variant}", block,
                              f"missing token {base}_{variant}")

    def test_generate_emits_one_fg_per_severity_variant(self):
        block = usidebar.generate(copy.deepcopy(SPEC_DEFAULTS))
        for colour in ("#a6e3a1", "#f9e2af", "#f38ba8", "#6c7086"):
            self.assertIn(colour, block, f"severity colour {colour} missing")

    def test_generate_follows_the_extras_toggles(self):
        cfg = copy.deepcopy(SPEC_DEFAULTS)
        cfg["display"]["show_model"] = True
        cfg["display"]["show_plan"] = False
        on = usidebar.generate(cfg)
        self.assertIn("uq_model", on)
        self.assertNotIn("uq_plan", on)

        cfg["display"]["show_model"] = False
        cfg["display"]["show_plan"] = True
        off = usidebar.generate(cfg)
        self.assertIn("uq_plan", off)
        self.assertNotIn("uq_model", off)

    def test_generate_honours_the_gauge_selection(self):
        cfg = copy.deepcopy(SPEC_DEFAULTS)
        cfg["display"]["gauges"] = ["5h"]
        block = usidebar.generate(cfg)
        self.assertIn("uq_g5h_ok", block)
        self.assertNotIn("uq_gwk_ok", block)

    def test_generate_is_deterministic(self):
        cfg = copy.deepcopy(SPEC_DEFAULTS)
        self.assertEqual(usidebar.generate(cfg), usidebar.generate(copy.deepcopy(cfg)))

    def test_generate_does_not_mutate_the_config(self):
        cfg = copy.deepcopy(SPEC_DEFAULTS)
        before = copy.deepcopy(cfg)
        usidebar.generate(cfg)
        self.assertEqual(cfg, before)


class _SidebarSandbox(unittest.TestCase):
    """Sandboxed HOME/XDG so no marker or backup escapes into the real dotfiles."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = pathlib.Path(self.tmp.name)
        # keep every marker/backup the module may write inside the sandbox
        env = mock.patch.dict(os.environ, {
            "HOME": str(self.dir),
            "XDG_CONFIG_HOME": str(self.dir / "config"),
            "XDG_CACHE_HOME": str(self.dir / "cache"),
            "XDG_STATE_HOME": str(self.dir / "state"),
            "XDG_DATA_HOME": str(self.dir / "data"),
        })
        env.start()
        self.addCleanup(env.stop)
        self.path = self.dir / "config" / "herdr" / "config.toml"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(BASE_CONFIG_FILE, encoding="utf-8")
        self.original = self.path.read_bytes()
        self.block = "[ui.sidebar.agents]\nrows = [\"$uq_g5h_ok\"]\n"

    def body(self):
        return self.path.read_text(encoding="utf-8")


@unittest.skipUnless(HAVE_SIDEBAR, NOT_YET)
class TestSidebarInstall(_SidebarSandbox):
    def test_install_appends_the_marked_block(self):
        ok = usidebar.install(self.path, self.block, check=lambda *a, **k: True)
        self.assertIs(ok, True)
        out = self.body()
        self.assertEqual(out.count(usidebar.BEGIN), 1)
        self.assertEqual(out.count(usidebar.END), 1)
        self.assertLess(out.index(usidebar.BEGIN), out.index(usidebar.END))
        self.assertIn("uq_g5h_ok", out)
        # pre-existing content untouched
        self.assertIn("# Herdr configuration -- hand written, must survive us.", out)
        self.assertIn('prefix = "C-a"', out)

    def test_reinstall_replaces_in_place_and_never_duplicates(self):
        usidebar.install(self.path, self.block, check=lambda *a, **k: True)
        first = self.body()
        second_block = "[ui.sidebar.agents]\nrows = [\"$uq_gwk_crit\"]\n"
        ok = usidebar.install(self.path, second_block, check=lambda *a, **k: True)
        self.assertIs(ok, True)
        out = self.body()
        self.assertEqual(out.count(usidebar.BEGIN), 1, "block duplicated")
        self.assertEqual(out.count(usidebar.END), 1, "block duplicated")
        self.assertIn("uq_gwk_crit", out)
        self.assertNotIn("uq_g5h_ok", out, "old block content survived")
        self.assertEqual(out.count("[general]"), 1)
        self.assertNotEqual(out, first)
        # the block stays where it was: prefix before it is unchanged
        self.assertEqual(first.split(usidebar.BEGIN)[0],
                         out.split(usidebar.BEGIN)[0],
                         "content before the block shifted on re-install")

    def test_repeated_identical_install_is_idempotent(self):
        usidebar.install(self.path, self.block, check=lambda *a, **k: True)
        once = self.body()
        usidebar.install(self.path, self.block, check=lambda *a, **k: True)
        self.assertEqual(self.body(), once)

    def test_failed_check_restores_the_original_byte_identically(self):
        """THE DATA-LOSS GUARD."""
        ok = usidebar.install(self.path, self.block, check=lambda *a, **k: False)
        self.assertIs(ok, False, "install must report failure")
        self.assertEqual(self.path.read_bytes(), self.original,
                         "the user's config was not restored byte-identically")
        self.assertNotIn(usidebar.BEGIN, self.body())

    def test_failed_check_on_reinstall_keeps_the_previous_good_block(self):
        usidebar.install(self.path, self.block, check=lambda *a, **k: True)
        good = self.path.read_bytes()
        bad = "[ui.sidebar.agents\nrows = broken\n"
        ok = usidebar.install(self.path, bad, check=lambda *a, **k: False)
        self.assertIs(ok, False)
        self.assertEqual(self.path.read_bytes(), good,
                         "a failed re-install clobbered the working block")

    def test_check_is_run_against_a_copy_not_the_real_file(self):
        seen = []

        def check(path=None, *a, **k):
            seen.append(str(path) if path is not None else None)
            return True

        usidebar.install(self.path, self.block, check=check)
        self.assertTrue(seen, "check callable was never invoked")
        if seen[0] is not None:
            self.assertNotEqual(os.path.abspath(seen[0]), str(self.path),
                                "config check ran against the real file; the "
                                "spec requires a temp copy")

    def test_install_result_reparses_as_toml(self):
        usidebar.install(self.path, self.block, check=lambda *a, **k: True)
        with self.path.open("rb") as fh:
            doc = tomllib.load(fh)
        self.assertEqual(doc["general"]["shell"], "/usr/bin/zsh")
        self.assertEqual(doc["ui"]["theme"], "mocha")


@unittest.skipUnless(HAVE_SIDEBAR, NOT_YET)
class TestSidebarUndo(_SidebarSandbox):
    def test_undo_restores_surrounding_content_byte_identically(self):
        usidebar.install(self.path, self.block, check=lambda *a, **k: True)
        self.assertIn(usidebar.BEGIN, self.body())
        ok = usidebar.undo(self.path)
        self.assertIs(ok, True)
        self.assertEqual(self.path.read_bytes(), self.original,
                         "undo did not leave surrounding content byte-identical")

    def test_undo_removes_the_block_only(self):
        extra = BASE_CONFIG_FILE + '\n[ui.sidebar.tabs]\nrows = ["$name"]\n'
        self.path.write_text(extra, encoding="utf-8")
        baseline = self.path.read_bytes()
        usidebar.install(self.path, self.block, check=lambda *a, **k: True)
        usidebar.undo(self.path)
        self.assertEqual(self.path.read_bytes(), baseline)

    def test_undo_when_no_block_present_is_harmless(self):
        result = usidebar.undo(self.path)
        self.assertEqual(self.path.read_bytes(), self.original)
        self.assertIn(result, (True, False))  # either is defensible; no crash

    def test_undo_writes_an_uninstalled_marker(self):
        """So auto-install never fights a deliberate removal (SPEC section 7)."""
        usidebar.install(self.path, self.block, check=lambda *a, **k: True)
        before = {p for p in self.dir.rglob("*") if p.is_file()}
        usidebar.undo(self.path)
        after = {p for p in self.dir.rglob("*") if p.is_file()}
        new = after - before
        marker_named = any("uninstall" in p.name.lower() for p in after)
        api = [n for n in dir(usidebar) if "uninstall" in n.lower()]
        self.assertTrue(
            new or marker_named or api,
            "undo left no uninstalled marker: no new file under the sandboxed "
            "HOME/XDG dirs and no uninstall-related module attribute "
            f"(module attrs: {sorted(n for n in dir(usidebar) if not n.startswith('_'))})"
        )


# --------------------------------------------------------------------------
# herdr_usage.herdrctl -- T12
# --------------------------------------------------------------------------
class _Runner:
    """Records argv for whichever subprocess entry point the module uses."""

    def __init__(self, stdout="", returncode=0):
        self.stdout = stdout
        self.returncode = returncode
        self.calls = []

    # ---- fakes -----------------------------------------------------------
    def run(self, argv, *a, **k):
        self.calls.append(argv)
        if k.get("check") and self.returncode != 0:
            raise subprocess.CalledProcessError(self.returncode, argv, self.stdout)
        out = self.stdout
        if k.get("capture_output") or k.get("stdout") is not None:
            if k.get("text") or k.get("encoding") or k.get("universal_newlines"):
                pass
            else:
                out = self.stdout.encode()
        return subprocess.CompletedProcess(argv, self.returncode, out,
                                           "" if isinstance(out, str) else b"")

    def check_output(self, argv, *a, **k):
        self.calls.append(argv)
        if self.returncode != 0:
            raise subprocess.CalledProcessError(self.returncode, argv, self.stdout)
        if k.get("text") or k.get("encoding") or k.get("universal_newlines"):
            return self.stdout
        return self.stdout.encode()

    def check_call(self, argv, *a, **k):
        self.calls.append(argv)
        if self.returncode != 0:
            raise subprocess.CalledProcessError(self.returncode, argv)
        return 0

    def popen(self, argv, *a, **k):
        self.calls.append(argv)
        outer = self

        class _P:
            returncode = outer.returncode
            stdin = None
            stdout = None

            def communicate(self, *_a, **_k):
                if k.get("text") or k.get("encoding") or k.get("universal_newlines"):
                    return outer.stdout, ""
                return outer.stdout.encode(), b""

            def wait(self, *_a, **_k):
                return outer.returncode

            def __enter__(self):
                return self

            def __exit__(self, *_a):
                return False

        return _P()

    # ---- assertions ------------------------------------------------------
    def argv(self):
        assert self.calls, "no subprocess call was made"
        raw = self.calls[-1]
        if isinstance(raw, (list, tuple)):
            return [str(x) for x in raw]
        return shlex.split(str(raw))

    def patches(self):
        return (
            mock.patch("subprocess.run", self.run),
            mock.patch("subprocess.check_output", self.check_output),
            mock.patch("subprocess.check_call", self.check_call),
            mock.patch("subprocess.Popen", self.popen),
        )


def _run_with(runner, fn, *a, **k):
    ps = runner.patches()
    for p in ps:
        p.start()
    try:
        return fn(*a, **k)
    finally:
        for p in ps:
            p.stop()


PANE_PAYLOAD = [
    {"id": "%1", "pane_id": "%1", "agent": "codex", "cwd": "/w/a", "title": "a"},
    {"id": "%2", "pane_id": "%2", "cwd": "/w/b", "title": "plain shell"},
    {"id": "%3", "pane_id": "%3", "agent": "claude", "cwd": "/w/c", "title": "c"},
]


@unittest.skipUnless(HAVE_HERDRCTL, NOT_YET)
class TestHerdrctlPaneList(unittest.TestCase):
    def _pane_list(self, payload_variants=None):
        """SPEC GAP: the JSON envelope of `herdr pane list` is unspecified;
        accept a bare list or a {"panes": [...]} wrapper."""
        variants = payload_variants or [
            json.dumps(PANE_PAYLOAD),
            json.dumps({"panes": PANE_PAYLOAD}),
        ]
        last = None
        for payload in variants:
            runner = _Runner(stdout=payload)
            try:
                got = _run_with(runner, uherdrctl.pane_list)
            except Exception as exc:  # noqa: BLE001 - trying the other envelope
                last = exc
                continue
            if got:
                return got, runner
            last = got
        raise AssertionError(
            "pane_list returned nothing for either a bare-list or a "
            f'{{"panes": [...]}} payload (last: {last!r})'
        )

    def test_only_panes_carrying_an_agent_are_returned(self):
        panes, _runner = self._pane_list()
        self.assertEqual(len(panes), 2, f"agentless pane not filtered: {panes}")
        agents = sorted(p.get("agent") for p in panes)
        self.assertEqual(agents, ["claude", "codex"])
        self.assertTrue(all("agent" in p for p in panes))

    def test_pane_fields_are_preserved(self):
        panes, _ = self._pane_list()
        codex = [p for p in panes if p["agent"] == "codex"][0]
        self.assertEqual(codex["cwd"], "/w/a")

    def test_invokes_herdr_pane_list(self):
        _panes, runner = self._pane_list()
        argv = runner.argv()
        self.assertIn("pane", argv)
        self.assertIn("list", argv)
        self.assertTrue(any("herdr" in part for part in argv),
                        f"argv does not invoke herdr: {argv}")

    def test_empty_pane_list(self):
        for payload in ("[]", '{"panes": []}'):
            runner = _Runner(stdout=payload)
            self.assertEqual(list(_run_with(runner, uherdrctl.pane_list)), [])

    def test_unparseable_output_does_not_return_garbage(self):
        runner = _Runner(stdout="not json")
        try:
            got = _run_with(runner, uherdrctl.pane_list)
        except Exception:
            return  # raising is acceptable
        self.assertEqual(list(got), [], "garbage output must not yield panes")


@unittest.skipUnless(HAVE_HERDRCTL, NOT_YET)
class TestHerdrctlReportMetadata(unittest.TestCase):
    TOKENS = {
        "uq_model": "Opus",
        "uq_g5h_ok": "5h ████░░░░░░  31%",
        "uq_gwk_crit": "wk █████████░  92%",
    }
    CLEAR = ["uq_g5h_warn", "uq_g5h_crit", "uq_g5h_none", "uq_plan"]

    def _argv(self, returncode=0):
        runner = _Runner(stdout="", returncode=returncode)
        _run_with(runner, uherdrctl.report_metadata, "%7", "herdr-usage",
                  dict(self.TOKENS), list(self.CLEAR))
        return runner.argv()

    @staticmethod
    def _pairs(argv, flag):
        return [argv[i + 1] for i, a in enumerate(argv)
                if a == flag and i + 1 < len(argv)]

    def test_one_token_flag_per_token(self):
        argv = self._argv()
        self.assertEqual(argv.count("--token"), len(self.TOKENS),
                         f"expected one --token per token: {argv}")
        got = self._pairs(argv, "--token")
        self.assertEqual(sorted(got),
                         sorted(f"{k}={v}" for k, v in self.TOKENS.items()))

    def test_one_clear_token_flag_per_cleared_name(self):
        argv = self._argv()
        self.assertEqual(argv.count("--clear-token"), len(self.CLEAR),
                         f"expected one --clear-token per name: {argv}")
        self.assertEqual(sorted(self._pairs(argv, "--clear-token")),
                         sorted(self.CLEAR))

    def test_source_is_passed(self):
        argv = self._argv()
        self.assertIn("--source", argv)
        self.assertEqual(self._pairs(argv, "--source"), ["herdr-usage"])

    def test_pane_id_is_passed(self):
        self.assertIn("%7", self._argv())

    def test_command_is_pane_report_metadata(self):
        argv = self._argv()
        self.assertIn("pane", argv)
        self.assertTrue(any("report-metadata" in a or "report_metadata" in a
                            for a in argv), f"argv: {argv}")

    def test_values_are_passed_as_a_single_argv_element_not_shell_quoted(self):
        argv = self._argv()
        self.assertIn("uq_g5h_ok=5h ████░░░░░░  31%", argv,
                      "token value must ride in one argv element (no shell)")

    def test_empty_token_and_clear_sets_are_handled(self):
        runner = _Runner()
        _run_with(runner, uherdrctl.report_metadata, "%7", "herdr-usage", {}, [])
        if runner.calls:
            argv = runner.argv()
            self.assertNotIn("--token", argv)
            self.assertNotIn("--clear-token", argv)

    def test_non_zero_exit_raises(self):
        runner = _Runner(stdout="boom", returncode=2)
        with self.assertRaises(Exception,
                               msg="a non-zero exit must never be swallowed"):
            _run_with(runner, uherdrctl.report_metadata, "%7", "herdr-usage",
                      {"uq_model": "Opus"}, [])


@unittest.skipUnless(HAVE_HERDRCTL, NOT_YET)
class TestHerdrctlConfigCheck(unittest.TestCase):
    def test_ok_exit_is_true(self):
        runner = _Runner(stdout="config: ok", returncode=0)
        self.assertIs(_run_with(runner, uherdrctl.config_check), True)

    def test_failing_exit_is_false_not_an_exception(self):
        runner = _Runner(stdout="config: error at line 3", returncode=1)
        try:
            got = _run_with(runner, uherdrctl.config_check)
        except Exception as exc:  # noqa: BLE001
            self.fail(f"config_check must report False, not raise: {exc!r}")
        self.assertIs(got, False)

    def test_explicit_path_is_passed_to_the_checker(self):
        # AMENDED after writing: this test originally required the candidate
        # path to appear in argv. Herdr 0.9.1 has no --config flag
        # (`herdr --config X config check` -> "unknown option: --config",
        # exit 2); HERDR_CONFIG_PATH in the environment is the only supported
        # way to validate a candidate file. Asserting the old way would have
        # forced an implementation that cannot work against the real binary.
        with tempfile.TemporaryDirectory() as td:
            p = pathlib.Path(td) / "candidate.toml"
            p.write_text("[ui]\n", encoding="utf-8")
            seen = {}

            def run(argv, *a, **k):
                seen["argv"] = [str(x) for x in argv]
                seen["env"] = k.get("env")
                return subprocess.CompletedProcess(argv, 0, "config: ok", "")

            with mock.patch("subprocess.run", run):
                uherdrctl.config_check(p)

            joined = " ".join(seen["argv"])
            self.assertIn("config", joined)
            self.assertIn("check", joined)
            env = seen["env"] or {}
            self.assertEqual(
                env.get("HERDR_CONFIG_PATH"), str(p),
                "candidate path not handed to the checker via HERDR_CONFIG_PATH: "
                f"argv={seen['argv']}"
            )


# --------------------------------------------------------------------------
# herdr_usage.main.refresh -- T13
# --------------------------------------------------------------------------
class _CountingProbe:
    """A probe that records how often it ran. This is the CPU guarantee."""

    def __init__(self, provider, account_id="acct", pct=25.0, fail=False):
        self.provider = provider
        self.account_id = account_id
        self.pct = pct
        self.fail = fail
        self.calls = 0

    def __call__(self, *a, **k):
        self.calls += 1
        if self.fail:
            raise RuntimeError(f"{self.provider} probe exploded")
        return self.result()

    def result(self):
        if umodel is not None:
            g = umodel.Gauge(key="5h", label="5h", pct=self.pct)
            return umodel.AccountUsage(provider=self.provider,
                                       account_id=self.account_id,
                                       plan="team", gauges=[g],
                                       fetched_at=NOW)
        return {"provider": self.provider, "account_id": self.account_id,
                "plan": "team", "fetched_at": NOW,
                "gauges": [{"key": "5h", "label": "5h", "pct": self.pct}]}


NOW = 1_700_000_000.0

PANES = [
    {"id": "%1", "pane_id": "%1", "agent": "codex", "cwd": "/w/a"},
    {"id": "%2", "pane_id": "%2", "agent": "codex", "cwd": "/w/b"},
    {"id": "%3", "pane_id": "%3", "agent": "codex", "cwd": "/w/c"},
    {"id": "%4", "pane_id": "%4", "agent": "claude", "cwd": "/w/d"},
    {"id": "%5", "pane_id": "%5", "agent": "claude", "cwd": "/w/e"},
]


def _cache_at(ts):
    """Cache dict fresh/stale at `ts`; carries fetched_at both at top level and
    per provider so either reading of `is_fresh` sees the same answer."""
    entry = lambda name: {          # noqa: E731
        "provider": name, "account_id": f"{name}-acct", "fetched_at": ts,
        "plan": "team",
        "gauges": [{"key": "5h", "label": "5h", "pct": 20.0, "prev_pct": 10.0,
                    "prev_fetched_at": ts - 600}],
    }
    return {"fetched_at": ts, "codex": entry("codex"), "claude": entry("claude")}


FORCE_NAMES = ("startup", "force", "forced", "force_probe", "always", "ignore_ttl")


@unittest.skipUnless(HAVE_REFRESH, NOT_YET)
class TestMainRefresh(unittest.TestCase):
    def setUp(self):
        self.cfg = copy.deepcopy(SPEC_DEFAULTS)
        self.codex = _CountingProbe("codex", "codex-acct")
        self.claude = _CountingProbe("claude", "claude-acct")
        self.probes = {"codex": self.codex, "claude": self.claude}
        self.reported = []

    def call(self, cache_data, **kw):
        """SPEC GAP: refresh may report through herdrctl or return per-pane
        results; capture both."""
        target = getattr(umain, "herdrctl", None)
        patcher = None
        if target is not None and hasattr(target, "report_metadata"):
            patcher = mock.patch.object(
                target, "report_metadata",
                side_effect=lambda *a, **k: self.reported.append((a, k)))
            patcher.start()
        try:
            return umain.refresh(self.cfg, cache_data, list(PANES),
                                 dict(self.probes), NOW, **kw)
        finally:
            if patcher is not None:
                patcher.stop()

    def _covered_pane_ids(self, result):
        ids = set()
        blob = json.dumps(result, default=str) if result is not None else ""
        for pane in PANES:
            if pane["id"] in blob:
                ids.add(pane["id"])
        for args, kwargs in self.reported:
            for v in list(args) + list(kwargs.values()):
                if isinstance(v, str) and v in {p["id"] for p in PANES}:
                    ids.add(v)
        return ids

    # -- the CPU guarantee -------------------------------------------------
    def test_fresh_cache_performs_zero_probes(self):
        self.call(_cache_at(NOW - 10))
        self.assertEqual(self.codex.calls, 0,
                         "a fresh cache must never spawn a probe")
        self.assertEqual(self.claude.calls, 0,
                         "a fresh cache must never spawn a probe")

    def test_fresh_cache_still_produces_output_for_every_pane(self):
        result = self.call(_cache_at(NOW - 10))
        covered = self._covered_pane_ids(result)
        self.assertEqual(covered, {p["id"] for p in PANES},
                         "a cache hit must still render every agent pane; "
                         f"covered {sorted(covered)}")

    def test_stale_cache_probes_once_per_account_not_once_per_pane(self):
        self.call(_cache_at(NOW - 10_000))
        self.assertEqual(self.codex.calls, 1,
                         f"3 codex panes caused {self.codex.calls} probes; "
                         "quota is account-level, probe once and fan out")
        self.assertEqual(self.claude.calls, 1,
                         f"2 claude panes caused {self.claude.calls} probes")

    def test_stale_cache_fans_the_single_probe_out_to_every_pane(self):
        result = self.call(_cache_at(NOW - 10_000))
        covered = self._covered_pane_ids(result)
        self.assertEqual(covered, {p["id"] for p in PANES},
                         f"probe result not fanned out; covered {sorted(covered)}")

    def test_empty_cache_probes(self):
        self.call({})
        self.assertEqual(self.codex.calls, 1)
        self.assertEqual(self.claude.calls, 1)

    def test_ttl_comes_from_the_configured_interval(self):
        self.cfg["refresh"]["interval_secs"] = 300
        self.call(_cache_at(NOW - 299))
        self.assertEqual(self.codex.calls, 0, "cache inside the interval probed")

        self.codex.calls = self.claude.calls = 0
        self.call(_cache_at(NOW - 301))
        self.assertEqual(self.codex.calls, 1, "cache past the interval did not probe")

    # -- isolation ---------------------------------------------------------
    def test_one_failing_provider_does_not_stop_the_other(self):
        self.codex.fail = True
        try:
            result = self.call(_cache_at(NOW - 10_000))
        except Exception as exc:  # noqa: BLE001
            self.fail(f"a failing probe must not propagate: {exc!r}")
        self.assertEqual(self.claude.calls, 1,
                         "the healthy provider was never probed")
        covered = self._covered_pane_ids(result)
        self.assertTrue({"%4", "%5"} <= covered,
                        f"claude panes not rendered; covered {sorted(covered)}")

    def test_a_failing_probe_is_not_retried_per_pane(self):
        self.codex.fail = True
        self.call(_cache_at(NOW - 10_000))
        self.assertEqual(self.codex.calls, 1,
                         f"failing probe retried {self.codex.calls}x -- once "
                         "per pane defeats the account-level rule")

    def test_disabled_provider_is_not_probed(self):
        self.cfg["providers"]["codex"]["enabled"] = False
        self.call(_cache_at(NOW - 10_000))
        self.assertEqual(self.codex.calls, 0, "disabled provider was probed")
        self.assertEqual(self.claude.calls, 1)

    def test_no_panes_means_no_probes(self):
        target = getattr(umain, "herdrctl", None)
        if target is not None and hasattr(target, "report_metadata"):
            with mock.patch.object(target, "report_metadata"):
                umain.refresh(self.cfg, {}, [], dict(self.probes), NOW)
        else:
            umain.refresh(self.cfg, {}, [], dict(self.probes), NOW)
        self.assertEqual(self.codex.calls, 0)
        self.assertEqual(self.claude.calls, 0)

    def test_provider_with_no_pane_is_not_probed(self):
        panes = [p for p in PANES if p["agent"] == "claude"]
        target = getattr(umain, "herdrctl", None)
        if target is not None and hasattr(target, "report_metadata"):
            with mock.patch.object(target, "report_metadata"):
                umain.refresh(self.cfg, {}, panes, dict(self.probes), NOW)
        else:
            umain.refresh(self.cfg, {}, panes, dict(self.probes), NOW)
        self.assertEqual(self.codex.calls, 0,
                         "probed a provider that has no pane on screen")
        self.assertEqual(self.claude.calls, 1)

    def test_refresh_does_not_mutate_the_config(self):
        before = copy.deepcopy(self.cfg)
        self.call(_cache_at(NOW - 10_000))
        self.assertEqual(self.cfg, before)

    # -- startup mode ------------------------------------------------------
    def test_startup_always_probes_even_with_a_fresh_cache(self):
        import inspect
        params = inspect.signature(umain.refresh).parameters
        name = next((n for n in FORCE_NAMES if n in params), None)
        self.assertIsNotNone(
            name,
            "SPEC GAP: refresh() exposes no startup/force parameter, so the "
            "'[[startup]] always probes' rule (SPEC section 6) cannot be "
            f"expressed. Accepted names: {FORCE_NAMES}; signature: {list(params)}"
        )
        self.call(_cache_at(NOW - 1), **{name: True})
        self.assertEqual(self.codex.calls, 1,
                         "startup must probe regardless of TTL "
                         "(a server restart wipes all pane tokens)")
        self.assertEqual(self.claude.calls, 1)


# --------------------------------------------------------------------------
# herdr_usage.settings_tui.reduce -- T14
# --------------------------------------------------------------------------
# Compatibility note: `reduce(state, key)` does not prescribe the state layout
# or key encoding. These tests therefore (a) prefer a factory the module exposes,
# (b) fall back to a documented minimal shape, and (c) try several spellings of
# each key, requiring only that one works.
DOWN_KEYS = ("j", "KEY_DOWN", "\x1b[B", 258)
UP_KEYS = ("k", "KEY_UP", "\x1b[A", 259)
TOGGLE_KEYS = (" ", "\n", "\r", "KEY_ENTER", "t", 10, 32)
INC_KEYS = ("l", "+", "KEY_RIGHT", "\x1b[C", 261)
DEC_KEYS = ("h", "-", "KEY_LEFT", "\x1b[D", 260)
SAVE_KEYS = ("s", "S", "w", "KEY_F(2)")
CANCEL_KEYS = ("q", "\x1b", "Q", 27)


def _make_state():
    for factory in ("initial_state", "new_state", "make_state", "init_state"):
        fn = getattr(utui, factory, None)
        if callable(fn):
            try:
                return fn(copy.deepcopy(SPEC_DEFAULTS))
            except TypeError:
                return fn()
    return {"cfg": copy.deepcopy(SPEC_DEFAULTS), "cursor": 0, "done": None}


def _try_keys(state, keys):
    """Return (key, new_state) for the first key that changes the state."""
    for k in keys:
        try:
            new = utui.reduce(copy.deepcopy(state), k)
        except Exception:  # noqa: BLE001 - unknown key encoding
            continue
        if isinstance(new, dict) and new != state:
            return k, new
    return None, None


@unittest.skipUnless(HAVE_TUI, NOT_YET)
class TestSettingsReducer(unittest.TestCase):
    def setUp(self):
        self.state = _make_state()
        self.assertIsInstance(self.state, dict)

    # -- purity ------------------------------------------------------------
    def test_reduce_never_mutates_the_input_state(self):
        keys = DOWN_KEYS + UP_KEYS + TOGGLE_KEYS + INC_KEYS + DEC_KEYS + \
            SAVE_KEYS + CANCEL_KEYS + ("z", "?", 0)
        state = self.state
        for k in keys:
            snapshot = copy.deepcopy(state)
            try:
                utui.reduce(state, k)
            except Exception:  # noqa: BLE001 - unknown keys may be rejected
                continue
            self.assertEqual(state, snapshot,
                             f"reduce mutated its input state on key {k!r}")

    def test_reduce_returns_a_new_dict(self):
        key, new = _try_keys(self.state, DOWN_KEYS)
        self.assertIsNotNone(key, "no down-key moved the state")
        self.assertIsInstance(new, dict)
        self.assertIsNot(new, self.state)

    def test_unknown_key_is_a_no_op(self):
        for junk in ("\x07", "~", 9999):
            try:
                new = utui.reduce(self.state, junk)
            except Exception as exc:  # noqa: BLE001
                self.fail(f"reduce raised on an unhandled key {junk!r}: {exc!r}")
            self.assertEqual(new, self.state,
                             f"unhandled key {junk!r} changed the state")

    # -- navigation --------------------------------------------------------
    def test_navigation_down_wraps_back_to_the_top(self):
        key, _ = _try_keys(self.state, DOWN_KEYS)
        self.assertIsNotNone(key, f"none of {DOWN_KEYS} moved the cursor")
        seen = [_cursor(self.state)]
        state = self.state
        for _ in range(200):
            state = utui.reduce(state, key)
            c = _cursor(state)
            if c == seen[0]:
                break
            seen.append(c)
        else:
            self.fail("cursor never wrapped back to the first row in 200 presses")
        self.assertGreater(len(seen), 1, "there is only one navigable row")
        self.assertEqual(len(set(seen)), len(seen), "cursor revisited a row early")

    def test_navigation_up_from_the_top_wraps_to_the_bottom(self):
        down, _ = _try_keys(self.state, DOWN_KEYS)
        up, _ = _try_keys(self.state, UP_KEYS)
        self.assertIsNotNone(up, f"none of {UP_KEYS} moved the cursor")
        rows, state = 0, self.state
        for _ in range(200):
            state = utui.reduce(state, down)
            rows += 1
            if _cursor(state) == _cursor(self.state):
                break
        wrapped = utui.reduce(self.state, up)
        self.assertNotEqual(_cursor(wrapped), _cursor(self.state))
        self.assertEqual(_cursor(wrapped), rows - 1,
                         "up from the first row must land on the last row")

    def test_down_then_up_returns_to_the_same_row(self):
        down, _ = _try_keys(self.state, DOWN_KEYS)
        up, _ = _try_keys(self.state, UP_KEYS)
        moved = utui.reduce(self.state, down)
        back = utui.reduce(moved, up)
        self.assertEqual(_cursor(back), _cursor(self.state))

    # -- toggles -----------------------------------------------------------
    def _rows(self):
        down, _ = _try_keys(self.state, DOWN_KEYS)
        self.assertIsNotNone(down, "cannot navigate")
        state, out = self.state, [self.state]
        for _ in range(60):
            state = utui.reduce(state, down)
            if _cursor(state) == _cursor(self.state):
                break
            out.append(state)
        return out

    def test_a_toggle_row_flips_exactly_one_boolean(self):
        flips = []
        for row in self._rows():
            before = _bool_leaves(row)
            for k in TOGGLE_KEYS:
                try:
                    new = utui.reduce(row, k)
                except Exception:  # noqa: BLE001
                    continue
                after = _bool_leaves(new)
                changed = [p for p in before if p in after and after[p] != before[p]]
                if changed:
                    flips.append((_cursor(row), changed, before[changed[0]],
                                  after[changed[0]]))
                    self.assertEqual(len(changed), 1,
                                     f"one keypress flipped {changed}")
                    self.assertIs(after[changed[0]], not before[changed[0]])
                    break
        self.assertTrue(flips, "no row toggles a boolean setting")
        self.assertGreaterEqual(len(flips), 2,
                                "SPEC section 8 says every extra is toggleable; "
                                f"only {len(flips)} toggle row(s) found")

    def test_toggling_twice_restores_the_original_value(self):
        for row in self._rows():
            before = _bool_leaves(row)
            for k in TOGGLE_KEYS:
                try:
                    once = utui.reduce(row, k)
                except Exception:  # noqa: BLE001
                    continue
                if _bool_leaves(once) != before:
                    twice = utui.reduce(once, k)
                    self.assertEqual(_bool_leaves(twice), before,
                                     "toggle is not an involution")
                    return
        self.fail("no toggle row found")

    # -- bar set cycling ---------------------------------------------------
    def test_bar_set_cycles_through_all_six_sets_and_wraps(self):
        start = _bar_set(self.state)
        self.assertIsNotNone(start, "state carries no bar_set")
        for row in self._rows():
            for k in INC_KEYS + TOGGLE_KEYS:
                try:
                    probe = utui.reduce(row, k)
                except Exception:  # noqa: BLE001
                    continue
                if _bar_set(probe) not in (None, _bar_set(row)):
                    seen, state = [_bar_set(row)], row
                    for _ in range(24):
                        state = utui.reduce(state, k)
                        v = _bar_set(state)
                        if v == seen[0]:
                            break
                        seen.append(v)
                    else:
                        self.fail(f"bar-set cycle never wrapped: {seen}")
                    self.assertEqual(set(seen), BAR_SET_NAMES,
                                     f"cycle must visit all six sets, saw {seen}")
                    return
        self.fail("no row cycles display.bar_set")

    # -- numeric bounds ----------------------------------------------------
    def test_numeric_edits_clamp_and_never_go_below_zero(self):
        dec, _ = _try_keys(self.state, DEC_KEYS)
        inc, _ = _try_keys(self.state, INC_KEYS)
        self.assertIsNotNone(dec, f"none of {DEC_KEYS} edits a number")
        touched = False
        for row in self._rows():
            baseline = _number_leaves(row)
            state = row
            for _ in range(40):
                state = utui.reduce(state, dec)
            low = _number_leaves(state)
            changed = [p for p in baseline if p in low and low[p] != baseline[p]]
            if not changed:
                continue
            touched = True
            for p in changed:
                self.assertGreaterEqual(low[p], 0,
                                        f"{'/'.join(p)} went negative: {low[p]}")
            state = row
            for _ in range(400):
                state = utui.reduce(state, inc)
            high = _number_leaves(state)
            for p in changed:
                name = "/".join(p)
                if "warn" in name or "crit" in name:
                    self.assertLessEqual(high[p], 100,
                                         f"{name} exceeded 100: {high[p]}")
                if "bar_cells" in name:
                    self.assertLessEqual(high[p], 80, f"{name} unbounded: {high[p]}")
                    self.assertGreaterEqual(low[p], 1,
                                            f"{name} fell below 1 cell: {low[p]}")
        self.assertTrue(touched, "no row edits a numeric setting")

    def test_interval_never_drops_below_the_configured_floor(self):
        dec, _ = _try_keys(self.state, DEC_KEYS)
        self.assertIsNotNone(dec)
        floor = SPEC_DEFAULTS["refresh"]["min_interval_secs"]
        for row in self._rows():
            state = row
            for _ in range(200):
                state = utui.reduce(state, dec)
            for p, v in _number_leaves(state).items():
                if "interval_secs" in "/".join(p) and "min_" not in "/".join(p):
                    self.assertGreaterEqual(
                        v, floor,
                        f"interval_secs fell to {v}, below the {floor}s floor")

    # -- save vs cancel ----------------------------------------------------
    def _terminal(self, state):
        return {"/".join(p): v for p, k, v in _walk(state)
                if k.lower() in {"done", "exit", "quit", "action", "result",
                                 "outcome", "finished", "status", "mode"}}

    def test_save_and_cancel_produce_distinguishable_outcomes(self):
        save_key, saved = _try_keys(self.state, SAVE_KEYS)
        cancel_key, cancelled = _try_keys(self.state, CANCEL_KEYS)
        self.assertIsNotNone(save_key, f"none of {SAVE_KEYS} saves")
        self.assertIsNotNone(cancel_key, f"none of {CANCEL_KEYS} cancels")
        self.assertNotEqual(saved, cancelled,
                            "save and cancel produced identical states")
        self.assertNotEqual(self._terminal(saved), self._terminal(self.state),
                            "save set no terminal marker")
        self.assertNotEqual(self._terminal(cancelled), self._terminal(self.state),
                            "cancel set no terminal marker")
        self.assertNotEqual(self._terminal(saved), self._terminal(cancelled),
                            "save and cancel are indistinguishable to the caller")

    def test_cancel_discards_pending_edits(self):
        """Cancel must leave the config untouched (SPEC section 8)."""
        toggled = None
        for row in self._rows():
            before = _bool_leaves(row)
            for k in TOGGLE_KEYS:
                try:
                    new = utui.reduce(row, k)
                except Exception:  # noqa: BLE001
                    continue
                if _bool_leaves(new) != before:
                    toggled = (row, new, before)
                    break
            if toggled:
                break
        self.assertIsNotNone(toggled, "no toggle row found")
        _row, edited, before = toggled
        cancel_key, _ = _try_keys(self.state, CANCEL_KEYS)
        cancelled = utui.reduce(edited, cancel_key)
        marker = self._terminal(cancelled)
        self.assertTrue(
            any(str(v).lower() in {"cancel", "cancelled", "canceled", "quit",
                                   "abort", "aborted"} for v in marker.values()),
            f"cancel must be identifiable as a discard, got {marker}")

    def test_save_marks_the_state_as_saved(self):
        save_key, saved = _try_keys(self.state, SAVE_KEYS)
        marker = self._terminal(saved)
        self.assertTrue(
            any(str(v).lower() in {"save", "saved", "commit", "write", "apply",
                                   "applied"} for v in marker.values()),
            f"save must be identifiable as a commit, got {marker}")

    def test_reduce_after_a_terminal_key_is_stable(self):
        save_key, saved = _try_keys(self.state, SAVE_KEYS)
        again = utui.reduce(saved, save_key)
        self.assertEqual(self._terminal(again), self._terminal(saved))


if __name__ == "__main__":
    unittest.main(verbosity=2)
