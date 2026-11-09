"""Run a stdio MCP command without creating a Windows console."""
import subprocess
import sys

from windows_process import hidden_subprocess_kwargs


def command_for_call(argv, windows=None):
    """`cmd /d /s /c <line>` goes to CreateProcess as one string. As a list, Python would quote the line again and
    escape its inner quotes as \\", which cmd.exe does not understand (a path with spaces then fails to run)."""
    if (sys.platform == 'win32' if windows is None else windows) and len(argv) == 5 and [item.casefold() for item in argv[1:4]] == ['/d', '/s', '/c']:
        return f'"{argv[0]}" /d /s /c "{argv[4]}"'
    return argv


if __name__ == '__main__':
    raise SystemExit(subprocess.call(command_for_call(sys.argv[1:]), stdin=0, stdout=1, stderr=2,
                                     **hidden_subprocess_kwargs()))
