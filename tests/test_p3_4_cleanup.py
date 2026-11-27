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
from veil.brain.storage import brain_events
from veil.brain.storage import brain_writer
from veil.brain.storage import veil_brain


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
            brain_events.ensure_schema(connection)
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
            connection.execute(
                'INSERT INTO versions(memory_id,time,operation_reason,why_remembered,fields_json,'
                'old_content,old_sha256) VALUES(?,?,?,?,?,?,?)',
                (self.normal['memory_id'], 'old', '', '', '[]', 'normal old version', 'x'))
            connection.execute(
                'INSERT INTO vectors(memory_id,dim,vector,vector_sha256,updated_at,content_hash) '
                'VALUES(?,?,?,?,?,?)', (self.normal['memory_id'], 1024, b'normal', 'x', 'now', 'x'))
            brain_events.upsert_event(connection, {'event_id': 'E-normal',
                                                   'memory_id': self.normal['memory_id'],
                                                   'story': 'normal story', 'title': 'Normal event'})
            brain_events.upsert_stage(connection, {'stage_id': 'S-normal', 'event_id': 'E-normal',
                                                   'text': 'normal stage'})
            connection.execute(
                'INSERT INTO event_ledger(ledger_seq,kind,source_file,event_id,memory_id,payload_json) '
                "VALUES(1,'stage-evidence','__live__','E-normal',?, '{\"text\":\"normal evidence\"}')",
                (self.normal['memory_id'],))
            brain_events.upsert_event(connection, {'event_id': 'E-tombstone',
                                                   'memory_id': self.deleted['memory_id'],
                                                   'story': 'deleted event story',
                                                   'title': 'Deleted event'})
            brain_events.upsert_stage(connection, {'stage_id': 'S-tombstone',
                                                   'event_id': 'E-tombstone',
                                                   'text': 'deleted event stage'})
            connection.execute(
                "INSERT INTO event_sources(event_id,ord,memory_id,part) VALUES('E-tombstone',0,?,'x')",
                (self.deleted['memory_id'],))
            connection.execute(
                'INSERT INTO event_ledger(ledger_seq,kind,source_file,event_id,memory_id,payload_json) '
                "VALUES(0,'stage-evidence','__live__',NULL,?, '{\"text\":\"deleted residue\"}')",
                (self.deleted['memory_id'],))
            connection.execute(
                'INSERT INTO delete_requests(request_id,memory_id,reason,status,raw_json) '
                "VALUES('r-closed',?,'old reason','approved','{}')", (self.deleted['memory_id'],))
            connection.execute(
                'INSERT INTO write_journal(time,operation,memory_id,detail_json,status) '
                "VALUES('now','state',?,'{\"old\":true}','saved')", (self.deleted['memory_id'],))
            connection.commit()
        finally:
            connection.close()
        for directory in ('dynamic', 'letters', 'plans', 'archive', '_agent', '_media'):
            (self.memory / directory).mkdir(parents=True, exist_ok=True)
        (self.memory / 'dynamic' / 'old.md').write_text('old vault', encoding='utf-8')
        (self.memory / 'embeddings.db').write_bytes(b'old embeddings')
        (self.memory / 'memory-versions.jsonl').write_text('{"old": true}\n', encoding='utf-8')
        (self.role_dir / 'events.jsonl').write_text('{"old": true}\n', encoding='utf-8')
        for active in ('chat.jsonl', 'tools.jsonl', 'context-log.jsonl',
                       'incoming-submissions.jsonl', 'provider-errors.jsonl'):
            (self.role_dir / active).write_text('{"live": true}\n', encoding='utf-8')
        (self.memory / '_agent' / 'state.jsonl').write_text('{}', encoding='utf-8')
        (self.memory / '_agent' / 'diagnostics.jsonl').write_text('{}', encoding='utf-8')
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
        files, dirs = [], ['payload/data', 'payload/data/peter']
        for path in sorted(self.role_dir.rglob('*')):
            rel = 'payload/' + path.relative_to(self.root).as_posix()
            if path.is_file():
                files.append({'path': rel, 'size': path.stat().st_size,
                               'sha256': p3_4_cleanup._sha_bytes(path.read_bytes())})
            elif path.is_dir():
                dirs.append(rel)
        self.patch = mock.patch.object(p3_4_cleanup.backup_restore, 'verify_backup',
                                       return_value={'files': files, 'directories': dirs})
        self.patch.start()
        self.addCleanup(self.patch.stop)

    def test_plan_lists_only_cleanup_targets_and_protects_live_state(self):
        plan = p3_4_cleanup._plan(self.root, 'peter', self.backup, self.archive)
        ids = {row['id'] for row in plan['tombstones']}
        self.assertIn(self.deleted['memory_id'], ids)
        self.assertEqual({row['id'] for row in plan['letters_plans']},
                         {self.letter['memory_id'], self.plan['memory_id']})
        self.assertEqual(plan['residuals']['event_ledger'], 1)
        self.assertEqual(plan['removed_event_ids'], ['E-tombstone'])
        self.assertEqual(plan['residuals']['delete_requests'], 1)
        self.assertGreaterEqual(plan['residuals']['write_journal'], 1)
        paths = {item['path'] for item in plan['file_targets']}
        self.assertIn('data/peter/memory/dynamic', paths)
        self.assertIn('data/peter/memory/embeddings.db', paths)
        self.assertIn('data/peter/memory/memory-versions.jsonl', paths)
        self.assertIn('data/peter/events.jsonl', paths)
        for active in ('chat.jsonl', 'tools.jsonl', 'context-log.jsonl',
                       'incoming-submissions.jsonl', 'provider-errors.jsonl'):
            self.assertNotIn('data/peter/' + active, paths)
        self.assertNotIn('data/peter/memory/_agent/state.jsonl', paths)
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
            self.assertEqual(connection.execute(
                'SELECT COUNT(*) FROM event_ledger WHERE memory_id=?',
                (self.deleted['memory_id'],)).fetchone()[0], 0)
            self.assertEqual(connection.execute(
                'SELECT COUNT(*) FROM delete_requests WHERE memory_id=?',
                (self.deleted['memory_id'],)).fetchone()[0], 0)
            self.assertEqual(connection.execute(
                'SELECT COUNT(*) FROM write_journal WHERE memory_id=?',
                (self.deleted['memory_id'],)).fetchone()[0], 0)
            self.assertEqual(connection.execute(
                'SELECT COUNT(*) FROM events WHERE event_id=?', ('E-normal',)).fetchone()[0], 1)
            self.assertEqual(connection.execute(
                'SELECT COUNT(*) FROM events WHERE event_id=?', ('E-tombstone',)).fetchone()[0], 0)
            self.assertEqual(connection.execute(
                'SELECT COUNT(*) FROM event_stages WHERE stage_id=?', ('S-tombstone',)).fetchone()[0], 0)
            self.assertEqual(connection.execute(
                'SELECT COUNT(*) FROM event_stages WHERE stage_id=?', ('S-normal',)).fetchone()[0], 1)
            self.assertEqual(connection.execute(
                'SELECT COUNT(*) FROM event_ledger WHERE memory_id=?',
                (self.normal['memory_id'],)).fetchone()[0], 1)
            self.assertEqual(connection.execute(
                'SELECT COUNT(*) FROM versions WHERE memory_id=?',
                (self.normal['memory_id'],)).fetchone()[0], 1)
            self.assertEqual(connection.execute(
                'SELECT COUNT(*) FROM vectors WHERE memory_id=?',
                (self.normal['memory_id'],)).fetchone()[0], 1)
        finally:
            connection.close()
        self.assertTrue((self.memory / '_agent' / 'state.jsonl').is_file())
        self.assertTrue((self.memory / '_media' / 'keep.bin').is_file())
        self.assertTrue((self.role_dir / 'archive' / 'pre-p3-2-event-ledgers-x' / 'manifest.json').is_file())
        self.assertFalse((self.memory / 'dynamic').exists())
        self.assertFalse((self.root / '.deps' / 'ombre').exists())
        self.assertTrue((self.archive / 'ombre-115d831.zip').is_file())
        archives = list(self.archive.glob('letters-plans-*.json'))
        self.assertTrue(archives)
        archived = json.loads(archives[0].read_text(encoding='utf-8'))
        by_id = {item['id']: item for item in archived}
        self.assertEqual(by_id[self.letter['memory_id']]['body'], 'letter body')
        self.assertEqual(by_id[self.letter['memory_id']]['type'], 'letter')
        # Tombstone bodies must not be resurrected into the letter/plan archive.
        self.assertNotIn(self.deleted['memory_id'], by_id)
        self.assertNotIn('deleted secret', json.dumps(by_id, ensure_ascii=False))
        self.assertFalse((self.memory / 'embeddings.db').exists())
        self.assertFalse((self.memory / 'memory-versions.jsonl').exists())
        self.assertFalse((self.role_dir / 'events.jsonl').exists())
        for active in ('chat.jsonl', 'tools.jsonl', 'context-log.jsonl',
                       'incoming-submissions.jsonl', 'provider-errors.jsonl'):
            self.assertTrue((self.role_dir / active).is_file())
        self.assertTrue((self.memory / '_agent' / 'state.jsonl').is_file())
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

    def test_plan_refuses_archive_backup_overlap_and_missing_coverage(self):
        with self.assertRaises(RuntimeError):
            p3_4_cleanup._plan(self.root, 'peter', self.backup, self.backup)
        (self.memory / 'dynamic' / 'late.md').write_text('late', encoding='utf-8')
        with self.assertRaises(RuntimeError):
            p3_4_cleanup._plan(self.root, 'peter', self.backup, self.archive)

    def test_apply_refuses_rows_added_after_plan(self):
        plan = p3_4_cleanup._plan(self.root, 'peter', self.backup, self.archive)
        plan_path = Path(self.temp.name) / 'plan.json'
        p3_4_cleanup._write_json(plan_path, plan)
        connection = sqlite3.connect(str(self.db))
        try:
            extra = brain_writer.create_memory(connection, {'body': 'late', 'title': 'Late'},
                                               embedder=None)
            connection.execute('UPDATE memories SET is_deleted=1 WHERE id=?', (extra['memory_id'],))
            connection.commit()
        finally:
            connection.close()
        with self.assertRaises(RuntimeError):
            p3_4_cleanup.apply_plan(self.root, 'peter', plan_path, backup=self.backup,
                                    archive_dir=self.archive, confirm=p3_4_cleanup.CONFIRM_WORD)
        connection = sqlite3.connect(str(self.db))
        try:
            self.assertEqual(connection.execute(
                'SELECT COUNT(*) FROM memories WHERE id=?', (extra['memory_id'],)).fetchone()[0], 1)
        finally:
            connection.close()

    def test_interrupted_after_db_commit_resumes_without_deleting_twice(self):
        plan = p3_4_cleanup._plan(self.root, 'peter', self.backup, self.archive)
        plan_path = Path(self.temp.name) / 'plan.json'
        p3_4_cleanup._write_json(plan_path, plan)
        original = p3_4_cleanup._finish_db
        with mock.patch.object(p3_4_cleanup, '_finish_db', side_effect=RuntimeError('fake disk full')):
            with self.assertRaises(RuntimeError):
                p3_4_cleanup.apply_plan(self.root, 'peter', plan_path, backup=self.backup,
                                        archive_dir=self.archive,
                                        confirm=p3_4_cleanup.CONFIRM_WORD)
        # Deletion committed, file cleanup not done; retry must finish, not re-delete.
        self.assertTrue((self.memory / 'dynamic').exists())
        result = p3_4_cleanup.apply_plan(self.root, 'peter', plan_path, backup=self.backup,
                                         archive_dir=self.archive,
                                         confirm=p3_4_cleanup.CONFIRM_WORD)
        self.assertFalse((self.memory / 'dynamic').exists())
        self.assertEqual(result['db']['tombstones_removed'], 1)

    def test_resume_rechecks_remaining_file_fingerprints(self):
        plan = p3_4_cleanup._plan(self.root, 'peter', self.backup, self.archive)
        record = next(item for item in plan['file_targets']
                      if item['path'] == 'data/peter/memory/dynamic')
        # A partially deleted directory may contain only approved files; that is resumable.
        (self.memory / 'dynamic' / 'old.md').unlink()
        p3_4_cleanup._validate_remaining_target(self.root, record)
        (self.memory / 'dynamic' / 'unapproved.md').write_text('new', encoding='utf-8')
        with self.assertRaises(RuntimeError):
            p3_4_cleanup._apply_files(self.root, plan, self.archive)

    def test_exclusive_plan_file_never_overwrites_user_file(self):
        path = Path(self.temp.name) / 'user.json'
        path.write_text('user data', encoding='utf-8')
        with self.assertRaises(FileExistsError):
            p3_4_cleanup._write_json(path, {'plan': True}, exclusive=True)
        self.assertEqual(path.read_text(encoding='utf-8'), 'user data')

    def test_external_symlink_target_is_not_listed_for_deletion(self):
        outside = Path(self.temp.name) / 'outside'
        outside.mkdir()
        (outside / 'keep.txt').write_text('keep', encoding='utf-8')
        with mock.patch.object(p3_4_cleanup, '_targets', return_value=[outside]):
            safe, external = p3_4_cleanup._safe_targets(self.root, 'peter')
        self.assertEqual(safe, [])
        self.assertEqual(len(external), 1)

    def test_plan_output_is_deterministic_across_target_order(self):
        resolved_root = self.root.resolve()
        first = p3_4_cleanup._plan(resolved_root, 'peter', self.backup, self.archive)
        original_targets = p3_4_cleanup._targets(resolved_root, 'peter')
        with mock.patch.object(p3_4_cleanup, '_targets', return_value=list(reversed(original_targets))):
            plan_rev = p3_4_cleanup._plan(resolved_root, 'peter', self.backup, self.archive)
        self.assertEqual(first['file_targets'], plan_rev['file_targets'])
        self.assertEqual(first['p6_before'], plan_rev['p6_before'])
        self.assertEqual(first['tombstones'], plan_rev['tombstones'])
        self.assertEqual(first['letters_plans'], plan_rev['letters_plans'])
        self.assertEqual(first['plan_hash'], plan_rev['plan_hash'])


if __name__ == '__main__':
    unittest.main()
