"""Run the voice/conflict regression set against the configured models.

Read-only: reads persona, optional persona/<role>.voice.md, .env keys and
data/<role>/models.json. Writes one markdown report. Costs real money.
"""
import argparse, json, re, sys, time, urllib.error, urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
HERE = Path(__file__).resolve().parent


def load_env():
    env = {}
    path = ROOT / '.env'
    if not path.exists():
        return env
    for line in path.read_text(encoding='utf-8').splitlines():
        m = re.match(r'^\s*([A-Za-z_0-9]+)\s*=\s*(.*)$', line)
        if m:
            value = m.group(2).strip()
            if len(value) >= 2 and value[0] == value[-1] and value[0] in '"\'':
                value = value[1:-1]
            env[m.group(1)] = value
    return env


def targets(role):
    env = load_env()
    out = []
    models_path = ROOT / 'data' / role / 'models.json'
    if models_path.exists():
        cfg = json.loads(models_path.read_text(encoding='utf-8'))
        endpoints = {e['id']: e for e in cfg.get('endpoints', [])}
        for row in cfg.get('models', []):
            endpoint = endpoints.get(row.get('endpoint'))
            if not endpoint:
                continue
            key = env.get(endpoint.get('key_env', ''), '')
            if key:
                out.append((row.get('label') or row.get('model'), endpoint['base_url'], key, row['model']))
    toml = ROOT / 'characters' / role / 'character.toml'
    if toml.exists():
        text = toml.read_text(encoding='utf-8')
        m = re.search(r'\[providers\.([a-z_]+)\]([\s\S]*?)(?=\n\[|\Z)', text)
        if m and env.get('DEEPSEEK_API_KEY'):
            block = re.search(r'\[providers\.deepseek\]([\s\S]*?)(?=\n\[|\Z)', text)
            if block:
                base = re.search(r'base_url\s*=\s*"([^"]+)"', block.group(1))
                model = re.search(r'model\s*=\s*"([^"]+)"', block.group(1))
                if base and model:
                    out.append(('deepseek (builtin)', base.group(1), env['DEEPSEEK_API_KEY'], model.group(1)))
    unique, seen = [], set()
    for item in out:
        key = (str(item[1]).rstrip('/').lower(), str(item[3]))
        if key in seen:
            continue
        seen.add(key)
        unique.append(item)
    return unique


def ask(base, key, model, messages, tries=2):
    body = json.dumps({'model': model, 'messages': messages, 'max_tokens': 3000}).encode()
    bases = [base, base.rstrip('/') + '/v1'] if 'deepseek.com' in base else [base]
    last = ''
    for b in bases:
        for _ in range(tries):
            req = urllib.request.Request(b.rstrip('/') + '/chat/completions', data=body,
                                         headers={'Authorization': 'Bearer ' + key,
                                                  'Content-Type': 'application/json'})
            try:
                resp = json.loads(urllib.request.urlopen(req, timeout=240).read())
                text = (resp['choices'][0]['message'].get('content') or '').strip()
                if text:
                    return text
                last = '[empty reply]'
            except urllib.error.HTTPError as exc:
                last = f'[HTTP {exc.code}] ' + exc.read().decode('utf-8', 'replace')[:160]
                time.sleep(1)
            except Exception as exc:
                last = f'[error] {type(exc).__name__}'
                time.sleep(1)
    return last


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--role', default='peter')
    parser.add_argument('--out', default='voice-eval.md')
    parser.add_argument('--conflict-only', action='store_true')
    args = parser.parse_args()

    persona_path = ROOT / 'persona' / f'{args.role}.md'
    if not persona_path.exists():
        sys.exit(f'missing {persona_path}')
    head = [{'role': 'system', 'content': persona_path.read_text(encoding='utf-8')}]
    voice_path = ROOT / 'persona' / f'{args.role}.voice.md'
    if voice_path.exists():
        head.append({'role': 'system', 'content': voice_path.read_text(encoding='utf-8')})

    scenarios = json.loads((HERE / 'scenarios.json').read_text(encoding='utf-8'))
    questions = scenarios['conflict'] if args.conflict_only else scenarios['daily'] + scenarios['conflict']

    lines = ['# voice regression', '']
    for name, base, key, model in targets(args.role):
        lines += [f'## {name}', '', f'`{model}`', '']
        for question in questions:
            answer = ask(base, key, model, head + [{'role': 'user', 'content': question}])
            lines += [f'**{question}**', '', answer, '']
        print(name, 'done', flush=True)

    Path(args.out).write_text('\n'.join(lines), encoding='utf-8')
    print('written', args.out)


if __name__ == '__main__':
    main()
