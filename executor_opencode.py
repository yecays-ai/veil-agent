"""Ticket 237 (prototype): a replaceable executor behind one tool, `workbench`.

Peter's own chat/autonomy model keeps the persona, memory, history and the final say. When a job needs a shell,
editing files, running programs or downloading things, it hands the job to the workbench: a short-lived
`opencode serve` process that works in one folder (data/<character>/workbench) with the same relay model.
VEIL decides every permission itself, enforces the step/time/repeat/cost limits from outside, writes the cost
into the shared ledger, and kills the whole process tree when the run ends. If OpenCode is missing or fails,
the tool just reports that; chat, memory and autonomous wakes are untouched.
"""
import base64
import hashlib
import http.client
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
import stat
import select
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request
from decimal import Decimal
from pathlib import Path
from typing import NamedTuple
from windows_process import hidden_subprocess_kwargs

TOOL_NAME = 'workbench'
MILLION = Decimal(1_000_000)
# The cost cap is the hard limit; the time and repeat limits only catch a stuck run (a free model never reaches the cost cap).
DEFAULT_LIMITS = {'max_seconds': 600, 'repeat_limit': 3, 'run_budget_cny': '0.5'}
PROXY_MAX_REQUESTS = 600  # runaway guard for the API key, not a task limit
RESULT_CHARS = 4000
_WINDOWS = os.name == 'nt'


CONTAINER_NOTE = (
    'Environment: you run in a Linux container with bash. The project is the current directory (/work). '
    'Any Windows path or Windows command (cmd, findstr, dir) in the request below refers to the owner\'s PC '
    'and does not exist here: use the current directory and Linux commands, and do not search for it elsewhere.\n\n')

MAINTENANCE_NOTE = (
    'This is an isolated maintenance worktree for VEIL itself. Read AGENTS.md, STATUS.md and HANDOFF.md first. '
    'Change only files under the current directory. '
    'Do not run git commit, push, checkout, reset, merge or any other command that rewrites repository history: '
    'VEIL commits, pushes and creates the pull request after your report. Run the project tests and report what '
    'actually failed or passed. Host Git metadata is hidden. Git in this container uses a disposable snapshot: '
    'status and diff reflect changes since this run started, while HEAD and history are synthetic. '
    'Use the verified host Git facts below for the real branch and commit.\n\n')

TASK_SCOPE_NOTE = (
    'Task scope: the current directory and its children are the only project files for this task. '
    'You may read, create and modify project files, and run ordinary local tests there. '
    'Network, installation, publishing, credentials, project-external paths and destructive operations are decided by VEIL. '
    'This scope ends when this task ends, is stopped, or reaches its budget or time limit.\n\n')



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
        'When isolated browsing is enabled for this role, it also has a public browser for research: it can open '
        'public HTTPS pages, follow links, scroll, fill a public search box and submit public GET searches in one '
        'run, without login, upload, download or private-network access. For several-step public research, hand it '
        'over once and ask for a report. It cannot see or operate Windows apps; do Windows software work yourself '
        'with your desktop tools. You decide what to do with the result.'),
    'parameters': {'type': 'object', 'properties': {
        'task': {'type': 'string', 'minLength': 1, 'maxLength': 4000,
                 'description': 'What to do and what to report back.'},
        'project': {'type': 'string', 'maxLength': 200,
                    'description': 'Optional project name when you know which existing project the job is about. '
                                   'The program finds it; nobody needs to give a path.'},
        'budget_cny': {'type': 'number', 'exclusiveMinimum': 0,
                       'description': 'Optional. Cost limit in yuan for this one job. Leave it out normally; the '
                                      'default is enough for most jobs. For a clearly bigger job, ask for more: Lan '
                                      'is asked to approve and the job does not run if she says no.'},
        'task_id': {'type': 'string', 'description': 'Optional VEIL maintenance task ID; works only in that task\'s '
                                                     'isolated worktree. The program checks it.'}},
        'required': ['task'], 'additionalProperties': False},
}}


def error_text(exc):
    """The real cause of `exc`: an ExceptionGroup (anyio TaskGroup) is opened down to its first leaf."""
    seen = 0
    while getattr(exc, 'exceptions', None) and seen < 10:
        exc, seen = exc.exceptions[0], seen + 1
    text = str(exc) or type(exc).__name__
    return text if text.startswith(type(exc).__name__) else f'{type(exc).__name__}: {text}'


class ExecutorResult(NamedTuple):
    text: str
    state: str
    cost: Decimal = Decimal(0)
    steps: int = 0
    reason: str = ''







def _command_name(token):
    name = posixpath.basename(token.replace('\\', '/')).casefold()
    return re.sub(r'\.(exe|cmd|bat|com)$', '', name)


def _is_test_script_name(value):
    name = posixpath.basename(str(value).replace('\\', '/')).casefold()
    return name.startswith('test_') and name.endswith('.py') or name.endswith('_test.py')


def _test_command(tokens):
    """True when this single command runs project tests; the test grant is asked once per task."""
    while tokens and re.match(r'^[A-Za-z_][A-Za-z0-9_]*=', tokens[0]):
        tokens = tokens[1:]
    if not tokens:
        return False
    command = _command_name(tokens[0])
    args = tokens[1:]
    if command in {'pytest', 'py.test'}:
        return True
    if command in {'python', 'python3'} or re.fullmatch(r'python\d+(?:\.\d+)*', command):
        if args[:2] in (['-m', 'unittest'], ['-m', 'pytest']):
            return True
        return bool(args and not args[0].startswith('-') and _is_test_script_name(args[0]))
    if command in {'node', 'nodejs'} and '--test' in args:
        return True
    if command == 'npm' and args and args[0].casefold() in {'test', 't'}:
        return True
    return False


class _PersistedApprovalSet:
    """Remember only opaque approval fingerprints for this maintenance task."""
    def __init__(self, kind, values, save):
        self.kind, self.save = kind, save
        self.values = set(value for value in values if isinstance(value, str) and
                          re.fullmatch(r'[0-9a-f]{64}', value))

    @staticmethod
    def fingerprint(key):
        return hashlib.sha256(json.dumps(key, ensure_ascii=False, sort_keys=True).encode('utf-8')).hexdigest()

    def __contains__(self, key):
        return self.fingerprint(key) in self.values

    def add(self, key):
        value = self.fingerprint(key)
        if value not in self.values:
            self.save(self.kind, value)
            self.values.add(value)




# What OpenCode asks about is decided by the permission config (`build_config`); VEIL only answers the requests
# it raises. Everything that is not listed there runs without a question.
INSTALLERS = {'pip', 'pip3', 'npm', 'pnpm', 'yarn', 'winget', 'choco', 'apt', 'apt-get', 'brew', 'cargo', 'go', 'bun'}
ASK_COMMANDS = ('git push', 'git clone', 'git fetch', 'git pull', 'git remote add', 'git config --global', 'git clean',
                'pip', 'pip3', 'python -m pip', 'python3 -m pip', 'py -m pip', 'curl', 'wget', 'iwr', 'irm',
                'Invoke-WebRequest', 'Invoke-RestMethod', 'npx', 'pnpx', 'bunx', 'sudo', 'runas', 'shutdown',
                'schtasks', 'net user', 'reg add', 'reg delete', 'rm -r', 'rm -rf', 'rm -fr', 'rmdir /s', 'del /s',
                'del /f', 'del /q', 'format',
                # installers ask only for the subcommands that fetch or change packages (npm test / go test run)
                *(f'{tool} {sub}' for tool in ('npm', 'pnpm', 'yarn', 'bun')
                  for sub in ('install', 'i', 'add', 'ci', 'update', 'upgrade', 'remove', 'uninstall', 'create')),
                *(f'{tool} {sub}' for tool in ('apt', 'apt-get') for sub in ('install', 'update', 'upgrade', 'remove')),
                *(f'{tool} {sub}' for tool in ('winget', 'choco', 'brew') for sub in ('install', 'upgrade', 'uninstall')),
                *(f'cargo {sub}' for sub in ('install', 'add', 'fetch', 'update')),
                *(f'go {sub}' for sub in ('get', 'install', 'mod download')))
MAINTENANCE_DENY = ('git commit', 'git push', 'git pull', 'git fetch', 'git merge', 'git rebase', 'git reset',
                    'git checkout', 'git switch', 'git tag', 'git worktree')
SECRET_READ_GLOBS = ('*.pem', '*.pfx', '*.key', '*.p12', '*id_rsa*', '*id_ed25519*', '*.env', '*.env.*',
                     '*Login Data*', '*Cookies*', '*/.ssh/*', '*\\.ssh\\*', '*/secrets/*', '*\\secrets\\*',
                     '*credentials*', '*.npmrc', '*.pypirc', '*.netrc', '*.git-credentials', '*.kdbx',
                     'secrets/*', '.ssh/*', '.aws/*', '.azure/*', '.gnupg/*', '.kube/*', '*/.aws/*', '*/.azure/*',
                     '*/.gnupg/*', '*/.kube/*', '*\\.aws\\*', '*\\.azure\\*', '*\\.gnupg\\*', '*\\.kube\\*',
                     '*/.docker/config.json')
KNOWN_ASKED = {'bash', 'edit', 'write', 'read', 'glob', 'grep', 'webfetch', 'external_directory'}


class TaskScope:
    """One workbench run's approvals; never reused by another task."""
    def __init__(self, workdir, *, approval_data=None, save_approval=None):
        self.workdir = Path(workdir)
        data = approval_data if isinstance(approval_data, dict) else {}
        self.grants = (_PersistedApprovalSet('approved', data.get('approved', []), save_approval)
                       if save_approval else set())
        self.denied = (_PersistedApprovalSet('denied', data.get('denied', []), save_approval)
                       if save_approval else set())

    @property
    def project_name(self):
        return self.workdir.name or '工作区'


def _approval_scope(permission, patterns, metadata):
    """A task-local approval covers the same request again, never a future workbench task."""
    if permission == 'webfetch':
        resources = tuple(sorted(re.findall(r'https?://([^/\s]+)', ' '.join(map(str, patterns)), re.I)))
        return permission, 'webfetch', resources or tuple(str(item) for item in patterns)
    if permission == 'bash':  # a command may carry a credential: only its fingerprint goes into keys and logs
        command = metadata.get('command') if isinstance(metadata, dict) else None
        digest = hashlib.sha256(json.dumps([list(map(str, patterns)), command], ensure_ascii=False).encode('utf-8')).hexdigest()
        return permission, 'command', digest
    return _approval_key(permission, patterns, metadata)



def _approval_key(permission, patterns, metadata):
    command = metadata.get('command') if isinstance(metadata, dict) else None
    return permission, tuple(str(item) for item in patterns), command if isinstance(command, str) else ''


def _ask_lan(prompt, operation, key, ask, grants, approvals, denied=None):
    if denied is not None and key in denied:
        if approvals is not None:
            approvals.append({'operation': operation, 'waiting': False, 'decision': 'user_denied_reused'})
        return 'reject', 'user_denied'
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
    if decision == 'user_denied' and denied is not None:
        denied.add(key)
    if approvals is not None:
        approvals.append({'operation': operation, 'waiting': True, 'decision': decision})
    return ('once', decision) if decision == 'approved' else ('reject', decision)


def _operation_summary(permission, patterns, metadata):
    command = metadata.get('command') if isinstance(metadata, dict) else None
    try:
        tokens = shlex.split(command, posix=True) if isinstance(command, str) else []
    except ValueError:
        tokens = []
    program = _command_name(tokens[0]) if tokens else ''
    return permission + (':' + program if program else '')




def _safe_display(value, limit=300):
    text = re.sub(r'(?i)(https?://)[^/@\s]+@', r'\1[redacted]@', str(value))
    text = re.sub(r'(?i)(--?(?:token|password|passwd|secret|api[-_]?key)\s*[= ]\s*)(\S+)',
                  r'\1[redacted]', text)
    return text[:limit]




PACKAGE_HOSTS = {
    'pip': {'pypi.org', 'files.pythonhosted.org', 'pypi.python.org'},
    'npm': {'registry.npmjs.org', 'registry.yarnpkg.com'},
    'apt': {'deb.debian.org', 'security.debian.org', 'archive.ubuntu.com', 'security.ubuntu.com'},
    'cargo': {'crates.io', 'static.crates.io', 'index.crates.io'},
    'go': {'proxy.golang.org', 'sum.golang.org'},
}


def _network_hosts(tokens, *, install=False):
    """Hosts an approved network/install command may reach through the relay. Only the command's own
    URLs and its package manager's registry hosts are opened; everything else stays blocked."""
    hosts = set()
    program = _command_name(tokens[0]) if tokens else ''
    if install:
        python_pip = (program in {'pip', 'pip3'} or re.fullmatch(r'pip\d+(?:\.\d+)*', program) or
                      program in {'python', 'python3'} and tokens[1:3] == ['-m', 'pip'] or
                      re.fullmatch(r'python\d+(?:\.\d+)*', program) and tokens[1:3] == ['-m', 'pip'])
        if python_pip:
            hosts |= PACKAGE_HOSTS['pip'] | {'github.com', 'codeload.github.com'}
        if program in {'npm', 'pnpm', 'yarn', 'bun'}:
            hosts |= PACKAGE_HOSTS['npm'] | {'github.com', 'codeload.github.com'}
        if program in {'apt', 'apt-get'}:
            hosts |= PACKAGE_HOSTS['apt']
        if program == 'cargo':
            hosts |= PACKAGE_HOSTS['cargo']
        if program == 'go':
            hosts |= PACKAGE_HOSTS['go']
    if program in {'npx', 'pnpx', 'bunx'}:
        hosts |= PACKAGE_HOSTS['npm']
    for token in tokens:
        if not isinstance(token, str):
            continue
        for match in re.findall(r'https?://[^\s"\'`<>]+', token, re.I):
            try:
                host = urllib.parse.urlsplit(match).hostname
            except ValueError:
                continue
            if host:
                hosts.add(host.casefold())
    return {host for host in hosts if host and len(host) <= 253}


def _allow_hosts(allow_network, tokens, *, install=False):
    if allow_network is None:
        return
    hosts = _network_hosts(tokens, install=install)
    if hosts:
        allow_network(hosts)


def _is_install_command(tokens):
    while tokens and re.match(r'^[A-Za-z_][A-Za-z0-9_]*=', tokens[0]):
        tokens = tokens[1:]
    if not tokens:
        return False
    program = _command_name(tokens[0])
    if program in INSTALLERS or re.fullmatch(r'pip\d*(?:\.\d+)*', program):
        return True
    return (program in {'python', 'python3'} or bool(re.fullmatch(r'python\d+(?:\.\d+)*', program))) \
        and tokens[1:3] == ['-m', 'pip']


def _approved_hosts(patterns, command_tokens):
    hosts = set()
    items = []
    for pattern in patterns if isinstance(patterns, list) else ():
        if isinstance(pattern, str):
            try:
                items.append(shlex.split(pattern, posix=True))
            except ValueError:
                continue
    if command_tokens:
        items.append(command_tokens)
    for tokens in items:
        if tokens:
            hosts |= _network_hosts(tokens, install=_is_install_command(tokens))
    return hosts


def _ephemeral_container_path(pattern):
    text = posixpath.normpath(str(pattern).replace('\\', '/'))
    return (text == '/tmp' or text.startswith('/tmp/') or
            text == '/var/tmp' or text.startswith('/var/tmp/'))




def decide(permission, patterns, metadata, workdir, *, autonomy, ask, approvals=None, task_scope=None,
           allow_network=None, isolated_tests=False):
    """VEIL's answer to an OpenCode permission request: (reply, reason). OpenCode only raises what the config
    marks "ask" (a push, an install, a download, a path outside the project, a secret file), so each request is
    one card for Lan. During autonomous activity nobody is there to ask, so it is refused."""
    if permission in {'task', 'skill', 'todowrite'}:
        return 'once', 'inside the workbench'
    if permission not in KNOWN_ASKED:
        # Tools of other MCP servers (VEIL's own tools included) are never approved here: VEIL runs those itself.
        return 'reject', 'not offered to the workbench'
    if permission == 'external_directory' and isolated_tests and patterns and all(
            _ephemeral_container_path(item) for item in patterns):
        return 'once', 'ephemeral container temp directory'  # /tmp in the disposable container holds no Lan data
    if autonomy:
        return 'reject', 'needs Lan\'s approval; not asked during autonomous activity'
    command = metadata.get('command') if isinstance(metadata, dict) else None
    shown = _safe_display(command if isinstance(command, str) else ', '.join(str(p) for p in patterns))
    project = task_scope.project_name if task_scope is not None else Path(workdir).name
    what = {'bash': '执行命令', 'webfetch': '访问外部地址', 'external_directory': '访问项目外的位置',
            'read': '读取敏感文件', 'edit': '修改文件', 'write': '写入文件'}.get(permission, '操作')
    reply, why = _ask_lan(f'Peter 的工作台想在项目“{project}”的本次任务中{what}：{shown}。同意吗？y/N ',
                          _operation_summary(permission, patterns, metadata),
                          _approval_scope(permission, patterns, metadata), ask,
                          task_scope.grants if task_scope is not None else None, approvals,
                          task_scope.denied if task_scope is not None else None)
    if reply == 'once' and allow_network is not None and permission in {'bash', 'webfetch'}:
        hosts = set()
        if permission == 'bash':
            hosts = _approved_hosts(patterns, shlex_tokens(command))
        else:
            for pattern in patterns:
                for match in re.findall(r'https?://[^\s"\'`<>]+', str(pattern), re.I):
                    try:
                        host = urllib.parse.urlsplit(match).hostname
                    except ValueError:
                        continue
                    if host:
                        hosts.add(host.casefold())
        if hosts:
            allow_network(hosts)
    return reply, why


def shlex_tokens(command):
    try:
        return shlex.split(command, posix=True) if isinstance(command, str) else []
    except ValueError:
        return []


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
    steps, repeat, last, calls, assistants = 0, 0, None, 0, []
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
                repeat = repeat + 1 if key == last else 1
                last = key
    final = ''
    if assistants and assistants[-1][0].get('finish') == 'stop':
        final = assistants[-1][1]
    return steps, calls, repeat, final


def _wrapped_test_command(tokens):
    while tokens and tokens[0] in {'timeout', 'time'}:
        tokens = tokens[1:]
        if tokens and not tokens[0].startswith('-') and re.fullmatch(r'\d+(?:\.\d+)?[smhd]?', tokens[0]):
            tokens = tokens[1:]
    if tokens and tokens[0] == 'env':
        tokens = tokens[1:]
        while tokens and (tokens[0].startswith('-') or re.match(r'^[A-Za-z_][A-Za-z0-9_]*=', tokens[0])):
            tokens = tokens[1:]
    return _test_command(tokens)


def _exit_from_output(state):
    values = [state.get('output'), (state.get('metadata') or {}).get('output'),
              (state.get('metadata') or {}).get('stdout')]
    for value in values:
        if not isinstance(value, str):
            continue
        matches = re.findall(r'(?:EXIT[_ ]?CODE|exit code)\s*[=: ]\s*(\d+)', value, re.I)
        if matches:
            return int(matches[-1])
    return None


def _last_test_exit(messages):
    """Use OpenCode's numeric shell exit for the last test command, including wrapped tests.

    A shell exit from a trailing non-test command (for example `pytest; printf ...`) is not the
    test's exit. If the command prints the real code, use it; otherwise fail closed instead of
    reporting a failed test as success."""
    result = None
    for message in messages:
        for part in message.get('parts', []):
            state = part.get('state') or {}
            if part.get('type') != 'tool' or part.get('tool') != 'bash' or state.get('status') != 'completed':
                continue
            command = (state.get('input') or {}).get('command')
            exit_code = (state.get('metadata') or {}).get('exit')
            if not isinstance(command, str) or type(exit_code) is not int:
                continue
            try:
                lexer = shlex.shlex(command, posix=True, punctuation_chars=';&|')
                lexer.whitespace_split = True
                tokens = list(lexer)
            except ValueError:
                continue
            segments, current = [], []
            for token in tokens:
                if re.fullmatch(r'[;&|]+', token):
                    if current:
                        segments.append(current)
                        current = []
                else:
                    current.append(token)
            if current:
                segments.append(current)
            test_indexes = [index for index, segment in enumerate(segments) if _wrapped_test_command(segment)]
            if not test_indexes:
                continue
            if max(test_indexes) == len(segments) - 1 or 'pipefail' in command.casefold():
                result = exit_code
                continue
            printed = _exit_from_output(state)
            result = printed if printed is not None else 1
    return result


RUN_LOG_KEEP = 30
RUN_LOG_MAX_BYTES = 2 * 1024 * 1024


def save_run_log(home, messages, *, stop='', steps=0, cost=Decimal(0)):
    """Keep the OpenCode session of one run (every step, tool input/output and error text) in
    `<home>/runs/`, because the session itself disappears with the container. Local diagnosis only;
    the newest RUN_LOG_KEEP runs are kept. Never raises."""
    try:
        folder = Path(home) / 'runs'
        folder.mkdir(parents=True, exist_ok=True)
        body = json.dumps({'time': time.strftime('%Y-%m-%dT%H:%M:%S%z'), 'stop': stop, 'steps': steps,
                           'cost_cny': str(cost), 'messages': messages}, ensure_ascii=False, default=str)
        if len(body.encode('utf-8')) > RUN_LOG_MAX_BYTES:
            body = json.dumps({'time': time.strftime('%Y-%m-%dT%H:%M:%S%z'), 'stop': stop, 'steps': steps,
                               'cost_cny': str(cost), 'truncated': True,
                               'diagnosis': diagnose(messages)}, ensure_ascii=False)
        name = time.strftime('%Y%m%d-%H%M%S') + '-' + secrets.token_hex(3) + '.json'
        (folder / name).write_text(body, encoding='utf-8')
        for old in sorted(folder.glob('*.json'))[:-RUN_LOG_KEEP]:
            old.unlink(missing_ok=True)
    except Exception:
        pass


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
                # VEIL's own bridged tools (veil_*) answer with program text, so their error is shown; shell and
                # file errors can echo file contents or arguments and stay reduced to their type.
                error_text = ''
                if str(tool).startswith('veil_') and state.get('status') == 'error':
                    raw = error.get('message', '') if isinstance(error, dict) else error
                    if not raw:
                        raw = state.get('output', '')
                    error_text = ('(' + ' '.join(str(raw or '').split())[:160] + ')') if raw else ''
                tool_lines.append(f"{tool} {state.get('status', '')} {shown} {error_type} {error_text}".strip())
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
    try:
        proc.wait(timeout=5)
    except (AttributeError, subprocess.TimeoutExpired):
        pass


def _relay(left, right, timeout=120):
    try:
        while True:
            readable, _, _ = select.select([left, right], [], [], timeout)
            if not readable:
                return
            for source in readable:
                data = source.recv(65536)
                if not data:
                    return
                (right if source is left else left).sendall(data)
    except OSError:
        return


class KeyProxy:
    """A loopback relay in front of the model API. OpenCode (and every command it runs) only ever holds a random
    local token; the real API key stays in this process and is added to forwarded requests. The token is only
    valid for this run, and the proxy refuses to forward more than `max_requests` calls.

    It is also the only egress a container workbench has: an explicit per-task host allowlist opens an
    HTTP/HTTPS tunnel for an approved install or network command. Tests and unknown commands never add hosts.
    """

    def __init__(self, upstream_base, key, *, max_requests=400, host='127.0.0.1', timeout=180,
                 allowed_ports=None):
        self.token = secrets.token_urlsafe(24)
        self.upstream = upstream_base.rstrip('/')
        self.allowed_ports = set(allowed_ports) if allowed_ports else {80, 443}
        self.requests = 0
        self.proxy_requests = 0
        # A large pip/npm install can open many connections; this is a runaway guard, not a
        # per-package quota. Model calls have their own max_requests limit.
        self.proxy_limit = 1000
        self.allowed_hosts = set()
        self.lock = threading.Lock()
        proxy = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = 'HTTP/1.0'

            def log_message(self, *_args):
                pass

            def _deny(self, code, text, headers=None):
                self.send_response(code)
                for name, value in (headers or {}).items():
                    self.send_header(name, value)
                self.send_header('Content-Length', str(len(text)))
                self.end_headers()
                self.wfile.write(text.encode())

            def _proxy_authenticated(self):
                value = (self.headers.get('Proxy-Authorization') or '').strip()
                if not value.casefold().startswith('basic '):
                    return False
                try:
                    decoded = base64.b64decode(value.split(None, 1)[1]).decode('utf-8', 'replace')
                except (ValueError, UnicodeError):
                    return False
                return secrets.compare_digest(decoded, 'veil:' + proxy.token)

            def _host_allowed(self, host):
                return bool(host and host.casefold().rstrip('.') in proxy.allowed_hosts)

            def _port_allowed(self, port):
                return port in proxy.allowed_ports

            def _take_proxy_slot(self):
                with proxy.lock:
                    if proxy.proxy_requests >= proxy.proxy_limit:
                        return False
                    proxy.proxy_requests += 1
                    return True

            def do_CONNECT(self):
                if not self._proxy_authenticated():
                    return self._deny(407, 'proxy authentication required',
                                      {'Proxy-Authenticate': 'Basic realm="veil"'})
                try:
                    parts = urllib.parse.urlsplit('//' + self.path)
                    host, port = parts.hostname, parts.port or 443
                except ValueError:
                    return self._deny(400, 'invalid tunnel target')
                if not self._host_allowed(host):
                    return self._deny(403, 'host was not approved for this task')
                if not self._port_allowed(port):
                    return self._deny(403, 'only HTTP/HTTPS ports are allowed')
                if not self._take_proxy_slot():
                    return self._deny(429, 'network approval limit reached')
                try:
                    upstream = socket.create_connection((host, port), timeout=20)
                except OSError as exc:
                    return self._deny(502, 'upstream error: ' + type(exc).__name__)
                self.send_response(200, 'Connection Established')
                self.end_headers()
                try:
                    _relay(self.connection, upstream)
                finally:
                    try:
                        upstream.close()
                    except OSError:
                        pass

            def do_POST(self):
                if self.path.startswith('/v1'):
                    return self._forward('POST')
                return self._forward_proxy('POST')

            def do_GET(self):
                if self.path == '/_veil_health/' + proxy.token:
                    return self._deny(200, 'ok')
                if self.path.startswith('/v1'):
                    return self._forward('GET')
                return self._forward_proxy('GET')

            def _forward(self, method):
                if self.headers.get('Authorization') != 'Bearer ' + proxy.token:
                    return self._deny(401, 'bad token')
                with proxy.lock:
                    if proxy.requests >= max_requests:
                        return self._deny(403, 'request limit reached')
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

            def _forward_proxy(self, method):
                if not self._proxy_authenticated():
                    return self._deny(407, 'proxy authentication required',
                                      {'Proxy-Authenticate': 'Basic realm="veil"'})
                try:
                    parts = urllib.parse.urlsplit(self.path)
                    port = parts.port
                except ValueError:
                    return self._deny(400, 'invalid proxy URL')
                if parts.scheme not in {'http', 'https'} or not parts.hostname:
                    return self._deny(400, 'proxy request must be an absolute http(s) URL')
                if not self._host_allowed(parts.hostname):
                    return self._deny(403, 'host was not approved for this task')
                port = port or (443 if parts.scheme == 'https' else 80)
                if not self._port_allowed(port):
                    return self._deny(403, 'only HTTP/HTTPS ports are allowed')
                if not self._take_proxy_slot():
                    return self._deny(429, 'network approval limit reached')
                body = self.rfile.read(int(self.headers.get('Content-Length') or 0)) if method == 'POST' else None
                target = parts.path + (('?' + parts.query) if parts.query else '')
                headers = {name: value for name, value in self.headers.items()
                           if name.casefold() not in {'proxy-authorization', 'proxy-connection', 'connection',
                                                      'content-length', 'transfer-encoding'}}
                connection = None
                try:
                    connection = (http.client.HTTPSConnection(parts.hostname, port, timeout=timeout)
                                  if parts.scheme == 'https' else
                                  http.client.HTTPConnection(parts.hostname, port, timeout=timeout))
                    connection.request(method, target, body=body, headers=headers)
                    response = connection.getresponse()
                    data = response.read()
                except (OSError, http.client.HTTPException) as exc:
                    return self._deny(502, 'upstream error: ' + type(exc).__name__)
                finally:
                    if connection is not None:
                        try:
                            connection.close()
                        except OSError:
                            pass
                self.send_response(response.status)
                for name, value in response.getheaders():
                    if name.casefold() not in {'transfer-encoding', 'connection', 'content-length'}:
                        self.send_header(name, value)
                self.send_header('Content-Length', str(len(data)))
                self.end_headers()
                self.wfile.write(data)

        self.server = ThreadingHTTPServer((host, 0), Handler)
        self.port = self.server.server_address[1]
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def allow_hosts(self, hosts):
        for host in hosts:
            value = str(host).strip().casefold().rstrip('.')
            if value and (re.fullmatch(r'[a-z0-9.-]+', value) or re.fullmatch(r'[0-9a-f:.]+', value)):
                self.allowed_hosts.add(value)


    def local_base(self, host='127.0.0.1'):
        return f'http://{host}:{self.port}/v1'

    def local_health(self, host='127.0.0.1'):
        return f'http://{host}:{self.port}/_veil_health/{self.token}'

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


class PermissionEvents:
    """Permission requests from OpenCode's event stream. `GET /permission` returns a schema error for the whole
    list when one pending request has a field it cannot serialise (seen in 1.19.5 with webfetch), which would
    leave every request unanswered; the stream carries each request on its own."""

    def __init__(self, base, password, opener=None):
        self.base, self.opener = base, opener or urllib.request.urlopen
        self.auth = 'Basic ' + base64.b64encode(('opencode:' + password).encode()).decode()
        self.requests, self.connected, self.closed, self.gap = [], False, False, False
        self.lock = threading.Lock()
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()

    def _run(self):
        while not self.closed:
            try:
                request = urllib.request.Request(self.base + '/event', headers={'Authorization': self.auth})
                with self.opener(request, timeout=30) as response:
                    self.connected = True
                    for raw in response:
                        if self.closed:
                            break
                        line = raw.decode('utf-8', 'replace').strip()
                        if line.startswith('data:'):
                            self._accept(line[5:].strip())
            except Exception:
                pass
            if self.connected:
                self.gap = True  # anything raised while disconnected is not replayed
            self.connected = False
            if not self.closed:
                time.sleep(1)

    def _accept(self, text):
        try:
            event = json.loads(text)
        except ValueError:
            return
        event = event.get('payload', event) if isinstance(event, dict) else {}
        if event.get('type') == 'permission.asked' and isinstance(event.get('properties'), dict):
            with self.lock:
                self.requests.append(event['properties'])

    def take(self):
        with self.lock:
            taken, self.requests = self.requests, []
        return taken

    def close(self):
        self.closed = True


def free_port():
    with socket.socket() as s:
        s.bind(('127.0.0.1', 0))
        return s.getsockname()[1]


def find_command(configured=''):
    """The configured path, else `opencode` on PATH; None when it is not installed."""
    if configured:
        return configured if Path(configured).exists() else None
    return shutil.which('opencode')


def build_config(provider, token, base_url, protect=(), browser_gateway=None, maintenance=False, isolated=True,
                 web_browser=False):
    """The OpenCode config, passed inline (OPENCODE_CONFIG_CONTENT) so nothing is written into the user's project
    and a project's own opencode.json cannot loosen it. `token` is the local proxy token, not the real key."""
    context = int(provider.get('max_input_tokens', 32768))
    edit_permission = ('allow' if not protect else
                       {'*': 'allow', **{Path(path).as_posix(): 'deny' for path in protect}})
    # Inside the container commands run; on the host (no container) an unlisted command is asked too.
    bash_rules = {'*': 'allow' if isolated else 'ask'}
    for command in ASK_COMMANDS:
        bash_rules.update({command: 'ask', command + ' *': 'ask'})
    if maintenance:  # VEIL commits, pushes and opens the pull request itself
        for command in MAINTENANCE_DENY:
            bash_rules.update({command: 'deny', command + ' *': 'deny'})
    model = {'name': provider['model'], 'tool_call': True,
             'limit': {'context': context, 'output': min(8192, context // 4)}}
    if provider.get('input_modalities'):
        model['modalities'] = {'input': list(provider['input_modalities']), 'output': ['text']}
    config = {
        '$schema': 'https://opencode.ai/config.json',
        'provider': {'relay': {
            'npm': '@ai-sdk/openai-compatible', 'name': 'Relay',
            'options': {'baseURL': base_url, 'apiKey': token},
            'models': {provider['model']: model}}},
        # OpenCode's own permission rules decide what is asked: a push, an install, a download, a path outside the
        # project, a secret file. Everything else runs. VEIL only answers the ones marked "ask" (the approval card).
        'permission': {'bash': bash_rules, 'edit': edit_permission, 'webfetch': 'ask', 'external_directory': 'ask',
                       'read': {'*': 'allow', **{g: 'ask' for g in SECRET_READ_GLOBS}}},
        # A refusal answers that one request; the task goes on with something else instead of ending.
        'experimental': {'continue_loop_on_deny': True},
        'share': 'disabled', 'autoupdate': False}
    if browser_gateway is not None:
        config['mcp'] = {}
    if browser_gateway is not None:
        config['mcp']['veil_browser'] = {
            'type': 'remote', 'url': browser_gateway.url, 'oauth': False,
            'headers': {'Authorization': 'Bearer ' + browser_gateway.token}}
    if web_browser:
        # Playwright MCP runs headless inside the container; its config (proxy -> the VEIL relay) is written
        # to /tmp/pw.json by the container entrypoint from VEIL_PW_CONFIG.
        config['permission']['webfetch'] = 'deny'  # one way onto the web: the headless browser, not an approval card
        config.setdefault('mcp', {})['playwright'] = {
            'type': 'local', 'command': ['playwright-mcp', '--config', '/tmp/pw.json']}
    return config


SECRET_FILE = re.compile(r'^(.*\.env(\..*)?|.*\.pem|id_(rsa|ed25519).*|.*\.pfx|.*\.key|cookies|login data)$', re.I)


def _link_like(path):
    """A symlink or Windows reparse point (including a junction)."""
    return bool(path.is_symlink() or
                getattr(path.lstat(), 'st_file_attributes', 0) &
                getattr(stat, 'FILE_ATTRIBUTE_REPARSE_POINT', 0x400))


def secret_directories(workdir):
    """Protect whole secret directories, including their otherwise ordinary-looking filenames."""
    found = []
    root = Path(workdir).resolve()
    def fail_walk(error):
        raise error
    for base, dirs, _names in os.walk(workdir, onerror=fail_walk):
        links = set()
        for name in dirs:
            if name.casefold() == '.git':
                continue
            path = Path(base) / name
            if _link_like(path):
                if name.casefold() in {'secrets', '.ssh'} or not path.resolve().is_relative_to(root):
                    raise ValueError('cannot safely mount a project containing external directory links')
                links.add(name)
                continue
            if name.casefold() in {'secrets', '.ssh'}:
                found.append(path)
        dirs[:] = [name for name in dirs if name not in links and name.casefold() not in {'.git', 'secrets', '.ssh'}]
    return found


def secret_files(workdir, max_depth=None):
    """Secret-looking files inside the project are masked with an empty file in the container. A link that
    points outside the project is refused: the container must never expose a host file through one."""
    found, root = [], Path(workdir)
    root_resolved = root.resolve()
    def fail_walk(error):
        raise error
    for base, dirs, names in os.walk(root, onerror=fail_walk):
        depth = len(Path(base).relative_to(root).parts)
        dirs[:] = [d for d in dirs if d.casefold() not in {'.git', 'secrets', '.ssh'}
                   and (max_depth is None or depth < max_depth)]
        for name in names:
            path = Path(base) / name
            if _link_like(path) and not path.resolve().is_relative_to(root_resolved):
                raise ValueError('cannot safely mount a project containing external file links')
            if SECRET_FILE.match(name):
                found.append(path)
    return found


_CONTAINER_WATCHDOG = '''import os, signal, subprocess, sys, time, urllib.request
child = subprocess.Popen(['sh', '-c', sys.argv[2], 'veil-workbench', sys.argv[1]], start_new_session=True)
failures = 0
try:
    while child.poll() is None:
        try:
            with urllib.request.urlopen(os.environ['VEIL_WATCHDOG_URL'], timeout=2) as response:
                if response.status != 200:
                    raise OSError('host heartbeat failed')
            failures = 0
        except Exception:
            failures += 1
        if failures >= 5:
            os.killpg(child.pid, signal.SIGKILL)
            break
        time.sleep(2)
    code = child.wait()
finally:
    if child.poll() is None:
        os.killpg(child.pid, signal.SIGKILL)
        child.wait()
sys.exit(code if code >= 0 else 1)
'''


def container_command(container, *, name, workdir, port, env, masks, bootstrap_git=False, watchdog=False,
                      network_lock=False):
    """`docker run` for OpenCode: only the project is mounted (read-write); secret-looking files in it are masked by
    empty read-only files; no host environment, bounded memory/processes. The server is published on the host's
    loopback only. A container run additionally gets an egress firewall: the only allowed destination is the
    local relay, so project tests cannot reach the network even though they run without an approval card."""
    cmd = ['docker', 'run', '--rm', '--name', name, '--cap-drop', 'ALL', '--security-opt', 'no-new-privileges',
           '--pids-limit', '256', '--memory', container.get('memory', '2g')]
    if network_lock:
        # The entrypoint needs these only long enough to install the rules, fix ownership and drop to node.
        cmd += ['--cap-add', 'NET_ADMIN', '--cap-add', 'SETUID', '--cap-add', 'SETGID', '--cap-add', 'CHOWN',
                '--user', 'root', '--add-host', 'veil-relay:host-gateway']
    cmd += ['--add-host', 'host.docker.internal:host-gateway',
            '-p', f'127.0.0.1:{port}:{port}', '-v', f'{workdir}:/work', '-w', '/work']
    for source, target in masks:
        cmd += ['-v', f'{source}:/work/{target}:ro']
    for key, value in env.items():
        cmd += ['-e', f'{key}={value}']
    serve = 'opencode serve --hostname 0.0.0.0 --port "$1"'
    prefix = ''
    if bootstrap_git:
        prefix = ('set -eu; mkdir -p /tmp/home; '
                  'export GIT_CONFIG_NOSYSTEM=1 GIT_CONFIG_GLOBAL=/tmp/home/.gitconfig; '
                  'git -C /tmp config --global --add safe.directory /work; '
                  'git --git-dir=/tmp/veil-git --work-tree=/work init -q -b veil-snapshot; '
                  'git --git-dir=/tmp/veil-git --work-tree=/work -c core.hooksPath=/dev/null add -A -- .; '
                  'git --git-dir=/tmp/veil-git --work-tree=/work -c core.hooksPath=/dev/null '
                  '-c user.name=VEIL -c user.email=veil-snapshot@example.invalid '
                  'commit --allow-empty -qm "Isolated starting snapshot"; ')
    if network_lock:
        script = (prefix +
                  'set -eu; '
                  'PROXY_IP=$(getent ahostsv4 veil-relay | awk "NR == 1 {print \\$1}"); '
                  'test -n "$PROXY_IP"; '
                  'iptables -A OUTPUT -d 127.0.0.11 -j REJECT; '
                  'iptables -A OUTPUT -o lo -j ACCEPT; '
                  'iptables -A OUTPUT -m conntrack --ctstate ESTABLISHED,RELATED -j ACCEPT; '
                  'iptables -A OUTPUT -d "$PROXY_IP" -p tcp --dport "$VEIL_PROXY_PORT" -j ACCEPT; '
                  'if test -n "${VEIL_BROWSER_PORT:-}"; then iptables -A OUTPUT -d "$PROXY_IP" -p tcp --dport "$VEIL_BROWSER_PORT" -j ACCEPT; fi; '
                  'iptables -A OUTPUT -j REJECT; '
                  'if command -v ip6tables >/dev/null 2>&1; then '
                  'if ip6tables -L OUTPUT -n >/dev/null 2>&1; then '
                  'ip6tables -A OUTPUT -o lo -j ACCEPT; '
                  'ip6tables -A OUTPUT -m conntrack --ctstate ESTABLISHED,RELATED -j ACCEPT; '
                  'ip6tables -A OUTPUT -j REJECT; fi; fi; '
                  'if test -n "${VEIL_PW_CONFIG:-}"; then printf %s "$VEIL_PW_CONFIG" > /tmp/pw.json; '
                  'chmod 644 /tmp/pw.json; fi; '
                  'mkdir -p /tmp/home; chown -R node:node /tmp/home /tmp/veil-git 2>/dev/null || true; '
                  'exec setpriv --reuid=node --regid=node --init-groups --inh-caps=-all --ambient-caps=-all '
                  '--bounding-set=-all ' + serve)
    else:
        script = prefix + 'exec ' + serve
    if watchdog:
        return cmd + [container['image'], 'python3', '-c', _CONTAINER_WATCHDOG, str(port), script]
    if network_lock or bootstrap_git:
        return cmd + [container['image'], 'sh', '-c', script, 'veil-workbench', str(port)]
    return cmd + [container['image'], 'opencode', 'serve', '--hostname', '0.0.0.0', '--port', str(port)]


def run(task, *, command, workdir, home, provider, key, limits, autonomy, ask, stopped, record_cost,
        api_factory=None, events_factory=None, proxy_factory=None, container=None, protect=(), popen=subprocess.Popen, clock=time.monotonic,
        sleep=time.sleep, diagnostic=None, maintenance=False, approval_data=None, save_approval=None,
        container_name=None, browser_gateway=None, web_hosts=None):
    """Run one task. `record_cost(cost)` is called once with the estimated cost, whatever the outcome."""
    limits = {**DEFAULT_LIMITS, **(limits or {})}
    workdir, home = Path(workdir), Path(home)
    if maintenance and not container:
        try:
            record_cost(Decimal(0))
        except Exception:
            pass
        return ExecutorResult('VEIL maintenance requires the isolated workbench container.',
                              '被拒绝', reason='isolation_required')
    if container_name is not None and not re.fullmatch(r'veil-workbench-[0-9a-f]{8,32}', container_name):
        try:
            record_cost(Decimal(0))
        except Exception:
            pass
        return ExecutorResult('Invalid workbench container identity.', '被拒绝', reason='invalid_container')
    if protect and not container:
        try:
            record_cost(Decimal(0))
        except Exception:
            pass
        return ExecutorResult('The workbench cannot protect the growth journal without Docker isolation.', '被拒绝')
    workdir.mkdir(parents=True, exist_ok=True)
    home.mkdir(parents=True, exist_ok=True)
    if stopped():
        try:
            record_cost(Decimal(0))
        except Exception:
            pass
        return ExecutorResult('[workbench=stopped; reason=user_stopped]', '出错', reason='user_stopped')
    try:
        protected_dirs = secret_directories(workdir) if container else []
        protected_files = secret_files(workdir) if container else []
    except (OSError, ValueError) as exc:
        try:
            record_cost(Decimal(0))
        except Exception:
            pass
        return ExecutorResult('The workbench cannot safely protect project secrets: ' + str(exc)[:200], '被拒绝')
    if container and popen is subprocess.Popen:
        # Production path: fail closed if the image predates the network lock. Tests that inject a
        # process factory inspect the command without starting Docker.
        try:
            probe = subprocess.run(
                ['docker', 'run', '--rm', container['image'], 'sh', '-c',
                 'command -v iptables >/dev/null 2>&1 && command -v setpriv >/dev/null 2>&1'],
                capture_output=True, timeout=60,
                **hidden_subprocess_kwargs(windows=_WINDOWS, module=subprocess))
            ready = probe.returncode == 0
        except (OSError, subprocess.TimeoutExpired):
            ready = False
        if not ready:
            try:
                record_cost(Decimal(0))
            except Exception:
                pass
            return ExecutorResult(
                'The workbench image lacks the network-isolation tools. Rebuild veil-workbench from '
                'docker/workbench.Dockerfile before running the container workbench.', '被拒绝',
                reason='isolation_required')
    task_scope = TaskScope(workdir, approval_data=approval_data, save_approval=save_approval)
    price = (Decimal(str(provider.get('input_cny_per_million', 0))),
             Decimal(str(provider.get('output_cny_per_million', 0))))
    cap = Decimal(str(limits['run_budget_cny']))
    proxy = (proxy_factory or KeyProxy)(provider['base_url'], key, max_requests=PROXY_MAX_REQUESTS,
                                        **({'host': '0.0.0.0'} if container else {}))
    def allow_network(hosts):
        proxy.allow_hosts(hosts)
    web_browser = bool(container and web_hosts)
    if web_browser:  # only these sites are reachable by the container's headless browser
        proxy.allow_hosts(web_hosts)
    proxy_host = 'veil-relay' if container else '127.0.0.1'
    gateway = browser_gateway(stopped, bool(container)) if browser_gateway is not None else None
    try:
        bridge = gateway.__enter__() if gateway is not None else None
        config = build_config(provider, proxy.token, proxy.local_base(proxy_host), protect=protect,
                              browser_gateway=bridge, maintenance=maintenance, isolated=bool(container),
                              web_browser=web_browser)
        port, password = free_port(), secrets.token_urlsafe(24)
        keep = {'PATH', 'PATHEXT', 'SYSTEMROOT', 'WINDIR', 'COMSPEC', 'TEMP', 'TMP', 'TMPDIR', 'LANG', 'LC_ALL', 'PROGRAMFILES',
                'PROGRAMFILES(X86)', 'PROGRAMDATA', 'APPDATA', 'LOCALAPPDATA', 'NUMBER_OF_PROCESSORS', 'PROCESSOR_ARCHITECTURE'}
        env = {k: v for k, v in os.environ.items() if k.upper() in keep}
        shared = {'OPENCODE_SERVER_PASSWORD': password, 'OPENCODE_DISABLE_AUTOUPDATE': '1',
                  'OPENCODE_CONFIG_CONTENT': json.dumps(config)}
        if container:
            proxy_url = f'http://veil:{proxy.token}@veil-relay:{proxy.port}'
            shared.update({
                'HTTP_PROXY': proxy_url, 'HTTPS_PROXY': proxy_url,
                'http_proxy': proxy_url, 'https_proxy': proxy_url, 'ALL_PROXY': proxy_url,
                'NO_PROXY': 'veil-relay,host.docker.internal,localhost,127.0.0.1,::1',
                'VEIL_PROXY_PORT': str(proxy.port),
                'VEIL_WATCHDOG_URL': proxy.local_health('veil-relay'),
            })
            if web_browser:
                shared['VEIL_PW_CONFIG'] = json.dumps({'browser': {'browserName': 'chromium', 'launchOptions': {
                    'headless': True, 'chromiumSandbox': False,
                    'proxy': {'server': f'http://veil-relay:{proxy.port}', 'username': 'veil',
                              'password': proxy.token}}}})
            if bridge is not None:
                shared['VEIL_BROWSER_PORT'] = str(bridge.server.server_address[1])
        git_pointer, empty_secret_dir = None, None
        if container:
            name = container_name or 'veil-workbench-' + secrets.token_hex(4)
            masks = []
            empty = home / 'empty-file'
            empty.write_text('', encoding='utf-8')
            for path in protected_files:
                masks.append((str(empty), path.relative_to(workdir).as_posix()))
            if protected_dirs:
                empty_secret_dir = home / ('empty-secret-dir-' + secrets.token_hex(8))
                empty_secret_dir.mkdir()
                for path in protected_dirs:
                    masks.append((str(empty_secret_dir), path.relative_to(workdir).as_posix()))
            if (workdir / '.git').is_file():
                # A linked worktree's .git file points at the main repository's metadata on the host.
                # Mount only a disposable in-container snapshot, never the host repository metadata.
                if maintenance:
                    git_pointer = home / ('git-pointer-' + secrets.token_hex(8))
                    git_pointer.write_text('gitdir: /tmp/veil-git\n', encoding='utf-8')
                    masks.append((str(git_pointer), '.git'))
                else:
                    masks.append((str(empty), '.git'))
            for relative in protect:  # e.g. Peter's growth journal: it has its own append-only grant
                if (workdir / relative).is_file() and all(target != Path(relative).as_posix() for _, target in masks):
                    masks.append((str(empty), Path(relative).as_posix()))
            command = container_command(container, name=name, workdir=str(workdir), port=port, masks=masks,
                                        env={**shared, 'HOME': '/tmp/home', 'XDG_DATA_HOME': '/tmp/home/data',
                                             'XDG_CONFIG_HOME': '/tmp/home/config', 'XDG_CACHE_HOME': '/tmp/home/cache',
                                             'XDG_STATE_HOME': '/tmp/home/state'}, bootstrap_git=bool(git_pointer),
                                        watchdog=maintenance, network_lock=True)
            env = {k: v for k, v in env.items() if k.upper() in ('PATH', 'PATHEXT', 'SYSTEMROOT', 'WINDIR', 'COMSPEC',
                                                                  'APPDATA', 'LOCALAPPDATA', 'USERPROFILE', 'HOME')}
        else:
            env.update({**shared, 'HOME': str(home), 'USERPROFILE': str(home),
                        'XDG_DATA_HOME': str(home / 'data'), 'XDG_CONFIG_HOME': str(home / 'config'),
                        'XDG_CACHE_HOME': str(home / 'cache'), 'XDG_STATE_HOME': str(home / 'state')})
    except BaseException:
        if gateway is not None:
            try:
                gateway.__exit__(None, None, None)
            except Exception:
                pass
        raise
    proc, messages, cost, steps, stop = None, [], Decimal(0), 0, ''
    events = None
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
                if stopped():
                    return ExecutorResult('[workbench=stopped; reason=user_stopped]', '出错',
                                          reason='user_stopped')
                if proc.poll() is not None or clock() - started > 60:
                    return ExecutorResult('The workbench could not start (OpenCode did not answer).', '出错')
                sleep(1)
        if stopped():
            return ExecutorResult('[workbench=stopped; reason=user_stopped]', '出错', reason='user_stopped')
        session = api.call('POST', '/session', {'title': 'workbench'})['id']
        list_failures, last_list_error = 0, ''
        # Listening starts before the prompt, so a request raised at once is not missed.
        events = (events_factory or (PermissionEvents if api_factory is None else (lambda *_: None)))(
            f'http://127.0.0.1:{port}', password)
        if stopped():
            return ExecutorResult('[workbench=stopped; reason=user_stopped]', '出错', reason='user_stopped')
        text = (CONTAINER_NOTE if container else '') + TASK_SCOPE_NOTE + (MAINTENANCE_NOTE if maintenance else '') + task
        api.call('POST', f'/session/{session}/prompt_async', {
            'parts': [{'type': 'text', 'text': text}], 'model': {'providerID': 'relay', 'modelID': provider['model']}})
        started, idle, answered, approvals = clock(), 0, [], []
        approval_stop = ''
        while True:
            sleep(1)
            if stopped():
                stop = 'stopped by Lan'
                if gateway is not None:
                    gateway.stop()
            try:
                listed = []
                if not stop:
                    try:
                        listed = api.call('GET', '/permission') or []
                        list_failures = 0
                    except Exception as exc:
                        list_failures += 1
                        last_list_error = error_text(exc)
                        if proc.poll() is not None:
                            raise
                        if list_failures >= 30 and not (events is not None and events.connected and not events.gap):
                            stop = 'cannot read OpenCode permission requests: ' + last_list_error[:200]
                    listed = list(listed) + (events.take() if events is not None else [])
                for request in ([] if stop else listed):
                    if request.get('sessionID') != session or request.get('id') in answered:
                        continue
                    answered.append(request.get('id'))
                    reply, why = decide(request.get('permission', ''), request.get('patterns', []),
                                        request.get('metadata'), workdir, autonomy=autonomy, ask=ask,
                                        approvals=approvals, task_scope=task_scope,
                                        allow_network=allow_network, isolated_tests=bool(container))
                    if stopped():
                        stop = 'stopped by Lan'
                        break
                    if diagnostic is not None:
                        try:
                            diagnostic({
                                'permission': request.get('permission', ''),
                                'patterns': request.get('patterns', []),
                                'command': _safe_display((request.get('metadata') or {}).get('command')),
                                'workdir': str(workdir),
                                'approval_scope': _approval_scope(
                                    request.get('permission', ''), request.get('patterns', []), request.get('metadata')),
                                'reply': reply, 'reason': why,
                            })
                        except Exception:
                            pass
                    api.call('POST', f"/permission/{request['id']}/reply", {'reply': reply})
                    if why in {'cancelled', 'timed_out'}:
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
                if clock() - started > int(limits['max_seconds']):
                    stop = f'reached the {int(limits["max_seconds"]) // 60}-minute limit'
                elif cost >= cap:
                    stop = (f'reached the cost limit (¥{cap}); if the job is worth more, run it again with a higher '
                            'budget_cny and Lan is asked to approve')
                elif repeat > int(limits['repeat_limit']):
                    stop = f'the same call was repeated more than {limits["repeat_limit"]} times'
            if stop:
                if gateway is not None:
                    gateway.stop()
                try:
                    api.call('POST', f'/session/{session}/abort')
                except Exception:
                    pass
                break
            try:
                status = api.call('GET', '/session/status') or {}
                busy = status.get(session, {}).get('type') not in (None, 'idle')
                try:
                    pending = any(r.get('sessionID') == session for r in (api.call('GET', '/permission') or []))
                except Exception:
                    pending = bool(events is not None and events.requests)
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
        stop = stop or return_text  # the run log in `finally` records why
        return ExecutorResult(return_text, '出错', cost, steps)
    finally:
        if events is not None:
            events.close()
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
        if gateway is not None:
            try:
                gateway.__exit__(None, None, None)
            except Exception:
                cleanup_warning += ' [warning: could not stop the public browser gateway]'
        if git_pointer is not None:
            try:
                git_pointer.unlink(missing_ok=True)
            except OSError:
                cleanup_warning += ' [warning: could not remove temporary Git pointer]'
        if empty_secret_dir is not None:
            try:
                empty_secret_dir.rmdir()
            except OSError:
                cleanup_warning += ' [warning: could not remove temporary secret mask]'
        save_run_log(home, messages, stop=stop, steps=steps, cost=cost)
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
                              '被拒绝', cost, steps, approval_stop)
    if stop:
        partial = next(('\n'.join(p.get('text', '') for p in m.get('parts', []) if p.get('type') == 'text').strip()
                        for m in reversed(messages) if m.get('info', {}).get('role') == 'assistant' or
                        m.get('info', {}).get('type') == 'assistant'), '')
        status = (approval_note + '\n' if approval_note else '') + '[workbench=stopped]\n'
        reason = ('user_stopped' if stop == 'stopped by Lan' else
                  'repeated_call' if stop.startswith('the same call was repeated') else 'limit_or_failure')
        return ExecutorResult(status + f'The workbench was stopped: {stop}. {diagnose(messages)}\nPartial report:\n{partial[:RESULT_CHARS]}\n{note}',
                              '出错', cost, steps, reason)
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
    if (test_exit := _last_test_exit(messages)) is not None and test_exit != 0:
        return ExecutorResult((approval_note + '\n' if approval_note else '') +
                              f'[workbench=failed; reason=test_failed; exit={test_exit}]\n' +
                              text[:RESULT_CHARS] + '\n' + note, '出错', cost, steps, 'test_failed')
    if any(p.get('type') == 'tool' and p.get('state', {}).get('status') == 'error'
           for m in messages for p in m.get('parts', [])):
        text = '[workbench tool diagnostics: ' + diagnose(messages) + ']\n' + text
    result = text[:RESULT_CHARS] + '\n' + note
    if approval_note:
        result = approval_note + '\n[workbench=success]\n' + result
    return ExecutorResult(result, '成功', cost, steps)
