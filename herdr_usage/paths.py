"""Per-OS config and state locations.

Herdr follows the platform convention, not XDG everywhere: Windows uses
%APPDATA%, macOS uses ~/Library/Application Support. Getting this wrong means
the plugin writes a config nobody reads.
"""

import os
import pathlib
import sys


def _home() -> pathlib.Path:
    return pathlib.Path(os.path.expanduser("~"))


def config_home() -> pathlib.Path:
    """Where Herdr keeps config.toml on this OS."""
    if sys.platform == "win32":
        base = os.environ.get("APPDATA")
        return pathlib.Path(base) if base else _home() / "AppData" / "Roaming"
    if sys.platform == "darwin":
        return _home() / "Library" / "Application Support"
    return pathlib.Path(os.environ.get("XDG_CONFIG_HOME") or _home() / ".config")


def state_home() -> pathlib.Path:
    """Where Herdr keeps plugin state on this OS."""
    if sys.platform == "win32":
        base = os.environ.get("LOCALAPPDATA")
        return pathlib.Path(base) if base else _home() / "AppData" / "Local"
    if sys.platform == "darwin":
        return _home() / "Library" / "Application Support"
    return pathlib.Path(os.environ.get("XDG_STATE_HOME") or _home() / ".local" / "state")


def herdr_config() -> pathlib.Path:
    env = os.environ.get("HERDR_CONFIG_PATH")
    return pathlib.Path(env) if env else config_home() / "herdr" / "config.toml"
