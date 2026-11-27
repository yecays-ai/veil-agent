"""SiliconFlow Reranker client with local caching and BudgetLedger accounting (AB-R2).

Supports BAAI/bge-reranker-v2-m3 (free tier, 0 CNY/1M) and Qwen/Qwen3-Reranker-0.6B
(0.07 CNY/1M input tokens) via SiliconFlow's /v1/rerank endpoint.
"""
from __future__ import annotations

import json
import math
import threading
import time
import urllib.request
from pathlib import Path

from . import core

RERANK_MODELS = {
    'BAAI/bge-reranker-v2-m3': {
        'rate_cny_per_million': 0.0,
        'price_source': 'SiliconFlow official pricing (free tier, verified 2026-11-26)',
    },
    'Qwen/Qwen3-Reranker-0.6B': {
        'rate_cny_per_million': 0.07,
        'price_source': 'SiliconFlow official pricing (0.07 CNY/1M input tokens, verified 2026-11-26)',
    },
}


class RerankError(RuntimeError):
    pass


class RerankClient:
    """Cached, budget-guarded SiliconFlow /v1/rerank client."""

    def __init__(self, cache_path, *, base_url='https://api.siliconflow.cn/v1',
                 api_key='', budget=None, tier=1, experiment='AB-R2', timeout=20.0,
                 allow_provider=True):
        self.path = Path(cache_path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.base_url = base_url.rstrip('/')
        self._api_key = api_key
        self.budget = budget
        self.tier = tier
        self.experiment = experiment
        self.timeout = float(timeout)
        self.allow_provider = bool(allow_provider)
        self._lock = threading.Lock()
        self._cache = {}
        if self.path.is_file():
            for row in core.read_jsonl(self.path):
                if isinstance(row.get('key'), str):
                    self._cache[row['key']] = row

    def _key(self, model, query, doc_ids, documents):
        payload = json.dumps({'model': model, 'query': query, 'doc_ids': doc_ids,
                              'doc_hashes': [core.sha256_text(d) for d in documents]},
                             ensure_ascii=False, sort_keys=True)
        return core.sha256_text(payload)

    def rerank(self, model, query, doc_ids, documents):
        """Return {'ranked_ids', 'scores', 'latency_ms', 'input_tokens', 'cost_cny', 'cached', 'error'}."""
        if model not in RERANK_MODELS:
            raise RerankError('unpriced rerank model refused: %s' % model)
        rate = float(RERANK_MODELS[model]['rate_cny_per_million'])
        key = self._key(model, query, doc_ids, documents)
        with self._lock:
            if key in self._cache:
                rec = dict(self._cache[key])
                rec['cached'] = True
                rec['cost_cny'] = 0.0
                return rec
        if not self.allow_provider:
            raise RerankError('rerank cache miss in cache-only mode: %s' % key[:12])
        if not self._api_key:
            raise RerankError('missing API key for rerank')
        # Conservative token upper bound for budget reserve
        total_bytes = len(query.encode('utf-8')) + sum(len(d.encode('utf-8')) for d in documents)
        est_tokens = max(256, math.ceil(total_bytes * 0.8))
        reserve_cny = round(est_tokens * rate / 1_000_000, 8)
        ticket = None
        if self.budget is not None:
            ticket = self.budget.reserve(
                self.tier, self.experiment, provider='siliconflow-rerank', model=model,
                kind='rerank', reserve_cny=reserve_cny,
                detail={'estimated_input_tokens': est_tokens, 'rate_cny_per_million': rate,
                        'doc_count': len(documents)})
        body = json.dumps({
            'model': model,
            'query': query,
            'documents': list(documents),
            'top_n': len(documents),
            'return_documents': False,
        }, ensure_ascii=False).encode('utf-8')
        req = urllib.request.Request(
            f'{self.base_url}/rerank',
            data=body,
            headers={'Authorization': f'Bearer {self._api_key}',
                     'Content-Type': 'application/json'},
            method='POST',
        )
        started = time.perf_counter()
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                data = json.loads(resp.read().decode('utf-8'))
        except Exception as exc:
            elapsed = round((time.perf_counter() - started) * 1000.0, 2)
            if ticket is not None:
                self.budget.settle(ticket, reserve_cny, accounting='rerank-failed-conservative',
                                   note=type(exc).__name__)
            return {'key': key, 'model': model, 'ranked_ids': list(doc_ids), 'scores': {},
                    'latency_ms': elapsed, 'input_tokens': 0, 'cost_cny': reserve_cny,
                    'cached': False, 'error': f'{type(exc).__name__}: {str(exc)[:200]}'}
        elapsed = round((time.perf_counter() - started) * 1000.0, 2)
        meta_tokens = ((data.get('meta') or {}).get('tokens') or {}).get('input_tokens')
        used_tokens = int(meta_tokens) if type(meta_tokens) is int and meta_tokens > 0 else None
        amount = round(used_tokens * rate / 1_000_000, 8) if used_tokens is not None else reserve_cny
        if ticket is not None:
            self.budget.settle(ticket, amount, usage={'input_tokens': used_tokens or 0},
                               accounting='rerank-usage' if used_tokens is not None
                               else 'rerank-usage-missing-conservative')
        scores = {}
        ordered_pairs = []
        for item in (data.get('results') or []):
            idx = item.get('index')
            sc = float(item.get('relevance_score') or 0.0)
            if isinstance(idx, int) and 0 <= idx < len(doc_ids):
                mid = doc_ids[idx]
                scores[mid] = round(sc, 6)
                ordered_pairs.append((sc, mid))
        ordered_pairs.sort(key=lambda pair: (-pair[0], pair[1]))
        ranked_ids = [mid for _sc, mid in ordered_pairs]
        for mid in doc_ids:
            if mid not in scores:
                ranked_ids.append(mid)
                scores[mid] = 0.0
        record = {'key': key, 'model': model, 'ranked_ids': ranked_ids, 'scores': scores,
                  'latency_ms': elapsed, 'input_tokens': used_tokens or 0,
                  'cost_cny': amount, 'cached': False, 'error': ''}
        with self._lock:
            self._cache[key] = record
            with self.path.open('a', encoding='utf-8') as handle:
                handle.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + '\n')
        return record
