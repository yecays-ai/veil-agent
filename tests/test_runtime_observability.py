"""Runtime safety status with fictional events only; no model or paid service."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock

from runtime_monitor import RuntimeMonitor
from veil_agent import ActivityState, Store
from web_agent import Hub


class RuntimeMonitorTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.store = Store(self.root / 'data')
        self.activity = ActivityState(self.store.root / 'fixture' / 'resident-state.json')
        self.activity.set(True)
        self.monitor = RuntimeMonitor(self.store, 'fixture', self.activity)

    def test_verified_memory_recovery_is_visible_without_model_probe(self):
        model = Mock()
        self.monitor.observe_memory({'code': 'mcp_call', 'stage': 'http_session',
                                     'error_types': ['ConnectionError']})
        self.monitor.observe_memory({'code': 'recovery_succeeded', 'stage': 'mcp_verified',
                                     'state': 'started'})
        self.monitor.observe_memory({'code': 'retry_succeeded', 'stage': 'mcp_verified',
                                     'tool': 'pulse', 'attempt': 2})

        status = self.monitor.snapshot()
        self.assertEqual(status['state'], 'recovered')
        self.assertEqual(status['classification'], 'safe_recovery')
        self.assertIn('已安全恢复', status['message'])
        model.assert_not_called()

    def test_service_restart_does_not_turn_uncertain_write_into_success(self):
        self.monitor.observe_memory({'code': 'mcp_call', 'stage': 'mcp_tool', 'tool': 'hold'})
        self.monitor.observe_memory({'code': 'recovery_succeeded', 'stage': 'mcp_verified'})
        status = self.monitor.snapshot()
        self.assertEqual((status['state'], status['classification']), ('paused', 'unknown'))

    def test_unknown_autonomous_side_effect_pauses_and_never_records_private_values(self):
        self.monitor.observe_tool('write_file', '出错', autonomy=True,
                                  detail='FICTIONAL_PRIVATE_CHAT E:/private/file.txt')

        self.assertFalse(self.activity.enabled())
        status = self.monitor.snapshot()
        self.assertEqual(status['state'], 'paused')
        self.assertEqual(status['classification'], 'unknown')
        self.assertIn('先核对', status['message'])
        saved = (self.store.root / 'fixture' / 'runtime-status.json').read_text(encoding='utf-8')
        self.assertNotIn('FICTIONAL_PRIVATE_CHAT', saved)
        self.assertNotIn('private/file', saved)

    def test_read_failure_needs_attention_but_does_not_pause_activity(self):
        self.monitor.observe_tool('read_text_file', '出错', autonomy=True)
        self.assertTrue(self.activity.enabled())
        status = self.monitor.snapshot()
        self.assertEqual(status['state'], 'attention')
        self.assertEqual(status['classification'], 'safe_to_retry')

    def test_diagnostic_schema_rejects_payload_fields(self):
        self.monitor.observe_memory({'code': 'mcp_call', 'stage': 'mcp_tool',
                                     'tool': 'hold', 'content': 'FICTIONAL_MEMORY_BODY',
                                     'api_key': 'FICTIONAL_KEY'})
        row = json.loads((self.store.root / 'fixture' / 'runtime-status.json').read_text(
            encoding='utf-8'))
        self.assertEqual(set(row), {'time', 'module', 'state', 'classification', 'code', 'action'})
        self.assertNotIn('FICTIONAL_MEMORY_BODY', json.dumps(row))
        self.assertNotIn('FICTIONAL_KEY', json.dumps(row))

    def test_web_snapshot_reuses_local_component_state(self):
        hub = Hub('Fixture', self.activity)
        hub.runtime_status = self.monitor.snapshot
        result = hub.snapshot(0)['runtime_status']
        self.assertEqual(result['components']['autonomy'], 'enabled')
        self.assertEqual(result['components']['file_tools'], 'disabled')
        self.assertNotIn('events', result)


if __name__ == '__main__':
    unittest.main()
