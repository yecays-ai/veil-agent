"""Temporary Chrome Guest profile folders that VEIL creates, and their cleanup.

Every folder lives inside one dedicated parent (`<system temp>/veil-chrome-guest/`) and carries an owner
marker. Only folders that are inside that parent AND carry the marker are ever removed; the user's own
Chrome profile and any other folder are never touched. One shared thread watches all running guest
browsers (instead of one thread per launch); folders left behind by a VEIL process that died are swept
on the next launch."""
import ctypes
import json
import os
import shutil
import tempfile
import threading
import time
import uuid
from pathlib import Path

PARENT_NAME = 'veil-chrome-guest'
MARKER = '.veil-owner.json'
POLL_SECONDS = 5.0
STALE_AFTER_DEAD_OWNER = 60 * 60       # owner process is gone and the folder is at least 1 hour old
STALE_ANYWAY = 7 * 24 * 60 * 60        # a pid can be reused, so also sweep anything over 7 days old


def parent_directory():
    return Path(tempfile.gettempdir()) / PARENT_NAME


def pid_alive(pid):
    if not isinstance(pid, int) or pid <= 0:
        return False
    if os.name == 'nt':
        kernel32 = ctypes.windll.kernel32
        handle = kernel32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
        if not handle:
            return False
        try:
            code = ctypes.c_ulong()
            return bool(kernel32.GetExitCodeProcess(handle, ctypes.byref(code))) and code.value == 259
        finally:
            kernel32.CloseHandle(handle)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _is_link(path):
    return path.is_symlink() or (hasattr(os.path, 'isjunction') and os.path.isjunction(path))


def remove_profile(directory, parent=None):
    """Delete one VEIL-owned guest profile; refuses anything not directly inside the dedicated parent."""
    directory, parent = Path(directory), Path(parent) if parent else parent_directory()
    try:
        if directory.parent.resolve() != parent.resolve() or _is_link(directory):
            return False
        if not (directory / MARKER).is_file():
            return False
    except OSError:
        return False
    shutil.rmtree(directory, ignore_errors=True)
    return True


def create_profile(parent=None):
    parent = Path(parent) if parent else parent_directory()
    parent.mkdir(parents=True, exist_ok=True)
    directory = parent / f'{os.getpid()}-{uuid.uuid4().hex[:12]}'
    directory.mkdir()
    (directory / MARKER).write_text(json.dumps({'owner_pid': os.getpid(), 'created': time.time()}),
                                    encoding='utf-8')
    return directory


def sweep_stale(parent=None, *, now=None, alive=pid_alive, skip=()):
    """Remove leftover guest profiles of VEIL processes that no longer exist. Returns removed paths."""
    parent = Path(parent) if parent else parent_directory()
    now = time.time() if now is None else now
    skip = {str(Path(p)) for p in skip}
    removed = []
    try:
        entries = list(parent.iterdir())
    except OSError:
        return removed
    for entry in entries:
        if str(entry) in skip or _is_link(entry) or not entry.is_dir():
            continue
        try:
            info = json.loads((entry / MARKER).read_text(encoding='utf-8'))
            age = now - float(info['created'])
            owner = info['owner_pid']
        except (OSError, ValueError, KeyError, TypeError):
            continue  # no valid marker: not ours to delete
        if (age >= STALE_AFTER_DEAD_OWNER and not alive(owner)) or age >= STALE_ANYWAY:
            if remove_profile(entry, parent):
                removed.append(entry)
    return removed


class GuestProfileWatcher:
    """One thread that removes each guest profile once its browser process has exited."""

    def __init__(self, poll_seconds=POLL_SECONDS, parent=None):
        self.poll_seconds, self.parent = poll_seconds, parent
        self.lock = threading.Lock()
        self.watched = []   # (process, directory)
        self.thread = None

    def watch(self, process, directory):
        with self.lock:
            self.watched.append((process, Path(directory)))
            if self.thread is None or not self.thread.is_alive():
                self.thread = threading.Thread(target=self._run, name='veil-chrome-guest-cleanup', daemon=True)
                self.thread.start()

    def poll_once(self):
        with self.lock:
            snapshot = list(self.watched)
        finished = []
        for process, directory in snapshot:
            try:
                done = process.poll() is not None
            except Exception:
                done = True
            if done:
                remove_profile(directory, self.parent)
                finished.append((process, directory))
        with self.lock:
            self.watched = [item for item in self.watched if item not in finished]
            return len(self.watched)

    def _run(self):
        while True:
            if self.poll_once() == 0:
                with self.lock:
                    if not self.watched:
                        self.thread = None
                        return
            time.sleep(self.poll_seconds)


WATCHER = GuestProfileWatcher()
