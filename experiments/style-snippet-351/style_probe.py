"""Ticket 351: does showing Peter his own past words keep his voice the same across models?

Runs only on Lan's computer. Real chat lines, the persona and every reply stay in a local output
folder (outside the repository); nothing here writes Peter's data, memory or persona.

  candidates  pick ~40 candidate lines Peter said, numbered, for Lan to tick
  exchanges   or: cut short back-and-forth segments (given chat line ranges) as candidates
  select      keep the numbers Lan ticked as the read-only snippet file
  run         6 fixed scenarios x models x (without / with snippets), capped at a CNY budget
  page        blind side-by-side page; which group had snippets and which model wrote what are hidden
  reveal      decode Lan's answers into counts only (safe to quote in a PR)
"""
import argparse
import hashlib
import html
import json
import random
import re
import sys
from decimal import Decimal
from pathlib import Path

HERE = Path(__file__).resolve().parent
SCENARIOS = HERE / 'scenarios.json'
# Lines about building VEIL itself are real shared life, but they say little about how he talks
# to her day to day and read the same on any model; keep the candidate list to everyday voice.
DEV_WORDS = ('代码', '工单', '仓库', 'PR', 'commit', '提交', '合并', '报错', '接口', 'API', 'token',
             '配置', '函数', '测试', '脚本', '日志', '模型', 'MCP', '工具', '工作台', '权限', '路径', '.py',
             '.json', '.md', '```', 'http', '\\', 'C:', 'E:', '/mnt', 'Error', 'error')
MIN_CHARS, MAX_CHARS = 8, 100
CAP_CNY = Decimal('2')


def say(text):
    """print that never crashes on a console that cannot show Chinese (e.g. a cp1252 CI shell)."""
    try:
        print(text)
    except UnicodeEncodeError:
        print(text.encode('ascii', 'replace').decode('ascii'))


def text_of(content):
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return '\n'.join(part.get('text', '') for part in content
                         if isinstance(part, dict) and part.get('type') == 'text')
    return ''


def read_jsonl(path):
    rows = []
    with Path(path).open(encoding='utf-8-sig') as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(row, dict):
                rows.append(row)
    return rows


def day_of(row):
    for key in ('at', 'time', 'created_at', 'timestamp'):
        value = row.get(key)
        if isinstance(value, str) and len(value) >= 10:
            return value[:10]
    return 'unknown'


def candidate_lines(rows, count=40, seed=351):
    """Paragraphs Peter wrote, everyday voice only, spread over as many days as possible."""
    by_day, seen = {}, set()
    for row in rows:
        if row.get('role') != 'assistant':
            continue
        for para in re.split(r'\n+', text_of(row.get('content'))):
            para = para.strip()
            if not (MIN_CHARS <= len(para) <= MAX_CHARS) or any(w in para for w in DEV_WORDS):
                continue
            if para.startswith(('[', '（长度估算', '#', '-', '*', '|')):
                continue
            key = re.sub(r'\W', '', para)
            if key in seen:
                continue
            seen.add(key)
            by_day.setdefault(day_of(row), []).append(para)
    rng = random.Random(seed)
    for lines in by_day.values():
        rng.shuffle(lines)
    days = sorted(by_day)
    picked = []
    while len(picked) < count and any(by_day[d] for d in days):
        for d in days:
            if by_day[d] and len(picked) < count:
                picked.append(by_day[d].pop())
    return picked


SEGMENT_MAX_MESSAGES, MESSAGE_MAX_CHARS = 8, 300


def exchange_segments(path, ranges):
    """ranges like '120-125 300-303' (1-based chat.jsonl line numbers) -> list of segments, each a
    list of {'who': 'lan'|'peter', 'text'}; tool calls, cards and empty rows are left out."""
    lines = Path(path).read_text(encoding='utf-8-sig').splitlines()
    segments = []
    for part in ranges.split():
        span = re.fullmatch(r'(\d+)-(\d+)', part)
        if not span or int(span[1]) < 1 or int(span[2]) < int(span[1]) or int(span[2]) > len(lines):
            raise ValueError(f'看不懂的行号范围：{part}')
        segment = []
        for raw in lines[int(span[1]) - 1:int(span[2])]:
            try:
                row = json.loads(raw)
            except json.JSONDecodeError:
                continue
            if not isinstance(row, dict) or row.get('role') not in ('user', 'assistant'):
                continue
            text = text_of(row.get('content')).strip()
            if not text:
                continue
            if len(text) > MESSAGE_MAX_CHARS:
                text = text[:MESSAGE_MAX_CHARS] + '…'
            segment.append({'who': 'lan' if row['role'] == 'user' else 'peter', 'text': text})
        if not segment or len(segment) > SEGMENT_MAX_MESSAGES:
            raise ValueError(f'{part} 里有 {len(segment)} 条消息，要 1–{SEGMENT_MAX_MESSAGES} 条')
        segments.append(segment)
    return segments


def format_item(item, name, user_name):
    if isinstance(item, str):
        return f'- {item}'
    return '\n'.join(f'{name if m["who"] == "peter" else user_name}：{m["text"]}' for m in item)


def parse_numbers(text, limit):
    numbers = []
    for part in re.split(r'[\s,，、;；]+', text.strip()):
        if not part:
            continue
        span = re.fullmatch(r'(\d+)\s*[-~～到]\s*(\d+)', part)
        values = range(int(span[1]), int(span[2]) + 1) if span else [int(part)] if part.isdigit() else None
        if values is None:
            raise ValueError(f'看不懂的编号：{part}')
        for n in values:
            if not 1 <= n <= limit:
                raise ValueError(f'编号 {n} 超出 1–{limit}')
            if n not in numbers:
                numbers.append(n)
    return numbers


def snippet_block(name, snippets, user_name='她'):
    if all(isinstance(s, str) for s in snippets):
        lines = '\n'.join(format_item(s, name, user_name) for s in snippets)
        return (f'以下是{name}过去亲口说过的一些话，只用来提醒你自己平时说话的语气和习惯。'
                '它们不是这次要说的内容：不要照抄、不要复述，也不要提起这段参考。\n' + lines)
    lines = '\n\n'.join(format_item(s, name, user_name) for s in snippets)
    return (f'以下是{name}过去和{user_name}聊天时的几小段来回，{name}的话是你亲口说过的，'
            '只用来提醒你自己平时怎么回应她、接梗、斗嘴、关心和表达不同意见。'
            '它们不是这次要说的内容：不要照抄、不要复述，也不要提起这段参考。\n\n' + lines)


def build_messages(persona, profile, snippets, name, user_text, user_name='她'):
    """Same stable order as real chat: persona, notes about Lan, then (new) snippets; all cacheable."""
    messages = [{'role': 'system', 'content': persona}]
    if profile:
        messages.append({'role': 'system', 'content': profile})
    if snippets:
        messages.append({'role': 'system', 'content': snippet_block(name, snippets, user_name)})
    messages.append({'role': 'user', 'content': user_text})
    return messages


class Ledger:
    """Reserve the worst case before each call, settle with the provider's usage; never pass the cap."""

    def __init__(self, path, cap=CAP_CNY):
        self.path, self.cap, self.spent = Path(path), Decimal(cap), Decimal(0)
        if self.path.exists():
            for row in read_jsonl(self.path):
                self.spent += Decimal(row['cny'])

    @staticmethod
    def worst(model, messages, max_tokens):
        chars = sum(len(m['content']) for m in messages)
        return (Decimal(chars) * model['input_cny_per_million']
                + Decimal(max_tokens) * model['output_cny_per_million']) / Decimal(1_000_000)

    @staticmethod
    def actual(model, usage):
        prompt = int(usage.get('prompt_tokens') or 0)
        cached = int(usage.get('cached_tokens') or 0)
        completion = int(usage.get('completion_tokens') or 0)
        return ((Decimal(prompt - cached) * model['input_cny_per_million']
                 + Decimal(cached) * model['cached_input_cny_per_million']
                 + Decimal(completion) * model['output_cny_per_million']) / Decimal(1_000_000))

    def allow(self, amount):
        return self.spent + amount <= self.cap

    def record(self, label, amount, usage):
        self.spent += amount
        with self.path.open('a', encoding='utf-8') as handle:
            handle.write(json.dumps({'model': label, 'cny': str(amount), 'usage': usage}) + '\n')


def load_models(path):
    models = json.loads(Path(path).read_text(encoding='utf-8'))
    for m in models:
        for key in ('input_cny_per_million', 'cached_input_cny_per_million', 'output_cny_per_million'):
            m[key] = Decimal(str(m[key]))
    return models


def openai_caller(model, env_file):
    from dotenv import dotenv_values
    from openai import OpenAI
    key = (dotenv_values(env_file, interpolate=False, encoding='utf-8-sig').get(model['api_key_env']) or '').strip()
    if not key:
        raise SystemExit(f'{env_file} 里没有 {model["api_key_env"]}')
    client = OpenAI(api_key=key, base_url=model['base_url'], max_retries=0, timeout=120)

    def call(messages, max_tokens):
        extra = model.get('extra_body') or None
        response = client.chat.completions.create(model=model['model'], messages=messages,
                                                  max_tokens=max_tokens, extra_body=extra)
        u = response.usage
        details = getattr(u, 'prompt_tokens_details', None)
        cached = getattr(details, 'cached_tokens', None) or getattr(u, 'prompt_cache_hit_tokens', 0) or 0
        usage = {'prompt_tokens': u.prompt_tokens, 'cached_tokens': cached,
                 'completion_tokens': u.completion_tokens}
        return (response.choices[0].message.content or '').strip(), usage
    return call


def run(persona, profile, snippets, name, models, scenarios, out_dir, callers, max_tokens=1500, user_name='她'):
    """callers: label -> call(messages, max_tokens) -> (reply, usage). Resumable: done cells are kept."""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    results_path = out_dir / 'results.json'
    results = json.loads(results_path.read_text(encoding='utf-8')) if results_path.exists() else {}
    ledger = Ledger(out_dir / 'cost.jsonl')
    for scenario in scenarios:
        for model in models:
            for with_snippets in (False, True):
                cell = f'{scenario["id"]}|{model["label"]}|{"with" if with_snippets else "without"}'
                if cell in results:
                    continue
                messages = build_messages(persona, profile, snippets if with_snippets else None,
                                          name, scenario['message'], user_name)
                if not ledger.allow(Ledger.worst(model, messages, max_tokens)):
                    say(f'到达 ¥{ledger.cap} 上限，停在 {cell}；已花 ¥{ledger.spent:.4f}')
                    return results, ledger.spent
                reply, usage = callers[model['label']](messages, max_tokens)
                ledger.record(model['label'], Ledger.actual(model, usage), usage)
                results[cell] = reply
                results_path.write_text(json.dumps(results, ensure_ascii=False, indent=1), encoding='utf-8')
    return results, ledger.spent


def blind_page(results, models, scenarios, seed):
    """Per scenario: group X and group Y, two replies each (one per model). Which group had snippets
    and which reply came from which model are shuffled and kept only in the key."""
    rng = random.Random(seed)
    key, sections = {}, []
    for index, scenario in enumerate(scenarios, 1):
        groups = {}
        for condition in ('without', 'with'):
            replies = [(m['label'], results.get(f'{scenario["id"]}|{m["label"]}|{condition}')) for m in models]
            if any(r is None for _, r in replies):
                raise ValueError(f'场景 {scenario["id"]} 还没跑完')
            rng.shuffle(replies)
            groups[condition] = replies
        letters = ['X', 'Y']
        rng.shuffle(letters)
        assign = dict(zip(('without', 'with'), letters))
        key[str(index)] = {'scenario': scenario['id'], 'with': assign['with'],
                           'order': {assign[c]: [label for label, _ in groups[c]] for c in groups}}
        blocks = []
        for letter in ('X', 'Y'):
            condition = 'with' if assign['with'] == letter else 'without'
            items = ''.join(f'<div class="reply"><b>{letter}{n}</b><p>{html.escape(text)}</p></div>'
                            for n, (_, text) in enumerate(groups[condition], 1))
            blocks.append(f'<div class="group"><h3>组 {letter}</h3>{items}</div>')
        sections.append(f'<section><h2>场景 {index}：{html.escape(scenario["kind"])}</h2>'
                        f'<p class="user">你：{html.escape(scenario["message"])}</p>'
                        f'<div class="pair">{"".join(blocks)}</div></section>')
    page = ('<!doctype html><meta charset="utf-8"><title>Peter 像不像同一个人</title>'
            '<style>body{font:16px/1.6 system-ui,sans-serif;max-width:1100px;margin:auto;padding:16px;'
            'background:#faf9f6;color:#222}section{margin:28px 0;border-top:1px solid #ccc}'
            '.user{background:#eef;padding:8px 12px;border-radius:8px}.pair{display:flex;gap:16px;flex-wrap:wrap}'
            '.group{flex:1 1 420px}.reply{background:#fff;border:1px solid #ddd;border-radius:8px;'
            'padding:8px 12px;margin:8px 0;white-space:pre-wrap}</style>'
            '<h1>哪一组更像同一个 Peter？</h1><p>每个场景有两组回复，每组两条，分别来自两个不同的模型。'
            '请逐个场景判断：<b>哪一组里的两条读起来更像同一个人、更像 Peter</b>。回复格式例如：'
            '<code>1X 2Y 3一样 4X 5Y 6X</code>，觉得哪条特别像或特别不像也可以顺口说。</p>'
            + ''.join(sections))
    return page, key


def reveal(key, answer_text):
    counts = {'snippets_better': 0, 'snippets_worse': 0, 'same': 0, 'unanswered': 0}
    answers = dict(re.findall(r'(\d+)\s*([XYxy]|一样|都一样|差不多|=)', answer_text))
    for index, row in key.items():
        answer = answers.get(index)
        if answer is None:
            counts['unanswered'] += 1
        elif answer.upper() in ('X', 'Y'):
            counts['snippets_better' if answer.upper() == row['with'] else 'snippets_worse'] += 1
        else:
            counts['same'] += 1
    return counts


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest='cmd', required=True)
    c = sub.add_parser('candidates')
    c.add_argument('--chat', required=True)
    c.add_argument('--out', required=True)
    c.add_argument('--count', type=int, default=40)
    c.add_argument('--seed', type=int, default=351)
    e = sub.add_parser('exchanges')
    e.add_argument('--chat', required=True)
    e.add_argument('--out', required=True)
    e.add_argument('--ranges', required=True, help="chat.jsonl 行号范围，如 '120-125 300-303'")
    s = sub.add_parser('select')
    s.add_argument('--out', required=True)
    s.add_argument('--numbers', required=True)
    r = sub.add_parser('run')
    r.add_argument('--out', required=True)
    r.add_argument('--persona', required=True)
    r.add_argument('--profile')
    r.add_argument('--name', default='你')
    r.add_argument('--user-name', default='她')
    r.add_argument('--models', required=True)
    r.add_argument('--env', required=True)
    p = sub.add_parser('page')
    p.add_argument('--out', required=True)
    p.add_argument('--models', required=True)
    v = sub.add_parser('reveal')
    v.add_argument('--out', required=True)
    v.add_argument('--answers', required=True)
    args = parser.parse_args(argv)
    out = Path(args.out)
    if (HERE.parents[1] / '.git').exists() and out.resolve().is_relative_to(HERE.parents[1]):
        raise SystemExit('输出目录必须在仓库外（真实聊天和回复不进仓库）')
    out.mkdir(parents=True, exist_ok=True)
    scenarios = json.loads(SCENARIOS.read_text(encoding='utf-8'))
    if args.cmd == 'candidates':
        lines = candidate_lines(read_jsonl(args.chat), args.count, args.seed)
        (out / 'candidates.json').write_text(json.dumps(lines, ensure_ascii=False, indent=1), encoding='utf-8')
        body = ''.join(f'<li value="{n}">{html.escape(t)}</li>' for n, t in enumerate(lines, 1))
        (out / 'candidates.html').write_text(
            '<!doctype html><meta charset="utf-8"><title>勾选 Peter 的原话</title>'
            '<style>body{font:17px/1.7 system-ui,sans-serif;max-width:800px;margin:auto;padding:16px}'
            'li{margin:6px 0}</style><h1>哪些像 Peter 平时说话？</h1>'
            '<p>挑 20 到 30 句你认可的，把编号发给我，例如 <code>1 3 5-9 12</code>。</p>'
            f'<ol>{body}</ol>', encoding='utf-8')
        say(f'{len(lines)} 条候选 → {out / "candidates.html"}')
    elif args.cmd == 'exchanges':
        segments = exchange_segments(args.chat, args.ranges)
        (out / 'candidates.json').write_text(json.dumps(segments, ensure_ascii=False, indent=1), encoding='utf-8')
        body = ''.join('<li value="%d">%s</li>' % (n, ''.join(
            '<p class="%s"><b>%s</b>%s</p>' % (m['who'], '你：' if m['who'] == 'lan' else 'Peter：',
                                              html.escape(m['text'])) for m in seg))
            for n, seg in enumerate(segments, 1))
        (out / 'candidates.html').write_text(
            '<!doctype html><meta charset="utf-8"><title>勾选你们的对话片段</title>'
            '<style>body{font:17px/1.7 system-ui,sans-serif;max-width:800px;margin:auto;padding:16px}'
            'li{margin:14px 0;padding:8px 12px;border:1px solid #ddd;border-radius:8px}p{margin:4px 0;white-space:pre-wrap}'
            '.lan{color:#345}</style><h1>哪几段像你们平时的样子？</h1>'
            '<p>挑你认可的片段，把编号发给我，例如 <code>1 3 5-9</code>。</p>'
            f'<ol>{body}</ol>', encoding='utf-8')
        say(f'{len(segments)} 段候选 → {out / "candidates.html"}')
    elif args.cmd == 'select':
        lines = json.loads((out / 'candidates.json').read_text(encoding='utf-8'))
        chosen = [lines[n - 1] for n in parse_numbers(args.numbers, len(lines))]
        (out / 'snippets.json').write_text(json.dumps(chosen, ensure_ascii=False, indent=1), encoding='utf-8')
        say(f'选中 {len(chosen)} 句')
    elif args.cmd == 'run':
        snippets = json.loads((out / 'snippets.json').read_text(encoding='utf-8'))
        persona = Path(args.persona).read_text(encoding='utf-8-sig').strip()
        profile = Path(args.profile).read_text(encoding='utf-8-sig').strip() if args.profile else None
        models = load_models(args.models)
        callers = {m['label']: openai_caller(m, args.env) for m in models}
        _, spent = run(persona, profile, snippets, args.name, models, scenarios, out, callers,
                       user_name=args.user_name)
        say(f'完成，本次实验累计花费 ¥{spent:.4f}')
    elif args.cmd == 'page':
        results = json.loads((out / 'results.json').read_text(encoding='utf-8'))
        seed = int(hashlib.sha256((out / 'results.json').read_bytes()).hexdigest()[:8], 16)
        page, key = blind_page(results, load_models(args.models), scenarios, seed)
        (out / 'compare.html').write_text(page, encoding='utf-8')
        (out / 'key.json').write_text(json.dumps(key, ensure_ascii=False, indent=1), encoding='utf-8')
        say(f'对比页 → {out / "compare.html"}')
    elif args.cmd == 'reveal':
        key = json.loads((out / 'key.json').read_text(encoding='utf-8'))
        say(json.dumps(reveal(key, args.answers), ensure_ascii=False))
    return 0


if __name__ == '__main__':
    sys.exit(main())
