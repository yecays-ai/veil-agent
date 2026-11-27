"""Isolated tests of the existing authenticated config and budget paths."""
from dataclasses import dataclass
from decimal import Decimal
from io import BytesIO
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Condition
from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import Mock

from veil.brain.learned_context import LearnedContext
from veil.brain.relationship_semantic import RelationshipSemanticWorker
from veil.core.model_settings import ModelSettings
from veil.core.veil_agent import Budget, Store, UserError
from veil.server.web_agent import Hub, make_handler


class SemanticBudgetConfigTests(TestCase):
    def setUp(self):
        folder = TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.root = Path(folder.name)
        self.models = ModelSettings(self.root, 'fixture', {}, 'none')
        self.models.save({'limits': {'daily_cny': '5', 'monthly_cny': '20'}})
        self.budget = Budget(Store(self.root / 'data'), daily=Decimal('5'), monthly=Decimal('20'),
                             clock=lambda: '2026-11-27T14:00:00+08:00')
        hub = Hub.__new__(Hub)
        hub.chat = SimpleNamespace(cfg=SimpleNamespace(id='fixture', models=self.models), budget=self.budget,
                                   profile=SimpleNamespace(relationship_semantic_summary=Mock(return_value={
                                       'pending': 2, 'promoted': 1, 'reasons': {'timeout': 3},
                                   })))
        hub.stopping = hub.stopped = False
        hub.cond = Condition()
        hub.acceptance_limits = hub.background_running = None
        self.handler_class = make_handler(hub, 'fixture-token')
        self.hub = hub

    def request(self, path, payload=None, *, token='fixture-token', origin=None):
        req = self.handler_class.__new__(self.handler_class)
        req.server = SimpleNamespace(server_address=('127.0.0.1', 65000))
        req.headers = {'Host': '127.0.0.1:65000', 'X-Token': token}
        if origin:
            req.headers['Origin'] = origin
        req.path = path
        req._send = Mock()
        req.connection = Mock()
        if payload is None:
            req.do_GET()
        else:
            encoded = json.dumps(payload).encode()
            req.headers['Content-Length'] = str(len(encoded))
            req.rfile = BytesIO(encoded)
            req.do_POST()
        return req._send.call_args.args[:2]

    def test_existing_post_and_running_configuration_share_values_without_restart(self):
        self.assertEqual({'daily_cap_cny': '0.06', 'timeout_seconds': 6}, self.models.semantic_verification())
        settings_patch = {'semantic_verification': {'daily_cap_cny': '0.12', 'timeout_seconds': 3}}
        self.assertEqual(200, self.request('/api/models', settings_patch)[0])
        self.assertIs(self.models, self.hub.chat.cfg.models)
        self.assertEqual(settings_patch['semantic_verification'], self.models.semantic_verification())
        status, overview = self.request('/api/config/overview')
        self.assertEqual(200, status)
        entries = {entry['key'].split('.')[-1]: entry for entry in overview['entries']
                   if entry['key'].startswith('budget.relationship_semantic.')}
        for key, value in {'daily_cap_cny': 0.12, 'timeout_seconds': 3}.items():
            self.assertEqual('next_task', entries[key]['apply_mode'])
            self.assertEqual({'status': 'known', 'value': value, 'source': 'runtime'}, entries[key]['effective'])
            self.assertEqual('saved', entries[key]['configured']['source'])
        self.assertEqual((Decimal('5'), Decimal('20')), self.models.limits())
        self.assertEqual(200, self.request('/api/models', {'limits': {'daily_cny': '4', 'monthly_cny': '18'}})[0])
        self.assertEqual(200, self.request('/api/models', {'endpoints': [], 'models': []})[0])
        self.assertEqual(200, self.request('/api/models', {'semantic_verification': {'daily_cap_cny': '0'}})[0])
        self.assertEqual({'daily_cap_cny': '0', 'timeout_seconds': 3}, self.models.semantic_verification())
        self.assertEqual(self.models.semantic_verification(),
                         ModelSettings(self.root, 'fixture', {}, 'none').semantic_verification())
        self.assertEqual('0.06', ModelSettings(self.root, 'other', {}, 'none').semantic_verification()['daily_cap_cny'])

    def test_existing_worker_observes_http_changes_and_updates_same_budget_summary(self):
        store = self.budget.store
        profile = LearnedContext(store.root, 'fixture', {'file': 'learned.json'})
        self.hub.chat.profile = profile
        store.append('fixture/chat.jsonl', {'role': 'user', 'message_id': 'origin',
                                            'content': 'I put a yellow candle beside the window each evening.'})
        profile.submit_candidate('relationship', 'Lan lights a candle near the window at night.',
                                 status='confirmed', sources=('chat:origin',))
        model = Mock(side_effect=TimeoutError)
        provider = {'model': 'fixture-model', 'cny_per_call': Decimal('0.01'), 'max_input_tokens': 32768}
        @dataclass
        class FixtureConfig:
            id: str
            provider: dict
            max_output_tokens: int

        worker = RelationshipSemanticWorker(FixtureConfig('fixture', provider, 1024), self.budget, model, self.models.semantic_verification,
                                            profile=profile)
        self.assertEqual(200, self.request('/api/models', {'semantic_verification': {'daily_cap_cny': '0'}})[0])
        self.assertFalse(worker.tick())
        model.assert_not_called()
        self.assertEqual(1, self.request('/api/budget/summary')[1]['relationship_semantic']['verification']['pending'])

        patch = {'semantic_verification': {'daily_cap_cny': '0.06', 'timeout_seconds': 3}}
        self.assertEqual(200, self.request('/api/models', patch)[0])
        worker.tick()
        model.assert_called_once()
        self.assertEqual(3, model.call_args.kwargs['request_timeout'])
        semantic = self.request('/api/budget/summary')[1]['relationship_semantic']
        self.assertEqual('0.01', semantic['spent_cny'])
        self.assertEqual('0.05', semantic['remaining_cny'])
        self.assertEqual({'pending': 0, 'promoted': 0, 'reasons': {'timeout': 1}}, semantic['verification'])

    def test_guard_and_invalid_payload_do_not_write(self):
        before = self.models.path.read_bytes()
        patch = {'semantic_verification': {'daily_cap_cny': '0'}}
        self.assertEqual(403, self.request('/api/models', patch, token='wrong')[0])
        self.assertEqual(403, self.request('/api/models', patch, origin='https://else.invalid')[0])
        for invalid in ({'daily_cap_cny': '-0.01'}, {'daily_cap_cny': 'NaN'}, {'daily_cap_cny': '1001'},
                        {'timeout_seconds': False}, {'timeout_seconds': 0}, {'timeout_seconds': 61},
                        {'timeout_seconds': '6'}, {'character': 'other'}, None):
            with self.subTest(invalid=invalid):
                self.assertEqual(400, self.request('/api/models', {'semantic_verification': invalid})[0])
        self.assertEqual(before, self.models.path.read_bytes())

    def test_role_subbudget_shares_ledger_and_missing_state_is_unknown(self):
        store = self.budget.store
        for ident, who, value in (('settled', 'fixture', '0.04'), ('pending', 'fixture', '0.01'),
                                  ('other-pending', 'other', '0.03')):
            store.append('costs.jsonl', {'type': 'reserve', 'id': ident, 'category': 'relationship_semantic',
                                        'amount': value, 'character': who, 'model': 'fixture-model',
                                        'period': self.budget.clock()})
        store.append('costs.jsonl', {'type': 'settle', 'id': 'settled', 'amount': '0.03'})
        path = store.root / 'costs.jsonl'
        before = path.read_bytes()
        status, data = self.request('/api/budget/summary')
        self.assertEqual(200, status)
        self.assertEqual('0.04', data['pending_cny'])  # Total still covers both roles.
        self.assertEqual({'daily_cap_cny': '0.06', 'timeout_seconds': 6, 'spent_cny': '0.03',
                          'pending_cny': '0.01', 'remaining_cny': '0.02',
                          'verification': {'pending': 2, 'promoted': 1, 'reasons': {'timeout': 3}}},
                         data['relationship_semantic'])
        self.hub.chat.profile.relationship_semantic_summary.side_effect = UserError('private detail')
        status, data = self.request('/api/budget/summary')
        self.assertEqual(200, status)
        self.assertIsNone(data['relationship_semantic']['verification'])
        self.assertNotIn('private detail', json.dumps(data))
        self.assertEqual(before, path.read_bytes())
