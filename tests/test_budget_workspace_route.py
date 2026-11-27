"""Budget summary uses the running budget and does not write the ledger."""
from decimal import Decimal
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import Mock

from veil.core.veil_agent import Budget, Store
from veil.server.web_agent import make_handler


class BudgetWorkspaceRouteTest(TestCase):
    def setUp(self):
        folder = TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        store = Store(Path(folder.name) / 'data')
        self.budget = Budget(store, daily=Decimal('10'), monthly=Decimal('25'),
                             clock=lambda: '2026-11-25T14:00:00+08:00')
        for row in (
            {'type': 'reserve', 'id': 'fixture-settled', 'category': 'autonomy', 'amount': '2',
             'character': 'fixture', 'model': 'fictional-model', 'period': self.budget.clock()},
            {'type': 'settle', 'id': 'fixture-settled', 'amount': '1.25'},
            {'type': 'reserve', 'id': 'fixture-pending', 'category': 'glance', 'amount': '0.5',
             'character': 'fixture', 'model': 'fictional-model', 'period': '2026-11-24T14:00:00+08:00'},
        ):
            store.append('costs.jsonl', row)
        self.hub = SimpleNamespace(chat=SimpleNamespace(budget=self.budget))
        handler = make_handler(self.hub, 'fictional-token')
        self.request = handler.__new__(handler)
        self.request.server = SimpleNamespace(server_address=('127.0.0.1', 65000))
        self.request.headers = {'Host': '127.0.0.1:65000', 'X-Token': 'fictional-token'}
        self.request._send = Mock()
        self.request.path = '/api/budget/summary'

    def test_summary_reports_real_consumer_totals_without_writing(self):
        path = self.budget.store.root / 'costs.jsonl'
        before = path.read_bytes()
        self.budget.raise_today(Decimal('1'))
        self.request.do_GET()
        status, data = self.request._send.call_args.args
        self.assertEqual(200, status)
        # limit_cny is effective (base + approved raise); the editable form uses base_limit_cny.
        self.assertEqual({'spent_cny': '1.25', 'limit_cny': '11', 'base_limit_cny': '10', 'raise_cny': '1',
                          'remaining_cny': '9.25'}, data['daily'])
        self.assertEqual({'spent_cny': '1.25', 'limit_cny': '26', 'base_limit_cny': '25', 'raise_cny': '1',
                          'remaining_cny': '24.25'}, data['monthly'])
        self.assertEqual('0.5', data['pending_cny'])
        self.assertFalse(data['ledger_unverified'])
        self.assertIn('fictional-model', data['model_summary'])
        categories = {row['id']: row for row in data['categories']}
        self.assertEqual('0.5', categories['glance']['pending_cny'])
        self.assertEqual('1.25', categories['autonomy']['spent_cny'])
        self.assertEqual(before, path.read_bytes())

    def test_denies_unauthenticated_and_query_override(self):
        self.request.path += '?role=another'
        self.request.do_GET()
        self.assertEqual(400, self.request._send.call_args.args[0])
        del self.request.headers['X-Token']
        self.request.do_GET()
        self.assertEqual(403, self.request._send.call_args.args[0])
