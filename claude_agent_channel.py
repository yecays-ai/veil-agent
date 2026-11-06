"""Ticket 228: an optional chat channel where Claude Code does the thinking and the tool use.

VEIL still builds the context (persona, facts, memories, history) and still owns the tools, the chat history
and the approvals. For each turn it starts `claude -p` once, gives it the VEIL tools through a small local
MCP bridge, and sends Claude Code's permission questions to Lan's chat as ordinary approval cards.

Everything here uses the standard library plus the `mcp` package VEIL already depends on.
"""
import json
import os
import queue
import re
import secrets
import shutil
import signal
import subprocess
import sys
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace

CHANNEL_KIND = 'claude_agent'
SERVER_NAME = 'veil'
APPROVE_TOOL = f'mcp__{SERVER_NAME}__approve'
BRIDGE_SCRIPT = Path(__file__).with_name('claude_agent_bridge.py')

MAX_TURNS = 30
MAX_BUDGET_USD = 1.5           # API-equivalent estimate; a subscription has no per-token price
MAX_WALL_SECONDS = 900         # active time; the wait for Lan's answer to an approval card does not count
REPEAT_LIMIT = 3               # the same tool with the same arguments more than this many times ends the run
MAX_TOOL_CALLS = 80
MAX_OUTPUT_BYTES = 8000

USAGE_LIMIT = re.compile(r"(hit your|reached your|exceeded your)[^.\n]{0,40}limit|usage limit|session limit", re.I)

REMINDER = ("Reminder: you are Peter texting Lan, not a coding assistant. Whatever you just did or are about to do, "
            "say it in a few short plain-text messages in your own voice with send_message: no markdown, no bold, "
            "no headings, no bullet lists, no report format. If she is upset, answer that first. Do the task "
            "instead of asking permission; the program asks her when her permission is needed.")

# Claude Code's own prompt wording for each native tool, as one line for Lan's approval card.
# Claude Code's own tools are not limited by VEIL's path checks (by design: the same ability as Claude Code on lan's
# PC), but VEIL's keys and secrets are off limits to them.
DENY_RULES = ['Read(**/.env)', 'Read(**/.env.*)', 'Edit(**/.env)', 'Edit(**/.env.*)', 'Write(**/.env)',
              'Read(**/secrets/**)', 'Edit(**/secrets/**)', 'Write(**/secrets/**)']


def describe_request(tool_name, tool_input):
    tool_input = tool_input if isinstance(tool_input, dict) else {}
    if tool_name == 'Bash':
        detail = tool_input.get('command', '')
    elif tool_name in ('Write', 'Edit', 'MultiEdit', 'NotebookEdit'):
        detail = tool_input.get('file_path') or tool_input.get('notebook_path') or ''
    elif tool_name in ('WebFetch',):
        detail = tool_input.get('url', '')
    else:
        detail = json.dumps(tool_input, ensure_ascii=False)
    detail = str(detail)
    if len(detail) > 600:
        detail = detail[:600] + '…'
    return f'Peter 想用 {tool_name}：\n{detail}\n\n同意吗？'


def is_channel(provider):
    return isinstance(provider, dict) and provider.get('kind') == CHANNEL_KIND


def stop_hook_source(aliases=None):
    """PostToolUse hook: a turn that ended in VEIL (stay_silent, or send_message with done) ends in Claude Code too;
    otherwise Claude Code keeps looping (ticket 227: about 730 stay_silent calls in one run).
    `aliases` maps canonical tool names to the model-facing aliases currently in use."""
    aliases = aliases or {}
    names = {key: sorted({key, aliases.get(key, key)}) for key in ('stay_silent', 'reply_later', 'send_message')}
    ends = sorted({f'__{n}' for key in ('stay_silent', 'reply_later') for n in names[key]})
    sends = sorted({f'__{n}' for n in names['send_message']})
    return ("import json,sys\n"
            "d=json.load(sys.stdin)\n"
            "n=str(d.get('tool_name',''))\n"
            "i=d.get('tool_input') or {}\n"
            f"if n.endswith(tuple({ends!r})) or (n.endswith(tuple({sends!r})) and i.get('done') is True):\n"
            "    print(json.dumps({'continue':False,'stopReason':'Peter ended his turn'}))\n"
            "else:\n"
            f"    print(json.dumps({{'hookSpecificOutput':{{'hookEventName':'PostToolUse','additionalContext':{REMINDER!r}}}}}))\n")


def prompt_hook_source():
    return ("import json,sys\nsys.stdin.read()\nprint(json.dumps({'hookSpecificOutput':"
            f"{{'hookEventName':'UserPromptSubmit','additionalContext':{REMINDER!r}}}}}))\n")


def minimal_env(extra=None):
    """Only what Claude Code needs. API keys and other credentials in VEIL's environment are not passed on, and a
    stray ANTHROPIC_API_KEY must never silently turn the subscription into API billing."""
    keep = ('PATH', 'PATHEXT', 'SYSTEMROOT', 'SystemRoot', 'COMSPEC', 'TEMP', 'TMP', 'USERPROFILE', 'HOME',
            'APPDATA', 'LOCALAPPDATA', 'PROGRAMFILES', 'PROGRAMFILES(X86)', 'PROGRAMDATA', 'PROGRAMW6432',
            'NUMBER_OF_PROCESSORS', 'PROCESSOR_ARCHITECTURE', 'LANG', 'TERM', 'USERNAME', 'USERDOMAIN', 'WINDIR')
    env = {k: v for k, v in os.environ.items() if k in keep}
    env.update(MCP_TIMEOUT='90000', MCP_TOOL_TIMEOUT='3600000', CLAUDE_CODE_SIMPLE_SYSTEM_PROMPT='0',
               ENABLE_TOOL_SEARCH='false')
    env.update(extra or {})
    return env


def _version_key(path):
    """Sort key from the version folder in .../claude-code/<version>/<hash>/claude.exe."""
    for part in reversed(Path(path).parts):
        if re.fullmatch(r'\d+(\.\d+)+', part):
            return tuple(int(n) for n in part.split('.'))
    return (0,)


def desktop_app_claudes(environ=None):
    """claude.exe copies the Claude desktop app keeps on Windows, newest version first. The version and hash folders
    change with every app update, so nobody should have to type them."""
    environ = os.environ if environ is None else environ
    patterns = []
    local, roaming = environ.get('LOCALAPPDATA'), environ.get('APPDATA')
    if local:
        patterns.append((Path(local), 'Packages/Claude_*/LocalCache/Roaming/Claude/claude-code/*/*/claude.exe'))
    if roaming:
        patterns.append((Path(roaming), 'Claude/claude-code/*/*/claude.exe'))
    found = []
    for base, pattern in patterns:
        try:
            found += [str(p) for p in base.glob(pattern) if p.is_file()]
        except OSError:
            continue
    return sorted(found, key=_version_key, reverse=True)


def find_claude(provider=None, env_file=None, environ=None):
    """A configured path wins when it exists; a wrong or stale one falls through to PATH and the desktop app's copy."""
    path = (provider or {}).get('claude_path') or (os.environ if environ is None else environ).get('VEIL_CLAUDE_PATH')
    if not path and env_file:
        try:
            from dotenv import dotenv_values
            path = (dotenv_values(env_file, interpolate=False, encoding='utf-8-sig').get('VEIL_CLAUDE_PATH') or '').strip()
        except Exception:
            path = ''
    path = (path or '').strip().strip('"')
    if path and (os.path.isfile(path) or shutil.which(path)):
        return path
    found = shutil.which('claude')
    if not found:
        candidates = desktop_app_claudes(environ)
        found = candidates[0] if candidates else None
    return found


def kill_tree(process):
    if process.poll() is not None:
        return
    try:
        if os.name == 'nt':
            subprocess.run(['taskkill', '/PID', str(process.pid), '/T', '/F'], capture_output=True, timeout=15)
        elif hasattr(os, 'killpg') and process.pid > 1:
            os.killpg(process.pid, signal.SIGKILL)  # the whole group: Claude Code's children too
        else:
            process.kill()
    except Exception:
        try:
            process.kill()
        except Exception:
            pass


def _process_group_options():
    return {'start_new_session': True} if os.name != 'nt' else {
        'creationflags': getattr(subprocess, 'DETACHED_PROCESS', 0) | getattr(subprocess, 'CREATE_NO_WINDOW', 0)}


class TurnState:
    """What one `claude -p` run is allowed to do and what it did."""

    def __init__(self, request_id, autonomy, offered):
        self.request_id = request_id
        self.autonomy = autonomy
        self.offered = offered
        self.tool_calls = 0
        self.signatures = {}
        self.kill_reason = ''
        self.waiting_for_lan = 0       # approval cards currently open
        self.waited = 0.0              # seconds spent waiting for Lan, not counted against the wall limit
        self.summaries = []

    def note_call(self, name, arguments):
        self.tool_calls += 1
        signature = name + '|' + json.dumps(arguments, sort_keys=True, ensure_ascii=False, default=str)
        self.signatures[signature] = self.signatures.get(signature, 0) + 1
        if self.signatures[signature] > REPEAT_LIMIT:
            self.kill_reason = f'同一个调用（{name}）重复了超过 {REPEAT_LIMIT} 次，已停止。'
        elif self.tool_calls > MAX_TOOL_CALLS:
            self.kill_reason = f'这一轮的工具调用超过 {MAX_TOOL_CALLS} 次，已停止。'
        return not self.kill_reason


class ToolServer:
    """A local-only HTTP endpoint inside the VEIL process. The bridge subprocess forwards Claude Code's tool calls
    here so the existing tool code runs with the existing state, permissions and logging."""

    def __init__(self, chat):
        self.chat = chat
        self.token = secrets.token_hex(24)
        self.state = None
        # VEIL's tools (signal handling in saving(), Windows UI automation) expect the thread that runs the chat
        # turn. HTTP handler threads only queue the work; run_turn executes it on the turn's own thread, one job
        # at a time, which also means one approval card at a time.
        self.jobs = queue.Queue()
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_POST(self):
                if self.headers.get('X-Veil-Token') != outer.token:
                    self.send_response(403)
                    self.end_headers()
                    return
                try:
                    length = int(self.headers.get('Content-Length') or 0)
                    body = json.loads(self.rfile.read(length) or b'{}')
                    if self.path == '/list':
                        payload = outer.list_tools()
                    elif self.path == '/call':
                        payload = outer.on_turn_thread(lambda: outer.call(body))
                    elif self.path == '/approve':
                        payload = outer.on_turn_thread(lambda: outer.approve(body))
                    else:
                        self.send_response(404)
                        self.end_headers()
                        return
                except Exception as exc:  # never let one bad call take the server down
                    payload = {'error': f'{type(exc).__name__}: {exc}'}
                data = json.dumps(payload, ensure_ascii=False).encode('utf-8')
                self.send_response(200)
                self.send_header('Content-Type', 'application/json; charset=utf-8')
                self.send_header('Content-Length', str(len(data)))
                self.end_headers()
                self.wfile.write(data)

        self.httpd = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        self.httpd.daemon_threads = True
        self.port = self.httpd.server_address[1]
        threading.Thread(target=self.httpd.serve_forever, daemon=True, name='claude-agent-tools').start()

    def close(self):
        self.httpd.shutdown()
        self.httpd.server_close()

    def on_turn_thread(self, work):
        """Called from a handler thread: run `work()` on the turn's thread and return its result."""
        if self.state is None:
            raise RuntimeError('No chat turn is running.')
        box = {'done': threading.Event()}

        def job():
            try:
                box['value'] = work()
            except BaseException as exc:  # noqa: BLE001 - handed back to the waiting handler
                box['error'] = exc
            finally:
                box['done'].set()

        self.jobs.put(job)
        while not box['done'].wait(0.5):
            if self.state is None:
                raise RuntimeError('The turn ended.')
        if 'error' in box:
            raise box['error']
        return box['value']

    # ---- endpoints ---------------------------------------------------
    def list_tools(self):
        state = self.state
        tools = []
        for schema in (state.offered if state and state.offered else []):
            function = schema.get('function', {})
            if not function.get('name'):
                continue
            tools.append({'name': function['name'], 'description': function.get('description', ''),
                          'inputSchema': function.get('parameters') or {'type': 'object', 'properties': {}}})
        return {'tools': tools}

    def call(self, body):
        from veil_agent import model_tool_result
        state = self.state
        name = str(body.get('name', ''))
        arguments = body.get('arguments') if isinstance(body.get('arguments'), dict) else {}
        if state is None:
            return {'text': 'No chat turn is running.', 'state': '被拒绝'}
        if state.kill_reason or not state.note_call(name, arguments):
            return {'text': 'The program stopped this turn: ' + state.kill_reason, 'state': '被拒绝'}
        call_id = body.get('id') or uuid.uuid4().hex
        call = SimpleNamespace(id=call_id, function=SimpleNamespace(
            name=name, arguments=json.dumps(arguments, ensure_ascii=False)))
        original = self.chat.approval

        def timed_approval(prompt):  # time spent waiting for Lan does not count against the wall clock
            started = time.monotonic()
            state.waiting_for_lan += 1
            try:
                return original(prompt)
            finally:
                state.waiting_for_lan -= 1
                state.waited += time.monotonic() - started

        self.chat.approval = timed_approval
        try:
            execution = self.chat._execute_tool_call(call, state.request_id, call_id, state.autonomy, state.offered)
        finally:
            self.chat.approval = original
        text = model_tool_result(name, execution.result, execution.state)
        text = text if isinstance(text, str) else str(text)
        self.chat.store.append(f'{self.chat.cfg.id}/tools.jsonl', {
            'request_id': state.request_id, 'tool': name, 'path': name, 'state': execution.state,
            'time': _now(), 'call_id': call_id, 'channel': CHANNEL_KIND})
        state.summaries.append(str(execution.summary or name))
        return {'text': text, 'state': execution.state}

    def approve(self, body):
        state = self.state
        prompt = describe_request(str(body.get('tool_name', '')), body.get('input'))
        started = time.monotonic()
        if state is not None:
            state.waiting_for_lan += 1
        try:
            if state is not None and (state.kill_reason or self.chat.user_stop.is_set()):
                answer = 'n'
            else:
                answer = self.chat.approval(prompt)
        finally:
            if state is not None:
                state.waiting_for_lan -= 1
                state.waited += time.monotonic() - started
        allowed = isinstance(answer, str) and answer.strip().lower().startswith(('y', '同意'))
        if allowed:
            return {'behavior': 'allow', 'updatedInput': body.get('input') if isinstance(body.get('input'), dict) else {}}
        return {'behavior': 'deny', 'message': 'Lan said no. Do not try another way around it; tell her what you could not do.'}


def _now():
    from agent_types import now
    return now()


class ClaudeAgentChannel:
    def __init__(self, chat, run_dir, provider=None, popen=subprocess.Popen):
        self.chat = chat
        self.run_dir = Path(run_dir)
        self.provider = provider or {}
        self.popen = popen
        self.server = None

    @staticmethod
    def _env_file():
        return Path(__file__).resolve().parent / '.env'

    def ensure_server(self):
        if self.server is None:
            self.server = ToolServer(self.chat)
        return self.server

    # ---- files Claude Code reads from the run directory -----------------
    def prepare(self, system_text):
        project = self.run_dir / 'project'
        (project / '.claude' / 'output-styles').mkdir(parents=True, exist_ok=True)
        style = ('---\nname: Peter\ndescription: Peter, texting Lan\nkeep-coding-instructions: true\n---\n\n'
                 + system_text)
        (project / '.claude' / 'output-styles' / 'peter.md').write_text(style, encoding='utf-8')
        (self.run_dir / 'hook_prompt.py').write_text(prompt_hook_source(), encoding='utf-8')
        (self.run_dir / 'hook_tool.py').write_text(
            stop_hook_source(getattr(self.chat, 'tool_aliases', None)), encoding='utf-8')
        hook = lambda name: [{'hooks': [{'type': 'command', 'command': f'"{sys.executable}" "{self.run_dir / name}"'}]}]
        (project / '.claude' / 'settings.json').write_text(json.dumps({
            'outputStyle': 'Peter',
            'permissions': {'deny': DENY_RULES},
            'hooks': {'UserPromptSubmit': hook('hook_prompt.py'), 'PostToolUse': hook('hook_tool.py')}}),
            encoding='utf-8')
        server = self.ensure_server()
        mcp = {'mcpServers': {SERVER_NAME: {
            'command': sys.executable, 'args': [str(BRIDGE_SCRIPT)],
            'env': {'VEIL_CC_URL': f'http://127.0.0.1:{server.port}', 'VEIL_CC_TOKEN': server.token}}}}
        (self.run_dir / 'mcp.json').write_text(json.dumps(mcp), encoding='utf-8')
        return project

    def command(self, claude, extra_dirs=()):
        cmd = [claude, '-p', '--output-format', 'stream-json', '--verbose',
               '--model', self.provider.get('model') or 'sonnet',
               '--mcp-config', str(self.run_dir / 'mcp.json'), '--setting-sources', 'project',
               '--max-turns', str(MAX_TURNS), '--max-budget-usd', str(MAX_BUDGET_USD),
               '--permission-mode', 'acceptEdits', '--permission-prompt-tool', APPROVE_TOOL,
               '--allowedTools', f'mcp__{SERVER_NAME}']
        for directory in extra_dirs:
            cmd += ['--add-dir', str(directory)]
        return cmd

    # ---- one chat turn ---------------------------------------------------
    def run_turn(self, system_text, prompt, request_id, offered, autonomy=False, extra_dirs=()):
        """Returns (summaries, result_event). Raises RuntimeError with a message for Lan if the run cannot happen."""
        claude = find_claude(self.provider, self._env_file())
        if not claude:
            raise RuntimeError('找不到 claude 命令。请先在这台电脑上安装并登录它，或在 .env 里设置 VEIL_CLAUDE_PATH。')
        try:
            project = self.prepare(system_text)
            server = self.ensure_server()
        except OSError as exc:  # nothing has run yet, so this is a safe, plain failure
            raise RuntimeError(f'Claude Code 的运行目录准备失败：{exc}') from exc
        state = TurnState(request_id, autonomy, offered)
        server.state = state
        group = _process_group_options()
        try:
            process = self.popen(self.command(claude, extra_dirs), stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                 stderr=subprocess.PIPE, cwd=project, env=minimal_env(), text=True,
                                 encoding='utf-8', **group)
        except OSError as exc:
            server.state = None
            raise RuntimeError(f'没能启动 claude：{exc}') from exc
        stderr_chunks = []
        threading.Thread(target=lambda: stderr_chunks.append(process.stderr.read()), daemon=True).start()
        threading.Thread(target=self._feed, args=(process, prompt), daemon=True).start()
        started = time.monotonic()
        stop_watch = threading.Event()

        def watch():
            while not stop_watch.wait(0.5):
                active = time.monotonic() - started - state.waited
                if self.chat.user_stop.is_set() and not state.kill_reason:
                    state.kill_reason = 'Lan 叫停了这一轮。'
                elif active > MAX_WALL_SECONDS and not state.waiting_for_lan and not state.kill_reason:
                    state.kill_reason = f'这一轮超过 {MAX_WALL_SECONDS} 秒，已停止。'
                if state.kill_reason:
                    kill_tree(process)
                    return

        watcher = threading.Thread(target=watch, daemon=True)
        watcher.start()
        outcome = {'result': None}

        def read_output():
            try:
                for line in process.stdout:
                    try:
                        event = json.loads(line)
                    except ValueError:
                        continue
                    if event.get('type') == 'assistant':
                        for block in event.get('message', {}).get('content', []):
                            if block.get('type') == 'tool_use' and not str(block.get('name', '')).startswith('mcp__'):
                                server.jobs.put(lambda block=block: self._log_native(state, block))
                    elif event.get('type') == 'result':
                        outcome['result'] = event
            finally:
                server.jobs.put(None)

        threading.Thread(target=read_output, daemon=True, name='claude-agent-output').start()
        try:
            while True:  # this thread runs the tool calls Claude Code makes, until its output ends
                job = server.jobs.get()
                if job is None:
                    break
                job()
            try:
                process.wait(timeout=30)
            except subprocess.TimeoutExpired:
                kill_tree(process)
        finally:
            stop_watch.set()
            kill_tree(process)
            server.state = None
            while not server.jobs.empty():
                server.jobs.get_nowait()
        result = outcome['result']
        stderr = ''.join(stderr_chunks)
        self.last_stderr = stderr
        if state.kill_reason:
            raise RuntimeError(state.kill_reason)
        if result is None:
            raise RuntimeError('Claude Code 没有返回结果：' + (stderr.strip()[-300:] or f'退出码 {process.returncode}'))
        text = str(result.get('result') or '')
        if result.get('is_error') and USAGE_LIMIT.search(text):
            raise RuntimeError('Claude 订阅额度用完了：' + text[:200] + '。可以在模型页换回其他模型。')
        if result.get('is_error') and result.get('subtype') not in ('error_max_turns',):
            raise RuntimeError('Claude Code 出错：' + text[:300])
        return state.summaries, result

    @staticmethod
    def _feed(process, prompt):
        try:
            process.stdin.write(prompt)
            process.stdin.close()
        except Exception:
            pass

    def _log_native(self, state, block):
        name = str(block.get('name', ''))
        state.note_call(name, block.get('input') or {})
        state.summaries.append(name)
        self.chat.store.append(f'{self.chat.cfg.id}/tools.jsonl', {
            'request_id': state.request_id, 'tool': name, 'path': name, 'state': '已请求',
            'time': _now(), 'call_id': block.get('id'), 'channel': CHANNEL_KIND,
            'detail': ''})  # the command itself may hold credentials; Lan saw it on the approval card


def build_prompt(messages):
    """VEIL's own message list -> (system text for the output style, the one prompt for this turn).

    The persona, facts and notes are the system messages in front of the history; the history becomes a short
    transcript, and the system messages that follow it (memories, time, capability notice) stay as notes."""
    leading, rest = [], list(messages)
    while rest and rest[0].get('role') == 'system':
        leading.append(rest.pop(0))
    user = rest.pop() if rest and rest[-1].get('role') == 'user' else {'content': ''}
    history = [m for m in rest if m.get('role') in ('user', 'assistant')]
    notes = [m for m in rest if m.get('role') == 'system']
    system = '\n\n'.join(_text(m) for m in leading)
    system += ('\n\n# How your messages reach Lan\nShe reads only what you send with the send_message tool, one short '
               'bubble per call; call it several times in one step to send several bubbles. Text you write outside '
               'send_message is not shown to her. When you are done, end with no text or set done to true on your '
               'last message.')
    parts = []
    if history:
        parts.append('# Recent chat with Lan (most recent last)\n' + '\n'.join(
            ('Lan: ' if m['role'] == 'user' else 'Peter: ') + _text(m)[:1500] for m in history))
    if notes:
        parts.append('# Notes for this moment\n' + '\n\n'.join(_text(m) for m in notes))
    parts.append('# Lan just wrote\n' + _text(user))
    return system, '\n\n'.join(parts)


def _text(message):
    content = message.get('content', '')
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return ''.join(part.get('text', '') for part in content if isinstance(part, dict) and part.get('type') == 'text')
    return str(content)
