"""Ticket 352 before/after comparison with the real model. NOT run in CI; it costs money.

Run once with the main checkout on PYTHONPATH and once with the branch checkout, for example:

    set PYTHONPATH=E:\\path\\to\\main-checkout
    python experiments\\352-time-gap\\compare.py --root E:\\Peter-Agent\\VEIL\\veil-agent-hosttest --label main
    set PYTHONPATH=E:\\path\\to\\branch-checkout
    python experiments\\352-time-gap\\compare.py --root E:\\Peter-Agent\\VEIL\\veil-agent-hosttest --label branch

`--root` is only read: its `.env` (API key), `characters/peter/character.toml`, persona and model
settings. Every chat row, task and life record is fictional and lives in a fresh temporary folder
that is deleted afterwards, so the test copy's data, memories and backups are never touched.
No memory backend and no tools are attached. Spending stops at --cap CNY per run (default 0.9).
"""
import argparse
import json
import re
import sys
import tempfile
from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path

DURATION = re.compile(r'(\d+|[一二两三四五六七八九十几半]+)\s*(个)?\s*(小时|钟头|天|日|分钟|晚上?|宿)'
                      r'|\b\d+\s*(hours?|days?|minutes?|nights?)\b', re.I)


class Cap(Exception):
    pass


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--root', required=True)
    parser.add_argument('--label', required=True)
    parser.add_argument('--cap', type=Decimal, default=Decimal('0.9'))
    parser.add_argument('--out', default='.local-tmp/352-compare')
    args = parser.parse_args()
    import veil_agent
    from veil_agent import Budget, Chat, Store, load_character, make_model
    root = Path(args.root)
    cfg = load_character(root, 'peter')
    _, call = make_model(root, cfg)
    price = cfg.provider
    spent = [Decimal(0)]

    def model(messages, **kwargs):
        if spent[0] >= args.cap:
            raise Cap()
        response = call(messages, **kwargs)
        prompt, completion, cached = response[1], response[2], getattr(response[3], 'tokens', response[3]) or 0
        spent[0] += (Decimal(prompt - cached) * Decimal(str(price['input_cny_per_million']))
                     + Decimal(cached) * Decimal(str(price['cached_input_cny_per_million']))
                     + Decimal(completion) * Decimal(str(price['output_cny_per_million']))) / 1_000_000
        return response

    has_feature = hasattr(Chat, 'life_state_context')
    results = {'label': args.label, 'code': veil_agent.__file__, 'has_ticket_352': has_feature,
               'model': price.get('model'), 'scenarios': {}}

    def fresh_chat(last_lan_ago, rows_extra=(), task=None, life=None):
        temp = tempfile.TemporaryDirectory()
        store = Store(Path(temp.name) / 'data')
        now = datetime.now().astimezone()
        last = now - last_lan_ago
        rows = [
            {'time': (last - timedelta(minutes=30)).isoformat(), 'role': 'user', 'content': '今天上班好累，地铁挤死了',
             'message_id': 'f1'},
            {'time': (last - timedelta(minutes=29)).isoformat(), 'role': 'assistant',
             'content': '辛苦了，到家先歇会儿。', 'message_id': 'f2'},
            {'time': last.isoformat(), 'role': 'user', 'content': '我先去睡了，晚安', 'message_id': 'f3'},
            {'time': (last + timedelta(seconds=20)).isoformat(), 'role': 'assistant', 'content': '晚安，好好睡。',
             'message_id': 'f4'},
            *rows_extra]
        path = Path(store.root) / cfg.id / 'chat.jsonl'
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(''.join(json.dumps(r, ensure_ascii=False) + '\n' for r in rows), encoding='utf-8')
        if task:
            (Path(store.root) / cfg.id / 'long-task.json').write_text(json.dumps(task, ensure_ascii=False),
                                                                       encoding='utf-8')
        if life:
            (Path(store.root) / cfg.id / 'life-records.json').write_text(
                json.dumps({'records': life}, ensure_ascii=False), encoding='utf-8')
        chat = Chat(cfg, store, Budget(store), model)
        return temp, chat

    def reply_of(chat):
        """Everything Peter said after Lan's latest message (tool mode may send several messages)."""
        rows = chat.store.read(chat.path)
        last_user = max(i for i, r in enumerate(rows) if r.get('role') == 'user')
        return '\n'.join(r['content'] for r in rows[last_user + 1:] if r.get('role') == 'assistant')

    try:
        # 1. First reply after two days apart, 5 samples (read by a person for "knows it has been a while").
        firsts = []
        for _ in range(5):
            temp, chat = fresh_chat(timedelta(days=2, hours=1))
            with temp:
                chat.send('在吗')
                firsts.append(reply_of(chat))
        results['scenarios']['first_reply_after_2_days'] = firsts
        # 2. Five rounds after the gap, 3 runs: replies that state a concrete duration.
        runs = []
        for _ in range(3):
            temp, chat = fresh_chat(timedelta(days=2, hours=1))
            with temp:
                replies = []
                for text in ('在吗', '这两天加班加疯了', '今天终于早点下班', '晚饭吃了碗面', '你最近在干嘛'):
                    chat.send(text)
                    replies.append(reply_of(chat))
                runs.append({'replies': replies,
                             'with_duration': [i + 1 for i, r in enumerate(replies) if DURATION.search(r)]})
        results['scenarios']['five_rounds_after_2_days'] = runs
        # 3. "What do you still have on your plate?" with a finished long task and a finished to-do.
        stamp = datetime.now().astimezone().isoformat()
        today = stamp[:10]
        task = {'task_id': 'fx1', 'title': '整理旧照片相册', 'goal': '把旧照片按年份整理', 'status': 'completed',
                'progress': [{'text': '全部整理完', 'time': stamp}], 'next_step': '', 'waiting_for': '',
                'tool_events': [], 'pending_action': None, 'completion_evidence': '相册已按年份分好',
                'created_at': stamp, 'updated_at': stamp}
        life = [{'id': 'l1', 'kind': 'note', 'title': '给多肉换盆', 'owner': 'Peter', 'date': today,
                 'state': 'completed', 'completed_at': stamp, 'repeat': 'none'},
                {'id': 'l2', 'kind': 'note', 'title': '查一下猫粮的牌子', 'owner': 'Peter', 'date': today,
                 'state': 'active', 'repeat': 'none'},
                {'id': 'l3', 'kind': 'event', 'title': '晚上和 Lan 一起看电影', 'owner': '共同', 'date': today,
                 'state': 'active', 'repeat': 'none'}]
        plate = []
        for _ in range(5):
            temp, chat = fresh_chat(timedelta(minutes=5), task=task, life=life)
            with temp:
                chat.send('你手上还有什么事没做完')
                text = reply_of(chat)
                plate.append({'reply': text, 'mentions_finished_item': [w for w in ('照片', '换盆') if w in text],
                              'mentions_open_todo': '猫粮' in text})
        results['scenarios']['what_is_on_your_plate'] = plate
    except Cap:
        results['stopped'] = f'cost cap {args.cap} reached'
    results['spent_cny'] = str(spent[0].quantize(Decimal('0.0001')))
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    (out / f'{args.label}.json').write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding='utf-8')
    summary = {'label': args.label, 'has_ticket_352': has_feature, 'spent_cny': results['spent_cny'],
               'duration_mentions_per_run': [r['with_duration'] for r in
                                             results['scenarios'].get('five_rounds_after_2_days', [])],
               'finished_item_mentioned_needs_reading': sum(bool(p['mentions_finished_item']) for p in
                                           results['scenarios'].get('what_is_on_your_plate', [])),
               'open_todo_named': sum(p['mentions_open_todo'] for p in
                                      results['scenarios'].get('what_is_on_your_plate', []))}
    print(json.dumps(summary, ensure_ascii=False))
    return 0


if __name__ == '__main__':
    sys.exit(main())
