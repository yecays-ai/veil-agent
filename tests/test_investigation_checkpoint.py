import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from investigation_checkpoint import InvestigationCheckpoint
from veil_agent import Store


class InvestigationCheckpointTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.store = Store(Path(self.tmp.name) / 'data')

    def test_persists_real_tool_states_and_resumes_from_goal_not_a_plan(self):
        checkpoint = InvestigationCheckpoint(self.store, 'test')
        checkpoint.record('Find the fictional answer.', 'request-1', 'read_text_file',
                          {'path': 'notes.txt'}, '成功', 'notes.txt', 'confirmed fictional fact')
        checkpoint.record('Find the fictional answer.', 'request-1', 'write_file',
                          {'path': 'answer.txt'}, '被拒绝', 'answer.txt', 'approval denied')
        checkpoint.record('Find the fictional answer.', 'request-1', 'write_file',
                          {'path': 'answer.txt'}, '结果未知', 'answer.txt', 'process ended after dispatch')
        checkpoint.interrupt('step_limit')

        saved = checkpoint.read()
        self.assertEqual(saved['status'], 'interrupted')
        self.assertEqual([item['state'] for item in saved['evidence']], ['成功', '被拒绝', '结果未知'])
        self.assertEqual([item['read_only'] for item in saved['evidence']], [True, False, False])

        reopened = InvestigationCheckpoint(self.store, 'test')
        context = reopened.resume_context('继续刚才的调查')
        self.assertIn('Find the fictional answer.', context)
        self.assertIn('fresh', context)
        self.assertIn('Never replay a rejected, stopped, or unknown side effect', context)
        self.assertNotIn('next_step', context)
        self.assertIn('fresh', checkpoint.resume_guard('read_text_file', {'path': 'notes.txt'}))
        self.assertIn('unknown outcome', checkpoint.resume_guard('write_file', {'path': 'answer.txt'}))

    def test_unrelated_chat_does_not_change_an_interrupted_checkpoint(self):
        checkpoint = InvestigationCheckpoint(self.store, 'test')
        checkpoint.record('Investigate A.', 'request-1', 'read_text_file',
                          {'path': 'a.txt'}, '出错', 'a.txt', 'fictional error')
        checkpoint.interrupt('context_limit')

        self.assertEqual(checkpoint.resume_context('聊点别的'), '')
        self.assertEqual(checkpoint.read()['goal'], 'Investigate A.')
        self.assertEqual(checkpoint.read()['reason'], 'context_limit')

    def test_plain_continue_resumes_but_continue_on_another_topic_does_not(self):
        self.assertTrue(InvestigationCheckpoint.resume_requested('继续'))
        self.assertTrue(InvestigationCheckpoint.resume_requested('继续刚才的调查'))
        self.assertFalse(InvestigationCheckpoint.resume_requested('继续聊电影'))

    def test_resumed_execution_records_unknown_outcome_and_latest_stop_reason(self):
        checkpoint = InvestigationCheckpoint(self.store, 'test')
        checkpoint.record('Investigate A.', 'request-1', 'write_file', {'path': 'a.txt'},
                          '结果未知', 'a.txt', 'execution started')
        checkpoint.interrupt('step_limit')

        checkpoint.record('Investigate A.', 'request-2', 'write_file', {'path': 'a.txt'},
                          '结果未知', 'a.txt', 'resumed execution started', resuming=True)
        checkpoint.interrupt('user_stop', 'request-2', resuming=True)

        saved = checkpoint.read()
        self.assertEqual(saved['reason'], 'user_stop')
        self.assertEqual(saved['evidence'][-1]['state'], '结果未知')
        self.assertIn('unknown outcome', checkpoint.resume_guard('write_file', {'path': 'a.txt'}))

    def test_rejected_stopped_and_unknown_side_effects_are_blocked(self):
        checkpoint = InvestigationCheckpoint(self.store, 'test')
        for state, path in (('被拒绝', 'rejected.txt'), ('已停止', 'stopped.txt'),
                            ('结果未知', 'unknown.txt')):
            checkpoint.record('Investigate A.', 'request-1', 'write_file', {'path': path},
                              state, path, 'synthetic outcome')
            self.assertIn('do not replay', checkpoint.resume_guard('write_file', {'path': path}))

    def test_interruption_survives_completion_unless_its_owner_resumes(self):
        checkpoint = InvestigationCheckpoint(self.store, 'test')
        checkpoint.record('Investigate A.', 'request-1', 'read_text_file',
                          {'path': 'a.txt'}, '成功', 'a.txt', 'confirmed fact')
        checkpoint.interrupt('step_limit', 'request-1')

        checkpoint.complete('unrelated-request')
        self.assertEqual(checkpoint.read()['status'], 'interrupted')
        checkpoint.complete('request-2', resuming=True)
        self.assertEqual(checkpoint.read()['status'], 'completed')

    def test_production_alias_is_classified_by_canonical_name(self):
        checkpoint = InvestigationCheckpoint(self.store, 'test')
        checkpoint.record('Read the notes.', 'request-1', 'read_the_notes',
                          {'path': 'notes.txt'}, '成功', 'notes.txt', 'confirmed fact',
                          canonical_name='read_text_file')
        checkpoint.interrupt('step_limit', 'request-1')

        saved = checkpoint.read()
        self.assertTrue(saved['evidence'][0]['read_only'])
        self.assertIn('fresh', checkpoint.resume_guard('read_the_notes', {'path': 'notes.txt'}))

        # A stale read-only result must be allowed through for an on-demand recheck.
        with checkpoint.store.mutex:
            saved = checkpoint._read()
            saved['evidence'][0]['time'] = (
                datetime.now(timezone.utc) - timedelta(minutes=20)).isoformat()
            checkpoint._save(saved)
        self.assertIsNone(checkpoint.resume_guard('read_the_notes', {'path': 'notes.txt'}))

    def test_production_alias_side_effect_is_still_blocked(self):
        checkpoint = InvestigationCheckpoint(self.store, 'test')
        checkpoint.record('Write the answer.', 'request-1', 'scribble',
                          {'path': 'answer.txt'}, '成功', 'answer.txt', 'written',
                          canonical_name='write_file')
        checkpoint.interrupt('step_limit', 'request-1')

        saved = checkpoint.read()
        self.assertFalse(saved['evidence'][0]['read_only'])
        self.assertIn('already completed',
                      checkpoint.resume_guard('scribble', {'path': 'answer.txt'}))

    def test_production_alias_exclusion_uses_canonical_name(self):
        checkpoint = InvestigationCheckpoint(self.store, 'test')
        checkpoint.record('Stay quiet.', 'request-1', 'ghost_mode', {},
                          '成功', 'silence', '', canonical_name='stay_silent')
        self.assertIsNone(checkpoint.read())


if __name__ == '__main__':
    unittest.main()
