"""Herdr CLI seam. Every subprocess call to `herdr` goes through here."""

import json
import os
import subprocess
import textwrap

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


def machines() -> list[dict]:
    """Saved SSH machines, as seen from THIS machine.

    Only meaningful in the client->server direction: a Herdr server cannot
    enumerate the clients viewing it, so this never proves a remote client
    exists -- it proves this machine has remote peers of its own.
    """
    try:
        proc = _run(["machine", "list", "--json"], check=False)
        doc = json.loads(proc.stdout or "[]")
    except (OSError, ValueError):
        return []
    if isinstance(doc, dict):
        doc = doc.get("result", doc)
        doc = doc.get("machines", []) if isinstance(doc, dict) else doc
    return [m for m in doc if isinstance(m, dict)] if isinstance(doc, list) else []


def client_width(default: int = 60) -> int:
    """Narrowest pane area the client is currently drawing.

    Herdr renders toasts on the client, and the server cannot ask it for a
    terminal size -- but the layout snapshot carries the real pane geometry,
    which is the same width budget a toast has to live in.
    """
    try:
        proc = _run(["api", "snapshot"], check=False)
        layouts = json.loads(proc.stdout)["result"]["snapshot"]["layouts"]
        widths = [
            int(l["area"]["width"]) for l in layouts
            if isinstance(l.get("area"), dict) and l["area"].get("width")
        ]
    except (OSError, ValueError, KeyError, TypeError):
        return default
    return min(widths) if widths else default


def wrap_body(text: str, width: int, max_lines: int = 4) -> str:
    """Wrap a toast body to the client's width.

    textwrap counts characters, which is right here: these messages are plain
    ASCII. Gauge strings, which do carry wide glyphs, never reach a toast.
    """
    width = max(20, int(width) - 6)  # toast padding/border
    lines = []
    for para in str(text).splitlines() or [""]:
        lines.extend(textwrap.wrap(para, width) or [""])
    if len(lines) > max_lines:
        lines = lines[:max_lines]
        lines[-1] = lines[-1][: width - 1].rstrip() + "…"
    return "\n".join(lines)


def notify(title: str, body: str = "", width: int | None = None) -> bool:
    args = ["notification", "show", title]
    if body:
        args += ["--body", wrap_body(body, client_width() if width is None else width)]
    try:
        return _run(args, check=False).returncode == 0
    except OSError:
        return False


def open_pane(plugin: str, entrypoint: str) -> bool:
    """Ask Herdr to open a plugin-owned pane.

    `herdr plugin pane open` takes --plugin/--entrypoint flags; the dotted
    `plugin.entrypoint` form is rejected as an unknown option.
    """
    proc = _run(
        ["plugin", "pane", "open", "--plugin", plugin, "--entrypoint", entrypoint],
        check=False,
    )
    return '"type":"ok"' in (proc.stdout or "").replace(" ", "")
