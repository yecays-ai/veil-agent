import json
import tempfile
import unittest
from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path
from unittest.mock import Mock

from autonomy import AutonomyController
from veil_agent import ActivityState, Budget, BudgetExceeded, Chat, Store, UserError, load_character
from web_agent import Hub


class AutonomyTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        (self.root / 'characters' / 'test').mkdir(parents=True)
        (self.root / 'persona').mkdir()
        (self.root / 'persona' / 'test.md').write_text('Fictional test persona', encoding='utf-8')
        (self.root / 'characters' / 'test' / 'character.toml').write_text('''name = "Test"
provider = "fake"
persona = "persona/test.md"
max_output_tokens = 50
[providers.fake]
model = "fake-model"
base_url = "https://example.invalid/v1"
api_key_env = "FAKE_KEY"
max_input_tokens = 6000
input_cny_per_million = "2"
cached_input_cny_per_million = "1"
output_cny_per_million = "8"
[autonomy]
interval_minutes = 1
daily_max_runs = 2
daily_budget_cny = "2"
''', encoding='utf-8')
        self.cfg = load_character(self.root, 'test')
        self.store = Store(self.root / 'data')
        self.model = Mock(return_value=(
            '决定：暂不行动\n原因：没有适合继续的工作。\n当前关注：实验室整理\n下一步：等待新信息。',
            100, 30, 20))
        self.budget = Budget(self.store)
        self.chat = Chat(self.cfg, self.store, self.budget, self.model)
        self.activity = ActivityState(self.store.root / 'test' / 'resident-state.json')
        self.hub = Hub('Test', self.activity)
        self.controller = AutonomyController(self.chat, self.hub)

    def test_autonomous_wake_is_logged_without_changing_chat_history(self):
        self.activity.set(True)
        enabled_at = datetime.fromisoformat(self.activity.snapshot()['enabled_at']) - timedelta(minutes=2)
        self.activity.path.write_text(json.dumps({'enabled': True, 'enabled_at': enabled_at.isoformat()}),
                                      encoding='utf-8')
        self.controller.started_at = enabled_at
        self.assertTrue(self.controller.due())
        self.controller.run()
        rows = self.store.read('test/autonomy.jsonl')
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['result'], '搁置')
        self.assertEqual(rows[0]['decision'], '暂不行动')
        self.assertEqual(rows[0]['reason'], '没有适合继续的工作。')
        self.assertEqual(rows[0]['current_focus'], '实验室整理')
        self.assertEqual(rows[0]['next_step'], '等待新信息。')
        self.assertEqual(self.store.read('test/chat.jsonl'), [])
        self.assertEqual(self.budget.category_totals('test')[0], Decimal('0.000420'))
        self.assertEqual(self.controller.command().split(' | ')[2], '搁置：按 Peter 本次决定结束。')

    def test_autonomy_budget_blocks_before_model_request(self):
        with self.budget.autonomous('test', Decimal('0.000001')):
            with self.assertRaises(BudgetExceeded):
                self.budget.reserve(self.cfg)
        self.model.assert_not_called()

    def test_chat_cache_display_excludes_autonomy_requests(self):
        with self.budget.autonomous('test'):
            request_id, _ = self.budget.reserve(self.cfg)
            self.budget.settle(request_id, self.cfg, 100, 20, 50)
        self.assertIn('还没有聊天模型的用量记录', self.budget.cache_display())

    def test_incomplete_autonomy_record_stops_safely(self):
        self.store.append('test/autonomy.jsonl', {'started_at': 'not-a-time', 'result': '搁置'})
        with self.assertRaises(UserError):
            AutonomyController(self.chat, self.hub)

    def test_paused_service_has_no_wake_and_daily_cap_hides_next_wake(self):
        self.assertIsNone(self.controller.next_wake())
        self.activity.set(True)
        self.controller.rows = [{'started_at': self.budget.clock(), 'result': '搁置'},
                                {'started_at': self.budget.clock(), 'result': '搁置'}]
        self.assertIsNone(self.controller.next_wake())

    def test_chat_can_enqueue_once_and_interrupt_autonomy_without_busy_indicator(self):
        self.hub.autonomy_busy = True
        self.assertEqual(self.hub.submit('continue chat', 'one'), 'accepted')
        self.assertEqual(self.hub.submit('continue chat', 'one'), 'duplicate')
        self.assertEqual(self.hub.submit('another message', 'two'), 'busy')
        self.assertTrue(self.hub.autonomy_interrupted.is_set())
        self.assertFalse(self.hub.snapshot(0)['busy'])
        self.assertEqual(self.hub.inbox.get_nowait(), 'continue chat')

    def test_chat_queued_during_due_check_wins_over_autonomy_wake(self):
        self.activity.set(True)
        self.hub.autonomy = self.controller

        def due_with_chat():
            self.hub.inbox.put('continue chat')
            return True
        self.controller.due = due_with_chat
        self.assertEqual(self.hub.input_fn(), 'continue chat')
        self.assertFalse(self.hub.autonomy_busy)

    def test_interrupt_arriving_before_run_is_not_cleared(self):
        self.activity.set(True)
        self.hub.autonomy_interrupted.set()
        self.controller.run()
        row = self.store.read('test/autonomy.jsonl')[-1]
        self.assertEqual(row['result'], '被打断')


if __name__ == '__main__':
    unittest.main()
