"""Writing plex's own JSON files (auth, state, conversations) safely."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

# What "private" means for plex's data: only the user can read the file or open its folder.
PRIVATE_FILE = 0o600
PRIVATE_DIR = 0o700


def atomic_write_json(path: Path, data: Any, private: bool = False) -> None:
    """Write `data` to a temp file next to `path`, then rename it over `path`.

    A crash mid-write leaves the old file intact. A `private` file gets PRIVATE_FILE
    permissions, and its folder PRIVATE_DIR if it has to be created; otherwise both follow
    the umask. Folders further up are always created with the umask.
    """
    path.parent.mkdir(parents=True, exist_ok=True, mode=PRIVATE_DIR if private else 0o777)
    tmp = path.with_name(f"{path.name}.{os.getpid()}.tmp")  # per process: two plex instances may write at once
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, PRIVATE_FILE if private else 0o666)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        if private:
            os.chmod(tmp, PRIVATE_FILE)  # O_CREAT's mode is masked by the umask, and ignored for an existing file
        os.replace(tmp, path)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise
