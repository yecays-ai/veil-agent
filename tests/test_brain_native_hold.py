"""P2-A: a real native hold behind the authority adapter writes Brain."""
import sqlite3
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import Mock

import brain_writer
import veil_brain
from memory_agent import Memory
from memory_native import NativeMemoryTools
from veil_agent import Budget, Character, Store


class FakeEmbedder:
    usage_tokens = None

    def embed(self, _text):
        return [0.5] * 1024


def tool(name):
    return SimpleNamespace(name=name, description='', inputSchema={'type': 'object'})


class NativeHoldAuthorityTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = Store(self.temp.name + '/data')
        self.db = self.store.root / 'fake' / 'brain.db'
        self.db.parent.mkdir(parents=True)
        connection = sqlite3.connect(str(self.db))
        connection.executescript(veil_brain.SCHEMA)
        brain_writer.ensure_schema(connection)
        self.target = brain_writer.create_memory(connection, {'body': 'target', 'title': 'T'},
                                                 embedder=lambda _: [0.5] * 1024)['memory_id']
        connection.close()
        provider = {'model': 'fake', 'max_input_tokens': 20000, 'input_cny_per_million': 1,
                    'output_cny_per_million': 2, 'cached_input_cny_per_million': 1}
        settings = {'model': 'fake-embedding', 'input_cny_per_million': '0', 'mode': 'native',
                    'base_url': 'https://example.invalid/v1',
                    'brain': {'write_authority': True, 'shadow': False}}
        cfg = Character('fake', 'Example', 'persona', 20, 1024, provider, 'fake', settings)
        class Backend:
            def list_tools(self):
                return [tool('trace'), tool('dream')]

            def call(self, name, args):
                return ''
        backend = Backend()
        self.memory = Memory(cfg, self.store, Budget(self.store), Mock(), backend)
        self.memory._embedder = FakeEmbedder()
        self.addCleanup(self.memory.__exit__, None, None, None)
        self.tools = NativeMemoryTools(self.memory)

    def test_native_hold_saves_through_authority(self):
        result = self.tools._hold({'content': 'a new linked memory', 'title': 'New'})
        self.assertIn(result[1], ('成功', '新建'))
        connection = sqlite3.connect(str(self.db))
        try:
            bodies = [row[0] for row in connection.execute(
                "SELECT body FROM memories WHERE body='a new linked memory'")]
            links = connection.execute('SELECT links_json FROM sidecar_state WHERE memory_id IN '
                                       '(SELECT id FROM memories WHERE body=?)',
                                       ('a new linked memory',)).fetchone()
        finally:
            connection.close()
        self.assertEqual(bodies, ['a new linked memory'])
        self.assertIsNone(links)


if __name__ == '__main__':
    unittest.main()
