"""Ticket 453 (post-merge hardening for #450): lock re-check and strict recovery times.

D1: EventMemory.record()/revise() check ``locked`` outside the bundle transaction, and
``BrainAuthority.commit_event_bundle`` only re-checks the seen projection inside it. A panel lock
committed in the window between that check and the bundle's write transaction must not let the
automatic story overwrite the locked body/title/vector. The stage is committed anyway, the reviewed
story is kept, and pending-review evidence is written (the existing "locked event got a new stage"
semantics). A retry of a pending stage must not overwrite a locked body and must stay retryable
after the unlock.

D2: scripts/p6_s2_recover_history.py used to drop a chat message whose timestamp cannot be parsed
while still committing the batch source_key; a rerun then returned already_recovered and the message
was lost forever. An unparseable (or missing) timestamp must refuse the whole batch before anything
is committed.
"""
import json
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / 'scripts'))

from event_memory import EventMemory
from memory_sidecar import Sidecar
from test_p6_s2_review_fixes import _brain_memory
from test_p6_s2_write_and_recovery import _init_db, _seed_event, _seed_memory


class LengthEmbedder:
    """A vector that depends on the body length, so a wrongly written body is visible in the index."""

    def embed(self, text):
        return [float(len(text) % 97) / 97.0] * 1024


class LockRecheckFixture(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.memory = _brain_memory(self.root)
        self.addCleanup(self.memory.__exit__, None, None, None)
        self.sidecar = Sidecar(self.memory.store, self.memory.cfg.id, self.memory.budget.clock)
        self.events = EventMemory(self.memory, settings={'enabled': True}, sidecar=self.sidecar)
        self.events.locked = self.sidecar.locked
        self.saved = self.events.record('we walked to the old harbour', 'Old harbour',
                                        at='2026-11-25T10:05:00+08:00')
        self.memory_id = self.saved['memory_id']
        self.event_id = self.saved['event_id']

    def _race(self):
        """Lock the memory after the caller's out-of-transaction check and before the bundle."""
        authority = self.memory.brain_authority
        original = authority.commit_event_bundle

        def racing(*args, **kwargs):
            self.sidecar.set_locked(self.memory_id, True)
            return original(*args, **kwargs)

        return patch.object(authority, 'commit_event_bundle', side_effect=racing)

    def _connection(self):
        connection = sqlite3.connect(str(self.memory.brain_authority.db_path), timeout=30)
        connection.row_factory = sqlite3.Row
        return connection

    def _scalar(self, sql, params=()):
        connection = self._connection()
        try:
            row = connection.execute(sql, params).fetchone()
            return row[0] if row is not None else None
        finally:
            connection.close()

    def _rows(self, sql, params=()):
        connection = self._connection()
        try:
            return connection.execute(sql, params).fetchall()
        finally:
            connection.close()


class TestLockedRecordRecheck(LockRecheckFixture):
    def test_a_lock_taken_before_the_bundle_never_overwrites_the_body(self):
        body_before = self.memory.content_for(self.memory_id, refresh=True)
        title_before = self.memory.title_for(self.memory_id)
        event_before = self.events.events()[self.event_id]
        versions_before = self._scalar('SELECT COUNT(*) FROM versions')
        vector_before = self._scalar('SELECT vector_sha256 FROM vectors WHERE memory_id=?',
                                     (self.memory_id,))
        # A different embedder makes a wrongly written candidate story visible in the vector row.
        self.memory._embedder = LengthEmbedder()
        with self._race():
            result = self.events.record('Later she showed me the ferry timetable', 'Old harbour',
                                        at='2026-11-25T11:05:00+08:00', event_id=self.event_id,
                                        source={'kind': 'chat', 'message_ids': ['m-ferry-1']})
        self.assertTrue(result.get('locked'), result)
        self.assertEqual(self.memory.content_for(self.memory_id, refresh=True), body_before,
                         'an automatic story must never overwrite a body locked before the bundle')
        self.assertEqual(self.memory.title_for(self.memory_id), title_before,
                         'a locked body keeps its panel title')
        self.assertEqual(self._scalar('SELECT COUNT(*) FROM versions'), versions_before,
                         'the refused overwrite must not record a version')
        self.assertEqual(self._scalar('SELECT vector_sha256 FROM vectors WHERE memory_id=?',
                                      (self.memory_id,)), vector_before,
                         'the refused overwrite must not re-index the body')
        event_after = self.events.events()[self.event_id]
        self.assertEqual(event_after.get('story'), event_before.get('story'),
                         'the reviewed story must be kept')
        self.assertTrue(event_after.get('pending_review'))
        self.assertTrue(event_after.get('dirty'))
        self.assertTrue(event_after.get('story_pending'))
        self.assertEqual(event_after.get('last_story_at'), event_before.get('last_story_at'))
        self.assertEqual(event_after.get('story_coverage'), event_before.get('story_coverage'))
        stages = self.events.stages(self.event_id)
        self.assertEqual(len(stages), 2, 'the new stage is committed even while the memory is locked')
        self.assertIn('ferry timetable', stages[-1]['text'])
        self.assertGreater(
            self._scalar("SELECT COUNT(*) FROM event_ledger WHERE kind='stage-evidence' "
                         'AND stage_id=?', (stages[-1]['stage_id'],)), 0,
            'the committed stage keeps its evidence')
        review_rows = self._rows("SELECT payload_json FROM event_ledger WHERE kind='event-review'")
        self.assertTrue(any('locked event got a new stage' in row[0] for row in review_rows),
                        'the locked overwrite refusal must leave review evidence')


class TestLockedReviseRecheck(LockRecheckFixture):
    def test_a_lock_taken_before_the_bundle_makes_revise_refuse(self):
        body_before = self.memory.content_for(self.memory_id, refresh=True)
        event_before = self.events.events()[self.event_id]
        with self._race():
            ok = self.events.revise(self.event_id,
                                    'we walked to the old harbour and saw the ferry timetable',
                                    'Old harbour', seen=body_before)
        self.assertFalse(ok)
        self.assertEqual(self.events.last_revise_status, 'locked')
        self.assertEqual(self.memory.content_for(self.memory_id, refresh=True), body_before)
        self.assertEqual(self.events.events()[self.event_id].get('story'),
                         event_before.get('story'))
        self.assertEqual(self._scalar("SELECT COUNT(*) FROM event_ledger "
                                      "WHERE kind='event-revision'"), 0,
                         'a refused revision must not leave a done revision row')


class TestLockedRetryPending(LockRecheckFixture):
    def _journal_pending_stage(self, event):
        stage = {
            'stage_id': 'st-pending-lock', 'event_id': self.event_id,
            'at': '2026-11-25T11:05:00+08:00', 'saved_at': '2026-11-25T11:05:00+08:00',
            'at_known': True, 'text': 'then we talked about the ferry timetable',
            'entities': [], 'facts': {'dates': [], 'numbers': [], 'files': []}, 'why': '',
        }
        self.memory.store.append(self.events.stages_path,
                                 {**stage, 'state': 'pending', 'event_row': event})
        return stage

    def test_a_locked_retry_stays_pending_and_commits_after_unlock(self):
        body_before = self.memory.content_for(self.memory_id, refresh=True)
        event_before = self.events.events()[self.event_id]
        self._journal_pending_stage(event_before)
        self.sidecar.set_locked(self.memory_id, True)
        self.assertEqual(self.events.retry_pending(), 0)
        self.assertEqual(len(self.events.pending_stages()), 1,
                         'a locked target must leave the stage retryable, not drop or commit it')
        self.assertEqual(self.memory.content_for(self.memory_id, refresh=True), body_before)
        self.assertEqual(self.events.events()[self.event_id].get('story'),
                         event_before.get('story'),
                         'a locked retry must not replace the reviewed event story')
        # D1: after the unlock the same journal row must still be committable.
        self.sidecar.set_locked(self.memory_id, False)
        self.assertEqual(self.events.retry_pending(), 1)
        self.assertEqual(len(self.events.pending_stages()), 0)
        self.assertEqual(self.events.stages(self.event_id)[-1]['text'],
                         'then we talked about the ferry timetable')
        self.events.retry_dirty()
        self.assertIn('ferry timetable', self.memory.content_for(self.memory_id, refresh=True),
                      'the unlocked retry must be able to project the recovered stage')


class ComposingModel:
    """A fake offline story model that returns every stage text it is given (no paid call)."""

    def __init__(self):
        self.calls = 0

    def __call__(self, messages, max_tokens=0, detail=False):
        self.calls += 1
        payload = {}
        for message in messages:
            if message.get('role') != 'user':
                continue
            try:
                payload = json.loads(message.get('content') or '')
            except (TypeError, ValueError):
                continue
            break
        stages = payload.get('stages') if isinstance(payload, dict) else None
        story = ' '.join(str(stage.get('text') or '') for stage in stages or [])
        return (story, 1, 1, 0, 'stop')


class TestLockedRetryDirty(LockRecheckFixture):
    def test_a_locked_retry_dirty_keeps_the_reviewed_story_and_pending_flags(self):
        event_before = self.events.events()[self.event_id]
        self.events.model = ComposingModel()
        self.sidecar.set_locked(self.memory_id, True)
        saved = self.events.record('Later she showed me the ferry timetable', 'Old harbour',
                                   at='2026-11-25T11:05:00+08:00', event_id=self.event_id)
        self.assertTrue(saved.get('locked'))
        reviewed = self.events.events()[self.event_id]
        self.assertEqual(reviewed.get('story'), event_before.get('story'))
        self.assertTrue(reviewed.get('pending_review'))
        self.assertTrue(reviewed.get('dirty'))
        # Ticket 453 review: the locked retry pass must not replace the reviewed story or clear the
        # pending flags; if it does, the new stage can never be projected after the unlock.
        self.events.retry_dirty()
        still_locked = self.events.events()[self.event_id]
        self.assertEqual(still_locked.get('story'), event_before.get('story'),
                         'a locked retry_dirty pass must keep the reviewed story')
        self.assertTrue(still_locked.get('dirty'),
                        'a locked retry_dirty pass must keep the event retryable')
        self.assertTrue(still_locked.get('story_pending'))
        self.assertTrue(still_locked.get('pending_review'))
        # After the unlock the reviewed pending story is rebuilt and projected once.
        self.sidecar.set_locked(self.memory_id, False)
        self.events.retry_dirty()
        unlocked = self.events.events()[self.event_id]
        self.assertIn('ferry timetable', unlocked.get('story') or '')
        self.assertFalse(unlocked.get('pending_review'))
        self.assertFalse(unlocked.get('dirty'))
        self.assertIn('ferry timetable', self.memory.content_for(self.memory_id, refresh=True))


class RecoveryTimestampFixture(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.role_dir = self.root / 'role'
        self.role_dir.mkdir(parents=True)
        self.db_path = self.role_dir / 'brain.db'
        connection = _init_db(self.db_path)
        _seed_memory(connection, 'mem-recover-001', 'Peter had a full day')
        _seed_event(connection, 'evt-recover-001', 'mem-recover-001', 'Full day story')
        connection.close()
        (self.role_dir / 'experience-memory.jsonl').write_text('', encoding='utf-8')

    def _chat(self, rows):
        (self.role_dir / 'chat.jsonl').write_text(
            '\n'.join(json.dumps(row, ensure_ascii=False) for row in rows) + '\n',
            encoding='utf-8')

    def _recover(self, dry_run=True):
        from p6_s2_recover_history import recover_slice
        return recover_slice(self.role_dir, self.db_path, memory_id='mem-recover-001',
                             from_time='2026-11-25T11:00:00+08:00',
                             to_time='2026-11-25T13:00:00+08:00', dry_run=dry_run)

    def _scalar(self, sql, params=()):
        connection = sqlite3.connect(str(self.db_path), timeout=30)
        try:
            row = connection.execute(sql, params).fetchone()
            return row[0] if row is not None else None
        finally:
            connection.close()

    def _markers(self):
        path = self.role_dir / 'experience-memory.jsonl'
        return [json.loads(line) for line in path.read_text(encoding='utf-8').split('\n')
                if line.strip()]


class TestRecoveryTimestampStrictness(RecoveryTimestampFixture):
    def test_unparseable_message_time_refuses_the_batch(self):
        good = {'message_id': 'm-ok', 'role': 'user',
                'time': '2026-11-25T12:00:00+08:00', 'content': 'the lost slice'}
        bad = {'message_id': 'm-bad', 'role': 'user',
               'time': 'not-a-timestamp', 'content': 'a message that cannot be placed'}
        self._chat([good, bad])
        with self.assertRaises(ValueError):
            self._recover(dry_run=True)
        with self.assertRaises(ValueError):
            self._recover(dry_run=False)
        self.assertEqual(self._scalar('SELECT COUNT(*) FROM event_stages'), 0,
                         'a refused batch must not commit any stage')
        self.assertEqual(self._scalar('SELECT COUNT(*) FROM experience_recoveries'), 0,
                         'a refused batch must not commit its source_key')
        self.assertEqual(self._markers(), [], 'a refused batch must not write the external marker')
        # D2: the batch is still recoverable once the timestamp is fixed; no message is lost.
        self._chat([good, {**bad, 'time': '2026-11-25T12:30:00+08:00'}])
        result = self._recover(dry_run=False)
        self.assertEqual(result['status'], 'recovered')
        self.assertEqual(result['message_count'], 2)
        body = self._scalar('SELECT body FROM memories WHERE id=?', ('mem-recover-001',))
        self.assertIn('the lost slice', body)
        self.assertIn('a message that cannot be placed', body)

    def test_message_without_a_timestamp_refuses_the_batch(self):
        self._chat([{'message_id': 'm-no-time', 'role': 'user', 'content': 'no timestamp here'}])
        with self.assertRaises(ValueError):
            self._recover(dry_run=True)
        self.assertEqual(self._scalar('SELECT COUNT(*) FROM event_stages'), 0)
        self.assertEqual(self._scalar('SELECT COUNT(*) FROM experience_recoveries'), 0)
        self.assertEqual(self._markers(), [])


if __name__ == '__main__':
    unittest.main()
