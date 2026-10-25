"""One process-wide gate for operations that share Windows desktop state."""
import threading
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
