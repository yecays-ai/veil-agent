"""P6-S1 post-merge hardening (after independent review of PR #452).

Covers the two blockers and the non-blocking diagnostics gaps found after #452 merged:
  1. an embedding-model switch must not re-embed (and pay for) incompatible pending vectors on
     every retry; the explicit reindex restores recovery;
  2. the new structured retrieval/degradation diagnostics reach the API layers P4/P5 read;
  3. the judge's failed request keeps its real candidate count and the shared embedding wait time;
  4. a keyword-only retrieval fallback keeps the vector failure reason in diagnostics;
  5. an active-search reversed date range is rejected.
"""
import json
import sqlite3
import tempfile
import time
import unittest
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from veil.brain.recall import brain_retrieval
from veil.brain.storage import brain_writer
import diagnostics
from veil.brain.storage import veil_brain
from veil.brain.storage.brain_read import BrainRead
from veil.brain.memory_agent import Memory
from veil.brain.recall.recall_judge import DEFAULTS as JUDGE_DEFAULTS
from veil_agent import Budget, Character, Store


class CountingEmbedder:
    def __init__(self, vector, model='model-a'):
        self.vector, self.model, self.calls = list(vector), model, 0

    def embed(self, _text):
        self.calls += 1
        return list(self.vector)


class FakeJudgeModel:
    def __init__(self, delay=0.0, answer='{"ids": []}'):
        self.delay, self.answer = delay, answer

    def __call__(self, _messages, **_kwargs):
        if self.delay:
            time.sleep(self.delay)
        return (self.answer, 900, 7, 0)


class MemoryFixture(unittest.TestCase):
    @contextmanager
    def db_conn(self):
        connection = sqlite3.connect(str(self.db))
        try:
            yield connection
        finally:
            connection.close()

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
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
        self.memory = Memory(self.cfg, self.store, Budget(self.store), SimpleNamespace(call=None))
        self.memory._embedder = CountingEmbedder([0.4] * 1024, 'fake-embedding')
        self.addCleanup(self.memory.__exit__, None, None, None)


class ModelSwitchRetryTests(MemoryFixture):
    def test_a_model_switch_blocks_paid_retry_until_the_explicit_reindex(self):
        with self.db_conn() as connection:
            created = brain_writer.create_memory(
                connection, {'body': '第一条记忆'}, embedder=CountingEmbedder([1.0], 'model-a').embed)
            connection.execute("UPDATE memories SET vector_state='pending' WHERE id=?",
                               (created['memory_id'],))
            connection.execute('DELETE FROM vectors')
            connection.commit()
        new_model = CountingEmbedder([1.0], 'model-b')
        with self.db_conn() as connection:
            blocked = brain_writer.retry_pending_vectors(connection, new_model.embed)
        self.assertEqual(new_model.calls, 0, 'a model switch must not pay for a rejected embedding')
        self.assertEqual(blocked['blocked'], 'embedding_model_mismatch')
        self.assertEqual(blocked['saved'], 0)
        with self.db_conn() as connection:
            self.assertEqual(connection.execute('SELECT COUNT(*) FROM vectors').fetchone()[0], 0)
            self.assertEqual(connection.execute(
                "SELECT COUNT(*) FROM memories WHERE vector_state='pending'").fetchone()[0], 1)
        # The explicit operator reindex restores the normal pending-vector recovery path.
        with self.db_conn() as connection:
            result = brain_writer.prepare_embedding_reindex(connection, 'model-b')
        self.assertEqual(result['memories_marked_pending'], 1)
        with self.db_conn() as connection:
            self.assertEqual(connection.execute('SELECT COUNT(*) FROM vectors').fetchone()[0], 0)
            self.assertEqual(brain_writer.retry_pending_vectors(connection, new_model.embed)['saved'], 1)
        self.assertEqual(new_model.calls, 1)
        with BrainRead(self.db) as reader:
            self.assertEqual(reader.embedding_model(), 'model-b')

    def test_authority_skips_the_retry_before_any_write_after_a_model_switch(self):
        self.memory.call('hold', {'content': '第一段', 'title': '第一段'})
        authority = self.memory.brain_authority
        with self.db_conn() as connection:
            connection.execute("UPDATE memories SET vector_state='pending'")
            connection.commit()
            stored = connection.execute(
                "SELECT value FROM meta WHERE key='embedding_model'").fetchone()
        self.assertIsNotNone(stored)
        authority._last_pending_retry = 0.0
        with patch('veil.brain.storage.brain_writer.retry_pending_vectors',
                   side_effect=AssertionError('must not retry after a model switch')):
            authority._retry_pending(CountingEmbedder([1.0] * 1024, 'another-model').embed)


class DiagnosticsVisibilityTests(MemoryFixture):
    def _judge(self, model, timeout=0.2):
        judge = self.memory.attach_recall_judge(
            self.cfg, model, {**JUDGE_DEFAULTS, 'timeout_seconds': timeout, 'enabled': True})
        judge.make_embedder = lambda: CountingEmbedder([0.4] * 1024, 'fake-embedding')
        return judge

    def test_a_timed_out_judge_keeps_candidates_and_records_the_shared_embedding_wait(self):
        self.memory.call('hold', {'content': '蓝色风筝挂在墙上', 'title': '风筝'})
        self.memory._embedder.calls = 0
        self._judge(FakeJudgeModel(delay=0.9), timeout=0.2)
        self.memory.context('风筝')
        judge = self.memory.last_recall_debug['judge']
        self.assertEqual(judge['status'], 'failed')
        self.assertEqual(judge['reason'], 'timeout')
        self.assertEqual(judge['detail'], 'model')
        self.assertGreater(judge.get('candidates', 0), 0,
                           'a failed judge request must keep the real candidate count')
        self.assertGreater(judge.get('embedding_wait_ms', -1), -1)
        self.assertEqual(self.memory.last_recall_debug['degraded']['stage'], 'judge')

    def test_a_keyword_only_fallback_keeps_the_vector_failure_reason(self):
        self.memory.call('hold', {'content': '蓝色风筝挂在墙上', 'title': '风筝'})

        class BrokenEmbedder:
            model = 'fake-embedding'

            def embed(self, _text):
                raise RuntimeError('embedding down')
        fake = SimpleNamespace(
            retrieval_settings={'backend': 'brain', 'weight_vector': 0.65, 'weight_keyword': 0.35},
            store=SimpleNamespace(root=self.store.root),
            cfg=SimpleNamespace(id='fake', memory={'model': 'fake-embedding',
                                                   'input_cny_per_million': '0'}),
            last_recall_debug={}, embedder=lambda: BrokenEmbedder(), budget=None,
            brain_authority=SimpleNamespace(db_path=self.db),
            call=lambda name, args: '')
        text = Memory.search_candidates(fake, '风筝', 5)
        self.assertIn('[bucket_id:', text)
        retrieval = fake.last_recall_debug['retrieval']
        self.assertEqual(retrieval.get('degraded'), 'keyword_only')
        self.assertEqual(retrieval.get('fallback'), 'RuntimeError')

    def test_active_search_rejects_a_reversed_date_range(self):
        authority = self.memory.brain_authority
        with self.assertRaises(ValueError):
            authority.breath_search('x', 5, self.memory._embedder,
                                    date_from='2026-11-30', date_to='2026-11-01')


class ApiPassThroughTests(MemoryFixture):
    def test_diagnostics_trace_carries_retrieval_and_degraded(self):
        role = self.store.root / 'peter'
        role.mkdir(parents=True, exist_ok=True)
        row = {'time': '2026-11-26T10:00:00+08:00', 'request_id': 'req-456',
               'retrieval': {'backend': 'brain', 'embedding': 'shared', 'embedding_wait_ms': 12.5},
               'degraded': {'stage': 'judge', 'reason': 'timeout', 'detail': 'model'},
               'judge': {'status': 'failed', 'candidates': 8}}
        (role / 'recall-diagnostics.jsonl').write_text(
            json.dumps(row, ensure_ascii=False) + '\n', encoding='utf-8')
        chain = diagnostics.trace(self.store.root, 'peter', request_id='req-456')
        self.assertEqual(chain['recall']['retrieval']['embedding'], 'shared')
        self.assertEqual(chain['recall']['degraded']['stage'], 'judge')
        self.assertEqual(chain['recall']['judge']['candidates'], 8)

    def test_live_context_passes_retrieval_and_degraded_to_the_api(self):
        from web_agent import Hub
        role = self.store.root / 'peter'
        role.mkdir(parents=True, exist_ok=True)
        context_row = {'time': '2026-11-26T10:00:00+08:00', 'kind': 'chat',
                       'request_id': 'req-456', 'memory_ids': []}
        recall_row = {'time': '2026-11-26T10:00:00+08:00', 'request_id': 'req-456',
                      'retrieval': {'backend': 'brain', 'embedding': 'shared'},
                      'degraded': {'stage': 'judge', 'reason': 'timeout', 'detail': 'model'}}
        (role / 'context-log.jsonl').write_text(
            json.dumps(context_row, ensure_ascii=False) + '\n', encoding='utf-8')
        (role / 'recall-diagnostics.jsonl').write_text(
            json.dumps(recall_row, ensure_ascii=False) + '\n', encoding='utf-8')

        def read_tail(name, limit):
            file = self.store.root / name
            lines = [json.loads(line) for line in file.read_text(encoding='utf-8').split('\n')
                     if line.strip()]
            return lines[-limit:]
        chat = SimpleNamespace(cfg=SimpleNamespace(id='peter'),
                               store=SimpleNamespace(root=self.store.root, read_tail=read_tail),
                               short_term_notes=None, voice_style=None, work_checkpoint=None)
        hub = Hub.__new__(Hub)
        hub.chat = chat
        hub._context_log_cache = None
        hub.tool_inventory = lambda _key: []
        view = hub.live_context([], limit=5)
        self.assertEqual(view['turns'][0]['recall']['retrieval']['embedding'], 'shared')
        self.assertEqual(view['turns'][0]['recall']['degraded']['stage'], 'judge')


if __name__ == '__main__':
    unittest.main()
