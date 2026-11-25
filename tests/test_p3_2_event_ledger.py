"""P3-2: event auxiliary ledgers live in brain.db and survive export/backup."""
import json
import sqlite3
import tempfile
import unittest
from unittest import mock
from pathlib import Path

import brain_backup
import brain_events
import brain_export
from brain_migrate import migrate_role
from brain_store import BrainStore
from brain_authority_init import initialize
from veil_agent import Store


class EventLedgerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = Store(self.temp.name + '/data')
        self.role_dir = self.store.root / 'fake'
        self.role_dir.mkdir(parents=True)
        self.db = self.role_dir / 'brain.db'
        initialize(self.db)
        (self.role_dir / 'stage-evidence.jsonl').write_text(
            json.dumps({'stage_id': 'S1', 'event_id': 'E1', 'kind': 'chat',
                        'text': 'proof'}, ensure_ascii=False) + '\n', encoding='utf-8')
        (self.role_dir / 'event-versions.jsonl').write_text(
            json.dumps({'event_id': 'E1', 'memory_id': 'M1', 'why': 'old story'}, ensure_ascii=False) + '\n',
            encoding='utf-8')
        (self.role_dir / 'event-ops.jsonl').write_text(
            json.dumps({'at': '2026-01-01', 'op': 'revise', 'event_id': 'E1'}, ensure_ascii=False) + '\n',
            encoding='utf-8')
        (self.role_dir / 'event-dream.json').write_text(
            json.dumps({'status': 'idle', 'last_outcome': 'succeeded'}, ensure_ascii=False), encoding='utf-8')

    def test_migration_moves_all_ledgers_into_brain_and_archives_files(self):
        result = migrate_role(self.store, 'fake')
        self.assertEqual(result['migrated_files'], 4)
        for name in ('stage-evidence.jsonl', 'event-versions.jsonl', 'event-ops.jsonl',
                     'event-dream.json'):
            self.assertFalse((self.role_dir / name).exists())
        archive = Path(result['archive'])
        self.assertTrue((archive / 'stage-evidence.jsonl').is_file())
        self.assertTrue((archive / 'manifest.json').is_file())
        self.assertEqual((archive / 'stage-evidence.jsonl').stat().st_mode & 0o444, 0o444)
        connection = sqlite3.connect(str(self.db))
        try:
            self.assertEqual(brain_events.ledger_count(connection, 'stage-evidence'), 1)
            self.assertEqual(brain_events.ledger_count(connection, 'event-version'), 1)
            self.assertEqual(brain_events.ledger_count(connection, 'event-op'), 1)
            self.assertEqual(brain_events.read_state(connection, 'event-dream')['last_outcome'],
                             'succeeded')
        finally:
            connection.close()
        # Idempotent: a second start has nothing left to move.
        self.assertEqual(migrate_role(self.store, 'fake')['migrated_files'], 0)

    def test_brain_store_round_trips_event_ledgers(self):
        store = BrainStore(self.store, 'fake', self.db)
        store.append('fake/stage-evidence.jsonl',
                     {'stage_id': 'S2', 'event_id': 'E2', 'kind': 'tool', 'text': 'later'})
        store.append('fake/event-review.jsonl', {'event_id': 'E2', 'why': 'reviewed'})
        self.assertEqual(store.read('fake/stage-evidence.jsonl')[0]['text'], 'later')
        self.assertEqual(store.read_tail('fake/event-review.jsonl', 1)[0]['why'], 'reviewed')
        store.replace('fake/event-dream.json', {'status': 'running'})
        self.assertEqual(store.read_state('fake/event-dream.json')['status'], 'running')

    def test_export_and_backup_include_the_event_lifecycle(self):
        migrate_role(self.store, 'fake')
        out = Path(self.temp.name) / 'export'
        manifest = brain_export.export_vault(self.db, out)
        self.assertEqual(manifest['event_ledger']['stage-evidence'], 1)
        self.assertTrue((out / 'stage-evidence.jsonl').is_file())
        self.assertTrue((out / 'event-dream.json').is_file())
        backup_path = Path(self.temp.name) / 'backup.db'
        saved = brain_backup.backup(self.db, backup_path)
        self.assertEqual(saved['counts']['event_ledger'], 3)
        self.assertEqual(saved['quick_check'], 'ok')
        restored = brain_backup.restore(backup_path, Path(self.temp.name) / 'restored.db')
        self.assertEqual(restored['counts']['event_ledger'], 3)
        self.assertEqual(restored['digest'], saved['digest'])


if __name__ == '__main__':
    unittest.main()


class StartupMigrationTests(unittest.TestCase):
    def test_memory_startup_migrates_legacy_event_files(self):
        from memory_agent import Memory
        from veil_agent import Budget, Character
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        store = Store(temp.name + '/data')
        role_dir = store.root / 'peter'
        role_dir.mkdir(parents=True)
        db = role_dir / 'brain.db'
        initialize(db)
        (role_dir / 'stage-evidence.jsonl').write_text(
            json.dumps({'stage_id': 'S1', 'event_id': 'E1', 'kind': 'chat', 'text': 'proof'}) + '\n',
            encoding='utf-8')
        provider = {'model': 'fake', 'max_input_tokens': 20000, 'input_cny_per_million': 1,
                    'output_cny_per_million': 2, 'cached_input_cny_per_million': 1}
        cfg = Character('peter', 'Peter', 'persona', 20, 1024, provider, 'peter',
                        {'model': 'fake-embedding', 'input_cny_per_million': '0',
                         'mode': 'native', 'events': {'enabled': False}})
        memory = Memory(cfg, store, Budget(store), object())
        self.addCleanup(memory.__exit__, None, None, None)
        self.assertFalse((role_dir / 'stage-evidence.jsonl').exists())
        connection = sqlite3.connect(str(db))
        try:
            self.assertEqual(brain_events.ledger_count(connection, 'stage-evidence'), 1)
        finally:
            connection.close()


class LosslessMigrationTests(unittest.TestCase):
    LEDGERS = (
        ('stage-evidence.jsonl', {'stage_id': 'S1', 'event_id': 'E1', 'text': 'proof'}),
        ('event-versions.jsonl', {'event_id': 'E1', 'memory_id': 'M1', 'why': 'v1'}),
        ('event-review.jsonl', {'event_id': 'E1', 'why': 'review'}),
        ('event-ops.jsonl', {'event_id': 'E1', 'op': 'revise'}),
        ('event-projections.jsonl', {'event_id': 'E1', 'memory_id': 'M1'}),
        ('event-revisions.jsonl', {'event_id': 'E1', 'state': 'done'}),
        ('event-dream-report.jsonl', {'event_id': 'E1', 'outcome': 'succeeded'}),
    )

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = Store(self.temp.name + '/data')
        self.role_dir = self.store.root / 'fake'
        self.role_dir.mkdir(parents=True)
        self.db = self.role_dir / 'brain.db'
        initialize(self.db)
        self.expected = {}
        for name, row in self.LEDGERS:
            # A legal duplicate row plus a distinct row: order and duplicates both matter.
            rows = [row, dict(row), {**row, 'text': 'different'}]
            self.expected[name] = rows
            (self.role_dir / name).write_text(
                ''.join(json.dumps(item, ensure_ascii=False) + '\n' for item in rows),
                encoding='utf-8')
        self.expected['event-dream.json'] = {'status': 'idle', 'last_outcome': 'succeeded'}
        (self.role_dir / 'event-dream.json').write_text(
            json.dumps(self.expected['event-dream.json'], ensure_ascii=False), encoding='utf-8')

    def _rows(self, kind):
        connection = sqlite3.connect(str(self.db))
        try:
            return brain_events.read_ledger(connection, kind)
        finally:
            connection.close()

    def test_all_ledgers_keep_order_duplicates_and_state(self):
        result = migrate_role(self.store, 'fake')
        self.assertEqual(result['migrated_files'], 8)
        for name, kind in brain_events.EVENT_LEDGER_FILES.items():
            rows = self._rows(kind)
            self.assertEqual(rows, [json.loads(json.dumps(row)) for row in self.expected[name]],
                             name)
        connection = sqlite3.connect(str(self.db))
        try:
            self.assertEqual(brain_events.read_state(connection, 'event-dream'),
                             self.expected['event-dream.json'])
            self.assertEqual(connection.execute(
                'SELECT COUNT(*) FROM event_ledger_imports').fetchone()[0], 8)
        finally:
            connection.close()
        self.assertEqual(migrate_role(self.store, 'fake')['migrated_files'], 0)
        self.assertEqual(self._rows('stage-evidence'), self.expected['stage-evidence.jsonl'])

    def test_interrupted_transaction_rolls_back_and_retries_cleanly(self):
        original = brain_events.append_ledger
        calls = {'n': 0}

        def interrupt(*args, **kwargs):
            calls['n'] += 1
            if calls['n'] == 2:
                raise RuntimeError('fictional crash before commit')
            return original(*args, **kwargs)

        with mock.patch.object(brain_events, 'append_ledger', side_effect=interrupt):
            with self.assertRaises(RuntimeError):
                migrate_role(self.store, 'fake')
        connection = sqlite3.connect(str(self.db))
        try:
            self.assertEqual(connection.execute(
                'SELECT COUNT(*) FROM event_ledger').fetchone()[0], 0)
            self.assertEqual(connection.execute(
                'SELECT COUNT(*) FROM event_ledger_imports').fetchone()[0], 0)
        finally:
            connection.close()
        self.assertTrue((self.role_dir / 'stage-evidence.jsonl').exists())
        migrate_role(self.store, 'fake')
        self.assertEqual(self._rows('stage-evidence'), self.expected['stage-evidence.jsonl'])

    def test_retry_after_database_commit_but_before_archive(self):
        with mock.patch.object(Path, 'unlink', side_effect=OSError('fictional unlink failure')):
            with self.assertRaises(OSError):
                migrate_role(self.store, 'fake')
        self.assertTrue((self.role_dir / 'stage-evidence.jsonl').exists())
        migrate_role(self.store, 'fake')
        self.assertFalse((self.role_dir / 'stage-evidence.jsonl').exists())
        self.assertEqual(self._rows('stage-evidence'), self.expected['stage-evidence.jsonl'])


class BackupSafetyTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.db = self.root / 'brain.db'
        initialize(self.db)
        import brain_writer
        connection = sqlite3.connect(str(self.db))
        try:
            brain_writer.create_memory(connection, {'body': 'old'}, embedder=lambda _: [0.1] * 1024)
        finally:
            connection.close()
        self.backup = self.root / 'backup.db'
        brain_backup.backup(self.db, self.backup)

    def _add_new(self):
        import brain_writer
        connection = sqlite3.connect(str(self.db))
        try:
            brain_writer.create_memory(connection, {'body': 'new after backup'},
                                       embedder=lambda _: [0.1] * 1024)
        finally:
            connection.close()

    def test_digest_is_recomputed_and_corrupt_backup_is_rejected(self):
        bad = self.root / 'bad.db'
        bad.write_bytes(self.backup.read_bytes())
        connection = sqlite3.connect(str(bad))
        try:
            connection.execute("UPDATE meta SET value='not-the-digest' WHERE key='digest'")
            connection.commit()
        finally:
            connection.close()
        before = self.db.read_bytes()
        with self.assertRaises(ValueError):
            brain_backup.restore(bad, self.db)
        self.assertEqual(self.db.read_bytes(), before)

    def test_restore_preserves_pre_restore_database_with_new_rows(self):
        self._add_new()
        # A post-backup event and its lifecycle row must survive the rollback too.
        connection = sqlite3.connect(str(self.db))
        try:
            brain_events.upsert_event(connection, {'event_id': 'E-after', 'memory_id': 'M-after',
                                                   'story': 'after backup', 'title': 'After'})
            brain_events.append_ledger(connection, 'stage-evidence',
                                       {'event_id': 'E-after', 'stage_id': 'S-after',
                                        'text': 'after proof'})
            connection.commit()
        finally:
            connection.close()
        result = brain_backup.restore(self.backup, self.db)
        self.assertTrue(result['safety_preserved'])
        safety = Path(result['pre_restore'])
        self.assertTrue(safety.is_file())
        current = sqlite3.connect(str(self.db))
        saved = sqlite3.connect(str(safety))
        try:
            # The old backup is restored, but post-backup rows are merged back into the live db.
            self.assertEqual(current.execute('SELECT COUNT(*) FROM memories').fetchone()[0], 2)
            self.assertEqual(current.execute("SELECT COUNT(*) FROM events WHERE event_id='E-after'").fetchone()[0], 1)
            self.assertGreaterEqual(current.execute(
                "SELECT COUNT(*) FROM event_ledger WHERE event_id='E-after'").fetchone()[0], 1)
            self.assertEqual(saved.execute('SELECT COUNT(*) FROM memories').fetchone()[0], 2)
        finally:
            current.close(); saved.close()

    def test_wal_is_refused(self):
        target = self.root / 'target.db'
        target.write_bytes(self.backup.read_bytes())
        target.with_name(target.name + '-wal').write_bytes(b'not checkpointed')
        with self.assertRaises(ValueError):
            brain_backup.restore(self.backup, target)

    def test_failed_atomic_replace_leaves_target_and_safety(self):
        self._add_new()
        before = self.db.read_bytes()
        with mock.patch.object(brain_backup.os, 'replace', side_effect=OSError('fictional')):
            with self.assertRaises(OSError):
                brain_backup.restore(self.backup, self.db)
        self.assertEqual(self.db.read_bytes(), before)
        self.assertTrue(list(self.root.glob('brain.db.pre-restore-*')))
