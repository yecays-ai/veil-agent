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

    def test_versions_round_trip(self):
        self.store.append('fake/memory-versions.jsonl', {
            'memory_id': 'm1', 'fields': ['content'], 'operation_reason': 'edit',
            'why_remembered': '', 'old_content': 'old'})
        rows = self.store.read('fake/memory-versions.jsonl')
        self.assertEqual(rows[0]['old_content'], 'old')
        self.assertEqual(rows[0]['fields'], ['content'])
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
