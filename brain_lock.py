"""Cross-process lock shared by Brain writes and freeze/unfreeze."""
from __future__ import annotations

import contextlib
import os
from pathlib import Path

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
            msvcrt.locking(fd, msvcrt.LK_LOCK, 1)
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
