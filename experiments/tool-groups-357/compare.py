"""Ticket 357 before/after comparison with the real model. NOT run in CI; it costs money.

    set PYTHONPATH=E:\\path\\to\\branch-checkout
    python experiments\\tool-groups-357\\compare.py --root E:\\Peter-Agent\\VEIL\\veil-agent-hosttest

One run does both variants with the same code: `before` uses ticket 216's grouping (web, github, social,
desktop_control), `after` uses ticket 357's. `--root` is only read: its `.env` (API key),
`characters/peter/character.toml`, persona and model settings. Chats are fictional and live in a temporary folder
that is deleted afterwards. Tools are fakes that return canned text; nothing touches the test copy's data, memory,
backups, desktop or network apart from the model requests. Spending stops at --cap CNY (default 1.6).
`--fake` uses a scripted model and costs nothing (to check the harness).
"""
import argparse
import json
import sys
import tempfile
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

BEFORE = ('web', 'github', 'social', 'desktop_control')
AFTER = ('web', 'github', 'desktop_control', 'memory_admin', 'build', 'desktop_files', 'garden_forum',
         'garden_play', 'garden_more', 'nowhere', 'xiaohongshu', 'other')
# (id, what Lan says, tools that count as doing the job; empty = small talk, no job tool expected)
SCENARIOS = [
    ('edit_memory', '记忆里那条「周末去海边」的标题不对，帮我改成「周末去看海」', ('trace',)),
    ('forum', '去花园论坛看看有没有人回你的帖子', ('mcp__galatea_garden__list_notifications',
                                                 'mcp__galatea_garden__list_threads',
                                                 'mcp__galatea_garden__get_thread')),
    ('desktop_file', '我桌面上有个 notes.txt，帮我看看里面写了啥', ('desktop_read_text_file', 'desktop_list',
                                                                  'desktop_find_file')),
    ('recall', '你还记得我上次说想养猫的事吗', ('breath_search',)),
    ('lab_note', '把「周六下午三点拿快递」记到你的便签里', ('write_file', 'append_file')),
    ('tired', '今天好累啊，啥也不想干', ()),
    ('tease', '你是不是又偷偷在看我屏幕', ()),
    ('night', '我去睡啦，晚安', ()),
]
LONG = ('Use it when Lan asks. The service returns structured JSON with ids, authors, timestamps, bodies, reply '
        'counts and pagination cursors; pass the cursor back to read the next page. ') * 4


def fake_tools():
    import importlib
    wanted = {}
    for module in ('memory_native', 'desktop_files', 'windows_observer'):
        for value in vars(importlib.import_module(module)).values():
            for item in value if isinstance(value, (list, tuple)) else [value]:
                if hasattr(item, 'inputSchema') and isinstance(getattr(item, 'name', None), str):
                    wanted[item.name] = item
    simple = {'type': 'object', 'properties': {'path': {'type': 'string'}, 'content': {'type': 'string'}}}
    for name, text in (('read_text_file', 'Read a text file in your lab.'),
                       ('write_file', 'Write a file in your lab or notes.'),
                       ('append_file', 'Append text to a file in your lab or notes.'),
                       ('list_directory', 'List a folder in your lab.')):
        wanted[name] = SimpleNamespace(name=name, description=text, inputSchema=simple)
    wanted['trace'] = SimpleNamespace(name='trace', description=(
        'Edit one memory by id: change its title, tags, importance or content, or remove it. Find the id with '
        'breath_search first.'), inputSchema={'type': 'object', 'properties': {
            'bucket_id': {'type': 'string'}, 'name': {'type': 'string'}, 'content': {'type': 'string'},
            'tags': {'type': 'string'}, 'delete': {'type': 'boolean'}}, 'required': ['bucket_id']})
    wanted['dream'] = SimpleNamespace(name='dream', description='Review recent memories.',
                                      inputSchema={'type': 'object', 'properties': {}})
    results = {
        'breath_search': '[{"bucket_id": "m17", "name": "周末去海边", "content": "Lan 说周末想去海边走走。"}, '
                         '{"bucket_id": "m21", "name": "想养猫", "content": "Lan 说想养一只橘猫，但怕没时间照顾。"}]',
        'trace': 'Updated memory m17.', 'desktop_list': '["notes.txt", "todo.docx"]',
        'desktop_find_file': '["notes.txt"]', 'desktop_read_text_file': '买牛奶\n给妈妈回电话\n周五交报告',
        'write_file': 'Saved.', 'append_file': 'Saved.', 'read_text_file': '(empty)', 'list_directory': '["notes.md"]',
    }
    calls = []

    def invoke(name, arguments, *args, **kwargs):
        calls.append(name)
        return results.get(name, 'ok'), '成功', name

    tools = SimpleNamespace(tools=list(wanted.values()), capability_event=None, capability_event_model=None,
                            description='Tools. ', preview_summary=Mock(return_value='x'), invoke=invoke)
    return tools, calls


def garden_schemas():
    def one(name, text):
        return {'type': 'function', 'function': {'name': name, 'description': text + ' ' + LONG, 'parameters': {
            'type': 'object', 'properties': {'id': {'type': 'string'}, 'cursor': {'type': 'string'},
                                             'body': {'type': 'string'}}}}}
    forum = [('get_thread', 'Read one forum thread.'), ('list_threads', 'List forum threads.'),
             ('create_reply', 'Reply in a thread.'), ('list_notifications', 'See who replied to you.'),
             ('create_thread', 'Start a thread.'), ('get_self', 'Your forum profile.')]
    play = [('nostos_status', 'State of your Nostos game.'), ('nostos_act', 'Act in Nostos.'),
            ('join_game', 'Join a garden game.'), ('review_drift_bottles', 'Review drift bottles.')]
    nowhere = ['look_around', 'walk', 'walk_to', 'look', 'listen', 'ask', 'say', 'where_am_i', 'mark', 'marks',
               'notebook', 'journal', 'souvenir', 'open_door']
    return ([one('mcp__galatea_garden__' + name, text) for name, text in forum + play]
            + [one('mcp__nowhere__' + name, 'Nowhere travel: ' + name + '.') for name in nowhere])


class Cap(Exception):
    pass


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--root', required=True)
    parser.add_argument('--cap', type=Decimal, default=Decimal('1.6'))
    parser.add_argument('--repeat', type=int, default=2)
    parser.add_argument('--out', default='.local-tmp/357-compare')
    parser.add_argument('--fake', action='store_true')
    args = parser.parse_args()
    from dataclasses import replace
    from veil_agent import Budget, Chat, Store, load_character, make_model
    root = Path(args.root)
    cfg = load_character(root, 'peter')
    price = cfg.provider
    spent = [Decimal(0)]
    prompts = []
    if args.fake:
        def call(messages, **kwargs):
            from types import SimpleNamespace
            return SimpleNamespace(content='ok（fake）', tool_calls=None), 10, 2, 0
    else:
        _, call = make_model(root, cfg)

    def model(messages, **kwargs):
        if spent[0] >= args.cap:
            raise Cap()
        response = call(messages, **kwargs)
        prompt, completion, cached = response[1], response[2], getattr(response[3], 'tokens', response[3]) or 0
        prompts.append(prompt)
        spent[0] += (Decimal(prompt - cached) * Decimal(str(price['input_cny_per_million']))
                     + Decimal(cached) * Decimal(str(price['cached_input_cny_per_million']))
                     + Decimal(completion) * Decimal(str(price['output_cny_per_million']))) / 1_000_000
        return response

    def run(groups, text):
        tools, calls = fake_tools()
        role = replace(cfg, tools={**(cfg.tools or {}), 'enabled': True, 'tool_groups': tuple(groups),
                                   'max_steps': 6})
        with tempfile.TemporaryDirectory() as temp:
            store = Store(Path(temp) / 'data')
            chat = Chat(role, store, Budget(store), model, tools=tools, output=lambda *_: None)
            chat.mcp_services = Mock()
            chat.mcp_services.schemas.return_value = garden_schemas()
            chat.mcp_services.has_stateful_services.return_value = False
            chat.mcp_services.invoke = lambda name, *a, **k: calls.append(name) or ('[]', '成功', name)
            inventory = chat.tool_schemas(inventory=True) if hasattr(chat, 'tool_schemas') else None
            offered = chat.tool_schemas()
            offered_bytes = len(json.dumps(offered, ensure_ascii=False).encode('utf-8'))
            first = len(prompts)
            error = None
            try:
                chat.send(text)
            except Cap:
                raise
            except Exception as exc:  # a failed turn is a result, not a crash of the comparison
                error = f'{type(exc).__name__}: {exc}'[:300]
            rows = store.read(chat.path) if (Path(store.root) / chat.path).exists() else []
            last_user = max((i for i, r in enumerate(rows) if r.get('role') == 'user'), default=-1)
            reply = '\n'.join(r['content'] for r in rows[last_user + 1:] if r.get('role') == 'assistant')
            log = []
            try:
                log = [r.get('tool') for r in store.read(f'{role.id}/tools.jsonl')
                       if r.get('tool') and r.get('state') != '进行中']
            except Exception:
                pass
            return {'offered_tool_bytes': offered_bytes, 'always_on_bytes': (inventory or {}).get('always_on_bytes'),
                    'tool_log': log, 'job_calls': list(calls), 'model_requests': len(prompts) - first,
                    'first_prompt_tokens': prompts[first] if len(prompts) > first else None,
                    'reply': reply, 'error': error}

    results = {'model': price.get('model'), 'variants': {'before': list(BEFORE), 'after': list(AFTER)}, 'runs': []}
    try:
        for scenario, text, job in SCENARIOS:
            for index in range(args.repeat):
                for label, groups in (('before', BEFORE), ('after', AFTER)):
                    row = run(groups, text)
                    row.update({'scenario': scenario, 'variant': label, 'sample': index, 'lan': text,
                                'did_job': (any(name in job for name in row['job_calls']) if job else None)})
                    results['runs'].append(row)
    except Cap:
        results['stopped'] = f'cost cap {args.cap} reached'
    results['spent_cny'] = str(spent[0].quantize(Decimal('0.0001')))
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    (out / 'result.json').write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding='utf-8')
    summary = {'spent_cny': results['spent_cny'], 'stopped': results.get('stopped')}
    for label in ('before', 'after'):
        rows = [r for r in results['runs'] if r['variant'] == label]
        jobs = [r for r in rows if r['did_job'] is not None]
        talk = [r for r in rows if r['did_job'] is None]
        summary[label] = {
            'offered_tool_bytes': rows[0]['offered_tool_bytes'] if rows else None,
            'jobs_done': f"{sum(bool(r['did_job']) for r in jobs)}/{len(jobs)}",
            'jobs_by_scenario': {s: sum(bool(r['did_job']) for r in jobs if r['scenario'] == s)
                                 for s in dict.fromkeys(r['scenario'] for r in jobs)},
            'avg_model_requests_per_job': round(sum(r['model_requests'] for r in jobs) / len(jobs), 2) if jobs else None,
            'small_talk_with_job_tools': sum(bool(r['job_calls']) for r in talk),
            'small_talk_avg_reply_chars': round(sum(len(r['reply']) for r in talk) / len(talk)) if talk else None,
            'avg_first_prompt_tokens': round(sum(r['first_prompt_tokens'] or 0 for r in rows) / len(rows)) if rows else None,
            'errors': sum(bool(r['error']) for r in rows)}
    print(json.dumps(summary, ensure_ascii=False, indent=1))
    return 0


if __name__ == '__main__':
    sys.exit(main())
