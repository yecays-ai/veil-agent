"""P6-S3: Relationship base decoupling, strict evidence promotion, and cognition governance tests."""
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from veil.brain.learned_context import LearnedContext, SECTION_BUDGETS
from veil.brain.relationship_base import build as build_relationship_base, select as select_relationship_base
from veil.brain.storage.veil_brain import SCHEMA as BRAIN_SCHEMA
from veil.brain.storage.brain_events import ensure_schema as ensure_event_schema
from veil.core.agent_types import UserError
from veil.core.veil_agent import Budget, Chat, Store, load_character


class FakeChatModel:
    def __init__(self, reply='ok'):
        self.reply = reply
        self.requests = []

    def __call__(self, messages, **kwargs):
        self.requests.append({'messages': list(messages), 'tools': list(kwargs.get('tools') or [])})
        return (self.reply, 120, 12, 0)


def _init_brain_db(role_dir: Path):
    conn = sqlite3.connect(str(role_dir / 'brain.db'))
    conn.executescript(BRAIN_SCHEMA)
    ensure_event_schema(conn)
    return conn


class BudgetAndMigrationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        (self.root / 'peter').mkdir(parents=True, exist_ok=True)
        (self.root / 'peter' / 'about-lan.md').write_text('Lan likes tea.', encoding='utf-8')
        self.lc = LearnedContext(self.root, 'peter', {
            'file': 'learned-context.json',
            'legacy_file': 'about-lan.md',
            'max_bytes': 3000,  # legacy shared limit must NOT restrict 4-section budgets
        })

    def tearDown(self):
        self.tmp.cleanup()

    def test_four_independent_budgets_and_utf8_bytes(self):
        self.assertEqual(self.lc.section_budgets, SECTION_BUDGETS)
        self.assertEqual(self.lc.max_bytes, 6200)
        # Fill facts to 1790B (close to 1800B) and growth to 1990B (close to 2000B)
        self.lc.save_section('facts', '你' * 596)   # 1788 UTF-8 bytes
        self.lc.save_section('growth', '我' * 663)  # 1989 UTF-8 bytes
        # relationship and ongoing must still accept new items freely!
        _, s1, _ = self.lc.invoke('profile_append', {
            'text': '说话直接一点，不要绕弯子。', 'section': 'relationship', 'status': 'uncertain',
        })
        _, s2, _ = self.lc.invoke('ongoing_update', {'action': 'add', 'text': '一起整理关系认知'})
        self.assertEqual(s1, '成功')
        self.assertEqual(s2, '成功')
        st = self.lc.status()
        self.assertEqual(st['budgets']['sections']['facts']['used_bytes'], 1788)
        self.assertEqual(st['budgets']['sections']['growth']['used_bytes'], 1989)
        self.assertFalse(st['capacity_degraded'])

    def test_over_budget_section_still_readable_and_allows_shrinking(self):
        self.lc.read()
        path = self.root / 'peter' / 'learned-context.json'
        raw = json.loads(path.read_text(encoding='utf-8'))
        raw['facts']['text'] = ('a' * 1600) + ('b' * 500)  # exceeds 1800B
        path.write_text(json.dumps(raw, ensure_ascii=False), encoding='utf-8')

        # Must still load cleanly and report capacity_degraded on facts only
        st = self.lc.status()
        self.assertTrue(st['available'])
        self.assertTrue(st['capacity_degraded'])
        self.assertEqual(st['degraded_sections'], ['facts'])
        self.assertEqual(st['budgets']['sections']['facts']['over_bytes'], 300)
        # Context injection still works
        self.assertIn('a' * 100, self.lc.context())
        # Expanding facts is rejected, shrinking facts is allowed
        _, state_grow, _ = self.lc.invoke('profile_append', {'text': 'more', 'section': 'facts'})
        self.assertEqual(state_grow, '被拒绝')
        _, state_shrink, _ = self.lc.invoke('profile_replace', {
            'old_text': 'b' * 500, 'new_text': '', 'section': 'facts',
        })
        self.assertEqual(state_shrink, '成功')
        self.assertFalse(self.lc.status()['capacity_degraded'])

    def test_migration_dry_run_idempotent_and_rollback(self):
        self.lc.save_section('facts', 'Lan drinks oolong.')
        dry = self.lc.migrate_storage(dry_run=True)
        self.assertTrue(dry['dry_run'])
        self.assertFalse(dry['applied'])
        applied = self.lc.migrate_storage(dry_run=False)
        self.assertFalse(applied['dry_run'])
        # Second run is idempotent
        second = self.lc.migrate_storage(dry_run=False)
        self.assertFalse(second['needs_upgrade'])
        self.assertFalse(second['applied'])
        if applied['backup_path']:
            restored = self.lc.rollback_migration(applied['backup_path'])
            self.assertEqual(restored['facts']['text'], 'Lan drinks oolong.')

    def test_load_character_ignores_legacy_max_bytes_cap(self):
        char_dir = self.root / 'characters' / 'peter'
        char_dir.mkdir(parents=True)
        (self.root / 'persona').mkdir(parents=True, exist_ok=True)
        (self.root / 'persona' / 'peter.md').write_text('Peter persona.', encoding='utf-8')
        toml = char_dir / 'character.toml'
        toml.write_text(
            'name = "Peter"\n'
            'persona = "persona/peter.md"\n'
            'provider = "default"\n'
            '[providers.default]\nbase_url = "https://example.invalid/v1"\napi_key_env = "TEST_KEY"\n'
            'model = "test-model"\nmax_input_tokens = 8192\n'
            'input_cny_per_million = "2"\ncached_input_cny_per_million = "1"\noutput_cny_per_million = "8"\n'
            '[profile]\nfile = "learned-context.json"\nlearned = true\nmax_bytes = 3000\n',
            encoding='utf-8',
        )
        cfg = load_character(self.root, 'peter')
        self.assertEqual(cfg.profile['section_budgets'], SECTION_BUDGETS)
        self.assertEqual(cfg.profile['max_bytes'], 6200)
        self.assertEqual(cfg.profile['legacy_max_bytes'], 3000)


class StrictEvidenceChainTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.role = self.root / 'peter'
        self.role.mkdir(parents=True, exist_ok=True)
        self.lc = LearnedContext(self.root, 'peter', {
            'file': 'learned-context.json', 'legacy_file': 'about-lan.md',
        })
        self.chat_path = self.role / 'chat.jsonl'
        self.chat_path.write_text(
            json.dumps({'role': 'user', 'message_id': 'u1', 'content': '以后别猜我心情，直接问我。'}) + '\n'
            + json.dumps({'role': 'user', 'message_id': 'u2', 'content': '今天午饭吃了牛肉面，挺好吃的。'}) + '\n'
            + json.dumps({'role': 'assistant', 'message_id': 'a1', 'content': '以后别猜心情，直接问。'}) + '\n',
            encoding='utf-8',
        )
        self.db = _init_brain_db(self.role)
        self.addCleanup(self.db.close)
        conn = self.db
        # Event e1 has user stage-evidence pointing to u1
        conn.execute(
            "INSERT INTO memories(id, file_rel, title, body, raw_markdown, body_sha256, raw_sha256, frontmatter_raw, frontmatter_json, bucket, type, importance, created, is_tombstone, is_deleted) "
            "VALUES('m1', 'm1.md', '沟通约定', '以后别猜心情直接问', '以后别猜心情直接问', 'sha1', 'rsha1', '', '{}', 'core', 'event', 8, '2026-11-26', 0, 0)"
        )
        conn.execute("INSERT INTO events(event_id, memory_id, title, story) VALUES('e1', 'm1', '沟通约定', '聊天约定')")
        conn.execute(
            "INSERT INTO event_ledger(ledger_seq, kind, source_file, event_id, payload_json) "
            "VALUES(1, 'stage-evidence', '__live__', 'e1', ?)",
            (json.dumps({'kind': 'chat', 'role': 'user', 'message_id': 'u1', 'text': '以后别猜我心情，直接问我。'}),),
        )
        # Event e_tool has ONLY tool evidence (no user chat evidence, no user quotes)
        conn.execute(
            "INSERT INTO memories(id, file_rel, title, body, raw_markdown, body_sha256, raw_sha256, frontmatter_raw, frontmatter_json, bucket, type, importance, created, is_tombstone, is_deleted) "
            "VALUES('m_tool', 'm_tool.md', '自猜总结', '她可能不喜欢下雨天', '她可能不喜欢下雨天', 'sha2', 'rsha2', '', '{}', 'core', 'event', 6, '2026-11-26', 0, 0)"
        )
        conn.execute("INSERT INTO events(event_id, memory_id, title, story) VALUES('e_tool', 'm_tool', '自猜', '自主反思')")
        conn.execute(
            "INSERT INTO event_ledger(ledger_seq, kind, source_file, event_id, payload_json) "
            "VALUES(2, 'stage-evidence', '__live__', 'e_tool', ?)",
            (json.dumps({'kind': 'tool', 'tool': 'autonomy', 'text': 'ran reflection'}),),
        )
        conn.commit()

    def tearDown(self):
        self.db.close()
        self.tmp.cleanup()

    def test_direct_and_indirect_user_evidence_promotes_to_confirmed(self):
        _, st1, _ = self.lc.invoke('profile_append', {
            'section': 'relationship', 'status': 'confirmed',
            'text': '不要猜Lan心情，直接问她。', 'sources': ['event:e1'],
        })
        self.assertEqual(st1, '成功')
        items = self.lc.read()['relationship']['items']
        self.assertEqual(items[0]['status'], 'confirmed')
        self.assertEqual(items[0]['source'], 'evidence')

    def test_tool_only_assistant_and_unrelated_user_msg_stay_uncertain(self):
        # Tool-only event cannot promote
        self.lc.invoke('profile_append', {
            'section': 'relationship', 'status': 'confirmed',
            'text': '她可能不喜欢下雨天', 'sources': ['event:e_tool', 'memory:m_tool'],
        })
        # Assistant message cannot promote
        self.lc.invoke('profile_append', {
            'section': 'relationship', 'status': 'confirmed',
            'text': '以后别猜心情，直接问。', 'sources': ['chat:a1'],
        })
        # Real but unrelated user message cannot promote
        self.lc.invoke('profile_append', {
            'section': 'relationship', 'status': 'confirmed',
            'text': '加班时不要发长消息打扰她。', 'sources': ['chat:u2'],
        })
        items = self.lc.read()['relationship']['items']
        self.assertEqual(len(items), 3)
        for item in items:
            self.assertEqual(item['status'], 'uncertain')
            self.assertEqual(item['source'], 'peter')

    def test_same_origin_dedup_and_stale_ref_degradation(self):
        # Both chat:u1 and event:e1 point to u1 -> counted as 1 origin, still valid
        self.lc.invoke('profile_append', {
            'section': 'relationship', 'status': 'confirmed',
            'text': '不要猜Lan心情，直接问她。', 'sources': ['chat:u1', 'event:e1'],
        })
        # Now delete event e1 from brain.db -> partial_stale (chat:u1 still valid!)
        self.db.execute("DELETE FROM events WHERE event_id='e1'")
        self.db.commit()
        live = self.lc.relationship_items()
        self.assertTrue(live[0]['stale_ref'])
        self.assertEqual(live[0]['missing_refs'], ['event:e1'])
        self.assertEqual(live[0].get('effective_status', live[0]['status']), 'confirmed')

        # Now remove u1 from chat.jsonl -> all evidence invalid -> safely degrades!
        self.chat_path.write_text(
            json.dumps({'role': 'user', 'message_id': 'u2', 'content': '其他消息'}) + '\n',
            encoding='utf-8',
        )
        self.lc.invalidate_evidence_cache()
        live2 = self.lc.relationship_items()
        self.assertTrue(live2[0]['evidence_degraded'])
        self.assertEqual(live2[0]['effective_status'], 'uncertain')
        # Base rendering must NOT render it as confirmed consensus
        base = build_relationship_base(self.lc)
        self.assertIn('(not confirmed yet)', base['text'])


class UserSovereigntyAndConsistencyTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        (self.root / 'peter').mkdir(parents=True, exist_ok=True)
        (self.root / 'peter' / 'chat.jsonl').write_text(
            json.dumps({'role': 'user', 'message_id': 'u1', 'content': '记住这个约定：有事直说。'}) + '\n',
            encoding='utf-8',
        )
        self.lc = LearnedContext(self.root, 'peter', {
            'file': 'learned-context.json', 'legacy_file': 'about-lan.md',
        })

    def tearDown(self):
        self.tmp.cleanup()

    def test_locked_item_cannot_be_overwritten_and_deleted_cannot_resurrect(self):
        self.lc.save_relationship([
            {'id': 'r1', 'text': '有事直说，不冷战。', 'status': 'confirmed', 'locked': True},
            {'id': 'r2', 'text': '不要睡太晚。', 'status': 'confirmed'},
        ])
        # Peter tries to replace locked item -> rejected
        _, st, _ = self.lc.invoke('profile_replace', {
            'section': 'relationship', 'old_text': '有事直说，不冷战。', 'new_text': '随便',
        })
        self.assertEqual(st, '被拒绝')

        # Lan deletes r2 -> recorded in vetoed -> Peter cannot re-add it even with chat:u1
        self.lc.save_relationship([
            {'id': 'r1', 'text': '有事直说，不冷战。', 'status': 'confirmed', 'locked': True},
        ])
        _, st2, _ = self.lc.invoke('profile_append', {
            'section': 'relationship', 'status': 'confirmed',
            'text': '不要睡太晚。', 'sources': ['chat:u1'],
        })
        self.assertEqual(st2, '被拒绝')

    def test_restore_outdated_item_and_rollback_on_version_error(self):
        self.lc.invoke('profile_append', {
            'section': 'relationship', 'status': 'confirmed',
            'text': '有事直说，不冷战。', 'sources': ['chat:u1'],
        })
        old_id = self.lc.read()['relationship']['items'][0]['id']
        self.lc.invoke('profile_replace', {
            'section': 'relationship', 'old_text': '有事直说，不冷战。', 'new_text': '',
        })
        self.assertEqual(self.lc.read()['relationship']['items'][0]['status'], 'outdated')
        restored = self.lc.restore_relationship_item(old_id)
        current = [r for r in restored['relationship']['items'] if r['status'] == 'confirmed']
        self.assertEqual(len(current), 1)
        self.assertEqual(current[0]['source'], 'lan')
        self.assertEqual(current[0]['restored_from'], old_id)

        # Simulate version write failure -> data file must roll back cleanly
        with patch.object(self.lc, '_record', side_effect=UserError('disk full')):
            with self.assertRaises(UserError):
                self.lc.save_section('facts', 'corrupted partial write')
        self.assertEqual(self.lc.read()['facts']['text'], '')


class ProductionPipelineAndCompressionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        char_dir = self.root / 'characters' / 'peter'
        char_dir.mkdir(parents=True)
        (self.root / 'persona').mkdir(parents=True, exist_ok=True)
        (self.root / 'persona' / 'peter.md').write_text('You are Peter.', encoding='utf-8')
        (char_dir / 'character.toml').write_text(
            'name = "Peter"\npersona = "persona/peter.md"\nprovider = "default"\n'
            '[providers.default]\nbase_url = "https://example.invalid/v1"\napi_key_env = "TEST_KEY"\n'
            'model = "test-model"\nmax_input_tokens = 8192\n'
            'input_cny_per_million = "2"\ncached_input_cny_per_million = "1"\noutput_cny_per_million = "8"\n'
            '[profile]\nfile = "learned-context.json"\nlearned = true\n',
            encoding='utf-8',
        )
        self.cfg = load_character(self.root, 'peter')
        self.store = Store(self.root / 'data')
        self.budget = Budget(self.store)
        self.model = FakeChatModel('收到。')

    def tearDown(self):
        self.tmp.cleanup()

    def test_compression_preserves_locked_long_item_and_autonomy_receives_base(self):
        long_rule = (
            '在讨论技术方案和工作排期时，不要急着给安慰或空话，要先直接给出结论和依据；'
            '但如果Lan明确说今天很累了，就先顺着情绪陪她说话，不要再继续追问技术细节。'
            + '补充背景说明' * 40
        )
        lc = LearnedContext(self.root / 'data', self.cfg.id, self.cfg.profile)
        lc.save_relationship([
            {'id': 'locked1', 'text': long_rule, 'status': 'confirmed', 'locked': True},
            {'id': 'unc1', 'text': '她可能喜欢早睡', 'status': 'uncertain'},
        ])
        chosen = select_relationship_base(lc.relationship_items(), max_chars=120)
        self.assertTrue(any(item['id'] == 'locked1' for item in chosen['items']))
        locked_entry = next(item for item in chosen['items'] if item['id'] == 'locked1')
        self.assertTrue(locked_entry['compressed'])
        self.assertTrue(locked_entry['line'].endswith('…'))

        chat = Chat(self.cfg, self.store, self.budget, self.model, output=lambda _: None)
        # Normal chat with no recall
        chat.send('你好呀')
        # Autonomous wake
        chat.send('自主醒来', autonomy=True)
        self.assertEqual(len(self.model.requests), 2)
        for req in self.model.requests:
            joined = '\n'.join(m['content'] for m in req['messages'] if m.get('role') == 'system')
            self.assertIn('CURRENT BETWEEN YOU AND LAN', joined)
            self.assertIn('不要急着给安慰或空话', joined)

    def test_negation_polarity_and_newer_user_rebuttal_block_promotion(self):
        lc = LearnedContext(self.root / 'data', self.cfg.id, self.cfg.profile)
        chat_path = self.root / 'data' / self.cfg.id / 'chat.jsonl'
        chat_path.parent.mkdir(parents=True, exist_ok=True)
        chat_path.write_text(
            json.dumps({'role': 'user', 'message_id': 'u_neg_zh', 'time': '2026-11-26T10:00:00+08:00',
                        'content': '我从来不喜欢每天早起打卡提醒。'}) + '\n'
            + json.dumps({'role': 'user', 'message_id': 'u_neg_en', 'time': '2026-11-26T10:01:00+08:00',
                          'content': "I don't like daily morning reminders."}) + '\n'
            + json.dumps({'role': 'user', 'message_id': 'u_orig', 'time': '2026-11-26T10:02:00+08:00',
                          'content': '以后晚上十点提醒我喝水。'}) + '\n'
            + json.dumps({'role': 'user', 'message_id': 'u_rebut', 'time': '2026-11-26T10:05:00+08:00',
                          'content': '不对，晚上十点提醒我喝水那个取消吧，不用了。'}) + '\n',
            encoding='utf-8',
        )
        # Opposite polarity (ZH & EN) must stay uncertain
        lc.invoke('profile_append', {
            'section': 'relationship', 'status': 'confirmed',
            'text': '她喜欢每天早起打卡提醒。', 'sources': ['chat:u_neg_zh'],
        })
        lc.invoke('profile_append', {
            'section': 'relationship', 'status': 'confirmed',
            'text': 'She likes daily morning reminders.', 'sources': ['chat:u_neg_en'],
        })
        # Rebutted by newer user message must stay uncertain
        lc.invoke('profile_append', {
            'section': 'relationship', 'status': 'confirmed',
            'text': '晚上十点提醒她喝水。', 'sources': ['chat:u_orig'],
        })
        for item in lc.read()['relationship']['items']:
            self.assertEqual(item['status'], 'uncertain')
            self.assertEqual(item['source'], 'peter')

    def test_cross_section_veto_rephrase_s6_contract_and_lossless_rollback(self):
        lc = LearnedContext(self.root / 'data', self.cfg.id, self.cfg.profile)
        lc.save_section('facts', 'Lan likes oolong.')
        plan = lc.migrate_storage(dry_run=False)
        # Add post-migration cognition
        lc.save_relationship([
            {'id': 'post1', 'text': '晚上十一点以后不谈工作排期和代码问题。', 'status': 'confirmed', 'locked': True},
            {'id': 'veto1', 'text': '每天早上八点准时发送新闻摘要提醒。', 'status': 'confirmed'},
        ])
        lc.invoke('ongoing_update', {'action': 'add', 'text': '迁移后新增的待办事项'})
        # Lan deletes veto1 -> recorded in vetoed
        lc.save_relationship([
            {'id': 'post1', 'text': '晚上十一点以后不谈工作排期和代码问题。', 'status': 'confirmed', 'locked': True},
        ])
        # Rephrased resurrection in relationship OR cross-section into facts/growth must be blocked
        _, st_rephrase, _ = lc.submit_candidate(
            'relationship', '每天早上八点准时发送新闻摘要提醒！', status='confirmed', sources=[],
        )
        self.assertEqual(st_rephrase, '被拒绝')
        _, st_cross, _ = lc.submit_candidate('growth', '每天早上八点准时发送新闻摘要提醒。')
        self.assertEqual(st_cross, '被拒绝')
        # Rollback migration MUST preserve post-migration relationship & ongoing items
        if plan['backup_path']:
            restored = lc.rollback_migration(plan['backup_path'])
            rel_ids = {r['id'] for r in restored['relationship']['items']}
            ong_texts = {r['text'] for r in restored['ongoing']['items']}
            self.assertIn('post1', rel_ids)
            self.assertIn('迁移后新增的待办事项', ong_texts)

    def test_concurrent_writes_and_five_modes_plus_correction_diff(self):
        import threading
        lc = LearnedContext(self.root / 'data', self.cfg.id, self.cfg.profile)
        lc.save_relationship([
            {'id': 'r1', 'text': '不要在她忙的时候连发消息。', 'status': 'confirmed', 'locked': True},
        ])
        errors = []

        def worker(idx):
            try:
                lc.invoke('profile_append', {
                    'section': 'relationship', 'status': 'uncertain',
                    'text': f'并发猜测规则编号 {idx}',
                })
            except Exception as exc:
                errors.append(exc)

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(6)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(errors, [])
        self.assertGreaterEqual(len(lc.read()['relationship']['items']), 7)

        # Verify normal chat, follow-up, new session, autonomy, and user correction before/after
        chat = Chat(self.cfg, self.store, self.budget, self.model, output=lambda _: None)
        chat.send('普通闲聊第一句')
        chat.send('连续追问第二句')
        chat2 = Chat(self.cfg, self.store, self.budget, self.model, output=lambda _: None)
        chat2.send('新会话第一句')
        chat2.send('自主唤醒测试', autonomy=True)
        # Before correction: r1 is in all 4 requests
        self.assertEqual(len(self.model.requests), 4)
        for req in self.model.requests:
            sys_txt = '\n'.join(m['content'] for m in req['messages'] if m.get('role') == 'system')
            self.assertIn('不要在她忙的时候连发消息。', sys_txt)
        # Lan corrects r1 -> next request immediately uses corrected text and drops old text
        lc.save_relationship([
            {'id': 'r1', 'text': '忙的时候可以发一条简短留言，但不要催回复。', 'status': 'confirmed', 'locked': True},
        ])
        chat2.send('纠正后的下一轮对话')
        last_sys = '\n'.join(m['content'] for m in self.model.requests[-1]['messages'] if m.get('role') == 'system')
        self.assertIn('忙的时候可以发一条简短留言，但不要催回复。', last_sys)
        self.assertNotIn('不要在她忙的时候连发消息。', last_sys)


if __name__ == '__main__':
    unittest.main()
