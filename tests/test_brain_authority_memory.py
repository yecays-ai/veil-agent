"""P2-A: a real Memory instance writes to Brain when authority is on, without Ombre."""
import sqlite3
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch
from unittest.mock import Mock

import brain_writer
import veil_brain
from memory_agent import Memory
from veil_agent import Budget, Character, Store


class FakeEmbedder:
    usage_tokens = None

    def embed(self, _text):
        return [0.4] * 1024


class MemoryAuthorityTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = Store(self.temp.name + '/data')
        self.db = self.store.root / 'fake' / 'brain.db'
        self.db.parent.mkdir(parents=True)
        connection = sqlite3.connect(str(self.db))
        connection.executescript(veil_brain.SCHEMA)
        brain_writer.ensure_schema(connection)
        connection.close()
        provider = {'model': 'fake', 'max_input_tokens': 20000, 'input_cny_per_million': 1,
                    'output_cny_per_million': 2, 'cached_input_cny_per_million': 1}
        settings = {'model': 'fake-embedding', 'input_cny_per_million': '0', 'mode': 'native',
                    'base_url': 'https://example.invalid/v1',
                    'brain': {'write_authority': True, 'shadow': False}}
        cfg = Character('fake', 'Example', 'persona', 20, 1024, provider, 'fake', settings)
        self.memory = Memory(cfg, self.store, Budget(self.store), Mock(), Mock())
        self.memory._embedder = FakeEmbedder()
        self.addCleanup(self.memory.__exit__, None, None, None)

    def test_is_hidden_includes_dont_surface(self):
        connection = sqlite3.connect(str(self.db))
        try:
            row = brain_writer.create_memory(connection, {'body': 'surface off'}, embedder=None)
            connection.execute('UPDATE memories SET dont_surface=1 WHERE id=?', (row['memory_id'],))
            connection.commit()
        finally:
            connection.close()
        self.assertTrue(self.memory.is_hidden(row['memory_id']))

    def test_brain_search_failure_falls_back_to_ombre_backend(self):
        self.memory.backend = SimpleNamespace(call=lambda name, args: 'OMBRE_FALLBACK')
        self.memory.last_recall_debug = {}
        self.memory.retrieval_settings = {'backend': 'brain', 'weight_vector': 0.65,
                                         'weight_keyword': 0.35}
        with patch('memory_agent.brain_retrieval_search', side_effect=RuntimeError('down')):
            self.assertEqual(self.memory.search_candidates('anything', 5), 'OMBRE_FALLBACK')
        self.assertEqual(self.memory.last_recall_debug['retrieval']['fallback'], 'RuntimeError')

    def test_hold_writes_brain_and_is_parseable(self):
        text = self.memory.call('hold', {'content': 'saved through authority', 'title': 'T'})
        self.assertRegex(text, r'^新建→[0-9a-f]{12}$')
        memory_id = text.split('→')[1]
        connection = sqlite3.connect(str(self.db))
        try:
            row = connection.execute('SELECT body,vector_state FROM memories WHERE id=?',
                                     (memory_id,)).fetchone()
        finally:
            connection.close()
        self.assertEqual(row[0], 'saved through authority')
        self.assertEqual(row[1], 'ready')

    def test_archive_page_reads_brain_not_stale_vault(self):
        """P2-B: after the switch the vault archive is an old Ombre copy; the page must read Brain."""
        from memory_native import NativeMemoryTools
        connection = sqlite3.connect(str(self.db))
        try:
            created = brain_writer.create_memory(
                connection, {'body': 'archived through brain', 'title': 'Archived Brain',
                             'bucket': 'archive', 'type': 'archived'}, embedder=None)
        finally:
            connection.close()
        stale = self.store.root / 'fake' / 'memory' / 'archive'
        stale.mkdir(parents=True)
        (stale / 'old.md').write_text('---\nid: stale\n---\nstale ombre row\n', encoding='utf-8')
        self.memory.backend = SimpleNamespace()
        rows = NativeMemoryTools(self.memory).panel_archived()
        ids = [row['memory_id'] for row in rows]
        self.assertIn(created['memory_id'], ids)
        self.assertNotIn('stale', ids)
        self.assertEqual(next(row for row in rows if row['memory_id'] == created['memory_id'])['body'],
                         'archived through brain')


if __name__ == '__main__':
    unittest.main()
