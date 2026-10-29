"""Pinned, role-scoped Nowhere MCP runtime configuration."""
from pathlib import Path
import sys


UPSTREAM_REPOSITORY = 'https://github.com/yuyixuanfu/nowhere.git'
UPSTREAM_REVISION = 'f0803c1053e5f97a6f44bab26e15043d71825ec7'
RUNTIME_DIR = Path(__file__).resolve().parent / '.runtime' / 'nowhere' / UPSTREAM_REVISION
SOURCE_DIR = RUNTIME_DIR / 'source'
VENV_DIR = RUNTIME_DIR / 'venv'
PYTHON = VENV_DIR / ('Scripts/python.exe' if sys.platform == 'win32' else 'bin/python')


def service_config():
    return {
        'id': 'nowhere',
        'managed': 'nowhere',
        'command': str(PYTHON),
        'args': ['-m', 'nowhere.server', '--web', '--host', '127.0.0.1'],
        'cwd': str(SOURCE_DIR),
        # Activating this service in the MCP manager is the explicit grant that
        # lets Peter travel without an approval prompt on every autonomous step.
        'enabled': False,
        'direct_grant': False,
        'permission': 'direct',
    }


def service_environment(data_dir):
    return {
        'NOWHERE_HOME': str(Path(data_dir).resolve()),
        'NOWHERE_PUBLIC_URL': 'http://127.0.0.1',
    }
