import json
import tempfile
import unittest
from decimal import Decimal
from pathlib import Path
from unittest.mock import Mock, patch

from model_settings import FOLLOW_CHAT, ModelSettings, SettingsError
from veil_agent import Budget, Chat, Store, background_cfg, load_character, make_model
from agent_types import UserError

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
        self.assertIsNone(cfg.models.public()['use']['delegate'])
        with self.assertRaises(SettingsError):
            cfg.models.chosen('delegate')

    def test_delegate_requires_an_explicit_model_and_can_be_disabled(self):
        cfg, model_id = self.add_relay()
        _, name, provider = cfg.models.use('delegate', model_id)
        self.assertEqual((name, provider['model']), ('relay', '[k]fake-sonnet'))
        self.assertEqual(cfg.models.public()['use']['delegate'], model_id)
        cfg.models.use('delegate', '')
        self.assertIsNone(cfg.models.public()['use']['delegate'])
        with self.assertRaises(SettingsError):
            cfg.models.chosen('delegate')

    def test_delegate_tool_is_only_offered_when_model_is_selected(self):
        from dataclasses import replace
        cfg, model_id = self.add_relay()
        chat_cfg = replace(cfg, tools={'enabled': True, 'max_steps': 2})
        chat = Chat(chat_cfg, Store(self.root / 'data'), Budget(Store(self.root / 'data')), Mock())
        names = {schema['function']['name'] for schema in chat.tool_schemas()}
        self.assertNotIn('delegate', names)
        main_model = chat_cfg.models.chosen('peter')[0]
        chat_cfg.models.use('delegate', model_id)
        schemas = chat.tool_schemas()
        self.assertIn('delegate', {schema['function']['name'] for schema in schemas})
        delegate_schema = next(s for s in schemas if s['function']['name'] == 'delegate')
        self.assertIn('Sonnet', delegate_schema['function']['description'])
        self.assertIn('¥0.06/次', delegate_schema['function']['description'])
        self.assertIn('完整任务最多预留', delegate_schema['function']['description'])
        self.assertEqual(chat_cfg.models.chosen('peter')[0], main_model)
        chat_cfg.models.use('delegate', '')
        self.assertNotIn('delegate', {schema['function']['name'] for schema in chat.tool_schemas()})

    def test_corrupt_existing_config_never_falls_back_or_changes_file(self):
        path = self.root / 'data' / 'test' / 'models.json'
        path.parent.mkdir(parents=True)
        for raw in ('{broken', '[]', '{"models": "bad"}',
                    '{"use": {"peter": "mabcdef"}}',
                    '{"endpoints": [{"name": "broken"}]}'):
            with self.subTest(raw=raw):
                path.write_text(raw, encoding='utf-8')
                with self.assertRaises(UserError):
                    load_character(self.root, 'test')
                self.assertEqual(path.read_text(encoding='utf-8'), raw)
        path.write_text('{"endpoints": [], "models": [], "use": {}, "limits": {}}', encoding='utf-8')
        self.assertEqual(load_character(self.root, 'test').provider['model'], 'fake-model')

    def test_valid_older_model_defaults_are_kept(self):
        path = self.root / 'data' / 'test' / 'models.json'
        path.parent.mkdir(parents=True)
        legacy = {'endpoints': [{'id': 'eabcdef', 'name': 'old', 'base_url': 'https://old.invalid/v1'}],
                  'models': [{'id': 'mabcdef', 'endpoint': 'eabcdef', 'model': 'old-model',
                              'billing': 'tokens', 'input_cny_per_million': '1',
                              'output_cny_per_million': '2'}],
                  'use': {'peter': 'mabcdef'}}
        path.write_text(json.dumps(legacy), encoding='utf-8')
        cfg = load_character(self.root, 'test')
        self.assertEqual(cfg.provider['model'], 'old-model')
        self.assertEqual(cfg.provider['cached_input_cny_per_million'], Decimal('1'))
        self.assertEqual(cfg.provider['max_input_tokens'], 32768)

    def _write_legacy_models(self, use, models=None):
        path = self.root / 'data' / 'test' / 'models.json'
        path.parent.mkdir(parents=True, exist_ok=True)
        raw = {'endpoints': [{'id': 'eabcdef', 'name': 'old', 'base_url': 'https://old.invalid/v1'}],
               'models': models if models is not None else [
                   {'id': 'mabcdef', 'endpoint': 'eabcdef', 'model': 'plain-api', 'label': '普通 API',
                    'billing': 'tokens', 'input_cny_per_million': '1', 'output_cny_per_million': '2'}],
               'use': use, 'limits': {'daily_cny': '30', 'monthly_cny': '600'}}
        path.write_text(json.dumps(raw), encoding='utf-8')
        return path, raw

    def test_retired_claude_code_choices_are_migrated_once_with_backup(self):
        path, raw = self._write_legacy_models({
            'peter': 'builtin:claude_agent',
            'autonomy': 'builtin:claude_agent_opus',
            'background': 'builtin:claude_agent_haiku',
            'glance': 'builtin:claude',
            'delegate': 'builtin:claude_agent',
        })
        before = path.read_bytes()
        cfg = load_character(self.root, 'test')
        view = cfg.models.public()
        self.assertEqual(cfg.provider['model'], 'fake-model')
        self.assertEqual(cfg.background_provider['model'], 'fake-model')
        self.assertEqual(view['use']['peter'], 'builtin:fake')
        self.assertEqual(view['use']['background'], 'builtin:fake')
        self.assertEqual(view['use']['autonomy'], 'builtin:fake')
        self.assertEqual(view['use']['glance'], FOLLOW_CHAT)
        self.assertIsNone(view['use']['delegate'])
        self.assertEqual(len(view['endpoints']), 1)
        self.assertEqual(len(view['models']), 3)  # fake, other and the preserved custom API model
        self.assertEqual(view['limits'], {'daily_cny': '30', 'monthly_cny': '600'})
        self.assertIn('Claude Code 专用模型已下线', view['migration_notice'])
        self.assertIn('请重新选择委派模型', view['migration_notice'])
        backup = path.with_name('models.json.cc-removal-backup')
        self.assertTrue(backup.exists())
        self.assertEqual(backup.read_bytes(), before)
        self.assertNotIn('builtin:claude_agent', path.read_text(encoding='utf-8'))
        # A second start does not migrate again, recreate the old models or overwrite a later manual choice.
        cfg.models.use('peter', 'mabcdef')
        self.assertEqual(load_character(self.root, 'test').provider['model'], 'plain-api')
        self.assertEqual(backup.read_bytes(), before)
        again = load_character(self.root, 'test').models.public()
        self.assertEqual(again['use']['peter'], 'mabcdef')
        self.assertNotIn('Claude Code 专用模型已下线；聊天', again['migration_notice'])

    def test_normal_anthropic_api_model_named_claude_is_kept(self):
        toml = TOML + '''
[providers.claude]
model = "claude-api-model"
base_url = "https://claude.invalid/v1"
api_key_env = "FAKE_KEY"
max_input_tokens = 6000
input_cny_per_million = "1"
cached_input_cny_per_million = "1"
output_cny_per_million = "2"
'''
        (self.root / 'characters' / 'test' / 'character.toml').write_text(toml, encoding='utf-8')
        path, _ = self._write_legacy_models({'peter': 'builtin:claude'})
        cfg = load_character(self.root, 'test')
        self.assertEqual(cfg.provider['model'], 'claude-api-model')
        self.assertEqual(cfg.models.public()['use']['peter'], 'builtin:claude')
        self.assertEqual(cfg.models.public()['migration_notice'], '')
        self.assertFalse(path.with_name('models.json.cc-removal-backup').exists())

    def test_custom_api_model_with_claude_in_its_name_is_kept(self):
        models = [{'id': 'mabcdef', 'endpoint': 'eabcdef', 'model': '[k]claude-sonnet-5.5',
                   'label': 'Claude Sonnet API', 'billing': 'tokens',
                   'input_cny_per_million': '1', 'output_cny_per_million': '2'}]
        path, _ = self._write_legacy_models({'peter': 'mabcdef'}, models)
        cfg = load_character(self.root, 'test')
        self.assertEqual(cfg.provider['model'], '[k]claude-sonnet-5.5')
        self.assertEqual(cfg.models.public()['use']['peter'], 'mabcdef')
        self.assertFalse(path.with_name('models.json.cc-removal-backup').exists())

    def test_valid_choices_for_other_roles_survive_cc_migration(self):
        path, _ = self._write_legacy_models({
            'peter': 'builtin:fake',
            'autonomy': 'builtin:claude_agent_haiku',
            'background': 'builtin:other',
            'glance': 'builtin:other',
            'delegate': 'builtin:claude_agent',
        })
        cfg = load_character(self.root, 'test')
        view = cfg.models.public()
        self.assertEqual(view['use']['peter'], 'builtin:fake')
        self.assertEqual(view['use']['background'], 'builtin:other')
        self.assertEqual(view['use']['glance'], 'builtin:other')
        self.assertEqual(view['use']['autonomy'], 'builtin:fake')
        self.assertIsNone(view['use']['delegate'])
        self.assertTrue(path.with_name('models.json.cc-removal-backup').exists())

    def test_stale_cc_provider_in_character_toml_is_not_registered(self):
        toml = TOML + '''
[providers.claude_agent]
kind = "claude_agent"
label = "Claude Code · Sonnet（均衡）"
model = "sonnet"
base_url = "https://claude.invalid/v1"
api_key_env = "FAKE_KEY"
max_input_tokens = 200000
input_cny_per_million = "1"
cached_input_cny_per_million = "1"
output_cny_per_million = "1"
'''
        (self.root / 'characters' / 'test' / 'character.toml').write_text(toml, encoding='utf-8')
        view = load_character(self.root, 'test').models.public()
        self.assertFalse([item for item in view['models'] if item.get('kind') == 'claude_agent'])

    def test_old_cc_default_provider_falls_back_to_a_normal_api_provider(self):
        toml = TOML.replace('provider = "fake"', 'provider = "claude_agent"') + '''
[providers.claude_agent]
kind = "claude_agent"
label = "Claude Code · Sonnet（均衡）"
model = "sonnet"
max_input_tokens = 200000
'''
        (self.root / 'characters' / 'test' / 'character.toml').write_text(toml, encoding='utf-8')
        cfg = load_character(self.root, 'test')
        self.assertEqual(cfg.provider_name, 'fake')
        self.assertEqual(cfg.provider['model'], 'fake-model')
        self.assertFalse([item for item in cfg.models.public()['models']
                          if item.get('kind') == 'claude_agent'])

    def test_fallback_skips_invalid_provider_and_uses_the_next_valid_one(self):
        toml = '''name = "Test"
provider = "claude_agent"
persona = "persona/test.md"
history_rounds = 20
max_output_tokens = 50
[providers.claude_agent]
kind = "claude_agent"
model = "sonnet"
[providers.broken]
model = "broken-model"
[providers.valid]
model = "valid-model"
base_url = "https://valid.invalid/v1"
api_key_env = "FAKE_KEY"
max_input_tokens = 6000
input_cny_per_million = "1"
cached_input_cny_per_million = "1"
output_cny_per_million = "2"
'''
        (self.root / 'characters' / 'test' / 'character.toml').write_text(toml, encoding='utf-8')
        cfg = load_character(self.root, 'test')
        self.assertEqual(cfg.provider_name, 'valid')
        self.assertEqual(cfg.provider['model'], 'valid-model')

    def test_character_without_a_providers_table_starts_in_recovery(self):
        toml = '''name = "Test"
provider = "claude_agent"
persona = "persona/test.md"
history_rounds = 20
max_output_tokens = 50
'''
        (self.root / 'characters' / 'test' / 'character.toml').write_text(toml, encoding='utf-8')
        cfg = load_character(self.root, 'test')
        self.assertTrue(cfg.provider.get('missing'))
        self.assertIsNone(cfg.models.public()['use']['peter'])
        self.assertIn('还没有可用的聊天模型', cfg.models.public()['migration_notice'])

    def test_migration_prefers_a_keyed_custom_model_when_default_key_is_missing(self):
        cfg, model_id = self.add_relay()
        env = self.root / '.env'
        kept = [line for line in env.read_text(encoding='utf-8').splitlines()
                if not line.startswith('FAKE_KEY=')]
        env.write_text('\n'.join(kept) + '\n', encoding='utf-8')
        raw = json.loads(cfg.models.path.read_text(encoding='utf-8'))
        raw['use'] = {'peter': 'builtin:claude_agent', 'background': 'builtin:claude_agent_haiku'}
        cfg.models.path.write_text(json.dumps(raw), encoding='utf-8')

        cfg = load_character(self.root, 'test')
        view = cfg.models.public()
        self.assertEqual(cfg.provider['model'], '[k]fake-sonnet')
        self.assertEqual(cfg.background_provider['model'], '[k]fake-sonnet')
        self.assertEqual(view['use']['peter'], model_id)
        self.assertEqual(view['use']['background'], model_id)
        self.assertIn('已恢复为可用的 API 模型「Sonnet」', view['migration_notice'])
        self.assertNotIn('没有找到已填写 Key', view['migration_notice'])

    def test_migration_does_not_claim_a_model_is_available_when_every_key_is_missing(self):
        (self.root / '.env').write_text('UNRELATED=fake\n', encoding='utf-8')
        path, _ = self._write_legacy_models({
            'peter': 'builtin:claude_agent',
            'background': 'builtin:claude_agent_opus',
        })
        cfg = load_character(self.root, 'test')
        view = cfg.models.public()
        self.assertEqual(cfg.provider['model'], 'fake-model')
        self.assertEqual(view['use']['peter'], 'builtin:fake')
        self.assertIn('没有找到已填写 Key 的 API 模型', view['migration_notice'])
        self.assertIn('暂用「fake-model」', view['migration_notice'])
        self.assertNotIn('已恢复为可用的 API 模型', view['migration_notice'])
        fake = next(item for item in view['models'] if item['id'] == 'builtin:fake')
        self.assertFalse(fake['key_set'])

    def test_migration_recovers_when_the_default_model_is_not_available(self):
        other = {'model': 'other-model', 'base_url': 'https://other.invalid/v1',
                 'api_key_env': 'OTHER_KEY', 'thinking': 'enabled', 'max_input_tokens': 6000,
                 'input_cny_per_million': Decimal('1'), 'cached_input_cny_per_million': Decimal('1'),
                 'output_cny_per_million': Decimal('2')}
        (self.root / '.env').write_text('OTHER_KEY=present\n', encoding='utf-8')
        self._write_legacy_models({
            'peter': 'builtin:claude_agent',
            'background': 'builtin:claude_agent_haiku',
        })
        settings = ModelSettings(self.root, 'test', {'other': other}, 'missing-default')
        view = settings.public()
        self.assertEqual(view['use']['peter'], 'builtin:other')
        self.assertEqual(view['use']['background'], 'builtin:other')
        self.assertIn('已恢复为可用的 API 模型「other-model」', view['migration_notice'])

    def test_only_cc_providers_start_without_a_model_and_keep_settings_usable(self):
        toml = '''name = "Test"
provider = "claude_agent"
persona = "persona/test.md"
history_rounds = 20
max_output_tokens = 50
[providers.claude_agent]
kind = "claude_agent"
model = "sonnet"
max_input_tokens = 200000
'''
        (self.root / 'characters' / 'test' / 'character.toml').write_text(toml, encoding='utf-8')
        path = self.root / 'data' / 'test' / 'models.json'
        path.parent.mkdir(parents=True)
        path.write_text(json.dumps({'use': {'peter': 'builtin:claude_agent',
                                            'background': 'builtin:claude_agent'}}), encoding='utf-8')
        cfg = load_character(self.root, 'test')
        self.assertTrue(cfg.provider.get('missing'))
        view = cfg.models.public()
        self.assertIsNone(view['use']['peter'])
        self.assertIn('没有可用的 API 模型', view['migration_notice'])
        self.assertTrue(path.with_name('models.json.cc-removal-backup').exists())
        clients, call = make_model(self.root, cfg)
        with clients, self.assertRaises(UserError):
            call([{'role': 'user', 'content': 'fake'}])
        store = Store(self.root / 'data')
        with self.assertRaises(UserError):
            Chat(cfg, store, Budget(store), Mock()).send('fake')

    def test_saving_first_endpoint_and_model_works_without_a_chat_model(self):
        from veil_agent import ActivityState
        from web_agent import Hub

        toml = '''name = "Test"
provider = "claude_agent"
persona = "persona/test.md"
history_rounds = 20
max_output_tokens = 50
[providers.claude_agent]
kind = "claude_agent"
model = "sonnet"
'''
        (self.root / 'characters' / 'test' / 'character.toml').write_text(toml, encoding='utf-8')
        cfg = load_character(self.root, 'test')
        store = Store(self.root / 'data')
        hub = Hub('Test', ActivityState(store.root / 'test' / 'resident-state.json'),
                  media_root=store.root / 'test' / 'media')
        hub.chat = Chat(cfg, store, Budget(store), Mock())
        limits = {'daily_cny': '30', 'monthly_cny': '600'}
        view = hub.save_models({'endpoints': [{'name': 'relay', 'base_url': 'https://relay.invalid/v1',
                                               'key': 'sk-fake'}], 'models': [], 'limits': limits})
        self.assertEqual(len(view['endpoints']), 1)
        endpoint = view['endpoints'][0]['id']
        view = hub.save_models({
            'endpoints': [{'id': endpoint, 'name': 'relay', 'base_url': 'https://relay.invalid/v1'}],
            'models': [{'endpoint': endpoint, 'model': 'relay-model', 'label': 'Relay',
                        'billing': 'call', 'cny_per_call': '0.06'}],
            'limits': limits})
        self.assertEqual(len(view['models']), 1)
        self.assertIsNone(view['use']['peter'])
        hub.use_model('peter', view['models'][0]['id'])
        self.assertEqual(hub.chat.pending_model[1]['model'], 'relay-model')

    def test_make_model_starts_without_a_key_but_the_request_explains_why(self):
        (self.root / '.env').write_text('OTHER_KEY=present\n', encoding='utf-8')
        cfg = load_character(self.root, 'test')
        self.assertEqual(cfg.provider['model'], 'fake-model')
        clients, call = make_model(self.root, cfg)
        with clients, self.assertRaises(UserError) as result:
            call([{'role': 'user', 'content': 'fake'}])
        self.assertIn('API Key', str(result.exception))

    def test_invalid_unrelated_choice_blocks_migration_without_touching_file(self):
        path, raw = self._write_legacy_models({
            'peter': 'builtin:claude_agent',
            'background': 'not-a-model',
        })
        before = path.read_bytes()
        with self.assertRaises(UserError):
            load_character(self.root, 'test')
        self.assertEqual(path.read_bytes(), before)
        self.assertFalse(path.with_name('models.json.cc-removal-backup').exists())

    def test_key_and_model_transaction_recovers_after_second_write_failure(self):
        cfg = load_character(self.root, 'test')
        initial_env = (self.root / '.env').read_bytes()
        path = cfg.models.path
        original_write = cfg.models._write
        def fail_write(data):
            raise OSError('fictional write failure')
        with patch.object(cfg.models, '_write', side_effect=fail_write):
            with self.assertRaises(OSError):
                cfg.models.save(RELAY)
        self.assertEqual((self.root / '.env').read_bytes(), initial_env)
        self.assertFalse(path.exists())
        self.assertFalse(cfg.models.journal.exists())
        self.assertEqual(load_character(self.root, 'test').provider['model'], 'fake-model')
        cfg.models._write = original_write
        cfg.models.save(RELAY)
        self.assertTrue(path.exists())
        self.assertIn('sk-fake-relay-1234', (self.root / '.env').read_text(encoding='utf-8'))

    def test_env_write_failure_keeps_old_files_and_can_retry(self):
        cfg = load_character(self.root, 'test')
        before = (self.root / '.env').read_bytes()
        with patch.object(cfg.models, '_write_env', side_effect=OSError('fake denied')):
            with self.assertRaises(OSError):
                cfg.models.save(RELAY)
        self.assertEqual((self.root / '.env').read_bytes(), before)
        self.assertFalse(cfg.models.path.exists())
        self.assertFalse(cfg.models.journal.exists())
        cfg.models.save(RELAY)
        self.assertEqual(len(load_character(self.root, 'test').models.public()['endpoints']), 1)

    def test_model_selection_waits_for_both_files_to_finish(self):
        import threading
        cfg = load_character(self.root, 'test')
        env_written = threading.Event()
        finish = threading.Event()
        selected = threading.Event()
        selecting_started = threading.Event()
        errors = []
        original = cfg.models._write
        def paused_write(data):
            env_written.set()
            self.assertTrue(finish.wait(5))
            original(data)
        def save():
            try:
                cfg.models.save(RELAY)
            except Exception as exc:
                errors.append(exc)
        def select():
            selecting_started.set()
            try:
                cfg.models.chosen('peter')
                selected.set()
            except Exception as exc:
                errors.append(exc)
        with patch.object(cfg.models, '_write', side_effect=paused_write):
            saving = threading.Thread(target=save)
            saving.start()
            self.assertTrue(env_written.wait(5))
            selecting = threading.Thread(target=select)
            selecting.start()
            self.assertTrue(selecting_started.wait(5))
            self.assertFalse(selected.wait(0.1))
            finish.set()
            saving.join(5)
            selecting.join(5)
        self.assertFalse(saving.is_alive())
        self.assertFalse(selecting.is_alive())
        self.assertEqual(errors, [])
        self.assertTrue(selected.is_set())
        self.assertFalse(cfg.models.journal.exists())

    def test_cleanup_sync_failure_does_not_report_committed_save_as_failed(self):
        cfg = load_character(self.root, 'test')
        original = cfg.models._sync_directory
        def fail_cleanup(directory):
            if not cfg.models.journal.exists():
                raise OSError('fictional cleanup sync failure')
            original(directory)
        with patch.object(cfg.models, '_sync_directory', side_effect=fail_cleanup):
            view = cfg.models.save(RELAY)
        self.assertEqual(len(view['endpoints']), 1)
        self.assertEqual(len(load_character(self.root, 'test').models.public()['endpoints']), 1)

    def test_second_instance_model_call_waits_for_shared_env_transaction(self):
        import threading
        first = load_character(self.root, 'test')
        second = load_character(self.root, 'test')
        entered = threading.Event()
        finish = threading.Event()
        called = threading.Event()
        requesting_started = threading.Event()
        errors = []
        original = first.models._write
        def paused_write(data):
            entered.set()
            if not finish.wait(5):
                raise AssertionError('save did not resume')
            original(data)
        def save():
            try:
                first.models.save(RELAY)
            except Exception as exc:
                errors.append(exc)
        def request(call):
            requesting_started.set()
            try:
                call([{'role': 'user', 'content': 'fake'}])
                called.set()
            except Exception as exc:
                errors.append(exc)
        fake = Mock()
        fake.chat.completions.create.return_value.usage.prompt_tokens = 1
        fake.chat.completions.create.return_value.usage.completion_tokens = 1
        fake.chat.completions.create.return_value.choices = [Mock(message=Mock(content='ok'))]
        with patch('openai.OpenAI', return_value=fake):
            _, call = make_model(self.root, second)
            with patch.object(first.models, '_write', side_effect=paused_write):
                saving = threading.Thread(target=save)
                saving.start()
                self.assertTrue(entered.wait(5))
                requesting = threading.Thread(target=request, args=(call,))
                requesting.start()
                self.assertTrue(requesting_started.wait(5))
                self.assertFalse(called.wait(0.1))
                finish.set()
                saving.join(5)
                requesting.join(5)
        self.assertFalse(saving.is_alive())
        self.assertFalse(requesting.is_alive())
        self.assertEqual(errors, [])
        self.assertTrue(called.is_set())

    def test_interrupted_save_rolls_back_on_restart(self):
        cfg = load_character(self.root, 'test')
        initial_env = (self.root / '.env').read_bytes()
        import base64
        record = {'state': 'prepared', 'env': base64.b64encode(initial_env).decode('ascii'), 'models': None}
        cfg.models._atomic_bytes(cfg.models.journal, json.dumps(record).encode())
        (self.root / '.env').write_text('FAKE_KEY=half-new\n', encoding='utf-8')
        cfg.models.path.parent.mkdir(parents=True)
        cfg.models.path.write_text('{"use": {"peter": "mabcdef"}}', encoding='utf-8')
        restarted = load_character(self.root, 'test')
        self.assertEqual((self.root / '.env').read_bytes(), initial_env)
        self.assertFalse(cfg.models.path.exists())
        self.assertFalse(cfg.models.journal.exists())
        self.assertEqual(restarted.provider['model'], 'fake-model')

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

