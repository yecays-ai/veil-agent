"""Own the role-local Ombre HTTP process and its single-writer lock."""
import json
import os
from pathlib import Path
import secrets
import shutil
import subprocess
import time
from urllib.error import URLError
from urllib.request import urlopen


PINNED_OMBRE = '115d831d979bf67efc7150f976e2cb34c333c935'
STARTUP_WAIT = 90


def resident_config(cfg, vault, token):
    settings = cfg.memory
    config = {
        'transport': 'streamable-http', 'log_level': 'WARNING',
        'buckets_dir': str(vault), 'ai_name': cfg.name,
        'mcp_require_auth': True, 'mcp_auth_mode': 'token', 'mcp_token': token,
        'github_sync': {'enabled': False, 'repo': '', 'token': '', 'auto_interval_minutes': 0},
        'tunnel': {'enabled': False, 'auto_start': False, 'token': ''},
        'ollama': {'enabled': False, 'auto_start': False},
        'update': {'allow_pip_install': False},
        'hooks': {'token': '', 'allow_public': False},
        'dehydration': ({'model': cfg.provider['model'], 'base_url': cfg.provider['base_url'],
                         'api_key': '', 'max_tokens': 1024, 'temperature': 0.1,
                         'timeout_seconds': 30} if settings.get('ombre_model', True) else {'api_key': ''}),
        'embedding': {'enabled': True, 'background_indexing': False,
                      'backend': 'api', 'model': settings['model'],
                      'base_url': settings['base_url'], 'dim': int(settings.get('dimensions', 1024)),
                      'timeout_seconds': 20},
    }
    if 'deepseek' in cfg.provider['base_url'] and settings.get('ombre_model', True):
        config['dehydration']['extra_body'] = {'thinking': {'type': 'disabled'}}
    return config


def _safe_private(vault):
    private = vault / '_agent'
    if private.is_symlink():
        raise ValueError('private memory directory redirected')
    private.mkdir(parents=True, exist_ok=True)
    if private.is_symlink() or private.resolve() != private:
        raise ValueError('private memory directory redirected')
    return private


def _copy_first_backup(vault, private):
    if private.is_symlink():
        raise ValueError('private memory directory redirected')
    marker = private / 'ombre-http-ready.json'
    if marker.is_symlink():
        raise ValueError('private Ombre marker redirected')
    try:
        if json.loads(marker.read_text(encoding='utf-8')).get('version') == PINNED_OMBRE:
            return
    except (OSError, ValueError, AttributeError):
        pass
    if not vault.exists():
        vault.mkdir(parents=True)
        return
    stamp = time.strftime('%Y%m%dT%H%M%S', time.gmtime()) + f'{time.time_ns() % 1_000_000_000:09d}Z'
    backup = vault.parent / f'memory.backup-{stamp}'
    shutil.copytree(vault, backup, symlinks=True)


def _health(port):
    try:
        with urlopen(f'http://127.0.0.1:{port}/health', timeout=0.5) as response:
            return response.status == 200
    except (OSError, URLError, ValueError):
        return False


def _environment(root, cfg, vault, private, config_path, token, port, api_key_overrides=None):
    from dotenv import dotenv_values

    raw = dotenv_values(Path(root) / '.env', interpolate=False, encoding='utf-8-sig')
    env = {key: value for key, value in os.environ.items()
           if key.upper() in {'PATH', 'SYSTEMROOT', 'WINDIR', 'TEMP', 'TMP', 'USERPROFILE'}}
    temp = private / 'tmp'
    temp.mkdir(exist_ok=True)
    api_key_overrides = api_key_overrides or {}
    env.update(
        PYTHONUTF8='1', PYTHONIOENCODING='utf-8',
        OMBRE_CONFIG_PATH=str(config_path), OMBRE_VAULT_DIR=str(vault),
        OMBRE_LOG_DIR=str(private), OMBRE_TRANSPORT='streamable-http',
        OMBRE_PORT=str(port), OMBRE_BIND_HOST='127.0.0.1',
        OMBRE_MCP_REQUIRE_AUTH='true', OMBRE_MCP_AUTH_MODE='token',
        OMBRE_MCP_TOKEN=token, OMBRE_HOOK_SKIP='true',
        VEIL_OMBRE_STOP_PATH=str(private / 'ombre.stop'),
        OMBRE_EMBED_API_KEY=(api_key_overrides.get('OMBRE_EMBED_API_KEY',
                                                   raw.get('EMBED_API_KEY')) or '').strip(),
        TEMP=str(temp), TMP=str(temp),
    )
    if cfg.memory.get('ombre_model', True):
        env['OMBRE_COMPRESS_API_KEY'] = (api_key_overrides.get(
            'OMBRE_COMPRESS_API_KEY', raw.get(cfg.provider['api_key_env'])) or '').strip()
    else:
        env['OMBRE_COMPRESS_API_KEY'] = ''
    return env


class ResidentProcess:
    def __init__(self, process, private, host_pid=None):
        self.process = process
        self.private = private
        self.host_pid = host_pid
        self.stop_path = private / 'ombre.stop'

    def close(self):
        if self.process is not None and self.process.poll() is not None:
            return
        state_path = self.private / 'ombre.process.json'
        try:
            state = json.loads(state_path.read_text(encoding='utf-8'))
        except (OSError, ValueError):
            state = {}
        expected_pid = self.process.pid if self.process is not None else self.host_pid
        if not expected_pid:
            return
        owns_state = state.get('host_pid') == expected_pid
        if self.process is None and not owns_state:
            return
        if self.process is not None and not owns_state:
            if os.name == 'nt':
                subprocess.run(['taskkill', '/PID', str(expected_pid), '/T', '/F'],
                               capture_output=True, timeout=5)
            else:
                self.process.terminate()
                self.process.wait(timeout=5)
            return
        self.stop_path.touch()
        if self.process is not None:
            try:
                self.process.wait(timeout=35)
            except subprocess.TimeoutExpired:
                self.process.terminate()
                try:
                    self.process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    self.process.kill()
                    self.process.wait(timeout=5)
        else:
            deadline = time.monotonic() + 35
            while time.monotonic() < deadline:
                try:
                    current = json.loads(state_path.read_text(encoding='utf-8'))
                except (OSError, ValueError):
                    break
                if current.get('host_pid') != expected_pid:
                    break
                time.sleep(0.1)
            else:
                if os.name == 'nt':
                    subprocess.run(['taskkill', '/PID', str(expected_pid), '/T', '/F'],
                                   capture_output=True, timeout=5)
                else:
                    os.kill(expected_pid, 15)
        self.stop_path.unlink(missing_ok=True)


def start_or_connect(root, cfg, *, data_root=None, api_key_overrides=None):
    """Return (port, token, owned_process), starting Ombre only when needed."""
    root = Path(root).resolve()
    vault = (Path(data_root) if data_root is not None else root / 'data') / cfg.id / 'memory'
    vault = vault.resolve()
    role_root = vault.parent.resolve()
    if vault != role_root / 'memory' or not vault.is_relative_to(role_root):
        raise ValueError('memory directory redirected')
    private = vault / '_agent'
    _copy_first_backup(vault, private)
    private = _safe_private(vault)
    dep = root / '.deps' / 'ombre'
    installed = (dep / 'installed').read_text(encoding='ascii').strip()
    if installed != PINNED_OMBRE:
        raise RuntimeError('pinned Ombre is not installed')
    source = dep / 'source'
    server = source / 'src' / 'server.py'
    entry = root / 'ombre_entry.py'
    python = dep / 'venv' / ('Scripts/python.exe' if os.name == 'nt' else 'bin/python')
    port = cfg.memory['ombre_port']
    config_path = private / 'config.yaml'
    token_path = private / 'mcp-token'
    for protected in (config_path, token_path, private / 'process.log',
                      private / 'ombre.process.json', private / 'ombre.stop',
                      private / 'ombre.lock'):
        if protected.is_symlink():
            raise ValueError('private Ombre file redirected')
    try:
        token = token_path.read_text(encoding='ascii').strip()
    except OSError:
        token = ''
    if len(token) < 40:
        token = secrets.token_urlsafe(48)
        token_path.write_text(token, encoding='ascii')
    config = resident_config(cfg, vault, token)
    config_temp = config_path.with_suffix('.yaml.tmp')
    if config_temp.is_symlink():
        raise ValueError('private Ombre file redirected')
    config_temp.write_text(json.dumps(config, ensure_ascii=False, indent=2), encoding='utf-8')
    os.replace(config_temp, config_path)
    env = _environment(root, cfg, vault, private, config_path, token, port,
                       api_key_overrides=api_key_overrides)

    owned = None
    if not _health(port):
        log_path = private / 'process.log'
        with log_path.open('a', encoding='utf-8') as log:
            flags = getattr(subprocess, 'DETACHED_PROCESS', 0) | getattr(subprocess, 'CREATE_NO_WINDOW', 0)
            owned = subprocess.Popen(
                [str(python), str(root / 'ombre_host.py'), '--source', str(source),
                 '--server', str(server), '--entry', str(entry), '--python', str(python),
                 '--private', str(private), '--port', str(port)],
                cwd=private, env=env, stdin=subprocess.DEVNULL, stdout=log, stderr=log,
                close_fds=True, creationflags=flags)
        deadline = time.monotonic() + STARTUP_WAIT
        while time.monotonic() < deadline:
            if _health(port):
                break
            if owned.poll() is not None:
                raise RuntimeError('Ombre resident process failed to start')
            time.sleep(0.2)
        else:
            ResidentProcess(owned, private).close()
            raise RuntimeError('Ombre resident process startup timed out')
    host_pid = None
    state_path = private / 'ombre.process.json'
    state_deadline = time.monotonic() + 2
    while time.monotonic() < state_deadline:
        try:
            state = json.loads(state_path.read_text(encoding='utf-8'))
            host_pid = state.get('host_pid')
            if host_pid:
                break
        except (OSError, ValueError):
            pass
        time.sleep(0.05)
    return port, token, ResidentProcess(owned, private, host_pid)


def mark_ready(private):
    marker = Path(private) / 'ombre-http-ready.json'
    if marker.is_symlink():
        raise ValueError('private Ombre marker redirected')
    temporary = marker.with_suffix('.json.tmp')
    temporary.write_text(json.dumps({'transport': 'streamable-http', 'version': PINNED_OMBRE}),
                         encoding='utf-8')
    os.replace(temporary, marker)
