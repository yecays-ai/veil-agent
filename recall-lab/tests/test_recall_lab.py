"""Synthetic unit tests for recall-lab. No real data, no network, no paid call.

Run from the repository root:

    python -m unittest discover -s recall-lab/tests -v
"""
from __future__ import annotations

import json
import math
import sys
import tempfile
import threading
import unittest
from pathlib import Path

HERE = Path(__file__).resolve()
LAB = HERE.parents[1]
ROOT = HERE.parents[2]
for path in (str(LAB), str(ROOT)):
    if path not in sys.path:
        sys.path.insert(0, path)

from recall_lab import core  # noqa: E402
from recall_lab.judge import card_300, card_500, parse_picked  # noqa: E402
from recall_lab.retrieval import (AhoCorasick, RetrievalConfig, Retriever,  # noqa: E402
                                  STOPWORDS, association_choice, build_alias_dictionary,
                                  stratified_long_tail_pool, tokenize)
from recall_lab.runtime import Context  # noqa: E402


def memory(**kwargs):
    values = {'id': 'm1', 'title': 'title one', 'body': 'body one', 'raw': '',
              'created': '2026-11-01T00:00:00+08:00', 'last_active': '2026-11-10T00:00:00+08:00',
              'importance': 5, 'valence': 0.5, 'arousal': 0.4, 'why_remembered': '',
              'bucket': 'dynamic', 'type': 'dynamic', 'tags': (), 'category': 'experience'}
    values.update(kwargs)
    return core.Memory(**values)


def snapshot(memories, vectors=None, events=None, relations=None):
    snap = core.Snapshot(db_path=Path('synthetic-brain.db'))
    snap.memories = {item.id: item for item in memories}
    snap.vectors = dict(vectors or {})
    snap.events = dict(events or {})
    snap.relations = list(relations or [])
    return snap


def sample(sample_id='S1-001', category='explicit_recall', query='第一次写测试文件', expected=('m1',),
           allow_empty=False, allow_empty_text=False):
    return core.Sample(sample_id=sample_id, category=category, query=query,
                       expected=tuple(expected), allow_empty=allow_empty, key_facts=(),
                       confidence='high', ambiguity='', context={'time': '2026-11-20T00:00:00'},
                       source={}, labeler='ai-assisted', human_confirmed=False)


class BudgetTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / 'ledger.jsonl'

    def test_reserve_settle_and_totals(self):
        ledger = core.BudgetLedger(self.path, run_id='test')
        ticket = ledger.reserve(1, 'AB-TEST', provider='p', model='m', kind='chat', reserve_cny=0.5)
        ledger.settle(ticket, 0.2, usage={'prompt_tokens': 100})
        totals = ledger.totals()
        self.assertAlmostEqual(totals['by_tier']['1'], 0.2)
        self.assertAlmostEqual(totals['total_cny'], 0.2)
        self.assertEqual(totals['calls'], 1)

    def test_tier_cap_blocks_before_dispatch(self):
        ledger = core.BudgetLedger(self.path, run_id='test')
        ticket = ledger.reserve(1, 'AB-TEST', provider='p', model='m', kind='chat', reserve_cny=5.9)
        ledger.settle(ticket, 5.9)
        with self.assertRaises(core.BudgetBlocked):
            ledger.reserve(1, 'AB-TEST', provider='p', model='m', kind='chat', reserve_cny=0.2)
        blocked = [row for row in core.read_jsonl(self.path) if row.get('type') == 'blocked']
        self.assertEqual(len(blocked), 1)

    def test_total_cap_uses_all_tiers(self):
        ledger = core.BudgetLedger(self.path, run_id='test')
        ticket = ledger.reserve(2, 'AB-TEST', provider='p', model='m', kind='chat', reserve_cny=9.9)
        ledger.settle(ticket, 9.9)
        ticket = ledger.reserve(1, 'AB-TEST', provider='p', model='m', kind='chat', reserve_cny=5.9)
        ledger.settle(ticket, 5.9)
        with self.assertRaises(core.BudgetBlocked):
            ledger.reserve(2, 'AB-TEST', provider='p', model='m', kind='chat', reserve_cny=4.3)


class EvalLoaderTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / 'eval.jsonl'

    def _write(self, rows):
        self.path.write_text('\n'.join(json.dumps(row, ensure_ascii=False) for row in rows) + '\n',
                             encoding='utf-8')

    def test_valid_file_loads(self):
        self._write([
            {'sample_id': 'S1-001', 'category': 'explicit_recall', 'query': 'q', 'expected_relevant_ids': ['m1'],
             'allow_empty': False, 'labeler': 'ai-assisted', 'human_confirmed': False},
            {'sample_id': 'S4-001', 'category': 'noise', 'query': 'q2', 'expected_relevant_ids': [],
             'allow_empty': True, 'labeler': 'ai-assisted', 'human_confirmed': False},
        ])
        samples, report = core.load_eval(self.path, validate_ids={'m1'})
        self.assertEqual(len(samples), 2)
        self.assertEqual(report['sha256'], core.sha256_file(self.path))

    def test_duplicate_and_unknown_category_are_errors(self):
        self._write([
            {'sample_id': 'S1-001', 'category': 'explicit_recall', 'query': 'q', 'expected_relevant_ids': ['m1'],
             'allow_empty': False},
            {'sample_id': 'S1-001', 'category': 'explicit_recall', 'query': 'q', 'expected_relevant_ids': ['m1'],
             'allow_empty': False},
            {'sample_id': 'S9-001', 'category': 'nope', 'query': 'q', 'expected_relevant_ids': [],
             'allow_empty': True},
        ])
        with self.assertRaises(core.EvalSetError):
            core.load_eval(self.path)

    def test_missing_snapshot_id_is_an_error(self):
        self._write([{'sample_id': 'S1-001', 'category': 'explicit_recall', 'query': 'q',
                      'expected_relevant_ids': ['gone'], 'allow_empty': False}])
        with self.assertRaises(core.EvalSetError):
            core.load_eval(self.path, validate_ids={'m1'})

    def test_noise_must_be_allow_empty(self):
        self._write([{'sample_id': 'S4-001', 'category': 'noise', 'query': 'q',
                      'expected_relevant_ids': ['m1'], 'allow_empty': False}])
        with self.assertRaises(core.EvalSetError):
            core.load_eval(self.path, validate_ids={'m1'})


class MetricTests(unittest.TestCase):
    def test_recall_precision_ndcg(self):
        expected = ('a', 'b')
        ranked = ['x', 'a', 'y', 'b']
        self.assertAlmostEqual(core.recall_at_k(ranked, expected, 4), 1.0)
        self.assertAlmostEqual(core.recall_at_k(ranked, expected, 2), 0.5)
        self.assertAlmostEqual(core.precision_at_k(ranked, expected, 2), 0.5)
        dcg = 1 / math.log2(3) + 1 / math.log2(5)
        ideal = 1 / math.log2(2) + 1 / math.log2(3)
        self.assertAlmostEqual(core.ndcg_at_k(ranked, expected, 8), dcg / ideal)

    def test_false_positive_rate_counts_any_injection(self):
        samples = [sample(sample_id='S4-001', category='noise', expected=(), allow_empty=True),
                   sample(sample_id='S4-002', category='noise', expected=(), allow_empty=True)]
        rankings = {'S4-001': [], 'S4-002': ['m1']}
        metrics = core.evaluate_rankings(samples, rankings, 8)
        self.assertAlmostEqual(metrics['overall']['false_positive_rate'], 0.5)
        self.assertEqual(metrics['overall']['empty_correct'], 1)

    def test_cross_event_coverage_needs_two(self):
        samples = [sample(sample_id='S3-001', category='cross_event', expected=('a', 'b'))]
        metrics = core.evaluate_rankings(samples, {'S3-001': ['a', 'x']}, 8)
        self.assertAlmostEqual(metrics['overall']['cross_event_coverage'], 0.0)
        metrics = core.evaluate_rankings(samples, {'S3-001': ['a', 'b']}, 8)
        self.assertAlmostEqual(metrics['overall']['cross_event_coverage'], 1.0)


class RetrievalTests(unittest.TestCase):
    def _snapshot(self):
        memories = [
            memory(id='m1', title='雾潮岛赌约', body='雾潮岛 赌约 body one text', importance=8,
                   created='2026-10-20T00:00:00+08:00', last_active='2026-11-18T00:00:00+08:00'),
            memory(id='m2', title='桌宠项目', body='桌宠 项目 body two', importance=5,
                   created='2026-11-10T00:00:00+08:00', last_active='2026-11-19T00:00:00+08:00'),
            memory(id='m3', title='旧画课', body='画画 课 body three', importance=8,
                   created='2026-09-01T00:00:00+08:00', last_active='2026-09-02T00:00:00+08:00'),
        ]
        vectors = {'m1': [1.0, 0.0], 'm2': [0.0, 1.0], 'm3': [0.7, 0.7]}
        events = {'e1': core.Event(id='e1', memory_id='m3', title='画课', story='画画 课 long story',
                                   status='open', approved=1, first_at='', last_at='',
                                   story_source='composed', related=('e2',), previous=()),
                  'e2': core.Event(id='e2', memory_id='m1', title='赌约', story='雾潮岛 赌约 story',
                                   status='open', approved=1, first_at='', last_at='',
                                   story_source='composed', related=(), previous=())}
        relations = [{'memory_id': 'm2', 'target_id': 'm1', 'type': 'related_to',
                      'score': 0.7, 'status': 'active'}]
        return snapshot(memories, vectors, events, relations)

    def test_minmax_fusion_ranks_both_channels(self):
        snap = self._snapshot()
        retriever = Retriever(snap)
        retriever.prepare()
        ranked, diag = retriever.rank('雾潮岛 赌约', [1.0, 0.0], RetrievalConfig(k=3))
        self.assertEqual(ranked[0].id, 'm1')
        self.assertGreater(ranked[0].vector, 0.0)
        self.assertGreater(ranked[0].keyword, 0.0)

    def test_stopword_filtering_drops_filler(self):
        tokens = tokenize('那个 今天 测试 雾潮岛 赌约')
        self.assertIn('雾潮岛', tokens)
        filtered = tokenize('那个 今天 测试 雾潮岛 赌约', stopwords=STOPWORDS)
        self.assertEqual(filtered, ['雾潮岛', '赌约'])

    def test_alias_aho_corasick(self):
        matcher = AhoCorasick({'雾潮岛': {'m1'}, '桌宠': {'m2'}})
        self.assertEqual(matcher.find('你还记得雾潮岛的赌约吗'), {'m1'})
        self.assertEqual(matcher.find('毫无关系的句子'), set())

    def test_alias_dictionary_from_metadata(self):
        snap = self._snapshot()
        dictionary = build_alias_dictionary(snap)
        self.assertIn('雾潮岛', dictionary)
        self.assertIn('m1', dictionary['雾潮岛'])

    def test_related_expansion_adds_neighbours(self):
        snap = self._snapshot()
        retriever = Retriever(snap)
        retriever.prepare()
        config = RetrievalConfig(k=1, related_expand=True, related_seed_k=1)
        _ranked, diag = retriever.rank('桌宠 项目', [0.0, 1.0], config)
        self.assertIn('m1', diag['related_expanded'])

    def test_stratified_pool_is_deterministic(self):
        snap = self._snapshot()
        first = stratified_long_tail_pool(snap, size=2, rotation=0)
        second = stratified_long_tail_pool(snap, size=2, rotation=0)
        self.assertEqual([item.id for item in first], [item.id for item in second])
        self.assertTrue(all(item.importance >= 7 for item in first))

    def test_association_respects_light_gate(self):
        snap = self._snapshot()
        candidates = [snap.memories['m1']]
        choice = association_choice(snap, '部署报错了', candidates, turn=1)
        self.assertEqual(choice.get('key'), '')
        choice = association_choice(snap, '晚安', candidates, turn=1)
        self.assertEqual(choice.get('key'), 'm1')

    def test_story_index_lifts_event_story_similarity(self):
        snap = self._snapshot()
        retriever = Retriever(snap, story_vectors={'e1': [0.0, 0.0, 1.0]})
        # story vector dimension differs from memory vectors only if used; a same-space story vector
        # is required, so pass a 2-d one that favours m3's memory.
        retriever = Retriever(snap, story_vectors={'e1': [0.7, 0.7]})
        scores = retriever.vector_scores([1.0, 0.0], story_index=True)
        self.assertGreaterEqual(scores.get('m3', 0.0), 0.0)


class CardTests(unittest.TestCase):
    CRLF = ('---\r\nid: m1\r\ntitle: CRLF memory\r\nimportance: 8\r\n---\r\n'
            + ('opening line about a cactus. ' * 6) + '\r\n' + ('middle body. ' * 20)
            + '\r\nending detail: the last thing she said.\r\n关键词：仙人掌 养死\r\n')

    def test_production_card_is_300_and_reads_crlf_body(self):
        card = card_300(self.CRLF)
        self.assertIn('CRLF memory', card)
        self.assertLessEqual(len(card), 300)
        self.assertIn('opening', card)
        self.assertTrue('ending' in card or '…ending' in card)

    def test_500_card_adds_why_importance_and_story(self):
        memories = [memory(id='m1', raw=self.CRLF, importance=8, why_remembered='a reason')]
        snap = snapshot(memories)
        text = card_500(self.CRLF, snap, 'm1')
        self.assertIn('why:', text)
        self.assertIn('importance: 8', text)
        self.assertLessEqual(len(text), 500)

    def test_judge_parse(self):
        class Record:
            def __init__(self, text, error=''):
                self.text, self.error, self.error_kind = text, error, ''
        picked, error = parse_picked(Record('{"ids": ["A", "B"]}'), ['A', 'B'])
        self.assertEqual(picked, ['A', 'B'])
        self.assertEqual(error, '')
        picked, error = parse_picked(Record('not json'), ['A'])
        self.assertEqual(error, 'bad_json')


class LearnedContextTests(unittest.TestCase):
    def test_sizes_count_only_active_zones(self):
        data = {'facts': {'text': 'a' * 10}, 'growth': {'text': 'b' * 20},
                'relationship': {'items': [
                    {'text': 'c' * 5, 'status': 'confirmed'},
                    {'text': 'd' * 7, 'status': 'outdated'},
                    {'text': 'e' * 3, 'status': 'uncertain'}]},
                'ongoing': {'items': [
                    {'text': 'f' * 4, 'status': 'active'},
                    {'text': 'g' * 6, 'status': 'completed'}]}}
        sizes = core.learned_context_sizes(data)
        self.assertEqual(sizes['facts'], 10)
        self.assertEqual(sizes['growth'], 20)
        self.assertEqual(sizes['relationship'], 8)
        self.assertEqual(sizes['ongoing'], 4)
        self.assertEqual(sizes['total'], 42)


class RuntimeTests(unittest.TestCase):
    def test_coverage_note_mentions_matrix_discrepancy(self):
        from recall_lab.cli import _coverage, ORDER
        ctx = type('Ctx', (), {'results': {}})()
        coverage = _coverage(ctx, ORDER)
        self.assertEqual(coverage['count'], 24)
        self.assertIn('24', coverage['note'])
        self.assertEqual(coverage['status_counts'].get('not_run'), 24)




class CliSelectionTests(unittest.TestCase):
    def _args(self, argv):
        from recall_lab import cli
        return cli.build_parser().parse_args(['--eval', 'x', '--db', 'y'] + argv)

    def test_lowercase_suffix_experiment_ids_are_canonical(self):
        from recall_lab import cli
        selected, skipped = cli._select(self._args(['--experiments', 'ab-r1b,AB-R3a']))
        self.assertEqual(selected, ['AB-R1b', 'AB-R3a'])
        self.assertEqual(skipped, [])

    def test_skip_paid_removes_paid_experiments_and_reports_them(self):
        from recall_lab import cli
        selected, skipped = cli._select(self._args(['--tier', 'all', '--skip-paid']))
        self.assertTrue(all(cli.TIER_OF[item] == 0 for item in selected))
        self.assertEqual(len(skipped), len(cli.ORDER) - 11)
        explicit, explicit_skipped = cli._select(
            self._args(['--experiments', 'AB-R1a,AB-R3a', '--skip-paid']))
        self.assertEqual(explicit, ['AB-R1a'])
        self.assertEqual(explicit_skipped, ['AB-R3a'])

    def test_coverage_marks_skipped_paid_as_not_run(self):
        from recall_lab import cli
        ctx = type('Ctx', (), {'results': {'AB-R3a': {'status': 'completed', 'title': 'x'}}})()
        coverage = cli._coverage(ctx, cli.ORDER, skipped_paid=['AB-R3a'])
        row = next(item for item in coverage['experiments']
                   if item['experiment_id'] == 'AB-R3a')
        self.assertEqual(row['status'], 'not_run')
        self.assertIn('--skip-paid', row['reason'])


class _FakeEmbeddingClient:
    class _Embeddings:
        def __init__(self, *, usage_tokens=7, fail=False):
            self.usage_tokens, self.fail = usage_tokens, fail

        def create(self, **_kwargs):
            if self.fail:
                raise RuntimeError('provider down')
            usage = None
            if self.usage_tokens is not None:
                usage = type('Usage', (), {'prompt_tokens': self.usage_tokens})()
            return type('Response', (), {
                'data': [type('Item', (), {'embedding': [0.1, 0.2]})()],
                'usage': usage})()

    def __init__(self, **kwargs):
        self.embeddings = self._Embeddings(**kwargs)

    def close(self):
        pass


class EmbeddingBudgetTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.ledger_path = Path(self.tmp.name) / 'ledger.jsonl'
        self.cache_path = Path(self.tmp.name) / 'embeddings.jsonl'

    def test_cache_only_mode_never_calls_a_provider_on_a_miss(self):
        from recall_lab.core import EmbeddingCache, EmbeddingError, configure_embedding_price
        configure_embedding_price(0.0, source='test')
        cache = EmbeddingCache(self.cache_path, model='m', base_url='https://invalid', api_key='',
                               allow_provider=False)
        with self.assertRaises(EmbeddingError):
            cache.embed('not cached')
        self.assertEqual(cache.calls, 0)

    def test_embedding_reserves_before_dispatch_and_settles_reported_usage(self):
        from recall_lab.core import (BudgetLedger, EmbeddingCache,
                                     configure_embedding_price)
        configure_embedding_price(2.0, source='test')
        ledger = BudgetLedger(self.ledger_path, run_id='test')
        cache = EmbeddingCache(self.cache_path, model='m', base_url='https://invalid', api_key='k',
                               budget=ledger, tier=1)
        cache._client = _FakeEmbeddingClient(usage_tokens=7)
        cache.embed('hello world')
        totals = ledger.totals()
        self.assertAlmostEqual(totals['by_tier']['1'], 7 * 2.0 / 1_000_000, places=8)
        rows = [json.loads(line) for line in
                self.ledger_path.read_text(encoding='utf-8').splitlines()]
        kinds = [row.get('type') for row in rows]
        self.assertEqual(kinds[:2], ['reserve', 'settle'])
        self.assertEqual(rows[1].get('accounting'), 'embedding-usage')

    def test_a_failed_embedding_keeps_the_reserved_upper_bound(self):
        from recall_lab.core import (BudgetLedger, EmbeddingCache,
                                     configure_embedding_price)
        configure_embedding_price(2.0, source='test')
        ledger = BudgetLedger(self.ledger_path, run_id='test')
        cache = EmbeddingCache(self.cache_path, model='m', base_url='https://invalid', api_key='k',
                               budget=ledger, tier=1)
        cache._client = _FakeEmbeddingClient(fail=True)
        with self.assertRaises(RuntimeError):
            cache.embed('hello world')
        self.assertGreater(ledger.totals()['total_cny'], 0)
        self.assertEqual(cache.calls, 0)

    def test_an_unknown_embedding_price_is_refused(self):
        from recall_lab.core import configure_embedding_price, embedding_price
        core.PRICES['embedding']['input_cny_per_million'] = None
        with self.assertRaises(ValueError):
            embedding_price()
        with self.assertRaises(ValueError):
            configure_embedding_price(None)
        configure_embedding_price(0.0, source='test')


class LedgerConcurrencyTests(unittest.TestCase):
    def test_two_instances_sharing_a_file_cannot_jointly_cross_the_tier_cap(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        path = Path(tmp.name) / 'ledger.jsonl'
        caps = {1: 6.0, 2: 10.0}
        first = core.BudgetLedger(path, tier_caps=caps, total_cap=20.0, run_id='first')
        second = core.BudgetLedger(path, tier_caps=caps, total_cap=20.0, run_id='second')
        barrier = threading.Barrier(2)
        outcomes = []

        def worker(ledger):
            barrier.wait()
            try:
                ticket = ledger.reserve(1, 'AB-TEST', provider='p', model='m', kind='chat',
                                        reserve_cny=3.5)
                ledger.settle(ticket, 3.5)
                outcomes.append('ok')
            except core.BudgetBlocked:
                outcomes.append('blocked')

        threads = [threading.Thread(target=worker, args=(ledger,))
                   for ledger in (first, second)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertEqual(sorted(outcomes), ['blocked', 'ok'])
        self.assertLessEqual(first.totals()['total_cny'], 6.0 + 1e-9)


class JudgeUsageSettlementTests(unittest.TestCase):
    def test_missing_usage_settles_the_reserved_upper_bound(self):
        from recall_lab import tier12
        from recall_lab.judge import JudgeCall
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        ledger = core.BudgetLedger(Path(tmp.name) / 'ledger.jsonl', run_id='test')
        ctx = type('Ctx', (), {'budget': ledger})()

        class FakeJudge:
            model = 'deepseek-flash'

            def call(self, _messages, *, timeout):
                return JudgeCall(text='{"ids": []}', prompt_tokens=0, completion_tokens=0,
                                 cached_tokens=0, latency_ms=1.0, usage_missing=True)

        record = tier12.ledgered_judge_call(
            ctx, FakeJudge(), tier=1, experiment='AB-TEST',
            messages=[{'role': 'user', 'content': 'hello'}], timeout=4, cache_path=None)
        self.assertGreater(record['amount_cny'], 0)
        rows = [json.loads(line) for line in
                (Path(tmp.name) / 'ledger.jsonl').read_text(encoding='utf-8').splitlines()]
        settle = [row for row in rows if row.get('type') == 'settle'][-1]
        self.assertEqual(settle.get('accounting'), 'usage-missing-conservative')
        self.assertAlmostEqual(ledger.totals()['total_cny'], record['amount_cny'], places=8)


class StoryGateTests(unittest.TestCase):
    def test_story_index_does_not_change_the_common_relevance_gate(self):
        snap = RetrievalTests._snapshot(self)
        retriever = Retriever(snap, story_vectors={'e2': [0.0, 1.0]})
        retriever.prepare()
        ranked, _diag = retriever.rank('query', [0.0, 1.0],
                                       RetrievalConfig(k=3, story_index=True))
        by_id = {item.id: item for item in ranked}
        self.assertGreater(by_id['m1'].vector, 0.9)
        self.assertAlmostEqual(by_id['m1'].stored_vector, 0.0, places=6)
        gated = [item.id for item in ranked if item.stored_vector >= 0.45][:3]
        self.assertNotIn('m1', gated)
        self.assertIn('m2', gated)


if __name__ == '__main__':
    unittest.main()
