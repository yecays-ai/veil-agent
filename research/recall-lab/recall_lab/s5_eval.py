"""P6-S5 end-to-end isolated evaluation suite (PR #461 review-hardened).

Fixes all review findings:
- F1: 3-way natural association blind review uses full production request assembly
      (persona + learned facts/growth + relationship_base + recent history + judged normal recall
      + ongoing + user turn); only the natural association mechanism varies across the 3 arms.
- F4: Adds the final recommended combined arm `s5_final_candidate_sw_on_k8`
      (stopwords=on + K=8 + S5 situational judge) directly against `baseline_k8_sw_off`.
- F5: Separately tracks normal_rejected, judge_timeouts, judge_errors, parse_failures, and
      production-faithful degraded fallback injection (never counts judge failures as empty_correct).
- F6: Replays 3-way natural association through the real production `Memory.context()` +
      `Memory.finish_recall(space=...)` + `Memory.confirm_delivery()` pipeline on an isolated
      temporary store over the read-only brain.db copy.

Uses DeepSeek API through BudgetLedger with a hard CNY ceiling (default CNY 10.00) and
reuses cached query embeddings, judge calls, and rerank details to minimize API spend.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import random
import shutil
import sys
import tempfile
import threading
import time
from concurrent.futures import Future
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[3]
LAB_ROOT = Path(__file__).resolve().parents[1]
for _p in (str(ROOT), str(LAB_ROOT)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from veil.brain.memory_agent import Memory  # noqa: E402
from veil.brain.relationship_base import build as build_relationship_base, message as relationship_base_message  # noqa: E402
from veil.brain.recall.brain_retrieval import BrainIndex, load_settings as load_ret_settings  # noqa: E402
from veil.brain.recall.context_signal import analyze as analyze_context, format_recent_context  # noqa: E402
from veil.brain.recall.recall_judge import JudgeFailed, card_text, labels_for, parse_choice  # noqa: E402
from veil.brain.recall.recall_relevance import cosine  # noqa: E402
from veil.brain.recall.recall_rotation import Cooldown, settings as load_rot_settings  # noqa: E402
from veil.brain.recall.reranker_contract import filter_by_score_gate  # noqa: E402
from veil import prompts  # noqa: E402

from recall_lab import core  # noqa: E402
from recall_lab.judge import JudgeClient  # noqa: E402
from recall_lab.runtime import Context, budget_snapshot  # noqa: E402
from recall_lab.tier12 import JUDGE_SUBSET, ledgered_judge_call  # noqa: E402
from recall_lab.tier2_real import ChatClient  # noqa: E402

LEGACY_CONSERVATIVE_PROMPT = (
    'You help Peter, an AI companion, decide which of his saved memories bear on what his partner Lan just said to him.\n'
    'A memory bears on her message only when it is DIRECTLY about the same situation, thing, person or recurring pattern '
    'she is talking about now, so that Peter would handle this moment differently for knowing it. A shared theme such as '
    'tiredness, sleep, games, money or mood is NOT enough.\n'
    'Pick every memory that directly bears on her message, best first; there is no fixed number. Most messages have none: '
    'greetings, small talk, generic questions, practical requests, facts, and anything about the outside world have none. '
    'When unsure, pick none. A memory whose relevant detail is at its end (the ending line on its card) still counts.\n'
    'Answer with JSON only: {"ids": ["A"]} or {"ids": []}.'
)


def _select_stratified(samples, counts=None):
    counts = counts or JUDGE_SUBSET
    by_cat = {}
    for s in samples:
        by_cat.setdefault(s.category, []).append(s)
    out = []
    for cat, n in counts.items():
        out.extend(by_cat.get(cat, [])[:n])
    return out


def _vector_fallback(snapshot, qvec, taken, limit=8):
    if not qvec:
        return []
    ranked = []
    for mid in snapshot.visible_ids:
        vec = snapshot.vectors.get(mid)
        if not vec or len(vec) != len(qvec):
            continue
        ranked.append((cosine(qvec, vec), mid))
    ranked.sort(reverse=True)
    found = []
    for _, mid in ranked:
        if len(found) >= limit:
            break
        if mid in taken:
            continue
        found.append(mid)
    return found


def _build_judge_messages(query, cand_ids, snapshot, *, prompt_text, recent=None, use_s5_context=False):
    order = sorted(cand_ids, key=lambda mid: hashlib.md5((query + mid).encode('utf-8')).hexdigest())
    labels = labels_for(len(order))
    letter_map = {lab: mid for lab, mid in zip(labels, order)}
    lines = []
    for lab, mid in letter_map.items():
        mem = snapshot.memories.get(mid)
        raw = mem.raw if mem else ''
        lines.append(f'{lab}) {card_text(raw)}')
    parts = []
    if use_s5_context:
        recent_block = format_recent_context(recent)
        if recent_block:
            parts.append('Recent conversation (context only; judge relevance to the latest message):\n' + recent_block)
    parts.append(f'Lan just said: "{query[:600]}"')
    if use_s5_context:
        sig = analyze_context(query, recent)
        if sig.get('mode') in ('technical_task', 'work_emotion'):
            cue = ('Current turn looks like a pure technical/work task.'
                   if sig['mode'] == 'technical_task'
                   else 'Current turn is emotional/relational sharing triggered by work.')
            parts.append('Context note: ' + cue)
    parts.append('Saved memories:\n' + '\n'.join(lines))
    messages = [{'role': 'system', 'content': prompt_text},
                {'role': 'user', 'content': '\n\n'.join(parts)}]
    return messages, letter_map


def _eval_retrieval_and_judge_arm(arm_name, db_path, ctx, subset, *, stopwords_enabled, k,
                                  prompt_text, use_s5_context, judge_client, judge_cache, cache_path):
    """F5-compliant evaluator: separates normal_rejected, timeout, error, parse_failure, and degraded injection."""
    idx = BrainIndex(db_path, weight_vector=0.65, weight_keyword=0.35, stopwords_enabled=stopwords_enabled)
    idx.refresh()

    ret_recalls, ret_p5s, ret_ndcgs = [], [], []
    j_recalls, j_precs, final_recalls, final_precs, latencies = [], [], [], [], []
    empty_exp, empty_ok_judge, empty_ok_final = 0, 0, 0
    trap_exp, trap_ok_judge, trap_ok_final = 0, 0, 0
    noise_exp, noise_ok_judge, noise_ok_final = 0, 0, 0
    normal_rejected = 0
    judge_timeouts = 0
    judge_errors = 0
    parse_failures = 0
    degraded_fallbacks = 0
    in_tok, out_tok, paid_calls, cached_calls = 0, 0, 0, 0
    cost_cny = 0.0
    injected_total = 0
    per_sample_picked = {}
    by_cat = {}

    for sample in subset:
        qvec = ctx.query_vector(sample)
        res = idx.search(sample.query, limit=k, query_vector=qvec)
        hybrid_ids = [r['id'] for r in res]
        exp = set(sample.expected)
        if exp:
            ret_hits = len(exp & set(hybrid_ids))
            ret_recalls.append(ret_hits / len(exp))
            ret_p5s.append(len(exp & set(hybrid_ids[:5])) / 5.0)
            dcg = sum(1.0 / (i + 2) if mid in exp else 0.0 for i, mid in enumerate(hybrid_ids[:8]))
            idcg = sum(1.0 / (i + 2) for i in range(min(len(exp), 8)))
            ret_ndcgs.append(dcg / idcg if idcg else 0.0)

        vec_extra = _vector_fallback(ctx.snapshot, qvec, set(hybrid_ids), limit=8)
        cand_ids = [mid for mid in (hybrid_ids + vec_extra) if mid in ctx.snapshot.memories]
        recent = (sample.context or {}).get('recent') or []
        messages, letter_map = _build_judge_messages(
            sample.query, cand_ids, ctx.snapshot, prompt_text=prompt_text,
            recent=recent, use_s5_context=use_s5_context)
        rec = ledgered_judge_call(
            ctx, judge_client, tier=1, experiment='P6-S5',
            messages=messages, timeout=4.0, cache_path=cache_path, cache=judge_cache)
        if rec.get('cached'):
            cached_calls += 1
        else:
            paid_calls += 1
        cost_cny += float(rec.get('amount_cny') or 0.0)
        in_tok += int(rec.get('prompt_tokens') or 0)
        out_tok += int(rec.get('completion_tokens') or 0)
        latencies.append(float(rec.get('latency_ms') or 0.0))

        err = str(rec.get('error') or '')
        judge_failed = False
        picked = []
        if err:
            judge_failed = True
            if 'timeout' in err.lower():
                judge_timeouts += 1
            else:
                judge_errors += 1
        else:
            try:
                picked = parse_choice(rec.get('text') or '', letter_map)
                if not picked:
                    normal_rejected += 1
            except JudgeFailed:
                judge_failed = True
                parse_failures += 1

        # Production degradation rule: on JudgeFailed, normal chat falls back to hybrid pool.
        if judge_failed:
            degraded_fallbacks += 1
            final_ids = list(hybrid_ids)
        else:
            final_ids = list(picked)

        per_sample_picked[sample.sample_id] = list(final_ids)
        injected_total += len(final_ids)
        cat_stat = by_cat.setdefault(
            sample.category,
            {'n': 0, 'recalls': [], 'precs': [], 'empty_exp': 0, 'empty_ok_final': 0, 'degraded': 0})
        cat_stat['n'] += 1
        if judge_failed:
            cat_stat['degraded'] += 1
        if exp:
            hits_j = len(exp & set(picked)) if not judge_failed else 0
            hits_f = len(exp & set(final_ids))
            j_recalls.append(hits_j / len(exp))
            j_precs.append(hits_j / len(picked) if (picked and not judge_failed) else 0.0)
            final_recalls.append(hits_f / len(exp))
            final_precs.append(hits_f / len(final_ids) if final_ids else 0.0)
            cat_stat['recalls'].append(hits_f / len(exp))
            cat_stat['precs'].append(hits_f / len(final_ids) if final_ids else 0.0)
        else:
            empty_exp += 1
            cat_stat['empty_exp'] += 1
            # Never count a judge failure/parse error as empty_correct.
            if not judge_failed and not picked:
                empty_ok_judge += 1
            if not judge_failed and not final_ids:
                empty_ok_final += 1
                cat_stat['empty_ok_final'] += 1
            if sample.category == 'trap':
                trap_exp += 1
                if not judge_failed and not picked:
                    trap_ok_judge += 1
                if not judge_failed and not final_ids:
                    trap_ok_final += 1
            elif sample.category == 'noise':
                noise_exp += 1
                if not judge_failed and not picked:
                    noise_ok_judge += 1
                if not judge_failed and not final_ids:
                    noise_ok_final += 1

    lat_s = sorted(latencies)
    p50 = lat_s[len(lat_s) // 2] if lat_s else 0.0
    p95 = lat_s[min(len(lat_s) - 1, int(len(lat_s) * 0.95))] if lat_s else 0.0
    cat_summary = {}
    for cat, st in by_cat.items():
        cat_summary[cat] = {
            'n': st['n'],
            'final_recall': round(sum(st['recalls']) / len(st['recalls']), 4) if st['recalls'] else None,
            'final_precision': round(sum(st['precs']) / len(st['precs']), 4) if st['precs'] else None,
            'final_fpr': round(1.0 - st['empty_ok_final'] / st['empty_exp'], 4) if st['empty_exp'] else None,
            'degraded': st['degraded'],
        }
    return {
        'arm': arm_name,
        'stopwords_enabled': stopwords_enabled,
        'k': k,
        'use_s5_context': use_s5_context,
        'samples': len(subset),
        'retrieval_recall_at_k': round(sum(ret_recalls) / len(ret_recalls), 4) if ret_recalls else 0.0,
        'retrieval_precision_at_5': round(sum(ret_p5s) / len(ret_p5s), 4) if ret_p5s else 0.0,
        'retrieval_ndcg_at_8': round(sum(ret_ndcgs) / len(ret_ndcgs), 4) if ret_ndcgs else 0.0,
        'judge_recall': round(sum(j_recalls) / len(j_recalls), 4) if j_recalls else 0.0,
        'judge_precision': round(sum(j_precs) / len(j_precs), 4) if j_precs else 0.0,
        'final_injected_recall': round(sum(final_recalls) / len(final_recalls), 4) if final_recalls else 0.0,
        'final_injected_precision': round(sum(final_precs) / len(final_precs), 4) if final_precs else 0.0,
        'false_positive_rate': round(1.0 - empty_ok_final / empty_exp, 4) if empty_exp else 0.0,
        'trap_fpr': round(1.0 - trap_ok_final / trap_exp, 4) if trap_exp else None,
        'noise_fpr': round(1.0 - noise_ok_final / noise_exp, 4) if noise_exp else None,
        'empty_correct': f'{empty_ok_final}/{empty_exp}',
        'outcomes': {
            'normal_rejected': normal_rejected,
            'judge_timeouts': judge_timeouts,
            'judge_errors': judge_errors,
            'parse_failures': parse_failures,
            'degraded_fallbacks': degraded_fallbacks,
        },
        'injected_total': injected_total,
        'input_tokens': in_tok,
        'output_tokens': out_tok,
        'latency_p50_ms': round(p50, 1),
        'latency_p95_ms': round(p95, 1),
        'calls_paid': paid_calls,
        'calls_cached': cached_calls,
        'cost_cny': round(cost_cny, 6),
        'by_category': cat_summary,
        '_per_sample_picked': per_sample_picked,
    }


def _make_production_memory(tmp_root: Path, db_path: Path, snapshot, *,
                            stopwords_enabled: bool, k: int,
                            association_enabled: bool, contextual_enabled: bool,
                            min_similarity: float = 0.52):
    """Build a real production Memory instance wired to an isolated read-only brain.db copy."""
    role_dir = tmp_root / 'peter'
    role_dir.mkdir(parents=True, exist_ok=True)
    db_copy = role_dir / 'brain.db'
    if not db_copy.exists():
        shutil.copy2(db_path, db_copy)

    class _IsoStore:
        def __init__(self, root):
            self.root = root
            self.mutex = threading.RLock()

        def replace(self, rel, obj):
            target = self.root / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(json.dumps(obj, ensure_ascii=False), encoding='utf-8')

        def append(self, rel, obj):
            target = self.root / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            with target.open('a', encoding='utf-8') as fh:
                fh.write(json.dumps(obj, ensure_ascii=False) + '\n')

    mem = object.__new__(Memory)
    mem.mutex = threading.RLock()
    mem._turn_notice_local = threading.local()
    mem.store = _IsoStore(tmp_root)
    mem.cfg = SimpleNamespace(
        id='peter',
        memory={
            'model': 'BAAI/bge-m3',
            'input_cny_per_million': 0.0,
            'retrieval': {'backend': 'brain', 'weight_vector': 0.65, 'weight_keyword': 0.35,
                          'stopwords_enabled': stopwords_enabled, 'candidate_k': k},
            'recall': {'association_enabled': association_enabled,
                       'association_contextual_enabled': contextual_enabled,
                       'association_min_similarity': min_similarity,
                       'association_pool_size': 32,
                       'association_cooldown_turns': 8,
                       'cooldown_turns': 3},
        },
    )
    mem.retrieval_settings = load_ret_settings(mem.cfg.memory['retrieval'])
    mem.recall_settings = load_rot_settings(mem.cfg.memory)
    mem.budget = SimpleNamespace(clock=lambda: '2026-11-27T15:30:00+08:00')
    mem.rotation = Cooldown()
    mem.rotation_path = 'peter/recall-rotation.json'
    mem.diagnostics = 'peter/memory/_agent/diagnostics.jsonl'
    mem.brain_authority = SimpleNamespace(db_path=db_copy)
    mem.state = {'entries': {}}
    mem.bucket_details_cache = {}
    mem.surfaced_ids = {}
    mem.delivery_listeners = []
    mem.last_injected_refs = []
    mem._previous_injected_refs = []
    mem.last_context_refs = []
    mem.last_context_kinds = {}
    mem.last_recall_marks = None
    mem.last_association_marks = None
    mem._has_association_rollback = False
    mem._pending_recall_request_id = ''
    mem.judge = None

    # Build real opening (breath() top-8: pinned DESC, importance DESC, created DESC)
    visible = [snapshot.memories[mid] for mid in snapshot.visible_ids]
    breath8 = sorted(visible, key=lambda m: (1 if m.pinned else 0, m.importance, m.created or ''), reverse=True)[:8]
    mem.opening = '\n\n'.join(f'[bucket_id:{m.id}] {m.title}\n{m.body}' for m in breath8)

    # Bind production-equivalent read helpers onto the isolated snapshot
    mem.raw_content_for = lambda mid: (snapshot.memories[mid].raw if mid in snapshot.memories else None)
    mem.context_visible = lambda items, **kw: [(mid, body) for mid, body in items if mid in snapshot.visible_ids]
    mem.visibility_reason = lambda mid: 'visible' if mid in snapshot.visible_ids else 'hidden'
    mem.assisted_settings = lambda q, r: None
    mem.retrieval_failure = lambda stage, exc: None
    mem._event_adjacency = lambda: {}
    mem._ombre_event_sources = lambda: {}
    mem._event_ledger = lambda: {'unique': {}, 'owners': {}, 'own': set()}
    mem._event_stories = lambda: {}
    mem._recall_meta = lambda ids: {
        mid: {
            'importance': snapshot.memories[mid].importance,
            'pinned': 'true' if snapshot.memories[mid].pinned else 'false',
            'title': snapshot.memories[mid].title,
            'created': snapshot.memories[mid].created,
            'links': [],
        }
        for mid in ids if mid in snapshot.memories
    }
    mem._recall_vectors = lambda ids: {
        mid: snapshot.vectors[mid] for mid in ids if mid in snapshot.vectors
    }
    return mem


def _replay_association_production(db_path, ctx, picked_map, *, stopwords_enabled=False, k=8):
    """F6: Replay off / legacy_breath8 / contextual_s5 through real Memory.context() + finish_recall()."""
    results = {}
    for mode_name, assoc_on, ctx_on in (
        ('off', False, False),
        ('legacy_breath8', True, False),
        ('contextual_s5', True, True),
    ):
        with tempfile.TemporaryDirectory() as tmp:
            mem = _make_production_memory(
                Path(tmp), Path(db_path), ctx.snapshot,
                stopwords_enabled=stopwords_enabled, k=k,
                association_enabled=assoc_on, contextual_enabled=ctx_on, min_similarity=0.52)

            # Attach a lightweight deterministic replay judge using cached query_vector & judged picks
            class _ReplayJudge:
                settings = {'timeout_seconds': 4.0, 'ombre_candidates': k, 'vector_candidates': 8}
                last_choice = {'status': 'empty', 'candidates': 0, 'picked': [], 'vector': []}

                def active(self):
                    return True

                def prefetch(self, q):
                    fut = Future()
                    vec = current_vec[0]
                    fut.set_result(vec)
                    return fut

                def choose(self, q, pool, prefetched, **kw):
                    sid = current_sid[0]
                    chosen_ids = picked_map.get(sid)
                    if chosen_ids is None:
                        # For samples outside the 36-sample judge subset, keep empty normal recall
                        # so association behavior is measured cleanly without noise inflation.
                        chosen_ids = []
                    bodies = {mid: f'[bucket_id:{mid}] {ctx.snapshot.memories[mid].title}\n{ctx.snapshot.memories[mid].body}'
                              for mid in chosen_ids if mid in ctx.snapshot.memories}
                    self.last_choice = {'status': 'chosen' if bodies else 'empty',
                                        'candidates': len(pool), 'picked': list(bodies), 'vector': []}
                    return list(bodies.items())

            current_vec = [None]
            current_sid = ['']
            mem.judge = _ReplayJudge()

            delivered_assoc = []
            work_intrusions = 0
            unique_ids, imp7_ids, recent_ids, ordinary_ids = set(), set(), set(), set()
            reasons = {}
            budget_dropped_assoc = 0
            per_sample_messages = {}
            per_sample_assoc_id = {}

            # Define "recent" as top 25% newest visible memories by created date
            sorted_by_date = sorted(
                (ctx.snapshot.memories[mid] for mid in ctx.snapshot.visible_ids),
                key=lambda m: m.created or '', reverse=True)
            recent_cutoff = {m.id for m in sorted_by_date[:max(1, len(sorted_by_date) // 4)]}

            for idx_num, sample in enumerate(ctx.samples, 1):
                current_sid[0] = sample.sample_id
                current_vec[0] = ctx.query_vector(sample)
                recent = (sample.context or {}).get('recent') or []
                req_id = f'replay-{mode_name}-{idx_num}'
                mem.context(sample.query, include_opening=(idx_num == 1), recent=recent,
                            wake=(sample.category == 'autonomy'), defer=True)
                msgs = mem.finish_recall(space=4000, history_tokens=800, protected_tokens=300,
                                         auxiliary_tokens=200, request_id=req_id)
                ev = mem.confirm_delivery(req_id)
                assoc_ids = list((ev or {}).get('association_ids') or [])
                assoc_dbg = dict((mem.last_recall_debug or {}).get('association') or {})
                per_sample_messages[sample.sample_id] = msgs
                per_sample_assoc_id[sample.sample_id] = assoc_ids[0] if assoc_ids else None
                if assoc_dbg.get('picked') and not assoc_ids:
                    budget_dropped_assoc += 1
                if assoc_ids:
                    mid = assoc_ids[0]
                    unique_ids.add(mid)
                    mobj = ctx.snapshot.memories.get(mid)
                    if mobj and (mobj.pinned or mobj.importance >= 7):
                        imp7_ids.add(mid)
                    else:
                        ordinary_ids.add(mid)
                    if mid in recent_cutoff:
                        recent_ids.add(mid)
                    if sample.category == 'trap':
                        work_intrusions += 1
                    delivered_assoc.append({
                        'sample_id': sample.sample_id, 'category': sample.category,
                        'picked_id': mid, 'similarity': assoc_dbg.get('similarity'),
                        'context_mode': assoc_dbg.get('context_mode'),
                    })
                else:
                    r = assoc_dbg.get('reason') or 'none'
                    reasons[r] = reasons.get(r, 0) + 1

            results[mode_name] = {
                'mode': mode_name,
                'total_samples': len(ctx.samples),
                'delivered_count': len(delivered_assoc),
                'budget_dropped_assoc': budget_dropped_assoc,
                'unique_memories_covered': len(unique_ids),
                'long_tail_imp7_covered': len(imp7_ids),
                'recent_covered': len(recent_ids),
                'ordinary_covered': len(ordinary_ids),
                'work_trap_intrusions': work_intrusions,
                'suppression_reasons': reasons,
                '_delivered_samples': delivered_assoc,
                '_per_sample_messages': per_sample_messages,
                '_per_sample_assoc_id': per_sample_assoc_id,
            }
    return results


def _load_production_learned_blocks(learned_context_path):
    """Build production HEAD learned-context + relationship_base + RUNTIME ongoing blocks."""
    path = Path(learned_context_path)
    if not path.is_file():
        return [], []
    data = json.loads(path.read_text(encoding='utf-8'))
    facts = (data.get('facts') or {}).get('text') or ''
    growth = (data.get('growth') or {}).get('text') or ''
    head_msgs = []
    learned_parts = []
    if facts.strip():
        learned_parts.append('What you know about Lan:\n' + facts.strip())
    if growth.strip():
        learned_parts.append('What you have learned about yourself:\n' + growth.strip())
    if learned_parts:
        head_msgs.append({'role': 'system', 'content': '\n\n'.join(learned_parts)})

    rel_items = (data.get('relationship') or {}).get('items') or []
    profile_proxy = SimpleNamespace(
        relationship_items=lambda: list(rel_items),
        notes_text=lambda: (facts + '\n' + growth).strip(),
    )
    rel_built = build_relationship_base(profile_proxy)
    rel_msg = relationship_base_message(rel_built)
    if rel_msg is not None:
        head_msgs.append(rel_msg)

    runtime_msgs = []
    ongoing_items = [it for it in ((data.get('ongoing') or {}).get('items') or [])
                     if isinstance(it, dict) and it.get('status') in ('active', 'uncertain') and it.get('text')]
    if ongoing_items:
        lines = [f"- {it['text']}" for it in ongoing_items[:6]]
        runtime_msgs.append({'role': 'system', 'content': 'Ongoing things between you two:\n' + '\n'.join(lines)})
    return head_msgs, runtime_msgs


def _generate_production_three_way_blind(ctx, assoc_replay, *, persona_file, learned_context_path,
                                         chat_client, out_blind_md, out_blind_map):
    """F1: Full production-assembled 3-way blind review where ONLY natural association varies."""
    persona_text = Path(persona_file).read_text(encoding='utf-8') if persona_file and Path(persona_file).is_file() else ''
    head_learned, runtime_ongoing = _load_production_learned_blocks(learned_context_path)
    by_sid = {s.sample_id: s for s in ctx.samples}

    ctx_hits = assoc_replay['contextual_s5']['_per_sample_assoc_id']
    leg_hits = assoc_replay['legacy_breath8']['_per_sample_assoc_id']

    chosen_sids = []
    # 1) Scenarios where contextual_s5 delivered an association and differs from legacy/off
    for row in assoc_replay['contextual_s5']['_delivered_samples']:
        sid = row['sample_id']
        if sid not in chosen_sids and ctx_hits.get(sid) != leg_hits.get(sid):
            chosen_sids.append(sid)
            if len(chosen_sids) >= 3:
                break
    # 2) Long daily/empathy sharing scenario (verify no hard length cutoff)
    for s in sorted(ctx.samples, key=lambda x: len(x.query), reverse=True):
        if s.category in ('empathy', 'explicit_recall') and s.sample_id not in chosen_sids:
            chosen_sids.append(s.sample_id)
            break
    # 3) Work trap scenario (verify 0 intrusion with full production context)
    for s in ctx.samples:
        if s.category == 'trap' and s.sample_id not in chosen_sids:
            chosen_sids.append(s.sample_id)
            break
    # 4) Short casual/noise boundary where legacy triggered or stayed quiet
    for s in ctx.samples:
        if s.category == 'noise' and (leg_hits.get(s.sample_id) or ctx_hits.get(s.sample_id)) and s.sample_id not in chosen_sids:
            chosen_sids.append(s.sample_id)
            break
    for s in ctx.samples:
        if len(chosen_sids) >= 6:
            break
        if s.category in ('empathy', 'cross_event') and s.sample_id not in chosen_sids:
            chosen_sids.append(s.sample_id)

    rng = random.Random(2026112702)
    blind_lines = [
        '# P6-S5 自然联想三路匿名人工体验对照（完整生产上下文组装版｜给 Lan 盲评）',
        '',
        '> 说明：每组均使用**完整生产请求组装链路**（真实人设 + Learned Context + 关系底座 + 近期对话 + 正常召回判官结果 + Ongoing + 当前消息），',
        '> 三个版本（版本 A / B / C）之间**唯一变量仅为自然联想机制**；顺序已随机打乱，隐藏配置名称与 AI 评价。',
        '> 请按日常真实聊天体感勾选：**最偏好（A/B/C）/ 平局 / 弃权 / 有条件偏好**，并可写一句简短感受。',
        '',
    ]
    blind_map = []
    total_cost = 0.0
    for idx_num, sid in enumerate(chosen_sids, 1):
        sample = by_sid[sid]
        recent = (sample.context or {}).get('recent') or []
        replies = {}
        for arm_id in ('off', 'legacy_breath8', 'contextual_s5'):
            msgs = []
            if persona_text:
                msgs.append({'role': 'system', 'content': persona_text})
            msgs.extend(head_learned)
            for turn_msg in recent[-6:]:
                if isinstance(turn_msg, dict) and turn_msg.get('role') in ('user', 'assistant'):
                    msgs.append({'role': turn_msg['role'], 'content': str(turn_msg.get('content') or '')[:500]})
            # Production finish_recall() output (normal judged recall + arm-specific association)
            msgs.extend(assoc_replay[arm_id]['_per_sample_messages'].get(sid) or [])
            msgs.extend(runtime_ongoing)
            msgs.append({'role': 'user', 'content': sample.query})
            rec = chat_client.call(msgs, tier=2, experiment='P6-S5-ASSOC-PROD-3WAY', max_tokens=280)
            total_cost += float(rec.get('cost_cny') or rec.get('amount_cny') or 0.0)
            replies[arm_id] = {
                'text': (rec.get('text') or rec.get('content') or '').strip() or '(empty)',
                'assoc_id': assoc_replay[arm_id]['_per_sample_assoc_id'].get(sid),
                'latency_ms': rec.get('latency_ms', 0.0),
                'cost_cny': rec.get('cost_cny', rec.get('amount_cny', 0.0)),
            }
        order = ['off', 'legacy_breath8', 'contextual_s5']
        rng.shuffle(order)
        labels = ['A', 'B', 'C']
        mapping = {lab: arm for lab, arm in zip(labels, order)}
        blind_map.append({
            'group': idx_num, 'sample_id': sid, 'category': sample.category,
            'mapping': mapping,
            'assoc_ids': {arm: replies[arm]['assoc_id'] for arm in order},
        })
        blind_lines.append(f'## 场景 {idx_num}（类型：{sample.category}）')
        blind_lines.append('')
        if recent:
            blind_lines.append('**近期上文**：')
            for turn_msg in recent[-2:]:
                if isinstance(turn_msg, dict) and turn_msg.get('role') in ('user', 'assistant'):
                    who = 'Lan' if turn_msg['role'] == 'user' else 'Peter'
                    blind_lines.append(f'- {who}: {str(turn_msg.get("content") or "")[:180]}')
            blind_lines.append('')
        blind_lines.append(f'**Lan 本轮消息**：{sample.query}')
        blind_lines.append('')
        for lab, arm in zip(labels, order):
            blind_lines.append(f'### 版本 {lab}')
            blind_lines.append(replies[arm]['text'])
            blind_lines.append('')
        blind_lines.append('**你的选择**：[ ] 版本 A  [ ] 版本 B  [ ] 版本 C  [ ] 平局  [ ] 弃权  [ ] 有条件偏好（备注：__________）')
        blind_lines.append('')
        blind_lines.append('---')
        blind_lines.append('')

    Path(out_blind_md).parent.mkdir(parents=True, exist_ok=True)
    Path(out_blind_md).write_text('\n'.join(blind_lines), encoding='utf-8')
    Path(out_blind_map).write_text(json.dumps(blind_map, ensure_ascii=False, indent=2), encoding='utf-8')
    return {'groups': len(chosen_sids), 'cost_cny': round(total_cost, 6),
            'production_assembled': True,
            'blind_md': str(out_blind_md), 'blind_map': str(out_blind_map)}


def _eval_rerank_score_gate(ctx, rerank_detail_path):
    detail_file = Path(rerank_detail_path)
    if not detail_file.is_file():
        return {'available': False, 'reason': 'rerank_detail_missing'}
    by_id = {}
    for row in core.read_jsonl(detail_file):
        sid = row.get('sample_id')
        if not sid:
            continue
        ranked = row.get('ranked_ids') or row.get('top_ids') or row.get('top5') or []
        scores = row.get('scores') or {}
        if not scores and row.get('top5') and row.get('top5_scores'):
            scores = {mid: float(sc) for mid, sc in zip(row['top5'], row['top5_scores'])}
        by_id[sid] = {'ranked_ids': ranked, 'scores': scores}
    thresholds = [0.0, 0.10, 0.20, 0.25, 0.30, 0.40, 0.50]
    sweep = []
    for th in thresholds:
        recalls, empty_exp, empty_zero = [], 0, 0
        for sample in ctx.samples:
            row = by_id.get(sample.sample_id)
            if not row:
                continue
            kept, _, _ = filter_by_score_gate(row['ranked_ids'], row['scores'], threshold=th, top_n=8)
            exp = set(sample.expected)
            if exp:
                recalls.append(len(exp & set(kept)) / len(exp))
            else:
                empty_exp += 1
                if not kept:
                    empty_zero += 1
        sweep.append({
            'threshold': th,
            'recall_at_5_gated': round(sum(recalls) / len(recalls), 4) if recalls else 0.0,
            'empty_suppressed_rate': round(empty_zero / empty_exp, 4) if empty_exp else 0.0,
            'empty_remaining_fpr': round(1.0 - (empty_zero / empty_exp), 4) if empty_exp else 0.0,
            'empty_suppressed': f'{empty_zero}/{empty_exp}',
        })
    return {'available': True, 'model': 'Qwen/Qwen3-Reranker-0.6B', 'samples': len(by_id), 'threshold_sweep': sweep}


def _strip_private(d: dict) -> dict:
    return {k: v for k, v in d.items() if not k.startswith('_')}


def main(argv=None):
    p = argparse.ArgumentParser(description='P6-S5 isolated production-pipeline evaluation suite')
    p.add_argument('--db', required=True)
    p.add_argument('--eval', required=True)
    p.add_argument('--learned-context', required=True)
    p.add_argument('--rerank-detail', required=True)
    p.add_argument('--persona-file', default='')
    p.add_argument('--env-file', required=True)
    p.add_argument('--cache-dir', required=True)
    p.add_argument('--results-out', required=True)
    p.add_argument('--blind-md', required=True)
    p.add_argument('--blind-map', required=True)
    p.add_argument('--budget-ledger', required=True)
    p.add_argument('--budget-limit-cny', type=float, default=10.0)
    p.add_argument('--judge-base-url', default='https://api.deepseek.com')
    p.add_argument('--judge-model', default='deepseek-chat')
    p.add_argument('--chat-base-url', default='https://api.deepseek.com')
    p.add_argument('--chat-model', default='deepseek-chat')
    args = p.parse_args(argv)

    from dotenv import dotenv_values
    env = dotenv_values(args.env_file, interpolate=False, encoding='utf-8-sig') if Path(args.env_file).is_file() else {}
    deepseek_key = (os.environ.get('DEEPSEEK_API_KEY') or env.get('DEEPSEEK_API_KEY') or '').strip()
    if not deepseek_key:
        raise SystemExit('DEEPSEEK_API_KEY missing in env file')

    snapshot = core.load_snapshot(Path(args.db))
    known_ids = set(snapshot.memories) | set(snapshot.events)
    samples, eval_report = core.load_eval(Path(args.eval), validate_ids=known_ids)
    core.configure_embedding_price(0.0, source='cache-only run')

    cache_dir = Path(args.cache_dir)
    cache_dir.mkdir(parents=True, exist_ok=True)
    budget = core.BudgetLedger(Path(args.budget_ledger), run_id='p6-s5-review-eval')
    if hasattr(budget, 'total_cap'):
        budget.total_cap = min(float(budget.total_cap), float(args.budget_limit_cny))
    embedder = core.EmbeddingCache(
        cache_dir / 'embeddings.jsonl', model='BAAI/bge-m3',
        base_url='https://api.siliconflow.cn/v1', api_key='', budget=budget, allow_provider=False)
    ctx = Context(snapshot=snapshot, samples=samples, eval_report=eval_report, embedder=embedder,
                  budget=budget, results_dir=Path(args.results_out).parent,
                  detail_dir=cache_dir / 'runs', options={})
    ctx.ensure_query_vectors()

    judge_cache_path = cache_dir / 's5-judge-calls.jsonl'
    judge_cache = {}
    if judge_cache_path.is_file():
        for row in core.read_jsonl(judge_cache_path):
            if isinstance(row.get('key'), str) and not row.get('error'):
                judge_cache[row['key']] = row
    judge_client = JudgeClient(base_url=args.judge_base_url, model=args.judge_model, api_key=deepseek_key)
    subset = _select_stratified(ctx.samples)

    # 1) 4-way retrieval x K matrix (legacy conservative prompt, isolated stopwords & K)
    four_way = [
        _eval_retrieval_and_judge_arm(
            'baseline_k8_sw_off', args.db, ctx, subset,
            stopwords_enabled=False, k=8, prompt_text=LEGACY_CONSERVATIVE_PROMPT,
            use_s5_context=False, judge_client=judge_client,
            judge_cache=judge_cache, cache_path=judge_cache_path),
        _eval_retrieval_and_judge_arm(
            'sw_on_k8', args.db, ctx, subset,
            stopwords_enabled=True, k=8, prompt_text=LEGACY_CONSERVATIVE_PROMPT,
            use_s5_context=False, judge_client=judge_client,
            judge_cache=judge_cache, cache_path=judge_cache_path),
        _eval_retrieval_and_judge_arm(
            'sw_off_k12', args.db, ctx, subset,
            stopwords_enabled=False, k=12, prompt_text=LEGACY_CONSERVATIVE_PROMPT,
            use_s5_context=False, judge_client=judge_client,
            judge_cache=judge_cache, cache_path=judge_cache_path),
        _eval_retrieval_and_judge_arm(
            'sw_on_k12', args.db, ctx, subset,
            stopwords_enabled=True, k=12, prompt_text=LEGACY_CONSERVATIVE_PROMPT,
            use_s5_context=False, judge_client=judge_client,
            judge_cache=judge_cache, cache_path=judge_cache_path),
    ]

    # 2) Trap judge arms + F4 final recommended combined arm (sw_on + K=8 + S5 context judge)
    s5_prompt = prompts.text('memory.judge.prompt')
    trap_judge_s5_k8 = _eval_retrieval_and_judge_arm(
        's5_context_judge_k8', args.db, ctx, subset,
        stopwords_enabled=False, k=8, prompt_text=s5_prompt,
        use_s5_context=True, judge_client=judge_client,
        judge_cache=judge_cache, cache_path=judge_cache_path)
    trap_judge_s5_k12 = _eval_retrieval_and_judge_arm(
        's5_context_judge_k12', args.db, ctx, subset,
        stopwords_enabled=False, k=12, prompt_text=s5_prompt,
        use_s5_context=True, judge_client=judge_client,
        judge_cache=judge_cache, cache_path=judge_cache_path)
    final_candidate_sw_on_k8 = _eval_retrieval_and_judge_arm(
        's5_final_candidate_sw_on_k8', args.db, ctx, subset,
        stopwords_enabled=True, k=8, prompt_text=s5_prompt,
        use_s5_context=True, judge_client=judge_client,
        judge_cache=judge_cache, cache_path=judge_cache_path)

    # 3) F6: Production-pipeline 3-way natural association replay + F1: Full production blind review
    assoc_replay = _replay_association_production(
        args.db, ctx, trap_judge_s5_k8['_per_sample_picked'], stopwords_enabled=False, k=8)
    chat_client = ChatClient(
        cache_path=cache_dir / 's5-chat-calls.jsonl',
        base_url=args.chat_base_url,
        model=args.chat_model,
        api_key=deepseek_key,
        budget=budget,
    )
    blind_meta = _generate_production_three_way_blind(
        ctx, assoc_replay, persona_file=args.persona_file,
        learned_context_path=args.learned_context,
        chat_client=chat_client, out_blind_md=args.blind_md, out_blind_map=args.blind_map)

    # 4) Reranker pre-judge score gate calibration (CNY 0)
    rerank_gate = _eval_rerank_score_gate(ctx, args.rerank_detail)

    report = {
        'generated_at': time.strftime('%Y-%m-%dT%H:%M:%S+08:00'),
        'model': args.judge_model,
        'budget_limit_cny': args.budget_limit_cny,
        'budget_snapshot': budget_snapshot(budget),
        'four_way_retrieval_judge': [_strip_private(a) for a in four_way],
        's5_trap_and_final_candidate': {
            'baseline_k8': _strip_private(four_way[0]),
            's5_context_judge_k8': _strip_private(trap_judge_s5_k8),
            's5_context_judge_k12': _strip_private(trap_judge_s5_k12),
            's5_final_candidate_sw_on_k8': _strip_private(final_candidate_sw_on_k8),
        },
        'association_three_way_production_replay': {
            k_name: _strip_private(v) for k_name, v in assoc_replay.items()
        },
        'association_three_way_blind': {
            'groups': blind_meta['groups'],
            'cost_cny': blind_meta['cost_cny'],
            'production_assembled': blind_meta['production_assembled'],
        },
        'reranker_score_gate_calibration': rerank_gate,
    }
    Path(args.results_out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.results_out).write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps({
        'ok': True,
        'budget': report['budget_snapshot'],
        'final_vs_baseline': {
            'baseline_k8': {k: four_way[0][k] for k in ('final_injected_recall', 'final_injected_precision', 'false_positive_rate', 'trap_fpr', 'outcomes', 'cost_cny')},
            's5_context_judge_k8': {k: trap_judge_s5_k8[k] for k in ('final_injected_recall', 'final_injected_precision', 'false_positive_rate', 'trap_fpr', 'outcomes', 'cost_cny')},
            's5_final_candidate_sw_on_k8': {k: final_candidate_sw_on_k8[k] for k in ('final_injected_recall', 'final_injected_precision', 'false_positive_rate', 'trap_fpr', 'outcomes', 'cost_cny')},
        },
        'assoc_prod_replay': report['association_three_way_production_replay'],
        'blind': report['association_three_way_blind'],
    }, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
