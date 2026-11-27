import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from veil.brain.learned_context import LearnedContext


class Pr460EvidenceTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.lc = LearnedContext(Path(tmp.name), 'fake', {'file': 'learned-context.json'})
        self.lc.root.mkdir()
        self.chat_path = self.lc.root / 'chat.jsonl'
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

    def chat(self, *entries):
        self.chat_path.write_text(''.join(
            json.dumps({'role': role, 'message_id': mid, 'content': text}) + '\n'
            if role else '{bad json}\n' for mid, role, text in entries
        ), encoding='utf-8')

    def ledger(self, mid, text):
        self.db.execute("INSERT INTO event_ledger VALUES (NULL, 1, 'stage-evidence', 'e1', ?)",
                        (json.dumps({'kind': 'chat', 'role': 'user', 'message_id': mid,
                                     'text': text}),))
        self.db.commit()

    def quote(self, text):
        self.db.execute("INSERT INTO quotes VALUES ('m1', 1, 'user', ?)", (text,))
        self.db.commit()

    def origins(self, claim, *sources):
        return self.lc._evaluate_sources(sources, claim)['valid_origin_count']

    def test_direct_support_rejects_inference_without_entailment(self):
        for raw, claim in [
            ('嗯', '以后只在周末聊天'),
            ('不行', '以后只在周末聊天'),
            ('我只在周末喝茶', 'I only drink tea on weekends'),
            ('我只在周末喝茶', '我只在工作日喝茶'),
            ('我只在周末喝茶', '喝茶'),
            ('不要在周末发消息', '不要在周末打电话'),
            ('以后在周末发消息', '不要在周末发消息'),
            ('小明说我喜欢柠檬茶', '我喜欢柠檬茶'),
            ('小明说我喜欢柠檬茶', '小明说我喜欢柠檬茶'),
            ('“以后只在周末聊天”', '以后只在周末聊天'),
            ('我可能只在周末喝茶', '我可能只在周末喝茶'),
        ]:
            with self.subTest(raw=raw, claim=claim):
                self.chat(('user', 'user', raw))
                self.assertEqual(0, self.origins(claim, 'chat:user'))
        self.chat(('user', 'user', '不要在周末发消息。'))
        self.assertEqual(1, self.origins('不要在周末发消息', 'chat:user'))

    def test_short_ack_requires_the_immediately_preceding_unique_question(self):
        claim = '周末不发消息'
        self.chat(('ask', 'assistant', claim + '，对吗？'), ('ack', 'user', '对。'))
        self.assertEqual(1, self.origins(claim, 'chat:ack'))
        self.assertEqual(0, self.origins('周末不打电话', 'chat:ack'))
        self.assertEqual(0, self.origins(claim, 'chat:ask'))
        self.chat(('ask', 'assistant', 'Do you like tea?'), ('ack', 'user', 'Yes.'))
        self.assertEqual(1, self.origins('You like tea', 'chat:ack'))
        for expr in (claim, claim + '，也不打电话，对吗？', claim + '还是工作日不发消息？',
                     '小明说周末不发消息，对吗？', '“周末不发消息”，对吗？',
                     '上次我们说的是周末不发消息，对吗？', '只要有空就周末不发消息吗？'):
            with self.subTest(expr=expr):
                self.chat(('ask', 'assistant', expr), ('ack', 'user', '对'))
                self.assertEqual(0, self.origins(claim, 'chat:ack'))
        for gap in (('gap', 'user', '今天天气不错'), ('gap', 'tool', '完成'),
                    ('gap', '', ''), ('gap', 'assistant', '收到')):
            with self.subTest(gap=gap):
                self.chat(('ask', 'assistant', claim + '吗？'), gap, ('ack', 'user', '对'))
                self.assertEqual(0, self.origins(claim, 'chat:ack'))

    def test_short_ack_via_event_loses_support_when_its_question_disappears(self):
        claim = '周末不发消息'
        self.ledger('ack', '对')
        self.quote('对')
        self.chat(('ask', 'assistant', claim + '吗？'), ('ack', 'user', '对'))
        self.assertEqual(1, self.origins(claim, 'chat:ack', 'event:e1', 'memory:m1'))
        self.chat(('ack', 'user', '对'))
        self.assertEqual(0, self.origins(claim, 'chat:ack', 'event:e1', 'memory:m1'))

    def test_event_and_quotes_deduplicate_via_raw_chat_and_degrade_after_deletion(self):
        claim = '周末不要发长消息'
        self.chat(('u1', 'user', claim))
        self.ledger('u1', claim)
        self.quote(claim)
        self.assertEqual(1, self.origins(claim, 'chat:u1', 'event:e1', 'memory:m1'))
        self.lc.submit_candidate('relationship', claim, status='confirmed', sources=('event:e1', 'memory:m1'))
        self.assertEqual('confirmed', self.lc.relationship_items()[0]['status'])
        self.chat_path.unlink()
        live = self.lc.relationship_items()[0]
        self.assertEqual('uncertain', live['effective_status'])
        self.assertEqual(['event:e1', 'memory:m1'], live['missing_refs'])
        self.assertEqual('uncertain', self.lc.reverify_relationship_evidence()['items'][0]['status'])

    def test_bad_raw_or_mismatched_ledger_never_falls_back_to_copied_text(self):
        claim = '周末不要发长消息'
        self.ledger('u1', claim)
        self.quote(claim)
        for entries in [(('u1', 'assistant', claim),), (('u1', 'user', ''),),
                        (('u1', 'user', '周末可以发消息'), ('u2', 'user', claim)),
                        (('u1', 'user', claim), ('u1', 'assistant', claim))]:
            with self.subTest(entries=entries):
                self.chat(*entries)
                self.assertEqual(0, self.origins(claim, 'event:e1', 'memory:m1'))
        self.chat(('u1', 'user', '周末不发消息'))
        self.assertEqual(0, self.origins('周末不发消息', 'event:e1', 'memory:m1'))

    def test_quote_with_authoritative_chat_ref_is_not_a_second_origin(self):
        claim = '周末不要发长消息'
        self.chat(('u1', 'user', claim))
        self.quote(claim)
        self.db.execute("INSERT INTO sources VALUES ('m1', 1, 'chat', 'u1', NULL)")
        self.db.commit()
        self.assertEqual(1, self.origins(claim, 'memory:m1', 'chat:u1'))
        self.chat(('u2', 'user', claim))
        self.assertEqual(0, self.origins(claim, 'memory:m1'))

    def test_deleted_memory_disables_related_event_and_quote_proof(self):
        claim = '周末不要发长消息'
        self.chat(('u1', 'user', claim))
        self.ledger('u1', claim)
        self.quote(claim)
        self.db.execute("UPDATE memories SET is_deleted=1 WHERE id='m1'")
        self.db.commit()
        self.assertEqual(0, self.origins(claim, 'event:e1', 'memory:m1'))
        self.assertEqual(1, self.origins(claim, 'chat:u1'))
