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
    from . import __file__ as package_file
    root = Path(package_file).resolve().parent.parent
    rerank_refs = []
    for path in root.rglob('*.py'):
        try:
            text = path.read_text(encoding='utf-8', errors='ignore').lower()
        except OSError:
            continue
        if 'rerank' in text and 'recall-lab' not in str(path).replace('\\', '/'):
            rerank_refs.append(path.name)
    result = core.result_stub('AB-R2', 1, title='Reranker 候选排序',
                              variable='无 vs Qwen3-Reranker-0.6B vs BGE-Reranker-v2-m3',
                              fixed='最佳候选池（R1+R4 胜出项）')
    result.update({'status': 'blocked',
                   'verdict': {'recommended': None,
                               'reason': '没有可用的 rerank provider、模型或价格上界；按工单规则不在无法确认价格时发起付费调用'},
                   'evidence': {'repo_rerank_implementations': sorted(set(rerank_refs)),
                                'configured_rerank_model': None,
                                'price_upper_bound_confirmed': False,
                                'probe_paid_call': False},
                   'evidence_gaps': ['需要先有 rerank 服务端点、模型标识与价格表，才能跑 Precision@5/p95/单轮成本对照'],
                   'budget': budget_snapshot(ctx.budget, tier=1, cost=0.0)})
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
                                                   '6.3pp 召回差属于模型采样噪声，维持 4s 并记录该限制' % pick_overlap)}})
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


def run_c1(ctx):
    result = core.result_stub('AB-C1', 2, title='HEAD 静态前缀纯化（capabilities.description 移出 system.facts）',
                              variable='capabilities.description 在 HEAD vs 移到 runtime_required',
                              fixed='完整请求组装观测 + 真实 provider 前缀缓存微探针')
    rows = _context_log_rows(ctx)
    cache = {'requests': len(rows)}
    if rows:
        total_in = sum(row.get('actual_input_tokens') or 0 for row in rows)
        total_cached = sum(row.get('cached_tokens') or 0 for row in rows)
        cache.update({'input_tokens': total_in, 'cached_tokens': total_cached,
                      'cache_ratio': round(total_cached / total_in, 4) if total_in else None})
        by_kind = {}
        for row in rows:
            kind = row.get('kind') or 'unknown'
            entry = by_kind.setdefault(kind, {'n': 0, 'input': 0, 'cached': 0})
            entry['n'] += 1
            entry['input'] += row.get('actual_input_tokens') or 0
            entry['cached'] += row.get('cached_tokens') or 0
        for entry in by_kind.values():
            entry['cache_ratio'] = round(entry['cached'] / entry['input'], 4) if entry['input'] else None
        cache['by_kind'] = by_kind
        cache['tool_schema_bytes_avg'] = round(sum(row.get('tool_schema_bytes') or 0 for row in rows) / len(rows))
    cap = ctx.options.get('capabilities') or {}
    updates = cap.get('capability_updates') if isinstance(cap, dict) else None
    variable_chars = len(json.dumps(updates, ensure_ascii=False)) if updates else 0
    probe = _cache_probe(ctx)
    ratios = probe.get('ratios') or {}
    result.update({'status': 'partial',
                   'arms': [{'arm': name, 'config': {}, 'metrics': metrics}
                            for name, metrics in (probe.get('arms') or {}).items()],
                   'comparison': [
                       {'metric': 'cache_hit_ratio_stable_head', 'value': ratios.get('stable_head')},
                       {'metric': 'cache_hit_ratio_changed_in_head', 'value': ratios.get('changed_in_head')},
                       {'metric': 'cache_hit_ratio_moved_out_of_head', 'value': ratios.get('moved_out_of_head')}],
                   'verdict': _cache_probe_verdict(ratios=ratios),
                   'evidence': {'context_log': cache,
                                'capability_updates_chars': variable_chars,
                                'probe': probe.get('evidence'),
                                'probe_kind': 'synthetic HEAD prefix modelled on system.facts, not the full production request'},
                   'evidence_gaps': ['未用完整生产请求组装验证（隔离环境不加载人设/工具 schema 全量），结果为 provider 级缓存行为证据，需 S7 实施后做真机复测'],
                   'budget': probe.get('budget') or budget_snapshot(ctx.budget, tier=2, cost=0.0)})
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
    result = core.result_stub('AB-C2a', 2, title='非首轮省略 memory.recall.empty 等空提示',
                              variable='tool_step>0 省略空提示 关 vs 开', fixed='完整多步工具循环切片')
    rows = _context_log_rows(ctx)
    empty_prompt = ''
    for path in (Path(__file__).resolve().parent.parent.parent / 'prompts' / 'defaults').glob('memory.*.empty.md'):
        empty_prompt += path.read_text(encoding='utf-8')
    prompt_bytes = len(empty_prompt.encode('utf-8'))
    non_first = [row for row in rows if (row.get('step') or 0) > 0]
    step_counts = {}
    for row in non_first:
        step = row.get('step') or 0
        step_counts[step] = step_counts.get(step, 0) + 1
    # recall-diagnostics says how often recall was empty in the same window.
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
    saved_tokens = round(saved_bytes * 0.30, 1)  # real context-log calibration
    result.update({'status': 'partial',
                   'arms': [{'arm': 'omit_empty_hint', 'config': {},
                             'metrics': {'non_first_steps': len(non_first),
                                         'empty_hint_bytes': prompt_bytes,
                                         'estimated_empty_occurrences': empty_opportunities,
                                         'saved_bytes': saved_bytes,
                                         'saved_tokens_est': saved_tokens,
                                         'cost_saved_cny_per_request': round(saved_tokens * 2.0 / 1_000_000, 8)}}],
                   'comparison': [{'metric': 'saved_tokens_est', 'a': 0, 'b': saved_tokens, 'delta': saved_tokens}],
                   'verdict': {'recommended': saved_tokens > 0,
                               'reason': '真实 555 次请求中非首轮 %d 次；按空召回率 %.1f%% 估算每窗口节省 %.0f tokens'
                                         % (len(non_first), (empty_rate or 0) * 100, saved_tokens)},
                   'evidence': {'context_log_requests': len(rows),
                                'non_first_step_distribution': dict(sorted(step_counts.items())),
                                'empty_recall_rate': empty_rate,
                                'hallucination_check': 'NOT measurable offline; 必须=0 的幻觉回忆率只能由 Tier 2 真实生成证明'},
                   'evidence_gaps': ['仅 token/结构侧证据，无真实生成；幻觉回忆率需真实请求与盲评，本次未执行'],
                   'budget': budget_snapshot(ctx.budget, tier=2, cost=0.0)})
    return result


def run_c2b(ctx):
    result = core.result_stub('AB-C2b', 2, title='短闲聊记忆注入上限 Top-2+1 vs 不限',
                              variable='light_chat recalled 上限 3 条 vs 不限', fixed='完整单轮聊天切片')
    from natural_association import light_chat
    diag_path = ctx.options.get('recall_diagnostics')
    picked_counts, light_counts = [], []
    if diag_path and Path(diag_path).is_file():
        for entry in core.read_jsonl(diag_path):
            choice = entry.get('judge') if isinstance(entry.get('judge'), dict) else entry
            picked = choice.get('picked') if isinstance(choice, dict) else None
            if isinstance(picked, list):
                picked_counts.append(len(picked))
    memory_token_est = {}
    for memory in ctx.snapshot.memories.values():
        memory_token_est[memory.id] = math.ceil(len((memory.body or '').encode('utf-8')) * 0.30)
    cap = 3
    over = [count for count in picked_counts if count > cap]
    # Estimate the per-request saving from real recall sizes: memories beyond the cap in a request.
    # recall-diagnostics only carries counts, so the saving is bounded by the observed count excess.
    excess_counts = [count - cap for count in over]
    avg_memory_tokens = round(sum(memory_token_est.values()) / max(1, len(memory_token_est)), 1)
    saved_tokens = round(sum(excess_counts) * avg_memory_tokens, 1)
    light_queries = []
    if ctx.options.get('chat_rows'):
        for row in ctx.options['chat_rows']:
            if row.get('role') == 'user' and light_chat(row.get('content') or ''):
                light_queries.append(row)
    result.update({'status': 'partial',
                   'arms': [{'arm': 'cap_top2+1', 'config': {'cap': cap},
                             'metrics': {'observed_requests_with_picks': len(picked_counts),
                                         'requests_over_cap': len(over),
                                         'excess_memories': sum(excess_counts),
                                         'avg_memory_tokens': avg_memory_tokens,
                                         'saved_tokens_est': saved_tokens,
                                         'saved_cny_per_1k_turns_est': round(saved_tokens * 2.0 / 1_000_000 * 1000, 4)}}],
                   'comparison': [{'metric': 'saved_tokens_est', 'a': 0, 'b': saved_tokens, 'delta': saved_tokens}],
                   'verdict': {'recommended': len(over) > 0,
                               'reason': '真实诊断里 %d/%d 次请求注入超过 3 条；按平均记忆长度估算每窗口节省 %.0f tokens'
                                         % (len(over), len(picked_counts), saved_tokens)},
                   'evidence': {'recall_diagnostics_entries': len(picked_counts),
                                'light_chat_real_messages': len(light_queries),
                                'companionship_quality': 'not measured offline; 陪伴自然度盲评未执行'},
                   'evidence_gaps': ['仅 token/结构侧证据；短闲聊注入上限会否降低陪伴自然度必须真实生成盲评，本次未执行'],
                   'budget': budget_snapshot(ctx.budget, tier=2, cost=0.0)})
    return result


def run_c3(ctx):
    result = core.result_stub('AB-C3', 2, title='完整 story vs 标题+因果摘要+timeline 指针',
                              variable='注入完整 events.story vs 摘要+指针', fixed='完整长事件召回切片')
    story_chars = [len(event.story) for event in ctx.snapshot.events.values() if event.story]
    summary_chars = 160 + 80  # summary head + timeline pointer
    long_events = [size for size in story_chars if size > summary_chars]
    avg_story = sum(story_chars) / max(1, len(story_chars))
    avg_saved = max(0.0, avg_story - summary_chars)
    # Which eval samples ask about long events? (expected id maps to an event with a long story.)
    long_expected = 0
    affected = []
    for sample in ctx.samples:
        for memory_id in sample.expected:
            event = ctx.snapshot.events.get(memory_id)
            if event and len(event.story) > summary_chars:
                long_expected += 1
                affected.append({'sample_id': sample.sample_id, 'event_id': memory_id,
                                 'story_chars': len(event.story)})
                break
    saved_tokens_per_injection = round(avg_saved * 0.30, 1)
    result.update({'status': 'partial',
                   'arms': [{'arm': 'full_story', 'config': {}, 'metrics': {
                                 'events': len(story_chars), 'avg_story_chars': round(avg_story, 1),
                                 'max_story_chars': max(story_chars) if story_chars else 0}},
                             {'arm': 'summary_pointer', 'config': {},
                              'metrics': {'summary_pointer_chars': summary_chars,
                                          'avg_saved_chars_per_long_event': round(avg_saved, 1),
                                          'avg_saved_tokens_per_long_event': saved_tokens_per_injection}}],
                   'comparison': [{'metric': 'tokens_saved_per_injection_est', 'a': 0,
                                   'b': saved_tokens_per_injection, 'delta': saved_tokens_per_injection}],
                   'verdict': {'recommended': len(long_events) > 0,
                               'long_events': len(long_events), 'affected_eval_samples': len(affected),
                               'reason': '%d/%d 个事件 story 长于摘要+指针，平均每条注入可省 %.0f tokens；模型主动读全文频次与回复完整度未测'
                                         % (len(long_events), len(story_chars), saved_tokens_per_injection)},
                   'evidence': {'story_chars_distribution': {'min': min(story_chars) if story_chars else 0,
                                                             'avg': round(avg_story, 1),
                                                             'max': max(story_chars) if story_chars else 0},
                                'story_source_mix': {source: sum(1 for event in ctx.snapshot.events.values() if event.story_source == source)
                                                     for source in sorted({event.story_source for event in ctx.snapshot.events.values()})}},
                   'evidence_gaps': ['仅费用/结构侧证据，无真实生成；回复完整度与模型主动读全文频次需真实请求验证，本次未执行'],
                   'budget': budget_snapshot(ctx.budget, tier=2, cost=0.0)})
    ctx.save_detail('ab_c3_long_events', affected)
    return result


def run_a1b(ctx):
    result = core.result_stub('AB-A1b', 2, title='autonomy 闲暇唤醒低打扰联想分支',
                              variable='闲暇唤醒加联想 关 vs 开（A1a 胜出池）', fixed='AB-A1a 长尾池 + 自主活动切片')
    autonomy = [sample for sample in ctx.samples if sample.category == 'autonomy']
    idle = [sample for sample in autonomy if sample.allow_empty]
    task = [sample for sample in autonomy if not sample.allow_empty]
    from .tier0 import _base
    base = _base(ctx)
    surfaced = []
    for index, sample in enumerate(idle):
        pool = stratified_long_tail_pool(ctx.snapshot, size=24, rotation=index)
        choice = association_choice(ctx.snapshot, sample.query, pool, turn=index, already=())
        if choice.get('key'):
            surfaced.append({'sample_id': sample.sample_id, 'memory_id': choice.get('key'),
                             'score': choice.get('score')})
    tokens_per_association = 60  # excerpt + header estimate, measured from recall head/body split
    result.update({'status': 'partial',
                   'arms': [{'arm': 'association_off', 'config': {}, 'metrics': {
                                 'idle_wakes': len(idle), 'task_wakes': len(task),
                                 'associations_proposed': 0, 'tokens': 0}},
                             {'arm': 'association_on', 'config': {'pool': 'A1a stratified 24'}, 'metrics': {
                                 'idle_wakes': len(idle), 'associations_proposed': len(surfaced),
                                 'task_wakes_untouched': len(task),
                                 'tokens_est': len(surfaced) * tokens_per_association}}],
                   'comparison': [{'metric': 'association_count', 'a': 0, 'b': len(surfaced), 'delta': len(surfaced)}],
                   'verdict': {'recommended': len(surfaced) > 0 and len(surfaced) <= len(idle),
                               'reason': '12 条自主唤醒切片里 %d 条闲暇唤醒有可用联想候选；只加在闲暇态，任务态 %d 条不受影响。自然度盲评未执行'
                                         % (len(surfaced), len(task))},
                   'evidence': {'autonomy_samples': len(autonomy), 'idle': len(idle), 'task': len(task),
                                'tokens_per_association_est': tokens_per_association},
                   'evidence_gaps': ['自主独处消息/日记自然度与不跑题率需要真实生成盲评，本次未执行；仅验证检索侧候选与预算'],
                   'budget': budget_snapshot(ctx.budget, tier=2, cost=0.0)})
    ctx.save_detail('ab_a1b_associations', surfaced)
    return result


def run_e1(ctx):
    result = core.result_stub('AB-E1', 2, title='认知巩固收益与跨时间认知修订',
                              variable='无巩固 vs 单次巩固回放', fixed='近 7 天真实隔离切片（events+autonomy+tools）')
    ledger = {}
    try:
        import sqlite3
        connection = sqlite3.connect('file:%s?mode=ro' % ctx.snapshot.db_path.as_posix(), uri=True)
        try:
            ledger = {row[0]: row[1] for row in connection.execute(
                'SELECT kind, COUNT(*) FROM event_ledger GROUP BY kind')}
        finally:
            connection.close()
    except Exception:
        ledger = {}
    learned = ctx.options.get('learned_context') or {}
    relationship = ((learned.get('relationship') or {}).get('items') or [])
    versions_path = ctx.options.get('versions_path')
    confirmed_promotions = 0
    if versions_path and Path(versions_path).is_file():
        for row in core.read_jsonl(versions_path):
            try:
                after = json.loads(row.get('after') or '{}')
            except (TypeError, ValueError):
                continue
            for item in ((after.get('relationship') or {}).get('items') or []):
                if isinstance(item, dict) and item.get('status') == 'confirmed':
                    confirmed_promotions += 1
    result.update({'status': 'blocked',
                   'verdict': {'recommended': None,
                               'reason': '历史数据无法验证巩固收益：证据链/有效认知样本不足，且真实巩固需要多日前瞻生成与人工验收，超出本次预算与隔离条件'},
                   'evidence': {'event_ledger_kinds': ledger,
                                'ledger_dream_report_share': round((ledger.get('event-dream-report', 0) /
                                                                    max(1, sum(ledger.values()))), 4),
                                'quotes': len(ctx.snapshot.quotes),
                                'learned_relationship_items': len(relationship),
                                'confirmed_promotions_history': confirmed_promotions,
                                'autonomy_rows': (len(ctx.options.get('autonomy_rows') or []) if ctx.options.get('autonomy_rows') else None),
                                'ground_truth': '不存在“这次巩固带来的后续回复增益”标签；任何收益结论都只能是 AI 辅助且人类未确认'},
                   'evidence_gaps': ['缺少巩固前后回复质量对照标签', 'learned-context 关系区历史 0 条 confirmed 晋升',
                                     '真实巩固需主模型多日生成 + 盲评，Tier 2 预算与隔离条件不足'],
                   'budget': budget_snapshot(ctx.budget, tier=2, cost=0.0)})
    return result


def run_comb(ctx):
    result = core.result_stub('AB-COMB', 2, title='最终组合验证',
                              variable='Tier 0/1 净收益为正的组合', fixed='Baseline-1')
    base = RetrievalConfig()   # production Baseline-1: K=8, minmax, importance cap 0.05
    winners = {}
    for experiment_id, key in (('AB-R1b', 'stopwords'), ('AB-R1c', 'fusion'),
                               ('AB-R5a', 'importance_weight')):
        row = ctx.results.get(experiment_id) or {}
        verdict = row.get('verdict') or {}
        if verdict.get('recommended') and verdict.get(key) is not None:
            winners[key] = verdict[key]
    if (ctx.results.get('AB-R5b') or {}).get('verdict', {}).get('recommended'):
        winners['last_active_boost'] = 0.02
    if (ctx.results.get('AB-R5c') or {}).get('verdict', {}).get('recommended'):
        winners['affect_boost'] = 0.02
    if (ctx.results.get('AB-R4b') or {}).get('verdict', {}).get('recommended'):
        winners['related_expand'] = True
    if (ctx.results.get('AB-R4c') or {}).get('verdict', {}).get('recommended'):
        winners['alias_expand'] = True
    if (ctx.results.get('AB-R4a') or {}).get('verdict', {}).get('recommended'):
        winners['story_index'] = True
    if winners.get('story_index'):
        try:
            ctx.ensure_story_vectors()
        except core.EmbeddingError:
            winners['story_index'] = False
    combined = RetrievalConfig(stopwords=bool(winners.get('stopwords', False)),
                               fusion=winners.get('fusion', base.fusion),
                               k=int(winners.get('k', base.k)),
                               importance_weight=float(winners.get('importance_weight', base.importance_weight)),
                               last_active_boost=float(winners.get('last_active_boost', 0.0)),
                               affect_boost=float(winners.get('affect_boost', 0.0)),
                               related_expand=bool(winners.get('related_expand')),
                               alias_expand=bool(winners.get('alias_expand')),
                               story_index=bool(winners.get('story_index')))
    metrics, _ = ctx.evaluate(combined)
    # Compare against the recomputed production baseline, not against the K=8 arm of a different
    # experiment: the combined config only contains mechanisms that passed the adoption gate.
    baseline_metrics, _ = ctx.evaluate(base)
    single_deltas = []
    for experiment_id in ('AB-R1b', 'AB-R1c', 'AB-R4b', 'AB-R5a', 'AB-R5b', 'AB-R4a'):
        row = ctx.results.get(experiment_id) or {}
        arms = row.get('arms') or []
        if len(arms) >= 2:
            first = arms[0]['metrics'].get('overall', {}).get('recall_at_k')
            second = arms[1]['metrics'].get('overall', {}).get('recall_at_k')
            if first is not None and second is not None:
                single_deltas.append(second - first)
    combined_delta = (metrics['overall']['recall_at_k'] or 0) - (baseline_metrics.get('overall', {}).get('recall_at_k') or 0)
    expected_delta = sum(single_deltas)
    # One paid judge arm over the same 36-sample subset, using the combined retrieval pool.
    judge_metrics = None
    judge_cost = 0.0
    try:
        samples = _selected_samples(ctx)
        pools = _candidate_pool(ctx, samples, combined)
        prompt = _best_prompt(ctx)
        card = ((ctx.results.get('AB-R3d') or {}).get('verdict', {}) or {}).get('card', '300')
        client = _judge_client(ctx)
        try:
            arm, rows = _run_judge_arm(ctx, client, samples, pools, experiment='AB-COMB',
                                       name='combined+judge', card=card, prompt=prompt,
                                       timeout=_best_timeout(ctx), tier=2)
            judge_metrics = arm['metrics']
            judge_cost = judge_metrics.get('cost_cny', 0.0)
            ctx.save_detail('ab_comb_judge_rows', rows)
        finally:
            client.close()
    except core.BudgetBlocked as exc:
        result['notes'].append('budget guard stopped the paid judge arm: %s' % exc)
    result.update({'status': 'completed' if judge_metrics else 'partial',
                   'arms': [{'arm': 'combined_offline',
                             'config': {key: getattr(combined, key) for key in combined.__dataclass_fields__},
                             'metrics': metrics},
                            {'arm': 'combined+judge',
                             'config': {'judge': _best_prompt(ctx), 'card': card},
                             'metrics': judge_metrics or {}}],
                   'comparison': [{'metric': 'recall_at_8',
                                   'baseline': baseline_metrics.get('overall', {}).get('recall_at_k'),
                                   'combined': metrics['overall']['recall_at_k'],
                                   'single_variable_sum_delta': round(expected_delta, 4),
                                   'combined_delta': round(combined_delta, 4),
                                   'negative_interaction': combined_delta < expected_delta - 0.01},
                                  {'metric': 'false_positive_rate',
                                   'combined': metrics['overall']['false_positive_rate']}],
                   'verdict': {'recommended': combined_delta >= 0 and not (combined_delta < expected_delta - 0.01),
                               'winners_used': winners,
                               'metrics': {'recall_at_k': metrics['overall']['recall_at_k'],
                                           'fpr': metrics['overall']['false_positive_rate'],
                                           'ndcg_at_8': metrics['overall']['ndcg_at_8']},
                               'reason': '组合离线 Recall@K %+.2fpp（单变量加总 %+.2fpp），负交互=%s；判官臂费用 ¥%.4f'
                                         % (combined_delta * 100, expected_delta * 100,
                                            'yes' if combined_delta < expected_delta - 0.01 else 'no', judge_cost)},
                   'evidence': {'baseline_recall_at_k': baseline_metrics.get('overall', {}).get('recall_at_k'),
                                'single_deltas': [round(delta, 4) for delta in single_deltas],
                                'judge_subset': JUDGE_SUBSET},
                   'evidence_gaps': ([] if judge_metrics else ['判官组合臂因预算守卫未执行']),
                   'budget': budget_snapshot(ctx.budget, tier=2, calls=0,
                                             tokens=(judge_metrics or {}).get('tokens', {}).get('input', 0) if judge_metrics else 0,
                                             cost=judge_cost)})
    return result
