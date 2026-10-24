"""Install the official fetch MCP server in an isolated, pinned environment."""
import hashlib
import os
from pathlib import Path
import subprocess
import sys
import venv


VERSION = '2026.10.19'
ROOT = Path(__file__).resolve().parent


def main():
    if sys.version_info[:2] not in {(3, 11), (3, 12)}:
        raise RuntimeError('Python 3.11 or 3.12 is required.')
    dep = ROOT / '.deps' / 'fetch'
    dep.mkdir(parents=True, exist_ok=True)
    requirements = ROOT / 'requirements.fetch.lock.txt'
    marker = dep / 'installed'
    marker_value = VERSION + ':' + hashlib.sha256(requirements.read_bytes()).hexdigest()
    environment = dep / 'venv'
    python = environment / ('Scripts/python.exe' if os.name == 'nt' else 'bin/python')
    if marker.exists() and marker.read_text(encoding='utf-8') == marker_value:
        try:
            check = subprocess.run(
                [str(python), '-c',
                 'import importlib.metadata, mcp_server_fetch; '
                 'assert importlib.metadata.version("mcp-server-fetch") == "' + VERSION + '"'],
                capture_output=True, timeout=20)
            if check.returncode == 0:
                print('Official fetch MCP server is already installed at the pinned version.')
                return
        except (OSError, subprocess.SubprocessError):
            pass
    if not environment.exists():
        venv.EnvBuilder(with_pip=True).create(environment)
    subprocess.run([str(python), '-m', 'pip', 'install', '--no-cache-dir',
                    '-r', str(requirements)], check=True)
    subprocess.run([str(python), '-c',
                    'import importlib.metadata, mcp_server_fetch; '
                    'assert importlib.metadata.version("mcp-server-fetch") == "' + VERSION + '"'],
                   check=True)
    temporary = marker.with_suffix('.tmp')
    temporary.write_text(marker_value, encoding='utf-8')
    os.replace(temporary, marker)
    print('Pinned official fetch MCP server installed in its isolated environment.')


if __name__ == '__main__':
    main()
