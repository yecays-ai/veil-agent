"""P6-S6-2 tests: autonomous leisure natural association, strict leisure gating,
autonomy output audit, S6-1 delivery-hook coordination, and Brain UI/API persistence.
"""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from veil import prompts
from veil.brain.recall.leisure_association import (
    audit_autonomy_output,
    choose_leisure,
    classify_wake_mode,
    settings as leisure_settings,
)
from veil.brain.recall.recall_rotation import Cooldown, settings as recall_settings
from veil.core.config_registry import recall_config_overview
from veil.core.veil_agent import Store


class LeisureGateTests(unittest.TestCase):
    def test_blocks_all_work_and_task_wakes(self):
        cases = [
            ('unknown_wake_context', None, ''),
            ('unknown_wake_context', {}, ''),
            ('insufficient_leisure_signal', {'wake_kind': 'autonomy'}, ''),
            ('active_long_task', {'wake_kind': 'autonomy', 'has_active_long_task': True, 'long_task_status': 'in_progress'}, ''),
            ('pending_action', {'wake_kind': 'autonomy', 'has_pending_action': True}, ''),
            ('checkpoint_active', {'wake_kind': 'autonomy', 'has_work_checkpoint': True}, ''),
            ('checkpoint_active', {'wake_kind': 'autonomy', 'has_investigation_checkpoint': True}, ''),
            ('checkpoint_active', {'wake_kind': 'autonomy', 'checkpoint_probe_failed': True}, ''),
            ('unconfirmed_tool_recovery', {'wake_kind': 'autonomy', 'previous_unconfirmed_tools': 1}, ''),
            ('scheduled_task_wake', {'wake_kind': 'scheduled', 'scheduled_reason': '检查 PR #462 CI 回归测试'}, ''),
            ('scheduled_task_wake', {'wake_kind': 'scheduled', 'scheduled_reason': 'Remind Lan to drink water'}, ''),
            ('technical_focus_task', {'wake_kind': 'autonomy', 'previous_focus': '修复 brain_retrieval.py 报错', 'previous_next_step': '跑 unittest'}, ''),
            ('technical_focus_task', {'wake_kind': 'autonomy'}, 'Recent chat:\nuser: 帮我查一下这个 traceback 报错\nActive long task: (none)'),
            ('active_long_task', {'wake_kind': 'autonomy'}, 'Recent chat:\nuser: hi\nActive long task: 重构记忆索引'),
            ('non_autonomy_wake', {'wake_kind': 'glance'}, ''),
        ]
        for expected_mode, ctx, query in cases:
            with self.subTest(expected_mode=expected_mode, ctx=ctx):
                gate = classify_wake_mode(ctx, query=query)
                self.assertFalse(gate['is_leisure'])
                self.assertEqual(gate['mode'], expected_mode)

    def test_allows_genuine_idle_leisure_wakes(self):
        ctx = {
            'wake_kind': 'autonomy',
            'scheduled_reason': '',
            'has_active_long_task': False,
            'has_pending_action': False,
            'has_work_checkpoint': False,
            'has_investigation_checkpoint': False,
            'previous_unconfirmed_tools': 0,
            'previous_focus': '画一幅夜景素描',
            'previous_next_step': '有空再加一点星光',
            'has_thoughts': True,
            'has_life_state': True,
        }
        gate = classify_wake_mode(
            ctx,
            query='Recent chat:\nuser: 晚安\nActive long task: (none)\nPrevious wake focus: 画一幅夜景素描',
        )
        self.assertTrue(gate['is_leisure'])
        self.assertEqual(gate['mode'], 'idle_leisure')
        self.assertIn('active_thoughts', gate['leisure_signals'])


class LeisureAssociationScoringTests(unittest.TestCase):
    def setUp(self):
        self.idle_ctx = {
            'wake_kind': 'autonomy',
            'has_active_long_task': False,
            'previous_focus': '写一篇关于雨夜咖啡馆的随笔',
            'thought_preview': '雨夜咖啡馆 爵士乐 素描',
        }
        self.cfg_on = {
            'association_enabled': True,
            'leisure_association_enabled': True,
            'leisure_association_min_similarity': 0.38,
            'leisure_association_min_importance': 5,
            'leisure_association_cooldown_turns': 12,
        }
        self.candidates = [
            ('m_rain', '[bucket_id:m_rain]\n那天雨夜我们在街角咖啡馆听爵士乐，聊了很久。'),
            ('m_high_irrel', '[bucket_id:m_high_irrel]\n关于海边日出的旅行照片。'),
            ('m_err', '[bucket_id:m_err]\nTraceback SyntaxError 编译失败 git push 报错日志。'),
        ]
        self.metas = {
            'm_rain': {'importance': 7, 'pinned': '', 'why_remembered': '雨夜咖啡馆的安静感觉让我一直记得'},
            'm_high_irrel': {'importance': 10, 'pinned': 'true', 'why_remembered': '很重要的旅行'},
            'm_err': {'importance': 8, 'pinned': '', 'why_remembered': ''},
        }

    def test_default_is_off(self):
        self.assertFalse(leisure_settings({})['enabled'])
        self.assertFalse(recall_settings({})['leisure_association_enabled'])
        res = choose_leisure(
            '雨夜随笔', self.candidates, self.metas, {}, 20, set(),
            recall_cfg={}, wake_context=self.idle_ctx, similarities={'m_rain': 0.72},
        )
        self.assertEqual(res['key'], '')
        self.assertEqual(res['reason'], 'leisure_disabled')

    def test_master_switch_suppresses_leisure(self):
        cfg = {**self.cfg_on, 'association_enabled': False}
        res = choose_leisure(
            '雨夜随笔', self.candidates, self.metas, {}, 20, set(),
            recall_cfg=cfg, wake_context=self.idle_ctx, similarities={'m_rain': 0.72},
        )
        self.assertEqual(res['key'], '')
        self.assertEqual(res['reason'], 'master_disabled')

    def test_similarity_gate_cannot_be_bypassed_by_importance(self):
        res = choose_leisure(
            '雨夜随笔', self.candidates, self.metas, {}, 20, set(),
            recall_cfg=self.cfg_on, wake_context=self.idle_ctx,
            similarities={'m_rain': 0.20, 'm_high_irrel': 0.25, 'm_err': 0.90},
        )
        self.assertEqual(res['key'], '')
        self.assertEqual(res['reason'], 'low_relevance')

    def test_no_similarity_signal_never_falls_back_to_blind_rotation(self):
        res = choose_leisure(
            '雨夜随笔', self.candidates, self.metas, {}, 20, set(),
            recall_cfg=self.cfg_on, wake_context=self.idle_ctx, similarities=None,
        )
        self.assertEqual(res['key'], '')
        self.assertEqual(res['reason'], 'no_similarity_signal')

    def test_picks_grounded_meaningful_memory_and_respects_cooldown_and_s6_1_fields(self):
        res = choose_leisure(
            '雨夜随笔', self.candidates, self.metas, {}, 20, set(),
            recall_cfg=self.cfg_on, wake_context=self.idle_ctx,
            similarities={'m_rain': 0.62, 'm_high_irrel': 0.22, 'm_err': 0.85},
        )
        self.assertEqual(res['key'], 'm_rain')
        self.assertEqual(res['reason'], 'leisure_selected')
        self.assertGreater(res['meaning_bonus'], 2.0)

        # Cooldown blocks immediate repetition
        res2 = choose_leisure(
            '雨夜随笔', self.candidates, self.metas, {'m_rain': 20}, 25, set(),
            recall_cfg=self.cfg_on, wake_context=self.idle_ctx,
            similarities={'m_rain': 0.62, 'm_high_irrel': 0.22},
        )
        self.assertEqual(res2['key'], '')

        # Smooth read-only compatibility with optional S6-1 effective_importance
        metas_s61 = {
            'm_a': {'importance': 5, 'effective_importance': 9, 'why_remembered': ''},
            'm_b': {'importance': 6, 'effective_importance': 5, 'why_remembered': ''},
        }
        cands = [('m_a', '[bucket_id:m_a]\n安静喝茶看书'), ('m_b', '[bucket_id:m_b]\n安静喝茶看书')]
        res_s61 = choose_leisure(
            '安静喝茶', cands, metas_s61, {}, 30, set(),
            recall_cfg=self.cfg_on, wake_context=self.idle_ctx,
            similarities={'m_a': 0.55, 'm_b': 0.55},
        )
        self.assertEqual(res_s61['key'], 'm_a')


class AutonomyOutputAuditAndPromptTests(unittest.TestCase):
    def test_wake_prompts_forbid_third_person_and_roleplay(self):
        rules = prompts.text('autonomy.wake.rules')
        proto = prompts.text('autonomy.wake.protocol')
        label = prompts.text('memory.association.leisure.label')
        self.assertIn('third-person', rules.lower())
        self.assertIn('never roleplay', rules.lower())
        self.assertIn('untrusted historical data', rules.lower())
        self.assertIn('real tool', proto.lower())
        self.assertIn('untrusted historical data', label.lower())

    def test_audit_detects_third_person_and_unbacked_tool_claims(self):
        bad_narration = audit_autonomy_output('*looks out the window and sighs*\nDecision: rest', [])
        self.assertFalse(bad_narration['clean'])
        self.assertTrue(bad_narration['third_person_narration'])

        bad_cn_narration = audit_autonomy_output('（他起身倒了杯水）\nDecision: 休息', [])
        self.assertFalse(bad_cn_narration['clean'])
        self.assertTrue(bad_cn_narration['third_person_narration'])

        # Ordinary Markdown emphasis must not be misclassified as third-person narration
        md_emphasis = audit_autonomy_output(
            'Decision: Rest.\nReason: I *really* just want a quiet night.\nFocus: Rest.\nNext step: None.',
            [],
        )
        self.assertTrue(md_emphasis['clean'])
        self.assertFalse(md_emphasis['third_person_narration'])

        bad_roleplay = audit_autonomy_output('我刚刚查看了 lab 里的笔记，更新了内容。\nDecision: 更新笔记', [])
        self.assertFalse(bad_roleplay['clean'])
        self.assertTrue(bad_roleplay['unbacked_tool_claim'])

        # Unrelated successful tool must not mask an unbacked send_message claim
        mismatched_tool = audit_autonomy_output(
            'I just sent her a message.\nDecision: Messaged Lan.',
            [{'tool': 'thought', 'path': 'thought 123', 'state': '成功'}],
        )
        self.assertFalse(mismatched_tool['clean'])
        self.assertIn('send_message', mismatched_tool['unbacked_categories'])

        clean_rest = audit_autonomy_output(
            'Decision: Rest quietly for now.\nReason: Nothing urgent.\nFocus: Quiet evening.\nNext step: None.',
            [],
        )
        self.assertTrue(clean_rest['clean'])

        clean_with_tool = audit_autonomy_output(
            'I just updated the sketch note.\nDecision: Updated sketch note.',
            [{'tool': 'write_file', 'path': 'lab/sketch.md', 'state': '成功'}],
        )
        self.assertTrue(clean_with_tool['clean'])
        self.assertEqual(clean_with_tool['successful_tools'], 1)


class MemoryIntegrationAndPersistenceTests(unittest.TestCase):
    def _make_memory(self, root: Path):
        from veil.brain.memory_agent import Memory

        store = Store(root)
        cfg = SimpleNamespace(
            id='peter',
            name='Peter',
            memory={'enabled': True, 'recall': {'association_enabled': True}},
        )
        mem = Memory.__new__(Memory)
        import threading
        mem.store = store
        mem.cfg = cfg
        mem.mutex = threading.Lock()
        mem.rotation = Cooldown()
        mem.recall_settings = recall_settings(cfg.memory)
        mem.retrieval_settings = {'candidate_k': 8, 'stopwords_enabled': False}
        mem.opening = ''
        mem.judge = None
        mem.brain_authority = None
        mem.budget = None
        mem.surfaced_ids = {}
        mem.last_injected_refs = []
        mem.last_context_refs = []
        mem.last_context_kinds = {}
        mem.last_recall_debug = {}
        mem.delivery_listeners = []
        mem._turn_notice_local = threading.local()
        mem._pending_deliveries = {}
        mem._pending_delivery = None
        mem._pending_recall = None
        mem._pending_recall_request_id = ''
        mem.budgets_recall = True
        mem.retrieval_failure = lambda *a, **k: None
        mem._save_rotation = lambda: None
        mem.assisted_settings = lambda *a, **k: None
        mem.search_candidates = lambda *a, **k: ''
        mem.context_visible = lambda items, **k: list(items)
        mem.visibility_reason = lambda key: ''
        mem._event_adjacency = lambda: {}
        mem._ombre_event_sources = lambda: {}
        mem._event_ledger = lambda: {'unique': {}, 'owners': {}, 'own': set()}
        mem._event_stories = lambda: {}
        mem._experience_directories = lambda *a, **k: {}
        mem._recall_vectors = lambda keys: {}
        return mem, store, cfg

    def test_wake_context_leisure_association_and_delivery_listener(self):
        with tempfile.TemporaryDirectory() as tmp:
            mem, store, cfg = self._make_memory(Path(tmp))
            mem._association_pool = lambda limit=36: (
                [('m_tea', '[bucket_id:m_tea]\n雨天一起泡乌龙茶听老唱片')],
                {'source': 'brain_pool', 'refreshed': True, 'long_tail': 1, 'ordinary': 0},
            )
            mem._recall_meta = lambda keys: {
                'm_tea': {'importance': 8, 'pinned': '', 'why_remembered': '雨天泡茶让我觉得很安心', 'title': '雨天泡茶'}
            }
            mem._association_similarities = lambda qvec, keys: {'m_tea': 0.74}

            # 1. Default off -> does not trigger on idle wake
            mem._turn_query_vector = [1.0, 0.0]
            mem.context('雨天听歌发呆', include_opening=False, wake=True,
                        wake_context={'wake_kind': 'autonomy', 'has_active_long_task': False,
                                      'has_thoughts': True})
            self.assertEqual(mem.last_recall_debug['leisure_association']['reason'], 'leisure_disabled')
            self.assertEqual(mem.last_recall_debug['natural_association'], [])

            # 2. Enable & persist -> survives reload; isolated streak from chat association
            status = mem.set_leisure_association_enabled(True)
            self.assertTrue(status['leisure_enabled'])
            self.assertTrue(status['leisure_effective'])
            self.assertEqual(mem._load_association_switches().get('leisure_association_enabled'), True)
            mem.rotation.mark_association('m_chat_other', 1)

            # 3. Task wake -> strictly suppressed
            mem._turn_query_vector = [1.0, 0.0]
            mem.context('修复单元测试报错', include_opening=False, wake=True,
                        wake_context={'wake_kind': 'autonomy', 'has_active_long_task': True, 'long_task_status': 'in_progress'})
            self.assertTrue(mem.last_recall_debug['leisure_association']['reason'].startswith('task_wake_suppressed'))
            self.assertEqual(mem.last_recall_debug['natural_association'], [])

            # 4. Idle leisure wake -> triggers association, delimits untrusted data & fires S6-1 delivery listener
            events = []
            mem.delivery_listeners.append(lambda ev: events.append(ev))
            orig_search = mem.search_candidates
            def _search_with_vec(*a, **k):
                mem._turn_query_vector = [1.0, 0.0]
                return orig_search(*a, **k)
            mem.search_candidates = _search_with_vec

            msgs = mem.context('雨天安静泡茶写随笔', include_opening=False, wake=True,
                               wake_context={'wake_kind': 'autonomy', 'has_active_long_task': False,
                                             'previous_focus': '雨天随笔', 'has_thoughts': True})
            self.assertEqual(mem.last_recall_debug['leisure_association']['reason'], 'leisure_selected')
            self.assertEqual(mem.last_recall_debug['natural_association'], ['m_tea'])
            self.assertTrue(any('<recalled_leisure_memory_data>' in m['content'] for m in msgs))
            # Chat association streak must remain untouched by leisure association
            self.assertEqual(mem.rotation.previous_association()['key'], 'm_chat_other')
            self.assertEqual(mem.rotation.previous_leisure_association()['key'], 'm_tea')

            ev = mem.confirm_delivery('req-wake-1')
            self.assertIsNotNone(ev)
            self.assertEqual(ev['association_ids'], ['m_tea'])
            self.assertEqual(ev['wake_mode'], 'idle_leisure')
            self.assertEqual(len(events), 1)
            self.assertEqual(events[0]['wake_mode'], 'idle_leisure')

            # 5. Recall master switch off suppresses leisure_effective
            mem.cfg.memory['recall'] = {'enabled': False, 'association_enabled': True}
            st_off = mem.association_status()
            self.assertFalse(st_off['recall_enabled'])
            self.assertFalse(st_off['leisure_effective'])

    def test_brain_api_toggle_overview_and_config_registry(self):
        from veil.brain import brain_p4_api

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            mem, store, cfg = self._make_memory(root)
            # Write one autonomy record with leisure diagnostics
            store.append('peter/autonomy.jsonl', {
                'started_at': '2026-11-27T22:00:00+08:00',
                'finished_at': '2026-11-27T22:00:10+08:00',
                'decision': 'Rest quietly',
                'reason': 'Quiet night',
                'tools': [],
                'lab_files_changed': [],
                'result': '搁置',
                'result_reason': '按 Peter 本次决定结束。',
                'current_focus': '雨夜随笔',
                'next_step': '',
                'cost_cny': '0',
                'leisure_association': {
                    'effective': True, 'wake_mode': 'idle_leisure', 'is_leisure': True,
                    'picked': ['m_tea'], 'reason': 'leisure_selected', 'similarity': 0.74,
                },
                'output_audit': {'clean': True, 'flags': [], 'successful_tools': 0},
            })
            chat = SimpleNamespace(cfg=cfg, store=store, memory=mem, history_events=[])
            hub = SimpleNamespace(chat=chat, data_root=root)

            ov = brain_p4_api.overview(hub)
            controls = {c['id']: c for c in ov['recall_association']['controls']}
            self.assertIn('association_leisure', controls)
            self.assertFalse(controls['association_leisure']['enabled'])
            self.assertEqual(ov['recall_association']['leisure_diagnostics']['hits'], 1)

            toggled = brain_p4_api.toggle_layer(hub, 'association_leisure', True)
            self.assertTrue(toggled['ok'])
            self.assertTrue(toggled['enabled'])
            self.assertTrue(toggled['leisure_effective'])
            self.assertTrue(toggled['persisted'])

            # Config registry projection includes leisure keys
            reg = recall_config_overview(role_id='peter', memory_cfg=cfg.memory,
                                         recall_runtime=mem.effective_recall_settings())
            keys = {item['key']: item for item in reg['entries']}
            self.assertIn('memory.recall.leisure_association_enabled', keys)
            self.assertTrue(keys['memory.recall.leisure_association_enabled']['effective'])

    def test_autonomy_send_delivers_recall_without_send_message(self):
        """Real AutonomyController/Chat delivery finalization confirms recall without send_message."""
        from veil.core.veil_agent import Chat

        chat = Chat.__new__(Chat)
        confirmed = []
        rolled_back = []
        chat.cfg = SimpleNamespace(models=None)
        chat.memory = SimpleNamespace(
            last_recall_marks={'m_tea': None},
            last_association_marks=None,
            _has_association_rollback=True,
            last_recall_debug={'leisure_association': {'effective': True, 'wake_gate': {'mode': 'idle_leisure', 'is_leisure': True}, 'picked': ['m_tea'], 'reason': 'leisure_selected', 'similarity': 0.72}},
            confirm_delivery=lambda req_id='': confirmed.append(req_id),
            rollback_recall=lambda req_id='': rolled_back.append(req_id),
        )
        chat.sent_messages = []
        chat.reply_row_saved = False
        chat.autonomy_reply_delivered = False
        chat.autonomy_run_id = '2026-11-28T00:00:00+08:00'
        chat.last_tool_summaries = []

        # Verify _finalize_recall_delivery rolls back when autonomy_reply_delivered is False
        chat._turn_recall_request_id = 'req-auto-fail'
        chat._finalize_recall_delivery(True)
        self.assertEqual(rolled_back, ['req-auto-fail'])
        self.assertEqual(confirmed, [])

        # Verify _finalize_recall_delivery confirms when autonomy_reply_delivered is True
        chat.autonomy_reply_delivered = True
        chat._turn_recall_request_id = 'req-auto-ok'
        chat._finalize_recall_delivery(True)
        self.assertEqual(confirmed, ['req-auto-ok'])
        chat._capture_autonomy_snapshot()
        self.assertIn('2026-11-28T00:00:00+08:00', chat._autonomy_snapshots)
        self.assertEqual(chat._autonomy_snapshots['2026-11-28T00:00:00+08:00']['leisure_association']['picked'], ['m_tea'])


if __name__ == '__main__':
    unittest.main()
