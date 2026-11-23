"""P2-B golden-set proxy: the real queries that still have logged Ombre candidates.

Only 17 request ids have old candidate ids, so this is a diagnostic, not a full A/B. It never writes.
"""
import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import brain_retrieval
from dotenv import dotenv_values
from recall_relevance import QueryEmbedder


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
    parser.add_argument('--tools', required=True)
    parser.add_argument('--diagnostics', required=True)
    parser.add_argument('--env-file', default=str(ROOT / '.env'))
    parser.add_argument('--out', required=True)
    args = parser.parse_args(argv)
    old = {}
    for row in read(args.diagnostics):
        if isinstance(row.get('request_id'), str) and isinstance(row.get('ombre'), list) and row['ombre']:
            old.setdefault(row['request_id'], [str(item) for item in row['ombre']])
    queries = {}
    for row in read(args.tools):
        if row.get('tool') == 'breath_search' and row.get('state') == '成功' and row.get('request_id') in old:
            queries.setdefault(row['request_id'], row.get('path'))
    values = dotenv_values(args.env_file, interpolate=False, encoding='utf-8-sig')
    key = (values.get('EMBED_API_KEY') or '').strip()
    embedder = QueryEmbedder('https://api.siliconflow.cn/v1', 'BAAI/bge-m3', key) if key else None
    items, hits = [], 0
    for request_id, query in queries.items():
        if not query:
            continue
        try:
            _text, meta = brain_retrieval.search_text(args.db, query, limit=5, embedder=embedder)
        except Exception as exc:
            items.append({'request_id': request_id, 'error': type(exc).__name__})
            continue
        expected = old[request_id]
        overlap = len(set(expected) & set(meta['ids']))
        hit = overlap > 0
        hits += int(hit)
        items.append({'request_id': request_id, 'expected': expected, 'got': meta['ids'],
                      'overlap': overlap, 'hit': hit})
    result = {'queries_with_old_candidates': len(items), 'hits': hits,
              'hit_rate': round(hits / len(items), 3) if items else 0,
              'coverage_note': 'only requests with logged Ombre ids; not a full A/B', 'items': items}
    Path(args.out).write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding='utf-8')
    print(json.dumps({key: value for key, value in result.items() if key != 'items'}, ensure_ascii=False))


if __name__ == '__main__':
    main()
