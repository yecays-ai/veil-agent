"""Hold the per-memory-directory lock while the pinned Ombre server is alive."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import time


def acquire(handle):
    handle.seek(0, os.SEEK_END)
    if handle.tell() == 0:
        handle.write(b'0')
        handle.flush()
    handle.seek(0)
    if os.name == 'nt':
        import msvcrt
        msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
    else:
        import fcntl
        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)


def release(handle):
    handle.seek(0)
    if os.name == 'nt':
        import msvcrt
        msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
    else:
        import fcntl
        fcntl.flock(handle, fcntl.LOCK_UN)


def main():
    parser = argparse.ArgumentParser()
    for name in ('source', 'server', 'entry', 'python', 'private'):
        parser.add_argument('--' + name, required=True)
    parser.add_argument('--port', type=int, required=True)
    args = parser.parse_args()
    private = Path(args.private)
    lock_path = private / 'ombre.lock'
    state_path = private / 'ombre.process.json'
    stop_path = private / 'ombre.stop'
    with lock_path.open('a+b') as lock:
        try:
            acquire(lock)
        except OSError:
            return 2
        child = None
        try:
            stop_path.unlink(missing_ok=True)
            env = dict(os.environ)
            env['PYTHONPATH'] = str(Path(args.source) / 'src')
            env['VEIL_OMBRE_STOP_PATH'] = str(stop_path)
            child = subprocess.Popen([args.python, args.entry, args.server], cwd=private, env=env,
                                     stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                     stderr=subprocess.DEVNULL, close_fds=True,
                                     creationflags=(getattr(subprocess, 'DETACHED_PROCESS', 0)
                                                    | getattr(subprocess, 'CREATE_NO_WINDOW', 0)))
            state_path.write_text(json.dumps({'host_pid': os.getpid(), 'server_pid': child.pid,
                                              'port': args.port}), encoding='utf-8')
            while child.poll() is None:
                if stop_path.exists():
                    try:
                        child.wait(timeout=30)
                    except subprocess.TimeoutExpired:
                        child.terminate()
                        try:
                            child.wait(timeout=5)
                        except subprocess.TimeoutExpired:
                            child.kill()
                            child.wait(timeout=5)
                    break
                time.sleep(0.2)
            return child.returncode or 0
        finally:
            if child is not None and child.poll() is None:
                child.terminate()
                try:
                    child.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    child.kill()
                    child.wait(timeout=5)
            try:
                state = json.loads(state_path.read_text(encoding='utf-8'))
                if state.get('host_pid') == os.getpid():
                    state_path.unlink(missing_ok=True)
            except (OSError, ValueError):
                pass
            release(lock)


if __name__ == '__main__':
    raise SystemExit(main())
