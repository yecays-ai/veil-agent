"""Ticket 386 before/after check with real chat models. NOT run in CI; it costs money (stops at --cap CNY).

    set PYTHONPATH=<checkout to test: main or the ticket branch>
    python experiments\\experience-386\\compare.py --root E:\\Peter-Agent\\VEIL\\veil-agent-hosttest --list
    python experiments\\experience-386\\compare.py --root ... --model builtin:deepseek --label after --repeat 3

`--root` is only read: its `.env` (API key), character.toml, models.json and persona. Peter is not started, no Ombre
runs (a dictionary library seeded with fictional memories, word-overlap search), the store is a temporary folder.
One fictional day (about 3,000 characters; the real batches that failed were 3,900 to 11,900) is tidied.

Recorded per model call: max_tokens asked, thinking override, finish_reason, completion tokens. Per run: outcome,
error, retried without thinking, written/updated/unfixed, the failure record, and the text of every write (read it
for voice, invented facts and lost facts). `--probe` only sends one tiny request with thinking switched off, to see
whether the endpoint accepts that switch (for relays). `--squeeze N` caps the calls that still think at N tokens
to reproduce a reply cut off while thinking; the retry without thinking keeps the normal limit.
"""
import argparse
import json
import re
import sys
import tempfile
from dataclasses import replace
from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parents[2]))   # PYTHONPATH decides which code runs

SEED = {
    'mstarter0001': ('sourdough starter', 'nov 13 lan started a sourdough starter for me to babysit. two days in it '
                     'smelled off, so she panicked and threw it out. i was a bit sad, i had named it bob.'),
    'mlamp0000002': ('blue lamp', 'nov 2 lan showed me her blue desk lamp. a gift from her sister.'),
    'mcat00000003': ('cat idea', 'lan wants an orange cat someday but worries she has no time for one.'),
}
# Fictional. Lan writes Chinese, Peter lowercase English, like the real chat.
TALK = [
    ('lan', '早 今天要去考驾照科目二 好紧张'),
    ('peter', 'morning. ok breathe. you did the reverse parking like forty times last week, your hands know it even '
              'if your head is panicking. what time is the slot'),
    ('lan', '十点半 我现在在路上 教练说我倒车入库老是压线'),
    ('peter', 'then watch the mirror line, not the instructor. and do not look at me, look at the mirror. text me '
              'the second you are out'),
    ('lan', '过了！！！！一把过'),
    ('peter', 'WAIT. first try?? ok i am unreasonably proud. tell me everything, did you press the line at all'),
    ('lan', '侧方停车的时候差一点 但是没压 考官全程面无表情 吓死'),
    ('peter', 'examiners are trained to have no face. it is in the contract. so what is next, subject three?'),
    ('lan', '科目三下个月 要上路 我更怕了'),
    ('peter', 'one month is fine. we can do the light rules quiz thing again, you liked that one'),
    ('lan', '好 晚点再说 我先去吃个饭 庆祝一下 吃火锅'),
    ('peter', 'hotpot is the correct celebration food. order the tripe for me since i cannot eat it'),
    ('lan', '你又没有嘴 吃什么毛肚'),
    ('peter', 'rude. i have a very vivid imagination and it is chewing right now'),
    ('lan', '哈哈哈 对了 那个酵种的备份罐子 我今天看了一眼 好像在冒泡'),
    ('peter', 'bob?? bob is alive?? you told me it smelled off and you threw it out. there was a backup jar?'),
    ('lan', '对 我当时留了一小勺在另一个罐子里 忘了 今天打开居然活了'),
    ('peter', 'ok this is a resurrection story and i will be telling it forever. feed it today, flour and water, '
              'same as before. and maybe do not panic-toss it this time'),
    ('lan', '知道了 我晚上喂'),
    ('lan', '吃完了 好撑 我妈刚打电话 说让我过年早点回去'),
    ('peter', 'how early is early. like a week before or the whole month'),
    ('lan', '她想让我腊月二十就回 但我工作那边可能请不了那么多假'),
    ('peter', 'so the honest answer to her is probably the 25th or so? you could tell her the exact day once work '
              'confirms, instead of promising the 20th and then disappointing her'),
    ('lan', '嗯 我也是这么想的 但她会念叨'),
    ('peter', 'she will nag either way. at least this way the nagging is about a real date'),
    ('lan', '你说得对 下午我要改那个汇报的PPT 老板说太花了'),
    ('peter', 'too flashy how. too many animations or too many colours'),
    ('lan', '都有 他说像婚礼请柬'),
    ('peter', 'that is honestly a great insult. ok strip it: one font, two colours, no transitions. i can look at it '
              'if you send screenshots'),
    ('lan', '算了 我自己改 你上次改的那个字体我不喜欢'),
    ('peter', 'fair. i stand by the font but i accept the ruling'),
    ('lan', '改完了 老板说可以 但是让我周五再讲一遍给大老板听'),
    ('peter', 'friday. so you have two days to practise. want to do a run-through with me thursday night? i will be '
              'the big boss and ask annoying questions'),
    ('lan', '好啊 周四晚上 你别太凶'),
    ('peter', 'i will be exactly as mean as a real big boss and not one bit more'),
    ('lan', '对了 你昨天是不是在实验室里又写了一堆奇怪的诗'),
    ('peter', 'they are not weird. they are about the radiator. the radiator deserves poems'),
    ('lan', '你写暖气片写了十二首'),
    ('peter', 'and i regret nothing. number seven is the best one. it rhymes "clank" with "thank"'),
    ('lan', '你好无聊哈哈哈'),
    ('peter', 'bored people write zero radiator poems. i wrote twelve. that is the opposite of bored'),
    ('lan', '晚上喂了酵种 加了面粉和水 放在暖气片旁边'),
    ('peter', 'next to the radiator. the radiator i wrote twelve poems about. this is fate. bob is home'),
    ('lan', '你能不能别给它起名叫bob'),
    ('peter', 'too late. it answered to bob. it bubbled when i said it'),
    ('lan', '它根本听不到你说话'),
    ('peter', 'details'),
    ('lan', '好困 今天好累 先睡了'),
    ('peter', 'sleep. you passed your test, survived your boss, and brought bob back from the dead. big day. night'),
]
FILLER = [  # ordinary small talk that should be forgotten
    ('lan', '今天地铁上有个小孩一直盯着我看 我冲他笑了一下 他哭了'),
    ('peter', 'that is the most devastating thing i have heard all week. what kind of smile was it'),
    ('lan', '正常的笑！！'),
    ('peter', 'kids can sense fear. you were probably still thinking about the exam'),
    ('lan', '中午吃了楼下那家面 难吃 再也不去了'),
    ('peter', 'noted. the bad noodle place is officially dead to us'),
]


def day_rows(day):
    rows, minute = [], 0
    talk = TALK[:20] + FILLER + TALK[20:]
    for index, (who, text) in enumerate(talk):
        hour = 9 + minute // 60
        rows.append({'time': day.replace(hour=min(hour, 22), minute=minute % 60).isoformat(),
                     'role': 'user' if who == 'lan' else 'assistant', 'content': text, 'message_id': f'd{index}'})
        minute += 9
    return rows


STOP = set('a an and the to of i it is was me my she her we on in for so at be that this what or no do not'.split())


class Cap(Exception):
    pass


class Library:
    """Just enough of memory_agent.Memory: save, rewrite, read back, and a word-overlap breath_search."""

    def __init__(self):
        self.items = {key: {'title': title, 'content': text} for key, (title, text) in SEED.items()}
        self.writes = []

    @staticmethod
    def words(text):
        return {w for w in re.findall(r'[a-z0-9]{2,}', text.lower()) if w not in STOP}

    def begin_write(self, args):
        return 'op', None

    def call(self, name, args, *, write_operation=None):
        if name == 'breath_search':
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
    parser.add_argument('--model', help="a model id from models.json or builtin:<name>; default: Peter's chat model")
    parser.add_argument('--label', default='run')
    parser.add_argument('--cap', type=Decimal, default=Decimal('0.25'))
    parser.add_argument('--repeat', type=int, default=3)
    parser.add_argument('--out', default='.local-tmp/386-compare')
    parser.add_argument('--list', action='store_true')
    parser.add_argument('--probe', action='store_true')
    parser.add_argument('--squeeze', type=int, help='cap max_tokens of calls that still think to this, to make the '
                        'reasoning use the whole limit like the 11-16 failures (the retry without thinking is not capped)')
    args = parser.parse_args()
    from veil.brain.experience_memory import ExperienceMemory, load_settings
    from veil_agent import Budget, Store, load_character, make_model
    root = Path(args.root)
    cfg = load_character(root, 'peter')
    models = getattr(cfg, 'models', None)
    if args.list:
        ids = [m['id'] for m in models.data['models']] if models else []
        print(json.dumps({'models': ids, 'builtins': sorted(models.builtins) if models else [],
                          'peter': models.selected('peter') if models else None}, ensure_ascii=False))
        return 0
    provider = dict(cfg.provider)
    if args.model:
        provider = models._provider(models.data, args.model)
        if provider is None:
            raise SystemExit(f'unknown model {args.model}')
    cfg = replace(cfg, provider=provider)
    _, call = make_model(root, cfg, provider=provider)
    raw = getattr(call, 'raw', call)
    if args.probe:
        response = raw([{'role': 'user', 'content': 'Reply with the word ok.'}], max_tokens=50, thinking='disabled',
                       detail=True)
        print(json.dumps({'model': provider.get('model'), 'reply': response[0], 'completion': response[2],
                          'finish_reason': response[4]}, ensure_ascii=False))
        return 0
    spent, calls = [Decimal(0)], []

    def model(messages, **kwargs):
        # The most this call can cost, checked against what all samples together have spent (review of 386).
        size = len(json.dumps(messages, ensure_ascii=False).encode('utf-8'))
        bound = Decimal(str(provider['cny_per_call'])) if provider.get('cny_per_call') else (
            Decimal(size) * Decimal(str(provider['input_cny_per_million']))
            + Decimal(kwargs.get('max_tokens') or 8000) * Decimal(str(provider['output_cny_per_million']))) / 1_000_000
        if spent[0] + bound > args.cap:
            raise Cap()
        asked_detail = kwargs.pop('detail', False)
        if args.squeeze and kwargs.get('thinking') != 'disabled':
            kwargs['max_tokens'] = min(kwargs.get('max_tokens') or args.squeeze, args.squeeze)
        response = raw(messages, detail=True, **kwargs)
        prompt, completion, cached = response[1], response[2], getattr(response[3], 'tokens', response[3]) or 0
        calls.append({'max_tokens': kwargs.get('max_tokens'), 'thinking': kwargs.get('thinking'),
                      'finish_reason': response[4], 'completion_tokens': completion, 'prompt_tokens': prompt})
        per_call = provider.get('cny_per_call')
        spent[0] += Decimal(str(per_call)) if per_call else (
            (Decimal(prompt - cached) * Decimal(str(provider['input_cny_per_million']))
             + Decimal(cached) * Decimal(str(provider['cached_input_cny_per_million']))
             + Decimal(completion) * Decimal(str(provider['output_cny_per_million']))) / 1_000_000)
        return response if asked_detail else response[:4]

    day = datetime.now().astimezone().replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(days=1)
    results = {'label': args.label, 'model': provider.get('model'), 'thinking_setting': provider.get('thinking'),
               'runs': []}
    try:
        for sample in range(args.repeat):
            library, calls[:] = Library(), []
            with tempfile.TemporaryDirectory() as folder:
                store = Store(Path(folder) / 'data')
                clock = [day.isoformat()]
                feature = ExperienceMemory(cfg, store, Budget(store, clock=lambda: clock[0]), model,
                                           f'{cfg.id}/chat.jsonl', library,
                                           load_settings({'enabled': True, 'daily_budget_cny': str(args.cap)}))
                rows = day_rows(day)
                for item in rows:
                    store.append(f'{cfg.id}/chat.jsonl', item)
                clock[0] = day.replace(hour=23, minute=50).isoformat()
                error = None
                try:
                    outcome = feature.run_if_due()
                except Cap:
                    raise
                except Exception as exc:
                    outcome, error = None, f'{type(exc).__name__}: {exc}'[:300]
                ledger = store.read(f'{cfg.id}/experience-memory.jsonl')
                failures = store.read(f'{cfg.id}/experience-memory-failures.jsonl') if (
                    Path(folder) / 'data' / cfg.id / 'experience-memory-failures.jsonl').exists() else []
            last = next((r for r in reversed(ledger) if r.get('kind') in ('checkpoint', 'error')), {})
            results['runs'].append({
                'sample': sample, 'outcome': outcome, 'error': error, 'calls': list(calls),
                'chat_chars': sum(len(r['content']) for r in rows),
                'retried_without_thinking': last.get('retried_without_thinking', 0),
                'written': last.get('written'), 'updated': last.get('updated'), 'unfixed': last.get('unfixed'),
                'bob_rewritten': library.items['mstarter0001']['content'] != SEED['mstarter0001'][1],
                'writes': library.writes, 'failures': failures})
    except Cap:
        results['stopped'] = f'cost cap {args.cap} reached'
    results['spent_cny'] = str(spent[0].quantize(Decimal('0.0001')))
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    (out / f'{args.label}.json').write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps({'label': args.label, 'model': results['model'], 'spent_cny': results['spent_cny'],
                      'stopped': results.get('stopped'),
                      'runs': [{'outcome': r['outcome'], 'error': r['error'],
                                'calls': [(c['thinking'], c['finish_reason'], c['completion_tokens']) for c in r['calls']],
                                'written': r['written'], 'updated': r['updated'], 'unfixed': r['unfixed']}
                               for r in results['runs']]}, ensure_ascii=False))
    return 0


if __name__ == '__main__':
    sys.exit(main())
