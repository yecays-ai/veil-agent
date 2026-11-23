"""Ticket 429 / P0-B: shadow sync cases on fictional role data only."""
import json
import shutil
import sqlite3
import struct
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

import brain_sync
import veil_brain

FEEL = 'feel_202610191458_V050'


def frontmatter(**fields):
    lines = ['---']
    for key, value in fields.items():
        lines.append('%s: %s' % (key, value))
    lines.append('---')
    return '\n'.join(lines) + '\n'


def write(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('w', encoding='utf-8', newline='') as handle:
        handle.write(text)


class ShadowSyncTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.seed = self.root / 'seed'
        self.role = self.root / 'peter'
        self.memory = self.seed / 'memory'
        write(self.memory / 'dynamic' / 'm1.md',
              frontmatter(id='m1', title='First fixture', created="'2026-10-19T10:00:00+08:00'",
                          importance='7', type='dynamic', domain='[AI]') + 'Body one.\n')
        self.vector = struct.pack('<4f', 0.1, 0.2, 0.3, 0.4)
        db = self.memory / 'embeddings.db'
        connection = sqlite3.connect(str(db))
        try:
            connection.execute('CREATE TABLE embeddings (bucket_id TEXT PRIMARY KEY, embedding TEXT '
                               'NOT NULL, updated_at TEXT NOT NULL, content_hash TEXT NOT NULL '
                               "DEFAULT '', meaning_embedding TEXT)")
            connection.execute('INSERT INTO embeddings(bucket_id, embedding, updated_at, '
                               'content_hash) VALUES(?,?,?,?)',
                               ('m1', self.vector, '2026-10-19T10:00:00+08:00', ''))
            connection.commit()
        finally:
            connection.close()
        write(self.seed / 'memory-versions.jsonl', json.dumps(
            {'time': '2026-10-20T00:00:00+08:00', 'memory_id': 'm1', 'fields': ['content'],
             'operation_reason': 'fixture edit', 'old_content': 'Old body.\n'},
            ensure_ascii=False) + '\n')
        side = [
            {'time': '2026-10-20T00:00:00+08:00', 'memory_id': 'm1', 'kind': 'lock'},
            {'time': '2026-10-20T00:01:00+08:00', 'memory_id': 'm1', 'kind': 'link', 'to': FEEL},
        ]
        write(self.seed / 'memory-sidecar.jsonl',
              ''.join(json.dumps(row) + '\n' for row in side))
        write(self.seed / 'events.jsonl', json.dumps(
            {'event_id': 'E1', 'memory_id': 'm1', 'source_memory_ids': ['m1', FEEL]},
            ensure_ascii=False) + '\n')
        write(self.seed / 'memory-simplify-log.jsonl', json.dumps(
            {'time': '2026-11-01T00:00:00+08:00', 'action': 'feel-delete',
             'target': 'data/peter/memory/feel/x/%s.md' % FEEL, 'result': 'deleted'},
            ensure_ascii=False) + '\n')
        self.role.mkdir()
        importer = veil_brain.run_import(self.seed, self.role / 'brain.db')
        self.assertEqual(importer.problems, [])
        shutil.copytree(self.seed, self.role, dirs_exist_ok=True)
        self.cfg = SimpleNamespace(id='peter', memory={'brain': {'shadow': True,
                                                                 'reconcile_seconds': 3600}})
        self.store = SimpleNamespace(root=self.root)

    def shadow(self, **kwargs):
        options = {'shadow': True, 'reconcile_seconds': 3600}
        options.update(kwargs)
        sync = brain_sync.ShadowSync(self.cfg, self.store, options)
        self.addCleanup(sync.close)
        return sync

    def db(self):
        connection = sqlite3.connect(str(self.role / 'brain.db'))
        connection.row_factory = sqlite3.Row
        return connection

    def test_confirmed_write_is_mirrored(self):
        sync = self.shadow()
        write(self.role / 'memory' / 'dynamic' / 'm2.md',
              frontmatter(id='m2', title='Second fixture') + 'Body two.\n')
        sync.note_write('hold', {}, '新建→m2')
        self.assertTrue(sync.wait_idle(5))
        connection = self.db()
        try:
            row = connection.execute('SELECT body_sha256 FROM memories WHERE id=?',
                                     ('m2',)).fetchone()
        finally:
            connection.close()
        self.assertIsNotNone(row)
        self.assertEqual(row[0], veil_brain.sha256_text('Body two.\n'))

    def test_modify_hide_and_duplicate_sync(self):
        sync = self.shadow()
        path = self.role / 'memory' / 'dynamic' / 'm1.md'
        write(path, path.read_text(encoding='utf-8').replace('Body one.', 'Body one changed.')
              .replace('type: dynamic', 'type: dynamic\ndont_surface: true'))
        sync.note_write('trace', {'bucket_id': 'm1'}, 'updated')
        self.assertTrue(sync.wait_idle(5))
        sync.note_write('trace', {'bucket_id': 'm1'}, 'updated')
        self.assertTrue(sync.wait_idle(5))
        connection = self.db()
        try:
            rows = connection.execute('SELECT body, is_hidden FROM memories WHERE id=?',
                                      ('m1',)).fetchall()
        finally:
            connection.close()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0][0], 'Body one changed.\n')
        self.assertEqual(rows[0][1], 1)

    def test_delete_marks_removed_not_dropped(self):
        sync = self.shadow()
        (self.role / 'memory' / 'dynamic' / 'm1.md').unlink()
        sync.note_write('trace', {'bucket_id': 'm1'}, 'deleted')
        self.assertTrue(sync.wait_idle(5))
        connection = self.db()
        try:
            row = connection.execute('SELECT removed_at FROM memories WHERE id=?',
                                     ('m1',)).fetchone()
        finally:
            connection.close()
        self.assertIsNotNone(row)
        self.assertIsNotNone(row[0])

    def test_archive_merge_version_and_vector_follow(self):
        sync = self.shadow()
        dynamic = self.role / 'memory' / 'dynamic' / 'm1.md'
        archived = self.role / 'memory' / 'archive' / 'm1.md'
        archived.parent.mkdir(parents=True, exist_ok=True)
        dynamic.rename(archived)
        with (self.role / 'memory-versions.jsonl').open('a', encoding='utf-8') as handle:
            handle.write(json.dumps({'time': '2026-10-21T00:00:00+08:00', 'memory_id': 'm1',
                                     'operation_reason': 'second edit',
                                     'old_content': 'Older body.\n'}) + '\n')
        with (self.role / 'memory-sidecar.jsonl').open('a', encoding='utf-8') as handle:
            handle.write(json.dumps({'time': '2026-10-21T00:01:00+08:00', 'memory_id': 'm1',
                                     'kind': 'merged_into', 'to': 'm9'}) + '\n')
        new_vector = struct.pack('<4f', 0.9, 0.8, 0.7, 0.6)
        connection = sqlite3.connect(str(self.role / 'memory' / 'embeddings.db'))
        try:
            connection.execute('UPDATE embeddings SET embedding=? WHERE bucket_id=?',
                               (new_vector, 'm1'))
            connection.commit()
        finally:
            connection.close()
        summary = sync.reconcile()
        self.assertIsNotNone(summary)
        connection = self.db()
        try:
            row = connection.execute('SELECT bucket, removed_at FROM memories WHERE id=?',
                                     ('m1',)).fetchone()
            versions = connection.execute('SELECT COUNT(*) FROM versions WHERE memory_id=?',
                                          ('m1',)).fetchone()[0]
            merged = connection.execute('SELECT merged_into FROM sidecar_state WHERE memory_id=?',
                                        ('m1',)).fetchone()[0]
            vector = connection.execute('SELECT vector_sha256 FROM vectors WHERE memory_id=?',
                                        ('m1',)).fetchone()[0]
        finally:
            connection.close()
        self.assertEqual(row[0], 'archive')
        self.assertIsNone(row[1])
        self.assertEqual(versions, 2)
        self.assertEqual(merged, 'm9')
        self.assertEqual(vector, veil_brain.sha256_bytes(new_vector))

    def test_disabled_shadow_does_nothing(self):
        (self.role / 'brain-shadow-off').touch()
        sync = self.shadow()
        write(self.role / 'memory' / 'dynamic' / 'm2.md',
              frontmatter(id='m2', title='Second fixture') + 'Body two.\n')
        sync.note_write('hold', {}, '新建→m2')
        self.assertTrue(sync.wait_idle(2))
        connection = self.db()
        try:
            count = connection.execute("SELECT COUNT(*) FROM memories WHERE id='m2'").fetchone()[0]
        finally:
            connection.close()
        self.assertEqual(count, 0)

    def test_reconcile_is_a_noop_while_off(self):
        (self.role / 'brain-shadow-off').touch()
        sync = self.shadow()
        self.assertIsNone(sync.reconcile())

    def test_sync_never_modifies_the_source_files(self):
        def snapshot():
            files = {path.relative_to(self.role).as_posix(): path.read_bytes()
                     for path in (self.role / 'memory').rglob('*') if path.is_file()}
            for name in ('memory-versions.jsonl', 'memory-sidecar.jsonl', 'events.jsonl',
                         'memory-simplify-log.jsonl'):
                files[name] = (self.role / name).read_bytes()
            return files
        before = snapshot()
        sync = self.shadow()
        path = self.role / 'memory' / 'dynamic' / 'm1.md'
        write(path, path.read_text(encoding='utf-8').replace('Body one.', 'Changed body.'))
        sync.note_write('trace', {'bucket_id': 'm1'}, 'updated')
        self.assertTrue(sync.wait_idle(5))
        sync.reconcile()
        after = snapshot()
        changed = sorted(name for name in before if before[name] != after.get(name))
        self.assertEqual(changed, ['memory/dynamic/m1.md', 'memory-versions.jsonl']) if False else None
        self.assertEqual(changed, ['memory/dynamic/m1.md'])

    def test_broken_worker_never_raises_into_the_writer(self):
        sync = self.shadow()

        def broken(*_args, **_kwargs):
            raise sqlite3.OperationalError('fixture database is unavailable')

        sync._open = broken
        sync.note_write('hold', {}, '新建→m2')
        sync.wait_idle(3)
        self.assertTrue((self.role / 'brain-sync-log.jsonl').is_file())

    def test_reconcile_catches_a_missed_change(self):
        sync = self.shadow()
        path = self.role / 'memory' / 'dynamic' / 'm1.md'
        write(path, path.read_text(encoding='utf-8').replace('Body one.', 'Missed body.'))
        summary = sync.reconcile()
        self.assertIsNotNone(summary)
        self.assertEqual(summary['comparison']['memory_mismatches'], [])
        connection = self.db()
        try:
            body = connection.execute("SELECT body FROM memories WHERE id='m1'").fetchone()[0]
        finally:
            connection.close()
        self.assertEqual(body, 'Missed body.\n')

    def test_known_removed_references_are_classified_not_corruption(self):
        sync = self.shadow()
        summary = sync.reconcile()
        self.assertEqual(summary['references']['unexpected_event_refs'], [])
        self.assertEqual(summary['references']['unexpected_link_refs'], [])
        self.assertEqual(len(summary['references']['known_event_refs']), 1)
        self.assertEqual(len(summary['references']['known_link_refs']), 1)
        report = (self.role / 'brain-sync-report.md').read_text(encoding='utf-8')
        self.assertIn('已知历史引用', report)
        self.assertIn('意外差异：无', report)

    def test_check_accepts_the_shadow_database(self):
        sync = self.shadow()
        write(self.role / 'memory' / 'dynamic' / 'm2.md',
              frontmatter(id='m2', title='Second fixture') + 'Body two.\n')
        sync.note_write('hold', {}, '新建→m2')
        self.assertTrue(sync.wait_idle(5))
        result = veil_brain.check_db(self.role / 'brain.db')
        self.assertEqual(result['integrity'], 'ok')
        self.assertTrue(result['digest_ok'])

    def test_attach_skips_a_missing_database_quietly(self):
        root = self.root / 'fresh'
        root.mkdir()
        cfg = SimpleNamespace(id='peter', memory={'brain': {'shadow': True}})
        self.assertIsNone(brain_sync.attach(cfg, SimpleNamespace(root=root)))
        self.assertFalse((root / 'peter').exists())

    def test_queue_full_only_sets_an_in_memory_counter(self):
        sync = self.shadow()
        sync.close()
        sync._queue = brain_sync.queue.Queue(maxsize=1)
        sync._queue.put_nowait('dummy')
        sync.note_write('hold', {}, '新建→m3')
        self.assertGreaterEqual(sync._overflow, 1)
        self.assertFalse((self.role / 'brain-sync-log.jsonl').exists())


    def test_hard_delete_scrubs_content_and_versions(self):
        sync = self.shadow()
        self.assertIsNotNone(sync.reconcile())
        (self.role / 'memory' / 'dynamic' / 'm1.md').unlink()
        with (self.role / 'memory-versions.jsonl').open('w', encoding='utf-8') as handle:
            handle.write('')
        connection = sqlite3.connect(str(self.role / 'memory' / 'embeddings.db'))
        try:
            connection.execute("DELETE FROM embeddings WHERE bucket_id='m1'")
            connection.commit()
        finally:
            connection.close()
        sync.note_write('trace', {'bucket_id': 'm1'}, 'deleted')
        self.assertTrue(sync.wait_idle(5))
        connection = self.db()
        try:
            row = connection.execute(
                'SELECT removed_at, body, raw_markdown, title FROM memories WHERE id=?',
                ('m1',)).fetchone()
            versions = connection.execute('SELECT COUNT(*) FROM versions WHERE memory_id=?',
                                          ('m1',)).fetchone()[0]
            vectors = connection.execute('SELECT COUNT(*) FROM vectors WHERE memory_id=?',
                                         ('m1',)).fetchone()[0]
        finally:
            connection.close()
        self.assertIsNotNone(row[0])
        self.assertEqual((row[1], row[2], row[3]), ('', '', None))
        self.assertEqual((versions, vectors), (0, 0))

    def test_versions_ledger_rewrite_drops_erased_text(self):
        sync = self.shadow()
        sync.reconcile()
        with (self.role / 'memory-versions.jsonl').open('w', encoding='utf-8') as handle:
            handle.write('')
        summary = sync.reconcile()
        self.assertIsNotNone(summary)
        connection = self.db()
        try:
            count = connection.execute('SELECT COUNT(*) FROM versions').fetchone()[0]
        finally:
            connection.close()
        self.assertEqual(count, 0)

    def test_meaning_vector_changes_are_synced_and_compared(self):
        sync = self.shadow()
        sync.reconcile()
        meaning = struct.pack('<4f', 0.5, 0.5, 0.5, 0.5)
        connection = sqlite3.connect(str(self.role / 'memory' / 'embeddings.db'))
        try:
            connection.execute("UPDATE embeddings SET meaning_embedding=? WHERE bucket_id='m1'",
                               (meaning,))
            connection.commit()
        finally:
            connection.close()
        summary = sync.reconcile()
        self.assertEqual(summary['comparison']['vector_mismatches'], [])
        connection = self.db()
        try:
            row = connection.execute(
                'SELECT meaning_dim, meaning_sha256 FROM vectors WHERE memory_id=?',
                ('m1',)).fetchone()
        finally:
            connection.close()
        self.assertEqual(tuple(row), (4, veil_brain.sha256_bytes(meaning)))

    def test_event_revision_uses_the_latest_mapping(self):
        sync = self.shadow()
        with (self.role / 'events.jsonl').open('a', encoding='utf-8') as handle:
            handle.write(json.dumps({'event_id': 'E1', 'memory_id': 'm1',
                                     'source_memory_ids': ['m1']}) + '\n')
        summary = sync.reconcile()
        self.assertEqual(summary['comparison']['events']['event_mismatches'], [])
        connection = self.db()
        try:
            sources = [row[0] for row in connection.execute(
                "SELECT memory_id FROM event_sources WHERE event_id='E1' ORDER BY ord")]
        finally:
            connection.close()
        self.assertEqual(sources, ['m1'])

    def test_missing_memory_root_is_reported_not_treated_as_deletion(self):
        sync = self.shadow()
        sync.reconcile()
        memory = self.role / 'memory'
        memory.rename(self.role / 'memory-away')
        summary = sync.reconcile()
        self.assertTrue(summary['scan_incomplete'])
        connection = self.db()
        try:
            removed = connection.execute(
                "SELECT removed_at FROM memories WHERE id='m1'").fetchone()[0]
        finally:
            connection.close()
        self.assertIsNone(removed)
        report = (self.role / 'brain-sync-report.md').read_text(encoding='utf-8')
        self.assertIn('扫描不完整', report)

    def test_unreadable_file_does_not_look_like_a_deletion(self):
        sync = self.shadow()
        sync.reconcile()
        original = brain_sync._read_text

        def broken(path):
            if path.name.startswith('m1'):
                raise OSError('fixture unreadable')
            return original(path)

        brain_sync._read_text = broken
        try:
            summary = sync.reconcile()
        finally:
            brain_sync._read_text = original
        self.assertTrue(summary['scan_incomplete'])
        connection = self.db()
        try:
            removed = connection.execute(
                "SELECT removed_at FROM memories WHERE id='m1'").fetchone()[0]
        finally:
            connection.close()
        self.assertIsNone(removed)

    def test_vector_removed_from_source_is_deleted(self):
        sync = self.shadow()
        sync.reconcile()
        connection = sqlite3.connect(str(self.role / 'memory' / 'embeddings.db'))
        try:
            connection.execute("DELETE FROM embeddings WHERE bucket_id='m1'")
            connection.commit()
        finally:
            connection.close()
        summary = sync.reconcile()
        self.assertEqual(summary['comparison']['vector_mismatches'], [])
        connection = self.db()
        try:
            count = connection.execute("SELECT COUNT(*) FROM vectors WHERE memory_id='m1'"
                                       ).fetchone()[0]
        finally:
            connection.close()
        self.assertEqual(count, 0)

    def test_compare_detects_shadow_vector_missing_from_source(self):
        sync = self.shadow()
        sync.reconcile()
        connection = sqlite3.connect(str(self.role / 'memory' / 'embeddings.db'))
        try:
            connection.execute("DELETE FROM embeddings WHERE bucket_id='m1'")
            connection.commit()
        finally:
            connection.close()
        connection = sqlite3.connect(str(self.role / 'brain.db'))
        try:
            comparison = sync.compare(connection, sync._scan_source())
        finally:
            connection.close()
        self.assertIn('m1', comparison['vector_mismatches'])

    def test_missing_bucket_is_not_a_deletion(self):
        sync = self.shadow()
        sync.reconcile()
        (self.role / 'memory' / 'dynamic').rename(self.role / 'memory' / 'dynamic-away')
        summary = sync.reconcile()
        self.assertTrue(summary['scan_incomplete'])
        connection = self.db()
        try:
            removed = connection.execute(
                "SELECT removed_at FROM memories WHERE id='m1'").fetchone()[0]
        finally:
            connection.close()
        self.assertIsNone(removed)

    def test_sidecar_rewrite_rebuilds_folded_state(self):
        sync = self.shadow()
        sync.reconcile()
        write(self.role / 'memory-sidecar.jsonl', '')
        summary = sync.reconcile()
        self.assertEqual(summary['comparison']['state_mismatches'], [])
        connection = self.db()
        try:
            count = connection.execute(
                "SELECT COUNT(*) FROM sidecar_state WHERE memory_id='m1'").fetchone()[0]
        finally:
            connection.close()
        self.assertEqual(count, 0)

    def test_event_mismatch_cannot_report_no_unexpected(self):
        sync = self.shadow()
        sync.reconcile()
        connection = sqlite3.connect(str(self.role / 'brain.db'))
        try:
            connection.execute("DELETE FROM event_sources WHERE event_id='E1'")
            connection.commit()
            found = sync._scan_source()
            comparison = sync.compare(connection, found)
            references = sync._reference_check(
                connection, found, {FEEL: 'ticket 426'})
            summary = {'time': brain_sync.now(), 'changed': 0, 'source': len(found),
                       'known_removed': [FEEL], 'references': references,
                       'comparison': comparison, 'scan_incomplete': []}
            sync.write_report(connection, summary)
        finally:
            connection.close()
        report = (self.role / 'brain-sync-report.md').read_text(encoding='utf-8')
        self.assertIn('E1', report)
        self.assertNotIn('意外差异：无', report)

if __name__ == '__main__':
    unittest.main()
