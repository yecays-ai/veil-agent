import json
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
import tempfile
import unittest
from types import SimpleNamespace

from ombre_entry import RestrictedHTTPApp
from ombre_host import acquire, release
from ombre_resident import (PINNED_OMBRE, ResidentError, _copy_first_backup,
                            _background_python, _environment, resident_config,
                            start_or_connect)
from veil_agent import UserError, load_character
from web_agent import Hub


class OmbreResidentTests(unittest.TestCase):
    def test_windows_background_python_uses_pythonw(self):
        with tempfile.TemporaryDirectory() as directory:
            python = Path(directory) / 'python.exe'
            pythonw = Path(directory) / 'pythonw.exe'
            pythonw.write_bytes(b'fixture')
            self.assertEqual(_background_python(python, windows=True), pythonw)

    def test_mcp_probe_preserves_stages_wrapped_in_exception_groups(self):
        import asyncio
        import time
        from unittest import mock

        from ombre_resident import _mcp_probe
        import mcp
        from mcp.client import streamable_http

        class Stream:
            async def __aenter__(self):
                return object(), object(), None

            async def __aexit__(self, *_args):
                return False

        class Session:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *_args):
                return False

            async def initialize(self):
                if stage == 'cancelled':
                    raise asyncio.CancelledError

            async def list_tools(self):
                if stage == 'mcp_tools':
                    raise ExceptionGroup('fixture wrapper', [ExceptionGroup(
                        'task group', [ResidentError(stage)])])
                return SimpleNamespace(tools=[SimpleNamespace(name=name) for name in
                    ('pulse', 'breath_search', 'hold')])

            async def call_tool(self, *_args, **_kwargs):
                raise ExceptionGroup('fixture wrapper', [ExceptionGroup(
                    'task group', [ResidentError(stage)])])

        for stage in ('mcp_tools', 'readonly_tool', 'cancelled'):
            with self.subTest(stage=stage):
                started = time.monotonic()
                with mock.patch.object(mcp, 'ClientSession', side_effect=lambda *_: Session()), \
                     mock.patch.object(streamable_http, 'streamablehttp_client',
                                        side_effect=lambda *_args, **_kwargs: Stream()):
                    error = asyncio.CancelledError if stage == 'cancelled' else ResidentError
                    with self.assertRaises(error) as failed:
                        _mcp_probe(18913, 'fixture-token')
                if stage != 'cancelled':
                    self.assertEqual(failed.exception.stage, stage)
                self.assertLess(time.monotonic() - started, 2.0)

    def test_adopted_resident_waits_for_old_vault_lock_release(self):
        from unittest import mock

        import ombre_resident

        with tempfile.TemporaryDirectory() as directory:
            private = Path(directory)
            (private / 'ombre.process.json').write_text(json.dumps({
                'port': 18913, 'host_pid': 123, 'server_pid': 456}), encoding='utf-8')
            adopted = ombre_resident.ResidentProcess(None, private, 123,
                restart_args={'start': {'root': private, 'cfg': object()},
                              'probe': (18913, 'fixture-token')})
            replacement = SimpleNamespace(process='new-process', host_pid=789,
                                          stop_path=private / 'ombre.stop')
            with mock.patch('ombre_resident._known_resident', return_value=False), \
                 mock.patch('ombre_resident._pid_alive', return_value=True), \
                 mock.patch('ombre_resident._lock_held', side_effect=[True, True, False]), \
                 mock.patch('ombre_resident.time.sleep'), \
                 mock.patch('ombre_resident.start_or_connect',
                            return_value=(18913, 'fixture-token', replacement)) as start:
                result = adopted.recover()

            self.assertEqual(result['state'], 'restarted')
            self.assertEqual(adopted.process, 'new-process')
            start.assert_called_once()

    def test_open_ombre_accepts_complete_isolated_key_overrides(self):
        from unittest import mock

        from memory_agent import open_ombre

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / '.env').write_text('', encoding='utf-8')
            cfg = SimpleNamespace(id='fixture',
                memory={'base_url': 'https://embed.invalid/v1',
                        'input_cny_per_million': '0', 'embedding_enabled': True,
                        'ombre_model': True},
                provider={'api_key_env': 'CHAT_API_KEY'})
            resident = SimpleNamespace(close=lambda: None)
            with mock.patch('memory_agent.subprocess.run'), \
                 mock.patch('ombre_resident.start_or_connect',
                            return_value=(18913, 'fixture-token', resident)) as start, \
                 mock.patch('ombre_resident.mark_ready'), \
                 mock.patch('memory_agent.MCP', return_value=resident):
                with open_ombre(root, cfg, api_key_overrides={
                        'OMBRE_EMBED_API_KEY': 'fixture-embed',
                        'OMBRE_COMPRESS_API_KEY': 'fixture-chat'}) as backend:
                    self.assertIs(backend, resident)
            self.assertEqual(start.call_args.kwargs['api_key_overrides'], {
                'OMBRE_EMBED_API_KEY': 'fixture-embed',
                'OMBRE_COMPRESS_API_KEY': 'fixture-chat'})

    def test_environment_key_overrides_are_scoped_and_default_keeps_dotenv_keys(self):
        from unittest import mock

        cfg = SimpleNamespace(memory={'ombre_model': True},
            provider={'api_key_env': 'CHAT_API_KEY'})
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            private = root / '_agent'
            private.mkdir()
            config_path = private / 'config.yaml'
            dotenv = {'EMBED_API_KEY': 'fixture-embed-value',
                      'CHAT_API_KEY': 'fixture-chat-value'}
            with mock.patch('dotenv.dotenv_values', return_value=dotenv):
                default_env = _environment(root, cfg, root / 'memory', private,
                    config_path, 'fixture-token', 18913)
                isolated_env = _environment(root, cfg, root / 'memory', private,
                    config_path, 'fixture-token', 18913, api_key_overrides={
                        'OMBRE_COMPRESS_API_KEY': 'proxy-dummy',
                        'OMBRE_EMBED_API_KEY': 'proxy-dummy',
                    })

        self.assertEqual(default_env['OMBRE_COMPRESS_API_KEY'], 'fixture-chat-value')
        self.assertEqual(default_env['OMBRE_EMBED_API_KEY'], 'fixture-embed-value')
        self.assertEqual(isolated_env['OMBRE_COMPRESS_API_KEY'], 'proxy-dummy')
        self.assertEqual(isolated_env['OMBRE_EMBED_API_KEY'], 'proxy-dummy')

    def test_config_requires_local_http_auth_and_disables_side_services(self):
        cfg = SimpleNamespace(id='fixture', name='Example',
            memory={'model': 'fake-embed', 'base_url': 'https://embed.example/v1',
                    'dimensions': 3, 'ombre_model': True},
            provider={'model': 'fake-chat', 'base_url': 'https://chat.example/v1'})
        config = resident_config(cfg, Path('fixture/memory'), 't' * 64)
        self.assertEqual(config['transport'], 'streamable-http')
        self.assertEqual(config['mcp_auth_mode'], 'token')
        self.assertTrue(config['mcp_require_auth'])
        self.assertEqual(config['mcp_token'], 't' * 64)
        self.assertFalse(config['tunnel']['auto_start'])
        self.assertFalse(config['github_sync']['auto_interval_minutes'])
        self.assertFalse(config['update']['allow_pip_install'])
        self.assertFalse(config['embedding']['background_indexing'])

    def test_first_launch_backup_is_complete_and_only_once_after_success_marker(self):
        with tempfile.TemporaryDirectory() as directory:
            vault = Path(directory) / 'data' / 'fixture' / 'memory'
            vault.mkdir(parents=True)
            (vault / 'fictional.md').write_text('synthetic memory', encoding='utf-8')
            private = vault / '_agent'
            _copy_first_backup(vault, private)
            backups = list(vault.parent.glob('memory.backup-*'))
            self.assertEqual(len(backups), 1)
            self.assertEqual((backups[0] / 'fictional.md').read_text(encoding='utf-8'), 'synthetic memory')
            private.mkdir()
            (private / 'ombre-http-ready.json').write_text(
                json.dumps({'version': PINNED_OMBRE}), encoding='utf-8')
            _copy_first_backup(vault, private)
            self.assertEqual(len(list(vault.parent.glob('memory.backup-*'))), 1)

    def test_http_guard_blocks_updates_and_tunnel_but_passes_memory_routes(self):
        self.assertTrue(RestrictedHTTPApp.is_blocked('POST', '/api/do-update'))
        self.assertTrue(RestrictedHTTPApp.is_blocked('POST', '/api/tunnel/start'))
        self.assertTrue(RestrictedHTTPApp.is_blocked('POST', '/api/github/sync'))
        self.assertTrue(RestrictedHTTPApp.is_blocked('POST', '/api/embedding/local/install'))
        self.assertTrue(RestrictedHTTPApp.is_blocked('POST', '/api/config'))
        self.assertTrue(RestrictedHTTPApp.is_blocked('POST', '/api/env-config',
            b'{"updates":{"OMBRE_COMPRESS_MODEL":"external"}}'))
        self.assertTrue(RestrictedHTTPApp.is_blocked('POST', '/api/env-config',
            b'{"updates":{"OMBRE_HOOK_SKIP":"false"}}'))
        self.assertFalse(RestrictedHTTPApp.is_blocked('POST', '/api/env-config',
            b'{"updates":{"OMBRE_DASHBOARD_PASSWORD":"fixture"}}'))
        self.assertFalse(RestrictedHTTPApp.is_blocked('POST', '/api/bucket/one/edit'))
        self.assertFalse(RestrictedHTTPApp.is_blocked('GET', '/api/tunnel/status'))

    def test_per_vault_lock_rejects_second_host(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'ombre.lock'
            with path.open('a+b') as first:
                acquire(first)
                try:
                    with self.assertRaises(OSError):
                        with path.open('a+b') as second:
                            acquire(second)
                finally:
                    release(first)

    def test_healthy_but_unowned_port_is_rejected_without_config_or_process_takeover(self):
        class HealthOnly(BaseHTTPRequestHandler):
            def do_GET(self):
                self.send_response(200)
                self.end_headers()
                self.wfile.write(b'{"status":"ok"}')

            def log_message(self, *_args):
                pass

        server = HTTPServer(('127.0.0.1', 0), HealthOnly)
        import threading
        worker = threading.Thread(target=server.serve_forever, daemon=True)
        worker.start()
        try:
            with tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                installed = root / '.deps' / 'ombre' / 'installed'
                installed.parent.mkdir(parents=True)
                installed.write_text(PINNED_OMBRE, encoding='ascii')
                cfg = SimpleNamespace(id='fixture', name='Fixture',
                    memory={'ombre_port': server.server_port, 'ombre_model': False},
                    provider={'model': 'offline', 'base_url': 'https://offline.invalid/v1',
                              'api_key_env': 'UNUSED'},)
                with self.assertRaises(ResidentError) as failed:
                    start_or_connect(root, cfg, data_root=root / 'isolated-data',
                        api_key_overrides={'OMBRE_EMBED_API_KEY': '',
                                           'OMBRE_COMPRESS_API_KEY': ''})
                self.assertEqual(failed.exception.stage, 'port_identity')
                self.assertFalse((root / 'isolated-data' / 'fixture' / 'memory' /
                                  '_agent' / 'config.yaml').exists())
                self.assertTrue(server.socket.fileno() >= 0)
        finally:
            server.shutdown()
            server.server_close()
            worker.join(timeout=2)

    def test_role_port_is_loaded_and_range_checked(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'characters' / 'fixture').mkdir(parents=True)
            (root / 'persona').mkdir()
            (root / 'persona' / 'fixture.md').write_text('Synthetic persona', encoding='utf-8')
            path = root / 'characters' / 'fixture' / 'character.toml'
            path.write_text('''name = "Fixture"
provider = "fake"
persona = "persona/fixture.md"
[providers.fake]
model = "fake"
base_url = "https://example.invalid/v1"
api_key_env = "FAKE_KEY"
max_input_tokens = 1000
input_cny_per_million = "2"
cached_input_cny_per_million = "1"
output_cny_per_million = "8"
[memory]
enabled = true
ombre_port = 18913
''', encoding='utf-8')
            self.assertEqual(load_character(root, 'fixture').memory['ombre_port'], 18913)
            path.write_text(path.read_text(encoding='utf-8').replace('18913', '70000'), encoding='utf-8')
            with self.assertRaises(UserError):
                load_character(root, 'fixture')

    def test_chat_snapshot_exposes_role_panel_url_without_auth_token(self):
        hub = Hub('Fixture', SimpleNamespace(enabled=lambda: False), 18913)
        state = hub.snapshot(0)
        self.assertEqual(state['memory_panel_url'], 'http://127.0.0.1:18913/dashboard')
        self.assertNotIn('token', state['memory_panel_url'])

    def test_session_errors_keep_their_own_message(self):
        from unittest import mock
        import memory_agent
        root = Path(tempfile.mkdtemp())
        (root / '.env').write_text('EMBED_API_KEY=fake\n', encoding='utf-8')
        cfg = SimpleNamespace(id='fixture', memory={'base_url': 'https://embed.example/v1',
            'input_cny_per_million': '0', 'ombre_model': False})
        closed = SimpleNamespace(close=lambda: None)
        with mock.patch('memory_agent.subprocess.run'), \
             mock.patch('ombre_resident.start_or_connect', return_value=(18011, 't', closed)), \
             mock.patch('ombre_resident.mark_ready'), \
             mock.patch('memory_agent.MCP', return_value=closed):
            with self.assertRaisesRegex(UserError, '预算已用完'):
                with memory_agent.open_ombre(root, cfg):
                    raise UserError('预算已用完')


if __name__ == '__main__':
    unittest.main()
