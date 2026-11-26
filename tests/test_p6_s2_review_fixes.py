"""P6-S2 review fixes: one regression test per substantive CodeRabbit finding.

Covered:
- dates: a known year participates in matching, a yearless date still matches, an independent
  quantity is not exempted by a date, a longer number is not read as a date
- accumulated story: each accepted merge must keep the facts already accepted
- revise(): the body it was based on is re-checked inside the bundle transaction
- a locked event never exposes the unconfirmed candidate body through the readable cache
- evidence never includes something that happened after the stage
- a pending stage is recovered together with its evidence and quotes
- creating an empty experience_recoveries table cannot make an old database look corrupt
- the vector retry never indexes a rewritten body and never fails a committed bundle
- a fact-mismatch draft is never reported as an applied deferred update or appended as a stage
- bundle restore validates paths and hashes, keeps U+2028 records intact, merges by record
  identity, preserves the current rotation state, clears repaired stale refs and rolls a failed
  child-table copy back
- history recovery: equal boundaries share one key (old keys still recognised), a committed key is
  honoured before requiring the chat again, the window compares instants, and message identity is
  the one the producers write
- a title-changing hold archives the complete previous Markdown so the old title is restorable
"""
import hashlib
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import sys
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / 'scripts'))

import brain_authority
import brain_backup
import brain_events
import brain_writer
import veil_brain
from brain_authority import BrainAuthority, ProjectionConflict
from event_memory import EventMemory, facts
from experience_memory import ExperienceMemory
from memory_agent import Memory
from memory_native import NativeMemoryTools
from veil_agent import Budget, Character, Store
from test_p6_s2_write_and_recovery import _init_db, _seed_event, _seed_memory


def _facts_missing(stage_text, story):
    return EventMemory._missing_facts([{'text': stage_text, 'facts': facts(stage_text)}], story)


class FakeEmbedder:
    usage_tokens = None

    def embed(self, _text):
        return [0.5] * 1024


def _brain_memory(root: Path, role='fake'):
    """A real Memory attached to a fresh Brain database (the production write path)."""
    store = Store(root / 'data')
    db = store.root / role / 'brain.db'
    db.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(str(db))
    connection.executescript(veil_brain.SCHEMA)
    brain_writer.ensure_schema(connection)
    connection.close()
    provider = {'model': 'fake', 'max_input_tokens': 20000, 'input_cny_per_million': 1,
                'output_cny_per_million': 2, 'cached_input_cny_per_million': 1}
    settings = {'model': 'fake-embedding', 'input_cny_per_million': '0', 'mode': 'native',
                'base_url': 'https://example.invalid/v1',
                'brain': {'write_authority': True, 'shadow': False},
                'retrieval': {'backend': 'brain'}}
    cfg = Character(role, 'Example', 'persona', 20, 1024, provider, 'fake', settings)
    backend = SimpleNamespace(list_tools=lambda: [], call=lambda name, args: '')
    memory = Memory(cfg, store, Budget(store), Mock(), backend)
    memory._embedder = FakeEmbedder()
    return memory


class TestDateAndNumberFacts(unittest.TestCase):
    def test_known_year_must_match(self):
        self.assertEqual(_facts_missing('2026-11-25', '2025-11-25 we went out'), ['2026-11-25'])

    def test_year_survives_a_format_change(self):
        self.assertEqual(_facts_missing('2026-11-25', 'we went out on 2026-11-25'), [])
        self.assertEqual(_facts_missing('2026-11-25', 'we went out on 11-25'), ['2026'])

    def test_yearless_forms_stay_equivalent(self):
        self.assertEqual(_facts_missing('11-25', '11月25日的下午去了电影院'), [])

    def test_independent_quantity_is_not_exempted_by_a_date(self):
        stage = 'On 11-25 we ordered 25 tickets'
        self.assertEqual(_facts_missing(stage, '11-25 order placed'), ['25'])
        self.assertEqual(_facts_missing(stage, '11-25 order placed, 25 tickets'), [])

    def test_long_number_is_not_read_as_a_date(self):
        self.assertEqual(EventMemory._month_day_set('id 11-250'), set())
        self.assertEqual(_facts_missing('11-25', 'id 11-250'), ['11-25'])


class TestAccumulatedStory(unittest.TestCase):
    def test_each_merge_keeps_earlier_facts(self):
        from event_memory import EventMemory as EM
        em = EM.__new__(EM)
        em.settings = {'story_batch_chars': 12000}
        em.cfg = SimpleNamespace(persona='')
        replies = iter([
            # batch 1: keeps its own number
            'Monday: 42 people came.',
            # batch 2: keeps its own number but drops the earlier 42
            'Tuesday: 73 people came.',
            # retry after the fact hint: keeps both
            'Monday: 42 people came. Tuesday: 73 people came.',
        ])
        seen_hints = []

        def call_model(messages, max_tokens):
            if any('Keep every one of these exactly' in str(m.get('content')) for m in messages):
                seen_hints.append(messages[-1]['content'])
            return next(replies), None

        em._call_story_model = call_model
        em._story_tokens = lambda chunk: 1200
        stages = [
            {'at': '2026-11-25T10:00:00+08:00', 'text': 'Monday: 42 people came.',
             'facts': facts('Monday: 42 people came.')},
            {'at': '2026-11-25T11:00:00+08:00', 'text': 'Tuesday: 73 people came.',
             'facts': facts('Tuesday: 73 people came.')},
        ]
        em._story_batches = lambda rows, budget=None: [[stages[0]], [stages[1]]]
        story, pending, _through = em._merge_many(stages, '')
        self.assertFalse(pending, 'the retry that keeps both numbers must be accepted')
        self.assertIn('42', story)
        self.assertIn('73', story)
        self.assertTrue(seen_hints, 'the dropped earlier fact must be demanded back')
        self.assertIn('42', seen_hints[0])


class TestEventWriteGuards(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.memory = _brain_memory(self.root)
        self.addCleanup(self.memory.__exit__, None, None, None)
        self.events = EventMemory(self.memory, settings={'enabled': True})

    def _one_event(self):
        saved = self.events.record('we walked to the old harbour', 'Old harbour',
                                   at='2026-11-25T10:05:00+08:00')
        self.assertTrue(saved['event_id'])
        return saved

    def test_revise_reports_a_conflict_when_the_body_changed(self):
        saved = self._one_event()
        stale = 'a stale body nobody stored'
        with patch.object(self.memory, 'content_for', lambda *a, **k: stale):
            ok = self.events.revise(saved['event_id'], 'a totally different story', 'Old harbour',
                                    seen=stale)
        self.assertFalse(ok)
        self.assertEqual(self.events.last_revise_status, 'conflict')
        connection = sqlite3.connect(str(self.memory.brain_authority.db_path))
        try:
            body = connection.execute('SELECT body FROM memories WHERE id=?',
                                      (saved['memory_id'],)).fetchone()[0]
        finally:
            connection.close()
        self.assertNotIn('totally different story', body)

    def test_bundle_conflict_is_reported_as_a_version_conflict(self):
        saved = self._one_event()
        memory_id = saved['memory_id']
        connection = sqlite3.connect(str(self.memory.brain_authority.db_path))
        try:
            connection.execute("UPDATE memories SET body='Lan saved something else' WHERE id=?",
                               (memory_id,))
            connection.commit()
        finally:
            connection.close()
        with patch.object(self.memory, 'content_for', lambda *a, **k: 'stale read'):
            result = self.events.record('a later stage on the same day', 'Old harbour',
                                        at='2026-11-25T11:05:00+08:00',
                                        event_id=saved['event_id'])
        self.assertTrue(result.get('version_conflict'))
        self.assertFalse(result.get('changed'))

    def test_locked_event_never_caches_the_candidate_body(self):
        saved = self._one_event()
        committed = self.memory.content_for(saved['memory_id'], refresh=True)
        self.events.locked = lambda _memory_id: True
        outcome = self.events.record('Lan locked this story and then Peter added a chapter',
                                     'Old harbour', at='2026-11-25T12:05:00+08:00',
                                     event_id=saved['event_id'])
        self.assertTrue(outcome['locked'])
        cached = self.memory.bucket_details_cache.get(saved['memory_id'])
        if cached is not None:
            self.assertEqual(cached[1], committed,
                             'the candidate story must not become the readable body before review')
        self.assertEqual(self.memory.content_for(saved['memory_id'], refresh=True), committed)


class TestEvidenceTimeBoundary(unittest.TestCase):
    def test_nothing_after_the_stage_is_evidence(self):
        temp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(temp.cleanup)
        store = Store(Path(temp.name) / 'data')
        role = 'fake'
        (store.root / role).mkdir(parents=True, exist_ok=True)
        rows = [
            {'id': 'before', 'role': 'user', 'time': '2026-11-25T09:50:00+08:00',
             'content': 'earlier message'},
            {'id': 'after', 'role': 'user', 'time': '2026-11-25T10:20:00+08:00',
             'content': 'this happens after the stage'},
        ]
        (store.root / role / 'chat.jsonl').write_text(
            '\n'.join(json.dumps(row, ensure_ascii=False) for row in rows) + '\n', encoding='utf-8')
        em = EventMemory.__new__(EventMemory)
        em.store = store
        em.cfg = SimpleNamespace(id=role)
        em.settings = {'evidence_minutes': 120, 'evidence_items': 8, 'evidence_chars': 500}
        stage = {'stage_id': 'st-b', 'event_id': 'evt-b', 'at': '2026-11-25T10:00:00+08:00',
                 'source': {}}
        evidence = em._capture_evidence(stage, persist=False)
        ids = {item.get('message_id') for item in evidence}
        self.assertIn('before', ids)
        self.assertNotIn('after', ids)

    def test_explicit_message_ids_are_still_resolved_after_the_stage(self):
        temp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(temp.cleanup)
        store = Store(Path(temp.name) / 'data')
        role = 'fake'
        (store.root / role).mkdir(parents=True, exist_ok=True)
        (store.root / role / 'chat.jsonl').write_text(json.dumps(
            {'message_id': 'late', 'role': 'user', 'time': '2026-11-25T23:00:00+08:00',
             'content': 'said hours later'}) + '\n', encoding='utf-8')
        em = EventMemory.__new__(EventMemory)
        em.store = store
        em.cfg = SimpleNamespace(id=role)
        stage = {'stage_id': 'st-c', 'event_id': 'evt-c', 'at': '2026-11-25T10:00:00+08:00',
                 'source': {'message_ids': ['late']}}
        quotes = em._explicit_user_quotes(stage)
        self.assertEqual([quote['message_id'] for quote in quotes], ['late'])


class TestRetryPendingRecovery(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.memory = _brain_memory(self.root)
        self.addCleanup(self.memory.__exit__, None, None, None)
        self.events = EventMemory(self.memory, settings={'enabled': True})
        self.saved = self.events.record('we walked to the old harbour', 'Old harbour',
                                        at='2026-11-25T10:05:00+08:00')
        (self.memory.store.root / 'fake' / 'chat.jsonl').write_text(json.dumps(
            {'message_id': 'msg-1', 'role': 'user', 'time': '2026-11-25T10:04:00+08:00',
             'content': 'the ferry bell sounded blue'}) + '\n', encoding='utf-8')

    def test_pending_stage_is_recovered_with_evidence_and_quotes(self):
        event = self.events.events()[self.saved['event_id']]
        stage = {
            'stage_id': 'st-pending-1', 'event_id': self.saved['event_id'],
            'at': '2026-11-25T10:06:00+08:00', 'saved_at': '2026-11-25T10:06:00+08:00',
            'at_known': True, 'text': 'then we talked about the ferry',
            'entities': [], 'facts': facts('then we talked about the ferry'), 'why': '',
            'source': {'kind': 'chat', 'message_ids': ['msg-1']},
        }
        self.memory.store.append(self.events.stages_path,
                                 {**stage, 'state': 'pending', 'event_row': event})
        fixed = self.events.retry_pending()
        self.assertEqual(fixed, 1)
        connection = sqlite3.connect(str(self.memory.brain_authority.db_path))
        connection.row_factory = sqlite3.Row
        try:
            committed = connection.execute(
                "SELECT state FROM event_stages WHERE stage_id=?", ('st-pending-1',)).fetchone()
            self.assertEqual(committed['state'], 'committed')
            evidence = connection.execute(
                "SELECT COUNT(*) FROM event_ledger WHERE kind='stage-evidence' AND stage_id=?",
                ('st-pending-1',)).fetchone()[0]
            self.assertGreater(evidence, 0, 'the recovered stage must carry its evidence')
            quotes = connection.execute(
                'SELECT text FROM quotes WHERE memory_id=?', (self.saved['memory_id'],)).fetchall()
            self.assertTrue(any('ferry' in row[0] for row in quotes))
        finally:
            connection.close()


class TestSchemaDigest(unittest.TestCase):
    def test_new_empty_table_does_not_look_like_corruption(self):
        temp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(temp.cleanup)
        root = Path(temp.name)
        db_path = root / 'test-role' / 'brain.db'
        db_path.parent.mkdir(parents=True)
        connection = _init_db(db_path)
        _seed_memory(connection, 'mem-digest-001', 'a kept memory')
        connection.execute('DROP TABLE experience_recoveries')
        connection.commit()
        connection.close()
        self.assertFalse(veil_brain.check_db(db_path)['digest_ok'],
                         'precondition: the stored digest still covers the dropped table')

        connection = sqlite3.connect(str(db_path), timeout=30)
        brain_writer.ensure_schema(connection)
        # A business transaction that rolls back must leave a valid digest: only the empty table
        # was created, and no memory data changed.
        connection.execute('BEGIN IMMEDIATE')
        connection.execute("INSERT INTO events(event_id, story) VALUES('evt-rollback','x')")
        connection.rollback()
        connection.close()
        result = veil_brain.check_db(db_path)
        self.assertTrue(result['digest_ok'], 'creating an empty table must not fake corruption')


class TestVectorRetryGuards(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.memory = _brain_memory(self.root)
        self.addCleanup(self.memory.__exit__, None, None, None)
        self.db_path = self.memory.brain_authority.db_path
        self.connection = sqlite3.connect(str(self.db_path), timeout=30)
        self.connection.row_factory = sqlite3.Row
        brain_writer.create_memory(self.connection, {'id': 'mem-vec-1', 'body': 'old body'},
                                   embedder=lambda _text: [0.5] * 1024)
        self.connection.execute("UPDATE memories SET vector_state='pending' WHERE id='mem-vec-1'")
        self.connection.commit()

    def test_retry_never_indexes_a_rewritten_body(self):
        def embedder(_body):
            other = sqlite3.connect(str(self.db_path), timeout=30)
            other.execute("UPDATE memories SET body='newer body', vector_state='pending' "
                          "WHERE id='mem-vec-1'")
            other.commit()
            other.close()
            return [0.1] * 1024

        brain_writer.retry_pending_vectors(self.connection, embedder)
        row = self.connection.execute(
            "SELECT body, vector_state FROM memories WHERE id='mem-vec-1'").fetchone()
        self.assertEqual(row['body'], 'newer body')
        self.assertEqual(row['vector_state'], 'pending',
                         'a vector embedded from the old body must not mark the newer body ready')

    def test_index_retry_failure_does_not_fail_a_committed_bundle(self):
        authority = self.memory.brain_authority
        real_connect = authority._connect
        calls = {'count': 0}

        def flaky_connect():
            calls['count'] += 1
            if calls['count'] > 1:
                raise sqlite3.OperationalError('cannot open database')
            return real_connect()

        with patch.object(authority, '_connect', flaky_connect):
            result = authority.commit_event_bundle(
                event={'event_id': 'evt-retry-1', 'memory_id': 'mem-vec-1',
                       'story': 'a story', 'status': 'active', 'sources': []},
                projection='a story',
                title='Retry',
                embedder=lambda _text: [0.5] * 1024,
                operation_reason='retry failure test')
        self.assertEqual(result['memory_id'], 'mem-vec-1')
        row = self.connection.execute(
            "SELECT story FROM events WHERE event_id='evt-retry-1'").fetchone()
        self.assertIsNotNone(row, 'the bundle must stay committed when only the index retry failed')


class TestExperienceDeferredStatus(unittest.TestCase):
    def _deferred_runner(self, saved_value):
        em = ExperienceMemory.__new__(ExperienceMemory)
        em.path = 'fake/experience-memory.jsonl'
        written = []
        em.store = SimpleNamespace(append=lambda path, row: written.append(row))
        em.budget = SimpleNamespace(clock=lambda: '2026-11-25T12:00:00+08:00')
        em.sidecar = Mock()
        em.sidecar.locked.return_value = False
        em.memory = Mock()
        em.memory.content_for.return_value = 'the committed text'
        rows = [{'kind': 'deferred-update', 'memory_id': 'mem-1', 'text': 'a newer draft',
                 'seen': 'the committed text', 'deferred_key': 'key-1'}]
        em._events = lambda: list(rows)
        em._alive = lambda _memory_id: True
        em._save_update = lambda *a, **k: saved_value
        em._record_failure = lambda *a, **k: None
        return em, written

    def test_fact_mismatch_is_not_reported_as_applied(self):
        em, written = self._deferred_runner('fact_mismatch')
        applied = em._apply_deferred()
        self.assertEqual(applied, 0)
        kinds = [row.get('kind') for row in written]
        self.assertNotIn('deferred-applied', kinds)
        self.assertIn('deferred-conflict', kinds)
        drafts = [row for row in written if row.get('kind') == 'update-pending-review']
        self.assertEqual(len(drafts), 1)
        self.assertEqual(drafts[0]['draft'], 'a newer draft')

    def test_still_locked_is_left_for_a_later_run(self):
        em, written = self._deferred_runner('locked')
        applied = em._apply_deferred()
        self.assertEqual(applied, 0)
        self.assertFalse([row for row in written
                          if row.get('kind') in ('deferred-applied', 'deferred-conflict')],
                         'a locked key must stay unfinished, not be recorded as a conflict')


class TestBackupBundleGuards(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.role_dir = self.root / 'role'
        self.role_dir.mkdir(parents=True)
        self.db_path = self.role_dir / 'brain.db'
        connection = _init_db(self.db_path)
        _seed_memory(connection, 'mem-backup-001', 'Important memory')
        _seed_event(connection, 'evt-backup-001', 'mem-backup-001', 'Important story')
        self.conn = connection
        self.addCleanup(lambda: connection.close())

    def _bundle(self):
        out = self.root / 'bundle'
        brain_backup.backup_bundle(self.role_dir, out)
        return out

    def _rewrite_manifest(self, bundle, mutate):
        path = bundle / 'bundle-manifest.json'
        manifest = json.loads(path.read_text(encoding='utf-8'))
        mutate(manifest)
        path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')

    def test_manifest_path_traversal_is_rejected(self):
        bundle = self._bundle()
        outside = self.root / 'outside.json'
        outside.write_text('{"kind": "entry"}', encoding='utf-8')
        digest = hashlib.sha256(outside.read_bytes()).hexdigest()
        self._rewrite_manifest(bundle, lambda manifest: manifest['companions'].update(
            {'../outside.json': {'bytes': outside.stat().st_size, 'sha256': digest}}))
        with self.assertRaises(ValueError):
            brain_backup.restore_bundle(bundle, self.root / 'target')

    def test_database_name_outside_the_format_is_rejected(self):
        bundle = self._bundle()
        self._rewrite_manifest(bundle, lambda manifest: manifest['db'].update(
            {'file': '../../elsewhere.db'}))
        with self.assertRaises(ValueError):
            brain_backup.restore_bundle(bundle, self.root / 'target')

    def test_a_missing_hash_is_rejected(self):
        (self.role_dir / 'learned-context.json').write_text('{"facts": []}\n', encoding='utf-8')
        bundle = self._bundle()
        self._rewrite_manifest(bundle, lambda manifest: manifest['db'].pop('sha256'))
        with self.assertRaises(ValueError):
            brain_backup.restore_bundle(bundle, self.root / 'target')
        bundle2 = self._bundle2()
        self._rewrite_manifest(bundle2, lambda manifest: manifest['companions']['learned-context.json']
                               .pop('sha256'))
        with self.assertRaises(ValueError):
            brain_backup.restore_bundle(bundle2, self.root / 'target2')

    def _bundle2(self):
        out = self.root / 'bundle2'
        brain_backup.backup_bundle(self.role_dir, out)
        return out

    def test_current_rotation_state_is_preserved(self):
        rotation = self.role_dir / 'recall-rotation.json'
        rotation.write_text('{"cursor": 1}\n', encoding='utf-8')
        bundle = self._bundle()
        rotation.write_text('{"cursor": 99}\n', encoding='utf-8')
        target = self.root / 'target-rotation'
        import shutil
        shutil.copytree(self.role_dir, target)
        result = brain_backup.restore_bundle(bundle, target)
        self.assertIn('recall-rotation.json', result['preserved_newer'])
        self.assertEqual(json.loads((target / 'recall-rotation.json').read_text(encoding='utf-8')),
                         {'cursor': 99})

    def test_reconcile_clears_a_repaired_stale_ref(self):
        learned = self.role_dir / 'learned-context.json'
        learned.write_text(json.dumps({
            'facts': [{'text': 'a kept fact', 'ref': 'memory:mem-backup-001'}],
        }, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
        first = brain_backup.reconcile_companion_state(self.role_dir)
        self.assertEqual(first['stale_learned_context_refs'], 0)
        learned.write_text(json.dumps({
            'facts': [{'text': 'a fact', 'ref': 'memory:gone'}],
        }, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
        second = brain_backup.reconcile_companion_state(self.role_dir)
        self.assertEqual(second['stale_learned_context_refs'], 1)
        entry = json.loads(learned.read_text(encoding='utf-8'))['facts'][0]
        self.assertTrue(entry['stale_ref'])
        entry['ref'] = 'memory:mem-backup-001'
        data = json.loads(learned.read_text(encoding='utf-8'))
        data['facts'][0] = entry
        learned.write_text(json.dumps(data, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
        third = brain_backup.reconcile_companion_state(self.role_dir)
        self.assertEqual(third['stale_learned_context_refs'], 0)
        repaired = json.loads(learned.read_text(encoding='utf-8'))['facts'][0]
        self.assertNotIn('stale_ref', repaired)
        self.assertNotIn('missing_refs', repaired)

    def test_u2028_and_reconcile_markers_survive_the_merge(self):
        exp = self.role_dir / 'experience-memory.jsonl'
        record = {'kind': 'entry', 'source_key': 'batch-1', 'entry_index': 0,
                  'memory_id': 'mem-backup-001', 'text': 'a line\u2028with a separator'}
        exp.write_text(json.dumps(record, ensure_ascii=False) + '\n', encoding='utf-8')
        bundle = self._bundle()
        # After the backup the database moved on: reconcile marks this entry as unlinked.
        self.conn.execute("DELETE FROM event_stages")
        self.conn.commit()
        brain_backup.reconcile_companion_state(self.role_dir)
        marked = json.loads(exp.read_text(encoding='utf-8').split('\n')[0])
        self.assertTrue(marked.get('stage_unlinked'))
        target = self.root / 'target-u2028'
        import shutil
        shutil.copytree(self.role_dir, target)
        brain_backup.restore_bundle(bundle, target)
        lines = [line for line in (target / 'experience-memory.jsonl')
                 .read_text(encoding='utf-8').split('\n') if line.strip()]
        matching = [json.loads(line) for line in lines
                    if json.loads(line).get('kind') == 'entry']
        self.assertEqual(len(matching), 1, 'the same record must not be duplicated')
        self.assertEqual(matching[0]['text'], 'a line\u2028with a separator')
        self.assertTrue(matching[0].get('stage_unlinked'),
                        'the current reconcile state must win over the older backup row')

    def test_failed_child_table_copy_rolls_the_merge_back(self):
        bundle = self._bundle()
        safety = self.root / 'safety.db'
        import shutil
        shutil.copy2(self.db_path, safety)
        candidate = self.root / 'candidate.db'
        shutil.copy2(bundle / 'brain.db', candidate)
        writer = sqlite3.connect(str(candidate), timeout=30)
        writer.execute("INSERT INTO quotes(memory_id, ord, speaker, text) "
                       "VALUES('mem-backup-001', 0, 'user', 'a kept quote')")
        writer.commit()
        writer.close()
        self.conn.close()
        self.conn = sqlite3.connect(str(self.db_path), timeout=30)
        original_columns = brain_backup._table_columns

        def broken_columns(connection, table):
            if table == 'quotes':
                raise sqlite3.OperationalError('disk I/O error')
            return original_columns(connection, table)

        with patch.object(brain_backup, '_table_columns', broken_columns):
            with self.assertRaises(sqlite3.OperationalError):
                brain_backup._merge_new_rows(safety, candidate)
        restored = sqlite3.connect(str(candidate))
        try:
            rows = restored.execute(
                "SELECT text FROM quotes WHERE memory_id='mem-backup-001'").fetchall()
            vectors = restored.execute(
                "SELECT COUNT(*) FROM vectors WHERE memory_id='mem-backup-001'").fetchone()[0]
        finally:
            restored.close()
        self.assertEqual(len(rows), 1, 'the rollback must keep the original quote rows')
        self.assertGreaterEqual(vectors, 0)


class TestRecoveryIdempotency(unittest.TestCase):
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
            '\n'.join(json.dumps(row, ensure_ascii=False) for row in rows) + '\n', encoding='utf-8')

    def test_equal_boundaries_share_one_key(self):
        from p6_s2_recover_history import make_recovery_source_key
        self.assertEqual(
            make_recovery_source_key('m', '2026-11-25T11:00:00+08:00', '2026-11-25T13:00:00+08:00'),
            make_recovery_source_key('m', '2026-11-25T11:00:00.000000+08:00',
                                     '2026-11-25T13:00:00.000000+08:00'))
        self.assertEqual(
            make_recovery_source_key('m', '2026-11-25T04:00:00+00:00', '2026-11-25T05:00:00+00:00'),
            make_recovery_source_key('m', '2026-11-25T12:00:00+08:00', '2026-11-25T13:00:00+08:00'))

    def test_window_compares_instants_not_strings(self):
        from p6_s2_recover_history import _load_chat_slice
        self._chat([{'message_id': 'm1', 'role': 'user',
                     'time': '2026-11-25T04:00:00+00:00', 'content': 'same moment'}])
        rows = _load_chat_slice(self.role_dir / 'chat.jsonl', '2026-11-25T11:00:00+08:00',
                                '2026-11-25T13:00:00+08:00')
        self.assertEqual([row['message_id'] for row in rows], ['m1'])

    def test_record_separator_is_a_physical_newline_only(self):
        from p6_s2_recover_history import _load_chat_slice
        self._chat([{'message_id': 'm2', 'role': 'user', 'time': '2026-11-25T12:00:00+08:00',
                     'content': 'line one\u2028line two'}])
        rows = _load_chat_slice(self.role_dir / 'chat.jsonl', '2026-11-25T11:00:00+08:00',
                                '2026-11-25T13:00:00+08:00')
        self.assertEqual(len(rows), 1)
        self.assertIn('\u2028', rows[0]['content'])

    def test_committed_key_is_honoured_before_the_chat_is_required(self):
        from p6_s2_recover_history import recover_slice
        self._chat([{'message_id': 'm3', 'role': 'user', 'time': '2026-11-25T12:00:00+08:00',
                     'content': 'the lost slice'}])
        first = recover_slice(self.role_dir, self.db_path, memory_id='mem-recover-001',
                              from_time='2026-11-25T11:00:00+08:00',
                              to_time='2026-11-25T13:00:00+08:00', dry_run=False)
        self.assertEqual(first['status'], 'recovered')
        (self.role_dir / 'chat.jsonl').unlink()
        second = recover_slice(self.role_dir, self.db_path, memory_id='mem-recover-001',
                               from_time='2026-11-25T11:00:00+08:00',
                               to_time='2026-11-25T13:00:00+08:00', dry_run=False)
        self.assertEqual(second['status'], 'already_recovered')
        markers = [json.loads(line) for line in
                   (self.role_dir / 'experience-memory.jsonl').read_text(encoding='utf-8').split('\n')
                   if line.strip()]
        self.assertEqual(len([row for row in markers if row.get('kind') == 'p6-s2-recovery']), 1,
                         'the marker must be repaired, not duplicated')

    def test_message_identity_uses_message_id(self):
        from p6_s2_recover_history import recover_slice
        self._chat([{'message_id': 'm4', 'role': 'user', 'time': '2026-11-25T12:00:00+08:00',
                     'content': 'the lost slice'}])
        result = recover_slice(self.role_dir, self.db_path, memory_id='mem-recover-001',
                               from_time='2026-11-25T11:00:00+08:00',
                               to_time='2026-11-25T13:00:00+08:00', dry_run=True)
        self.assertEqual(result['status'], 'would_recover')
        self.assertEqual(result['message_ids'], ['m4'])


class TestNativeHoldKeepsOldTitle(unittest.TestCase):
    def test_title_change_archives_the_complete_old_markdown(self):
        temp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(temp.cleanup)
        root = Path(temp.name)
        memory = _brain_memory(root)
        self.addCleanup(memory.__exit__, None, None, None)
        authority = memory.brain_authority
        writer = sqlite3.connect(str(authority.db_path))
        try:
            target = brain_writer.create_memory(
                writer, {'body': 'the first body', 'title': 'Old title'},
                embedder=lambda _text: [0.5] * 1024)['memory_id']
        finally:
            writer.close()
        tools = NativeMemoryTools(memory)
        # Peter may only rewrite a memory he was shown in this conversation.
        tools.search_results = {target: 'the first body'}
        result = tools._hold({'content': 'a rewritten body', 'title': 'New title', 'target_id': target})
        if result[1] == '待选择':
            result = tools._hold({'content': 'a rewritten body', 'title': 'New title',
                                  'target_id': target})
        self.assertIn(result[1], ('成功', '新建', '已更新'))
        versions = tools.panel_versions(target)
        self.assertTrue(versions, 'a version must be recorded for the rewrite')
        titles = [row['title'] for row in versions]
        self.assertIn('Old title', titles,
                      'the old title must be recoverable from the archived version')


if __name__ == '__main__':
    unittest.main()
