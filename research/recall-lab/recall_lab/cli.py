"""CLI entry point: load a read-only snapshot, run the selected ablations, write results.

    cd recall-lab
    python -m recall_lab.cli --eval ../.local-tmp/s4/eval-v2.jsonl \
        --db ../.local-tmp/s4-prep/isolated/brain.db \
        --chat ../.local-tmp/s4-prep/isolated/chat.jsonl \
        --learned-context ../.local-tmp/s4/extra/learned-context.json \
        --versions ../.local-tmp/s4/extra/learned-context.versions.jsonl \
        --env-file ../../../veil-agent/.env \
        --cache-dir ../.local-tmp/s4/cache --budget-ledger ../.local-tmp/s4/budget-ledger.jsonl \
        --detail-dir ../.local-tmp/s4/runs --tier 0

No production file is written. --fake-embedder exists only to exercise the harness without a
provider; real ablations require the configured embedding endpoint and a read-only env file.
"""
from __future__ import annotations

import argparse
import getpass
import json
import os
import platform
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from recall_lab import core  # noqa: E402
from recall_lab.runtime import Context, snapshot_fingerprint  # noqa: E402

ORDER = [
    # Tier 0
    'AB-R1a', 'AB-R1b', 'AB-R1c', 'AB-R4b', 'AB-R4c', 'AB-R5a', 'AB-R5b', 'AB-R5c',
    'AB-A1a', 'AB-A2', 'AB-A3',
    # Tier 1
    'AB-R4a', 'AB-R2', 'AB-R3a', 'AB-R3b', 'AB-R3c', 'AB-R3d',
    # Tier 2
    'AB-C1', 'AB-C2a', 'AB-C2b', 'AB-C3', 'AB-A1b', 'AB-E1', 'AB-COMB',
]
TIER_OF = {experiment_id: 0 for experiment_id in ORDER[:11]}
TIER_OF.update({experiment_id: 1 for experiment_id in ORDER[11:17]})
TIER_OF.update({experiment_id: 2 for experiment_id in ORDER[17:]})


def _keys(args):
    values = {}
    if args.env_file:
        from dotenv import dotenv_values
        values = dotenv_values(args.env_file, interpolate=False, encoding='utf-8-sig')
    def pick(env_name):
        return (os.environ.get(env_name) or '').strip() or str(values.get(env_name) or '').strip()
    return pick


def _load_json(path):
    with Path(path).open('r', encoding='utf-8') as handle:
        return json.load(handle)


def _code_commit():
    try:
        return subprocess.run(['git', 'rev-parse', 'HEAD'], cwd=str(ROOT), capture_output=True,
                              text=True, timeout=10).stdout.strip()
    except Exception:
        return ''


def build_parser():
    parser = argparse.ArgumentParser(description='P6-S4 isolated recall ablations')
    parser.add_argument('--eval', required=True, help='eval-v2 JSONL (local copy)')
    parser.add_argument('--db', required=True, help='isolated brain.db snapshot (read-only)')
    parser.add_argument('--chat', help='isolated chat.jsonl (evidence verification)')
    parser.add_argument('--learned-context', help='isolated learned-context.json')
    parser.add_argument('--versions', help='isolated learned-context.versions.jsonl')
    parser.add_argument('--context-log', help='isolated context-log.jsonl')
    parser.add_argument('--recall-diagnostics', help='isolated recall-diagnostics.jsonl')
    parser.add_argument('--capabilities', help='isolated capabilities.json')
    parser.add_argument('--persona-file', help='read-only local persona markdown for Tier 2 replies (never committed)')
    parser.add_argument('--env-file', help='read-only .env to read keys from (never written)')
    parser.add_argument('--cache-dir', default='.local/cache', help='embedding/judge cache dir')
    parser.add_argument('--results-dir', default='results', help='committed aggregate results dir')
    parser.add_argument('--detail-dir', default='.local/runs', help='gitignored per-sample detail dir')
    parser.add_argument('--budget-ledger', default='.local/budget-ledger.jsonl')
    parser.add_argument('--tier', default='all', help='0 / 1 / 2 / all / comma list')
    parser.add_argument('--experiments', help='comma list of experiment ids (default: selection)')
    parser.add_argument('--fake-embedder', action='store_true',
                        help='exercise the harness with a local fake embedder (not for real ablations)')
    parser.add_argument('--skip-paid', action='store_true',
                        help='run only Tier 0 with cached embeddings; Tier 1/2 stay not_run and '
                             'no provider call is allowed')
    parser.add_argument('--embedding-rate-cny-per-million', type=float, default=None,
                        help='declared embedding price per million tokens; required for provider runs '
                             '(0.0 only when the endpoint is declared free/covered)')
    parser.add_argument('--embed-base-url', default='https://api.siliconflow.cn/v1')
    parser.add_argument('--embed-model', default='BAAI/bge-m3')
    parser.add_argument('--judge-base-url', default='https://api.deepseek.com')
    parser.add_argument('--judge-model', default='deepseek-flash')
    parser.add_argument('--list', action='store_true', help='print experiment ids and exit')
    return parser


def _select(args):
    """(selected, skipped_paid). Experiment ids are matched case-insensitively to canonical ids."""
    canonical = {experiment_id.casefold(): experiment_id for experiment_id in ORDER}
    if args.experiments:
        chosen, unknown = [], []
        for raw in args.experiments.split(','):
            item = raw.strip()
            if not item:
                continue
            canonical_id = canonical.get(item.casefold())
            if canonical_id is None:
                unknown.append(item)
            elif canonical_id not in chosen:
                chosen.append(canonical_id)
        if unknown:
            raise SystemExit('unknown experiment ids: %s (known: %s)'
                             % (', '.join(unknown), ', '.join(ORDER)))
        selected = chosen
    elif args.tier == 'all':
        selected = list(ORDER)
    else:
        tiers = {int(item) for item in str(args.tier).split(',') if item.strip()}
        selected = [item for item in ORDER if TIER_OF[item] in tiers]
    if args.skip_paid:
        skipped = [item for item in selected if TIER_OF[item] > 0]
        return [item for item in selected if TIER_OF[item] == 0], skipped
    return selected, []


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.list:
        for experiment_id in ORDER:
            print('%s\ttier %d' % (experiment_id, TIER_OF[experiment_id]))
        return 0
    experiments, skipped_paid = _select(args)
    snapshot = core.load_snapshot(args.db)
    known_ids = set(snapshot.memories) | set(snapshot.events)
    samples, eval_report = core.load_eval(args.eval, validate_ids=known_ids)
    chat_message_ids, chat_rows = (set(), [])
    if args.chat:
        chat_message_ids, chat_rows = core.load_chat_user_messages(args.chat)
    if skipped_paid:
        print('--skip-paid: not running paid experiments: %s' % ', '.join(skipped_paid))
    options = {
        'chat_user_message_ids': chat_message_ids,
        'chat_rows': chat_rows,
        'learned_context': _load_json(args.learned_context) if args.learned_context else None,
        'learned_context_path': args.learned_context,
        'versions_path': args.versions,
        'embed_api_key': '',
        'embed_base_url': args.embed_base_url,
        'context_log': args.context_log,
        'recall_diagnostics': args.recall_diagnostics,
        'capabilities': _load_json(args.capabilities) if args.capabilities else {},
        'persona_text': (Path(args.persona_file).read_text(encoding='utf-8-sig').strip()
                         if args.persona_file and Path(args.persona_file).is_file() else ''),
        'persona_loaded': bool(args.persona_file and Path(args.persona_file).is_file()),
        'cache_dir': args.cache_dir,
        'judge_base_url': args.judge_base_url,
        'judge_model': args.judge_model,
        'embedding_mode': ('fake' if args.fake_embedder
                           else ('cache-only' if args.skip_paid else 'provider')),
    }
    pick = _keys(args)
    embedding_rate = args.embedding_rate_cny_per_million
    if args.fake_embedder:
        core.configure_embedding_price(0.0, source='fake embedder (harness validation only)')
    elif args.skip_paid:
        # Cache-only: a cache miss must fail the experiment instead of silently paying for it.
        core.configure_embedding_price(0.0 if embedding_rate is None else embedding_rate,
                                       source='cache-only run (no provider call allowed)')
    else:
        if embedding_rate is None:
            raise SystemExit(
                'embedding price is not declared; pass --embedding-rate-cny-per-million X '
                '(use 0.0 only when the embedding endpoint is declared free/covered). '
                'An unknown price is refused so the spend ledger cannot silently undercount.')
        core.configure_embedding_price(embedding_rate,
                                       source='--embedding-rate-cny-per-million')
    budget = core.BudgetLedger(args.budget_ledger,
                               run_id='recall-lab-%s' % time.strftime('%Y%m%d-%H%M%S'))
    if args.fake_embedder:
        embedder = core.FakeEmbedder()
    elif args.skip_paid:
        embedder = core.EmbeddingCache(Path(args.cache_dir) / 'embeddings.jsonl',
                                       model=args.embed_model, base_url=args.embed_base_url,
                                       api_key='', budget=budget, allow_provider=False)
    else:
        api_key = pick('EMBED_API_KEY')
        if not api_key:
            raise SystemExit('no EMBED_API_KEY supplied (pass --env-file or set the environment)')
        embedder = core.EmbeddingCache(Path(args.cache_dir) / 'embeddings.jsonl',
                                       model=args.embed_model, base_url=args.embed_base_url,
                                       api_key=api_key, budget=budget)
        options['embed_api_key'] = api_key
    if not args.skip_paid and not args.fake_embedder and any(TIER_OF[item] > 0 for item in experiments):
        judge_key = pick('DEEPSEEK_API_KEY')
        if not judge_key:
            raise SystemExit('Tier 1/2 selected but no DEEPSEEK_API_KEY supplied')
        options['judge_api_key'] = judge_key
    expected_ids = list(ORDER)
    ctx = Context(snapshot=snapshot, samples=samples, eval_report=eval_report, embedder=embedder,
                  budget=budget, results_dir=args.results_dir, detail_dir=args.detail_dir,
                  options=options)
    # Resume/chain support: results already on disk take part in the dependency chain (the
    # single-variable experiments read the winner of the previous one), so a later invocation
    # or a rerun after an interruption never silently falls back to defaults.
    _load_existing_results(ctx, results_dir=Path(args.results_dir))
    print('snapshot: %d memories (%d visible), %d events, %d relations; eval samples: %d'
          % (snapshot.stats['memories'], snapshot.stats['visible_memories'],
             snapshot.stats['events'], snapshot.stats['relations'], len(samples)))
    if options['embedding_mode'] == 'fake':
        print('WARNING: fake embedder: results only validate the harness, not the real baseline')
    started = time.time()
    runners = _runners()
    for experiment_id in experiments:
        runner = runners.get(experiment_id)
        if runner is None:
            continue
        print('running %s ...' % experiment_id, flush=True)
        try:
            result = runner(ctx)
        except core.BudgetBlocked as exc:
            result = core.result_stub(experiment_id, TIER_OF[experiment_id],
                                      title=experiment_id, variable='-', fixed='-')
            result.update({'status': 'blocked', 'verdict': {'recommended': None,
                                                           'reason': 'budget guard: %s' % exc}})
        except Exception as exc:   # a single experiment must not lose the others
            result = core.result_stub(experiment_id, TIER_OF[experiment_id],
                                      title=experiment_id, variable='-', fixed='-')
            result.update({'status': 'blocked',
                           'verdict': {'recommended': None,
                                       'reason': 'harness error: %s: %s' % (type(exc).__name__, exc)},
                           'evidence': {'traceback_kind': type(exc).__name__}})
            print('  ! %s failed: %s: %s' % (experiment_id, type(exc).__name__, exc))
        result['status'] = result.get('status') or 'not_run'
        if options['embedding_mode'] == 'fake' and result['status'] == 'completed':
            result.setdefault('evidence', {})['embedding_mode'] = 'fake (harness validation only)'
        path = ctx.save_result(result)
        print('  %s -> %s (%s)' % (experiment_id, result['status'], path.name),
              flush=True)
    embedding_stats = ctx.charge_embeddings('PREP-embedding',
                                            note='one-off query/story embedding preparation')
    coverage = _coverage(ctx, expected_ids, skipped_paid=skipped_paid)
    manifest = {
        'harness_version': core.HARNESS_VERSION,
        'generated_at': core.utc_now(),
        'code_commit': _code_commit(),
        'python': platform.python_version(),
        'platform': sys.platform,
        'embedding_mode': options['embedding_mode'],
        'embedding': embedding_stats,
        'prices': json.loads(json.dumps(core.PRICES)),
        'tier_caps_cny': core.TIER_CAPS_CNY,
        'total_cap_cny': core.TOTAL_CAP_CNY,
        'snapshot': snapshot_fingerprint(snapshot, eval_report, extra_files={
            'chat': args.chat, 'learned_context': args.learned_context,
            'versions': args.versions, 'context_log': args.context_log,
            'recall_diagnostics': args.recall_diagnostics}),
        'eval': eval_report,
        'budget': budget.totals(),
        'experiments_run': experiments,
        'experiments_skipped_paid': skipped_paid,
        'skip_paid': bool(args.skip_paid),
        'coverage': coverage,
        'elapsed_seconds': round(time.time() - started, 1),
    }
    results_dir = Path(args.results_dir)
    results_dir.mkdir(parents=True, exist_ok=True)
    (results_dir / 'run-manifest.json').write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + '\n', encoding='utf-8')
    (results_dir / 'coverage.json').write_text(
        json.dumps(coverage, ensure_ascii=False, indent=2, sort_keys=True) + '\n', encoding='utf-8')
    print('budget: %s' % json.dumps(budget.totals(), ensure_ascii=False))
    print('embedding prep: %s' % json.dumps(embedding_stats, ensure_ascii=False))
    print('coverage: %s' % json.dumps(coverage.get('status_counts'), ensure_ascii=False))
    return 0


def _load_existing_results(ctx, *, results_dir):
    if not results_dir.is_dir():
        return
    for experiment_id in ORDER:
        path = results_dir / ('%s.json' % experiment_id.replace('-', '_').lower())
        if not path.is_file():
            continue
        try:
            with path.open('r', encoding='utf-8') as handle:
                loaded = json.load(handle)
        except (OSError, ValueError):
            continue
        if isinstance(loaded, dict) and loaded.get('experiment_id') == experiment_id:
            ctx.results[experiment_id] = loaded


def _runners():
    from recall_lab import tier0, tier12
    return {
        'AB-R1a': tier0.run_r1a, 'AB-R1b': tier0.run_r1b, 'AB-R1c': tier0.run_r1c,
        'AB-R4b': tier0.run_r4b, 'AB-R4c': tier0.run_r4c, 'AB-R5a': tier0.run_r5a,
        'AB-R5b': tier0.run_r5b, 'AB-R5c': tier0.run_r5c, 'AB-A1a': tier0.run_a1a,
        'AB-A2': tier0.run_a2, 'AB-A3': tier0.run_a3,
        'AB-R4a': tier12.run_r4a, 'AB-R2': tier12.run_r2, 'AB-R3a': tier12.run_r3a,
        'AB-R3b': tier12.run_r3b, 'AB-R3c': tier12.run_r3c, 'AB-R3d': tier12.run_r3d,
        'AB-C1': tier12.run_c1, 'AB-C2a': tier12.run_c2a, 'AB-C2b': tier12.run_c2b,
        'AB-C3': tier12.run_c3, 'AB-A1b': tier12.run_a1b, 'AB-E1': tier12.run_e1,
        'AB-COMB': tier12.run_comb,
    }


def _coverage(ctx, expected_ids, skipped_paid=()):
    skipped = set(skipped_paid)
    rows = []
    for experiment_id in expected_ids:
        result = ctx.results.get(experiment_id) or {}
        verdict = result.get('verdict') or {}
        if experiment_id in skipped:
            rows.append({'experiment_id': experiment_id, 'tier': TIER_OF[experiment_id],
                         'title': result.get('title') or experiment_id,
                         'status': 'not_run', 'recommended': None,
                         'reason': 'skipped by --skip-paid in this invocation'})
            continue
        rows.append({'experiment_id': experiment_id, 'tier': TIER_OF[experiment_id],
                     'title': result.get('title') or experiment_id,
                     'status': result.get('status') or 'not_run',
                     'recommended': verdict.get('recommended'),
                     'reason': verdict.get('reason', 'not run in this invocation')})
    counts = {}
    for row in rows:
        counts[row['status']] = counts.get(row['status'], 0) + 1
    return {'matrix_ids': list(expected_ids), 'count': len(expected_ids),
            'status_counts': counts, 'experiments': rows,
            'note': 'P6-4 正文写 21 项原子实验、工单描述写 22 项，实际矩阵表列出 24 个 ID（23 原子 + AB-COMB 组合）；本 harness 按实际表逐行覆盖 24 个。'}


if __name__ == '__main__':
    sys.exit(main())
