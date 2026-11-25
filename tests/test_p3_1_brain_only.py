"""P3-1: Brain works as the only memory backend; no Ombre process is needed."""
import sqlite3
import tempfile
import unittest
from unittest.mock import Mock

import brain_writer
import veil_brain
from brain_read import BrainRead
from memory_agent import Memory
from memory_native import NativeMemoryTools
from veil_agent import UserError
from recall_judge import RecallJudge, load_settings
from veil_agent import Budget, Character, Store


class FakeEmbedder:
    usage_tokens = None

    def embed(self, _text):
        return [0.4] * 1024


class BrainOnlyTests(unittest.TestCase):
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
                    'brain': {'write_authority': True, 'shadow': False},
                    'retrieval': {'backend': 'brain'}}
        self.cfg = Character('fake', 'Example', 'persona', 20, 1024, provider, 'fake', settings)
        # No backend argument: P3-1 startup must work without an Ombre client at all.
        self.memory = Memory(self.cfg, self.store, Budget(self.store), Mock())
        self.memory._embedder = FakeEmbedder()
        self.addCleanup(self.memory.__exit__, None, None, None)

    def test_hold_search_and_tools_work_without_ombre(self):
        text = self.memory.call('hold', {'content': 'pineapple pizza evening', 'title': 'Pizza'})
        self.assertRegex(text, r'^新建→[0-9a-f]{12}$')
        memory_id = text.split('→')[1]
        found = self.memory.search_candidates('pineapple', 5)
        self.assertIn(memory_id, found)
        tools = NativeMemoryTools(self.memory, 8192, model=Mock())
        names = {tool.name for tool in tools.tools}
        self.assertIn('trace', names)
        self.assertNotIn('dream', names)

    def test_pinned_and_importance_are_written_to_brain(self):
        text = self.memory.call('hold', {'content': 'always remember this', 'title': 'Core',
                                         'pinned': True, 'why_remembered': 'core promise'})
        memory_id = text.split('→')[1]
        with BrainRead(self.db) as reader:
            row = reader.memory(memory_id)
        self.assertEqual(row['pinned'], 1)
        self.assertEqual(row['importance'], 10)
        opening = self.memory.call('breath', {})
        self.assertIn('Core', opening)
        self.memory.call('trace', {'bucket_id': memory_id, 'pinned': False, 'importance': 4,
                                   'edit_reason': 'no longer a core promise'})
        with BrainRead(self.db) as reader:
            row = reader.memory(memory_id)
        self.assertEqual(row['pinned'], 0)
        self.assertEqual(row['importance'], 4)

    def test_metadata_trace_keeps_the_existing_vector(self):
        memory_id = self.memory.call('hold', {'content': 'metadata only body', 'title': 'Old'}).split('→')[1]
        with BrainRead(self.db) as reader:
            before = reader.all_vectors().get(memory_id)
        self.memory.call('trace', {'bucket_id': memory_id, 'title': 'New', 'importance': 7,
                                   'edit_reason': 'rename only'})
        with BrainRead(self.db) as reader:
            after = reader.all_vectors().get(memory_id)
            row = reader.memory(memory_id)
        self.assertIsNotNone(before)
        self.assertEqual(before, after)
        self.assertEqual(row['title'], 'New')
        self.assertEqual(row['importance'], 7)
        self.assertEqual(row['vector_state'], 'ready')

    def test_recall_meta_reads_pinned_and_importance_from_brain(self):
        memory_id = self.memory.call('hold', {'content': 'core promise', 'title': 'Core',
                                              'pinned': True,
                                              'why_remembered': 'core'}).split('→')[1]
        meta = self.memory._recall_meta([memory_id])[memory_id]
        self.assertEqual(meta['pinned'], 'true')
        self.assertEqual(meta['importance'], 10)
        self.memory.call('trace', {'bucket_id': memory_id, 'pinned': False, 'importance': 3,
                                   'edit_reason': 'not core anymore'})
        meta = self.memory._recall_meta([memory_id])[memory_id]
        self.assertEqual(meta['pinned'], '')
        self.assertEqual(meta['importance'], 3)
        raw = self.memory.raw_content_for(memory_id)
        self.assertIn('pinned: false', raw)
        self.assertIn('importance: 3', raw)

    def test_keyword_only_fallback_works_when_configured_keyword_weight_is_zero(self):
        memory_id = self.memory.call('hold', {'content': 'unique keyword lantern', 'title': 'Lantern'}).split('→')[1]
        self.memory.retrieval_settings = {'backend': 'brain', 'weight_vector': 1.0,
                                          'weight_keyword': 0.0}
        self.memory._embedder = Mock()
        self.memory._embedder.embed.side_effect = RuntimeError('embedding down')
        found = self.memory.search_candidates('lantern', 5)
        self.assertIn(memory_id, found)

    def test_hidden_delete_request_does_not_leak_into_model_notes(self):
        memory_id = self.memory.call('hold', {'content': 'hidden secret', 'title': 'Hidden secret'}).split('→')[1]
        connection = sqlite3.connect(str(self.db))
        try:
            connection.execute('UPDATE memories SET is_hidden=1 WHERE id=?', (memory_id,))
            connection.commit()
        finally:
            connection.close()
        self.memory.store.append('fake/delete-requests.jsonl', {
            'request_id': 'r-hidden', 'memory_id': memory_id, 'title': 'Hidden secret',
            'reason': 'private reason', 'status': 'pending'})
        tools = NativeMemoryTools(self.memory, 8192, model=Mock())
        notes = tools.process_pending_deletions(lambda _: 'y')
        self.assertEqual(notes, [])
        rows = self.memory.store.read('fake/delete-requests.jsonl')
        self.assertEqual(rows[-1]['status'], 'rejected')

    def test_event_caches_follow_brain_updates(self):
        path = 'fake/events.jsonl'
        self.memory.store.append(path, {
            'event_id': 'e-test', 'memory_id': 'm-own', 'title': 'Old', 'story': 'old story',
            'status': 'open', 'first_at': '2026-01-01', 'last_at': '2026-01-01',
            'source_memory_ids': ['m-source']})
        self.assertEqual(self.memory._event_stories()['e-test']['story'], 'old story')
        self.assertIn('m-source', self.memory._event_ledger()['owners'])
        self.memory.store.append(path, {
            'event_id': 'e-test', 'memory_id': 'm-own', 'title': 'New', 'story': 'new story',
            'status': 'open', 'first_at': '2026-01-01', 'last_at': '2026-01-02',
            'source_memory_ids': ['m-source', 'm-source-2']})
        self.assertEqual(self.memory._event_stories()['e-test']['story'], 'new story')
        self.assertIn('m-source-2', self.memory._event_ledger()['owners'])

    def test_autonomous_delete_request_does_not_depend_on_tags(self):
        memory_id = self.memory.call('hold', {'content': 'delete me', 'title': 'Delete'}).split('→')[1]
        tools = NativeMemoryTools(self.memory, 8192, model=Mock())
        tools.autonomy_mode = True
        result = tools.invoke('trace', {'bucket_id': memory_id, 'delete': True,
                                        'delete_reason': 'duplicate'})
        self.assertEqual(result[1], '成功')
        rows = self.memory.store.read('fake/delete-requests.jsonl')
        self.assertEqual(rows[-1]['status'], 'pending')
        tools.autonomy_mode = False
        tools.process_pending_deletions(lambda _: 'n')
        rows = self.memory.store.read('fake/delete-requests.jsonl')
        self.assertEqual(rows[-1]['status'], 'rejected')
        self.assertIsNotNone(self.memory.raw_content_for(memory_id))

    def test_recall_judge_scans_brain_visibility(self):
        visible = self.memory.call('hold', {'content': 'visible blue lamp', 'title': 'Lamp'}).split('→')[1]
        hidden = self.memory.call('hold', {'content': 'hidden blue lamp', 'title': 'Hidden'}).split('→')[1]
        connection = sqlite3.connect(str(self.db))
        try:
            connection.execute('UPDATE memories SET is_hidden=1 WHERE id=?', (hidden,))
            connection.commit()
        finally:
            connection.close()
        judge = RecallJudge(self.memory, self.cfg, Mock(), load_settings({}))
        judge._scan()
        self.assertIsNotNone(judge._eligible(visible))
        self.assertIsNone(judge._eligible(hidden))


class BrainInitializationTests(unittest.TestCase):
    """P3-1: a fresh role initializes Brain; a lost existing database stops loudly."""

    def make(self, role='fresh'):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = Store(self.temp.name + '/data')
        provider = {'model': 'fake', 'max_input_tokens': 20000, 'input_cny_per_million': 1,
                    'output_cny_per_million': 2, 'cached_input_cny_per_million': 1}
        settings = {'model': 'fake-embedding', 'input_cny_per_million': '0', 'mode': 'native',
                    'base_url': 'https://example.invalid/v1',
                    'brain': {'write_authority': True, 'shadow': False},
                    'retrieval': {'backend': 'brain'}}
        self.cfg = Character(role, 'Example', 'persona', 20, 1024, provider, role, settings)
        return self.store.root / role

    def test_fresh_role_gets_an_empty_brain_database(self):
        role_dir = self.make()
        self.assertFalse(role_dir.exists())
        memory = Memory(self.cfg, self.store, Budget(self.store), Mock())
        self.addCleanup(memory.__exit__, None, None, None)
        self.assertTrue((role_dir / 'brain.db').is_file())
        with BrainRead(role_dir / 'brain.db') as reader:
            tables = {row[0] for row in reader.connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'")}
        self.assertIn('memories', tables)
        self.assertIn('events', tables)
        self.assertEqual(memory.call('breath', {}), '')

    def test_existing_role_without_brain_does_not_get_an_empty_database(self):
        role_dir = self.make('existing')
        role_dir.mkdir(parents=True)
        (role_dir / 'chat.jsonl').write_text('{"text": "old"}\n', encoding='utf-8')
        with self.assertRaises(UserError):
            Memory(self.cfg, self.store, Budget(self.store), Mock())
        self.assertFalse((role_dir / 'brain.db').exists())

    def test_corrupt_brain_is_not_replaced(self):
        role_dir = self.make('broken')
        role_dir.mkdir(parents=True)
        path = role_dir / 'brain.db'
        path.write_bytes(b'not sqlite')
        with self.assertRaises(UserError):
            Memory(self.cfg, self.store, Budget(self.store), Mock())
        self.assertEqual(path.read_bytes(), b'not sqlite')
