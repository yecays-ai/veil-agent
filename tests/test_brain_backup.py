"""P2-A: online backup/restore keeps the same digest and counts."""
import sqlite3
import tempfile
import unittest

import brain_backup
import brain_writer
import veil_brain


class BackupTests(unittest.TestCase):
    def test_backup_and_restore(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        root = temp.name
        db = root + '/brain.db'
        connection = sqlite3.connect(db)
        connection.executescript(veil_brain.SCHEMA)
        brain_writer.ensure_schema(connection)
        brain_writer.create_memory(connection, {'body': 'backup me'}, embedder=lambda _: [0.1] * 1024)
        connection.close()
        saved = brain_backup.backup(db, root + '/backup.db')
        restored = brain_backup.restore(root + '/backup.db', root + '/restored.db')
        self.assertEqual(saved['sha256'], restored['sha256'])
        for path in (db, root + '/restored.db'):
            connection = sqlite3.connect(path)
            try:
                self.assertEqual(connection.execute('SELECT COUNT(*) FROM memories').fetchone()[0], 1)
                self.assertEqual(connection.execute('PRAGMA integrity_check').fetchone()[0], 'ok')
            finally:
                connection.close()


if __name__ == '__main__':
    unittest.main()
