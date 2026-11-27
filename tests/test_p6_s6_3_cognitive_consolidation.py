"""P6-S6-3: Background cognitive consolidation, evolution, governance, multi-day & module collaboration tests."""
import json
import sqlite3
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

from veil.brain.cognitive_consolidation import (
    COST_ACCOUNTING,
    COST_CATEGORY,
    CognitiveConsolidator,
    parse_consolidation_output,
)
from veil.brain.learned_context import LearnedContext
from veil.brain.memory_weights import MemoryWeights
from veil.brain.relationship_base import build as build_relationship_base
from veil.brain.relationship_base import message as relationship_message
from veil.brain.storage import brain_events, brain_writer, veil_brain
from veil.core.config_registry import consolidation_config_overview
from veil.core.veil_agent import Budget, Character, Store


def _make_cfg(root, role='peter'):
    return Character(
        id=role,
        name='Peter',
        persona='Test persona',
        history_rounds=10,
        max_output_tokens=2048,
        provider={
            'model': 'fake-bg',
            'base_url': 'https://example.invalid/v1',
            'api_key_env': 'FAKE_KEY',
            'max_input_tokens': 32000,
            'input_cny_per_million': Decimal('2'),
            'cached_input_cny_per_million': Decimal('1'),
            'output_cny_per_million': Decimal('8'),
        },
        provider_name='fake',
        profile={'file': 'learned-context.json'},
    )


def _init_brain_db(db_path):
    db_path = Path(db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path))
    conn.executescript(veil_brain.SCHEMA)
    brain_events.ensure_schema(conn)
    brain_writer.ensure_schema(conn)
    brain_writer.apply_digest(conn)
    conn.commit()
    conn.close()


def _insert_memory(db_path, memory_id, title, body, *, created='2026-11-20T10:00:00+08:00',
                   importance=5, pinned=0, why='', meaning='', locked=False,
                   event_id='', event_title='', event_story='', user_chat_id='', user_quote=''):
    conn = sqlite3.connect(str(db_path))
    try:
        raw_md = f'---\ntitle: {title}\n---\n{body}'
        conn.execute(
            'INSERT INTO memories(id,bucket,file_rel,title,body,body_sha256,raw_markdown,raw_sha256,'
            'frontmatter_raw,frontmatter_json,created,last_active,type,'
            'importance,pinned,meaning,why_remembered,is_tombstone,is_deleted,vector_state) '
            'VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,0,0,\'ready\')',
            (memory_id, 'memories', f'memories/{memory_id}.md', title, body, brain_writer._sha(body),
             raw_md, brain_writer._sha(raw_md), f'title: {title}', json.dumps({'title': title}, ensure_ascii=False),
             created, created, 'story', importance, int(pinned), meaning, why),
        )
        if locked:
            conn.execute(
                "INSERT OR REPLACE INTO sidecar_state(memory_id,locked,links_json,sources_json) VALUES(?,1,'[]','[]')",
                (memory_id,),
            )
        quote_text = user_quote or body[:120]
        if event_id:
            brain_events.upsert_event(
                conn,
                {
                    'event_id': event_id, 'memory_id': memory_id, 'title': event_title or title,
                    'story': event_story or body, 'status': 'active', 'first_at': created,
                    'last_at': created, 'updated_at': created, 'sources': [memory_id],
                },
                commit=False, ensure=False,
            )
            brain_events.upsert_stage(
                conn,
                {'stage_id': f'st-{event_id}-1', 'event_id': event_id, 'at': created, 'state': 'done', 'text': body},
                commit=False, ensure=False,
            )
            if user_chat_id:
                brain_events.append_ledger(
                    conn, 'stage-evidence',
                    {'event_id': event_id, 'memory_id': memory_id, 'kind': 'chat', 'role': 'user',
                     'message_id': user_chat_id, 'text': quote_text, 'at': created},
                )
        if user_chat_id:
            conn.execute(
                'INSERT INTO sources(memory_id,ord,kind,ref,status) VALUES(?,0,\'chat\',?,\'verified\')',
                (memory_id, user_chat_id),
            )
            conn.execute(
                'INSERT INTO quotes(memory_id,ord,speaker,text) VALUES(?,0,\'user\',?)',
                (memory_id, quote_text),
            )
        brain_writer.apply_digest(conn)
        conn.commit()
    finally:
        conn.close()


class _FakeClock:
    def __init__(self, start_iso='2026-11-20T22:00:00+08:00'):
        self.current = datetime.fromisoformat(start_iso)

    def __call__(self):
        return self.current.isoformat(timespec='seconds')

    def advance(self, **kwargs):
        self.current += timedelta(**kwargs)


class _ScriptedModel:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def __call__(self, messages, **kwargs):
        self.calls.append({'messages': messages, 'kwargs': kwargs})
        if not self.responses:
            raise RuntimeError('no scripted response')
        item = self.responses.pop(0)
        if isinstance(item, Exception):
            raise item
        if callable(item):
            item = item(messages)
        text = item if isinstance(item, str) else json.dumps(item, ensure_ascii=False)
        return text, 800, 220, 0, 'stop'


class CognitiveConsolidationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.store = Store(self.root)
        self.cfg = _make_cfg(self.root, 'peter')
        self.clock = _FakeClock('2026-11-20T22:00:00+08:00')
        self.budget = Budget(self.store, Decimal('10'), Decimal('300'), clock=self.clock)
        self.db_path = self.root / 'peter' / 'brain.db'
        _init_brain_db(self.db_path)
        self.profile = LearnedContext(self.root, 'peter', {'file': 'learned-context.json'})
        self.weights = MemoryWeights(self.cfg, self.store, now_fn=self.clock, review_inline=True)
        self.memory = SimpleNamespace(
            weights=self.weights,
            brain_authority=SimpleNamespace(db_path=self.db_path),
        )

    def tearDown(self):
        self.tmp.cleanup()

    def _append_chat(self, message_id, text, role='user', at=None):
        self.store.append('peter/chat.jsonl', {
            'role': role, 'content': text, 'message_id': message_id,
            'time': at or self.clock(),
        })
        self.profile.invalidate_evidence_cache()

    def _append_autonomy(self, started_at, decision, *, result='完成', clean=True,
                         tools=None, lab_files=None, leisure_picked=None):
        row = {
            'started_at': started_at,
            'finished_at': started_at,
            'decision': decision,
            'result': result,
            'current_focus': decision[:60],
            'tools': tools or [],
            'lab_files_changed': lab_files or [],
            'output_audit': {'clean': clean, 'issues': [] if clean else ['narration']},
        }
        if leisure_picked is not None:
            row['leisure_association'] = {'wake_mode': 'idle_leisure', 'picked': list(leisure_picked)}
        self.store.append('peter/autonomy.jsonl', row)

    def _make_consolidator(self, model):
        return CognitiveConsolidator(
            self.cfg, self.store, self.budget, model,
            memory=self.memory, profile=self.profile, now_fn=self.clock,
        )

    def test_defaults_disabled_and_settings_persist_across_restart(self):
        cons = self._make_consolidator(_ScriptedModel([]))
        st = cons.status()
        self.assertFalse(st['settings']['enabled'])
        self.assertEqual(st['effective']['code'], 'disabled')
        # Scheduled run must do nothing when disabled by default.
        skipped = cons.run_once(trigger='scheduled')
        self.assertEqual(skipped['reason'], 'disabled')

        # Update & persist settings, reject invalid values.
        res = cons.update_settings({
            'enabled': True, 'interval_hours': 12, 'daily_budget_cny': 0.08,
            'max_runs_per_day': 3, 'bad_key': 1, 'timeout_seconds': 999,
        })
        self.assertTrue(res['ok'])
        self.assertEqual(res['rejected'], {'bad_key': 'unknown', 'timeout_seconds': 'invalid'})

        # New instance after restart retains exact persisted config.
        restarted = self._make_consolidator(_ScriptedModel([]))
        self.assertTrue(restarted.settings()['enabled'])
        self.assertEqual(restarted.settings()['interval_hours'], 12)
        self.assertEqual(restarted.settings()['daily_budget_cny'], 0.08)
        self.assertEqual(restarted.settings()['max_runs_per_day'], 3)

        # Config registry projects saved vs runtime accurately.
        reg = consolidation_config_overview(role_id='peter', persisted=res['settings'], runtime=restarted.settings())
        keys = {entry['key']: entry for entry in reg['entries']}
        self.assertTrue(keys['memory.consolidation.enabled']['effective']['value'])
        self.assertEqual(keys['memory.consolidation.interval_hours']['effective']['value'], 12)

    def test_multi_experience_synthesis_flows_into_chat_and_autonomy_and_triggers_s6_1(self):
        self._append_chat('u-coffee-1', '晚上下班累的时候别急着给我讲道理，先陪我待一会儿就好。')
        _insert_memory(
            self.db_path, 'mem-c1', '周二加班晚归',
            '周二晚上 Lan 加班很累，说先陪她待一会儿就好，不要立刻讲道理。',
            created='2026-11-18T21:30:00+08:00', importance=5,
            why='关系陪伴方式', event_id='ev-c1', user_chat_id='u-coffee-1',
        )
        _insert_memory(
            self.db_path, 'mem-c2', '周四熬夜调试后情绪低落',
            '周四晚间调试失败后 Lan 很疲惫，安安静静陪着聊了两句轻松的话，她情绪明显舒缓。',
            created='2026-11-20T21:00:00+08:00', importance=5,
            why='再次印证疲惫时先陪伴', event_id='ev-c2',
        )

        # Already existing growth text to ensure duplicates are rejected.
        self.profile.submit_candidate('growth', '已有总结：周二加班晚归。')

        model = _ScriptedModel([{
            'cognition_updates': [
                {
                    'kind': 'new', 'section': 'relationship',
                    'text': '晚上下班累的时候别急着给我讲道理，先陪我待一会儿就好',
                    'replace_old_text': '',
                    'reason': '周二和周四两次深夜疲惫经历共同印证了先安静陪伴再谈解决的相处方式',
                    'evidence_refs': ['memory:mem-c1', 'memory:mem-c2', 'chat:u-coffee-1'],
                    'claim_status': 'evidence_backed',
                },
                {
                    'kind': 'new', 'section': 'growth',
                    'text': '在 Lan 深夜疲惫或调试受挫时，优先给予安静陪伴和轻松回应，避免立刻展开长篇分析。',
                    'replace_old_text': '',
                    'reason': '跨两日相同情绪场景对比得出可复用的陪伴节奏',
                    'evidence_refs': ['memory:mem-c1', 'memory:mem-c2'],
                    'claim_status': 'evidence_backed',
                },
                {
                    # Duplicate of existing summary -> must be skipped!
                    'kind': 'new', 'section': 'growth',
                    'text': '已有总结：周二加班晚归。',
                    'replace_old_text': '',
                    'reason': '重复已有总结',
                    'evidence_refs': ['memory:mem-c1'],
                    'claim_status': 'uncertain',
                },
                {
                    # Hallucinated evidence -> must be rejected!
                    'kind': 'new', 'section': 'relationship',
                    'text': '她每天早上必须喝美式咖啡',
                    'replace_old_text': '',
                    'reason': '臆测',
                    'evidence_refs': ['memory:nonexistent-999'],
                    'claim_status': 'evidence_backed',
                },
            ],
            'importance_proposals': [
                {
                    'memory_id': 'mem-c1',
                    'proposed_importance': 6,
                    'confidence': 0.82,
                    'reason': '该经历与周四经历形成明确关系陪伴规律并已被关系认知引用',
                    'evidence_refs': ['memory:mem-c1', 'event:ev-c1'],
                },
            ],
            'experience_candidates': [
                {
                    'kind': 'communication_pattern',
                    'trigger_condition': 'Lan 深夜疲惫或调试受挫表达低落时',
                    'guidance': '先简短温和地陪着，不急着复盘技术细节或长篇给建议。',
                    'reason': '两次经历一致表明先共情陪伴效果更好',
                    'evidence_refs': ['memory:mem-c1', 'memory:mem-c2'],
                    'supersedes_id': '',
                },
            ],
        }])

        cons = self._make_consolidator(model)
        cons.update_settings({'enabled': True, 'auto_apply_importance': True})
        out = cons.run_once(trigger='scheduled')
        self.assertTrue(out['ok'])
        self.assertEqual(out['status'], 'completed')
        cat_spent, _ = self.budget.category_totals(None, None, COST_CATEGORY)
        self.assertAlmostEqual(out['cost_cny'], float(cat_spent), places=6)
        self.assertGreater(out['cost_cny'], 0.0)
        self.assertLess(out['cost_cny'], 0.05)
        self.assertIn('认知巩固', self.budget.display())

        res = out['results']
        self.assertEqual(res['rejected_ungrounded'], 1)
        self.assertEqual(res['skipped_duplicates'], 1)
        self.assertEqual(len(res['cognition_updates']), 2)
        rel_out = res['cognition_updates'][0]
        self.assertEqual(rel_out['effective_status'], 'effective_confirmed')
        self.assertEqual(res['cognition_updates'][1]['effective_status'], 'effective_learned')

        # Verify S6-1 importance re-evaluation was triggered & safely auto-applied (5 -> 6, with version!).
        self.assertEqual(len(res['importance_proposals']), 1)
        imp_out = res['importance_proposals'][0]
        self.assertEqual(imp_out['outcome'], 'applied')
        self.assertTrue(imp_out['proposal_id'])
        self.assertIsNotNone(imp_out['version_seq'])

        # Verify consolidated cognition enters BOTH subsequent chat AND autonomous wake prompt assembly!
        proj = self.profile.build_prompt()
        self.assertIn('优先给予安静陪伴', proj['stable'])
        rel_base = build_relationship_base(self.profile, {}, projection=proj)
        rel_msg = relationship_message(rel_base)
        self.assertIsNotNone(rel_msg)
        self.assertIn('先陪我待一会儿就好', rel_msg['content'])

        # Verify P7 experience candidate starts uncertain (never self-promoted) & respects contract.
        p7_list = cons.list_experience_candidates()
        self.assertEqual(len(p7_list), 1)
        self.assertEqual(p7_list[0]['status'], 'uncertain')
        self.assertEqual(cons.query_experience_candidates(situation='深夜疲惫'), [])
        # After Lan confirms, P7 query returns it for matching situation!
        cons.decide_experience_candidate(p7_list[0]['id'], 'confirm')
        hits = cons.query_experience_candidates(situation='深夜疲惫调试受挫')
        self.assertEqual(len(hits), 1)
        self.assertEqual(hits[0]['id'], p7_list[0]['id'])

    def test_cross_day_conflict_revision_preserves_history_and_sovereignty_holds(self):
        # Day 1: Peter forms an unlocked relationship item (evidence-backed).
        self._append_chat('u-d1', '周末早上十点前别发主动消息吵我睡觉')
        self.profile.submit_candidate(
            'relationship', '周末早上十点前别发主动消息吵我睡觉',
            status='confirmed', sources=['chat:u-d1'],
        )
        # Also create a LOCKED relationship item and a Lan-vetoed claim.
        self._append_chat('u-lock', '绝不在外人面前提我本名')
        self.profile.submit_candidate(
            'relationship', '绝不在外人面前提我本名',
            status='confirmed', sources=['chat:u-lock'],
        )
        items = self.profile.status()['relationship']['items']
        locked_id = next(i['id'] for i in items if '本名' in i['text'])
        self.profile.set_relationship_lock(locked_id, True)
        # Veto another claim by adding then deleting via save_relationship.
        self.profile.save_relationship([
            *[{k: v for k, v in it.items() if k in ('id', 'text', 'status', 'locked', 'sources')} for it in items],
            {'id': 'temp-veto', 'text': '每天早上七点发打卡提醒', 'status': 'confirmed'},
        ])
        self.profile.save_relationship([
            {k: v for k, v in it.items() if k in ('id', 'text', 'status', 'locked', 'sources')} for it in items
        ])

        # Day 1 background S3 semantic worker settles Day 1 items before Day 2 begins.
        from veil.brain.relationship_semantic import RelationshipSemanticWorker

        def verify_cb(messages):
            payload = json.loads(messages[1]['content'])
            return {
                'verdict': 'supported',
                'supporting_evidence_ids': [row['id'] for row in payload['evidence']],
                'counterevidence': [{'id': row['id'], 'verdict': 'unrelated'} for row in payload['counterevidence']],
            }

        day1_worker = RelationshipSemanticWorker(
            self.cfg, self.budget, _ScriptedModel([verify_cb] * 4),
            lambda: {'daily_cap_cny': '0.06', 'timeout_seconds': 6},
            profile=self.profile,
        )
        while day1_worker.tick():
            pass

        # Day 2: Lan explicitly updates her weekend morning preference.
        self.clock.advance(hours=25)
        self._append_chat('u-d2', '以后周末九点以后就可以发消息了，我最近起得早')
        _insert_memory(
            self.db_path, 'mem-d2-1', '周末作息变化',
            'Lan 说以后周末九点以后就可以发消息了，她最近起得早。',
            created=self.clock(), importance=6, event_id='ev-d2-1',
            user_chat_id='u-d2', user_quote='以后周末九点以后就可以发消息了，我最近起得早',
        )
        _insert_memory(
            self.db_path, 'mem-d2-2', '周日九点主动聊早茶',
            '周日九点十分 Lan 已经醒了并聊起早茶计划。',
            created=self.clock(), importance=5,
        )

        model = _ScriptedModel([{
            'cognition_updates': [
                {
                    # Legitimate conflict revision on unlocked item!
                    'kind': 'revision', 'section': 'relationship',
                    'replace_old_text': '周末早上十点前别发主动消息吵我睡觉',
                    'text': '以后周末九点以后就可以发消息了，我最近起得早',
                    'reason': '新经历显示 Lan 周末作息提前，原十点限制已过时',
                    'evidence_refs': ['memory:mem-d2-1', 'memory:mem-d2-2', 'chat:u-d2'],
                    'claim_status': 'evidence_backed',
                },
                {
                    # Attempt to overwrite LOCKED item -> MUST BE BLOCKED!
                    'kind': 'revision', 'section': 'relationship',
                    'replace_old_text': '绝不在外人面前提我本名',
                    'text': '偶尔可以提本名',
                    'reason': '误以为放松限制',
                    'evidence_refs': ['memory:mem-d2-1'],
                    'claim_status': 'evidence_backed',
                },
                {
                    # Attempt to resurrect VETOED claim -> MUST BE BLOCKED!
                    'kind': 'new', 'section': 'relationship',
                    'text': '每天早上七点发打卡提醒',
                    'replace_old_text': '',
                    'reason': '试图复活已被 Lan 删除的规则',
                    'evidence_refs': ['memory:mem-d2-2'],
                    'claim_status': 'uncertain',
                },
            ],
            'importance_proposals': [],
            'experience_candidates': [
                {
                    # Attempt to sneak Lan-vetoed claim into P7 -> MUST BE BLOCKED!
                    'kind': 'behavioral_pattern',
                    'trigger_condition': '每天早上七点',
                    'guidance': '每天早上七点发打卡提醒',
                    'reason': '试图绕道进入 P7',
                    'evidence_refs': ['memory:mem-d2-2'],
                    'supersedes_id': '',
                },
            ],
        }])

        cons = self._make_consolidator(model)
        cons.update_settings({'enabled': True})
        out = cons.run_once(trigger='scheduled')
        self.assertTrue(out['ok'])

        c_updates = out['results']['cognition_updates']
        self.assertEqual(c_updates[0]['governance_outcome'], 'accepted')
        self.assertEqual(c_updates[0]['effective_status'], 'queued_semantic_verify')
        self.assertEqual(c_updates[1]['effective_status'], 'blocked_sovereignty')
        self.assertEqual(c_updates[2]['effective_status'], 'blocked_sovereignty')
        self.assertEqual(out['results']['experience_candidates'][0]['outcome'], 'blocked_sovereignty')

        # Before S3 semantic verification finishes, old_item is safely NOT retired yet.
        mid_items = self.profile.status()['relationship']['items']
        self.assertEqual(next(i for i in mid_items if '十点前' in i['text'])['status'], 'confirmed')

        # Run S3's real RelationshipSemanticWorker to independently verify pending candidates against counters!
        from veil.brain.relationship_semantic import RelationshipSemanticWorker
        tasks = self.profile.pending_relationship_semantics()
        self.assertGreaterEqual(len(tasks), 1)

        def verify_cb(messages):
            payload = json.loads(messages[1]['content'])
            return {
                'verdict': 'supported',
                'supporting_evidence_ids': [row['id'] for row in payload['evidence']],
                'counterevidence': [{'id': row['id'], 'verdict': 'unrelated'} for row in payload['counterevidence']],
            }

        worker = RelationshipSemanticWorker(
            self.cfg, self.budget, _ScriptedModel([verify_cb] * (len(tasks) + 1)),
            lambda: {'daily_cap_cny': '0.06', 'timeout_seconds': 6},
            profile=self.profile,
        )
        verified_count = 0
        while worker.tick():
            verified_count += 1
        self.assertGreaterEqual(verified_count, 1)

        # Check S3 relationship items: locked intact, vetoed absent, old item retired as outdated with superseded_by!
        after_items = self.profile.status()['relationship']['items']
        self.assertTrue(any(i['text'] == '绝不在外人面前提我本名' and i.get('locked') and i['status'] == 'confirmed' for i in after_items))
        self.assertFalse(any('七点发打卡' in i['text'] and i['status'] != 'outdated' for i in after_items))
        old_item = next(i for i in after_items if '十点前' in i['text'])
        new_item = next(i for i in after_items if '九点以后' in i['text'])
        self.assertEqual(old_item['status'], 'outdated')
        self.assertEqual(old_item.get('superseded_by'), new_item['id'])
        self.assertEqual(new_item['status'], 'confirmed')

    def test_s6_2_autonomy_filtering_and_anti_self_confirmation(self):
        # One clean autonomous wake with real tool + leisure hit, one UNCLEAN roleplay wake!
        self._append_autonomy(
            '2026-11-20T14:00:00+08:00',
            '用 lab 工具整理了上次聊过的电子音乐清单并写进 notes.md',
            clean=True,
            tools=[{'tool': 'write_file', 'path': 'lab/notes.md', 'state': '成功'}],
            lab_files=['lab/notes.md'],
            leisure_picked=['mem-music'],
        )
        self._append_autonomy(
            '2026-11-20T16:00:00+08:00',
            '*walks to the window and pretends to check email*',
            clean=False,
        )
        _insert_memory(
            self.db_path, 'mem-music', '一起听后摇和电子乐',
            '之前聊过喜欢干净克制的电子氛围音乐。',
            created='2026-11-10T20:00:00+08:00', importance=7,
        )

        # Model tries to promote an autonomy-only reflection to confirmed relationship -> MUST stay uncertain!
        def responder(messages):
            user_payload = json.loads(messages[1]['content'])
            # Verify unclean narration wake was strictly excluded from input!
            auto_refs = [a['ref'] for a in user_payload['autonomous_experiences']]
            self.assertEqual(auto_refs, ['autonomy:2026-11-20T14:00:00+08:00'])
            return {
                'cognition_updates': [
                    {
                        'kind': 'new', 'section': 'relationship',
                        'text': '我猜她以后每天下午都想听电子音乐',
                        'replace_old_text': '',
                        'reason': '来自自主活动自我猜想',
                        'evidence_refs': ['autonomy:2026-11-20T14:00:00+08:00'],
                        'claim_status': 'evidence_backed',
                    },
                ],
                'importance_proposals': [],
                'experience_candidates': [
                    {
                        'kind': 'autonomy_lesson',
                        'trigger_condition': '自主闲暇整理音乐或创意资料时',
                        'guidance': '把真实整理成果落盘到 lab/notes.md，后续聊天时可自然分享真实结果。',
                        'reason': '自主活动实际落盘比空想更有连续性',
                        'evidence_refs': ['autonomy:2026-11-20T14:00:00+08:00', 'memory:mem-music'],
                        'supersedes_id': '',
                    },
                ],
            }

        cons = self._make_consolidator(_ScriptedModel([responder]))
        cons.update_settings({'enabled': True, 'min_new_items': 1})
        out = cons.run_once(trigger='scheduled')
        self.assertTrue(out['ok'])
        upd = out['results']['cognition_updates'][0]
        # Zero circular self-confirmation: autonomy-only claim MUST stay uncertain, never confirmed!
        self.assertEqual(upd['requested_status'], 'uncertain')
        self.assertEqual(upd['effective_status'], 'effective_uncertain')
        rel_items = self.profile.relationship_items()
        self.assertEqual(rel_items[0]['status'], 'uncertain')
        self.assertEqual(rel_items[0]['source'], 'peter')

    def test_multiday_incremental_budget_limit_crash_recovery_and_no_repeat_promotion(self):
        # Day 1: 2 new memories
        _insert_memory(self.db_path, 'm-day1-a', 'Day1 体验A', '一起调试 Windows 权限问题并记下了边界。',
                       created='2026-11-20T10:00:00+08:00', importance=5, event_id='ev-d1a')
        _insert_memory(self.db_path, 'm-day1-b', 'Day1 体验B', '再次确认修改系统前先说清影响和回滚。',
                       created='2026-11-20T15:00:00+08:00', importance=5, event_id='ev-d1b')

        day1_resp = {
            'cognition_updates': [{
                'kind': 'new', 'section': 'growth',
                'text': '涉及本机系统或权限改动前，先说明影响范围和回滚步骤再动手。',
                'replace_old_text': '',
                'reason': '两次权限讨论共同总结出的稳健习惯',
                'evidence_refs': ['memory:m-day1-a', 'memory:m-day1-b'],
                'claim_status': 'uncertain',
            }],
            'importance_proposals': [{
                'memory_id': 'm-day1-a',
                'proposed_importance': 6,
                'confidence': 0.8,
                'reason': '形成可复用权限习惯',
                'evidence_refs': ['memory:m-day1-a', 'event:ev-d1a'],
            }],
            'experience_candidates': [{
                'kind': 'tool_lesson',
                'trigger_condition': '需要修改系统配置或权限时',
                'guidance': '先列出影响与回滚方式，再请求确认执行。',
                'reason': '两次经历均验证该流程可避免误操作',
                'evidence_refs': ['memory:m-day1-a', 'memory:m-day1-b'],
                'supersedes_id': '',
            }],
        }

        model = _ScriptedModel([day1_resp])
        cons = self._make_consolidator(model)
        cons.update_settings({'enabled': True, 'interval_hours': 24, 'daily_budget_cny': 0.05})
        r1 = cons.run_once(trigger='scheduled')
        self.assertTrue(r1['ok'])
        self.assertEqual(len(model.calls), 1)
        exp1_id = r1['results']['experience_candidates'][0]['id']
        prop1_id = r1['results']['importance_proposals'][0]['proposal_id']

        # Lan rejects the importance proposal -> must not be re-proposed repeatedly!
        self.weights.decide(prop1_id, 'reject', note='暂时维持 5')

        # Immediate second scheduled tick on Day 1 -> skipped by interval (0 model calls!).
        r1_again = cons.run_once(trigger='scheduled')
        self.assertEqual(r1_again['status'], 'skipped')
        self.assertEqual(r1_again['reason'], 'interval_not_elapsed')
        self.assertEqual(len(model.calls), 1)

        # Day 1 evening: force run with tiny budget cap (0.0001 CNY) + new memories -> budget_exhausted, cursor NOT advanced!
        _insert_memory(self.db_path, 'm-day2-a', 'Day2 体验A', '发现权限边界外还需要先在隔离副本 dry-run 演练。',
                       created='2026-11-21T09:00:00+08:00', importance=6, event_id='ev-d2a')
        _insert_memory(self.db_path, 'm-day2-b', 'Day2 体验B', '隔离演练顺利验证了备份回滚流程。',
                       created='2026-11-21T11:00:00+08:00', importance=6, event_id='ev-d2b')
        cons.update_settings({'daily_budget_cny': 0.0001})
        r_cap = cons.run_once(trigger='manual', force=True)
        self.assertFalse(r_cap['ok'])
        self.assertEqual(r_cap['status'], 'budget_exhausted')
        # Cursor did NOT consume m-day2-a / m-day2-b!
        self.assertNotIn('m-day2-a', cons._load_state()['processed_memories'])

        # Simulate crash with in_flight + restart recovery!
        st = cons._load_state()
        st['in_flight'] = {
            'run_id': 'cons-crashed', 'batch_key': 'deadbeef',
            'started_at': self.clock(), 'trigger': 'scheduled', 'phase': 'dispatched',
        }
        cons._save_state(st)

        # Advance to Day 2 (+25h): raise cap back to 0.05, restart consolidator -> recovers cleanly!
        self.clock.advance(hours=25)
        day2_resp = {
            'cognition_updates': [{
                'kind': 'revision', 'section': 'growth',
                'replace_old_text': '涉及本机系统或权限改动前，先说明影响范围和回滚步骤再动手。',
                'text': '涉及本机系统或权限改动前，先在隔离副本完成 dry-run 演练，再说明影响范围和回滚步骤。',
                'reason': 'Day2 隔离演练经验补充了原习惯',
                'evidence_refs': ['memory:m-day2-a', 'memory:m-day2-b', 'memory:m-day1-a'],
                'claim_status': 'uncertain',
            }],
            'importance_proposals': [{
                # Try re-proposing the rejected memory -> MUST be skipped!
                'memory_id': 'm-day1-a',
                'proposed_importance': 6,
                'confidence': 0.85,
                'reason': '重复提权尝试',
                'evidence_refs': ['memory:m-day1-a', 'event:ev-d1a'],
            }],
            'experience_candidates': [{
                'kind': 'tool_lesson',
                'trigger_condition': '需要修改系统配置或权限时',
                'guidance': '先在隔离副本 dry-run 演练，再向用户说明影响与回滚并请求确认。',
                'reason': 'Day2 演化修订 Day1 经验',
                'evidence_refs': ['memory:m-day2-a', 'memory:m-day2-b'],
                'supersedes_id': exp1_id,
            }],
        }
        model2 = _ScriptedModel([day2_resp])
        cons2 = self._make_consolidator(model2)
        cons2.update_settings({'daily_budget_cny': 0.05})
        # Crash recovery wrote an interrupted record and cleared in_flight; backoff protects immediate loop.
        self.assertIsNone(cons2._load_state()['in_flight'])
        self.assertEqual(cons2._load_state()['last_status'], 'interrupted')
        blocked_by_backoff = cons2.run_once(trigger='scheduled')
        self.assertEqual(blocked_by_backoff['reason'], 'failure_backoff')

        # Advance past 15m backoff -> Day 2 scheduled run succeeds and evolves Day 1 cognition + P7 experience!
        self.clock.advance(minutes=20)
        r2 = cons2.run_once(trigger='scheduled')
        self.assertTrue(r2['ok'])
        self.assertEqual(r2['status'], 'completed')
        # Historical anchor m-day1-a was retrieved alongside Day 2 memories!
        self.assertIn('m-day1-a', r2['inputs']['anchor_memory_ids'])
        # Growth revised cleanly!
        growth_text = self.profile.status()['sections']['growth']['text']
        self.assertIn('隔离副本完成 dry-run', growth_text)
        # Rejected importance proposal was NOT re-created!
        self.assertEqual(r2['results']['importance_proposals'], [])
        # P7 experience candidate evolved: old marked outdated with superseded_by!
        exps = {e['id']: e for e in cons2.list_experience_candidates()}
        self.assertEqual(exps[exp1_id]['status'], 'outdated')
        new_exp_id = r2['results']['experience_candidates'][0]['id']
        self.assertEqual(exps[exp1_id]['superseded_by'], new_exp_id)
        self.assertEqual(exps[new_exp_id]['supersedes'], exp1_id)

        # Day 3 (+25h): no new material -> skipped at zero cost!
        self.clock.advance(hours=25)
        r3 = cons2.run_once(trigger='scheduled')
        self.assertEqual(r3['status'], 'skipped')
        self.assertEqual(r3['reason'], 'no_new_material')
        self.assertEqual(len(model2.calls), 1)

        # Verify costs were properly recorded under cognitive_consolidation category in shared Budget ledger!
        rows = self.store.read('costs.jsonl')
        cons_rows = [r for r in rows if r.get('category') == COST_CATEGORY]
        self.assertTrue(cons_rows)

    def test_brain_api_and_budget_summary_routes(self):
        from veil.brain.brain_p4_api import (
            consolidation_status,
            decide_consolidation_experience,
            overview,
            run_consolidation,
            set_consolidation_settings,
        )

        cons = self._make_consolidator(_ScriptedModel([]))
        hub = SimpleNamespace(
            chat=SimpleNamespace(
                cfg=self.cfg, store=self.store, budget=self.budget,
                memory=self.memory, profile=self.profile, consolidation=cons,
            ),
            data_root=self.root,
        )
        st = consolidation_status(hub)
        self.assertTrue(st['available'])
        self.assertFalse(st['settings']['enabled'])

        upd = set_consolidation_settings(hub, {'enabled': True, 'daily_budget_cny': 0.06, 'interval_hours': 12})
        self.assertTrue(upd['ok'])
        self.assertTrue(upd['status']['settings']['enabled'])

        ov = overview(hub)
        self.assertIn('consolidation', ov)
        self.assertTrue(ov['consolidation']['enabled'])

        run_res = run_consolidation(hub, force=False)
        self.assertEqual(run_res['status'], 'skipped')
        self.assertEqual(run_res['reason'], 'no_new_material')

        bad_exp = decide_consolidation_experience(hub, 'missing-id', 'confirm')
        self.assertFalse(bad_exp['ok'])
        self.assertEqual(bad_exp['status_code'], 404)

        # Verify actual HTTP handler routes & auth guard (/api/brain/consolidation*, /api/budget/summary).
        from unittest.mock import Mock
        from veil.server.web_agent import make_handler

        handler = make_handler(hub, 'test-token')
        req = handler.__new__(handler)
        req.server = SimpleNamespace(server_address=('127.0.0.1', 65123))
        req.headers = {'Host': '127.0.0.1:65123', 'X-Token': 'test-token'}
        req._send = Mock()
        req.path = '/api/brain/consolidation'
        req.do_GET()
        code, payload = req._send.call_args.args
        self.assertEqual(code, 200)
        self.assertTrue(payload['available'])
        self.assertTrue(payload['settings']['enabled'])

        req._send.reset_mock()
        req.path = '/api/budget/summary'
        req.do_GET()
        code, bdata = req._send.call_args.args
        self.assertEqual(code, 200)
        self.assertIn('cognitive_consolidation', {c['id'] for c in bdata['categories']})
        self.assertIsInstance(bdata.get('cognitive_consolidation'), dict)
        self.assertTrue(bdata['cognitive_consolidation']['enabled'])

        # Unauthenticated request must be denied (403).
        req._send.reset_mock()
        req.headers = {'Host': '127.0.0.1:65123'}
        req.path = '/api/brain/consolidation'
        req.do_GET()
        self.assertEqual(req._send.call_args.args[0], 403)

    def test_parser_accepts_code_fence_and_rejects_malformed(self):
        fenced = '```json\n{"cognition_updates": [], "importance_proposals": [], "experience_candidates": []}\n```'
        parsed = parse_consolidation_output(fenced)
        self.assertEqual(parsed, {'cognition_updates': [], 'importance_proposals': [], 'experience_candidates': []})
        with self.assertRaises(ValueError):
            parse_consolidation_output('{"unknown_key": []}')
        with self.assertRaises(ValueError):
            parse_consolidation_output('not json')


if __name__ == '__main__':
    unittest.main()
