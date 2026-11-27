"""P6-S5 end-to-end isolated evaluation suite (retrieval x K, trap judge, 3-way association, rerank gate).

Runs strictly on isolated read-only copies (never touches formal Peter runtime or data).
Uses DeepSeek API through BudgetLedger with a hard CNY ceiling (default CNY 10.00) and
reuses cached query embeddings and rerank details to minimize API spend.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import random
import sqlite3
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
LAB_ROOT = Path(__file__).resolve().parents[1]
for _p in (str(ROOT), str(LAB_ROOT)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from veil.brain.recall.brain_retrieval import BrainIndex, load_settings as load_ret_settings  # noqa: E402
from veil.brain.recall.context_signal import analyze as analyze_context, format_recent_context  # noqa: E402
from veil.brain.recall.natural_association import choose as choose_association  # noqa: E402
from veil.brain.recall.recall_judge import JudgeFailed, card_text, labels_for, parse_choice  # noqa: E402
from veil.brain.recall.recall_relevance import cosine  # noqa: E402
from veil.brain.recall.reranker_contract import filter_by_score_gate  # noqa: E402
from veil import prompts  # noqa: E402

from recall_lab import core  # noqa: E402
from recall_lab.judge import JudgeClient  # noqa: E402

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
from recall_lab.runtime import Context, budget_snapshot  # noqa: E402
from recall_lab.tier12 import JUDGE_SUBSET, ledgered_judge_call  # noqa: E402
from recall_lab.tier2_real import ChatClient  # noqa: E402


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
    idx = BrainIndex(db_path, weight_vector=0.65, weight_keyword=0.35, stopwords_enabled=stopwords_enabled)
    idx.refresh()

    ret_recalls, ret_p5s, ret_ndcgs = [], [], []
    j_recalls, j_precs, latencies = [], [], []
    empty_exp, empty_ok = 0, 0
    trap_exp, trap_ok = 0, 0
    noise_exp, noise_ok = 0, 0
    in_tok, out_tok, paid_calls, cached_calls = 0, 0, 0, 0
    cost_cny = 0.0
    injected_total = 0
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

        picked = []
        if not rec.get('error'):
            try:
                picked = parse_choice(rec.get('text') or '', letter_map)
            except JudgeFailed:
                picked = []
        injected_total += len(picked)
        cat_stat = by_cat.setdefault(sample.category, {'n': 0, 'recalls': [], 'precs': [], 'empty_exp': 0, 'empty_ok': 0})
        cat_stat['n'] += 1
        if exp:
            hits = len(exp & set(picked))
            r_val = hits / len(exp)
            p_val = hits / len(picked) if picked else 0.0
            j_recalls.append(r_val)
            j_precs.append(p_val)
            cat_stat['recalls'].append(r_val)
            cat_stat['precs'].append(p_val)
        else:
            empty_exp += 1
            cat_stat['empty_exp'] += 1
            if not picked:
                empty_ok += 1
                cat_stat['empty_ok'] += 1
            if sample.category == 'trap':
                trap_exp += 1
                if not picked:
                    trap_ok += 1
            elif sample.category == 'noise':
                noise_exp += 1
                if not picked:
                    noise_ok += 1

    lat_s = sorted(latencies)
    p50 = lat_s[len(lat_s) // 2] if lat_s else 0.0
    p95 = lat_s[min(len(lat_s) - 1, int(len(lat_s) * 0.95))] if lat_s else 0.0
    cat_summary = {}
    for cat, st in by_cat.items():
        cat_summary[cat] = {
            'n': st['n'],
            'judge_recall': round(sum(st['recalls']) / len(st['recalls']), 4) if st['recalls'] else None,
            'judge_precision': round(sum(st['precs']) / len(st['precs']), 4) if st['precs'] else None,
            'fpr': round(1.0 - st['empty_ok'] / st['empty_exp'], 4) if st['empty_exp'] else None,
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
        'false_positive_rate': round(1.0 - empty_ok / empty_exp, 4) if empty_exp else 0.0,
        'trap_fpr': round(1.0 - trap_ok / trap_exp, 4) if trap_exp else None,
        'noise_fpr': round(1.0 - noise_ok / noise_exp, 4) if noise_exp else None,
        'empty_correct': f'{empty_ok}/{empty_exp}',
        'injected_total': injected_total,
        'input_tokens': in_tok,
        'output_tokens': out_tok,
        'latency_p50_ms': round(p50, 1),
        'latency_p95_ms': round(p95, 1),
        'calls_paid': paid_calls,
        'calls_cached': cached_calls,
        'cost_cny': round(cost_cny, 6),
        'by_category': cat_summary,
    }


def _eval_association_offline(db_path, ctx):
    """Compare off / legacy breath(8) / new contextual bounded multi-source association across all 73 samples."""
    idx = BrainIndex(db_path, weight_vector=0.65, weight_keyword=0.35, stopwords_enabled=False)
    idx.refresh()
    # Build breath(8) pool (legacy) and multi-source pool (contextual)
    visible = [ctx.snapshot.memories[mid] for mid in ctx.snapshot.visible_ids]
    breath8 = sorted(visible, key=lambda m: (0 if m.pinned else 1, -m.importance, m.created or ''), reverse=False)
    # Match breath(): pinned DESC, importance DESC, created DESC LIMIT 8
    breath8 = sorted(visible, key=lambda m: (1 if m.pinned else 0, m.importance, m.created or ''), reverse=True)[:8]
    breath_cands = [(m.id, f'[bucket_id:{m.id}] {m.title}\n{m.body}') for m in breath8]
    metas = {m.id: {'importance': m.importance, 'pinned': 'true' if m.pinned else 'false', 'title': m.title}
             for m in visible}

    def run_mode(mode_name, contextual_enabled, min_sim=0.34):
        last_shown = {}
        prev = None
        triggered = []
        work_intrusions = 0
        reasons = {}
        unique_ids = set()
        imp7_ids = set()
        ordinary_ids = set()
        for turn, sample in enumerate(ctx.samples, 1):
            recent = (sample.context or {}).get('recent') or []
            if mode_name == 'off':
                reasons['disabled'] = reasons.get('disabled', 0) + 1
                continue
            if contextual_enabled:
                high = [m for m in visible if m.pinned or m.importance >= 7]
                high.sort(key=lambda m: (0 if m.pinned else 1, last_shown.get(m.id, -1), -m.importance, m.id))
                ord_m = [m for m in visible if not (m.pinned or m.importance >= 7) and m.importance >= 4]
                ord_m.sort(key=lambda m: (last_shown.get(m.id, -1), m.id))
                pool_m = high[:22] + ord_m[:10]
                cands = [(m.id, f'[bucket_id:{m.id}] {m.title}\n{m.body}') for m in pool_m]
                qvec = ctx.query_vector(sample)
                sims = idx.vector_similarities(qvec, [m.id for m in pool_m]) if qvec else {}
                cfg = {'association_enabled': True, 'association_contextual_enabled': True,
                       'association_min_similarity': min_sim, 'association_cooldown_turns': 8}
                choice = choose_association(sample.query, cands, metas, last_shown, turn, set(),
                                            cfg, previous=prev, similarities=sims, recent=recent)
            else:
                cfg = {'association_enabled': True, 'association_contextual_enabled': False,
                       'association_cooldown_turns': 8, 'association_light_chars': 40}
                choice = choose_association(sample.query, breath_cands, metas, last_shown, turn, set(),
                                            cfg, previous=prev)
            if choice['key']:
                mid = choice['key']
                last_shown[mid] = turn
                prev = {'key': mid, 'turn': turn, 'repeat': (prev['repeat'] + 1 if prev and prev['key'] == mid else 1)}
                unique_ids.add(mid)
                mem = ctx.snapshot.memories.get(mid)
                if mem and (mem.pinned or mem.importance >= 7):
                    imp7_ids.add(mid)
                else:
                    ordinary_ids.add(mid)
                if sample.category == 'trap':
                    work_intrusions += 1
                triggered.append({
                    'sample_id': sample.sample_id, 'category': sample.category,
                    'picked_id': mid, 'similarity': choice.get('similarity'),
                    'score': choice.get('score'), 'context_mode': choice.get('context_mode'),
                })
            else:
                r = choice.get('reason') or 'none'
                reasons[r] = reasons.get(r, 0) + 1
        return {
            'mode': mode_name,
            'total_samples': len(ctx.samples),
            'triggered_count': len(triggered),
            'unique_memories_covered': len(unique_ids),
            'long_tail_imp7_covered': len(imp7_ids),
            'ordinary_covered': len(ordinary_ids),
            'work_trap_intrusions': work_intrusions,
            'suppression_reasons': reasons,
            'triggered_samples': triggered,
        }

    return {
        'off': run_mode('off', False),
        'legacy_breath8': run_mode('legacy_breath8', False),
        'contextual_s5': run_mode('contextual_s5', True, min_sim=0.52),
    }


def _eval_rerank_score_gate(ctx, rerank_detail_path):
    """Calibrate pre-judge Qwen3-Reranker-0.6B score threshold on cached S4 AB-R2 data (CNY 0)."""
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
            ranked = row.get('ranked_ids') or []
            scores = row.get('scores') or {}
            kept, _, _ = filter_by_score_gate(ranked, scores, threshold=th, top_n=8)
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


def _generate_three_way_blind_replies(ctx, assoc_offline, *, persona_file, chat_client,
                                      out_blind_md, out_blind_map):
    """Generate 3-way real-persona replies (off / legacy / contextual_s5) on discriminative scenarios."""
    persona_text = Path(persona_file).read_text(encoding='utf-8') if persona_file and Path(persona_file).is_file() else ''
    # Pick 6 discriminative scenarios covering empathy, casual/noise, work trap, and long-tail contextual hits.
    ctx_hits = {row['sample_id']: row['picked_id'] for row in assoc_offline['contextual_s5']['triggered_samples']}
    leg_hits = {row['sample_id']: row['picked_id'] for row in assoc_offline['legacy_breath8']['triggered_samples']}
    by_sid = {s.sample_id: s for s in ctx.samples}

    chosen_sids = []
    # 1) Scenarios where contextual triggered a long-tail hit and legacy did not (or different)
    for row in assoc_offline['contextual_s5']['triggered_samples']:
        if row['category'] in ('empathy', 'explicit_recall', 'cross_event') and row['sample_id'] not in chosen_sids:
            chosen_sids.append(row['sample_id'])
            if len(chosen_sids) >= 3:
                break
    # 2) A work trap scenario (verify 0 intrusion)
    for s in ctx.samples:
        if s.category == 'trap' and s.sample_id not in chosen_sids:
            chosen_sids.append(s.sample_id)
            break
    # 3) A noise/short casual scenario (verify natural restraint)
    for s in ctx.samples:
        if s.category == 'noise' and (s.sample_id in leg_hits or s.sample_id in ctx_hits) and s.sample_id not in chosen_sids:
            chosen_sids.append(s.sample_id)
            break
    # 4) Another empathy scenario if < 6
    for s in ctx.samples:
        if len(chosen_sids) >= 6:
            break
        if s.category == 'empathy' and s.sample_id not in chosen_sids:
            chosen_sids.append(s.sample_id)

    rng = random.Random(20261127)
    blind_lines = [
        '# P6-S5 自然联想三路匿名人工体验对照（给 Lan 盲评）',
        '',
        '> 说明：每组包含 3 个匿名版本（版本 A / B / C），顺序已随机打乱，隐藏了配置名称与任何 AI 评价。',
        '> 请按日常真实聊天体感勾选：**最偏好（A/B/C）/ 平局 / 弃权 / 有条件偏好**，并可写一句简短感受。',
        '',
    ]
    blind_map = []
    total_cost = 0.0
    for idx_num, sid in enumerate(chosen_sids, 1):
        sample = by_sid[sid]
        recent = (sample.context or {}).get('recent') or []
        arms_cfg = [
            ('off', None),
            ('legacy_breath8', leg_hits.get(sid)),
            ('contextual_s5', ctx_hits.get(sid)),
        ]
        replies = {}
        for arm_id, assoc_mid in arms_cfg:
            msgs = []
            if persona_text:
                msgs.append({'role': 'system', 'content': persona_text})
            if assoc_mid and assoc_mid in ctx.snapshot.memories:
                mem = ctx.snapshot.memories[assoc_mid]
                assoc_block = (
                    'Natural association (optional background; only weave in if it genuinely fits this moment, '
                    'never force it):\n'
                    f'[bucket_id:{mem.id}] {mem.title}\n{mem.body[:600]}'
                )
                msgs.append({'role': 'system', 'content': assoc_block})
            for turn_msg in recent[-4:]:
                if isinstance(turn_msg, dict) and turn_msg.get('role') in ('user', 'assistant'):
                    msgs.append({'role': turn_msg['role'], 'content': str(turn_msg.get('content') or '')[:400]})
            msgs.append({'role': 'user', 'content': sample.query})
            rec = chat_client.call(msgs, tier=2, experiment='P6-S5-ASSOC-3WAY', max_tokens=280)
            total_cost += float(rec.get('cost_cny') or rec.get('amount_cny') or 0.0)
            replies[arm_id] = {
                'text': (rec.get('text') or rec.get('content') or '').strip() or '(empty)',
                'assoc_id': assoc_mid,
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
            'blind_md': str(out_blind_md), 'blind_map': str(out_blind_map)}


def main(argv=None):
    p = argparse.ArgumentParser(description='P6-S5 isolated production-pipeline evaluation suite')
    p.add_argument('--db', required=True)
    p.add_argument('--eval', required=True)
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
    budget = core.BudgetLedger(Path(args.budget_ledger), run_id='p6-s5-eval')
    if hasattr(budget, 'total_cap'):
        budget.total_cap = min(float(budget.total_cap), float(args.budget_limit_cny))
    elif hasattr(budget, 'cap'):
        budget.cap = min(float(budget.cap), float(args.budget_limit_cny))
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
            if isinstance(row.get('key'), str):
                judge_cache[row['key']] = row
    judge_client = JudgeClient(base_url=args.judge_base_url, model=args.judge_model, api_key=deepseek_key)
    subset = _select_stratified(ctx.samples)

    # 1) 4-way retrieval x K matrix (using legacy conservative prompt so stopwords & K are isolated)
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

    # 2) Trap & situational context judge optimization (same K=8, sw=off vs new S5 judge prompt + recent context)
    s5_prompt = prompts.text('memory.judge.prompt')
    trap_judge_s5 = _eval_retrieval_and_judge_arm(
        's5_context_judge_k8', args.db, ctx, subset,
        stopwords_enabled=False, k=8, prompt_text=s5_prompt,
        use_s5_context=True, judge_client=judge_client,
        judge_cache=judge_cache, cache_path=judge_cache_path)
    trap_judge_s5_k12 = _eval_retrieval_and_judge_arm(
        's5_context_judge_k12', args.db, ctx, subset,
        stopwords_enabled=False, k=12, prompt_text=s5_prompt,
        use_s5_context=True, judge_client=judge_client,
        judge_cache=judge_cache, cache_path=judge_cache_path)

    # 3) 3-way natural association offline + real-persona blind review replies
    assoc_offline = _eval_association_offline(args.db, ctx)
    chat_client = ChatClient(
        cache_path=cache_dir / 's5-chat-calls.jsonl',
        base_url=args.chat_base_url,
        model=args.chat_model,
        api_key=deepseek_key,
        budget=budget,
    )
    blind_meta = _generate_three_way_blind_replies(
        ctx, assoc_offline, persona_file=args.persona_file,
        chat_client=chat_client, out_blind_md=args.blind_md, out_blind_map=args.blind_map)

    # 4) Reranker pre-judge score gate calibration (offline, CNY 0)
    rerank_gate = _eval_rerank_score_gate(ctx, args.rerank_detail)

    report = {
        'generated_at': time.strftime('%Y-%m-%dT%H:%M:%S+08:00'),
        'model': args.judge_model,
        'budget_limit_cny': args.budget_limit_cny,
        'budget_snapshot': budget_snapshot(budget),
        'four_way_retrieval_judge': four_way,
        's5_trap_judge_comparison': {
            'baseline_k8': four_way[0],
            's5_context_judge_k8': trap_judge_s5,
            's5_context_judge_k12': trap_judge_s5_k12,
        },
        'association_three_way_offline': {
            k: {kk: vv for kk, vv in v.items() if kk != 'triggered_samples'}
            for k, v in assoc_offline.items()
        },
        'association_three_way_blind': {'groups': blind_meta['groups'], 'cost_cny': blind_meta['cost_cny']},
        'reranker_score_gate_calibration': rerank_gate,
    }
    Path(args.results_out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.results_out).write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps({
        'ok': True,
        'results_out': args.results_out,
        'budget': report['budget_snapshot'],
        'four_way': [{k: row[k] for k in ('arm', 'retrieval_recall_at_k', 'judge_recall', 'judge_precision', 'false_positive_rate', 'trap_fpr', 'cost_cny')} for row in four_way],
        'trap_s5_k8': {k: trap_judge_s5[k] for k in ('arm', 'judge_recall', 'judge_precision', 'false_positive_rate', 'trap_fpr', 'cost_cny')},
        'trap_s5_k12': {k: trap_judge_s5_k12[k] for k in ('arm', 'judge_recall', 'judge_precision', 'false_positive_rate', 'trap_fpr', 'cost_cny')},
        'assoc_offline': report['association_three_way_offline'],
        'rerank_gate_best': rerank_gate.get('threshold_sweep', [])[:5],
    }, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
