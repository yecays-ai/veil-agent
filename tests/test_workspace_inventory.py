"""The inventory route serves the committed report, never role or runtime data."""
from collections import Counter
import csv
from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import Mock, patch

import web_agent


class WorkspaceInventoryRouteTests(TestCase):
    def setUp(self):
        handler = web_agent.make_handler(SimpleNamespace(), 'fixture-token')
        self.request = handler.__new__(handler)
        self.request.server = SimpleNamespace(server_address=('127.0.0.1', 65000))
        self.request.headers = {'Host': '127.0.0.1:65000', 'X-Token': 'fixture-token'}
        self.request.path = '/api/config/inventory'
        self.request._send = Mock()

    def test_serves_all_report_rows_in_source_order_and_no_runtime_snapshot(self):
        self.request.do_GET()
        status, body = self.request._send.call_args.args
        self.assertEqual(status, 200)
        self.assertEqual(body['audit'], '#446')
        self.assertEqual(body['schema_version'], 1)
        path = web_agent.ROOT / 'docs/reports/config-audit/config-inventory.csv'
        with path.open(encoding='utf-8-sig', newline='') as handle:
            expected = list(csv.DictReader(handle))
        self.assertEqual(body['rows'], expected)
        self.assertEqual(len(expected), 668)
        self.assertEqual(len(expected[0]), 19)
        self.assertEqual(Counter(row['effective_class'] for row in expected), {
            'verified_runtime': 21, 'source_default': 4, 'readable_unverified': 387,
            'constant_not_applicable': 256,
        })

    def test_unauthenticated_and_parameterized_requests_are_rejected(self):
        self.request.headers.pop('X-Token')
        self.request.do_GET()
        self.assertEqual(self.request._send.call_args.args[0], 403)
        self.request.headers['X-Token'] = 'fixture-token'
        self.request.path += '?role=other'
        self.request.do_GET()
        self.assertEqual(self.request._send.call_args.args[0], 400)

    def test_missing_report_returns_operator_facing_error(self):
        with patch.object(type(web_agent.ROOT), 'open', side_effect=FileNotFoundError):
            self.request.do_GET()
        self.assertEqual(self.request._send.call_args.args[0], 503)
        self.assertNotIn('rows', self.request._send.call_args.args[1])
