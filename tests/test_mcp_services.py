import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from mcp_services import MCPConfigError, MCPServices


FIXTURE = Path(__file__).parent / 'fixtures' / 'mcp_echo_server.py'


class MCPServicesTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(dir=Path(__file__).parent)
        self.manager = MCPServices(Path(self.tmp.name) / 'mcp.json')

    def tearDown(self):
        self.manager.close()
        self.tmp.cleanup()

    def config(self, enabled=True, service_enabled=True, command=None):
        return {'enabled': enabled, 'services': [{
            'id': 'echo', 'command': command or sys.executable, 'args': [str(FIXTURE)],
            'cwd': None, 'enabled': service_enabled, 'permission': 'ask'}]}

    def await_status(self, expected):
        deadline = time.monotonic() + 8
        while time.monotonic() < deadline:
            self.manager.schemas()
            if self.manager.view()['services'][0]['status'] == expected:
                return
            time.sleep(0.05)
        self.fail(f'MCP service did not reach {expected}: {self.manager.view()}')

    def test_discovery_call_and_switches(self):
        self.manager.save(self.config())
        self.await_status('connected')
        self.assertEqual([s['function']['name'] for s in self.manager.schemas()], ['mcp__echo__echo', 'mcp__echo__slow'])
        self.assertEqual(self.manager.view()['services'][0]['status'], 'connected')
        self.assertEqual(self.manager.invoke('mcp__echo__echo', {'text': 'hello'}, lambda _: 'n')[1], '被拒绝')
        result, state, _ = self.manager.invoke('mcp__echo__echo', {'text': 'hello'}, lambda _: 'y')
        self.assertEqual((result, state), ('hello', '成功'))
        self.manager.save(self.config(service_enabled=False))
        self.assertEqual(self.manager.schemas(), [])
        self.assertEqual(self.manager.invoke('mcp__echo__echo', {'text': 'x'}, lambda _: 'y')[1], '被拒绝')
        self.manager.save(self.config(enabled=False))
        self.assertEqual(self.manager.schemas(), [])
        self.assertFalse(MCPServices(self.manager.path).view()['enabled'])

    def test_failure_and_validation(self):
        with self.assertRaises(MCPConfigError):
            self.manager.save({'enabled': True, 'services': [{**self.config()['services'][0], 'id': '../bad'}]})
        self.manager.save(self.config(command='does-not-exist-veil-mcp'))
        self.await_status('error')
        self.assertEqual(self.manager.schemas(), [])
        self.assertEqual(self.manager.view()['services'][0]['status'], 'error')
        self.assertTrue(self.manager.view()['services'][0]['error'])

    def test_disconnection_is_visible(self):
        config = self.config()
        config['services'][0]['args'].append('--exit-after')
        self.manager.save(config)
        self.await_status('connected')
        self.await_status('error')
        self.assertIn('断开', self.manager.view()['services'][0]['error'])

    def test_call_timeout_is_visible_and_chat_safe(self):
        self.manager.save(self.config())
        self.await_status('connected')
        with patch('mcp_services.CALL_TIMEOUT', 0.1):
            result, state, _ = self.manager.invoke('mcp__echo__slow', {}, lambda _: 'y')
        self.assertEqual(state, '出错')
        self.assertIn('超时', result)
        self.assertIn('超时', self.manager.view()['services'][0]['error'])
        self.assertEqual(self.manager.view()['services'][0]['status'], 'connected')


if __name__ == '__main__':
    unittest.main()
