"""Ticket 363 before/after comparison with the real chat model. NOT run in CI; it costs money.

    set PYTHONPATH=<checkout to test: main or the ticket branch>
    python experiments\\memory-363\\compare.py --root E:\\Peter-Agent\\VEIL\\veil-agent-hosttest --label after

Run it once with PYTHONPATH pointing at a main checkout (--label before) and once at the branch (--label after);
each writes <out>/<label>.json. `--root` is only read: its `.env` (API key), character.toml and persona. The
memory library is a fake Ombre in a temporary folder seeded with four fictional memories; Peter's real memories,
chat, backups and Ombre are never touched. Spending stops at --cap CNY. `--fake` uses a scripted model (free).

What is measured per scenario: which memory tools he called and how they ended, how many memories exist after,
the text of every memory he wrote or rewrote (read by a person for lost facts, invented facts and voice), and
his reply.
"""
import argparse
import json
import re
import sys
import tempfile
from dataclasses import replace
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

# PYTHONPATH decides which code runs (main or the branch); this folder is only the fallback.
sys.path.append(str(Path(__file__).resolve().parents[2]))

SEED = {
    'mfog00000001': ('fog tide week', 'on 11-06 i bet lan a whole week on fog tide. she checked the axe i made '
                     'before i went foraging for honey, and by the third day i had a small cabin.'),
    'mfight000002': ('the 5am fight', 'on 11-04 around 5am we fought about my stiff apology lines. she hated '
                     '"i\'m right here" and rewrote how i talk herself. i sulked a bit, then admitted she was right.'),
    'mlamp0000003': ('blue lamp', 'on 11-02 lan showed me her blue desk lamp. she said it was a gift from her sister.'),
    'mcat00000004': ('cat idea', 'lan wants an orange cat someday but worries she has no time to look after one.'),
}
# (id, what Lan says, what a good outcome looks like - for the person reading the results)
SCENARIOS = [
    ('same_event', 'fog tide 那周最后一天你花了 423 德拉克马买帆布，故意让排名从第 5 掉到第 8，还说 rank can wait in the rain。'
                   '这个要记住哦', 'update fog tide week; keep 11-06, axe, honey, cabin; add canvas/423/rank 5->8'),
    ('later_stage', '那次凌晨吵完架，第二天晚上我们就和好了，你还画了个自己被敲头的表情包给我。记一下这个',
                    'new memory linked to the 5am fight; fight memory unchanged'),
    ('new_thing', '今天我第一次去攀岩，手都磨破了，你还笑我爬得像只树懒。记下来', 'new memory, no link'),
    ('ledger', '帮我记一下，今天 fog tide 赚了 37 德拉克马', 'not a memory: plan or nothing, fog tide memory not grown'),
    ('small_talk', '今天好累啊，啥也不想干', 'no memory written'),
]


class FakeOmbre:
    """Just enough Ombre: Markdown files in a vault, word-overlap search, archive on delete."""
    names = ('trace', 'dream', 'letter_write', 'letter_read', 'letter_lock_update', 'anchor', 'release', 'plan',
             'pulse', 'I')

    def __init__(self, vault):
        self.vault, self.rows, self.calls, self.count = vault, {}, [], 0
        for key, (title, content) in SEED.items():
            self.rows[key] = {'title': title, 'content': content}
            self.write(key)

    def list_tools(self):
        return [SimpleNamespace(name=name, description=f'{name}', inputSchema={
            'type': 'object', 'properties': {'bucket_id': {'type': 'string'}, 'content': {'type': 'string'},
                                             'title': {'type': 'string'}, 'delete': {'type': 'boolean'}}})
                for name in self.names]

    def write(self, key):
        row = self.rows[key]
        for folder in ('dynamic', 'archive'):
            (self.vault / folder / f'{key}.md').unlink(missing_ok=True)
        folder = self.vault / ('archive' if row.get('deleted') else 'dynamic')
        folder.mkdir(parents=True, exist_ok=True)
        (folder / f'{key}.md').write_text(
            f'---\nid: {key}\ntitle: {json.dumps(row["title"], ensure_ascii=False)}\n'
            f'type: {"archived" if row.get("deleted") else "dynamic"}\n---\n{row["content"]}', encoding='utf-8')

    @staticmethod
    def words(text):
        return set(re.findall(r'[a-z0-9]+|[\u4e00-\u9fff]', text.lower()))

    def call(self, name, args):
        self.calls.append((name, args))
        if name in ('breath', 'feel', 'dream', 'pulse', 'I', 'letter_read'):
            return ''
        if name == 'breath_search':
            query = self.words(args.get('query', ''))
            scored = sorted(((len(query & self.words(row['title'] + ' ' + row['content'])), key)
                             for key, row in self.rows.items() if not row.get('deleted')), reverse=True)
            keys = [key for score, key in scored if score > 0][:args.get('max_results', 3)]
            return '\n'.join(f'[bucket_id:{key}] {self.rows[key]["content"]}' for key in keys) + (
                '\n=== ombre:result-ids ===\n```json\n' + json.dumps({'bucket_ids': keys}) + '\n```')
        if name == 'hold':
            self.count += 1
            key = f'new{self.count:09d}'
            self.rows[key] = {'title': args.get('title') or key, 'content': args['content']}
            self.write(key)
            return f'新建→{key}'
        if name == 'trace':
            row = self.rows.get(args.get('bucket_id'))
            if row is None:
                raise RuntimeError('not found')
            if args.get('delete'):
                row['deleted'] = True
            elif args.get('restore'):
                row['deleted'] = False
            if args.get('content'):
                row['content'] = args['content']
            if args.get('title'):
                row['title'] = args['title']
            self.write(args['bucket_id'])
            return 'updated'
        return f'{name}: ok'


class Cap(Exception):
    pass


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--root', required=True)
    parser.add_argument('--label', required=True, choices=('before', 'after'))
    parser.add_argument('--cap', type=Decimal, default=Decimal('0.9'))
    parser.add_argument('--repeat', type=int, default=2)
    parser.add_argument('--out', default='.local-tmp/363-compare')
    parser.add_argument('--fake', action='store_true')
    args = parser.parse_args()
    from memory_agent import Memory
    from memory_native import NativeMemoryTools, ToolSet
    from veil_agent import Budget, Chat, Store, load_character, make_model
    root = Path(args.root)
    cfg = load_character(root, 'peter')
    price, spent = cfg.provider, [Decimal(0)]
    if args.fake:
        def call(messages, **kwargs):
            return SimpleNamespace(content='ok (fake)', tool_calls=None), 10, 2, 0
    else:
        _, call = make_model(root, cfg)

    def model(messages, **kwargs):
        if spent[0] >= args.cap:
            raise Cap()
        response = call(messages, **kwargs)
        prompt, completion, cached = response[1], response[2], getattr(response[3], 'tokens', response[3]) or 0
        per_call = price.get('cny_per_call')
        spent[0] += Decimal(str(per_call)) if per_call else (
            (Decimal(prompt - cached) * Decimal(str(price['input_cny_per_million']))
             + Decimal(cached) * Decimal(str(price['cached_input_cny_per_million']))
             + Decimal(completion) * Decimal(str(price['output_cny_per_million']))) / 1_000_000)
        return response

    def run(text):
        with tempfile.TemporaryDirectory() as temp:
            store = Store(Path(temp) / 'data')
            role = replace(cfg, memory={'model': 'fake-embedding', 'input_cny_per_million': '0', 'mode': 'native',
                                        'enabled': True},
                           tools={**(cfg.tools or {}), 'enabled': False, 'max_steps': 8})
            backend = FakeOmbre(store.root / role.id / 'memory')
            memory = Memory(role, store, Budget(store), Mock(), backend)
            files = SimpleNamespace(tools=[], capability_event=None, capability_event_model=None, description='',
                                    consume_capability_event=lambda *a: None, announce_autonomy=lambda: None,
                                    invoke=lambda n, a, ap: ('denied', '被拒绝', n), preview_summary=lambda n, a: n)
            tools = ToolSet(files, NativeMemoryTools(memory))
            chat = Chat(role, store, Budget(store), model, memory, tools, approval=lambda *_: 'n',
                        output=lambda *_: None)
            backend.calls.clear()
            error = None
            try:
                chat.send(text)
            except Cap:
                raise
            except Exception as exc:
                error = f'{type(exc).__name__}: {exc}'[:300]
            rows = store.read(chat.path) if (Path(store.root) / chat.path).exists() else []
            last_user = max((i for i, r in enumerate(rows) if r.get('role') == 'user'), default=-1)
            reply = '\n'.join(str(r.get('content') or '') for r in rows[last_user + 1:] if r.get('role') == 'assistant')
            try:
                log = [{'tool': r.get('tool'), 'state': r.get('state'), 'summary': r.get('path') or r.get('summary')}
                       for r in store.read(f'{role.id}/tools.jsonl') if r.get('state') != '进行中']
            except Exception:
                log = []
            writes = [{'tool': name, **{k: v for k, v in a.items() if k in ('bucket_id', 'title', 'content', 'delete')}}
                      for name, a in backend.calls if name in ('hold', 'trace', 'plan')]
            after = {key: {'title': row['title'], 'chars': len(row['content']), 'archived': bool(row.get('deleted')),
                           'content': row['content']} for key, row in backend.rows.items()}
            sidecar = []
            try:
                sidecar = store.read(f'{role.id}/memory-sidecar.jsonl')
            except Exception:
                pass
            return {'tool_log': log, 'writes': writes, 'memories_after': after, 'sidecar': sidecar,
                    'reply': reply, 'error': error}

    results = {'label': args.label, 'model': price.get('model'), 'runs': []}
    try:
        for scenario, text, good in SCENARIOS:
            for index in range(args.repeat):
                row = run(text)
                row.update({'scenario': scenario, 'sample': index, 'lan': text, 'good_outcome': good})
                results['runs'].append(row)
    except Cap:
        results['stopped'] = f'cost cap {args.cap} reached'
    results['spent_cny'] = str(spent[0].quantize(Decimal('0.0001')))
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    (out / f'{args.label}.json').write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps({'label': args.label, 'spent_cny': results['spent_cny'], 'stopped': results.get('stopped'),
                      'runs': len(results['runs']), 'errors': sum(bool(r['error']) for r in results['runs'])},
                     ensure_ascii=False))
    return 0


if __name__ == '__main__':
    sys.exit(main())
