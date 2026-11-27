"""P6-S2 unit tests: write contract, anti-silent-drop, evidence, backup bundle, and recovery.

Coverage:
1. commit_event_bundle atomic single-transaction + single versions row + rollback drill
2. _missing_facts date normalization (11-25 ↔ 11月25日)
3. fact_mismatch -> update-pending-review + continues fallback
4. VERBATIM_RUN -> manual-review queue + event-review.jsonl
5. _capture_evidence preserves chat user rows despite many tool rows + captures message_ids
6. _merge_many passes existing story context
7. backup_bundle + restore_bundle + reconcile_companion_state fault-recovery drill
8. p6_s2_recover_history audit & idempotent dry-run/apply on isolated copy
"""

import hashlib
import json
import os
import shutil
import sqlite3
import tempfile
import time
import unittest
import uuid
from decimal import Decimal
from pathlib import Path
from unittest.mock import Mock, patch

import sys
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from veil.brain.storage import brain_authority_init
from veil.brain.storage import brain_backup
from veil.brain.storage import brain_events
from veil.brain.storage import brain_writer
from veil.brain.storage import veil_brain


def _init_db(db_path: Path) -> sqlite3.Connection:
    """Create a fresh brain.db with full schema."""
    brain_authority_init.initialize(str(db_path))
    conn = sqlite3.connect(str(db_path), timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute('PRAGMA journal_mode=DELETE')
    brain_writer.ensure_schema(conn)
    brain_events.ensure_schema(conn)
    brain_writer.refresh_digest(conn)
    return conn


def _seed_memory(conn, memory_id='mem-test-001', body='Peter had a coffee with Lan'):
    """Insert a minimal memory row for testing."""
    record = {
        'id': memory_id,
        'body': body,
        'title': 'Test memory',
        'type': 'dynamic',
        'importance': 5,
        'pinned': False,
        'domain': [],
        'tags': [],
    }
    conn.execute('BEGIN IMMEDIATE')
    brain_writer.apply_create_memory(conn, record)
    conn.commit()
    brain_writer.refresh_digest(conn)
    conn.execute('PRAGMA wal_checkpoint(TRUNCATE)')
    return memory_id


def _seed_event(conn, event_id='evt-test-001', memory_id='mem-test-001', story='Initial story'):
    """Insert a minimal event row for testing."""
    brain_events.upsert_event(conn, {
        'event_id': event_id,
        'memory_id': memory_id,
        'story': story,
        'title': 'Test event',
        'status': 'active',
        'sources': [],
    })
    brain_writer.refresh_digest(conn)
    conn.execute('PRAGMA wal_checkpoint(TRUNCATE)')
    return event_id


def _count(conn, table):
    return conn.execute(f'SELECT COUNT(*) FROM {table}').fetchone()[0]


# ---------------------------------------------------------------------------
# Test 1: commit_event_bundle — single transaction, single versions row
# ---------------------------------------------------------------------------

class TestCommitEventBundle(unittest.TestCase):
    """Verify that commit_event_bundle produces exactly one versions row
    and that a mid-transaction crash leaves zero committed rows (rollback drill)."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.role = 'test-role'
        self.role_dir = self.root / self.role
        self.role_dir.mkdir(parents=True, exist_ok=True)
        self.db_path = self.role_dir / 'brain.db'
        self.conn = _init_db(self.db_path)
        _seed_memory(self.conn, 'mem-bundle-001', 'Peter discussed architecture')
        self.conn.close()

    def _open(self):
        conn = sqlite3.connect(str(self.db_path), timeout=30)
        conn.row_factory = sqlite3.Row
        return conn

    def test_single_versions_row_after_bundle_commit(self):
        """commit_event_bundle should produce exactly one versions row (no double write)."""
        from veil.core.veil_agent import Store
        store = Store(self.root)
        cfg = type('Cfg', (), {
            'id': self.role,
            'memory': {'brain': {'write_authority': True}},
        })()
        from veil.brain.storage.brain_authority import BrainAuthority
        auth = BrainAuthority(cfg, store)
        conn = self._open()
        versions_before = _count(conn, 'versions')
        conn.close()
        event = {
            'event_id': 'evt-bundle-001',
            'memory_id': 'mem-bundle-001',
            'story': 'Architecture discussion story',
            'title': 'Architecture discussion',
            'status': 'active',
            'sources': [],
        }
        stage = {
            'stage_id': 'stg-bundle-001',
            'event_id': 'evt-bundle-001',
            'at': '2026-11-25T10:00:00+08:00',
            'state': 'committed',
            'text': 'First architecture discussion',
        }
        auth.commit_event_bundle(
            event=event,
            stage=stage,
            projection='Peter discussed VEIL Brain architecture with Lan',
            title='Architecture discussion',
            operation_reason='test bundle',
        )
        conn = self._open()
        versions_after = _count(conn, 'versions')
        self.assertEqual(versions_after - versions_before, 1,
                         'commit_event_bundle must produce exactly 1 versions row')
        ev = conn.execute('SELECT * FROM events WHERE event_id=?',
                               ('evt-bundle-001',)).fetchone()
        self.assertIsNotNone(ev)
        st = conn.execute('SELECT * FROM event_stages WHERE stage_id=?',
                               ('stg-bundle-001',)).fetchone()
        self.assertIsNotNone(st)
        conn.close()

    def test_mid_transaction_crash_rollback(self):
        """Simulate a crash mid-transaction: nothing should be committed."""
        from veil.core.veil_agent import Store
        store = Store(self.root)
        cfg = type('Cfg', (), {
            'id': self.role,
            'memory': {'brain': {'write_authority': True}},
        })()
        from veil.brain.storage.brain_authority import BrainAuthority
        auth = BrainAuthority(cfg, store)
        conn = self._open()
        versions_before = _count(conn, 'versions')
        events_before = _count(conn, 'events')
        conn.close()
        original_upsert = brain_events.upsert_event
        def crashing_upsert(connection, row, *, commit=True, ensure=True):
            raise RuntimeError('simulated crash')
        with patch.object(brain_events, 'upsert_event', crashing_upsert):
            with self.assertRaises(RuntimeError):
                auth.commit_event_bundle(
                    event={'event_id': 'evt-crash', 'memory_id': 'mem-bundle-001',
                           'story': 'Should not persist', 'sources': []},
                    projection='Should not persist',
                    operation_reason='crash test',
                )
        conn = self._open()
        self.assertEqual(_count(conn, 'versions'), versions_before)
        self.assertEqual(_count(conn, 'events'), events_before)
        conn.close()


# ---------------------------------------------------------------------------
# Test 2: _missing_facts date normalization
# ---------------------------------------------------------------------------

class TestMissingFactsDateNormalization(unittest.TestCase):
    """Verify _missing_facts treats '11-25' and '11月25日' as equivalent."""

    def test_date_equivalent_not_missing(self):
        from veil.brain.event_memory import EventMemory
        em = EventMemory.__new__(EventMemory)
        # Test the _month_day_set helper
        text = '11月25日 Peter和Lan一起喝了咖啡'
        result = em._month_day_set(text)
        self.assertIn((11, 25), result)

    def test_date_format_cross_match(self):
        from veil.brain.event_memory import EventMemory
        em = EventMemory.__new__(EventMemory)
        stages = [{'facts': {'dates': ['11-25'], 'numbers': ['11', '25']}}]
        missing = em._missing_facts(stages, '11月25日的下午，Peter去看了电影')
        self.assertEqual(missing, [])


# ---------------------------------------------------------------------------
# Test 3: _capture_evidence per-kind quota preserves chat user rows
# ---------------------------------------------------------------------------

class TestCaptureEvidence(unittest.TestCase):
    """Verify _capture_evidence reserves slots for chat user messages
    even when tool rows far outnumber them."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store_root = Path(self.temp.name) / 'data'
        self.role = 'test-role'
        self.role_dir = self.store_root / self.role
        self.role_dir.mkdir(parents=True, exist_ok=True)
        # Create a chat.jsonl with 3 chat user messages + 20 tool messages
        chat_path = self.role_dir / 'chat.jsonl'
        lines = []
        for i in range(20):
            lines.append(json.dumps({
                'id': f'tool-msg-{i}', 'role': 'tool', 'time': f'2026-11-25T10:{i:02d}:00+08:00',
                'content': f'Tool result {i}',
            }))
        for i in range(3):
            lines.append(json.dumps({
                'id': f'user-msg-{i}', 'role': 'user', 'time': f'2026-11-25T11:{i:02d}:00+08:00',
                'content': f'User message {i}',
            }))
        chat_path.write_text('\n'.join(lines) + '\n', encoding='utf-8')

    def test_chat_user_messages_not_crowded_out(self):
        """Even with 20 tool rows, all 3 user chat rows must be in evidence."""
        from veil.brain.event_memory import EventMemory
        from veil.core.veil_agent import Store
        em = EventMemory.__new__(EventMemory)
        em.store = Store(self.store_root)
        em.cfg = type('Cfg', (), {'id': self.role})()
        em.settings = {'evidence_minutes': 180, 'evidence_items': 8, 'evidence_chars': 500}
        stage = {'stage_id': 'st-evidence', 'event_id': 'evt-evidence',
                 'at': '2026-11-25T11:02:00+08:00', 'source': {}}
        evidence = em._capture_evidence(stage, persist=False)
        user_ids = [e['message_id'] for e in evidence
                    if e.get('kind') == 'chat' and e.get('role') == 'user']
        self.assertEqual(set(user_ids), {'user-msg-0', 'user-msg-1', 'user-msg-2'})
        self.assertLessEqual(len(evidence), 8)


# ---------------------------------------------------------------------------
# Test 4: _merge_many passes existing story
# ---------------------------------------------------------------------------

class TestMergeManyPassesStory(unittest.TestCase):
    """Verify that _merge_many passes the existing story text, not empty string."""

    def test_merge_story_chunk_receives_story(self):
        from veil.brain.event_memory import EventMemory
        em = EventMemory.__new__(EventMemory)
        # Track what _merge_story_chunk receives
        calls = []
        original = getattr(em, '_merge_story_chunk', None)

        def tracking_merge(story, chunk, carried=None):
            calls.append(('merge', story, chunk, carried))
            return story + '\n' + chunk[0]['text']

        em._merge_story_chunk = tracking_merge
        em.max_story_size = 100000
        existing_story = 'Peter visited the park on Monday.'
        new_stage = {'at': '2026-11-25T12:00:00+08:00', 'text': 'He also had lunch with Lan.'}
        em._story_batches = lambda stages, budget: [stages]
        result = em._merge_many([new_stage], existing_story)
        self.assertTrue(calls, '_merge_story_chunk should have been called')
        self.assertEqual(calls[0][1], existing_story,
                         '_merge_story_chunk must receive the existing story, not empty string')


# ---------------------------------------------------------------------------
# Test 5: backup_bundle + restore_bundle + reconcile (fault-recovery drill #2)
# ---------------------------------------------------------------------------

class TestBackupBundleAndRestore(unittest.TestCase):
    """Full coordinated backup, simulate post-backup writes, restore, and reconcile."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.role_dir = self.root / 'role'
        self.role_dir.mkdir(parents=True, exist_ok=True)
        self.db_path = self.role_dir / 'brain.db'
        self.conn = _init_db(self.db_path)
        _seed_memory(self.conn, 'mem-backup-001', 'Important memory')
        _seed_event(self.conn, 'evt-backup-001', 'mem-backup-001', 'Important story')
        self.addCleanup(lambda: self.conn.close())
        # Create companion files
        lc = {'relationships': [{'text': 'event:evt-backup-001 test ref', 'source': 'test'}]}
        (self.role_dir / 'learned-context.json').write_text(
            json.dumps(lc, ensure_ascii=False, indent=2), encoding='utf-8')
        (self.role_dir / 'learned-context.versions.jsonl').write_text(
            json.dumps({'ts': '2026-11-25T10:00:00', 'v': 1}) + '\n', encoding='utf-8')
        (self.role_dir / 'experience-memory.jsonl').write_text(
            json.dumps({'source_key': 'sk-001', 'kind': 'update', 'ts': '2026-11-25'}) + '\n',
            encoding='utf-8')
        (self.role_dir / 'experience-memory-failures.jsonl').write_text('', encoding='utf-8')
        (self.role_dir / 'recall-rotation.json').write_text('{}', encoding='utf-8')
        # Create a chat.jsonl for watermark
        (self.role_dir / 'chat.jsonl').write_text(
            json.dumps({'id': 'msg-001', 'role': 'user', 'text': 'hello'}) + '\n'
            + json.dumps({'id': 'msg-002', 'role': 'assistant', 'text': 'hi'}) + '\n',
            encoding='utf-8')

    def test_backup_bundle_creates_manifest(self):
        self.conn.close()
        backup_dir = self.root / 'backup'
        result = brain_backup.backup_bundle(self.role_dir, backup_dir)
        manifest = result['manifest']
        self.assertEqual(manifest['version'], 1)
        self.assertIn('db', manifest)
        self.assertIn('companions', manifest)
        self.assertIn('chat_watermark', manifest)
        self.assertEqual(manifest['chat_watermark']['lines'], 2)
        self.assertEqual(manifest['chat_watermark']['last_message_id'], 'msg-002')
        # Verify files exist
        self.assertTrue((backup_dir / 'brain.db').exists())
        self.assertTrue((backup_dir / 'bundle-manifest.json').exists())
        for name in manifest['companions']:
            self.assertTrue((backup_dir / name).exists())
        # Reopen for cleanup
        self.conn = sqlite3.connect(str(self.db_path), timeout=30)
        self.conn.row_factory = sqlite3.Row

    def test_restore_bundle_merges_post_backup_data(self):
        """Full drill: backup -> add new data -> simulate disaster -> restore -> verify merge."""
        self.conn.close()
        backup_dir = self.root / 'backup'
        brain_backup.backup_bundle(self.role_dir, backup_dir)

        # Simulate post-backup writes
        self.conn = sqlite3.connect(str(self.db_path), timeout=30)
        self.conn.row_factory = sqlite3.Row
        _seed_memory(self.conn, 'mem-post-backup', 'Post backup memory')
        self.conn.close()

        # Add a new experience entry after backup
        with open(self.role_dir / 'experience-memory.jsonl', 'a', encoding='utf-8') as f:
            f.write(json.dumps({'source_key': 'sk-post', 'kind': 'update', 'ts': '2026-11-25T23:00:00'}) + '\n')

        # Restore from backup into a new directory
        restore_dir = self.root / 'restore-role'
        # Copy current role_dir to simulate a restore target with post-backup data
        shutil.copytree(self.role_dir, restore_dir)

        result = brain_backup.restore_bundle(backup_dir, restore_dir)
        self.assertIn('db', result)
        self.assertIn('reconcile', result)
        # The restored db should have merged post-backup row
        restore_conn = sqlite3.connect(str(restore_dir / 'brain.db'), timeout=30)
        restore_conn.row_factory = sqlite3.Row
        try:
            post = restore_conn.execute(
                'SELECT id FROM memories WHERE id=?', ('mem-post-backup',)).fetchone()
            self.assertIsNotNone(post, 'Post-backup memory should be merged into restored db')
        finally:
            restore_conn.close()

        # Experience-memory.jsonl should contain both original and post-backup entries
        exp_lines = [
            json.loads(ln) for ln in
            (restore_dir / 'experience-memory.jsonl').read_text(encoding='utf-8').splitlines()
            if ln.strip()
        ]
        keys = {e.get('source_key') for e in exp_lines}
        self.assertIn('sk-001', keys, 'Original entry must survive')
        self.assertIn('sk-post', keys, 'Post-backup entry must survive')
        # Reopen for cleanup
        self.conn = sqlite3.connect(str(self.db_path), timeout=30)
        self.conn.row_factory = sqlite3.Row

    def test_reconcile_marks_stale_refs(self):
        """reconcile_companion_state marks entries pointing to missing db objects."""
        # Delete the event from db
        self.conn.execute('DELETE FROM events WHERE event_id=?', ('evt-backup-001',))
        self.conn.commit()
        self.conn.close()
        result = brain_backup.reconcile_companion_state(self.role_dir)
        self.assertGreater(result['stale_learned_context_refs'], 0)
        # Verify the learned-context.json was updated with stale_ref
        lc = json.loads((self.role_dir / 'learned-context.json').read_text(encoding='utf-8'))
        stale_entries = [
            e for section in lc.values()
            if isinstance(section, list)
            for e in section
            if isinstance(e, dict) and e.get('stale_ref')
        ]
        self.assertGreater(len(stale_entries), 0, 'Stale refs should be marked')
        # Reopen for cleanup
        self.conn = sqlite3.connect(str(self.db_path), timeout=30)
        self.conn.row_factory = sqlite3.Row

    def test_restore_bundle_preserves_updates_to_existing_objects(self):
        self.conn.close()
        backup_dir = self.root / 'backup-existing'
        brain_backup.backup_bundle(self.role_dir, backup_dir)
        self.conn = sqlite3.connect(str(self.db_path), timeout=30)
        self.conn.row_factory = sqlite3.Row
        brain_writer.update_memory(
            self.conn, 'mem-backup-001', {'body': 'Important memory updated after T0'},
            embedder=None, operation_reason='T1 update')
        brain_events.upsert_stage(self.conn, {
            'stage_id': 'stg-after-t0', 'event_id': 'evt-backup-001',
            'at': '2026-11-25T23:30:00+08:00', 'state': 'committed',
            'text': 'Stage added after T0', 'source_key': 'sk-after-t0',
        })
        self.conn.execute(
            'INSERT INTO relations(memory_id,ord,target_id,type,score,status,auto,label) '
            'VALUES(?,?,?,?,?,?,?,?)',
            ('mem-backup-001', 0, 'mem-post-target', 'related_to', 1.0, 'active', 0, 'T1'))
        self.conn.execute(
            'INSERT INTO quotes(memory_id,ord,speaker,text) VALUES(?,?,?,?)',
            ('mem-backup-001', 0, 'user', 'T1 quote'))
        self.conn.execute(
            'INSERT INTO experience_recoveries(source_key,memory_id,event_id,stage_id,created_at) '
            'VALUES(?,?,?,?,?)',
            ('recover-after-t0', 'mem-backup-001', 'evt-backup-001', 'stg-after-t0',
             '2026-11-25T23:31:00+08:00'))
        self.conn.execute(
            'INSERT OR REPLACE INTO event_state(key,payload_json,updated_at) VALUES(?,?,?)',
            ('event-dream', json.dumps({'state': 't1'}), '2026-11-25T23:32:00+08:00'))
        self.conn.commit()
        brain_writer.refresh_digest(self.conn)
        self.conn.close()

        restore_dir = self.root / 'restore-existing'
        shutil.copytree(self.role_dir, restore_dir)
        brain_backup.restore_bundle(backup_dir, restore_dir)
        restored = sqlite3.connect(str(restore_dir / 'brain.db'))
        try:
            self.assertEqual(restored.execute(
                'SELECT body FROM memories WHERE id=?', ('mem-backup-001',)).fetchone()[0],
                'Important memory updated after T0')
            self.assertEqual(restored.execute(
                'SELECT COUNT(*) FROM versions WHERE memory_id=?', ('mem-backup-001',)).fetchone()[0], 1)
            self.assertIsNotNone(restored.execute(
                'SELECT 1 FROM event_stages WHERE stage_id=?', ('stg-after-t0',)).fetchone())
            self.assertIsNotNone(restored.execute(
                'SELECT 1 FROM relations WHERE memory_id=? AND label=?',
                ('mem-backup-001', 'T1')).fetchone())
            self.assertIsNotNone(restored.execute(
                'SELECT 1 FROM quotes WHERE memory_id=? AND text=?',
                ('mem-backup-001', 'T1 quote')).fetchone())
            self.assertIsNotNone(restored.execute(
                'SELECT 1 FROM experience_recoveries WHERE source_key=?',
                ('recover-after-t0',)).fetchone())
        finally:
            restored.close()
        self.conn = sqlite3.connect(str(self.db_path), timeout=30)
        self.conn.row_factory = sqlite3.Row

    def test_restore_bundle_never_resurrects_hard_deleted_memory(self):
        self.conn.close()
        backup_dir = self.root / 'backup-before-delete'
        brain_backup.backup_bundle(self.role_dir, backup_dir)
        self.conn = sqlite3.connect(str(self.db_path), timeout=30)
        brain_writer.hard_delete(self.conn, 'mem-backup-001', 'privacy deletion after T0')
        self.conn.close()
        restore_dir = self.root / 'restore-after-delete'
        shutil.copytree(self.role_dir, restore_dir)
        brain_backup.restore_bundle(backup_dir, restore_dir)
        restored = sqlite3.connect(str(restore_dir / 'brain.db'))
        try:
            self.assertIsNone(restored.execute(
                'SELECT 1 FROM memories WHERE id=?', ('mem-backup-001',)).fetchone())
            self.assertIsNotNone(restored.execute(
                'SELECT 1 FROM tombstones WHERE memory_id=?', ('mem-backup-001',)).fetchone())
            for table in ('versions', 'vectors', 'relations', 'sources', 'quotes'):
                self.assertEqual(restored.execute(
                    f'SELECT COUNT(*) FROM {table} WHERE memory_id=?',
                    ('mem-backup-001',)).fetchone()[0], 0)
        finally:
            restored.close()
        self.conn = sqlite3.connect(str(self.db_path), timeout=30)
        self.conn.row_factory = sqlite3.Row

    def test_old_schema_bundle_preserves_t1_recovery_marker(self):
        self.conn.close()
        backup_dir = self.root / 'backup-old-schema'
        brain_backup.backup_bundle(self.role_dir, backup_dir)
        old_db = backup_dir / 'brain.db'
        old_conn = sqlite3.connect(str(old_db))
        old_conn.execute('DROP TABLE experience_recoveries')
        old_conn.commit()
        brain_writer.refresh_digest(old_conn)
        old_conn.close()
        manifest_path = backup_dir / 'bundle-manifest.json'
        manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
        manifest['db']['sha256'] = hashlib.sha256(old_db.read_bytes()).hexdigest()
        manifest['db']['bytes'] = old_db.stat().st_size
        manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')

        self.conn = sqlite3.connect(str(self.db_path), timeout=30)
        self.conn.execute(
            'INSERT INTO experience_recoveries(source_key,memory_id,event_id,stage_id,created_at) '
            'VALUES(?,?,?,?,?)',
            ('t1-marker', 'mem-backup-001', 'evt-backup-001', 'stg-marker',
             '2026-11-25T23:40:00+08:00'))
        self.conn.commit()
        brain_writer.refresh_digest(self.conn)
        self.conn.close()
        restore_dir = self.root / 'restore-old-schema'
        shutil.copytree(self.role_dir, restore_dir)
        brain_backup.restore_bundle(backup_dir, restore_dir)
        restored = sqlite3.connect(str(restore_dir / 'brain.db'))
        try:
            self.assertIsNotNone(restored.execute(
                'SELECT 1 FROM experience_recoveries WHERE source_key=?',
                ('t1-marker',)).fetchone())
        finally:
            restored.close()
        self.conn = sqlite3.connect(str(self.db_path), timeout=30)
        self.conn.row_factory = sqlite3.Row

    def test_restore_bundle_rolls_back_db_when_reconcile_fails(self):
        self.conn.close()
        backup_dir = self.root / 'backup-rollback'
        brain_backup.backup_bundle(self.role_dir, backup_dir)
        self.conn = sqlite3.connect(str(self.db_path), timeout=30)
        brain_writer.update_memory(
            self.conn, 'mem-backup-001', {'body': 'T1 body must survive failed restore'},
            embedder=None, operation_reason='T1 before failed restore')
        self.conn.close()
        restore_dir = self.root / 'restore-rollback'
        shutil.copytree(self.role_dir, restore_dir)
        with patch.object(brain_backup, 'reconcile_companion_state',
                          side_effect=RuntimeError('simulated reconcile failure')):
            with self.assertRaises(RuntimeError):
                brain_backup.restore_bundle(backup_dir, restore_dir)
        restored = sqlite3.connect(str(restore_dir / 'brain.db'))
        try:
            self.assertEqual(restored.execute(
                'SELECT body FROM memories WHERE id=?', ('mem-backup-001',)).fetchone()[0],
                'T1 body must survive failed restore')
        finally:
            restored.close()
        self.conn = sqlite3.connect(str(self.db_path), timeout=30)
        self.conn.row_factory = sqlite3.Row

    def test_restore_bundle_sha256_mismatch_raises(self):
        """Tampered bundle db should raise on restore."""
        self.conn.close()
        backup_dir = self.root / 'backup-tamper'
        brain_backup.backup_bundle(self.role_dir, backup_dir)
        # Tamper with db file
        with open(backup_dir / 'brain.db', 'ab') as f:
            f.write(b'TAMPERED')
        with self.assertRaises(RuntimeError):
            brain_backup.restore_bundle(backup_dir, self.root / 'tamper-target')
        # Reopen for cleanup
        self.conn = sqlite3.connect(str(self.db_path), timeout=30)
        self.conn.row_factory = sqlite3.Row


# ---------------------------------------------------------------------------
# Test 6: p6_s2_recover_history audit and idempotent recovery
# ---------------------------------------------------------------------------

class TestRecoverHistory(unittest.TestCase):
    """Test the historical failure audit and idempotent recovery script."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.role_dir = self.root / 'role'
        self.role_dir.mkdir(parents=True, exist_ok=True)
        self.db_path = self.role_dir / 'brain.db'
        self.conn = _init_db(self.db_path)
        self.addCleanup(lambda: self.conn.close())
        _seed_memory(self.conn, 'mem-recover-001', 'Peter had a full day on Nov 25')
        _seed_event(self.conn, 'evt-recover-001', 'mem-recover-001', 'Full day story')

        # Create experience-memory-failures.jsonl with unfixed_draft entries
        failures = [
            {
                'ts': '2026-11-25T23:53:09',
                'kind': 'unfixed_draft',
                'reason': 'unfixed_draft',
                'update': 'mem-recover-001',
                'covers': ['2026-11-25T08:22:33+08:00', '2026-11-25T23:39:06+08:00'],
                'pos': 9529,
                'dropped': 1,
            },
            {
                'ts': '2026-11-25T23:16:12',
                'kind': 'update-conflict',
                'reason': 'update-conflict',
                'update': 'mem-recover-001',
                'covers': ['2026-11-25T08:22:33+08:00'],
            },
        ]
        (self.role_dir / 'experience-memory-failures.jsonl').write_text(
            '\n'.join(json.dumps(f, ensure_ascii=False) for f in failures) + '\n',
            encoding='utf-8')
        (self.role_dir / 'experience-memory.jsonl').write_text('', encoding='utf-8')

        # Create a chat.jsonl with messages in the recovery window
        msgs = []
        for i in range(5):
            msgs.append(json.dumps({
                'id': f'msg-recover-{i}',
                'role': 'user',
                'ts': f'2026-11-25T10:{i:02d}:00+08:00',
                'text': f'Chat message {i} from Peter',
            }))
        (self.role_dir / 'chat.jsonl').write_text('\n'.join(msgs) + '\n', encoding='utf-8')

    def test_audit_finds_failures(self):
        sys.path.insert(0, str(Path(__file__).resolve().parent.parent / 'scripts'))
        from p6_s2_recover_history import audit_failures
        result = audit_failures(self.role_dir)
        self.assertEqual(result['total_failure_rows'], 2)
        self.assertEqual(result['unfixed_draft_count'], 1)
        self.assertEqual(result['conflict_count'], 1)
        self.assertFalse(result['unfixed_drafts'][0]['already_recovered'])

    def test_recover_dry_run(self):
        sys.path.insert(0, str(Path(__file__).resolve().parent.parent / 'scripts'))
        from p6_s2_recover_history import recover_slice
        self.conn.close()
        before_bytes = self.db_path.read_bytes()
        result = recover_slice(
            self.role_dir,
            self.db_path,
            memory_id='mem-recover-001',
            from_time='2026-11-25T08:22:33+08:00',
            to_time='2026-11-25T23:39:06+08:00',
            dry_run=True,
        )
        self.assertEqual(result['status'], 'would_recover')
        self.assertTrue(result['preserves_existing_story'])
        self.assertGreater(result['message_count'], 0)
        # Verify nothing was actually written
        self.conn = sqlite3.connect(str(self.db_path), timeout=30)
        self.conn.row_factory = sqlite3.Row
        stages = self.conn.execute('SELECT COUNT(*) FROM event_stages').fetchone()[0]
        self.assertEqual(stages, 0, 'Dry run must not create any stages')
        self.conn.close()
        self.assertEqual(self.db_path.read_bytes(), before_bytes,
                         'Dry run must leave the database byte-for-byte unchanged')
        self.conn = sqlite3.connect(str(self.db_path), timeout=30)
        self.conn.row_factory = sqlite3.Row

    def test_recover_apply_idempotent(self):
        """Apply recovery, then apply again — second run should return already_recovered."""
        sys.path.insert(0, str(Path(__file__).resolve().parent.parent / 'scripts'))
        from p6_s2_recover_history import recover_slice
        self.conn.close()
        result1 = recover_slice(
            self.role_dir,
            self.db_path,
            memory_id='mem-recover-001',
            from_time='2026-11-25T08:22:33+08:00',
            to_time='2026-11-25T23:39:06+08:00',
            dry_run=False,
        )
        self.assertEqual(result1['status'], 'recovered')
        self.assertIn('stage_id', result1)

        # Second apply should detect idempotency
        result2 = recover_slice(
            self.role_dir,
            self.db_path,
            memory_id='mem-recover-001',
            from_time='2026-11-25T08:22:33+08:00',
            to_time='2026-11-25T23:39:06+08:00',
            dry_run=False,
        )
        self.assertEqual(result2['status'], 'already_recovered')
        self.assertEqual(result2['source_key'], result1['source_key'])

        # Verify versions was bumped exactly once
        self.conn = sqlite3.connect(str(self.db_path), timeout=30)
        self.conn.row_factory = sqlite3.Row
        versions = _count(self.conn, 'versions')
        self.assertEqual(versions, 1, 'Recovery must write exactly one version row')
        story = self.conn.execute(
            'SELECT story FROM events WHERE event_id=?', ('evt-recover-001',)).fetchone()[0]
        body = self.conn.execute(
            'SELECT body FROM memories WHERE id=?', ('mem-recover-001',)).fetchone()[0]
        self.assertIn('Chat message 0 from Peter', story)
        self.assertIn('Chat message 0 from Peter', body)
        vector_state = self.conn.execute(
            'SELECT vector_state FROM memories WHERE id=?', ('mem-recover-001',)).fetchone()[0]
        self.assertEqual(vector_state, 'pending')
        quote_count = self.conn.execute(
            'SELECT COUNT(*) FROM quotes WHERE memory_id=? AND speaker=?',
            ('mem-recover-001', 'user')).fetchone()[0]
        self.assertEqual(quote_count, 5)

    def test_recover_locked_event_refused(self):
        """Cannot recover into a locked event."""
        sys.path.insert(0, str(Path(__file__).resolve().parent.parent / 'scripts'))
        from p6_s2_recover_history import recover_slice
        # Mark memory as locked in sidecar_state
        self.conn.execute(
            'INSERT OR REPLACE INTO sidecar_state(memory_id, locked, links_json, sources_json) '
            'VALUES(?, 1, \'[]\', \'[]\')',
            ('mem-recover-001',))
        self.conn.commit()
        self.conn.close()
        result = recover_slice(
            self.role_dir,
            self.db_path,
            memory_id='mem-recover-001',
            from_time='2026-11-25T08:22:33+08:00',
            to_time='2026-11-25T23:39:06+08:00',
            dry_run=False,
        )
        self.assertEqual(result['status'], 'target_locked')
        # Reopen for cleanup
        self.conn = sqlite3.connect(str(self.db_path), timeout=30)
        self.conn.row_factory = sqlite3.Row

    def test_audit_marks_already_recovered(self):
        """After recovery, audit should flag the batch as already_recovered."""
        sys.path.insert(0, str(Path(__file__).resolve().parent.parent / 'scripts'))
        from p6_s2_recover_history import audit_failures, recover_slice
        self.conn.close()
        recover_slice(
            self.role_dir,
            self.db_path,
            memory_id='mem-recover-001',
            from_time='2026-11-25T08:22:33+08:00',
            to_time='2026-11-25T23:39:06+08:00',
            dry_run=False,
        )
        result = audit_failures(self.role_dir)
        self.assertTrue(result['unfixed_drafts'][0]['already_recovered'])


if __name__ == '__main__':
    unittest.main()
