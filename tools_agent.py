Warning: truncated output (original token count: 20735)
Total output lines: 1437

"""Bounded MCP filesystem access and application-enforced tool policy."""
import asyncio
import ctypes
import errno
import hashlib
import json
import os
import re
import shutil
import stat
import subprocess
import threading
from datetime import datetime, timezone
from concurrent.futures import Future
from pathlib import Path, PureWindowsPath
from types import SimpleNamespace

from agent_types import UserError
from capability_state import capability_transaction, save_capabilities
from attachment_utils import MAX_ATTACHMENT_BYTES, inspect_attachment
from web_tools import WEB_TOOL_NAMES
from desktop_files import DESKTOP_FILE_TOOL_NAMES, DesktopFileTools
from windows_observer import WINDOWS_OBSERVATION_TOOL_NAMES, WindowsObservationTools
from windows_interaction import (
    WINDOWS_INTERACTION_TOOL_NAMES,
    WINDOWS_DESKTOP_TOOL_NAMES,
    WindowsInteractionBackend,
    WindowsInteractionTools,
    load_application_allowlist,
)
from screen_pop import SCREEN_POP_TOOL, SCREEN_POP_TOOL_NAMES, normalize_screen_pop_args, screen_state


VERSION = 1
ALLOW = {
    'list_directory', 'directory_tree', 'read_text_file', 'read_multiple_files',
    'get_file_info', 'search_files', 'list_directory_with_sizes', *WEB_TOOL_NAMES,
    *WINDOWS_OBSERVATION_TOOL_NAMES, *WINDOWS_INTERACTION_TOOL_NAMES, *DESKTOP_FILE_TOOL_NAMES,
    *SCREEN_POP_TOOL_NAMES,
}
ASK = {'write_file', 'append_file', 'grant_append'}
DENY = {'edit_file', 'move_file'}
LAB_ONLY = {'create_directory', 'move_lab_file', 'lab_file_digest'}
# Memory tools (ticket 007) are offered by memory_native.ToolSet, never by the filesystem server.
NATIVE = {'hold', 'breath_search', 'trace', 'dream', 'letter_write', 'letter_read',
          'letter_lock_update', 'anchor', 'release', 'plan', 'pulse', 'memory_history', 'memory_catalog', 'memory_read'}
AUTONOMY_DIRECT = {'set_next_wake'}


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
    if name in WINDOWS_OBSERVATION_TOOL_NAMES:
        return 'desktop_read'
    if name in WINDOWS_INTERACTION_TOOL_NAMES:
        return 'desktop_control'
    if name in DESKTOP_FILE_TOOL_NAMES:
        return 'desktop_create'
    if name in WEB_TOOL_NAMES | AUTONOMY_DIRECT:
        return 'allow'
    if name in LAB_ONLY:
        return 'lab'
    if name in DENY or name not in ALLOW | ASK | AUTONOMY_DIRECT:
        return 'deny'
    return 'ask' if name in ASK else 'allow'


def lab_name_problem(name):
    """Use Windows filename rules on every platform, including config names."""
    if not isinstance(name, str) or not name or name in ('.', '..'):
        return 'Names must be a single file or folder name.'
    if any(ord(c) < 32 or c in '<>:"|?*/\\' for c in name) or name.endswith((' ', '.')):
        return 'Names cannot contain Windows-invalid characters or end with a space or period.'
    stem = name.split('.')[0].upper()
    if stem in {'CON', 'PRN', 'AUX', 'NUL', 'CONIN$', 'CONOUT$'} or re.fullmatch(r'(COM|LPT)[1-9¹²³]', stem):
        return 'This is a reserved Windows name.'
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


def _rename_noreplace(source, destination):
    if os.name == 'nt':
        os.rename(source, destination)
        return
    try:
        renameat2 = ctypes.CDLL(None, use_errno=True).renameat2
    except AttributeError as exc:
        raise OSError(errno.ENOTSUP, 'atomic no-replace rename is unavailable') from exc
    renameat2.argtypes = (ctypes.c_int, ctypes.c_char_p, ctypes.c_int,
                          ctypes.c_char_p, ctypes.c_uint)
    renameat2.restype = ctypes.c_int
    result = renameat2(-100, os.fsencode(source), -100, os.fsencode(destination), 1)
    if result != 0:
        code = ctypes.get_errno()
        raise OSError(code, os.strerror(code), str(source), str(destination))


def display_call(name, summary, state, reason=None):
    line = f'🔧 Peter 用了 {name}：{summary}（{state}）'
    return f'{line}：{reason}' if reason else line


class FilesystemTools:
    def __init__(self, root, cfg, store, *, web_factory=None):
        self.root = Path(root).resolve()
        self.cfg, self.store = cfg, store
        role_path = Path(store.root) / cfg.id
        role_root = role_path.resolve()
        # Activity transitions write capability history even when file tools
        # are absent from the role configuration. Validate the role boundary
        # before exposing that persistent capability path in every branch.
        expected_role = role_path.parent.resolve() / cfg.id
        if _path_key(expected_role) != _path_key(role_root) or not role_root.is_relative_to(self.root):
            raise UserError('角色数据目录被重定向，文件工具已停止。')
        self.cap_path = role_path / 'capabilities.json'
        self.lab = None
        settings = cfg.tools or {}
        self.screen_pop_enabled = (getattr(cfg, 'screen_pop', None) or {}).get('enabled') is True
        self.tools = [SCREEN_POP_TOOL] if self.screen_pop_enabled else []
        interaction_settings = getattr(cfg, 'windows_interaction', None) or {}
        interaction_error = False
        applications = {}
        if os.name == 'nt' and interaction_settings.get('enabled') is True:
            try:
                applications = load_application_allowlist(self.root, cfg.id, interaction_settings)
            except (OSError, ValueError, TypeError):
                interaction_error = True
        windows_backend = WindowsInteractionBackend()
        self.windows_tools = WindowsObservationTools(
            (getattr(cfg, 'windows_observation', None) or {}).get('enabled', False),
            settings.get('max_result_bytes', 8192),
            backend=windows_backend,
        )
        self.windows_interaction_tools = WindowsInteractionTools(
            interaction_settings.get('enabled', False), applications,
            max_result_bytes=settings.get('max_result_bytes', 8192),
            backend=windows_backend,
            platform_enabled=os.name == 'nt',
            idle_seconds=interaction_settings.get('idle_seconds', 3),
        )
        self.windows_tools.application_id_for = self.windows_interaction_tools.application_id_for
        self.tools.extend(self.windows_tools.tools)
        self.tools.extend(self.windows_interaction_tools.tools)
        self.desktop_files = DesktopFileTools(
            os.name == 'nt' and interaction_settings.get('enabled') is True and not interaction_error)
        self.tools.extend(self.desktop_files.tools)
        if cfg.tools is None:
            self.enabled = False
            self.description = 'You have no tools (cannot open files/folders or run scripts). '
            self.client = None
            self.capability_event = None
            self.capability_event_model = None
            return
        if type(settings.get('enabled', True)) is not bool:
            raise UserError('文件工具配置无效，请检查 character.toml。')
        self.enabled = settings.get('enabled', True)
        self.folder_name = Path(settings.get('folder', f'data/{cfg.id}/workspace')).name
        # Resolve the parent first: Windows temp roots can have an alias, while
        # resolving the role itself must still reveal a redirected role folder.
        configured = (self.root / settings.get('folder', f'data/{cfg.id}/workspace')).resolve()
        if not configured.is_relative_to(role_root):
            raise UserError('文件工具文件夹必须位于该角色自己的 data 目录。')
        self.workspace = configured
        self.workspace.mkdir(parents=True, exist_ok=True)
        self.grant_path = Path(store.root) / cfg.id / 'append-grant.json'
        self.client = None
        self.web_tools = None
        self.capability_event = None
        self.capability_event_model = None
        self.startup_notice = ('Windows 桌面交互白名单配置无效，启动、窗口动作和 UIA 操作已关闭。'
                               if interaction_error else None)
        if not self.enabled:
            names = sorted(tool.name for tool in self.tools)
            self._record_capabilities(names)
            self.description = self._description(names) or (
                'You have no tools; tools are disabled by this character configuration. ')
            return
        if web_factory:
            try:
                self.web_tools = web_factory(self.root, cfg)
                self.tools.extend(self.web_tools.tools)
                if self.web_tools.startup_notice:
                    self._add_startup_notice(self.web_tools.startup_notice)
            except Exception:
                self._add_startup_notice('Web tools are unavailable; their service did not start.')
        if not shutil.which('node') or not shutil.which('npx'):
            self._add_startup_notice('文件工具不可用：请安装 Node.js LTS（含 npm/npx）；聊天、已配置的网页工具和记忆仍可使用。')
            names = sorted(tool.name for tool in self.tools if tool_policy(tool.name) != 'deny')
            self._record_capabilities(names)
            self.description = self._description(names) if names else (
                'You currently have no tools because Node.js/npm/npx is unavailable. ')
            return
        try:
            self.client = _McpWorker(self.workspace)
        except Exception:
            self._add_startup_notice('文件工具服务没有启动；聊天、已配置的网页工具和记忆仍可使用，请检查 Node.js 与网络。')
            names = sorted(tool.name for tool in self.tools if tool_policy(tool.name) != 'deny')
            self._record_capabilities(names)
            self.description = self._description(names) if names else (
                'You currently have no tools because the filesystem service did not start. ')
            return
        self.tools.extend(tool for tool in self.client.tools
                          if tool_policy(tool.name) != 'deny'
                          and tool.name not in WINDOWS_DESKTOP_TOOL_NAMES
                          and tool.name not in ('append_file', 'grant_append', 'create_directory', 'move_lab_file'))
        self._open_lab(settings)
        if self.lab:
            self.tools.append(SimpleNamespace(name='create_directory',
                description='Create folders only inside your lab, e.g. lab/research. '
                            'No confirmation needed. Directories outside lab are forbidden.',
                inputSchema={'type': 'object', 'properties': {'path': {'type': 'string'}},
                             'required': ['path']}))
            self.tools.append(SimpleNamespace(name='move_lab_file',
                description=('Move one regular file between a normal lab folder and lab/archive/. '
                             'For archiving, preserve its relative path, e.g. lab/play/v1.html to '
                             'lab/archive/play/v1.html. Restoring may target another lab folder. '
                             'No confirmation needed; existing targets are rejected.'),
                inputSchema={'type': 'object', 'properties': {
                    'source': {'type': 'string', 'description': 'Existing file path inside lab/.'},
                    'destination': {'type': 'string', 'description': 'New lab/archive/ path or lab restore path.'}},
                    'required': ['source', 'destination'], 'additionalProperties': False}))
            self.tools.append(SimpleNamespace(name='lab_file_digest',
                description=('Return the byte length and SHA-256 digest of one regular, unlinked file inside lab/. '
                             'This reads bytes in bounded chunks and returns metadata only; it cannot read or change '
                             'files outside lab/.'),
                inputSchema={'type': 'object', 'properties': {'path': {'type': 'string'}},
                             'required': ['path'], 'additionalProperties': False}))
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

    @capability_transaction
    def _record_capabilities(self, names=None):
        names = names or []
        core = {'version': VERSION, 'folder': self.folder_name, 'tools': {
            name: ('web' if name in WEB_TOOL_NAMES else tool_policy(name)) for name in names},
            'resident_memory': bool((getattr(self.cfg, 'memory', None) or {}).get('enabled')),
            'lab': ({'folder': self.lab.name, 'max_write_bytes': self.lab_max_write_bytes}
                    if getattr(self, 'lab', None) else False),
            # send_message lives in veil_agent but is offered whenever tools are enabled.
            'messaging': bool(getattr(self, 'enabled', False))}
        try:
            previous = json.loads(self.cap_path.read_text(encoding='utf-8')) if self.cap_path.exists() else None
            previous_core = {k: previous.get(k) for k in core} if previous else None
            if previous_core is not None:
                previous_core['resident_memory'] = bool(previous.get('resident_memory', False))
            if previous and previous_core == core:
                self.capability_event = previous.get('pending_event')
                # Older manifests stored a Chinese display event and an incomplete
                # English model summary.  An undelivered event is not chat history,
                # so replace it with one complete English body before delivery.
                if self.capability_event and not self.capability_event.startswith('Capability update:'):
                    self.capability_event = self._legacy_capability_event(core, previous)
                    previous = {**previous, 'pending_event': self.capability_event,
                                'pending_event_model': self.capability_event}
                    self._record_update(previous, self.capability_event)
                    save_capabilities(self.cap_path, previous)
                self.capability_event_model = self.capability_event
                return
            if previous != core:
                old = previous.get('tools', {}) if previous else {}
                changes = []
                if previous and previous.get('folder') != core['folder']:
                    changes.append(f'The configured folder changed to "{core["folder"]}".')
                if previous and previous.get('version') != core['version']:
                    changes.append('The capability manifest version changed.')
                for name in sorted(set(old) | set(core['tools'])):
                    before, after = old.get(name), core['tools'].get(name)
                    if before != after:
                        if before is None:
                            changes.append(f'Added {name} ({self._permission_text(after)}).')
                        elif after is None:
                            changes.append(f'Removed {name}; it is no longer available.')
                        else:
                            changes.append(f'Changed {name} from {self._permission_text(before)} '
                                           f'to {self._permission_text(after)}.')
                if previous is None:
                    changes = ([f'Added {name} ({self._permission_text(level)}).'
                                for name, level in sorted(core['tools'].items())]
                               or ['No file tools are currently available.'])
                memory_announcement = (core['resident_memory'] and previous is not None
                                       and not previous.get('resident_memory', False))
                old_lab = previous.get('lab', False) if previous else False
                if core['lab'] and core['lab'] != old_lab:
                    changes.append(f'Your lab folder "{self.lab.name}/" is available: you may create folders, '
                                   'create or append files, and move one file between ordinary folders and archive. '
                                   'You may not overwrite, delete, or run scripts; journal permission is separate.')
                elif old_lab and not core['lab']:
                    changes.append('Direct-use lab permission is no longer available.')
                old_messaging = previous.get('messaging', False) if previous else False
                messaging = None
                if core['messaging'] and not old_messaging:
                    messaging = ('Added send_message: you may send up to three separate short messages in one '
                                 'turn, or send only one; each is saved and displayed separately.')
                elif old_messaging and not core['messaging']:
                    messaging = 'Removed send_message: you may send only one reply at a time.'
                if changes or not messaging:
                    self.capability_event = self._capability_event(core, changes or ['The capability manifest was initialized.'])
                    if messaging:
                        self.capability_event += ' ' + messaging
                else:
                    self.capability_event = 'Capability update: ' + messaging
                if core['resident_memory'] and (previous is None or memory_announcement):
                    message = 'Your memories now fade naturally over time; you can review, pin, and archive them in the memory panel.'
                    self.capability_event = (self.capability_event + '\n' + message
                                             if self.capability_event else message)
            self.capability_event_model = self.capability_event
            row = {**core, 'pending_event': self.capability_event,
                   'pending_event_model': self.capability_event_model,
                   'memory_tools': previous.get('memory_tools', []) if previous else [],
                   'capability_updates': previous.get('capability_updates', []) if previous else [],
                   'autonomy_announced': bool(previous and previous.get('autonomy_announced')),
                   'long_tasks_announced': bool(previous and previous.get('long_tasks_announced')),
                   'photo_input_announced': bool(previous and previous.get('photo_input_announced')),
                   'voice_input_announced': bool(previous and previous.get('voice_input_announced')),
                   'voice_output_announced': bool(previous and previous.get('voice_output_announced')),
                   **{key: True for key, value in (previous or {}).items()
                      if key.endswith('_announced') and value is True}}
            self._record_update(row, self.capability_event)
            if previous != row:
                save_capabilities(self.cap_path, row)
        except Exception:
            # Never permit tools if capability state cannot be durably recorded.
            self.enabled = False
            raise UserError('能力清单无法读取或保存，文件工具已停止；请保留 data 文件夹。')

    @staticmethod
    def _permission_text(level):
        if level == 'web':
            return 'direct read-only use on public webpages'
        if level == 'desktop_read':
            return 'direct read-only observation of ordinary Windows applicat…8735 tokens truncated…, summary
            info = source.stat(follow_symlinks=False)
            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                return 'Only an unlinked regular file can be moved. No file was moved.', '被拒绝', summary
            self._ensure_lab_parent(destination_raw)
            # Re-resolve after creating destination parents and immediately before the
            # no-replace operation. Existing case-insensitive aliases are resolved too.
            source = self._checked_lab_path(source_raw)
            destination = self._checked_lab_path(destination_raw)
            if source.is_symlink() or _redirected(source) or not source.is_file():
                return 'The source changed while preparing the move. No file was moved.', '被拒绝', summary
            if destination.exists() or destination.is_symlink():
                return 'The destination already exists; nothing was overwritten or moved. Choose another destination.', '被拒绝', summary
            before = source.stat(follow_symlinks=False)
            if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1:
                return 'Only an unlinked regular file can be moved. No file was moved.', '被拒绝', summary
            digest = hashlib.sha256()
            with source.open('rb') as handle:
                opened = os.fstat(handle.fileno())
                if (opened.st_dev, opened.st_ino, opened.st_size) != (
                        before.st_dev, before.st_ino, before.st_size):
                    return 'The source changed while it was being checked. No file was moved.', '被拒绝', summary
                for chunk in iter(lambda: handle.read(1024 * 1024), b''):
                    digest.update(chunk)
                after_read = os.fstat(handle.fileno())
            if (after_read.st_dev, after_read.st_ino, after_read.st_size, after_read.st_mtime_ns) != (
                    before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns):
                return 'The source changed while it was being checked. No file was moved.', '被拒绝', summary
            expected_digest = digest.digest()
            source = self._checked_lab_path(source_raw)
            destination = self._checked_lab_path(destination_raw)
            if destination.exists() or destination.is_symlink():
                return 'The destination already exists; nothing was overwritten or moved. Choose another destination.', '被拒绝', summary
            before_move = source.stat(follow_symlinks=False)
            if (before_move.st_dev, before_move.st_ino, before_move.st_size, before_move.st_mtime_ns) != (
                    before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns):
                return 'The source changed before the move. No file was moved.', '被拒绝', summary
            # Use one no-replace directory-entry rename so a concurrent source
            # replacement cannot be unlinked after the original was linked.
            _rename_noreplace(source, destination)
            move_completed = True
            moved = destination.stat(follow_symlinks=False)
            moved_identity = (moved.st_dev, moved.st_ino, moved.st_size)
            if (not stat.S_ISREG(moved.st_mode) or (moved.st_dev, moved.st_ino, moved.st_size) !=
                    (before.st_dev, before.st_ino, before.st_size)):
                raise OSError('moved file identity changed')
            verify = hashlib.sha256()
            with destination.open('rb') as handle:
                for chunk in iter(lambda: handle.read(1024 * 1024), b''):
                    verify.update(chunk)
            if verify.digest() != expected_digest:
                raise OSError('moved file content verification failed')
            if source.exists():
                raise OSError('source remained after move')
            return (f'File moved intact ({before.st_size} bytes, SHA-256 {verify.hexdigest()}).',
                    '成功', summary)
        except FileExistsError:
            return 'The destination already exists; nothing was overwritten or moved. Choose another destination.', '被拒绝', summary
        except ValueError:
            return 'The move path is outside the permitted lab/archive area or crosses a link. No file was moved.', '被拒绝', summary
        except (OSError, RuntimeError):
            if (move_completed and moved_identity is not None
                    and source is not None and destination is not None):
                try:
                    moved = destination.stat(follow_symlinks=False)
                    if (not source.exists() and stat.S_ISREG(moved.st_mode) and
                            (moved.st_dev, moved.st_ino, moved.st_size) == moved_identity and
                            not _redirected(destination)):
                        _rename_noreplace(destination, source)
                except OSError:
                    pass
            return ('The move could not be completed safely. Check that the original file is still present '
                    'before retrying; no file contents were intentionally changed.'), '出错', summary

    def _lab_file_digest(self, args):
        summary = 'lab_file_digest'
        try:
            if (not isinstance(args, dict) or set(args) != {'path'}
                    or not isinstance(args['path'], str)):
                return 'A single lab file path is required.', '被拒绝', summary
            path = self._checked_lab_path(args['path'])
            summary = _summary(self.workspace, path)
            if path.is_symlink() or _redirected(path) or not path.is_file():
                return 'Only a regular file inside lab/ can be checked.', '被拒绝', summary
            before = path.stat(follow_symlinks=False)
            if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1:
                return 'Only an unlinked regular file inside lab/ can be checked.', '被拒绝', summary
            digest = hashlib.sha256()
            with path.open('rb') as handle:
                opened = os.fstat(handle.fileno())
                identity = (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns)
                if (opened.st_dev, opened.st_ino, opened.st_size, opened.st_mtime_ns) != identity:
                    return 'The file changed while it was being checked.', '出错', summary
                for chunk in iter(lambda: handle.read(1024 * 1024), b''):
                    digest.update(chunk)
                after_read = os.fstat(handle.fileno())
            after = path.stat(follow_symlinks=False)
            if any((item.st_dev, item.st_ino, item.st_size, item.st_mtime_ns) != identity
                   for item in (after_read, after)):
                return 'The file changed while it was being checked.', '出错', summary
            return (json.dumps({'path': summary.replace('\\', '/'), 'byte_length': before.st_size,
                                'sha256': digest.hexdigest()}, separators=(',', ':')),
                    '成功', summary)
        except ValueError:
            return 'The requested path is outside the permitted lab area or crosses a link.', '被拒绝', summary
        except (OSError, RuntimeError):
            return 'The lab file could not be checked safely.', '出错', summary

    def _invoke_lab(self, name, raw, args, summary):
        try:
            target = self._checked_lab_path(raw)
            if name == 'create_directory':
                target.mkdir(parents=True, exist_ok=True)
                self._checked_lab_path(raw)
                return '实验室文件夹已创建。', '成功', summary
            content = args.get('content')
            if not isinstance(content, str) or (name == 'append_file' and not content):
                return 'Content must be text, and appended content cannot be empty. No content was written.', '被拒绝', summary
            data = content.encode('utf-8')
            if len(data) > self.lab_max_write_bytes:
                return (f'This operation contains {len(data)} bytes and exceeds the lab per-operation limit of '
                        f'{self.lab_max_write_bytes} bytes; no content was written, so split it into smaller files or append smaller chunks.',
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
                return 'The append target must be an existing regular file. No content was written.', '被拒绝', summary
            info = target.stat()
            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                return 'Appending to links is not allowed in the lab. Choose a regular file.', '被拒绝', summary
            identity = _file_identity(info)
            flags = os.O_WRONLY | os.O_APPEND | getattr(os, 'O_BINARY', 0) | getattr(os, 'O_NOFOLLOW', 0)
            with os.fdopen(os.open(target, flags), 'ab', buffering=0) as handle:
                self._checked_lab_path(raw)
                opened = os.fstat(handle.fileno())
                if ((opened.st_dev, opened.st_ino, opened.st_size) !=
                        (identity[0], identity[1], identity[3]) or opened.st_nlink != 1 or
                        _file_identity(target.stat()) != identity):
                    return 'The lab file changed while it was being opened. No content was written; re-read it before retrying.', '被拒绝', summary
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
            return 'The target already exists and overwriting is not allowed. No content was written; choose a new filename.', '被拒绝', summary
        except ValueError:
            return 'The lab path is invalid, too long, or redirected through a link. No content was written; choose a valid short path inside the lab.', '被拒绝', summary
        except (OSError, UnicodeError):
            return 'The lab operation or its post-write verification failed, so a complete save cannot be confirmed. Read the file before deciding whether to retry.', '出错', summary

    def _invoke_append(self, name, target, summary, args, approval):
        if not target.is_file():
            return 'The append target must be an existing regular file.', '被拒绝', summary
        identity = _file_identity(target.stat())
        if name == 'grant_append':
            allowed = self.cfg.tools.get('persistent_append_file')
            if not allowed or _path_key(target) != _path_key(checked_path(self.workspace, allowed)):
                return 'Persistent append authorization is limited to the configured growth journal. Use one-time approval for other files.', '被拒绝', summary
            if approval(f'允许 Peter 今后持续追加到 {summary} 吗？本次输入 y 后重启仍有效。y/N ').strip().casefold() != 'y':
                return 'Lan did not approve persistent append access. Request authorization before using it.', '被拒绝', summary
            if _file_identity(target.stat()) != identity:
                return 'The target file changed while authorization was being confirmed. Request authorization again for the current file.', '被拒绝', summary
            try:
                self._save_append_grant(summary, identity)
            except OSError:
                return 'Persistent append authorization could not be saved, so it was not granted. Check the character data folder.', '出错', summary
            return '已保存此文件的持续追加授权。', '成功', summary
        content = args.get('content')
        if not isinstance(content, str) or not content:
            return 'Append content must be non-empty text.', '被拒绝', summary
        try:
            grant = (json.loads(self.grant_path.read_text(encoding='utf-8'))
                     if self.grant_path.exists() else None)
        except (OSError, ValueError, UnicodeError):
            return 'The persistent append authorization record could not be read. Check the character data folder.', '被拒绝', summary
        allowed = self.cfg.tools.get('persistent_append_file')
        granted = (bool(allowed) and isinstance(grant, dict) and grant.get('version') == 1
                   and grant.get('path') == summary
                   and grant.get('device') == identity[0]
                   and grant.get('inode') == identity[1]
                   and grant.get('ctime_ns') == identity[2]
                   and grant.get('size') == identity[3]
                   and _path_key(target) == _path_key(checked_path(self.workspace, allowed)))
        if not granted and approval(f'Peter 想追加内容到 {summary}。同意这一次吗？y/N ').strip().casefold() != 'y':
            return 'Lan did not approve this append, so no content was added. Ask for approval if it is still needed.', '被拒绝', summary
        try:
            # O_APPEND never seeks into old content; omitting O_CREAT refuses missing files.
            fd = os.open(target, os.O_WRONLY | os.O_APPEND | getattr(os, 'O_BINARY', 0))
            with os.fdopen(fd, 'ab', buffering=0) as handle:
                opened = os.fstat(handle.fileno())
                if ((opened.st_dev, opened.st_ino, opened.st_size) !=
                    (identity[0], identity[1], identity[3]) or
                    _file_identity(target.stat()) != identity):
                    return 'The target file changed after confirmation, so nothing was appended. Re-read it and request approval again.', '被拒绝', summary
                data = content.encode('utf-8')
                if handle.write(data) != len(data):
                    return 'The append did not complete. Read the file before deciding whether to retry.', '出错', summary
                os.fsync(handle.fileno())
                latest = _file_identity(target.stat())
                if latest[:2] != identity[:2] or latest[3] != identity[3] + len(data):
                    return 'The file changed during the append. Read the file before deciding whether to retry.', '出错', summary
            if granted:
                self._save_append_grant(summary, latest)
            return '已追加到文件末尾。', '成功', summary
        except OSError:
            return 'The append failed and may have written partial content. Read the file before deciding whether to retry.', '出错', summary

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
        if name in SCREEN_POP_TOOL_NAMES:
            style = args.get('style') if isinstance(args, dict) else None
            count = args.get('count', 1) if isinstance(args, dict) else 1
            return f'popup × {count}' if style == 'popup' else 'danmaku'
        if name in WINDOWS_OBSERVATION_TOOL_NAMES:
            return self.windows_tools.preview_summary(name, args)
        if name in WINDOWS_INTERACTION_TOOL_NAMES:
            return self.windows_interaction_tools.preview_summary(name, args)
        if name in WEB_TOOL_NAMES and self.web_tools is not None:
            return self.web_tools.preview_summary(name, args)
        try:
            paths = [checked_path(self.workspace, value) for _, _, value in _path_arguments(name, args)]
            return ', '.join(_summary(self.workspace, path) for path in paths)
        except Exception:
            value = args.get('path', name) if isinstance(args, dict) else name
            return Path(str(value)).name or name

    def invoke_screen_pop(self, args, *, request_id=None, call_id=None,
                          autonomy_run_id=None, handler=None):
        summary = self.preview_summary('screen_pop', args)
        if not getattr(self, 'screen_pop_enabled', False):
            return 'screen_pop is not enabled for this character.', '被拒绝', summary
        try:
            command = normalize_screen_pop_args(args)
        except (TypeError, ValueError):
            return 'screen_pop arguments are invalid.', '被拒绝', summary
        safe, fullscreen, reason = screen_state()
        if reason == 'locked':
            return "Lan's screen is locked right now; nothing was shown.", '被拒绝', summary
        if not safe:
            return 'VEIL Desktop is not available on this platform; nothing was shown.', '出错', summary
        fullscreen_fallback = command['style'] == 'popup' and fullscreen
        if fullscreen_fallback:
            command = {**command, 'style': 'danmaku', 'count': 1}
            summary = 'danmaku (fullscreen fallback)'
        if not callable(handler):
            return 'VEIL Desktop shell is not open; nothing was shown.', '出错', summary
        try:
            response = handler(command, request_id=request_id, call_id=call_id,
                               autonomy_run_id=autonomy_run_id)
        except Exception:
            return ('The display could not be confirmed; nothing was reported as shown. '
                    'Check the VEIL Desktop state before retrying.'), '出错', summary
        if isinstance(response, dict) and response.get('state') == 'shown':
            if response.get('style') == 'danmaku' and command['style'] == 'popup':
                summary = 'danmaku (fullscreen fallback)'
            return 'shown', '成功', summary
        if isinstance(response, dict) and response.get('reason') == 'locked':
            return "Lan's screen is locked right now; nothing was shown.", '被拒绝', summary
        if isinstance(response, dict) and response.get('reason') == 'unavailable':
            return 'VEIL Desktop shell is not open; nothing was shown.', '出错', summary
        return ('The display could not be confirmed; the message may not have appeared. '
                'Check the VEIL Desktop state before retrying.'), '出错', summary

    def __enter__(self):
        return self

    def __exit__(self, *_):
        if self.client:
            self.client.close()
        if self.web_tools:
            self.web_tools.close()

    def _add_startup_notice(self, notice):
        self.startup_notice = (self.startup_notice + ' ' + notice
                               if self.startup_notice else notice)


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


