"""Ticket 329: VEIL's single permission checkpoint for the host execution path.

Every real operation of a host workbench run is ruled here, in one place, before it runs:
- OpenCode's own permission requests (shell, edits, paths outside the project, web fetches), and
- every tool call that reaches the desktop (Windows-MCP) or the background browser (Playwright MCP) through
  VEIL's MCP gateway (`veil_mcp_gateway.py`). OpenCode never talks to those servers directly.

Each operation belongs to one of four classes (computer, files_code, network_browser, system) and gets one of
three answers: allow, ask Lan (approval card), or deny. Every decision and every tool error text is written to
one run log. Limits, budget and stop stay in `executor_opencode.run` (one loop, one place).
"""
from datetime import datetime, timezone
import fnmatch
import json
import os
from pathlib import Path
import re
import shlex
import threading

import executor_opencode as ex
from web_tools import public_http_url
from windows_observer import BROWSER_PROCESSES, PROTECTED_PROCESSES, SENSITIVE_LABEL

COMPUTER, FILES_CODE, NETWORK_BROWSER, SYSTEM = 'computer', 'files_code', 'network_browser', 'system'
CLASSES = (COMPUTER, FILES_CODE, NETWORK_BROWSER, SYSTEM)
ALLOW, ASK, DENY = 'allow', 'ask', 'deny'

# MCP servers VEIL offers to OpenCode, and the upstream tools each exposes. Anything not listed is never offered.
COMPUTER_SERVER, BROWSER_SERVER = 'veil_computer', 'veil_browser'
COMPUTER_TOOLS = frozenset({  # Windows-MCP 0.8.8 (PowerShell, FileSystem, Registry, Clipboard, Scrape: not offered)
    'Snapshot', 'Screenshot', 'App', 'Click', 'Type', 'Scroll', 'Move', 'Shortcut', 'Wait', 'WaitFor',
    'Process', 'DisplayInventory', 'ControlStatus'})
BROWSER_TOOLS = frozenset({  # Playwright MCP 0.0.84 (evaluate, run_code, file upload, storage, network: not offered)
    'browser_navigate', 'browser_navigate_back', 'browser_snapshot', 'browser_click', 'browser_type',
    'browser_press_key', 'browser_hover', 'browser_select_option', 'browser_wait_for', 'browser_tabs',
    'browser_find', 'browser_take_screenshot', 'browser_close', 'browser_handle_dialog', 'browser_fill_form'})

# Keys whose meaning is "save, send, submit, close or delete" ask Lan first.
COMMIT_SHORTCUT = re.compile(
    r'^(?:ctrl\+(?:shift\+)?s|ctrl\+enter|ctrl\+return|alt\+s|alt\+f4|ctrl\+w|enter|return|'
    r'(?:shift\+)?(?:delete|del)|ctrl\+d|ctrl\+p|win\+r|win\+x|win\+l|ctrl\+shift\+(?:esc|escape|delete|del)|'
    r'ctrl\+alt\+(?:delete|del))$', re.I)
# Shortcuts that run the selected program as administrator (Start search / Run box): never.
ELEVATION_SHORTCUT = re.compile(r'^(?:ctrl\+shift\+(?:enter|return)|shift\+ctrl\+(?:enter|return))$', re.I)
ELEVATION = re.compile(
    r'(?:\brunas\b|-verb\s+runas|\bsudo\b|\bgsudo\b|\bpsexec|\bschtasks\b|set-mppreference|'
    r'disable-?(?:realtime|windowsdefender)|\bbcdedit\b|\bdiskpart\b|\bformat(?:\.com)?\s+[a-z]:|'
    r'\bvssadmin\b|\bnetsh\s+advfirewall|\bsc(?:\.exe)?\s+(?:stop|delete|config)\b|reg(?:\.exe)?\s+(?:add|delete)|'
    r'set-executionpolicy|\bcipher\s+/w|\btakeown\b|\bicacls\b)', re.I)
ELEVATED_PROGRAMS = frozenset({'regedit.exe', 'regedit', 'mmc.exe', 'gpedit.msc', 'secpol.msc', 'cmd.exe', 'cmd',
                               'powershell.exe', 'powershell', 'pwsh.exe', 'pwsh', 'taskmgr.exe', 'msconfig.exe',
                               'diskpart.exe', 'bcdedit.exe', 'wscript.exe', 'cscript.exe', 'mshta.exe'})
CONTENT_READERS = frozenset({'grep', 'egrep', 'fgrep', 'rg', 'ag', 'ack', 'cat', 'type', 'head', 'tail', 'more',
                             'less', 'sort', 'uniq', 'strings', 'xxd', 'od', 'hexdump', 'base64', 'findstr',
                             'select-string', 'sls', 'get-content', 'gc', 'tar', 'zip', '7z', 'cp', 'copy',
                             'xcopy', 'robocopy', 'scp', 'rsync', 'awk', 'sed', 'jq', 'diff', 'cmp', 'md5sum',
                             'sha256sum', 'git'})
RECURSIVE_FLAG = re.compile(r'^-(?:[a-zA-Z]*[rR][a-zA-Z]*|-recursive|-dereference-recursive|recurse|s)$')
BROWSER_PASSWORD_FIELD = re.compile(r'password|passwd|密码|口令|pin\b|otp|验证码|card\s*number|cvv|cvc|卡号', re.I)


SENSITIVE_ARGUMENTS = frozenset({'text', 'value', 'values', 'fields', 'shortcut_text', 'code', 'function'})


def _expand(text):
    """Environment variables in any shell syntax ($env:NAME, %NAME%, $NAME, ${NAME}, ~) resolved for path checks."""
    text = re.sub(r'\$env:([A-Za-z_][A-Za-z0-9_]*)', lambda m: os.environ.get(m.group(1), m.group(0)), text, flags=re.I)
    text = re.sub(r'%([A-Za-z_][A-Za-z0-9_]*)%', lambda m: os.environ.get(m.group(1), m.group(0)), text)
    return os.path.expanduser(os.path.expandvars(text))


def windows_safe(command):
    """A host command with backslashes as slashes, so POSIX splitting keeps Windows paths intact for the checks."""
    return str(command).replace('\\', '/')


def redacted(arguments):
    """Tool arguments for the log, without what Peter typed or filled in."""
    return {key: (f'[{len(str(value))} chars]' if key in SENSITIVE_ARGUMENTS else value)
            for key, value in (arguments or {}).items()}


def _now():
    return datetime.now(timezone.utc).isoformat(timespec='seconds')


def _norm(path):
    return os.path.normcase(os.path.normpath(str(path)))


def _forms(path):
    """The normalized path as given and as resolved (they differ for links and Windows short names)."""
    forms = [_norm(path)]
    try:
        forms.append(_norm(path.resolve()))
    except (OSError, RuntimeError, ValueError):
        pass
    return list(dict.fromkeys(forms))


def forbidden_roots(veil_root, home=None):
    """Paths no host run may touch: VEIL's own data (memory, ledgers, logs), keys, SSH and browser profiles."""
    veil_root = Path(veil_root)
    home = Path(home or Path.home())
    local = Path(os.environ.get('LOCALAPPDATA') or home / 'AppData' / 'Local')
    roaming = Path(os.environ.get('APPDATA') or home / 'AppData' / 'Roaming')
    return [veil_root / 'data', veil_root / '.env', veil_root.parent / 'secrets', home / '.ssh',
            local / 'Google' / 'Chrome' / 'User Data', local / 'Microsoft' / 'Edge' / 'User Data',
            local / 'BraveSoftware', roaming / 'Mozilla' / 'Firefox' / 'Profiles', roaming / 'Opera Software',
            local / 'Microsoft' / 'Credentials', roaming / 'Microsoft' / 'Credentials',
            roaming / 'Microsoft' / 'Protect']


def foreground_window():
    """(process name, title) of the foreground window on Windows; None elsewhere or when it cannot be read."""
    if os.name != 'nt':
        return None
    try:
        import ctypes
        from ctypes import wintypes
        user32, kernel32 = ctypes.windll.user32, ctypes.windll.kernel32
        hwnd = user32.GetForegroundWindow()
        if not hwnd:
            return None
        buffer = ctypes.create_unicode_buffer(512)
        user32.GetWindowTextW(hwnd, buffer, 512)
        title = buffer.value
        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        handle = kernel32.OpenProcess(0x1000, False, pid.value)  # PROCESS_QUERY_LIMITED_INFORMATION
        process = ''
        if handle:
            try:
                size = wintypes.DWORD(1024)
                path = ctypes.create_unicode_buffer(1024)
                if kernel32.QueryFullProcessImageNameW(handle, 0, path, ctypes.byref(size)):
                    process = Path(path.value).name
            finally:
                kernel32.CloseHandle(handle)
        return process, title
    except Exception:
        return None


class Checkpoint:
    """One host run's permission checkpoint. `ask(prompt)` is Lan's approval card ('y' approves)."""

    def __init__(self, workdir, *, ask, autonomy, log_path, forbidden=(), task_scope=None, allow_network=None,
                 foreground=foreground_window, protect=()):
        self.workdir = Path(workdir).resolve()
        self.ask = ask
        self.autonomy = bool(autonomy)
        self.log_path = Path(log_path)
        # Both the given and the resolved form: Windows may report one folder by its short (8.3) or long name.
        self.forbidden = list(dict.fromkeys(form for path in forbidden for form in _forms(Path(path))))
        self.task_scope = task_scope
        self.allow_network = allow_network
        self.foreground = foreground
        self.protect = [_norm(self.workdir / item) for item in protect]
        self.approvals = []
        self.grants, self.denied = set(), set()
        self.lock = threading.Lock()
        try:
            self.secret_files = [_norm(path.resolve()) for path in ex.secret_files(self.workdir)]
            self.secret_dirs = [_norm(path.resolve()) for path in ex.secret_directories(self.workdir)]
            self.refusal = ''
        except (OSError, ValueError) as exc:
            # A link out of the project would let OpenCode's own reads leave it unchecked: no host run then.
            self.secret_files, self.secret_dirs = None, None
            self.refusal = 'The project cannot run on the host: ' + str(exc)[:200]
        if any(self._inside(root, _norm(self.workdir)) for root in self.forbidden):
            self.refusal = "The project contains VEIL's own data or keys; it cannot run on the host."


    # ---- log -------------------------------------------------------------------------------------------
    def log(self, **event):
        event = {'time': _now(), **event}
        with self.lock:
            try:
                self.log_path.parent.mkdir(parents=True, exist_ok=True)
                with self.log_path.open('a', encoding='utf-8') as handle:
                    handle.write(json.dumps(event, ensure_ascii=False, default=str)[:8000] + '\n')
            except OSError:
                pass

    def _record(self, cls, operation, answer, reason):
        self.log(kind='decision', cls=cls, operation=ex._safe_display(operation)[:500], decision=answer, reason=reason)
        return answer, reason

    # ---- paths -----------------------------------------------------------------------------------------
    def _resolve(self, value, current=None):
        return (self._candidates(value, current) or [None])[-1]

    def _candidates(self, value, current=None):
        """The path as written (made absolute) and as resolved; [] for an option or empty text."""
        text = windows_safe(str(value).strip().strip('"\''))
        if not text or text.startswith('-'):
            return []
        path = Path(_expand(text))
        if not path.is_absolute():
            path = Path(current or self.workdir) / path
        return _forms(path)

    def _inside(self, path, root):
        return path == root or path.startswith(root.rstrip(os.sep) + os.sep)

    def forbidden_path(self, value, current=None):
        """Why `value` must never be read or written, or ''."""
        name = Path(windows_safe(str(value).strip().strip('"\''))).name
        if name and ex.SECRET_FILE.match(name):
            return 'secret file'
        if re.search(r'\$env:|%[A-Za-z_][A-Za-z0-9_]*%|\$\{?[A-Za-z_]', str(value), re.I) and \
                re.search(r'\$env:|%[A-Za-z_][A-Za-z0-9_]*%|\$\{?[A-Za-z_]', _expand(str(value)), re.I):
            return 'unresolved environment variable in a path'
        paths = self._candidates(value, current)
        if not paths:
            return ''
        works = _forms(self.workdir)
        for path in paths:
            inside_work = any(self._inside(path, work) for work in works)
            for root in self.forbidden:
                if self._inside(path, root) and not inside_work:
                    return 'VEIL data, keys or browser passwords'
                # A project inside VEIL's data (Peter's own workspace) may still not reach memory or ledgers.
                if any(self._inside(work, root) for work in works) and not inside_work and self._inside(path, root):
                    return 'VEIL data, keys or browser passwords'
            parts = {part.casefold() for part in Path(path).parts}
            if parts & {'.ssh', 'secrets', '.aws', '.azure', '.gnupg', '.kube'} or \
                    'credentials' in Path(path).name.casefold():
                return 'secret folder'
            if any(path == item for item in self.protect):
                return 'protected file'
            if self.secret_files and path in self.secret_files:
                return 'secret file'
        return ''

    def _reads_secrets_recursively(self, tokens, current=None):
        """A recursive or wildcard read (grep -r, rg, cat *) whose target holds secret files."""
        program = ex._command_name(tokens[0]) if tokens else ''
        if program not in CONTENT_READERS:
            return False
        if program == 'git' and ex._git_subcommand(tokens) != 'grep':
            return False
        recursive = program in {'rg', 'ag', 'ack', 'tar', 'zip', '7z', 'robocopy', 'rsync', 'scp'} or any(
            RECURSIVE_FLAG.match(arg) for arg in tokens[1:])
        targets = [arg for arg in tokens[1:] if not arg.startswith('-')]
        wildcard = [arg for arg in targets if any(ch in arg for ch in '*?[')]
        if not recursive and not wildcard:
            return False
        if self.secret_files is None:
            return True
        secrets_all = self.secret_files + (self.secret_dirs or [])
        if not secrets_all:
            return False
        for pattern in wildcard:
            base = self._resolve(pattern, current) or ''
            if any(fnmatch.fnmatch(item, base) or fnmatch.fnmatch(os.path.basename(item), os.path.basename(pattern))
                   for item in secrets_all):
                return True
        if recursive:
            dirs = [self._resolve(arg, current) for arg in targets]
            dirs = [item for item in dirs if item and os.path.isdir(item)] or [_norm(current or self.workdir)]
            return any(self._inside(item, base) for base in dirs for item in secrets_all)
        return False

    # ---- approval --------------------------------------------------------------------------------------
    def _ask(self, cls, prompt, key, operation):
        if self.autonomy:
            return self._record(cls, operation, DENY, 'needs Lan; not asked during autonomous activity')
        reply, why = ex._ask_lan(prompt, operation, key, self.ask, self.grants, self.approvals, self.denied)
        return self._record(cls, operation, ALLOW if reply == 'once' else DENY, why)

    # ---- OpenCode's own permission requests -------------------------------------------------------------
    @staticmethod
    def classify_permission(permission, patterns, metadata):
        if permission in ('webfetch', 'websearch', 'codesearch'):
            return NETWORK_BROWSER
        if permission == 'bash':
            command = (metadata or {}).get('command') if isinstance(metadata, dict) else None
            text = command if isinstance(command, str) else ' '.join(map(str, patterns or ()))
            try:
                tokens = shlex.split(text, posix=True)
            except ValueError:
                return SYSTEM
            program = ex._command_name(tokens[0]) if tokens else ''
            if program in ex.ASK_LAN['network'] or program == 'git' and ex._git_subcommand(tokens) in {
                    'push', 'clone', 'fetch', 'pull'}:
                return NETWORK_BROWSER
            if program in ex.ASK_LAN['installers'] or ELEVATION.search(text) or program in {
                    'taskkill', 'kill', 'stop-process', 'shutdown', 'restart-computer', 'sc', 'net', 'reg',
                    'wmic', 'bcdedit', 'powershell', 'pwsh', 'cmd', 'start-process'}:
                return SYSTEM
            return FILES_CODE
        return FILES_CODE

    def opencode_permission(self, permission, patterns, metadata, **decide_kwargs):
        """(reply, reason) for one OpenCode permission request; reply is 'once' or 'reject'."""
        patterns = patterns if isinstance(patterns, list) else []
        metadata = metadata if isinstance(metadata, dict) else {}
        if permission == 'bash':
            # Windows paths keep their meaning for every check below (POSIX splitting drops backslashes).
            patterns = [windows_safe(item) if isinstance(item, str) else item for item in patterns]
            if isinstance(metadata.get('command'), str):
                metadata = {**metadata, 'command': windows_safe(metadata['command'])}
        cls = self.classify_permission(permission, patterns, metadata)
        command = metadata.get('command') if isinstance(metadata, dict) else None
        operation = permission + ': ' + (command if isinstance(command, str) else ', '.join(map(str, patterns)))
        if permission.startswith((COMPUTER_SERVER + '_', BROWSER_SERVER + '_')):
            # VEIL's own gateway rules these with the real arguments (OpenCode sends none here).
            return 'once', 'ruled by the VEIL gateway'
        if permission not in ('bash', 'edit', 'write', 'read', 'glob', 'external_directory', 'webfetch', 'task',
                              'todowrite', 'skill', 'doom_loop'):
            self._record(cls, operation, DENY, 'not offered on the host path')
            return 'reject', 'not offered on the host path'
        text = ' '.join(map(str, patterns)) + ' ' + (command if isinstance(command, str) else '')
        if ELEVATION.search(text):
            self._record(SYSTEM, operation, DENY, 'elevation or system protection change')
            return 'reject', 'elevation or system protection change'
        tokens = []
        if permission == 'bash':
            try:
                tokens = shlex.split(command if isinstance(command, str) else ' '.join(map(str, patterns)), posix=True)
            except ValueError:
                tokens = []
            for token in tokens:
                for piece in re.split(r'[;&|<>]+', token):
                    if piece and (why := self.forbidden_path(piece)):
                        self._record(cls, operation, DENY, why)
                        return 'reject', why
            for segment in re.split(r'\s*(?:&&|\|\||;|\|)\s*', command if isinstance(command, str) else ''):
                try:
                    part = shlex.split(segment, posix=True)
                except ValueError:
                    continue
                if part and self._reads_secrets_recursively(part):
                    why = 'recursive or wildcard read over a folder with secret files; name the files instead'
                    self._record(cls, operation, DENY, why)
                    return 'reject', why
        else:
            for pattern in patterns:
                if why := self.forbidden_path(pattern):
                    self._record(cls, operation, DENY, why)
                    return 'reject', why
            for key in ('filepath', 'filePath', 'path'):
                if isinstance(metadata.get(key), str) and (why := self.forbidden_path(metadata[key])):
                    self._record(cls, operation, DENY, why)
                    return 'reject', why
        reply, why = ex.decide(permission, patterns, metadata, self.workdir, autonomy=self.autonomy, ask=self.ask,
                               approvals=self.approvals, task_scope=self.task_scope,
                               allow_network=self.allow_network, **decide_kwargs)
        self._record(cls, operation, ALLOW if reply == 'once' else DENY, why)
        return reply, why

    # ---- desktop and browser tool calls (VEIL MCP gateway) -----------------------------------------------
    def _sensitive_foreground(self, typing=False):
        window = self.foreground() if self.foreground else None
        if not window:
            # Typing blind could land in a password field; on Windows that is refused.
            return 'the window in front cannot be read' if typing and os.name == 'nt' else ''
        process, title = window
        if str(process).casefold() in PROTECTED_PROCESSES or SENSITIVE_LABEL.search(str(title or '')):
            return 'a password, security or credential window is in front'
        if typing and str(process).casefold() in BROWSER_PROCESSES:
            return "typing into Lan's own browser (her accounts); use the background browser instead"
        if re.search(r'用户帐户控制|用户账户控制|user account control|支付|付款|收银台|checkout|payment', str(title or ''), re.I):
            return 'a security or payment window is in front'
        return ''

    def tool_call(self, server, name, arguments):
        """(allowed: bool, reason) for one desktop/browser tool call with its real arguments."""
        arguments = arguments if isinstance(arguments, dict) else {}
        operation = f'{server}.{name} ' + json.dumps(redacted(arguments), ensure_ascii=False)[:300]
        if server == COMPUTER_SERVER:
            answer, why = self._computer(name, arguments, operation)
        elif server == BROWSER_SERVER:
            answer, why = self._browser(name, arguments, operation)
        else:
            answer, why = self._record(SYSTEM, operation, DENY, 'unknown server')
        return answer == ALLOW, why

    def _computer(self, name, args, operation):
        if name not in COMPUTER_TOOLS:
            return self._record(COMPUTER, operation, DENY, 'not offered')
        if name in {'Snapshot', 'Screenshot', 'Wait', 'WaitFor', 'DisplayInventory', 'ControlStatus'}:
            return self._record(COMPUTER, operation, ALLOW, 'look')
        if name == 'Process':
            if args.get('mode') == 'list':
                return self._record(SYSTEM, operation, ALLOW, 'list processes')
            return self._ask(SYSTEM, f'Peter 想结束进程：{ex._safe_display(args.get("name") or args.get("pid"))}。同意吗？y/N ',
                             ('process', 'kill', str(args.get('name') or args.get('pid'))), 'process:kill')
        if name == 'App':
            mode = args.get('mode', 'launch')
            target = str(args.get('executable') or args.get('name') or '')
            if Path(target).name.casefold() in ELEVATED_PROGRAMS or ELEVATION.search(target):
                return self._record(SYSTEM, operation, DENY, 'system tool or shell')
            if why := (self.forbidden_path(target) if mode == 'launch_executable' else ''):
                return self._record(SYSTEM, operation, DENY, why)
            if mode == 'launch_executable':
                return self._ask(SYSTEM, f'Peter 想直接运行程序：{ex._safe_display(target)}。同意吗？y/N ',
                                 ('app', 'exe', _norm(target)), 'app:launch_executable')
            if mode in {'launch', 'resize', 'switch'}:
                return self._record(COMPUTER, operation, ALLOW, 'open or arrange an app')
            return self._record(COMPUTER, operation, DENY, 'unknown app mode')
        # Mouse and keyboard: never into password, security or payment windows, nor typing into her browser.
        if why := self._sensitive_foreground(typing=name in {'Type', 'Shortcut'}):
            return self._record(COMPUTER, operation, DENY, why)
        if name in {'Scroll', 'Move', 'Click'}:
            return self._record(COMPUTER, operation, ALLOW, 'pointer')
        if name == 'Type':
            if str(args.get('press_enter', '')).casefold() in {'true', '1'}:
                return self._ask(COMPUTER, 'Peter 想输入文字并按回车提交（可能发送或提交内容）。同意吗？y/N ',
                                 ('type', 'submit'), 'type:submit')
            return self._record(COMPUTER, operation, ALLOW, 'type')
        if name == 'Shortcut':
            keys = re.sub(r'\s+', '', str(args.get('shortcut', ''))).casefold()
            if ELEVATION_SHORTCUT.match(keys):
                return self._record(SYSTEM, operation, DENY, 'runs a program as administrator')
            if COMMIT_SHORTCUT.match(keys):
                return self._ask(COMPUTER, f'Peter 想按 {ex._safe_display(keys)}（可能保存、发送、关闭或删除）。同意吗？y/N ',
                                 ('shortcut', keys), 'shortcut:' + keys)
            return self._record(COMPUTER, operation, ALLOW, 'shortcut')
        return self._record(COMPUTER, operation, DENY, 'not offered')

    def _browser(self, name, args, operation):
        if name not in BROWSER_TOOLS:
            return self._record(NETWORK_BROWSER, operation, DENY, 'not offered')
        if 'filename' in args:
            # Playwright MCP writes such files relative to its own folder; results come back as text instead.
            return self._record(NETWORK_BROWSER, operation, DENY, 'no files from the browser; read results as text')
        if args.get('isolatedContext') not in (None, '', 'default'):
            return self._record(NETWORK_BROWSER, operation, DENY, 'one browser context per run')
        if name == 'browser_navigate' or name == 'browser_tabs' and args.get('url') not in (None, ''):
            url = public_http_url(str(args.get('url', '')))
            if not url:
                return self._record(NETWORK_BROWSER, operation, DENY, 'only public http(s) pages')
            return self._record(NETWORK_BROWSER, operation, ALLOW, 'public page')
        if name in {'browser_type', 'browser_fill_form'}:
            fields = args.get('fields') if name == 'browser_fill_form' else [args]
            for field in fields if isinstance(fields, list) else []:
                label = ' '.join(str((field or {}).get(key, '')) for key in ('element', 'name', 'ref', 'type'))
                if BROWSER_PASSWORD_FIELD.search(label):
                    return self._record(NETWORK_BROWSER, operation, DENY, 'password, code or payment field')
        # The browser has a fresh in-memory profile: no accounts are signed in, so clicks and typing are research.
        return self._record(NETWORK_BROWSER, operation, ALLOW, 'background browser')
