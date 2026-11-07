"""Ticket 237 (prototype): a replaceable executor behind one tool, `workbench`.

Peter's own chat/autonomy model keeps the persona, memory, history and the final say. When a job needs a shell,
editing files, running programs or downloading things, it hands the job to the workbench: a short-lived
`opencode serve` process that works in one folder (data/<character>/workbench) with the same relay model.
VEIL decides every permission itself, enforces the step/time/repeat/cost limits from outside, writes the cost
into the shared ledger, and kills the whole process tree when the run ends. If OpenCode is missing or fails,
the tool just reports that; chat, memory and autonomous wakes are untouched.
"""
import base64
import json
import os
import re
import secrets
import shutil
import signal
import socket
import subprocess
import time
import urllib.request
from decimal import Decimal
from pathlib import Path
from typing import NamedTuple

TOOL_NAME = 'workbench'
MILLION = Decimal(1_000_000)
DEFAULT_LIMITS = {'max_steps': 30, 'max_seconds': 600, 'repeat_limit': 3, 'run_budget_cny': '0.5'}
RESULT_CHARS = 4000
_WINDOWS = os.name == 'nt'

TOOL_SCHEMA = {'type': 'function', 'function': {
    'name': TOOL_NAME,
    'description': (
        'Hand a job that needs a shell, editing files, running programs, installing packages or downloading '
        'things to your workbench. It works in its own folder (the workbench), can run commands and change '
        'files there, and returns a report. Anything outside that folder is refused or asked of Lan by the '
        'program. Give a complete, self-contained task; you decide what to do with the result.'),
    'parameters': {'type': 'object', 'properties': {
        'task': {'type': 'string', 'minLength': 1, 'maxLength': 4000,
                 'description': 'What to do and what to report back.'}},
        'required': ['task'], 'additionalProperties': False},
}}


class ExecutorResult(NamedTuple):
    text: str
    state: str
    cost: Decimal = Decimal(0)
    steps: int = 0


DANGEROUS = re.compile(
    r'(rm\s+-rf\s+(/|~|\$HOME)|rmdir\s+/s|\bdel\s+/[sfq]|\bformat\s+[a-z]:|\.env\b|[\\/]secrets\b|\.ssh|'
    r'\bsudo\b|\brunas\b|reg\s+(add|delete)|powershell.*-enc|\|\s*(sh|bash|iex)\b|invoke-expression|'
    r'shutdown|net\s+user|schtasks|\bcipher\s+/w|git\s+push|git\s+remote\s+add)', re.I)


def decide(permission, patterns, metadata, workdir, *, autonomy, ask):
    """VEIL's own answer to an OpenCode permission request: (reply, reason). `ask` is Lan's approval card."""
    text = ' '.join(str(p) for p in patterns) + ' ' + json.dumps(metadata or {}, ensure_ascii=False)
    if DANGEROUS.search(text):
        return 'reject', 'dangerous command'
    if permission == 'external_directory':
        if autonomy:
            return 'reject', 'outside the workbench; Lan is not asked during autonomous activity'
        shown = ', '.join(str(p) for p in patterns)[:300]
        answer = ask(f'Peter 的工作台想访问工作台文件夹之外的位置：{shown}。同意这一次吗？y/N ')
        return ('once', 'Lan approved') if isinstance(answer, str) and answer.strip().casefold() == 'y' \
            else ('reject', 'Lan did not approve')
    work = str(workdir).replace('\\', '/').lower()
    for pattern in patterns:
        p = str(pattern).replace('\\', '/')
        if permission in ('edit', 'write') and (p.startswith('/') or re.match(r'^[A-Za-z]:', p)):
            if not p.lower().startswith(work):
                return 'reject', 'write outside the workbench'
    if permission in ('bash', 'edit', 'write', 'webfetch', 'task', 'skill', 'todowrite', 'read', 'glob', 'grep'):
        return 'once', 'inside the workbench'
    # Tools from other MCP servers (VEIL's own tools included) are never approved here: VEIL runs those itself.
    return 'reject', 'not offered to the workbench'


def estimate_cost(messages, price, steps):
    """Tokens and cost from what OpenCode reports; a conservative guess when the relay reports nothing."""
    tin = tout = 0
    for m in messages:
        info = m.get('info', {})
        if info.get('role') == 'assistant':
            t = info.get('tokens') or {}
            cache = t.get('cache') or {}
            tin += int(t.get('input') or 0) + int(cache.get('read') or 0) + int(cache.get('write') or 0)
            tout += int(t.get('output') or 0) + int(t.get('reasoning') or 0)
    if not tin and not tout and steps:
        chars = len(json.dumps(messages, ensure_ascii=False))
        tin, tout = steps * chars // 3, chars // 6
    return tin, tout, Decimal(tin) / MILLION * price[0] + Decimal(tout) / MILLION * price[1]


def summarize(messages):
    steps, repeats, calls, text = 0, {}, 0, []
    for m in messages:
        if m.get('info', {}).get('role') == 'assistant':
            steps += 1
        for p in m.get('parts', []):
            if p.get('type') == 'tool':
                calls += 1
                key = (p.get('tool'), json.dumps(p.get('state', {}).get('input', {}), sort_keys=True))
                repeats[key] = repeats.get(key, 0) + 1
            elif p.get('type') == 'text' and m.get('info', {}).get('role') == 'assistant':
                text.append(p.get('text', ''))
    return steps, calls, max(repeats.values(), default=0), '\n'.join(text).strip()


def kill_tree(proc):
    if proc is None or proc.poll() is not None:
        return
    if _WINDOWS:
        subprocess.run(['taskkill', '/F', '/T', '/PID', str(proc.pid)], capture_output=True)
    else:
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except OSError:
            proc.kill()


class Api:
    def __init__(self, base, password):
        self.base = base
        self.auth = 'Basic ' + base64.b64encode(('opencode:' + password).encode()).decode()

    def call(self, method, path, body=None):
        request = urllib.request.Request(
            self.base + path, method=method, data=json.dumps(body).encode() if body is not None else None,
            headers={'Content-Type': 'application/json', 'Authorization': self.auth})
        with urllib.request.urlopen(request, timeout=30) as response:
            text = response.read().decode('utf-8', 'replace')
        return json.loads(text) if text.strip() else None


def free_port():
    with socket.socket() as s:
        s.bind(('127.0.0.1', 0))
        return s.getsockname()[1]


def find_command(configured=''):
    """The configured path, else `opencode` on PATH; None when it is not installed."""
    if configured:
        return configured if Path(configured).exists() else None
    return shutil.which('opencode')


def write_config(workdir, provider, key_env_name):
    context = int(provider.get('max_input_tokens', 32768))
    config = {
        '$schema': 'https://opencode.ai/config.json',
        'provider': {'relay': {
            'npm': '@ai-sdk/openai-compatible', 'name': 'Relay',
            'options': {'baseURL': provider['base_url'], 'apiKey': '{env:' + key_env_name + '}'},
            'models': {provider['model']: {'name': provider['model'], 'tool_call': True,
                                           'limit': {'context': context, 'output': min(8192, context // 4)}}}}},
        'permission': {'bash': 'ask', 'edit': 'ask', 'webfetch': 'ask', 'external_directory': 'ask',
                       'read': {'*': 'allow', '*.env': 'deny', '*.env.*': 'deny'}},
        'share': 'disabled', 'autoupdate': False}
    (Path(workdir) / 'opencode.json').write_text(json.dumps(config, ensure_ascii=False, indent=1), encoding='utf-8')


def run(task, *, command, workdir, home, provider, key, limits, autonomy, ask, stopped, record_cost,
        api_factory=None, popen=subprocess.Popen, clock=time.monotonic, sleep=time.sleep):
    """Run one task. `record_cost(cost)` is called once with the estimated cost, whatever the outcome."""
    limits = {**DEFAULT_LIMITS, **(limits or {})}
    workdir, home = Path(workdir), Path(home)
    workdir.mkdir(parents=True, exist_ok=True)
    home.mkdir(parents=True, exist_ok=True)
    price = (Decimal(str(provider.get('input_cny_per_million', 0))),
             Decimal(str(provider.get('output_cny_per_million', 0))))
    cap = Decimal(str(limits['run_budget_cny']))
    key_env = 'VEIL_WORKBENCH_KEY'
    write_config(workdir, provider, key_env)
    port, password = free_port(), secrets.token_urlsafe(24)
    env = {k: v for k, v in os.environ.items() if not k.upper().endswith(('_API_KEY', '_KEY', '_TOKEN', '_SECRET'))}
    env.update({key_env: key, 'OPENCODE_SERVER_PASSWORD': password, 'HOME': str(home), 'USERPROFILE': str(home),
                'XDG_DATA_HOME': str(home / 'data'), 'XDG_CONFIG_HOME': str(home / 'config'),
                'XDG_CACHE_HOME': str(home / 'cache'), 'XDG_STATE_HOME': str(home / 'state'),
                'OPENCODE_DISABLE_AUTOUPDATE': '1'})
    proc, messages, cost, steps, stop = None, [], Decimal(0), 0, ''
    try:
        proc = popen([command, 'serve', '--port', str(port)], cwd=str(workdir), env=env, shell=_WINDOWS,
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                     **({} if _WINDOWS else {'start_new_session': True}))
        api = (api_factory or Api)(f'http://127.0.0.1:{port}', password)
        started = clock()
        while True:
            try:
                api.call('GET', '/session/status')
                break
            except Exception:
                if proc.poll() is not None or clock() - started > 60 or stopped():
                    return ExecutorResult('The workbench could not start (OpenCode did not answer).', '出错')
                sleep(1)
        session = api.call('POST', '/session', {'title': 'workbench'})['id']
        api.call('POST', f'/session/{session}/prompt_async', {
            'parts': [{'type': 'text', 'text': task}], 'model': {'providerID': 'relay', 'modelID': provider['model']}})
        started, idle, answered = clock(), 0, []
        while True:
            sleep(1)
            if stopped():
                stop = 'stopped by Lan'
            try:
                for request in api.call('GET', '/permission') or []:
                    if request.get('sessionID') != session or request.get('id') in answered:
                        continue
                    answered.append(request.get('id'))
                    reply, _why = decide(request.get('permission', ''), request.get('patterns', []),
                                         request.get('metadata'), workdir, autonomy=autonomy, ask=ask)
                    api.call('POST', f"/permission/{request['id']}/reply", {'reply': reply})
                messages = api.call('GET', f'/session/{session}/message') or []
            except Exception:
                if proc.poll() is not None:
                    stop = stop or 'OpenCode stopped unexpectedly'
            steps, calls, repeat, _text = summarize(messages)
            _tin, _tout, cost = estimate_cost(messages, price, steps)
            if not stop:
                if steps >= int(limits['max_steps']):
                    stop = f'reached the {limits["max_steps"]}-step limit'
                elif clock() - started > int(limits['max_seconds']):
                    stop = f'reached the {int(limits["max_seconds"]) // 60}-minute limit'
                elif cost >= cap:
                    stop = f'reached the cost limit (¥{cap})'
                elif repeat > int(limits['repeat_limit']):
                    stop = f'the same call was repeated more than {limits["repeat_limit"]} times'
            if stop:
                try:
                    api.call('POST', f'/session/{session}/abort')
                except Exception:
                    pass
                break
            try:
                status = api.call('GET', '/session/status') or {}
                busy = status.get(session, {}).get('type') not in (None, 'idle')
                pending = any(r.get('sessionID') == session for r in (api.call('GET', '/permission') or []))
            except Exception:
                busy, pending = True, False
                if proc.poll() is not None:
                    stop = 'OpenCode stopped unexpectedly'
                    break
            idle = idle + 1 if (not busy and not pending and steps > 0) else 0
            if idle >= 3:
                break
    except Exception as exc:
        return_text = 'The workbench failed: ' + str(exc)[:300]
        return ExecutorResult(return_text, '出错', cost, steps)
    finally:
        kill_tree(proc)
        try:
            record_cost(cost)
        except Exception:
            pass
    steps, calls, _repeat, text = summarize(messages)
    note = f'[workbench: {steps} steps, about ¥{cost.quantize(Decimal("0.0001"))}, folder {workdir}]'
    if stop:
        return ExecutorResult(f'The workbench was stopped: {stop}. Partial report:\n{text[:RESULT_CHARS]}\n{note}',
                              '出错', cost, steps)
    if not text:
        return ExecutorResult('The workbench finished without a report.\n' + note, '出错', cost, steps)
    return ExecutorResult(text[:RESULT_CHARS] + '\n' + note, '成功', cost, steps)
