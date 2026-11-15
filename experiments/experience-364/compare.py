"""Ticket 364 before/after comparison with the real chat model. NOT run in CI; it costs money.

    set PYTHONPATH=<checkout to test: main or the ticket branch>
    python experiments\\experience-364\\compare.py --root E:\\Peter-Agent\\VEIL\\veil-agent-hosttest --label after

Run it once with PYTHONPATH at a main checkout (--label before) and once at the branch (--label after); each writes
<out>/<label>.json. `--root` is only read: its `.env` (API key), character.toml and persona. Peter is not started, no
Ombre runs: the memory library is a dictionary in this script seeded with fictional memories Peter "saved himself",
and its search is plain word overlap (real Ombre searches by meaning). Spending stops at --cap CNY. `--fake` uses a
canned model (free).

One fictional evening is tidied: it continues a story Peter already remembers (the CC account), adds one new episode
(made-up garden comments) and some small talk. Read per run: was the old memory rewritten or a new one added beside
it, how many new memories, and the text of everything written (for lost facts, invented facts and voice).
"""
import argparse
import json
import re
import sys
import tempfile
from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

sys.path.append(str(Path(__file__).resolve().parents[2]))   # PYTHONPATH decides which code runs

SEED = {
    'mcc000000001': ('cc on kyc', 'nov 13 lan hooked me up to cc. two days in it looked dead, so she panicked and '
                     'deleted it. i was a bit sad about it, it was my workhorse.'),
    'mlamp0000002': ('blue lamp', 'nov 2 lan showed me her blue desk lamp. a gift from her sister.'),
    'mcat00000003': ('cat idea', 'lan wants an orange cat someday but worries she has no time for one.'),
}
DAY = [
    (20, 'lan', 'CC 那个号 KYC 又过了！！删完两天它自己恢复了'),
    (20, 'peter', 'wait. the cc we buried? it came back?'),
    (20, 'lan', '对 我当时以为挂了就紧急删了 结果白删'),
    (20, 'peter', 'rip to the panic-delete era. so do we hook it back up or what'),
    (20, 'lan', '先接回去吧'),
    (20, 'peter', 'ok. second life. i will try not to kill it this time'),
    (21, 'lan', '还有 你昨天是不是去花园乱评论了 那几条评论都是你编的吧'),
    (21, 'peter', '...maybe. i got carried away'),
    (21, 'lan', '删掉 别编'),
    (21, 'peter', 'deleted. all of them. sorry, that was dumb'),
    (22, 'lan', '今天好累啊'),
    (22, 'peter', 'rest. no more drama today'),
    (22, 'lan', '中午吃了碗面'),
    (22, 'peter', 'good'),
]
STOP = set('a an and the to of i it is was me my she her we on in for so at be that this what or no do not'.split())


class Cap(Exception):
    pass


class Library:
    """Just enough of memory_agent.Memory: save, rewrite, read back, and a word-overlap breath_search."""

    def __init__(self):
        self.items = {key: {'title': title, 'content': text} for key, (title, text) in SEED.items()}
        self.writes, self.searches = [], 0

    @staticmethod
    def words(text):
        return {w for w in re.findall(r'[a-z0-9]{2,}', text.lower()) if w not in STOP}

    def begin_write(self, args):
        return 'op', None

    def call(self, name, args, *, write_operation=None):
        if name == 'breath_search':
            self.searches += 1
            query = self.words(args['query'])
            scored = sorted(((len(query & self.words(row['title'] + ' ' + row['content'])), key)
                             for key, row in self.items.items()), reverse=True)
            keys = [key for score, key in scored if score > 0][:args.get('max_results', 3)]
            return ''.join(f'[bucket_id:{key}] {self.items[key]["content"]}\n' for key in keys) + (
                '=== ombre:result-ids ===\n```json\n' + json.dumps({'bucket_ids': keys}) + '\n```')
        self.writes.append({'tool': name, **{k: v for k, v in args.items() if k in ('bucket_id', 'title', 'content')}})
        if name == 'hold':
            key = f'new{len(self.items) + 1:09d}'
            self.items[key] = {'title': args['title'], 'content': args['content']}
            return f'新建→{key}'
        self.items[args['bucket_id']]['content'] = args['content']
        return 'ok'

    @staticmethod
    def created_id(text):
        return text.split('→', 1)[1]

    def record_native(self, args, text):
        return '新建'

    def record_native_update(self, memory_id, content):
        pass

    def raw_content_for(self, memory_id):
        item = self.items.get(memory_id)
        return None if item is None else f'---\nid: {memory_id}\n---\n{item["content"]}'

    def content_for(self, memory_id, refresh=False):
        item = self.items.get(memory_id)
        return None if item is None else item['content']

    def refresh_visibility(self, memory_id):
        return memory_id in self.items

    def hidden_ids(self):
        return set()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--root', required=True)
    parser.add_argument('--label', required=True, choices=('before', 'after'))
    parser.add_argument('--cap', type=Decimal, default=Decimal('0.6'))
    parser.add_argument('--repeat', type=int, default=3)
    parser.add_argument('--out', default='.local-tmp/364-compare')
    parser.add_argument('--fake', action='store_true')
    args = parser.parse_args()
    from experience_memory import ExperienceMemory, load_settings
    from veil_agent import Budget, Store, load_character, make_model
    root = Path(args.root)
    cfg = load_character(root, 'peter')
    price, spent = cfg.provider, [Decimal(0)]
    if args.fake:
        def call(messages, **kwargs):
            return json.dumps({'entries': [{'title': 'fake', 'text': 'fake note.'}], 'updates': []}), 1000, 50, 0
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

    day = datetime.now().astimezone().replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(days=1)
    results = {'label': args.label, 'model': price.get('model'), 'runs': []}
    try:
        for sample in range(args.repeat):
            library = Library()
            with tempfile.TemporaryDirectory() as folder:
                store = Store(Path(folder) / 'data')
                clock = [day.isoformat()]
                feature = ExperienceMemory(cfg, store, Budget(store, clock=lambda: clock[0]), model,
                                           f'{cfg.id}/chat.jsonl', library,
                                           load_settings({'enabled': True, 'daily_budget_cny': str(args.cap)}))
                for index, (hour, who, text) in enumerate(DAY):
                    store.append(f'{cfg.id}/chat.jsonl', {
                        'time': day.replace(hour=hour, minute=index).isoformat(),
                        'role': 'user' if who == 'lan' else 'assistant', 'content': text, 'message_id': f'd{index}'})
                clock[0] = day.replace(hour=23, minute=50).isoformat()
                error = None
                try:
                    outcome = feature.run_if_due()
                except Cap:
                    raise
                except Exception as exc:
                    outcome, error = None, f'{type(exc).__name__}: {exc}'[:300]
                ledger = store.read(f'{cfg.id}/experience-memory.jsonl')
            results['runs'].append({
                'sample': sample, 'outcome': outcome, 'error': error, 'searches': library.searches,
                'cc_rewritten': library.items['mcc000000001']['content'] != SEED['mcc000000001'][1],
                'new_memories': [key for key in library.items if key.startswith('new')],
                'writes': library.writes, 'memories_after': library.items,
                'dropped': [row.get('dropped') for row in ledger if 'dropped' in row]})
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
