"""Cross-process lock shared by Brain writes and freeze/unfreeze."""
from __future__ import annotations

import contextlib
import os
import time
from pathlib import Path

LOCK_TIMEOUT_SECONDS = 120.0

try:
    import msvcrt
except ImportError:  # pragma: no cover
    msvcrt = None
try:
    import fcntl
except ImportError:  # pragma: no cover
    fcntl = None


@contextlib.contextmanager
def write_lock(store, role):
    path = Path(store.root) / role / 'brain-write.lock'
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o600)
    try:
        if os.fstat(fd).st_size == 0:
            os.write(fd, b'0')
        os.lseek(fd, 0, os.SEEK_SET)
        if msvcrt is not None:
            # LK_LOCK gives up after ~10 seconds on Windows; keep retrying non-blocking so a slow
            # in-flight write or freeze cannot fail a legitimate cutover after ten seconds.
            deadline = time.monotonic() + LOCK_TIMEOUT_SECONDS
            while True:
                try:
                    msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
                    break
                except OSError:
                    if time.monotonic() >= deadline:
                        raise TimeoutError('brain write lock is busy')
                    time.sleep(0.05)
        elif fcntl is not None:
            fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        try:
            os.lseek(fd, 0, os.SEEK_SET)
            if msvcrt is not None:
                msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
            elif fcntl is not None:
                fcntl.flock(fd, fcntl.LOCK_UN)
        finally:
            os.close(fd)
