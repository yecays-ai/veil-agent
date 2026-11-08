import json
import tempfile
import unittest
from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path
from unittest.mock import Mock, patch

from model_settings import FOLLOW_CHAT, SettingsError
from veil_agent import Budget, Chat, Store, load_character
from test_model_settings import RELAY, TOML


class GlanceModelTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        (self.root / 'characters' / 'test').mkdir(parents=True)
        (self.root / 'persona').mkdir()
        (self.root / 'persona' / 'test.md').write_text('Fictional test persona', encoding='utf-8')
        (self.root / 'characters' / 'test' / 'character.toml').write_text(TOML, encoding='utf-8')
        (self.root / '.env').write_text('FAKE_KEY=fake\n', encoding='utf-8')

    def add_models(self, labels):
        cfg = load_character(self.root, 'test')
        view = cfg.models.save(RELAY)
        endpoint = {k: v for k, v in view['endpoints'][0].items() if k in ('id', 'name', 'base_url')}
        models = [{'endpoint': endpoint['id'], 'model': f'[x]{label}', 'label': label, 'thinking': True,
                   'billing': 'tokens', 'input_cny_per_million': '0.04', 'output_cny_per_million': '0.12',
                   'max_input_tokens': 65536} for label in labels]
        view = cfg.models.save({**RELAY, 'endpoints': [endpoint], 'models': models})
        return cfg, {m['label']: m['id'] for m in view['models'] if not m['builtin']}

    def test_glance_follows_chat_when_no_grok_is_configured(self):
        cfg, ids = self.add_models(['Sonnet'])
        self.assertEqual(cfg.models.public()['use']['glance'], FOLLOW_CHAT)
        self.assertEqual(cfg.models.chosen('glance')[0], cfg.models.chosen('peter')[0])

    def test_glance_defaults_to_grok_and_can_be_changed_without_touching_chat(self):
        cfg, ids = self.add_models(['Sonnet', 'grok-4.6'])
        chat_before = cfg.models.chosen('peter')[0]
        self.assertEqual(cfg.models.public()['use']['glance'], ids['grok-4.6'])
        cfg.models.use('glance', ids['Sonnet'])
        self.assertEqual(cfg.models.chosen('glance')[0], ids['Sonnet'])
        cfg.models.use('glance', FOLLOW_CHAT)
        self.assertEqual(cfg.models.public()['use']['glance'], FOLLOW_CHAT)
        self.assertEqual(cfg.models.chosen('peter')[0], chat_before)
        with self.assertRaises(SettingsError):
            cfg.models.use('peter', FOLLOW_CHAT)
        # Survives a restart.
        again = load_character(self.root, 'test')
        self.assertEqual(again.models.public()['use']['glance'], FOLLOW_CHAT)

    def test_glance_request_uses_its_own_model_and_chat_is_restored(self):
        cfg, ids = self.add_models(['Sonnet', 'grok-4.6'])
        store = Store(self.root / 'data')
        seen = []
        chat = Chat(cfg, store, Budget(store), Mock())
        chat._send = lambda *a, **k: seen.append((cfg.provider['model'], cfg.provider_name)) or 'ok'
        chat.glance_mode = True
        chat.send('look', autonomy=True)
        chat.glance_mode = False
        chat.send('hello')
        self.assertEqual(seen[0][0], '[x]grok-4.6')
        self.assertEqual(seen[1][0], 'fake-model')
        self.assertEqual(cfg.provider['model'], 'fake-model')

    def test_autonomy_follows_chat_until_explicitly_selected(self):
        cfg = load_character(self.root, 'test')
        self.assertEqual(cfg.models.public()['use']['autonomy'], 'builtin:fake')
        self.assertFalse(cfg.models.public()['use_explicit']['autonomy'])
        cfg.models.use('peter', 'builtin:other')
        self.assertEqual(cfg.models.public()['use']['autonomy'], 'builtin:other')
        cfg.models.use('autonomy', 'builtin:fake')
        cfg.models.use('peter', 'builtin:other')
        self.assertTrue(cfg.models.public()['use_explicit']['autonomy'])
        self.assertEqual(cfg.models.public()['use']['autonomy'], 'builtin:fake')
        again = load_character(self.root, 'test')
        self.assertEqual(again.models.public()['use']['autonomy'], 'builtin:fake')

    def test_builtin_grok_is_the_default_when_configured(self):
        toml = TOML + '[providers.grok]\nmodel = "grok-4.6"\nbase_url = "https://g.invalid/v1"\n' \
            'api_key_env = "FAKE_KEY"\nmax_input_tokens = 6000\ninput_cny_per_million = "1"\n' \
            'cached_input_cny_per_million = "1"\noutput_cny_per_million = "1"\n'
        (self.root / 'characters' / 'test' / 'character.toml').write_text(toml, encoding='utf-8')
        cfg = load_character(self.root, 'test')
        self.assertEqual(cfg.models.public()['use']['glance'], 'builtin:grok')


class StaleGlanceReservationTests(unittest.TestCase):
    def test_old_dispatched_glance_reservation_is_settled_once_and_stops_blocking(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = Store(Path(tmp))
            start = datetime.fromisoformat('2026-11-04T06:00:00+08:00')
            clock = Mock(return_value=start.isoformat())
            budget = Budget(store, clock=clock)
            budget.glance_daily = Decimal('1')
            cfg = Mock(provider={'model': 'm', 'cny_per_call': Decimal('0.3'), 'max_input_tokens': 1,
                                 'input_cny_per_million': Decimal(0), 'output_cny_per_million': Decimal(0)},
                       max_output_tokens=1, id='peter')
            with budget.glancing('peter'):
                stale_id, _ = budget.reserve(cfg)
                budget.mark_dispatched(stale_id)
                clock.return_value = (start + timedelta(minutes=5)).isoformat()
                budget.reserve(cfg)  # younger than 10 minutes: still pending, nothing settled yet
                self.assertEqual([r for r in store.read('costs.jsonl') if r['type'] == 'settle'], [])
                # Next day: the old reservation must not keep occupying the new day's budget.
                clock.return_value = '2026-11-05T00:30:00+08:00'
                for _ in range(3):
                    budget.reserve(cfg)
            rows = store.read('costs.jsonl')
            settles = [r for r in rows if r['type'] == 'settle' and r['id'] == stale_id]
            self.assertEqual(len(settles), 1)
            self.assertEqual(settles[0]['accounting'], 'glance-stale-recovered')
            self.assertEqual(Decimal(settles[0]['amount']), Decimal('0.3'))


if __name__ == '__main__':
    unittest.main()
