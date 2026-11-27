"""One process-wide gate for operations that share Windows desktop state."""
import threading
import weakref
from contextlib import contextmanager


_DESKTOP_LOCK = threading.RLock()


class DesktopBusy(RuntimeError):
    pass


@contextmanager
def desktop_access(timeout=0):
    """Serialize UIA reads with the QQ adapter's foreground and clipboard work."""
    if not _DESKTOP_LOCK.acquire(timeout=max(0, float(timeout))):
        raise DesktopBusy('The Windows desktop resource is busy.')
    try:
        yield
    finally:
        _DESKTOP_LOCK.release()


# Input this program generates (UIA keyboard fallback, QQ send) shows up in GetLastInputInfo like
# Lan's own. Each real-clock input tracker registers here so input made by one part of VEIL is not
# mistaken for Lan by another (QQ waiting 2 minutes after Peter typed, or Peter's next action
# refused right after a QQ message went out).
_INPUT_TRACKERS = weakref.WeakSet()
_TRACKERS_LOCK = threading.Lock()


def register_input_tracker(tracker):
    """`tracker` provides note_program_input(), called when another part of VEIL made input."""
    with _TRACKERS_LOCK:
        _INPUT_TRACKERS.add(tracker)


def program_input_made(source):
    with _TRACKERS_LOCK:
        others = [tracker for tracker in _INPUT_TRACKERS if tracker is not source]
    for tracker in others:
        tracker.note_program_input()
