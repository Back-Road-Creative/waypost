"""Where a deployment's private input files live.

The package is publishable; the data plugged into it (the tenant's
``brand.yaml``, site configs, link maps) is not, so it is never read from
inside the package tree. The inputs directory is resolved in this order:

1. ``$SEO_INPUTS_DIR``
2. ``<data home>/inputs`` (see :mod:`seo_manager.paths`)

Resolving the directory never creates or reads it. :func:`require_inputs_dir`
and :func:`input_path` are the checked forms: they raise
:class:`InputsDirError` naming the variable and the path that was tried.
"""

from __future__ import annotations

import os
from pathlib import Path

from seo_manager import paths

INPUTS_DIR_ENV = "SEO_INPUTS_DIR"
BRAND_YAML_ENV = "SEO_BRAND_YAML"
BRAND_YAML_NAME = "brand.yaml"


class InputsDirError(ValueError):
    """The inputs directory, or a file expected in it, is not usable."""


def inputs_dir() -> Path:
    """Return the inputs directory (not created, not checked)."""
    explicit = os.environ.get(INPUTS_DIR_ENV)
    if explicit:
        return Path(explicit).expanduser()
    return paths.data_home() / "inputs"


def require_inputs_dir() -> Path:
    """Return the inputs directory, or raise if it is not an existing directory."""
    path = inputs_dir()
    if not path.exists():
        raise InputsDirError(
            f"inputs directory {path} does not exist; set {INPUTS_DIR_ENV} to the "
            "directory holding your private input files"
        )
    if not path.is_dir():
        raise InputsDirError(
            f"inputs directory {path} is not a directory; check {INPUTS_DIR_ENV}"
        )
    return path


def input_path(*parts: str) -> Path:
    """Return an existing file under the inputs directory.

    Raises :class:`InputsDirError` when the directory is unusable, the file is
    missing, or ``parts`` would resolve outside the directory.
    """
    root = require_inputs_dir().resolve()
    target = root.joinpath(*parts)
    resolved = target.resolve()
    if resolved != root and root not in resolved.parents:
        raise InputsDirError(f"{target} is outside the inputs directory {root}")
    if not resolved.is_file():
        rel = "/".join(parts)
        raise InputsDirError(
            f"input file {rel} not found in {root} (from {INPUTS_DIR_ENV})"
        )
    return target


def default_brand_yaml() -> str | None:
    """``$SEO_BRAND_YAML``, else ``brand.yaml`` in ``$SEO_INPUTS_DIR``, else ``None``.

    The inputs directory is consulted only when ``SEO_INPUTS_DIR`` is set, so a
    caller that configured nothing still gets "not configured" rather than a
    guess under the data home.
    """
    explicit = os.environ.get(BRAND_YAML_ENV)
    if explicit:
        return explicit
    if os.environ.get(INPUTS_DIR_ENV):
        return str(input_path(BRAND_YAML_NAME))
    return None
