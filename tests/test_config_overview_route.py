"""The configuration view is role-bound and never changes settings."""
from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import Mock

from veil.server.web_agent import make_handler


class ConfigOverviewRouteTests(TestCase):
    def setUp(self):
        self.models = Mock()
        self.models.public.return_value = {
            'use': {'background': 'builtin:next'},
            'use_explicit': {'background': True},
            'endpoints': [{'key_hint': 'fixture-secret-hint'}],
        }
        self.models.semantic_verification_observation.return_value = (
            {'daily_cap_cny': '0.06', 'timeout_seconds': 6}, False,
        )
        hub = SimpleNamespace(chat=SimpleNamespace(cfg=SimpleNamespace(id='fixture', models=self.models)),
                              background_running='builtin:current')
        handler = make_handler(hub, 'fixture-token')
        self.request = handler.__new__(handler)
        self.request.server = SimpleNamespace(server_address=('127.0.0.1', 65000))
        self.request.headers = {'Host': '127.0.0.1:65000', 'X-Token': 'fixture-token'}
        self.request._send = Mock()
        self.request.path = '/api/config/overview'

    def test_get_returns_scoped_safe_projection(self):
        self.request.do_GET()
        status, view = self.request._send.call_args.args
        self.assertEqual(200, status)
        self.assertEqual({'role_id': 'fixture'}, view['scope'])
        self.assertNotIn('fixture-secret-hint', str(view))
        self.assertEqual(['public', 'semantic_verification_observation'],
                         [call[0] for call in self.models.method_calls])
        self.assertIn('budget.relationship_semantic.daily_cap_cny', str(view))
        self.assertIn('budget.relationship_semantic.timeout_seconds', str(view))

    def test_cannot_select_another_role(self):
        self.request.path += '?role=another'
        self.request.do_GET()
        self.assertEqual(400, self.request._send.call_args.args[0])
        self.models.public.assert_not_called()

    def test_requires_existing_request_guard(self):
        del self.request.headers['X-Token']
        self.request.do_GET()
        self.assertEqual(403, self.request._send.call_args.args[0])
        self.models.public.assert_not_called()
