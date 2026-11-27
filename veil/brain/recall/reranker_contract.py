"""P6-S5: Qwen3-Reranker-0.6B local/deep-recall integration contract & pre-judge score gate.

Design constraints (from P6-S4 AB-R2 & AB-COMB):
1. Remote rerankers (p50=2.7s / p95=8.2s) MUST NOT be placed synchronously on the normal chat path.
2. Whenever a reranker is attached (local ONNX/llama.cpp/GPU service or explicit deep-recall path),
   the downstream LLM judge and pure-vector fallback MUST be preserved.
3. A calibrated pre-judge score gate is mandatory: S4 AB-COMB proved that feeding unfiltered
   Rerank Top-8 + Vec-8 into the judge pushed empty-query FPR from 0.4545 to 0.7273 (+27.3pp).
4. Asynchronous/deep-recall results MUST pass staleness & permission verification (request_id,
   topic_hash, memory version/DB signature, and live visibility) before entering any conversation.
5. Disabled by default; any service unavailability, load failure, or timeout deterministically
   falls back to the stable hybrid retrieval baseline without altering installation/startup flows.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, field


DEFAULTS = {
    'enabled': False,
    'mode': 'disabled',           # disabled | local_http | deep_recall_async
    'model': 'Qwen/Qwen3-Reranker-0.6B',
    'score_threshold': 0.25,      # calibrated on S4 AB-R2/AB-COMB to suppress empty-query FPR
    'top_n': 8,
    'timeout_seconds': 1.2,
    'preserve_vector_fallback': True,
    'daily_budget_cny': 1.0,
}


def load_settings(raw):
    raw = raw if isinstance(raw, dict) else {}
    cfg = dict(DEFAULTS)
    if type(raw.get('enabled')) is bool:
        cfg['enabled'] = raw['enabled']
    if raw.get('mode') in ('disabled', 'local_http', 'deep_recall_async'):
        cfg['mode'] = raw['mode']
    if isinstance(raw.get('model'), str) and raw['model'].strip():
        cfg['model'] = raw['model'].strip()
    thresh = raw.get('score_threshold')
    if type(thresh) in (int, float) and 0.0 <= float(thresh) <= 1.0:
        cfg['score_threshold'] = float(thresh)
    top_n = raw.get('top_n')
    if type(top_n) is int and 1 <= top_n <= 24:
        cfg['top_n'] = top_n
    timeout = raw.get('timeout_seconds')
    if type(timeout) in (int, float) and 0.1 <= float(timeout) <= 10.0:
        cfg['timeout_seconds'] = float(timeout)
    if type(raw.get('preserve_vector_fallback')) is bool:
        cfg['preserve_vector_fallback'] = raw['preserve_vector_fallback']
    budget = raw.get('daily_budget_cny')
    if type(budget) in (int, float) and 0.0 <= float(budget) <= 100.0:
        cfg['daily_budget_cny'] = float(budget)
    if not cfg['enabled']:
        cfg['mode'] = 'disabled'
    return cfg


def topic_fingerprint(text: str) -> str:
    normalized = ' '.join((text or '').strip().lower().split())
    return hashlib.sha256(normalized.encode('utf-8')).hexdigest()[:16]


@dataclass(frozen=True)
class AsyncRerankEnvelope:
    request_id: str
    topic_hash: str
    db_signature: tuple
    ranked_ids: tuple[str, ...]
    scores: dict[str, float] = field(default_factory=dict)
    memory_versions: dict[str, int] = field(default_factory=dict)


def filter_by_score_gate(ranked_ids, scores, *, threshold=0.25, top_n=8):
    """Apply pre-judge reranker score gate.

    Returns (kept_ids, gated_out_ids, gate_meta) so diagnostics can distinguish
    'no relevant hits after rerank gate' from 'reranker unavailable/degraded'.
    """
    scores = scores if isinstance(scores, dict) else {}
    kept, gated_out = [], []
    for mid in list(ranked_ids or []):
        sc = float(scores.get(mid, 0.0))
        if sc >= float(threshold) and len(kept) < int(top_n):
            kept.append(mid)
        else:
            gated_out.append({'id': mid, 'score': round(sc, 4), 'reason': 'below_rerank_threshold'})
    meta = {
        'threshold': float(threshold),
        'input_count': len(ranked_ids or []),
        'kept_count': len(kept),
        'gated_out_count': len(gated_out),
        'top_score': round(max((float(scores.get(m, 0.0)) for m in (ranked_ids or [])), default=0.0), 4),
    }
    return kept, gated_out, meta


def validate_async_result(envelope: AsyncRerankEnvelope, *,
                          current_request_id: str,
                          current_topic_hash: str,
                          current_db_signature: tuple,
                          is_visible_fn=None,
                          current_versions: dict[str, int] | None = None) -> tuple[list[str], dict]:
    """Verify an asynchronous/deep-recall reranker result before letting it enter a turn.

    Rejects stale request IDs, shifted topics, invalidated DB snapshots, changed memory versions,
    and memories that are no longer visible/permitted.
    """
    if not isinstance(envelope, AsyncRerankEnvelope):
        return [], {'accepted': False, 'reason': 'invalid_envelope'}
    if envelope.request_id != current_request_id:
        return [], {'accepted': False, 'reason': 'request_id_mismatch'}
    if envelope.topic_hash != current_topic_hash:
        return [], {'accepted': False, 'reason': 'topic_shifted'}
    if envelope.db_signature != current_db_signature:
        return [], {'accepted': False, 'reason': 'db_signature_stale'}
    accepted = []
    dropped = []
    versions = current_versions if isinstance(current_versions, dict) else {}
    for mid in envelope.ranked_ids:
        if mid in envelope.memory_versions:
            if mid not in versions:
                dropped.append({'id': mid, 'reason': 'version_missing'})
                continue
            if envelope.memory_versions[mid] != versions[mid]:
                dropped.append({'id': mid, 'reason': 'version_changed'})
                continue
        elif versions:
            dropped.append({'id': mid, 'reason': 'version_untracked'})
            continue
        if callable(is_visible_fn) and not is_visible_fn(mid):
            dropped.append({'id': mid, 'reason': 'not_visible'})
            continue
        accepted.append(mid)
    return accepted, {'accepted': True, 'kept': len(accepted), 'dropped': dropped}
