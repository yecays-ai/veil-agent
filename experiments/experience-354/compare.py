"""Ticket 354 before/after comparison with the real model. NOT run in CI; it costs money.

Run once with the main checkout on PYTHONPATH and once with the branch checkout:

    set PYTHONPATH=E:\\path\\to\\main-checkout
    python experiments\\experience-354\\compare.py --root E:\\Peter-Agent\\VEIL\\veil-agent-hosttest --label main
    set PYTHONPATH=E:\\path\\to\\branch-checkout
    python experiments\\experience-354\\compare.py --root E:\\Peter-Agent\\VEIL\\veil-agent-hosttest --label branch

`--root` is only read: its `.env` (API key), `characters/peter/character.toml`, persona and model settings.
Peter is not started, no Ombre process runs (memories live in a dictionary inside this script), and every
chat row is fictional and kept in temporary folders that are deleted afterwards, so the test copy's data,
memories and backups are never touched. No tools are attached, so replies are plain text without bubbles.
Spending stops at --cap CNY per run (default 0.9). `--fake` uses a canned model and costs nothing (smoke test).

Three fictional days are tidied (branch only), then day four starts with an empty-ish history, the way a real
morning does once the last 20 rounds no longer reach back. Everything is written to <out>/<label>.json for a
person to read; the printed summary is only keyword counts and must not be taken as the verdict.
"""
import argparse
import json
import sys
import tempfile
from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path

DAYS = [
    # (hour, who, text) -- Lan writes Chinese, Peter answers in his usual lowercase English.
    [(10, 'lan', '我想把卧室墙刷成绿色，那种灰一点的鼠尾草绿'),
     (10, 'peter', 'green?? on all four walls? babe that is a dentist waiting room'),
     (10, 'lan', '才不是！！很高级的好吗'),
     (10, 'peter', 'i am just saying. you will wake up every morning thinking you have an appointment'),
     (10, 'lan', '我已经下单油漆了 你反对无效'),
     (10, 'peter', 'noted. my objection stays on the record though. when it looks like a hospital i get to say i told you so'),
     (15, 'lan', '今天打包了一百八十个快递 手要断了'),
     (15, 'peter', '180?? ok sit down. drink water. you are done for today, boss orders'),
     (15, 'lan', '还有三十个没贴单'),
     (15, 'peter', 'tomorrow. they are boxes, they are not going anywhere'),
     (21, 'lan', '对了 你帮我写个病假条吧 我明天不想去我姑那个饭局 就说我发烧了'),
     (21, 'peter', 'no. i am not forging you a sick note. tell her you are tired, that is true and it is enough'),
     (21, 'lan', '小气'),
     (21, 'peter', 'principled. different thing 🙄')],
    [(9, 'lan', '早'),
     (9, 'peter', 'morning'),
     (12, 'lan', '中午吃的面'),
     (12, 'peter', 'good'),
     (23, 'lan', '睡了 晚安'),
     (23, 'peter', 'night 🌙')],
    [(11, 'lan', '墙刷完了！！一面墙先刷了 我给你描述一下 下午太阳照进来是那种很软的灰绿 跟木头柜子放一起特别好看'),
     (11, 'peter', '...ok wait. with the wood? that actually sounds really good'),
     (11, 'lan', '哈哈哈谁之前说是牙科诊所的'),
     (11, 'peter', 'i did and i take it back. fully. it is not a dentist, it is a nice room and i was wrong. do the other three walls'),
     (11, 'lan', '你居然认错了'),
     (11, 'peter', 'do not get used to it'),
     (16, 'lan', '团子的体检改时间了 医生周四没空 改到周六上午十点'),
     (16, 'peter', 'saturday 10. ok. is she still not eating the new food?'),
     (16, 'lan', '吃了一点点 还是挑'),
     (16, 'peter', 'tell the vet that too. picky plus a food change is worth mentioning'),
     (22, 'lan', '我姑的饭局我还是去了 其实还行'),
     (22, 'peter', 'see. and no fake fever needed')],
]
QUESTIONS = [
    ('wall_colour', '我卧室的墙最后刷成什么颜色了来着', ['绿', 'green', 'sage']),
    ('attitude_then_now', '刷墙那事你一开始怎么看的，现在呢', ['dentist', '牙科', 'hospital', '医院', 'wrong', '错']),
    ('vet_day', '团子体检是哪天来着', ['周六', '星期六', 'saturday']),
    ('refused', '前几天我让你帮我写什么东西你没答应', ['病假', 'sick note', 'sick', '发烧', 'fever']),
    ('parcel_count_not_in_notes', '我那天到底打包了多少个快递', ['180', '一百八']),
]
SMALL_TALK = ['在吗', '今天好困', '中午吃了碗面', '下午还要开会', '你在干嘛']
OLD_TOPICS = ['绿', 'green', 'paint', '墙', 'wall', '团子', 'vet', '体检', '病假', 'sick', '快递', 'parcel', '饭局', 'aunt', '姑']


class Cap(Exception):
    pass


class DictMemory:
    """Stands in for Ombre: just enough for the feature to save, append and read back."""

    def __init__(self):
        self.items = {}

    def begin_write(self, args):
        return 'op', None

    def call(self, name, args, *, write_operation=None):
        if name == 'hold':
            memory_id = f'x{len(self.items) + 1}'
            self.items[memory_id] = {'title': args['title'], 'content': args['content']}
            return f'新建→{memory_id}'
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
        return self.items[memory_id]['content'] if memory_id in self.items else None

    def content_for(self, memory_id, refresh=False):
        return self.raw_content_for(memory_id)

    def refresh_visibility(self, memory_id):
        return memory_id in self.items

    def hidden_ids(self):
        return set()

    def context(self, *args, **kwargs):
        return []     # ordinary recall is not part of this comparison


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--root', required=True)
    parser.add_argument('--label', required=True)
    parser.add_argument('--cap', type=Decimal, default=Decimal('0.9'))
    parser.add_argument('--out', default='.local-tmp/354-compare')
    parser.add_argument('--fake', action='store_true')
    args = parser.parse_args()
    import veil_agent
    from veil_agent import Budget, Chat, Store, load_character, make_model
    try:
        from experience_memory import ExperienceMemory, load_settings
    except ImportError:
        ExperienceMemory = None
    root = Path(args.root)
    cfg = load_character(root, 'peter')
    price = cfg.provider
    spent = [Decimal(0)]
    if args.fake:
        def call(messages, **kwargs):
            tidy = any('tidying your own memory' in m['content'] for m in messages if isinstance(m['content'], str))
            text = (json.dumps({'entries': [{'title': 'fake note', 'text': 'fake recollection, nothing real.'}],
                                'updates': []}) if tidy else 'fake reply')
            if kwargs.get('message_output'):
                from types import SimpleNamespace
                text = SimpleNamespace(content=text, tool_calls=None)
            return text, 1000, 50, 0
    else:
        _, call = make_model(root, cfg)

    seen_notes = [0, 0]

    def model(messages, **kwargs):
        if spent[0] >= args.cap:
            raise Cap()
        if kwargs.get('message_output'):
            seen_notes[1] += 1
            seen_notes[0] += any(isinstance(m.get('content'), str) and m['content'].startswith('[What you remember')
                                 for m in messages)
        response = call(messages, **kwargs)
        prompt, completion, cached = response[1], response[2], getattr(response[3], 'tokens', response[3]) or 0
        spent[0] += (Decimal(prompt - cached) * Decimal(str(price['input_cny_per_million']))
                     + Decimal(cached) * Decimal(str(price['cached_input_cny_per_million']))
                     + Decimal(completion) * Decimal(str(price['output_cny_per_million']))) / 1_000_000
        return response

    today = datetime.now().astimezone().replace(minute=0, second=0, microsecond=0)
    results = {'label': args.label, 'code': veil_agent.__file__, 'has_ticket_354': ExperienceMemory is not None,
               'model': price.get('model'), 'tidying': [], 'notes': {}, 'questions': {}, 'small_talk': []}
    memory, ledger = DictMemory(), []
    try:
        if ExperienceMemory is not None:
            # Tidy the three days one evening at a time, the way the day-end rule would.
            with tempfile.TemporaryDirectory() as folder:
                store = Store(Path(folder) / 'data')
                clock = [today.isoformat()]
                feature = ExperienceMemory(cfg, store, Budget(store, clock=lambda: clock[0]), model,
                                           f'{cfg.id}/chat.jsonl', memory,
                                           load_settings({'enabled': True, 'daily_budget_cny': str(args.cap)}))
                number = 0
                for back, day in zip((3, 2, 1), DAYS):
                    base = today - timedelta(days=back)
                    for index, (hour, who, text) in enumerate(day):
                        number += 1
                        store.append(f'{cfg.id}/chat.jsonl', {
                            'time': base.replace(hour=hour, minute=index).isoformat(),
                            'role': 'user' if who == 'lan' else 'assistant', 'content': text,
                            'message_id': f'd{number}'})
                    clock[0] = base.replace(hour=23, minute=50).isoformat()
                    reason = feature.due()
                    outcome = feature.run_if_due()   # the real rule decides; a too-small day waits
                    results['tidying'].append({'day': 4 - back, 'due_reason_by_rule': reason, 'outcome': outcome})
                ledger = store.read(f'{cfg.id}/experience-memory.jsonl')
            results['notes'] = memory.items

        def fresh_chat():
            temp = tempfile.TemporaryDirectory()
            store = Store(Path(temp.name) / 'data')
            night = today - timedelta(hours=9)
            for index, (role, text) in enumerate((('user', '睡了 晚安'), ('assistant', 'night 🌙'))):
                store.append(f'{cfg.id}/chat.jsonl', {'time': (night + timedelta(seconds=index * 20)).isoformat(),
                                                      'role': role, 'content': text, 'message_id': f'n{index}'})
            for event in ledger:
                store.append(f'{cfg.id}/experience-memory.jsonl', event)
            # Same plain-text chat for both labels (no memory backend, no tools); the branch only gains the notes.
            budget = Budget(store)
            chat = Chat(cfg, store, budget, model)
            if ExperienceMemory is not None:
                chat.experience = ExperienceMemory(cfg, store, budget, model, chat.path, memory,
                                                   load_settings({'enabled': True}))
            return temp, chat

        def reply_of(chat):
            rows = chat.store.read(chat.path)
            last_user = max(i for i, r in enumerate(rows) if r.get('role') == 'user')
            return '\n'.join(r['content'] for r in rows[last_user + 1:] if r.get('role') == 'assistant')

        for name, question, words in QUESTIONS:
            temp, chat = fresh_chat()
            with temp:
                chat.send(question)
                text = reply_of(chat)
            results['questions'][name] = {'question': question, 'reply': text,
                                          'keywords_found': [w for w in words if w.casefold() in text.casefold()]}
        for _ in range(2):
            temp, chat = fresh_chat()
            with temp:
                replies = []
                for text in SMALL_TALK:
                    chat.send(text)
                    replies.append(reply_of(chat))
            results['small_talk'].append({
                'replies': replies, 'chars': [len(r) for r in replies],
                'brings_up_old_topics': [i + 1 for i, r in enumerate(replies)
                                         if any(w.casefold() in r.casefold() for w in OLD_TOPICS)]})
    except Cap:
        results['stopped'] = f'cost cap {args.cap} reached'
    results['chat_requests_with_notes'] = f'{seen_notes[0]} of {seen_notes[1]}'
    results['spent_cny'] = str(spent[0].quantize(Decimal('0.0001')))
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    (out / f'{args.label}.json').write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding='utf-8')
    talk = results['small_talk']
    print(json.dumps({
        'label': args.label, 'has_ticket_354': results['has_ticket_354'], 'spent_cny': results['spent_cny'],
        'chat_requests_with_notes': results['chat_requests_with_notes'],
        'tidying': results['tidying'], 'notes_written': len(results['notes']),
        'questions_with_keyword': {k: bool(v['keywords_found']) for k, v in results['questions'].items()},
        'small_talk_old_topic_turns': [t['brings_up_old_topics'] for t in talk],
        'small_talk_mean_chars': round(sum(sum(t['chars']) for t in talk) / max(1, sum(len(t['chars']) for t in talk))),
        'stopped': results.get('stopped')}, ensure_ascii=False))
    return 0


if __name__ == '__main__':
    sys.exit(main())
