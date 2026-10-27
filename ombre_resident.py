"""Own the role-local Ombre HTTP process and its single-writer lock."""
import json
import os
from pathlib import Path
import secrets
import shutil
import socket
import subprocess
import time
from urllib.error import URLError
from urllib.request import urlopen


PINNED_OMBRE = '115d831d979bf67efc7150f976e2cb34c333c935'
STARTUP_WAIT = 90
MCP_PROBE_WAIT = 15
RESIDENT_CLEANUP_WAIT = 5


class ResidentError(RuntimeError):
    def __init__(self, stage):
        self.stage = stage
        super().__init__(stage)


def _model_provider(cfg):
    """Ticket 134: Ombre's tagging uses the background model Lan picked, not Peter's chat model."""
    return getattr(cfg, 'background_provider', None) or cfg.provider


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
        'dehydration': ({'model': _model_provider(cfg)['model'], 'base_url': _model_provider(cfg)['base_url'],
                         'api_key': '', 'max_tokens': 1024, 'temperature': 0.1,
                         'timeout_seconds': 30} if settings.get('ombre_model', True) else {'api_key': ''}),
        'embedding': {'enabled': bool(settings.get('embedding_enabled', True)),
                      'background_indexing': False,
                      'backend': 'api', 'model': settings['model'],
                      'base_url': settings['base_url'], 'dim': int(settings.get('dimensions', 1024)),
                      'timeout_seconds': 20},
    }
    if 'deepseek' in _model_provider(cfg)['base_url'] and settings.get('ombre_model', True):
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


def _port_open(port):
    try:
        with socket.create_connection(('127.0.0.1', port), timeout=0.3):
            return True
    except OSError:
        return False


def _pid_alive(pid):
    if os.name == 'nt':
        import ctypes
        try:
            process_id = int(pid)
            kernel32 = ctypes.WinDLL('kernel32', use_last_error=True)
            kernel32.OpenProcess.argtypes = (ctypes.c_ulong, ctypes.c_int, ctypes.c_ulong)
            kernel32.OpenProcess.restype = ctypes.c_void_p
            kernel32.CloseHandle.argtypes = (ctypes.c_void_p,)
            kernel32.GetExitCodeProcess.argtypes = (ctypes.c_void_p,
                                                    ctypes.POINTER(ctypes.c_ulong))
            kernel32.GetExitCodeProcess.restype = ctypes.c_int
            handle = kernel32.OpenProcess(0x1000, 0, process_id)  # QUERY_LIMITED_INFORMATION
            if handle:
                code = ctypes.c_ulong()
                try:
                    return bool(kernel32.GetExitCodeProcess(handle, ctypes.byref(code))
                                and code.value == 259)  # STILL_ACTIVE
                finally:
                    kernel32.CloseHandle(handle)
            return False
        except (OSError, TypeError, ValueError):
            return False
    try:
        os.kill(int(pid), 0)
        return True
    except (OSError, TypeError, ValueError):
        return False


def _lock_held(path):
    try:
        handle = Path(path).open('a+b')
    except OSError:
        return False
    with handle:
        try:
            from ombre_host import acquire, release
            acquire(handle)
        except OSError:
            return True
        release(handle)
        return False


def _mcp_probe(port, token):
    """Authenticate and exercise a local, read-only Ombre tool; never trust /health alone."""
    import asyncio
    from mcp import ClientSession
    from mcp.client.streamable_http import streamablehttp_client

    async def probe():
        try:
            async with asyncio.timeout(MCP_PROBE_WAIT):
                async with streamablehttp_client(
                        f'http://127.0.0.1:{port}/mcp',
                        headers={'Authorization': f'Bearer {token}'}) as (read, write, _):
                    async with ClientSession(read, write) as session:
                        await session.initialize()
                        names = {item.name for item in (await session.list_tools()).tools}
                        if not {'pulse', 'breath_search', 'hold'} <= names:
                            raise ResidentError('mcp_tools')
                        response = await session.call_tool('pulse', {'include_archive': False})
                        if response.isError or not any(
                                getattr(item, 'text', '').strip() for item in response.content):
                            raise ResidentError('readonly_tool')
        except ResidentError:
            raise
        except Exception as exc:
            # Preserve only a safe stage; callers must never log transport text or headers.
            pending, names, statuses = [exc], set(), set()
            resident_stage = None
            while pending:
                error = pending.pop()
                if isinstance(error, ResidentError) and resident_stage is None:
                    resident_stage = error.stage
                names.add(type(error).__name__)
                response = getattr(error, 'response', None)
                if response is not None:
                    statuses.add(getattr(response, 'status_code', None))
                pending.extend(getattr(error, 'exceptions', ()))
                cause = getattr(error, '__cause__', None)
                if cause is not None:
                    pending.append(cause)
            stage = (resident_stage if resident_stage is not None else
                     'mcp_auth' if statuses & {401, 403} else
                     'mcp_http' if 'HTTPStatusError' in names else 'mcp_init')
            raise ResidentError(stage) from exc

    asyncio.run(probe())


def _known_resident(private, port):
    try:
        state = json.loads((private / 'ombre.process.json').read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return False
    return (state.get('port') == port and _pid_alive(state.get('host_pid'))
            and _pid_alive(state.get('server_pid')) and _lock_held(private / 'ombre.lock'))


def _environment(root, cfg, vault, private, config_path, token, port, api_key_overrides=None):
    from dotenv import dotenv_values

    api_key_overrides = api_key_overrides or {}
    if {'OMBRE_EMBED_API_KEY', 'OMBRE_COMPRESS_API_KEY'} <= api_key_overrides.keys():
        raw = {}
    else:
        raw = dotenv_values(Path(root) / '.env', interpolate=False, encoding='utf-8-sig')
    env = {key: value for key, value in os.environ.items()
           if key.upper() in {'PATH', 'SYSTEMROOT', 'WINDIR', 'TEMP', 'TMP', 'USERPROFILE'}}
    temp = private / 'tmp'
    temp.mkdir(exist_ok=True)
    env.update(
        PYTHONUTF8='1', PYTHONIOENCODING='utf-8',
        OMBRE_CONFIG_PATH=str(config_path), OMBRE_VAULT_DIR=str(vault),
        OMBRE_LOG_DIR=str(private), OMBRE_TRANSPORT='streamable-http',
        OMBRE_PORT=str(port), OMBRE_BIND_HOST='127.0.0.1',
        OMBRE_MCP_REQUIRE_AUTH='true', OMBRE_MCP_AUTH_MODE='token',
        OMBRE_MCP_TOKEN=token, OMBRE_HOOK_SKIP='true',
        VEIL_OMBRE_STOP_PATH=str(private / 'ombre.stop'),
        OMBRE_EMBED_API_KEY=(api_key_overrides['OMBRE_EMBED_API_KEY']
                             if 'OMBRE_EMBED_API_KEY' in api_key_overrides
                             else raw.get('EMBED_API_KEY') or '').strip(),
        TEMP=str(temp), TMP=str(temp),
    )
    if cfg.memory.get('ombre_model', True):
        env['OMBRE_COMPRESS_API_KEY'] = (api_key_overrides['OMBRE_COMPRESS_API_KEY']
            if 'OMBRE_COMPRESS_API_KEY' in api_key_overrides
            else raw.get(_model_provider(cfg)['api_key_env']) or '').strip()
    else:
        env['OMBRE_COMPRESS_API_KEY'] = ''
    return env


class ResidentProcess:
    def __init__(self, process, private, host_pid=None, *, restart_args=None):
        self.process = process
        self.private = private
        self.host_pid = host_pid
        self.stop_path = private / 'ombre.stop'
        self.restart_args = restart_args
        self.recovery_lock = __import__('threading').Lock()
        self.closing = False
        self.recovery_count = 0

    def recover(self):
        """Restart only our still-owned host; never adopt or kill an unknown listener."""
        with self.recovery_lock:
            if self.closing or not self.restart_args:
                raise ResidentError('not_owned')
            if self.recovery_count >= 2:
                raise ResidentError('recovery_limit')
            self.recovery_count += 1
            exit_code = None
            if self.process is not None and self.process.poll() is None:
                # A failed MCP call can be transient. Verify the service before restarting it.
                try:
                    _mcp_probe(*self.restart_args['probe'])
                    return {'exit_code': None, 'state': 'already_healthy'}
                except ResidentError:
                    pass
                exit_code = self._stop_owned()
            elif self.process is not None:
                exit_code = self.process.poll()
                self.process = None
            elif _known_resident(self.private, self.restart_args['probe'][0]):
                try:
                    _mcp_probe(*self.restart_args['probe'])
                    return {'exit_code': None, 'state': 'already_healthy'}
                except ResidentError:
                    exit_code = self._stop_owned()
            elif self._wait_for_adopted_host(self.restart_args['probe'][0]):
                return {'exit_code': None, 'state': 'already_healthy'}
            port, token, replacement = start_or_connect(**self.restart_args['start'])
            self.process, self.host_pid = replacement.process, replacement.host_pid
            self.stop_path = replacement.stop_path
            return {'exit_code': exit_code, 'state': 'restarted'}

    def _wait_for_adopted_host(self, port):
        """Wait briefly for this adopted host to release its vault lock before replacement."""
        if not self.host_pid:
            return False
        state_path = self.private / 'ombre.process.json'
        lock_path = self.private / 'ombre.lock'

        def owns_lock():
            try:
                state = json.loads(state_path.read_text(encoding='utf-8'))
            except (OSError, ValueError):
                return False
            return (state.get('port') == port and state.get('host_pid') == self.host_pid
                    and _pid_alive(self.host_pid) and _lock_held(lock_path))

        if not owns_lock():
            return False
        deadline = time.monotonic() + RESIDENT_CLEANUP_WAIT
        while owns_lock():
            if _known_resident(self.private, port):
                try:
                    _mcp_probe(*self.restart_args['probe'])
                    return True
                except ResidentError:
                    pass
            if time.monotonic() >= deadline:
                raise ResidentError('resident_cleanup')
            time.sleep(0.1)
        return False


    def _stop_owned(self):
        if self.process is not None and self.process.poll() is not None:
            exit_code = self.process.returncode
            self.process = None
            return exit_code
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
            try:
                self.process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait(timeout=5)
            exit_code = self.process.returncode
            self.process = None
            return exit_code
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
        exit_code = self.process.returncode if self.process is not None else None
        self.process = None
        return exit_code

    def close(self):
        self.closing = True
        self._stop_owned()


def start_or_connect(root, cfg, *, data_root=None, api_key_overrides=None,
                     startup_wait=STARTUP_WAIT):
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
    # setup_memory.py records the pin followed by the exact dependency-lock digest.
    if installed != PINNED_OMBRE and not installed.startswith(PINNED_OMBRE + ':'):
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
    start_args = {'root': root, 'cfg': cfg, 'data_root': data_root,
                  'api_key_overrides': api_key_overrides, 'startup_wait': 30}
    if _health(port):
        if not _known_resident(private, port):
            raise ResidentError('port_identity')
        _mcp_probe(port, token)
        return port, token, ResidentProcess(None, private,
            json.loads((private / 'ombre.process.json').read_text(encoding='utf-8')).get('host_pid'),
            restart_args={'start': start_args, 'probe': (port, token)})
    if _port_open(port):
        raise ResidentError('port_identity')
    config = resident_config(cfg, vault, token)
    config_temp = config_path.with_suffix('.yaml.tmp')
    if config_temp.is_symlink():
        raise ValueError('private Ombre file redirected')
    config_temp.write_text(json.dumps(config, ensure_ascii=False, indent=2), encoding='utf-8')
    os.replace(config_temp, config_path)
    env = _environment(root, cfg, vault, private, config_path, token, port,
                       api_key_overrides=api_key_overrides)

    owned = None
    log_path = private / 'process.log'
    with log_path.open('a', encoding='utf-8') as log:
        flags = getattr(subprocess, 'DETACHED_PROCESS', 0) | getattr(subprocess, 'CREATE_NO_WINDOW', 0)
        owned = subprocess.Popen(
            [str(python), str(root / 'ombre_host.py'), '--source', str(source),
             '--server', str(server), '--entry', str(entry), '--python', str(python),
             '--private', str(private), '--port', str(port)],
            cwd=private, env=env, stdin=subprocess.DEVNULL, stdout=log, stderr=log,
            close_fds=True, creationflags=flags)
    deadline = time.monotonic() + startup_wait
    saw_http = False
    while time.monotonic() < deadline:
        if _health(port):
            saw_http = True
            try:
                _mcp_probe(port, token)
                break
            except ResidentError as exc:
                if exc.stage in {'mcp_auth', 'mcp_tools', 'readonly_tool'}:
                    ResidentProcess(owned, private).close()
                    raise
                if owned.poll() is not None:
                    raise
        if owned.poll() is not None:
            raise ResidentError('process_exit')
        time.sleep(0.2)
    else:
        ResidentProcess(owned, private).close()
        raise ResidentError('mcp_init' if saw_http else 'http_unavailable')
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
    resident = ResidentProcess(owned, private, host_pid,
        restart_args={'start': start_args, 'probe': (port, token)})
    return port, token, resident


def mark_ready(private):
    marker = Path(private) / 'ombre-http-ready.json'
    if marker.is_symlink():
        raise ValueError('private Ombre marker redirected')
    temporary = marker.with_suffix('.json.tmp')
    temporary.write_text(json.dumps({'transport': 'streamable-http', 'version': PINNED_OMBRE}),
                         encoding='utf-8')
    os.replace(temporary, marker)
