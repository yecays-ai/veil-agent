"""Ticket 139: Peter's own wake rhythm, several alarms with notes, and a natural wake that varies."""
import json
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from autonomy import AutonomyController
from tools_agent import tool_policy
from veil_agent import ActivityState, Budget, Chat, Store, load_character
from web_agent import Hub


CONFIG = '''name = "Test"
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
interval_minutes = 90
daily_max_runs = 12
daily_budget_cny = "3"
min_alarm_gap_minutes = 15
quiet_hours = "01:00-06:00"
quiet_hours_enabled = false
'''


class WakeControlTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        (root / 'characters' / 'test').mkdir(parents=True)
        (root / 'persona').mkdir()
        (root / 'persona' / 'test.md').write_text('Fictional test persona', encoding='utf-8')
        (root / 'characters' / 'test' / 'character.toml').write_text(CONFIG, encoding='utf-8')
        self.cfg = load_character(root, 'test')
        self.store = Store(root / 'data')
        model = Mock(return_value=('Decision: rest\nReason: x\nFocus: x\nNext step: x', 100, 30, 0))
        self.chat = Chat(self.cfg, self.store, Budget(self.store, autonomy_daily=self.cfg.autonomy['daily_budget_cny']),
                         model)
        self.activity = ActivityState(self.store.root / 'test' / 'resident-state.json')
        self.activity.set(True)
        self.controller = AutonomyController(self.chat, Hub('Test', self.activity))

    def past_alarm(self, alarm_id, minutes_ago, note):
        return {'alarm_id': alarm_id, 'state': 'pending', 'reason': note,
                'at': (datetime.now().astimezone() - timedelta(minutes=minutes_ago)).isoformat()}

    def test_natural_wake_varies_but_a_restart_gets_the_same_time(self):
        start = datetime(2026, 10, 27, 8, 0).astimezone()
        waits = [self.controller._natural_wait(start + timedelta(minutes=7 * i), 90) for i in range(400)]
        self.assertGreater(len(set(waits)), 20)
        self.assertTrue(70 <= sum(waits) / len(waits) <= 110)
        self.assertTrue(all(20 <= wait <= 270 for wait in waits))
        self.assertEqual(self.controller._natural_wait(start, 90), self.controller._natural_wait(start, 90))

    def test_quieter_and_custom_change_the_pace_and_expire_back_to_normal(self):
        start = datetime(2026, 10, 27, 8, 0).astimezone()
        quieter = [self.controller._natural_wait(start + timedelta(minutes=7 * i), 225) for i in range(200)]
        self.assertGreater(sum(quieter) / len(quieter), 150)
        result, state, _ = self.controller.set_wake_rhythm(
            {'mode': 'quieter', 'minutes': 120, 'reason': 'I want to read quietly for a while.'})
        self.assertEqual(state, '成功')
        self.assertIn('quieter', self.controller.wake_count_text())
        self.assertIn('read quietly', self.controller.wake_count_text())
        _, state, _ = self.controller.set_wake_rhythm({'mode': 'custom', 'minutes': 60, 'reason': 'x'})
        self.assertEqual(state, '被拒绝')
        _, state, _ = self.controller.set_wake_rhythm(
            {'mode': 'custom', 'minutes': 60, 'interval_minutes': 45, 'reason': 'Busy afternoon.'})
        self.assertEqual(state, '成功')
        self.assertIn('every 45 minutes', self.controller.rhythm_text())
        later = datetime.now().astimezone() + timedelta(minutes=61)
        with patch('autonomy.datetime') as clock:
            clock.now.return_value = later
            clock.fromisoformat = datetime.fromisoformat
            self.assertEqual(self.controller._read_rhythm()['mode'], 'normal')
        _, state, _ = self.controller.set_wake_rhythm({'mode': 'normal'})
        self.assertEqual(state, '成功')
        self.assertTrue(self.controller.rhythm_text().startswith('normal'))

    def test_silent_stops_natural_wakes_but_his_alarm_still_rings(self):
        _, state, _ = self.controller.set_wake_rhythm(
            {'mode': 'silent', 'minutes': 180, 'reason': 'Lan is asleep.'})
        self.assertEqual(state, '成功')
        self.controller.started_at -= timedelta(hours=10)
        self.assertIsNone(self.controller.next_wake())
        self.assertIn('Peter 自己暂停了自然醒', self.controller.status()['next_wake_reason'])
        self.assertFalse(self.controller.due())
        self.controller._save_alarm(self.past_alarm('a1', 1, 'Finish the sentence I left.'))
        self.assertTrue(self.controller.claim_due())
        self.assertEqual(self.controller.claimed_wake_reason, 'Finish the sentence I left.')

    def test_two_due_alarms_ring_as_one_wake_with_both_notes(self):
        self.controller._save_alarm(self.past_alarm('a1', 3, 'Check the poem.'))
        self.controller._save_alarm(self.past_alarm('a2', 1, 'Tell Lan good morning.'))
        self.assertTrue(self.controller.claim_due())
        self.assertEqual(self.controller.claimed_wake_reason, ['Check the poem.', 'Tell Lan good morning.'])
        context = self.controller._context(self.controller.claimed_wake_reason)
        self.assertIn('Tell Lan good morning.', context)
        self.assertEqual(self.controller._pending_alarms(), [])
        self.assertFalse(AutonomyController(self.chat, self.controller.hub).claim_due())

    def test_missed_alarms_are_reported_together_once(self):
        self.controller._save_alarm(self.past_alarm('a1', 3, 'Check the poem.'))
        self.controller._save_alarm(self.past_alarm('a2', 1, 'Tell Lan good morning.'))
        self.controller._eligible_count = lambda day=None: self.controller.daily_max_runs
        self.assertFalse(self.controller.claim_due())
        text = self.controller._missed_alarm_text()
        self.assertEqual(text.count('did not happen (daily wake limit reached)'), 2)
        self.assertIn('Tell Lan good morning.', text)
        self.assertEqual(self.controller._missed_alarm_text(), '')

    def test_alarm_file_from_before_139_is_still_read(self):
        legacy = self.past_alarm('old', -120, 'Old single alarm.')
        self.store.replace('test/autonomy-alarm.json', legacy)
        restarted = AutonomyController(self.chat, self.controller.hub)
        self.assertEqual([alarm['reason'] for alarm in restarted._pending_alarms()], ['Old single alarm.'])
        restarted.set_next_wake({'at': (datetime.now().astimezone() + timedelta(minutes=30)).isoformat(),
                                 'reason': 'New one.'})
        self.assertEqual(len(restarted._pending_alarms()), 2)

    def test_rhythm_tool_is_offered_as_a_direct_action(self):
        names = {schema['function']['name'] for schema in self.chat.tool_schemas()}
        self.assertIn('set_wake_rhythm', names)
        self.assertEqual(tool_policy('set_wake_rhythm'), 'allow')
        call = SimpleNamespace(function=SimpleNamespace(name='set_wake_rhythm', arguments=json.dumps({
            'mode': 'quieter', 'minutes': 60, 'reason': 'Resting.'})))
        execution = self.chat._execute_tool_call(call, 'request', 'call', False)
        self.assertEqual(execution.state, '成功')
        self.assertIn('Scheduled wakes you set still ring', execution.result)


if __name__ == '__main__':
    unittest.main()
