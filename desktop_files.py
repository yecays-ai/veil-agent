"""Ticket 114: Peter may create new files in Lan's Windows Desktop, Documents and Pictures folders.

Creating only: the program refuses any path that already exists, never opens an existing file for
writing, and never deletes or renames anything. There is no daily count and no fixed subfolder.
"""
import ctypes
import os
import re
import stat
import uuid
from pathlib import Path, PureWindowsPath
from types import SimpleNamespace


DESKTOP_FILE_TOOL_NAMES = frozenset({'desktop_create_file'})
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
        f'. At most {MAX_CREATE_BYTES // 1024} KB per file. No approval and no daily limit. The program '
        'only creates: if the path already exists it refuses, so existing files are never overwritten, '
        'changed or deleted. The result gives the full path, so you can open the file with an allowlisted '
        'app or tell Lan where it is.'
    ),
    inputSchema={'type': 'object', 'properties': {
        'folder': {'type': 'string', 'enum': sorted(FOLDER_IDS)},
        'path': {'type': 'string', 'description': 'Relative path such as "notes/raccoon-plan.md".'},
        'content': {'type': 'string'},
    }, 'required': ['folder', 'path', 'content'], 'additionalProperties': False},
)]


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

    def invoke(self, name, args):
        """Returns (result, state, summary) like the other file tools."""
        if not self.enabled or name not in DESKTOP_FILE_TOOL_NAMES:
            return 'Creating files on the Windows desktop is not available here.', '被拒绝', name
        folder, raw, content = args.get('folder'), args.get('path'), args.get('content')
        if folder not in FOLDER_IDS or not isinstance(raw, str) or not isinstance(content, str):
            return 'Use folder desktop, documents or pictures, a relative path, and text content.', '被拒绝', name
        parts = PureWindowsPath(raw.strip()).parts
        if (not parts or PureWindowsPath(raw.strip()).anchor or '\x00' in raw
                or any(_name_problem(part) for part in parts)):
            return ('path must be relative to the chosen folder, with valid Windows names and no "..".',
                    '被拒绝', name)
        summary = f'{folder}/' + '/'.join(parts)
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
            # 'x' never opens an existing file: an existing name fails here, even one that appeared just now.
            with target.open('xb') as handle:
                handle.write(data)
        except FileExistsError:
            return ('That file already exists; existing files are never overwritten. Choose a new name.',
                    '被拒绝', summary)
        except OSError:
            return 'The file could not be created. Check the path and try a different name.', '出错', summary
        return f'Created {target} ({len(data)} bytes).', '成功', summary
