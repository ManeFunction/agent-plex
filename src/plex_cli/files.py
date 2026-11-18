"""Writing plex's own JSON files (auth, state, conversations) safely."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any


def atomic_write_json(path: Path, data: Any, mode: int | None = None, dir_mode: int | None = None) -> None:
    """Write `data` to a temp file next to `path`, then rename it over `path`.

    A crash mid-write leaves the old file intact. `mode` sets the file's permissions and
    `dir_mode` those of the parent folder if it has to be created (both default to the umask;
    folders further up are always created with the umask).
    """
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o777 if dir_mode is None else dir_mode)
    tmp = path.with_suffix(".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o666 if mode is None else mode)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        if mode is not None:
            os.chmod(tmp, mode)  # O_CREAT's mode is masked by the umask, and ignored for an existing file
        os.replace(tmp, path)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise
