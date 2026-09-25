"""Settings popup: a pure keypress reducer plus a thin curses shell.

The reducer is separated from curses on purpose -- a Herdr popup has no pane
id, so an agent can never drive the real UI end to end. Everything that can
be tested lives in `reduce`; the curses layer is only paint and key capture.
"""

import contextlib
import copy
import io

from .render import BAR_SETS, ICON_SETS

# (label, dotted config key, kind, bounds)
ROWS = [
    ("Bar style", "display.bar_set", "choice", tuple(BAR_SETS)),
    ("Bar width", "display.bar_cells", "int", (1, 80)),
    ("Show model", "display.show_model", "bool", None),
    ("Show vendor icon", "display.show_icon", "bool", None),
    ("Icon style", "display.icon_set", "choice", tuple(ICON_SETS)),
    ("Show plan badge", "display.show_plan", "bool", None),
    ("Show burn-rate ETA", "display.show_burn", "bool", None),
    ("Show resets-in", "display.show_resets_in", "bool", None),
    ("Show reset credits", "display.show_credits", "bool", None),
    ("Show session cost", "display.show_cost", "bool", None),
    ("Show model-scoped gauges", "display.show_scoped", "bool", None),
    ("Warn threshold %", "thresholds.warn", "int", (0, 100)),
    ("Crit threshold %", "thresholds.crit", "int", (0, 100)),
    ("Refresh interval (s)", "refresh.interval_secs", "int", (60, 86400)),
    ("Codex enabled", "providers.codex.enabled", "bool", None),
    ("Claude enabled", "providers.claude.enabled", "bool", None),
]

DOWN = {"j", "KEY_DOWN", "\x1b[B", 258}
UP = {"k", "KEY_UP", "\x1b[A", 259}
TOGGLE = {" ", "\n", "\r", "KEY_ENTER", 10, 32}
INC = {"l", "+", "KEY_RIGHT", "\x1b[C", 261}
DEC = {"h", "-", "KEY_LEFT", "\x1b[D", 260}
SAVE = {"s", "S", "w", "KEY_F(2)"}
CANCEL = {"q", "Q", "\x1b", 27}


def initial_state(cfg: dict) -> dict:
    return {"cfg": copy.deepcopy(cfg), "cursor": 0, "done": None}


def _get(cfg, dotted):
    node = cfg
    for part in dotted.split("."):
        if not isinstance(node, dict) or part not in node:
            return None
        node = node[part]
    return node


def _set(cfg, dotted, value):
    parts = dotted.split(".")
    node = cfg
    for part in parts[:-1]:
        node = node.setdefault(part, {})
    node[parts[-1]] = value


def _bounds(cfg, dotted, low, high):
    """Thresholds and the refresh floor constrain each other, not just 0..100."""
    if dotted == "thresholds.warn":
        return low, min(high, _get(cfg, "thresholds.crit") or high)
    if dotted == "thresholds.crit":
        return max(low, _get(cfg, "thresholds.warn") or low), high
    if dotted == "refresh.interval_secs":
        return max(low, _get(cfg, "refresh.min_interval_secs") or low), high
    return low, high


def _step(state: dict, delta: int) -> dict:
    label, key, kind, bounds = ROWS[state["cursor"] % len(ROWS)]
    cfg = state["cfg"]
    if kind == "bool":
        _set(cfg, key, not bool(_get(cfg, key)))
    elif kind == "choice":
        options = list(bounds)
        current = _get(cfg, key)
        idx = options.index(current) if current in options else 0
        _set(cfg, key, options[(idx + (delta or 1)) % len(options)])
    else:
        low, high = _bounds(cfg, key, *bounds)
        value = _get(cfg, key)
        value = low if not isinstance(value, (int, float)) else value
        _set(cfg, key, max(low, min(high, int(value) + delta)))
    return state


def reduce(state: dict, key) -> dict:
    """Pure: never mutates `state`, always returns a new dict.

    An unrecognised key is a no-op rather than an error -- a terminal sends
    plenty of escape noise we have no business crashing on.
    """
    new = copy.deepcopy(state)
    new.setdefault("cursor", 0)
    new.setdefault("done", None)

    if key in DOWN:
        new["cursor"] = (new["cursor"] + 1) % len(ROWS)
    elif key in UP:
        new["cursor"] = (new["cursor"] - 1) % len(ROWS)
    elif key in TOGGLE:
        _step(new, 1)
    elif key in INC:
        _step(new, 1)
    elif key in DEC:
        _step(new, -1)
    elif key in SAVE:
        new["done"] = "save"
    elif key in CANCEL:
        new["done"] = "cancel"
    else:
        return state if new == state else new
    return new


def render_lines(state: dict, rows: int | None = None, note: str = "") -> list[str]:
    """Header, rows, then the control hint.

    When `rows` is given the row list scrolls to keep the cursor visible, so
    the hint line is never pushed off the bottom of a short popup -- losing it
    is what makes the controls undiscoverable.

    `note` is transient status ("saved") shown in the header, never appended
    below: a growing message area is what pushed the menu out of alignment.
    """
    head = ["herdr-usage settings" + (f"   [{note}]" if note else ""), ""]
    hint = ["", "j/k move  space/←→ change  s save  q cancel"]
    body = []
    for i, (label, key, kind, _b) in enumerate(ROWS):
        value = _get(state["cfg"], key)
        shown = ("on" if value else "off") if kind == "bool" else str(value)
        body.append(f"{'>' if i == state['cursor'] else ' '} {label:<26} {shown}")

    if rows is None or rows >= len(head) + len(body) + len(hint):
        return head + body + hint

    room = max(1, rows - len(head) - len(hint))
    start = min(max(0, state["cursor"] - room // 2), max(0, len(body) - room))
    window = body[start:start + room]
    if start:
        window[0] = "  ↑ more"
    if start + room < len(body):
        window[-1] = "  ↓ more"
    return head + window + hint


def run_plain(cfg: dict, stdin=None, stdout=None, on_save=None):  # pragma: no cover - I/O loop
    """Line-mode fallback for terminals without curses.

    Windows CPython ships no `_curses` module at all, so the popup would be
    unusable there. Same reducer, same keys, just redrawn as plain lines.
    """
    import sys as _sys

    stdin = stdin or _sys.stdin
    stdout = stdout or _sys.stdout
    state = initial_state(cfg)
    saved = None
    note = ""
    while True:
        stdout.write("\n".join(render_lines(state, note=note)) + "\n> ")
        stdout.flush()
        note = ""
        line = stdin.readline()
        if not line:  # EOF: treat as cancel, never as a silent save
            return saved
        for ch in line.strip() or "\n":
            state = reduce(state, ch)
            if state["done"] is not None:
                break
        if state["done"] == "cancel":
            return saved
        if state["done"] == "save":
            saved = copy.deepcopy(state["cfg"])
            if on_save is None:
                return saved
            # Keep on_save's output off this screen too, so the menu below
            # stays aligned.
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(buf):
                on_save(saved)
            note = "saved"
            state = dict(state, done=None)


def run(cfg: dict, on_save=None):  # pragma: no cover - needs a real terminal
    """Curses shell. Returns the edited config on save, None on cancel.

    With `on_save` the popup stays open: each save persists and refreshes in
    place, so the gauges update without losing your place in the list. The
    reducer still reports done="save" -- only this shell decides to continue.
    """
    try:
        import curses
    except ImportError:
        return run_plain(cfg, on_save=on_save)

    def _loop(scr):
        state = initial_state(cfg)
        saved = None
        note = ""
        curses.curs_set(0)
        while True:
            scr.erase()
            rows, cols = scr.getmaxyx()
            lines = render_lines(state, rows=rows, note=note)
            for i, line in enumerate(lines[:rows]):
                try:
                    scr.addstr(i, 0, line[: cols - 1])
                except curses.error:
                    pass
            scr.clrtobot()
            scr.refresh()
            try:
                key = scr.getkey()
            except curses.error:
                continue
            note = ""
            state = reduce(state, key)

            if state["done"] == "cancel":
                return saved
            if state["done"] == "save":
                saved = copy.deepcopy(state["cfg"])
                if on_save is None:
                    return saved
                # on_save writes files and shells out to `herdr`; anything it
                # prints would land on the raw terminal underneath curses and
                # desync the screen. Capture it, then repaint from scratch.
                buf = io.StringIO()
                with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(buf):
                    on_save(saved)
                note = "saved"
                state = dict(state, done=None)
                scr.clear()  # full repaint, not a diff against a stale model

    return curses.wrapper(_loop)
