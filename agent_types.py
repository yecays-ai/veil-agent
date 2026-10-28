"""Shared types and errors used across the Agent's core modules."""
from datetime import datetime


AUTONOMY_WAKE = object()
GLANCE_WAKE = object()
CODEX_WATCH_WAKE = object()


class UserError(Exception):
    """An expected, user-safe Agent error."""


class TooLong(UserError):
    """Nothing was saved or sent; the session can carry on."""


class RequestFailed(UserError):
    """A provider request failed after being sent; its reservation stays pending."""


class BudgetExceeded(UserError):
    """A request was refused before sending because of the configured limit."""


class EmptyReplyError(UserError):
    """The provider returned an empty or whitespace-only text reply."""


# Ticket 133: during Peter's own autonomous activity nobody is asked; tools report that truthfully.
NOT_ASKED = 'not asked'
NOT_ASKED_TEXT = ('Lan is not asked during your own autonomous activity, so this was not done. '
                  'It needs her approval: ask her later in chat or on QQ.')


def not_asked(*_):
    return NOT_ASKED


def now():
    """Return the local ISO timestamp format used by existing Agent ledgers."""
    return datetime.now().astimezone().isoformat()

