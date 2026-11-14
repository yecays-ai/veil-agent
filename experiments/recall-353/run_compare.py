"""Ticket 353: before/after comparison of automatic recall on a fictional library (not run in CI).

    python experiments/recall-353/run_compare.py seed   --runtime <checkout with .env and .deps/ombre>
    python experiments/recall-353/run_compare.py run    --runtime <...> --code <dir> --label main
    python experiments/recall-353/run_compare.py run    --runtime <...> --code <dir> --label branch [--assisted 0.5]
    python experiments/recall-353/run_compare.py report --runtime <...>

Everything is written under <runtime>/.local-tmp/recall-353/. Ombre runs as a separate, isolated
instance on a random loopback port with its own vault; Peter's real memory, chat and backups are
never opened. All model traffic goes through the 017 budget proxy with a hard cap of 2 CNY.
`--code` is a folder holding the memory_agent.py (and helpers) to test; it is put first on the
import path, so `main` and `branch` differ only in that code.
"""
import argparse
import json
import shutil
import socket
import sys
import time
from decimal import Decimal
from pathlib import Path

HERE = Path(__file__).resolve().parent
PROXY_HOST = '127.0.0.1'
COST_CAP = Decimal('2')
PAID_LIMIT = 300


def options():
    parser = argparse.ArgumentParser()
    parser.add_argument('phase', choices=('seed', 'run', 'report'))
    parser.add_argument('--runtime', required=True)
    parser.add_argument('--code')
    parser.add_argument('--label')
    parser.add_argument('--assisted', type=float)
    parser.add_argument('--no-replies', action='store_true')
    return parser.parse_args()


def setup_paths(runtime, code=None):
    for path in (runtime, *( [code] if code else [] )):
        sys.path.insert(0, str(path))
    sys.path.insert(0, str(HERE.parent / 'memory-quality-017'))
    sys.path.insert(0, str(HERE))
    import budget_proxy
    budget_proxy.TOTAL_COST_CAP = COST_CAP
    budget_proxy.PAID_REQUEST_LIMIT = PAID_LIMIT


class Proxy:
    def __init__(self, runtime, work):
        from http.server import ThreadingHTTPServer
        import threading
        from dotenv import dotenv_values
        from budget_proxy import Meter, make_handler
        env = dotenv_values(runtime / '.env', interpolate=False, encoding='utf-8-sig')
        keys = {'deepseek': env.get('DEEPSEEK_API_KEY', ''), 'siliconflow': env.get('EMBED_API_KEY', '')}
        missing = [name for name, value in keys.items() if not value or not value.strip()]
        if missing:
            raise SystemExit('missing local provider key(s): ' + ', '.join(missing))
        self.meter = Meter(work / 'meter.jsonl')  # one cumulative ledger for every phase
        self.server = ThreadingHTTPServer((PROXY_HOST, 0), make_handler(self.meter, keys))
        self.server.daemon_threads = True
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base = f'http://{PROXY_HOST}:{self.server.server_port}'

    def close(self):
        self.server.shutdown()
        self.server.server_close()
        self.meter.close()


def open_memory(runtime, home, proxy, recall_context=None):
    from memory_agent import MCP, Memory
    from ombre_resident import start_or_connect
    from veil_agent import Budget, Character, Store
    store = Store(home / 'data')
    (home / 'data' / 'exp' / 'memory' / '_agent').mkdir(parents=True, exist_ok=True)
    with socket.socket() as port_socket:
        port_socket.bind((PROXY_HOST, 0))
        ombre_port = port_socket.getsockname()[1]
    provider = {'model': 'deepseek-flash', 'base_url': proxy.base + '/ombre_internal/deepseek/v1',
                'api_key_env': 'LOCAL_PROXY_DUMMY', 'input_cny_per_million': Decimal('2'),
                'cached_input_cny_per_million': Decimal('0.04'), 'output_cny_per_million': Decimal('8'),
                'max_input_tokens': 12000}
    memory_cfg = {'model': 'BAAI/bge-m3', 'base_url': proxy.base + '/ombre_embedding/siliconflow/v1',
                  'input_cny_per_million': Decimal('0'), 'dimensions': 1024, 'mode': 'native',
                  'ombre_port': ombre_port, 'ombre_model': True}
    if recall_context is not None:
        memory_cfg['recall_context'] = {'enabled': True, 'min_similarity': recall_context, 'short_chars': 12}
    cfg = Character('exp', 'Exp', 'fictional', 20, 1200, provider, 'experiment', memory_cfg, None)
    port, token, resident = start_or_connect(runtime, cfg, data_root=home / 'data', api_key_overrides={
        'OMBRE_COMPRESS_API_KEY': 'proxy-dummy', 'OMBRE_EMBED_API_KEY': 'proxy-dummy'})
    backend = MCP(f'http://{PROXY_HOST}:{port}/mcp', token)
    budget = Budget(Store(home / 'budget'), daily=COST_CAP, monthly=COST_CAP)
    memory = Memory(cfg, store, budget, None, backend)
    if recall_context is not None:
        from recall_relevance import QueryEmbedder
        memory._embedder = QueryEmbedder(memory_cfg['base_url'], memory_cfg['model'], 'proxy-dummy')
    return memory, backend, resident


def close_memory(parts):
    memory, backend, resident = parts
    memory.__exit__(None, None, None)
    backend.close()
    resident.close()


def seed(runtime, work):
    from fixture import MEMORIES
    home = work / 'seed'
    if home.exists():
        raise SystemExit('seed already exists; delete .local-tmp/recall-353/seed to redo it')
    proxy = Proxy(runtime, work)
    parts = open_memory(runtime, home, proxy)
    ids = {}
    try:
        for index, (topic, importance, title, content) in enumerate(MEMORIES):
            text = parts[0].call('hold', {'title': title, 'content': content, 'importance': importance})
            ids[index] = {'topic': topic, 'title': title, 'reply': text[:200]}
            print(json.dumps({'seeded': index, 'paid': proxy.meter.paid_requests,
                              'committed_cny': str(proxy.meter.committed)}))
    finally:
        close_memory(parts)
        proxy.close()
    (work / 'seed-log.json').write_text(json.dumps(ids, ensure_ascii=False, indent=1), encoding='utf-8')


def topics_by_id(memory_dir):
    """Map stored memory IDs to fixture topics by title (titles are unique in the fixture)."""
    from fixture import MEMORIES
    by_title = {title: topic for topic, _, title, _ in MEMORIES}
    found = {}
    for path in memory_dir.rglob('*.md'):
        lines = path.read_text(encoding='utf-8').splitlines()
        meta = {}
        for line in lines[1:]:
            if line.strip() == '---':
                break
            key, sep, value = line.partition(':')
            if sep and not line[:1].isspace():
                meta[key.strip()] = value.strip().strip('\'"')
        if meta.get('id'):
            found[meta['id']] = by_title.get(meta.get('title') or meta.get('name'), 'merged-or-unknown')
    return found


def run(runtime, work, label, assisted, replies):
    from fixture import TURNS
    from openai import OpenAI
    home = work / label
    if home.exists():
        shutil.rmtree(home)
    shutil.copytree(work / 'seed', home)
    proxy = Proxy(runtime, work)
    parts = open_memory(runtime, home, proxy, assisted)
    memory = parts[0]
    topics = topics_by_id(home / 'data' / 'exp' / 'memory')
    client = OpenAI(api_key='proxy-dummy', base_url=proxy.base + '/ombre_answer/deepseek/v1',
                    max_retries=0, timeout=120)
    history, rows = [], []
    start_paid, start_cost = proxy.meter.paid_requests, proxy.meter.committed
    try:
        for index, (text, topic) in enumerate(TURNS):
            context = memory.context(text, include_opening=index == 0, recent=history[-8:])
            refs = list(memory.last_context_refs)
            recall_text = context[-1]['content']
            row = {'turn': index, 'text': text, 'topic': topic, 'refs': refs,
                   'ref_topics': [topics.get(key, '?') for key in refs],
                   'kinds': dict(getattr(memory, 'last_context_kinds', {}) or {}),
                   'recall_chars': len(recall_text), 'opening_chars': len(context[0]['content']) if index == 0 else 0,
                   'assisted': getattr(memory, 'last_assisted', None)}
            reply = ''
            if replies:
                answer = client.chat.completions.create(model='deepseek-flash', max_tokens=200, messages=[
                    {'role': 'system', 'content': 'You are Peter, a playful AI companion chatting with Lan. '
                     'Reply briefly in Chinese. Use a memory only if it fits what she just said.'},
                    *context, *history[-6:], {'role': 'user', 'content': text}])
                reply = answer.choices[0].message.content or ''
            row['reply'] = reply
            rows.append(row)
            history += [{'role': 'user', 'content': text}, {'role': 'assistant', 'content': reply or '嗯'}]
            print(json.dumps({'turn': index, 'refs': len(refs), 'paid': proxy.meter.paid_requests}))
    finally:
        client.close()
        close_memory(parts)
        proxy.close()
    result = {'label': label, 'assisted': assisted, 'rows': rows,
              'paid_requests': proxy.meter.paid_requests - start_paid,
              'cost_cny': str(proxy.meter.committed - start_cost)}
    (work / f'result-{label}.json').write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding='utf-8')


def summary(result):
    rows = result['rows'][1:]  # turn 0 carries the opening; recall is compared from turn 1
    counts = {}
    for row in rows:
        for key in row['refs']:
            counts[key] = counts.get(key, 0) + 1
    shown = sum(counts.values())
    relevant = sum(1 for row in rows for t in row['ref_topics'] if row['topic'] and t == row['topic'])
    off = sum(1 for row in rows for t in row['ref_topics'] if not row['topic'] or t != row['topic'])
    small_talk = [row for row in rows if not row['topic']]
    return {
        'distinct_memories': len(counts),
        'top_share': round(max(counts.values()) / shown, 3) if shown else 0,
        'empty_turns': sum(1 for row in rows if not row['refs']),
        'empty_topic_turns': sum(1 for row in rows if row['topic'] and not row['refs']),
        'avg_recall_chars': round(sum(row['recall_chars'] for row in rows) / len(rows)),
        'shown': shown, 'on_topic': relevant, 'off_topic': off,
        'small_talk_with_memories': sum(1 for row in small_talk if row['refs']),
        'paid_requests': result['paid_requests'], 'cost_cny': result['cost_cny']}


def report(work):
    found = {}
    for path in sorted(work.glob('result-*.json')):
        result = json.loads(path.read_text(encoding='utf-8'))
        found[result['label']] = summary(result)
    print(json.dumps(found, ensure_ascii=False, indent=1))


def main():
    args = options()
    runtime = Path(args.runtime).resolve()
    work = runtime / '.local-tmp' / 'recall-353'
    work.mkdir(parents=True, exist_ok=True)
    if args.phase == 'report':
        return report(work)
    setup_paths(runtime, Path(args.code).resolve() if args.code else None)
    if args.phase == 'seed':
        return seed(runtime, work)
    if not args.code or not args.label:
        raise SystemExit('run needs --code and --label')
    started = time.time()
    run(runtime, work, args.label, args.assisted, not args.no_replies)
    print(json.dumps({'seconds': round(time.time() - started)}))


if __name__ == '__main__':
    main()
