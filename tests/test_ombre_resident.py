import json
from pathlib import Path
import tempfile
import unittest
from types import SimpleNamespace

from ombre_entry import RestrictedHTTPApp
from ombre_host import acquire, release
from ombre_resident import PINNED_OMBRE, _copy_first_backup, resident_config
from veil_agent import UserError, load_character
from web_agent import Hub


class OmbreResidentTests(unittest.TestCase):
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
