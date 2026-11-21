"""One shape for every failed tool call (ticket 415).

Lan's three corrections, which this module exists to enforce:

1. The program never invents a corrective step.  A fix is included only when the
   refusing code already knows it; otherwise the failure says so explicitly.
2. Internal exception text never reaches the model or the activity ledger.  The
   failure carries a short error id, and the traceback stays in a local-only file.
3. Old ledger rows keep whatever they recorded; only new rows are covered.

Tools opt in by returning ``refusal(...)`` (or a result object carrying ``fix`` /
``received`` attributes).  ``finalize`` is the single renderer used by the tool
funnel, so the model text and the ledger detail are always built from the same
facts.
"""
import json
import secrets

# Sentinel for "the program knows it has no corrective step".  Distinct from
# ``None``, which means "the refusing code did not state a fix" (legacy messages
# that already say what to do keep their own wording).
UNKNOWN = object()


class ArgumentError(ValueError):
    """The tool arguments could not be parsed at all (a ValueError, as before)."""


class Refusal(str):
    """A refusal that keeps the tool's own reason and, when known, the fix."""

    def __new__(cls, reason, *, fix=None, received=None, detail=None):
        value = super().__new__(cls, reason)
        value.fix = fix
        value.received = received
        value.detail = detail
        return value


def refusal(reason, *, fix=None, received=None, detail=None):
    return Refusal(reason, fix=fix, received=received, detail=detail)


def _short(value, limit=160):
    if isinstance(value, str):
        text = value
    else:
        try:
            text = json.dumps(value, ensure_ascii=False, default=str)
        except (TypeError, ValueError):
            text = str(value)
    text = ' '.join(text.split())
    return text if len(text) <= limit else text[:limit] + '…'


def finalize(result, state, detail=None):
    """Return ``(model text, ledger detail)`` for one finished tool call.

    A successful result is returned untouched.  For a failure the tool's own
    words always come first; the program only appends facts it has (the
    arguments received, and a correction only when the tool supplied one).
    """
    if state == '成功' or not isinstance(result, str):
        return result, detail
    fix = getattr(result, 'fix', None)
    received = getattr(result, 'received', None)
    if fix is None and received is None:
        return result, detail
    text = str(result)
    if received is not None:
        text += '\n[program] arguments received: ' + _short(received)
    if fix is UNKNOWN:
        text += ('\n[program] The program does not know a corrective step for this failure; '
                 'do not repeat the same call unchanged.')
    elif fix:
        text += '\n[program] correction: ' + str(fix)
    ledger = (detail or str(result)).rstrip()
    if received is not None:
        ledger += '；收到参数：' + _short(received)
    if fix is UNKNOWN:
        ledger += '；程序不知道该怎么改，别原样重试'
    elif fix:
        ledger += '；怎么改：' + str(fix)
    return text, ledger


def internal_failure(tool, exc):
    """Return ``(model text, ledger detail, error id)`` for a program-internal failure.

    The exception text is deliberately not used: it can hold local paths, file
    contents or credentials.  Only the class name (a Python type name, never
    data) and a random id leave this function; the caller writes the traceback
    to the local-only ``tool-errors.jsonl``.
    """
    error_id = 'err-' + secrets.token_hex(4)
    kind = type(exc).__name__
    text = (f'The tool failed inside the program (error id {error_id}, {kind}). '
            'The program could not finish this call; it cannot tell whether the arguments '
            'caused it and does not know a corrective step. Do not repeat the same call '
            f'unchanged. If it happens again, tell lan the id {error_id}.')
    ledger = (f'工具内部故障（{error_id}，{kind}）；程序无法判断是否与参数有关，'
              '原始错误只保存在本机 tool-errors.jsonl')
    return text, ledger, error_id
