"""P3-3: legacy hidden markers no longer hide a memory; archived/deleted lifecycle still does."""
import json
import tempfile
import unittest
from decimal import Decimal
from pathlib import Path
from unittest.mock import Mock, patch

from veil.brain.memory_agent import Memory
from veil.brain.memory_native import NativeMemoryTools
from test_memory import Backend
from veil.core.veil_agent import Budget, Character, Chat, Store


class LegacyHiddenContextTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = Store(Path(self.temp.name) / 'data')
        provider = {'model': 'fake', 'max_input_tokens': 20000,
                    'input_cny_per_million': Decimal(2), 'output_cny_per_million': Decimal(8),
                    'cached_input_cny_per_million': Decimal(1)}
        self.cfg = Character('fake', 'Example', 'fictional persona', 20, 1024, provider, 'fake',
                             {'model': 'fake-embedding', 'input_cny_per_million': '0.07', 'mode': 'native',
                              'recall': {'cooldown_turns': 0}})
        self.backend = Backend(self.store.root / 'fake' / 'memory' / 'dynamic')
        self.backend.rows = {'legacy': {'content': 'FICTIONAL_LEGACY_HIDDEN_MARKER'},
                             'normal': {'content': 'FICTIONAL_VISIBLE_MARKER'}}
        for key in self.backend.rows:
            self.backend.write_row(key)
        self.store.append('fake/chat.jsonl', {'role': 'user', 'content': 'fictional chat'})
        self.history = (self.store.root / 'fake' / 'chat.jsonl').read_bytes()
        self.memory = self.open()

    def open(self):
        memory = Memory(self.cfg, self.store, Budget(self.store), Mock(), self.backend)
        self.addCleanup(memory.__exit__, None, None, None)
        return memory

    def context(self, memory=None):
        return '\n'.join(row['content'] for row in (memory or self.memory).context('fictional query'))

    def tearDown(self):
        self.assertEqual((self.store.root / 'fake' / 'chat.jsonl').read_bytes(), self.history)

    def test_legacy_dont_surface_is_ignored_by_context_and_visibility(self):
        path = self.backend.vault / 'legacy.md'
        path.write_text('---\nid: legacy\ndont_surface: true\ntype: dynamic\n---\n'
                        'FICTIONAL_LEGACY_HIDDEN_MARKER', encoding='utf-8')
        self.assertIn('FICTIONAL_LEGACY_HIDDEN_MARKER', self.context())
        self.assertTrue(self.memory.refresh_visibility('legacy'))
        self.assertFalse(self.memory.is_hidden('legacy'))

    def test_a_removed_hide_tool_argument_cannot_hide_a_memory(self):
        native = NativeMemoryTools(self.memory)
        native._trace({'bucket_id': 'legacy', 'dont_surface': True}, None)
        self.assertFalse(self.memory.is_hidden('legacy'))
        self.assertIn('FICTIONAL_LEGACY_HIDDEN_MARKER', self.context())

    def test_archived_tombstone_and_deleted_still_fail_closed(self):
        path = self.backend.vault / 'legacy.md'
        for metadata in ('type: archived', 'deleted_at: 2026-10-21', 'tombstone: true'):
            with self.subTest(metadata=metadata):
                path.write_text(f'---\nid: legacy\n{metadata}\n---\n'
                                'FICTIONAL_LEGACY_HIDDEN_MARKER', encoding='utf-8')
                self.assertNotIn('FICTIONAL_LEGACY_HIDDEN_MARKER', self.context())

    def test_deletion_pending_state_is_still_excluded(self):
        self.memory.state['entries']['legacy'] = {'title': 'old', 'hidden': True,
                                                  'deleted': True, 'delete_pending': True}
        self.assertNotIn('FICTIONAL_LEGACY_HIDDEN_MARKER', self.context())
        self.assertTrue(self.memory.is_hidden('legacy'))

    def test_actual_model_request_keeps_legacy_hidden_marker(self):
        chat_store = Store(Path(self.temp.name) / 'chat-copy')
        model = Mock(return_value=('fictional reply', 100, 20, 0))
        chat = Chat(self.cfg, chat_store, Budget(chat_store), model, self.memory)
        chat.send('fictional first query')
        sent = '\n'.join(row['content'] for row in model.call_args.args[0])
        self.assertIn('FICTIONAL_LEGACY_HIDDEN_MARKER', sent)
        self.assertIn('FICTIONAL_VISIBLE_MARKER', sent)

    def test_nested_relation_type_does_not_make_visibility_unknown(self):
        raw = ('---\nid: linked\ntype: dynamic\nrelation_links:\n- auto: true\n'
               "  label: ''\n  type: same_event\n  target_bucket_id: normal\n---\nFICTIONAL_LINKED\n")
        with patch.object(self.memory, 'raw_content_for', return_value=raw):
            self.assertTrue(self.memory.refresh_visibility('linked'))
        self.assertEqual(self.memory.notices(), [])

    def test_unreadable_legacy_metadata_is_still_withheld(self):
        path = self.backend.vault / 'legacy.md'
        path.write_text('---\nid: legacy\ntombstone: unknown\n---\n'
                        'FICTIONAL_LEGACY_HIDDEN_MARKER', encoding='utf-8')
        with patch.object(self.memory, 'raw_content_for', return_value=None):
            self.assertNotIn('FICTIONAL_VISIBLE_MARKER', self.context())
        self.assertTrue(self.memory.notices())


if __name__ == '__main__':
    unittest.main()
