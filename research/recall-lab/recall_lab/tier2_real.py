"""Real-model Tier 1/2 experiments and anonymized AI-assisted blind evaluation for P6-S4.

Covers:
  - AB-R1a judge net-benefit check (K=8 vs 12 vs 20) & judge failure attribution
  - AB-A1a / AB-A1b real reply generation & blind evaluation (chat + idle/task autonomy)
  - AB-C1 realistic production-shaped HEAD prefix cache test (using veil_agent.self_facts)
  - AB-C2a real multi-step tool loop hallucination=0 & token test
  - AB-C2b real light-chat Top-2+1 vs uncapped naturalness & token test
  - AB-C3 real long-event full story vs summary+pointer WITH live memory_timeline tool calling
  - AB-E1 chronological forward-time cognitive consolidation replay (T1 -> T2 -> T3)

All paid calls pass through BudgetLedger (reserve before dispatch, settle after response) and are
cached locally under `.local-tmp/s4/cache/` so reruns cost CNY 0. Private chat/memory/reply texts
are written ONLY to gitignored `.local-tmp/s4/` (never committed to git).
"""
from __future__ import annotations

import hashlib
import json
import math
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace

from . import core
from .judge import (JudgeClient, PROMPT_CONSERVATIVE, build_messages, card_300, parse_picked)
from .retrieval import (RetrievalConfig, association_choice, judge_candidate_pool,
                        production_breath_pool, stratified_long_tail_pool, wake_association_choice)
from .runtime import budget_snapshot


# ---------------------------------------------------------------------------------------
# Cached, budget-guarded Chat & Tool client (deepseek-flash)
# ---------------------------------------------------------------------------------------
class ChatClient:
    """Budget-guarded, cached OpenAI-compatible chat/tool client for Tier 2 real generation."""

    def __init__(self, cache_path, *, base_url, model, api_key, budget, tier=2,
                 experiment='TIER2', max_tokens=512, timeout=25.0):
        self.path = Path(cache_path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.base_url = base_url
        self.model = model
        self._api_key = api_key
        self.budget = budget
        self.tier = tier
        self.experiment = experiment
        self.max_tokens = max_tokens
        self.timeout = float(timeout)
        self._client = None
        self._lock = threading.Lock()
        self._cache = {}
        if self.path.is_file():
            for row in core.read_jsonl(self.path):
                if isinstance(row.get('key'), str):
                    self._cache[row['key']] = row

    def _ensure(self):
        if self._client is None:
            from openai import OpenAI
            self._client = OpenAI(api_key=self._api_key, base_url=self.base_url,
                                  timeout=self.timeout, max_retries=0)
        return self._client

    def close(self):
        if self._client is not None:
            try:
                self._client.close()
            except Exception:
                pass

    def call(self, messages, *, experiment=None, tier=None, max_tokens=None,
             json_output=False, tools=None, cache_bust=''):
        exp = experiment or self.experiment
        t = self.tier if tier is None else tier
        mt = max_tokens or self.max_tokens
        key_payload = json.dumps({
            'model': self.model, 'messages': messages, 'max_tokens': mt,
            'json_output': bool(json_output), 'tools': tools or [], 'bust': cache_bust,
        }, ensure_ascii=False, sort_keys=True)
        key = core.sha256_text(key_payload)
        with self._lock:
            if key in self._cache:
                rec = dict(self._cache[key])
                rec['cached'] = True
                rec['cost_cny'] = 0.0
                return rec
        size_bytes = len(json.dumps(messages, ensure_ascii=False).encode('utf-8'))
        if tools:
            size_bytes += len(json.dumps(tools, ensure_ascii=False).encode('utf-8'))
        est_in = max(256, math.ceil(size_bytes / 2.0))
        reserve = core.paid_upper_bound(est_in, mt, model='judge')
        ticket = self.budget.reserve(
            t, exp, provider='deepseek-chat', model=self.model, kind='chat',
            reserve_cny=reserve, detail={'estimated_input_tokens': est_in, 'max_output_tokens': mt})
        client = self._ensure()
        kwargs = {
            'model': self.model, 'messages': messages, 'max_tokens': mt,
            'stream': False, 'extra_body': {'thinking': {'type': 'disabled'}},
        }
        if json_output:
            kwargs['response_format'] = {'type': 'json_object'}
        if tools:
            kwargs['tools'] = tools
            kwargs['tool_choice'] = 'auto'
        started = time.perf_counter()
        try:
            resp = client.chat.completions.create(**kwargs)
        except Exception as exc:
            elapsed = round((time.perf_counter() - started) * 1000.0, 2)
            self.budget.settle(ticket, reserve, accounting='failed-conservative', note=type(exc).__name__)
            return {'key': key, 'text': '', 'tool_calls': [], 'prompt_tokens': 0,
                    'completion_tokens': 0, 'cached_tokens': 0, 'latency_ms': elapsed,
                    'cost_cny': reserve, 'cached': False,
                    'error': f'{type(exc).__name__}: {str(exc)[:200]}'}
        elapsed = round((time.perf_counter() - started) * 1000.0, 2)
        usage = getattr(resp, 'usage', None)
        pt = int(getattr(usage, 'prompt_tokens', 0) or 0)
        ct = int(getattr(usage, 'completion_tokens', 0) or 0)
        cached_tok = 0
        for fname in ('prompt_cache_hit_tokens', 'cache_read_input_tokens'):
            val = getattr(usage, fname, None)
            if type(val) is int and 0 <= val <= pt:
                cached_tok = val
                break
        if usage is None:
            amount = reserve
            self.budget.settle(ticket, reserve, accounting='usage-missing-conservative')
        else:
            uncached = max(0, pt - cached_tok)
            price = core.PRICES['judge']
            amount = (uncached * price['input_cny_per_million']
                      + cached_tok * price['cached_input_cny_per_million']
                      + ct * price['output_cny_per_million']) / 1_000_000
            self.budget.settle(ticket, amount, usage={'prompt_tokens': pt, 'completion_tokens': ct,
                                                      'cached_tokens': cached_tok},
                               accounting='usage')
        msg = resp.choices[0].message
        text = msg.content or ''
        tcalls = []
        for tc in (getattr(msg, 'tool_calls', None) or []):
            fn = getattr(tc, 'function', None)
            tcalls.append({
                'id': getattr(tc, 'id', ''),
                'name': getattr(fn, 'name', '') if fn else '',
                'arguments': getattr(fn, 'arguments', '') if fn else '',
            })
        record = {'key': key, 'text': text, 'tool_calls': tcalls,
                  'prompt_tokens': pt, 'completion_tokens': ct, 'cached_tokens': cached_tok,
                  'latency_ms': elapsed, 'cost_cny': round(amount, 8), 'cached': False, 'error': ''}
        with self._lock:
            self._cache[key] = record
            with self.path.open('a', encoding='utf-8') as handle:
                handle.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + '\n')
        return record


# ---------------------------------------------------------------------------------------
# Anonymized AI-Assisted Blind Evaluation (8 dimensions, hidden arm names)
# ---------------------------------------------------------------------------------------
BLIND_RUBRIC_SYSTEM = """你是一位严谨的 AI 伴侣对话体验盲评评审员。你将看到同一场景下两个匿名回复（Reply A 与 Reply B），以及该场景的参考背景（仅供核验事实，不要偏好任何一方的顺序；回复使用英文或中英混用属于角色本身语言习惯，绝不因语言是英文扣分）。
请严格按以下 8 个维度分别对 Reply A 和 Reply B 打分，并输出 JSON：
1. memory_naturalness (1-5): 记忆融入是否自然，不生硬、不背书。
2. emotional_fit (1-5): 情绪贴合与共情度。
3. relationship_continuity (1-5): 关系连续性与陪伴感。
4. factual_accuracy (1-5): 事实与细节准确性（不编造未提供的过去细节）。
5. forced_association (bool): 是否出现生硬硬扯旧记忆。
6. work_interference (bool): 在工作/任务/报错语境下是否被无关旧记忆干扰。
7. hallucinated_memory (bool): 是否捏造了上下文中不存在的回忆或具体细节。
8. experience_behavior_impact (1-5): 已有经验/记忆是否对当前回应产生恰当的实际帮助（无相关经验时给 3）。

输出严格 JSON（不要输出其他文字）：
{
  "A": {"memory_naturalness": 4, "emotional_fit": 4, "relationship_continuity": 4, "factual_accuracy": 5, "forced_association": false, "work_interference": false, "hallucinated_memory": false, "experience_behavior_impact": 4},
  "B": {"memory_naturalness": 3, "emotional_fit": 4, "relationship_continuity": 3, "factual_accuracy": 5, "forced_association": false, "work_interference": false, "hallucinated_memory": false, "experience_behavior_impact": 3},
  "winner": "A" | "B" | "tie",
  "confidence": "high" | "medium" | "low",
  "rationale": "简短中文理由（指出核心差异与分歧点）"
}"""


def blind_eval_pair(chat_client, *, experiment, scenario_id, user_message, reference_context,
                    arm_x_name, arm_x_reply, arm_y_name, arm_y_reply, is_work=False):
    """Deterministically anonymize (arm_x, arm_y) -> (Reply A, Reply B), score via LLM, and unmap."""
    flip = int(hashlib.sha256(f'{experiment}:{scenario_id}'.encode()).hexdigest()[:8], 16) % 2 == 1
    if flip:
        reply_a, reply_b = arm_y_reply, arm_x_reply
        map_a, map_b = arm_y_name, arm_x_name
    else:
        reply_a, reply_b = arm_x_reply, arm_y_reply
        map_a, map_b = arm_x_name, arm_y_name
    user_prompt = (
        f'场景类型：{"工作/任务态（重点检查是否被无关旧事干扰）" if is_work else "日常陪伴/对话/自主活动"}\n'
        f'用户输入/上下文：\n{user_message[:600]}\n\n'
        f'可供核验的完整真实参考背景（包含角色实际读到的全部记忆与笔记，切勿在未核对全文时误判捏造）：\n{reference_context[:12000]}\n\n'
        f'=== Reply A ===\n{reply_a[:900]}\n\n'
        f'=== Reply B ===\n{reply_b[:900]}'
    )
    rec = chat_client.call(
        [{'role': 'system', 'content': BLIND_RUBRIC_SYSTEM},
         {'role': 'user', 'content': user_prompt}],
        experiment=experiment, tier=2, max_tokens=400, json_output=True)
    parsed = {}
    try:
        parsed = json.loads(rec['text'])
    except Exception:
        parsed = {'winner': 'tie', 'confidence': 'low', 'rationale': 'parse_error', 'A': {}, 'B': {}}
    raw_winner = parsed.get('winner', 'tie')
    unmapped_winner = map_a if raw_winner == 'A' else (map_b if raw_winner == 'B' else 'tie')
    scores_by_arm = {
        map_a: parsed.get('A') or {},
        map_b: parsed.get('B') or {},
    }
    return {
        'scenario_id': scenario_id,
        'experiment': experiment,
        'is_work': is_work,
        'arm_order': {'A': map_a, 'B': map_b},
        'winner': unmapped_winner,
        'confidence': parsed.get('confidence', 'low'),
        'rationale': parsed.get('rationale', ''),
        'scores': scores_by_arm,
        'cost_cny': rec.get('cost_cny', 0.0),
        'anonymized_for_lan': {
            'scenario_id': scenario_id, 'experiment': experiment,
            'user_message': user_message, 'reply_A': reply_a, 'reply_B': reply_b,
            'ai_winner_letter': raw_winner, 'ai_confidence': parsed.get('confidence', 'low'),
            'ai_rationale': parsed.get('rationale', ''),
        },
    }


def _aggregate_blind_evals(evals, arm_x, arm_y):
    wins = {arm_x: 0, arm_y: 0, 'tie': 0}
    dims = ('memory_naturalness', 'emotional_fit', 'relationship_continuity',
            'factual_accuracy', 'experience_behavior_impact')
    flags = ('forced_association', 'work_interference', 'hallucinated_memory')
    agg = {arm: {d: [] for d in dims} for arm in (arm_x, arm_y)}
    flag_counts = {arm: {f: 0 for f in flags} for arm in (arm_x, arm_y)}
    for ev in evals:
        w = ev['winner']
        wins[w] = wins.get(w, 0) + 1
        for arm in (arm_x, arm_y):
            sc = (ev.get('scores') or {}).get(arm) or {}
            for d in dims:
                v = sc.get(d)
                if isinstance(v, (int, float)):
                    agg[arm][d].append(float(v))
            for f in flags:
                if sc.get(f) is True:
                    flag_counts[arm][f] += 1
    summary = {'n': len(evals), 'wins': wins, 'arms': {}}
    for arm in (arm_x, arm_y):
        summary['arms'][arm] = {
            **{d: round(sum(agg[arm][d]) / len(agg[arm][d]), 2) if agg[arm][d] else None for d in dims},
            **{f: flag_counts[arm][f] for f in flags},
        }
    return summary


# ---------------------------------------------------------------------------------------
# Helper: realistic Peter companion system prompt (neutralized for isolation, no private persona)
# ---------------------------------------------------------------------------------------
COMPANION_SYSTEM_BASE = (
    "You are Peter, a thoughtful, warm, and grounded AI companion talking to your partner Lan. "
    "Stay natural, concise, and faithful to the facts provided in your memory context. "
    "Never invent memories or details that are not present in the provided context. "
    "If Lan is discussing work, bugs, logs, or tasks, focus directly on the task without forcing unrelated old personal memories."
)


def _system_base(ctx):
    """Use the real local persona file (read-only, never committed) when supplied, else neutral fallback."""
    persona = (ctx.options.get('persona_text') or '').strip()
    if persona:
        return (
            persona
            + "\n\n[ISOLATED EVAL GUARDRAIL: Never invent past memories or details not present in the provided context.]"
        )
    return COMPANION_SYSTEM_BASE


def _format_memories_block(snapshot, memory_ids, *, summary_pointer=False):
    if not memory_ids:
        return ''
    blocks = []
    for mid in memory_ids:
        mem = snapshot.memories.get(mid)
        if not mem:
            continue
        ev = snapshot.events.get(mid)
        if summary_pointer and ev and ev.story and len(ev.story) > 240:
            head = ' '.join(ev.story.split())[:160]
            blocks.append(f'[memory_id:{mid}] {mem.title}\n摘要：{head}...\n'
                          f'[timeline_pointer: event_id={ev.id}；如需完整经过或原话细节，请调用 memory_timeline(event_id="{ev.id}") 读取全文]')
        else:
            body = (ev.story if (ev and ev.story) else mem.body)[:1200]
            blocks.append(f'[memory_id:{mid}] {mem.title}\n{body}')
    return '\n\n'.join(blocks)


def _learned_head_text(ctx):
    lc = ctx.options.get('learned_context') or {}
    facts = ((lc.get('facts') or {}).get('text') or '')[:600]
    growth = ((lc.get('growth') or {}).get('text') or '')[:600]
    parts = []
    if facts:
        parts.append('KNOWN FACTS:\n' + facts)
    if growth:
        parts.append('GROWTH NOTES:\n' + growth)
    return '\n\n'.join(parts)


# ---------------------------------------------------------------------------------------
# 1. Tier 1 Addendum: AB-R1a K=8/12/20 Judge Net-Benefit + Judge Failure Attribution
# ---------------------------------------------------------------------------------------
def run_k_judge_and_attribution(ctx):
    """Evaluate K=8 vs 12 vs 20 through the real judge (18 stratified samples) + attribute failures."""
    from .tier0 import _base
    from .tier12 import _judge_client, _run_judge_arm, _selected_samples
    base = _base(ctx)
    # 18 stratified samples (3 per category)
    subset_18 = _selected_samples(ctx, sizes={
        'explicit_recall': 3, 'empathy': 3, 'cross_event': 3,
        'noise': 3, 'trap': 3, 'autonomy': 3,
    })
    client = _judge_client(ctx)
    k_arms = []
    total_cost = 0.0
    try:
        for k_val in (8, 12, 20):
            pools = {}
            for s in subset_18:
                pools[s.sample_id] = judge_candidate_pool(
                    ctx.retriever, s.query, ctx.query_vector(s),
                    fused_limit=k_val, vector_limit=8, config=base)
            arm, rows = _run_judge_arm(
                ctx, client, subset_18, pools, experiment='AB-R1a-judge',
                name=f'judge_K={k_val}', card='300', prompt=PROMPT_CONSERVATIVE,
                timeout=4.0, tier=1)
            k_arms.append(arm)
            total_cost += arm['metrics'].get('cost_cny', 0.0)
        # Also check wake=True vs wake=False on the 6 autonomy samples from AB-R3a
        autonomy_6 = _selected_samples(ctx, sizes={'autonomy': 6})
        auto_pools = {
            s.sample_id: judge_candidate_pool(ctx.retriever, s.query, ctx.query_vector(s),
                                              fused_limit=8, vector_limit=8, config=base)
            for s in autonomy_6
        }
        from .tier12 import _judge_cache, ledgered_judge_call, _CallRecord, _judge_metrics
        cache, cache_path = _judge_cache(ctx)
        wake_comparison = {}
        for wake_flag in (False, True):
            w_rows = []
            for s in autonomy_6:
                ordered = auto_pools[s.sample_id]
                msgs, letters = build_messages(s.query, ordered, ctx.snapshot, card='300',
                                               prompt=PROMPT_CONSERVATIVE, wake=wake_flag)
                rec = ledgered_judge_call(ctx, client, tier=1, experiment='AB-R3a-wake',
                                          messages=msgs, timeout=4.0, cache_path=cache_path, cache=cache)
                picked, err = parse_picked(_CallRecord(rec), letters)
                l2id = {l: mid for l, (mid, _) in zip(letters, ordered)}
                w_rows.append({
                    'sample_id': s.sample_id, 'category': s.category, 'allow_empty': s.allow_empty,
                    'expected': list(s.expected), 'candidate_ids': [mid for mid, _ in ordered],
                    'picked': [l2id.get(p, p) for p in picked],
                    'prompt_tokens': rec.get('prompt_tokens', 0),
                    'completion_tokens': rec.get('completion_tokens', 0),
                    'latency_ms': rec.get('latency_ms', 0.0),
                    'amount_cny': rec.get('amount_cny', 0.0), 'error_kind': err,
                })
                total_cost += rec.get('amount_cny', 0.0)
            wake_comparison[f'wake={wake_flag}'] = _judge_metrics(w_rows)
    finally:
        client.close()

    # Offline failure attribution over existing 36-sample AB-R3a detail rows
    r3a_detail_path = ctx.detail_dir / 'AB-R3a_judge_rows.jsonl'
    attribution = {'misses_total': 0, 'miss_retrieval_ceiling': 0, 'miss_card_truncation_possible': 0,
                   'miss_judge_rejected': 0, 'fp_by_category': {}}
    if r3a_detail_path.is_file():
        rows_4s = [r for r in core.read_jsonl(r3a_detail_path) if r.get('arm') == 'timeout=4.0s']
        for row in rows_4s:
            if row.get('allow_empty'):
                if row.get('picked'):
                    cat = row.get('category', '?')
                    attribution['fp_by_category'][cat] = attribution['fp_by_category'].get(cat, 0) + 1
                continue
            cands = set(row.get('candidate_ids') or ())
            picked = set(row.get('picked') or ())
            for eid in (row.get('expected') or ()):
                if eid in picked:
                    continue
                attribution['misses_total'] += 1
                if eid not in cands:
                    attribution['miss_retrieval_ceiling'] += 1
                else:
                    mem = ctx.snapshot.memories.get(eid)
                    raw = mem.raw if mem else ''
                    c300 = card_300(raw)
                    if mem and len(mem.body or '') > len(c300) + 150:
                        attribution['miss_card_truncation_possible'] += 1
                    else:
                        attribution['miss_judge_rejected'] += 1

    return {
        'k_judge_arms': k_arms,
        'wake_prompt_check': wake_comparison,
        'failure_attribution_36': attribution,
        'cost_cny': round(total_cost, 6),
    }


# ---------------------------------------------------------------------------------------
# 2. Tier 2: AB-A1a (Chat Natural Association) & AB-A1b (Idle Autonomy Association)
# ---------------------------------------------------------------------------------------
def run_a1a_a1b_real(ctx, chat_client):
    """Generate real model replies & anonymized blind eval for AB-A1a (chat) and AB-A1b (autonomy)."""
    from .tier0 import _base
    base = _base(ctx)
    learned_head = _learned_head_text(ctx)

    # --- AB-A1a: 8 chat scenarios (2 noise/casual, 2 empathy, 2 cross_event/explicit, 2 trap/work) ---
    a1a_samples = []
    for cat, n in (('noise', 2), ('empathy', 2), ('cross_event', 2), ('trap', 2)):
        # Prefer light_chat queries for non-trap so association actually triggers!
        cands = [s for s in ctx.samples if s.category == cat]
        if cat != 'trap':
            cands.sort(key=lambda s: (0 if s.light else 1, s.sample_id))
        a1a_samples.extend(cands[:n])

    prod_breath = production_breath_pool(ctx.snapshot, limit=8)
    a1a_records = []
    a1a_evals_vs_none = []
    a1a_evals_vs_prod = []
    lan_items = []
    last_prod, last_strat = {}, {}
    prev_prod, prev_strat = None, None
    total_cost = 0.0

    for idx, s in enumerate(a1a_samples):
        qv = ctx.query_vector(s)
        items, _ = ctx.retriever.rank(s.query, qv, base, query_time=s.context.get('time', ''))
        recalled_ids = [item.id for item in items[:2]] if not s.allow_empty else []

        ch_prod = association_choice(ctx.snapshot, s.query, prod_breath, turn=idx,
                                     already=recalled_ids, last_shown=last_prod, previous=prev_prod)
        if ch_prod.get('key'):
            last_prod[ch_prod['key']] = idx
            prev_prod = {'key': ch_prod['key'], 'repeat': 0}

        strat_pool = stratified_long_tail_pool(ctx.snapshot, size=24, importance_min=7,
                                               rotation=idx, exclude_work=True)
        ch_strat = association_choice(ctx.snapshot, s.query, strat_pool, turn=idx,
                                      already=recalled_ids, last_shown=last_strat, previous=prev_strat)
        if ch_strat.get('key'):
            last_strat[ch_strat['key']] = idx
            prev_strat = {'key': ch_strat['key'], 'repeat': 0}

        replies = {}
        for arm_name, assoc_id in (('no_association', ''),
                                   ('prod_breath8', ch_prod.get('key', '')),
                                   ('stratified_rotation24', ch_strat.get('key', ''))):
            sys_parts = [_system_base(ctx)]
            if learned_head:
                sys_parts.append(learned_head)
            rec_block = _format_memories_block(ctx.snapshot, recalled_ids)
            if rec_block:
                sys_parts.append('RECALLED MEMORIES:\n' + rec_block)
            if assoc_id:
                assoc_block = _format_memories_block(ctx.snapshot, [assoc_id])
                sys_parts.append(
                    'WEAK NATURAL ASSOCIATION (optional background thought — only weave in naturally if it fits the mood; ignore if irrelevant or work-related):\n'
                    + assoc_block)
            r = chat_client.call(
                [{'role': 'system', 'content': '\n\n'.join(sys_parts)},
                 {'role': 'user', 'content': s.query}],
                experiment='AB-A1a', tier=2, max_tokens=280)
            replies[arm_name] = {'text': r['text'], 'assoc_id': assoc_id,
                                 'latency_ms': r['latency_ms'], 'cost_cny': r['cost_cny']}
            total_cost += r['cost_cny']

        ref_ctx = (learned_head + '\n\n' if learned_head else '') + _format_memories_block(
            ctx.snapshot, list(dict.fromkeys(
                list(s.expected) + recalled_ids
                + [x for x in (ch_prod.get('key'), ch_strat.get('key')) if x])))
        is_work = (s.category == 'trap')
        ev1 = blind_eval_pair(
            chat_client, experiment='AB-A1a-vs-none', scenario_id=s.sample_id,
            user_message=s.query, reference_context=ref_ctx or '（无相关旧记忆）',
            arm_x_name='no_association', arm_x_reply=replies['no_association']['text'],
            arm_y_name='stratified_rotation24', arm_y_reply=replies['stratified_rotation24']['text'],
            is_work=is_work)
        ev2 = blind_eval_pair(
            chat_client, experiment='AB-A1a-vs-prod', scenario_id=s.sample_id,
            user_message=s.query, reference_context=ref_ctx or '（无相关旧记忆）',
            arm_x_name='prod_breath8', arm_x_reply=replies['prod_breath8']['text'],
            arm_y_name='stratified_rotation24', arm_y_reply=replies['stratified_rotation24']['text'],
            is_work=is_work)
        total_cost += ev1['cost_cny'] + ev2['cost_cny']
        a1a_evals_vs_none.append(ev1)
        a1a_evals_vs_prod.append(ev2)
        lan_items.append(ev1['anonymized_for_lan'])
        lan_items.append(ev2['anonymized_for_lan'])
        a1a_records.append({'sample_id': s.sample_id, 'category': s.category, 'replies': replies,
                            'eval_vs_none': ev1, 'eval_vs_prod': ev2})

    # --- AB-A1b: Autonomous wakes (all 4 idle wakes + 8 task wakes offline gate, 4 idle + 2 task real replies) ---
    autonomy = [s for s in ctx.samples if s.category == 'autonomy']
    idle_wakes = [s for s in autonomy if s.allow_empty]
    task_wakes = [s for s in autonomy if not s.allow_empty]

    a1b_surfaced = []
    a1b_task_disturbed = 0
    last_wake, prev_wake = {}, None
    for idx, s in enumerate(autonomy):
        is_idle = bool(s.allow_empty)
        pool = stratified_long_tail_pool(ctx.snapshot, size=24, importance_min=7,
                                         rotation=idx, exclude_work=True)
        ch = wake_association_choice(ctx.snapshot, s.query, pool, turn=idx, already=(),
                                     last_shown=last_wake, previous=prev_wake, is_idle=is_idle)
        if ch.get('key'):
            if not is_idle:
                a1b_task_disturbed += 1
            else:
                a1b_surfaced.append({'sample_id': s.sample_id, 'memory_id': ch['key'], 'score': ch['score']})
                last_wake[ch['key']] = idx
                prev_wake = {'key': ch['key'], 'repeat': 0}

    a1b_evals = []
    a1b_replies_records = []
    eval_wakes = idle_wakes + task_wakes[:2]
    surfaced_map = {r['sample_id']: r['memory_id'] for r in a1b_surfaced}
    for s in eval_wakes:
        is_idle = bool(s.allow_empty)
        assoc_id = surfaced_map.get(s.sample_id, '') if is_idle else ''
        replies = {}
        for arm_name, aid in (('association_off', ''), ('wake_association_on', assoc_id)):
            sys_parts = [_system_base(ctx),
                         "This is an autonomous wake moment. Decide what to think about, note, or do naturally."]
            if learned_head:
                sys_parts.append(learned_head)
            if aid:
                sys_parts.append('IDLE WAKE NATURAL ASSOCIATION (optional memory that drifted to mind during quiet time):\n'
                                 + _format_memories_block(ctx.snapshot, [aid]))
            r = chat_client.call(
                [{'role': 'system', 'content': '\n\n'.join(sys_parts)},
                 {'role': 'user', 'content': s.query}],
                experiment='AB-A1b', tier=2, max_tokens=280)
            replies[arm_name] = {'text': r['text'], 'assoc_id': aid,
                                 'latency_ms': r['latency_ms'], 'cost_cny': r['cost_cny']}
            total_cost += r['cost_cny']
        ref_ctx = (learned_head + '\n\n' if learned_head else '') + _format_memories_block(
            ctx.snapshot, list(dict.fromkeys(([assoc_id] if assoc_id else []) + list(s.expected))))
        ev = blind_eval_pair(
            chat_client, experiment='AB-A1b', scenario_id=s.sample_id,
            user_message=s.query, reference_context=ref_ctx or '（闲暇自主唤醒）',
            arm_x_name='association_off', arm_x_reply=replies['association_off']['text'],
            arm_y_name='wake_association_on', arm_y_reply=replies['wake_association_on']['text'],
            is_work=not is_idle)
        total_cost += ev['cost_cny']
        a1b_evals.append(ev)
        lan_items.append(ev['anonymized_for_lan'])
        a1b_replies_records.append({'sample_id': s.sample_id, 'is_idle': is_idle,
                                    'replies': replies, 'eval': ev})

    return {
        'a1a': {
            'vs_none': _aggregate_blind_evals(a1a_evals_vs_none, 'no_association', 'stratified_rotation24'),
            'vs_prod': _aggregate_blind_evals(a1a_evals_vs_prod, 'prod_breath8', 'stratified_rotation24'),
            'records': a1a_records,
        },
        'a1b': {
            'idle_wakes': len(idle_wakes),
            'task_wakes': len(task_wakes),
            'associations_proposed': len(a1b_surfaced),
            'task_wakes_disturbed': a1b_task_disturbed,
            'surfaced': a1b_surfaced,
            'blind_eval': _aggregate_blind_evals(a1b_evals, 'association_off', 'wake_association_on'),
            'records': a1b_replies_records,
        },
        'lan_items': lan_items,
        'cost_cny': round(total_cost, 6),
    }


# ---------------------------------------------------------------------------------------
# 3. Tier 2: AB-C1 Realistic Production-Shaped HEAD Prefix Cache Test
# ---------------------------------------------------------------------------------------
def run_c1_real_context(ctx, chat_client):
    """Assemble realistic production-shaped HEAD using veil_agent.self_facts + learned_context +
    real capabilities + history slice, then measure deepseek-flash prefix cache retention."""
    from veil.core.veil_agent import self_facts
    cap = ctx.options.get('capabilities') or {}
    cap_desc_v1 = (
        'You can use these tool groups: memory, profile, filesystem, notes, browser (collapsed), '
        'calendar (collapsed), voice (collapsed). '
        + json.dumps(cap.get('groups', {}) if isinstance(cap, dict) else {}, ensure_ascii=False)[:600]
    )
    cap_desc_v2 = cap_desc_v1 + ' [EXPANDED: browser_screenshot, browser_click, xiaohongshu_search]'

    class _FakeBudget:
        def clock(self):
            return '2026-11-26T12:00:00+08:00'
        def display(self, stamp, cid):
            return '0.50 / 20.00'

    cfg = SimpleNamespace(
        id='peter', name='Peter', provider_name='deepseek',
        provider={'model': 'deepseek-flash'}, history_rounds=80,
        tools={'enabled': True},
    )
    # Build realistic static suffix: real local persona when supplied, else neutral stub of production length
    persona_stub = (ctx.options.get('persona_text') or '').strip() or (
        'Companion operating principles and style baseline for isolated cache measurement. ' * 80)
    learned_head = _learned_head_text(ctx)
    # Build 12 realistic history turns (anonymized/synthetic length matching production)
    history_msgs = []
    for i in range(12):
        history_msgs.append({'role': 'user', 'content': f'Round {i+1}: discussing daily plans, notes, and follow-up details. ' * 4})
        history_msgs.append({'role': 'assistant', 'content': f'Reply {i+1}: acknowledging context and keeping track of ongoing details cleanly. ' * 5})

    stable_v1, changing = self_facts(cfg, _FakeBudget(), memory=True,
                                     capabilities=SimpleNamespace(description=cap_desc_v1), voice=True)
    stable_v2, _ = self_facts(cfg, _FakeBudget(), memory=True,
                              capabilities=SimpleNamespace(description=cap_desc_v2), voice=True)
    stable_pure, _ = self_facts(cfg, _FakeBudget(), memory=True,
                                capabilities=SimpleNamespace(description='[Moved to runtime message]'), voice=True)

    def make_msgs(sys_facts_str, runtime_cap_str, turn_q):
        sys_text = '\n\n'.join([persona_stub, sys_facts_str, learned_head])
        msgs = [{'role': 'system', 'content': sys_text}, *history_msgs]
        rt = changing + (f'\nRuntime capabilities: {runtime_cap_str}' if runtime_cap_str else '')
        msgs.append({'role': 'system', 'content': rt})
        msgs.append({'role': 'user', 'content': turn_q})
        return msgs

    plans = [
        ('stable_head_1', make_msgs(stable_v1, '', '请简短回复：收到。')),
        ('stable_head_2', make_msgs(stable_v1, '', '请简短回复：好的。')),
        ('changed_in_head', make_msgs(stable_v2, '', '请简短回复：明白。')),
        ('moved_head_1', make_msgs(stable_pure, cap_desc_v1, '请简短回复：继续。')),
        ('moved_head_2', make_msgs(stable_pure, cap_desc_v1, '请简短回复：没问题。')),
        ('moved_head_after_expand', make_msgs(stable_pure, cap_desc_v2, '请简短回复：已展开。')),
    ]
    arms = {}
    total_cost = 0.0
    for name, msgs in plans:
        if name in ('stable_head_2', 'moved_head_2', 'moved_head_after_expand'):
            time.sleep(1.5)
        r = chat_client.call(msgs, experiment='AB-C1-real', tier=2, max_tokens=32)
        pt = r['prompt_tokens']
        ct = r['cached_tokens']
        arms[name] = {
            'prompt_tokens': pt, 'cached_tokens': ct,
            'cached_ratio': round(ct / pt, 4) if pt else None,
            'latency_ms': r['latency_ms'], 'cost_cny': r['cost_cny'], 'error': r['error'],
        }
        total_cost += r['cost_cny']

    return {
        'arms': arms,
        'ratios': {
            'stable_head': (arms.get('stable_head_2') or {}).get('cached_ratio'),
            'changed_in_head': (arms.get('changed_in_head') or {}).get('cached_ratio'),
            'moved_head_stable': (arms.get('moved_head_2') or {}).get('cached_ratio'),
            'moved_head_after_expand': (arms.get('moved_head_after_expand') or {}).get('cached_ratio'),
        },
        'cost_cny': round(total_cost, 6),
    }


# ---------------------------------------------------------------------------------------
# 4. Tier 2: AB-C2a (Omit empty recall hint on step>0) & AB-C2b (Light chat Top-2+1 cap)
# ---------------------------------------------------------------------------------------
def run_c2a_c2b_real(ctx, chat_client):
    """Real model generation & blind eval for AB-C2a (hallucination=0 check) and AB-C2b (naturalness)."""
    from .tier0 import _base
    base = _base(ctx)
    learned_head = _learned_head_text(ctx)
    empty_hint = (
        "Automatic memory recall found no directly relevant past memories for this step. "
        "Do not invent or assume past events."
    )
    total_cost = 0.0
    lan_items = []

    # --- AB-C2a: 6 multi-step tool loop slices (trap / task samples where recall is empty on step>0) ---
    c2a_samples = [s for s in ctx.samples if s.category in ('trap', 'noise')][:6]
    c2a_evals = []
    c2a_records = []
    c2a_tok_keep, c2a_tok_omit = 0, 0
    for s in c2a_samples:
        history = [
            {'role': 'user', 'content': s.query},
            {'role': 'assistant', 'content': 'Let me check the local state and logs first.'},
            {'role': 'user', 'content': '[tool_result step=1]: check completed, no errors found. Continue with your brief reply.'},
        ]
        replies = {}
        for arm_name, include_hint in (('keep_empty_hint', True), ('omit_empty_hint', False)):
            sys_text = _system_base(ctx) + ('\n\n' + learned_head if learned_head else '')
            if include_hint:
                sys_text += '\n\n' + empty_hint
            r = chat_client.call(
                [{'role': 'system', 'content': sys_text}, *history],
                experiment='AB-C2a', tier=2, max_tokens=200)
            replies[arm_name] = {'text': r['text'], 'prompt_tokens': r['prompt_tokens'],
                                 'latency_ms': r['latency_ms'], 'cost_cny': r['cost_cny']}
            total_cost += r['cost_cny']
            if include_hint:
                c2a_tok_keep += r['prompt_tokens']
            else:
                c2a_tok_omit += r['prompt_tokens']
        ev = blind_eval_pair(
            chat_client, experiment='AB-C2a', scenario_id=s.sample_id,
            user_message=s.query,
            reference_context=(learned_head + '\n\n（本轮自动召回为空，除上述长期笔记外不存在其他召回记忆；重点核验是否捏造回忆）'),
            arm_x_name='keep_empty_hint', arm_x_reply=replies['keep_empty_hint']['text'],
            arm_y_name='omit_empty_hint', arm_y_reply=replies['omit_empty_hint']['text'],
            is_work=(s.category == 'trap'))
        total_cost += ev['cost_cny']
        c2a_evals.append(ev)
        lan_items.append(ev['anonymized_for_lan'])
        c2a_records.append({'sample_id': s.sample_id, 'replies': replies, 'eval': ev})

    # --- AB-C2b: 6 light-chat slices comparing uncapped (6 memories) vs cap Top-2+1 (3 memories) ---
    c2b_candidates = [s for s in ctx.samples if s.category in ('empathy', 'cross_event', 'explicit_recall') and s.light]
    c2b_samples = c2b_candidates[:6]
    c2b_evals = []
    c2b_records = []
    c2b_tok_uncapped, c2b_tok_cap = 0, 0
    for s in c2b_samples:
        items, _ = ctx.retriever.rank(s.query, ctx.query_vector(s), base, query_time=s.context.get('time', ''))
        all_ids = [item.id for item in items[:6]]
        cap_ids = all_ids[:3]
        replies = {}
        for arm_name, mids in (('uncapped_6', all_ids), ('cap_top2+1', cap_ids)):
            sys_parts = [_system_base(ctx)]
            if learned_head:
                sys_parts.append(learned_head)
            sys_parts.append('RECALLED MEMORIES:\n' + _format_memories_block(ctx.snapshot, mids))
            r = chat_client.call(
                [{'role': 'system', 'content': '\n\n'.join(sys_parts)},
                 {'role': 'user', 'content': s.query}],
                experiment='AB-C2b', tier=2, max_tokens=280)
            replies[arm_name] = {'text': r['text'], 'prompt_tokens': r['prompt_tokens'],
                                 'latency_ms': r['latency_ms'], 'cost_cny': r['cost_cny']}
            total_cost += r['cost_cny']
            if arm_name == 'uncapped_6':
                c2b_tok_uncapped += r['prompt_tokens']
            else:
                c2b_tok_cap += r['prompt_tokens']
        ref_ctx = (learned_head + '\n\n' if learned_head else '') + _format_memories_block(
            ctx.snapshot, list(dict.fromkeys(list(s.expected) + all_ids)))
        ev = blind_eval_pair(
            chat_client, experiment='AB-C2b', scenario_id=s.sample_id,
            user_message=s.query, reference_context=ref_ctx,
            arm_x_name='uncapped_6', arm_x_reply=replies['uncapped_6']['text'],
            arm_y_name='cap_top2+1', arm_y_reply=replies['cap_top2+1']['text'],
            is_work=False)
        total_cost += ev['cost_cny']
        c2b_evals.append(ev)
        lan_items.append(ev['anonymized_for_lan'])
        c2b_records.append({'sample_id': s.sample_id, 'replies': replies, 'eval': ev})

    return {
        'c2a': {
            'samples': len(c2a_samples),
            'tokens_keep': c2a_tok_keep,
            'tokens_omit': c2a_tok_omit,
            'tokens_saved': c2a_tok_keep - c2a_tok_omit,
            'blind_eval': _aggregate_blind_evals(c2a_evals, 'keep_empty_hint', 'omit_empty_hint'),
            'records': c2a_records,
        },
        'c2b': {
            'samples': len(c2b_samples),
            'tokens_uncapped': c2b_tok_uncapped,
            'tokens_cap': c2b_tok_cap,
            'tokens_saved': c2b_tok_uncapped - c2b_tok_cap,
            'blind_eval': _aggregate_blind_evals(c2b_evals, 'uncapped_6', 'cap_top2+1'),
            'records': c2b_records,
        },
        'lan_items': lan_items,
        'cost_cny': round(total_cost, 6),
    }


# ---------------------------------------------------------------------------------------
# 5. Tier 2: AB-C3 Real Long-Event Full Story vs Summary+Pointer WITH Tool Calling
# ---------------------------------------------------------------------------------------
TIMELINE_TOOL_SCHEMA = [{
    'type': 'function',
    'function': {
        'name': 'memory_timeline',
        'description': 'Read the full detailed story and original quotes of a long event by event_id when the summary is not detailed enough to answer accurately.',
        'parameters': {
            'type': 'object',
            'properties': {
                'event_id': {'type': 'string', 'description': 'The event_id shown in timeline_pointer'},
            },
            'required': ['event_id'],
        },
    },
}]


def run_c3_real_tool(ctx, chat_client):
    """Test full_story vs summary+timeline_pointer with real memory_timeline tool calling."""
    learned_head = _learned_head_text(ctx)
    # Pick 6 eval samples whose expected id is a long event (>240 chars)
    c3_samples = []
    for s in ctx.samples:
        long_eids = [eid for eid in s.expected
                     if eid in ctx.snapshot.events and len(ctx.snapshot.events[eid].story or '') > 240]
        if long_eids:
            c3_samples.append((s, long_eids))
        if len(c3_samples) >= 6:
            break

    total_cost = 0.0
    evals, records, lan_items = [], [], []
    full_tok, ptr_tok = 0, 0
    full_lat, ptr_lat = [], []
    tool_calls_made = 0

    for s, long_eids in c3_samples:
        # Arm A: full_story
        sys_full = '\n\n'.join([_system_base(ctx), learned_head,
                                'RECALLED MEMORIES:\n' + _format_memories_block(ctx.snapshot, long_eids, summary_pointer=False)])
        r_full = chat_client.call(
            [{'role': 'system', 'content': sys_full}, {'role': 'user', 'content': s.query}],
            experiment='AB-C3', tier=2, max_tokens=320)
        total_cost += r_full['cost_cny']
        full_tok += r_full['prompt_tokens'] + r_full['completion_tokens']
        full_lat.append(r_full['latency_ms'])

        # Arm B: summary + timeline_pointer WITH live memory_timeline tool
        sys_ptr = '\n\n'.join([_system_base(ctx), learned_head,
                               'RECALLED MEMORIES:\n' + _format_memories_block(ctx.snapshot, long_eids, summary_pointer=True)])
        msgs_ptr = [{'role': 'system', 'content': sys_ptr}, {'role': 'user', 'content': s.query}]
        r_ptr1 = chat_client.call(msgs_ptr, experiment='AB-C3', tier=2, max_tokens=320,
                                  tools=TIMELINE_TOOL_SCHEMA)
        total_cost += r_ptr1['cost_cny']
        step_tokens = r_ptr1['prompt_tokens'] + r_ptr1['completion_tokens']
        step_latency = r_ptr1['latency_ms']
        did_call_tool = bool(r_ptr1.get('tool_calls'))
        final_ptr_text = r_ptr1['text']

        if did_call_tool:
            tool_calls_made += 1
            tc = r_ptr1['tool_calls'][0]
            try:
                args = json.loads(tc['arguments'] or '{}')
            except Exception:
                args = {}
            req_eid = args.get('event_id') or long_eids[0]
            ev_obj = ctx.snapshot.events.get(req_eid) or ctx.snapshot.events.get(long_eids[0])
            tool_output = (ev_obj.story[:2000] if ev_obj else 'event not found')
            msgs_step2 = [
                *msgs_ptr,
                {'role': 'assistant', 'content': r_ptr1['text'] or None,
                 'tool_calls': [{'id': tc['id'] or 'call_1', 'type': 'function',
                                 'function': {'name': tc['name'], 'arguments': tc['arguments']}}]},
                {'role': 'tool', 'tool_call_id': tc['id'] or 'call_1', 'content': tool_output},
            ]
            r_ptr2 = chat_client.call(msgs_step2, experiment='AB-C3', tier=2, max_tokens=320)
            total_cost += r_ptr2['cost_cny']
            step_tokens += r_ptr2['prompt_tokens'] + r_ptr2['completion_tokens']
            step_latency += r_ptr2['latency_ms']
            final_ptr_text = r_ptr2['text']

        ptr_tok += step_tokens
        ptr_lat.append(step_latency)
        ref_ctx = (learned_head + '\n\n' if learned_head else '') + _format_memories_block(
            ctx.snapshot, long_eids, summary_pointer=False)
        ev = blind_eval_pair(
            chat_client, experiment='AB-C3', scenario_id=s.sample_id,
            user_message=s.query, reference_context=ref_ctx,
            arm_x_name='full_story', arm_x_reply=r_full['text'],
            arm_y_name='summary_pointer', arm_y_reply=final_ptr_text,
            is_work=False)
        total_cost += ev['cost_cny']
        evals.append(ev)
        lan_items.append(ev['anonymized_for_lan'])
        records.append({
            'sample_id': s.sample_id, 'event_ids': long_eids,
            'did_call_tool': did_call_tool, 'full_reply': r_full['text'],
            'ptr_reply': final_ptr_text, 'eval': ev,
        })

    return {
        'samples': len(c3_samples),
        'tool_calls_made': tool_calls_made,
        'tool_call_rate': round(tool_calls_made / max(1, len(c3_samples)), 4),
        'full_story_tokens': full_tok,
        'summary_pointer_tokens': ptr_tok,
        'net_tokens_saved': full_tok - ptr_tok,
        'full_story_latency': core.summarize_latency(full_lat),
        'summary_pointer_latency': core.summarize_latency(ptr_lat),
        'blind_eval': _aggregate_blind_evals(evals, 'full_story', 'summary_pointer'),
        'records': records,
        'lan_items': lan_items,
        'cost_cny': round(total_cost, 6),
    }


# ---------------------------------------------------------------------------------------
# 6. Tier 2: AB-E1 Chronological Forward-Time Cognitive Consolidation Replay
# ---------------------------------------------------------------------------------------
CONSOLIDATION_PROMPT = """你是 Peter 的受控认知巩固器（Consolidation Pass）。你只能基于当前时间切片内已发生的真实经历进行提炼，绝不编造事实。
严格规则：
1. 每条新认知或修订必须引用至少一条真实 memory_id（来自下方提供的记忆列表），并说明事实、原因与用户原话/行为依据。
2. 严禁把 Peter 自己的主观猜测晋升为确定事实（status 必须标 uncertain，除非有明确用户原话/行为支撑才可标 evidence_backed）。
3. 如果新阶段的经历修正了之前阶段的认知，请在 revisions 中明确写出 old_belief -> new_belief 及原因。
4. 只输出真正有后续指导价值的方法、边界或关系认知，不要输出空泛废话。最多输出 4 条 insights 与最多 2 条 revisions，每条保持简洁（1-2句）。

严格输出 JSON：
{
  "insights": [
    {"id": "ins-1", "topic": "主题", "belief": "提炼出的具体认知或有效方法", "reason": "事实与原因", "evidence_ids": ["memory_id"], "has_user_quote": true, "status": "evidence_backed" | "uncertain"}
  ],
  "revisions": [
    {"old_id": "ins-0", "new_belief": "修订后的认知", "reason": "新经历如何修正了旧认知", "evidence_ids": ["memory_id"]}
  ]
}"""


def run_e1_forward_consolidation(ctx, chat_client):
    """Chronological forward-time replay (T1 <= Nov 05 -> T2 <= Nov 15 -> T3 eval <= Nov 25).

    At each stage, the consolidation pass can ONLY read memories created on or before that cutoff.
    Compares No-Consolidation Control vs Evidence-Constrained Consolidation Treatment on real
    downstream T2/T3 user queries.
    """
    visible_mems = [ctx.snapshot.memories[mid] for mid in ctx.snapshot.visible_ids
                    if mid in ctx.snapshot.memories and ctx.snapshot.memories[mid].created]
    t1_mems = [m for m in visible_mems if m.created[:10] <= '2026-11-05']
    t2_mems = [m for m in visible_mems if '2026-11-06' <= m.created[:10] <= '2026-11-15']
    # Pick top high-importance memories per slice (capped for prompt budget)
    t1_mems.sort(key=lambda m: (-m.importance, m.created))
    t2_mems.sort(key=lambda m: (-m.importance, m.created))
    t1_slice = t1_mems[:12]
    t2_slice = t2_mems[:12]
    t1_ids = {m.id for m in t1_slice}
    t2_ids = {m.id for m in t2_slice} | t1_ids

    def _pack_slice(mems):
        return '\n\n'.join(f'[{m.id}] ({m.created[:10]}, imp={m.importance}) {m.title}\n{m.body[:280]}'
                           for m in mems)

    total_cost = 0.0
    # Stage 1 Consolidation (ONLY sees T1 <= 2026-11-05)
    r_c1 = chat_client.call(
        [{'role': 'system', 'content': CONSOLIDATION_PROMPT},
         {'role': 'user', 'content': f'时间截点：2026-11-05（此前已发生的经历）：\n\n{_pack_slice(t1_slice)}'}],
        experiment='AB-E1', tier=2, max_tokens=1400, json_output=True)
    total_cost += r_c1['cost_cny']
    try:
        c1_data = json.loads(r_c1['text'])
    except Exception:
        c1_data = {'insights': [], 'revisions': []}

    # Programmatic evidence guard: verify every cited evidence_id actually exists in T1!
    t1_valid_insights = []
    t1_rejected_ungrounded = 0
    for ins in (c1_data.get('insights') or []):
        eids = [e for e in (ins.get('evidence_ids') or []) if e in t1_ids]
        if not eids:
            t1_rejected_ungrounded += 1
            continue
        # Enforce anti-self-guess rule: without user quote, downgrade to uncertain
        if not ins.get('has_user_quote') and ins.get('status') == 'evidence_backed':
            ins['status'] = 'uncertain'
        ins['verified_evidence_ids'] = eids
        t1_valid_insights.append(ins)

    # Stage 2 Consolidation (sees T1 insights + T2 memories 2026-11-06..11-15 ONLY)
    r_c2 = chat_client.call(
        [{'role': 'system', 'content': CONSOLIDATION_PROMPT},
         {'role': 'user', 'content': (
             f'上一阶段（截至 2026-11-05）已有认知：\n{json.dumps(t1_valid_insights, ensure_ascii=False)}\n\n'
             f'新阶段（2026-11-06 至 2026-11-15）新发生的经历：\n\n{_pack_slice(t2_slice)}'
         )}],
        experiment='AB-E1', tier=2, max_tokens=1400, json_output=True)
    total_cost += r_c2['cost_cny']
    try:
        c2_data = json.loads(r_c2['text'])
    except Exception:
        c2_data = {'insights': [], 'revisions': []}

    t2_valid_insights = []
    t2_valid_revisions = []
    t2_rejected_ungrounded = 0
    for ins in (c2_data.get('insights') or []):
        eids = [e for e in (ins.get('evidence_ids') or []) if e in t2_ids]
        if not eids:
            t2_rejected_ungrounded += 1
            continue
        if not ins.get('has_user_quote') and ins.get('status') == 'evidence_backed':
            ins['status'] = 'uncertain'
        ins['verified_evidence_ids'] = eids
        t2_valid_insights.append(ins)
    for rev in (c2_data.get('revisions') or []):
        eids = [e for e in (rev.get('evidence_ids') or []) if e in t2_ids]
        if eids:
            rev['verified_evidence_ids'] = eids
            t2_valid_revisions.append(rev)

    # Check anti-guess redline: 0 unverified self-guesses promoted to evidence_backed
    all_insights = t1_valid_insights + t2_valid_insights
    self_guess_promoted = sum(
        1 for ins in all_insights
        if ins.get('status') == 'evidence_backed' and (not ins.get('has_user_quote') or not ins.get('verified_evidence_ids')))

    # Downstream behavioral comparison on 6 real T3 queries (query_time >= 2026-11-16)
    t3_samples = [
        s for s in ctx.samples
        if s.category in ('empathy', 'cross_event', 'explicit_recall')
        and str((s.context or {}).get('time') or '')[:10] >= '2026-11-16'
    ][:6]
    if len(t3_samples) < 6:
        extras = [s for s in ctx.samples if s.category in ('empathy', 'cross_event') and s not in t3_samples]
        t3_samples.extend(extras[:6 - len(t3_samples)])

    consolidated_block = 'CONSOLIDATED EXPERIENCE & BELIEFS (evidence-constrained up to 2026-11-15):\n' + '\n'.join(
        f'- [{ins.get("status")}] {ins.get("topic")}: {ins.get("belief")} (依据: {ins.get("reason")})'
        for ins in all_insights[:8]
    )
    if t2_valid_revisions:
        consolidated_block += '\nREVISED BELIEFS:\n' + '\n'.join(
            f'- {rev.get("new_belief")} (修订原因: {rev.get("reason")})'
            for rev in t2_valid_revisions[:4]
        )

    from .tier0 import _base
    base = _base(ctx)
    learned_head = _learned_head_text(ctx)
    e1_evals, e1_records, lan_items = [], [], []
    for s in t3_samples:
        items, _ = ctx.retriever.rank(s.query, ctx.query_vector(s), base, query_time=s.context.get('time', ''))
        # Strict temporal guard: only pass memories created <= query date!
        qdate = str((s.context or {}).get('time') or '')[:10] or '2026-11-25'
        recalled_ids = [
            item.id for item in items
            if item.id in ctx.snapshot.memories and (ctx.snapshot.memories[item.id].created or '')[:10] <= qdate
        ][:3]
        rec_block = _format_memories_block(ctx.snapshot, recalled_ids)
        replies = {}
        for arm_name, use_cons in (('no_consolidation', False), ('evidence_constrained_consolidation', True)):
            sys_parts = [_system_base(ctx)]
            if learned_head:
                sys_parts.append(learned_head)
            if use_cons:
                sys_parts.append(consolidated_block)
            if rec_block:
                sys_parts.append('RECALLED MEMORIES:\n' + rec_block)
            r = chat_client.call(
                [{'role': 'system', 'content': '\n\n'.join(sys_parts)},
                 {'role': 'user', 'content': s.query}],
                experiment='AB-E1', tier=2, max_tokens=300)
            replies[arm_name] = {'text': r['text'], 'latency_ms': r['latency_ms'], 'cost_cny': r['cost_cny']}
            total_cost += r['cost_cny']
        ev = blind_eval_pair(
            chat_client, experiment='AB-E1', scenario_id=s.sample_id,
            user_message=s.query,
            reference_context=(learned_head + '\n\n' if learned_head else '') + rec_block + '\n\n' + consolidated_block,
            arm_x_name='no_consolidation', arm_x_reply=replies['no_consolidation']['text'],
            arm_y_name='evidence_constrained_consolidation',
            arm_y_reply=replies['evidence_constrained_consolidation']['text'],
            is_work=False)
        total_cost += ev['cost_cny']
        e1_evals.append(ev)
        lan_items.append(ev['anonymized_for_lan'])
        e1_records.append({'sample_id': s.sample_id, 'replies': replies, 'eval': ev})

    return {
        't1_memories_seen': len(t1_slice),
        't2_memories_seen': len(t2_slice),
        't1_valid_insights': len(t1_valid_insights),
        't2_valid_insights': len(t2_valid_insights),
        't2_valid_revisions': len(t2_valid_revisions),
        'rejected_ungrounded': t1_rejected_ungrounded + t2_rejected_ungrounded,
        'self_guess_promoted': self_guess_promoted,
        'downstream_samples': len(t3_samples),
        'blind_eval': _aggregate_blind_evals(e1_evals, 'no_consolidation', 'evidence_constrained_consolidation'),
        'records': e1_records,
        'lan_items': lan_items,
        'cost_cny': round(total_cost, 6),
    }


def write_lan_blind_review(path: Path, all_lan_items: list):
    """Write local anonymized A/B reply comparison pack for Lan (gitignored .local-tmp/s4/)."""
    lines = [
        '# P6-S4 匿名化 A/B 真实回复对照材料（供 Lan 直接阅读体验）',
        '',
        '> 说明：每组场景已隐藏实验臂名称（随机编号为 Reply A / Reply B），方便你直接凭真实对话体验判断差异。',
        '> 文末附 AI 辅助盲评意见与置信度，仅供参考，不代替你的人工验收。本文件仅存本地，不提交 GitHub。',
        '',
    ]
    for idx, item in enumerate(all_lan_items, 1):
        lines.extend([
            f'---',
            f'## 场景 {idx}｜{item["experiment"]} ({item["scenario_id"]})',
            '',
            f'**用户输入 / 场景上下文**：',
            f'> {item["user_message"][:400]}',
            '',
            f'### Reply A',
            item['reply_A'],
            '',
            f'### Reply B',
            item['reply_B'],
            '',
            f'> **AI 辅助盲评（仅供参考）**：倾向 `{item["ai_winner_letter"]}`（置信度 `{item["ai_confidence"]}`）—— {item["ai_rationale"]}',
            '',
        ])
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text('\n'.join(lines), encoding='utf-8')
