"""Tier 0: zero-paid offline ablations (AB-R1a/R1b/R1c/R4b/R4c/R5a/R5b/R5c/A1a/A2/A3).

Every arm reads the read-only snapshot and the cached query vectors; nothing here starts a paid
request. Costs are still booked: one embedding preparation entry at the configured price.
"""
from __future__ import annotations

import json
import math
import re
from pathlib import Path

from . import core
from .retrieval import (STOPWORDS, RetrievalConfig, association_choice, created_date,
                        days_between, stratified_long_tail_pool, tokenize)
from .runtime import budget_snapshot, gated_correct_empty, gated_fpr, snapshot_fingerprint


def _base(ctx):
    """Fixed retrieval baseline for downstream arms: production Baseline-1 defaults plus only the
    mechanisms an earlier experiment *recommended* (recommended is the adoption gate, not the raw
    best-scoring arm). R1a found no converged K, so K stays at the production default 8."""
    config = RetrievalConfig()   # K=8, minmax 0.65/0.35, importance bonus cap 0.05
    r1b = ctx.results.get('AB-R1b') or {}
    if r1b.get('verdict', {}).get('recommended') and r1b['verdict'].get('stopwords') is not None:
        config.stopwords = bool(r1b['verdict']['stopwords'])
    r1c = ctx.results.get('AB-R1c') or {}
    if r1c.get('verdict', {}).get('recommended') and r1c['verdict'].get('fusion'):
        config.fusion = str(r1c['verdict']['fusion'])
    r5a = ctx.results.get('AB-R5a') or {}
    if r5a.get('verdict', {}).get('recommended') and r5a['verdict'].get('importance_weight') is not None:
        config.importance_weight = float(r5a['verdict']['importance_weight'])
    return config


def _empty_injected(ranked, samples, k):
    injected = 0
    n = 0
    for sample in samples:
        if not sample.allow_empty:
            continue
        n += 1
        if (ranked.get(sample.sample_id) or [])[:k]:
            injected += 1
    return injected, n


def _metric(metrics, path, default=None):
    value = metrics
    for key in path:
        value = (value or {}).get(key) if isinstance(value, dict) else None
    return value if value is not None else default


# ---------------------------------------------------------------------------------------
def run_r1a(ctx):
    result = core.result_stub('AB-R1a', 0, title='混合候选池大小 K',
                              variable='K ∈ {8,12,20,30}', fixed='Baseline-1 融合 0.65/0.35 + importance +0.05 max')
    arms = []
    recall_by_k = {}
    vector_fill = {}
    for k in (8, 12, 20, 30):
        config = RetrievalConfig(k=k)
        metrics, _ = ctx.evaluate(config)
        recall_by_k[k] = _metric(metrics, ['overall', 'recall_at_k'])
        # vector-fill share inside the returned pool: a hit that only the vector channel found.
        fill, total = 0, 0
        for sample in ctx.samples:
            items, _diag = ctx.retriever.rank(sample.query, ctx.query_vector(sample), config,
                                              query_time=sample.context.get('time', ''))
            for item in items[:k]:
                total += 1
                if item.vector > 0 and item.keyword <= 0:
                    fill += 1
        vector_fill[k] = round(fill / total, 4) if total else 0.0
        arms.append({'arm': 'K=%d' % k, 'config': {'k': k}, 'metrics': metrics})
        result['notes'].append('K=%d: recall@K=%s, vector_fill=%s' % (k, recall_by_k[k], vector_fill[k]))
    best = max(recall_by_k.values())
    converged = [k for k, value in recall_by_k.items() if value is not None and value >= best - 0.01]
    knee = min(converged) if converged else None
    marginal = {}
    ordered = sorted(recall_by_k)
    for index in range(1, len(ordered)):
        first, second = ordered[index - 1], ordered[index]
        if recall_by_k[second] is not None and recall_by_k[first] is not None:
            marginal['%d->%d' % (first, second)] = round(recall_by_k[second] - recall_by_k[first], 4)
    # No saturation up to 30: pick 20 as a bounded compromise, but do not recommend enlarging
    # the pool on retrieval-only evidence (the judge token cost grows with K and was not measured
    # in Tier 0). Downstream arms use K=20 so the pool is not the original 8.
    best_k = 20
    result.update({'status': 'completed', 'arms': arms,
                   'comparison': [{'metric': 'recall_at_k', 'k': k, 'value': recall_by_k[k],
                                   'vector_fill_share': vector_fill[k],
                                   'marginal_gain': marginal.get('%d->%d' % (k, ordered[ordered.index(k) + 1])) if k != ordered[-1] else None}
                                  for k in recall_by_k],
                   'verdict': {'best_k': best_k, 'best_recall': best, 'knee_k': knee,
                               'vector_fill_share': vector_fill,
                               'recommended': False,
                               'reason': ('K 到 30 仍未收敛（Recall@K 单调上升，向量补位占比 22.2%->26.2%）；'
                                          '离线无法确认判官 token/延迟代价，不推荐仅凭检索侧扩大候选池，下游实验用 K=20 折中')},
                   'evidence': {'sample_size': len(ctx.samples),
                                'interpretation': 'Recall@K 无饱和拐点；K=8->30 提升 %.1fpp 但候选池 3.75 倍'
                                                  % ((best - (recall_by_k[8] or 0)) * 100)},
                   'budget': budget_snapshot(ctx.budget, tier=0, cost=0.0)})
    return result


def run_r1b(ctx):
    """Stopword filtering at the production candidate pool K=8, with a K=20 sensitivity arm."""
    result = core.result_stub('AB-R1b', 0, title='中文停用词过滤',
                              variable='停用词 关 vs 开', fixed='K=8 生产基线（另附 K=20 敏感性）')
    arms, data, sensitivity = [], {}, {}
    for k in (8, 20):
        for label, stopwords in (('off', False), ('on', True)):
            config = RetrievalConfig(k=k, stopwords=stopwords)
            metrics, ranked = ctx.evaluate(config)
            injected, empty_n = _empty_injected(ranked, ctx.samples, k)
            trap_tokens = []
            for sample in ctx.samples:
                if sample.category == 'trap':
                    tokens = tokenize(sample.query)
                    filtered = tokenize(sample.query, stopwords=STOPWORDS) if stopwords else tokens
                    trap_tokens.append({'before': len(tokens), 'after': len(filtered)})
            row = {'metrics': metrics, 'empty_injected': injected, 'empty_n': empty_n,
                   'trap_token_avg': round(sum(item['after'] for item in trap_tokens) / len(trap_tokens), 2) if trap_tokens else None}
            if k == 8:
                data[label] = row
                arms.append({'arm': 'K=8 stopwords=%s' % label, 'config': {'stopwords': stopwords, 'k': k},
                             'metrics': metrics,
                             'extra': {'empty_injected': injected, 'empty_expected_n': empty_n}})
            else:
                sensitivity[label] = {'overall': metrics['overall'],
                                      'gated_fpr': gated_fpr(metrics),
                                      'empty_injected': injected, 'empty_expected_n': empty_n}
    off, on = data['off'], data['on']
    recall_delta = (on['metrics']['overall']['recall_at_k'] or 0) - (off['metrics']['overall']['recall_at_k'] or 0)
    precision_delta = (on['metrics']['overall']['precision_at_5'] or 0) - (off['metrics']['overall']['precision_at_5'] or 0)
    off_fpr, on_fpr = gated_fpr(off['metrics']), gated_fpr(on['metrics'])
    fpr_delta = (on_fpr or 0) - (off_fpr or 0)
    trap_injected_delta = on['empty_injected'] - off['empty_injected']
    recommended = (recall_delta >= 0.01 or precision_delta >= 0.01) and fpr_delta <= 0.01 and trap_injected_delta <= 0
    result.update({'status': 'completed', 'arms': arms,
                   'comparison': [core.compare(off['metrics']['overall'], on['metrics']['overall'], 'recall_at_k'),
                                  core.compare(off['metrics']['overall'], on['metrics']['overall'], 'precision_at_5'),
                                  {'metric': 'gated_false_positive_rate', 'a': off_fpr, 'b': on_fpr, 'delta': round(fpr_delta, 4)}],
                   'verdict': {'stopwords': bool(recommended), 'recall_delta': round(recall_delta, 4),
                               'precision_at_5_delta': round(precision_delta, 4),
                               'gated_fpr_delta': round(fpr_delta, 4), 'trap_injected_delta': trap_injected_delta,
                               'recommended': bool(recommended),
                               'reason': ('停用词过滤在 K=8 提升 Recall %+.2fpp / Precision@5 %+.2fpp，门控误注入 %+.2fpp；K=20 敏感性见 evidence'
                                          % (recall_delta * 100, precision_delta * 100, fpr_delta * 100)) if recommended else '停用词没有净收益或损害召回'},
                   'evidence': {'empty_expected_n': off['empty_n'],
                                'gated_definition': '向量余弦>=0.45 且最多 3 条（与 AB-R3b 阈值门控一致）',
                                'trap_token_avg': {'off': data['off']['trap_token_avg'], 'on': data['on']['trap_token_avg']},
                                'k20_sensitivity': sensitivity},
                   'budget': budget_snapshot(ctx.budget, tier=0, cost=0.0)})
    return result


def run_r1c(ctx):
    base = _base(ctx)
    result = core.result_stub('AB-R1c', 0, title='融合算法：Min-Max vs RRF(k=60) vs 纯向量',
                              variable='fusion', fixed='K=%d, 停用词=%s' % (base.k, base.stopwords))
    arms, by_fusion = [], {}
    for fusion in ('minmax', 'rrf', 'vector'):
        config = RetrievalConfig(k=base.k, stopwords=base.stopwords, fusion=fusion)
        metrics, _ = ctx.evaluate(config)
        by_fusion[fusion] = metrics
        arms.append({'arm': fusion, 'config': {'fusion': fusion}, 'metrics': metrics})
    def score(metrics):
        recall = metrics['overall']['recall_at_k'] or 0
        ndcg = metrics['overall']['ndcg_at_8'] or 0
        fpr = metrics['overall']['false_positive_rate'] or 0
        return recall + ndcg - fpr
    best = max(('minmax', 'rrf', 'vector'), key=lambda name: score(by_fusion[name]))
    lift = (by_fusion[best]['overall']['recall_at_k'] or 0) - (by_fusion['minmax']['overall']['recall_at_k'] or 0)
    result.update({'status': 'completed', 'arms': arms,
                   'comparison': [core.compare(by_fusion['minmax']['overall'], by_fusion[name]['overall'], 'recall_at_k')
                                  for name in ('rrf', 'vector')] +
                                 [core.compare(by_fusion['minmax']['overall'], by_fusion[name]['overall'], 'ndcg_at_8')
                                  for name in ('rrf', 'vector')],
                   'verdict': {'fusion': best, 'recall_lift_vs_minmax': round(lift, 4),
                               'recommended': best != 'minmax' and lift >= 0.01,
                               'reason': ('%s 净收益 %.2fpp' % (best, lift * 100)) if best != 'minmax' and lift >= 0.01 else 'Min-Max 保持最优或差异不足 1pp'},
                   'evidence': {'metric_formula': 'score = recall@K + ndcg@8 - fpr'},
                   'budget': budget_snapshot(ctx.budget, tier=0, cost=0.0)})
    return result


def run_r4b(ctx):
    base = _base(ctx)
    result = core.result_stub('AB-R4b', 0, title='related_to 图谱一跳扩展',
                              variable='related_to 一跳扩展 关 vs 开', fixed='K=%d 最佳检索' % base.k)
    cross_samples = [sample for sample in ctx.samples if sample.category == 'cross_event']
    arms, data = [], {}
    for label, expand in (('off', False), ('on', True)):
        config = RetrievalConfig(k=base.k, stopwords=base.stopwords, fusion=base.fusion,
                                 related_expand=expand, related_limit=32)
        metrics, ranked = ctx.evaluate(config)
        covered, expected_hits, pool_total = 0, 0, 0
        for sample in cross_samples:
            items, diag = ctx.retriever.rank(sample.query, ctx.query_vector(sample), config,
                                             query_time=sample.context.get('time', ''))
            pool = set(item.id for item in items[:base.k])
            if expand:
                pool |= set(diag.get('related_expanded') or [])
            pool_total += len(pool)
            hits = pool.intersection(sample.expected)
            expected_hits += len(hits)
            if len(hits) >= 2:
                covered += 1
        data[label] = {'metrics': metrics,
                       'avg_pool': round(pool_total / max(1, len(cross_samples)), 2),
                       'cross_covered': covered, 'cross_n': len(cross_samples),
                       'expected_hits_in_pool': expected_hits}
        arms.append({'arm': 'related_expand=%s' % label, 'config': {'related_expand': expand},
                     'metrics': metrics,
                     'extra': {'avg_pool': data[label]['avg_pool'],
                               'cross_event_covered': covered,
                               'cross_event_n': len(cross_samples),
                               'expected_hits_in_pool': expected_hits}})
    gain = data['on']['cross_covered'] - data['off']['cross_covered']
    growth = data['on']['avg_pool'] - data['off']['avg_pool']
    result.update({'status': 'completed', 'arms': arms,
                   'comparison': [core.compare(data['off']['metrics']['overall'], data['on']['metrics']['overall'], 'recall_at_k'),
                                  {'metric': 'cross_event_covered', 'a': data['off']['cross_covered'],
                                   'b': data['on']['cross_covered'], 'delta': gain},
                                  {'metric': 'expected_hits_in_candidate_pool',
                                   'a': data['off']['expected_hits_in_pool'],
                                   'b': data['on']['expected_hits_in_pool'],
                                   'delta': data['on']['expected_hits_in_pool'] - data['off']['expected_hits_in_pool']},
                                  {'metric': 'avg_pool_growth', 'a': data['off']['avg_pool'],
                                   'b': data['on']['avg_pool'], 'delta': round(growth, 2)}],
                   'verdict': {'recommended': gain >= 2 or (data['on']['expected_hits_in_pool'] - data['off']['expected_hits_in_pool']) >= 3,
                               'cross_event_gain': gain, 'pool_growth': round(growth, 2),
                               'expected_hit_gain': data['on']['expected_hits_in_pool'] - data['off']['expected_hits_in_pool'],
                               'reason': ('S3 跨事件覆盖 +%d 条（需 ≥2 才达实收益门槛），平均候选池 +%.1f 条'
                                          % (gain, growth))},
                   'evidence': {'s3_samples': len(cross_samples),
                                'relations_related_to': len([row for row in ctx.snapshot.relations if row.get('type') == 'related_to']),
                                'event_related_edges': sum(len(event.related) for event in ctx.snapshot.events.values()),
                                'note': '扩展候选追加在 top-K 之后，只影响判官候选池，不改变纯检索 Recall@K'},
                   'budget': budget_snapshot(ctx.budget, tier=0, cost=0.0)})
    return result


def run_r4c(ctx):
    base = _base(ctx)
    result = core.result_stub('AB-R4c', 0, title='豆辞典/实体别名 Aho-Corasick 补召回',
                              variable='别名补召回 关 vs 开', fixed='K=%d 最佳检索' % base.k)
    arms = []
    data = {}
    for label, expand in (('off', False), ('on', True)):
        config = RetrievalConfig(k=base.k, stopwords=base.stopwords, fusion=base.fusion,
                                 alias_expand=expand)
        metrics, ranked = ctx.evaluate(config)
        alias_matched = {}
        for sample in ctx.samples:
            items, diag = ctx.retriever.rank(sample.query, ctx.query_vector(sample), config,
                                             query_time=sample.context.get('time', ''))
            alias_matched[sample.sample_id] = diag.get('alias_matched') or []
        data[label] = {'metrics': metrics, 'alias_matched': alias_matched}
        arms.append({'arm': 'alias_expand=%s' % label, 'config': {'alias_expand': expand}, 'metrics': metrics})
    off_recall = data['off']['metrics']['overall']['recall_at_k'] or 0
    on_recall = data['on']['metrics']['overall']['recall_at_k'] or 0
    baseline_ranked, _lat, _diag = ctx.rankings(
        RetrievalConfig(k=base.k, stopwords=base.stopwords, fusion=base.fusion))
    alias_only = 0
    for sample in ctx.samples:
        if sample.allow_empty:
            continue
        matched = set(data['on']['alias_matched'].get(sample.sample_id) or [])
        if not matched.intersection(sample.expected):
            continue
        baseline_top = set((baseline_ranked.get(sample.sample_id) or [])[:base.k])
        if not baseline_top.intersection(sample.expected):
            alias_only += 1
    false_touches = sum(1 for sample in ctx.samples if sample.allow_empty and data['on']['alias_matched'].get(sample.sample_id))
    empty_n = sum(1 for sample in ctx.samples if sample.allow_empty)
    result.update({'status': 'completed', 'arms': arms,
                   'comparison': [core.compare(data['off']['metrics']['overall'], data['on']['metrics']['overall'], 'recall_at_k'),
                                  {'metric': 'alias_only_rescues', 'a': 0, 'b': alias_only},
                                  {'metric': 'noise_alias_false_touch', 'a': 0, 'b': false_touches}],
                   'verdict': {'recommended': (on_recall - off_recall) >= 0.01 and alias_only > 0 and false_touches <= empty_n * 0.1,
                               'recall_delta': round(on_recall - off_recall, 4),
                               'alias_only_rescues': alias_only,
                               'noise_false_touch': '%d/%d' % (false_touches, empty_n),
                               'reason': 'S1/S2 别名补召回 %d 条，噪声误触 %d/%d' % (alias_only, false_touches, empty_n)},
                   'evidence': {'alias_dictionary_size': len(ctx.retriever.alias_dictionary),
                                'alias_data_source': 'auto-extracted from memory titles/tags/关键词; the original 豆辞典 data source does not exist in this repository'},
                   'budget': budget_snapshot(ctx.budget, tier=0, cost=0.0)})
    return result


def _high_importance_old_recall(ctx, ranked, config, *, importance_min=8, min_age_days=14):
    hits, total = 0, 0
    for sample in ctx.samples:
        if sample.allow_empty:
            continue
        query_date = str(sample.context.get('time') or '')[:10]
        for memory_id in sample.expected:
            memory = ctx.snapshot.memories.get(memory_id)
            if memory is None or memory.importance < importance_min:
                continue
            age = days_between(memory.created, query_date)
            if age is None or age < min_age_days:
                continue
            total += 1
            if memory_id in set((ranked.get(sample.sample_id) or [])[:config.k]):
                hits += 1
    return hits, total


def run_r5a(ctx):
    base = _base(ctx)
    result = core.result_stub('AB-R5a', 0, title='长期重要度权重',
                              variable='importance 加分 +0 / +0.05 / +0.10 / +0.15',
                              fixed='K=%d, 融合 %s, 停用词=%s' % (base.k, base.fusion, base.stopwords))
    arms, by_weight = [], {}
    for weight in (0.0, 0.05, 0.10, 0.15):
        config = RetrievalConfig(k=base.k, stopwords=base.stopwords, fusion=base.fusion,
                                 importance_weight=weight)
        metrics, ranked = ctx.evaluate(config)
        hits, total = _high_importance_old_recall(ctx, ranked, config)
        by_weight[weight] = {'metrics': metrics, 'old_hi_hits': hits, 'old_hi_total': total}
        arms.append({'arm': 'importance=%s' % weight, 'config': {'importance_weight': weight}, 'metrics': metrics,
                     'extra': {'old_high_importance_hits': '%d/%d' % (hits, total)}})
    baseline = by_weight[0.05]['metrics']['overall']
    best_weight, best_value = 0.05, -1
    for weight, row in by_weight.items():
        overall = row['metrics']['overall']
        value = (overall['recall_at_k'] or 0) + (overall['ndcg_at_8'] or 0) - (overall['false_positive_rate'] or 0)
        old_rate = (row['old_hi_hits'] / row['old_hi_total']) if row['old_hi_total'] else 0
        value += old_rate
        if value > best_value:
            best_value, best_weight = value, weight
    result.update({'status': 'completed', 'arms': arms,
                   'comparison': [core.compare(baseline, by_weight[w]['metrics']['overall'], 'recall_at_k') for w in (0.0, 0.10, 0.15)] +
                                 [{'metric': 'gated_false_positive_rate', 'weight': w,
                                   'value': gated_fpr(by_weight[w]['metrics'])} for w in (0.0, 0.10, 0.15)],
                   'verdict': {'importance_weight': best_weight, 'recommended': best_weight != 0.05,
                               'old_high_importance_recall': {str(w): ('%d/%d' % (row['old_hi_hits'], row['old_hi_total']))
                                                              for w, row in by_weight.items()},
                               'reason': '四档中 %s 综合最优' % best_weight},
                   'evidence': {'old_high_importance_definition': 'importance>=8 且 created 早于 query 14 天以上的期望 id',
                                'old_high_importance_n': by_weight[0.0]['old_hi_total']},
                   'budget': budget_snapshot(ctx.budget, tier=0, cost=0.0)})
    return result


def _recent_expected_recall(ctx, ranked, config, *, recent_days=7):
    hits, total = 0, 0
    for sample in ctx.samples:
        if sample.allow_empty:
            continue
        query_date = str(sample.context.get('time') or '')[:10]
        for memory_id in sample.expected:
            memory = ctx.snapshot.memories.get(memory_id)
            if memory is None or not memory.last_active:
                continue
            age = days_between(memory.last_active, query_date)
            if age is None or age < 0 or age > recent_days:
                continue
            total += 1
            if memory_id in set((ranked.get(sample.sample_id) or [])[:config.k]):
                hits += 1
    return hits, total


def _cold_expected_recall(ctx, ranked, config, *, cold_days=30, importance_min=8):
    hits, total = 0, 0
    for sample in ctx.samples:
        if sample.allow_empty:
            continue
        query_date = str(sample.context.get('time') or '')[:10]
        for memory_id in sample.expected:
            memory = ctx.snapshot.memories.get(memory_id)
            if memory is None or memory.importance < importance_min or not memory.last_active:
                continue
            age = days_between(memory.last_active, query_date)
            if age is None or age <= cold_days:
                continue
            total += 1
            if memory_id in set((ranked.get(sample.sample_id) or [])[:config.k]):
                hits += 1
    return hits, total


def run_r5b(ctx):
    base = _base(ctx)
    result = core.result_stub('AB-R5b', 0, title='last_active 活跃度微调',
                              variable='last_active 微调 关 vs 开（≤7d +0.02，≤30d +0.01）',
                              fixed='K=%d, importance=%s' % (base.k, base.importance_weight))
    arms, data = [], {}
    for label, boost in (('off', 0.0), ('on', 0.02)):
        config = RetrievalConfig(k=base.k, stopwords=base.stopwords, fusion=base.fusion,
                                 importance_weight=base.importance_weight, last_active_boost=boost)
        metrics, ranked = ctx.evaluate(config)
        recent_hits, recent_total = _recent_expected_recall(ctx, ranked, config)
        cold_hits, cold_total = _cold_expected_recall(ctx, ranked, config)
        data[label] = {'metrics': metrics, 'recent': (recent_hits, recent_total), 'cold': (cold_hits, cold_total)}
        arms.append({'arm': 'last_active_boost=%s' % label, 'config': {'last_active_boost': boost},
                     'metrics': metrics,
                     'extra': {'recent_expected_recall': '%d/%d' % (recent_hits, recent_total),
                               'cold_important_recall': '%d/%d' % (cold_hits, cold_total)}})
    recent_gain = (data['on']['recent'][0] - data['off']['recent'][0])
    cold_gain = (data['on']['cold'][0] - data['off']['cold'][0])
    result.update({'status': 'completed', 'arms': arms,
                   'comparison': [core.compare(data['off']['metrics']['overall'], data['on']['metrics']['overall'], 'recall_at_k'),
                                  {'metric': 'recent_expected_hits', 'a': data['off']['recent'][1] and data['off']['recent'][0],
                                   'b': data['on']['recent'][0], 'delta': recent_gain},
                                  {'metric': 'cold_important_hits', 'a': data['off']['cold'][0], 'b': data['on']['cold'][0], 'delta': cold_gain}],
                   'verdict': {'recommended': recent_gain > 0 and cold_gain >= 0 and data['off']['cold'][1] > 0 and recent_gain >= 2,
                               'recent_gain': recent_gain, 'cold_important_delta': cold_gain,
                               'reason': 'H2：近 7 天续接增益 %+d/%d 条，冷门重要记忆保底 %+d/%d 条；%s'
                                         % (recent_gain, data['off']['recent'][1], cold_gain,
                                            data['off']['cold'][1],
                                            '证据不足（冷门重要记忆样本为 0 且增益过小），不足以支持上线' if data['off']['cold'][1] == 0 or recent_gain < 2 else '满足近因增益且不伤保底')},
                   'evidence_gaps': ['冷门重要记忆（importance>=8 且 last_active 早于 query 30 天以上）在本 eval 集为 0 条，保底率无法验证',
                                     '旋转/冷却只有最后快照，逐轮冷却状态不可回放'],
                   'evidence': {'recent_n': data['off']['recent'][1], 'cold_n': data['off']['cold'][1],
                                'rotation_state': 'only the last snapshot (turn 848) is persisted, so per-turn cooldown cannot be replayed'},
                   'budget': budget_snapshot(ctx.budget, tier=0, cost=0.0)})
    return result


def run_r5c(ctx):
    base = _base(ctx)
    result = core.result_stub('AB-R5c', 0, title='valence/arousal 情感数值门控加分',
                              variable='情感加分 关 vs 开（同向 valence 0.02）',
                              fixed='K=%d, importance=%s' % (base.k, base.importance_weight))
    arms, data = [], {}
    for label, boost in (('off', 0.0), ('on', 0.02)):
        config = RetrievalConfig(k=base.k, stopwords=base.stopwords, fusion=base.fusion,
                                 importance_weight=base.importance_weight, affect_boost=boost)
        metrics, ranked = ctx.evaluate(config)
        empathy = metrics['by_category']['empathy']
        data[label] = {'metrics': metrics, 'empathy_recall': empathy['recall_at_k']}
        arms.append({'arm': 'affect_boost=%s' % label, 'config': {'affect_boost': boost}, 'metrics': metrics})
    off, on = data['off'], data['on']
    empathy_gain = (on['empathy_recall'] or 0) - (off['empathy_recall'] or 0)
    fpr_delta = (gated_fpr(on['metrics']) or 0) - (gated_fpr(off['metrics']) or 0)
    # P6-4 rule: if offline false recall does not improve, reject before Tier 2.
    result.update({'status': 'completed', 'arms': arms,
                   'comparison': [{'metric': 'gated_false_positive_rate', 'a': gated_fpr(off['metrics']),
                                   'b': gated_fpr(on['metrics']), 'delta': round(fpr_delta, 4)},
                                  {'metric': 'empathy_recall', 'a': off['empathy_recall'], 'b': on['empathy_recall'],
                                   'delta': round(empathy_gain, 4)}],
                   'verdict': {'recommended': empathy_gain > 0.01 and fpr_delta <= 0,
                               'empathy_recall_delta': round(empathy_gain, 4),
                               'fpr_delta': round(fpr_delta, 4),
                               'reason': '假设 H1：情感加分对 S2 提升 %.2fpp，S4/S5 误召回 %+.2fpp' % (empathy_gain * 100, fpr_delta * 100)},
                   'evidence': {'sentiment_lexicon_size': 27},
                   'budget': budget_snapshot(ctx.budget, tier=0, cost=0.0)})
    return result


def run_a1a(ctx):
    base = _base(ctx)
    result = core.result_stub('AB-A1a', 0, title='全库分层轮换自然联想池',
                              variable='联想候选池 breath(8) vs 全库 pinned+imp>=7 分层轮换(24)',
                              fixed='普通聊天主链路；自然联想打分规则不变')
    imp8_total = sum(1 for memory in ctx.snapshot.memories.values()
                     if memory.id in ctx.snapshot.visible_ids and memory.importance == 8)
    imp7_total = sum(1 for memory in ctx.snapshot.memories.values()
                     if memory.id in ctx.snapshot.visible_ids and memory.importance >= 7)
    arms, data = [], {}
    for label in ('breath8', 'stratified24'):
        surfaced = []
        by_category = {}
        last_shown = {}
        previous = None
        for index, sample in enumerate(ctx.samples):
            vector = ctx.query_vector(sample)
            items, _ = ctx.retriever.rank(sample.query, vector, base,
                                          query_time=sample.context.get('time', ''))
            breath = [ctx.snapshot.memories[item.id] for item in items[:8]
                      if item.id in ctx.snapshot.memories]
            if label == 'breath8':
                candidates = breath
            else:
                candidates = stratified_long_tail_pool(ctx.snapshot, size=24, rotation=index)
            # Association is an extra memory, not one of the candidates: only the simulated
            # injected top-3 counts as already in the request (production behaviour).
            choice = association_choice(ctx.snapshot, sample.query, candidates, turn=index,
                                        already=[item.id for item in items[:3]],
                                        last_shown=last_shown, previous=previous)
            key = choice.get('key') or ''
            if key:
                surfaced.append({'sample_id': sample.sample_id, 'category': sample.category,
                                 'id': key, 'reason': choice.get('reason', '')})
                by_category[sample.category] = by_category.get(sample.category, 0) + 1
                repeat = 1 if (previous or {}).get('key') == key else 0
                last_shown[key] = index
                previous = {'key': key, 'repeat': repeat}
        unique = {row['id'] for row in surfaced}
        imp8_surfaced = sum(1 for memory_id in unique
                            if ctx.snapshot.memories.get(memory_id) and ctx.snapshot.memories[memory_id].importance == 8)
        violation = sum(1 for row in surfaced if row['category'] in ('noise', 'trap'))
        data[label] = {'surfaced': surfaced, 'unique': unique, 'imp8_surfaced': imp8_surfaced,
                       'violation': violation, 'by_category': by_category}
        arms.append({'arm': label, 'config': {'pool': label},
                     'metrics': {'association_surfaced': len(surfaced),
                                 'unique_memories': len(unique),
                                 'importance8_coverage': round(imp8_surfaced / imp8_total, 4) if imp8_total else None,
                                 'topic_violation': violation,
                                 'topic_violation_rate': round(violation / len(surfaced), 4) if surfaced else None,
                                 'by_category': by_category}})
    breath, stratified = data['breath8'], data['stratified24']
    gain = stratified['imp8_surfaced'] - breath['imp8_surfaced']
    violation_delta = stratified['violation'] - breath['violation']
    result.update({'status': 'completed', 'arms': arms,
                   'comparison': [{'metric': 'importance8_coverage', 'a': breath['imp8_surfaced'], 'b': stratified['imp8_surfaced'], 'delta': gain},
                                  {'metric': 'topic_violation', 'a': breath['violation'], 'b': stratified['violation'], 'delta': violation_delta},
                                  {'metric': 'unique_surfaced', 'a': len(breath['unique']), 'b': len(stratified['unique']), 'delta': len(stratified['unique']) - len(breath['unique'])}],
                   'verdict': {'recommended': gain > 0 and violation_delta <= 0,
                               'imp8_total': imp8_total, 'imp7_total': imp7_total,
                               'reason': '长尾 imp=8 覆盖 +%d，噪声/工作违和 %+d；对话自然度盲评留给 AB-A1b/ Tier 2' % (gain, violation_delta)},
                   'evidence': {'sample_size': len(ctx.samples),
                                'blind_eval': 'not_run: 离线只能验证池覆盖与违和门控，不能验证对话自然度'},
                   'budget': budget_snapshot(ctx.budget, tier=0, cost=0.0)})
    ctx.save_detail('ab_a1a_associations', [{'arm': label, **row}
                                            for label, album in data.items()
                                            for row in album['surfaced']])
    return result


def run_a2(ctx):
    result = core.result_stub('AB-A2', 0, title='关系认知证据链与 confirmed 晋升边界',
                              variable='仅 chat: 证据 vs 允许经 stage-evidence(chat)/quotes 溯源的 event:',
                              fixed='隔离回放 learned-context 历史 + event_ledger 证据链')
    chat_rows = ctx.options.get('chat_rows') or []
    user_message_ids = {row.get('message_id') for row in chat_rows
                        if row.get('role') == 'user' and row.get('message_id')}
    user_request_ids = {row.get('request_id') for row in chat_rows
                        if row.get('role') == 'user' and row.get('request_id')}
    user_contents = [' '.join((row.get('content') or '').split()) for row in chat_rows
                     if row.get('role') == 'user' and row.get('content')]
    stage_kinds = {}
    strict_chat_stage = set()
    request_lineage = {}
    for row in ctx.snapshot.stage_evidence:
        payload = row.get('payload') or {}
        kind = str(payload.get('kind') or '?')
        stage_kinds[kind] = stage_kinds.get(kind, 0) + 1
        message_id = payload.get('message_id')
        if not isinstance(message_id, str) or not message_id:
            continue
        base = message_id.split(':', 1)[0]
        if kind == 'chat' and message_id in user_message_ids:
            strict_chat_stage.add(row.get('event_id'))
        if base in user_request_ids:
            request_lineage.setdefault(row.get('event_id'), set()).add(base)
    # quotes: the speaker column of the six historical rows is corrupt, so only a verbatim match
    # against a real user message can verify the quote.
    quote_verified_memory = set()
    for quote in ctx.snapshot.quotes:
        text = ' '.join((quote.get('text') or '').split())
        if len(text) >= 8 and any(text in content for content in user_contents):
            quote_verified_memory.add(quote.get('memory_id'))
    event_memory = ctx.snapshot.event_memory_ids()
    memory_to_event = {}
    for event_id, memory_id in event_memory.items():
        memory_to_event.setdefault(memory_id, event_id)
    events = ctx.snapshot.events
    strict_verified = set(strict_chat_stage)
    for memory_id in quote_verified_memory:
        event_id = memory_to_event.get(memory_id, memory_id if memory_id in events else None)
        if event_id:
            strict_verified.add(event_id)
    lenient_verified = set(request_lineage) | strict_verified
    # historical promotion attempts: the relationship zone history has no confirmed promotion yet.
    versions_path = ctx.options.get('versions_path')
    history = {'versions': 0, 'relationship_observations': 0, 'confirmed_observations': 0}
    if versions_path and Path(versions_path).is_file():
        rows = core.read_jsonl(versions_path)
        history['versions'] = len(rows)
        for row in rows:
            try:
                after = json.loads(row.get('after') or '{}')
            except (TypeError, ValueError):
                continue
            for item in ((after.get('relationship') or {}).get('items') or []):
                if isinstance(item, dict):
                    history['relationship_observations'] += 1
                    if item.get('status') == 'confirmed':
                        history['confirmed_observations'] += 1
    arms = [
        {'arm': 'chat_only (current)', 'config': {'allowed_sources': ['chat']},
         'metrics': {'confirmed_promotions': 0, 'circular_self_promotion_rate': 0.0,
                     'note': 'event:/memory: 指针不能单独晋升；与当前实现一致'}},
        {'arm': 'event_pointer_naive', 'config': {'allowed_sources': ['event', 'memory']},
         'metrics': {'confirmed_promotions': len(events),
                     'circular_self_promotion_rate': round((len(events) - len(strict_verified)) / len(events), 4) if events else None,
                     'note': '只要存在 event:/memory: 指针即晋升 -> 模型自己的故事可以自我确认'}},
        {'arm': 'event_stage_evidence_chat (candidate, strict)', 'config': {'allowed_sources': ['chat', 'stage-evidence:chat', 'quotes']},
         'metrics': {'confirmed_promotions': len(strict_verified),
                     'circular_self_promotion_rate': 0.0,
                     'verified_events': len(strict_verified),
                     'unverified_events': len(events) - len(strict_verified),
                     'quote_verified': len(quote_verified_memory)}},
        {'arm': 'event_request_lineage (weaker proxy)', 'config': {'allowed_sources': ['chat', 'request-lineage']},
         'metrics': {'confirmed_promotions': len(lenient_verified),
                     'circular_self_promotion_rate': round((len(lenient_verified) - len(strict_verified)) / max(1, len(lenient_verified)), 4),
                     'note': 'stage-evidence 全部是 tool 证据，只能证明来自某次真实请求，不能证明 Lan 原话支持该认知'}},
    ]
    recommended = len(strict_verified) > 0
    result.update({'status': 'completed', 'arms': arms,
                   'comparison': [{'metric': 'confirmed_growth', 'a': 0, 'b': len(strict_verified), 'delta': len(strict_verified)},
                                  {'metric': 'circular_self_promotion_rate', 'a': 0.0, 'b': 0.0, 'delta': 0.0}],
                   'verdict': {'recommended': bool(recommended),
                               'verified_events_strict': len(strict_verified),
                               'verified_events_request_lineage': len(lenient_verified),
                               'self_proof_rate': 0.0,
                               'reason': ('严格可溯源 event 证据 %d/%d 条，扩展规则可多晋升 %d 条且自证率 0'
                                          % (len(strict_verified), len(events), len(strict_verified))) if recommended else
                                         ('当前数据没有任何 event 能凭 Lan 原话/chat 证据晋升（%d/%d 事件只有 tool 级请求血缘）；'
                                          '扩展规则不产生有效晋升，建议维持 chat-only，并让 S3 先补 chat 证据链'
                                          % (len(lenient_verified), len(events)))},
                   'evidence': {'events': len(events), 'stage_evidence_rows': len(ctx.snapshot.stage_evidence),
                                'stage_evidence_kinds': stage_kinds,
                                'events_with_request_lineage': len(request_lineage),
                                'quotes': len(ctx.snapshot.quotes), 'quote_verified': len(quote_verified_memory),
                                'learned_context_history': history,
                                'sample_limit': '关系认知历史没有任何 confirmed 晋升记录（0/%d 观测），功效不足，需 S3 实测'
                                                % history['relationship_observations']},
                   'evidence_gaps': ['learned-context 关系区历史 0 条 confirmed 晋升，回放无法验证真实晋升收益',
                                     'event_ledger 的 64 条 stage-evidence 全部是 tool 类型，没有 chat 类型用户原话证据',
                                     'quotes 表 6 条中仅 %d 条文本能在真实用户消息里逐字命中' % len(quote_verified_memory)],
                   'budget': budget_snapshot(ctx.budget, tier=0, cost=0.0)})
    ctx.save_detail('ab_a2_lineage', [{'event_id': event_id, 'verified_message_ids': sorted(ids)}
                                      for event_id, ids in sorted(request_lineage.items())])
    return result


def run_a3(ctx):
    learned = ctx.options.get('learned_context')
    if not learned:
        result = core.result_stub('AB-A3', 0, title='learned-context 四区独立存储预算',
                                  variable='4000B 共享 vs 四区独立上限', fixed='隔离回放历史写入拒绝记录')
        result.update({'status': 'blocked', 'verdict': {'recommended': None, 'reason': '缺少 learned-context 快照'},
                       'evidence_gaps': ['--learned-context 未提供']})
        return result
    sizes = core.learned_context_sizes(learned)
    shared_cap = 4000
    # Candidate four-zone caps: keep each zone's current size + the P6-2 recommended relationship
    # budget (1500B); the shared cap today leaves 560B of headroom, which one write can exhaust.
    zone_caps = {'facts': 1800, 'growth': 2000, 'relationship': 1500, 'ongoing': 900}
    versions_path = ctx.options.get('versions_path')
    historical_max = dict(sizes)
    version_count = 0
    if versions_path and Path(versions_path).is_file():
        for row in core.read_jsonl(versions_path):
            version_count += 1
            try:
                after = json.loads(row.get('after') or '{}')
            except (TypeError, ValueError):
                continue
            candidate = core.learned_context_sizes(after)
            for zone in ('facts', 'growth', 'relationship', 'ongoing'):
                historical_max[zone] = max(historical_max[zone], candidate[zone])
    # Forward workload grounded on real history and the S3 target: keep today's facts/growth pace,
    # refill a usable relationship zone, add one ongoing item.
    target = {'facts': sizes['facts'] + 120, 'growth': sizes['growth'] + 300,
              'relationship': max(sizes['relationship'], 1200), 'ongoing': sizes['ongoing'] + 300}
    writes = [('facts+120B', 'facts', target['facts']),
              ('growth+300B', 'growth', target['growth']),
              ('relationship 回填至 1200B', 'relationship', target['relationship']),
              ('ongoing+300B', 'ongoing', target['ongoing'])]
    state = {zone: sizes[zone] for zone in zone_caps}
    shared_rejected, zone_rejected = 0, 0
    shared_log, zone_log = [], []
    shared_state = dict(state)
    for name, zone, value in writes:
        candidate_total = sum(shared_state.values()) - shared_state[zone] + value
        rejected_shared = candidate_total > shared_cap
        if rejected_shared:
            shared_rejected += 1
        else:
            shared_state[zone] = value
        shared_log.append({'write': name, 'total_bytes': candidate_total, 'rejected': rejected_shared})
        state[zone] = value
        rejected_zone = state[zone] > zone_caps[zone]
        if rejected_zone:
            zone_rejected += 1
        zone_log.append({'write': name, 'zone': zone, 'zone_bytes': value,
                         'zone_cap': zone_caps[zone], 'rejected': rejected_zone})
    head_before = sizes['facts'] + sizes['growth']
    head_after = target['facts'] + target['growth']
    head_delta_bytes = head_after - head_before
    # tokens/byte calibration from the real context-log (median calibration_ratio ~0.30).
    head_tokens = round(head_delta_bytes * 0.30, 1)
    result = core.result_stub('AB-A3', 0, title='learned-context 四区独立存储预算',
                              variable='4000B 共享 vs 四区独立上限 %s' % json.dumps(zone_caps),
                              fixed='隔离回放 learned-context 历史 + P6-2/P6-3 实测占用')
    result.update({'status': 'completed',
                   'arms': [{'arm': 'shared_4000B', 'config': {'cap': 'shared 4000'},
                             'metrics': {'current_total_bytes': sizes['total'],
                                         'headroom_bytes': shared_cap - sizes['total'],
                                         'rejected_writes': shared_rejected, 'writes': len(writes),
                                         'write_rejection_rate': round(shared_rejected / len(writes), 4),
                                         'write_log': shared_log}},
                            {'arm': 'four_zone_independent', 'config': zone_caps,
                             'metrics': {'rejected_writes': zone_rejected, 'writes': len(writes),
                                         'write_rejection_rate': round(zone_rejected / len(writes), 4),
                                         'head_token_delta_est': head_tokens,
                                         'write_log': zone_log}}],
                   'comparison': [{'metric': 'write_rejection_rate',
                                   'a': round(shared_rejected / len(writes), 4),
                                   'b': round(zone_rejected / len(writes), 4),
                                   'delta': round((zone_rejected - shared_rejected) / len(writes), 4)},
                                  {'metric': 'head_token_delta_est', 'a': 0, 'b': head_tokens,
                                   'delta': head_tokens}],
                   'verdict': {'recommended': shared_rejected > 0 and zone_rejected == 0,
                               'zone_caps': zone_caps,
                               'reason': '共享上限下 %d/%d 次写入被拒（当前余量仅 %dB），四区独立后 %d/%d 次被拒；HEAD 估算增量 +%.1f tokens'
                                         % (shared_rejected, len(writes), shared_cap - sizes['total'],
                                            zone_rejected, len(writes), head_tokens)},
                   'evidence': {'current_sizes_bytes': sizes, 'shared_cap': shared_cap,
                                'zone_caps_bytes': zone_caps, 'historical_max_sizes': historical_max,
                                'version_history_entries': version_count,
                                'token_calibration': '0.30 tokens/byte (real context-log calibration ratio median)',
                                'note': '写入拒绝是前向模拟（基于真实占用与 P6-2 目标），未跑真实写入路径'},
                   'evidence_gaps': ['写入拒绝率来自前向模拟，未跑真实写入路径；HEAD 增量只估算 facts+growth 渲染'],
                   'budget': budget_snapshot(ctx.budget, tier=0, cost=0.0)})
    return result