"""P6-S6-1: delivered activation, protective decay and versioned importance re-evaluation.

Every case uses a throwaway role directory with a synthetic brain.db; no model, network or real
data is involved.
"""
from __future__ import annotations

import json
import sqlite3
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace

from veil.brain import memory_weights
from veil.brain.memory_weights import (
    DEFAULTS,
    MemoryWeights,
    display_activity,
    effective_score,
    load_persisted,
    parse_time,
    settings_from,
)
from veil.brain.recall import brain_retrieval
from veil.brain.recall import recall_rotation
from veil.brain.storage import brain_events, brain_writer, veil_brain


class Clock:
    def __init__(self, value):
        self.value = value

    def __call__(self):
        return self.value


class Store:
    """The production Store surface this module uses: root + atomic single-object replace."""

    def __init__(self, root):
        self.root = Path(root)

    def replace(self, name, event):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({'time': '2026-11-01T10:00:00+0800', **event},
                                   ensure_ascii=False) + '\n', encoding='utf-8')


def _now_iso(text):
    return text


def make_db(path, rows, *, relationship=None):
    conn = sqlite3.connect(str(path))
    try:
        if not conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='memories'"
                            ).fetchone():
            conn.executescript(veil_brain.SCHEMA)
            conn.commit()
        brain_writer.ensure_schema(conn)
        brain_events.ensure_schema(conn)
        for row in rows:
            created = row.get('created') or '2026-11-01T10:00:00+0800'
            conn.execute(
                'INSERT INTO memories(id,bucket,file_rel,title,name,body,body_sha256,'
                'raw_markdown,raw_sha256,frontmatter_raw,frontmatter_json,created,last_active,'
                'type,importance,why_remembered,meaning,pinned,is_tombstone,is_deleted,'
                'vector_state) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
                (row['id'], row.get('bucket', 'dynamic'), row.get('file_rel', 'dynamic/%s.md' % row['id']),
                 row.get('title', row['id']), row.get('title', row['id']),
                 row.get('body', '一段共同经历正文'), 'sha', row.get('raw', '---\n---\n'),
                 'sha', '---\n---\n', '{}', created, created,
                 row.get('type', 'dynamic'), row.get('importance', 5),
                 row.get('why_remembered', ''), row.get('meaning', ''),
                 int(row.get('pinned', 0)), int(row.get('is_tombstone', 0)),
                 int(row.get('is_deleted', 0)), 'ready'))
        for item in rows:
            event_id = item.get('event_id')
            if event_id:
                conn.execute('INSERT INTO events(event_id,memory_id,source_count,title,first_at,last_at) '
                             'VALUES(?,?,?,?,?,?)',
                             (event_id, item['id'], 1, item.get('event_title') or event_id,
                              '2026-10-01T10:00:00+0800', '2026-11-02T10:00:00+0800'))
            if item.get('locked'):
                conn.execute("INSERT INTO sidecar_state(memory_id,locked,links_json,sources_json) "
                             "VALUES(?,1,'[]','[]')", (item['id'],))
        conn.commit()
    finally:
        conn.close()
    if relationship is not None:
        (Path(path).parent / 'learned-context.json').write_text(
            json.dumps({'relationship': {'items': relationship}}, ensure_ascii=False), encoding='utf-8')


class _Base(unittest.TestCase):
    rows = ()

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / 'peter').mkdir()
        self.db = self.root / 'peter' / 'brain.db'
        self.clock = Clock('2026-11-01T10:00:00+0800')
        self.cfg = SimpleNamespace(id='peter', profile={'file': 'learned-context.json'},
                                   memory={'recall': {}})

    def make(self, rows, relationship=None, **kwargs):
        make_db(self.db, rows, relationship=relationship)
        kwargs.setdefault('review_inline', True)
        return MemoryWeights(self.cfg, Store(self.root), now_fn=self.clock, **kwargs)

    def weights(self):
        return MemoryWeights(self.cfg, Store(self.root), now_fn=self.clock, review_inline=True)


class ActivationAccountingTests(_Base):
    def setUp(self):
        super().setUp()
        self.w = self.make([{'id': 'm1', 'title': '海边散步', 'importance': 5},
                            {'id': 'm2', 'title': '只被重新生成过', 'importance': 5}])

    def test_only_confirmed_delivery_writes_activation(self):
        self.assertEqual(self.w.activation_map(['m1']), {})
        self.clock.value = '2026-11-01T12:34:56+0800'
        out = self.w.apply_delivery({'request_id': 'r1', 'injected_ids': ['m1']})
        self.assertEqual(out['status'], 'applied')
        info = self.w.activation_map(['m1'])['m1']
        self.assertEqual(info['deliveries'], 1)
        self.assertEqual(info['distinct_days'], 1)
        self.assertGreater(info['display'], 0)
        self.assertEqual(info['last_active'], '2026-11-01T12:34:56+0800')
        conn = sqlite3.connect(str(self.db))
        try:
            self.assertEqual(conn.execute(
                "SELECT last_active FROM memories WHERE id='m1'").fetchone()[0],
                '2026-11-01T12:34:56+0800')
        finally:
            conn.close()

    def test_duplicate_confirmation_and_restart_do_not_double_count(self):
        self.w.apply_delivery({'request_id': 'r1', 'injected_ids': ['m1']})
        out = self.w.apply_delivery({'request_id': 'r1', 'injected_ids': ['m1']})
        self.assertEqual(out['status'], 'duplicate')
        self.assertEqual(out['duplicate_ids'], ['m1'])
        # A fresh instance (process restart) sees the same durable event and still does not count.
        fresh = self.weights()
        out2 = fresh.apply_delivery({'request_id': 'r1', 'injected_ids': ['m1']})
        self.assertEqual(out2['status'], 'duplicate')
        self.assertEqual(fresh.activation_map(['m1'])['m1']['deliveries'], 1)

    def test_distinct_days_and_sessions_are_counted(self):
        self.w.apply_delivery({'request_id': 'r1', 'injected_ids': ['m1'], 'session_key': 'conv:1'})
        self.clock.value = '2026-11-02T10:00:00+0800'
        self.w.apply_delivery({'request_id': 'r2', 'injected_ids': ['m1'], 'session_key': 'conv:2'})
        info = self.w.activation_map(['m1'])['m1']
        self.assertEqual(info['deliveries'], 2)
        self.assertEqual(info['distinct_days'], 2)
        self.assertEqual(info['distinct_sessions'], 2)

    def test_regenerated_reply_updates_recency_without_reinforcement(self):
        self.w.apply_delivery({'request_id': 'r1', 'injected_ids': ['m1']})
        self.clock.value = '2026-11-01T10:05:00+0800'
        out = self.w.apply_delivery({'request_id': 'r2', 'injected_ids': ['m1'],
                                     'regenerated': True})
        self.assertEqual(out['counted_ids'], [])
        info = self.w.activation_map(['m1'])['m1']
        self.assertEqual(info['deliveries'], 1)
        self.assertEqual(info['last_delivery_at'][:16], '2026-11-01T10:00')
        # The factual recency timestamp still moves: the memory really was on screen again.
        self.assertEqual(info['last_active'][:16], '2026-11-01T10:05')

    def test_first_regenerated_exposure_is_visible_without_reinforcement(self):
        out = self.w.apply_delivery({'request_id': 'r2', 'injected_ids': ['m2'],
                                     'regenerated': True})
        self.assertEqual(out['counted_ids'], [])
        info = self.w.activation_map(['m2'])['m2']
        self.assertEqual(info['deliveries'], 0)
        self.assertEqual(info['display'], 0)
        self.assertTrue(info['last_active'])
        self.assertFalse(info['reeval_pending'])

    def test_concurrent_confirmations_of_one_request_count_once(self):
        workers = [MemoryWeights(self.cfg, Store(self.root), now_fn=self.clock, review_inline=True)
                   for _ in range(4)]
        barrier = threading.Barrier(4)

        def run(w):
            barrier.wait()
            return w.apply_delivery({'request_id': 'race', 'injected_ids': ['m1']})

        with ThreadPoolExecutor(max_workers=4) as pool:
            results = list(pool.map(run, workers))
        self.assertTrue(all(r['status'] in ('applied', 'duplicate') for r in results))
        self.assertEqual(self.weights().activation_map(['m1'])['m1']['deliveries'], 1)

    def test_missing_and_archived_memories_are_ignored(self):
        w = self.make([{'id': 'gone', 'bucket': 'archive'}])
        out = w.apply_delivery({'request_id': 'r9', 'injected_ids': ['missing', 'gone']})
        self.assertEqual(out['counted_ids'], [])
        self.assertEqual(w.activation_map(['missing', 'gone']), {})

    def test_switch_off_stops_new_writes_but_keeps_history(self):
        self.w.apply_delivery({'request_id': 'r1', 'injected_ids': ['m1']})
        result = self.w.update_settings({'activation_enabled': False})
        self.assertTrue(result['ok'])
        out = self.w.apply_delivery({'request_id': 'r2', 'injected_ids': ['m1']})
        self.assertEqual(out['reason'], 'activation_disabled')
        self.assertEqual(self.w.activation_map(['m1'])['m1']['deliveries'], 1)

    def test_empty_delivery_never_creates_rows(self):
        out = self.w.apply_delivery({'request_id': 'r0', 'injected_ids': [], 'opening_ids': []})
        self.assertEqual(out['reason'], 'nothing_delivered')
        self.assertEqual(self.w.activation_map(['m1']), {})


class DecayProtectionTests(_Base):
    def test_protected_memories_never_decay(self):
        for importance, pinned in ((5, 0), (6, 0), (7, 0), (8, 0), (5, 1)):
            score = effective_score(8.0, '2026-01-01T00:00:00+0800', importance, pinned,
                                    half_life_days=30, now=parse_time('2026-03-01T00:00:00+0800'))
            if importance >= 7 or pinned:
                self.assertEqual(score, 8.0)
            else:
                self.assertLess(score, 8.0)
                self.assertGreater(score, 0.0)

    def test_decay_never_changes_importance_visibility_or_existence(self):
        w = self.make([
            {'id': 'low', 'importance': 5},
            {'id': 'high', 'importance': 8},
            {'id': 'pin', 'importance': 5, 'pinned': 1},
        ])
        for mid in ('low', 'high', 'pin'):
            w.apply_delivery({'request_id': 'r-' + mid, 'injected_ids': [mid]})
        # Age every activation by 120 days (four half-lives).
        conn = sqlite3.connect(str(self.db))
        try:
            conn.execute('UPDATE memory_activation SET score_updated_at=?, last_delivery_at=?',
                         ('2026-07-01T10:00:00+0800', '2026-07-01T10:00:00+0800'))
            conn.commit()
        finally:
            conn.close()
        self.clock.value = '2026-11-01T10:00:00+0800'
        info = w.activation_map(['low', 'high', 'pin'])
        self.assertLess(info['low']['score'], 0.2)
        self.assertGreater(info['low']['stored_score'], info['low']['score'])
        self.assertGreater(info['low']['deliveries'], 0)
        self.assertEqual(info['high']['score'], info['high']['stored_score'])
        self.assertEqual(info['pin']['score'], info['pin']['stored_score'])
        conn = sqlite3.connect(str(self.db))
        try:
            rows = {row[0]: (row[1], row[2], row[3]) for row in conn.execute(
                'SELECT id, importance, is_deleted, is_tombstone FROM memories')}
        finally:
            conn.close()
        self.assertEqual(rows['low'], (5, 0, 0))
        self.assertEqual(rows['high'], (8, 0, 0))
        self.assertEqual(rows['pin'], (5, 0, 0))

    def test_display_activity_is_bounded_and_monotonic(self):
        self.assertEqual(display_activity(0), 0)
        self.assertGreater(display_activity(1), 0)
        self.assertGreater(display_activity(4), display_activity(1))
        self.assertLessEqual(display_activity(1000), 100)


class ImportanceProposalTests(_Base):
    def setUp(self):
        super().setUp()
        self.w = self.make(
            [{'id': 'm1', 'title': '一起讨论他能不能自己记住', 'importance': 5,
              'why_remembered': '这是她第一次认真谈我的记忆', 'meaning': '关于我如何记得她',
              'event_id': 'e1', 'event_title': '记忆系统长谈'}],
            relationship=[{'id': 'rel1', 'status': 'confirmed', 'text': 'Lan 希望我记得我们讨论过的事',
                           'sources': ['event:e1'], 'updated_at': '2026-10-02T10:00:00+0800'}])

    def _deliver(self, request_id, session, day):
        self.clock.value = day
        return self.w.apply_delivery({'request_id': request_id, 'injected_ids': ['m1'],
                                      'session_key': session})

    def test_new_repeated_experience_triggers_evidence_backed_versioned_review(self):
        out = self._deliver('r1', 'conv:1', '2026-11-01T10:00:00+0800')
        self.assertEqual(out['status'], 'applied')
        applied = self.w.proposals(memory_id='m1', status='applied')
        self.assertEqual(len(applied), 1)
        proposal = applied[0]
        self.assertEqual(proposal['current_importance'], 5)
        self.assertEqual(proposal['proposed_importance'], 6)
        self.assertTrue(proposal['auto_applied'])
        codes = {item['code'] for item in proposal['reasons']}
        self.assertIn('event', codes)
        self.assertIn('relationship', codes)
        refs = {(item.get('kind'), item.get('ref')) for item in proposal['evidence']}
        self.assertIn(('event', 'e1'), refs)
        self.assertIn(('relationship', 'rel1'), refs)
        conn = sqlite3.connect(str(self.db))
        try:
            importance = conn.execute("SELECT importance FROM memories WHERE id='m1'").fetchone()[0]
            version = conn.execute('SELECT operation_reason, fields_json FROM versions '
                                   "WHERE memory_id='m1' ORDER BY seq DESC LIMIT 1").fetchone()
        finally:
            conn.close()
        self.assertEqual(importance, 6)
        self.assertIn('importance-reevaluation', version[0])
        self.assertIn('importance', version[1])

    def test_cooldown_and_bounded_auto_then_review_for_top_band(self):
        self._deliver('r1', 'conv:1', '2026-11-01T10:00:00+0800')
        # Same day, immediately again: cooldown blocks a second proposal.
        again = self._deliver('r2', 'conv:1', '2026-11-01T11:00:00+0800')
        self.assertEqual(len(again['proposals']), 0)
        # Next day after the cooldown: one more bounded step, never a jump.
        self.clock.value = '2026-11-02T12:00:00+0800'
        out = self.w.apply_delivery({'request_id': 'r3', 'injected_ids': ['m1'],
                                     'session_key': 'conv:2'})
        self.assertEqual(out['status'], 'applied')
        conn = sqlite3.connect(str(self.db))
        try:
            importance = conn.execute("SELECT importance FROM memories WHERE id='m1'").fetchone()[0]
        finally:
            conn.close()
        self.assertEqual(importance, 7, 'auto-apply stops at the configured upper bound')
        # A further step would enter the top band, so it is only proposed for review.
        self.clock.value = '2026-11-04T12:00:00+0800'
        self.w.apply_delivery({'request_id': 'r4', 'injected_ids': ['m1'],
                               'session_key': 'conv:2'})
        pending = self.w.proposals(memory_id='m1', status='pending')
        self.assertEqual(len(pending), 1, 'top-band changes must wait for review')
        self.assertEqual(pending[0]['current_importance'], 7)
        self.assertEqual(pending[0]['proposed_importance'], 8)
        conn = sqlite3.connect(str(self.db))
        try:
            importance = conn.execute("SELECT importance FROM memories WHERE id='m1'").fetchone()[0]
        finally:
            conn.close()
        self.assertEqual(importance, 7, 'pending proposal must not change importance yet')

    def test_pinned_and_locked_stay_pending_not_auto_applied(self):
        w = self.make([
            {'id': 'pin', 'importance': 5, 'pinned': 1, 'event_id': 'e3'},
            {'id': 'lock', 'importance': 5, 'locked': 1, 'event_id': 'e4'},
        ], relationship=[{'id': 'r1', 'status': 'confirmed', 'text': '关系',
                          'sources': ['event:e3', 'event:e4']}])
        for mid in ('pin', 'lock'):
            w.apply_delivery({'request_id': 'r-' + mid, 'injected_ids': [mid]})
        for mid in ('pin', 'lock'):
            proposals = w.proposals(memory_id=mid)
            self.assertEqual(len(proposals), 1, mid)
            self.assertEqual(proposals[0]['status'], 'pending')
        conn = sqlite3.connect(str(self.db))
        try:
            rows = dict(conn.execute('SELECT id, importance FROM memories').fetchall())
        finally:
            conn.close()
        self.assertEqual(rows['pin'], 5)
        self.assertEqual(rows['lock'], 5)

    def test_user_can_accept_and_revert_an_applied_change(self):
        self._deliver('r1', 'conv:1', '2026-11-01T10:00:00+0800')
        applied = self.w.proposals(memory_id='m1', status='applied')[0]
        revert = self.w.decide(applied['proposal_id'], 'revert', '这次不算')
        self.assertTrue(revert['ok'], revert)
        self.assertEqual(revert['importance'], 5)
        history = self.w.proposals(memory_id='m1')
        self.assertEqual(history[0]['status'], 'reverted')
        conn = sqlite3.connect(str(self.db))
        try:
            importance = conn.execute("SELECT importance FROM memories WHERE id='m1'").fetchone()[0]
            versions = conn.execute("SELECT COUNT(*) FROM versions WHERE memory_id='m1'").fetchone()[0]
        finally:
            conn.close()
        self.assertEqual(importance, 5)
        self.assertGreaterEqual(versions, 2, 'both the change and the revert keep versions')

    def test_decrease_is_never_automatic_and_needs_review(self):
        result = self.w.propose('m1', trigger='consolidation', proposed_importance=3,
                                confidence=0.9, reasons=[{'code': 'external', 'label': '巩固',
                                                          'detail': '测试', 'weight': 0.9}],
                                evidence=[{'kind': 'event', 'ref': 'e1', 'detail': '测试'}],
                                auto_apply=True)
        self.assertTrue(result['ok'], result)
        self.assertFalse(result['applied'])
        self.assertEqual(result['proposal']['status'], 'pending')
        conn = sqlite3.connect(str(self.db))
        try:
            importance = conn.execute("SELECT importance FROM memories WHERE id='m1'").fetchone()[0]
        finally:
            conn.close()
        self.assertEqual(importance, 5)
        accepted = self.w.decide(result['proposal']['proposal_id'], 'accept')
        self.assertTrue(accepted['ok'], accepted)
        self.assertEqual(accepted['apply']['importance'], 3)

    def test_only_one_pending_proposal_per_memory(self):
        self.w.update_settings({'reeval_auto_apply': False})
        first = self.w.reevaluate('m1', force=True)
        self.assertTrue(first['ok'], first)
        self.assertEqual(first['proposal']['status'], 'pending')
        second = self.w.reevaluate('m1', force=True)
        self.assertFalse(second['ok'])
        self.assertEqual(second['reason'], 'pending_exists')

    def test_reevaluation_disabled_stops_proposals(self):
        self.w.update_settings({'reeval_enabled': False})
        out = self._deliver('r1', 'conv:1', '2026-11-01T10:00:00+0800')
        self.assertEqual(out['proposals'], [])
        self.assertEqual(self.w.proposals(memory_id='m1'), [])

    def test_hard_delete_erases_activation_and_proposal_rows(self):
        self._deliver('r1', 'conv:1', '2026-11-01T10:00:00+0800')
        conn = sqlite3.connect(str(self.db))
        try:
            brain_writer.hard_delete(conn, 'm1', 'test')
        finally:
            conn.close()
        conn = sqlite3.connect(str(self.db))
        try:
            for table in ('memory_activation', 'memory_delivery_events',
                          'memory_importance_proposals'):
                count = conn.execute('SELECT COUNT(*) FROM %s WHERE memory_id=?' % table,
                                     ('m1',)).fetchone()[0]
                self.assertEqual(count, 0, table)
        finally:
            conn.close()


class SettingsAndRankingTests(_Base):
    def test_defaults_keep_ranking_off_and_protect_importance(self):
        self.assertFalse(DEFAULTS['ranking_enabled'])
        self.assertIs(DEFAULTS['reeval_auto_apply'], True)
        self.assertEqual(DEFAULTS['reeval_max_auto_importance'], 7)
        settings = brain_retrieval.load_settings({})
        self.assertFalse(settings['activation_ranking_enabled'])

    def test_inactive_activation_settings_do_not_create_index_variants(self):
        self.make([{'id': 'm1', 'title': '常聊的事', 'body': '我们最近一直聊这件事'}])
        base = {'weight_vector': 0.65, 'weight_keyword': 0.35, 'candidate_k': 8,
                'stopwords_enabled': False, 'activation_ranking_enabled': False}
        first = brain_retrieval.get_index(self.db, {**base, 'activation_half_life_days': 30})
        second = brain_retrieval.get_index(self.db, {**base, 'activation_half_life_days': 7})
        self.assertIs(first, second)
        third = brain_retrieval.get_index(
            self.db, {**base, 'activation_ranking_enabled': True, 'activation_half_life_days': 7})
        self.assertIsNot(first, third)

    def test_ui_settings_persist_and_validate(self):
        w = self.make([{'id': 'm1'}])
        result = w.update_settings({'ranking_enabled': True, 'ranking_weight': 0.08,
                                    'decay_half_life_days': 14, 'nonsense': 1})
        self.assertTrue(result['ok'])
        self.assertIn('nonsense', result['rejected'])
        fresh = self.weights()
        self.assertTrue(fresh.settings()['ranking_enabled'])
        self.assertEqual(fresh.settings()['decay_half_life_days'], 14)
        bad = fresh.update_settings({'ranking_weight': 5, 'decay_half_life_days': 2})
        self.assertEqual(set(bad['rejected']), {'ranking_weight', 'decay_half_life_days'})
        self.assertTrue(load_persisted(SimpleNamespace(root=self.root), 'peter')['ranking_enabled'])

    def test_activation_ranking_changes_order_only_when_enabled(self):
        w = self.make([
            {'id': 'fresh', 'title': '常聊的事', 'body': '我们最近一直聊这件事', 'importance': 5},
            {'id': 'cold', 'title': '常聊的事', 'body': '我们最近一直聊这件事', 'importance': 5},
        ])
        base = {'weight_vector': 0.65, 'weight_keyword': 0.35, 'candidate_k': 8,
                'stopwords_enabled': False}
        off = brain_retrieval.get_index(self.db, {**base, 'activation_ranking_enabled': False})
        on = brain_retrieval.get_index(self.db, {**base, 'activation_ranking_enabled': True,
                                                 'activation_weight': 0.2,
                                                 'activation_half_life_days': 30,
                                                 'activation_decay_enabled': True})
        # Before any real delivery the optional signal is a strict no-op: same ids, same order.
        before_off, before_on = {}, {}
        ids_off = [item['id'] for item in off.search('常聊的事', limit=8, query_vector=[],
                                                     diagnostics=before_off)]
        ids_on = [item['id'] for item in on.search('常聊的事', limit=8, query_vector=[],
                                                   diagnostics=before_on)]
        self.assertEqual(ids_off, ids_on)
        self.assertEqual(before_on['activation_ranked'], 0)
        self.assertFalse(before_off['activation_ranking'])
        # After one counted delivery, only the enabled index can use the signal.
        w.apply_delivery({'request_id': 'r1', 'injected_ids': ['fresh']})
        diag_off, diag_on = {}, {}
        off_ids = [item['id'] for item in off.search('常聊的事', limit=8, query_vector=[],
                                                     diagnostics=diag_off)]
        on_items = on.search('常聊的事', limit=8, query_vector=[], diagnostics=diag_on)
        self.assertEqual(diag_on['activation_ranked'], 1)
        self.assertEqual(on_items[0]['id'], 'fresh')
        self.assertGreater(diag_on['activation_weight'], 0)
        self.assertEqual(off_ids, ids_off)
        self.assertFalse(diag_off['activation_ranking'])


class DeliveryIntegrationTests(_Base):
    def test_memory_confirm_is_the_only_activation_trigger(self):
        from veil.brain.memory_agent import Memory
        w = self.make([{'id': 'm1', 'title': '旧事', 'importance': 5}])
        mem = object.__new__(Memory)
        mem.mutex = threading.RLock()
        mem.rotation = recall_rotation.Cooldown()
        mem.rotation_path = 'peter/recall-rotation.jsonl'
        mem.store = SimpleNamespace(root=self.root, replace=lambda *a, **k: None)
        mem.cfg = SimpleNamespace(id='peter', memory={'model': 'BAAI/bge-m3'})
        mem.budget = SimpleNamespace(clock=lambda: '2026-11-01T10:00:00+0800')
        mem.last_recall_debug = {'ombre': ['m1'], 'judge': {'picked': ['m1']}}
        mem.last_recall_skipped = {'cooldown': 0, 'same_event': 0, 'stage_cap': 0, 'budget': 0}
        mem.last_injected_refs = []
        mem._previous_injected_refs = []
        mem.last_recall_marks = None
        mem.last_association_marks = None
        mem.surfaced_ids = {}
        mem.delivery_listeners = []
        mem.weights = w
        mem._pending_recall = {
            'include_opening': False,
            'blocks': [{'id': 'm1', 'group': 'recall', 'text': '[bucket_id:m1]\n正文',
                        'priority': 10, 'source': 'search', 'event_id': '', 'pointer': '',
                        'kind': 'search'}],
        }
        mem.finish_recall(request_id='req-a')
        self.assertEqual(w.activation_map(['m1']), {}, 'injection alone is not delivery')
        self.assertTrue(mem.rollback_recall('req-a'))
        self.assertEqual(w.activation_map(['m1']), {}, 'a failed/undelivered turn writes nothing')
        # A mismatch also writes nothing.
        mem.finish_recall(request_id='req-b')
        self.assertIsNone(mem.confirm_delivery('req-x'))
        self.assertEqual(w.activation_map(['m1']), {})
        event = mem.confirm_delivery('req-b', session_key='conv:1')
        self.assertIsNotNone(event)
        info = w.activation_map(['m1'])['m1']
        self.assertEqual(info['deliveries'], 1)
        self.assertEqual(mem.last_recall_debug['lifecycle']['effective_used'], ['m1'])

    def test_final_trim_removes_ids_from_pending_delivery(self):
        from veil.brain.memory_agent import Memory
        w = self.make([{'id': 'm1'}, {'id': 'm2'}])
        mem = object.__new__(Memory)
        mem.mutex = threading.RLock()
        mem.rotation = recall_rotation.Cooldown()
        mem.rotation_path = 'peter/recall-rotation.jsonl'
        mem.store = SimpleNamespace(root=self.root, replace=lambda *a, **k: None)
        mem.cfg = SimpleNamespace(id='peter', memory={'model': 'BAAI/bge-m3'})
        mem.budget = SimpleNamespace(clock=lambda: '2026-11-01T10:00:00+0800')
        mem.last_recall_debug = {'ombre': ['m1', 'm2'], 'judge': {'picked': ['m1', 'm2']}}
        mem.last_recall_skipped = {'cooldown': 0, 'same_event': 0, 'stage_cap': 0, 'budget': 0}
        mem.last_injected_refs = []
        mem._previous_injected_refs = []
        mem.last_recall_marks = None
        mem.last_association_marks = None
        mem.surfaced_ids = {}
        mem.delivery_listeners = []
        mem.weights = w
        mem._pending_recall = {
            'include_opening': False,
            'blocks': [
                {'id': 'm1', 'group': 'recall', 'text': '[bucket_id:m1]\n正文', 'priority': 10,
                 'source': 'search', 'event_id': '', 'pointer': '', 'kind': 'search'},
                {'id': 'm2', 'group': 'recall', 'text': '[bucket_id:m2]\n正文', 'priority': 20,
                 'source': 'search', 'event_id': '', 'pointer': '', 'kind': 'search'},
            ],
        }
        mem.finish_recall(request_id='req-trim')
        mem.note_budget_dropped(['m2'])
        event = mem.confirm_delivery('req-trim')
        self.assertIsNotNone(event)
        info = w.activation_map(['m1', 'm2'])
        self.assertEqual(info['m1']['deliveries'], 1)
        self.assertNotIn('m2', info)

    def test_non_user_visible_delivery_does_not_reinforce(self):
        from veil.brain.memory_agent import Memory
        w = self.make([{'id': 'm1'}])
        mem = object.__new__(Memory)
        mem.mutex = threading.RLock()
        mem.rotation = recall_rotation.Cooldown()
        mem.rotation_path = 'peter/recall-rotation.jsonl'
        mem.store = SimpleNamespace(root=self.root, replace=lambda *a, **k: None)
        mem.cfg = SimpleNamespace(id='peter', memory={'model': 'BAAI/bge-m3'})
        mem.budget = SimpleNamespace(clock=lambda: '2026-11-01T10:00:00+0800')
        mem.last_recall_debug = {'ombre': ['m1'], 'judge': {'picked': ['m1']}}
        mem.last_recall_skipped = {}
        mem.last_injected_refs = []
        mem._previous_injected_refs = []
        mem.last_recall_marks = None
        mem.last_association_marks = None
        mem.surfaced_ids = {}
        mem.delivery_listeners = []
        mem.weights = w
        mem._pending_recall = {
            'include_opening': False,
            'blocks': [{'id': 'm1', 'group': 'recall', 'text': '[bucket_id:m1]\n正文',
                        'priority': 10, 'source': 'search', 'event_id': '', 'pointer': '',
                        'kind': 'search'}],
        }
        mem.finish_recall(request_id='req-a')
        event = mem.confirm_delivery('req-a', activation=False)
        self.assertEqual(event['activity']['reason'], 'not_user_visible')
        self.assertEqual(w.activation_map(['m1']), {})

    def test_activation_error_never_breaks_delivery_confirmation(self):
        from veil.brain.memory_agent import Memory
        w = self.make([{'id': 'm1'}])
        w.apply_delivery = lambda event: (_ for _ in ()).throw(RuntimeError('boom'))
        mem = object.__new__(Memory)
        mem.mutex = threading.RLock()
        mem.rotation = recall_rotation.Cooldown()
        mem.rotation_path = 'peter/recall-rotation.jsonl'
        mem.store = SimpleNamespace(root=self.root, replace=lambda *a, **k: None)
        mem.cfg = SimpleNamespace(id='peter', memory={'model': 'BAAI/bge-m3'})
        mem.budget = SimpleNamespace(clock=lambda: '2026-11-01T10:00:00+0800')
        mem.last_recall_debug = {}
        mem.last_recall_skipped = {}
        mem.last_injected_refs = []
        mem._previous_injected_refs = []
        mem.last_recall_marks = None
        mem.last_association_marks = None
        mem.surfaced_ids = {}
        mem.delivery_listeners = []
        mem.weights = w
        mem._pending_recall = {
            'include_opening': False,
            'blocks': [{'id': 'm1', 'group': 'recall', 'text': '[bucket_id:m1]\n正文',
                        'priority': 10, 'source': 'search', 'event_id': '', 'pointer': '',
                        'kind': 'search'}],
        }
        mem.finish_recall(request_id='req-a')
        event = mem.confirm_delivery('req-a')
        self.assertIsNotNone(event)
        self.assertEqual(event['activity']['status'], 'error')




class IntegrityAndRecoveryTests(_Base):
    def test_digest_stays_valid_after_delivery_and_importance_change(self):
        w = self.make([{'id': 'm1', 'title': '共同经历', 'importance': 5,
                        'meaning': '有意义', 'event_id': 'e1', 'event_title': '长谈'}],
                      relationship=[{'id': 'rel1', 'status': 'confirmed', 'text': '关系',
                                     'sources': ['event:e1']}])
        out = w.apply_delivery({'request_id': 'r1', 'injected_ids': ['m1']})
        self.assertTrue(out['counted_ids'])
        conn = sqlite3.connect(str(self.db))
        try:
            stored = conn.execute("SELECT value FROM meta WHERE key='digest'").fetchone()[0]
            actual = veil_brain.canonical_digest(conn)
        finally:
            conn.close()
        self.assertEqual(stored, actual)
        applied = w.proposals(memory_id='m1', status='applied')
        self.assertEqual(len(applied), 1)
        conn = sqlite3.connect(str(self.db))
        try:
            self.assertEqual(conn.execute(
                "SELECT importance FROM memories WHERE id='m1'").fetchone()[0], 6)
            stored = conn.execute("SELECT value FROM meta WHERE key='digest'").fetchone()[0]
            self.assertEqual(stored, veil_brain.canonical_digest(conn))
        finally:
            conn.close()

    def test_receipts_keep_replay_idempotent_after_event_prune(self):
        w = self.make([{'id': 'm1'}])
        w.apply_delivery({'request_id': 'r1', 'injected_ids': ['m1']})
        conn = sqlite3.connect(str(self.db))
        try:
            conn.execute('DELETE FROM memory_delivery_events')
            conn.commit()
        finally:
            conn.close()
        out = w.apply_delivery({'request_id': 'r1', 'injected_ids': ['m1']})
        self.assertEqual(out['status'], 'duplicate')
        self.assertEqual(w.activation_map(['m1'])['m1']['deliveries'], 1)

    def test_session_and_day_totals_survive_event_prune(self):
        w = self.make([{'id': 'm1'}])
        w.apply_delivery({'request_id': 'r1', 'injected_ids': ['m1'], 'session_key': 'conv:1'})
        self.clock.value = '2026-11-02T10:00:00+0800'
        w.apply_delivery({'request_id': 'r2', 'injected_ids': ['m1'], 'session_key': 'conv:2'})
        conn = sqlite3.connect(str(self.db))
        try:
            conn.execute('DELETE FROM memory_delivery_events')
            conn.commit()
        finally:
            conn.close()
        info = w.activation_map(['m1'])['m1']
        self.assertEqual((info['deliveries'], info['distinct_days'], info['distinct_sessions']),
                         (2, 2, 2))
        self.clock.value = '2026-11-03T10:00:00+0800'
        w.apply_delivery({'request_id': 'r3', 'injected_ids': ['m1'], 'session_key': 'conv:3'})
        info = w.activation_map(['m1'])['m1']
        self.assertEqual((info['distinct_days'], info['distinct_sessions']), (3, 3))

    def test_external_proposal_needs_real_evidence(self):
        w = self.make([{'id': 'm1', 'event_id': 'e1', 'event_title': '共同经历'}])
        missing = w.propose('m1', proposed_importance=6, confidence=0.9, evidence=[])
        self.assertEqual(missing['reason'], 'no_evidence')
        bad = w.propose('m1', proposed_importance=6, confidence=0.9,
                        evidence=[{'kind': 'event', 'ref': 'does-not-exist'}])
        self.assertEqual(bad['reason'], 'no_evidence')
        self.assertEqual(w.proposals(memory_id='m1'), [])
        good = w.propose('m1', proposed_importance=6, confidence=0.9,
                         evidence=[{'kind': 'event', 'ref': 'e1', 'detail': '共同经历'}],
                         auto_apply=True)
        self.assertTrue(good['ok'], good)
        self.assertTrue(good['applied'])
        self.assertEqual(good['proposal']['status'], 'applied')
        self.assertEqual(good['proposal']['metrics']['external_has_experience'], True)

    def test_auto_limits_cannot_be_raised_above_policy(self):
        w = self.make([{'id': 'm1'}])
        rejected = w.update_settings({'reeval_max_auto_importance': 9})
        self.assertIn('reeval_max_auto_importance', rejected['rejected'])
        self.assertEqual(w.settings()['reeval_max_auto_importance'], 7)
        lowered = w.update_settings({'reeval_max_auto_importance': 5})
        self.assertTrue(lowered['ok'])
        w2 = self.make([{'id': 'm2', 'event_id': 'e1'}],
                       relationship=[{'id': 'rel1', 'status': 'confirmed', 'text': '关系',
                                      'sources': ['event:e1']}])
        out = w2.apply_delivery({'request_id': 'r1', 'injected_ids': ['m2']})
        proposals = w2.proposals(memory_id='m2')
        self.assertEqual(len(proposals), 1)
        self.assertEqual(proposals[0]['status'], 'pending')
        self.assertEqual(proposals[0]['proposed_importance'], 6)

    def test_association_and_carried_deliveries_are_counted(self):
        w = self.make([{'id': 'm1'}])
        out = w.apply_delivery({'request_id': 'r1', 'association_ids': ['m1']})
        self.assertEqual(out['counted_ids'], ['m1'])
        out = w.apply_delivery({'request_id': 'r2', 'carried_ids': ['m1']})
        self.assertEqual(out['counted_ids'], ['m1'])
        info = w.activation_map(['m1'])['m1']
        self.assertEqual(info['deliveries'], 2)
        self.assertAlmostEqual(info['score'], 1.5, places=3)

    def test_pending_review_recovers_after_interruption(self):
        from unittest.mock import patch
        w = self.make([{'id': 'm1', 'meaning': '有意义', 'event_id': 'e1',
                        'event_title': '共同经历'}],
                      relationship=[{'id': 'rel1', 'status': 'confirmed', 'text': '关系',
                                     'sources': ['event:e1']}])
        with patch.object(w, '_reevaluate', side_effect=RuntimeError('locked')):
            out = w.apply_delivery({'request_id': 'r1', 'injected_ids': ['m1']})
        self.assertEqual(out['status'], 'applied')
        info = w.activation_map(['m1'])['m1']
        self.assertTrue(info['reeval_pending'])
        self.assertEqual(w.proposals(memory_id='m1'), [])
        proposals = w.drain_pending_reeval()
        self.assertEqual(len(proposals), 1)
        self.assertFalse(w.activation_map(['m1'])['m1']['reeval_pending'])
        self.assertEqual(w.proposals(memory_id='m1', status='applied')[0]['proposed_importance'], 6)

    def test_transient_write_failure_is_queued_and_replayed(self):
        from unittest.mock import patch
        w = self.make([{'id': 'm1'}])
        with patch.object(w, '_write_connection', side_effect=RuntimeError('locked')):
            out = w.apply_delivery({'request_id': 'r1', 'injected_ids': ['m1']})
        self.assertEqual(out['status'], 'error')
        self.assertEqual(len(w._load_retries()), 1)
        self.assertEqual(w.drain_retries(), 1)
        self.assertEqual(w.activation_map(['m1'])['m1']['deliveries'], 1)
        self.assertEqual(w._load_retries(), [])

    def test_backup_merge_removes_derived_rows_for_tombstoned_memory(self):
        from veil.brain.storage import brain_backup
        current = self.root / 'current.db'
        target = self.root / 'target.db'
        rows = [{'id': 'm1', 'title': '旧事', 'importance': 5},
                {'id': 'm2', 'title': '仍在的记忆', 'importance': 5}]
        make_db(current, rows)
        make_db(target, rows)
        conn = sqlite3.connect(str(target))
        try:
            conn.execute("INSERT INTO memory_activation(memory_id, delivery_count, distinct_days, "
                         "distinct_sessions, activity_score) VALUES('m1', 2, 1, 1, 2.0)")
            conn.execute("INSERT INTO memory_delivery_receipts(event_key, memory_id, request_id, "
                         "delivered_at) VALUES('k1', 'm1', 'r1', '2026-11-01T10:00:00+0800')")
            conn.execute("INSERT INTO memory_importance_proposals(proposal_id, memory_id, "
                         "created_at, proposed_importance) VALUES('p1', 'm1', "
                         "'2026-11-01T10:00:00+0800', 6)")
            conn.execute("INSERT INTO memory_importance_proposals(proposal_id, memory_id, "
                         "created_at, proposed_importance) VALUES('p2', 'm2', "
                         "'2026-11-01T10:00:00+0800', 6)")
            conn.commit()
        finally:
            conn.close()
        conn = sqlite3.connect(str(current))
        try:
            conn.execute("INSERT INTO tombstones(memory_id, deleted_at, reason_hash) "
                         "VALUES('m1', '2026-11-02T10:00:00+0800', 'x')")
            conn.execute("DELETE FROM memories WHERE id='m1'")
            conn.execute("INSERT INTO memory_importance_proposals(proposal_id, memory_id, "
                         "created_at, status, proposed_importance, decided_at, decided_by) "
                         "VALUES('p2', 'm2', '2026-11-01T10:00:00+0800', 'rejected', 6, "
                         "'2026-11-02T10:00:00+0800', 'lan')")
            conn.execute("INSERT INTO memory_importance_proposals(proposal_id, memory_id, "
                         "created_at, status, proposed_importance, applied_at, version_seq) "
                         "VALUES('p3', 'm2', '2026-11-02T11:00:00+0800', 'applied', 6, "
                         "'2026-11-02T11:00:00+0800', 99)")
            conn.execute("INSERT INTO memory_activation(memory_id, delivery_count, distinct_days, "
                         "distinct_sessions, activity_score) VALUES('m2', 4, 2, 2, 4.0)")
            conn.commit()
        finally:
            conn.close()
        brain_backup._merge_new_rows(current, target)
        conn = sqlite3.connect(str(target))
        try:
            for table in ('memory_activation', 'memory_delivery_receipts',
                          'memory_importance_proposals'):
                count = conn.execute('SELECT COUNT(*) FROM %s WHERE memory_id=?' % table,
                                     ('m1',)).fetchone()[0]
                self.assertEqual(count, 0, table)
            self.assertIsNone(conn.execute(
                "SELECT 1 FROM memories WHERE id='m1'").fetchone())
            self.assertIsNotNone(conn.execute(
                "SELECT 1 FROM tombstones WHERE memory_id='m1'").fetchone())
            decision = conn.execute(
                "SELECT status, decided_by FROM memory_importance_proposals "
                "WHERE proposal_id='p2'").fetchone()
            self.assertEqual(tuple(decision), ('rejected', 'lan'))
            self.assertIsNotNone(conn.execute(
                "SELECT 1 FROM memory_importance_proposals WHERE proposal_id='p3'").fetchone())
            self.assertEqual(conn.execute(
                "SELECT delivery_count FROM memory_activation WHERE memory_id='m2'"
            ).fetchone()[0], 4)
        finally:
            conn.close()

    def test_external_auto_cannot_override_global_switch(self):
        w = self.make([{'id': 'm1', 'event_id': 'e1', 'event_title': '共同经历'}])
        w.update_settings({'reeval_auto_apply': False})
        out = w.propose('m1', proposed_importance=6, confidence=0.95,
                        evidence=[{'kind': 'event', 'ref': 'e1'}], auto_apply=True)
        self.assertTrue(out['ok'], out)
        self.assertFalse(out['applied'])
        self.assertEqual(out['proposal']['status'], 'pending')
        conn = sqlite3.connect(str(self.db))
        try:
            self.assertEqual(conn.execute(
                "SELECT importance FROM memories WHERE id='m1'").fetchone()[0], 5)
        finally:
            conn.close()

    def test_revert_refuses_after_a_later_importance_version(self):
        w = self.make([{'id': 'm1', 'meaning': '有意义', 'event_id': 'e1',
                        'event_title': '共同经历'}],
                      relationship=[{'id': 'rel1', 'status': 'confirmed', 'text': '关系',
                                     'sources': ['event:e1']}])
        w.apply_delivery({'request_id': 'r1', 'injected_ids': ['m1']})
        applied = w.proposals(memory_id='m1', status='applied')[0]
        conn = sqlite3.connect(str(self.db))
        try:
            conn.execute("INSERT INTO versions(memory_id,time,operation_reason,fields_json,"
                         "old_content,old_sha256) VALUES('m1','2026-11-02T10:00:00+0800',"
                         "'Lan 手动改重要度','[\"importance\"]','x','x')")
            conn.commit()
        finally:
            conn.close()
        out = w.decide(applied['proposal_id'], 'revert')
        self.assertFalse(out['ok'])
        self.assertEqual(out['error'], 'importance_changed_since_apply')

    def test_cold_important_memory_stays_retrievable_under_activation_ranking(self):
        rows = [{'id': 'hot', 'title': '常聊的日常', 'body': '今天吃什么', 'importance': 5},
                {'id': 'cold', 'title': '冷门但重要', 'body': '冷门但重要的共同经历', 'importance': 8}]
        for index in range(30):
            rows.append({'id': 'noise%d' % index, 'title': '无关%d' % index,
                         'body': '完全无关的日常记录 %d' % index, 'importance': 5})
        w = self.make(rows)
        w.apply_delivery({'request_id': 'r1', 'injected_ids': ['hot']})
        settings = {'weight_vector': 0.65, 'weight_keyword': 0.35, 'candidate_k': 8,
                    'stopwords_enabled': False, 'activation_ranking_enabled': True,
                    'activation_weight': 0.2, 'activation_half_life_days': 30,
                    'activation_decay_enabled': True}
        index = brain_retrieval.get_index(self.db, settings)
        results = index.search('冷门但重要的共同经历', limit=8, query_vector=[])
        self.assertIn('cold', [item['id'] for item in results])

    def test_first_counted_delivery_fills_first_delivery_at(self):
        w = self.make([{'id': 'm1'}])
        w.apply_delivery({'request_id': 'r0', 'injected_ids': ['m1'], 'regenerated': True})
        self.assertEqual(w.activation_map(['m1'])['m1']['deliveries'], 0)
        w.apply_delivery({'request_id': 'r1', 'injected_ids': ['m1']})
        info = w.activation_map(['m1'])['m1']
        self.assertEqual(info['deliveries'], 1)
        self.assertTrue(info['first_delivery_at'])

    def test_pruned_same_day_evidence_does_not_double_count_the_day(self):
        w = self.make([{'id': 'm1'}])
        w.apply_delivery({'request_id': 'r1', 'injected_ids': ['m1']})
        conn = sqlite3.connect(str(self.db))
        try:
            conn.execute('DELETE FROM memory_delivery_events')
            conn.commit()
        finally:
            conn.close()
        w.apply_delivery({'request_id': 'r2', 'injected_ids': ['m1']})
        self.assertEqual(w.activation_map(['m1'])['m1']['distinct_days'], 1)
        self.clock.value = '2026-11-02T10:00:00+0800'
        w.apply_delivery({'request_id': 'r3', 'injected_ids': ['m1']})
        self.assertEqual(w.activation_map(['m1'])['m1']['distinct_days'], 2)

    def test_external_evidence_must_belong_to_the_target_memory(self):
        w = self.make([{'id': 'm1', 'event_id': 'e1', 'event_title': '自己的经历'},
                       {'id': 'm2', 'event_id': 'e2', 'event_title': '别人的经历'}])
        foreign = w.propose('m1', proposed_importance=6, confidence=0.9,
                            evidence=[{'kind': 'event', 'ref': 'e2'}], auto_apply=True)
        self.assertEqual(foreign['reason'], 'no_evidence')
        own = w.propose('m1', proposed_importance=6, confidence=0.9,
                        evidence=[{'kind': 'event', 'ref': 'e1'}], auto_apply=True)
        self.assertTrue(own['ok'], own)
        self.assertTrue(own['applied'])

    def test_activation_disabled_keeps_queued_delivery_until_reenabled(self):
        from unittest.mock import patch
        w = self.make([{'id': 'm1'}])
        with patch.object(w, '_write_connection', side_effect=RuntimeError('locked')):
            out = w.apply_delivery({'request_id': 'r1', 'injected_ids': ['m1']})
        self.assertEqual(out['status'], 'error')
        self.assertEqual(len(w._load_retries()), 1)
        w.update_settings({'activation_enabled': False})
        self.assertEqual(w.drain_retries(), 0)
        self.assertEqual(len(w._load_retries()), 1)
        w.update_settings({'activation_enabled': True})
        self.assertEqual(w.drain_retries(), 1)
        self.assertEqual(w.activation_map(['m1'])['m1']['deliveries'], 1)

    def test_manual_force_does_not_repeat_unchanged_evidence(self):
        w = self.make([{'id': 'm1', 'meaning': '有意义', 'event_id': 'e1',
                        'event_title': '共同经历'}],
                      relationship=[{'id': 'rel1', 'status': 'confirmed', 'text': '关系',
                                     'sources': ['event:e1']}])
        w.apply_delivery({'request_id': 'r1', 'injected_ids': ['m1']})
        self.assertEqual(w.activation_map(['m1'])['m1']['deliveries'], 1)
        first = w.proposals(memory_id='m1', status='applied')
        self.assertEqual(len(first), 1)
        again = w.reevaluate('m1')
        self.assertFalse(again['ok'])
        self.assertEqual(again['reason'], 'no_change')
        conn = sqlite3.connect(str(self.db))
        try:
            self.assertEqual(conn.execute(
                "SELECT importance FROM memories WHERE id='m1'").fetchone()[0],
                first[0]['proposed_importance'])
        finally:
            conn.close()

class ApiSurfaceTests(_Base):
    def _hub(self, weights):
        from types import SimpleNamespace as NS
        store = Store(self.root)
        memory = NS(weights=weights, store=store,
                    brain_authority=NS(db_path=self.db))
        cfg = SimpleNamespace(id='peter', profile={'file': 'learned-context.json'},
                              memory={'recall': {}})
        chat = NS(cfg=cfg, store=store, memory=memory)
        return NS(name='Peter', ready=True, stopped=False, data_root=self.root, chat=chat,
                  memory_tools=None)

    def test_library_and_controls_expose_activation_and_versioned_review(self):
        from veil.brain import brain_p4_api
        w = self.make(
            [{'id': 'm1', 'title': '一起讨论记忆', 'importance': 5,
              'why_remembered': '第一次认真谈', 'meaning': '关于如何记得她',
              'event_id': 'e1', 'event_title': '记忆长谈'}],
            relationship=[{'id': 'rel1', 'status': 'confirmed', 'text': '她希望我记得',
                           'sources': ['event:e1']}])
        self.clock.value = '2026-11-01T10:00:00+0800'
        w.apply_delivery({'request_id': 'r1', 'injected_ids': ['m1'], 'session_key': 'conv:1'})
        hub = self._hub(w)
        lib = brain_p4_api.library(hub, {})
        self.assertEqual(len(lib['items']), 1)
        item = lib['items'][0]
        self.assertEqual(item['activation']['deliveries'], 1)
        self.assertGreater(item['activation']['display'], 0)
        self.assertEqual(item['activation']['protected'], False)
        self.assertIsNotNone(item['reeval']['latest'])
        self.assertEqual(item['reeval']['latest']['status'], 'applied')

        status = brain_p4_api.memory_weights_status(hub)
        self.assertTrue(status['available'])
        self.assertEqual(status['guards']['never_delete_or_hide'], True)
        saved = brain_p4_api.set_memory_weights(hub, {'ranking_enabled': True,
                                                      'ranking_weight': 0.06})
        self.assertTrue(saved['ok'])
        self.assertTrue(saved['status']['settings']['ranking_enabled'])

        applied = brain_p4_api.memory_proposals(hub, status='applied')
        self.assertEqual(len(applied['items']), 1)
        decision = brain_p4_api.decide_memory_proposal(
            hub, applied['items'][0]['proposal_id'], 'revert')
        self.assertTrue(decision['ok'], decision)
        self.assertEqual(decision['importance'], 5)
        again = brain_p4_api.reevaluate_memory(hub, 'm1')
        self.assertFalse(again['ok'])
        self.assertEqual(again['reason'], 'no_change', 'reverted evidence must not be re-applied')
        conn = sqlite3.connect(str(self.db))
        try:
            self.assertEqual(conn.execute(
                "SELECT importance FROM memories WHERE id='m1'").fetchone()[0], 5)
        finally:
            conn.close()

    def test_get_routes_wire_weights_and_proposals(self):
        from unittest.mock import Mock
        from veil.server import web_agent
        w = self.make([{'id': 'm1', 'title': '旧事', 'importance': 5,
                        'meaning': '有意义', 'event_id': 'e1', 'event_title': '共同经历'}],
                      relationship=[{'id': 'rel1', 'status': 'confirmed', 'text': '关系',
                                     'sources': ['event:e1']}])
        w.apply_delivery({'request_id': 'r1', 'injected_ids': ['m1']})
        hub = self._hub(w)
        handler = web_agent.make_handler(hub, 'tok-test')
        req = handler.__new__(handler)
        req.server = SimpleNamespace(server_address=('127.0.0.1', 65100))
        req.headers = {'Host': '127.0.0.1:65100', 'X-Token': 'tok-test'}
        req._send = Mock()
        req.path = '/api/brain/memory-weights'
        req.do_GET()
        status, payload = req._send.call_args.args
        self.assertEqual(200, status)
        self.assertTrue(payload['available'])
        self.assertIn('persistence_path', payload)
        req.path = '/api/brain/memory-proposals?status=applied'
        req.do_GET()
        status, payload = req._send.call_args.args
        self.assertEqual(200, status)
        self.assertEqual(payload['source'], 'brain.db')
        self.assertEqual(len(payload['items']), 1)
        self.assertEqual(payload['items'][0]['status'], 'applied')

if __name__ == '__main__':
    unittest.main()