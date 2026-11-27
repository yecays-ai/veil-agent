"""Ticket 428 / P0-A: isolated import of a fictional memory copy into brain.db.

No real memory, no model, no network. Every case builds its own temporary source tree.
"""
import json
import shutil
import sqlite3
import struct
import tempfile
import unittest
from pathlib import Path

from veil.brain.storage import veil_brain


def frontmatter(**fields):
    lines = ['---']
    for key, value in fields.items():
        lines.append('%s: %s' % (key, value))
    lines.append('---')
    return '\n'.join(lines) + '\n'


def write(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding='utf-8')


class BrainImportTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = self.root / 'source'
        memory = self.source / 'memory'
        self.m1 = frontmatter(id='m1', title='First fictional memory', created="'2026-10-19T10:00:00+08:00'",
                              importance='7', type='dynamic', source_tool='hold',
                              why_remembered='because it is a fixture') + 'Alpha body.\nSecond line.\n'
        self.m2 = frontmatter(id='m2', title='A tombstoned fixture',
                              created="'2026-10-20T10:00:00+08:00'", importance='5', type='dynamic',
                              tombstone='true', deleted_at="'2026-11-01T00:00:00+08:00'",
                              erasure_mode='tombstone_only') + 'Archived body.\n'
        write(memory / 'dynamic' / 'm1.md', self.m1)
        write(memory / 'archive' / 'm2.md', self.m2)
        connection = sqlite3.connect(str(memory / 'embeddings.db'))
        try:
            connection.execute('CREATE TABLE embeddings (bucket_id TEXT PRIMARY KEY, embedding TEXT NOT NULL, '
                               'updated_at TEXT NOT NULL, content_hash TEXT NOT NULL DEFAULT \'\', '
                               'meaning_embedding TEXT)')
            self.vector = struct.pack('<4f', 0.25, -0.5, 0.75, 1.0)
            connection.execute('INSERT INTO embeddings(bucket_id, embedding, updated_at, content_hash) '
                               'VALUES(?,?,?,?)', ('m1', self.vector, '2026-10-19T10:00:00+08:00', 'abc'))
            connection.commit()
        finally:
            connection.close()
        old = frontmatter(id='m1', title='First fictional memory', importance='5') + 'Old body.\n'
        write(self.source / 'memory-versions.jsonl', json.dumps(
            {'time': '2026-10-20T00:00:00+08:00', 'memory_id': 'm1', 'fields': ['content'],
             'operation_reason': 'fictional correction', 'why_remembered': '',
             'old_content': old}, ensure_ascii=False) + '\n')
        side = [
            {'time': '2026-10-20T00:00:00+08:00', 'memory_id': 'm1', 'kind': 'lock'},
            {'time': '2026-10-20T00:01:00+08:00', 'memory_id': 'm1', 'kind': 'link', 'to': 'm2'},
            {'time': '2026-10-20T00:02:00+08:00', 'memory_id': 'm1', 'kind': 'source', 'via': 'hold'},
        ]
        write(self.source / 'memory-sidecar.jsonl',
              ''.join(json.dumps(row, ensure_ascii=False) + '\n' for row in side))
        write(self.source / 'events.jsonl', json.dumps(
            {'event_id': 'E1', 'memory_id': 'm1', 'title': 'Fixture event',
             'story': 'FICTIONAL_STORY_MUST_NOT_BE_IMPORTED',
             'source_memory_ids': ['m1', 'gone']}, ensure_ascii=False) + '\n')
        self.db = self.root / 'brain.db'

    def import_once(self, db=None):
        return veil_brain.run_import(self.source, db or self.db)

    def test_import_counts_hashes_and_history(self):
        snapshot = self.root / 'snapshot'
        importer = veil_brain.run_import(self.source, self.db, snapshot)
        self.assertEqual(importer.problems, [])
        self.assertEqual(importer.counts['memories'], 2)
        index = (snapshot / '_INDEX.md').read_text(encoding='utf-8')
        self.assertIn('m1', index)
        self.assertIn('版本=1', index)
        self.assertIn('tombstone', index)
        self.assertEqual((snapshot / 'dynamic' / 'm1.md').read_bytes(),
                         (self.source / 'memory' / 'dynamic' / 'm1.md').read_bytes())
        self.assertEqual(importer.counts['versions'], 1)
        self.assertEqual(importer.counts['sidecar_events'], 3)
        self.assertEqual(importer.counts['sidecar_state'], 1)
        self.assertEqual(importer.counts['vectors'], 1)
        self.assertEqual(importer.counts['events'], 1)
        self.assertEqual(importer.counts['event_sources'], 2)
        connection = sqlite3.connect(str(self.db))
        try:
            body, sha, raw = connection.execute(
                'SELECT body, body_sha256, raw_markdown FROM memories WHERE id=?', ('m1',)).fetchone()
            on_disk = (self.source / 'memory' / 'dynamic' / 'm1.md').read_bytes().decode('utf-8')
            self.assertEqual(body, veil_brain.split_frontmatter(on_disk)[1])
            self.assertEqual(sha, veil_brain.sha256_text(body))
            self.assertEqual(raw, on_disk)
            tombstone = connection.execute(
                'SELECT is_tombstone, is_deleted, erasure_mode FROM memories WHERE id=?',
                ('m2',)).fetchone()
            self.assertEqual(tombstone, (1, 1, 'tombstone_only'))
            dim, blob, blob_sha = connection.execute(
                'SELECT dim, vector, vector_sha256 FROM vectors WHERE memory_id=?', ('m1',)).fetchone()
            self.assertEqual(dim, 4)
            self.assertEqual(blob, self.vector)
            self.assertEqual(blob_sha, veil_brain.sha256_bytes(self.vector))
            story_column = [row[1] for row in connection.execute('PRAGMA table_info(events)')]
            self.assertNotIn('story', story_column)
        finally:
            connection.close()
        text = veil_brain.inspect(self.db, 'm1')
        self.assertIn('First fictional memory', text)
        self.assertIn('fictional correction', text)
        self.assertIn('locked: 1', text)
        self.assertIn('m2', text)

    def test_event_mapping_keeps_missing_source_as_dangling(self):
        importer = self.import_once()
        self.assertEqual(importer.dangling['event_sources'], ['gone'])
        self.assertNotIn('gone', '\n'.join(importer.problems))
        report = (self.root / 'report.md')
        importer.report = report
        importer.write_report()
        text = report.read_text(encoding='utf-8')
        self.assertIn('event_sources: 1 个', text)
        self.assertNotIn('FICTIONAL_STORY_MUST_NOT_BE_IMPORTED', text)

    def test_reimport_is_deterministic(self):
        first = self.import_once()
        second_db = self.root / 'brain2.db'
        second = veil_brain.run_import(self.source, second_db)
        self.assertEqual(first.digest, second.digest)
        self.assertEqual(veil_brain.check_db(self.db)['digest'],
                         veil_brain.check_db(second_db)['digest'])

    def test_append_only_event_updates_fold_to_one_row(self):
        """The live events.jsonl appends an update row for the same event; import must keep the last."""
        with (self.source / 'events.jsonl').open('a', encoding='utf-8') as handle:
            handle.write(json.dumps({'event_id': 'E1', 'memory_id': 'm1',
                                     'title': 'Updated fixture event',
                                     'source_memory_ids': ['m1', 'm1']},
                                    ensure_ascii=False) + '\n')
        importer = veil_brain.run_import(self.source, self.db)
        self.assertEqual(importer.problems, [])
        self.assertEqual(importer.counts['events'], 1)
        self.assertEqual(importer.counts['event_sources'], 1)
        connection = sqlite3.connect(str(self.db))
        try:
            self.assertEqual(connection.execute(
                'SELECT COUNT(*) FROM events WHERE event_id=?', ('E1',)).fetchone()[0], 1)
        finally:
            connection.close()

    def test_db_is_portable_to_another_directory(self):
        first = self.import_once()
        portable = self.root / 'elsewhere'
        portable.mkdir()
        moved = portable / 'brain-copy.db'
        shutil.copy2(self.db, moved)
        result = veil_brain.check_db(moved)
        self.assertEqual(result['integrity'], 'ok')
        self.assertEqual(result['digest'], first.digest)
        self.assertEqual(result['counts']['memories'], 2)
        self.assertIn('fictional correction', veil_brain.inspect(moved, 'm1'))

    def test_check_notices_changed_rows(self):
        self.import_once()
        connection = sqlite3.connect(str(self.db))
        try:
            connection.execute("UPDATE memories SET title='tampered' WHERE id='m1'")
            connection.commit()
        finally:
            connection.close()
        result = veil_brain.check_db(self.db)
        self.assertEqual(result['integrity'], 'ok')
        self.assertFalse(result['digest_ok'])

    def test_source_tree_is_unchanged(self):
        before = veil_brain.manifest(self.source)
        importer = self.import_once()
        self.assertEqual(importer.problems, [])
        self.assertEqual(veil_brain.manifest(self.source), before)

    def test_output_inside_source_is_rejected(self):
        with self.assertRaises(ValueError):
            veil_brain.run_import(self.source, self.source / 'brain.db')
        self.assertFalse((self.source / 'brain.db').exists())

    def test_missing_source_is_rejected(self):
        with self.assertRaises(ValueError):
            veil_brain.run_import(self.root / 'nope', self.db)
        self.assertFalse(self.db.exists())

    def test_missing_id_fails_without_writing_rows(self):
        write(self.source / 'memory' / 'dynamic' / 'broken.md', '---\ntitle: no id\n---\nbody\n')
        importer = self.import_once(self.root / 'broken.db')
        self.assertTrue(importer.problems)
        self.assertIn('missing id', ' '.join(importer.problems))
        self.assertFalse((self.root / 'broken.db').exists())


if __name__ == '__main__':
    unittest.main()
