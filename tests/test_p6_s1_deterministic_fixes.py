"""P6-S1: deterministic retrieval, card and metadata fixes.

Covers: CRLF/BOM card_text and raw_markdown metadata sync (G-A04); one shared embedding per
turn (G-A05); active search date filter and configured weights (G-A09); dynamic embedding
dimensions plus model-identity protection (G-A14); separate embedding/judge-model timeouts and
explicit degradation diagnostics (G-A12); and the removed dead reuse/drift branches (G-B01/G-A08).

All fixtures are fictional, local and zero-cost: fake embedders and fake judge models only.
"""
import json
import sqlite3
from contextlib import contextmanager
import tempfile
import threading
import time
import unittest
from concurrent.futures import Future, ThreadPoolExecutor
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from veil.brain.recall import brain_retrieval
from veil.brain.storage import brain_writer
from veil.brain.storage import veil_brain
from veil.brain.storage.brain_read import BrainRead
from veil.brain.memory_agent import Memory
from veil.brain.recall.recall_judge import DEFAULTS as JUDGE_DEFAULTS, JudgeFailed, RecallJudge, card_text, split_card
from veil.core.veil_agent import Budget, Character, Store

CRLF_RAW = ('---\r\nid: old\r\ntitle: 老记忆\r\nimportance: 8\r\ntags:\r\n- 那年\r\n---\r\n'
            '开头一句关于蓝色玩笑的内容。' + '中间是一段很长很长的对话。' * 5 + '结尾是那只蓝色风筝。\r\n'
            '关键词：蓝色玩笑、风筝\r\n')


class CountingEmbedder:
    """A 3-D or 1024-D fake embedding provider, with its model name for identity checks."""

    def __init__(self, vector, model='fake-embedding'):
        self.vector, self.model, self.calls = list(vector), model, 0

    def embed(self, _text):
        self.calls += 1
        return list(self.vector)


class FakeJudgeModel:
    def __init__(self, answer='{"ids": []}', usage=(900, 7, 0)):
        self.answer, self.usage, self.calls = answer, usage, []

    def __call__(self, messages, **_kwargs):
        self.calls.append(messages)
        return (self.answer, *self.usage)


class BrainFixture(unittest.TestCase):
    @contextmanager
    def db_conn(self):
        connection = sqlite3.connect(str(self.db))
        try:
            yield connection
        finally:
            connection.close()

    def create(self, body, title='', created='', embedder=None):
        connection = sqlite3.connect(str(self.db))
        try:
            brain_writer.ensure_schema(connection)
            provider = embedder or self.memory._embedder
            callable_embed = getattr(provider, 'embed', None) or provider
            result = brain_writer.create_memory(
                connection, {'body': body, 'title': title or body[:40],
                             'created': created or brain_writer.now()},
                embedder=callable_embed)
            return result['memory_id']
        finally:
            connection.close()

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = Store(Path(self.temp.name) / 'data')
        self.db = self.store.root / 'fake' / 'brain.db'
        self.db.parent.mkdir(parents=True)
        connection = sqlite3.connect(str(self.db))
        connection.executescript(veil_brain.SCHEMA)
        brain_writer.ensure_schema(connection)
        connection.close()
        provider = {'model': 'fake', 'max_input_tokens': 20000, 'input_cny_per_million': 1,
                    'output_cny_per_million': 2, 'cached_input_cny_per_million': 1}
        self.settings = {'model': 'fake-embedding', 'input_cny_per_million': '0', 'mode': 'native',
                         'base_url': 'https://example.invalid/v1',
                         'brain': {'write_authority': True, 'shadow': False},
                         'retrieval': {'backend': 'brain'}}
        self.cfg = Character('fake', 'Example', 'persona', 20, 1024, provider, 'fake', self.settings)
        self.memory = Memory(self.cfg, self.store, Budget(self.store), Mock())
        self.memory._embedder = CountingEmbedder([0.4] * 1024)
        self.addCleanup(self.memory.__exit__, None, None, None)


class CardAndMetadataTests(BrainFixture):
    def test_card_text_reads_a_crlf_card_completely(self):
        card = card_text(CRLF_RAW)
        self.assertIn('老记忆', card)
        self.assertIn('开头一句关于蓝色玩笑', card)
        self.assertIn('结尾是那只蓝色风筝', card)
        self.assertIn('蓝色玩笑', card)
        self.assertIn('那年', card)
        # The old hard-coded split found no body at all on CRLF files: that is the defect fixed here.
        self.assertEqual(CRLF_RAW.partition('\n---\n')[2], '')
        head, body = split_card(CRLF_RAW)
        self.assertIn('title: 老记忆', head)
        self.assertTrue(body.startswith('开头一句'))
        # LF, CRLF and a UTF-8 BOM describe the same memory and produce the same card.
        self.assertEqual(card, card_text(CRLF_RAW.replace('\r\n', '\n')))
        self.assertEqual(card, card_text('\ufeff' + CRLF_RAW))
        self.assertEqual(card, card_text(CRLF_RAW.encode('utf-8')))

    def test_card_text_still_reads_a_plain_body(self):
        self.assertEqual(card_text('just a plain body'), '…opening: just a plain body')
        self.assertEqual(card_text(None), '')

    def test_with_metadata_updates_crlf_front_matter_and_keeps_the_body(self):
        out = brain_writer._with_metadata(
            CRLF_RAW, {'title': '新标题', 'importance': 9, 'pinned': True})
        self.assertIn('title: 新标题\r\n', out)
        self.assertIn('importance: 9\r\n', out)
        self.assertIn('pinned: true\r\n', out)
        self.assertTrue(out.endswith('关键词：蓝色玩笑、风筝\r\n'))
        self.assertEqual(out.count('\r\n'), CRLF_RAW.count('\r\n') + 1)
        self.assertNotIn('\n', out.replace('\r\n', ''))

    def test_with_metadata_keeps_lf_files_lf_and_leaves_plain_text_alone(self):
        raw = '---\nid: x\ntitle: Old\nimportance: 5\n---\nBody\n'
        out = brain_writer._with_metadata(raw, {'title': 'New', 'importance': 9})
        self.assertIn('title: New\n', out)
        self.assertNotIn('\r', out)
        self.assertEqual(out, '---\nid: x\ntitle: New\nimportance: 9\n---\nBody\n')
        self.assertEqual(brain_writer._with_metadata('plain', {'title': 'x'}), 'plain')

    def test_apply_update_syncs_crlf_metadata_without_rewriting_the_body(self):
        with self.db_conn() as connection:
            created = brain_writer.create_memory(
                connection, {'body': 'body text', 'title': 'Old'}, embedder=None)
            memory_id = created['memory_id']
        with self.db_conn() as connection:
            connection.execute('UPDATE memories SET raw_markdown=? WHERE id=?',
                               (CRLF_RAW, memory_id))
            connection.commit()
        with self.db_conn() as connection:
            brain_writer.update_memory(connection, memory_id, {'title': 'New', 'importance': 9})
        with BrainRead(self.db) as reader:
            raw = reader.raw(memory_id)
        self.assertIn('title: New\r\n', raw)
        self.assertIn('importance: 9\r\n', raw)
        self.assertIn('结尾是那只蓝色风筝', raw)


class EmbeddingDimensionTests(BrainFixture):
    def test_retrieval_uses_whatever_dimension_the_model_produces(self):
        with self.db_conn() as connection:
            created = brain_writer.create_memory(
                connection, {'body': '蓝色风筝', 'title': '风筝'},
                embedder=CountingEmbedder([1.0, 0.0, 0.0], 'tiny-3d').embed)
        self.assertEqual(created['vector_state'], 'ready')
        text, meta = brain_retrieval.search_text(
            self.db, '风筝', embedder=CountingEmbedder([1.0, 0.0, 0.0], 'tiny-3d'))
        self.assertIn('风筝', text)
        self.assertFalse(meta['model_mismatch'])
        self.assertEqual(meta['skipped_dim'], 0)
        self.assertEqual(meta['embedding_model'], 'tiny-3d')

    def test_mismatched_dimensions_are_skipped_with_a_diagnostic(self):
        with self.db_conn() as connection:
            brain_writer.create_memory(
                connection, {'body': '蓝色风筝', 'title': '3D'},
                embedder=CountingEmbedder([1.0, 0.0, 0.0], 'tiny-3d').embed)
            brain_writer.create_memory(
                connection, {'body': '别的记忆', 'title': '1024'},
                embedder=CountingEmbedder([0.4] * 1024, 'tiny-3d').embed)
        text, meta = brain_retrieval.search_text(
            self.db, '别的东西', embedder=CountingEmbedder([0.4] * 1024, 'tiny-3d'))
        self.assertEqual(meta['skipped_dim'], 1)
        self.assertIn('[bucket_id:', text)

    def test_a_different_embedding_model_is_never_mixed(self):
        with self.db_conn() as connection:
            brain_writer.create_memory(
                connection, {'body': '蓝色风筝', 'title': '风筝'},
                embedder=CountingEmbedder([1.0, 0.0, 0.0], 'model-a').embed)
        text, meta = brain_retrieval.search_text(
            self.db, '风筝', embedder=CountingEmbedder([1.0, 0.0, 0.0], 'model-b'), model='model-b')
        self.assertTrue(meta['model_mismatch'])
        self.assertTrue(meta['keyword_only'])
        self.assertIn('[bucket_id:', text)   # the keyword channel still answers
        same, same_meta = brain_retrieval.search_text(
            self.db, '风筝', embedder=CountingEmbedder([1.0, 0.0, 0.0], 'model-a'), model='model-a')
        self.assertFalse(same_meta['model_mismatch'])
        self.assertIn('风筝', same)

    def test_writer_refuses_a_vector_from_another_embedding_model(self):
        with self.db_conn() as connection:
            brain_writer.create_memory(
                connection, {'body': '第一条', 'title': 'A'},
                embedder=CountingEmbedder([1.0, 0.0, 0.0], 'model-a').embed)
            refused = brain_writer.create_memory(
                connection, {'body': '第二条', 'title': 'B'},
                embedder=CountingEmbedder([1.0, 0.0, 0.0], 'model-b').embed)
            journal = connection.execute(
                "SELECT detail_json FROM write_journal WHERE memory_id=? ORDER BY seq DESC LIMIT 1",
                (refused['memory_id'],)).fetchone()[0]
        self.assertEqual(refused['vector_state'], 'pending')
        self.assertEqual(refused['vector_rejected'], 'embedding_model_mismatch')
        self.assertIn('embedding_model_mismatch', journal)


class ConfigAndDateTests(BrainFixture):
    def test_embedding_model_is_recorded_once_in_meta(self):
        with self.db_conn() as connection:
            brain_writer.create_memory(
                connection, {'body': '第一条'}, embedder=CountingEmbedder([1.0], 'model-a').embed)
        with BrainRead(self.db) as reader:
            self.assertEqual(reader.embedding_model(), 'model-a')

    def test_breath_search_uses_the_configured_weights_and_dates(self):
        authority = self.memory.brain_authority
        captured = {}

        def fake_search_text(db_path, query, limit=8, embedder=None, settings=None, **kwargs):
            captured.update({'query': query, 'limit': limit, 'settings': settings, **kwargs})
            return 'ok', {'ids': []}
        with patch('veil.brain.recall.brain_retrieval.search_text', fake_search_text):
            self.assertEqual(authority.breath_search(
                'q', 5, CountingEmbedder([1.0], 'model-a'), date_from='2026-11-01',
                date_to='2026-11-30'), 'ok')
        self.assertEqual(captured['date_from'], '2026-11-01')
        self.assertEqual(captured['date_to'], '2026-11-30')
        self.assertEqual(captured['model'], 'fake-embedding')
        self.assertEqual(authority.retrieval_settings()['weight_vector'], 0.65)
        self.cfg.memory['retrieval'] = {'backend': 'brain', 'weight_vector': 0.9, 'weight_keyword': 0.1}
        self.assertEqual(authority.retrieval_settings()['weight_vector'], 0.9)

    def test_dates_filter_created_day_inclusively(self):
        with self.db_conn() as connection:
            for body, created in (('十一月一号的事', '2026-11-01T10:00:00+08:00'),
                                  ('十一月二十号的事', '2026-11-20T10:00:00+08:00')):
                brain_writer.create_memory(
                    connection, {'body': body, 'title': body, 'created': created},
                    embedder=CountingEmbedder([0.4] * 1024, 'fake-embedding').embed)
        authority = self.memory.brain_authority
        text = authority.breath_search('十一月', 10, self.memory._embedder,
                                       date_from='2026-11-10', date_to='2026-11-30')
        self.assertIn('十一月二十号的事', text)
        self.assertNotIn('十一月一号的事', text)
        with self.assertRaises(ValueError):
            authority.breath_search('十一月', 10, self.memory._embedder, date_from='2026-13-40')


class SharedEmbeddingTests(BrainFixture):
    def test_one_turn_embeds_the_query_once_and_reuses_it(self):
        self.memory.call('hold', {'content': '蓝色风筝挂在墙上', 'title': '风筝'})
        self.memory._embedder.calls = 0
        judge_embedder = CountingEmbedder([0.4] * 1024, 'fake-embedding')
        judge = self.memory.attach_recall_judge(
            self.cfg, FakeJudgeModel(),
            {**JUDGE_DEFAULTS, 'timeout_seconds': 1.0, 'enabled': True})
        judge.make_embedder = lambda: judge_embedder
        self.memory.context('风筝')
        self.assertEqual(judge_embedder.calls, 1)          # exactly one embedding for the turn
        self.assertEqual(self.memory._embedder.calls, 0)   # the retrieval path reused it
        self.assertEqual(self.memory.last_recall_debug['retrieval'].get('embedding'), 'shared')
        self.assertIn(self.memory.last_recall_debug['judge']['status'], ('chosen', 'empty'))
        self.assertEqual(self.memory.last_recall_debug['judge']['embedding_seconds'] >= 0, True)

    def test_without_the_judge_the_retrieval_embeds_itself(self):
        self.memory.call('hold', {'content': '蓝色风筝挂在墙上', 'title': '风筝'})
        self.memory._embedder.calls = 0
        self.memory.context('风筝')
        self.assertEqual(self.memory._embedder.calls, 1)
        self.assertNotEqual(self.memory.last_recall_debug['retrieval'].get('embedding'), 'shared')


class JudgeTimerTests(unittest.TestCase):
    """The judge's embedding and model calls keep separate clocks (G-A12)."""

    def _judge(self, model, timeout):
        judge = RecallJudge.__new__(RecallJudge)
        judge.cfg = SimpleNamespace(provider={'model': 'fake', 'max_input_tokens': 2000,
                                              'input_cny_per_million': Decimal(0),
                                              'output_cny_per_million': Decimal(0),
                                              'cached_input_cny_per_million': Decimal(0)})
        judge.model = model
        judge.settings = {'timeout_seconds': timeout, 'ombre_candidates': 8,
                          'vector_candidates': 8, 'enabled': True,
                          'breaker_failures': 3, 'breaker_seconds': 60}
        judge.memory = SimpleNamespace(
            cfg=SimpleNamespace(id='fake', memory={'model': 'fake-embedding'}),
            budget=SimpleNamespace(clock=time.monotonic),
            store=SimpleNamespace(append=lambda *_a, **_k: None))
        judge.clock = time.monotonic
        judge.last = None
        judge.last_choice = {'status': 'skipped', 'candidates': 0, 'picked': [], 'vector': []}
        judge.failures, judge.open_until = 0, 0.0
        judge._files, judge._brain_rows = {}, None
        judge._lock = threading.Lock()
        judge._pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix='p6s1-test')
        self.addCleanup(judge._pool.shutdown, False)
        judge._scan = lambda: {}
        judge._vector_candidates = lambda *_a: []
        judge._raw = lambda key: '---\ntitle: %s\n---\nbody' % key

        def fake_ask(_messages):
            time.sleep(model.delay)
            return json.dumps({'ids': []})
        judge._ask = fake_ask
        return judge

    @staticmethod
    def _prefetch(delay):
        future = Future()
        timer = threading.Timer(delay, lambda: future.set_result([0.0]))
        timer.daemon = True
        timer.start()
        return future

    def test_a_slow_embedding_no_longer_eats_the_model_timeout(self):
        judge = self._judge(SimpleNamespace(delay=0.3), timeout=0.5)
        started = time.monotonic()
        self.assertEqual(judge.choose('q', [('a', 'body a')], self._prefetch(0.8)), [])
        elapsed = time.monotonic() - started
        self.assertLess(elapsed, 1.6)
        self.assertEqual(judge.last_choice['status'], 'empty')
        self.assertGreater(judge.last_choice['embedding_seconds'], 0.6)
        self.assertGreater(judge.last_choice['model_seconds'], 0.2)

    def test_a_slow_model_fails_as_a_model_timeout(self):
        judge = self._judge(SimpleNamespace(delay=0.9), timeout=0.2)
        with self.assertRaises(JudgeFailed) as caught:
            judge.choose('q', [('a', 'body a')], self._prefetch(0.0))
        self.assertEqual(caught.exception.reason, 'timeout')
        self.assertEqual(caught.exception.detail, 'model')
        self.assertEqual(judge.last_choice['status'], 'failed')
        self.assertEqual(judge.last_choice['reason'], 'timeout')


class RemovedDeadBranchTests(BrainFixture):
    def test_reuse_recall_and_random_drift_are_gone(self):
        self.create('一条用来让索引非空的记忆')
        self.assertFalse(hasattr(Memory, 'reuse_recall'))
        self.assertFalse(hasattr(brain_retrieval, 'DRIFT_MARK'))
        text, meta = brain_retrieval.search_text(
            self.db, 'nothing matches this', embedder=self.memory._embedder)
        self.assertNotIn('\u5ffd\u7136\u60f3\u8d77\u6765', text)   # the drift section marker
        self.assertNotIn('drift', meta)