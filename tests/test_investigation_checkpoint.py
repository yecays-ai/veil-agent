import tempfile
import unittest
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


if __name__ == '__main__':
    unittest.main()
