"""P6-S5 unit & integration tests: stopwords/K, situational judge, contextual association,
continuity & delivery/rollback contract, reranker score-gate/staleness contract, and config registry.
"""
from __future__ import annotations

import array
import json
import sqlite3
import tempfile
import unittest
from concurrent.futures import Future
from pathlib import Path
from types import SimpleNamespace

from veil.brain.recall import brain_retrieval, context_signal, natural_association, recall_rotation
from veil.brain.recall.reranker_contract import (
    AsyncRerankEnvelope,
    filter_by_score_gate,
    load_settings as load_rerank_settings,
    topic_fingerprint,
    validate_async_result,
)
from veil.core.config_registry import recall_config_overview


def _make_brain_db(path: Path, rows, *, model='BAAI/bge-m3'):
    conn = sqlite3.connect(str(path))
    try:
        conn.execute('CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT)')
        conn.execute("INSERT INTO meta(key, value) VALUES ('embedding_model', ?)", (model,))
        conn.execute(
            'CREATE TABLE memories (id TEXT PRIMARY KEY, title TEXT, body TEXT, created TEXT, '
            'importance INTEGER, pinned INTEGER DEFAULT 0, bucket TEXT DEFAULT "dynamic", '
            'type TEXT DEFAULT "experience", is_tombstone INTEGER DEFAULT 0, is_deleted INTEGER DEFAULT 0, '
            'source_tool TEXT DEFAULT "hold", raw_markdown TEXT DEFAULT "")'
        )
        conn.execute('CREATE TABLE vectors (memory_id TEXT PRIMARY KEY, dim INTEGER, vector BLOB)')
        conn.execute('CREATE TABLE delete_requests (memory_id TEXT, status TEXT)')
        for item in rows:
            conn.execute(
                'INSERT INTO memories(id, title, body, created, importance, pinned, bucket, type, '
                'is_tombstone, is_deleted, raw_markdown) VALUES (?,?,?,?,?,?,?,?,?,?,?)',
                (
                    item['id'], item['title'], item['body'], item.get('created', '2026-11-01T10:00:00+08:00'),
                    item.get('importance', 5), int(item.get('pinned', 0)),
                    item.get('bucket', 'dynamic'), item.get('type', 'experience'),
                    int(item.get('is_tombstone', 0)), int(item.get('is_deleted', 0)),
                    f"---\nid: {item['id']}\ntitle: {item['title']}\n---\n{item['body']}",
                ),
            )
            vec = item.get('vector')
            if vec:
                blob = array.array('f', vec).tobytes()
                conn.execute('INSERT INTO vectors(memory_id, dim, vector) VALUES (?,?,?)',
                             (item['id'], len(vec), blob))
        conn.commit()
    finally:
        conn.close()


class TestStopwordsAndCandidateK(unittest.TestCase):
    def test_protected_terms_never_in_stopwords(self):
        overlap = brain_retrieval.CHINESE_STOPWORDS & brain_retrieval.PROTECTED_TERMS
        self.assertEqual(overlap, frozenset(), f'Protected terms leaked into stopwords: {overlap}')

    def test_stopwords_default_off_and_only_affect_bm25(self):
        cfg = brain_retrieval.load_settings({})
        self.assertFalse(cfg['stopwords_enabled'])
        self.assertEqual(cfg['candidate_k'], 8)
        tokens_off = brain_retrieval.tokenize('今天我真的没有忘记我们的约定', stopwords_enabled=False)
        tokens_on = brain_retrieval.tokenize('今天我真的没有忘记我们的约定', stopwords_enabled=True)
        # Protected date/negation/agreement words stay in both; filler '真的'/'的' removed when on.
        for must_keep in ('今天', '没有', '约定'):
            self.assertIn(must_keep, tokens_on)
        self.assertIn('真的', tokens_off)
        self.assertNotIn('真的', tokens_on)

    def test_get_index_separates_stopwords_cache_keys(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / 'brain.db'
            _make_brain_db(db, [
                {'id': 'm1', 'title': '海边散步', 'body': '那天晚上一起看星星聊天', 'vector': [1.0, 0.0]},
            ])
            s_off = brain_retrieval.load_settings({'stopwords_enabled': False, 'candidate_k': 8})
            s_on = brain_retrieval.load_settings({'stopwords_enabled': True, 'candidate_k': 12})
            idx_off = brain_retrieval.get_index(db, s_off)
            idx_on = brain_retrieval.get_index(db, s_on)
            self.assertIsNot(idx_off, idx_on)
            self.assertFalse(idx_off.stopwords_enabled)
            self.assertTrue(idx_on.stopwords_enabled)
            diag = {}
            idx_on.search('海边星星', limit=12, query_vector=[1.0, 0.0], diagnostics=diag)
            self.assertTrue(diag['stopwords_enabled'])
            self.assertEqual(diag['candidate_k'], 12)


class TestContextSignalAndAssociation(unittest.TestCase):
    def test_context_signal_distinguishes_work_task_vs_work_emotion(self):
        tech = context_signal.analyze('帮我排查一下 pytest 报错 Traceback KeyError')
        self.assertEqual(tech['mode'], 'technical_task')
        self.assertTrue(tech['is_work_task'])
        self.assertFalse(tech['allow_association'])

        work_emo = context_signal.analyze('今天加班开会好累，真的委屈想吐槽')
        self.assertEqual(work_emo['mode'], 'work_emotion')
        self.assertFalse(work_emo['is_work_task'])
        self.assertTrue(work_emo['allow_association'])

        # F2: concrete technical task co-occurring with emotion must NOT be misclassified as casual/work_emotion
        emo_tech = context_signal.analyze('好烦好累，先帮我修一下这个 Traceback KeyError 报错')
        self.assertEqual(emo_tech['mode'], 'technical_task')
        self.assertTrue(emo_tech['is_work_task'])
        self.assertTrue(emo_tech['has_emotion_signal'])
        self.assertFalse(emo_tech['allow_association'])

        # F2: long daily-life sharing must NOT be blocked by character length
        long_life = context_signal.analyze('今天下午去楼下散步的时候看到一只特别胖的三花猫趴在台阶上晒太阳，' * 3)
        self.assertEqual(long_life['mode'], 'casual_chat')
        self.assertFalse(long_life['is_work_task'])
        self.assertTrue(long_life['allow_association'])

        # F3: followup flag must not mask concrete technical task intent
        shift_sig = context_signal.analyze('然后帮我查一下 pytest 报错日志', followup=True)
        self.assertTrue(shift_sig['is_work_task'])

        sticker = context_signal.analyze('[表情包：邪恶比格]')
        self.assertEqual(sticker['mode'], 'short_ack')
        self.assertFalse(sticker['allow_association'])

    def test_contextual_association_respects_similarity_gate_and_allows_tech_shared_memory(self):
        candidates = [
            ('m_high_irrelevant', '[bucket_id:m_high_irrelevant] 很重要的旧事\n完全无关的话题'),
            ('m_work_shared', '[bucket_id:m_work_shared] 一起熬夜修代码\n那晚一起修 CI 报错后来吃夜宵'),
        ]
        metas = {
            'm_high_irrelevant': {'importance': 10, 'pinned': 'true'},
            'm_work_shared': {'importance': 7, 'pinned': 'false'},
        }
        sims = {'m_high_irrelevant': 0.12, 'm_work_shared': 0.62}
        cfg = {'association_enabled': True, 'association_contextual_enabled': True,
               'association_min_similarity': 0.34, 'association_cooldown_turns': 8}
        # Work-induced emotion (without explicit debug command) allows association;
        # high-importance irrelevant item CANNOT bypass similarity gate.
        res = natural_association.choose(
            '今天加班开会好累啊想吐槽', candidates, metas, {}, 10, set(), cfg, similarities=sims)
        self.assertEqual(res['key'], 'm_work_shared')
        self.assertEqual(res['mode'], 'contextual')
        self.assertAlmostEqual(res['similarity'], 0.62, places=2)

        # Concrete technical task with emotion is still suppressed for unrelated association.
        res_emo_cmd = natural_association.choose(
            '好烦，帮我修一下这个 CI 报错日志', candidates, metas, {}, 11, set(), cfg, similarities=sims)
        self.assertEqual(res_emo_cmd['key'], '')
        self.assertEqual(res_emo_cmd['reason'], 'work_task_suppressed')

        # Pure technical task is strictly suppressed (0 intrusion).
        res_task = natural_association.choose(
            '帮我查一下 CI 部署报错日志', candidates, metas, {}, 11, set(), cfg, similarities=sims)
        self.assertEqual(res_task['key'], '')
        self.assertEqual(res_task['reason'], 'work_task_suppressed')

        # Without similarity signal, never falls back to blind rotation.
        res_no_sim = natural_association.choose(
            '今晚风好舒服', candidates, metas, {}, 12, set(), cfg, similarities={})
        self.assertEqual(res_no_sim['key'], '')
        self.assertEqual(res_no_sim['reason'], 'no_similarity_signal')


class TestRerankerContract(unittest.TestCase):
    def test_score_gate_and_async_staleness_validation(self):
        cfg = load_rerank_settings({})
        self.assertFalse(cfg['enabled'])
        self.assertEqual(cfg['mode'], 'disabled')

        kept, dropped, meta = filter_by_score_gate(
            ['m1', 'm2', 'm3'], {'m1': 0.72, 'm2': 0.28, 'm3': 0.09}, threshold=0.25, top_n=8)
        self.assertEqual(kept, ['m1', 'm2'])
        self.assertEqual(len(dropped), 1)
        self.assertEqual(meta['gated_out_count'], 1)

        env = AsyncRerankEnvelope(
            request_id='req-1',
            topic_hash=topic_fingerprint('上次看星星'),
            db_signature=(100, 200),
            ranked_ids=('m1', 'm2', 'm3'),
            scores={'m1': 0.8, 'm2': 0.6, 'm3': 0.5},
            memory_versions={'m1': 1, 'm2': 2, 'm3': 1},
        )
        # Topic shifted -> rejected
        out, info = validate_async_result(
            env, current_request_id='req-1', current_topic_hash=topic_fingerprint('今天吃什么'),
            current_db_signature=(100, 200))
        self.assertEqual(out, [])
        self.assertEqual(info['reason'], 'topic_shifted')

        # Matching envelope filters version-changed and hidden items
        out2, info2 = validate_async_result(
            env, current_request_id='req-1', current_topic_hash=topic_fingerprint('上次看星星'),
            current_db_signature=(100, 200),
            is_visible_fn=lambda mid: mid != 'm3',
            current_versions={'m1': 1, 'm2': 3, 'm3': 1})
        self.assertEqual(out2, ['m1'])
        self.assertTrue(info2['accepted'])
        self.assertEqual(len(info2['dropped']), 2)


class TestDeliveryContractAndConfigRegistry(unittest.TestCase):
    def test_recall_config_overview_projects_all_switches(self):
        view = recall_config_overview(
            role_id='peter',
            memory_cfg={'retrieval': {'stopwords_enabled': False, 'candidate_k': 8},
                        'recall': {'association_enabled': True, 'association_contextual_enabled': False}},
            judge_enabled=True,
            judge_timeout=4.0,
        )
        keys = {e['key'] for e in view['entries']}
        self.assertIn('memory.retrieval.stopwords_enabled', keys)
        self.assertIn('memory.retrieval.candidate_k', keys)
        self.assertIn('memory.recall.association_contextual_enabled', keys)
        for entry in view['entries']:
            if entry['key'] == 'memory.retrieval.stopwords_enabled':
                self.assertEqual(entry['effective']['value'], False)
            if entry['key'] == 'memory.recall.association_contextual_enabled':
                self.assertEqual(entry['effective']['value'], False)

    def test_finish_recall_delivery_and_rollback_contract(self):
        from veil.brain.memory_agent import Memory
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / 'peter').mkdir()
            db = root / 'peter' / 'brain.db'
            _make_brain_db(db, [
                {'id': 'm1', 'title': '旧事A', 'body': '正文很长' * 80, 'importance': 8, 'vector': [1.0, 0.0]},
                {'id': 'm2', 'title': '旧事B', 'body': '另一段生活经历', 'importance': 5, 'vector': [0.8, 0.2]},
            ])
            mem = object.__new__(Memory)
            mem.mutex = __import__('threading').RLock()
            mem.rotation = recall_rotation.Cooldown()
            mem.rotation_path = 'peter/recall-rotation.json'
            mem.store = SimpleNamespace(root=root, replace=lambda *a, **k: None)
            mem.cfg = SimpleNamespace(id='peter', memory={'model': 'BAAI/bge-m3'})
            mem.budget = SimpleNamespace(clock=lambda: '2026-11-27T15:00:00+08:00')
            mem.last_recall_debug = {'ombre': ['m1'], 'judge': {'picked': ['m1']}}
            mem.last_recall_skipped = {'cooldown': 0, 'same_event': 0, 'stage_cap': 0, 'budget': 0}
            mem.last_injected_refs = []
            mem._previous_injected_refs = []
            mem.last_recall_marks = None
            mem.last_association_marks = None
            mem.surfaced_ids = {}
            mem.delivery_listeners = []
            events = []
            mem.delivery_listeners.append(lambda ev: events.append(ev))
            mem._pending_recall = {
                'include_opening': False,
                'association_key': 'm2',
                'blocks': [
                    {'id': 'm1', 'group': 'recall', 'text': '[bucket_id:m1]\n正文', 'priority': 10,
                     'source': 'search', 'event_id': '', 'pointer': '', 'kind': 'search'},
                    {'id': 'm2', 'group': 'recall', 'text': '[bucket_id:m2]\n联想正文', 'priority': 900,
                     'source': 'association', 'event_id': '', 'pointer': '', 'kind': 'association'},
                ],
            }
            # 1) Unbudgeted finish_recall injects both, marks association, then confirm_delivery fires listener
            mem.finish_recall()
            self.assertEqual(mem.rotation.association['key'], 'm2')
            self.assertFalse(mem.last_recall_debug['lifecycle']['delivered'])
            ev = mem.confirm_delivery('req-ok')
            self.assertIsNotNone(ev)
            self.assertTrue(mem.last_recall_debug['lifecycle']['delivered'])
            self.assertIsNone(ev['effective_used'])
            self.assertEqual(len(events), 1)
            self.assertEqual(events[0]['association_ids'], ['m2'])

            # 2) F7: First-time association failure (prior state None/empty) & mismatched request_id guard
            mem_fresh = object.__new__(Memory)
            mem_fresh.mutex = __import__('threading').RLock()
            mem_fresh.rotation = recall_rotation.Cooldown()
            mem_fresh.rotation_path = 'peter/recall-rotation.json'
            mem_fresh.store = SimpleNamespace(root=root, replace=lambda *a, **k: None)
            mem_fresh.cfg = SimpleNamespace(id='peter', memory={'model': 'BAAI/bge-m3'})
            mem_fresh.budget = SimpleNamespace(clock=lambda: '2026-11-27T15:00:00+08:00')
            mem_fresh.last_recall_debug = {'ombre': [], 'judge': {'picked': []}}
            mem_fresh.last_recall_skipped = {'cooldown': 0, 'same_event': 0, 'stage_cap': 0, 'budget': 0}
            mem_fresh.last_injected_refs = []
            mem_fresh._previous_injected_refs = []
            mem_fresh.last_recall_marks = None
            mem_fresh.last_association_marks = None
            mem_fresh._has_association_rollback = False
            mem_fresh._pending_recall_request_id = ''
            mem_fresh.surfaced_ids = {}
            mem_fresh.delivery_listeners = [lambda ev: events.append(ev)]
            # Simulate a rotation whose previous_association returns None on first turn
            mem_fresh.rotation.previous_association = lambda: None
            mem_fresh._pending_recall = {
                'include_opening': False,
                'association_key': 'm2',
                'blocks': [
                    {'id': 'm2', 'group': 'recall', 'text': '[bucket_id:m2]\n联想正文', 'priority': 900,
                     'source': 'association', 'event_id': '', 'pointer': '', 'kind': 'association'},
                ],
            }
            mem_fresh.finish_recall(request_id='req-A')
            self.assertEqual(mem_fresh.rotation.association['key'], 'm2')
            # Mismatched request_id must be rejected by both confirm_delivery and rollback_recall
            self.assertIsNone(mem_fresh.confirm_delivery('req-B'))
            self.assertFalse(mem_fresh.rollback_recall('req-B'))
            self.assertEqual(mem_fresh.rotation.association['key'], 'm2')
            # Matching request_id rolls back cleanly even though previous_association was None
            self.assertTrue(mem_fresh.rollback_recall('req-A'))
            self.assertEqual(mem_fresh.rotation.association['key'], '')
            self.assertEqual(mem_fresh.rotation.association['repeat'], 0)
            self.assertEqual(len(events), 1)

            # 3) F6: Bounded multi-source pool caches lightweight metadata only (no full-body cache)
            pool_blocks, pool_meta = mem._association_pool(limit=8)
            self.assertEqual(pool_meta['source'], 'bounded_multi_source')
            self.assertEqual(len(pool_blocks), 2)
            for cached_row in mem._assoc_pool_cache['rows']:
                self.assertNotIn('body', cached_row)

    def test_association_toggles_persist_apply_next_turn_and_survive_restart(self):
        from veil.brain.brain_p4_api import overview as brain_overview, toggle_layer
        from veil.brain.memory_agent import Memory
        from veil.core.veil_agent import Store

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / 'peter').mkdir()
            store = Store(root)

            def make_mem():
                mem = object.__new__(Memory)
                mem.mutex = __import__('threading').RLock()
                mem.store = store
                mem.cfg = SimpleNamespace(
                    id='peter',
                    memory={'recall': {'association_enabled': True, 'association_contextual_enabled': False}},
                )
                mem.recall_settings = recall_rotation.settings(mem.cfg.memory)
                mem.retrieval_settings = brain_retrieval.load_settings({})
                mem.judge = None
                return mem

            mem1 = make_mem()
            hub1 = SimpleNamespace(
                data_root=root,
                chat=SimpleNamespace(cfg=mem1.cfg, store=store, memory=mem1, experience=None, profile=None),
            )
            # Default: 6-layer grid intact; association total on, contextual off -> effective_mode == legacy
            home0 = brain_overview(hub1)
            self.assertEqual(len(home0['layers']), 6)
            assoc0 = home0['recall_association']
            ctrls0 = {x['id']: x for x in assoc0['controls']}
            self.assertTrue(assoc0['enabled'])
            self.assertFalse(assoc0['contextual_enabled'])
            self.assertEqual(assoc0['effective_mode'], 'legacy')
            self.assertTrue(ctrls0['association']['enabled'])
            self.assertFalse(ctrls0['association_contextual']['enabled'])

            # Toggle contextual on via existing /api/brain/layer-toggle handler -> persists & applies immediately
            r1 = toggle_layer(hub1, 'association_contextual', True)
            self.assertTrue(r1['ok'])
            self.assertTrue(r1['persisted'])
            self.assertEqual(r1['effective_mode'], 'contextual')
            self.assertTrue(mem1.effective_recall_settings()['association_contextual_enabled'])

            # Simulate restart (fresh Memory instance reading same store) -> retained!
            mem2 = make_mem()
            self.assertTrue(mem2.effective_recall_settings()['association_contextual_enabled'])
            self.assertEqual(mem2.association_status()['effective_mode'], 'contextual')

            # Toggle contextual off -> deterministically restores legacy mode
            hub2 = SimpleNamespace(
                data_root=root,
                chat=SimpleNamespace(cfg=mem2.cfg, store=store, memory=mem2, experience=None, profile=None),
            )
            r2 = toggle_layer(hub2, 'association_contextual', False)
            self.assertTrue(r2['ok'])
            self.assertEqual(r2['effective_mode'], 'legacy')
            self.assertFalse(mem2.effective_recall_settings()['association_contextual_enabled'])

            # Toggle master association off -> disables both
            r3 = toggle_layer(hub2, 'association', False)
            self.assertTrue(r3['ok'])
            self.assertEqual(r3['effective_mode'], 'disabled')
            self.assertFalse(make_mem().effective_recall_settings()['association_enabled'])


if __name__ == '__main__':
    unittest.main()
