"""P2-A: full event story/stage storage and lossless export. Fictional data only."""
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

import brain_events


class EventStorageTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.db = self.root / 'brain.db'
        connection = sqlite3.connect(self.db)
        connection.executescript(
            'CREATE TABLE meta(key TEXT PRIMARY KEY, value TEXT);'
            'CREATE TABLE events(event_id TEXT PRIMARY KEY, memory_id TEXT, source_count INTEGER NOT NULL DEFAULT 0);'
            'CREATE TABLE event_sources(event_id TEXT NOT NULL, ord INTEGER NOT NULL, memory_id TEXT, part TEXT, PRIMARY KEY(event_id, ord));'
            'CREATE TABLE event_stages(stage_id TEXT PRIMARY KEY, event_id TEXT, at TEXT, state TEXT);')
        connection.commit()
        connection.close()
        self.events = self.root / 'events.jsonl'
        self.stages = self.root / 'event-stages.jsonl'
        rows = [{'event_id': 'V01', 'memory_id': 'M1', 'title': 'A day',
                 'story': 'The whole story.', 'status': 'closed', 'first_at': 't1',
                 'last_at': 't2', 'story_source': 'curator', 'confidence': 0.8,
                 'approved': True, 'created_at': 'c', 'updated_at': 'u',
                 'related_events': ['V02'], 'previous_events': ['V00'],
                 'source_parts': [{'part': 1}], 'sources': [{'memory_id': 'M1'}],
                 'import_baseline': False, 'import_signature': 'sig',
                 'a_field_from_the_future': {'kept': True}}]
        stages = [{'stage_id': 'S1', 'event_id': 'V01', 'at': 't1', 'state': 'open',
                   'text': 'first stage', 'facts': ['fact one']},
                  {'stage_id': 'S2', 'event_id': 'V01', 'at': 't2', 'state': 'closed',
                   'text': 'second stage', 'facts': []}]
        self.events.write_text('\n'.join(json.dumps(r, ensure_ascii=False) for r in rows) + '\n', encoding='utf-8')
        self.stages.write_text('\n'.join(json.dumps(r, ensure_ascii=False) for r in stages) + '\n', encoding='utf-8')

    def test_migration_is_lossless_idempotent_and_ordered(self):
        connection = sqlite3.connect(self.db)
        try:
            first = brain_events.import_events(connection, self.events, self.stages)
            self.assertEqual(first, {'events': 1, 'stages': 2})
            self.assertEqual(brain_events.import_events(connection, self.events, self.stages),
                             {'events': 1, 'stages': 2})
            item = brain_events.read_event(connection, 'V01')
        finally:
            connection.close()
        self.assertEqual(item['story'], 'The whole story.')
        self.assertEqual(item['sources'], [{'memory_id': 'M1'}])
        self.assertEqual([stage['stage_id'] for stage in item['stages']], ['S1', 'S2'])
        self.assertEqual([stage['text'] for stage in item['stages']], ['first stage', 'second stage'])
        self.assertEqual(item['payload']['a_field_from_the_future'], {'kept': True})

    def test_export_round_trips_the_same_fields(self):
        connection = sqlite3.connect(self.db)
        try:
            brain_events.import_events(connection, self.events, self.stages)
            counts = brain_events.export_jsonl(connection, self.root / 'export')
        finally:
            connection.close()
        self.assertEqual((counts['events'], counts['stages']), (1, 2))
        self.assertEqual(set(counts['ledger']), set(brain_events.EVENT_LEDGER_FILES.values()))
        exported = json.loads((self.root / 'export' / 'events.jsonl').read_text(encoding='utf-8'))
        self.assertEqual(exported['story'], 'The whole story.')
        self.assertEqual(exported['a_field_from_the_future'], {'kept': True})
        stages = [json.loads(line) for line in
                  (self.root / 'export' / 'event-stages.jsonl').read_text(encoding='utf-8').splitlines()]
        self.assertEqual([row['stage_id'] for row in stages], ['S1', 'S2'])


if __name__ == '__main__':
    unittest.main()
