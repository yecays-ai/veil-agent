import json
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path

from agent_types import now
from veil_agent import Store
from work_checkpoint import WorkCheckpoint


class FakeInvestigation:
    def __init__(self, task=None):
        self.task = task

    def read(self):
        return self.task


class FakeLongTasks:
    def __init__(self, task=None):
        self.task = task

    def read(self):
        return self.task


def evidence(tool='write_file', path='lab/a.html', state='成功', read_only=False, when=None):
    return {'time': when or now(), 'tool': tool, 'call': tool + ':x', 'state': state,
            'read_only': read_only, 'source': path, 'evidence': ''}


class WorkCheckpointTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.store = Store(Path(self.tmp.name) / 'data')
        self.investigation = FakeInvestigation()
        self.long_tasks = FakeLongTasks()
        self.checkpoint = WorkCheckpoint(self.store, 'test', self.investigation, self.long_tasks)

    def make(self, rows, goal='build the page'):
        self.investigation.task = {'id': 'i1', 'goal': goal, 'request_id': 'r1', 'status': 'completed',
                                   'reason': '', 'updated_at': now(), 'evidence': rows}

    def test_a_side_effect_turn_becomes_an_open_checkpoint(self):
        self.make([evidence(), evidence(tool='read_text_file', path='lab/a.html', state='成功', read_only=True)])
        saved = self.checkpoint.capture()
        self.assertEqual(saved['status'], 'open')
        self.assertEqual(saved['goal'], 'build the page')
        self.assertEqual(len(saved['confirmed']), 1)
        self.assertEqual(saved['next_step'], '')

    def test_a_read_only_turn_is_not_carried_forward(self):
        self.make([evidence(tool='read_text_file', read_only=True)])
        self.assertIsNone(self.checkpoint.capture())
        self.assertIsNone(self.checkpoint.view())

    def test_goal_comes_from_the_long_task_and_brings_its_next_step(self):
        self.long_tasks.task = {'task_id': 't1', 'title': 'Six-day hunt', 'goal': 'finish', 'status': 'active',
                                'next_step': 'check the third clue', 'updated_at': now()}
        self.make([])
        saved = self.checkpoint.capture()
        self.assertEqual(saved['goal'], 'Six-day hunt')
        self.assertEqual(saved['goal_source'], 'long_task')
        self.assertEqual(saved['next_step'], 'check the third clue')

    def test_unconfirmed_and_rejected_actions_keep_their_state(self):
        self.make([evidence(state='结果未知'), evidence(tool='edit_file', state='被拒绝')])
        saved = self.checkpoint.capture()
        self.assertEqual(len(saved['unconfirmed']), 1)
        self.assertEqual(len(saved['failed']), 1)

    def test_a_completed_long_task_closes_the_checkpoint(self):
        self.long_tasks.task = {'task_id': 't1', 'title': 'Done thing', 'goal': 'x', 'status': 'active',
                                'next_step': '', 'updated_at': now()}
        self.make([])
        self.checkpoint.capture()
        self.long_tasks.task['status'] = 'completed'
        saved = self.checkpoint.capture()
        self.assertEqual(saved['status'], 'closed')

    def test_context_is_offered_once_per_conversation(self):
        self.make([evidence()])
        self.checkpoint.capture()
        first = self.checkpoint.context('conversation:0', 0)
        self.assertIn('goal: build the page', first)
        self.assertEqual(self.checkpoint.context('conversation:0', 0), '')
        second = self.checkpoint.context('conversation:120', 0)
        self.assertIn('goal: build the page', second)

    def test_a_dropped_history_chunk_offers_it_again(self):
        self.make([evidence()])
        self.checkpoint.capture()
        self.assertNotEqual(self.checkpoint.context('conversation:0', 20), '')
        self.assertEqual(self.checkpoint.context('conversation:0', 20), '')
        self.assertNotEqual(self.checkpoint.context('conversation:0', 40), '')

    def test_lan_can_end_it_and_it_is_not_offered_again(self):
        self.make([evidence()])
        self.checkpoint.capture()
        self.checkpoint.close()
        self.assertEqual(self.checkpoint.view()['status'], 'closed')
        self.assertEqual(self.checkpoint.context('conversation:9', 0), '')
        self.checkpoint.capture()
        self.assertEqual(self.checkpoint.view()['status'], 'closed')

    def test_an_unchanged_turn_does_not_extend_the_expiry(self):
        self.make([evidence()])
        first = self.checkpoint.capture()
        stamp = first['updated_at']
        again = self.checkpoint.capture()
        self.assertEqual(again['updated_at'], stamp)

    def test_old_work_is_expired(self):
        self.make([evidence()])
        self.checkpoint.capture()
        value = self.checkpoint.read()
        value['updated_at'] = (datetime.now().astimezone() - timedelta(hours=49)).isoformat()
        self.checkpoint._save(value)
        self.assertEqual(self.checkpoint.view()['status'], 'expired')
        self.assertEqual(self.checkpoint.context('conversation:1', 0), '')

    def test_new_work_replaces_a_closed_one(self):
        self.make([evidence()])
        self.checkpoint.capture()
        self.checkpoint.close()
        self.make([evidence(path='lab/b.html')], goal='another task')
        saved = self.checkpoint.capture()
        self.assertEqual(saved['status'], 'open')
        self.assertEqual(saved['goal'], 'another task')

    def test_broken_file_is_not_shown_and_never_injected(self):
        path = self.store.root / 'test' / 'work-checkpoint.json'
        path.parent.mkdir(parents=True)
        path.write_text('{not json', encoding='utf-8')
        self.assertIsNone(self.checkpoint.view())
        self.assertEqual(self.checkpoint.context('conversation:0', 0), '')

    def test_a_later_turn_without_a_recorded_goal_does_not_erase_the_known_goal(self):
        self.make([evidence()])
        self.checkpoint.capture()
        self.make([evidence(path='lab/b.html')], goal='')
        saved = self.checkpoint.capture()
        self.assertEqual(saved['goal'], 'build the page')

    def test_peter_config_enables_the_checkpoint(self):
        import tomllib
        root = Path(__file__).resolve().parents[1]
        with (root / 'characters' / 'peter' / 'character.toml').open('rb') as handle:
            peter = tomllib.load(handle)
        self.assertTrue(peter['context']['work_checkpoint'])


if __name__ == '__main__':
    unittest.main()
