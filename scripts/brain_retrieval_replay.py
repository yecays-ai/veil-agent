"""Ticket 430: replay real search queries against Brain retrieval and compare with Ombre's hits.

Reads only local ledgers:
  data/<role>/tools.jsonl            successful breath_search rows carry the query text + request id
  data/<role>/recall-diagnostics.jsonl  the Ombre candidate ids for that request
  data/<role>/brain.db               Brain's own store (shadow sync / P0-A import)

Overlap is diagnostic only; the report lists differences for a quality review.
The vector channel is OFF unless --embed is passed, and every run has a query cap and an estimated
spend cap because each vector query is a paid embedding request.
"""
import argparse
import json
import math
import statistics
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import brain_retrieval


def read_jsonl(path, required):
    rows = []
    try:
        with open(path, encoding='utf-8') as handle:
            for line in handle:
                line = line.strip()
                if line:
                    try:
                        rows.append(json.loads(line))
                    except ValueError:
                        continue
    except OSError as exc:
        if required:
            raise SystemExit('cannot read required ledger %s: %s' % (path, exc))
        print('warning: optional ledger missing: %s' % path, file=sys.stderr)
    return rows


def old_hits(diagnostics_path):
    by_request = {}
    for row in read_jsonl(diagnostics_path, required=True):
        request = row.get('request_id')
        ids = row.get('ombre')
        if not isinstance(request, str) or not isinstance(ids, list) or not ids:
            continue
        if request not in by_request:
            by_request[request] = [str(item) for item in ids]
    return by_request


def queries(tools_path):
    out = []
    for row in read_jsonl(tools_path, required=True):
        if row.get('tool') != 'breath_search' or row.get('state') != '成功':
            continue
        query = row.get('path')
        request = row.get('request_id')
        if not isinstance(query, str) or not query.strip() or not isinstance(request, str):
            continue
        out.append({'request_id': request, 'query': query})
    if not out:
        raise SystemExit('no successful breath_search rows found in tools.jsonl')
    return out


class Embedder:
    """Only created with --embed; a missing key or provider setup is a hard error, not a silent downgrade."""

    def __init__(self, env_file, model, base):
        from dotenv import dotenv_values
        from recall_relevance import QueryEmbedder
        if not Path(env_file).is_file():
            raise SystemExit('--embed needs an env file with EMBED_API_KEY: %s' % env_file)
        values = dotenv_values(Path(env_file), interpolate=False, encoding='utf-8-sig')
        key = (values.get('EMBED_API_KEY') or '').strip()
        if not key:
            raise SystemExit('EMBED_API_KEY is missing from %s' % env_file)
        self.embedder = QueryEmbedder(base, model, key)

    def embed(self, text):
        return self.embedder.embed(text)


def percentile(values, fraction):
    if not values:
        return 0.0
    ordered = sorted(values)
    rank = max(1, min(len(ordered), int(math.ceil(fraction * len(ordered)))))
    return ordered[rank - 1]


def check_weights(parser, vector, keyword):
    for name, value in (('--weight-vector', vector), ('--weight-keyword', keyword)):
        if not 0 <= value <= 1:
            parser.error('%s must be between 0 and 1' % name)
    if vector + keyword <= 0:
        parser.error('the two weights cannot both be zero')


def run(args):
    check_weights(args, args.weight_vector, args.weight_keyword)
    diagnostics = old_hits(Path(args.diagnostics))
    rows = queries(Path(args.tools))
    if args.max_queries and len(rows) > args.max_queries:
        rows = rows[:args.max_queries]
    embedder = Embedder(args.env_file, args.embed_model, args.embed_base) if args.embed else None
    settings = brain_retrieval.load_settings({'weight_vector': args.weight_vector,
                                              'weight_keyword': args.weight_keyword})
    results = []
    estimated_spend = 0.0
    stopped = ''
    for row in rows:
        if embedder is not None:
            estimated = len(row['query'].encode('utf-8')) * args.rate_cny_per_million / 1_000_000
            if estimated_spend + estimated > args.budget_cny:
                stopped = 'estimated spend cap %.4f CNY reached' % args.budget_cny
                break
            estimated_spend += estimated
        expected = diagnostics.get(row['request_id']) or []
        try:
            text, meta = brain_retrieval.search_text(
                Path(args.db), row['query'], limit=max(3, len(expected) or args.limit),
                embedder=embedder, settings=settings)
        except Exception as exc:
            results.append({'query': row['query'], 'error': type(exc).__name__})
            continue
        new = meta['ids']
        results.append({
            'query': row['query'], 'old': expected, 'new': new,
            'top1_match': bool(expected and new and expected[0] == new[0]),
            'overlap': len(set(expected) & set(new)),
            'old_only': [key for key in expected if key not in new],
            'new_only': [key for key in new if key not in expected],
            'latency_ms': meta['elapsed_ms'],
        })
    ok = [item for item in results if 'error' not in item]
    latencies = [item['latency_ms'] for item in ok]
    summary = {
        'queries': len(results), 'compared': len(ok), 'errors': len(results) - len(ok),
        'embedding_channel': bool(embedder), 'stopped_by': stopped,
        'weights': {'vector': round(settings['weight_vector'], 3),
                    'keyword': round(settings['weight_keyword'], 3)},
        'top1_match_rate': round(sum(1 for item in ok if item['top1_match']) / len(ok), 4) if ok else 0,
        'mean_overlap': round(statistics.mean(item['overlap'] for item in ok), 2) if ok else 0,
        'old_only_total': sum(len(item['old_only']) for item in ok),
        'new_only_total': sum(len(item['new_only']) for item in ok),
        'latency_mean_ms': round(statistics.mean(latencies), 1) if latencies else 0,
        'latency_p95_ms': round(percentile(latencies, 0.95), 1),
        'estimated_embed_cny': round(estimated_spend, 6),
    }
    differences = [item for item in ok if item['old_only'] or item['new_only']]
    lines = ['# Brain retrieval replay (ticket 430)', '',
             '- queries: %(queries)s (compared %(compared)s, errors %(errors)s)' % summary,
             '- vector channel: %s' % ('on' if summary['embedding_channel'] else 'off'),
             '- weights vector/keyword: %(vector)s / %(keyword)s' % summary['weights'],
             '- Top-1 match rate: %(top1_match_rate)s (diagnostic only)' % summary,
             '- mean overlap: %(mean_overlap)s' % summary,
             '- old-only hits: %(old_only_total)s; new-only hits: %(new_only_total)s' % summary,
             '- latency mean/p95 ms: %(latency_mean_ms)s / %(latency_p95_ms)s' % summary,
             '- estimated embedding spend: %(estimated_embed_cny)s CNY' % summary]
    if stopped:
        lines.append('- stopped early: ' + stopped)
    lines += ['', 'Overlap is a diagnostic, not a pass mark. Review the differences by quality.', '',
              '## Differences (first %d)' % args.sample]
    for item in differences[:args.sample]:
        old = ', '.join(item['old']) or '-'
        lines.append('- %s' % item['query'])
        lines.append('  old: %s' % old)
        lines.append('  new: %s' % (', '.join(item['new']) or '-'))
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text('\n'.join(lines) + '\n', encoding='utf-8')
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0 if not summary['errors'] else 1


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument('--db', default='data/peter/brain.db')
    parser.add_argument('--tools', default='data/peter/tools.jsonl')
    parser.add_argument('--diagnostics', default='data/peter/recall-diagnostics.jsonl')
    parser.add_argument('--out', default='data/peter/brain-replay.md',
                        help='keep the report inside the ignored data directory')
    parser.add_argument('--limit', type=int, default=8)
    parser.add_argument('--sample', type=int, default=30)
    parser.add_argument('--max-queries', type=int, default=50,
                        help='0 disables the cap; every vector query costs money')
    parser.add_argument('--embed', action='store_true', help='enable the paid vector channel')
    parser.add_argument('--budget-cny', type=float, default=0.05)
    parser.add_argument('--rate-cny-per-million', type=float, default=0.07)
    parser.add_argument('--env-file', default=str(ROOT / '.env'))
    parser.add_argument('--embed-model', default='BAAI/bge-m3')
    parser.add_argument('--embed-base', default='https://api.siliconflow.cn/v1')
    parser.add_argument('--weight-vector', type=float, default=0.65)
    parser.add_argument('--weight-keyword', type=float, default=0.35)
    return run(parser.parse_args(argv))


if __name__ == '__main__':
    raise SystemExit(main())
