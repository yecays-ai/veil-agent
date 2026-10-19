"""Bounded MCP filesystem access and application-enforced tool policy."""
import asyncio
import json
import os
import shutil
import subprocess
import threading
from concurrent.futures import Future
from pathlib import Path
from types import SimpleNamespace


VERSION = 1
ALLOW = {
    'list_directory', 'directory_tree', 'read_text_file', 'read_multiple_files',
    'get_file_info', 'search_files', 'list_directory_with_sizes',
}
ASK = {'write_file', 'append_file', 'grant_append'}
DENY = {'edit_file', 'move_file', 'create_directory'}
# Memory tools (ticket 007) are offered by memory_native.ToolSet, never by the filesystem server.
NATIVE = {'hold', 'breath_search'}


def permitted_tools(server_tools):
    """Convert only application-allowlisted MCP tools to OpenAI tool schemas."""
    result = []
    for tool in server_tools:
        if tool.name not in ALLOW | ASK | NATIVE:
            continue
        result.append({'type': 'function', 'function': {
            'name': tool.name, 'description': tool.description or tool.name,
            'parameters': tool.inputSchema,
        }})
    return result


def tool_policy(name):
    if name in DENY or name not in ALLOW | ASK:
        return 'deny'
    return 'ask' if name in ASK else 'allow'


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
                      and tool.name not in ('append_file', 'grant_append')]
        self.tools.append(SimpleNamespace(name='append_file',
            description='Append text to an existing workspace file without changing old content. '
                        'Requires terminal consent unless this exact file has a persistent grant.',
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

    def _record_capabilities(self, names=None):
        names = names or []
        core = {'version': VERSION, 'folder': self.folder_name, 'tools': {
            name: ('ask' if name in ASK else 'allow') for name in names}}
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
                        level = {'allow': '可直接读取', 'ask': '需要先问你同意'}
                        changes.append(f'{verb}{name}' + (f'（{level[after]}）' if after else ''))
                if previous is None:
                    changes = ([f'{name}（{ "需要先问你同意" if level == "ask" else "可直接读取"}）'
                                for name, level in sorted(core['tools'].items())]
                               or ['当前没有可用工具'])
                detail = '、'.join(changes) or '工具能力清单已初始化'
                self.capability_event = f'你的能力有变化：文件夹“{self.folder_name}”中的工具为{detail}。'
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
        return f'You can use tools only in the “{self.folder_name}” folder: ' + '、'.join(entries) + '. '

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
