"""Isolated HTTP regression for the desktop's authenticated Peter profile read."""
import http.client
import json
import threading
import unittest
from http.server import ThreadingHTTPServer
from types import SimpleNamespace
from unittest.mock import Mock

from veil.server.web_agent import make_handler


class PeterPageGetGuardTests(unittest.TestCase):
    def test_profile_read_requires_existing_loopback_and_token_guard(self):
        read_page = Mock(return_value={'persona': {'text': 'fictional profile'}, 'learned': {}})
        hub = SimpleNamespace(peter_page=read_page)
        with ThreadingHTTPServer(('127.0.0.1', 0), make_handler(hub, 'fixture-token')) as server:
            port = server.server_address[1]
            worker = threading.Thread(target=server.serve_forever, daemon=True)
            worker.start()
            try:
                def get(headers):
                    client = http.client.HTTPConnection('127.0.0.1', port, timeout=5)
                    try:
                        client.request('GET', '/api/peter-page', headers=headers)
                        response = client.getresponse()
                        return response.status, json.loads(response.read())
                    finally:
                        client.close()

                # Desktop main-process fetch sends this header on loopback, with no Origin.
                token = {'X-Token': 'fixture-token'}
                for headers in (token, {**token, 'Host': f'localhost:{port}',
                                               'Origin': f'http://localhost:{port}'}):
                    status, body = get(headers)
                    self.assertEqual(status, 200)
                    self.assertEqual(body['persona']['text'], 'fictional profile')
                    self.assertIn('budget', body['learned'])

                for headers, reason in (
                    ({**token, 'Host': 'invalid.example'}, 'host'),
                    ({**token, 'Origin': 'https://invalid.example'}, 'origin'),
                    ({}, 'token'),
                    ({'X-Token': 'wrong-test-token'}, 'token'),
                ):
                    with self.subTest(reason=reason):
                        self.assertEqual(get(headers), (403, {'error': reason}))
                self.assertEqual(read_page.call_count, 2)
            finally:
                server.shutdown()
                worker.join(timeout=5)
