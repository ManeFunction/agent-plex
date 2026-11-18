"""API keys: which one plex uses, and storing one opencode-style in a 0600 JSON file.

~/.local/share/plex/auth.json
    {"perplexity": {"type": "api", "key": "pplx-..."}}
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from .config import DATA_DIR
from .files import atomic_write_json

AUTH_FILE = DATA_DIR / "auth.json"
PROVIDER = "perplexity"


def _path(path: Path | None) -> Path:
    return path if path is not None else AUTH_FILE


def _read(path: Path) -> dict:
    try:
        data = json.loads(path.read_text())
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def get_key(path: Path | None = None) -> str | None:
    path = _path(path)
    entry = _read(path).get(PROVIDER) or {}
    key = entry.get("key") if isinstance(entry, dict) else None
    return key or None


def set_key(key: str, path: Path | None = None) -> Path:
    path = _path(path)
    key = key.strip()
    if not key:
        raise ValueError("empty API key")
    data = _read(path)
    data[PROVIDER] = {"type": "api", "key": key}
    atomic_write_json(path, data, private=True)
    return path


def remove_key(path: Path | None = None) -> bool:
    path = _path(path)
    data = _read(path)
    if PROVIDER not in data:
        return False
    del data[PROVIDER]
    if data:  # keep other providers untouched
        atomic_write_json(path, data, private=True)
    else:
        path.unlink(missing_ok=True)
    return True


def mask(key: str) -> str:
    return key[:5] + "…" + key[-4:] if len(key) > 12 else "…" * 3


def api_key_source(file_cfg: dict) -> tuple[str, str] | None:
    """(key, source) for the key plex will use, or None. Sources, in order of precedence:
    "env" (PERPLEXITY_API_KEY), "auth" (auth.json, `plex auth login`), "config" (config.toml)."""
    for source, key in (("env", lambda: os.environ.get("PERPLEXITY_API_KEY")),
                        ("auth", get_key),
                        ("config", lambda: file_cfg.get("api_key"))):
        if k := key():
            return k, source
    return None


def resolve_api_key(file_cfg: dict) -> str | None:
    found = api_key_source(file_cfg)
    return found[0] if found else None
