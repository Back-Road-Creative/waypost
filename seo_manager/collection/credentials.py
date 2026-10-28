"""Resolve the YouTube token a ``collect`` command uses: a plain file, or SOPS.

A SOPS file is decrypted with the same helper the reach probe uses into a
private temp directory (0700, file 0600) that lives only for the ``with`` body,
so a scheduled run never needs a plaintext token on disk between runs. A decrypt
failure raises :class:`CredentialsError` before any client is built; its message
carries sops' exit status or the exception type, never token text.
"""

from __future__ import annotations

import json
import os
import shutil
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from seo_manager.analytics.youtube_reach_probe import _sops_token


class CredentialsError(Exception):
    """The SOPS token could not be decrypted."""


@contextmanager
def youtube_credentials(
    plain: str | None, sops_file: str | None
) -> Iterator[Path | None]:
    """Yield the token file path to hand to the clients, or None if neither is set."""
    if sops_file is None:
        yield Path(plain) if plain else None
        return
    try:
        info, error = _sops_token(sops_file)
    except Exception as exc:  # the type only: a message could echo a token
        raise CredentialsError(
            f"could not decrypt {sops_file}: {type(exc).__name__}"
        ) from None
    if info is None:
        raise CredentialsError(f"could not decrypt {sops_file}: {error}")
    work = Path(tempfile.mkdtemp(prefix="seo-token-"))  # mkdtemp makes it 0700
    try:
        path = work / "token.json"
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(info, fh)
        yield path
    finally:
        shutil.rmtree(work, ignore_errors=True)
