"""Ticket 314: maintain VEIL itself in an isolated worktree and deliver through GitHub.

The program owns the facts that decide whether a delivery may proceed: repository, branch,
worktree, pushed commit, pull request, CI, review, Lan's actual acceptance and merge
authorisation. OpenCode only edits and tests the worktree; it never receives the GitHub
credential or the main repository's git metadata.
"""
from __future__ import annotations

import json
import os
import re
import secrets
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

from agent_types import UserError
from windows_process import hidden_subprocess_kwargs

DEFAULT_GITHUB_REPO = 'yecays-ai/veil-agent'
TASK_STAGES = frozenset({
    'developing', 'delivering', 'pr_open', 'ci_pending', 'ci_failed', 'review_failed',
    'awaiting_acceptance', 'acceptance_failed', 'ready_to_merge', 'merging', 'merged',
    'deploy_pending', 'done', 'stopped', 'blocked',
})
MAX_TITLE = 160
MAX_GOAL = 4000
MAX_NOTE = 1000
MAX_HISTORY = 60
FORBIDDEN_PATH = re.compile(
    r'(^|/)(\.env(\..*)?|persona/[^/]+\.md|data/|logs?/|memory/|.*\.(log|pem|pfx|key)|'
    r'id_(rsa|ed25519).*|cookies?|desktop/node_modules/|\.venv/|node_modules/)', re.I)
ISSUE_LINE = re.compile(r'\b(P[0-3]|critical|security|bug)\b|错误|安全|数据|阻断', re.I)


def _now():
    return datetime.now().astimezone().isoformat(timespec='seconds')


def _age(task):
    try:
        created = datetime.fromisoformat(task['created_at'])
        return (datetime.now().astimezone() - created).total_seconds() / 3600
    except (KeyError, TypeError, ValueError):
        return 0.0


def _redact(text):
    text = re.sub(r'(?i)(https?://)[^/@\s]+@', r'\1[redacted]@', str(text))
    return re.sub(r'\b(gh[pousr]_|github_pat_)[A-Za-z0-9_-]{8,}', r'\1[redacted]', text)


def _short(text, limit=600):
    text = _redact(text or '').strip()
    return text if len(text) <= limit else text[:limit] + '...'


def _json(text):
    try:
        return json.loads(text or 'null')
    except ValueError:
        return None


def _slug(title):
    words = re.findall(r'[a-z0-9]+', (title or '').lower())
    slug = '-'.join(words)[:40].strip('-')
    return slug or 'task'


def _branch_name(ticket, title):
    return f'ticket/{ticket}-{_slug(title)}'


def _valid_task(task):
    if not isinstance(task, dict) or task.get('schema') != 1:
        return False
    strings = ('task_id', 'ticket', 'title', 'goal', 'repo', 'github_repo', 'worktree', 'branch',
               'base_main', 'running_version', 'stage', 'created_at', 'updated_at', 'next_step')
    if any(not isinstance(task.get(key), str) for key in strings):
        return False
    if task['stage'] not in TASK_STAGES or not task['task_id']:
        return False
    for key in ('authorization', 'git', 'pull_request', 'checks', 'review', 'acceptance',
                'deployment', 'development'):
        if not isinstance(task.get(key), dict):
            return False
    if not isinstance(task.get('history'), list) or len(task['history']) > MAX_HISTORY:
        return False
    if not all(isinstance(row, dict) and isinstance(row.get('at'), str) and isinstance(row.get('stage'), str)
               for row in task['history']):
        return False
    return isinstance(task.get('blockers', []), list)


class MaintenanceError(UserError):
    pass


class Runner:
    def run(self, argv, cwd=None, timeout=180, input_text=None, env=None):
        try:
            finished = subprocess.run(
                [str(item) for item in argv], cwd=str(cwd) if cwd else None,
                capture_output=True, text=True, encoding='utf-8', errors='replace',
                input=input_text, timeout=timeout, env=env,
                **hidden_subprocess_kwargs(windows=os.name == 'nt', module=subprocess))
        except FileNotFoundError as exc:
            raise MaintenanceError(f'missing command: {argv[0]}') from exc
        except subprocess.TimeoutExpired as exc:
            raise MaintenanceError(f'command timed out: {argv[0]}') from exc
        return finished.returncode, finished.stdout or '', finished.stderr or ''


class MaintenanceStore:
    """Atomic per-task JSON plus a one-line active task for the next request."""

    def __init__(self, store, character_id):
        self.root = Path(store.root) / character_id / 'maintenance'
        self.tasks = self.root / 'tasks'

    def _path(self, task_id):
        if not re.fullmatch(r'm-[0-9]{8}-[0-9]{6}-[0-9a-f]{6}', task_id or ''):
            raise MaintenanceError('invalid maintenance task id')
        return self.tasks / f'{task_id}.json'

    def save(self, task):
        if not _valid_task(task):
            raise MaintenanceError('maintenance state is incomplete; the previous version was kept')
        path = self._path(task['task_id'])
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix('.tmp')
        payload = json.dumps(task, ensure_ascii=False, indent=2) + '\n'
        try:
            with temporary.open('w', encoding='utf-8', newline='\n') as handle:
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, path)
        except OSError as exc:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass
            raise MaintenanceError('maintenance state could not be saved safely') from exc
        self.root.mkdir(parents=True, exist_ok=True)
        (self.root / 'active.json').write_text(json.dumps({'task_id': task['task_id']}),
                                                encoding='utf-8', newline='\n')

    def load(self, task_id=''):
        task_id = task_id or self.active_id()
        if not task_id:
            return None
        try:
            value = json.loads(self._path(task_id).read_text(encoding='utf-8'))
        except FileNotFoundError:
            return None
        except (OSError, ValueError, UnicodeError) as exc:
            raise MaintenanceError('maintenance state is corrupted; stopped without guessing') from exc
        if not _valid_task(value):
            raise MaintenanceError('maintenance state is incomplete; stopped without guessing')
        return value

    def active_id(self):
        try:
            value = json.loads((self.root / 'active.json').read_text(encoding='utf-8'))
            return value.get('task_id') if isinstance(value, dict) else ''
        except (OSError, ValueError):
            return ''

    def all(self):
        if not self.tasks.exists():
            return []
        result = []
        for path in self.tasks.glob('m-*.json'):
            try:
                value = json.loads(path.read_text(encoding='utf-8'))
            except (OSError, ValueError):
                continue
            if _valid_task(value):
                result.append(value)
        return sorted(result, key=lambda item: item['updated_at'], reverse=True)

    def find_ticket(self, ticket):
        for task in self.all():
            if task['ticket'] == str(ticket) and task['stage'] not in {'merged', 'done', 'stopped'}:
                return task
        return None


def _new_task(*, ticket, title, goal, repo, github_repo, worktree, branch, base_main,
              running_version, acceptance, hours):
    task_id = f"m-{time.strftime('%Y%m%d-%H%M%S')}-{secrets.token_hex(3)}"
    now = _now()
    deadline = (datetime.now().astimezone() + timedelta(hours=hours)).isoformat(timespec='seconds')
    return {
        'schema': 1, 'task_id': task_id, 'ticket': str(ticket), 'title': title, 'goal': goal,
        'repo': str(repo), 'github_repo': github_repo, 'worktree': str(worktree), 'branch': branch,
        'base_main': base_main, 'running_version': running_version, 'stage': 'developing',
        'created_at': now, 'updated_at': now, 'deadline': deadline,
        'next_step': 'Read AGENTS.md, STATUS.md and HANDOFF.md in the worktree, then edit and test.',
        'authorization': {'develop_deliver': {}, 'merge': {}, 'denials': {}, 'merge_denied': {}},
        'git': {'last_commit': '', 'pushed_sha': '', 'files': 0},
        'pull_request': {'number': 0, 'url': '', 'state': '', 'head_sha': '', 'mergeable': '', 'base': 'main'},
        'checks': {'state': 'none', 'required': [], 'failed': [], 'observed_at': '', 'head_sha': '', 'logs': ''},
        'review': {'blocking': [], 'unresolved': [], 'observed_at': '', 'head_sha': '', 'reviewed_sha': ''},
        'acceptance': {'status': 'not_yet', 'note': '', 'instance_url': '', 'pids': [], 'recorded_at': '',
                       'waived': False},
        'deployment': {'merged_commit': '', 'deployed': False},
        'development': {'cost_cny': '0', 'steps': 0, 'last_report': ''},
        'blockers': [], 'history': [{'at': now, 'stage': 'developing', 'note': 'created isolated worktree'}],
        'acceptance_note': acceptance or '',
    }


def _event(task, stage, note):
    task['stage'] = stage
    task['updated_at'] = _now()
    task['history'].append({'at': task['updated_at'], 'stage': stage, 'note': _short(note, 300)})
    task['history'] = task['history'][-MAX_HISTORY:]


def _section(task, status_text):
    ticket = task['ticket']
    return (f'<!-- maintenance:{ticket}:start -->\n'
            f'## 工单 {ticket}（{status_text}）\n'
            f'- 目标：{task["goal"]}\n'
            f'- 分支：`{task["branch"]}`，使用独立 worktree；正式运行目录未改动。\n'
            f'- 状态：{task["stage"]}；{task["next_step"]}\n'
            f'- PR：{task["pull_request"].get("url") or "尚未创建"}；验收：{task["acceptance"]["status"]}。\n'
            f'<!-- maintenance:{ticket}:end -->')


def _replace_section(path, ticket, block):
    try:
        text = Path(path).read_text(encoding='utf-8')
    except FileNotFoundError:
        text = ''
    start = f'<!-- maintenance:{ticket}:start -->'
    end = f'<!-- maintenance:{ticket}:end -->'
    if start in text and end in text:
        before = text.split(start, 1)[0]
        after = text.split(end, 1)[1]
        text = before.rstrip() + '\n\n' + block + '\n' + after.lstrip('\n')
    else:
        text = text.rstrip() + '\n\n' + block + '\n'
    Path(path).write_text(text, encoding='utf-8', newline='\n')


@dataclass
class AcceptanceResult:
    url: str
    pids: list
    note: str = ''


class AcceptanceInstance:
    """The worktree's own web service and desktop window, with copied config and test data."""

    def __init__(self, task, repo_path, *, python=None, electron=None, data_root=None):
        self.task = task
        self.repo = Path(repo_path)
        self.worktree = Path(task['worktree'])
        self.root = Path(data_root) if data_root else self.worktree / '.agents'
        self.dir = self.root / f'acceptance-{task["ticket"]}'
        self.config = self.dir / 'config'
        self.data = self.config / 'data'
        self.desktop = self.dir / 'desktop'
        self.python = Path(python) if python else self.repo / '.venv' / 'Scripts' / 'python.exe'
        if not self.python.exists():
            self.python = Path(sys.executable)
        self.electron = (Path(electron) if electron else
                         self.repo / 'desktop' / 'node_modules' / 'electron' / 'dist' / 'electron.exe')
        self.processes = []

    def _prepare(self):
        self.dir.mkdir(parents=True, exist_ok=True)
        if self.config.exists():
            shutil.rmtree(self.config)
        self.config.mkdir(parents=True)
        self.data.mkdir(parents=True)
        self.desktop.mkdir(parents=True, exist_ok=True)
        for name in ('characters', 'persona'):
            source = self.repo / name
            if source.is_dir():
                shutil.copytree(source, self.config / name)
        env_file = self.repo / '.env'
        if env_file.is_file():
            shutil.copy2(env_file, self.config / '.env')

    def start(self):
        self._prepare()
        command = [str(self.python), str(self.worktree / 'web_agent.py'), '--acceptance',
                   '--config-root', str(self.config), '--data-dir', str(self.data), '--port', '0']
        backend = subprocess.Popen(
            command, cwd=str(self.worktree), env={**os.environ, 'PYTHONUTF8': '1'},
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            **hidden_subprocess_kwargs(windows=os.name == 'nt', module=subprocess))
        self.processes.append(backend)
        endpoint = self.data / 'web_service.json'
        deadline, state = time.monotonic() + 90, None
        while time.monotonic() < deadline:
            if backend.poll() is not None:
                break
            try:
                state = json.loads(endpoint.read_text(encoding='utf-8'))
                if isinstance(state.get('port'), int) and isinstance(state.get('token'), str):
                    break
            except (OSError, ValueError):
                pass
            time.sleep(0.5)
        if not isinstance(state, dict) or not isinstance(state.get('port'), int):
            self.stop()
            raise MaintenanceError('测试实例没有启动；请检查开发工作区的依赖和配置。')
        url = f"http://127.0.0.1:{state['port']}/?t={state['token']}"
        ready, deadline = False, time.monotonic() + 90
        while time.monotonic() < deadline:
            if backend.poll() is not None:
                break
            try:
                request = urllib.request.Request(
                    f"http://127.0.0.1:{state['port']}/api/events?after=0",
                    headers={'X-Token': state['token']})
                with urllib.request.urlopen(request, timeout=3) as response:
                    live = json.loads(response.read().decode('utf-8', 'replace'))
                if live.get('stopped') is False and live.get('ready') is True:
                    ready = True
                    break
            except (OSError, ValueError, urllib.error.URLError):
                pass
            time.sleep(0.5)
        try:
            (self.config / '.env').unlink(missing_ok=True)
        except OSError:
            pass
        if not ready:
            self.stop()
            raise MaintenanceError('测试实例没有就绪；请检查开发工作区的模型配置和依赖。')
        if self.electron.is_file():
            try:
                window = subprocess.Popen(
                    [str(self.electron), str(self.worktree / 'desktop')], cwd=str(self.worktree / 'desktop'),
                    env={**os.environ, 'VEIL_AGENT_ROOT': str(self.worktree),
                         'VEIL_AGENT_DATA': str(self.data), 'VEIL_PYTHON': str(self.python),
                         'VEIL_DESKTOP_DATA': str(self.desktop), 'VEIL_DESKTOP_TEST': '1'},
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                self.processes.append(window)
            except OSError:
                pass
        return AcceptanceResult(
            url, [process.pid for process in self.processes],
            'test instance uses the development worktree code and its own data; autonomy, external '
            'channels, memory, voice and desktop control are disabled')

    def stop(self):
        for process in reversed(self.processes):
            try:
                if process.poll() is None:
                    if os.name == 'nt':
                        subprocess.run(['taskkill', '/F', '/T', '/PID', str(process.pid)],
                                       capture_output=True, timeout=15,
                                       **hidden_subprocess_kwargs(windows=True, module=subprocess))
                    else:
                        process.terminate()
            except (OSError, subprocess.TimeoutExpired):
                pass
        self.processes = []
        try:
            (self.config / '.env').unlink(missing_ok=True)
        except OSError:
            pass


class MaintenanceManager:
    def __init__(self, store, character_id, *, repo_path, worktree_root=None,
                 github_repo=DEFAULT_GITHUB_REPO, approval=None, runner=None,
                 instance_factory=None, python=None, electron=None, hours=2):
        self.store = MaintenanceStore(store, character_id)
        self.character_id = character_id
        self.data_root = Path(store.root) / character_id / 'maintenance'
        self.repo = Path(repo_path).resolve()
        self.worktree_root = Path(worktree_root or (self.repo.parent / 'worktrees')).resolve()
        self.github_repo = github_repo
        self.approval = approval
        self.runner = runner or Runner()
        self.instance_factory = instance_factory or (lambda task: AcceptanceInstance(
            task, self.repo, python=python, electron=electron, data_root=self.data_root))
        self.hours = hours

    # --- host commands -------------------------------------------------
    def _run(self, argv, cwd=None, timeout=180, allow_fail=False):
        env = None
        if argv and str(argv[0]) == 'git':
            env = {**os.environ, 'GIT_TERMINAL_PROMPT': '0', 'GCM_INTERACTIVE': 'never'}
        elif argv and str(argv[0]) == 'gh':
            env = {**os.environ, 'GH_PROMPT_DISABLED': '1', 'GH_NO_UPDATE_NOTIFIER': '1'}
        code, out, err = self.runner.run(argv, cwd=cwd, timeout=timeout, env=env)
        if code and not allow_fail:
            raise MaintenanceError(_short(err or out or f'command failed: {argv[0]}', 700))
        return code, out, err

    def _git(self, *args, cwd=None, allow_fail=False, timeout=180):
        _code, out, _err = self._run(['git', *args], cwd=cwd or self.repo, allow_fail=allow_fail,
                                     timeout=timeout)
        return out

    def _gh(self, *args, allow_fail=False):
        return self._run(['gh', *args], cwd=self.repo, timeout=120, allow_fail=allow_fail)

    # --- repository identity ------------------------------------------
    def _verify_repo(self):
        if not self.repo.is_dir():
            raise MaintenanceError('VEIL repository directory does not exist')
        top = self._git('rev-parse', '--show-toplevel').strip()
        if Path(top).resolve() != self.repo:
            raise MaintenanceError('maintenance must start from the VEIL repository root')
        if self._git('symbolic-ref', '--short', 'HEAD').strip() != 'main':
            raise MaintenanceError('the formal checkout is not on main; maintenance was not started')
        if self._git('status', '--porcelain', '--untracked-files=no').strip():
            raise MaintenanceError('the formal checkout has uncommitted source changes; not overwriting them')
        remote = self._git('remote', 'get-url', 'origin').strip()
        match = re.search(r'github\.com[:/]([^/]+)/([^/]+?)(?:\.git)?$', remote, re.I)
        if not match or f'{match.group(1)}/{match.group(2)}'.casefold() != self.github_repo.casefold():
            raise MaintenanceError('origin does not match the configured VEIL GitHub repository')
        return self._git('rev-parse', 'HEAD').strip()

    def _sync_main(self):
        code, _out, _err = self._run(['git', 'fetch', 'origin', 'main', '--prune'], cwd=self.repo,
                                     timeout=180, allow_fail=True)
        warning = '' if code == 0 else ' (offline: using the local main record)'
        try:
            return self._git('rev-parse', 'origin/main').strip(), warning
        except MaintenanceError:
            return self._git('rev-parse', 'main').strip(), warning

    def _next_ticket(self, base):
        try:
            names = self._git('ls-tree', '--name-only', f'{base}:docs/tickets').splitlines()
        except MaintenanceError:
            names = []
        numbers = [int(match.group(1)) for name in names
                   if (match := re.match(r'^(\d+)-[^/]+\.md$', name))]
        numbers += [int(task['ticket']) for task in self.store.all() if task['ticket'].isdigit()]
        return max(numbers, default=0) + 1

    # --- worktree and documents ---------------------------------------
    def _worktree_add(self, worktree, branch, base):
        worktree = Path(worktree)
        if worktree.exists():
            top = self._git('rev-parse', '--show-toplevel', cwd=worktree).strip()
            if Path(top).resolve() != worktree.resolve():
                raise MaintenanceError('existing maintenance directory is not a Git worktree')
            if self._git('symbolic-ref', '--short', 'HEAD', cwd=worktree).strip() != branch:
                raise MaintenanceError('existing maintenance worktree is on another branch')
            return 'reused'
        current = [line[9:] for line in self._git('worktree', 'list', '--porcelain').splitlines()
                   if line.startswith('worktree ')]
        if any(Path(item).resolve() == worktree.resolve() for item in current):
            raise MaintenanceError('the maintenance directory is registered to another task')
        if self._git('branch', '--list', branch).strip():
            self._git('worktree', 'add', str(worktree), branch)
        else:
            self._git('worktree', 'add', '-b', branch, str(worktree), base)
        return 'created'

    def _write_documents(self, task, status_text='施工中'):
        worktree = Path(task['worktree'])
        ticket = task['ticket']
        path = worktree / 'docs' / 'tickets' / f'{ticket}-{_slug(task["title"])}.md'
        path.parent.mkdir(parents=True, exist_ok=True)
        if not path.exists():
            acceptance = task.get('acceptance_note') or '1. Lan uses the test instance and confirms the result.'
            path.write_text(
                f'# 工单 {ticket}：{task["title"]}\n\n'
                f'## 目标\n\n{task["goal"]}\n\n'
                f'## 当前状态\n\n分支 `{task["branch"]}`，从 `main` 的 `{task["base_main"][:8]}` 建立；'
                '开发在独立 worktree 中进行，正式运行目录未改动。\n\n'
                f'## 验收步骤\n\n{acceptance}\n', encoding='utf-8', newline='\n')
        _replace_section(worktree / 'STATUS.md', ticket, _section(task, status_text))
        _replace_section(worktree / 'HANDOFF.md', ticket, _section(task, status_text))

    def _forbidden_changes(self, worktree):
        bad = []
        for line in self._git('status', '--porcelain', cwd=worktree).splitlines():
            if len(line) < 4:
                continue
            path = line[3:].strip().strip('"').replace('\\', '/')
            if path and FORBIDDEN_PATH.search(path) and not path.endswith('.example.md'):
                bad.append(path)
        for path in self._git('ls-files', cwd=worktree).splitlines():
            path = path.strip().strip('"').replace('\\', '/')
            if path and FORBIDDEN_PATH.search(path) and not path.endswith('.example.md'):
                bad.append(path)
        return sorted(set(bad))

    def _commit(self, task, message):
        worktree = Path(task['worktree'])
        bad = self._forbidden_changes(worktree)
        if bad:
            raise MaintenanceError('refusing to commit sensitive or generated files: ' + ', '.join(bad[:5]))
        self._git('add', '-A', cwd=worktree)
        if not self._git('diff', '--cached', '--name-only', cwd=worktree).strip():
            return ''
        self._git('diff', '--cached', '--check', cwd=worktree)
        self._git('-c', 'user.name=Peter', '-c', 'user.email=peter@veil.local',
                  'commit', '-m', message, cwd=worktree)
        return self._git('rev-parse', 'HEAD', cwd=worktree).strip()

    def _push(self, task):
        worktree = Path(task['worktree'])
        branch = task['branch']
        local = self._git('rev-parse', 'HEAD', cwd=worktree).strip()
        out = self._git('ls-remote', '--heads', 'origin', f'refs/heads/{branch}',
                        cwd=worktree, allow_fail=True)
        remote = out.split()[0] if out.strip() else ''
        if remote != local:
            self._git('push', '-u', 'origin', f'HEAD:{branch}', cwd=worktree, timeout=300)
        return local

    # --- pull request --------------------------------------------------
    def _pr_list(self, task):
        _code, out, _err = self._gh('pr', 'list', '--repo', self.github_repo, '--head', task['branch'],
                                    '--state', 'all', '--json', 'number,url,state,headRefOid', allow_fail=True)
        values = _json(out)
        return values if isinstance(values, list) else []

    def _pr_view(self, number):
        fields = ('number,url,state,isDraft,mergeable,mergeStateStatus,headRefOid,baseRefName,'
                  'statusCheckRollup,reviews,mergeCommit')
        _code, out, err = self._gh('pr', 'view', str(number), '--repo', self.github_repo, '--json', fields)
        value = _json(out)
        if not isinstance(value, dict):
            raise MaintenanceError(_short(err or 'cannot read pull request state', 500))
        return value

    def _create_or_update_pr(self, task, title, body):
        existing = self._pr_list(task)
        open_pr = next((item for item in existing if item.get('state') == 'OPEN'), None)
        if open_pr:
            number = int(open_pr['number'])
            self._gh('pr', 'edit', str(number), '--repo', self.github_repo, '--title', title, '--body', body)
        else:
            if existing:
                raise MaintenanceError('this branch already has a closed or merged pull request; start a new task')
            _code, out, err = self._gh('pr', 'create', '--repo', self.github_repo, '--base', 'main',
                                       '--head', task['branch'], '--title', title, '--body', body)
            match = re.search(r'/pull/(\d+)', out)
            number = int(match.group(1)) if match else 0
            if not number:
                raise MaintenanceError(_short(err or 'pull request creation returned no number', 500))
        return number, self._pr_view(number)

    def _required_checks(self):
        _code, out, _err = self._gh('api', f'repos/{self.github_repo}/branches/main/protection',
                                    allow_fail=True)
        value = _json(out)
        if not isinstance(value, dict):
            return []
        required = value.get('required_status_checks') or {}
        result = list(required.get('contexts') or []) if isinstance(required, dict) else []
        if isinstance(required, dict) and isinstance(required.get('checks'), list):
            result += [item.get('context') for item in required['checks']
                       if isinstance(item, dict) and item.get('context')]
        return sorted({item for item in result if isinstance(item, str)})

    @staticmethod
    def _check_rows(view):
        rows = []
        for item in view.get('statusCheckRollup') or []:
            if not isinstance(item, dict):
                continue
            rows.append({'name': item.get('name') or item.get('context') or '',
                         'status': (item.get('status') or '').upper(),
                         'conclusion': (item.get('conclusion') or item.get('state') or '').upper(),
                         'url': item.get('detailsUrl') or item.get('targetUrl') or ''})
        return rows

    def _failed_logs(self, failed):
        for row in failed:
            match = re.search(r'/actions/runs/(\d+)', row.get('url') or '')
            if not match:
                continue
            _code, out, err = self._gh('run', 'view', match.group(1), '--repo', self.github_repo,
                                       '--log-failed', allow_fail=True)
            text = (out or err or '').strip()
            if text:
                return _short(text[-3500:], 3500)
        return ''

    @staticmethod
    def _review_state(view):
        blocking, advisory = [], []
        latest = {}
        for review in view.get('reviews') or []:
            if isinstance(review, dict) and (review.get('author') or {}).get('login'):
                latest[(review['author'] or {}).get('login')] = review.get('state') or ''
        blocking = [login for login, state in latest.items() if state == 'CHANGES_REQUESTED']
        for review in view.get('reviews') or []:
            if isinstance(review, dict) and (review.get('state') or '') == 'COMMENTED':
                body = _short(review.get('body') or '', 300)
                if body and ISSUE_LINE.search(body):
                    advisory.append(body)
        return blocking, advisory[:6]

    def _check_summary(self, task, view, *, log=False):
        rows = self._check_rows(view)
        required = self._required_checks()
        names = {row['name'] for row in rows}
        missing = [name for name in required if name not in names]
        required = [name for name in required if name in names]
        watched = required or [row['name'] for row in rows if row['conclusion'] != 'SKIPPED']
        pending = [row for row in rows if row['name'] in watched and row['status'] and row['status'] != 'COMPLETED']
        if missing:
            pending.append({'name': missing[0]})
        failed = [row for row in rows if row['name'] in watched and row['status'] in ('COMPLETED', '') and
                  row['conclusion'] not in ('SUCCESS', 'NEUTRAL', 'SKIPPED')]
        if failed:
            state = 'failed'
        elif pending:
            state = 'pending'
        elif watched:
            state = 'passed'
        else:
            state = 'none'
        head = view.get('headRefOid') or ''
        checks = {'state': state, 'required': required, 'failed': [row['name'] for row in failed],
                  'observed_at': _now(), 'head_sha': head, 'logs': ''}
        if log and state == 'failed':
            checks['logs'] = self._failed_logs(failed)
        return checks

    # --- public actions ------------------------------------------------
    def invoke(self, action, args, *, autonomy=False):
        if not isinstance(args, dict):
            return 'invalid maintenance arguments', '被拒绝'
        try:
            handler = {
                'begin': self.begin, 'status': self.status, 'resume': self.resume,
                'deliver': self.deliver, 'checks': self.checks, 'accept': self.accept,
                'merge': self.merge, 'stop': self.stop, 'cleanup': self.cleanup,
            }.get(action)
            if handler is None:
                return 'unknown maintenance action', '被拒绝'
            return handler(args, autonomy=autonomy)
        except MaintenanceError as exc:
            return _short(str(exc), 900), '出错'
        except (OSError, ValueError, TypeError, KeyError):
            return 'maintenance action could not continue', '出错'

    def begin(self, args, *, autonomy=False):
        if autonomy:
            return 'autonomous activity cannot start a VEIL maintenance task; Lan must ask for it', '被拒绝'
        title = str(args.get('title') or '').strip()
        goal = str(args.get('goal') or '').strip()
        if not title or not goal:
            return 'maintenance begin needs title and goal', '被拒绝'
        title, goal = _short(title, MAX_TITLE), _short(goal, MAX_GOAL)
        running = self._verify_repo()
        base, warning = self._sync_main()
        try:
            ticket = int(args.get('ticket') or self._next_ticket(base))
        except (TypeError, ValueError):
            return 'invalid ticket number', '被拒绝'
        existing = self.store.find_ticket(ticket)
        if existing:
            return (f"ticket {ticket} already has task {existing['task_id']} ({existing['stage']}); "
                    'use action=resume instead of creating a second branch'), '出错'
        branch = _branch_name(ticket, title)
        if re.search(r'[^A-Za-z0-9._/-]', branch) or '..' in branch:
            return 'invalid branch name', '被拒绝'
        worktree = (self.worktree_root / f'{ticket}-{_slug(title)}').resolve()
        if self.worktree_root not in worktree.parents:
            return 'maintenance worktree must stay under the configured worktrees directory', '被拒绝'
        scope = f'{self.github_repo}#{branch}'
        if not self._ask(f'开始工单 {ticket}「{title}」：允许 Peter 在独立工作区开发，并只对 '
                         f'{self.github_repo} 的 {branch} 分支推送、创建或更新 PR 吗？正式目录不会改动。y/N '):
            return 'Lan did not authorise pushing this branch; the task was not started', '被拒绝'
        self._worktree_add(worktree, branch, base)
        task = _new_task(ticket=ticket, title=title, goal=goal, repo=self.repo,
                         github_repo=self.github_repo, worktree=worktree, branch=branch,
                         base_main=base, running_version=running,
                         acceptance=str(args.get('acceptance') or '')[:2000], hours=self.hours)
        task['authorization']['develop_deliver'] = {'scope': scope, 'at': _now()}
        self._write_documents(task)
        self.store.save(task)
        return (f'维护任务 {task["task_id"]} 已开始。工单 {ticket}，分支 {branch}，独立 worktree 已建立，'
                f'基于 main {base[:8]}。正式运行版本 {running[:8]}。{warning}\n'
                '下一步：先读 worktree 的 AGENTS.md、STATUS.md、HANDOFF.md，再用 workbench 并带上 task_id '
                '完成代码修改和测试，然后 action=deliver。'), '成功'

    def _task(self, args):
        task = self.store.load(str(args.get('task_id') or ''))
        if task is None:
            raise MaintenanceError('there is no maintenance task to continue')
        return task

    def status(self, args, *, autonomy=False):
        task = self._task(args)
        self._reconcile(task)
        return self._status_text(task), '成功'

    def resume(self, args, *, autonomy=False):
        task = self._task(args)
        self._reconcile(task)
        return self._status_text(task) + '\n' + task['next_step'], '成功'

    def _status_text(self, task):
        pr, acceptance = task['pull_request'], task['acceptance']
        age = _age(task)
        lines = [
            f"task={task['task_id']} ticket={task['ticket']} stage={task['stage']}",
            f"title={task['title']}",
            f"branch={task['branch']} base={task['base_main'][:8]} "
            f"worktree={'exists' if Path(task['worktree']).exists() else 'missing'}",
            f"pr={pr.get('number') or '-'} {pr.get('url') or ''} state={pr.get('state') or '-'} "
            f"head={pr.get('head_sha', '')[:8]}",
            f"ci={task['checks']['state']} failed={','.join(task['checks']['failed'][:5]) or '-'}",
            f"review_blocking={','.join(task['review']['blocking'][:5]) or '-'}",
            f"acceptance={acceptance['status']} {'(waived)' if acceptance.get('waived') else ''}",
            f"age_hours={age:.1f} next={task['next_step']}",
        ]
        if age >= 1:
            lines.append('time_check: over one hour; report the state and blockers to Lan.')
        if task['blockers']:
            lines.append('blockers=' + '; '.join(task['blockers'][:3]))
        return '\n'.join(lines)

    def _reconcile(self, task):
        worktree = Path(task['worktree'])
        if not worktree.exists():
            task['blockers'] = ['maintenance worktree is missing']
            _event(task, 'blocked', 'worktree missing')
            self.store.save(task)
            return
        try:
            branch = self._git('symbolic-ref', '--short', 'HEAD', cwd=worktree).strip()
            head = self._git('rev-parse', 'HEAD', cwd=worktree).strip()
        except MaintenanceError:
            return
        if branch != task['branch']:
            task['blockers'] = ['worktree branch does not match the task']
            _event(task, 'blocked', 'branch mismatch')
            self.store.save(task)
            return
        task['git']['last_commit'] = head
        if task['pull_request'].get('number'):
            try:
                view = self._pr_view(task['pull_request']['number'])
            except MaintenanceError:
                return
            task['pull_request'].update({'state': view.get('state') or '', 'head_sha': view.get('headRefOid') or '',
                                         'mergeable': view.get('mergeable') or '', 'url': view.get('url') or ''})
            if view.get('state') == 'MERGED' and task['stage'] != 'merged':
                _event(task, 'merged', 'pull request was merged on GitHub')
                task['deployment']['merged_commit'] = ((view.get('mergeCommit') or {}).get('oid') or
                                                       view.get('headRefOid') or '')
                task['next_step'] = 'Let Lan run update-main.cmd for the formal version.'
            self.store.save(task)

    def deliver(self, args, *, autonomy=False):
        if autonomy:
            return 'autonomous activity cannot push or create a pull request', '被拒绝'
        task = self._task(args)
        if task['stage'] in {'stopped', 'merged', 'done'}:
            return f"task is {task['stage']}; it cannot be delivered again", '被拒绝'
        scope = f"{self.github_repo}#{task['branch']}"
        grant = task['authorization'].get('develop_deliver') or {}
        if grant.get('scope') != scope:
            if not self._ask(f'允许 Peter 把工单 {task["ticket"]} 的独立分支 {task["branch"]} 推送到 '
                             f'{self.github_repo} 并创建/更新 PR 吗？y/N '):
                task['authorization']['denials']['develop_deliver'] = _now()
                task['blockers'] = ['Lan declined pushing or creating the pull request']
                _event(task, 'blocked', 'deliver denied')
                self.store.save(task)
                return 'Lan declined the push or pull request; the worktree changes were kept', '被拒绝'
            task['authorization']['develop_deliver'] = {'scope': scope, 'at': _now()}
        task['next_step'] = 'Commit and push the development branch, then create or update the PR.'
        _event(task, 'delivering', 'started commit and push')
        self.store.save(task)
        self._write_documents(task, '待检查')
        commit = self._commit(task, f"工单 {task['ticket']}: {task['title']}")
        if commit:
            task['git']['last_commit'] = commit
        head = self._push(task)
        task['git']['pushed_sha'] = head
        diff = self._git('diff', '--stat', f"{task['base_main']}...HEAD", cwd=task['worktree']).strip()
        changed = len(self._git('diff', '--name-only', f"{task['base_main']}...HEAD",
                                cwd=task['worktree']).splitlines())
        task['git']['files'] = changed
        verification = _short(str(args.get('verification') or
                                  'The development workbench ran local checks; the PR CI is the external evidence.'), 1500)
        body = (f"## 工单\n\n{task['goal']}\n\n## 改动\n\n```\n{diff[-3000:]}\n```\n\n"
                f"## 验证\n\n{verification}\n\n## 验收\n\n")
        body += ('Lan 已在独立测试实例中验收通过。\n' if task['acceptance']['status'] == 'passed'
                 else '待 Lan 在独立测试实例中实际验收；未验收不会被自动视为通过。\n')
        number, view = self._create_or_update_pr(task, f"[工单 {task['ticket']}] {task['title']}", body)
        task['pull_request'] = {'number': number,
                                'url': view.get('url') or f'https://github.com/{self.github_repo}/pull/{number}',
                                'state': view.get('state') or 'OPEN', 'head_sha': view.get('headRefOid') or head,
                                'mergeable': view.get('mergeable') or '', 'base': view.get('baseRefName') or 'main'}
        _event(task, 'pr_open', f'PR #{number} created or updated')
        task['next_step'] = 'Check the required PR CI and substantive review comments.'
        self.store.save(task)
        return (f'已提交 {head[:8]} 并推送；PR #{number} {task["pull_request"]["url"]}。'
                f'共改动 {changed} 个文件。下一步 action=checks。'), '成功'

    def checks(self, args, *, autonomy=False):
        task = self._task(args)
        if not task['pull_request'].get('number'):
            return 'there is no pull request yet; use action=deliver first', '被拒绝'
        view = self._pr_view(task['pull_request']['number'])
        checks = self._check_summary(task, view, log=True)
        blocking, advisory = self._review_state(view)
        task['checks'] = checks
        task['review'] = {'blocking': blocking, 'unresolved': advisory, 'observed_at': _now(),
                          'head_sha': view.get('headRefOid') or '',
                          'reviewed_sha': view.get('headRefOid') or ''}
        task['pull_request'].update({'state': view.get('state') or '', 'head_sha': view.get('headRefOid') or '',
                                     'mergeable': view.get('mergeable') or '', 'url': view.get('url') or ''})
        if checks['state'] == 'failed':
            stage, next_step = 'ci_failed', 'Read the failed log, fix the cause, and deliver again; stop after two attempts.'
        elif checks['state'] == 'pending':
            stage, next_step = 'ci_pending', 'Wait for the required CI, then action=checks again.'
        elif blocking:
            stage, next_step = 'review_failed', 'Handle review comments that affect function, data or security.'
        elif task['acceptance']['status'] == 'passed':
            stage, next_step = 'ready_to_merge', 'Acceptance and checks are satisfied; action=merge may proceed.'
        else:
            stage, next_step = 'awaiting_acceptance', 'Offer the test instance; unaccepted work must not merge.'
        _event(task, stage, next_step)
        task['next_step'] = next_step
        self.store.save(task)
        lines = [f"PR #{task['pull_request']['number']} {task['pull_request']['url']}",
                 f"head={checks['head_sha'][:8]} ci={checks['state']}",
                 f"failed_checks={', '.join(checks['failed']) or '-'}",
                 f"blocking_reviews={', '.join(blocking) or '-'}"]
        if advisory:
            lines.append('review_notes=' + ' | '.join(advisory[:3]))
        if checks['state'] == 'failed' and checks['logs']:
            lines.append('failed_log_tail:\n' + checks['logs'][-1800:])
        if checks['state'] == 'none':
            lines.append('no required checks were found; verify branch protection before merging.')
        return '\n'.join(lines) + '\nnext: ' + next_step, '成功'

    def accept(self, args, *, autonomy=False):
        task = self._task(args)
        if autonomy:
            return 'autonomous activity cannot replace Lan\'s acceptance', '被拒绝'
        result = str(args.get('result') or '').strip().lower()
        if result not in {'passed', 'failed'}:
            if task['acceptance'].get('instance_url'):
                return (f"test instance: {task['acceptance']['instance_url']}. After Lan uses it, record "
                        'result=passed or result=failed.'), '成功'
            try:
                started = self.instance_factory(task).start()
            except (MaintenanceError, OSError) as exc:
                task['acceptance']['status'] = 'not_yet'
                task['blockers'] = ['could not start an isolated test instance']
                _event(task, 'awaiting_acceptance', str(exc))
                task['next_step'] = 'Explain the limitation to Lan and wait for her delivery decision.'
                self.store.save(task)
                return _short(f'无法启动独立测试实例：{exc}', 700), '出错'
            task['acceptance'].update({'instance_url': started.url, 'pids': started.pids,
                                       'status': 'not_yet', 'recorded_at': _now()})
            _event(task, 'awaiting_acceptance', 'test instance started')
            task['next_step'] = 'Lan uses the isolated test instance; unaccepted work must not merge.'
            self.store.save(task)
            return (f"独立测试实例已启动：{started.url}\n{started.note}\n"
                    '请 Lan 实际体验。通过后 action=accept result=passed，未通过用 failed。'), '成功'
        if not self._ask(f'工单 {task["ticket"]} 的功能，你在独立测试实例中实际用过并确认'
                         f'{"符合需求" if result == "passed" else "不符合需求"}吗？y/N '):
            return 'Lan did not confirm the acceptance result; it stays unaccepted', '被拒绝'
        task['acceptance']['status'] = result
        task['acceptance']['note'] = _short(str(args.get('note') or ''), MAX_NOTE)
        task['acceptance']['recorded_at'] = _now()
        if result == 'passed' and task['checks']['state'] == 'passed' and not task['review']['blocking']:
            _event(task, 'ready_to_merge', 'acceptance passed and checks are green')
            task['next_step'] = 'Confirm merge authorisation, then action=merge.'
        elif result == 'failed':
            _event(task, 'acceptance_failed', 'Lan rejected the result')
            task['next_step'] = 'Fix the experience problem Lan found; do not merge.'
        else:
            _event(task, 'awaiting_acceptance', 'acceptance result recorded')
            task['next_step'] = 'Finish required CI and review first.'
        self.store.save(task)
        return f"验收结果已记录为 {result}。下一步：{task['next_step']}", '成功'

    def merge(self, args, *, autonomy=False):
        if autonomy:
            return 'autonomous activity cannot merge a pull request', '被拒绝'
        task = self._task(args)
        if task['stage'] in {'stopped', 'merged', 'done'}:
            return f"task is {task['stage']}; it cannot be merged", '被拒绝'
        number = task['pull_request'].get('number')
        if not number:
            return 'there is no pull request yet; use action=deliver first', '被拒绝'
        view = self._pr_view(number)
        checks = self._check_summary(task, view)
        blocking, _advisory = self._review_state(view)
        head = view.get('headRefOid') or ''
        if view.get('state') != 'OPEN':
            return f"pull request state is {view.get('state')}; it cannot be merged", '被拒绝'
        if view.get('baseRefName') != 'main':
            return 'the pull request base is not main', '被拒绝'
        if view.get('mergeable') != 'MERGEABLE':
            return f"pull request is not mergeable: {view.get('mergeable') or 'unknown'}", '被拒绝'
        local = self._git('rev-parse', 'HEAD', cwd=task['worktree']).strip()
        if head != local:
            return 'the PR head differs from the worktree; deliver the latest commit first', '被拒绝'
        if checks['state'] != 'passed':
            return f"required checks are not passing ({checks['state']})", '被拒绝'
        if blocking:
            return 'a review still requests changes', '被拒绝'
        acceptance = task['acceptance']['status']
        waiver = False
        authorization = task['authorization'].get('merge') or {}
        same_head = authorization.get('head_sha') == head and authorization.get('granted') is True
        if task['authorization'].get('merge_denied', {}).get('head_sha') == head and not args.get('retry'):
            return 'Lan already declined this merge; it will not ask again without a new instruction', '被拒绝'
        if acceptance != 'passed':
            if not self._ask(f'工单 {task["ticket"]} 尚未通过 Lan 的实际验收（当前 {acceptance}）。'
                             f'仍要合并 PR #{number} 吗？这会跳过用户验收。y/N '):
                task['authorization']['merge_denied'] = {'head_sha': head, 'at': _now()}
                _event(task, task['stage'], 'merge not authorised without acceptance')
                self.store.save(task)
                return 'Lan did not agree to skip acceptance; the PR stays open', '被拒绝'
            waiver = True
        elif not same_head:
            if not self._ask(f'PR #{number} 的必需 CI 已通过、验收已通过。现在合并到 main 吗？y/N '):
                task['authorization']['merge_denied'] = {'head_sha': head, 'at': _now()}
                _event(task, task['stage'], 'merge not authorised')
                self.store.save(task)
                return 'Lan did not authorise the merge; the PR stays open', '被拒绝'
        task['authorization']['merge'] = {'head_sha': head, 'at': _now(), 'waiver': waiver, 'granted': True}
        task['acceptance']['waived'] = waiver
        _event(task, 'merging', f'merging PR #{number}')
        task['next_step'] = 'Merge the pull request on GitHub and verify the result.'
        self.store.save(task)
        code, out, err = self._gh('pr', 'merge', str(number), '--repo', self.github_repo,
                                  '--merge', '--delete-branch', allow_fail=True)
        if code:
            _event(task, 'ready_to_merge', 'GitHub rejected the merge')
            task['blockers'] = [_short(err or out, 400)]
            task['next_step'] = 'Read GitHub\'s actual refusal; do not bypass branch protection.'
            self.store.save(task)
            return _short(f'GitHub 拒绝合并：{err or out}', 900), '出错'
        after = self._pr_view(number)
        if after.get('state') != 'MERGED':
            return f"merge command ran but the PR state is {after.get('state')}; verify it", '出错'
        merge_commit = ((after.get('mergeCommit') or {}).get('oid') or after.get('headRefOid') or '')
        task['pull_request'].update({'state': 'MERGED', 'head_sha': after.get('headRefOid') or head,
                                     'url': after.get('url') or task['pull_request']['url']})
        task['deployment']['merged_commit'] = merge_commit
        _event(task, 'merged', f'PR #{number} merged')
        cleanup = self._cleanup(task)
        task['next_step'] = 'Let Lan run update-main.cmd, restart, and confirm the feature.'
        self.store.save(task)
        return (f"已合并 PR #{number}：{task['pull_request']['url']}\n"
                f"合并提交 {merge_commit[:8]}；验收={acceptance}{'（Lan 同意跳过）' if waiver else ''}。\n"
                f"worktree {'已清理' if cleanup else '保留（有未提交改动或无法清理）'}。\n"
                '请 Lan 双击 update-main.cmd 更新正式版本，重启后按 STATUS.md 验收。'), '成功'

    def stop(self, args, *, autonomy=False):
        task = self._task(args)
        if task['stage'] in {'merged', 'done'}:
            return 'the task is already merged and cannot be stopped', '被拒绝'
        _event(task, 'stopped', 'Lan stopped the task')
        task['next_step'] = 'Stopped by Lan; no more commands, pushes or merges.'
        self.store.save(task)
        return '任务已停止；保留 worktree 和已有提交，不再自动执行。', '成功'

    def cleanup(self, args, *, autonomy=False):
        task = self._task(args)
        removed = self._cleanup(task)
        task['next_step'] = 'cleanup finished' if removed else 'worktree kept because it has uncommitted changes'
        self.store.save(task)
        return ('已清理本次 worktree。' if removed else 'worktree 仍有未提交改动，未删除。'), '成功'

    def _cleanup(self, task):
        worktree = Path(task['worktree'])
        if not worktree.exists():
            return True
        try:
            if self._git('status', '--porcelain', cwd=worktree).strip():
                return False
            self._git('worktree', 'remove', str(worktree))
            return True
        except MaintenanceError:
            return False

    def workbench_target(self, task_id):
        task = self.store.load(task_id)
        if task is None:
            raise MaintenanceError('maintenance task does not exist')
        if task['stage'] in {'stopped', 'merged', 'done'}:
            raise MaintenanceError(f"maintenance task is {task['stage']} and cannot be edited")
        worktree = Path(task['worktree'])
        if not worktree.is_dir():
            raise MaintenanceError('maintenance worktree is missing; use action=resume first')
        branch = self._git('symbolic-ref', '--short', 'HEAD', cwd=worktree).strip()
        if branch != task['branch']:
            raise MaintenanceError('maintenance worktree branch does not match the task')
        return task

    def record_workbench(self, task_id, *, cost, steps, report=''):
        try:
            task = self.store.load(task_id)
        except MaintenanceError:
            return
        if not task:
            return
        task['development'] = {'cost_cny': str(cost), 'steps': int(steps), 'last_report': _short(report, 1200)}
        task['updated_at'] = _now()
        if task['stage'] == 'developing':
            task['next_step'] = 'Fix failures from the test run; when done, action=deliver.'
        self.store.save(task)

    def active_summary(self):
        try:
            task = self.store.load()
        except MaintenanceError:
            return ''
        if not task or task['stage'] in {'merged', 'done', 'stopped'}:
            return ''
        return (f"Active VEIL maintenance task {task['task_id']} (ticket {task['ticket']}, {task['stage']}): "
                f"{task['next_step']} Use the maintenance tool with action=resume before unrelated work.")

    def _ask(self, prompt):
        if self.approval is None:
            return False
        try:
            answer = self.approval(prompt)
        except Exception:
            return False
        return isinstance(answer, str) and answer.strip().casefold() == 'y'


TOOL_SCHEMA = {'type': 'function', 'function': {
    'name': 'maintenance',
    'description': (
        'Maintain VEIL itself and deliver the change through GitHub. Use this when Lan asks you to change your '
        'own code and submit a pull request. Workflow: action=begin with a title, goal and 1-3 user-visible '
        'acceptance steps; then call workbench with the returned task_id to edit and test the isolated worktree; '
        'action=deliver commits, pushes and creates or updates the PR; action=checks reads real CI and review '
        'state; action=accept starts an isolated test instance and records Lan\'s actual acceptance; '
        'action=merge merges only after the program verifies acceptance, CI, review and a fresh authorisation. '
        'action=status or resume reads the persisted task and verifies the real worktree, commit and PR. '
        'OpenCode never receives the GitHub credential and must not commit or push; VEIL does that. '
        'Never treat a created PR or green CI as user acceptance.'),
    'parameters': {'type': 'object', 'properties': {
        'action': {'type': 'string', 'enum': ['begin', 'status', 'resume', 'deliver', 'checks',
                                              'accept', 'merge', 'stop', 'cleanup']},
        'task_id': {'type': 'string', 'description': 'Task ID returned by begin or status.'},
        'title': {'type': 'string', 'description': 'Short feature title for begin.'},
        'goal': {'type': 'string', 'description': 'What Lan can do after this change, for begin.'},
        'acceptance': {'type': 'string', 'description': '1-3 steps Lan will perform in the test instance, for begin.'},
        'ticket': {'type': 'integer', 'description': 'Optional ticket number; otherwise the next one is used.'},
        'verification': {'type': 'string', 'description': 'For deliver: what the workbench actually ran and the result.'},
        'result': {'type': 'string', 'enum': ['passed', 'failed'],
                   'description': 'For accept: whether Lan actually used and accepted the feature.'},
        'note': {'type': 'string', 'description': 'For accept: Lan\'s actual experience or problem.'},
        'retry': {'type': 'boolean', 'description': 'For merge: ask Lan again after an earlier refusal.'},
    }, 'required': ['action'], 'additionalProperties': False},
}}
