"""P2-B: opening, search and context read from Brain when write authority is on."""
import sqlite3
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import Mock

from veil.brain.storage import brain_writer
from veil.brain.storage import veil_brain
from veil.brain.memory_agent import Memory
from veil_agent import Budget, Character, Store


def tool(name):
    return SimpleNamespace(name=name, description='', inputSchema={'type': 'object'})


class OpeningSearchTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = Store(self.temp.name + '/data')
        self.db = self.store.root / 'fake' / 'brain.db'
        self.db.parent.mkdir(parents=True)
        connection = sqlite3.connect(str(self.db))
        connection.executescript(veil_brain.SCHEMA)
        brain_writer.ensure_schema(connection)
        brain_writer.create_memory(connection, {'body': 'the blue harbour story', 'title': 'Blue harbour',
                                                'importance': 9}, embedder=None)
        connection.close()
        provider = {'model': 'fake', 'max_input_tokens': 20000, 'input_cny_per_million': 1,
                    'output_cny_per_million': 2, 'cached_input_cny_per_million': 1}
        settings = {'model': 'fake-embedding', 'input_cny_per_million': '0', 'mode': 'native',
                    'base_url': 'https://example.invalid/v1',
                    'brain': {'write_authority': True, 'shadow': False},
                    'retrieval': {'backend': 'brain'}}
        cfg = Character('fake', 'Example', 'persona', 20, 1024, provider, 'fake', settings)
        class Backend:
            def list_tools(self):
                return [tool('trace'), tool('dream')]
            def call(self, name, args):
                return ''
        self.memory = Memory(cfg, self.store, Budget(self.store), Mock(), Backend())
        self.addCleanup(self.memory.__exit__, None, None, None)

    def test_opening_and_search_use_brain(self):
        opening = self.memory.call('breath', {})
        self.assertIn('[bucket_id:', opening)
        self.assertIn('blue harbour', opening)
        search = self.memory.call('breath_search', {'query': 'blue harbour', 'max_results': 3})
        self.assertIn('=== ombre:result-ids ===', search)
        self.assertIn('blue harbour', search)

    def test_context_recall_uses_brain(self):
        rows = self.memory.context('blue harbour', include_opening=False)
        self.assertTrue(any('blue harbour' in (row.get('content') or '') for row in rows))


if __name__ == '__main__':
    unittest.main()
