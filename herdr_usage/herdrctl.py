"""Herdr CLI seam. Every subprocess call to `herdr` goes through here."""

import json
import os
import subprocess

SOURCE = "herdr-usage"

# Plugin processes do NOT inherit an interactive shell's PATH, so a bare
# "herdr" is not resolvable (verified: Errno 2 in the plugin log). Herdr
# injects the absolute binary path for exactly this reason.
HERDR = os.environ.get("HERDR_BIN_PATH") or "herdr"


def _run(args, check=True):
    return subprocess.run(
        [HERDR, *args], capture_output=True, text=True, check=check
    )


def pane_list() -> list[dict]:
    """Only panes running an agent. Unparseable output yields nothing."""
    out = _run(["pane", "list"]).stdout
    try:
        doc = json.loads(out)
    except ValueError:
        return []
    if isinstance(doc, dict):
        panes = (doc.get("result") or doc).get("panes", [])
    else:
        panes = doc
    if not isinstance(panes, list):
        return []
    return [p for p in panes if isinstance(p, dict) and p.get("agent")]


def report_metadata(pane_id: str, source: str, tokens: dict, clear: list) -> None:
    """One --token per token, one --clear-token per cleared name.

    Values ride as single argv elements: no shell, so block glyphs and spaces
    need no quoting.
    """
    args = ["pane", "report-metadata", pane_id, "--source", source]
    for name, value in tokens.items():
        args += ["--token", f"{name}={value}"]
    for name in clear:
        args += ["--clear-token", name]
    _run(args)


def config_check(config_path=None) -> bool:
    """False for an invalid config -- that's an expected answer, not a failure.

    Herdr has no --config flag (verified on 0.9.1: `unknown option: --config`);
    HERDR_CONFIG_PATH is the only way to validate a candidate file.
    """
    env = None
    if config_path is not None:
        env = dict(os.environ)
        env["HERDR_CONFIG_PATH"] = str(config_path)
    try:
        proc = subprocess.run(
            [HERDR, "config", "check"],
            capture_output=True,
            text=True,
            check=False,
            env=env,
        )
    except OSError:
        return False
    return proc.returncode == 0 and "issues" not in (proc.stdout or "").lower()
