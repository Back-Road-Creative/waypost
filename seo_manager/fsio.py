"""Crash-safe file writes for state that must survive an interrupted process."""

from __future__ import annotations

import contextlib
import json
import os
import shutil
import uuid
from pathlib import Path
from typing import Any


def atomic_write_json(path: str | Path, data: Any, indent: int = 2) -> None:
    """Write ``data`` as JSON to ``path`` so a reader sees the old file or the new.

    The document is written to a sibling temporary file, flushed to disk, and
    moved over ``path`` with ``os.replace`` (one step on a single filesystem).
    A crash, a full disk or an exception while serialising leaves the previous
    content of ``path`` untouched and removes the temporary file. The mode of
    an existing file is kept; a new file gets the process umask, as a plain
    ``open(path, "w")`` would give it.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.{uuid.uuid4().hex[:8]}.tmp")
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=indent)
            f.flush()
            os.fsync(f.fileno())
        with contextlib.suppress(FileNotFoundError):
            shutil.copymode(path, tmp)
        os.replace(tmp, path)
    except BaseException:
        with contextlib.suppress(FileNotFoundError):
            tmp.unlink()
        raise
