"""Ticket 238: how the workbench finds Peter's work target and protects what is already there.

Two jobs, both plain code:
* find_projects(): Peter's task names a project (or just describes a problem); search the allowed roots for
  folders that look like projects. Lan never types a path.
* snapshot()/restore(): before the workbench may change an existing project, keep a way back. With git the
  snapshot is a ref under refs/veil-snapshots (no branch, index, working-tree or stash change, no commit on
  the user's branch); untracked files are copied beside it. Without git the project files are copied.
"""
import json
import os
import re
import shutil
import stat
import subprocess
import time
import uuid
from pathlib import Path
from windows_process import hidden_subprocess_kwargs

MARKERS = ('.git', 'pyproject.toml', 'package.json', 'requirements.txt', 'Cargo.toml', 'go.mod', 'pom.xml')
SKIP_DIRS = {'node_modules', '.venv', 'venv', '__pycache__', '.git', '.ssh', 'secrets', 'appdata', '$recycle.bin',
             'windows', 'program files', 'program files (x86)', '.local-tmp', 'site-packages'}
COPY_SKIP = {'.git', 'node_modules', '__pycache__', '.venv', 'venv', '.local-tmp'}
GENERIC_NAMES = {'assess', 'review', 'reviews', 'project', 'projects', 'setup', 'build', 'tests', 'test',
                 'notes', 'note', 'reports', 'report', 'code', 'source', 'app', 'apps', 'docs', 'documents',
                 'files', 'archive', 'backup', 'temp', 'tmp', 'work', 'workspace', 'tools', 'utils', 'server',
                 'config', 'output', 'scripts'}
MAX_COPY_BYTES = 200 * 1024 * 1024
MAX_COPY_FILES = 20000
_GIT_ENV = {'GIT_TERMINAL_PROMPT': '0'}
_WINDOWS = os.name == 'nt'
_REPARSE = getattr(stat, 'FILE_ATTRIBUTE_REPARSE_POINT', 0x400)


def _ordinary_dir(path):
    """A real directory, not a symlink or a Windows junction/reparse point."""
    try:
        info = path.lstat()
    except OSError:
        return False
    return (stat.S_ISDIR(info.st_mode) and not Path(path).is_symlink()
            and not (getattr(info, 'st_file_attributes', 0) & _REPARSE))


def _tokens(text):
    return [t for t in re.split(r'[^0-9a-zA-Z一-鿿]+', (text or '').lower()) if len(t) >= 2]


def find_projects(hint, roots, *, limit=5, max_depth=3):
    """Candidate project folders under `roots`, best first: [{'path','name','score','markers'}].

    A hint (project name or words from the task) raises the score of name matches; recently touched projects win ties.
    """
    words = _tokens(hint)
    found = []
    for root in roots or []:
        root = Path(root)
        if not root.is_dir():
            continue
        stack = [(root, 0)]
        while stack:
            folder, depth = stack.pop()
            try:
                names = os.listdir(folder)
            except OSError:
                continue
            markers = [m for m in MARKERS if m in names]
            if markers and depth > 0 or (markers and depth == 0):
                try:
                    touched = max((folder / m).stat().st_mtime for m in markers)
                except OSError:
                    touched = 0
                name = folder.name.lower()
                score = sum(3 for w in words if w in name) + sum(1 for w in words if w in str(folder).lower())
                found.append({'path': str(folder), 'name': folder.name, 'score': score, 'markers': markers,
                              'touched': touched})
                continue  # a project's subfolders are not separate candidates
            if depth < max_depth:
                for name in names:
                    child = folder / name
                    if name.lower() in SKIP_DIRS or name.startswith('.'):
                        continue
                    if _ordinary_dir(child):
                        stack.append((child, depth + 1))
    found.sort(key=lambda p: (-p['score'], -p['touched']))
    return found[:limit]


def choose_project(hint, roots):
    """('one', project) when the choice is clear, ('many', [projects]) when it is not, ('none', None)."""
    found = find_projects(hint, roots, limit=5)
    if not found:
        return 'none', None
    if not _tokens(hint):
        return ('one', found[0]) if len(found) == 1 else ('many', found)
    top = found[0]
    if top['score'] == 0:
        return 'none', None
    if len(found) == 1 or found[1]['score'] < top['score']:
        return 'one', top
    return 'many', [p for p in found if p['score'] == top['score']]


def find_named_workspace_folders(name, root):
    """Exact project-name matches inside Peter's workspace, including lightweight test projects."""
    if (not isinstance(name, str) or not name.strip() or name in {'.', '..'} or
            any(char in name for char in '/\\:*?[]{}')):
        return []
    wanted = name.casefold()
    if wanted in GENERIC_NAMES:
        return []
    found, stack = [], [(Path(root), 0)]
    while stack:
        folder, depth = stack.pop()
        try:
            children = list(folder.iterdir())
        except OSError:
            continue
        for child in children:
            child_name = child.name.casefold()
            if child_name in SKIP_DIRS or child.name.startswith('.') or not _ordinary_dir(child):
                continue
            if child_name == wanted:
                found.append(child)
            elif depth < 3:
                stack.append((child, depth + 1))
    return found


def find_task_named_workspace_folders(task, root):
    """Use a workspace subproject only when its literal, non-generic folder name appears as a token in the task."""
    text = task.casefold() if isinstance(task, str) else ''
    if not text:
        return []
    found, stack = [], [(Path(root), 0)]
    while stack:
        folder, depth = stack.pop()
        try:
            children = list(folder.iterdir())
        except OSError:
            continue
        for child in children:
            name = child.name.casefold()
            if name in SKIP_DIRS or child.name.startswith('.') or not _ordinary_dir(child):
                continue
            if (len(name) >= 5 and name not in GENERIC_NAMES and
                    re.search(r'(?<![0-9a-z\u4e00-\u9fff])' + re.escape(name) +
                              r'(?![0-9a-z\u4e00-\u9fff])', text)):
                found.append(child)
            elif depth < 3:
                stack.append((child, depth + 1))
    return found


def _git(root, *args):
    return subprocess.run(['git', '-C', str(root), *args], capture_output=True, encoding='utf-8', errors='replace', timeout=60,
                          env={**os.environ, **_GIT_ENV},
                          **hidden_subprocess_kwargs(windows=_WINDOWS, module=subprocess))


def _copy_tree(source, target, files=None):
    total = count = 0
    pairs = []
    if files is None:
        for base, dirs, names in os.walk(source):
            dirs[:] = [d for d in dirs if d not in COPY_SKIP]
            pairs += [Path(base) / n for n in names]
    else:
        pairs = [Path(source) / f for f in files]
    for path in pairs:
        try:
            if path.is_symlink():
                continue
            total += path.stat().st_size
        except OSError:
            continue
        count += 1
        if total > MAX_COPY_BYTES or count > MAX_COPY_FILES:
            return False
    for path in pairs:
        dest = Path(target) / path.relative_to(source)
        dest.parent.mkdir(parents=True, exist_ok=True)
        try:
            if path.is_symlink():  # a link is not followed: copying its target could overwrite something outside later
                continue
            shutil.copy2(path, dest)
        except OSError:
            return False
    return True


KEEP_SNAPSHOTS = 20


def prune(store, keep=None):
    """Keep the newest `keep` snapshots; older folders and their git refs are removed."""
    keep = KEEP_SNAPSHOTS if keep is None else keep
    folders = sorted((p for p in Path(store).iterdir() if (p / 'meta.json').exists()),
                     key=lambda p: p.stat().st_mtime, reverse=True)
    for old in folders[keep:]:
        try:
            meta = json.loads((old / 'meta.json').read_text(encoding='utf-8'))
            if meta.get('kind') == 'git':
                _git(meta['project'], 'update-ref', '-d', meta['ref'])
        except (OSError, ValueError):
            pass
        shutil.rmtree(old, ignore_errors=True)


def snapshot(project, store, task_id):
    """Keep a way back for `project`. Returns a metadata dict; kind is 'git', 'copy' or 'none' (could not)."""
    project, store = Path(project), Path(store) / task_id
    store.mkdir(parents=True, exist_ok=True)
    meta = {'id': task_id, 'project': str(project), 'taken': time.time(), 'dir': str(store)}
    head = _git(project, 'rev-parse', '--verify', 'HEAD') if (project / '.git').exists() else None
    if head is not None and head.returncode == 0:
        # `git stash create` only builds a commit object of the tracked changes; it touches no branch, index or file.
        made = _git(project, 'stash', 'create')
        if made.returncode != 0:
            meta.update(kind='none', note='git could not record the uncommitted changes')
            (store / 'meta.json').write_text(json.dumps(meta), encoding='utf-8')
            return meta
        base = made.stdout.strip() or head.stdout.strip()
        ref = f'refs/veil-snapshots/{task_id}'
        if _git(project, 'update-ref', ref, base).returncode == 0:
            untracked = [f for f in _git(project, 'ls-files', '--others', '--exclude-standard', '-z').stdout.split('\0') if f]
            if untracked and not _copy_tree(project, store / 'untracked', untracked):
                meta.update(kind='none', note='too many untracked files to keep')
            else:
                meta.update(kind='git', ref=ref, commit=base, untracked=untracked)
            (store / 'meta.json').write_text(json.dumps(meta), encoding='utf-8')
            return meta
    if _copy_tree(project, store / 'files'):
        meta.update(kind='copy')
    else:
        shutil.rmtree(store / 'files', ignore_errors=True)
        meta.update(kind='none', note='project too large to copy')
    (store / 'meta.json').write_text(json.dumps(meta), encoding='utf-8')
    prune(store.parent)
    return meta


def restore(meta):
    """Put the project back as it was when the snapshot was taken. Files created since are left alone."""
    project = Path(meta['project'])
    if meta['kind'] == 'git':
        check = _git(project, 'checkout', meta['commit'], '--', '.')
        if check.returncode != 0:
            return False
        saved = Path(meta['dir']) / 'untracked'
        for rel in meta.get('untracked', []):
            if (saved / rel).exists():
                (project / rel).parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(saved / rel, project / rel)
        _git(project, 'reset', '-q')  # `checkout <commit> -- .` stages the files; put the index back
        return True
    if meta['kind'] == 'copy':
        return _copy_tree(Path(meta['dir']) / 'files', project)
    return False


def merge_legacy_workbench(old, workspace):
    """Ticket 247: Peter has one file root. Whatever the old separate workbench folder held moves into
    workspace/from-workbench/ (nothing is overwritten or deleted; an entry that cannot move stays where it was).
    Returns the names that moved."""
    old, workspace = Path(old), Path(workspace)
    reparse = getattr(stat, 'FILE_ATTRIBUTE_REPARSE_POINT', 0x400)
    def ordinary_dir(path):
        try:
            info = path.lstat()
            return stat.S_ISDIR(info.st_mode) and not (getattr(info, 'st_file_attributes', 0) & reparse)
        except OSError:
            return False
    if not ordinary_dir(old) or not ordinary_dir(workspace):
        return []
    try:
        names = os.listdir(old)
    except OSError:
        return []
    moved = []
    target = workspace / 'from-workbench'
    if target.exists() or target.is_symlink():
        if not ordinary_dir(target):
            return []
    for name in names:
        source, dest = old / name, target / name
        temp = target / ('.' + name + '.migrating-' + uuid.uuid4().hex)
        try:
            source_info = source.lstat()
            if (source.is_symlink() or getattr(source_info, 'st_file_attributes', 0) & reparse
                    or dest.exists() or dest.is_symlink()):
                continue
            target.mkdir(parents=True, exist_ok=True)
            if not ordinary_dir(target):
                continue
            if source.is_dir():
                shutil.copytree(source, temp, symlinks=True)
                os.replace(temp, dest)
                shutil.rmtree(source)
            else:
                shutil.copy2(source, temp)
                os.replace(temp, dest)
                source.unlink()
            moved.append(name)
        except OSError:
            if temp.is_dir() and not temp.is_symlink():
                shutil.rmtree(temp, ignore_errors=True)
            else:
                temp.unlink(missing_ok=True)
            continue
    try:
        old.rmdir()  # only succeeds when it is empty
    except OSError:
        pass
    return moved
