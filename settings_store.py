"""settings_store.py — Minimal disk-backed settings store (stdlib only)

Storage location:
  1. ~/.rics/settings.json  (preferred)
  2. <repo>/.rics/settings.json  (fallback if home not writable)

Robustness:
  - Missing file  => {}
  - Corrupt JSON   => {}
  - Atomic write via temp file + os.replace
"""

import json
import os
import tempfile
from pathlib import Path


def _settings_dir():
    """Return the writable .rics directory, creating it if needed."""
    # Try home directory first
    home = Path.home()
    home_rics = home / ".rics"
    try:
        home_rics.mkdir(parents=True, exist_ok=True)
        # Verify writable by touching a probe file
        probe = home_rics / ".probe"
        probe.write_text("")
        probe.unlink()
        return home_rics
    except OSError:
        pass

    # Fallback: repo-local
    repo_rics = Path(__file__).resolve().parent / ".rics"
    repo_rics.mkdir(parents=True, exist_ok=True)
    return repo_rics


def _settings_path():
    return _settings_dir() / "settings.json"


def load_settings() -> dict:
    """Load settings from disk. Returns {} on missing/corrupt file."""
    path = _settings_path()
    try:
        data = path.read_text(encoding="utf-8")
        result = json.loads(data)
        if not isinstance(result, dict):
            return {}
        return result
    except (OSError, json.JSONDecodeError, ValueError):
        return {}


def save_settings(patch: dict) -> dict:
    """Shallow-merge *patch* into current settings, persist, and return merged dict."""
    current = load_settings()
    current.update(patch)

    path = _settings_path()
    dir_ = path.parent
    dir_.mkdir(parents=True, exist_ok=True)

    # Atomic write: write to temp file in same dir, then replace
    fd, tmp = tempfile.mkstemp(dir=str(dir_), suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(current, f, indent=2)
        os.replace(tmp, str(path))
    except BaseException:
        # Clean up temp file on failure
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise

    return current


def get_setting(key, default=None):
    """Read a single setting value."""
    return load_settings().get(key, default)


def set_setting(key, value):
    """Write a single setting value (persists immediately)."""
    return save_settings({key: value})
