"""Shared experiment runtime: cached query vectors, ranking evaluation, result I/O.

The committed results keep aggregated metrics only; per-sample working data (which contains real
queries and memory ids) stays under the gitignored local run directory.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import time
from pathlib import Path

from . import core
from .retrieval import RetrievalConfig, Retriever, build_alias_dictionary

DETAIL_DIR_NAME = 'runs'


class Context:
    def __init__(self, *, snapshot, samples, eval_report, embedder, budget, results_dir,
                 detail_dir, options=None):
        self.snapshot = snapshot
        self.samples = list(samples)
        self.samples_by_id = {sample.sample_id: sample for sample in samples}
        self.eval_report = eval_report
        self.embedder = embedder
        self.budget = budget
        self.results_dir = Path(results_dir)
        self.detail_dir = Path(detail_dir)
        self.results_dir.mkdir(parents=True, exist_ok=True)
        self.detail_dir.mkdir(parents=True, exist_ok=True)
        self.options = dict(options or {})
        self.query_vectors = {}
        self.story_vectors = {}
        self.story_embedding_stats = None
        self.retriever = Retriever(snapshot, alias_dictionary=build_alias_dictionary(snapshot))
        self.results = {}
        self._embedding_charged = False

    # ---------------------------------------------------------------- embeddings
    def query_vector(self, sample):
        if sample.sample_id not in self.query_vectors:
            self.query_vectors[sample.sample_id] = self.embedder.embed(sample.query)
        return self.query_vectors[sample.sample_id]

    def ensure_query_vectors(self, samples=None):
        for sample in samples or self.samples:
            self.query_vector(sample)

    def ensure_story_vectors(self):
        if self.story_vectors:
            return self.story_vectors
        from .retrieval import Retriever
        for event in self.snapshot.events.values():
            if not event.story:
                continue
            self.story_vectors[event.id] = self.embedder.embed(event.story)
        self.retriever = Retriever(self.snapshot, alias_dictionary=self.retriever.alias_dictionary,
                                   story_vectors=self.story_vectors)
        return self.story_vectors

    def charge_embeddings(self, experiment, note=''):
        """Return the embedding audit summary; the per-call booking already happened in
        EmbeddingCache (reserve before dispatch, settle with reported usage). Keeping this method
        avoids double-counting the same embedding spend in the ledger."""
        stats = self.embedder.stats() if hasattr(self.embedder, 'stats') else {
            'model': getattr(self.embedder, 'model', 'fake'), 'provider_calls': getattr(self.embedder, 'calls', 0),
            'usage_tokens': getattr(self.embedder, 'usage_tokens', 0), 'cost_cny': 0.0,
            'input_cny_per_million': 0.0, 'cache_hits': getattr(self.embedder, 'used_cache', 0),
            'blocked_calls': 0}
        stats = dict(stats)
        stats['note'] = note
        self._embedding_charged = True
        return stats

    # ---------------------------------------------------------------- ranking
    def rankings(self, config, samples=None):
        samples = list(samples or self.samples)
        ranked = {}
        latencies = []
        diagnostics = {}
        for sample in samples:
            vector = self.query_vector(sample)
            items, diag = self.retriever.rank(sample.query, vector, config,
                                              query_time=sample.context.get('time', ''))
            ranked[sample.sample_id] = [item.id for item in items]
            latencies.append(diag.get('elapsed_ms', 0.0))
            diagnostics[sample.sample_id] = {'count': diag.get('count'),
                                             'alias_matched': len(diag.get('alias_matched') or []),
                                             'related_expanded': len(diag.get('related_expanded') or []),
                                             'story_hits': diag.get('story_hits', 0)}
        return ranked, latencies, diagnostics

    def evaluate(self, config, samples=None):
        """Raw top-K metrics plus a common offline gate for the noise/trap FPR column.

        Tier 0 cannot call the paid judge, so a fixed relevance gate (stored-vector cosine
        >= 0.45, at most 3 memories — the same gate used by AB-R3b) makes the "noise/trap
        injected = false positive" metric comparable across retrieval variants. Raw candidate
        injection is always non-empty after the judge stage, so it is not a useful signal.
        """
        samples = list(samples or self.samples)
        rankings, gated, latencies = {}, {}, []
        for sample in samples:
            items, diag = self.retriever.rank(sample.query, self.query_vector(sample), config,
                                              query_time=sample.context.get('time', ''))
            rankings[sample.sample_id] = [item.id for item in items]
            # The gate is defined on the stored-memory cosine, not on the fused score or the
            # story-index override; otherwise story-index arms would use a different gate and their
            # FPR/correct-empty counts would not be comparable with the other arms.
            gated[sample.sample_id] = [item.id for item in items if item.stored_vector >= 0.45][:3]
            latencies.append(diag.get('elapsed_ms', 0.0))
        metrics = core.evaluate_rankings(samples, rankings, config.k)
        metrics['gated'] = core.evaluate_rankings(samples, gated, 3)
        metrics['latency'] = core.summarize_latency(latencies)
        metrics['cost_cny'] = 0.0
        return metrics, rankings


    def arm(self, name, config, samples=None):
        metrics, _rankings = self.evaluate(config, samples)
        return {'arm': name, 'config': _config_dict(config), 'metrics': metrics}

    # ---------------------------------------------------------------- I/O
    def save_detail(self, name, rows):
        path = self.detail_dir / (name + '.jsonl')
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open('w', encoding='utf-8') as handle:
            for row in rows:
                handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + '\n')
        return path

    def save_result(self, result):
        result['finished_at'] = core.utc_now()
        result['budget'] = self.budget.totals()
        path = self.results_dir / ('%s.json' % result['experiment_id'].replace('-', '_').lower())
        path.write_text(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + '\n',
                        encoding='utf-8')
        self.results[result['experiment_id']] = result
        return path


def gated_fpr(metrics):
    """Noise/trap FPR under the shared offline gate; None when unavailable."""
    return ((metrics.get('gated') or {}).get('overall') or {}).get('false_positive_rate')


def gated_correct_empty(metrics):
    overall = (metrics.get('gated') or {}).get('overall') or {}
    return overall.get('empty_correct'), overall.get('empty_expected_n')


def _config_dict(config):
    if isinstance(config, dict):
        return dict(config)
    return {key: getattr(config, key) for key in config.__dataclass_fields__}


def snapshot_fingerprint(snapshot, eval_report, *, extra_files=None):
    """Aggregate identity in the committed result: no paths, only hashes and counts."""
    identity = snapshot.identity()
    identity.update({'eval_sha256': eval_report.get('sha256'), 'eval_samples': eval_report.get('samples'),
                     'eval_errors': len(eval_report.get('errors') or [])})
    for name, path in (extra_files or {}).items():
        if path and Path(path).is_file():
            identity['%s_sha256' % name] = core.sha256_file(path)
    return identity


def budget_snapshot(budget, *, tier=0, calls=0, tokens=0, cost=None):
    totals = budget.totals()
    entry = {'tier': tier, 'paid_calls': calls, 'tokens': tokens,
             'tier_spent_cny': totals['by_tier'].get(str(tier), 0.0),
             'total_spent_cny': totals['total_cny']}
    if cost is not None:
        entry['this_experiment_cny'] = round(float(cost), 6)
    return entry