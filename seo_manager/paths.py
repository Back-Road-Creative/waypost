"""Per-user application directories.

The data home is where seo-manager keeps what it owns when the caller names no
location: the Metricool PDF importer's default event store. It is resolved in
this order:

1. ``$SEO_MANAGER_DATA_HOME``
2. ``$XDG_DATA_HOME/seo-manager``
3. ``~/.local/share/seo-manager``

Other stores (the per-site SEO store, quota counters, the optimisation
daemon's and trackers' state) take an explicit path argument or option and
fall back to a ``data/...`` directory relative to the working directory.

Locations that belong to a particular deployment (a sibling site checkout, a
brand's data directory, scripts of another tool) have no default here: they
are passed in explicitly and an unset one is reported as "not configured".
"""

from __future__ import annotations

import os
from pathlib import Path

DATA_HOME_ENV = "SEO_MANAGER_DATA_HOME"
COLLECTION_DB_ENV = "SEO_COLLECTION_DB"


def data_home() -> Path:
    """Return the directory seo-manager keeps its own data in (not created)."""
    explicit = os.environ.get(DATA_HOME_ENV)
    if explicit:
        return Path(explicit).expanduser()
    xdg = os.environ.get("XDG_DATA_HOME")
    if xdg:
        return Path(xdg).expanduser() / "seo-manager"
    return Path.home() / ".local" / "share" / "seo-manager"


def collection_db() -> Path:
    """Return the collection store file: ``$SEO_COLLECTION_DB`` or under the data home.

    Never repo-relative, and not created here (only ``CollectionStore.init`` does).
    """
    explicit = os.environ.get(COLLECTION_DB_ENV)
    if explicit:
        return Path(explicit).expanduser()
    return data_home() / "collection.sqlite3"
