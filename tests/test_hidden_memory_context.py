"""Final model-context checks against fresh, fictional Ombre Markdown state."""
import tempfile
import unittest
from decimal import Decimal
from pathlib import Path
from unittest.mock import Mock, patch

from memory_agent import Memory
from memory_native import NativeMemoryTools
from test_memory import Backend
from veil_agent import Budget, Character, Chat, Store


class HiddenMemoryContextTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = Store(Path(self.temp.name) / 'data')
        provider = {'model': 'fake', 'max_input_tokens': 20000,
                    'input_cny_per_million': Decimal(2), 'output_cny_per_million': Decimal(8),
                    'cached_input_cny_per_million': Decimal(1)}
        self.cfg = Character('fake', 'Example', 'fictional persona', 20, 1024, provider, 'fake',
                             {'model': 'fake-embedding', 'input_cny_per_million': '0.07', 'mode': 'native'})
        self.backend = Backend(self.store.root / 'fake' / 'memory' / 'dynamic')
        self.backend.rows = {'secret': {'content': 'FICTIONAL_HIDDEN_MARKER'},
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

    def test_native_trace_hide_filters_next_context_and_local_visibility(self):
        self.assertIn('FICTIONAL_HIDDEN_MARKER', self.context())
        native = NativeMemoryTools(self.memory)
        result = native._trace({'bucket_id': 'secret', 'dont_surface': True}, None)
        self.assertEqual(result[1], '成功')
        self.assertNotIn('FICTIONAL_HIDDEN_MARKER', self.context())
        self.assertTrue(self.memory.is_hidden('secret'))
        self.assertIn('FICTIONAL_VISIBLE_MARKER', self.context())

    def test_panel_state_change_defeats_opening_and_details_caches_after_restart(self):
        self.memory.bucket_details('secret')
        self.assertIn('FICTIONAL_HIDDEN_MARKER', self.context())
        self.backend.rows['secret']['hidden'] = True
        self.backend.write_row('secret')  # Dashboard's dont_surface metadata update.
        self.assertNotIn('FICTIONAL_HIDDEN_MARKER', self.context())
        self.assertNotIn('FICTIONAL_HIDDEN_MARKER', self.context(self.open()))
        self.backend.rows['normal']['content'] = 'FICTIONAL_VISIBLE_UPDATED'
        self.backend.write_row('normal')
        self.assertIn('FICTIONAL_VISIBLE_UPDATED', self.context())
        self.backend.rows['secret']['hidden'] = False
        self.backend.write_row('secret')
        self.assertIn('FICTIONAL_HIDDEN_MARKER', self.context())

    def test_archived_state_and_missing_or_unreadable_metadata_fail_closed(self):
        path = self.backend.vault / 'secret.md'
        for metadata in ('type: archived', 'deleted_at: 2026-10-21', 'tombstone: true',
                         'dont_surface: unknown', 'dont_surface: true'):
            with self.subTest(metadata=metadata):
                path.write_text(f'---\nid: secret\n{metadata}\n---\nFICTIONAL_HIDDEN_MARKER', encoding='utf-8')
                self.assertNotIn('FICTIONAL_HIDDEN_MARKER', self.context())
        path.unlink()
        self.assertNotIn('FICTIONAL_HIDDEN_MARKER', self.context())
        with patch.object(self.memory, 'raw_content_for', return_value=None):
            self.assertNotIn('FICTIONAL_VISIBLE_MARKER', self.context())
        self.assertTrue(self.memory.notices())

    def test_visibility_diagnostic_is_once_per_turn_and_valid_memory_still_reaches_model(self):
        self.backend.rows['secret']['hidden'] = True
        self.backend.write_row('secret')
        for key in ('unknown_a', 'unknown_b'):
            self.backend.rows[key] = {'content': f'FICTIONAL_UNCONFIRMED_{key}'}
            self.backend.write_row(key)

        original = self.memory.raw_content_for
        def unknown_metadata(memory_id):
            if memory_id in {'unknown_a', 'unknown_b'}:
                return None
            return original(memory_id)

        chat_store = Store(Path(self.temp.name) / 'visibility-chat-copy')
        model = Mock(return_value=('fictional reply', 100, 20, 0))
        chat = Chat(self.cfg, chat_store, Budget(chat_store), model, self.memory)
        with patch.object(self.memory, 'raw_content_for', side_effect=unknown_metadata):
            chat.send('fictional first query')
            first_sent = '\\n'.join(row['content'] for row in model.call_args.args[0])
            first_notices = self.memory.notices()
            chat.send('fictional second query')
            second_sent = '\\n'.join(row['content'] for row in model.call_args.args[0])
            second_notices = self.memory.notices()

        self.assertEqual(first_notices, [
            'Memory visibility could not be confirmed; affected memories were withheld this turn.'])
        self.assertEqual(second_notices, first_notices)
        for sent in (first_sent, second_sent):
            self.assertIn('FICTIONAL_VISIBLE_MARKER', sent)
            self.assertNotIn('FICTIONAL_HIDDEN_MARKER', sent)
            self.assertNotIn('FICTIONAL_UNCONFIRMED_unknown_a', sent)
            self.assertNotIn('FICTIONAL_UNCONFIRMED_unknown_b', sent)

    def test_nested_relation_type_does_not_make_visibility_unknown(self):
        raw = ('---\nid: linked\ntype: dynamic\nrelation_links:\n- auto: true\n'
               "  label: ''\n  type: same_event\n  target_bucket_id: normal\n---\nFICTIONAL_LINKED\n")
        with patch.object(self.memory, 'raw_content_for', return_value=raw):
            self.assertTrue(self.memory.refresh_visibility('linked'))
        self.assertEqual(self.memory.notices(), [])
        archived = raw.replace('type: dynamic', 'type: archived')
        with patch.object(self.memory, 'raw_content_for', return_value=archived):
            self.assertFalse(self.memory.refresh_visibility('linked'))

    def test_backend_failure_does_not_reuse_opening_cache(self):
        self.backend.fail = True
        self.assertNotIn('FICTIONAL_HIDDEN_MARKER', self.context())
        self.assertNotIn('FICTIONAL_VISIBLE_MARKER', self.context())

    def test_actual_model_request_excludes_hidden_opening_but_keeps_visible_memory(self):
        # Send only into a separate disposable chat ledger; the source stays byte-identical.
        chat_store = Store(Path(self.temp.name) / 'chat-copy')
        model = Mock(return_value=('fictional reply', 100, 20, 0))
        chat = Chat(self.cfg, chat_store, Budget(chat_store), model, self.memory)
        chat.send('fictional first query')
        sent = '\n'.join(row['content'] for row in model.call_args.args[0])
        self.assertIn('FICTIONAL_HIDDEN_MARKER', sent)
        self.backend.rows['secret']['hidden'] = True
        self.backend.write_row('secret')
        chat.send('fictional second query')
        sent = '\n'.join(row['content'] for row in model.call_args.args[0])
        self.assertNotIn('FICTIONAL_HIDDEN_MARKER', sent)
        self.assertIn('FICTIONAL_VISIBLE_MARKER', sent)
