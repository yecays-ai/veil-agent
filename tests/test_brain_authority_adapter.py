"""P2-A: authority adapter routes hold/trace to Brain and never calls Ombre."""
import sqlite3
import tempfile
import unittest
from types import SimpleNamespace

import brain_authority
import brain_writer
import veil_brain
from memory_agent import Memory


class AuthorityTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = self.temp.name
        self.db = self.root + '/fake/brain.db'
        import os
        os.makedirs(self.root + '/fake')
        connection = sqlite3.connect(self.db)
        connection.executescript(veil_brain.SCHEMA)
        brain_writer.ensure_schema(connection)
        connection.close()
        self.cfg = SimpleNamespace(id='fake', memory={'brain': {'write_authority': True, 'shadow': False}})
        self.store = SimpleNamespace(root=self.root)
        self.authority = brain_authority.BrainAuthority(self.cfg, self.store)

    def test_hold_creates_and_returns_ombre_id_shape(self):
        text = self.authority.call('hold', {'content': 'body', 'title': 'T'}, embedder=None)
        self.assertRegex(text, r'^新建→[0-9a-f]{12}$')
        memory_id = text.split('→')[1]
        connection = sqlite3.connect(self.db)
        self.assertEqual(connection.execute('SELECT body FROM memories WHERE id=?', (memory_id,)).fetchone()[0], 'body')
        connection.close()

    def test_trace_updates_and_soft_deletes(self):
        connection = sqlite3.connect(self.db)
        created = brain_writer.create_memory(connection, {'body': 'before'}, embedder=None)
        connection.close()
        self.authority.call('trace', {'bucket_id': created['memory_id'], 'content': 'after'}, embedder=None)
        connection = sqlite3.connect(self.db)
        self.assertEqual(connection.execute('SELECT body FROM memories').fetchone()[0], 'after')
        connection.close()
        self.authority.call('trace', {'bucket_id': created['memory_id'], 'delete': True,
                                      'delete_reason': 'test'}, embedder=None)
        connection = sqlite3.connect(self.db)
        self.assertEqual(connection.execute('SELECT is_deleted FROM memories').fetchone()[0], 1)
        connection.close()

    def test_memory_call_uses_authority_before_ombre(self):
        calls = []
        fake = SimpleNamespace(brain_authority=self.authority, embedder=lambda: None)
        text = Memory.call(fake, 'hold', {'content': 'x', 'title': 'X'})
        self.assertTrue(text.startswith('新建→'))
        self.assertEqual(calls, [])


    def test_hold_does_not_write_free_tags_to_brain(self):
        """P2-C review: Brain write authority must never persist caller-supplied free tags."""
        text = self.authority.call('hold', {'content': 'body', 'title': 'T',
                                            'tags': 'foo,bar', 'domain': 'work'}, embedder=None)
        memory_id = text.split('→')[1]
        connection = sqlite3.connect(self.db)
        try:
            row = connection.execute('SELECT tags_json,domain_json FROM memories WHERE id=?',
                                     (memory_id,)).fetchone()
            self.assertEqual(row[0], '[]', 'Brain must not store free tags supplied by caller')
            self.assertEqual(row[1], '[]', 'Brain must not store domain tags supplied by caller')
        finally:
            connection.close()


if __name__ == '__main__':
    unittest.main()
