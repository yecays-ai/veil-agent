"""Single source of truth for repository and runtime root paths."""
from __future__ import annotations

from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def repo_path(*parts: str) -> Path:
    """Resolve a path relative to the repository root."""
    return PROJECT_ROOT.joinpath(*parts)
