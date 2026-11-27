"""P3-3: legacy hidden rows become normal; archived lifecycle and page groups remain coherent."""
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from veil.brain.storage import brain_events
from veil.brain.storage import brain_writer
from veil.brain.storage import veil_brain
from veil.brain.storage.brain_migrate import unhide_legacy_rows
from veil.brain.storage.brain_read import BrainRead
from veil.core.veil_agent import Store


class UnhideMigrationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = Store(Path(self.temp.name) / 'data')
        self.role_dir = self.store.root / 'fake'
        self.role_dir.mkdir(parents=True)
        self.db = self.role_dir / 'brain.db'
        connection = sqlite3.connect(str(self.db))
        try:
            connection.executescript(veil_brain.SCHEMA)
            brain_writer.ensure_schema(connection)
            brain_events.ensure_schema(connection)
            connection.commit()
        finally:
            connection.close()

    def _create(self, body, **values):
        connection = sqlite3.connect(str(self.db))
        try:
            row = brain_writer.create_memory(connection, {'body': body,
                                                          'title': body[:20]}, embedder=None)
            if values:
                columns = ', '.join('%s=?' % key for key in values)
                connection.execute('UPDATE memories SET %s WHERE id=?' % columns,
                                   (*values.values(), row['memory_id']))
                connection.commit()
            return row['memory_id']
        finally:
            connection.close()

    def test_hidden_archived_row_returns_to_normal_visibility_without_content_change(self):
        connection = sqlite3.connect(str(self.db))
        try:
            before = brain_writer.create_memory(
                connection, {'body': 'FICTIONAL_HIDDEN_BODY', 'title': 'Hidden title'},
                embedder=None)
            connection.execute(
                "UPDATE memories SET is_hidden=1, dont_surface=1, bucket='archive', "
                "type='archived', digested=1, resolved=1, domain_json=?, tags_json=? WHERE id=?",
                ('["legacy"]', '["old-tag"]', before['memory_id']))
            connection.commit()
            original = connection.execute(
                'SELECT body,title,why_remembered,domain_json,tags_json FROM memories WHERE id=?',
                (before['memory_id'],)).fetchone()
        finally:
            connection.close()
        self.assertEqual(unhide_legacy_rows(self.store, 'fake'), 1)
        connection = sqlite3.connect(str(self.db))
        try:
            row = connection.execute(
                'SELECT body,title,why_remembered,domain_json,tags_json,bucket,type,is_hidden,'
                'dont_surface,digested,resolved FROM memories WHERE id=?',
                (before['memory_id'],)).fetchone()
        finally:
            connection.close()
        self.assertEqual(row[:5], original)
        self.assertEqual(row[5], 'dynamic')
        self.assertEqual(row[6], 'note')
        self.assertEqual(row[7:9], (0, 0))
        # Legacy decay values are data, not behavior.
        self.assertIsNotNone(row[9])
        self.assertIsNotNone(row[10])
        self.assertEqual(unhide_legacy_rows(self.store, 'fake'), 0)

    def test_normal_archive_is_not_touched_and_panel_has_no_hidden_field(self):
        archived = self._create('normal archived', bucket='archive', type='archived')
        hidden = self._create('legacy hidden', is_hidden=1, dont_surface=1)
        unhide_legacy_rows(self.store, 'fake')
        connection = sqlite3.connect(str(self.db))
        try:
            archived_row = connection.execute(
                'SELECT bucket,type,is_hidden FROM memories WHERE id=?', (archived,)).fetchone()
            hidden_row = connection.execute(
                'SELECT bucket,type,is_hidden FROM memories WHERE id=?', (hidden,)).fetchone()
        finally:
            connection.close()
        self.assertEqual(archived_row, ('archive', 'archived', 0))
        self.assertEqual(hidden_row, ('dynamic', 'dynamic', 0))
        with BrainRead(self.db) as reader:
            entries = reader.panel_entries(limit=100)
        self.assertTrue(entries)
        self.assertTrue(all('hidden' not in entry for entry in entries))
        self.assertIn(hidden, {entry['memory_id'] for entry in entries})
        # The low-level panel reader may include archive rows; page-level filtering still keeps
        # them out of the normal list and uses panel_archived for the 已归档 group.
        self.assertIn(archived, {entry['memory_id'] for entry in entries})

    def test_pending_delete_is_not_unhidden(self):
        memory_id = self._create('pending delete', is_hidden=1, dont_surface=1)
        connection = sqlite3.connect(str(self.db))
        try:
            connection.execute(
                "INSERT INTO delete_requests(request_id,memory_id,reason,status) "
                "VALUES('r1',?,'reason','pending')", (memory_id,))
            connection.commit()
        finally:
            connection.close()
        self.assertEqual(unhide_legacy_rows(self.store, 'fake'), 0)
        connection = sqlite3.connect(str(self.db))
        try:
            row = connection.execute(
                'SELECT is_hidden,dont_surface FROM memories WHERE id=?', (memory_id,)).fetchone()
        finally:
            connection.close()
        self.assertEqual(row, (1, 1))

    def test_memory_startup_runs_unhide_migration(self):
        from unittest.mock import Mock
        from veil.brain.memory_agent import Memory
        from veil.core.veil_agent import Budget, Character
        memory_id = self._create('startup hidden', is_hidden=1, dont_surface=1,
                                 bucket='archive', type='archived')
        provider = {'model': 'fake', 'max_input_tokens': 20000, 'input_cny_per_million': 1,
                    'output_cny_per_million': 2, 'cached_input_cny_per_million': 1}
        cfg = Character('fake', 'Example', 'persona', 20, 1024, provider, 'fake',
                        {'model': 'fake-embedding', 'input_cny_per_million': '0',
                         'mode': 'native', 'events': {'enabled': False}})
        memory = Memory(cfg, self.store, Budget(self.store), Mock())
        try:
            connection = sqlite3.connect(str(self.db))
            try:
                row = connection.execute(
                    'SELECT is_hidden,dont_surface,bucket,type,body FROM memories WHERE id=?',
                    (memory_id,)).fetchone()
            finally:
                connection.close()
            self.assertEqual(row[:4], (0, 0, 'dynamic', 'note'))
            self.assertEqual(row[4], 'startup hidden')
        finally:
            memory.__exit__(None, None, None)

    def test_pending_delete_stays_unreadable_after_hidden_removal(self):
        from unittest.mock import Mock
        from veil.brain.memory_agent import Memory
        from veil.core.veil_agent import Budget, Character
        memory_id = self._create('pending runtime')
        connection = sqlite3.connect(str(self.db))
        try:
            connection.execute(
                "INSERT INTO delete_requests(request_id,memory_id,reason,status) "
                "VALUES('r2',?,'reason','pending')", (memory_id,))
            connection.commit()
        finally:
            connection.close()
        provider = {'model': 'fake', 'max_input_tokens': 20000, 'input_cny_per_million': 1,
                    'output_cny_per_million': 2, 'cached_input_cny_per_million': 1}
        cfg = Character('fake', 'Example', 'persona', 20, 1024, provider, 'fake',
                        {'model': 'fake-embedding', 'input_cny_per_million': '0',
                         'mode': 'native', 'events': {'enabled': False}})
        memory = Memory(cfg, self.store, Budget(self.store), Mock())
        try:
            self.assertFalse(memory.refresh_visibility(memory_id))
            self.assertTrue(memory.is_hidden(memory_id))
            self.assertNotIn('pending runtime', memory.brain_authority.breath())
            with BrainRead(self.db) as reader:
                self.assertTrue(reader.memory(memory_id)['delete_pending'])
                self.assertTrue(any(entry['memory_id'] == memory_id
                                    and entry['delete_pending']
                                    for entry in reader.panel_entries()))
        finally:
            memory.__exit__(None, None, None)

    def test_autonomous_delete_request_reaches_lan_and_approval_removes(self):
        from unittest.mock import Mock
        from veil.brain.memory_agent import Memory
        from veil.brain.memory_native import NativeMemoryTools
        from veil.core.veil_agent import Budget, Character
        rejected_id = self._create('keep after rejection')
        approved_id = self._create('remove after approval')
        connection = sqlite3.connect(str(self.db))
        try:
            connection.execute(
                "INSERT INTO delete_requests(request_id,memory_id,reason,status,raw_json) "
                "VALUES('r-reject',?,'not now','pending',?)",
                (rejected_id, json.dumps({'request_id': 'r-reject', 'memory_id': rejected_id,
                                          'reason': 'not now', 'status': 'pending'})))
            connection.execute(
                "INSERT INTO delete_requests(request_id,memory_id,reason,status,raw_json) "
                "VALUES('r-approve',?,'duplicate','pending',?)",
                (approved_id, json.dumps({'request_id': 'r-approve', 'memory_id': approved_id,
                                          'reason': 'duplicate', 'status': 'pending'})))
            connection.commit()
        finally:
            connection.close()
        provider = {'model': 'fake', 'max_input_tokens': 20000, 'input_cny_per_million': 1,
                    'output_cny_per_million': 2, 'cached_input_cny_per_million': 1}
        cfg = Character('fake', 'Example', 'persona', 20, 1024, provider, 'fake',
                        {'model': 'fake-embedding', 'input_cny_per_million': '0',
                         'mode': 'native', 'events': {'enabled': False}})
        memory = Memory(cfg, self.store, Budget(self.store), Mock())
        try:
            prompts = []
            notes = NativeMemoryTools(memory).process_pending_deletions(
                lambda prompt: prompts.append(prompt) or ('n' if rejected_id in prompt else 'y'))
            self.assertTrue(any(rejected_id in prompt for prompt in prompts), notes)
            self.assertTrue(any(approved_id in prompt for prompt in prompts), notes)
            rows = {row['request_id']: row for row in memory.store.read('fake/delete-requests.jsonl')}
            self.assertEqual(rows['r-reject']['status'], 'rejected')
            self.assertEqual(rows['r-approve']['status'], 'approved')
            # Rejected request must leave the memory visible and recall-eligible again.
            self.assertTrue(memory.refresh_visibility(rejected_id))
            self.assertFalse(memory.is_hidden(rejected_id))
            # Approved request must actually remove it.
            self.assertFalse(memory.refresh_visibility(approved_id))
            self.assertTrue(memory.is_hidden(approved_id))
        finally:
            memory.__exit__(None, None, None)

    def test_trace_pending_delete_can_be_approved_even_while_request_is_pending(self):
        # Regression guard for the is_hidden(delete_pending) conflict: the pending flag itself
        # must not make process_pending_deletions reject the request before Lan sees it.
        from unittest.mock import Mock
        from veil.brain.memory_agent import Memory
        from veil.brain.memory_native import NativeMemoryTools
        from veil.core.veil_agent import Budget, Character
        memory_id = self._create('pending approval')
        connection = sqlite3.connect(str(self.db))
        try:
            connection.execute(
                "INSERT INTO delete_requests(request_id,memory_id,reason,status,raw_json) "
                "VALUES('r-pending',?,'reason','pending',?)",
                (memory_id, json.dumps({'request_id': 'r-pending', 'memory_id': memory_id,
                                        'title': 'pending approval', 'reason': 'reason',
                                        'status': 'pending'})))
            connection.commit()
        finally:
            connection.close()
        provider = {'model': 'fake', 'max_input_tokens': 20000, 'input_cny_per_million': 1,
                    'output_cny_per_million': 2, 'cached_input_cny_per_million': 1}
        cfg = Character('fake', 'Example', 'persona', 20, 1024, provider, 'fake',
                        {'model': 'fake-embedding', 'input_cny_per_million': '0',
                         'mode': 'native', 'events': {'enabled': False}})
        memory = Memory(cfg, self.store, Budget(self.store), Mock())
        try:
            self.assertFalse(memory.refresh_visibility(memory_id))
            prompts = []
            notes = NativeMemoryTools(memory).process_pending_deletions(
                lambda prompt: prompts.append(prompt) or 'n')
            self.assertTrue(prompts, notes)
            self.assertIn('pending approval', prompts[0])
            self.assertTrue(memory.refresh_visibility(memory_id))
        finally:
            memory.__exit__(None, None, None)


class PageContractTests(unittest.TestCase):
    def test_page_uses_lifecycle_groups_and_does_not_search_domains_or_tags(self):
        root = Path(__file__).resolve().parents[1]
        js = (root / 'desktop' / 'chat.js').read_text(encoding='utf-8')
        html = (root / 'desktop' / 'chat.html').read_text(encoding='utf-8')
        self.assertIn("all:{label:'全部'}", js)
        self.assertIn("recent:{label:'最近'}", js)
        self.assertIn("events:{label:'经历'", js)
        self.assertIn("archived:{label:'已归档'", js)
        self.assertIn('const fields=[item.title,item.why,item.body];', js)
        self.assertNotIn("data-memory-group=\"us\"", html)
        self.assertNotIn("data-memory-group=\"peter\"", html)
        self.assertNotIn("data-memory-group=\"system\"", html)

    def test_native_trace_schema_no_longer_offers_a_hide_argument(self):
        root = Path(__file__).resolve().parents[1]
        source = (root / 'veil' / 'brain' / 'memory_native.py').read_text(encoding='utf-8')
        schema = source.split('trace_schema', 1)[1][:3000] if 'trace_schema' in source else source
        self.assertNotIn("'dont_surface': {'type': 'boolean'", schema)


if __name__ == '__main__':
    unittest.main()
