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
import stat
import tempfile
import threading
import time
import uuid
from pathlib import Path

PARENT_NAME = 'veil-chrome-guest'
MARKER = '.veil-owner.json'
POLL_SECONDS = 5.0
STALE_AFTER_DEAD_OWNER = 24 * 60 * 60  # owner process is gone and the folder is at least 1 day old
LEGACY_PREFIX = 'veil-chrome-guest-'   # folders made by earlier versions directly in the temp folder
LEGACY_STALE_AFTER = 7 * 24 * 60 * 60
WATCH_ATTEMPTS = 60                    # removal retries (about 5 minutes) before leaving it to the sweep
STALE_ANYWAY = 7 * 24 * 60 * 60        # a pid can be reused, so also sweep anything over 7 days old


def parent_directory():
    return Path(tempfile.gettempdir()) / PARENT_NAME


def pid_alive(pid):
    if not isinstance(pid, int) or pid <= 0:
        return False
    if os.name == 'nt':
        kernel32 = ctypes.WinDLL('kernel32', use_last_error=True)
        kernel32.OpenProcess.argtypes = (ctypes.c_ulong, ctypes.c_int, ctypes.c_ulong)
        kernel32.OpenProcess.restype = ctypes.c_void_p
        kernel32.GetExitCodeProcess.argtypes = (ctypes.c_void_p, ctypes.POINTER(ctypes.c_ulong))
        kernel32.GetExitCodeProcess.restype = ctypes.c_int
        kernel32.CloseHandle.argtypes = (ctypes.c_void_p,)
        kernel32.CloseHandle.restype = ctypes.c_int
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
    """Symlink or Windows junction/reparse point (also on Python 3.11, which lacks os.path.isjunction)."""
    try:
        info = os.lstat(path)
    except OSError:
        return False
    return (stat.S_ISLNK(info.st_mode)
            or bool(getattr(info, 'st_file_attributes', 0) & getattr(stat, 'FILE_ATTRIBUTE_REPARSE_POINT', 0x400)))


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
    return not directory.exists()


def create_profile(parent=None):
    parent = Path(parent) if parent else parent_directory()
    if _is_link(parent):
        raise OSError('the guest profile folder is a link; refusing to use it')
    parent.mkdir(parents=True, exist_ok=True)
    directory = parent / f'{os.getpid()}-{uuid.uuid4().hex[:12]}'
    directory.mkdir()
    try:
        (directory / MARKER).write_text(json.dumps({'owner_pid': os.getpid(), 'created': time.time()}),
                                        encoding='utf-8')
    except OSError:
        shutil.rmtree(directory, ignore_errors=True)
        raise
    return directory


def sweep_stale(parent=None, *, now=None, alive=pid_alive, skip=()):
    """Remove leftover guest profiles of VEIL processes that no longer exist. Returns removed paths."""
    parent = Path(parent) if parent else parent_directory()
    now = time.time() if now is None else now
    skip = {str(Path(p)) for p in skip}
    removed = []
    if _is_link(parent):
        return removed
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


def sweep_legacy(temp=None, *, now=None, skip=()):
    """One-off cleanup of `veil-chrome-guest-*` folders earlier versions left directly in the temp folder
    (that prefix is only ever made by VEIL). Only real folders untouched for a week are removed."""
    temp = Path(temp) if temp else Path(tempfile.gettempdir())
    now = time.time() if now is None else now
    skip = {str(Path(p)) for p in skip}
    removed = []
    try:
        entries = list(temp.iterdir())
    except OSError:
        return removed
    for entry in entries:
        try:
            if (not entry.name.startswith(LEGACY_PREFIX) or str(entry) in skip or _is_link(entry)
                    or not entry.is_dir() or now - entry.stat().st_mtime < LEGACY_STALE_AFTER):
                continue
        except OSError:
            continue
        shutil.rmtree(entry, ignore_errors=True)
        if not entry.exists():
            removed.append(entry)
    return removed


class GuestProfileWatcher:
    """One thread that removes each guest profile once its browser process has exited."""

    def __init__(self, poll_seconds=POLL_SECONDS, parent=None):
        self.poll_seconds, self.parent = poll_seconds, parent
        self.lock = threading.Lock()
        self.watched = []   # (process, directory)
        self.attempts = {}  # directory -> failed removals
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
                tries = self.attempts.get(directory, 0) + 1
                if remove_profile(directory, self.parent) or tries >= WATCH_ATTEMPTS:
                    self.attempts.pop(directory, None)
                    finished.append((process, directory))  # gone, or left to the next sweep
                else:
                    self.attempts[directory] = tries       # still locked (Chrome shutting down): retry
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
