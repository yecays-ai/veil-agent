import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

from short_term_notes import ShortTermNotes
from veil_agent import Store
from web_agent import Hub


class LiveContextTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name) / 'data'
        self.store = Store(root)
        cfg = SimpleNamespace(id='peter')
        self.notes = ShortTermNotes(cfg, self.store, None, None, 'peter/chat.jsonl',
                                    root / 'peter' / 'chat-session.json')
        self.chat = SimpleNamespace(cfg=cfg, store=self.store, short_term_notes=self.notes)

    def tearDown(self):
        self.temp.cleanup()

    def test_snapshot_lists_active_and_recently_closed_notes(self):
        active = self.notes._append_note_event(None, '进行中', 'Lan ordered bullfrog takeout.', None, [], [])
        done = self.notes._append_note_event(None, '进行中', 'Lan is choosing a movie.', None, [], [])
        self.notes._append_note_event(done, '已完成', done['text'], None, [], [])
        snapshot = self.notes.snapshot()
        self.assertEqual([note['text'] for note in snapshot['active']], [active['text']])
        self.assertEqual([note['status'] for note in snapshot['recent']], ['已完成'])
        self.assertIsNotNone(snapshot['active'][0]['fades_at'])
        self.assertIsNotNone(snapshot['next_extraction_at'])

    def test_snapshot_hides_notes_closed_long_ago(self):
        old = (datetime.now(timezone.utc) - timedelta(hours=5)).isoformat()
        self.store.append('peter/notes.jsonl', {'kind': 'note', 'note_id': 'a', 'status': '已过期',
                                                'text': 'Old.', 'time': old})
        self.assertEqual(self.notes.snapshot()['recent'], [])

    def test_hub_joins_context_rows_with_their_tool_calls(self):
        self.store.append('peter/context-log.jsonl', {
            'kind': 'chat', 'request_id': 'r1', 'history_rounds': 20, 'memory_ids': ['m1', 'm2'],
            'background': {'paragraphs': [{'n': 1}], 'core': [3]},
            'short_term_notes': {'count': 2, 'characters': 90}, 'estimated_input_tokens': 9000})
        hub = Hub('Peter', SimpleNamespace(enabled=lambda: True))
        hub.chat = self.chat
        live = hub.live_context([{'request_id': 'r1', 'tool': 'send_message', 'state': '成功'},
                                 {'request_id': 'other', 'tool': 'hold', 'state': '成功'}])
        turn = live['turns'][0]
        self.assertEqual((turn['kind'], turn['memory_count'], turn['background_count'], turn['notes_count']),
                         ('chat', 2, 2, 2))
        self.assertEqual(turn['tools'], [{'tool': 'send_message', 'state': '成功'}])
        self.assertEqual(live['notes']['active'], [])


if __name__ == '__main__':
    unittest.main()
