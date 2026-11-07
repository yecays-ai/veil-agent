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
import subprocess
import time
from pathlib import Path

MARKERS = ('.git', 'pyproject.toml', 'package.json', 'requirements.txt', 'Cargo.toml', 'go.mod', 'pom.xml')
SKIP_DIRS = {'node_modules', '.venv', 'venv', '__pycache__', '.git', '.ssh', 'secrets', 'appdata', '$recycle.bin',
             'windows', 'program files', 'program files (x86)', '.local-tmp', 'site-packages'}
COPY_SKIP = {'.git', 'node_modules', '__pycache__', '.venv', 'venv', '.local-tmp'}
MAX_COPY_BYTES = 200 * 1024 * 1024
MAX_COPY_FILES = 20000
_GIT_ENV = {'GIT_TERMINAL_PROMPT': '0'}


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
                    if child.is_dir() and not child.is_symlink():
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


def _git(root, *args):
    return subprocess.run(['git', '-C', str(root), *args], capture_output=True, text=True, timeout=60,
                          env={**os.environ, **_GIT_ENV})


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
            shutil.copy2(path, dest)
        except OSError:
            pass
    return True


def snapshot(project, store, task_id):
    """Keep a way back for `project`. Returns a metadata dict; kind is 'git', 'copy' or 'none' (could not)."""
    project, store = Path(project), Path(store) / task_id
    store.mkdir(parents=True, exist_ok=True)
    meta = {'id': task_id, 'project': str(project), 'taken': time.time(), 'dir': str(store)}
    head = _git(project, 'rev-parse', '--verify', 'HEAD') if (project / '.git').exists() else None
    if head is not None and head.returncode == 0:
        # `git stash create` only builds a commit object of the tracked changes; it touches no branch, index or file.
        stash = _git(project, 'stash', 'create').stdout.strip()
        base = stash or head.stdout.strip()
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
