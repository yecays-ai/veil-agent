"""Ticket 114: Peter may create new files in Lan's Windows Desktop, Documents and Pictures folders.

Ticket 133: files he created himself (recorded in a program ledger, unchanged since) may also be
rewritten, with the previous version kept by the program, or moved into his lab/trash/. Lan's own
files are never opened for writing, renamed or deleted. There is no daily count and no fixed subfolder.
"""
import ctypes
from datetime import datetime
import hashlib
import json
import os
import re
import shutil
import stat
import uuid
from pathlib import Path, PureWindowsPath
from types import SimpleNamespace


DESKTOP_FILE_TOOL_NAMES = frozenset({'desktop_create_file', 'desktop_move_to_trash'})
MAX_CREATE_BYTES = 512 * 1024
# Text formats only; nothing Windows would run when double-clicked.
CREATE_EXTENSIONS = frozenset({'.txt', '.md', '.html', '.htm', '.svg', '.csv', '.json', '.css', '.xml'})
FOLDER_IDS = {
    'desktop': '{B4BFCC3A-DB2C-424C-B029-7FE99A87C641}',
    'documents': '{FDD39AD0-238F-46AF-ADB4-6C85480369C7}',
    'pictures': '{33E28130-4E1E-4676-835A-98395C3BC3BB}',
}
FALLBACK_NAMES = {'desktop': 'Desktop', 'documents': 'Documents', 'pictures': 'Pictures'}
_RESERVED = {'CON', 'PRN', 'AUX', 'NUL', 'CONIN$', 'CONOUT$'}

DESKTOP_FILE_TOOL_SCHEMAS = [SimpleNamespace(
    name='desktop_create_file',
    description=(
        "Create a new text file in Lan's Windows Desktop, Documents or Pictures folder: notes, "
        'documents, HTML pages, SVG pictures, anything you make. path is relative to that folder and may '
        'include new subfolders. Allowed extensions: ' + ', '.join(sorted(CREATE_EXTENSIONS)) +
        f'. At most {MAX_CREATE_BYTES // 1024} KB per file. No approval and no daily limit. '
        'With replace: true you may rewrite a file you created yourself with this tool; the program keeps '
        "the previous version. Lan's own files, and files changed since you made them, are never changed. "
        'The result gives the full path, so you can open the file with an allowlisted app or tell Lan where it is.'
    ),
    inputSchema={'type': 'object', 'properties': {
        'folder': {'type': 'string', 'enum': sorted(FOLDER_IDS)},
        'path': {'type': 'string', 'description': 'Relative path such as "notes/raccoon-plan.md".'},
        'content': {'type': 'string'},
        'replace': {'type': 'boolean', 'description': 'true rewrites a file you created earlier at this path.'},
    }, 'required': ['folder', 'path', 'content'], 'additionalProperties': False},
), SimpleNamespace(
    name='desktop_move_to_trash',
    description=(
        "Move a file you created yourself with desktop_create_file out of Lan's folder into your lab/trash/. "
        "Nothing is deleted; Lan's own files and files changed since you made them cannot be moved."
    ),
    inputSchema={'type': 'object', 'properties': {
        'folder': {'type': 'string', 'enum': sorted(FOLDER_IDS)},
        'path': {'type': 'string', 'description': 'The same relative path you created it with.'},
    }, 'required': ['folder', 'path'], 'additionalProperties': False},
)]


def keep_previous_version(root, relative, data):
    """Save bytes that are about to be replaced under root, never inside Peter's own folders."""
    stamp = datetime.now().astimezone().strftime('%Y%m%dT%H%M%S%f')
    target = Path(root) / Path(*relative.parts[:-1]) / f'{stamp}-{relative.name}'
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open('xb') as handle:
        handle.write(data)
        handle.flush()
        os.fsync(handle.fileno())
    return target


def replace_file_bytes(target, data):
    """Write data next to target, then swap it in, so a failure never leaves a half-written file."""
    temporary = target.with_name(f'.{target.name}.{uuid.uuid4().hex}.tmp')
    try:
        with temporary.open('xb') as handle:
            if handle.write(data) != len(data):
                raise OSError('short write')
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, target)
    finally:
        temporary.unlink(missing_ok=True)
    if hashlib.sha256(target.read_bytes()).digest() != hashlib.sha256(data).digest():
        raise OSError('write verification failed')


def _identity(info):
    return [info.st_ino, info.st_size, info.st_mtime_ns]


def _known_folder(name):
    """The real (possibly OneDrive-redirected) known folder path."""
    if os.name == 'nt':
        class GUID(ctypes.Structure):
            _fields_ = [('Data1', ctypes.c_uint32), ('Data2', ctypes.c_uint16),
                        ('Data3', ctypes.c_uint16), ('Data4', ctypes.c_ubyte * 8)]
        raw = uuid.UUID(FOLDER_IDS[name]).bytes_le
        guid = GUID.from_buffer_copy(raw)
        path = ctypes.c_wchar_p()
        if ctypes.windll.shell32.SHGetKnownFolderPath(ctypes.byref(guid), 0, None, ctypes.byref(path)) == 0:
            try:
                return Path(path.value)
            finally:
                ctypes.windll.ole32.CoTaskMemFree(path)
    return Path.home() / FALLBACK_NAMES[name]


def desktop_item_names(limit=40, *, folder_for=_known_folder):
    """Names (never contents) of what sits on Lan's Desktop, for a glance at the bare desktop."""
    try:
        base = folder_for('desktop')
        entries = sorted(base.iterdir(), key=lambda entry: entry.stat().st_mtime, reverse=True)
    except OSError:
        return None
    names = []
    for entry in entries:
        try:
            hidden = getattr(entry.stat(), 'st_file_attributes', 0) & getattr(stat, 'FILE_ATTRIBUTE_HIDDEN', 2)
        except OSError:
            continue
        if hidden or entry.name.startswith('.') or entry.name.casefold() == 'desktop.ini':
            continue
        names.append(entry.name + ('/' if entry.is_dir() else ''))
    return names[:limit], len(names) > limit


def _is_link(path):
    info = path.lstat()
    return stat.S_ISLNK(info.st_mode) or bool(
        getattr(info, 'st_file_attributes', 0) & getattr(stat, 'FILE_ATTRIBUTE_REPARSE_POINT', 0x400))


def _name_problem(part):
    if part in ('', '.', '..'):
        return True
    if any(ord(c) < 32 or c in '<>:"|?*/\\' for c in part) or part.endswith((' ', '.')):
        return True
    stem = part.split('.')[0].upper()
    return stem in _RESERVED or re.fullmatch(r'(COM|LPT)[1-9¹²³]', stem) is not None


class DesktopFileTools:
    def __init__(self, enabled, *, folder_for=_known_folder):
        self.enabled = bool(enabled)
        self.folder_for = folder_for
        self.tools = list(DESKTOP_FILE_TOOL_SCHEMAS) if self.enabled else []
        # Bound by FilesystemTools once the role folder and lab are known (ticket 133).
        self.ledger_path = self.versions_root = None
        self.trash_for = None

    def bind_own_work(self, ledger_path, versions_root, trash_for):
        self.ledger_path, self.versions_root, self.trash_for = Path(ledger_path), Path(versions_root), trash_for

    def _ledger(self):
        if self.ledger_path is None:
            return {}
        try:
            data = json.loads(self.ledger_path.read_text(encoding='utf-8'))
        except FileNotFoundError:
            return {}
        except (OSError, ValueError, UnicodeError):
            return None
        return data if isinstance(data, dict) else None

    def _save_ledger(self, ledger):
        self.ledger_path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.ledger_path.with_suffix('.tmp')
        temporary.write_text(json.dumps(ledger, ensure_ascii=False), encoding='utf-8')
        os.replace(temporary, self.ledger_path)

    def _record(self, target):
        ledger = self._ledger()
        if ledger is None or self.ledger_path is None:
            return
        ledger[str(target)] = _identity(target.stat())
        try:
            self._save_ledger(ledger)
        except OSError:
            pass

    def _own_file(self, target):
        """True only for a plain file Peter created that nobody has changed since."""
        ledger = self._ledger()
        if not ledger or str(target) not in ledger:
            return False
        try:
            info = target.stat(follow_symlinks=False)
        except OSError:
            return False
        return (stat.S_ISREG(info.st_mode) and not _is_link(target) and info.st_nlink == 1
                and ledger[str(target)] == _identity(info))

    def _resolve(self, folder, raw):
        if folder not in FOLDER_IDS or not isinstance(raw, str):
            return None, None
        parts = PureWindowsPath(raw.strip()).parts
        if (not parts or PureWindowsPath(raw.strip()).anchor or '\x00' in raw
                or any(_name_problem(part) for part in parts)):
            return None, None
        return parts, f'{folder}/' + '/'.join(parts)

    def _move_to_trash(self, args):
        folder, raw = args.get('folder'), args.get('path')
        parts, summary = self._resolve(folder, raw)
        if parts is None:
            return 'Use folder desktop, documents or pictures and the relative path you created.', '被拒绝', 'desktop_move_to_trash'
        trash = self.trash_for() if callable(self.trash_for) else None
        if trash is None:
            return 'Your lab is unavailable, so there is no trash to move into.', '被拒绝', summary
        try:
            base = self.folder_for(folder)
            target = base.joinpath(*parts)
            if any(_is_link(base.joinpath(*parts[:end])) for end in range(len(parts) + 1)):
                return 'A path component is a link; nothing was moved.', '被拒绝', summary
            if not self._own_file(target):
                return ("Only a file you created yourself, unchanged since, can be moved. "
                        'Nothing was moved.', '被拒绝', summary)
            destination = trash / folder / Path(*parts)
            destination.parent.mkdir(parents=True, exist_ok=True)
            if destination.exists() or destination.is_symlink():
                stamp = datetime.now().astimezone().strftime('%Y%m%dT%H%M%S')
                destination = destination.with_name(f'{destination.stem}-{stamp}{destination.suffix}')
            if destination.exists():
                return 'A file with that name is already in the trash; nothing was moved.', '被拒绝', summary
            data = target.read_bytes()
            with destination.open('xb') as handle:
                handle.write(data)
                handle.flush()
                os.fsync(handle.fileno())
            if not self._own_file(target) or hashlib.sha256(destination.read_bytes()).digest() != hashlib.sha256(data).digest():
                destination.unlink(missing_ok=True)
                return 'The file changed while it was being moved; nothing was moved.', '被拒绝', summary
            target.unlink()
        except OSError:
            return 'The file could not be moved. Nothing was deleted.', '出错', summary
        ledger = self._ledger() or {}
        ledger.pop(str(target), None)
        try:
            self._save_ledger(ledger)
        except OSError:
            pass
        return f'Moved {target} into lab/trash/{folder}/{"/".join(destination.relative_to(trash / folder).parts)}.', '成功', summary

    def invoke(self, name, args):
        """Returns (result, state, summary) like the other file tools."""
        if not self.enabled or name not in DESKTOP_FILE_TOOL_NAMES:
            return 'Creating files on the Windows desktop is not available here.', '被拒绝', name
        if name == 'desktop_move_to_trash':
            return self._move_to_trash(args)
        folder, raw, content = args.get('folder'), args.get('path'), args.get('content')
        replace = args.get('replace', False)
        if not isinstance(content, str) or type(replace) is not bool:
            return 'Use folder desktop, documents or pictures, a relative path, and text content.', '被拒绝', name
        parts, summary = self._resolve(folder, raw)
        if parts is None:
            if folder not in FOLDER_IDS or not isinstance(raw, str):
                return 'Use folder desktop, documents or pictures, a relative path, and text content.', '被拒绝', name
            return ('path must be relative to the chosen folder, with valid Windows names and no "..".',
                    '被拒绝', name)
        if PureWindowsPath(parts[-1]).suffix.lower() not in CREATE_EXTENSIONS:
            return ('This file type cannot be created. Allowed extensions: '
                    + ', '.join(sorted(CREATE_EXTENSIONS)) + '.', '被拒绝', summary)
        data = content.encode('utf-8')
        if len(data) > MAX_CREATE_BYTES:
            return (f'Content is {len(data)} bytes; the limit is {MAX_CREATE_BYTES} bytes per file. '
                    'Split it into several files.', '被拒绝', summary)
        try:
            base = self.folder_for(folder)
            if not base.is_dir() or _is_link(base):
                return 'That Windows folder is unavailable.', '出错', summary
            current = base
            for part in parts[:-1]:
                current = current / part
                if current.exists() or current.is_symlink():
                    if _is_link(current) or not current.is_dir():
                        return ('A path component is not a plain folder; choose another path.', '被拒绝', summary)
                else:
                    current.mkdir()
            target = current / parts[-1]
            if replace and (target.exists() or target.is_symlink()):
                if self.versions_root is None or not self._own_file(target):
                    return ("Only a file you created yourself, unchanged since, can be rewritten. "
                            'Nothing was changed; choose a new name.', '被拒绝', summary)
                old = target.read_bytes()
                if not self._own_file(target):
                    return 'The file changed while it was being read; nothing was changed.', '被拒绝', summary
                keep_previous_version(self.versions_root, Path(folder, *parts), old)
                replace_file_bytes(target, data)
                self._record(target)
                return (f'Rewrote {target} ({len(data)} bytes); the program kept the previous version.',
                        '成功', summary)
            # 'x' never opens an existing file: an existing name fails here, even one that appeared just now.
            with target.open('xb') as handle:
                handle.write(data)
            self._record(target)
        except FileExistsError:
            return ('That file already exists. Existing files are never overwritten; to rewrite a file you '
                    'created yourself, pass replace: true.', '被拒绝', summary)
        except OSError:
            return 'The file could not be created. Check the path and try a different name.', '出错', summary
        return f'Created {target} ({len(data)} bytes).', '成功', summary
