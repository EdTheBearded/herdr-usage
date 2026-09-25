#!/usr/bin/env python3
"""herdr-usage CLI entry point.

Every Herdr trigger (startup hook, event hook, action, popup) lands here and
runs the same idempotent refresh. The cache TTL is the rate limiter, so an
event storm costs ~10ms and zero API calls.
"""

import os
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

from herdr_usage import cache, config, herdrctl, main, providers, sidebar

PLUGIN_ID = "herdr-usage"


def _config_dir() -> pathlib.Path:
    env = os.environ.get("HERDR_PLUGIN_CONFIG_DIR")
    if env:
        return pathlib.Path(env)
    base = os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config")
    return pathlib.Path(base) / "herdr" / "plugins" / "config" / PLUGIN_ID


def _config_path() -> pathlib.Path:
    return _config_dir() / "config.toml"


def _cache_path() -> pathlib.Path:
    return sidebar.state_dir() / "usage.json"


def _herdr_config() -> pathlib.Path:
    env = os.environ.get("HERDR_CONFIG_PATH")
    if env:
        return pathlib.Path(env)
    base = os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config")
    return pathlib.Path(base) / "herdr" / "config.toml"


def _probes(cfg):
    """Built-ins plus any declarative provider the user added to the config."""
    out = {
        "codex": lambda: providers.normalize_codex(providers.probe_codex()),
        "claude": lambda: providers.normalize_claude(providers.probe_claude()),
    }
    for name, spec in (cfg.get("providers") or {}).items():
        if name in out or not isinstance(spec, dict) or not spec.get("command"):
            continue
        out[name] = (
            lambda n=name, s=spec: providers.run_declarative(
                n, s, providers.probe_declarative(s)
            )
        )
    return out


def _with_models(panes):
    """Attach each pane's model and the provider whose quota it consumes.

    For Hermes the two differ: the agent is `hermes`, but the account billed is
    whatever that session was routed to. A session on a local or custom
    endpoint gets provider=None so it renders no gauge at all.
    """
    codex_db = pathlib.Path.home() / ".codex" / "state_5.sqlite"
    hermes_db = pathlib.Path.home() / ".hermes" / "state.db"
    for pane in panes:
        slug, agent = None, pane.get("agent")
        sid = (pane.get("agent_session") or {}).get("value")

        if agent == "hermes" and hermes_db.exists():
            info = providers.hermes_session(sid, str(hermes_db))
            if info:
                slug = info["model"]
                pane["provider"] = info["provider"]
                pane["vendor"] = info.get("vendor")
            else:
                pane["provider"] = None
        elif agent == "codex" and codex_db.exists():
            slug = providers.codex_model(pane.get("cwd", ""), str(codex_db))
        elif agent == "claude" and sid:
            root = pathlib.Path.home() / ".claude" / "projects"
            hits = list(root.glob(f"*/{sid}.jsonl")) if root.exists() else []
            if hits:
                slug = providers.claude_model(hits[0])
        pane["model"] = providers.short_model(slug)
    return panes


def cmd_refresh(force: bool = False) -> int:
    cfg = config.load(_config_path())
    panes = _with_models(herdrctl.pane_list())
    if not panes:
        return 0
    data = main.refresh(
        cfg, cache.load(_cache_path()), panes, _probes(cfg), force=force
    )
    data.pop("panes", None)  # per-pane render output isn't cache state
    cache.save(_cache_path(), data)
    return 0


def cmd_setup_sidebar(undo: bool = False) -> int:
    path = _herdr_config()
    if undo:
        removed = sidebar.undo(path)
        print(f"{'removed' if removed else 'no block found in'} {path}")
        return 0
    if sidebar.is_uninstalled():
        print("sidebar block was removed on purpose; not re-adding")
        print("run with --force to re-install")
        return 0
    return _install(path)


def _install(path: pathlib.Path) -> int:
    cfg = config.load(_config_path())
    if sidebar.install(path, sidebar.generate(cfg)):
        print(f"sidebar block written to {path}")
        herdrctl._run(["server", "reload-config"], check=False)
        return 0
    print(f"config check failed; {path} left untouched", file=sys.stderr)
    return 1


def cmd_settings() -> int:
    from herdr_usage import settings_tui

    path = _config_path()
    edited = settings_tui.run(config.load(path))
    if edited is None:
        return 0
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        path.write_text("# herdr-usage configuration\n", encoding="utf-8")
    for table, keys in edited.items():
        for key, value in (keys or {}).items():
            if isinstance(value, dict):
                for sub, subval in value.items():
                    config.set_value(path, f"{table}.{key}.{sub}", subval)
            else:
                config.set_value(path, f"{table}.{key}", value)
    _install(_herdr_config())
    return cmd_refresh(force=True)


def cmd_print_sidebar() -> int:
    """Print the block for a remote client's own config.toml.

    Herdr draws the sidebar on the CLIENT, so a remote/Windows client needs
    this block in its own config even though the plugin runs on the server.
    """
    print(sidebar.generate(config.load(_config_path())), end="")
    return 0


def cmd_doctor() -> int:
    cfg = config.load(_config_path())
    print(f"config      {_config_path()}")
    print(f"cache       {_cache_path()}")
    print(f"herdr conf  {_herdr_config()}")
    block = sidebar.BEGIN in _herdr_config().read_text(encoding="utf-8") \
        if _herdr_config().exists() else False
    print(f"sidebar     {'installed' if block else 'NOT installed'}")
    panes = herdrctl.pane_list()
    print(f"agent panes {len(panes)}: "
          f"{sorted(str(p.get('agent')) for p in panes)}")
    for name, probe in _probes(cfg).items():
        if not (cfg.get("providers") or {}).get(name, {}).get("enabled", True):
            print(f"  {name:<8} disabled")
            continue
        try:
            usage = probe()
            gauges = ", ".join(
                f"{g.label} {g.pct:.0f}%" if g.pct is not None else f"{g.label} ?"
                for g in usage.gauges
            )
            print(f"  {name:<8} ok    plan={usage.plan} {gauges}")
        except Exception as exc:
            print(f"  {name:<8} FAIL  {exc}")
    return 0


def usage() -> int:
    print((__doc__ or "herdr-usage").strip())
    print("\ncommands: refresh | startup | on-event | setup-sidebar [--undo]"
          " | settings | print-sidebar | doctor")
    return 2


if __name__ == "__main__":
    argv = sys.argv[1:]
    cmd = argv[0] if argv else ""
    flags = argv[1:]
    try:
        if cmd in ("refresh", "on-event"):
            code = cmd_refresh()
        elif cmd == "startup":
            code = cmd_refresh(force=True)  # a restart wiped every token
        elif cmd == "setup-sidebar":
            code = (
                _install(_herdr_config())
                if "--force" in flags
                else cmd_setup_sidebar(undo="--undo" in flags)
            )
        elif cmd == "settings":
            code = cmd_settings()
        elif cmd == "print-sidebar":
            code = cmd_print_sidebar()
        elif cmd == "doctor":
            code = cmd_doctor()
        else:
            code = usage()
    except Exception as exc:
        print(f"herdr-usage: {exc}", file=sys.stderr)
        code = 1
    sys.exit(code)
