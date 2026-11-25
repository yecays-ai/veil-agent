"""P2-A: legacy version/sidecar ledgers are redirected to Brain while the API stays intact."""
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

import brain_store
import brain_writer
import veil_brain


class FakeStore:
    def __init__(self, root):
        self.root = Path(root)
        self.appended = []

    def append(self, path, row):
        self.appended.append((path, row))

    def read(self, path):
        return []

    def read_tail(self, path, limit):
        return []


class BrainStoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.db = self.root / 'fake' / 'brain.db'
        self.db.parent.mkdir(parents=True)
        connection = sqlite3.connect(self.db)
        connection.executescript(veil_brain.SCHEMA)
        brain_writer.ensure_schema(connection)
        connection.close()
        self.base = FakeStore(self.root)
        self.store = brain_store.BrainStore(self.base, 'fake', self.db)

    def test_freeze_blocks_ledger_writes(self):
        (self.root / 'fake').mkdir(exist_ok=True)
        (self.root / 'fake' / 'brain-write-freeze').write_text('frozen', encoding='utf-8')
        with self.assertRaises(RuntimeError):
            self.store.append('fake/memory-versions.jsonl', {'memory_id': 'm1', 'old_content': 'x'})
        (self.root / 'fake' / 'brain-write-freeze').unlink()
        self.store.append('fake/memory-versions.jsonl', {'memory_id': 'm1', 'old_content': 'x'})

    def test_versions_round_trip(self):
        self.store.append('fake/memory-versions.jsonl', {
            'memory_id': 'm1', 'fields': ['content'], 'operation_reason': 'edit',
            'why_remembered': '', 'old_content': 'old'})
        rows = self.store.read('fake/memory-versions.jsonl')
        self.assertEqual(rows[0]['old_content'], 'old')
        self.assertEqual(rows[0]['fields'], ['content'])
        self.assertEqual(self.base.appended, [])

    def test_event_ledgers_round_trip(self):
        event = {'event_id': 'V01', 'memory_id': 'V01', 'title': 'T', 'story': 'full',
                 'sources': [{'memory_id': 'm1'}], 'approved': True}
        self.store.append('fake/events.jsonl', event)
        self.store.append('fake/event-stages.jsonl',
                          {'stage_id': 'S1', 'event_id': 'V01', 'text': 'stage', 'facts': ['f'],
                           'event_row': {'kept': True}})
        events = self.store.read('fake/events.jsonl')
        stages = self.store.read('fake/event-stages.jsonl')
        self.assertEqual(events[0]['story'], 'full')
        self.assertEqual(stages[0]['text'], 'stage')
        self.assertEqual(stages[0]['facts'], ['f'])
        self.assertEqual(stages[0]['event_row'], {'kept': True})
        self.assertEqual(self.base.appended, [])

    def test_projection_duplicates_keep_order(self):
        rows = [
            {'stage_id': 'S1', 'event_id': 'E1', 'memory_id': 'A'},
            {'stage_id': 'S1', 'event_id': 'E1', 'memory_id': 'B'},
            {'stage_id': 'S1', 'event_id': 'E1', 'memory_id': 'A'},
        ]
        for row in rows:
            self.store.append('fake/event-projections.jsonl', row)
        self.assertEqual(self.store.read('fake/event-projections.jsonl'), rows)
        self.assertEqual(self.base.appended, [])

    def test_freeze_blocks_dream_state_replace(self):
        (self.root / 'fake' / 'brain-write-freeze').write_text('frozen', encoding='utf-8')
        with self.assertRaises(RuntimeError):
            self.store.replace('fake/event-dream.json', {'status': 'running'})
        self.assertIsNone(self.store.read_state('fake/event-dream.json'))

    def test_delete_requests_are_durable_in_brain(self):
        self.store.append('fake/delete-requests.jsonl', {
            'request_id': 'r1', 'memory_id': 'm1', 'reason': 'test', 'status': 'pending',
            'created_at': 'now'})
        rows = self.store.read('fake/delete-requests.jsonl')
        self.assertEqual(rows[0]['request_id'], 'r1')
        self.assertEqual(self.base.appended, [])

    def test_sidecar_link_and_lock_are_folded(self):
        self.store.append('fake/memory-sidecar.jsonl', {'memory_id': 'm1', 'kind': 'link', 'to': 'm2'})
        self.store.append('fake/memory-sidecar.jsonl', {'memory_id': 'm1', 'kind': 'locked'})
        rows = self.store.read('fake/memory-sidecar.jsonl')
        self.assertEqual(len(rows), 2)
        connection = sqlite3.connect(self.db)
        try:
            links, locked = connection.execute(
                'SELECT links_json,locked FROM sidecar_state WHERE memory_id=?', ('m1',)).fetchone()
        finally:
            connection.close()
        self.assertEqual(json.loads(links), ['m2'])
        self.assertEqual(locked, 1)


if __name__ == '__main__':
    unittest.main()
