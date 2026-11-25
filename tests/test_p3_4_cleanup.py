"""P3-4 cleanup planner/applier on a fictional isolated role (never real data)."""
import json
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
sys.path.insert(0, str(ROOT))
import p3_4_cleanup
import brain_writer
import veil_brain


class CleanupTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / 'repo'
        self.role_dir = self.root / 'data' / 'peter'
        self.memory = self.role_dir / 'memory'
        self.role_dir.mkdir(parents=True)
        self.db = self.role_dir / 'brain.db'
        connection = sqlite3.connect(str(self.db))
        try:
            connection.executescript(veil_brain.SCHEMA)
            brain_writer.ensure_schema(connection)
            self.normal = brain_writer.create_memory(
                connection, {'body': 'normal body', 'title': 'Normal'}, embedder=None)
            connection.execute(
                "UPDATE memories SET meaning='keep-me', valence=3, arousal=4, "
                "domain_json='[\"keep\"]', tags_json='[\"keep-tag\"]' WHERE id=?",
                (self.normal['memory_id'],))
            self.deleted = brain_writer.create_memory(
                connection, {'body': 'deleted secret', 'title': 'Deleted'}, embedder=None)
            connection.execute('UPDATE memories SET is_deleted=1 WHERE id=?',
                               (self.deleted['memory_id'],))
            connection.execute(
                'INSERT INTO versions(memory_id,time,operation_reason,why_remembered,fields_json,'
                'old_content,old_sha256) VALUES(?,?,?,?,?,?,?)',
                (self.deleted['memory_id'], 'old', '', '', '[]', 'old deleted body', 'x'))
            connection.execute(
                'INSERT INTO vectors(memory_id,dim,vector,vector_sha256,updated_at,content_hash) '
                'VALUES(?,?,?,?,?,?)', (self.deleted['memory_id'], 1024, b'x', 'x', 'now', 'x'))
            self.letter = brain_writer.create_memory(
                connection, {'body': 'letter body', 'title': 'Letter'}, embedder=None)
            connection.execute(
                "UPDATE memories SET type='letter', bucket='letters', file_rel='letters/a.md' "
                'WHERE id=?', (self.letter['memory_id'],))
            self.plan = brain_writer.create_memory(
                connection, {'body': 'plan body', 'title': 'Plan'}, embedder=None)
            connection.execute(
                "UPDATE memories SET type='plan', bucket='plans', file_rel='plans/a.md' "
                'WHERE id=?', (self.plan['memory_id'],))
            connection.execute(
                'INSERT INTO relations(memory_id,ord,target_id,type,score,status,auto,label) '
                'VALUES(?,?,?,?,?,?,?,?)',
                (self.normal['memory_id'], 0, 'target', 'related_to', 1.0, 'active', 0, 'x'))
            connection.execute(
                'INSERT INTO sources(memory_id,ord,kind,ref,status,ranges_json) VALUES(?,?,?,?,?,?)',
                (self.normal['memory_id'], 0, 'chat', 'ref', 'active', '[]'))
            connection.execute(
                'INSERT INTO quotes(memory_id,ord,speaker,text) VALUES(?,?,?,?)',
                (self.normal['memory_id'], 0, 'Lan', 'quote'))
            connection.commit()
        finally:
            connection.close()
        for directory in ('dynamic', 'letters', 'plans', 'archive', '_agent', '_media'):
            (self.memory / directory).mkdir(parents=True, exist_ok=True)
        (self.memory / 'dynamic' / 'old.md').write_text('old vault', encoding='utf-8')
        (self.memory / '_agent' / 'state.jsonl').write_text('{}', encoding='utf-8')
        (self.memory / '_media' / 'keep.bin').write_bytes(b'keep-media')
        (self.memory / 'archive').mkdir(exist_ok=True)
        (self.role_dir / 'archive' / 'pre-p3-2-event-ledgers-x').mkdir(parents=True)
        (self.role_dir / 'archive' / 'pre-p3-2-event-ledgers-x' / 'manifest.json').write_text('{}')
        (self.role_dir / 'memory.backup-old').mkdir()
        (self.role_dir / '.event-import-backup-old').mkdir()
        (self.role_dir / 'backups' / 'old').mkdir(parents=True)
        (self.root / '.deps' / 'ombre').mkdir(parents=True)
        (self.root / '.deps' / 'ombre' / 'runtime.bin').write_bytes(b'runtime')
        (self.root / '.local-tmp').mkdir()
        (self.root / '.local-tmp' / 'ombre-115d831.zip').write_bytes(b'installer')
        (self.root / '.local-tmp' / 'ombre312').mkdir()
        self.backup = Path(self.temp.name) / 'backup'
        self.backup.mkdir()
        self.archive = Path(self.temp.name) / 'archive-outside'
        self.patch = mock.patch.object(p3_4_cleanup.backup_restore, 'verify_backup',
                                       return_value={'files': [1, 2]})
        self.patch.start()
        self.addCleanup(self.patch.stop)

    def test_plan_lists_only_cleanup_targets_and_protects_live_state(self):
        plan = p3_4_cleanup._plan(self.root, 'peter', self.backup, self.archive)
        ids = {row['id'] for row in plan['tombstones']}
        self.assertIn(self.deleted['memory_id'], ids)
        self.assertEqual({row['id'] for row in plan['letters_plans']},
                         {self.letter['memory_id'], self.plan['memory_id']})
        paths = {item['path'] for item in plan['file_targets']}
        self.assertIn('data/peter/memory/dynamic', paths)
        self.assertIn('data/peter/memory.backup-old', paths)
        self.assertIn('.deps/ombre', paths)
        self.assertNotIn('data/peter/memory/_agent', paths)
        self.assertNotIn('data/peter/memory/_media', paths)
        self.assertNotIn('data/peter/archive/pre-p3-2-event-ledgers-x', paths)
        self.assertNotIn('data/peter/brain.db', paths)

    def test_apply_removes_only_listed_rows_and_files(self):
        plan = p3_4_cleanup._plan(self.root, 'peter', self.backup, self.archive)
        plan_path = Path(self.temp.name) / 'plan.json'
        p3_4_cleanup._write_json(plan_path, plan)
        result = p3_4_cleanup.apply_plan(
            self.root, 'peter', plan_path, backup=self.backup,
            archive_dir=self.archive, confirm=p3_4_cleanup.CONFIRM_WORD)
        self.assertEqual(result['db']['tombstones_removed'], 1)
        self.assertEqual(result['db']['letters_plans_removed'], 2)
        connection = sqlite3.connect(str(self.db))
        try:
            self.assertEqual(connection.execute(
                'SELECT COUNT(*) FROM memories WHERE id=?', (self.normal['memory_id'],)).fetchone()[0], 1)
            row = connection.execute(
                'SELECT meaning,valence,arousal,domain_json,tags_json FROM memories WHERE id=?',
                (self.normal['memory_id'],)).fetchone()
            self.assertEqual(row[0], 'keep-me')
            self.assertEqual(row[1:3], (3, 4))
            self.assertEqual(connection.execute(
                'SELECT COUNT(*) FROM memories WHERE id=?', (self.deleted['memory_id'],)).fetchone()[0], 0)
            self.assertEqual(connection.execute(
                'SELECT COUNT(*) FROM memories WHERE id IN (?,?)',
                (self.letter['memory_id'], self.plan['memory_id'])).fetchone()[0], 0)
            self.assertEqual(connection.execute(
                'SELECT COUNT(*) FROM versions WHERE memory_id=?',
                (self.deleted['memory_id'],)).fetchone()[0], 0)
            self.assertEqual(connection.execute(
                'SELECT COUNT(*) FROM vectors WHERE memory_id=?',
                (self.deleted['memory_id'],)).fetchone()[0], 0)
            self.assertEqual(connection.execute(
                'SELECT COUNT(*) FROM tombstones WHERE memory_id IN (?,?)',
                (self.deleted['memory_id'], self.letter['memory_id'])).fetchone()[0], 2)
            self.assertEqual(connection.execute(
                'SELECT COUNT(*) FROM relations WHERE memory_id=?', (self.normal['memory_id'],)).fetchone()[0], 1)
            self.assertEqual(connection.execute(
                'SELECT COUNT(*) FROM sources WHERE memory_id=?', (self.normal['memory_id'],)).fetchone()[0], 1)
            self.assertEqual(connection.execute(
                'SELECT COUNT(*) FROM quotes WHERE memory_id=?', (self.normal['memory_id'],)).fetchone()[0], 1)
        finally:
            connection.close()
        self.assertTrue((self.memory / '_agent' / 'state.jsonl').is_file())
        self.assertTrue((self.memory / '_media' / 'keep.bin').is_file())
        self.assertTrue((self.role_dir / 'archive' / 'pre-p3-2-event-ledgers-x' / 'manifest.json').is_file())
        self.assertFalse((self.memory / 'dynamic').exists())
        self.assertFalse((self.root / '.deps' / 'ombre').exists())
        self.assertTrue((self.archive / 'ombre-115d831.zip').is_file())
        self.assertTrue((self.archive / 'letters-plans.json').is_file())
        self.assertEqual(result['verify']['tombstone_rows'], 0)
        self.assertEqual(result['verify']['letter_plan_rows'], 0)

    def test_wrong_confirmation_and_stale_plan_are_refused(self):
        plan = p3_4_cleanup._plan(self.root, 'peter', self.backup, self.archive)
        plan_path = Path(self.temp.name) / 'plan.json'
        p3_4_cleanup._write_json(plan_path, plan)
        with self.assertRaises(RuntimeError):
            p3_4_cleanup.apply_plan(self.root, 'peter', plan_path, backup=self.backup,
                                    archive_dir=self.archive, confirm='no')
        p3_4_cleanup._write_json(plan_path, plan)
        connection = sqlite3.connect(str(self.db))
        try:
            connection.execute('UPDATE memories SET title=? WHERE id=?', ('changed', self.normal['memory_id']))
            connection.commit()
        finally:
            connection.close()
        with self.assertRaises(RuntimeError):
            p3_4_cleanup.apply_plan(self.root, 'peter', plan_path, backup=self.backup,
                                    archive_dir=self.archive, confirm=p3_4_cleanup.CONFIRM_WORD)


if __name__ == '__main__':
    unittest.main()
