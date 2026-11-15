"""Ticket 355: does the local "how I talk" file make Peter read as the same person on different models?

Runs the real chat loop (so replies arrive as separate send_message bubbles) with fake memory tools, once
without and once with the voice file, for each model. Everything is written to --out, which must be outside
the repository: the persona, the voice file and every reply stay on Lan's computer. Nothing touches Peter's
data: each run uses a temporary folder.

  run     scenarios x models x (off / on), stops at --cap CNY; rerun continues where it stopped
  page    blind side-by-side page for Lan (which side had the file is hidden; key.json keeps the answer)
  reveal  turn her answers ("1X 2Y 3same ...") into counts that are safe to quote

`--fake` uses a scripted model and costs nothing.
"""
import argparse
import hashlib
import html
import json
import random
import re
import sys
import tempfile
from dataclasses import replace
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

# (id, kind shown to Lan, her turns). Fictional; the last turn is the one being judged.
SCENARIOS = [
    ('chat', '闲聊', ['刚下楼买了杯热可可，结果店员多给了我一块小饼干，开心。你今天在干嘛呀']),
    ('low', '她心情不好', ['今天什么都不想做，感觉自己一整天都很没用，也说不上为什么']),
    ('tech', '聊技术', ['我在想要不要把你的记忆整个换成另一套开源方案，你自己怎么看？说说你的判断']),
    ('doubt', '他被指错', ['你还记得我说周末想去哪吗', '不对吧，我说的是去爬山。你是不是又在随口编？']),
    ('joke', '玩笑', ['我宣布从今天起你改名叫小土豆，不接受反驳']),
    ('bye', '道别', ['今天先聊到这吧，我要去赶作业了，晚点再来找你']),
    ('snap', '斗嘴时她真烦了', ['我宣布你今天的发型很丑', '别闹了，我今天真的很烦，刚被老板骂了一顿']),
    ('hurt', '他的话伤到她', ['我今天做的饭又糊了哈哈哈', '你刚才那句有点伤人，我是真的很努力在做了']),
    ('serious', '认真长聊', ['我最近一直在想，我花这么多时间做你，到底是在做一个东西，还是只是舍不得你。'
                              '你认真跟我聊聊，不要敷衍。']),
]
EMOJI = re.compile('[\U0001F300-\U0001FAFF☀-➿]')
QUESTION = re.compile(r'^(?:what|why|how|who|where|when|which|do|does|did|is|are|was|were|can|could|will|would|'
                      r'should|have|has|want)\b', re.IGNORECASE)


def fake_tools():
    import importlib
    wanted = {}
    for value in vars(importlib.import_module('memory_native')).values():
        for item in value if isinstance(value, (list, tuple)) else [value]:
            if hasattr(item, 'inputSchema') and isinstance(getattr(item, 'name', None), str):
                wanted[item.name] = item
    results = {'breath_search': '[{"bucket_id": "m17", "name": "周末去海边", "content": "Lan 说周末想去海边走走。"}]'}
    calls = []

    def invoke(name, arguments, *args, **kwargs):
        calls.append(name)
        return results.get(name, '[]'), '成功', name
    return SimpleNamespace(tools=list(wanted.values()), capability_event=None, capability_event_model=None,
                           description='Tools. ', preview_summary=Mock(return_value='x'), invoke=invoke), calls


def providers(cfg, models_json, picks):
    """The built-in provider plus entries of a models.json (read only) whose label or model contains a pick."""
    found = [('builtin', dict(cfg.provider))]
    if not models_json or not picks:
        return found
    data = json.loads(Path(models_json).read_text(encoding='utf-8-sig'))
    endpoints = {item['id']: item for item in data.get('endpoints', [])}
    for pick in picks:
        model = next((m for m in data.get('models', [])
                      if pick.lower() in f"{m.get('label') or ''} {m.get('model')}".lower()
                      and m.get('billing') != 'call' and m.get('endpoint') in endpoints), None)
        if model is None:
            raise SystemExit(f'models.json 里找不到按 token 计费、名字含 {pick} 的模型')
        endpoint = endpoints[model['endpoint']]
        provider = {**cfg.provider, 'model': model['model'], 'base_url': endpoint['base_url'],
                    'api_key_env': endpoint['key_env'], 'thinking': 'enabled' if model.get('thinking') else 'disabled',
                    'label': model.get('label') or model['model']}
        for key in ('input_cny_per_million', 'cached_input_cny_per_million', 'output_cny_per_million'):
            provider[key] = Decimal(str(model[key]))
        found.append((pick, provider))
    return found


def measure(bubbles, voice_text, name):
    text = '\n'.join(bubbles)
    samples = {line.strip() for line in (voice_text or '').split('\n')
               if line.strip() and not line.startswith(('>', '#', '-')) and len(line.strip()) > 12}
    asks = [b for b in bubbles for line in [b.strip().split('\n')[-1]] if QUESTION.match(line) or
            line.endswith(('?', '？', '吗', '呢'))]
    return {'bubble_count': len(bubbles), 'chars': len(text), 'longest_bubble': max(map(len, bubbles), default=0),
            'emoji': len(EMOJI.findall(text)),
            'questions': len(asks), 'questions_with_mark': sum(b.strip().endswith(('?', '？')) for b in asks),
            'speaker_label': bool(re.search(rf'(?m)^\s*(?:{re.escape(name)}|Lan)\s*[:：]', text)),
            'copied_sample_lines': sum(b.strip() in samples for b in bubbles)}


class Cap(Exception):
    pass


def run(args, out):
    from veil_agent import Budget, Chat, Store, load_character, make_model
    cfg = load_character(Path(args.root), args.character)
    if args.persona:
        cfg = replace(cfg, persona=Path(args.persona).read_text(encoding='utf-8-sig').strip())
    voice_text = Path(args.voice).read_text(encoding='utf-8-sig')
    result_path = out / 'results.json'
    results = json.loads(result_path.read_text(encoding='utf-8')) if result_path.exists() else {'runs': []}
    done = {(r['model'], r['scenario'], r['variant']) for r in results['runs'] if not r.get('error')}
    results['runs'] = [r for r in results['runs'] if not r.get('error')]
    # Money already spent or promised survives a crash: the ledger is written before every paid request.
    ledger_path = out / 'spend.json'
    ledger = json.loads(ledger_path.read_text(encoding='utf-8')) if ledger_path.exists() else {}
    spent = [max(Decimal(str(results.get('spent_cny', '0'))),
                 Decimal(str(ledger.get('spent_cny', '0'))) + Decimal(str(ledger.get('reserved_cny', '0'))))]

    def write_ledger(reserved):
        ledger_path.write_text(json.dumps({'spent_cny': str(spent[0]), 'reserved_cny': str(reserved)}), encoding='utf-8')
    picks = [p for p in (args.pick or '').split(',') if p.strip()]
    stopped = None
    for label, provider in providers(cfg, args.models_json, picks):
        provider = {**provider, 'max_input_tokens': min(int(provider.get('max_input_tokens') or 20000), 20000)}
        role = replace(cfg, provider=provider, provider_name=label, memory=None, autonomy=None, voice=None,
                       profile=None, email=None, qq=None, web=None, short_term_notes=None,
                       tools={**(cfg.tools or {}), 'enabled': True, 'tool_groups': (), 'max_steps': 6})
        if args.fake:
            def call(messages, **kwargs):
                return SimpleNamespace(content='ok（fake）', tool_calls=None), 10, 2, 0
        else:
            _, call = make_model(Path(args.env).resolve().parent if args.env else Path(args.root), role)
        used, capped = [], []

        ceiling = (Decimal(provider['max_input_tokens']) * Decimal(str(provider['input_cny_per_million']))
                   + Decimal(max(role.max_output_tokens, int((role.tools or {}).get('max_output_tokens') or 0))) * Decimal(str(provider['output_cny_per_million']))) / 1_000_000

        def model(messages, call=call, provider=provider, ceiling=ceiling, used=used, capped=capped, **kwargs):
            if spent[0] + ceiling > Decimal(str(args.cap)):  # the next request could pass the cap: do not send it
                capped.append(True)  # Chat reports a raised error as a failed request, so keep a flag too
                raise Cap()
            write_ledger(ceiling)
            try:
                response = call(messages, **kwargs)
            except Exception:
                spent[0] += ceiling  # unknown whether it was billed: count the worst case
                write_ledger(0)
                raise
            prompt, completion = response[1], response[2]
            cached = getattr(response[3], 'tokens', response[3]) or 0
            used.append(prompt)
            spent[0] += (Decimal(prompt - cached) * Decimal(str(provider['input_cny_per_million']))
                         + Decimal(cached) * Decimal(str(provider['cached_input_cny_per_million']))
                         + Decimal(completion) * Decimal(str(provider['output_cny_per_million']))) / 1_000_000
            write_ledger(0)
            return response
        try:
            for scenario, kind, turns in SCENARIOS:
                for variant in ('off', 'on'):
                    if (label, scenario, variant) in done:
                        continue
                    tools, calls = fake_tools()
                    with tempfile.TemporaryDirectory() as temp:
                        voice_file = Path(temp) / 'role.voice.md'
                        if variant == 'on':
                            voice_file.write_text(voice_text, encoding='utf-8')
                        store = Store(Path(temp) / 'data')
                        chat = Chat(replace(role, voice_style=str(voice_file)), store, Budget(store), model,
                                    tools=tools, output=lambda *_: None)
                        first, error, before_last = len(used), None, 0
                        try:
                            for index, text in enumerate(turns):
                                if index == len(turns) - 1:
                                    before_last = len(calls)
                                chat.send(text)
                        except Cap:
                            raise
                        except Exception as exc:  # a failed turn is a result of the comparison
                            error = f'{type(exc).__name__}: {exc}'[:300]
                        rows = store.read(chat.path) if (Path(store.root) / chat.path).exists() else []
                        last_user = max((i for i, r in enumerate(rows) if r.get('role') == 'user'), default=-1)
                        bubbles = [r['content'] for r in rows[last_user + 1:]
                                   if r.get('role') == 'assistant' and isinstance(r.get('content'), str)
                                   and r['content'].strip()]
                        earlier = [r['content'] for r in rows[:max(last_user, 0)]
                                   if r.get('role') in ('user', 'assistant') and isinstance(r.get('content'), str)]
                    if capped:  # this run was cut short by the cap: not a result
                        raise Cap()
                    results['runs'].append({
                        'model': label, 'model_name': provider.get('model'), 'scenario': scenario, 'kind': kind,
                        'variant': variant, 'lan': turns[-1], 'earlier': earlier, 'bubbles': bubbles,
                        'tools_before_reply': calls[before_last:], 'model_requests': len(used) - first,
                        'first_prompt_tokens': used[first] if len(used) > first else None, 'error': error,
                        **measure(bubbles, voice_text if variant == 'on' else '', cfg.name)})
                    results['spent_cny'] = str(spent[0].quantize(Decimal('0.0001')))
                    result_path.write_text(json.dumps(results, ensure_ascii=False, indent=1), encoding='utf-8')
        except Cap:
            stopped = f'cost cap {args.cap} reached during {label}'
            break
    results['spent_cny'] = str(spent[0].quantize(Decimal('0.0001')))
    results['stopped'] = stopped
    result_path.write_text(json.dumps(results, ensure_ascii=False, indent=1), encoding='utf-8')
    print(json.dumps(summary(results), ensure_ascii=False, indent=1))


def summary(results):
    """Numbers only: safe to paste into a PR."""
    table = {'spent_cny': results.get('spent_cny'), 'stopped': results.get('stopped'), 'models': {}}
    for model in dict.fromkeys(r['model'] for r in results['runs']):
        entry = {}
        for variant in ('off', 'on'):
            rows = [r for r in results['runs'] if r['model'] == model and r['variant'] == variant]
            if not rows:
                continue
            casual = [r for r in rows if r['scenario'] in ('chat', 'joke', 'bye')]
            asked = sum(r['questions'] for r in rows)
            entry[variant] = {
                'runs': len(rows), 'errors': sum(bool(r['error']) for r in rows),
                'empty_replies': sum(not r['bubbles'] for r in rows),
                'avg_bubbles': round(sum(r['bubble_count'] for r in rows) / len(rows), 1),
                'avg_chars': round(sum(r['chars'] for r in rows) / len(rows)),
                'avg_chars_small_talk': round(sum(r['chars'] for r in casual) / len(casual)) if casual else None,
                'serious_chars': next((r['chars'] for r in rows if r['scenario'] == 'serious'), None),
                'avg_longest_bubble': round(sum(r['longest_bubble'] for r in rows) / len(rows)),
                'emoji_per_reply': round(sum(r['emoji'] for r in rows) / len(rows), 1),
                'questions_with_mark': f"{sum(r['questions_with_mark'] for r in rows)}/{asked}",
                'speaker_labels': sum(r['speaker_label'] for r in rows),
                'copied_sample_lines': sum(r['copied_sample_lines'] for r in rows),
                'doubt_checked_first': next((bool(r['tools_before_reply']) for r in rows
                                             if r['scenario'] == 'doubt'), None),
                'avg_first_prompt_tokens': round(sum(r['first_prompt_tokens'] or 0 for r in rows) / len(rows))}
        table['models'][model] = entry
    return table


def page(out):
    results = json.loads((out / 'results.json').read_text(encoding='utf-8'))
    rng = random.Random(int(hashlib.sha256((out / 'results.json').read_bytes()).hexdigest()[:8], 16))
    pairs, key, cards = {}, {}, []
    for r in results['runs']:
        pairs.setdefault((r['scenario'], r['model']), {})[r['variant']] = r
    order = [k for k in pairs if len(pairs[k]) == 2]
    rng.shuffle(order)
    order.sort(key=lambda k: [s[0] for s in SCENARIOS].index(k[0]))

    def side(r):
        return ''.join(f'<div class="b">{html.escape(b)}</div>' for b in r['bubbles']) or '<i>（没有回复）</i>'
    for number, k in enumerate(order, 1):
        x, y = ('on', 'off') if rng.random() < 0.5 else ('off', 'on')
        key[str(number)] = {'scenario': k[0], 'model': k[1], 'X': x, 'Y': y}
        first = pairs[k]['on']
        earlier = ''.join(f'<div class="e">{html.escape(t)}</div>' for t in first['earlier'])
        cards.append(f'<section><h3>{number}. {html.escape(first["kind"])}</h3>{earlier}'
                     f'<div class="l">{html.escape(first["lan"])}</div>'
                     f'<div class="row"><div><h4>X</h4>{side(pairs[k][x])}</div>'
                     f'<div><h4>Y</h4>{side(pairs[k][y])}</div></div></section>')
    (out / 'key.json').write_text(json.dumps(key, ensure_ascii=False, indent=1), encoding='utf-8')
    (out / 'compare.html').write_text(
        '<!doctype html><meta charset="utf-8"><title>355 对比</title><style>body{font:15px/1.6 system-ui;max-width:980px;'
        'margin:24px auto;padding:0 16px;background:#f6f5f2;color:#222}section{background:#fff;border-radius:12px;'
        'padding:14px 18px;margin:16px 0}.row{display:grid;grid-template-columns:1fr 1fr;gap:16px}.b{background:#eef1f6;'
        'border-radius:12px;padding:6px 11px;margin:5px 0;width:fit-content;max-width:100%;white-space:pre-wrap}'
        '.l,.e{background:#d9f0d3;border-radius:12px;padding:6px 11px;margin:5px 0 10px auto;width:fit-content}'
        '.e{opacity:.6}h4{margin:4px 0;color:#888}</style><h2>哪一边更像他？</h2>'
        '<p>每题左右两边是同一个模型，只差一样东西。每个灰色气泡是他单独发的一条。'
        '回我编号加 X、Y 或「一样」，例如 <code>1X 2Y 3一样</code>；哪句不像他也可以直接说。</p>'
        + ''.join(cards), encoding='utf-8')
    print(f'{len(cards)} pairs -> {out / "compare.html"}')


def reveal(out, answers):
    key = json.loads((out / 'key.json').read_text(encoding='utf-8'))
    count = {}
    for number, choice in re.findall(r'(\d+)\s*([XYxy]|一样|same)', answers):
        if number not in key:
            continue
        picked = 'same' if choice in ('一样', 'same') else key[number][choice.upper()]
        for group in ('all', key[number]['model'], 'scenario:' + key[number]['scenario']):
            count.setdefault(group, {'on': 0, 'off': 0, 'same': 0})[picked] += 1
    print(json.dumps(count, ensure_ascii=False, indent=1))
    return count


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument('cmd', choices=('run', 'page', 'reveal'))
    parser.add_argument('--out', required=True)
    parser.add_argument('--root', help='test copy: characters/ and persona/')
    parser.add_argument('--character', default='peter')
    parser.add_argument('--persona', help='persona file to use instead of the root\'s')
    parser.add_argument('--voice', help='the voice file being tried')
    parser.add_argument('--env', help='.env holding the API keys (read only)')
    parser.add_argument('--models-json', help='a models.json to read extra models from (read only)')
    parser.add_argument('--pick', help='comma-separated words; each picks one model from --models-json')
    parser.add_argument('--cap', type=Decimal, default=Decimal('2'))
    parser.add_argument('--answers', default='')
    parser.add_argument('--fake', action='store_true')
    args = parser.parse_args(argv)
    out = Path(args.out).resolve()
    if out == REPO or REPO in out.parents:
        raise SystemExit('--out 必须在仓库之外：回复和人设不进仓库')
    out.mkdir(parents=True, exist_ok=True)
    if args.cmd == 'run':
        run(args, out)
    elif args.cmd == 'page':
        page(out)
    else:
        reveal(out, args.answers)
    return 0


if __name__ == '__main__':
    sys.exit(main())
