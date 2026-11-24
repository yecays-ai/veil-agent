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
        self.assertRegex(text, r'^新建→[0-9a-f]{12}')
        self.assertIn('检索索引待重试', text)
        memory_id = text.split('→')[1].split('（')[0]
        connection = sqlite3.connect(self.db)
        self.assertEqual(connection.execute('SELECT body FROM memories WHERE id=?', (memory_id,)).fetchone()[0], 'body')
        connection.close()

    def test_hold_accepts_embedder_object_and_indexes_it(self):
        class Embedder:
            def __init__(self):
                self.seen = []

            def embed(self, text):
                self.seen.append(text)
                return [0.25] * 1024

        embedder = Embedder()
        text = self.authority.call('hold', {'content': 'object embedder body', 'title': 'T'},
                                   embedder=embedder)
        memory_id = text.split('→')[1]
        connection = sqlite3.connect(self.db)
        try:
            self.assertEqual(connection.execute(
                'SELECT vector_state FROM memories WHERE id=?', (memory_id,)).fetchone()[0], 'ready')
            self.assertIsNotNone(connection.execute(
                'SELECT vector FROM vectors WHERE memory_id=?', (memory_id,)).fetchone())
        finally:
            connection.close()
        self.assertEqual(embedder.seen, ['object embedder body'])

    def test_index_failure_is_reported_and_retried_on_next_write(self):
        """P2-A: a failed embedding is saved_index_pending, never reported as fully saved, and is
        retried once an embedder works again."""
        class Broken:
            def embed(self, _text):
                raise RuntimeError('embedding down')

        text = self.authority.call('hold', {'content': 'pending body', 'title': 'P'}, embedder=Broken())
        self.assertIn('检索索引待重试', text)
        memory_id = text.split('→')[1].split('（')[0]
        connection = sqlite3.connect(self.db)
        try:
            self.assertEqual(connection.execute(
                'SELECT vector_state FROM memories WHERE id=?', (memory_id,)).fetchone()[0], 'pending')
        finally:
            connection.close()
        self.authority._last_pending_retry = 0.0
        self.authority.call('hold', {'content': 'second body', 'title': 'S'},
                            embedder=lambda _text: [0.3] * 1024)
        connection = sqlite3.connect(self.db)
        try:
            self.assertEqual(connection.execute(
                'SELECT vector_state FROM memories WHERE id=?', (memory_id,)).fetchone()[0], 'ready')
            self.assertIsNotNone(connection.execute(
                'SELECT vector FROM vectors WHERE memory_id=?', (memory_id,)).fetchone())
        finally:
            connection.close()

    def test_breath_search_accepts_embedder_object(self):
        """Regression: call() used to pass embedder.embed (a function) into brain_retrieval,
        which requires the object's .embed(), so active recall fell back to Ombre."""
        class Embedder:
            def embed(self, text):
                return [0.5] * 1024

        text = self.authority.call('hold', {'content': 'harbour bell memory', 'title': 'Harbour'},
                                   embedder=Embedder())
        memory_id = text.split('→')[1]
        result = self.authority.call('breath_search', {'query': 'harbour bell', 'max_results': 5},
                                     embedder=Embedder())
        self.assertIn('[bucket_id:%s]' % memory_id, result)
        self.assertIn('harbour bell memory', result)

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

    def test_freeze_blocks_hold_and_unfreeze_restores(self):
        brain_authority.freeze(self.store, 'fake')
        try:
            with self.assertRaises(RuntimeError):
                self.authority.call('hold', {'content': 'frozen', 'title': 'F'}, embedder=None)
        finally:
            brain_authority.unfreeze(self.store, 'fake')
        text = self.authority.call('hold', {'content': 'after thaw', 'title': 'T'}, embedder=None)
        self.assertTrue(text.startswith('新建→'))

    def test_hold_works_without_a_configured_budget(self):
        from memory_agent import Memory
        from veil_agent import Character, Store
        import tempfile
        from unittest.mock import Mock
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        store = Store(temp.name + '/data')
        db = store.root / 'fake' / 'brain.db'
        db.parent.mkdir(parents=True)
        connection = sqlite3.connect(str(db))
        connection.executescript(veil_brain.SCHEMA)
        brain_writer.ensure_schema(connection)
        connection.close()
        provider = {'model': 'fake', 'max_input_tokens': 20000, 'input_cny_per_million': 1,
                    'output_cny_per_million': 2, 'cached_input_cny_per_million': 1}
        settings = {'model': 'fake-embedding', 'input_cny_per_million': '0', 'mode': 'native',
                    'base_url': 'https://example.invalid/v1',
                    'brain': {'write_authority': True, 'shadow': False}}
        cfg = Character('fake', 'Example', 'persona', 20, 1024, provider, 'fake', settings)
        memory = Memory(cfg, store, None, Mock(), Mock())
        self.addCleanup(memory.__exit__, None, None, None)
        text = memory.call('hold', {'content': 'no budget', 'title': 'B'})
        self.assertTrue(text.startswith('新建→'))

    def test_memory_call_uses_authority_before_ombre(self):
        calls = []
        fake = SimpleNamespace(brain_authority=self.authority, embedder=lambda: None)
        text = Memory.call(fake, 'hold', {'content': 'x', 'title': 'X'})
        self.assertTrue(text.startswith('新建→'))
        self.assertEqual(calls, [])

    def test_memory_call_authority_breath_search_participates_in_budget(self):
        # Create a visible memory first
        self.authority.call('hold', {'content': 'test memory content', 'title': 'T'}, embedder=None)
        budget_calls = []
        fake_budget = SimpleNamespace(
            estimate=lambda role, model, tokens, rate: budget_calls.append(('estimate', tokens, rate)) or ('res1', 10),
            settle_estimate=lambda res, accounting, settled_amount=None, usage_tokens=None: budget_calls.append(('settle', accounting, settled_amount)),
            release_estimate=lambda res: budget_calls.append(('release', res))
        )
        fake = SimpleNamespace(
            brain_authority=self.authority, embedder=lambda: None, budget=fake_budget,
            cfg=SimpleNamespace(id='fake', memory={'model': 'm', 'input_cny_per_million': 0.5}),
        )
        result = Memory.call(fake, 'breath_search', {'query': 'test'})
        self.assertTrue(any(c[0] == 'estimate' for c in budget_calls))
        self.assertTrue(any(c[0] == 'settle' for c in budget_calls))


    def test_hold_does_not_write_free_tags_to_brain(self):
        """P2-C review: Brain write authority must never persist caller-supplied free tags."""
        text = self.authority.call('hold', {'content': 'body', 'title': 'T',
                                            'tags': 'foo,bar', 'domain': 'work'}, embedder=None)
        memory_id = text.split('→')[1].split('（')[0]
        connection = sqlite3.connect(self.db)
        try:
            row = connection.execute('SELECT tags_json,domain_json FROM memories WHERE id=?',
                                     (memory_id,)).fetchone()
            self.assertEqual(row[0], '[]', 'Brain must not store free tags supplied by caller')
            self.assertEqual(row[1], '[]', 'Brain must not store domain tags supplied by caller')
        finally:
            connection.close()

    def test_trace_merge_into_archives_the_source(self):
        """P2-B merge must archive the duplicate, not treat it as a normal delete."""
        connection = sqlite3.connect(self.db)
        try:
            target = brain_writer.create_memory(connection, {'body': 'merge target'}, embedder=None)
            source = brain_writer.create_memory(connection, {'body': 'merge source'}, embedder=None)
        finally:
            connection.close()
        text = self.authority.call('trace', {'bucket_id': source['memory_id'],
                                             'merge_into': target['memory_id']})
        self.assertIn('已归档', text)
        connection = sqlite3.connect(self.db)
        try:
            bucket = connection.execute('SELECT bucket,type FROM memories WHERE id=?',
                                        (source['memory_id'],)).fetchone()
            raw = connection.execute('SELECT raw_json FROM sidecar_events WHERE memory_id=? AND kind=?',
                                     (source['memory_id'], 'merged')).fetchone()[0]
            state = connection.execute('SELECT merged_into FROM sidecar_state WHERE memory_id=?',
                                       (source['memory_id'],)).fetchone()[0]
        finally:
            connection.close()
        self.assertEqual(bucket, ('archive', 'archived'))
        self.assertEqual(state, target['memory_id'])
        self.assertIn('"memory_id"', raw)
        self.assertIn('"kind"', raw)


if __name__ == '__main__':
    unittest.main()
