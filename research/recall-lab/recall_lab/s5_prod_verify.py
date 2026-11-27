"""P6-S5 full production Chat.send() request-assembly verification & 3-way association对照.

Unlike component replay, this harness instantiates the real production `veil.core.veil_agent.Chat`,
`BrainAuthority`, `Memory`, `RecallJudge`, `LearnedContext`, `relationship_base`, `EventMemory`
event stories/ledger, global `recall_budget`, `request_snapshots`, and `confirm_delivery` against
an isolated temporary copy of `brain.db` and `learned-context.json` (never touches formal Peter).

Verifies:
1. Final default configuration (`stopwords_enabled=False`, `candidate_k=8`, S5 situational judge)
   end-to-end inside `Chat.send()` (including diagnostics, request snapshot, config overview, and
   delivery lifecycle).
2. Three-way natural association (`off` / `legacy_breath8` / `contextual_s5`) on 6 representative
   samples drawn strictly from the 36-sample verified judge subset, where all 3 arms share the
   exact same production Chat request assembly and ONLY the natural association setting varies.
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
import time
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
LAB_ROOT = Path(__file__).resolve().parents[1]
for _p in (str(ROOT), str(LAB_ROOT)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from veil.brain.memory_agent import Memory  # noqa: E402
from veil.brain.recall.recall_judge import DEFAULTS as JUDGE_DEFAULTS  # noqa: E402
from veil.brain.storage.brain_authority import BrainAuthority  # noqa: E402
from veil.core.config_registry import recall_config_overview  # noqa: E402
from veil.core.veil_agent import Budget, Chat, Store, load_character  # noqa: E402

from recall_lab import core  # noqa: E402
from recall_lab.judge import JudgeClient  # noqa: E402
from recall_lab.runtime import budget_snapshot  # noqa: E402
from recall_lab.tier12 import JUDGE_SUBSET  # noqa: E402
from recall_lab.tier2_real import ChatClient  # noqa: E402


def _select_verified_representatives(samples):
    """Pick 6 representative samples strictly inside the 36-sample verified judge subset."""
    by_cat = {}
    for s in samples:
        by_cat.setdefault(s.category, []).append(s)
    subset = []
    for cat, n in JUDGE_SUBSET.items():
        subset.extend(by_cat.get(cat, [])[:n])
    by_sub_cat = {}
    for s in subset:
        by_sub_cat.setdefault(s.category, []).append(s)

    chosen = []
    for cat, idx in (
        ('empathy', 0),
        ('empathy', 1),
        ('explicit_recall', 0),
        ('cross_event', 0),
        ('trap', 0),
        ('noise', 0),
    ):
        rows = by_sub_cat.get(cat) or []
        if idx < len(rows) and rows[idx].sample_id not in {x.sample_id for x in chosen}:
            chosen.append(rows[idx])
    return chosen


def _write_isolated_character(work_root: Path, persona_file: Path, *,
                              association_enabled: bool, contextual_enabled: bool):
    (work_root / 'persona').mkdir(parents=True, exist_ok=True)
    (work_root / 'characters' / 'peter').mkdir(parents=True, exist_ok=True)
    (work_root / 'data' / 'peter').mkdir(parents=True, exist_ok=True)
    persona_dst = work_root / 'persona' / 'peter.md'
    if persona_file and persona_file.is_file():
        shutil.copy2(persona_file, persona_dst)
    else:
        persona_dst.write_text('You are Peter, an AI companion.', encoding='utf-8')
    (work_root / 'characters' / 'peter' / 'character.toml').write_text(f'''id = "peter"
name = "Peter"
persona = "persona/peter.md"
provider = "deepseek"
daily_limit_cny = "50"
max_output_tokens = 280
history_rounds = 8

[profile]
enabled = true
learned = true
file = "learned-context.json"
max_bytes = 6200

[memory]
mode = "native"
model = "BAAI/bge-m3"
base_url = "https://api.siliconflow.cn/v1"
input_cny_per_million = "0"

[memory.retrieval]
backend = "brain"
weight_vector = 0.65
weight_keyword = 0.35
stopwords_enabled = false
candidate_k = 8

[memory.recall]
cooldown_turns = 3
association_enabled = {'true' if association_enabled else 'false'}
association_contextual_enabled = {'true' if contextual_enabled else 'false'}
association_min_similarity = 0.52
association_pool_size = 32
association_cooldown_turns = 8

[memory.recall_judge]
enabled = true
timeout_seconds = 4.0
ombre_candidates = 8
vector_candidates = 8

[providers.deepseek]
base_url = "https://api.deepseek.com"
model = "deepseek-chat"
api_key_env = "DEEPSEEK_API_KEY"
max_input_tokens = 32768
input_cny_per_million = "2"
cached_input_cny_per_million = "0.5"
output_cny_per_million = "3"

[tools]
enabled = false
''', encoding='utf-8')


class _CachedEmbedderAdapter:
    """Reuses S4 cached query embeddings (CNY 0) inside real production RecallJudge & BrainIndex."""

    def __init__(self, emb_cache: core.EmbeddingCache):
        self._cache = emb_cache
        self.usage_tokens = None

    def embed(self, text: str):
        return self._cache.embed(text)


class _CachedJudgeModelAdapter:
    """Runs real RecallJudge prompts through cached DeepSeek JudgeClient under BudgetLedger."""

    def __init__(self, judge_client: JudgeClient, cache_path: Path, cache: dict, budget: core.BudgetLedger):
        self.client = judge_client
        self.cache_path = cache_path
        self.cache = cache
        self.budget = budget

    def __call__(self, messages, **kwargs):
        key = core.sha256_text(json.dumps({'model': self.client.model, 'messages': messages},
                                          ensure_ascii=False, sort_keys=True))
        rec = self.cache.get(key)
        if rec is None or rec.get('error'):
            t0 = time.perf_counter()
            res = self.client.call(messages, timeout=float(kwargs.get('request_timeout', 4.0)))
            elapsed = round((time.perf_counter() - t0) * 1000.0, 1)
            getf = (res.get if isinstance(res, dict) else lambda k, d='': getattr(res, k, d))
            pt = int(getf('prompt_tokens', 0) or 0)
            ct = int(getf('completion_tokens', 0) or 0)
            amt = round((pt * 2.0 + ct * 3.0) / 1_000_000.0, 6)
            rec = {
                'key': key, 'text': str(getf('text', '') or ''), 'error': str(getf('error', '') or ''),
                'prompt_tokens': pt, 'completion_tokens': ct, 'latency_ms': elapsed,
                'amount_cny': amt,
            }
            if not rec['error']:
                self.cache[key] = rec
                with self.cache_path.open('a', encoding='utf-8') as fh:
                    fh.write(json.dumps(rec, ensure_ascii=False) + '\n')
        if rec.get('error'):
            raise RuntimeError(rec['error'])
        return rec.get('text') or '{"ids":[]}', int(rec.get('prompt_tokens') or 100), int(rec.get('completion_tokens') or 10), 0


def _run_one_production_chat_turn(sample, *, db_path: Path, learned_context_path: Path,
                                  persona_file: Path, association_enabled: bool,
                                  contextual_enabled: bool, emb_cache: core.EmbeddingCache,
                                  judge_adapter: _CachedJudgeModelAdapter,
                                  chat_client: ChatClient):
    """Execute one full production `Chat.send()` turn in a fresh isolated workspace."""
    with tempfile.TemporaryDirectory() as tmp:
        work_root = Path(tmp)
        _write_isolated_character(
            work_root, persona_file,
            association_enabled=association_enabled,
            contextual_enabled=contextual_enabled,
        )
        shutil.copy2(db_path, work_root / 'data' / 'peter' / 'brain.db')
        if learned_context_path.is_file():
            shutil.copy2(learned_context_path, work_root / 'data' / 'peter' / 'learned-context.json')

        store = Store(work_root / 'data')
        # Seed recent conversation history into chat.jsonl just like real Chat turns
        recent = (sample.context or {}).get('recent') or []
        for idx, row in enumerate(recent[-6:], 1):
            if isinstance(row, dict) and row.get('role') in ('user', 'assistant') and row.get('content'):
                store.append('peter/chat.jsonl', {
                    'role': row['role'],
                    'content': str(row['content'])[:600],
                    'message_id': f'hist-{idx}',
                })

        cfg = load_character(work_root, 'peter')
        budget = Budget(store)
        captured_request = {'messages': None, 'reply': '', 'cost_cny': 0.0, 'latency_ms': 0.0}

        def real_chat_model(messages, **kwargs):
            captured_request['messages'] = list(messages)
            rec = chat_client.call(messages, tier=2, experiment='P6-S5-CHAT-PROD-VERIFY', max_tokens=280)
            text = (rec.get('text') or rec.get('content') or '').strip() or '(empty)'
            captured_request['reply'] = text
            captured_request['cost_cny'] = float(rec.get('cost_cny') or rec.get('amount_cny') or 0.0)
            captured_request['latency_ms'] = float(rec.get('latency_ms') or 0.0)
            return (types.SimpleNamespace(content=text, tool_calls=[]),
                    int(rec.get('prompt_tokens') or 100), int(rec.get('completion_tokens') or 40), 0)

        chat = Chat(cfg, store, budget, real_chat_model, output=[].append)
        authority = BrainAuthority(cfg, store)
        memory = Memory(cfg, store, budget, real_chat_model, authority)
        memory.brain_authority = authority
        memory.attach_recall_judge(cfg, judge_adapter, {**JUDGE_DEFAULTS, 'timeout_seconds': 4.0})
        memory.judge.make_embedder = lambda: _CachedEmbedderAdapter(emb_cache)
        memory.refresh_opening()
        chat.memory = memory

        # Execute REAL production Chat.send()!
        chat.send(sample.query, message_id=f'prod-{sample.sample_id}')

        dbg = dict(getattr(memory, 'last_recall_debug', None) or {})
        cfg_view = recall_config_overview(
            role_id='peter',
            memory_cfg=cfg.memory,
            retrieval_runtime=memory.retrieval_settings,
            recall_runtime=memory.recall_settings,
            judge_enabled=memory.judge.enabled(),
            judge_timeout=memory.judge.settings.get('timeout_seconds'),
        )
        snap_dir = work_root / 'data' / 'peter' / 'request-snapshots'
        snap_file = work_root / 'data' / 'peter' / 'request-snapshots.jsonl'
        if snap_file.is_file():
            snap_count = len(snap_file.read_text(encoding='utf-8').splitlines())
        elif snap_dir.is_dir():
            snap_count = len(list(snap_dir.glob('*')))
        else:
            snap_count = 0
        sent_msgs = captured_request['messages'] or []
        return {
            'reply': captured_request['reply'],
            'cost_cny': captured_request['cost_cny'],
            'latency_ms': captured_request['latency_ms'],
            'sent_message_count': len(sent_msgs),
            'has_relationship_base': bool((dbg.get('relationship_base') or {}).get('items')),
            'request_snapshots_written': snap_count,
            'context_mode': (dbg.get('context_signal') or {}).get('mode', ''),
            'judge_status': (dbg.get('judge') or {}).get('status', ''),
            'judge_outcome': (dbg.get('judge') or {}).get('outcome', ''),
            'judge_picked': list((dbg.get('judge') or {}).get('picked') or []),
            'relevant_recall': list(dbg.get('relevant_recall') or []),
            'natural_association': list(dbg.get('natural_association') or []),
            'association_mode': (dbg.get('association') or {}).get('mode', ''),
            'association_reason': (dbg.get('association') or {}).get('reason', ''),
            'association_similarity': (dbg.get('association') or {}).get('similarity'),
            'story_replaced_count': sum(1 for it in (dbg.get('items') or []) if it.get('story')),
            'lifecycle_delivered': bool((dbg.get('lifecycle') or {}).get('delivered')),
            'lifecycle_rolled_back': bool((dbg.get('lifecycle') or {}).get('rolled_back')),
            'retrieval_stopwords': (dbg.get('retrieval') or {}).get('stopwords_enabled'),
            'retrieval_k': (dbg.get('retrieval') or {}).get('candidate_k'),
            'config_entries_count': len(cfg_view.get('entries') or []),
        }


def main(argv=None):
    p = argparse.ArgumentParser(description='P6-S5 full production Chat.send() verification')
    p.add_argument('--db', required=True)
    p.add_argument('--eval', required=True)
    p.add_argument('--learned-context', required=True)
    p.add_argument('--persona-file', required=True)
    p.add_argument('--env-file', required=True)
    p.add_argument('--cache-dir', required=True)
    p.add_argument('--results-out', required=True)
    p.add_argument('--blind-md', required=True)
    p.add_argument('--blind-map', required=True)
    p.add_argument('--budget-ledger', required=True)
    p.add_argument('--budget-limit-cny', type=float, default=10.0)
    args = p.parse_args(argv)

    from dotenv import dotenv_values
    env = dotenv_values(args.env_file, interpolate=False, encoding='utf-8-sig') if Path(args.env_file).is_file() else {}
    ds_key = (os.environ.get('DEEPSEEK_API_KEY') or env.get('DEEPSEEK_API_KEY') or '').strip()
    if not ds_key:
        raise SystemExit('DEEPSEEK_API_KEY missing')

    snapshot = core.load_snapshot(Path(args.db))
    samples, _ = core.load_eval(Path(args.eval), validate_ids=set(snapshot.memories) | set(snapshot.events))
    reps = _select_verified_representatives(samples)

    cache_dir = Path(args.cache_dir)
    budget = core.BudgetLedger(Path(args.budget_ledger), run_id='p6-s5-prod-chat-verify')
    if hasattr(budget, 'total_cap'):
        budget.total_cap = min(float(budget.total_cap), float(args.budget_limit_cny))
    emb_cache = core.EmbeddingCache(
        cache_dir / 'embeddings.jsonl', model='BAAI/bge-m3',
        base_url='https://api.siliconflow.cn/v1', api_key='', budget=budget, allow_provider=False)
    jcache_path = cache_dir / 's5-judge-calls.jsonl'
    jcache = {}
    if jcache_path.is_file():
        for row in core.read_jsonl(jcache_path):
            if isinstance(row.get('key'), str) and not row.get('error'):
                jcache[row['key']] = row
    judge_adapter = _CachedJudgeModelAdapter(
        JudgeClient(base_url='https://api.deepseek.com', model='deepseek-chat', api_key=ds_key),
        jcache_path, jcache, budget)
    chat_client = ChatClient(
        cache_path=cache_dir / 's5-prod-chat-calls.jsonl',
        base_url='https://api.deepseek.com', model='deepseek-chat',
        api_key=ds_key, budget=budget)

    rng = random.Random(2026112703)
    blind_lines = [
        '# P6-S5 自然联想三路匿名人工体验对照（真实 Chat.send() 完整生产链路组装版｜给 Lan 盲评）',
        '',
        '> 说明：全部 6 组样本均选自 36 条已验证判官子集，并直接通过隔离环境中的真实 `veil_agent.Chat.send()` 完整生产链路组装',
        '> （含真实 `BrainAuthority`、事件故事与事件账本、权限过滤、`LearnedContext`、`relationship_base`、全局 `recall_budget` 与 S5 情境判官）。',
        '> 每组版本 A / B / C 仅改变自然联想机制（关闭 / 现行 / 新情境候选），顺序已随机打乱，隐藏配置名称与 AI 评价。',
        '',
    ]
    blind_map = []
    scenario_summaries = []
    total_cost = 0.0

    for idx_num, sample in enumerate(reps, 1):
        arm_results = {}
        for arm_id, assoc_on, ctx_on in (
            ('off', False, False),
            ('legacy_breath8', True, False),
            ('contextual_s5', True, True),
        ):
            arm_results[arm_id] = _run_one_production_chat_turn(
                sample, db_path=Path(args.db), learned_context_path=Path(args.learned_context),
                persona_file=Path(args.persona_file), association_enabled=assoc_on,
                contextual_enabled=ctx_on, emb_cache=emb_cache,
                judge_adapter=judge_adapter, chat_client=chat_client)
            total_cost += arm_results[arm_id]['cost_cny']

        order = ['off', 'legacy_breath8', 'contextual_s5']
        rng.shuffle(order)
        labels = ['A', 'B', 'C']
        mapping = {lab: arm for lab, arm in zip(labels, order)}
        blind_map.append({
            'group': idx_num, 'sample_id': sample.sample_id, 'category': sample.category,
            'mapping': mapping,
            'natural_association': {arm: arm_results[arm]['natural_association'] for arm in order},
        })
        recent = (sample.context or {}).get('recent') or []
        blind_lines.append(f'## 场景 {idx_num}（类型：{sample.category}）')
        blind_lines.append('')
        if recent:
            blind_lines.append('**近期上文**：')
            for rmsg in recent[-2:]:
                if isinstance(rmsg, dict) and rmsg.get('role') in ('user', 'assistant'):
                    who = 'Lan' if rmsg['role'] == 'user' else 'Peter'
                    blind_lines.append(f'- {who}: {str(rmsg.get("content") or "")[:180]}')
            blind_lines.append('')
        blind_lines.append(f'**Lan 本轮消息**：{sample.query}')
        blind_lines.append('')
        for lab, arm in zip(labels, order):
            blind_lines.append(f'### 版本 {lab}')
            blind_lines.append(arm_results[arm]['reply'])
            blind_lines.append('')
        blind_lines.append('**你的选择**：[ ] 版本 A  [ ] 版本 B  [ ] 版本 C  [ ] 平局  [ ] 弃权  [ ] 有条件偏好（备注：__________）')
        blind_lines.append('')
        blind_lines.append('---')
        blind_lines.append('')

        # Desensitized structural summary for committed JSON (no user text, no memory text, no reply text)
        scenario_summaries.append({
            'sample_id': sample.sample_id,
            'category': sample.category,
            'arms': {
                arm_id: {k: v for k, v in res.items() if k != 'reply'}
                for arm_id, res in arm_results.items()
            },
        })

    Path(args.blind_md).parent.mkdir(parents=True, exist_ok=True)
    Path(args.blind_md).write_text('\n'.join(blind_lines), encoding='utf-8')
    Path(args.blind_map).write_text(json.dumps(blind_map, ensure_ascii=False, indent=2), encoding='utf-8')

    out_payload = {
        'generated_at': time.strftime('%Y-%m-%dT%H:%M:%S+08:00'),
        'verification_tier': 'D_full_production_chat_assembly',
        'default_config_verified': {'stopwords_enabled': False, 'candidate_k': 8, 'judge': 's5_context_judge'},
        'samples_verified': len(reps),
        'total_chat_cost_cny': round(total_cost, 6),
        'budget_snapshot': budget_snapshot(budget),
        'scenarios': scenario_summaries,
    }
    Path(args.results_out).write_text(json.dumps(out_payload, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps({
        'ok': True,
        'samples_verified': len(reps),
        'total_chat_cost_cny': round(total_cost, 6),
        'budget': out_payload['budget_snapshot'],
        'all_delivered': all(
            res['lifecycle_delivered'] and res['request_snapshots_written'] >= 1
            and res['retrieval_stopwords'] is False and res['retrieval_k'] == 8
            for sc in scenario_summaries for res in sc['arms'].values()
        ),
        'contextual_assoc_hits': [
            (sc['sample_id'], sc['category'], sc['arms']['contextual_s5']['natural_association'],
             sc['arms']['contextual_s5']['association_reason'])
            for sc in scenario_summaries
        ],
    }, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
