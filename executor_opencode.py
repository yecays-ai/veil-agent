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
DEFAULT_LIMITS = {'max_steps': 30, 'max_seconds': 600, 'repeat_limit': 3, 'run_budget_cny': '0.5'}
RESULT_CHARS = 4000
_WINDOWS = os.name == 'nt'

SECRET_GLOBS = ('*.env', '*.env.*', 'secrets/*', '*/secrets/*', '*\\secrets\\*', '.ssh/*', '*/.ssh/*', '*\\.ssh\\*',
                '*id_rsa*', '*id_ed25519*', '*Login Data*', 'Cookies', '*/Cookies', '*\\Cookies')

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
        'You decide what to do with the result.'),
    'parameters': {'type': 'object', 'properties': {
        'task': {'type': 'string', 'minLength': 1, 'maxLength': 4000,
                 'description': 'What to do and what to report back.'},
        'project': {'type': 'string', 'maxLength': 200,
                    'description': 'Optional project name when you know which existing project the job is about. '
                                   'The program finds it; nobody needs to give a path.'},
        'task_id': {'type': 'string', 'description': 'Optional VEIL maintenance task ID; works only in that task\'s '
                                                     'isolated worktree. The program checks it.'}},
        'required': ['task'], 'additionalProperties': False},
}}


class ExecutorResult(NamedTuple):
    text: str
    state: str
    cost: Decimal = Decimal(0)
    steps: int = 0
    reason: str = ''


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


def _test_command_in_scope(tokens, workdir, current=None):
    """Reject test runners that would load code or read files outside the worktree."""
    while tokens and re.match(r'^[A-Za-z_][A-Za-z0-9_]*=', tokens[0]):
        tokens = tokens[1:]
    if not tokens:
        return False
    command = _command_name(tokens[0])
    args = tokens[1:]
    if command in {'node', 'nodejs'}:
        for arg in args:
            if arg == '--test':
                continue
            if arg.startswith('-'):
                return False  # --eval, --require, --loader and other code-loading flags
            if _resolved_work_path(arg, workdir, current) is None:
                return False
        return True
    if command == 'npm':
        return bool(args and args[0].casefold() in {'test', 't'})
    if command in {'python', 'python3'} or re.fullmatch(r'python\d+(?:\.\d+)*', command):
        if args[:2] in (['-m', 'unittest'], ['-m', 'pytest']):
            for arg in args[2:]:
                if not arg.startswith('-') and ('/' in arg or '\\' in arg or arg in {'.', '..'}):
                    if _resolved_work_path(arg, workdir, current) is None:
                        return False
            return True
        if not args or args[0].startswith('-') or not _is_test_script_name(args[0]):
            return False
        return _resolved_work_path(args[0], workdir, current) is not None
    return not any(_outside_workdir_argument(arg, workdir, current) for arg in args if not arg.startswith('-'))


def _git_write_request(tokens):
    while tokens and re.match(r'^[A-Za-z_][A-Za-z0-9_]*=', tokens[0]):
        tokens = tokens[1:]
    if not tokens or _command_name(tokens[0]) != 'git':
        return False
    if any(re.match(r'^(?:--config-env=)?alias\.', token, re.I) or token.casefold().startswith('alias.')
           for token in tokens[1:]):
        return True
    subcommand = _git_subcommand(tokens)
    if subcommand in {'commit', 'push', 'pull', 'fetch', 'merge', 'rebase', 'cherry-pick', 'revert', 'reset',
                      'checkout', 'switch', 'clean', 'stash', 'tag', 'remote', 'config', 'submodule',
                      'worktree', 'gc', 'filter-branch', 'update-ref'}:
        return True
    return subcommand == 'branch' and any(arg in {'-d', '-D', '-m', '-M', '--delete', '--move'}
                                           for arg in tokens[1:])


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


def _complex_bash_request(patterns, metadata, workdir=None, test_scripts=None, task_scope=None):
    # OpenCode supplies parsed command fragments in patterns and the original shell input in metadata.command.
    command = metadata.get('command') if isinstance(metadata, dict) else None
    if not isinstance(command, str) or not command.strip() or not isinstance(patterns, list) or not patterns:
        return True
    # Only discard a standalone stderr-to-null redirection; all file writes still need approval.
    def strip_stderr(value):
        return re.sub(r'(?<!\S)(?:2>/dev/null|2>&1)(?=\s|$|[;&|])', '', value)
    try:
        lexer = shlex.shlex(strip_stderr(command), posix=True, punctuation_chars=';&|<>()')
        lexer.whitespace_split = True
        shell_tokens = list(lexer)
        commands = [shlex.split(strip_stderr(pattern), posix=True) for pattern in patterns if isinstance(pattern, str)]
    except ValueError:
        return True
    if len(commands) != len(patterns) or not commands or any(not item for item in commands):
        return True
    if '\n' in command or '\r' in command or '`' in command or '$(' in command:
        return True
    if any('<' in token or '>' in token for token in shell_tokens):
        return True
    operators = [token for token in shell_tokens if re.fullmatch(r'[;&|]+', token)]
    if operators and len(commands) != len(operators) + 1:
        return True
    if len(commands) > 1:
        current = workdir
        for item in commands:
            if _command_name(item[0]) == 'cd':
                if len(item) != 2:
                    return True
                current = _resolved_work_path(item[1], workdir, current)
                if current is None:
                    return True
            elif not _safe_project_command(item, workdir, test_scripts, current, task_scope):
                return True
        return False
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
    if command_name in SAFE_INSPECTION or command_name in {'git', 'python', 'python3', 'node', 'npm', 'mkdir',
                                                           'touch', 'sort', 'pytest', 'py.test'} or re.fullmatch(
            r'python\d+(?:\.\d+)*', command_name):
        return not _safe_project_command(command_tokens, workdir, test_scripts, task_scope=task_scope)
    if command_name in {'python', 'python3', 'node', 'nodejs', 'deno', 'bun', 'ruby', 'perl', 'awk',
                        'gawk', 'mawk', 'php', 'osascript'} or re.fullmatch(r'python\d+(?:\.\d+)*', command_name):
        return True
    # Default deny: only commands with an explicit, local, read-only/development rule above are safe.
    # An unknown command (ssh, nc, mv, rm, a new tool) must be decided by Lan, never auto-allowed.
    return True


SAFE_INSPECTION = {'ls', 'dir', 'pwd', 'cat', 'type', 'head', 'tail', 'wc', 'stat', 'file', 'find', 'grep', 'rg',
                   'echo', 'printf', 'which'}


def _safe_project_command(tokens, workdir=None, test_scripts=None, current=None, task_scope=None):
    """Normal project work allowed inside this task's Docker-mounted project scope."""
    while tokens and re.match(r'^[A-Za-z_][A-Za-z0-9_]*=', tokens[0]):
        tokens = tokens[1:]
    if not tokens:
        return False
    command = _command_name(tokens[0])
    if command == 'git':
        if _git_write_request(tokens):
            return False
        if any(arg == '--output' or arg.startswith('--output=') or arg == '--ext-diff' for arg in tokens[1:]):
            return False
        return (_git_subcommand(tokens) in {'status', 'branch', 'diff', 'log', 'show', 'rev-parse', 'ls-files'} and
                not any(_outside_workdir_argument(arg, workdir, current) for arg in tokens[1:] if not arg.startswith('-')))
    if command in SAFE_INSPECTION:
        if command == 'find' and any(arg.casefold() in {'-delete', '-exec', '-execdir', '-ok', '-okdir',
                                                         '-fprint', '-fprint0', '-fprintf', '-fls'} for arg in tokens[1:]):
            return False
        return not any(_outside_workdir_argument(arg, workdir, current) for arg in tokens[1:] if not arg.startswith('-'))
    if command == 'sort':
        safe_flags = {'-', '--', '-b', '-d', '-f', '-g', '-h', '-i', '-M', '-n', '-r', '-s', '-u', '-V', '-z',
                      '--ignore-leading-blanks', '--dictionary-order', '--ignore-case', '--numeric-sort',
                      '--reverse', '--stable', '--unique', '--version-sort', '--zero-terminated'}
        return all(arg in safe_flags if arg.startswith('-') else not _outside_workdir_argument(arg, workdir, current)
                   for arg in tokens[1:])
    if command == 'mkdir':
        return (all(arg in {'-p', '--parents'} or not arg.startswith('-') for arg in tokens[1:]) and
                any(not arg.startswith('-') for arg in tokens[1:]) and
                not any(_outside_workdir_argument(arg, workdir, current) for arg in tokens[1:] if not arg.startswith('-')))
    if command == 'touch':
        return (len(tokens) > 1 and
                not any(_outside_workdir_argument(arg, workdir, current) for arg in tokens[1:] if not arg.startswith('-')))
    if command in {'pytest', 'py.test'}:
        return not any(_outside_workdir_argument(arg, workdir, current) for arg in tokens[1:] if not arg.startswith('-'))
    if command == 'node':
        if len(tokens) < 2 or tokens[1] != '--test' or '--eval' in tokens or '-e' in tokens:
            return False
        return not any(_outside_workdir_argument(arg, workdir, current)
                       for arg in tokens[2:] if not arg.startswith('-'))
    if command == 'npm':
        return len(tokens) >= 2 and tokens[1] in {'test', 't'}
    if command in {'python', 'python3'} or re.fullmatch(r'python\d+(?:\.\d+)*', command):
        if command and tokens[1:3] in (['-m', 'unittest'], ['-m', 'pytest']):
            return True
        if len(tokens) < 2 or tokens[1].startswith('-'):
            return False
        path = _resolved_work_path(tokens[1], workdir, current)
        if task_scope is not None:
            return task_scope.permits_test(path)
        return bool(path and test_scripts is not None and path in test_scripts and _is_test_script_name(tokens[1]))
    return False


def _resolved_work_path(value, workdir, current=None):
    if workdir is None or not isinstance(value, str):
        return None
    text = value.replace('\\', '/')
    if any(char in text for char in '$~*?[]{}'):
        return None
    work = posixpath.normpath(str(workdir).replace('\\', '/')).casefold()
    if text == '/work' or text.startswith('/work/'):
        container = posixpath.normpath(text)
        if container != '/work' and not container.startswith('/work/'):
            return None
        target = posixpath.normpath(work + container[5:]).casefold()
    elif text.startswith('/') or re.match(r'^[A-Za-z]:', text):
        return None
    else:
        base = posixpath.normpath(str(current or workdir).replace('\\', '/')).casefold()
        target = posixpath.normpath(base + '/' + text).casefold()
    return target if target == work or target.startswith(work.rstrip('/') + '/') else None


def _outside_workdir_argument(value, workdir, current=None):
    if value in {'.', '..'} or '/' in value or '\\' in value or re.match(r'^[A-Za-z]:', value):
        return _resolved_work_path(value, workdir, current) is None
    return False


def existing_test_scripts(workdir):
    found = set()
    for base, dirs, files in os.walk(workdir):
        dirs[:] = [item for item in dirs if item not in {'.git', 'node_modules', '.venv', 'venv', '__pycache__'}]
        for name in files:
            lower = name.casefold()
            path = Path(base, name)
            if not path.is_symlink() and (lower.startswith('test_') and lower.endswith('.py') or lower.endswith('_test.py')):
                found.add(posixpath.normpath(str(path).replace('\\', '/')).casefold())
    return found


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


class TaskScope:
    """One workbench run's project boundary and approvals; never reused by another task."""
    def __init__(self, workdir, *, approval_data=None, save_approval=None):
        self.workdir = Path(workdir)
        self.initial_tests = existing_test_scripts(workdir)
        self.written = set()
        data = approval_data if isinstance(approval_data, dict) else {}
        self.grants = (_PersistedApprovalSet('approved', data.get('approved', []), save_approval)
                       if save_approval else set())
        self.denied = (_PersistedApprovalSet('denied', data.get('denied', []), save_approval)
                       if save_approval else set())
        self.tests_granted = False

    @property
    def project_name(self):
        return self.workdir.name or '工作区'

    def record_write(self, patterns):
        for pattern in patterns if isinstance(patterns, list) else ():
            path = _resolved_work_path(pattern, self.workdir)
            if path:
                self.written.add(path)

    def permits_test(self, path):
        return bool(path and _is_test_script_name(path) and
                    (self.tests_granted or path in self.initial_tests or path in self.written))


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


def _approval_scope(permission, patterns, metadata):
    """A task-local approval covers the same risk class, never a future workbench task."""
    command = metadata.get('command') if isinstance(metadata, dict) else None
    try:
        tokens = shlex.split(command, posix=True) if isinstance(command, str) else []
    except ValueError:
        tokens = []
    while tokens and re.match(r'^[A-Za-z_][A-Za-z0-9_]*=', tokens[0]):
        tokens = tokens[1:]
    program = _command_name(tokens[0]) if tokens else ''
    install = (program in ASK_LAN['installers'] or re.fullmatch(r'pip\d*(?:\.\d+)*', program) or
               program in {'python', 'python3'} and tokens[1:3] == ['-m', 'pip'] or
               re.fullmatch(r'python\d+(?:\.\d+)*', program) and tokens[1:3] == ['-m', 'pip'])
    if permission == 'bash' and install:
        # Scope must include requirement files and options without exposing credentials in diagnostics.
        digest = hashlib.sha256(json.dumps(tokens, ensure_ascii=False).encode('utf-8')).hexdigest()
        return permission, 'install', (program, digest)
    if permission == 'bash' and _test_command(tokens):
        return permission, 'tests', 'project'
    if permission == 'bash' and program in {'npx', 'pnpx', 'bunx'}:
        return permission, 'network', (program, tuple(tokens[1:3]))
    if permission == 'bash' and (program in ASK_LAN['network'] or program == 'git' and _git_subcommand(tokens) in {'clone', 'fetch', 'pull'}):
        digest = hashlib.sha256(json.dumps(tokens, ensure_ascii=False).encode('utf-8')).hexdigest()
        return permission, 'network', (program, digest)
    if permission == 'bash' and program == 'git' and _git_subcommand(tokens) == 'push':
        refspec = tuple(arg for arg in tokens[1:] if not arg.startswith('-'))
        force = ('force',) if any(arg in {'-f', '--force', '--force-with-lease', '--force-if-includes'}
                                   for arg in tokens[1:]) else ()
        return permission, 'publish', refspec + force
    if permission == 'webfetch':
        resources = tuple(sorted(re.findall(r'https?://([^/\s]+)', ' '.join(map(str, patterns)), re.I)))
        return permission, 'webfetch', resources or tuple(str(item) for item in patterns)
    if permission == 'external_directory':
        return permission, tuple(str(item) for item in patterns)
    return _approval_key(permission, patterns, metadata)


def _safe_display(value):
    text = re.sub(r'(?i)(https?://)[^/@\s]+@', r'\1[redacted]@', str(value))
    text = re.sub(r'(?i)(--?(?:token|password|passwd|secret|api[-_]?key)\s*[= ]\s*)(\S+)',
                  r'\1[redacted]', text)
    return text[:300]


def _resolved_targets(permission, patterns, workdir):
    """Resolved project paths are diagnostics only; raw commands never go into chat results."""
    if permission not in {'edit', 'write', 'bash'}:
        return []
    values = patterns if permission in {'edit', 'write'} else []
    if permission == 'bash':
        for pattern in patterns if isinstance(patterns, list) else ():
            try:
                values += [arg for arg in shlex.split(pattern, posix=True)[1:]
                           if '/' in arg or '\\' in arg or arg in {'.', '..'}]
            except ValueError:
                continue
    return sorted({path for value in values if (path := _resolved_work_path(value, workdir))})


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
    if program in ASK_LAN['installers'] or re.fullmatch(r'pip\d*(?:\.\d+)*', program):
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


def decide(permission, patterns, metadata, workdir, *, autonomy, ask, grants=None, approvals=None,
           test_scripts=None, task_scope=None, maintenance=False, allow_network=None, isolated_tests=False):
    """VEIL's own answer to an OpenCode permission request: (reply, reason). `ask` is Lan's approval card."""
    if task_scope is not None:
        grants = task_scope.grants
    text = ' '.join(str(p) for p in patterns) + ' ' + json.dumps(metadata or {}, ensure_ascii=False)
    if DANGEROUS.search(text):
        return 'reject', 'dangerous command'
    if permission == 'external_directory':
        if autonomy:
            return 'reject', 'outside the workbench; Lan is not asked during autonomous activity'
        shown = _safe_display(', '.join(str(p) for p in patterns))
        key = _approval_scope(permission, patterns, metadata)
        return _ask_lan(f'Peter 的工作台想在本次任务中访问项目外的位置：{shown}。同意这项范围吗？y/N ',
                        _operation_summary(permission, patterns, metadata), key, ask, grants, approvals,
                        task_scope.denied if task_scope is not None else None)
    work = posixpath.normpath(str(workdir).replace('\\', '/')).lower()
    for pattern in patterns:
        p = str(pattern).replace('\\', '/')
        if permission in ('edit', 'write'):
            absolute = p.startswith('/') or re.match(r'^[A-Za-z]:', p)
            target = posixpath.normpath(p if absolute else work + '/' + p).lower()
            if target != work and not target.startswith(work.rstrip('/') + '/'):
                return 'reject', 'write outside the workbench'
        if permission in ('edit', 'write') and task_scope is not None:
            task_scope.record_write(patterns)
    if permission == 'bash':
        needs_lan = False
        complex_request = False
        git_write = False
        command_tokens = []
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
            git_write = git_write or _git_write_request(tokens)
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
                    git_write = git_write or _git_write_request(command_tokens)
                    needs_lan = needs_lan or _bash_command_needs_lan(command_tokens)
                else:
                    complex_request = True
        if maintenance and (git_write or re.search(
                r'\bgit\s+(?:commit|push|pull|fetch|merge|rebase|reset|checkout|switch|tag|worktree)\b', text, re.I)
                or re.search(r'\b(?:bash|sh|dash|zsh|cmd|powershell|pwsh)\b[^\n]{0,120}\bgit\s+'
                             r'(?:commit|push|pull|fetch|merge|rebase|reset|checkout|switch|tag|worktree)\b', text, re.I)):
            return 'reject', 'VEIL commits, pushes and creates the pull request; the workbench must not run git write commands'
        test_items = []
        for pattern in patterns if isinstance(patterns, list) else ():
            if isinstance(pattern, str):
                try:
                    tokens = shlex.split(pattern, posix=True)
                except ValueError:
                    tokens = []
                if tokens and _test_command(tokens):
                    test_items.append(tokens)
        if task_scope is not None and test_items:
            for tokens in test_items:
                if not _test_command_in_scope(tokens, workdir):
                    return 'reject', 'test path or flag outside the workbench'
            if isolated_tests:
                # The workbench container has the model relay as its only egress and runs as an
                # unprivileged user, so tests are allowed to run without a card. The network lock is
                # installed before OpenCode starts; this branch does not grant any network host.
                task_scope.tests_granted = True
                if approvals is not None:
                    approvals.append({'operation': 'bash:tests', 'waiting': False,
                                      'decision': 'auto_approved_isolated'})
            else:
                project = task_scope.project_name
                key = ('bash', 'tests', project)
                reply, why = _ask_lan(
                    f'项目“{project}”的本次任务要运行项目测试。测试会执行项目代码，可能联网或读写文件；'
                    '批准后本次任务的测试命令继续使用这个授权。同意吗？y/N ',
                    'bash:tests', key, ask, grants, approvals, task_scope.denied)
                if reply != 'once':
                    return reply, why
                task_scope.tests_granted = True
        complex_request = complex_request or _complex_bash_request(
            patterns, metadata, workdir, test_scripts, task_scope)
        if needs_lan or complex_request:
            if autonomy:
                return 'reject', 'needs Lan\'s approval; not asked during autonomous activity'
            command = metadata.get('command') if isinstance(metadata, dict) else None
            shown = _safe_display(command if isinstance(command, str) else patterns)
            key = _approval_scope(permission, patterns, metadata)
            project = task_scope.project_name if task_scope is not None else Path(workdir).name
            if needs_lan:
                prompt = (f'Peter 的工作台想在项目“{project}”的本次任务中执行：{shown}。'
                          '这会联网、安装、发布或批量删除；批准后只在此任务的相同风险范围内继续。同意吗？y/N ')
            else:
                prompt = f'Peter 的工作台想执行一条复合或无法确认安全的项目命令：{shown}。同意这一次吗？y/N '
            reply, why = _ask_lan(prompt, _operation_summary(permission, patterns, metadata),
                                  key, ask, grants, approvals,
                                  task_scope.denied if task_scope is not None else None)
            if reply == 'once' and allow_network is not None:
                allow_network(_approved_hosts(patterns, command_tokens))
            return reply, why
    if permission == 'webfetch':
        if autonomy:
            return 'reject', 'needs Lan\'s approval; not asked during autonomous activity'
        shown = _safe_display(', '.join(str(p) for p in patterns))
        key = _approval_scope(permission, patterns, metadata)
        project = task_scope.project_name if task_scope is not None else Path(workdir).name
        reply, why = _ask_lan(f'Peter 的工作台想为项目“{project}”访问外部文档：{shown}。同意这项范围吗？y/N ',
                              _operation_summary(permission, patterns, metadata), key, ask, grants, approvals,
                              task_scope.denied if task_scope is not None else None)
        if reply == 'once' and allow_network is not None:
            hosts = set()
            for pattern in patterns if isinstance(patterns, list) else ():
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
    if permission in ('bash', 'edit', 'write', 'task', 'skill', 'todowrite', 'read', 'glob', 'grep'):
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


def _last_test_exit(messages):
    """Use OpenCode's numeric shell exit for a final test command, not model prose."""
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
            last = []
            for token in tokens:
                if re.fullmatch(r'[;&|]+', token):
                    last = []
                else:
                    last.append(token)
            if _test_command(last):
                result = exit_code
    return result


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

    def __init__(self, upstream_base, key, *, max_requests=400, host='127.0.0.1', timeout=180):
        self.token = secrets.token_urlsafe(24)
        self.upstream = upstream_base.rstrip('/')
        self.requests = 0
        self.proxy_requests = 0
        self.proxy_limit = 100
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
                if not self._take_proxy_slot():
                    return self._deny(429, 'network approval limit reached')
                port = port or (443 if parts.scheme == 'https' else 80)
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
        # Every shell request reaches VEIL, which has the current task's project scope and approval state.
        # Reading secrets is denied outright by OpenCode before it can expose them to the model.
        'permission': {'bash': {'*': 'ask'},
                       'edit': edit_permission, 'webfetch': 'ask', 'external_directory': 'ask',
                       'read': {'*': 'allow', **{g: 'deny' for g in SECRET_GLOBS}}},
        'share': 'disabled', 'autoupdate': False}


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
                  'iptables -A OUTPUT -j REJECT; '
                  'if command -v ip6tables >/dev/null 2>&1; then '
                  'if ip6tables -L OUTPUT -n >/dev/null 2>&1; then '
                  'ip6tables -A OUTPUT -o lo -j ACCEPT; '
                  'ip6tables -A OUTPUT -m conntrack --ctstate ESTABLISHED,RELATED -j ACCEPT; '
                  'ip6tables -A OUTPUT -j REJECT; fi; fi; '
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
        api_factory=None, proxy_factory=None, container=None, protect=(), popen=subprocess.Popen, clock=time.monotonic,
        sleep=time.sleep, diagnostic=None, maintenance=False, approval_data=None, save_approval=None,
        container_name=None):
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
    proxy = (proxy_factory or KeyProxy)(provider['base_url'], key, max_requests=int(limits['max_steps']) * 3 + 5,
                                        **({'host': '0.0.0.0'} if container else {}))
    def allow_network(hosts):
        proxy.allow_hosts(hosts)
    proxy_host = 'veil-relay' if container else '127.0.0.1'
    config = build_config(provider, proxy.token, proxy.local_base(proxy_host), protect=protect)
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
            'NO_PROXY': 'veil-relay,localhost,127.0.0.1,::1',
            'VEIL_PROXY_PORT': str(proxy.port),
            'VEIL_WATCHDOG_URL': proxy.local_health('veil-relay'),
        })
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
                if stopped():
                    return ExecutorResult('[workbench=stopped; reason=user_stopped]', '出错',
                                          reason='user_stopped')
                if proc.poll() is not None or clock() - started > 60:
                    return ExecutorResult('The workbench could not start (OpenCode did not answer).', '出错')
                sleep(1)
        if stopped():
            return ExecutorResult('[workbench=stopped; reason=user_stopped]', '出错', reason='user_stopped')
        session = api.call('POST', '/session', {'title': 'workbench'})['id']
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
            try:
                for request in ([] if stop else api.call('GET', '/permission') or []):
                    if request.get('sessionID') != session or request.get('id') in answered:
                        continue
                    answered.append(request.get('id'))
                    reply, why = decide(request.get('permission', ''), request.get('patterns', []),
                                        request.get('metadata'), workdir, autonomy=autonomy, ask=ask,
                                        approvals=approvals, task_scope=task_scope, maintenance=maintenance,
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
                                'resolved_targets': _resolved_targets(
                                    request.get('permission', ''), request.get('patterns', []), workdir),
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
