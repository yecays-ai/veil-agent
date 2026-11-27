"""Ticket 461: S6 evidence gate — incremental chat index, bounded pages, wall-clock budget."""
import json
import os
import sqlite3
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from veil.brain import learned_context as lc_mod
from veil.brain import relationship_base
from veil.brain.learned_context import LearnedContext, _EvidenceBudget


class ChatEvidenceIndexTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.lc = LearnedContext(self.root, 'fake', {'file': 'learned-context.json'})
        self.lc.root.mkdir()
        self.chat = self.lc.root / 'chat.jsonl'

    def write_rows(self, rows):
        self.chat.write_text(''.join(json.dumps(row, ensure_ascii=False) + '\n' for row in rows),
                             encoding='utf-8')

    def user_row(self, mid, text, **extra):
        return {'id': mid, 'role': 'user', 'content': text, 'time': '2026-11-20T10:00:00+08:00', **extra}

    def test_incremental_parse_does_not_reparse_old_rows(self):
        self.write_rows([self.user_row('u%d' % i, '消息 %d' % i) for i in range(80)])
        self.lc._chat_user_index()
        state = self.lc._chat_state
        self.assertEqual(self.chat.stat().st_size, state['offset'])
        real = self.lc._apply_chat_row
        with patch.object(self.lc, '_apply_chat_row', wraps=real) as spy:
            with self.chat.open('a', encoding='utf-8') as handle:
                handle.write(json.dumps(self.user_row('new', '新追加的一条'), ensure_ascii=False) + '\n')
            self.lc._chat_user_index()
        self.assertEqual(1, spy.call_count)

    def test_bounded_cache_still_resolves_old_evidence_on_demand(self):
        self.write_rows([self.user_row('u%d' % i, '旧消息内容 %d' % i) for i in range(40)])
        with patch.object(lc_mod, 'CHAT_CACHE_LIMIT', 5):
            index = self.lc._chat_user_index()
        self.assertLessEqual(len(index), 5)
        self.assertNotIn('u0', index)
        info, status = self.lc._chat_lookup('u0')
        self.assertEqual('ok', status)
        self.assertEqual('旧消息内容 0', info['text'])
        self.assertEqual(('missing'), self.lc._chat_lookup('does-not-exist')[1])

    def test_same_size_rewrite_with_unchanged_mtime_never_hits_old_cache(self):
        self.write_rows([self.user_row('u1', '原始内容，稍后会被改写。')])
        self.lc._chat_user_index()
        old = self.chat.stat()
        data = self.chat.read_text(encoding='utf-8').replace('u1', 'u2')
        self.chat.write_text(data, encoding='utf-8')
        os.utime(self.chat, ns=(old.st_atime_ns, old.st_mtime_ns))
        index = self.lc._chat_user_index()
        self.assertNotIn('u1', index)
        self.assertIn('u2', index)
        self.assertEqual('missing', self.lc._chat_lookup('u1')[1])

    def test_deleted_log_is_missing_evidence_not_stale_cache(self):
        self.write_rows([self.user_row('u1', '会被删除。')])
        self.lc._chat_user_index()
        self.chat.unlink()
        self.assertEqual({}, self.lc._chat_user_index())
        self.assertEqual('missing', self.lc._chat_lookup('u1')[1])

    def test_duplicate_correction_is_not_counterevidence(self):
        self.write_rows([
            self.user_row('c1', '不对，周末可以发消息'),
            self.user_row('c1', '不对，周末可以发消息'),
        ])
        corrections = self.lc._chat_corrections()
        self.assertEqual([], corrections)

    def test_evicted_yes_keeps_the_preceding_question(self):
        rows = [
            {'id': 'q1', 'role': 'assistant', 'content': '你喜欢喝茶吗？', 'time': '2026-11-20T09:00:00+08:00'},
            self.user_row('yes', '是的'),
        ]
        rows.extend(self.user_row('u%d' % i, '填充消息 %d' % i) for i in range(8))
        self.write_rows(rows)
        with patch.object(lc_mod, 'CHAT_CACHE_LIMIT', 2):
            index = self.lc._chat_user_index()
            self.assertNotIn('yes', index)
            info, status = self.lc._chat_lookup('yes')
        self.assertEqual('ok', status)
        self.assertEqual('你喜欢喝茶', info['confirmation_target'])
        self.assertEqual({'id': 'chat:q1', 'text': '你喜欢喝茶吗？'}, info['question'])

    def test_repeated_question_id_is_not_a_confirmation(self):
        self.write_rows([
            {'id': 'q1', 'role': 'assistant', 'content': '你喜欢喝茶吗？'},
            self.user_row('yes', '是的'),
            {'id': 'q1', 'role': 'assistant', 'content': '你喜欢喝茶吗？'},
            self.user_row('tail', '后面还有一条'),
        ])
        with patch.object(lc_mod, 'CHAT_CACHE_LIMIT', 1):
            self.lc._chat_user_index()
            info, status = self.lc._chat_lookup('yes')
        self.assertEqual('ok', status)
        self.assertNotIn('confirmation_target', info)
        self.assertNotIn('question', info)

    def test_duplicate_and_recalled_rows_are_never_evidence(self):
        self.write_rows([
            self.user_row('u1', '重复消息'),
            self.user_row('u1', '重复消息'),
            self.user_row('u2', '已召回消息', recalled=True),
        ])
        index = self.lc._chat_user_index()
        self.assertNotIn('u1', index)
        self.assertNotIn('u2', index)
        self.assertEqual('missing', self.lc._chat_lookup('u1')[1])
        info, status = self.lc._chat_lookup('u2')
        self.assertEqual('missing', status)
        self.assertIsNone(info)

    def test_seen_eviction_does_not_resurrect_a_duplicate_or_drop_a_unique_id(self):
        rows = [self.user_row('old-dup', '第一次出现')]
        rows.extend(self.user_row('u%d' % i, '唯一消息 %d' % i) for i in range(6))
        rows.append(self.user_row('old-dup', '淘汰之后又出现'))
        self.write_rows(rows)
        with patch.object(lc_mod, 'CHAT_SEEN_LIMIT', 3):
            index = self.lc._chat_user_index()
            self.assertNotIn('old-dup', index)
            self.assertEqual('missing', self.lc._chat_lookup('old-dup')[1])
            info, status = self.lc._chat_lookup('u0')
        self.assertEqual('ok', status)
        self.assertEqual('唯一消息 0', info['text'])

    def test_evicted_duplicate_id_stays_missing(self):
        self.write_rows([
            self.user_row('d1', '重复甲'),
            self.user_row('d1', '重复甲'),
            self.user_row('d2', '重复乙'),
            self.user_row('d2', '重复乙'),
            self.user_row('d1', '重复甲又来了'),
        ])
        with patch.object(lc_mod, 'CHAT_DUP_LIMIT', 1):
            index = self.lc._chat_user_index()
            self.assertNotIn('d1', index)
            self.assertNotIn('d2', index)
            self.assertEqual('missing', self.lc._chat_lookup('d1')[1])
            self.assertEqual('missing', self.lc._chat_lookup('d2')[1])

    def test_evicted_correction_is_still_returned(self):
        rows = [self.user_row('c0', '不对，旧的纠正必须留下', time='2026-11-20T09:00:00+08:00')]
        rows.extend(self.user_row('u%d' % i, '普通消息 %d' % i) for i in range(3))
        rows.append(self.user_row('c1', '不对，新的纠正也在', time='2026-11-20T12:00:00+08:00'))
        self.write_rows(rows)
        with patch.object(lc_mod, 'CHAT_CORRECTION_LIMIT', 1):
            corrections = self.lc._chat_corrections()
        self.assertEqual(['c0', 'c1'], [row['id'] for row in corrections])

    def test_unfinished_correction_scan_is_unavailable_not_empty(self):
        claim = '我们约好了，每次分开超过三天，重逢时先拥抱再开口。'
        self.write_rows([
            self.user_row('origin', claim, time='2026-11-20T08:00:00+08:00'),
            self.user_row('c0', '不对，旧的纠正必须留下', time='2026-11-20T09:00:00+08:00'),
            self.user_row('c1', '不对，新的纠正也在', time='2026-11-20T12:00:00+08:00'),
        ])
        with patch.object(lc_mod, 'CHAT_CORRECTION_LIMIT', 1):
            self.assertEqual(['c0', 'c1'], [row['id'] for row in self.lc._chat_corrections()])
            budget = _EvidenceBudget(1000)
            budget.deadline = time.monotonic() - 1
            self.assertIsNone(self.lc._scan_chat_corrections(budget))
            with patch.object(self.lc, '_scan_chat_corrections', return_value=None):
                ev = self.lc._evaluate_sources(['chat:origin'], claim, self.lc.read())
        self.assertTrue(ev['corrections_unavailable'])
        self.assertEqual(1, ev['valid_origin_count'])
        self.assertEqual('unavailable', ev['evidence_state'])
        self.assertEqual([], ev['chat_corrections'])


class EvidencePageAndBudgetTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.lc = LearnedContext(self.root, 'fake', {'file': 'learned-context.json'})
        self.lc.root.mkdir()
        self.claim = '周末不要发长消息'
        self.chat = self.lc.root / 'chat.jsonl'
        self.chat.write_text(json.dumps({'id': 'u-old', 'role': 'user', 'content': self.claim},
                                        ensure_ascii=False) + '\n', encoding='utf-8')
        self.db = sqlite3.connect(self.lc.root / 'brain.db')
        self.addCleanup(self.db.close)
        self.db.executescript("""
        CREATE TABLE memories (id TEXT, is_tombstone INTEGER, is_deleted INTEGER);
        CREATE TABLE events (event_id TEXT, memory_id TEXT);
        CREATE TABLE event_ledger (id INTEGER PRIMARY KEY, ledger_seq INTEGER, kind TEXT,
                                   event_id TEXT, payload_json TEXT);
        CREATE TABLE quotes (memory_id TEXT, ord INTEGER, speaker TEXT, text TEXT);
        CREATE TABLE sources (memory_id TEXT, ord INTEGER, kind TEXT, ref TEXT, status TEXT);
        INSERT INTO memories VALUES ('m1', 0, 0);
        INSERT INTO events VALUES ('e1', 'm1');
        """)
        self.db.commit()

    def add_ledger(self, seq, mid, text):
        self.db.execute(
            "INSERT INTO event_ledger(ledger_seq, kind, event_id, payload_json) VALUES(?,?,?,?)",
            (seq, 'stage-evidence', 'e1', json.dumps({'kind': 'chat', 'role': 'user',
                                                     'message_id': mid, 'text': text})))

    def test_validation_finds_evidence_outside_the_old_fixed_window(self):
        for seq in range(2, 262):
            self.add_ledger(seq, 'd%d' % seq, '无关内容 %d' % seq)
        self.add_ledger(1, 'u-old', self.claim)
        self.db.commit()
        ev = self.lc._evaluate_sources(['event:e1'], self.claim)
        self.assertEqual(1, ev['valid_origin_count'])
        self.assertEqual(['chat:u-old'], [row['id'] for row in ev['raw_origins']])

    def test_truncated_window_is_unavailable_not_invalid(self):
        for seq in range(2, 1202):
            self.add_ledger(seq, 'd%d' % seq, '无关内容 %d' % seq)
        self.add_ledger(1, 'u-old', self.claim)
        self.db.commit()
        with patch.object(lc_mod, 'EVIDENCE_STAGE_PAGES', 2):
            ev = self.lc._evaluate_sources(['event:e1'], self.claim)
        self.assertIn(ev['evidence_state'], ('unavailable', 'none'))
        self.assertTrue(ev['source_unavailable'])
        self.assertNotIn('event:e1', ev['missing_refs'])

    def test_same_ledger_seq_does_not_skip_the_lower_rowid(self):
        # The matching row is inserted first, so it has the lower rowid. A page of one row
        # returns the higher rowid first; the next page must still see the tie.
        self.add_ledger(5, 'u-old', self.claim)
        self.add_ledger(5, 'noise', '无关内容')
        self.db.commit()
        with patch.object(lc_mod, 'EVIDENCE_PAGE_ROWS', 1):
            ev = self.lc._evaluate_sources(['event:e1'], self.claim)
        self.assertEqual(1, ev['valid_origin_count'])
        self.assertEqual(['chat:u-old'], [row['id'] for row in ev['raw_origins']])

    def test_one_budget_covers_the_whole_pass_and_degrades_safely(self):
        conn = sqlite3.connect(str(self.lc.root / 'brain.db'))
        conn.execute("INSERT INTO event_ledger(ledger_seq, kind, event_id, payload_json) "
                     "VALUES(1, 'stage-evidence', 'e1', ?)",
                     (json.dumps({'kind': 'chat', 'role': 'user', 'message_id': 'u-old',
                                  'text': self.claim}),))
        conn.commit()
        conn.close()
        for i in range(3):
            self.lc.submit_candidate('relationship', '周末不发长消息 %d' % i, status='confirmed',
                                     sources=('event:e1',))
        opens = []
        real_open = self.lc._open_brain_ro

        def exhaust_shared(budget=None):
            opens.append(1)
            result = real_open(budget)
            if budget is not None:
                budget.deadline = time.monotonic()
            return result

        data = self.lc._load()
        with patch.object(self.lc, '_open_brain_ro', side_effect=exhaust_shared):
            items = self.lc._apply_live_evidence_check(data)
        self.assertEqual(1, len(opens))
        self.assertEqual('unavailable', items[1]['semantic_state'])
        self.assertEqual('unavailable', items[2]['semantic_state'])
        self.assertTrue(items[1].get('budget_exhausted'))
        self.assertTrue(items[2].get('budget_exhausted'))

    def test_promoted_note_leaves_the_relationship_prompt_when_the_budget_runs_out(self):
        conn = sqlite3.connect(str(self.lc.root / 'brain.db'))
        conn.execute("INSERT INTO event_ledger(ledger_seq, kind, event_id, payload_json) "
                     "VALUES(1, 'stage-evidence', 'e1', ?)",
                     (json.dumps({'kind': 'chat', 'role': 'user', 'message_id': 'u-old',
                                  'text': self.claim}),))
        conn.commit()
        conn.close()
        self.lc.submit_candidate('relationship', self.claim, status='confirmed', sources=('event:e1',))
        self.assertIn(self.claim, relationship_base.build(self.lc)['text'])
        real_open = self.lc._open_brain_ro

        def exhaust_shared(budget=None):
            result = real_open(budget)
            if budget is not None:
                budget.deadline = time.monotonic()
            return result

        with patch.object(self.lc, '_open_brain_ro', side_effect=exhaust_shared):
            rendered = relationship_base.build(self.lc)['text']
        self.assertNotIn(self.claim, rendered)

    def test_budget_skip_keeps_a_saved_semantic_veto(self):
        data = self.lc._load()
        data['relationship']['items'] = [
            {'id': 'r1', 'text': '英文译文', 'status': 'uncertain', 'source': 'peter',
             'sources': ['chat:u-old'], 'verification': {'was_contradicted': True}},
            {'id': 'r2', 'text': '锁定译文', 'status': 'uncertain', 'source': 'peter', 'locked': True,
             'sources': ['chat:u-old'], 'verification': {'was_contradicted': True}},
        ]
        budget = _EvidenceBudget(50)
        budget.deadline = time.monotonic() - 1
        items = self.lc._apply_live_evidence_check(data, budget)
        self.assertTrue(items[0]['prompt_vetoed'])
        self.assertNotIn('prompt_vetoed', items[1])

    @unittest.skipUnless(os.name == 'nt', 'real SQLite exclusive-lock behaviour is verified on Windows')
    def test_real_sqlite_exclusive_lock_is_bounded_and_never_promotes(self):
        conn = sqlite3.connect(str(self.lc.root / 'brain.db'), isolation_level=None)
        self.addCleanup(conn.close)
        conn.execute("INSERT INTO event_ledger(ledger_seq, kind, event_id, payload_json) "
                     "VALUES(1, 'stage-evidence', 'e1', ?)",
                     (json.dumps({'kind': 'chat', 'role': 'user', 'message_id': 'u-old',
                                  'text': self.claim}),))
        for i in range(4):
            self.lc.submit_candidate('relationship', '周末不发长消息 %d' % i, status='confirmed',
                                     sources=('event:e1',))
        self.lc.evidence_budget_ms = 300
        conn.execute('BEGIN EXCLUSIVE')
        started = time.monotonic()
        prompt = self.lc.build_prompt()
        items = self.lc.relationship_items()
        elapsed = time.monotonic() - started
        conn.execute('ROLLBACK')
        self.assertLess(elapsed, 3.0)
        self.assertNotIn('周末不发长消息', prompt.get('stable') or '')
        self.assertTrue(all(item.get('effective_status') != 'confirmed' for item in items))
        self.assertTrue(any(item.get('budget_exhausted') or item.get('semantic_state') in
                            ('unavailable', 'stale') for item in items))


class CounterEvidenceBoundsTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.lc = LearnedContext(Path(tmp.name), 'fake', {'file': 'learned-context.json'})
        self.lc.root.mkdir()
        self.chat = self.lc.root / 'chat.jsonl'

    def test_identical_vetoes_are_deduplicated(self):
        data = self.lc.read()
        data['facts']['vetoed'] = [{'text': '不要在周末发消息', 'norm_text': '不要在周末发消息'}]
        data['growth']['vetoed'] = [{'text': '不要在周末发消息', 'norm_text': '不要在周末发消息'}]
        item = {'id': 'r1', 'text': '周末不发消息', 'status': 'confirmed', 'source': 'peter'}
        ev = {'raw_origins': [], 'chat_corrections': []}
        counters = self.lc._semantic_counters(data, item, ev)
        self.assertEqual(1, len([row for row in counters if row['kind'] == 'veto']))

    def test_unrelated_overflow_does_not_block_a_standing_agreement(self):
        claim = '我们约好了，每次分开超过三天，重逢时先拥抱再开口。'
        data = self.lc.read()
        data['relationship']['vetoed'] = [
            {'text': '用户否决的独立事项 %d' % i, 'norm_text': '用户否决的独立事项 %d' % i}
            for i in range(121)
        ]
        self.lc._write(data)
        self.chat.write_text(json.dumps({'id': 'u1', 'role': 'user', 'content': claim},
                                        ensure_ascii=False) + '\n', encoding='utf-8')
        _, state, _ = self.lc.submit_candidate('relationship', claim, status='confirmed',
                                               sources=('chat:u1',))
        self.assertEqual('成功', state)
        item = self.lc.relationship_items()[0]
        self.assertEqual('literal', item['semantic_state'])
        self.assertEqual('confirmed', item['status'])
        self.assertIn(claim, relationship_base.build(self.lc)['text'])
        self.assertNotIn('not confirmed', relationship_base.build(self.lc)['text'])

    def test_relevant_veto_still_blocks_when_unrelated_history_overflows(self):
        claim = '我们约好了，每次分开超过三天，重逢时先拥抱再开口。'
        data = self.lc.read()
        data['relationship']['vetoed'] = [
            {'text': '用户否决的独立事项 %d' % i, 'norm_text': '用户否决的独立事项 %d' % i}
            for i in range(120)
        ]
        data['relationship']['vetoed'].append({'text': '不要在重逢时先拥抱', 'norm_text': '不要在重逢时先拥抱'})
        self.lc._write(data)
        self.chat.write_text(json.dumps({'id': 'u1', 'role': 'user', 'content': claim},
                                        ensure_ascii=False) + '\n', encoding='utf-8')
        _, state, _ = self.lc.submit_candidate('relationship', claim, status='confirmed',
                                               sources=('chat:u1',))
        self.assertEqual('成功', state)
        self.assertNotIn(claim, relationship_base.build(self.lc)['text'])
        task, = self.lc.pending_relationship_semantics()
        texts = [row['text'] for row in task['payload']['counterevidence']]
        self.assertIn('不要在重逢时先拥抱', texts)
        self.assertFalse(any(text.startswith('用户否决的独立事项') for text in texts))
        self.assertTrue(self.lc.claim_relationship_semantic(task['claim_id']))
        self.assertTrue(self.lc.finish_relationship_semantic(task['claim_id'], verdict='contradicted'))
        self.assertNotIn(claim, relationship_base.build(self.lc)['text'])
        self.assertEqual('contradicted', self.lc.relationship_items()[0]['semantic_state'])

    def test_exact_veto_still_rejects_the_write_above_the_batch_limit(self):
        claim = '我们约好了，每次分开超过三天，重逢时先拥抱再开口。'
        data = self.lc.read()
        data['relationship']['vetoed'] = [
            {'text': '用户否决的独立事项 %d' % i, 'norm_text': '用户否决的独立事项 %d' % i}
            for i in range(120)
        ]
        data['relationship']['vetoed'].append({'text': claim, 'norm_text': claim})
        self.lc._write(data)
        _, state, _ = self.lc.submit_candidate('relationship', claim, status='confirmed', sources=())
        self.assertEqual('被拒绝', state)
        self.assertNotIn(claim, relationship_base.build(self.lc)['text'])

    def test_relevant_counters_promote_only_after_the_last_clear_batch(self):
        claim = '我们约好了，每次分开超过三天，重逢时先拥抱再开口。'
        data = self.lc.read()
        data['relationship']['vetoed'] = [
            {'text': '不要拥抱事项%d' % i, 'norm_text': '不要拥抱事项%d' % i}
            for i in range(121)
        ]
        self.lc._write(data)
        self.chat.write_text(json.dumps({'id': 'u1', 'role': 'user', 'content': claim},
                                        ensure_ascii=False) + '\n', encoding='utf-8')
        _, state, _ = self.lc.submit_candidate('relationship', claim, status='confirmed',
                                               sources=('chat:u1',))
        self.assertEqual('成功', state)
        first, = self.lc.pending_relationship_semantics()
        self.assertEqual(120, len(first['payload']['counterevidence']))
        self.assertTrue(self.lc.claim_relationship_semantic(first['claim_id']))
        self.assertTrue(self.lc.finish_relationship_semantic(first['claim_id'], verdict='supported'))
        item = self.lc.relationship_items()[0]
        self.assertNotEqual('confirmed', item.get('effective_status', item['status']))
        self.assertNotIn(claim, relationship_base.build(self.lc)['text'])
        self.assertEqual(1, self.lc.read()['relationship']['items'][0]['verification']['counter_batch'])
        second, = self.lc.pending_relationship_semantics()
        self.assertNotEqual(first['claim_id'], second['claim_id'])
        self.assertEqual(1, len(second['payload']['counterevidence']))
        self.assertTrue(self.lc.claim_relationship_semantic(second['claim_id']))
        self.assertTrue(self.lc.finish_relationship_semantic(second['claim_id'], verdict='supported'))
        self.assertEqual('confirmed', self.lc.relationship_items()[0]['status'])
        self.assertIn(claim, relationship_base.build(self.lc)['text'])


if __name__ == '__main__':
    unittest.main()
