"""Ticket 400: every piece of text that reaches the model is registered here.

The registry is the single list of instructions Peter can receive. Defaults ship with the code
(in prompts/defaults); Lan's edits live outside the repository (in persona/instructions) and win.
Nothing in a request path writes prose inline any more: call text(entry_id) instead.
"""
from pathlib import Path

from . import registry
from .store import PromptStore

ROOT = Path(__file__).resolve().parents[1]
DEFAULTS = Path(__file__).with_name('defaults')
OVERRIDES = ROOT / 'persona' / 'instructions'

_store = None


def store():
    global _store
    if _store is None:
        _store = PromptStore(DEFAULTS, OVERRIDES)
    return _store


def text(entry_id):
    """The text for one registered entry: Lan's edit if she saved one, otherwise the default."""
    return store().text(entry_id)


def entries():
    return registry.entries()


def entry(entry_id):
    return registry.get(entry_id)


def validate():
    """Raise ValueError when the registry cannot be served. Called at startup and in tests."""
    return registry.validate(DEFAULTS)
