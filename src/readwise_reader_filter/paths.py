"""Centralized path resolution for config and cache directories.

Respects XDG Base Directory Specification:
- XDG_CONFIG_HOME (default: ~/.config)
- XDG_CACHE_HOME (default: ~/.cache)
"""

import os
from pathlib import Path

APP_NAME = "readwise-reader-filter"


def _xdg_config_home() -> Path:
    return Path(os.getenv("XDG_CONFIG_HOME", Path.home() / ".config"))


def _xdg_cache_home() -> Path:
    return Path(os.getenv("XDG_CACHE_HOME", Path.home() / ".cache"))


def config_dir() -> Path:
    return _xdg_config_home() / APP_NAME


def config_path() -> Path:
    return config_dir() / "config.yaml"


def cache_dir() -> Path:
    return _xdg_cache_home() / APP_NAME


def action_log_path() -> Path:
    return cache_dir() / "actions.jsonl"


def api_cache_dir() -> Path:
    return _xdg_cache_home() / "readwise_reader_api"


def api_state_cache_dir() -> Path:
    return api_cache_dir() / "state"
