"""P3-2: event auxiliary ledgers live in brain.db and survive export/backup."""
import json
import sqlite3
import tempfile
import unittest
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
