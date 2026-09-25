"""Hermes wrapper-agent support.

Hermes is not a provider: each session routes to an account of its own
choosing, so a hermes pane's quota depends on that session's billing_provider,
never on the agent name. Getting this wrong would attribute one account's
usage to a pane billed somewhere else entirely.
"""

import pathlib
import sqlite3
import tempfile
import unittest

from herdr_usage.main import _provider_of
from herdr_usage.providers import hermes_session

SCHEMA = """
CREATE TABLE sessions (
  id TEXT PRIMARY KEY, model TEXT, billing_provider TEXT,
  billing_base_url TEXT, billing_mode TEXT
);
"""

ROWS = [
    # (id, model, billing_provider, billing_base_url, billing_mode)
    ("s_anthropic", "claude-opus-5", "anthropic",
     "https://api.anthropic.com", None),
    ("s_codex", "gpt-5.6-terra", "openai-codex",
     "https://chatgpt.com/backend-api/codex", "subscription_included"),
    ("s_codex_slash", "gpt-5.6-terra", "openai-codex",
     "https://chatgpt.com/backend-api/codex/", "subscription_included"),
    ("s_local", "ggml-org/Qwen3.8-27B-GGUF:Q4_K_M", "my-llama-cpp",
     "http://localhost:9931/v1", "chat_completions"),
    ("s_custom", "ggml-org/Qwen3.8-27B-GGUF:Q4_K_M", "custom",
     "http://10.0.0.5:8080/v1", None),
    ("s_null", "claude-sonnet-5", None, None, None),
    ("s_proxy", "claude-opus-5", "anthropic",
     "http://my-proxy.internal/v1", None),
]


class TestHermesSession(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.db = pathlib.Path(cls.tmp.name) / "state.db"
        con = sqlite3.connect(cls.db)
        con.executescript(SCHEMA)
        con.executemany("INSERT INTO sessions VALUES (?,?,?,?,?)", ROWS)
        con.commit()
        con.close()

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def get(self, sid):
        return hermes_session(sid, str(self.db))

    def test_anthropic_session_maps_to_claude(self):
        info = self.get("s_anthropic")
        self.assertEqual(info["provider"], "claude")
        self.assertEqual(info["model"], "claude-opus-5")

    def test_codex_session_maps_to_codex(self):
        self.assertEqual(self.get("s_codex")["provider"], "codex")

    def test_trailing_slash_base_url_still_matches(self):
        self.assertEqual(self.get("s_codex_slash")["provider"], "codex")

    def test_local_endpoint_has_no_quota_provider(self):
        """A local model consumes no subscription -- show nothing, not a
        borrowed gauge from another account."""
        info = self.get("s_local")
        self.assertIsNone(info["provider"])
        self.assertEqual(info["model"], "ggml-org/Qwen3.8-27B-GGUF:Q4_K_M")

    def test_custom_endpoint_has_no_quota_provider(self):
        self.assertIsNone(self.get("s_custom")["provider"])

    def test_null_billing_provider_is_unknown(self):
        self.assertIsNone(self.get("s_null")["provider"])

    def test_unexpected_base_url_is_not_claimed(self):
        """anthropic billing through a proxy is not provably our account."""
        self.assertIsNone(self.get("s_proxy")["provider"])

    def test_missing_session_returns_none(self):
        self.assertIsNone(self.get("nope"))

    def test_empty_session_id_returns_none(self):
        self.assertIsNone(self.get(""))
        self.assertIsNone(self.get(None))

    def test_missing_db_returns_none_not_crash(self):
        self.assertIsNone(hermes_session("s_codex", "/nonexistent/state.db"))

    def test_db_is_opened_read_only(self):
        before = self.db.read_bytes()
        self.get("s_codex")
        self.assertEqual(self.db.read_bytes(), before)


class TestProviderOf(unittest.TestCase):
    def test_plain_agent_pane_uses_its_agent(self):
        self.assertEqual(_provider_of({"agent": "claude"}), "claude")
        self.assertEqual(_provider_of({"agent": "codex"}), "codex")

    def test_resolved_provider_wins_over_agent(self):
        pane = {"agent": "hermes", "provider": "claude"}
        self.assertEqual(_provider_of(pane), "claude")

    def test_explicit_none_provider_is_respected(self):
        """A resolved-but-unquotaed pane must not fall back to its agent
        name, or a local-model pane would render another account's gauge."""
        pane = {"agent": "hermes", "provider": None}
        self.assertIsNone(_provider_of(pane))

    def test_unresolved_pane_has_no_provider(self):
        self.assertIsNone(_provider_of({}))


if __name__ == "__main__":
    unittest.main()
