"""Short-lived launcher for the detached local web chat service."""
import ctypes
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import time
from ctypes import wintypes
from urllib.error import URLError
from urllib.request import Request, urlopen
import webbrowser

ROOT = Path(__file__).resolve().parent
ENDPOINT = ROOT / 'data' / 'web_service.json'
STARTUP_ERROR = ROOT / 'data' / 'web_service_error.txt'
FAILED = 'Peter 网页服务没有启动。请检查安装状态后重新双击 start-web.bat。'
STARTUP_WAIT = 25


def report_error(message):
    if os.name == 'nt':
        ctypes.windll.user32.MessageBoxW(None, message, 'VEIL', 0x10)
    else:
        print(message)


class StartupMutex:
    def __enter__(self):
        self.handle = None
        if os.name == 'nt':
            kernel = ctypes.WinDLL('kernel32', use_last_error=True)
            kernel.CreateMutexW.restype = wintypes.HANDLE
            kernel.CreateMutexW.argtypes = (wintypes.LPVOID, wintypes.BOOL, wintypes.LPCWSTR)
            kernel.WaitForSingleObject.restype = wintypes.DWORD
            kernel.WaitForSingleObject.argtypes = (wintypes.HANDLE, wintypes.DWORD)
            kernel.ReleaseMutex.argtypes = (wintypes.HANDLE,)
            kernel.CloseHandle.argtypes = (wintypes.HANDLE,)
            self.handle = kernel.CreateMutexW(None, False, 'Local\\VEILAgentWebStartup')
            result = kernel.WaitForSingleObject(self.handle, 30000)
            if result not in (0, 0x80):
                raise RuntimeError('启动协调超时')
        return self

    def __exit__(self, *_):
        if self.handle:
            kernel = ctypes.WinDLL('kernel32', use_last_error=True)
            kernel.ReleaseMutex(self.handle)
            kernel.CloseHandle(self.handle)


def read_endpoint():
    try:
        value = json.loads(ENDPOINT.read_text(encoding='utf-8'))
        port, token = value.get('port'), value.get('token')
        if not isinstance(port, int) or not 1 <= port <= 65535 or not isinstance(token, str):
            return None
        request = Request(f'http://127.0.0.1:{port}/api/events?after=0',
                          headers={'X-Token': token})
        with urlopen(request, timeout=1) as response:
            state = json.loads(response.read())
        if state.get('stopped') is not False:
            return None
        return f'http://127.0.0.1:{port}/?t={token}'
    except (OSError, ValueError, URLError, AttributeError, TypeError):
        return None


def startup_error():
    # The service writes why it stopped (for example the terminal chat is open).
    try:
        message = STARTUP_ERROR.read_text(encoding='utf-8').strip()
    except (OSError, UnicodeError):
        message = ''
    return f'{FAILED}\n\n{message}' if message else FAILED


def start_service(character, port):
    pythonw = Path(sys.executable).with_name('pythonw.exe')
    executable = str(pythonw if pythonw.exists() else Path(sys.executable))
    flags = getattr(subprocess, 'DETACHED_PROCESS', 0) | getattr(subprocess, 'CREATE_NO_WINDOW', 0)
    return subprocess.Popen([executable, str(ROOT / 'web_agent.py'), '--character', character,
                      '--port', str(port), '--no-browser'],
                     cwd=ROOT, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                     stderr=subprocess.DEVNULL, close_fds=True, creationflags=flags)


def main():
    try:
        parser = argparse.ArgumentParser(description='VEIL 网页聊天启动器')
        parser.add_argument('--character', default='peter')
        parser.add_argument('--port', type=int, default=0)
        parser.add_argument('--no-browser', action='store_true')
        args = parser.parse_args()
        with StartupMutex():
            url = read_endpoint()
            if url is None:
                STARTUP_ERROR.unlink(missing_ok=True)
                process = start_service(args.character, args.port)
                deadline = time.monotonic() + STARTUP_WAIT
                while time.monotonic() < deadline:
                    time.sleep(0.2)
                    url = read_endpoint()
                    if url or process.poll() is not None:
                        break
                if not url:
                    report_error(startup_error())
                    return 1
        if not args.no_browser:
            webbrowser.open(url)
        return 0
    except Exception:
        report_error(FAILED)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())

