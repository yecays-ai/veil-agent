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

from capability_state import capability_transaction, save_capabilities


VERSION = 1
ALLOW = {
    'list_directory', 'directory_tree', 'read_text_file', 'read_multiple_files',
    'get_file_info', 'search_files', 'list_directory_with_sizes',
}
ASK = {'write_file', 'append_file', 'grant_append'}
DENY = {'edit_file', 'move_file'}
LAB_ONLY = {'create_directory', 'move_lab_file', 'lab_file_digest'}
# Memory tools (ticket 007) are offered by memory_native.ToolSet, never by the filesystem server.
NATIVE = {'hold', 'breath_search', 'trace', 'dream', 'letter_write', 'letter_read',
          'letter_lock_update', 'anchor', 'release', 'plan', 'pulse', 'memory_history', 'memory_catalog', 'memory_read'}


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
    def __init__(self, root, cfg, store):
        from veil_agent import UserError
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
        if cfg.tools is None:
            self.enabled = False
            self.description = 'You have no tools (cannot open files/folders or run scripts). '
            self.client = None
            self.tools = []
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
        self.tools = []
        self.client = None
        self.capability_event = None
        self.capability_event_model = None
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
                      and tool.name not in ('append_file', 'grant_append', 'create_directory', 'move_lab_file')]
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

    @capability_transaction
    def _record_capabilities(self, names=None):
        names = names or []
        core = {'version': VERSION, 'folder': self.folder_name, 'tools': {
            name: tool_policy(name) for name in names},
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
                   'voice_output_announced': bool(previous and previous.get('voice_output_announced'))}
            self._record_update(row, self.capability_event)
            if previous != row:
                save_capabilities(self.cap_path, row)
        except Exception:
            # Never permit tools if capability state cannot be durably recorded.
            self.enabled = False
            from veil_agent import UserError
            raise UserError('能力清单无法读取或保存，文件工具已停止；请保留 data 文件夹。')

    @staticmethod
    def _permission_text(level):
        return {
            'allow': 'direct use in the configured folder',
            'ask': "Lan's approval before use outside the lab folder",
            'lab': 'direct use only inside the lab folder; no overwrite, deletion, or scripts',
        }.get(level, 'not available')

    def _capability_event(self, core, changes):
        return (f'Capability update: in the "{core["folder"]}" folder, ' + ' '.join(changes))

    def _legacy_capability_event(self, core, previous):
        changes = ([f'Current {name} permission: {self._permission_text(level)}.'
                    for name, level in sorted(core['tools'].items())]
                   or ['No file tools are currently available.'])
        if previous.get('autonomy_announced'):
            changes.append('Autonomous activity remains enabled; you may act or choose not to on each scheduled wake.')
        if previous.get('long_tasks_announced'):
            changes.append('Long-task tools remain available to save one active task and continue it after restart.')
        if previous.get('photo_input_announced'):
            changes.append('Photo input remains available; the newest photo is visible directly and older photos use placeholders.')
        if previous.get('voice_output_announced'):
            changes.append('Voice output remains available through send_message with voice: true.')
        memory_tools = previous.get('memory_tools')
        if isinstance(memory_tools, list) and memory_tools:
            changes.append('Memory tools are available: ' + ', '.join(str(name) for name in memory_tools) +
                           '. Deletion through trace requires explicit UI approval; requests made during '
                           'autonomous activity remain pending for later approval.')
        return self._capability_event(core, changes)

    @property
    def capability_record(self):
        try:
            row = json.loads(self.cap_path.read_text(encoding='utf-8'))
            updates = row.get('capability_updates', [])
            if not isinstance(updates, list):
                return None
            lines = [f'{item["at"]}: {item["text"]}' for item in updates
                     if isinstance(item, dict) and isinstance(item.get('at'), str)
                     and isinstance(item.get('text'), str)]
            return ('CAPABILITY RECORD (program-maintained; use it when asked what changed, do not claim an '
                    'unlisted permission).\n' + '\n'.join(lines)) if lines else None
        except (OSError, ValueError, TypeError, KeyError):
            return None

    @staticmethod
    def _record_update(row, event):
        if not isinstance(event, str) or not event:
            return
        updates = row.get('capability_updates')
        updates = updates if isinstance(updates, list) else []
        if not updates or not isinstance(updates[-1], dict) or updates[-1].get('text') != event:
            updates = [*updates[-20:], {'at': datetime.now(timezone.utc).isoformat(), 'text': event}]
        row['capability_updates'] = updates

    @capability_transaction
    def record_capability_change(self, event):
        """Persist a dated runtime ability change in the existing role history."""
        try:
            row = json.loads(self.cap_path.read_text(encoding='utf-8')) if self.cap_path.exists() else {}
            if not isinstance(row, dict):
                raise ValueError('invalid capability record')
            self._record_update(row, event)
            save_capabilities(self.cap_path, row)
        except Exception as exc:
            from veil_agent import UserError
            raise UserError('能力清单无法更新，未切换自主活动状态；请保留 data 文件夹。') from exc

    @capability_transaction
    def consume_capability_event(self, delivered_event=None):
        try:
            row = json.loads(self.cap_path.read_text(encoding='utf-8'))
            if delivered_event is not None and row.get('pending_event') != delivered_event:
                self.capability_event = row.get('pending_event')
                self.capability_event_model = self.capability_event
                return False
            row['pending_event'] = None
            row['pending_event_model'] = None
            save_capabilities(self.cap_path, row)
            self.capability_event = None
            self.capability_event_model = None
            return True
        except Exception:
            self.enabled = False
            from veil_agent import UserError
            raise UserError('能力清单无法更新，文件工具已停止；请保留 data 文件夹。')

    @capability_transaction
    def announce_autonomy(self):
        if getattr(self, 'cap_path', None) is None:
            return
        try:
            row = json.loads(self.cap_path.read_text(encoding='utf-8')) if self.cap_path.exists() else {}
            if not isinstance(row, dict):
                raise ValueError('invalid capability record')
            if row.get('autonomy_announced'):
                return
            event = ('Capability update: autonomous activity is enabled: you will wake on the configured schedule and may act or '
                     'choose not to. If send_message is available, you may send up to three chat messages per wake, or none.')
            previous_event = row.get('pending_event')
            if isinstance(previous_event, str) and previous_event and event not in previous_event:
                event = previous_event + '\n' + event
            row['autonomy_announced'] = True
            row['pending_event'] = event
            row['pending_event_model'] = event
            self._record_update(row, event)
            save_capabilities(self.cap_path, row)
            self.capability_event = event
            self.capability_event_model = row['pending_event_model']
        except Exception as exc:
            self.enabled = False
            from veil_agent import UserError
            raise UserError('能力清单无法更新，未开启自主活动；请保留 data 文件夹。') from exc

    @capability_transaction
    def announce_long_tasks(self):
        if getattr(self, 'cap_path', None) is None:
            return
        try:
            row = json.loads(self.cap_path.read_text(encoding='utf-8')) if self.cap_path.exists() else {}
            if row.get('long_tasks_announced'):
                self.capability_event = row.get('pending_event')
                self.capability_event_model = row.get('pending_event_model')
                return
            event = ('Capability update: long-task tools are available to save one active task, record confirmed '
                     'progress and next steps, and continue after autonomous activity or a service restart. '
                     'Existing budgets, tool permissions, and approval rules still apply.')
            previous_event = row.get('pending_event')
            if isinstance(previous_event, str) and previous_event and event not in previous_event:
                event = previous_event + '\n' + event
            row['long_tasks_announced'] = True
            row['pending_event'] = event
            row['pending_event_model'] = event
            self._record_update(row, event)
            save_capabilities(self.cap_path, row)
            self.capability_event = event
            self.capability_event_model = row['pending_event_model']
        except Exception as exc:
            self.enabled = False
            from veil_agent import UserError
            raise UserError('长期任务能力通知无法安全保存，工具已停止；请保留 data 文件夹。') from exc

    @capability_transaction
    def announce_photo_input(self):
        if getattr(self, 'cap_path', None) is None:
            return
        try:
            row = json.loads(self.cap_path.read_text(encoding='utf-8')) if self.cap_path.exists() else {}
            if not isinstance(row, dict):
                raise ValueError('invalid capability record')
            if row.get('photo_input_announced'):
                self.capability_event = row.get('pending_event')
                self.capability_event_model = row.get('pending_event_model')
                return
            event = ('Capability update: Lan can now send you photos in chat; you see the newest one '
                     'directly. Older photos appear as [Lan sent a photo].')
            previous_event = row.get('pending_event')
            if isinstance(previous_event, str) and previous_event and event not in previous_event:
                event = previous_event + '\n' + event
            row['photo_input_announced'] = True
            row['pending_event'] = event
            row['pending_event_model'] = event
            self._record_update(row, event)
            save_capabilities(self.cap_path, row)
            self.capability_event = event
            self.capability_event_model = event
            return True
        except Exception:
            return False

    @capability_transaction
    def announce_voice_input(self):
        if getattr(self, 'cap_path', None) is None:
            return
        try:
            row = json.loads(self.cap_path.read_text(encoding='utf-8')) if self.cap_path.exists() else {}
            if not isinstance(row, dict):
                raise ValueError('invalid capability record')
            if row.get('voice_input_announced'):
                self.capability_event = row.get('pending_event')
                self.capability_event_model = row.get('pending_event_model')
                return
            event = ('Capability update: Lan can now send you voice messages; you receive them as '
                     '[voice message] followed by the transcript.')
            previous_event = row.get('pending_event')
            if isinstance(previous_event, str) and previous_event and event not in previous_event:
                event = previous_event + '\n' + event
            row['voice_input_announced'] = True
            row['pending_event'] = event
            row['pending_event_model'] = event
            self._record_update(row, event)
            save_capabilities(self.cap_path, row)
            self.capability_event = event
            self.capability_event_model = event
            return True
        except Exception as exc:
            self.voice_notice_error = str(exc)
            return False

    @capability_transaction
    def suppress_voice_output_notice(self):
        """Drop an undelivered availability notice when local voice config is absent."""
        if getattr(self, 'cap_path', None) is None:
            return
        try:
            row = json.loads(self.cap_path.read_text(encoding='utf-8')) if self.cap_path.exists() else {}
            if not isinstance(row, dict):
                raise ValueError('invalid capability record')
            marker = 'send_message with voice: true'
            pending = row.get('pending_event')
            if isinstance(pending, str) and marker in pending:
                remaining = '\n'.join(line for line in pending.splitlines() if marker not in line).strip()
                row['pending_event'] = remaining or None
                row['pending_event_model'] = remaining or None
                row['voice_output_announced'] = False
                save_capabilities(self.cap_path, row)
            self.capability_event = row.get('pending_event')
            self.capability_event_model = row.get('pending_event_model')
            return True
        except Exception:
            return False

    @capability_transaction
    def announce_voice_output(self):
        if getattr(self, 'cap_path', None) is None:
            return
        try:
            row = json.loads(self.cap_path.read_text(encoding='utf-8')) if self.cap_path.exists() else {}
            if not isinstance(row, dict):
                raise ValueError('invalid capability record')
            if row.get('voice_output_announced'):
                self.capability_event = row.get('pending_event')
                self.capability_event_model = row.get('pending_event_model')
                return
            event = ('Capability update: You can now send a message as a voice message in your own voice '
                     'by calling send_message with voice: true.')
            previous_event = row.get('pending_event')
            if isinstance(previous_event, str) and previous_event and event not in previous_event:
                event = previous_event + '\n' + event
            row['voice_output_announced'] = True
            row['pending_event'] = event
            row['pending_event_model'] = event
            self._record_update(row, event)
            save_capabilities(self.cap_path, row)
            self.capability_event = event
            self.capability_event_model = event
            return True
        except Exception:
            return False

    def _description(self, names):
        if not names:
            return 'You currently have no tools. '
        entries = [f'{name} (ask lan first)' if name in ASK else f'{name} (direct use)' for name in names]
        description = f'You can use tools only in the "{self.folder_name}" folder: ' + ', '.join(entries) + '. '
        if getattr(self, 'lab', None):
            lab = self.lab.name
            description += (
                f'Your own lab is open at {lab}/, relative to the workspace tool root; '
                f'use paths such as {lab}/research/v1.md, never workspace/{lab}/. '
                'Choose its contents yourself. Within lab you may create directories and new files '
                'and append to existing files without asking. You may move a regular file only '
                'between a normal lab folder and lab/archive/; keep its relative path when archiving '
                'and choose any lab folder when restoring. Existing destinations are rejected. '
                'Never overwrite, edit in place, delete, move folders, move outside the lab, or '
                'execute scripts (the script allowlist is empty). Use plan B: '
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
            return 'This tool is not authorized for the current role. Choose an available tool; permissions cannot be bypassed.', '被拒绝', name
        if name == 'move_lab_file':
            return self._move_lab_file(args)
        if name == 'lab_file_digest':
            return self._lab_file_digest(args)
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
                    return 'Directories can only be created inside the configured lab folder.', '被拒绝', summary
            if name in ('append_file', 'grant_append'):
                return self._invoke_append(name, next(iter(resolved.values())), summary, args, approval)
            if policy == 'ask':
                target = next(iter(resolved.values()))
                exists = target.exists()
                if exists:
                    return 'The target already exists and overwriting is not allowed. Choose a new filename or use an authorized append operation.', '被拒绝', summary
                action = approval(f'Peter 想把内容写到 {summary}（新文件）。同意吗？y/N ')
                if action.strip().casefold() != 'y':
                    return 'Lan did not approve this write, so the file was not changed. Ask for approval if it is still needed.', '被拒绝', summary
                # Check again after the user has had time to answer.
                target = checked_path(self.workspace, str(target))
                if target.exists():
                    return 'The target already exists and overwriting is not allowed. Choose a new filename or use an authorized append operation.', '被拒绝', summary
                content = args.get('content')
                if not isinstance(content, str):
                    return 'New file content must be text.', '被拒绝', summary
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
            return 'The target already exists and overwriting is not allowed. Choose a new filename or use an authorized append operation.', '被拒绝', summary
        except ValueError:
            value = args.get('path', name)
            return 'The path is outside the configured workspace or cannot be resolved safely. Use a permitted path inside the workspace.', '被拒绝', Path(str(value)).name or name
        except Exception:
            value = args.get('path', name)
            return 'The tool failed. Check the target state before retrying.', '出错', Path(str(value)).name or name

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

    def _ensure_lab_parent(self, raw):
        parts = re.split(r'[/\\]', raw)
        if len(parts) < 2:
            raise ValueError('destination must name a file')
        parent_parts = parts[:-1]
        for end in range(1, len(parent_parts) + 1):
            directory_raw = '/'.join(parent_parts[:end])
            directory = self._checked_lab_path(directory_raw)
            if directory.exists():
                if _redirected(directory) or not directory.is_dir():
                    raise ValueError('destination parent is not a normal directory')
                continue
            try:
                directory.mkdir()
            except FileExistsError:
                pass
            directory = self._checked_lab_path(directory_raw)
            if not directory.is_dir() or _redirected(directory):
                raise ValueError('destination parent changed')

    def _move_lab_file(self, args):
        summary = 'move_lab_file'
        source = destination = None
        moved_identity = None
        move_completed = False
        try:
            if (not isinstance(args, dict) or set(args) != {'source', 'destination'} or
                    not all(isinstance(args[key], str) for key in ('source', 'destination'))):
                return 'Move requires source and destination lab paths. No file was moved.', '被拒绝', summary
            source_raw, destination_raw = args['source'], args['destination']
            source = self._checked_lab_path(source_raw)
            destination = self._checked_lab_path(destination_raw)
            summary = f'{_summary(self.workspace, source)} -> {_summary(self.workspace, destination)}'
            source_relative = source.relative_to(self.lab)
            destination_relative = destination.relative_to(self.lab)
            source_in_archive = bool(source_relative.parts and source_relative.parts[0].casefold() == 'archive')
            destination_in_archive = bool(destination_relative.parts and
                                           destination_relative.parts[0].casefold() == 'archive')
            if source_in_archive == destination_in_archive:
                return ('A file may move only between a normal lab folder and lab/archive/. '
                        'No file was moved.', '被拒绝', summary)
            if destination_in_archive:
                expected = ('archive', *source_relative.parts)
                if tuple(part.casefold() for part in destination_relative.parts) != tuple(
                        part.casefold() for part in expected):
                    return ('Archiving must preserve the file path under lab/archive/. No file was moved.',
                            '被拒绝', summary)
            if source.is_symlink() or _redirected(source) or not source.is_file():
                return 'Only an existing regular file can be moved. No file was moved.', '被拒绝', summary
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


