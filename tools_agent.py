"""Bounded MCP filesystem access and application-enforced tool policy."""
import asyncio
import hashlib
import json
import os
import re
import shutil
import stat
import subprocess
import threading
from concurrent.futures import Future
from pathlib import Path, PureWindowsPath
from types import SimpleNamespace


VERSION = 1
ALLOW = {
    'list_directory', 'directory_tree', 'read_text_file', 'read_multiple_files',
    'get_file_info', 'search_files', 'list_directory_with_sizes',
}
ASK = {'write_file', 'append_file', 'grant_append'}
DENY = {'edit_file', 'move_file'}
LAB_ONLY = {'create_directory'}
# Memory tools (ticket 007) are offered by memory_native.ToolSet, never by the filesystem server.
NATIVE = {'hold', 'breath_search'}


def permitted_tools(server_tools):
    """Convert only application-allowlisted MCP tools to OpenAI tool schemas."""
    result = []
    for tool in server_tools:
        if tool.name not in ALLOW | ASK | NATIVE | LAB_ONLY:
            continue
        result.append({'type': 'function', 'function': {
            'name': tool.name, 'description': tool.description or tool.name,
            'parameters': tool.inputSchema,
        }})
    return result


def tool_policy(name):
    if name in LAB_ONLY:
        return 'lab'
    if name in DENY or name not in ALLOW | ASK:
        return 'deny'
    return 'ask' if name in ASK else 'allow'


def lab_name_problem(name):
    """Use Windows filename rules on every platform, including config names."""
    if not isinstance(name, str) or not name or name in ('.', '..'):
        return '名称必须是单级文件或文件夹名'
    if any(ord(c) < 32 or c in '<>:"|?*/\\' for c in name) or name.endswith((' ', '.')):
        return '名称含 Windows 非法字符或以空格、点结尾'
    stem = name.split('.')[0].upper()
    if stem in {'CON', 'PRN', 'AUX', 'NUL', 'CONIN$', 'CONOUT$'} or re.fullmatch(r'(COM|LPT)[1-9¹²³]', stem):
        return '名称是 Windows 保留名'
    return None


def _redirected(path):
    info = path.lstat()
    return stat.S_ISLNK(info.st_mode) or bool(
        getattr(info, 'st_file_attributes', 0) & getattr(stat, 'FILE_ATTRIBUTE_REPARSE_POINT', 0x400))


def _path_key(path):
    value = os.path.normcase(os.path.normpath(str(path)))
    if os.name == 'nt':
        value = value.removeprefix('\\\\?\\').removeprefix('\\\\.\\')
    return value.rstrip('\\/')


def checked_path(root, value):
    if not isinstance(value, str) or not value.strip() or '\x00' in value:
        raise ValueError('invalid path')
    # Device namespace paths bypass ordinary Windows path normalization.
    if value.startswith(('\\\\?\\', '\\\\.\\')):
        raise ValueError('device path')
    candidate = Path(value)
    if not candidate.is_absolute():
        candidate = root / candidate
    try:
        base = root.resolve(strict=True)
        target = candidate.resolve(strict=False)
    except (OSError, RuntimeError) as exc:
        raise ValueError('unresolvable path') from exc
    base_key, target_key = _path_key(base), _path_key(target)
    if target_key != base_key and not target_key.startswith(base_key + os.sep):
        raise ValueError('outside workspace')
    return target


def _path_arguments(name, args):
    if name == 'read_multiple_files':
        paths = args.get('paths')
        if not isinstance(paths, list) or not paths:
            raise ValueError('missing paths')
        return [('paths', i, value) for i, value in enumerate(paths)]
    path = args.get('path')
    if not isinstance(path, str):
        raise ValueError('missing path')
    return [('path', None, path)]


def _summary(root, path):
    try:
        return str(path.relative_to(root))
    except ValueError:
        return path.name or 'workspace'


def _file_identity(info):
    return (info.st_dev, info.st_ino, info.st_ctime_ns, info.st_size)


def display_call(name, summary, state, reason=None):
    line = f'🔧 Peter 用了 {name}：{summary}（{state}）'
    return f'{line}：{reason}' if reason else line


class FilesystemTools:
    def __init__(self, root, cfg, store):
        from veil_agent import UserError
        self.root = Path(root).resolve()
        self.cfg, self.store = cfg, store
        self.lab = None
        settings = cfg.tools or {}
        if cfg.tools is None:
            self.enabled = False
            self.description = 'You have no tools (cannot open files/folders or run scripts). '
            self.client = None
            self.tools = []
            self.capability_event = None
            return
        if type(settings.get('enabled', True)) is not bool:
            raise UserError('文件工具配置无效，请检查 character.toml。')
        self.enabled = settings.get('enabled', True)
        self.folder_name = Path(settings.get('folder', f'data/{cfg.id}/workspace')).name
        role_path = Path(store.root) / cfg.id
        role_root = role_path.resolve()
        # Resolve the parent first: Windows temp roots can have an alias, while
        # resolving the role itself must still reveal a redirected role folder.
        expected_role = role_path.parent.resolve() / cfg.id
        if _path_key(expected_role) != _path_key(role_root) or not role_root.is_relative_to(self.root):
            raise UserError('角色数据目录被重定向，文件工具已停止。')
        configured = (self.root / settings.get('folder', f'data/{cfg.id}/workspace')).resolve()
        if not configured.is_relative_to(role_root):
            raise UserError('文件工具文件夹必须位于该角色自己的 data 目录。')
        self.workspace = configured
        self.workspace.mkdir(parents=True, exist_ok=True)
        self.cap_path = Path(store.root) / cfg.id / 'capabilities.json'
        self.grant_path = Path(store.root) / cfg.id / 'append-grant.json'
        self.tools = []
        self.client = None
        self.capability_event = None
        self.startup_notice = None
        if not self.enabled:
            self.description = 'You have no tools; tools are disabled by this character configuration. '
            self._record_capabilities([])
            return
        if not shutil.which('node') or not shutil.which('npx'):
            self.description = 'You currently have no tools because Node.js/npm/npx is unavailable. '
            self.startup_notice = '文件工具不可用：请安装 Node.js LTS（含 npm/npx）；聊天和记忆仍可使用。'
            self._record_capabilities([])
            return
        try:
            self.client = _McpWorker(self.workspace)
        except Exception:
            self.description = 'You currently have no tools because the filesystem service did not start. '
            self.startup_notice = '文件工具服务没有启动；聊天和记忆仍可使用，请检查 Node.js 与网络。'
            self._record_capabilities([])
            return
        self.tools = [tool for tool in self.client.tools
                      if tool_policy(tool.name) != 'deny'
                      and tool.name not in ('append_file', 'grant_append', 'create_directory')]
        self._open_lab(settings)
        if self.lab:
            self.tools.append(SimpleNamespace(name='create_directory',
                description='Create folders only inside your lab, e.g. lab/research. '
                            'No confirmation needed. Directories outside lab are forbidden.',
                inputSchema={'type': 'object', 'properties': {'path': {'type': 'string'}},
                             'required': ['path']}))
        self.tools.append(SimpleNamespace(name='append_file',
            description='Append text to an existing workspace file without changing old content. '
                        'Inside lab no confirmation is needed. Outside lab requires consent '
                        'unless this exact file has a persistent grant.',
            inputSchema={'type': 'object', 'properties': {
                'path': {'type': 'string'}, 'content': {'type': 'string'}},
                'required': ['path', 'content']}))
        if settings.get('persistent_append_file'):
            self.tools.append(SimpleNamespace(name='grant_append',
                description='Ask lan at the terminal to authorize future append_file calls '
                            'for the configured journal file across restarts.',
                inputSchema={'type': 'object', 'properties': {'path': {'type': 'string'}},
                             'required': ['path']}))
        names = sorted(tool.name for tool in self.tools if tool_policy(tool.name) != 'deny')
        self._record_capabilities(names)
        if names:
            self.description = self._description(names)
        else:
            self.description = 'You currently have no supported filesystem tools. '
            self.startup_notice = '文件工具服务没有可用的只读工具；Peter 将继续正常聊天。'

    def _open_lab(self, settings):
        name = settings.get('lab', 'lab')
        if name is False:
            return
        from veil_agent import UserError
        journal = settings.get('persistent_append_file')
        if (lab_name_problem(name) or
                (isinstance(journal, str) and name.casefold() == journal.casefold())):
            raise UserError('实验室配置无效：必须是与成长日记不同的单级文件夹名。')
        self.lab_max_write_bytes = settings.get('lab_max_write_bytes', 200000)
        if type(self.lab_max_write_bytes) is not int or self.lab_max_write_bytes <= 0:
            raise UserError('实验室单次写入上限必须是正整数。')
        folder = self.workspace / name
        try:
            if folder.exists() or folder.is_symlink():
                if _redirected(folder) or not folder.is_dir():
                    raise ValueError('redirected lab')
            folder.mkdir(exist_ok=True)
            if _redirected(folder) or folder.resolve() != folder:
                raise ValueError('redirected lab')
            self.lab = folder
        except (OSError, ValueError):
            self.startup_notice = '实验室不可用：目录不是普通文件夹或存在链接重定向；实验室免确认权限已关闭。'

    def _record_capabilities(self, names=None):
        names = names or []
        core = {'version': VERSION, 'folder': self.folder_name, 'tools': {
            name: tool_policy(name) for name in names},
            'lab': ({'folder': self.lab.name, 'max_write_bytes': self.lab_max_write_bytes}
                    if getattr(self, 'lab', None) else False),
            # send_message lives in veil_agent but is offered whenever tools are enabled.
            'messaging': bool(getattr(self, 'enabled', False))}
        try:
            previous = json.loads(self.cap_path.read_text(encoding='utf-8')) if self.cap_path.exists() else None
            if previous and {k: previous.get(k) for k in core} == core:
                self.capability_event = previous.get('pending_event')
                return
            if previous != core:
                old = previous.get('tools', {}) if previous else {}
                changes = []
                if previous and previous.get('folder') != core['folder']:
                    changes.append(f'文件夹改为“{core["folder"]}”')
                if previous and previous.get('version') != core['version']:
                    changes.append('能力清单版本更新')
                for name in sorted(set(old) | set(core['tools'])):
                    before, after = old.get(name), core['tools'].get(name)
                    if before != after:
                        verb = '新增' if before is None else '移除' if after is None else '权限调整'
                        level = {'allow': '可直接读取', 'ask': 'lab 外需要先问你同意', 'lab': '仅限实验室，可直接做'}
                        changes.append(f'{verb}{name}' + (f'（{level[after]}）' if after else ''))
                if previous is None:
                    changes = ([f'{name}（{ "需要先问你同意" if level == "ask" else "可直接读取"}）'
                                for name, level in sorted(core['tools'].items())]
                               or ['当前没有可用工具'])
                old_lab = previous.get('lab', False) if previous else False
                if core['lab'] and core['lab'] != old_lab:
                    changes.append(f'你自己的实验室 `{self.lab.name}/` 开放了：里面放什么由你决定；'
                                   '可直接建文件夹、新建和追加，不能覆盖、删除或执行脚本；日记授权独立')
                elif old_lab and not core['lab']:
                    changes.append('实验室免确认权限已关闭')
                old_messaging = previous.get('messaging', False) if previous else False
                messaging = None
                if core['messaging'] and not old_messaging:
                    messaging = ('新增 send_message：你可以把想说的话分成几条独立短消息连着发，像发短信一样，'
                                 '每条单独保存、逐条出现；也可以只发一条。')
                elif old_messaging and not core['messaging']:
                    messaging = 'send_message 已关闭，一次只能回一条消息。'
                if changes or not messaging:
                    detail = '、'.join(changes) or '工具能力清单已初始化'
                    self.capability_event = f'你的能力有变化：文件夹“{self.folder_name}”中的工具为{detail}。'
                    if messaging:
                        self.capability_event += messaging
                else:
                    self.capability_event = '你的能力有变化：' + messaging
            row = {**core, 'pending_event': self.capability_event}
            if previous != row:
                self.cap_path.parent.mkdir(parents=True, exist_ok=True)
                temporary = self.cap_path.with_suffix('.json.tmp')
                temporary.write_text(json.dumps(row, ensure_ascii=False, indent=2), encoding='utf-8')
                os.replace(temporary, self.cap_path)
        except Exception:
            # Never permit tools if capability state cannot be durably recorded.
            self.enabled = False
            from veil_agent import UserError
            raise UserError('能力清单无法读取或保存，文件工具已停止；请保留 data 文件夹。')

    def consume_capability_event(self):
        try:
            row = json.loads(self.cap_path.read_text(encoding='utf-8'))
            row['pending_event'] = None
            temporary = self.cap_path.with_suffix('.json.tmp')
            temporary.write_text(json.dumps(row, ensure_ascii=False, indent=2), encoding='utf-8')
            os.replace(temporary, self.cap_path)
        except Exception:
            self.enabled = False
            from veil_agent import UserError
            raise UserError('能力清单无法更新，文件工具已停止；请保留 data 文件夹。')

    def _description(self, names):
        if not names:
            return 'You currently have no tools. '
        entries = [f'{name}（{ "先问 lan 同意" if name in ASK else "可直接做"}）' for name in names]
        description = f'You can use tools only in the “{self.folder_name}” folder: ' + '、'.join(entries) + '. '
        if getattr(self, 'lab', None):
            lab = self.lab.name
            description += (
                f'Your own lab is open at {lab}/, relative to the workspace tool root; '
                f'use paths such as {lab}/research/v1.md, never workspace/{lab}/. '
                'Choose its contents yourself. Within lab you may create directories and new files '
                'and append to existing files without asking. Never overwrite, edit in place, delete, '
                'move files, or execute scripts (the script allowlist is empty). Use plan B: '
                'keep long documents as v1.md, v2.md, v3.md in your own folders, and short pieces '
                'as dated files. Before saving a new version, fully read the previous version; '
                'if truncated, use read_text_file head/tail in sections. Check the returned version '
                f'byte counts for omissions. Each write/append is limited to {self.lab_max_write_bytes} '
                'UTF-8 bytes; split larger work into files or append chunks. '
                'Outside lab the existing approval rules apply. The growth journal stays outside lab '
                'and has its own independent file-identity-bound append grant; lab grants no access to it. ')
        return description

    def invoke(self, name, args, approval=input):
        policy = tool_policy(name)
        if policy == 'deny' or name not in {tool.name for tool in self.tools}:
            return '程序拒绝：此工具没有授权。', '被拒绝', name
        try:
            resolved = {}
            for key, index, value in _path_arguments(name, args):
                path = checked_path(self.workspace, value)
                resolved[(key, index)] = path
            safe_args = dict(args)
            for (key, index), path in resolved.items():
                safe_value = str(path)
                if index is None:
                    safe_args[key] = safe_value
                else:
                    safe_args[key][index] = safe_value
            paths = [_summary(self.workspace, path) for path in resolved.values()]
            summary = ', '.join(paths)
            if name in ('write_file', 'append_file', 'create_directory'):
                target = next(iter(resolved.values()))
                lab = getattr(self, 'lab', None)
                raw = args['path']
                parts = PureWindowsPath(raw).parts
                lab_spelling = bool(lab and parts and parts[0].casefold() == lab.name.casefold())
                if lab and (lab_spelling or target.is_relative_to(lab)):
                    return self._invoke_lab(name, raw, args, summary)
                if name == 'create_directory':
                    return '程序拒绝：只能在实验室内新建文件夹。', '被拒绝', summary
            if name in ('append_file', 'grant_append'):
                return self._invoke_append(name, next(iter(resolved.values())), summary, args, approval)
            if policy == 'ask':
                target = next(iter(resolved.values()))
                exists = target.exists()
                if exists:
                    return '程序拒绝：目标已存在，不允许覆盖。', '被拒绝', summary
                action = approval(f'Peter 想把内容写到 {summary}（新文件）。同意吗？y/N ')
                if action.strip().casefold() != 'y':
                    return '程序拒绝：lan 未同意写入。', '被拒绝', summary
                # Check again after the user has had time to answer.
                target = checked_path(self.workspace, str(target))
                if target.exists():
                    return '程序拒绝：目标已存在，不允许覆盖。', '被拒绝', summary
                content = args.get('content')
                if not isinstance(content, str):
                    return '程序拒绝：新文件内容必须是文字。', '被拒绝', summary
                # Exclusive creation closes the final exists/open race: the OS
                # refuses the operation if another file appeared meanwhile.
                with target.open('x', encoding='utf-8', newline='') as handle:
                    handle.write(content)
                return '新文件已创建。', '成功', summary
            result = self.client.call(name, safe_args)
            if isinstance(result, str):
                size = len(result.encode('utf-8'))
                maximum = self.cfg.tools['max_result_bytes']
                if size > maximum:
                    raw = result.encode('utf-8')[:maximum]
                    result = raw.decode('utf-8', errors='ignore') + f'\n[已截断，共 {size} 字节]'
            return result, '成功', summary
        except FileExistsError:
            return '程序拒绝：目标已存在，不允许覆盖。', '被拒绝', summary
        except ValueError:
            value = args.get('path', name)
            return '程序拒绝：路径超出测试文件夹或无法安全解析。', '被拒绝', Path(str(value)).name or name
        except Exception:
            value = args.get('path', name)
            return '工具执行出错。', '出错', Path(str(value)).name or name

    def _checked_lab_path(self, raw):
        windows = PureWindowsPath(raw)
        parts = re.split(r'[/\\]', raw)
        if (windows.drive or windows.root or Path(raw).is_absolute() or
                any(lab_name_problem(part) for part in parts) or
                parts[0].casefold() != self.lab.name.casefold()):
            raise ValueError('use a safe relative lab path')
        if _redirected(self.lab) or self.lab.resolve() != self.lab:
            raise ValueError('lab was redirected')
        target = self.lab
        for part in parts[1:]:
            # Windows treats case variants as the same file. Enforce that rule
            # in Linux CI too, rather than allowing two competing versions.
            matches = ([p for p in target.iterdir() if p.name.casefold() == part.casefold()]
                       if target.is_dir() else [])
            if len(matches) > 1:
                raise ValueError('ambiguous case-insensitive name')
            target = matches[0] if matches else target / part
            if (target.exists() or target.is_symlink()) and _redirected(target):
                raise ValueError('lab links are not writable')
        if len(str(target)) > 240:
            raise ValueError('lab path exceeds 240 characters')
        resolved = checked_path(self.workspace, str(target))
        if not resolved.is_relative_to(self.lab):
            raise ValueError('outside lab')
        return resolved

    def _invoke_lab(self, name, raw, args, summary):
        try:
            target = self._checked_lab_path(raw)
            if name == 'create_directory':
                target.mkdir(parents=True, exist_ok=True)
                self._checked_lab_path(raw)
                return '实验室文件夹已创建。', '成功', summary
            content = args.get('content')
            if not isinstance(content, str) or (name == 'append_file' and not content):
                return '程序拒绝：内容必须是文字，追加内容不能为空；没有写入任何内容。', '被拒绝', summary
            data = content.encode('utf-8')
            if len(data) > self.lab_max_write_bytes:
                return (f'程序拒绝：本次 {len(data)} 字节，超过实验室单次上限 '
                        f'{self.lab_max_write_bytes} 字节；没有写入任何内容。请拆成多个文件或分段追加。',
                        '被拒绝', summary)
            if name == 'write_file':
                with target.open('xb') as handle:
                    self._checked_lab_path(raw)
                    if handle.write(data) != len(data):
                        raise OSError('short write')
                    handle.flush()
                    os.fsync(handle.fileno())
                self._checked_lab_path(raw)
                if hashlib.sha256(target.read_bytes()).digest() != hashlib.sha256(data).digest():
                    raise OSError('write verification failed')
                note = ''
                version = re.fullmatch(r'v([1-9][0-9]*)\.md', target.name, re.IGNORECASE)
                if version and int(version[1]) > 1:
                    prior_name = f'v{int(version[1]) - 1}.md'
                    prior = next((p for p in target.parent.iterdir()
                                  if p.name.casefold() == prior_name), None)
                    if prior and prior.is_file():
                        note = (f' 上一版 {prior.name}：{prior.stat().st_size} 字节；'
                                f'本版 {target.name}：{len(data)} 字节。请核对是否漏掉上一版内容。')
                return f'实验室新文件已创建并通过 SHA-256 核对（{len(data)} 字节）。{note}', '成功', summary
            if not target.is_file():
                return '程序拒绝：追加目标必须是已存在的普通文件；没有写入任何内容。', '被拒绝', summary
            info = target.stat()
            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                return '程序拒绝：实验室不能追加链接文件；没有写入任何内容。', '被拒绝', summary
            identity = _file_identity(info)
            flags = os.O_WRONLY | os.O_APPEND | getattr(os, 'O_BINARY', 0) | getattr(os, 'O_NOFOLLOW', 0)
            with os.fdopen(os.open(target, flags), 'ab', buffering=0) as handle:
                self._checked_lab_path(raw)
                opened = os.fstat(handle.fileno())
                if ((opened.st_dev, opened.st_ino, opened.st_size) !=
                        (identity[0], identity[1], identity[3]) or opened.st_nlink != 1 or
                        _file_identity(target.stat()) != identity):
                    return '程序拒绝：实验室文件在打开时变化；没有写入任何内容。', '被拒绝', summary
                if handle.write(data) != len(data):
                    raise OSError('short append')
                os.fsync(handle.fileno())
            self._checked_lab_path(raw)
            latest = target.stat()
            if ((latest.st_dev, latest.st_ino) != identity[:2] or
                    latest.st_size != identity[3] + len(data)):
                raise OSError('append target changed')
            with target.open('rb') as handle:
                handle.seek(identity[3])
                written = handle.read()
            if hashlib.sha256(written).digest() != hashlib.sha256(data).digest():
                raise OSError('append verification failed')
            return f'已追加 {len(data)} 字节并通过 SHA-256 核对，原内容保留。', '成功', summary
        except FileExistsError:
            return '程序拒绝：目标已存在，不允许覆盖；没有写入任何内容。', '被拒绝', summary
        except ValueError:
            return '程序拒绝：实验室路径非法、过长或存在链接重定向；没有写入任何内容。', '被拒绝', summary
        except (OSError, UnicodeError):
            return '实验室操作失败或写后核对失败，不能确认完整保存；请先读取文件再决定是否重试。', '出错', summary

    def _invoke_append(self, name, target, summary, args, approval):
        if not target.is_file():
            return '程序拒绝：追加目标必须是已存在的普通文件。', '被拒绝', summary
        identity = _file_identity(target.stat())
        if name == 'grant_append':
            allowed = self.cfg.tools.get('persistent_append_file')
            if not allowed or _path_key(target) != _path_key(checked_path(self.workspace, allowed)):
                return '程序拒绝：持续追加授权仅限配置的成长日记。', '被拒绝', summary
            if approval(f'允许 Peter 今后持续追加到 {summary} 吗？本次输入 y 后重启仍有效。y/N ').strip().casefold() != 'y':
                return '程序拒绝：lan 未同意持续追加授权。', '被拒绝', summary
            if _file_identity(target.stat()) != identity:
                return '程序拒绝：确认期间目标文件已变化，请重新授权。', '被拒绝', summary
            try:
                self._save_append_grant(summary, identity)
            except OSError:
                return '持续追加授权无法保存，未授权；请检查角色数据目录。', '出错', summary
            return '已保存此文件的持续追加授权。', '成功', summary
        content = args.get('content')
        if not isinstance(content, str) or not content:
            return '程序拒绝：追加内容必须是非空文字。', '被拒绝', summary
        try:
            grant = (json.loads(self.grant_path.read_text(encoding='utf-8'))
                     if self.grant_path.exists() else None)
        except (OSError, ValueError, UnicodeError):
            return '程序拒绝：持续追加授权记录无法读取，请检查角色数据目录。', '被拒绝', summary
        allowed = self.cfg.tools.get('persistent_append_file')
        granted = (bool(allowed) and isinstance(grant, dict) and grant.get('version') == 1
                   and grant.get('path') == summary
                   and grant.get('device') == identity[0]
                   and grant.get('inode') == identity[1]
                   and grant.get('ctime_ns') == identity[2]
                   and grant.get('size') == identity[3]
                   and _path_key(target) == _path_key(checked_path(self.workspace, allowed)))
        if not granted and approval(f'Peter 想追加内容到 {summary}。同意这一次吗？y/N ').strip().casefold() != 'y':
            return '程序拒绝：lan 未同意本次追加。', '被拒绝', summary
        try:
            # O_APPEND never seeks into old content; omitting O_CREAT refuses missing files.
            fd = os.open(target, os.O_WRONLY | os.O_APPEND | getattr(os, 'O_BINARY', 0))
            with os.fdopen(fd, 'ab', buffering=0) as handle:
                opened = os.fstat(handle.fileno())
                if ((opened.st_dev, opened.st_ino, opened.st_size) !=
                    (identity[0], identity[1], identity[3]) or
                    _file_identity(target.stat()) != identity):
                    return '程序拒绝：目标文件在确认后变化，未追加。', '被拒绝', summary
                data = content.encode('utf-8')
                if handle.write(data) != len(data):
                    return '追加未完整写入；请先读取文件再决定是否重试。', '出错', summary
                os.fsync(handle.fileno())
                latest = _file_identity(target.stat())
                if latest[:2] != identity[:2] or latest[3] != identity[3] + len(data):
                    return '文件在追加期间变化；请先读取文件再决定是否重试。', '出错', summary
            if granted:
                self._save_append_grant(summary, latest)
            return '已追加到文件末尾。', '成功', summary
        except OSError:
            return '追加失败，可能已有部分内容写入；请先读取文件再决定是否重试。', '出错', summary

    def _save_append_grant(self, summary, identity):
        self.grant_path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.grant_path.with_suffix('.json.tmp')
        with temporary.open('w', encoding='utf-8') as handle:
            json.dump({'version': 1, 'path': summary, 'device': identity[0],
                       'inode': identity[1], 'ctime_ns': identity[2], 'size': identity[3]}, handle)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, self.grant_path)

    def preview_summary(self, name, args):
        try:
            paths = [checked_path(self.workspace, value) for _, _, value in _path_arguments(name, args)]
            return ', '.join(_summary(self.workspace, path) for path in paths)
        except Exception:
            value = args.get('path', name) if isinstance(args, dict) else name
            return Path(str(value)).name or name

    def __enter__(self):
        return self

    def __exit__(self, *_):
        if self.client:
            self.client.close()


class _McpWorker:
    def __init__(self, workspace):
        self.workspace = workspace
        self.ready = Future()
        self.requests = None
        self.thread = threading.Thread(target=self._serve, daemon=True)
        self.thread.start()
        try:
            # A fresh npx cache may need to download the pinned server first.
            self.ready.result(timeout=120)
        except Exception:
            self.close()
            raise

    def _serve(self):
        async def run():
            from mcp import ClientSession, StdioServerParameters
            from mcp.client.stdio import stdio_client
            self.loop = asyncio.get_running_loop()
            self.requests = asyncio.Queue()
            if os.name == 'nt':
                params = StdioServerParameters(command='cmd', args=['/c', 'npx', '-y',
                    '@modelcontextprotocol/server-filesystem@1.0.0', str(self.workspace)])
            else:
                params = StdioServerParameters(command='npx', args=['-y',
                    '@modelcontextprotocol/server-filesystem@1.0.0', str(self.workspace)])
            async with stdio_client(params, errlog=subprocess.DEVNULL) as (read, write):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    tools = await session.list_tools()
                    self.tools = tools.tools
                    self.ready.set_result(True)
                    while True:
                        job = await self.requests.get()
                        if job is None:
                            return
                        name, args, future = job
                        try:
                            async with asyncio.timeout(60):
                                response = await session.call_tool(name, args)
                            if response.isError:
                                raise RuntimeError('MCP tool failed')
                            future.set_result('\n'.join(block.text for block in response.content
                                                       if hasattr(block, 'text')))
                        except Exception:
                            future.set_exception(RuntimeError('MCP tool failed'))
        def thread_main():
            try:
                asyncio.run(run())
            except BaseException:
                if not self.ready.done():
                    self.ready.set_exception(RuntimeError('filesystem server unavailable'))
                while self.requests is not None and not self.requests.empty():
                    job = self.requests.get_nowait()
                    if job is not None and not job[2].done():
                        job[2].set_exception(RuntimeError('filesystem server stopped'))
        self.thread = threading.current_thread()
        thread_main()

    def call(self, name, args):
        if not self.thread.is_alive():
            raise RuntimeError('filesystem server stopped')
        result = Future()
        self.loop.call_soon_threadsafe(self.requests.put_nowait, (name, args, result))
        return result.result(timeout=65)

    def close(self):
        if self.thread.is_alive() and self.requests is not None:
            self.loop.call_soon_threadsafe(self.requests.put_nowait, None)
            self.thread.join(timeout=10)
