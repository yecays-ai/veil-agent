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

    def test_one_budget_covers_the_whole_pass_and_degrades_safely(self):
        # Three model-promoted items would each wait on SQLite; one shared budget must cap the pass.
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
        self.lc.evidence_budget_ms = 80
        real_open = self.lc._open_brain_ro

        def slow_open(budget=None):
            time.sleep(0.12)
            return real_open(budget)

        with patch.object(self.lc, '_open_brain_ro', side_effect=slow_open):
            started = time.monotonic()
            prompt = self.lc.build_prompt()
            elapsed = time.monotonic() - started
            items = self.lc.relationship_items()
        self.assertLess(elapsed, 1.0)
        states = [item.get('semantic_state') for item in items]
        self.assertTrue(any(state in ('unavailable', 'stale') or item.get('budget_exhausted')
                            for state, item in zip(states, items)))
        self.assertNotIn('周末不发长消息', prompt.get('stable') or '')


class CounterEvidenceBoundsTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.lc = LearnedContext(Path(tmp.name), 'fake', {'file': 'learned-context.json'})
        self.lc.root.mkdir()

    def test_identical_vetoes_are_deduplicated(self):
        data = self.lc.read()
        data['facts']['vetoed'] = [{'text': '不要在周末发消息', 'norm_text': '不要在周末发消息'}]
        data['growth']['vetoed'] = [{'text': '不要在周末发消息', 'norm_text': '不要在周末发消息'}]
        item = {'id': 'r1', 'text': '周末不发消息', 'status': 'confirmed', 'source': 'peter'}
        ev = {'raw_origins': [], 'chat_corrections': []}
        counters = self.lc._semantic_counters(data, item, ev)
        self.assertEqual(1, len([row for row in counters if row['kind'] == 'veto']))

    def test_overflow_fails_closed_without_truncating_vetoes(self):
        data = self.lc.read()
        data['relationship']['vetoed'] = [
            {'text': '用户否决的独立事项 %d' % i, 'norm_text': '用户否决的独立事项 %d' % i}
            for i in range(121)
        ]
        item = {'id': 'r1', 'text': 'A calm new note.', 'status': 'uncertain', 'source': 'peter'}
        ev = {'raw_origins': [], 'raw_provenance': {}, 'source_unavailable': False,
              'missing_refs': [], 'valid_origin_count': 0, 'kept_refs': [],
              'chat_corrections': [], 'chat_index': {}, 'evidence_state': 'none', 'rebutted': False}
        state, fingerprints, payload = self.lc._semantic_basis(data, item, ev, 'rule')
        self.assertEqual('counterevidence_overflow', state)
        self.assertIsNone(payload)


if __name__ == '__main__':
    unittest.main()
