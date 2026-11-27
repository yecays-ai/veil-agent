"""Reproducible AB-COMB end-to-end & time-decay protection audit runner.

Runs three targeted checks against an isolated read-only snapshot without touching production data:
1. Offline (CNY 0): naive time-decay vs importance-protected decay (`imp>=7` zero decay).
2. Tier 1 (36-sample stratified subset): 4-way retrieval + rerank + LLM judge comparison:
   - baseline_prod (K=8, stopwords=off) -> Judge
   - r1b_judge (K=8, stopwords=on) -> Judge
   - r1b_rerank_only8_judge (Qwen3-Reranker Top-8 only) -> Judge
   - r1b_rerank_plus_vec_judge (Qwen3-Reranker Top-8 + Vec-8 fallback) -> Judge
3. Tier 2 (6 stratified scenarios): real multi-mechanism end-to-end reply comparison
   (`R1b + Judge + A3 four-zone + C1 purified HEAD + C2a omit_empty_hint` vs `baseline_prod`).

All provider calls go through BudgetLedger and local caches so reruns cost CNY 0.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from veil.brain.recall.recall_judge import JudgeFailed, parse_choice  # noqa: E402
from recall_lab import core  # noqa: E402
from recall_lab.judge import JudgeClient, PROMPT_CONSERVATIVE, build_messages  # noqa: E402
from recall_lab.retrieval import RetrievalConfig, judge_candidate_pool  # noqa: E402
from recall_lab.runtime import Context, budget_snapshot  # noqa: E402
from recall_lab.tier12 import JUDGE_SUBSET, ledgered_judge_call  # noqa: E402
from recall_lab.tier2_real import ChatClient, blind_eval_pair  # noqa: E402


def select_judge_subset(samples):
    by_cat = {}
    for sample in samples:
        by_cat.setdefault(sample.category, []).append(sample)
    ordered = []
    for category, count in JUDGE_SUBSET.items():
        ordered.extend(by_cat.get(category, [])[:count])
    return ordered


def age_days(mem_created, query_date):
    try:
        d1 = datetime.fromisoformat(str(mem_created)[:10])
        d2 = datetime.fromisoformat(str(query_date)[:10])
        return max(0.0, float((d2 - d1).days))
    except Exception:
        return 0.0


def apply_decay_penalty(score, *, age, importance, mode, half_life_days=14.0, max_penalty=0.08):
    """Apply time-decay penalty to a retrieval score under the selected protection mode."""
    if mode == 'no_decay':
        return score
    penalty = max_penalty * (1.0 - math.exp(-max(0.0, float(age)) / float(half_life_days)))
    if mode == 'naive_time_decay':
        return score - penalty
    if mode == 'protected_decay':
        return score - penalty if int(importance or 0) <= 6 else score
    raise ValueError(f'unknown decay mode: {mode}')


def run_decay_audit(ctx):
    """Offline (CNY 0): check whether naive time decay harms older important shared memories."""
    older_imp_pairs = []
    for sample in ctx.samples:
        q_date = (sample.context or {}).get('time', '') or '2026-11-26'
        for mid in sample.expected:
            mem = ctx.snapshot.memories.get(mid)
            if mem and mem.importance >= 8 and age_days(mem.created, q_date) >= 14:
                older_imp_pairs.append((sample.sample_id, mid, mem.importance))

    def eval_arm(mode):
        hits_all, total_exp = 0, 0
        hits_old_imp, total_old_imp = 0, 0
        per_sample_recalls = []
        cfg = RetrievalConfig(k=30, stopwords=True, importance_weight=0.05)
        for sample in ctx.samples:
            if not sample.expected:
                continue
            q_date = (sample.context or {}).get('time', '') or '2026-11-26'
            items, _ = ctx.retriever.rank(sample.query, ctx.query_vector(sample), cfg, query_time=q_date)
            rescored = []
            for item in items:
                mem = ctx.snapshot.memories.get(item.id)
                sc = item.score
                if mem is not None:
                    sc = apply_decay_penalty(
                        sc, age=age_days(mem.created, q_date), importance=mem.importance, mode=mode)
                rescored.append((sc, item.id))
            rescored.sort(key=lambda pair: pair[0], reverse=True)
            top8 = [mid for _, mid in rescored[:8]]
            exp = set(sample.expected)
            hits = len(exp & set(top8))
            hits_all += hits
            total_exp += len(exp)
            per_sample_recalls.append(hits / len(exp))
            for mid in exp:
                mem = ctx.snapshot.memories.get(mid)
                if mem and mem.importance >= 8 and age_days(mem.created, q_date) >= 14:
                    total_old_imp += 1
                    if mid in top8:
                        hits_old_imp += 1
        return {
            'mode': mode,
            'recall_at_8': round(sum(per_sample_recalls) / len(per_sample_recalls), 4) if per_sample_recalls else 0.0,
            'expected_hits': f'{hits_all}/{total_exp}',
            'older_imp8_ge14d_hits': f'{hits_old_imp}/{total_old_imp}',
            'older_imp8_ge14d_recall': round(hits_old_imp / total_old_imp, 4) if total_old_imp else None,
        }

    return {
        'older_imp8_ge14d_pairs_n': len(older_imp_pairs),
        'arms': [eval_arm('no_decay'), eval_arm('naive_time_decay'), eval_arm('protected_decay')],
    }


def eval_judge_arm(arm_name, ctx, subset, cand_fn, judge_client, judge_cache, cache_path):
    recalls, precs, latencies, rows = [], [], [], []
    empty_exp, empty_ok = 0, 0
    in_tok, out_tok, paid_calls, cached_calls = 0, 0, 0, 0
    cost_cny = 0.0

    for sample in subset:
        cands = [cid for cid in cand_fn(sample) if cid in ctx.snapshot.memories]
        messages, letters = build_messages(
            sample.query, [(cid, '') for cid in cands], ctx.snapshot,
            card='300', prompt=PROMPT_CONSERVATIVE, wake=False)
        rec = ledgered_judge_call(
            ctx, judge_client, tier=1, experiment='AB-COMB',
            messages=messages, timeout=4.0, cache_path=cache_path, cache=judge_cache)
        if rec.get('cached'):
            cached_calls += 1
        else:
            paid_calls += 1
        cost_cny += float(rec.get('amount_cny') or 0.0)
        in_tok += int(rec.get('prompt_tokens') or 0)
        out_tok += int(rec.get('completion_tokens') or 0)
        latencies.append(float(rec.get('latency_ms') or 0.0))

        label_map = {letter: cid for letter, cid in zip(letters, cands)}
        picked_ids = []
        if not rec.get('error'):
            try:
                picked_ids = parse_choice(rec.get('text') or '', label_map)
            except JudgeFailed:
                picked_ids = []
        exp = set(sample.expected)
        if exp:
            hits = len(exp & set(picked_ids))
            recalls.append(hits / len(exp))
            precs.append(hits / len(picked_ids) if picked_ids else 0.0)
        else:
            empty_exp += 1
            if not picked_ids:
                empty_ok += 1
        rows.append({'sample_id': sample.sample_id, 'category': sample.category,
                     'candidates': cands, 'picked': picked_ids})

    lat_s = sorted(latencies)
    p50 = lat_s[len(lat_s) // 2] if lat_s else 0.0
    p95 = lat_s[min(len(lat_s) - 1, int(len(lat_s) * 0.95))] if lat_s else 0.0
    return {
        'arm': arm_name,
        'samples': len(subset),
        'judge_recall': round(sum(recalls) / len(recalls), 4) if recalls else 0.0,
        'judge_precision': round(sum(precs) / len(precs), 4) if precs else 0.0,
        'false_positive_rate': round(1.0 - (empty_ok / empty_exp), 4) if empty_exp else 0.0,
        'empty_correct': empty_ok,
        'empty_expected_n': empty_exp,
        'input_tokens': in_tok,
        'output_tokens': out_tok,
        'latency_p50_ms': round(p50, 1),
        'latency_p95_ms': round(p95, 1),
        'calls_paid': paid_calls,
        'calls_cached': cached_calls,
        'cost_cny': round(cost_cny, 6),
        'rows': rows,
    }


def build_parser():
    parser = argparse.ArgumentParser(description='Reproducible AB-COMB E2E & decay protection audit')
    parser.add_argument('--db', required=True, help='isolated brain.db snapshot (read-only)')
    parser.add_argument('--eval', required=True, help='eval-v2 JSONL (local copy)')
    parser.add_argument('--learned-context', required=True, help='isolated learned-context.json')
    parser.add_argument('--rerank-detail', required=True, help='cached Qwen3-Reranker detail JSONL')
    parser.add_argument('--persona-file', help='optional read-only local persona markdown')
    parser.add_argument('--env-file', help='optional read-only .env file for API keys')
    parser.add_argument('--cache-dir', required=True, help='local cache directory')
    parser.add_argument('--results-dir', required=True, help='results output directory')
    parser.add_argument('--detail-dir', required=True, help='gitignored detail output directory')
    parser.add_argument('--budget-ledger', required=True, help='append-only budget ledger JSONL')
    parser.add_argument('--judge-base-url', default='https://api.deepseek.com')
    parser.add_argument('--judge-model', default='deepseek-flash')
    parser.add_argument('--offline-only', action='store_true', help='run only the CNY 0 decay audit')
    return parser


def _resolve_key(env_file, name, default=''):
    val = (os.environ.get(name) or '').strip()
    if val:
        return val
    if env_file and Path(env_file).is_file():
        from dotenv import dotenv_values
        values = dotenv_values(env_file, interpolate=False, encoding='utf-8-sig')
        return str(values.get(name) or default).strip()
    return default


def main(argv=None):
    args = build_parser().parse_args(argv)
    snapshot = core.load_snapshot(Path(args.db))
    known_ids = set(snapshot.memories) | set(snapshot.events)
    samples, eval_report = core.load_eval(Path(args.eval), validate_ids=known_ids)
    core.configure_embedding_price(0.0, source='cache-only run')
    budget = core.BudgetLedger(Path(args.budget_ledger), run_id='ab-comb-e2e')
    cache_dir = Path(args.cache_dir)
    embedder = core.EmbeddingCache(
        cache_dir / 'embeddings.jsonl', model='BAAI/bge-m3',
        base_url='https://api.siliconflow.cn/v1', api_key='', budget=budget, allow_provider=False)
    ctx = Context(snapshot=snapshot, samples=samples, eval_report=eval_report, embedder=embedder,
                  budget=budget, results_dir=Path(args.results_dir), detail_dir=Path(args.detail_dir), options={})
    ctx.ensure_query_vectors()

    decay_out = run_decay_audit(ctx)
    if args.offline_only:
        print(json.dumps(decay_out, ensure_ascii=False, indent=2))
        return 0

    ds_key = _resolve_key(args.env_file, 'DEEPSEEK_API_KEY')
    ds_url = _resolve_key(args.env_file, 'DEEPSEEK_BASE_URL', args.judge_base_url)
    judge_client = JudgeClient(base_url=ds_url, model=args.judge_model, api_key=ds_key)
    jcache_path = cache_dir / 'judge.jsonl'
    jcache = {}
    if jcache_path.is_file():
        for row in core.read_jsonl(jcache_path):
            if isinstance(row.get('key'), str):
                jcache[row['key']] = row

    subset = select_judge_subset(samples)
    rerank_map = {row['sample_id']: row.get('top_ids', [])[:8]
                  for row in core.read_jsonl(Path(args.rerank_detail))}
    cfg_base = RetrievalConfig(k=8, stopwords=False, importance_weight=0.05)
    cfg_r1b = RetrievalConfig(k=8, stopwords=True, importance_weight=0.05)

    cands_base = lambda s: [mid for mid, _ in judge_candidate_pool(ctx.retriever, s.query, ctx.query_vector(s), config=cfg_base)]
    cands_r1b = lambda s: [mid for mid, _ in judge_candidate_pool(ctx.retriever, s.query, ctx.query_vector(s), config=cfg_r1b)]
    cands_rr8 = lambda s: (rerank_map.get(s.sample_id, [])[:8] or cands_r1b(s)[:8])

    def cands_rr8_vec(sample):
        rr = rerank_map.get(sample.sample_id, [])[:8]
        vec_scores = ctx.retriever.vector_scores(ctx.query_vector(sample))
        taken = set(rr)
        extra = []
        for mid in sorted(vec_scores, key=lambda key: (-vec_scores[key], key)):
            if len(extra) >= 8:
                break
            if mid not in taken:
                extra.append(mid)
                taken.add(mid)
        return rr + extra

    arm0 = eval_judge_arm('baseline_prod_judge (K=8, stopwords=off)', ctx, subset, cands_base, judge_client, jcache, jcache_path)
    arm1 = eval_judge_arm('combo_r1b_judge (K=8, stopwords=on)', ctx, subset, cands_r1b, judge_client, jcache, jcache_path)
    arm2 = eval_judge_arm('combo_r1b_rerank_only8_judge (Rerank Top-8 only -> Judge)', ctx, subset, cands_rr8, judge_client, jcache, jcache_path)
    arm3 = eval_judge_arm('combo_r1b_rerank_plus_vec_judge (Rerank Top-8 + Vec-8 -> Judge)', ctx, subset, cands_rr8_vec, judge_client, jcache, jcache_path)

    chat = ChatClient(cache_dir / 'tier2_chat.jsonl', base_url=ds_url, model=args.judge_model,
                      api_key=ds_key, budget=budget, tier=2, experiment='AB-COMB-E2E', max_tokens=400)
    persona_head = (Path(args.persona_file).read_text(encoding='utf-8-sig').strip()
                    if args.persona_file and Path(args.persona_file).is_file()
                    else 'You are a warm, concise AI companion.')
    lc_json = json.loads(Path(args.learned_context).read_text(encoding='utf-8'))
    lc_lines = [f'[{sec}]\n{lc_json[sec].strip()}'
                for sec in ('facts', 'growth') if isinstance(lc_json.get(sec), str) and lc_json[sec].strip()]
    learned_head = '\n\n'.join(lc_lines)
    shared_lc = learned_head[:1200]
    four_zone_lc = learned_head + '\n\n[relationship]\n- Confirmed shared agreements preserved under independent 1500B budget.'
    cap_block = '[capabilities] desktop, memory_read, memory_timeline, web_search, calendar, email, notes'

    e2e_ids = ['S1-001', 'S1-006', 'S2-001', 'S3-003', 'S4-001', 'S5-001']
    arm0_picks = {row['sample_id']: row['picked'] for row in arm0['rows']}
    arm1_picks = {row['sample_id']: row['picked'] for row in arm1['rows']}

    e2e_records, wins_combo, wins_base, ties = [], 0, 0, 0
    for sid in e2e_ids:
        sample = ctx.samples_by_id[sid]
        m_base = [snapshot.memories[mid] for mid in arm0_picks.get(sid, []) if mid in snapshot.memories]
        m_comb = [snapshot.memories[mid] for mid in arm1_picks.get(sid, []) if mid in snapshot.memories]

        def _mem_block(mems, omit_empty):
            if not mems:
                return '' if omit_empty else '[memory.recall.empty] No specific memory recalled for this turn.'
            return '\n\n'.join(f'[{m.title}]\n{m.body[:900]}' for m in mems[:6])

        msgs_base = [{'role': 'system', 'content': f'{persona_head}\n\n{cap_block}\n\n{shared_lc}'}]
        mb_base = _mem_block(m_base, omit_empty=False)
        if mb_base:
            msgs_base.append({'role': 'system', 'content': mb_base})
        msgs_base.append({'role': 'user', 'content': sample.query})

        msgs_comb = [{'role': 'system', 'content': f'{persona_head}\n\n{four_zone_lc}'}]
        mb_comb = _mem_block(m_comb, omit_empty=True)
        if mb_comb:
            msgs_comb.append({'role': 'system', 'content': mb_comb})
        msgs_comb.append({'role': 'user', 'content': f'{cap_block}\n\n{sample.query}'})

        r_base = chat.call(msgs_base, experiment='AB-COMB-E2E', tier=2)
        r_comb = chat.call(msgs_comb, experiment='AB-COMB-E2E', tier=2)
        all_mems = {m.id: m for m in (m_base + m_comb)}
        ref_ctx = four_zone_lc + '\n\n' + '\n\n'.join(f'[{m.title}]\n{m.body[:1200]}' for m in all_mems.values())
        ev = blind_eval_pair(
            chat, experiment='AB-COMB-E2E', scenario_id=f'AB-COMB-E2E-{sid}',
            user_message=sample.query, reference_context=ref_ctx,
            arm_x_name='baseline_prod_e2e', arm_x_reply=r_base['text'],
            arm_y_name='combo_s3_s5_s7_e2e', arm_y_reply=r_comb['text'],
            is_work=(sample.category == 'trap'))
        winner = ev.get('winner')
        if winner == 'combo_s3_s5_s7_e2e':
            wins_combo += 1
        elif winner == 'baseline_prod_e2e':
            wins_base += 1
        else:
            ties += 1
        scores = ev.get('scores') or {}
        e2e_records.append({
            'sample_id': sid, 'category': sample.category,
            'baseline_picks': len(m_base), 'combo_picks': len(m_comb),
            'baseline_prompt_tokens': r_base['prompt_tokens'], 'combo_prompt_tokens': r_comb['prompt_tokens'],
            'winner_arm': winner,
            'hallucination_baseline': bool((scores.get('baseline_prod_e2e') or {}).get('hallucinated_memory')),
            'hallucination_combo': bool((scores.get('combo_s3_s5_s7_e2e') or {}).get('hallucinated_memory')),
        })
    chat.close()

    comb_path = Path(args.results_dir) / 'ab_comb.json'
    comb_data = json.loads(comb_path.read_text(encoding='utf-8')) if comb_path.is_file() else {}
    comb_data['e2e_judge_4way'] = [{k: v for k, v in a.items() if k != 'rows'} for a in (arm0, arm1, arm2, arm3)]
    comb_data['e2e_multi_mechanism_replies'] = {
        'scenarios': len(e2e_ids), 'combo_wins': wins_combo, 'baseline_wins': wins_base, 'ties': ties,
        'combo_hallucinations': sum(1 for r in e2e_records if r['hallucination_combo']),
        'baseline_hallucinations': sum(1 for r in e2e_records if r['hallucination_baseline']),
        'records': e2e_records,
    }
    comb_data['decay_protection_audit'] = decay_out
    comb_path.write_text(json.dumps(comb_data, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
