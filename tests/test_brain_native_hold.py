"""P2-A: a real native hold behind the authority adapter writes Brain."""
import sqlite3
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import Mock

import brain_writer
import veil_brain
from memory_agent import Memory
from event_memory import EventMemory
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
                    'brain': {'write_authority': True, 'shadow': False},
                    'retrieval': {'backend': 'brain'}}
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
        # When related candidates exist in Brain, hold presents them for review ('待选择');
        # sending the hold again confirms it as a distinct new memory and saves.
        if result[1] == '待选择':
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
        self.memory.last_recall_debug = {}
        recalled = self.memory.search_candidates('a new linked memory', 8)
        self.assertIn('a new linked memory', recalled)

    def test_event_record_writes_brain_story_and_projection(self):
        self.store.append('fake/chat.jsonl', {
            'id': 'message-for-event', 'role': 'user',
            'content': 'Lan said the ferry bell sounded blue.',
            'time': '2026-11-25T10:00:00+08:00'})
        events = EventMemory(self.memory, settings={'enabled': True})
        events.record('we walked to the old harbour and talked about the ferry', 'Old harbour',
                      at='2026-11-25T10:05:00+08:00',
                      source={'message_ids': ['message-for-event']})
        rows = events.events()
        self.assertTrue(rows)
        event_id = next(iter(rows))
        detail = events.detail(event_id)
        self.assertTrue(detail and detail.get('story'))
        memory_id = rows[event_id]['memory_id']
        self.assertIn(memory_id, self.memory.state['entries'])
        connection = sqlite3.connect(str(self.db))
        try:
            quote = connection.execute(
                'SELECT speaker,text FROM quotes WHERE memory_id=?', (memory_id,)).fetchone()
        finally:
            connection.close()
        self.assertEqual(quote, ('user', 'Lan said the ferry bell sounded blue.'))

    def test_panel_read_api_shape_matches_frontend(self):
        panel = self.tools.panel_entries()
        self.assertEqual(set(panel), {'items', 'recent_requests'})
        item = next(row for row in panel['items'] if row['memory_id'] == self.target)
        for key in ('memory_id', 'title', 'created', 'kind', 'domain', 'tags', 'importance',
                    'why', 'body', 'locked', 'links', 'sources'):
            self.assertIn(key, item)
        self.assertEqual(self.tools.panel_versions(self.target), [])
        self.assertEqual(self.tools.panel_archived(), [])

    def test_panel_lock_edit_and_delete_work_against_brain(self):
        self.assertIn(self.target, self.tools._visible_records())
        ok, _ = self.tools.panel_lock(self.target, True)
        self.assertTrue(ok)
        ok, _ = self.tools.panel_edit(self.target, {'content': 'edited on page'})
        self.assertTrue(ok)
        ok, _ = self.tools.panel_delete(self.target)
        self.assertTrue(ok)
        connection = sqlite3.connect(str(self.db))
        try:
            self.assertIsNone(connection.execute('SELECT 1 FROM memories WHERE id=?',
                                                 (self.target,)).fetchone())
        finally:
            connection.close()

    def test_erase_removes_brain_content_and_keeps_a_tombstone(self):
        self.assertTrue(self.memory.erase(self.target))
        connection = sqlite3.connect(str(self.db))
        try:
            self.assertIsNone(connection.execute('SELECT 1 FROM memories WHERE id=?',
                                                 (self.target,)).fetchone())
            self.assertIsNotNone(connection.execute('SELECT 1 FROM tombstones WHERE memory_id=?',
                                                    (self.target,)).fetchone())
        finally:
            connection.close()

    def test_native_trace_updates_brain_and_keeps_a_version(self):
        result = self.tools._trace({'bucket_id': self.target, 'content': 'changed body'}, None)
        self.assertIn('成功', result[1])
        connection = sqlite3.connect(str(self.db))
        try:
            body = connection.execute('SELECT body FROM memories WHERE id=?', (self.target,)).fetchone()[0]
            versions = connection.execute('SELECT old_content FROM versions WHERE memory_id=?',
                                          (self.target,)).fetchall()
        finally:
            connection.close()
        self.assertEqual(body, 'changed body')
        self.assertIn('target', versions[0][0])


if __name__ == '__main__':
    unittest.main()
