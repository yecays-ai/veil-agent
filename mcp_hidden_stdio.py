"""Run a stdio MCP command without creating a Windows console."""
import subprocess
import sys

from windows_process import hidden_subprocess_kwargs


if __name__ == '__main__':
    raise SystemExit(subprocess.call(sys.argv[1:], stdin=0, stdout=1, stderr=2,
                                     **hidden_subprocess_kwargs()))
