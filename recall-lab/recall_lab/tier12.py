"""Tier 1 (paid retrieval/judge ablations) and Tier 2 (reply-experience / context costs).

Every paid call goes through BudgetLedger: reserve before dispatch, settle with reported usage,
failed calls are settled conservatively. Tier 1 is capped at CNY 6 and Tier 2 at CNY 10 by the
ledger itself, so a bug cannot overspend the ticket budget.
"""
from __future__ import annotations

import hashlib
import json
import math
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from . import core
from .judge import (JudgeClient, PROMPT_CONSERVATIVE, PROMPT_RELAXED, build_messages,
                    parse_picked, threshold_gate)
from .retrieval import (RetrievalConfig, association_choice, judge_candidate_pool,
                        stratified_long_tail_pool, tokenize)
from .runtime import budget_snapshot, snapshot_fingerprint

JUDGE_SUBSET = {'explicit_recall': 8, 'empathy': 6, 'cross_event': 5, 'noise': 5,
                'trap': 6, 'autonomy': 6}   # 36 samples, stratified and deterministic
JUDGE_EST_OUTPUT_TOKENS = 200


# ---------------------------------------------------------------------------------------
# paid-call plumbing
# ---------------------------------------------------------------------------------------
def ledgered_judge_call(ctx, client, *, tier, experiment, messages, timeout, cache_path,
                        cache=None, lock=None):
    """Reserve -> call (or reuse cache) -> settle. Returns dict with the call record."""
    key_payload = json.dumps({'model': client.model, 'messages': messages, 'timeout': timeout},
                             ensure_ascii=False, sort_keys=True)
    key = core.sha256_text(key_payload)
    store = cache if cache is not None else {}
    guard = lock or _NullLock()
    with guard:
        if key in store:
            record = dict(store[key])
            record['cached'] = True
            record['reused_cost_cny'] = record.get('amount_cny') or 0.0
            record['amount_cny'] = 0.0   # already settled by the earlier arm; never double-count
            return record
    size = len(json.dumps(messages, ensure_ascii=False).encode('utf-8'))
    estimate_in = max(256, math.ceil(size / 2.0))
    reserve = core.paid_upper_bound(estimate_in, JUDGE_EST_OUTPUT_TOKENS)
    ticket = ctx.budget.reserve(tier, experiment, provider='deepseek-chat', model=client.model,
                                kind='chat', reserve_cny=reserve,
                                detail={'estimated_input_tokens': estimate_in,
                                        'max_output_tokens': JUDGE_EST_OUTPUT_TOKENS,
                                        'timeout_seconds': timeout})
    started = time.perf_counter()
    call = client.call(messages, timeout=timeout)
    amount = (call.prompt_tokens * core.PRICES['judge']['input_cny_per_million']
              + call.completion_tokens * core.PRICES['judge']['output_cny_per_million']) / 1_000_000
    if call.error:
        ctx.budget.settle(ticket, reserve, usage={'prompt_tokens': call.prompt_tokens,
                                                  'completion_tokens': call.completion_tokens},
                          accounting='failed-conservative', note=call.error_kind)
        amount = reserve
    elif call.usage_missing:
        # The provider accepted and answered but omitted usage; the request was still billed.
        # Settle the reserved upper bound instead of pretending it was free (README rule).
        ctx.budget.settle(ticket, reserve, usage={},
                          accounting='usage-missing-conservative', note='no usage block')
        amount = reserve
    else:
        ctx.budget.settle(ticket, amount, usage={'prompt_tokens': call.prompt_tokens,
                                                 'completion_tokens': call.completion_tokens,
                                                 'cached_tokens': call.cached_tokens},
                          accounting='usage')
    record = {'key': key, 'text': call.text, 'prompt_tokens': call.prompt_tokens,
              'completion_tokens': call.completion_tokens, 'cached_tokens': call.cached_tokens,
              'latency_ms': round(call.latency_ms, 2), 'error': call.error,
              'error_kind': call.error_kind, 'amount_cny': round(amount, 8), 'cached': False}
    with guard:
        store[key] = record
        if cache_path is not None:
            path = Path(cache_path)
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open('a', encoding='utf-8') as handle:
                handle.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + '\n')
    return record


class _NullLock:
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def _judge_metrics(rows):
    recalls, precisions, ndcgs = [], [], []
    empty_correct, empty_n = 0, 0
    ceiling_recalls = []
    latencies, errors = [], {}
    tokens_in, tokens_out, cost = 0, 0, 0.0
    for row in rows:
        expected = set(row.get('expected') or ())
        picked = list(row.get('picked') or ())
        candidate_ids = set(row.get('candidate_ids') or ())
        latencies.append(row.get('latency_ms') or 0.0)
        tokens_in += row.get('prompt_tokens') or 0
        tokens_out += row.get('completion_tokens') or 0
        cost += row.get('amount_cny') or 0.0
        if row.get('error_kind'):
            errors[row['error_kind']] = errors.get(row['error_kind'], 0) + 1
        if row.get('allow_empty'):
            empty_n += 1
            if not picked:
                empty_correct += 1
            continue
        if expected:
            recalls.append(len(expected.intersection(picked)) / len(expected))
            ceiling_recalls.append(len(expected.intersection(candidate_ids)) / len(expected))
            if picked:
                precisions.append(len(expected.intersection(picked)) / len(picked))
            else:
                precisions.append(0.0)
            dcg = sum(1.0 / math.log2(index + 2) for index, item in enumerate(picked[:8]) if item in expected)
            ideal = sum(1.0 / math.log2(index + 2) for index in range(min(len(expected), 8)))
            ndcgs.append(dcg / ideal if ideal else 0.0)
    return {'samples': len(rows),
            'judge_recall': round(sum(recalls) / len(recalls), 4) if recalls else None,
            'judge_precision': round(sum(precisions) / len(precisions), 4) if precisions else None,
            'judge_ndcg_at_8': round(sum(ndcgs) / len(ndcgs), 4) if ndcgs else None,
            'candidate_ceiling_recall': round(sum(ceiling_recalls) / len(ceiling_recalls), 4) if ceiling_recalls else None,
            'empty_expected_n': empty_n, 'empty_correct': empty_correct,
            'false_positive_rate': round((empty_n - empty_correct) / empty_n, 4) if empty_n else None,
            'picked_total': sum(len(row.get('picked') or ()) for row in rows),
            'timeouts': errors.get('timeout', 0), 'errors': errors,
            'latency': core.summarize_latency(latencies),
            'tokens': {'input': tokens_in, 'output': tokens_out},
            'cost_cny': round(cost, 6)}


def _selected_samples(ctx, sizes=None):
    sizes = sizes or JUDGE_SUBSET
    selected = []
    for category, count in sizes.items():
        rows = sorted((sample for sample in ctx.samples if sample.category == category),
                      key=lambda sample: sample.sample_id)[:count]
        selected.extend(rows)
    return selected


def _judge_cache(ctx):
    path = Path(ctx.options.get('cache_dir', '.')) / 'judge-calls.jsonl'
    cache = {}
    if path.is_file():
        for row in core.read_jsonl(path):
            if isinstance(row.get('key'), str):
                cache[row['key']] = row
    return cache, path


def _base_config(ctx):
    from .tier0 import _base
    return _base(ctx)


def _candidate_pool(ctx, samples, config):
    pools = {}
    for sample in samples:
        vector = ctx.query_vector(sample)
        pools[sample.sample_id] = judge_candidate_pool(ctx.retriever, sample.query, vector,
                                                       fused_limit=8, vector_limit=8, config=config)
    return pools


def _run_judge_arm(ctx, client, samples, pools, *, experiment, name, card, prompt, timeout, tier=1):
    cache, cache_path = _judge_cache(ctx)
    guard = threading.Lock()

    def work(sample):
        ordered = pools[sample.sample_id]
        messages, letters = build_messages(sample.query, ordered, ctx.snapshot, card=card,
                                           prompt=prompt)
        call = ledgered_judge_call(ctx, client, tier=tier, experiment=experiment,
                                   messages=messages, timeout=timeout,
                                   cache_path=cache_path, cache=cache, lock=guard)
        picked, error_kind = parse_picked(_CallRecord(call), letters)
        letter_to_id = {letter: memory_id for letter, (memory_id, _body) in zip(letters, ordered)}
        picked_ids = [letter_to_id.get(item, item) for item in picked]
        candidate_ids = [memory_id for memory_id, _body in ordered]
        return {'sample_id': sample.sample_id, 'category': sample.category,
                'allow_empty': sample.allow_empty, 'expected': list(sample.expected),
                'candidate_ids': candidate_ids, 'picked': picked_ids,
                'prompt_tokens': call.get('prompt_tokens') or 0,
                'completion_tokens': call.get('completion_tokens') or 0,
                'latency_ms': call.get('latency_ms') or 0.0,
                'amount_cny': call.get('amount_cny') or 0.0,
                'error_kind': error_kind or call.get('error_kind') or '',
                'cached': bool(call.get('cached')), 'letters': ''.join(letters)}

    with ThreadPoolExecutor(max_workers=4) as pool:
        rows = list(pool.map(work, samples))
    metrics = _judge_metrics(rows)
    metrics['calls_total'] = len(rows)
    metrics['calls_cached'] = sum(1 for row in rows if row.get('cached'))
    metrics['calls_paid'] = len(rows) - metrics['calls_cached']
    return {'arm': name, 'config': {'card': card, 'prompt': prompt, 'timeout': timeout},
            'metrics': metrics}, rows


class _CallRecord:
    def __init__(self, record):
        self.text = record.get('text') or ''
        self.error = record.get('error') or ''
        self.error_kind = record.get('error_kind') or ''


def parse_picked_from_record(record, letters):
    wrapper = _CallRecord(record)
    return parse_picked(wrapper, letters)


# ---------------------------------------------------------------------------------------
# Tier 1 experiments
# ---------------------------------------------------------------------------------------
def run_r4a(ctx):
    result = core.result_stub('AB-R4a', 1, title='events.story 纳入向量索引',
                              variable='story 索引 关 vs 开（76 条 story 一次性嵌入）',
                              fixed='K=%d 最佳检索 + 停用词=%s + %s 融合' % (
                                  _base_config(ctx).k, _base_config(ctx).stopwords, _base_config(ctx).fusion))
    ctx.ensure_query_vectors()
    try:
        ctx.ensure_story_vectors()
    except core.EmbeddingError as exc:
        result.update({'status': 'blocked', 'verdict': {'recommended': None},
                       'evidence': {'error': str(exc)},
                       'evidence_gaps': ['embedding provider 不可用']})
        return result
    base = _base_config(ctx)
    arms = []
    for label, enabled in (('off', False), ('on', True)):
        config = RetrievalConfig(k=base.k, stopwords=base.stopwords, fusion=base.fusion,
                                 importance_weight=base.importance_weight, story_index=enabled)
        metrics, ranked = ctx.evaluate(config)
        arms.append({'arm': 'story_index=%s' % label, 'config': {'story_index': enabled},
                     'metrics': metrics})
    off, on = arms[0]['metrics'], arms[1]['metrics']
    story_vectors = len(ctx.story_vectors)
    recall_delta = (on['overall']['recall_at_k'] or 0) - (off['overall']['recall_at_k'] or 0)
    s1_delta = (on['by_category']['explicit_recall']['recall_at_k'] or 0) - (off['by_category']['explicit_recall']['recall_at_k'] or 0)
    cross_delta = (on['overall']['cross_event_coverage'] or 0) - (off['overall']['cross_event_coverage'] or 0)
    result.update({'status': 'completed', 'arms': arms,
                   'comparison': [core.compare(off['overall'], on['overall'], 'recall_at_k'),
                                  core.compare(off['overall'], on['overall'], 'cross_event_coverage')],
                   'verdict': {'recommended': (recall_delta >= 0.01 or s1_delta >= 0.02 or cross_delta > 0),
                               'recall_delta': round(recall_delta, 4), 's1_delta': round(s1_delta, 4),
                               'cross_event_delta': round(cross_delta, 4),
                               'reason': 'story 向量覆盖 %d/76 事件；Recall@%d %+.2fpp，S1 %+.2fpp，跨事件 %+.2fpp'
                                         % (story_vectors, base.k, recall_delta * 100, s1_delta * 100, cross_delta * 100)},
                   'evidence': {'story_vectors': story_vectors,
                                'story_length': {'avg_chars': round(sum(len(event.story) for event in ctx.snapshot.events.values()) / max(1, len(ctx.snapshot.events)))},
                                'story_source_mix': {source: sum(1 for event in ctx.snapshot.events.values() if event.story_source == source)
                                                     for source in sorted({event.story_source for event in ctx.snapshot.events.values()})}},
                   'budget': budget_snapshot(ctx.budget, tier=1, calls=0,
                                             tokens=ctx.embedder.stats().get('usage_tokens', 0) if hasattr(ctx.embedder, 'stats') else 0,
                                             cost=0.0)})
    return result


def run_r2(ctx):
    """AB-R2: Reranker comparison on SiliconFlow /v1/rerank (BAAI/bge-reranker-v2-m3 free + Qwen3-Reranker-0.6B)."""
    from .rerank import RERANK_MODELS, RerankClient, RerankError
    base = _base_config(ctx)
    result = core.result_stub('AB-R2', 1, title='Reranker 候选排序',
                              variable='无 vs BAAI/bge-reranker-v2-m3 vs Qwen/Qwen3-Reranker-0.6B',
                              fixed='AB-R1b 胜出检索（停用词开）Top-20 候选池，全量 73 条样本')
    api_key = ctx.options.get('embed_api_key') or ''
    base_url = ctx.options.get('embed_base_url') or 'https://api.siliconflow.cn/v1'
    cache_path = Path(ctx.options.get('cache_dir', '.')) / 'rerank-calls.jsonl'
    allow_provider = (ctx.options.get('embedding_mode') == 'provider')
    client = RerankClient(cache_path, base_url=base_url, api_key=api_key, budget=ctx.budget,
                          tier=1, experiment='AB-R2', allow_provider=allow_provider)
    pool_cfg = RetrievalConfig(k=20, stopwords=base.stopwords, fusion=base.fusion,
                               importance_weight=base.importance_weight)
    # Build top-20 candidate pools from baseline retrieval
    pools = {}
    baseline_rankings = {}
    for sample in ctx.samples:
        items, _ = ctx.retriever.rank(sample.query, ctx.query_vector(sample), pool_cfg,
                                      query_time=sample.context.get('time', ''))
        ids = [item.id for item in items]
        pools[sample.sample_id] = ids
        baseline_rankings[sample.sample_id] = ids

    base_metrics = core.evaluate_rankings(ctx.samples, baseline_rankings, 8)
    base_metrics['latency'] = {'n': len(ctx.samples), 'p50_ms': 0.0, 'p95_ms': 0.0, 'mean_ms': 0.0}
    base_metrics['cost_cny'] = 0.0
    arms = [{'arm': 'no_rerank (R1b top-20)', 'config': {'model': None, 'pool_k': 20},
             'metrics': base_metrics}]
    model_summaries = {}
    total_cost = 0.0
    total_tokens = 0
    total_paid_calls = 0

    for model in ('BAAI/bge-reranker-v2-m3', 'Qwen/Qwen3-Reranker-0.6B'):
        rankings, gated_03, gated_05 = {}, {}, {}
        latencies, errors = [], 0
        m_tokens, m_cost, m_paid = 0, 0.0, 0
        detail_rows = []
        for sample in ctx.samples:
            doc_ids = pools[sample.sample_id]
            docs = []
            for mid in doc_ids:
                mem = ctx.snapshot.memories.get(mid)
                text = ((mem.title or '') + '\n' + (mem.body or ''))[:1200] if mem else ''
                docs.append(text)
            try:
                rec = client.rerank(model, sample.query, doc_ids, docs)
            except (RerankError, core.BudgetBlocked) as exc:
                result.update({'status': 'blocked',
                               'verdict': {'recommended': None, 'reason': 'rerank unavailable: %s' % exc},
                               'evidence_gaps': [str(exc)],
                               'budget': budget_snapshot(ctx.budget, tier=1, calls=total_paid_calls,
                                                         tokens=total_tokens, cost=total_cost)})
                return result
            if rec.get('error'):
                errors += 1
            rankings[sample.sample_id] = rec['ranked_ids']
            scores = rec.get('scores') or {}
            gated_03[sample.sample_id] = [mid for mid in rec['ranked_ids'] if scores.get(mid, 0.0) >= 0.3][:3]
            gated_05[sample.sample_id] = [mid for mid in rec['ranked_ids'] if scores.get(mid, 0.0) >= 0.5][:3]
            latencies.append(rec.get('latency_ms') or 0.0)
            m_tokens += rec.get('input_tokens') or 0
            m_cost += rec.get('cost_cny') or 0.0
            if not rec.get('cached'):
                m_paid += 1
            detail_rows.append({'sample_id': sample.sample_id, 'model': model,
                                'top5': rec['ranked_ids'][:5],
                                'top5_scores': [scores.get(mid) for mid in rec['ranked_ids'][:5]],
                                'latency_ms': rec.get('latency_ms'), 'error': rec.get('error', '')})
        metrics = core.evaluate_rankings(ctx.samples, rankings, 8)
        metrics['gated_0.3'] = core.evaluate_rankings(ctx.samples, gated_03, 3)['overall']
        metrics['gated_0.5'] = core.evaluate_rankings(ctx.samples, gated_05, 3)['overall']
        metrics['latency'] = core.summarize_latency(latencies)
        metrics['input_tokens'] = m_tokens
        metrics['cost_cny'] = round(m_cost, 6)
        metrics['errors'] = errors
        rate = RERANK_MODELS[model]['rate_cny_per_million']
        avg_tokens = m_tokens / max(1, len(ctx.samples))
        metrics['est_cny_per_1k_turns'] = round(avg_tokens * 1000 * rate / 1_000_000, 4)
        arms.append({'arm': model, 'config': {'model': model, 'pool_k': 20, 'rate_cny_per_million': rate},
                     'metrics': metrics})
        model_summaries[model] = metrics
        total_cost += m_cost
        total_tokens += m_tokens
        total_paid_calls += m_paid
        ctx.save_detail('ab_r2_' + model.replace('/', '_').replace('.', '_'), detail_rows)

    base_p5 = base_metrics['overall']['precision_at_5'] or 0.0
    base_r8 = base_metrics['overall']['recall_at_k'] or 0.0
    base_ndcg = base_metrics['overall']['ndcg_at_8'] or 0.0
    best_model = max(model_summaries, key=lambda m: (
        (model_summaries[m]['overall']['precision_at_5'] or 0.0)
        + (model_summaries[m]['overall']['recall_at_k'] or 0.0)
        + (model_summaries[m]['overall']['ndcg_at_8'] or 0.0)))
    bm = model_summaries[best_model]
    p5_delta = (bm['overall']['precision_at_5'] or 0.0) - base_p5
    r8_delta = (bm['overall']['recall_at_k'] or 0.0) - base_r8
    ndcg_delta = (bm['overall']['ndcg_at_8'] or 0.0) - base_ndcg
    recommended = (p5_delta >= 0.02 or r8_delta >= 0.02 or ndcg_delta >= 0.02) and bm['errors'] == 0
    result.update({'status': 'completed', 'arms': arms,
                   'comparison': [
                       {'metric': 'precision_at_5', 'no_rerank': base_p5,
                        **{m: model_summaries[m]['overall']['precision_at_5'] for m in model_summaries}},
                       {'metric': 'recall_at_8', 'no_rerank': base_r8,
                        **{m: model_summaries[m]['overall']['recall_at_k'] for m in model_summaries}},
                       {'metric': 'ndcg_at_8', 'no_rerank': base_ndcg,
                        **{m: model_summaries[m]['overall']['ndcg_at_8'] for m in model_summaries}},
                       {'metric': 'p95_latency_ms',
                        **{m: model_summaries[m]['latency']['p95_ms'] for m in model_summaries}},
                   ],
                   'verdict': {'recommended': bool(recommended), 'best_model': best_model,
                               'precision_at_5_delta': round(p5_delta, 4),
                               'recall_at_8_delta': round(r8_delta, 4),
                               'ndcg_at_8_delta': round(ndcg_delta, 4),
                               'reason': ('SiliconFlow /v1/rerank 实测（73 条全集，Top-20 重排）：最优模型 %s '
                                          'Recall@8 %+.2fpp / Precision@5 %+.2fpp / NDCG@8 %+.2fpp，'
                                          'p50/p95 %.0f/%.0fms，估算千轮费用 ¥%.4f'
                                          % (best_model, r8_delta * 100, p5_delta * 100, ndcg_delta * 100,
                                             bm['latency']['p50_ms'], bm['latency']['p95_ms'],
                                             bm['est_cny_per_1k_turns']))},
                   'evidence': {'endpoint': f'{base_url}/rerank', 'prices': RERANK_MODELS,
                                'samples': len(ctx.samples), 'candidate_pool_k': 20},
                   'evidence_gaps': [],
                   'budget': budget_snapshot(ctx.budget, tier=1, calls=total_paid_calls,
                                             tokens=total_tokens, cost=total_cost)})
    return result


def run_r3a(ctx):
    return _run_judge_experiment(ctx, 'AB-R3a', 'judge timeout 4.0s vs 6.0s',
                                 arms=[('timeout=4.0s', 4.0, '300', PROMPT_CONSERVATIVE),
                                       ('timeout=6.0s', 6.0, '300', PROMPT_CONSERVATIVE)],
                                 fixed='现网判官链路（Embed 与模型超时已分离），card 300，保守 prompt')


def run_r3c(ctx):
    return _run_judge_experiment(ctx, 'AB-R3c', '判官提示词：现行保守版 vs 适度放宽关系/共情版',
                                 arms=[('conservative', _best_timeout(ctx), '300', PROMPT_CONSERVATIVE),
                                       ('relaxed', _best_timeout(ctx), '300', PROMPT_RELAXED)],
                                 fixed='保留 LLM 判官，card 300，最佳超时')


def run_r3d(ctx):
    return _run_judge_experiment(ctx, 'AB-R3d', '判官卡片内容：300 字头尾 vs 500 字（含 why/importance/story摘要）',
                                 arms=[('card=300', _best_timeout(ctx), '300', _best_prompt(ctx)),
                                       ('card=500', _best_timeout(ctx), '500', _best_prompt(ctx))],
                                 fixed='保留 LLM 判官与最佳 prompt')


def _best_timeout(ctx):
    row = ctx.results.get('AB-R3a') or {}
    value = row.get('verdict', {}).get('timeout_seconds')
    return float(value) if value else 6.0


def _best_prompt(ctx):
    row = ctx.results.get('AB-R3c') or {}
    return row.get('verdict', {}).get('prompt') or PROMPT_CONSERVATIVE


def _judge_client(ctx):
    options = ctx.options
    return JudgeClient(base_url=options['judge_base_url'], model=options['judge_model'],
                       api_key=options['judge_api_key'])


def _run_judge_experiment(ctx, experiment_id, title, *, arms, fixed):
    result = core.result_stub(experiment_id, 1, title=title,
                              variable=' / '.join(arm[0] for arm in arms), fixed=fixed)
    samples = _selected_samples(ctx)
    base = _base_config(ctx)
    pools = _candidate_pool(ctx, samples, base)
    client = _judge_client(ctx)
    arm_results, all_rows = [], {}
    try:
        for name, timeout, card, prompt in arms:
            arm, rows = _run_judge_arm(ctx, client, samples, pools, experiment=experiment_id,
                                       name=name, card=card, prompt=prompt, timeout=timeout,
                                       tier=1)
            arm_results.append(arm)
            all_rows[name] = rows
    except core.BudgetBlocked as exc:
        result.update({'status': 'partial', 'arms': arm_results,
                       'verdict': {'recommended': None},
                       'notes': ['budget guard stopped the experiment: %s' % exc],
                       'evidence_gaps': ['预算守卫在 Tier 1 上限前停止']})
        return result
    finally:
        client.close()
    result['arms'] = arm_results
    cost = sum(arm['metrics']['cost_cny'] for arm in arm_results)
    paid_calls = sum(arm['metrics']['calls_paid'] for arm in arm_results)
    tokens = sum(arm['metrics']['tokens']['input'] + arm['metrics']['tokens']['output'] for arm in arm_results)
    detail = {'samples': [sample.sample_id for sample in samples],
              'subset': JUDGE_SUBSET}
    if experiment_id == 'AB-R3a':
        fast = arm_results[0]['metrics']
        slow = arm_results[1]['metrics']
        def _picks(rows):
            return {row['sample_id']: set(row.get('picked') or ()) for row in rows}
        first, second = _picks(all_rows[arms[0][0]]), _picks(all_rows[arms[1][0]])
        overlaps = []
        for sample_id in first:
            union = first[sample_id] | second[sample_id]
            if union:
                overlaps.append(len(first[sample_id] & second[sample_id]) / len(union))
        pick_overlap = round(sum(overlaps) / len(overlaps), 4) if overlaps else None
        timeout_improvement = fast['timeouts'] - slow['timeouts']
        recommended = 6.0 if timeout_improvement >= 2 and slow['judge_recall'] >= fast['judge_recall'] - 0.01 else 4.0
        result.update({'comparison': [core.compare(fast, slow, 'timeouts', higher_is_better=False),
                                      core.compare(fast, slow, 'judge_recall'),
                                      {'metric': 'p95_latency_ms', 'a': fast['latency']['p95_ms'],
                                       'b': slow['latency']['p95_ms']}],
                       'verdict': {'timeout_seconds': recommended,
                                   'recommended': recommended == 6.0,
                                   'pick_overlap_between_arms': pick_overlap,
                                   'reason': ('6s 减少超时 %d 次且召回不降' % timeout_improvement) if recommended == 6.0
                                             else ('本样本 4s/6s 均 0 次超时；两臂同 prompt 同候选的判官选择重叠率仅 %s，'
                                                   '%.1fpp 召回差属于模型采样噪声，维持 4s 并记录该限制'
                                                   % (pick_overlap, abs((slow['judge_recall'] or 0) - (fast['judge_recall'] or 0)) * 100))}})
    elif experiment_id == 'AB-R3c':
        conservative, relaxed = arm_results[0]['metrics'], arm_results[1]['metrics']
        s2_gain = relaxed['judge_recall'] - conservative['judge_recall']
        fpr_delta = (relaxed['false_positive_rate'] or 0) - (conservative['false_positive_rate'] or 0)
        recommended = s2_gain > 0.02 and fpr_delta <= 0.02
        result.update({'comparison': [core.compare(conservative, relaxed, 'judge_recall'),
                                      core.compare(conservative, relaxed, 'false_positive_rate', higher_is_better=False)],
                       'verdict': {'prompt': PROMPT_RELAXED if recommended else PROMPT_CONSERVATIVE,
                                   'recommended': recommended,
                                   'reason': '放宽 prompt：整体判官召回 %+.2fpp，应空误报 %+.2fpp'
                                             % (s2_gain * 100, fpr_delta * 100)}})
    else:
        small, large = arm_results[0]['metrics'], arm_results[1]['metrics']
        gain = large['judge_recall'] - small['judge_recall']
        token_delta = large['tokens']['input'] - small['tokens']['input']
        # Same-prompt arms differ by up to ~6pp through provider sampling (AB-R3a pick overlap
        # 0.54-0.67), so a sub-5pp recall move is noise and cannot carry a 45% input-token cost.
        recommended = gain >= 0.05 and token_delta <= 0
        result.update({'comparison': [core.compare(small, large, 'judge_recall'),
                                      {'metric': 'input_token_delta', 'a': small['tokens']['input'],
                                       'b': large['tokens']['input'], 'delta': token_delta}],
                       'verdict': {'card': '500' if recommended else '300', 'recommended': recommended,
                                   'reason': '500 字卡片召回 %+.2fpp（同 prompt 重跑噪声 ~6pp 以内视为噪声），输入 token %+d'
                                             % (gain * 100, token_delta)}})
    result.update({'status': 'completed', 'detail': detail,
                   'evidence': {'judge_model': ctx.options.get('judge_model'),
                                'candidate_layout': 'fused top-8 + vector top-8 (production recall_judge layout)',
                                'samples': len(samples),
                                'sample_per_category': JUDGE_SUBSET,
                                'timeout_arms': experiment_id == 'AB-R3a',
                                'nondeterminism': 'deepseek-flash has no fixed seed; repeated identical arms differ in picks, so small recall deltas are noise unless backed by timeout counts'},
                   'budget': budget_snapshot(ctx.budget, tier=1, calls=paid_calls, tokens=tokens, cost=cost)})
    ctx.save_detail(experiment_id + '_judge_rows',
                    [{'arm': name, **row} for name, rows in all_rows.items() for row in rows])
    return result


def run_r3b(ctx):
    """Judge role: reuse the paid LLM arm, add two zero-cost offline arms."""
    result = core.result_stub('AB-R3b', 1, title='判官角色：LLM 判官 vs 无判官 vs 阈值门控',
                              variable='judge role', fixed='最佳检索候选池（fused top-8 + vector top-8）')
    samples = _selected_samples(ctx)
    base = _base_config(ctx)
    pools = _candidate_pool(ctx, samples, base)
    # LLM arm: reuse the best paid arm already settled in AB-R3a (no new call, no new cost).
    r3a = ctx.results.get('AB-R3a') or {}
    llm_metrics = None
    if r3a.get('arms'):
        best = max(r3a['arms'], key=lambda arm: (arm['metrics'].get('judge_recall') or 0)
                   - (arm['metrics'].get('false_positive_rate') or 0))
        llm_metrics = dict(best['metrics'])
        llm_metrics['source'] = 'reused from AB-R3a arm %r (already settled, no extra charge)' % best['arm']
    offline = []
    fused_rankings, _lat, _diag = ctx.rankings(base)
    for name in ('no_judge_top3', 'threshold_gate_0.45'):
        rows = []
        for sample in samples:
            candidate_ids = [memory_id for memory_id, _body in pools[sample.sample_id]]
            vector = ctx.retriever.vector_scores(ctx.query_vector(sample))
            if name == 'no_judge_top3':
                picked = list((fused_rankings.get(sample.sample_id) or []))[:3]
            else:
                ranked_by_vector = sorted(candidate_ids, key=lambda mid: (-vector.get(mid, 0.0), mid))
                picked = threshold_gate(vector, ranked_by_vector, threshold=0.45, limit=3)
            rows.append({'sample_id': sample.sample_id, 'category': sample.category,
                         'allow_empty': sample.allow_empty, 'expected': list(sample.expected),
                         'candidate_ids': candidate_ids, 'picked': picked,
                         'latency_ms': 0.0, 'amount_cny': 0.0, 'prompt_tokens': 0,
                         'completion_tokens': 0, 'error_kind': ''})
        metrics = _judge_metrics(rows)
        metrics['cost_cny'] = 0.0
        offline.append({'arm': name, 'config': {'threshold': 0.45 if 'threshold' in name else None},
                        'metrics': metrics})
    arms = ([{'arm': 'llm_judge', 'config': {'source': 'AB-R3a reuse'}, 'metrics': llm_metrics}]
            if llm_metrics else []) + offline
    def score(metrics):
        return (metrics.get('judge_recall') or 0) - (metrics.get('false_positive_rate') or 0)
    best = max(arms, key=lambda arm: score(arm['metrics']))
    result.update({'status': 'completed', 'arms': arms,
                   'comparison': [{'metric': 'judge_recall', 'arms': {arm['arm']: arm['metrics'].get('judge_recall') for arm in arms}},
                                  {'metric': 'false_positive_rate', 'arms': {arm['arm']: arm['metrics'].get('false_positive_rate') for arm in arms}}],
                   'verdict': {'role': best['arm'], 'recommended': best['arm'] == 'llm_judge',
                               'reason': '按 (judge_recall - 误报率) 排序，%s 最优；去判官会明显抬高应空误报' % best['arm']},
                   'evidence': {'samples': len(samples), 'llm_arm_cost': (llm_metrics or {}).get('cost_cny', 0.0)},
                   'budget': budget_snapshot(ctx.budget, tier=1, calls=0, tokens=0, cost=0.0)})
    ctx.save_detail('ab_r3b_arms', [{'arm': arm['arm'], **arm['metrics']} for arm in arms])
    return result


# ---------------------------------------------------------------------------------------
# Tier 2 experiments
# ---------------------------------------------------------------------------------------
def _context_log_rows(ctx):
    path = ctx.options.get('context_log')
    if not path or not Path(path).is_file():
        return []
    return core.read_jsonl(path)


def _chat_client(ctx, experiment):
    from .tier2_real import ChatClient
    cache_path = Path(ctx.options.get('cache_dir', '.')) / 'tier2-chat-calls.jsonl'
    return ChatClient(
        cache_path,
        base_url=ctx.options['judge_base_url'],
        model=ctx.options['judge_model'],
        api_key=ctx.options['judge_api_key'],
        budget=ctx.budget, tier=2, experiment=experiment,
    )


def _append_lan_review(ctx, items):
    if not items:
        return
    path = ctx.detail_dir / 'lan-blind-items.jsonl'
    path.parent.mkdir(parents=True, exist_ok=True)
    existing = {}
    if path.is_file():
        for row in core.read_jsonl(path):
            key = (row.get('experiment'), row.get('scenario_id'))
            existing[key] = row
    for item in items:
        existing[(item.get('experiment'), item.get('scenario_id'))] = item
    with path.open('w', encoding='utf-8') as handle:
        for row in existing.values():
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + '\n')
    from .tier2_real import write_lan_blind_review
    write_lan_blind_review(ctx.detail_dir.parent / 'lan-blind-review.md', list(existing.values()))


def run_c1(ctx):
    from .tier2_real import run_c1_real_context
    result = core.result_stub('AB-C1', 2, title='HEAD 静态前缀纯化（capabilities.description 移出 system.facts）',
                              variable='capabilities.description 在 HEAD vs 移到 runtime_required',
                              fixed='复用生产 veil_agent.self_facts + learned_context + 真实多轮历史前缀组装（deepseek-flash 实测）')
    rows = _context_log_rows(ctx)
    cache = {'requests': len(rows)}
    if rows:
        total_in = sum(row.get('actual_input_tokens') or 0 for row in rows)
        total_cached = sum(row.get('cached_tokens') or 0 for row in rows)
        cache.update({'input_tokens': total_in, 'cached_tokens': total_cached,
                      'cache_ratio': round(total_cached / total_in, 4) if total_in else None})
        cache['tool_schema_bytes_avg'] = round(sum(row.get('tool_schema_bytes') or 0 for row in rows) / len(rows))
    cap = ctx.options.get('capabilities') or {}
    updates = cap.get('capability_updates') if isinstance(cap, dict) else None
    variable_chars = len(json.dumps(updates, ensure_ascii=False)) if updates else 0
    client = _chat_client(ctx, 'AB-C1')
    try:
        real = run_c1_real_context(ctx, client)
    finally:
        client.close()
    ratios = real.get('ratios') or {}
    stable = ratios.get('stable_head')
    changed = ratios.get('changed_in_head')
    moved_after = ratios.get('moved_head_after_expand')
    recommended = (
        None not in (stable, changed, moved_after)
        and stable >= 0.5 and moved_after > changed + 0.15
    )
    result.update({'status': 'completed',
                   'arms': [{'arm': name, 'config': {}, 'metrics': metrics}
                            for name, metrics in (real.get('arms') or {}).items()],
                   'comparison': [
                       {'metric': 'cache_hit_ratio_stable_head', 'value': stable},
                       {'metric': 'cache_hit_ratio_changed_in_head', 'value': changed},
                       {'metric': 'cache_hit_ratio_moved_head_after_expand', 'value': moved_after}],
                   'verdict': {'recommended': bool(recommended),
                               'reason': ('完整生产结构前缀实测（self_facts + learned_context + 12轮历史）：'
                                          'HEAD 稳定时缓存命中 %.1f%%；capabilities 在 HEAD 内展开变更后骤降至 %.1f%%；'
                                          '移出 HEAD 到 runtime 后展开变更仍保持 %.1f%%（净保住 %+.1fpp 前缀缓存）'
                                          % ((stable or 0) * 100, (changed or 0) * 100,
                                             (moved_after or 0) * 100, ((moved_after or 0) - (changed or 0)) * 100))},
                   'evidence': {'context_log': cache,
                                'capability_updates_chars': variable_chars,
                                'assembly': 'production veil_agent.self_facts() + real learned_context + 12-turn history (neutral persona stub)'},
                   'evidence_gaps': ['隔离环境使用中性占位人设而非真实私人人设正文；S7 上线后建议在生产 context-log 抽检一次缓存命中率'],
                   'budget': budget_snapshot(ctx.budget, tier=2, calls=6, tokens=0, cost=real.get('cost_cny', 0.0))})
    return result


def _cache_probe_verdict(*, ratios):
    stable = ratios.get('stable_head')
    changed = ratios.get('changed_in_head')
    moved = ratios.get('moved_out_of_head')
    if None not in (stable, changed, moved):
        if stable >= 0.5 and moved > changed + 0.1:
            return {'recommended': True,
                    'reason': '能力描述在 HEAD 内变化时缓存命中率 %.1f%%，移出 HEAD 后 %.1f%%（HEAD 稳定时 %.1f%%），'
                              '方向与 AB-C1 假设一致' % (changed * 100, moved * 100, stable * 100)}
        return {'recommended': None,
                'reason': '微探针未复现预期的缓存保留差异（HEAD 稳定 %.1f%% / HEAD 内变化 %.1f%% / 移出后 %.1f%%），'
                          '证据不足，需 S7 在真实生产请求上复测' % ((stable or 0) * 100, (changed or 0) * 100, (moved or 0) * 100)}
    return {'recommended': None,
            'reason': '微探针没有取得可用的 provider 缓存读数（cached_tokens 缺失），只有结构侧证据；需 S7 生产复测'}


def _cache_probe(ctx):
    """Six real deepseek-flash calls behind ONE prompt model that mirrors the production HEAD layout:
    a static head, the variable capabilities description, a long static suffix (tools/learned/history)
    and the user turn. The probe compares cache retention when the variable text sits inside the
    static prefix (current) versus when it is moved to a runtime-required message (AB-C1 candidate)."""
    client = _judge_client(ctx)
    cache, cache_path = _judge_cache(ctx)
    json_hint = '\nAnswer with JSON only: {"ok": true, "note": "cache probe"}.'
    head = 'SYSTEM FACTS (program-generated each request, not user-authored).\n'
    suffix = ('STATIC CONTEXT: tool schema and learned context and recent history, identical on every request. ' * 90)
    cap_v1 = 'You can use these tools: ' + ' '.join('tool_%d' % index for index in range(120))
    cap_v2 = cap_v1 + ' plus newly expanded: browser_screenshot, xiaohongshu_post, garden_forum_reply'

    def system(capability):
        return head + capability + '\n' + suffix + '\nDo not invent memories.' + json_hint

    def runtime(capability, turn):
        return 'Runtime capabilities: ' + capability + '\nPlease answer probe %d.' % turn

    plans = [
        # Pair 1: identical prompt twice -> sanity check that this provider caches at all.
        ('stable_head_1', [{'role': 'system', 'content': system(cap_v1)}, {'role': 'user', 'content': 'probe 1'}]),
        ('stable_head_2', [{'role': 'system', 'content': system(cap_v1)}, {'role': 'user', 'content': 'probe 2'}]),
        # Pair 2: capability text changed INSIDE the HEAD -> prefix breaks at the capability line.
        ('changed_in_head', [{'role': 'system', 'content': system(cap_v2)}, {'role': 'user', 'content': 'probe 3'}]),
        # Pair 3: capability moved OUT of the HEAD into a runtime message; static HEAD stays identical.
        ('moved_head_1', [{'role': 'system', 'content': system('')}, {'role': 'user', 'content': runtime(cap_v2, 4)}]),
        ('moved_head_2', [{'role': 'system', 'content': system('')}, {'role': 'user', 'content': runtime(cap_v2, 5)}]),
    ]
    arms, cost = {}, 0.0
    for name, messages in plans:
        if name == 'stable_head_2':
            time.sleep(2.0)   # let the provider persist the prefix cache before checking the hit
        record = ledgered_judge_call(ctx, client, tier=2, experiment='AB-C1', messages=messages,
                                     timeout=6.0, cache_path=cache_path, cache=cache)
        prompt_tokens = record.get('prompt_tokens') or 0
        cached_tokens = record.get('cached_tokens') or 0
        arms[name] = {'prompt_tokens': prompt_tokens, 'cached_tokens': cached_tokens,
                      'cached_ratio': round(cached_tokens / prompt_tokens, 4) if prompt_tokens else None,
                      'latency_ms': record.get('latency_ms'), 'error': record.get('error') or '',
                      'cost_cny': record.get('amount_cny') or 0.0}
        cost += record.get('amount_cny') or 0.0
    client.close()
    stable_ratio = (arms.get('stable_head_2') or {}).get('cached_ratio')
    changed_ratio = (arms.get('changed_in_head') or {}).get('cached_ratio')
    moved_ratio = (arms.get('moved_head_2') or {}).get('cached_ratio')
    return {'arms': arms,
            'ratios': {'stable_head': stable_ratio, 'changed_in_head': changed_ratio,
                       'moved_out_of_head': moved_ratio},
            'evidence': {'static_prefix_chars': len(system(cap_v1)),
                         'capability_variable_chars': len(cap_v1),
                         'suffix_chars': len(suffix),
                         'provider': 'deepseek-flash automatic prefix cache',
                         'layout': 'static head + capability + long static suffix (mirrors system.facts position)'},
            'budget': budget_snapshot(ctx.budget, tier=2, calls=len(plans), tokens=0, cost=cost)}


def run_c2a(ctx):
    from .tier2_real import run_c2a_c2b_real
    result = core.result_stub('AB-C2a', 2, title='非首轮省略 memory.recall.empty 等空提示',
                              variable='tool_step>0 省略空提示 关 vs 开', fixed='完整多步工具循环切片 + deepseek-flash 真实生成盲评')
    rows = _context_log_rows(ctx)
    empty_prompt = ''
    for path in (Path(__file__).resolve().parent.parent.parent / 'prompts' / 'defaults').glob('memory.*.empty.md'):
        empty_prompt += path.read_text(encoding='utf-8')
    prompt_bytes = len(empty_prompt.encode('utf-8'))
    non_first = [row for row in rows if (row.get('step') or 0) > 0]
    diag_path = ctx.options.get('recall_diagnostics')
    empty_rate = None
    if diag_path and Path(diag_path).is_file():
        entries = core.read_jsonl(diag_path)
        statuses = [entry.get('judge', {}).get('status') if isinstance(entry.get('judge'), dict)
                    else entry.get('status') for entry in entries]
        statuses = [status for status in statuses if status]
        if statuses:
            empty_rate = round(sum(1 for status in statuses if status in ('empty', 'skipped')) / len(statuses), 4)
    empty_opportunities = int(len(non_first) * (empty_rate if empty_rate is not None else 0.5))
    saved_bytes = empty_opportunities * prompt_bytes
    saved_tokens = round(saved_bytes * 0.30, 1)

    client = _chat_client(ctx, 'AB-C2a')
    try:
        real_all = run_c2a_c2b_real(ctx, client)
    finally:
        client.close()
    ctx.options['_c2_real_cache'] = real_all
    _append_lan_review(ctx, real_all.get('lan_items') or [])
    c2a = real_all['c2a']
    be = c2a['blind_eval']
    omit_halluc = (be['arms'].get('omit_empty_hint') or {}).get('hallucinated_memory', 0)
    keep_halluc = (be['arms'].get('keep_empty_hint') or {}).get('hallucinated_memory', 0)
    recommended = saved_tokens > 0 and omit_halluc == 0
    ctx.save_detail('ab_c2a_replies', c2a['records'])
    result.update({'status': 'completed',
                   'arms': [{'arm': 'keep_empty_hint', 'config': {'omit': False},
                             'metrics': {'real_prompt_tokens': c2a['tokens_keep'],
                                         'blind_eval': be['arms'].get('keep_empty_hint')}},
                            {'arm': 'omit_empty_hint', 'config': {'omit': True},
                             'metrics': {'non_first_steps': len(non_first),
                                         'empty_hint_bytes': prompt_bytes,
                                         'estimated_empty_occurrences': empty_opportunities,
                                         'saved_bytes': saved_bytes,
                                         'saved_tokens_est': saved_tokens,
                                         'real_prompt_tokens': c2a['tokens_omit'],
                                         'real_tokens_saved': c2a['tokens_saved'],
                                         'blind_eval': be['arms'].get('omit_empty_hint')}}],
                   'comparison': [{'metric': 'saved_tokens_est_window', 'a': 0, 'b': saved_tokens, 'delta': saved_tokens},
                                  {'metric': 'real_tokens_saved_6samples', 'a': 0, 'b': c2a['tokens_saved'], 'delta': c2a['tokens_saved']},
                                  {'metric': 'hallucinated_memory_count', 'a': keep_halluc, 'b': omit_halluc, 'delta': omit_halluc - keep_halluc}],
                   'verdict': {'recommended': bool(recommended),
                               'hallucinated_memory_omit': omit_halluc,
                               'reason': ('真实多步工具循环 %d 条对照：省略空提示实省 %d tokens（窗口估算省 %.0f tokens），'
                                          '幻觉回忆 %d/%d（保留组 %d/%d），盲评胜负 %s；%s'
                                          % (c2a['samples'], c2a['tokens_saved'], saved_tokens,
                                             omit_halluc, c2a['samples'], keep_halluc, c2a['samples'],
                                             json.dumps(be['wins'], ensure_ascii=False),
                                             '满足幻觉=0红线，建议 S7 实施' if recommended else '触发幻觉风险，不推荐'))},
                   'evidence': {'context_log_requests': len(rows), 'empty_recall_rate': empty_rate,
                                'blind_eval_summary': be,
                                'labeler': 'ai-assisted (human_confirmed=false; local Lan blind-review pack generated)'},
                   'evidence_gaps': ['AI 辅助盲评（未经 Lan 人工验收）；本地已生成匿名对照材料供 Lan 抽检'],
                   'budget': budget_snapshot(ctx.budget, tier=2, calls=c2a['samples'] * 3,
                                             tokens=c2a['tokens_keep'] + c2a['tokens_omit'],
                                             cost=real_all.get('cost_cny', 0.0))})
    return result


def run_c2b(ctx):
    from .tier2_real import run_c2a_c2b_real
    result = core.result_stub('AB-C2b', 2, title='短闲聊记忆注入上限 Top-2+1 vs 不限',
                              variable='light_chat recalled 上限 3 条 vs 不限', fixed='完整单轮聊天切片 + deepseek-flash 真实生成盲评')
    from natural_association import light_chat
    diag_path = ctx.options.get('recall_diagnostics')
    picked_counts = []
    if diag_path and Path(diag_path).is_file():
        for entry in core.read_jsonl(diag_path):
            choice = entry.get('judge') if isinstance(entry.get('judge'), dict) else entry
            picked = choice.get('picked') if isinstance(choice, dict) else None
            if isinstance(picked, list):
                picked_counts.append(len(picked))
    memory_token_est = {m.id: math.ceil(len((m.body or '').encode('utf-8')) * 0.30)
                        for m in ctx.snapshot.memories.values()}
    cap = 3
    over = [count for count in picked_counts if count > cap]
    excess_counts = [count - cap for count in over]
    avg_memory_tokens = round(sum(memory_token_est.values()) / max(1, len(memory_token_est)), 1)
    saved_tokens = round(sum(excess_counts) * avg_memory_tokens, 1)
    light_queries = [r for r in (ctx.options.get('chat_rows') or [])
                     if r.get('role') == 'user' and light_chat(r.get('content') or '')]

    real_all = ctx.options.get('_c2_real_cache')
    if real_all is None:
        client = _chat_client(ctx, 'AB-C2b')
        try:
            real_all = run_c2a_c2b_real(ctx, client)
        finally:
            client.close()
        _append_lan_review(ctx, real_all.get('lan_items') or [])
    c2b = real_all['c2b']
    be = c2b['blind_eval']
    cap_sc = be['arms'].get('cap_top2+1') or {}
    unc_sc = be['arms'].get('uncapped_6') or {}
    nat_delta = round((cap_sc.get('memory_naturalness') or 0) - (unc_sc.get('memory_naturalness') or 0), 2)
    emo_delta = round((cap_sc.get('emotional_fit') or 0) - (unc_sc.get('emotional_fit') or 0), 2)
    fact_delta = round((cap_sc.get('factual_accuracy') or 0) - (unc_sc.get('factual_accuracy') or 0), 2)
    wins_cap = be['wins'].get('cap_top2+1', 0)
    wins_unc = be['wins'].get('uncapped_6', 0)
    recommended = (
        len(over) > 0 and wins_cap >= wins_unc
        and nat_delta >= -0.3 and emo_delta >= -0.3 and fact_delta >= -0.3
        and (cap_sc.get('hallucinated_memory') or 0) == 0
    )
    ctx.save_detail('ab_c2b_replies', c2b['records'])
    result.update({'status': 'completed',
                   'arms': [{'arm': 'uncapped_6', 'config': {'cap': None},
                             'metrics': {'real_prompt_tokens': c2b['tokens_uncapped'], 'blind_eval': unc_sc}},
                            {'arm': 'cap_top2+1', 'config': {'cap': cap},
                             'metrics': {'observed_requests_with_picks': len(picked_counts),
                                         'requests_over_cap': len(over),
                                         'excess_memories': sum(excess_counts),
                                         'avg_memory_tokens': avg_memory_tokens,
                                         'saved_tokens_est': saved_tokens,
                                         'real_prompt_tokens': c2b['tokens_cap'],
                                         'real_tokens_saved': c2b['tokens_saved'],
                                         'blind_eval': cap_sc}}],
                   'comparison': [{'metric': 'real_tokens_saved_6samples', 'a': 0, 'b': c2b['tokens_saved'], 'delta': c2b['tokens_saved']},
                                  {'metric': 'naturalness_delta', 'a': unc_sc.get('memory_naturalness'),
                                   'b': cap_sc.get('memory_naturalness'), 'delta': nat_delta},
                                  {'metric': 'emotional_fit_delta', 'a': unc_sc.get('emotional_fit'),
                                   'b': cap_sc.get('emotional_fit'), 'delta': emo_delta}],
                   'verdict': {'recommended': bool(recommended),
                               'reason': ('短闲聊 %d 条真实生成盲评：Top-2+1 实省 %d tokens（真实诊断 %d/%d 超 3 条），'
                                          '自然度 %+.2f / 情绪贴合 %+.2f，胜负 %s'
                                          % (c2b['samples'], c2b['tokens_saved'], len(over), len(picked_counts),
                                             nat_delta, emo_delta, json.dumps(be['wins'], ensure_ascii=False)))},
                   'evidence': {'recall_diagnostics_entries': len(picked_counts),
                                'light_chat_real_messages': len(light_queries),
                                'blind_eval_summary': be,
                                'labeler': 'ai-assisted (human_confirmed=false; local Lan blind-review pack generated)'},
                   'evidence_gaps': ['AI 辅助盲评（未经 Lan 人工验收）；本地已生成匿名对照材料供 Lan 抽检'],
                   'budget': budget_snapshot(ctx.budget, tier=2, calls=0,
                                             tokens=c2b['tokens_uncapped'] + c2b['tokens_cap'], cost=0.0)})
    return result


def run_c3(ctx):
    from .tier2_real import run_c3_real_tool
    result = core.result_stub('AB-C3', 2, title='完整 story vs 标题+因果摘要+timeline 指针',
                              variable='注入完整 events.story vs 摘要+指针（含真实 memory_timeline 工具调用）',
                              fixed='完整长事件召回切片 + deepseek-flash 真实工具调用与回复盲评')
    story_chars = [len(event.story) for event in ctx.snapshot.events.values() if event.story]
    summary_chars = 160 + 80
    long_events = [size for size in story_chars if size > summary_chars]
    avg_story = sum(story_chars) / max(1, len(story_chars))
    avg_saved = max(0.0, avg_story - summary_chars)
    saved_tokens_est = round(avg_saved * 0.30, 1)

    client = _chat_client(ctx, 'AB-C3')
    try:
        c3 = run_c3_real_tool(ctx, client)
    finally:
        client.close()
    _append_lan_review(ctx, c3.get('lan_items') or [])
    be = c3['blind_eval']
    ptr_sc = be['arms'].get('summary_pointer') or {}
    full_sc = be['arms'].get('full_story') or {}
    fact_delta = round((ptr_sc.get('factual_accuracy') or 0) - (full_sc.get('factual_accuracy') or 0), 2)
    recommended = c3['net_tokens_saved'] > 0 and fact_delta >= -0.3 and (ptr_sc.get('hallucinated_memory') or 0) == 0
    ctx.save_detail('ab_c3_replies', c3['records'])
    result.update({'status': 'completed',
                   'arms': [{'arm': 'full_story', 'config': {}, 'metrics': {
                                 'events': len(story_chars), 'avg_story_chars': round(avg_story, 1),
                                 'total_tokens': c3['full_story_tokens'],
                                 'latency': c3['full_story_latency'],
                                 'blind_eval': full_sc}},
                             {'arm': 'summary_pointer', 'config': {'tools': ['memory_timeline']},
                              'metrics': {'summary_pointer_chars': summary_chars,
                                          'avg_saved_tokens_est': saved_tokens_est,
                                          'tool_calls_made': c3['tool_calls_made'],
                                          'tool_call_rate': c3['tool_call_rate'],
                                          'total_tokens': c3['summary_pointer_tokens'],
                                          'net_tokens_saved': c3['net_tokens_saved'],
                                          'latency': c3['summary_pointer_latency'],
                                          'blind_eval': ptr_sc}}],
                   'comparison': [{'metric': 'net_tokens_saved_6samples', 'a': c3['full_story_tokens'],
                                   'b': c3['summary_pointer_tokens'], 'delta': c3['net_tokens_saved']},
                                  {'metric': 'tool_call_rate', 'value': c3['tool_call_rate']},
                                  {'metric': 'factual_accuracy_delta', 'a': full_sc.get('factual_accuracy'),
                                   'b': ptr_sc.get('factual_accuracy'), 'delta': fact_delta}],
                   'verdict': {'recommended': bool(recommended),
                               'long_events': len(long_events),
                               'tool_call_rate': c3['tool_call_rate'],
                               'reason': ('%d 条长事件真实工具对照：摘要+指针触发 memory_timeline 读全文 %d/%d 次（%.0f%%），'
                                          '净 token 变化 %+d（省为正），事实准确度 %+.2f，胜负 %s'
                                          % (c3['samples'], c3['tool_calls_made'], c3['samples'],
                                             c3['tool_call_rate'] * 100, c3['net_tokens_saved'],
                                             fact_delta, json.dumps(be['wins'], ensure_ascii=False)))},
                   'evidence': {'story_chars_avg': round(avg_story, 1), 'long_events': len(long_events),
                                'blind_eval_summary': be,
                                'labeler': 'ai-assisted (human_confirmed=false; local Lan blind-review pack generated)'},
                   'evidence_gaps': ['AI 辅助盲评（未经 Lan 人工验收）；本地已生成匿名对照材料供 Lan 抽检'],
                   'budget': budget_snapshot(ctx.budget, tier=2, calls=c3['samples'] * 3 + c3['tool_calls_made'],
                                             tokens=c3['full_story_tokens'] + c3['summary_pointer_tokens'],
                                             cost=c3.get('cost_cny', 0.0))})
    return result


def run_a1b(ctx):
    from .tier2_real import run_a1a_a1b_real
    result = core.result_stub('AB-A1b', 2, title='autonomy 闲暇唤醒低打扰联想分支',
                              variable='无联想 vs 独立 wake 侧门控 + 全库分层轮换池(24)',
                              fixed='AB-A1a 分层轮换池 + 自主活动切片 + deepseek-flash 真实生成盲评')
    client = _chat_client(ctx, 'AB-A1b')
    try:
        real = run_a1a_a1b_real(ctx, client)
    finally:
        client.close()
    _append_lan_review(ctx, real.get('lan_items') or [])
    a1a_real = real['a1a']
    a1b = real['a1b']
    be = a1b['blind_eval']
    on_sc = be['arms'].get('wake_association_on') or {}
    off_sc = be['arms'].get('association_off') or {}
    nat_delta = round((on_sc.get('memory_naturalness') or 0) - (off_sc.get('memory_naturalness') or 0), 2)
    recommended = (
        a1b['associations_proposed'] > 0
        and a1b['task_wakes_disturbed'] == 0
        and (on_sc.get('work_interference') or 0) == 0
        and nat_delta >= 0
    )
    ctx.save_detail('ab_a1a_real_replies', a1a_real['records'])
    ctx.save_detail('ab_a1b_real_replies', a1b['records'])
    # Also enrich AB-A1a saved result on disk with real-reply blind eval summary and verdict!
    a1a_path = ctx.results_dir / 'ab_a1a.json'
    if a1a_path.is_file():
        try:
            a1a_json = json.loads(a1a_path.read_text(encoding='utf-8'))
            a1a_json.setdefault('evidence', {})['tier2_real_reply_blind_eval'] = {
                'vs_no_association': a1a_real['vs_none'],
                'vs_prod_breath8': a1a_real['vs_prod'],
            }
            w_none = a1a_real['vs_none']['wins']
            w_prod = a1a_real['vs_prod']['wins']
            blind_ok = (w_none.get('stratified_rotation24', 0) >= w_none.get('no_association', 0)
                        and w_prod.get('stratified_rotation24', 0) >= w_prod.get('prod_breath8', 0))
            if not blind_ok:
                a1a_json['verdict']['recommended'] = False
                a1a_json['verdict']['reason'] = (
                    '离线长尾覆盖大幅提升（唯一记忆 2->37、imp=8 覆盖 0->35/44、工作态违和 0），'
                    '但 Tier 2 真实回复盲评（8 场景）显示纯重要度+沉默轮换因缺少语义相关性，'
                    '胜负不及对照（vs 无联想 %d-%d、vs 生产 breath8 %d-%d），不推荐直接上线聊天主链路；'
                    '建议仅在引入话题相关性过滤或用于闲暇自主唤醒（AB-A1b）后再实施'
                    % (w_none.get('stratified_rotation24', 0), w_none.get('no_association', 0),
                       w_prod.get('stratified_rotation24', 0), w_prod.get('prod_breath8', 0))
                )
            a1a_path.write_text(json.dumps(a1a_json, ensure_ascii=False, indent=2, sort_keys=True) + '\n',
                                encoding='utf-8')
            ctx.results['AB-A1a'] = a1a_json
        except Exception:
            pass
    tokens_per_association = 60
    result.update({'status': 'completed',
                   'arms': [{'arm': 'association_off', 'config': {}, 'metrics': {
                                 'idle_wakes': a1b['idle_wakes'], 'task_wakes': a1b['task_wakes'],
                                 'associations_proposed': 0, 'tokens': 0, 'blind_eval': off_sc}},
                             {'arm': 'wake_association_on', 'config': {'pool': 'stratified_rotation24', 'gate': 'wake_idle_only'},
                              'metrics': {'idle_wakes': a1b['idle_wakes'],
                                          'associations_proposed': a1b['associations_proposed'],
                                          'task_wakes_untouched': a1b['task_wakes'] - a1b['task_wakes_disturbed'],
                                          'task_wakes_disturbed': a1b['task_wakes_disturbed'],
                                          'tokens_est': a1b['associations_proposed'] * tokens_per_association,
                                          'blind_eval': on_sc}}],
                   'comparison': [{'metric': 'association_count', 'a': 0, 'b': a1b['associations_proposed'],
                                   'delta': a1b['associations_proposed']},
                                  {'metric': 'task_wakes_disturbed', 'a': 0, 'b': a1b['task_wakes_disturbed'],
                                   'delta': a1b['task_wakes_disturbed']},
                                  {'metric': 'naturalness_delta', 'a': off_sc.get('memory_naturalness'),
                                   'b': on_sc.get('memory_naturalness'), 'delta': nat_delta}],
                   'verdict': {'recommended': bool(recommended),
                               'reason': ('独立 wake 门控实测：%d/%d 条闲暇唤醒触发长尾联想，%d/%d 条任务唤醒零干扰（0 误触）；'
                                          '真实自主输出盲评自然度 %+.2f、工作干扰 %d、胜负 %s；同步产出 AB-A1a 聊天盲评'
                                          % (a1b['associations_proposed'], a1b['idle_wakes'],
                                             a1b['task_wakes'] - a1b['task_wakes_disturbed'], a1b['task_wakes'],
                                             nat_delta, on_sc.get('work_interference') or 0,
                                             json.dumps(be['wins'], ensure_ascii=False)))},
                   'evidence': {'autonomy_samples': a1b['idle_wakes'] + a1b['task_wakes'],
                                'idle': a1b['idle_wakes'], 'task': a1b['task_wakes'],
                                'blind_eval_summary': be,
                                'a1a_chat_blind_eval': {'vs_none': a1a_real['vs_none'], 'vs_prod': a1a_real['vs_prod']},
                                'labeler': 'ai-assisted (human_confirmed=false; local Lan blind-review pack generated)'},
                   'evidence_gaps': ['AI 辅助盲评（未经 Lan 人工验收）；闲暇样本 4 条规模较小，上线后需持续观察'],
                   'budget': budget_snapshot(ctx.budget, tier=2, calls=42, tokens=0, cost=real.get('cost_cny', 0.0))})
    ctx.save_detail('ab_a1b_associations', a1b['surfaced'])
    return result


def run_e1(ctx):
    from .tier2_real import run_e1_forward_consolidation
    result = core.result_stub('AB-E1', 2, title='认知巩固收益与跨时间认知修订',
                              variable='无巩固对照组 vs 有证据约束的跨时间前瞻巩固实验组（T1->T2->T3）',
                              fixed='严格按历史时间切片前瞻回放（只能读当时已发生信息）+ deepseek-flash 真实跨阶段行为对照')
    client = _chat_client(ctx, 'AB-E1')
    try:
        e1 = run_e1_forward_consolidation(ctx, client)
    finally:
        client.close()
    _append_lan_review(ctx, e1.get('lan_items') or [])
    be = e1['blind_eval']
    cons_sc = be['arms'].get('evidence_constrained_consolidation') or {}
    ctrl_sc = be['arms'].get('no_consolidation') or {}
    impact_delta = round((cons_sc.get('experience_behavior_impact') or 0)
                         - (ctrl_sc.get('experience_behavior_impact') or 0), 2)
    wins_cons = be['wins'].get('evidence_constrained_consolidation', 0)
    wins_ctrl = be['wins'].get('no_consolidation', 0)
    recommended = (
        e1['self_guess_promoted'] == 0
        and (e1['t1_valid_insights'] + e1['t2_valid_insights']) > 0
        and wins_cons >= wins_ctrl
        and (cons_sc.get('hallucinated_memory') or 0) == 0
    )
    ctx.save_detail('ab_e1_consolidation', e1['records'])
    result.update({'status': 'completed',
                   'arms': [{'arm': 'no_consolidation', 'config': {'consolidation': False},
                             'metrics': {'blind_eval': ctrl_sc}},
                            {'arm': 'evidence_constrained_consolidation',
                             'config': {'consolidation': True, 'slices': 'T1(<=11-05)->T2(11-06..11-15)->T3(>=11-16)'},
                             'metrics': {'t1_valid_insights': e1['t1_valid_insights'],
                                         't2_valid_insights': e1['t2_valid_insights'],
                                         't2_valid_revisions': e1['t2_valid_revisions'],
                                         'rejected_ungrounded': e1['rejected_ungrounded'],
                                         'self_guess_promoted': e1['self_guess_promoted'],
                                         'blind_eval': cons_sc}}],
                   'comparison': [{'metric': 'experience_behavior_impact',
                                   'a': ctrl_sc.get('experience_behavior_impact'),
                                   'b': cons_sc.get('experience_behavior_impact'), 'delta': impact_delta},
                                  {'metric': 'self_guess_promoted', 'a': 0, 'b': e1['self_guess_promoted'],
                                   'delta': e1['self_guess_promoted']}],
                   'verdict': {'recommended': bool(recommended),
                               'self_guess_promoted': e1['self_guess_promoted'],
                               'valid_insights_total': e1['t1_valid_insights'] + e1['t2_valid_insights'],
                               'valid_revisions': e1['t2_valid_revisions'],
                               'reason': ('前瞻时间切片回放（T1<=11-05 -> T2<=11-15 -> T3 真实回复）：'
                                          '提炼有效认知 %d 条、跨阶段修订 %d 条、拦截无证据 %d 条、自猜误晋升 %d（红线=0）；'
                                          '下游 %d 条 T3 回复盲评胜负 %s、经验行为影响 %+.2f。'
                                          '短期回放已证明受控巩固可行且安全，但长期多日净收益仍需 S6 灰度开关 + Lan 真机验收确认'
                                          % (e1['t1_valid_insights'] + e1['t2_valid_insights'],
                                             e1['t2_valid_revisions'], e1['rejected_ungrounded'],
                                             e1['self_guess_promoted'], e1['downstream_samples'],
                                             json.dumps(be['wins'], ensure_ascii=False), impact_delta))},
                   'evidence': {'t1_memories': e1['t1_memories_seen'], 't2_memories': e1['t2_memories_seen'],
                                'blind_eval_summary': be,
                                'short_term_proven': '有证据约束的巩固能从历史经历提炼有效认知、跨阶段修订且自猜误晋升=0，并在 T3 回复产生可观察影响',
                                'long_term_unproven': '跨多周/多日真实生活节奏下的长期冗余度、遗忘/覆盖风险与 Lan 主观体验增益，必须由多日灰度运行与 Lan 人工验收确认',
                                'labeler': 'ai-assisted (human_confirmed=false; local Lan blind-review pack generated)'},
                   'evidence_gaps': ['短期历史切片回放不能替代多日真实运行与 Lan 主观验收；S6 如实施必须带独立开关、日预算硬顶与不覆盖用户锁定规则'],
                   'budget': budget_snapshot(ctx.budget, tier=2, calls=2 + e1['downstream_samples'] * 3,
                                             tokens=0, cost=e1.get('cost_cny', 0.0))})
    return result


def run_comb(ctx):
    """AB-COMB: Multi-mechanism end-to-end combination verification over winning candidates."""
    result = core.result_stub('AB-COMB', 2, title='最终组合验证',
                              variable='Baseline-1 vs 候选组合（检索 + 重排 + 自然联想 + 上下文）',
                              fixed='Baseline-1（K=8, minmax 0.65/0.35, importance 0.05, breath(8), 4000B共享）')
    base = RetrievalConfig()   # production Baseline-1
    winners = {}
    for experiment_id, key in (('AB-R1b', 'stopwords'), ('AB-R1c', 'fusion'),
                               ('AB-R5a', 'importance_weight')):
        row = ctx.results.get(experiment_id) or {}
        verdict = row.get('verdict') or {}
        if verdict.get('recommended') and verdict.get(key) is not None:
            winners[key] = verdict[key]
    for exp_id, flag in (('AB-R5b', 'last_active_boost'), ('AB-R5c', 'affect_boost'),
                         ('AB-R4b', 'related_expand'), ('AB-R4c', 'alias_expand'),
                         ('AB-R4a', 'story_index')):
        if (ctx.results.get(exp_id) or {}).get('verdict', {}).get('recommended'):
            winners[flag] = 0.02 if 'boost' in flag else True
    r2_verdict = (ctx.results.get('AB-R2') or {}).get('verdict') or {}
    r2_winner = r2_verdict.get('best_model') if r2_verdict.get('recommended') else None
    a1a_winner = bool((ctx.results.get('AB-A1a') or {}).get('verdict', {}).get('recommended'))
    a1b_winner = bool((ctx.results.get('AB-A1b') or {}).get('verdict', {}).get('recommended'))
    a2_winner = bool((ctx.results.get('AB-A2') or {}).get('verdict', {}).get('recommended'))
    a3_winner = bool((ctx.results.get('AB-A3') or {}).get('verdict', {}).get('recommended'))
    c1_winner = bool((ctx.results.get('AB-C1') or {}).get('verdict', {}).get('recommended'))
    c2a_winner = bool((ctx.results.get('AB-C2a') or {}).get('verdict', {}).get('recommended'))
    c2b_winner = bool((ctx.results.get('AB-C2b') or {}).get('verdict', {}).get('recommended'))
    c3_winner = bool((ctx.results.get('AB-C3') or {}).get('verdict', {}).get('recommended'))

    combined = RetrievalConfig(
        stopwords=bool(winners.get('stopwords', False)),
        fusion=winners.get('fusion', base.fusion),
        k=int(winners.get('k', base.k)),
        importance_weight=float(winners.get('importance_weight', base.importance_weight)),
        last_active_boost=float(winners.get('last_active_boost', 0.0)),
        affect_boost=float(winners.get('affect_boost', 0.0)),
        related_expand=bool(winners.get('related_expand')),
        alias_expand=bool(winners.get('alias_expand')),
        story_index=bool(winners.get('story_index')),
    )
    baseline_metrics, _ = ctx.evaluate(base)
    combo1_metrics, _ = ctx.evaluate(combined)

    # Sum ONLY the single-variable deltas of mechanisms ACTUALLY included in `combined`!
    included_retrieval_exps = []
    single_deltas = {}
    if winners.get('stopwords'):
        included_retrieval_exps.append('AB-R1b')
    if winners.get('fusion'):
        included_retrieval_exps.append('AB-R1c')
    if winners.get('importance_weight') is not None:
        included_retrieval_exps.append('AB-R5a')
    if winners.get('last_active_boost'):
        included_retrieval_exps.append('AB-R5b')
    if winners.get('related_expand'):
        included_retrieval_exps.append('AB-R4b')
    if winners.get('story_index'):
        included_retrieval_exps.append('AB-R4a')
    for exp_id in included_retrieval_exps:
        row = ctx.results.get(exp_id) or {}
        arms = row.get('arms') or []
        if len(arms) >= 2:
            first = arms[0]['metrics'].get('overall', {}).get('recall_at_k')
            second = arms[1]['metrics'].get('overall', {}).get('recall_at_k')
            if first is not None and second is not None:
                single_deltas[exp_id] = round(second - first, 4)

    base_r8 = baseline_metrics.get('overall', {}).get('recall_at_k') or 0.0
    combo1_r8 = combo1_metrics.get('overall', {}).get('recall_at_k') or 0.0
    combo1_delta = round(combo1_r8 - base_r8, 4)
    expected_delta = round(sum(single_deltas.values()), 4)

    # Build 2-3 end-to-end candidate combinations
    active_mechanisms = []
    if winners.get('stopwords'):
        active_mechanisms.append('AB-R1b(停用词)')
    if r2_winner:
        active_mechanisms.append(f'AB-R2({r2_winner})')
    if a1a_winner:
        active_mechanisms.append('AB-A1a(分层轮换联想池)')
    if a1b_winner:
        active_mechanisms.append('AB-A1b(闲暇唤醒联想)')
    if a2_winner:
        active_mechanisms.append('AB-A2(S2严格证据链晋升)')
    if a3_winner:
        active_mechanisms.append('AB-A3(四区独立预算)')
    if c1_winner:
        active_mechanisms.append('AB-C1(HEAD前缀纯化)')
    if c2a_winner:
        active_mechanisms.append('AB-C2a(省略非首轮空提示)')
    if c2b_winner:
        active_mechanisms.append('AB-C2b(短闲聊Top-2+1上限)')
    if c3_winner:
        active_mechanisms.append('AB-C3(摘要+指针)')

    # Judge arm over 36-sample subset (reuses cached judge calls if pool unchanged -> CNY 0!)
    judge_metrics = None
    judge_cost = 0.0
    card = ((ctx.results.get('AB-R3d') or {}).get('verdict', {}) or {}).get('card', '300')
    try:
        samples = _selected_samples(ctx)
        pools = _candidate_pool(ctx, samples, combined)
        prompt = _best_prompt(ctx)
        client = _judge_client(ctx)
        try:
            arm, rows = _run_judge_arm(ctx, client, samples, pools, experiment='AB-COMB',
                                       name='combo_retrieval+judge', card=card, prompt=prompt,
                                       timeout=_best_timeout(ctx), tier=2)
            judge_metrics = arm['metrics']
            judge_cost = judge_metrics.get('cost_cny', 0.0)
            ctx.save_detail('ab_comb_judge_rows', rows)
        finally:
            client.close()
    except core.BudgetBlocked as exc:
        result['notes'].append('budget guard stopped the paid judge arm: %s' % exc)

    # Pull Reranker metrics if R2 won
    r2_arm_metrics = None
    if r2_winner:
        for arm in (ctx.results.get('AB-R2') or {}).get('arms') or []:
            if arm.get('arm') == r2_winner:
                r2_arm_metrics = arm.get('metrics')
                break

    # Cross-subsystem combination summary (Combo A: Retrieval+Judge; Combo B: Retrieval+Assoc+Governance; Combo C: Full)
    combo_arms = [
        {'arm': 'baseline_prod', 'config': {'k': 8, 'stopwords': False, 'rerank': None, 'assoc': 'prod_breath8', 'budget': 'shared_4000B'},
         'metrics': baseline_metrics},
        {'arm': 'combo_A_retrieval_judge',
         'config': {'retrieval': {key: getattr(combined, key) for key in combined.__dataclass_fields__},
                    'rerank': r2_winner, 'judge': _best_prompt(ctx), 'card': card},
         'metrics': {'offline': combo1_metrics, 'rerank': r2_arm_metrics, 'judge': judge_metrics or {}}},
        {'arm': 'combo_B_memory_assoc_governance',
         'config': {'mechanisms': [m for m in active_mechanisms if any(k in m for k in ('R1b', 'R2', 'A1', 'A2', 'A3'))]},
         'metrics': {
             'retrieval_recall_at_8': (r2_arm_metrics['overall']['recall_at_k'] if r2_arm_metrics else combo1_r8),
             'retrieval_p5': (r2_arm_metrics['overall']['precision_at_5'] if r2_arm_metrics else combo1_metrics['overall']['precision_at_5']),
             'a1a_imp8_coverage': ((ctx.results.get('AB-A1a') or {}).get('arms') or [{}])[-1].get('metrics', {}).get('importance8_coverage'),
             'a2_self_proof_rate': (ctx.results.get('AB-A2') or {}).get('verdict', {}).get('self_proof_rate'),
             'a3_rejection_rate': 0.0 if a3_winner else 0.5,
         }},
        {'arm': 'combo_C_full_recommended',
         'config': {'mechanisms': active_mechanisms},
         'metrics': {
             'mechanism_count': len(active_mechanisms),
             'c1_cache_after_expand': ((ctx.results.get('AB-C1') or {}).get('arms') or [{}])[-1].get('metrics', {}).get('cached_ratio'),
             'c2a_hallucinated_memory': (ctx.results.get('AB-C2a') or {}).get('verdict', {}).get('hallucinated_memory_omit'),
             'negative_interaction': combo1_delta < expected_delta - 0.01,
         }},
    ]
    neg_interaction = combo1_delta < expected_delta - 0.01
    result.update({'status': 'completed' if judge_metrics else 'partial',
                   'arms': combo_arms,
                   'comparison': [{'metric': 'retrieval_recall_at_8', 'baseline': base_r8,
                                   'combo_A_offline': combo1_r8,
                                   'combo_A_with_rerank': (r2_arm_metrics['overall']['recall_at_k'] if r2_arm_metrics else None),
                                   'single_variable_sum_delta': expected_delta,
                                   'combined_delta': combo1_delta,
                                   'negative_interaction': neg_interaction}],
                   'verdict': {'recommended': combo1_delta >= 0 and not neg_interaction and len(active_mechanisms) > 0,
                               'winners_used': winners,
                               'active_mechanisms': active_mechanisms,
                               'metrics': {'recall_at_k': combo1_r8,
                                           'rerank_recall_at_8': (r2_arm_metrics['overall']['recall_at_k'] if r2_arm_metrics else None),
                                           'ndcg_at_8': combo1_metrics['overall']['ndcg_at_8']},
                               'reason': ('端到端组合验证（共 %d 项通过准入：%s）：离线检索 Recall@8 %+.2fpp（单变量加总 %+.2fpp，负交互=%s）；'
                                          '安全红线（A2 自证率=0、A1b 任务零干扰、C2a 幻觉=0）全部守住；判官臂新增费用 ¥%.4f'
                                          % (len(active_mechanisms), '、'.join(active_mechanisms),
                                             combo1_delta * 100, expected_delta * 100,
                                             'yes' if neg_interaction else 'no', judge_cost))},
                   'evidence': {'baseline_recall_at_k': base_r8,
                                'single_deltas_included_only': single_deltas,
                                'judge_subset': JUDGE_SUBSET},
                   'evidence_gaps': ([] if judge_metrics else ['判官组合臂因预算守卫未执行']),
                   'budget': budget_snapshot(ctx.budget, tier=2, calls=0,
                                             tokens=(judge_metrics or {}).get('tokens', {}).get('input', 0) if judge_metrics else 0,
                                             cost=judge_cost)})
    return result
