"""Ticket 400: every piece of text that reaches the model is registered here.

The registry is the single list of instructions Peter can receive. Defaults ship with the code
(in prompts/defaults); Lan's edits live outside the repository (in persona/instructions) and win.
Nothing in a request path writes prose inline any more: call text(entry_id) instead.
"""
import re
from pathlib import Path

from . import registry
from .store import PromptStore
from .tools import ToolText

from veil.paths import PROJECT_ROOT as ROOT
DEFAULTS = Path(__file__).with_name('defaults')
OVERRIDES = ROOT / 'persona' / 'instructions'

_store = None
_tools = None


def store():
    global _store
    if _store is None:
        _store = PromptStore(DEFAULTS, OVERRIDES)
    return _store


def tools():
    """Per-tool description overrides; a new tool needs no registration."""
    global _tools
    if _tools is None:
        _tools = ToolText(OVERRIDES)
    return _tools


def tool_overrides():
    return tools().all()


def tool_text(name, default):
    return tools().text(name, default)


def save_tool(name, text):
    return tools().save(name, text)


def text(entry_id):
    """The text for one registered entry: Lan's edit if she saved one, otherwise the default."""
    return store().text(entry_id)


def entries():
    return registry.entries()


def entry(entry_id):
    return registry.get(entry_id)


PLACEHOLDER = re.compile(r'\{([a-z_]+)\}')


def fill(template, values):
    """Fill named placeholders once; an unknown name stays visible instead of vanishing."""
    return PLACEHOLDER.sub(lambda match: str(values.get(match.group(1), match.group(0))), template)


def validate():
    """Raise ValueError when the registry cannot be served. Called at startup and in tests."""
    return registry.validate(DEFAULTS)
