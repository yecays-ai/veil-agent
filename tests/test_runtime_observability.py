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
        self.monitor.observe_memory({'code': 'memory_operation_failed', 'tool': 'hold',
                                     'error_code': 'unknown'})
        self.monitor.observe_memory({'code': 'recovery_succeeded', 'stage': 'mcp_verified'})
        status = self.monitor.snapshot()
        self.assertEqual((status['state'], status['classification']), ('paused', 'unknown'))

    def test_safe_mutation_retry_does_not_pause_activity(self):
        self.monitor.observe_memory({'code': 'mcp_call', 'tool': 'hold', 'attempt': 1})
        self.assertTrue(self.activity.enabled())
        self.monitor.observe_memory({'code': 'retry_succeeded', 'stage': 'mcp_verified',
                                     'tool': 'hold', 'attempt': 2})
        self.assertTrue(self.activity.enabled())
        self.assertEqual(self.monitor.snapshot()['state'], 'recovered')

    def test_read_only_recovery_failure_needs_attention_without_pausing(self):
        self.monitor.observe_memory({'code': 'mcp_call', 'tool': 'breath_search'})
        self.monitor.observe_memory({'code': 'recovery_failed', 'tool': 'breath_search',
                                     'stage': 'recovery'})
        self.assertTrue(self.activity.enabled())
        self.assertEqual((self.monitor.snapshot()['state'], self.monitor.snapshot()['classification']),
                         ('attention', 'safe_to_retry'))

    def test_activity_pause_failure_never_reports_paused(self):
        self.activity.set = Mock(side_effect=OSError('fictional disk failure'))
        self.monitor.observe_memory({'code': 'memory_operation_failed', 'tool': 'hold',
                                     'error_code': 'unknown'})
        status = self.monitor.snapshot()
        self.assertEqual((status['state'], status['classification'], status['code']),
                         ('attention', 'unknown', 'memory_pause_failed'))
        self.assertIn('disable autonomy', status['action'])

    def test_read_events_cannot_clear_unknown_memory_write(self):
        self.monitor.observe_memory({'code': 'memory_operation_failed', 'tool': 'hold',
                                     'error_code': 'unknown'})
        self.monitor.observe_memory({'code': 'mcp_call', 'tool': 'breath_search'})
        self.monitor.observe_memory({'code': 'retry_succeeded', 'stage': 'mcp_verified',
                                     'tool': 'breath_search', 'attempt': 2})
        self.assertEqual((self.monitor.snapshot()['state'], self.monitor.snapshot()['classification']),
                         ('paused', 'unknown'))

    def test_regular_long_task_update_cannot_verify_pending_side_effect(self):
        self.monitor.observe_tool('write_file', '出错', autonomy=True)
        self.monitor.observe_tool('long_task_update', '成功')
        self.assertEqual((self.monitor.snapshot()['state'], self.monitor.snapshot()['classification']),
                         ('paused', 'unknown'))
        self.monitor.observe_tool('long_task_update', '成功', checkpoint_verified=True)
        self.assertEqual((self.monitor.snapshot()['state'], self.monitor.snapshot()['classification']),
                         ('recovered', 'safe_recovery'))

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

    def test_side_effect_pause_failure_never_reports_paused(self):
        self.activity.set = Mock(side_effect=OSError('fictional disk failure'))
        self.monitor.observe_tool('write_file', '出错', autonomy=True)
        status = self.monitor.snapshot()
        self.assertEqual((status['state'], status['classification'], status['code']),
                         ('attention', 'unknown', 'side_effect_pause_failed'))
        self.assertIn('Disable autonomy manually', status['action'])

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
