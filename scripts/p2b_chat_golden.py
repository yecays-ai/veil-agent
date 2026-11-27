"""P2-B golden set from real chat turns: expected = the memories the old system actually injected.

This is agreement with past production behaviour, not ground truth, but it is a much larger sample
than the 7 logged candidate lists. Read-only.
"""
import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from veil.brain.recall import brain_retrieval
from dotenv import dotenv_values
from veil.brain.recall.recall_relevance import QueryEmbedder


def read(path):
    rows = []
    for line in Path(path).read_text(encoding='utf-8').splitlines():
        if line.strip():
            try:
                rows.append(json.loads(line))
            except ValueError:
                pass
    return rows


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument('--db', required=True)
    parser.add_argument('--chat', required=True)
    parser.add_argument('--env-file', default=None)
    parser.add_argument('--limit', type=int, default=50)
    parser.add_argument('--out', required=True)
    args = parser.parse_args(argv)
    users, expected = {}, {}
    for row in read(args.chat):
        request = row.get('request_id')
        if not isinstance(request, str):
            continue
        if row.get('role') == 'user' and isinstance(row.get('content'), str):
            users.setdefault(request, row['content'])
        elif row.get('role') == 'assistant' and isinstance(row.get('memory_refs'), list) and row['memory_refs']:
            expected.setdefault(request, [str(item) for item in row['memory_refs']])
    cases = [(request, users[request], expected[request]) for request in expected if request in users]
    cases = cases[-args.limit:] if args.limit else cases
    values = dotenv_values(args.env_file, interpolate=False, encoding='utf-8-sig') if args.env_file else {}
    key = (values.get('EMBED_API_KEY') or '').strip()
    embedder = QueryEmbedder('https://api.siliconflow.cn/v1', 'BAAI/bge-m3', key) if key else None
    hits, overlaps, items = 0, 0, []
    for request, query, want in cases:
        try:
            _text, meta = brain_retrieval.search_text(args.db, query, limit=5, embedder=embedder)
        except Exception as exc:
            items.append({'request_id': request, 'error': type(exc).__name__})
            continue
        overlap = len(set(want) & set(meta['ids']))
        hit = overlap > 0
        hits += int(hit)
        overlaps += overlap
        items.append({'request_id': request, 'expected': want, 'got': meta['ids'],
                      'overlap': overlap, 'hit': hit})
    result = {'cases': len(cases), 'hits': hits,
              'hit_rate': round(hits / len(cases), 3) if cases else 0,
              'mean_overlap': round(overlaps / len(cases), 2) if cases else 0,
              'note': 'expected = memories the old system injected in past real chat; agreement proxy',
              'items': items}
    Path(args.out).write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding='utf-8')
    print(json.dumps({key: value for key, value in result.items() if key != 'items'}, ensure_ascii=False))


if __name__ == '__main__':
    main()
