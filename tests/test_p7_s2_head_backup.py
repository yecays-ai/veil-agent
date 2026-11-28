import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from veil.brain.storage import brain_backup, brain_events, brain_writer, veil_brain


class HeadCacheBackupTests(unittest.TestCase):
    def test_head_vectors_follow_role_bundle_restore(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temporary:
            root = Path(temporary)
            role = root / 'peter'
            role.mkdir()
            connection = sqlite3.connect(role / 'brain.db')
            connection.executescript(veil_brain.SCHEMA)
            brain_events.ensure_schema(connection)
            brain_writer.ensure_schema(connection)
            connection.close()
            path = role / 'experience-head-vectors.json'
            cached = {'synthetic-fingerprint': {'space': 'synthetic-space',
                                               'model': 'fake', 'vector': [1.0, 0.0]}}
            path.write_text(json.dumps(cached), encoding='utf-8')
            bundle = root / 'bundle'
            result = brain_backup.backup_bundle(role, bundle)
            self.assertIn(path.name, result['manifest']['companions'])
            path.write_text('{}', encoding='utf-8')
            brain_backup.restore_bundle(bundle, role)
            self.assertEqual(json.loads(path.read_text(encoding='utf-8')), cached)


if __name__ == '__main__':
    unittest.main()
