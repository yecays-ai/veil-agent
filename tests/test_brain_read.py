"""P2-B read accessors from brain.db. No files or Ombre."""
import json
import sqlite3
import tempfile
import unittest

import brain_events
import brain_read
import brain_writer
import veil_brain


def vector():
    return [0.2] * 1024


class BrainReadTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.db = self.temp.name + '/brain.db'
        connection = sqlite3.connect(self.db)
        connection.executescript(veil_brain.SCHEMA)
        connection.commit()
        self.writer = connection
        brain_writer.ensure_schema(connection)
        self.created = brain_writer.create_memory(
            connection, {'body': 'old body', 'title': 'Title'}, embedder=lambda _: vector())
        brain_writer.update_memory(connection, self.created['memory_id'], {'body': 'new body'},
                                   embedder=lambda _: vector())
        brain_writer.set_state(connection, self.created['memory_id'], hidden=True, locked=True)
        self.target = brain_writer.create_memory(connection, {'body': 'target'}, embedder=lambda _: vector())
        connection.close()

    def test_panel_versions_state_and_vectors(self):
        with brain_read.BrainRead(self.db) as reader:
            entry = next(item for item in reader.panel_entries()
                         if item['memory_id'] == self.created['memory_id'])
            self.assertEqual(entry['memory_id'], self.created['memory_id'])
            self.assertTrue(entry['hidden'])
            self.assertTrue(entry['locked'])
            self.assertEqual(entry['versions'], 1)
            self.assertEqual(reader.content(self.created['memory_id']), 'new body')
            self.assertEqual(reader.versions(self.created['memory_id'])[0]['old_content'], 'old body')
            self.assertEqual(len(reader.all_vectors()[self.created['memory_id']]), 1024)
            self.assertIn('Title', reader.raw(self.created['memory_id']))

    def test_archived_and_events(self):
        with brain_read.BrainRead(self.db) as reader:
            self.assertEqual(reader.archived(), [])
        connection = sqlite3.connect(self.db)
        try:
            brain_writer.merge_into(connection, self.created['memory_id'], self.target['memory_id'], 'dup')
        finally:
            connection.close()
        with brain_read.BrainRead(self.db) as reader:
            self.assertEqual(reader.archived()[0]['memory_id'], self.created['memory_id'])


if __name__ == '__main__':
    unittest.main()
