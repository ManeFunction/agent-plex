"""API-key storage, opencode-style: a 0600 JSON file in the data dir.

~/.local/share/plex/auth.json
    {"perplexity": {"type": "api", "key": "pplx-..."}}
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from .config import DATA_DIR

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
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    tmp = path.with_suffix(".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        json.dump(data, f, indent=2)
    os.chmod(tmp, 0o600)
    os.replace(tmp, path)
    return path


def remove_key(path: Path | None = None) -> bool:
    path = _path(path)
    data = _read(path)
    if PROVIDER not in data:
        return False
    del data[PROVIDER]
    if data:  # keep other providers untouched
        fd = os.open(path, os.O_WRONLY | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as f:
            json.dump(data, f, indent=2)
    else:
        path.unlink(missing_ok=True)
    return True


def mask(key: str) -> str:
    return key[:5] + "…" + key[-4:] if len(key) > 12 else "…" * 3
