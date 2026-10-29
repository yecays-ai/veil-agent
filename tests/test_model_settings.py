import json
import tempfile
import unittest
from decimal import Decimal
from pathlib import Path
from unittest.mock import Mock, patch

from model_settings import ModelSettings, SettingsError
from veil_agent import Budget, Chat, Store, background_cfg, load_character, make_model

TOML = '''name = "Test"
provider = "fake"
persona = "persona/test.md"
history_rounds = 20
max_output_tokens = 50
[providers.fake]
model = "fake-model"
base_url = "https://example.invalid/v1"
api_key_env = "FAKE_KEY"
max_input_tokens = 6000
input_cny_per_million = "2"
cached_input_cny_per_million = "1"
output_cny_per_million = "8"
[providers.other]
model = "other-model"
base_url = "https://other.invalid/v1"
api_key_env = "OTHER_KEY"
max_input_tokens = 6000
input_cny_per_million = "4"
cached_input_cny_per_million = "1"
output_cny_per_million = "16"
'''

RELAY = {'endpoints': [{'name': 'relay', 'base_url': 'https://relay.invalid/v1/', 'key': 'sk-fake-relay-1234'}],
         'models': [], 'limits': {'daily_cny': '30', 'monthly_cny': '600'}}


class ModelSettingsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        (self.root / 'characters' / 'test').mkdir(parents=True)
        (self.root / 'persona').mkdir()
        (self.root / 'persona' / 'test.md').write_text('Fictional test persona', encoding='utf-8')
        (self.root / 'characters' / 'test' / 'character.toml').write_text(TOML, encoding='utf-8')
        (self.root / '.env').write_text('FAKE_KEY=fake\n', encoding='utf-8')

    def add_relay(self, per_call=True):
        cfg = load_character(self.root, 'test')
        view = cfg.models.save(RELAY)
        endpoint = view['endpoints'][0]['id']
        model = {'endpoint': endpoint, 'model': '[k]fake-sonnet', 'label': 'Sonnet', 'thinking': True,
                 'max_input_tokens': 64000}
        model.update({'billing': 'call', 'cny_per_call': '0.06'} if per_call else
                     {'billing': 'tokens', 'input_cny_per_million': '20', 'output_cny_per_million': '100'})
        payload = {**RELAY, 'endpoints': [{k: v for k, v in view['endpoints'][0].items()
                                           if k in ('id', 'name', 'base_url')}], 'models': [model]}
        view = cfg.models.save(payload)
        return cfg, next(m['id'] for m in view['models'] if not m['builtin'])

    def test_key_goes_to_env_only_and_is_masked(self):
        cfg, _ = self.add_relay()
        saved = (self.root / 'data' / 'test' / 'models.json').read_text(encoding='utf-8')
        self.assertNotIn('sk-fake-relay-1234', saved)
        env = (self.root / '.env').read_text(encoding='utf-8')
        self.assertIn('sk-fake-relay-1234', env)
        self.assertIn('FAKE_KEY=fake', env)
        view = cfg.models.public()
        self.assertNotIn('sk-fake-relay-1234', json.dumps(view))
        self.assertEqual(view['endpoints'][0]['key_hint'], '…1234')
        self.assertEqual(view['endpoints'][0]['base_url'], 'https://relay.invalid/v1')

    def test_new_endpoint_needs_key_and_https(self):
        cfg = load_character(self.root, 'test')
        with self.assertRaises(SettingsError):
            cfg.models.save({'endpoints': [{'name': 'x', 'base_url': 'https://x.invalid/v1'}]})
        with self.assertRaises(SettingsError):
            cfg.models.save({'endpoints': [{'name': 'x', 'base_url': 'http://x.invalid/v1', 'key': 'sk-1'}]})

    def test_defaults_without_local_file(self):
        cfg = load_character(self.root, 'test')
        self.assertEqual((cfg.provider_name, cfg.provider['model']), ('fake', 'fake-model'))
        self.assertEqual(cfg.background_provider['model'], 'fake-model')
        self.assertEqual(cfg.models.limits(), (Decimal('10'), Decimal('300')))

    def test_peter_and_background_pick_separately_and_survive_restart(self):
        cfg, model_id = self.add_relay()
        cfg.models.use('peter', model_id)
        cfg.models.use('background', 'builtin:other')
        cfg = load_character(self.root, 'test')
        self.assertEqual((cfg.provider_name, cfg.provider['model']), ('relay', '[k]fake-sonnet'))
        self.assertEqual(cfg.provider['max_input_tokens'], 64000)
        self.assertEqual(cfg.background_provider['model'], 'other-model')
        self.assertEqual(background_cfg(cfg).provider['model'], 'other-model')
        self.assertEqual(cfg.models.limits(), (Decimal('30'), Decimal('600')))

    def test_deleted_model_falls_back_to_character_default(self):
        cfg, model_id = self.add_relay()
        cfg.models.use('peter', model_id)
        cfg.models.save({'endpoints': [], 'models': [], 'limits': {}})
        self.assertNotIn('VEIL_KEY_', (self.root / '.env').read_text(encoding='utf-8'))
        cfg = load_character(self.root, 'test')
        self.assertEqual(cfg.provider['model'], 'fake-model')

    def test_per_call_price_is_charged_flat(self):
        cfg, model_id = self.add_relay()
        cfg.models.use('peter', model_id)
        cfg = load_character(self.root, 'test')
        store = Store(self.root / 'data')
        budget = Budget(store)
        model = Mock(return_value=('A fictional reply', 30000, 40, 0))
        Chat(cfg, store, budget, model).send('虚构消息')
        settles = [row for row in store.read('costs.jsonl') if row['type'] == 'settle']
        self.assertEqual([Decimal(row['amount']) for row in settles], [Decimal('0.06')])
        self.assertIn('[k]fake-sonnet ¥0.0600（1 次）', budget.model_display())

    def test_switch_applies_on_next_request_and_hits_the_relay(self):
        cfg, model_id = self.add_relay()
        requests = []

        import httpx
        from openai import OpenAI

        def transport(request):
            requests.append((str(request.url), request.headers['authorization'], json.loads(request.content)))
            return httpx.Response(200, json={
                'id': 'fake', 'object': 'chat.completion', 'created': 0, 'model': 'm',
                'choices': [{'index': 0, 'finish_reason': 'stop',
                             'message': {'role': 'assistant', 'content': '模拟回复'}}],
                'usage': {'prompt_tokens': 100, 'completion_tokens': 20, 'total_tokens': 120}})

        def factory(**kwargs):
            return OpenAI(**kwargs, http_client=httpx.Client(transport=httpx.MockTransport(transport)))

        store = Store(self.root / 'data')
        with patch('openai.OpenAI', side_effect=factory):
            clients, model = make_model(self.root, cfg)
            with clients:
                chat = Chat(cfg, store, Budget(store), model)
                chat.send('第一句')
                first_settlement = [row for row in store.read('costs.jsonl')
                                    if row.get('type') == 'settle' and 'prompt_tokens' in row][-1]
                self.assertIs(first_settlement['cache_reported'], False)
                _, name, provider = cfg.models.use('peter', model_id)
                chat.switch_model(name, provider)
                chat.send('第二句')
        self.assertTrue(requests[0][0].startswith('https://example.invalid/v1/'))
        self.assertEqual(requests[0][1], 'Bearer fake')
        self.assertTrue(requests[1][0].startswith('https://relay.invalid/v1/'))
        self.assertEqual(requests[1][1], 'Bearer sk-fake-relay-1234')
        self.assertEqual(requests[1][2]['model'], '[k]fake-sonnet')
        self.assertNotIn('thinking', requests[1][2])
        self.assertIn('model "[k]fake-sonnet"', requests[1][2]['messages'][1]['content'])
        models = [row['model'] for row in store.read('costs.jsonl') if row['type'] == 'reserve']
        self.assertEqual(models, ['fake-model', '[k]fake-sonnet'])

    def test_limits_are_validated(self):
        cfg = load_character(self.root, 'test')
        with self.assertRaises(SettingsError):
            cfg.models.save({'limits': {'daily_cny': '50', 'monthly_cny': '20'}})
        with self.assertRaises(SettingsError):
            cfg.models.save({'limits': {'daily_cny': 'abc'}})


    def test_settings_page_requests_save_switch_and_never_return_the_key(self):
        import socket
        import urllib.error
        import urllib.request
        from veil_agent import ActivityState
        from web_agent import Hub, start_server
        cfg = load_character(self.root, 'test')
        store = Store(self.root / 'data')
        hub = Hub('Test', ActivityState(store.root / 'test' / 'resident-state.json'),
                  media_root=store.root / 'test' / 'media')
        hub.chat = Chat(cfg, store, Budget(store), Mock(return_value=('A fictional reply', 100, 20, 10)))
        hub.background_running = cfg.models.chosen('background')[0]
        hub.refresh_models()
        with socket.socket() as sock:
            sock.bind(('127.0.0.1', 0))
            port = sock.getsockname()[1]
        server = start_server(hub, 'fake-token', port)
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)

        def call(path, body=None):
            request = urllib.request.Request(f'http://127.0.0.1:{port}{path}',
                headers={'X-Token': 'fake-token', 'Content-Type': 'application/json'},
                data=None if body is None else json.dumps(body).encode())
            try:
                with urllib.request.urlopen(request, timeout=5) as response:
                    return response.status, json.loads(response.read())
            except urllib.error.HTTPError as exc:
                return exc.code, json.loads(exc.read() or b'{}')

        status, data = call('/api/models', {'endpoints': [{'name': 'relay', 'base_url': 'ftp://x'}]})
        self.assertEqual(status, 400)
        self.assertIn('https://', data['message'])
        status, data = call('/api/models', RELAY)
        self.assertEqual(status, 200)
        endpoint = data['models']['endpoints'][0]['id']
        status, data = call('/api/models', {
            'endpoints': [{'id': endpoint, 'name': 'relay', 'base_url': 'https://relay.invalid/v1'}],
            'models': [{'endpoint': endpoint, 'model': '[k]fake-sonnet', 'billing': 'call', 'cny_per_call': '0.06'}],
            'limits': {'daily_cny': '30', 'monthly_cny': '600'}})
        self.assertEqual(status, 200)
        self.assertEqual((hub.chat.budget.daily, hub.chat.budget.monthly), (Decimal('30'), Decimal('600')))
        model_id = next(m['id'] for m in data['models']['models'] if not m['builtin'])
        status, data = call('/api/models/use', {'role': 'peter', 'model': model_id})
        self.assertEqual((status, data['models']['use']['peter']), (200, model_id))
        self.assertEqual(hub.chat.pending_model[1]['model'], '[k]fake-sonnet')
        status, data = call('/api/models/use', {'role': 'background', 'model': model_id})
        self.assertTrue(data['models']['background_restart'])
        status, events = call('/api/events?after=0')
        self.assertEqual(events['models']['use']['peter'], model_id)
        self.assertNotIn('sk-fake-relay-1234', json.dumps(events))


if __name__ == '__main__':
    unittest.main()


class ProviderErrorTests(unittest.TestCase):
    def test_failure_says_status_and_logs_redacted_detail(self):
        import tempfile as _tempfile
        from agent_types import UserError
        tmp = _tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        (root / 'characters' / 'test').mkdir(parents=True)
        (root / 'persona').mkdir()
        (root / 'persona' / 'test.md').write_text('Fictional', encoding='utf-8')
        (root / 'characters' / 'test' / 'character.toml').write_text(TOML, encoding='utf-8')
        cfg = load_character(root, 'test')
        store = Store(root / 'data')

        class FakeStatusError(Exception):
            status_code = 401
            message = 'Invalid token sk-abcdef123456 for model'

        chat = Chat(cfg, store, Budget(store), Mock(side_effect=FakeStatusError()))
        with self.assertRaises(UserError) as result:
            chat.send('虚构消息')
        self.assertIn('HTTP 401', str(result.exception))
        self.assertNotIn('Invalid token', str(result.exception))
        rows = store.read('test/provider-errors.jsonl')
        self.assertEqual(rows[0]['status'], 401)
        self.assertIn('sk-***', rows[0]['detail'])
        self.assertNotIn('abcdef123456', rows[0]['detail'])
