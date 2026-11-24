"""P2-A: transaction, three-state feedback, tombstones and versioning. Fictional data only."""
import json
import sqlite3
import tempfile
import unittest

import brain_writer
import veil_brain


def vector(value=0.1):
    return [value] * 1024


class WriterTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.db = self.temp.name + '/brain.db'
        connection = sqlite3.connect(self.db)
        connection.executescript(veil_brain.SCHEMA)
        connection.commit()
        connection.close()

    def connect(self):
        return sqlite3.connect(self.db)

    def test_create_saved_and_pending_are_distinct(self):
        connection = self.connect()
        try:
            first = brain_writer.create_memory(connection, {'body': 'one', 'title': 'One'},
                                               embedder=lambda _: vector())
            self.assertEqual(first['status'], 'saved')
            self.assertEqual(connection.execute('SELECT COUNT(*) FROM vectors').fetchone()[0], 1)
            second = brain_writer.create_memory(connection, {'body': 'two', 'title': 'Two'},
                                                embedder=lambda _: (_ for _ in ()).throw(RuntimeError('down')))
            self.assertEqual(second['status'], 'saved_index_pending')
            self.assertEqual(connection.execute('SELECT vector_state FROM memories WHERE id=?',
                                                (second['memory_id'],)).fetchone()[0], 'pending')
            self.assertEqual(connection.execute('SELECT COUNT(*) FROM vectors').fetchone()[0], 1)
        finally:
            connection.close()

    def test_update_archives_old_body_and_restore_brings_it_back(self):
        connection = self.connect()
        try:
            created = brain_writer.create_memory(connection, {'body': 'before', 'title': 'T'},
                                                 embedder=lambda _: vector())
            result = brain_writer.update_memory(connection, created['memory_id'],
                                                {'body': 'after'}, embedder=lambda _: vector())
            self.assertEqual(result['status'], 'saved')
            version = connection.execute('SELECT seq,old_content FROM versions').fetchone()
            self.assertEqual(version[1], 'before')
            restored = brain_writer.restore_version(connection, created['memory_id'], version[0],
                                                    embedder=lambda _: vector())
            self.assertEqual(restored['status'], 'saved')
            self.assertEqual(connection.execute('SELECT body FROM memories').fetchone()[0], 'before')
        finally:
            connection.close()

    def test_hard_delete_keeps_only_a_minimal_tombstone(self):
        connection = self.connect()
        try:
            created = brain_writer.create_memory(connection, {'body': 'secret body', 'title': 'S'},
                                                 embedder=lambda _: vector())
            # Trigger a state change so a sidecar_event row exists before hard delete.
            brain_writer.set_state(connection, created['memory_id'], hidden=True)
            self.assertGreater(
                connection.execute('SELECT COUNT(*) FROM sidecar_events').fetchone()[0], 0)
            brain_writer.hard_delete(connection, created['memory_id'], 'test reason')
            self.assertEqual(connection.execute('SELECT COUNT(*) FROM memories').fetchone()[0], 0)
            self.assertEqual(connection.execute('SELECT COUNT(*) FROM vectors').fetchone()[0], 0)
            self.assertEqual(connection.execute('SELECT COUNT(*) FROM versions').fetchone()[0], 0)
            self.assertEqual(connection.execute('SELECT COUNT(*) FROM sidecar_events').fetchone()[0], 0)
            self.assertEqual(connection.execute('SELECT COUNT(*) FROM event_sources').fetchone()[0], 0)
            self.assertEqual(connection.execute('SELECT COUNT(*) FROM events').fetchone()[0], 0)
            self.assertEqual(connection.execute('SELECT COUNT(*) FROM event_stages').fetchone()[0], 0)
            # P2-A review: sidecar_events must also be cleared.
            self.assertEqual(
                connection.execute('SELECT COUNT(*) FROM sidecar_events').fetchone()[0], 0,
                'hard_delete must erase sidecar_events rows for the deleted memory')
            row = connection.execute('SELECT memory_id,deleted_at,reason_hash FROM tombstones').fetchone()
            self.assertEqual(row[0], created['memory_id'])
            self.assertNotIn('secret', json.dumps(row, ensure_ascii=False))
        finally:
            connection.close()

    def test_hidden_and_locked_do_not_delete_and_locked_is_recorded(self):
        connection = self.connect()
        try:
            created = brain_writer.create_memory(connection, {'body': 'body'}, embedder=lambda _: vector())
            brain_writer.set_state(connection, created['memory_id'], hidden=True, locked=True)
            self.assertEqual(connection.execute('SELECT is_hidden FROM memories').fetchone()[0], 1)
            kinds = [row[0] for row in connection.execute('SELECT kind FROM sidecar_events')]
            self.assertIn('locked', kinds)
        finally:
            connection.close()

    def test_pending_vector_retry_is_idempotent(self):
        connection = self.connect()
        try:
            created = brain_writer.create_memory(connection, {'body': 'retry me'}, embedder=None)
            self.assertEqual(created['status'], 'saved_index_pending')
            first = brain_writer.retry_pending_vectors(connection, lambda _: vector())
            second = brain_writer.retry_pending_vectors(connection, lambda _: vector())
            self.assertEqual(first['saved'], 1)
            self.assertEqual(second['retried'], 0)
            self.assertEqual(connection.execute('SELECT COUNT(*) FROM vectors').fetchone()[0], 1)
        finally:
            connection.close()

    def test_failed_insert_rolls_back_without_journal(self):
        connection = self.connect()
        try:
            brain_writer.create_memory(connection, {'body': 'one'}, embedder=lambda _: vector())
            before = connection.execute('SELECT COUNT(*) FROM write_journal').fetchone()[0]
            with self.assertRaises(sqlite3.IntegrityError):
                brain_writer.create_memory(connection, {'id': 'dup', 'body': 'a'}, embedder=lambda _: vector())
                brain_writer.create_memory(connection, {'id': 'dup', 'body': 'b'}, embedder=lambda _: vector())
            self.assertEqual(connection.execute('SELECT COUNT(*) FROM write_journal').fetchone()[0], before + 1)
        finally:
            connection.close()


if __name__ == '__main__':
    unittest.main()
