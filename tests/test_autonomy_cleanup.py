"""Ticket 133: wake count, quiet hours, honest approvals, own-work rewrite/trash, wake input window."""
import json
import tempfile
import unittest
from dataclasses import replace
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from agent_types import NOT_ASKED_TEXT, not_asked
from autonomy import AutonomyController
from desktop_files import DesktopFileTools
from tools_agent import ALLOW, ASK, DENY, LAB_ONLY, FilesystemTools
from veil_agent import ActivityState, Budget, Chat, Store, load_character, parse_quiet_hours, self_facts
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
daily_max_runs = 3
daily_budget_cny = "3"
quiet_hours = "01:00-06:00"
max_input_tokens = 12000
'''


class WakeCountAndQuietHoursTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        (self.root / 'characters' / 'test').mkdir(parents=True)
        (self.root / 'persona').mkdir()
        (self.root / 'persona' / 'test.md').write_text('Fictional test persona', encoding='utf-8')
        (self.root / 'characters' / 'test' / 'character.toml').write_text(CONFIG, encoding='utf-8')
        self.cfg = load_character(self.root, 'test')
        self.store = Store(self.root / 'data')
        self.model = Mock(return_value=('Decision: rest\nReason: nothing\nFocus: none\nNext step: none', 100, 30, 0))
        self.budget = Budget(self.store, autonomy_daily=self.cfg.autonomy['daily_budget_cny'])
        self.chat = Chat(self.cfg, self.store, self.budget, self.model)
        self.activity = ActivityState(self.store.root / 'test' / 'resident-state.json')
        self.activity.set(True)
        self.controller = AutonomyController(self.chat, Hub('Test', self.activity))

    def add_row(self, started_at):
        self.controller.rows.append({
            'started_at': started_at, 'finished_at': started_at, 'decision': '', 'reason': '', 'tools': [],
            'lab_files_changed': [], 'result': '搁置', 'result_reason': '', 'current_focus': '',
            'next_step': '', 'cost_cny': '0'})

    def test_config_parses_quiet_hours_and_wake_input_window(self):
        self.assertEqual(self.cfg.autonomy['quiet_hours'], ((1, 0), (6, 0)))
        self.assertTrue(self.cfg.autonomy['quiet_hours_enabled'])
        self.assertEqual(self.cfg.autonomy['max_input_tokens'], 12000)
        self.assertIsNone(parse_quiet_hours(''))
        for bad in ('1-6', '25:00-06:00', '01:00-01:00'):
            with self.assertRaises(ValueError):
                parse_quiet_hours(bad)

    def test_wake_context_and_chat_ledger_show_todays_count(self):
        self.assertIn('Autonomous wakes today: 0 of 3 used', self.controller._context())
        self.add_row(datetime.now().astimezone().isoformat())
        self.assertIn('Autonomous wakes today: 1 of 3 used; the count resets at local midnight.',
                      self.controller._context())
        self.chat.send('hi')
        sent = self.model.call_args.args[0]
        self.assertIn('Autonomous wakes today: 1 of 3 used', sent[-2]['content'])
        self.assertIn('quiet hours 01:00-06:00 are on', sent[-2]['content'])

    def test_quiet_window_detection_and_switch(self):
        day = datetime.now().astimezone().replace(hour=12, minute=0, second=0, microsecond=0)
        self.assertTrue(self.controller.in_quiet(day.replace(hour=3)))
        self.assertTrue(self.controller.in_quiet(day.replace(hour=1)))
        self.assertFalse(self.controller.in_quiet(day.replace(hour=6)))
        self.assertFalse(self.controller.in_quiet(day.replace(hour=0, minute=59)))
        self.assertTrue(self.controller.set_quiet_enabled(False))
        self.assertFalse(self.controller.in_quiet(day.replace(hour=3)))
        reopened = AutonomyController(self.chat, Hub('Test', self.activity))
        self.assertFalse(reopened.quiet_enabled())
        self.assertIn('quiet hours', self.chat.capability_history_record or '')
        status = reopened.status()
        self.assertEqual((status['quiet_hours'], status['quiet_enabled'], status['daily_max_runs']),
                         ('01:00-06:00', False, 3))

    def test_no_wake_during_quiet_hours_and_missed_alarm_is_reported_once(self):
        past = (datetime.now().astimezone() - timedelta(minutes=5)).isoformat()
        self.controller._save_alarm({'alarm_id': 'a1', 'at': past, 'reason': 'send piece 4', 'state': 'pending'})
        with patch.object(self.controller, 'in_quiet', return_value=True):
            self.assertFalse(self.controller.claim_due())
        alarm = self.controller._read_alarm()
        self.assertEqual((alarm['state'], alarm['missed_reason']), ('missed', 'night quiet hours'))
        context = self.controller._context()
        self.assertIn('did not happen (night quiet hours)', context)
        self.assertIn('send piece 4', context)
        self.assertIn('did not happen', self.controller._context())  # until a wake request succeeds
        self.controller._mark_missed_reported()
        self.assertNotIn('did not happen', self.controller._context())

    def test_set_next_wake_refuses_quiet_hours_and_reports_remaining_wakes(self):
        tomorrow = datetime.now().astimezone() + timedelta(days=1)
        result, state, _ = self.controller.set_next_wake(
            {'at': tomorrow.replace(hour=3, minute=0, second=0, microsecond=0).isoformat(), 'reason': 'x'})
        self.assertEqual(state, '被拒绝')
        self.assertIn('quiet hours', result)
        result, state, _ = self.controller.set_next_wake(
            {'at': tomorrow.replace(hour=9, minute=0, second=0, microsecond=0).isoformat(), 'reason': 'x'})
        self.assertEqual(state, '成功')
        self.assertIn('3 of 3 wakes left', result)

    def test_next_wake_shown_after_quiet_hours(self):
        tomorrow = datetime.now().astimezone() + timedelta(days=1)
        periodic = tomorrow.replace(hour=3, minute=0, second=0, microsecond=0)
        with patch.object(self.controller, '_periodic_next_wake', return_value=periodic):
            self.assertEqual(self.controller.next_wake(), periodic.replace(hour=6))
            self.controller.set_quiet_enabled(False)
            self.assertEqual(self.controller.next_wake(), periodic)

    def test_wakes_use_the_larger_input_window_and_chat_keeps_its_own(self):
        seen = []
        self.model.side_effect = lambda *_: (
            seen.append(self.chat.cfg.provider['max_input_tokens']) or ('Decision: rest', 10, 5, 0))
        self.chat.send('wake', autonomy=True)
        self.chat.send('hi')
        self.assertEqual(seen, [12000, 6000])
        self.assertEqual(self.chat.cfg.provider['max_input_tokens'], 6000)

    def test_self_facts_name_real_channels_and_memory_page(self):
        stable, _ = self_facts(self.cfg, self.budget, memory=True, capabilities=SimpleNamespace(description=''),
                               channels=('email', 'qq'))
        self.assertIn('Lan talks with you in VEIL (desktop app or terminal), and also on email and QQ', stable)
        self.assertIn('memory page', stable)
        self.assertIn('capability_history', stable)
        self.assertNotIn('Unlisted abilities', stable)
        self.assertNotIn('/delete', stable)


class HonestApprovalTests(unittest.TestCase):
    def test_outside_lab_write_during_autonomy_says_lan_was_not_asked(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            (root / 'characters/test').mkdir(parents=True)
            (root / 'persona').mkdir()
            (root / 'persona/test.md').write_text('Fictional persona', encoding='utf-8')
            (root / 'characters/test/character.toml').write_text(
                CONFIG.split('[autonomy]')[0] + '[tools]\nenabled = true\n', encoding='utf-8')
            cfg = load_character(root, 'test')
            store = Store(root / 'data')
            worker = SimpleNamespace(tools=[SimpleNamespace(name=name, description=name, inputSchema={})
                                            for name in ALLOW | ASK | DENY | LAB_ONLY],
                                     call=Mock(), close=Mock())
            with patch('tools_agent.shutil.which', return_value='fake-node'), \
                    patch('tools_agent._McpWorker', return_value=worker):
                tools = FilesystemTools(root, cfg, store)
            result, state, _ = tools.invoke('write_file', {'path': 'note.md', 'content': 'x'}, not_asked)
            self.assertEqual((result, state), (NOT_ASKED_TEXT, '被拒绝'))
            (tools.workspace / 'note.md').write_text('old', encoding='utf-8')
            result, state, _ = tools.invoke('append_file', {'path': 'note.md', 'content': 'x'}, not_asked)
            self.assertEqual((result, state), (NOT_ASKED_TEXT, '被拒绝'))
            result, _, _ = tools.invoke('append_file', {'path': 'note.md', 'content': 'x'}, lambda _: 'n')
            self.assertIn('Lan did not approve', result)

            # Lab trash: move in and back out, nothing deleted.
            (tools.lab / 'play').mkdir()
            (tools.lab / 'play/old.html').write_text('<p>old</p>', encoding='utf-8')
            move = lambda s, d: tools.invoke('move_lab_file', {'source': s, 'destination': d}, not_asked)
            self.assertEqual(move('lab/play/old.html', 'lab/trash/other.html')[1], '被拒绝')
            self.assertEqual(move('lab/play/old.html', 'lab/trash/play/old.html')[1], '成功')
            self.assertEqual((tools.lab / 'trash/play/old.html').read_text(encoding='utf-8'), '<p>old</p>')
            self.assertEqual(move('lab/trash/play/old.html', 'lab/play/old.html')[1], '成功')


class OwnDesktopWorkTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        for name in ('Desktop', 'Documents', 'Pictures', 'lab'):
            (self.root / name).mkdir()
        self.tools = DesktopFileTools(True, folder_for=lambda name: self.root / name.capitalize())
        self.tools.bind_own_work(self.root / 'data/desktop-created.json', self.root / 'data/versions',
                                 lambda: self.root / 'lab' / 'trash')

    def create(self, path, content, replace_existing=False):
        args = {'folder': 'desktop', 'path': path, 'content': content}
        if replace_existing:
            args['replace'] = True
        return self.tools.invoke('desktop_create_file', args)

    def test_rewrites_own_file_keeping_previous_version(self):
        self.assertEqual(self.create('art/sun.svg', '<svg>1</svg>')[1], '成功')
        self.assertEqual(self.create('art/sun.svg', '<svg>2</svg>')[1], '被拒绝')
        result, state, _ = self.create('art/sun.svg', '<svg>2</svg>', True)
        self.assertEqual(state, '成功', result)
        self.assertEqual((self.root / 'Desktop/art/sun.svg').read_text(encoding='utf-8'), '<svg>2</svg>')
        kept = list((self.root / 'data/versions').rglob('*sun.svg'))
        self.assertEqual([p.read_text(encoding='utf-8') for p in kept], ['<svg>1</svg>'])
        self.assertEqual(self.create('art/sun.svg', '<svg>3</svg>', True)[1], '成功')

    def test_lans_files_and_files_she_changed_are_never_touched(self):
        hers = self.root / 'Desktop/diary.txt'
        hers.write_text('Lan wrote this', encoding='utf-8')
        self.assertEqual(self.create('diary.txt', 'x', True)[1], '被拒绝')
        self.assertEqual(self.tools.invoke('desktop_move_to_trash', {'folder': 'desktop', 'path': 'diary.txt'})[1],
                         '被拒绝')
        self.assertEqual(hers.read_text(encoding='utf-8'), 'Lan wrote this')
        self.assertEqual(self.create('note.md', 'mine')[1], '成功')
        (self.root / 'Desktop/note.md').write_text('Lan edited it', encoding='utf-8')
        self.assertEqual(self.create('note.md', 'mine again', True)[1], '被拒绝')
        self.assertEqual((self.root / 'Desktop/note.md').read_text(encoding='utf-8'), 'Lan edited it')

    def test_moves_own_file_into_lab_trash(self):
        self.assertEqual(self.create('mess/toy.html', '<b>toy</b>')[1], '成功')
        result, state, _ = self.tools.invoke('desktop_move_to_trash', {'folder': 'desktop', 'path': 'mess/toy.html'})
        self.assertEqual(state, '成功', result)
        self.assertFalse((self.root / 'Desktop/mess/toy.html').exists())
        self.assertEqual((self.root / 'lab/trash/desktop/mess/toy.html').read_text(encoding='utf-8'), '<b>toy</b>')
        ledger = json.loads((self.root / 'data/desktop-created.json').read_text(encoding='utf-8'))
        self.assertEqual(ledger, {})


if __name__ == '__main__':
    unittest.main()
