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
import posixpath
import re
import secrets
import shlex
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import shutil
import signal
import socket
import subprocess
import time
import urllib.error
import urllib.request
from decimal import Decimal
from pathlib import Path
from typing import NamedTuple
from windows_process import hidden_subprocess_kwargs

TOOL_NAME = 'workbench'
MILLION = Decimal(1_000_000)
DEFAULT_LIMITS = {'max_steps': 30, 'max_seconds': 600, 'repeat_limit': 3, 'run_budget_cny': '0.5'}
RESULT_CHARS = 4000
_WINDOWS = os.name == 'nt'

SAFE_COMMANDS = ('git status*', 'git branch*', 'ls', 'ls *', 'dir', 'dir *',
                 'pwd', 'python -m unittest*', 'python -m pytest*', 'pytest*', 'npm test*', 'node --test*')
SECRET_GLOBS = ('*.env', '*.env.*', 'secrets/*', '*/secrets/*', '*\\secrets\\*', '.ssh/*', '*/.ssh/*', '*\\.ssh\\*',
                '*id_rsa*', '*id_ed25519*', '*Login Data*', 'Cookies', '*/Cookies', '*\\Cookies')

CONTAINER_NOTE = (
    'Environment: you run in a Linux container with bash. The project is the current directory (/work). '
    'Any Windows path or Windows command (cmd, findstr, dir) in the request below refers to the owner\'s PC '
    'and does not exist here: use the current directory and Linux commands, and do not search for it elsewhere.\n\n')

TOOL_SCHEMA = {'type': 'function', 'function': {
    'name': TOOL_NAME,
    'description': (
        'Hand a job that needs a shell, editing files, running programs, installing packages or downloading '
        'things to your workbench. The program finds the project the job is about (or uses your own workspace '
        'for new work, the same folder your file tools use), keeps a way back before changing an existing project, '
        'and returns a report. '
        'Publishing, downloading, installing and bulk deletes are asked of Lan by the program. Give a '
        'complete, self-contained task in words (what is wrong, what to do, what to report); the workbench works in '
        'the project folder itself, so name the project rather than giving file paths or system commands. '
        'You decide what to do with the result.'),
    'parameters': {'type': 'object', 'properties': {
        'task': {'type': 'string', 'minLength': 1, 'maxLength': 4000,
                 'description': 'What to do and what to report back.'},
        'project': {'type': 'string', 'maxLength': 200,
                    'description': 'Optional project name when you know which existing project the job is about. '
                                   'The program finds it; nobody needs to give a path.'}},
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
    r'shutdown|net\s+user|schtasks|\bcipher\s+/w|git\s+remote\s+add|\bgit\s+config\s+--global)', re.I)

# Not forbidden, but Lan decides (chat) or the job is parked (wake).
ASK_LAN = {
    'git': {'push', 'clone', 'fetch', 'pull'},
    'installers': {'pip', 'pip3', 'npm', 'pnpm', 'yarn', 'winget', 'choco', 'apt', 'apt-get',
                   'brew', 'cargo', 'go', 'bun'},
    'network': {'curl', 'wget', 'iwr', 'invoke-webrequest', 'invoke-restmethod', 'irm', 'npx', 'pnpx', 'bunx'},
}


def _git_subcommand(tokens):
    value_options = {'-C', '-c', '--git-dir', '--work-tree', '--namespace'}
    args = iter(tokens[1:])
    for arg in args:
        if arg in value_options:
            next(args, None)
        elif arg.startswith('-'):
            continue
        else:
            return arg.casefold()
    return ''


def _command_name(token):
    name = posixpath.basename(token.replace('\\', '/')).casefold()
    return re.sub(r'\.(exe|cmd|bat|com)$', '', name)


def _bash_command_needs_lan(tokens):
    git_config_alias = any(
        re.match(r'^GIT_CONFIG_KEY_\d+=alias\.', token, re.I) or
        token.casefold().startswith('git_config_parameters=')
        for token in tokens)
    while tokens and re.match(r'^[A-Za-z_][A-Za-z0-9_]*=', tokens[0]):
        tokens = tokens[1:]
    if not tokens:
        return False
    command = _command_name(tokens[0])
    if command == 'git' and (git_config_alias or any(
            arg.casefold().startswith('alias.') or
            arg.casefold().startswith('--config-env=alias.') or
            arg.casefold() == '--config-env' and index + 1 < len(tokens) and
            tokens[index + 1].casefold().startswith('alias.')
            for index, arg in enumerate(tokens[1:], 1))):
        return True
    if command in ASK_LAN['network']:
        return True
    if command == 'git':
        return _git_subcommand(tokens) in ASK_LAN['git']
    if command in ASK_LAN['installers']:
        return any(arg.casefold() in {'install', 'add', 'get', 'i', 'ci', 'up', 'update', 'upgrade', 'dlx'}
                   for arg in tokens[1:]) or (command == 'npm' and 'exec' in tokens[1:])
    if re.fullmatch(r'pip\d*(?:\.\d+)*', command):
        return any(arg.casefold() == 'install' for arg in tokens[1:])
    if command in {'python', 'python3'} and tokens[1:3] == ['-m', 'pip']:
        return any(arg.casefold() == 'install' for arg in tokens[3:])
    if re.fullmatch(r'python\d+(?:\.\d+)*', command) and tokens[1:3] == ['-m', 'pip']:
        return any(arg.casefold() == 'install' for arg in tokens[3:])
    if command == 'rm':
        return any(arg == '--recursive' or
                   (arg.startswith('-') and not arg.startswith('--') and 'r' in arg[1:].casefold())
                   for arg in tokens[1:])
    if command == 'remove-item':
        return any(arg.casefold() in {'-recurse', '-r'} for arg in tokens[1:])
    if command in {'del', 'rmdir', 'rd'}:
        return any(arg.casefold().startswith('/s') for arg in tokens[1:])
    return False


def _complex_bash_request(patterns, metadata):
    # OpenCode supplies parsed command fragments in patterns and the original shell input in metadata.command.
    command = metadata.get('command') if isinstance(metadata, dict) else None
    if not isinstance(command, str) or not command.strip() or not isinstance(patterns, list) or not patterns:
        return True
    try:
        lexer = shlex.shlex(command, posix=True, punctuation_chars=';&|<>()')
        lexer.whitespace_split = True
        shell_tokens = list(lexer)
        commands = [shlex.split(pattern, posix=True) for pattern in patterns if isinstance(pattern, str)]
    except ValueError:
        return True
    if len(commands) != len(patterns) or not commands or any(not item for item in commands):
        return True
    if '\n' in command or '\r' in command or '`' in command or '$(' in command:
        return True
    if any(re.fullmatch(r'[<>()]+', token) for token in shell_tokens):
        return True
    operators = [token for token in shell_tokens if re.fullmatch(r'[;&|]+', token)]
    if operators and len(commands) != len(operators) + 1:
        return True
    if len(commands) > 1:
        return not all(_safe_project_command(item) for item in commands)
    command_tokens = commands[0]
    while command_tokens and re.match(r'^[A-Za-z_][A-Za-z0-9_]*=', command_tokens[0]):
        command_tokens = command_tokens[1:]
    if not command_tokens:
        return True
    command_name = _command_name(command_tokens[0])
    if command_name in {'bash', 'sh', 'dash', 'zsh', 'fish', 'powershell', 'pwsh', 'cmd', 'xargs',
                        'env', 'command', 'time', 'exec', 'busybox', 'nohup', 'nice', 'setsid', 'eval',
                        'source', '.'}:
        return True
    if command_name in {'python', 'python3'} and command_tokens[1:3] in (
            ['-m', 'unittest'], ['-m', 'pytest']):
        return False
    if _safe_project_command(command_tokens):
        return False
    if command_name in {'python', 'python3', 'node', 'nodejs', 'deno', 'bun', 'ruby', 'perl', 'awk',
                        'gawk', 'mawk', 'php', 'osascript'} or re.fullmatch(r'python\d+(?:\.\d+)*', command_name):
        return True
    return False


def _safe_project_command(tokens):
    """Commands whose requested effect is inspection or running an explicitly named test file."""
    while tokens and re.match(r'^[A-Za-z_][A-Za-z0-9_]*=', tokens[0]):
        tokens = tokens[1:]
    if not tokens:
        return False
    command = _command_name(tokens[0])
    if command == 'git':
        return _git_subcommand(tokens) in {'status', 'branch', 'diff', 'log', 'show', 'rev-parse', 'ls-files'}
    if command in {'ls', 'dir', 'pwd', 'cat', 'type', 'head', 'tail', 'wc', 'stat', 'file', 'find', 'grep', 'rg'}:
        return True
    if command in {'python', 'python3'} or re.fullmatch(r'python\d+(?:\.\d+)*', command):
        if len(tokens) < 2 or tokens[1].startswith('-'):
            return False
        name = posixpath.basename(tokens[1].replace('\\', '/')).casefold()
        return name.startswith('test_') and name.endswith('.py') or name.endswith('_test.py')
    return False


def _approval_key(permission, patterns, metadata):
    command = metadata.get('command') if isinstance(metadata, dict) else None
    return permission, tuple(str(item) for item in patterns), command if isinstance(command, str) else ''


def _ask_lan(prompt, operation, key, ask, grants, approvals):
    if grants is not None and key in grants:
        if approvals is not None:
            approvals.append({'operation': operation, 'waiting': False, 'decision': 'approved_reused'})
        return 'once', 'approved_reused'
    answer = ask(prompt)
    normalized = answer.strip().casefold() if isinstance(answer, str) else ''
    decision = ('approved' if normalized == 'y' else 'cancelled' if normalized in {'cancel', 'cancelled'} else
                'timed_out' if normalized in {'timeout', 'timed_out'} else 'user_denied')
    if decision == 'approved' and grants is not None:
        grants.add(key)
    if approvals is not None:
        approvals.append({'operation': operation, 'waiting': True, 'decision': decision})
    return ('once', decision) if decision == 'approved' else ('reject', decision)


def decide(permission, patterns, metadata, workdir, *, autonomy, ask, grants=None, approvals=None):
    """VEIL's own answer to an OpenCode permission request: (reply, reason). `ask` is Lan's approval card."""
    text = ' '.join(str(p) for p in patterns) + ' ' + json.dumps(metadata or {}, ensure_ascii=False)
    if DANGEROUS.search(text):
        return 'reject', 'dangerous command'
    if permission == 'external_directory':
        if autonomy:
            return 'reject', 'outside the workbench; Lan is not asked during autonomous activity'
        shown = ', '.join(str(p) for p in patterns)[:300]
        return _ask_lan(f'Peter 的工作台想访问工作台文件夹之外的位置：{shown}。同意这一次吗？y/N ',
                        shown, _approval_key(permission, patterns, metadata), ask, grants, approvals)
    work = posixpath.normpath(str(workdir).replace('\\', '/')).lower()
    for pattern in patterns:
        p = str(pattern).replace('\\', '/')
        if permission in ('edit', 'write'):
            absolute = p.startswith('/') or re.match(r'^[A-Za-z]:', p)
            target = posixpath.normpath(p if absolute else work + '/' + p).lower()
            if target != work and not target.startswith(work.rstrip('/') + '/'):
                return 'reject', 'write outside the workbench'
    if permission == 'bash':
        needs_lan = False
        complex_request = False
        for pattern in patterns if isinstance(patterns, list) else ():
            if not isinstance(pattern, str):
                complex_request = True
                break
            try:
                tokens = shlex.split(pattern, posix=True)
            except ValueError:
                complex_request = True
                break
            if not tokens:
                complex_request = True
                break
            if _bash_command_needs_lan(tokens):
                needs_lan = True
                break
        command = metadata.get('command') if isinstance(metadata, dict) else None
        if isinstance(command, str):
            try:
                command_tokens = shlex.split(command, posix=True)
            except ValueError:
                complex_request = True
            else:
                if command_tokens:
                    needs_lan = needs_lan or _bash_command_needs_lan(command_tokens)
                else:
                    complex_request = True
        complex_request = complex_request or _complex_bash_request(patterns, metadata)
        if needs_lan or complex_request:
            if autonomy:
                return 'reject', 'needs Lan\'s approval; not asked during autonomous activity'
            command = metadata.get('command') if isinstance(metadata, dict) else None
            shown = str(command if isinstance(command, str) else patterns)[:300]
            if needs_lan:
                prompt = f'Peter 的工作台想执行：{shown}。这会联网、安装、发布或批量删除。同意这一次吗？y/N '
            else:
                prompt = f'Peter 的工作台想执行一条复合或无法确认安全的命令：{shown}。只执行这一次吗？y/N '
            return _ask_lan(prompt, shown, _approval_key(permission, patterns, metadata),
                            ask, grants, approvals)
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
        tin, tout = steps * chars, chars // 2  # no usage reported: assume the worst, never undercount
    return tin, tout, Decimal(tin) / MILLION * price[0] + Decimal(tout) / MILLION * price[1]


def summarize(messages):
    steps, repeats, calls, assistants = 0, {}, 0, []
    for m in messages:
        info = m.get('info', {})
        if info.get('role') == 'assistant' or info.get('type') == 'assistant':
            steps += 1
            text = '\n'.join(p.get('text', '') for p in m.get('parts', []) if p.get('type') == 'text').strip()
            assistants.append((info, text))
        for p in m.get('parts', []):
            if p.get('type') == 'tool':
                calls += 1
                key = (p.get('tool') or p.get('name'), json.dumps(p.get('state', {}).get('input', {}), sort_keys=True))
                repeats[key] = repeats.get(key, 0) + 1
    final = ''
    if assistants and assistants[-1][0].get('finish') == 'stop':
        final = assistants[-1][1]
    return steps, calls, max(repeats.values(), default=0), final


def diagnose(messages):
    """A short account of why a run ended without a report: model/API errors and the last tool calls."""
    lines = []
    tool_lines = []
    assistants = [m.get('info', {}) for m in messages
                  if m.get('info', {}).get('role') == 'assistant' or m.get('info', {}).get('type') == 'assistant']
    if assistants and assistants[-1].get('finish') not in (None, 'stop'):
        lines.append('last assistant message did not finish normally (' + str(assistants[-1]['finish'])[:40] + ')')
    for m in messages:
        info = m.get('info', {})
        error = info.get('error')
        if (info.get('role') == 'assistant' or info.get('type') == 'assistant') and error:
            data = error.get('data', {}) if isinstance(error, dict) else {}
            name = (error.get('name') or error.get('type') or '') if isinstance(error, dict) else error
            message = ((data.get('message') if isinstance(data, dict) else '') or error.get('message', '')) if isinstance(error, dict) else ''
            lines.append('model error: ' + str(name)[:60] + ' ' + str(message)[:200])
        for part in m.get('parts', []):
            if part.get('type') == 'tool':
                state = part.get('state', {})
                tool_input = state.get('input')
                words = str(tool_input.get('command') or '').split() if isinstance(tool_input, dict) else []
                shown = words[0][:30] if words else ''  # program name only: arguments may hold secrets
                tool = part.get('tool') or part.get('name') or 'tool'
                error = state.get('error')
                error_type = ((error.get('name') or error.get('type')) if isinstance(error, dict) else '')
                tool_lines.append(f"{tool} {state.get('status', '')} {shown} {error_type}".strip())
    if not any(p.get('type') == 'tool' for m in messages for p in m.get('parts', [])):
        lines.append('no tools were executed')
    return '; '.join(lines + tool_lines[-6:])


def kill_tree(proc):
    if proc is None:
        return
    if _WINDOWS:
        if proc.poll() is None:
            subprocess.run(['taskkill', '/F', '/T', '/PID', str(proc.pid)], capture_output=True,
                           **hidden_subprocess_kwargs(windows=_WINDOWS, module=subprocess))
    else:
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except OSError:
            proc.kill()


class KeyProxy:
    """A loopback relay in front of the model API. OpenCode (and every command it runs) only ever holds a random
    local token; the real API key stays in this process and is added to forwarded requests. The token is only
    valid for this run, and the proxy refuses to forward more than `max_requests` calls."""

    def __init__(self, upstream_base, key, *, max_requests=400, host='127.0.0.1', timeout=180):
        self.token = secrets.token_urlsafe(24)
        self.upstream = upstream_base.rstrip('/')
        self.requests = 0
        self.lock = threading.Lock()
        proxy = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = 'HTTP/1.0'

            def log_message(self, *_args):
                pass

            def _deny(self, code, text):
                self.send_response(code)
                self.send_header('Content-Length', str(len(text)))
                self.end_headers()
                self.wfile.write(text.encode())

            def do_POST(self):
                self._forward('POST')

            def do_GET(self):
                self._forward('GET')

            def _forward(self, method):
                if self.headers.get('Authorization') != 'Bearer ' + proxy.token:
                    return self._deny(401, 'bad token')
                with proxy.lock:
                    if proxy.requests >= max_requests:
                        return self._deny(429, 'request limit reached')
                    proxy.requests += 1
                path = self.path[3:] if self.path.startswith('/v1') else self.path
                body = self.rfile.read(int(self.headers.get('Content-Length') or 0)) if method == 'POST' else None
                request = urllib.request.Request(proxy.upstream + path, data=body, method=method, headers={
                    'Authorization': 'Bearer ' + key, 'Content-Type': self.headers.get('Content-Type', 'application/json'),
                    'Accept': self.headers.get('Accept', '*/*')})
                try:
                    response = urllib.request.urlopen(request, timeout=timeout)
                except urllib.error.HTTPError as exc:
                    response = exc
                except Exception as exc:
                    return self._deny(502, 'upstream error: ' + type(exc).__name__)
                self.send_response(response.status if hasattr(response, 'status') else response.code)
                self.send_header('Content-Type', response.headers.get('Content-Type', 'application/json'))
                self.end_headers()
                while True:
                    chunk = response.read(2048)
                    if not chunk:
                        break
                    self.wfile.write(chunk)
                    self.wfile.flush()

        self.server = ThreadingHTTPServer((host, 0), Handler)
        self.port = self.server.server_address[1]
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def local_base(self, host='127.0.0.1'):
        return f'http://{host}:{self.port}/v1'

    def close(self):
        self.server.shutdown()
        self.server.server_close()


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


def build_config(provider, token, base_url, protect=()):
    """The OpenCode config, passed inline (OPENCODE_CONFIG_CONTENT) so nothing is written into the user's project
    and a project's own opencode.json cannot loosen it. `token` is the local proxy token, not the real key."""
    context = int(provider.get('max_input_tokens', 32768))
    edit_permission = ('ask' if not protect else
                       {'*': 'ask', **{Path(path).as_posix(): 'deny' for path in protect}})
    return {
        '$schema': 'https://opencode.ai/config.json',
        'provider': {'relay': {
            'npm': '@ai-sdk/openai-compatible', 'name': 'Relay',
            'options': {'baseURL': base_url, 'apiKey': token},
            'models': {provider['model']: {'name': provider['model'], 'tool_call': True,
                                           'limit': {'context': context, 'output': min(8192, context // 4)}}}}},
        # OpenCode's own rule engine settles the clear cases itself (last matching pattern wins); what is left
        # as `ask` comes to the program (`decide`). Reading secrets is denied outright.
        'permission': {'bash': {'*': 'ask', **{c: 'allow' for c in SAFE_COMMANDS}},
                       'edit': edit_permission, 'webfetch': 'ask', 'external_directory': 'ask',
                       'read': {'*': 'allow', **{g: 'deny' for g in SECRET_GLOBS}}},
        'share': 'disabled', 'autoupdate': False}


SECRET_FILE = re.compile(r'^(\.env(\..*)?|.*\.pem|id_(rsa|ed25519).*|.*\.pfx|.*\.key)$', re.I)


def secret_files(workdir, max_depth=6):
    """Secret-looking files inside the project (shallow). In a container they are masked with an empty file."""
    found, root = [], Path(workdir)
    for base, dirs, names in os.walk(root):
        depth = len(Path(base).relative_to(root).parts)
        dirs[:] = [d for d in dirs if d not in ('.git', 'node_modules') and depth < max_depth]
        found += [Path(base) / n for n in names if SECRET_FILE.match(n)]
    return found


def container_command(container, *, name, workdir, port, env, masks):
    """`docker run` for OpenCode: only the project is mounted (read-write); secret-looking files in it are masked by
    empty read-only files; no host environment, no capabilities, bounded memory/processes. The server is published
    on the host's loopback only."""
    cmd = ['docker', 'run', '--rm', '--name', name, '--cap-drop', 'ALL', '--security-opt', 'no-new-privileges',
           '--pids-limit', '256', '--memory', container.get('memory', '2g'),
           '--add-host', 'host.docker.internal:host-gateway',
           '-p', f'127.0.0.1:{port}:{port}', '-v', f'{workdir}:/work', '-w', '/work']
    for source, target in masks:
        cmd += ['-v', f'{source}:/work/{target}:ro']
    for key, value in env.items():
        cmd += ['-e', f'{key}={value}']
    return cmd + [container['image'], 'opencode', 'serve', '--hostname', '0.0.0.0', '--port', str(port)]


def run(task, *, command, workdir, home, provider, key, limits, autonomy, ask, stopped, record_cost,
        api_factory=None, proxy_factory=None, container=None, protect=(), popen=subprocess.Popen, clock=time.monotonic, sleep=time.sleep):
    """Run one task. `record_cost(cost)` is called once with the estimated cost, whatever the outcome."""
    limits = {**DEFAULT_LIMITS, **(limits or {})}
    workdir, home = Path(workdir), Path(home)
    if protect and not container:
        try:
            record_cost(Decimal(0))
        except Exception:
            pass
        return ExecutorResult('The workbench cannot protect the growth journal without Docker isolation.', '被拒绝')
    workdir.mkdir(parents=True, exist_ok=True)
    home.mkdir(parents=True, exist_ok=True)
    price = (Decimal(str(provider.get('input_cny_per_million', 0))),
             Decimal(str(provider.get('output_cny_per_million', 0))))
    cap = Decimal(str(limits['run_budget_cny']))
    proxy = (proxy_factory or KeyProxy)(provider['base_url'], key, max_requests=int(limits['max_steps']) * 3 + 5,
                                        **({'host': '0.0.0.0'} if container else {}))
    config = build_config(provider, proxy.token, proxy.local_base('host.docker.internal' if container else '127.0.0.1'),
                          protect=protect)
    port, password = free_port(), secrets.token_urlsafe(24)
    keep = {'PATH', 'PATHEXT', 'SYSTEMROOT', 'WINDIR', 'COMSPEC', 'TEMP', 'TMP', 'TMPDIR', 'LANG', 'LC_ALL', 'PROGRAMFILES',
            'PROGRAMFILES(X86)', 'PROGRAMDATA', 'APPDATA', 'LOCALAPPDATA', 'NUMBER_OF_PROCESSORS', 'PROCESSOR_ARCHITECTURE'}
    env = {k: v for k, v in os.environ.items() if k.upper() in keep}
    shared = {'OPENCODE_SERVER_PASSWORD': password, 'OPENCODE_DISABLE_AUTOUPDATE': '1',
              'OPENCODE_CONFIG_CONTENT': json.dumps(config)}
    if container:
        name = 'veil-workbench-' + secrets.token_hex(4)
        masks = []
        empty = home / 'empty-file'
        empty.write_text('', encoding='utf-8')
        for path in secret_files(workdir):
            masks.append((str(empty), path.relative_to(workdir).as_posix()))
        for relative in protect:  # e.g. Peter's growth journal: it has its own append-only grant
            if (workdir / relative).is_file() and all(target != Path(relative).as_posix() for _, target in masks):
                masks.append((str(empty), Path(relative).as_posix()))
        command = container_command(container, name=name, workdir=str(workdir), port=port, masks=masks,
                                    env={**shared, 'HOME': '/tmp/home', 'XDG_DATA_HOME': '/tmp/home/data',
                                         'XDG_CONFIG_HOME': '/tmp/home/config', 'XDG_CACHE_HOME': '/tmp/home/cache',
                                         'XDG_STATE_HOME': '/tmp/home/state'})
        env = {k: v for k, v in env.items() if k.upper() in ('PATH', 'PATHEXT', 'SYSTEMROOT', 'WINDIR', 'COMSPEC',
                                                              'APPDATA', 'LOCALAPPDATA', 'USERPROFILE', 'HOME')}
    else:
        env.update({**shared, 'HOME': str(home), 'USERPROFILE': str(home),
                    'XDG_DATA_HOME': str(home / 'data'), 'XDG_CONFIG_HOME': str(home / 'config'),
                    'XDG_CACHE_HOME': str(home / 'cache'), 'XDG_STATE_HOME': str(home / 'state')})
    proc, messages, cost, steps, stop = None, [], Decimal(0), 0, ''
    cleanup_warning = ''
    try:
        proc = popen(command if container else [command, 'serve', '--port', str(port)], cwd=str(workdir), env=env,
                     shell=_WINDOWS and not container, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                     **hidden_subprocess_kwargs(windows=_WINDOWS, module=subprocess),
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
        text = (CONTAINER_NOTE + task) if container else task
        api.call('POST', f'/session/{session}/prompt_async', {
            'parts': [{'type': 'text', 'text': text}], 'model': {'providerID': 'relay', 'modelID': provider['model']}})
        started, idle, answered, grants, approvals = clock(), 0, [], set(), []
        approval_stop = ''
        while True:
            sleep(1)
            if stopped():
                stop = 'stopped by Lan'
            try:
                for request in ([] if stop else api.call('GET', '/permission') or []):
                    if request.get('sessionID') != session or request.get('id') in answered:
                        continue
                    answered.append(request.get('id'))
                    reply, why = decide(request.get('permission', ''), request.get('patterns', []),
                                        request.get('metadata'), workdir, autonomy=autonomy, ask=ask,
                                        grants=grants, approvals=approvals)
                    api.call('POST', f"/permission/{request['id']}/reply", {'reply': reply})
                    if why in {'user_denied', 'cancelled', 'timed_out'}:
                        approval_stop = why
                        stop = 'approval ' + why
                        break
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
            if container:
                subprocess.run(['docker', 'kill', name], capture_output=True, timeout=30,
                               **hidden_subprocess_kwargs(windows=_WINDOWS, module=subprocess))
        except Exception:
            cleanup_warning = f' [warning: could not confirm container {name} stopped; check Docker]'
        try:
            proxy.close()
        except Exception:
            pass
        try:
            record_cost(cost)
        except Exception:
            pass
    steps, calls, _repeat, text = summarize(messages)
    note = f'[workbench: {steps} steps, {calls} tool calls, about ¥{cost.quantize(Decimal("0.0001"))}, folder {workdir}]' + cleanup_warning
    approval_note = '\n'.join(
        f"[approval: operation={json.dumps(item['operation'], ensure_ascii=False)}, waiting={str(item['waiting']).lower()}, decision={item['decision']}]"
        for item in approvals)
    if approval_stop:
        return ExecutorResult(f'{approval_note}\n[workbench=stopped; reason={approval_stop}]\n{note}',
                              '被拒绝', cost, steps)
    if stop:
        partial = next(('\n'.join(p.get('text', '') for p in m.get('parts', []) if p.get('type') == 'text').strip()
                        for m in reversed(messages) if m.get('info', {}).get('role') == 'assistant' or
                        m.get('info', {}).get('type') == 'assistant'), '')
        status = (approval_note + '\n' if approval_note else '') + '[workbench=stopped]\n'
        return ExecutorResult(status + f'The workbench was stopped: {stop}. {diagnose(messages)}\nPartial report:\n{partial[:RESULT_CHARS]}\n{note}',
                              '出错', cost, steps)
    if not calls:
        why = diagnose(messages)
        status = (approval_note + '\n' if approval_note else '') + '[workbench=failed]\n'
        return ExecutorResult(status + f'The workbench ended without executing any tools. Partial report:\n{text[:RESULT_CHARS]}\n'
                              f'{why}\n{note}', '出错', cost, steps)
    if not text:
        why = diagnose(messages)
        status = (approval_note + '\n' if approval_note else '') + '[workbench=failed]\n'
        return ExecutorResult(status + 'The workbench finished without a report.' + (f' ({why})' if why else '') + '\n' + note,
                              '出错', cost, steps)
    if any(p.get('type') == 'tool' and p.get('state', {}).get('status') == 'error'
           for m in messages for p in m.get('parts', [])):
        text = '[workbench tool diagnostics: ' + diagnose(messages) + ']\n' + text
    result = text[:RESULT_CHARS] + '\n' + note
    if approval_note:
        result = approval_note + '\n[workbench=success]\n' + result
    return ExecutorResult(result, '成功', cost, steps)
