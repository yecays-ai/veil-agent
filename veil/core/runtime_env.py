"""Where the running instance reads its secrets from.

The repository root is the production instance. An acceptance/test instance started with
``--config-root`` must read that directory's own ``.env`` (or none at all), never fall back to the
production keys next to the code. ``Character.root`` carries the config root for that purpose.
"""
from __future__ import annotations

from pathlib import Path

from veil.paths import PROJECT_ROOT as ROOT


def config_root(cfg=None):
    """The config root for this instance; the repository root only for a legacy/plain config."""
    root = getattr(cfg, 'root', None) if cfg is not None else None
    if isinstance(root, str) and root.strip():
        return Path(root).resolve()
    return ROOT


def env_file(cfg=None):
    """The only .env file this instance may read for model, embedding and channel keys."""
    return config_root(cfg) / '.env'
