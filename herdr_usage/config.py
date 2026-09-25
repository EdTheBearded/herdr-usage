"""Plugin configuration: defaults, tolerant load, comment-preserving writer."""

import copy
import json
import re
import tomllib

DEFAULTS = {
    "refresh": {"interval_secs": 300, "min_interval_secs": 60},
    "display": {
        "bar_set": "blocks",
        "bar_cells": 10,
        "gauges": ["5h", "wk"],
        "show_model": True,
        "show_icon": True,
        "icon_set": "brand",
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


def _merge(base: dict, over: dict) -> dict:
    out = copy.deepcopy(base)
    for k, v in over.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _merge(out[k], v)
        else:
            out[k] = copy.deepcopy(v)
    return out


def load(path) -> dict:
    """Defaults, deep-merged under whatever the file specifies.

    A missing or unreadable file yields defaults; the caller always gets its
    own copy, so mutating a loaded config can't poison the next load.
    """
    try:
        with open(path, "rb") as fh:
            user = tomllib.load(fh)
    except (OSError, tomllib.TOMLDecodeError):
        user = {}
    cfg = _merge(DEFAULTS, user)

    floor = cfg["refresh"].get("min_interval_secs", 60)
    if cfg["refresh"].get("interval_secs", 0) < floor:
        cfg["refresh"]["interval_secs"] = floor
    return cfg


def _fmt(value) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return repr(value)
    if isinstance(value, (list, tuple)):
        return "[" + ", ".join(_fmt(v) for v in value) + "]"
    return json.dumps(str(value))


_HEADER = re.compile(r"^\s*\[\s*([^\]]+?)\s*\]\s*$")


def _key_of(line: str) -> str | None:
    head = line.split("#", 1)[0]
    if "=" not in head:
        return None
    return head.split("=", 1)[0].strip().strip('"').strip("'")


def set_value(path, dotted_key: str, value) -> None:
    """Rewrite exactly one key, leaving comments, order and spacing alone.

    A whole-document round-trip through a TOML writer would reformat the
    user's file; we only ever touch the one line that owns the key.
    """
    table, _, leaf = dotted_key.rpartition(".")
    with open(path, encoding="utf-8") as fh:
        lines = fh.read().splitlines(keepends=True)

    new_line = f"{leaf} = {_fmt(value)}\n"
    current, start, end = None, None, None
    for i, line in enumerate(lines):
        m = _HEADER.match(line)
        if m:
            if current == table and start is not None and end is None:
                end = i
            current = m.group(1)
            if current == table:
                start = i + 1
        elif current == table and start is not None and _key_of(line) == leaf:
            comment = line.split("#", 1)
            tail = f"  #{comment[1]}" if len(comment) > 1 else ""
            lines[i] = new_line.rstrip("\n") + tail.rstrip("\n") + "\n"
            with open(path, "w", encoding="utf-8") as fh:
                fh.writelines(lines)
            return

    if start is None:  # table absent: append it
        if lines and not lines[-1].endswith("\n"):
            lines.append("\n")
        lines.extend(["\n", f"[{table}]\n", new_line])
    else:  # table present, key absent: append inside it
        stop = end if end is not None else len(lines)
        while stop > start and not lines[stop - 1].strip():
            stop -= 1
        lines.insert(stop, new_line)

    with open(path, "w", encoding="utf-8") as fh:
        fh.writelines(lines)
