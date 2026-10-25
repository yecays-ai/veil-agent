"""Shared types and errors used across the Agent's core modules."""
from datetime import datetime


AUTONOMY_WAKE = object()
GLANCE_WAKE = object()


class UserError(Exception):
    """An expected, user-safe Agent error."""


class TooLong(UserError):
    """Nothing was saved or sent; the session can carry on."""


class RequestFailed(UserError):
    """A provider request failed after being sent; its reservation stays pending."""


class BudgetExceeded(UserError):
    """A request was refused before sending because of the configured limit."""


def now():
    """Return the local ISO timestamp format used by existing Agent ledgers."""
    return datetime.now().astimezone().isoformat()

