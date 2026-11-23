"""P2-B real A/B on the same memories: Brain vs an isolated Ombre instance.

Reads 30 real chat turns whose replies carried memory_refs, runs both searches, and reports
agreement with the old injected memories plus latency. Does not touch the live Peter.
"""
import argparse
import json
import re
import shutil
import socket
import sqlite3
import sys
import time
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import brain_events
import brain_export
import brain_retrieval
import ombre_resident
from dotenv import dotenv_values
from memory_agent import MCP
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


def ids_from(text):
    match = re.search(r'=== ombre:result-ids ===\s*```json\s*(.*?)\s*```', text, re.S)
    if not match:
        return []
    try:
        return [str(item) for item in json.loads(match.group(1)).get('bucket_ids') or []]
    except ValueError:
        return []


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument('--source-brain', required=True)
    parser.add_argument('--events', required=True)
    parser.add_argument('--stages', required=True)
    parser.add_argument('--chat', required=True)
    parser.add_argument('--env-file', required=True)
    parser.add_argument('--workdir', required=True)
    parser.add_argument('--limit', type=int, default=30)
    parser.add_argument('--veil-root', default=r'E:\Peter-Agent\VEIL\veil-agent')
    args = parser.parse_args(argv)
    work = Path(args.workdir)
    if work.exists():
        shutil.rmtree(work)
    work.mkdir(parents=True)
    db = work / 'brain.db'
    shutil.copyfile(args.source_brain, db)
    connection = sqlite3.connect(db)
    try:
        brain_events.import_events(connection, args.events, args.stages)
    finally:
        connection.close()
    brain_export.export_vault(db, work / 'fake')
    values = dotenv_values(args.env_file, interpolate=False, encoding='utf-8-sig')
    embedder = QueryEmbedder('https://api.siliconflow.cn/v1', 'BAAI/bge-m3',
                             values['EMBED_API_KEY'].strip())
    users, expected = {}, {}
    for row in read(args.chat):
        request = row.get('request_id')
        if not isinstance(request, str):
            continue
        if row.get('role') == 'user' and isinstance(row.get('content'), str):
            users.setdefault(request, row['content'])
        elif row.get('role') == 'assistant' and row.get('memory_refs'):
            expected.setdefault(request, [str(item) for item in row['memory_refs']])
    cases = [(request, users[request], expected[request]) for request in expected if request in users]
    cases = cases[-args.limit:]
    sock = socket.socket(); sock.bind(('127.0.0.1', 0)); port = sock.getsockname()[1]; sock.close()
    cfg = SimpleNamespace(id='fake', name='Fake', memory={
        'ombre_port': port, 'model': 'BAAI/bge-m3',
        'base_url': 'https://api.siliconflow.cn/v1', 'embedding_enabled': True,
        'ombre_model': False, 'input_cny_per_million': '0',
    }, provider={'api_key_env': 'DEEPSEEK_API_KEY', 'model': 'deepseek-flash',
                 'base_url': 'https://api.deepseek.com'})
    process = None
    items = []
    try:
        started, token, process = ombre_resident.start_or_connect(Path(args.veil_root), cfg, data_root=work)
        client = MCP('http://127.0.0.1:%d/mcp' % started, token)
        try:
            for request, query, want in cases:
                old_start = time.monotonic()
                old_text = client.call('breath_search', {'query': query, 'mode': 'automatic',
                                                         'with_ids': True, 'max_results': 5})
                old_ms = (time.monotonic() - old_start) * 1000
                old_ids = ids_from(old_text)
                brain_start = time.monotonic()
                _text, meta = brain_retrieval.search_text(db, query, limit=5, embedder=embedder)
                brain_ms = (time.monotonic() - brain_start) * 1000
                items.append({'request_id': request, 'expected': want, 'ombre': old_ids,
                              'brain': meta['ids'], 'old_hits_expected': len(set(want) & set(old_ids)),
                              'brain_hits_expected': len(set(want) & set(meta['ids'])),
                              'agreement': len(set(old_ids) & set(meta['ids'])),
                              'old_ms': round(old_ms, 1), 'brain_ms': round(brain_ms, 1)})
        finally:
            client.close()
    finally:
        if process is not None:
            process.close()
    result = {'cases': len(items),
              'old_hit': sum(1 for item in items if item['old_hits_expected']),
              'brain_hit': sum(1 for item in items if item['brain_hits_expected']),
              'mean_agreement': round(sum(item['agreement'] for item in items) / len(items), 2) if items else 0,
              'old_ms_mean': round(sum(item['old_ms'] for item in items) / len(items), 1) if items else 0,
              'brain_ms_mean': round(sum(item['brain_ms'] for item in items) / len(items), 1) if items else 0,
              'items': items}
    (work / 'ab-report.json').write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding='utf-8')
    print(json.dumps({key: value for key, value in result.items() if key != 'items'}, ensure_ascii=False))


if __name__ == '__main__':
    main()
