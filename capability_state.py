"""Process-local transactions for the role capability manifest."""
import json
import os
import threading
from functools import wraps
from pathlib import Path


_locks = {}
_registry_lock = threading.Lock()


def capability_lock(path):
    # Shared by FilesystemTools instances and NativeMemoryTools for this file.
    key = os.path.normcase(str(Path(path).resolve()))
    with _registry_lock:
        return _locks.setdefault(key, threading.RLock())


def capability_transaction(method):
    @wraps(method)
    def locked(self, *args, **kwargs):
        path = getattr(self, 'cap_path', None)
        if path is None:
            return method(self, *args, **kwargs)
        # Do not call Hub/approval/model callbacks while holding this lock.
        with capability_lock(path):
            return method(self, *args, **kwargs)
    return locked


def save_capabilities(path, row):
    """Caller holds capability_lock across read, mutation, save and publication."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix('.json.tmp')
    with temporary.open('w', encoding='utf-8', newline='\n') as handle:
        handle.write(json.dumps(row, ensure_ascii=False, indent=2))
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)
