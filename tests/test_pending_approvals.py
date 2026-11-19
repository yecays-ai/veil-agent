
import tempfile
import unittest
from pathlib import Path

from agent_types import UserError
from pending_approvals import PendingApprovals


class PendingApprovalsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.pending = PendingApprovals(self.root, 'test')

    def test_add_list_decide_and_context(self):
        item = self.pending.add('Peter 想保存 note.txt 到桌面。', 'run-1')
        self.assertEqual(item['status'], 'pending')
        listed = self.pending.list()
        self.assertEqual(listed['pending'], 1)
        self.assertIn('waiting for Lan', self.pending.context())
        decided = self.pending.decide(item['id'], 'approved')
        self.assertEqual(decided['status'], 'approved')
        context = self.pending.context()
        self.assertIn('Lan approved', context)
        self.assertEqual(self.pending.context(), '')  # told once
        with self.assertRaises(UserError):
            self.pending.decide(item['id'], 'approved')

    def test_duplicate_pending_is_not_added_twice(self):
        a = self.pending.add('same prompt')
        b = self.pending.add('same prompt')
        self.assertEqual(a['id'], b['id'])
        self.assertEqual(len(self.pending.list()['items']), 1)

    def test_expired_is_not_pending(self):
        item = self.pending.add('old request')
        item['expires_at'] = '2000-01-01T00:00:00+00:00'
        self.pending.path.parent.mkdir(parents=True, exist_ok=True)
        self.pending.path.write_text(__import__('json').dumps({'version': 1, 'items': [item]}), encoding='utf-8')
        self.assertEqual(self.pending.list()['pending'], 0)
        with self.assertRaises(UserError):
            self.pending.decide(item['id'], 'denied')
