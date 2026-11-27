"""Rewrite VEIL flat module imports to package-qualified paths using module_map.json.

Usage:
    python scripts/dev/migrate_layout.py --batch B1 --move --rewrite
    python scripts/dev/migrate_layout.py --batch B1 --rewrite
    python scripts/dev/migrate_layout.py --all --rewrite   # useful on parallel branches
    python scripts/dev/migrate_layout.py --all --check

A move is preflighted before anything changes.  A failed move or rewrite reverses
what this run changed and restores the pre-run file contents from .local-tmp.
"""
from __future__ import annotations

import argparse
import ast
import json
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

MAP_PATH = Path(__file__).with_name('module_map.json')
REPO_ROOT = Path(__file__).resolve().parents[2]


def load_mapping(batches: list[str] | None = None) -> tuple[dict[str, str], dict[str, str], dict[str, dict], list[str]]:
    """Return (mod_to_fqn, mod_to_dir, packages, root_shims) for the selected batches."""
    data = json.loads(MAP_PATH.read_text(encoding='utf-8'))
    selected = set(batches) if batches else set(data['batches'].keys())
    mod_to_fqn: dict[str, str] = {}
    mod_to_dir: dict[str, str] = {}
    for batch_name, dirs in data['batches'].items():
        if batch_name not in selected:
            continue
        for target_dir, mods in dirs.items():
            pkg = target_dir.replace('/', '.')
            for mod in mods:
                mod_to_dir[mod] = target_dir
                # scripts/simplify_memory.py is a standalone CLI script, not a veil package module
                if target_dir.startswith('veil'):
                    mod_to_fqn[mod] = f'{pkg}.{mod}'
    packages = {
        name: info
        for name, info in data.get('packages', {}).items()
        if info.get('batch') in selected
    }
    root_shims = list(data.get('root_shims', []))
    return mod_to_fqn, mod_to_dir, packages, root_shims


def _ensure_init_files(root: Path, target_dir: str) -> list[Path]:
    parts = Path(target_dir).parts
    if not parts or parts[0] != 'veil':
        return []
    created: list[Path] = []
    current = root
    for part in parts:
        current = current / part
        current.mkdir(parents=True, exist_ok=True)
        init_file = current / '__init__.py'
        if not init_file.exists():
            init_file.write_text('', encoding='utf-8', newline='\n')
            created.append(init_file)
    return created


def preflight_moves(root: Path, mod_to_dir: dict[str, str], packages: dict[str, dict]) -> list[str]:
    """Return every destination conflict before a single file is moved."""
    problems: list[str] = []
    for pkg_name, info in packages.items():
        src_dir = root / pkg_name
        dst_dir = root / info['target_dir']
        if not src_dir.is_dir():
            continue
        if dst_dir.exists() and not dst_dir.is_dir():
            problems.append(f'{info["target_dir"]} already exists and is not a directory')
            continue
        if dst_dir.exists() and any(dst_dir.iterdir()):
            problems.append(f'{info["target_dir"]}/ already exists and is not empty')
            continue
        for item in sorted(src_dir.iterdir()):
            if (dst_dir / item.name).exists():
                problems.append(f'{info["target_dir"]}/{item.name} already exists')
    for mod, target_dir in mod_to_dir.items():
        src = root / f'{mod}.py'
        if not src.exists():
            continue
        dst = root / target_dir / f'{mod}.py'
        if dst.exists():
            problems.append(f'{target_dir}/{mod}.py already exists')
    return problems


def _git_mv(root: Path, src: Path, dst: Path) -> None:
    subprocess.run(
        ['git', '-c', 'safe.directory=*', 'mv',
         str(src.relative_to(root)), str(dst.relative_to(root))],
        cwd=root,
        check=True,
    )


def reverse_moves(root: Path, moved: list[tuple[Path, Path]]) -> list[str]:
    errors: list[str] = []
    for src, dst in reversed(moved):
        try:
            _git_mv(root, dst, src)
        except Exception as exc:  # keep reversing the rest; report all failures
            errors.append(f'{dst.relative_to(root)} -> {src.relative_to(root)}: {exc}')
    return errors


def move_batch(root: Path, mod_to_dir: dict[str, str], packages: dict[str, dict]) -> tuple[list[tuple[Path, Path]], list[Path]]:
    """Move package directories first, then modules.  Return (moved pairs, created inits)."""
    moved: list[tuple[Path, Path]] = []
    created: list[Path] = []
    try:
        for pkg_name, info in packages.items():
            src_dir = root / pkg_name
            dst_dir = root / info['target_dir']
            if not src_dir.is_dir():
                continue
            dst_dir.parent.mkdir(parents=True, exist_ok=True)
            dst_dir.mkdir(parents=True, exist_ok=True)
            for item in sorted(src_dir.iterdir()):
                target = dst_dir / item.name
                _git_mv(root, item, target)
                moved.append((item, target))
        for mod, target_dir in mod_to_dir.items():
            src = root / f'{mod}.py'
            if not src.exists():
                continue
            created.extend(_ensure_init_files(root, target_dir))
            dst = root / target_dir / f'{mod}.py'
            dst.parent.mkdir(parents=True, exist_ok=True)
            _git_mv(root, src, dst)
            moved.append((src, dst))
    except Exception:
        errors = reverse_moves(root, moved)
        for init_file in reversed(created):
            try:
                init_file.unlink(missing_ok=True)
            except OSError as exc:
                errors.append(f'remove {init_file.relative_to(root)}: {exc}')
        if errors:
            print('move rollback was incomplete:', file=sys.stderr)
            for error in errors:
                print('  ' + error, file=sys.stderr)
        raise
    return moved, created


SHIM_TEMPLATE = '''"""Compatibility entrypoint kept at repository root for legacy commands."""
from {parent} import {leaf}

if __name__ == '__main__':
    raise SystemExit({leaf}.main())
'''


def create_root_shims(root: Path, mod_to_fqn: dict[str, str], root_shims: list[str]) -> list[Path]:
    created: list[Path] = []
    for name in root_shims:
        if not name.endswith('.py'):
            continue
        mod = name[:-3]
        target = mod_to_fqn.get(mod)
        if not target:
            continue
        destination = root / name
        if destination.exists():
            continue
        parent, leaf = target.rsplit('.', 1)
        destination.write_text(SHIM_TEMPLATE.format(parent=parent, leaf=leaf),
                               encoding='utf-8', newline='\n')
        created.append(destination)
    return created


def _read_text(path: Path) -> str:
    with path.open('r', encoding='utf-8', newline='') as handle:
        return handle.read()


def _write_text(path: Path, text: str) -> None:
    with path.open('w', encoding='utf-8', newline='') as handle:
        handle.write(text)


def snapshot_files(root: Path, files: list[Path], backup_dir: Path) -> None:
    for path in files:
        relative = path.relative_to(root)
        target = backup_dir / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, target)


def restore_snapshot(root: Path, backup_dir: Path) -> list[str]:
    errors: list[str] = []
    for source in sorted(backup_dir.rglob('*')):
        if not source.is_file():
            continue
        relative = source.relative_to(backup_dir)
        target = root / relative
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
        except OSError as exc:
            errors.append(f'{relative}: {exc}')
    return errors


def _rewrite_import_line(
    line: str,
    mod_to_fqn: dict[str, str],
    packages: dict[str, dict],
) -> str:
    # Preserve newline
    eol = '\r\n' if line.endswith('\r\n') else ('\n' if line.endswith('\n') else '')
    body = line[:-len(eol)] if eol else line

    # Split trailing comment safely (only outside quotes; import lines have no string literals)
    comment = ''
    if '#' in body:
        idx = body.index('#')
        comment = body[idx:]
        body = body[:idx].rstrip()

    indent_match = re.match(r'^(\s*)', body)
    indent = indent_match.group(1) if indent_match else ''
    stripped = body[len(indent):]

    # Case 1: from <mod>(.<sub...>) import ...
    m_from = re.match(r'^from\s+([A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)*)\s+import\s+(.*)$', stripped)
    if m_from:
        mod_path, rest = m_from.group(1), m_from.group(2)
        top = mod_path.split('.', 1)[0]
        if mod_path in mod_to_fqn:
            new_mod = mod_to_fqn[mod_path]
            suffix = f'  {comment}' if comment else ''
            return f'{indent}from {new_mod} import {rest}{suffix}{eol}'
        if top in packages:
            new_top = packages[top]['target_module']
            new_mod = new_top + mod_path[len(top):]
            suffix = f'  {comment}' if comment else ''
            return f'{indent}from {new_mod} import {rest}{suffix}{eol}'
        return line

    # Case 2: import <a [as b]>, <c [as d]>
    m_imp = re.match(r'^import\s+(.+)$', stripped)
    if m_imp:
        raw_items = [item.strip() for item in m_imp.group(1).split(',')]
        rewritten = []
        changed = False
        for item in raw_items:
            m_alias = re.match(r'^([A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)*)(?:\s+as\s+([A-Za-z_][A-Za-z0-9_]*))?$', item)
            if not m_alias:
                return line
            name, alias = m_alias.group(1), m_alias.group(2)
            top = name.split('.', 1)[0]
            if name in mod_to_fqn:
                fqn = mod_to_fqn[name]
                parent, leaf = fqn.rsplit('.', 1)
                if alias:
                    rewritten.append(f'{indent}from {parent} import {leaf} as {alias}')
                else:
                    rewritten.append(f'{indent}from {parent} import {leaf}')
                changed = True
            elif top in packages:
                target_pkg = packages[top]['target_module']
                if '.' not in name:
                    parent, leaf = target_pkg.rsplit('.', 1)
                    if alias:
                        rewritten.append(f'{indent}from {parent} import {leaf} as {alias}')
                    else:
                        rewritten.append(f'{indent}from {parent} import {leaf}')
                else:
                    sub = name[len(top) + 1:]
                    if alias:
                        rewritten.append(f'{indent}import {target_pkg}.{sub} as {alias}')
                    else:
                        rewritten.append(f'{indent}import {target_pkg}.{sub}')
                changed = True
            else:
                if alias:
                    rewritten.append(f'{indent}import {name} as {alias}')
                else:
                    rewritten.append(f'{indent}import {name}')
        if changed:
            if comment:
                rewritten[-1] = f'{rewritten[-1]}  {comment}'
            return eol.join(rewritten) + eol
    return line


def _rewrite_patch_strings(
    text: str,
    mod_to_fqn: dict[str, str],
    packages: dict[str, dict],
) -> str:
    if not mod_to_fqn and not packages:
        return text

    # Rewrite mock.patch("mod.attr") / patch('mod.attr') / importlib.import_module('mod')
    all_targets = dict(mod_to_fqn)
    for pkg_name, info in packages.items():
        all_targets[pkg_name] = info['target_module']

    keys = sorted(all_targets.keys(), key=len, reverse=True)
    if not keys:
        return text
    key_alt = '|'.join(re.escape(k) for k in keys)

    # 1. patch('mod.attr') or patch("mod.attr")
    patch_rx = re.compile(
        rf"""(patch(?:\.object)?\(\s*)(['"])({key_alt})(\.[A-Za-z0-9_.]+)(['"])"""
    )
    text = patch_rx.sub(
        lambda m: f'{m.group(1)}{m.group(2)}{all_targets[m.group(3)]}{m.group(4)}{m.group(5)}',
        text,
    )

    # 2. importlib.import_module('mod')
    imp_mod_rx = re.compile(
        rf"""(import_module\(\s*)(['"])({key_alt})(['"])"""
    )
    text = imp_mod_rx.sub(
        lambda m: f'{m.group(1)}{m.group(2)}{all_targets[m.group(3)]}{m.group(4)}',
        text,
    )
    return text


def rewrite_file(
    path: Path,
    mod_to_fqn: dict[str, str],
    packages: dict[str, dict],
    *,
    dry_run: bool = False,
) -> bool:
    original = _read_text(path)
    lines = original.splitlines(keepends=True)
    new_lines = [_rewrite_import_line(line, mod_to_fqn, packages) for line in lines]
    updated = ''.join(new_lines)
    updated = _rewrite_patch_strings(updated, mod_to_fqn, packages)
    if updated == original:
        return False
    # Verify AST remains valid Python before writing
    ast.parse(updated, filename=str(path))
    if not dry_run:
        _write_text(path, updated)
    return True


def iter_python_files(root: Path) -> list[Path]:
    out = subprocess.run(
        ['git', '-c', 'safe.directory=*', 'ls-files', '*.py'],
        cwd=root,
        capture_output=True,
        text=True,
        encoding='utf-8',
        check=True,
    ).stdout.splitlines()
    result = []
    for rel in out:
        if not rel or rel.endswith('scripts/dev/migrate_layout.py'):
            continue
        p = root / rel
        if p.is_file():
            result.append(p)
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description='Migrate VEIL flat module layout to veil/ package.')
    parser.add_argument('--root', default=str(REPO_ROOT))
    parser.add_argument('--batch', action='append', help='Batch ID (B1..B6); repeatable')
    parser.add_argument('--all', action='store_true', help='Select all batches')
    parser.add_argument('--move', action='store_true', help='git mv files for selected batches')
    parser.add_argument('--rewrite', action='store_true', help='Rewrite imports and mock.patch strings')
    parser.add_argument('--check', action='store_true', help='Report conflicts/changes without modifying')
    args = parser.parse_args()

    root = Path(args.root).resolve()
    batches = None if args.all else args.batch
    if not args.all and not batches:
        parser.error('Specify --batch <ID> or --all')
    if not args.move and not args.rewrite and not args.check:
        parser.error('Specify --move, --rewrite or --check')

    mod_to_fqn, mod_to_dir, packages, root_shims = load_mapping(batches)

    if args.move:
        problems = preflight_moves(root, mod_to_dir, packages)
        if problems:
            print('PRE-FLIGHT CONFLICTS:')
            for problem in problems:
                print('  ' + problem)
            return 1
        if args.check:
            planned_packages = sum(1 for name in packages if (root / name).is_dir())
            planned_modules = sum(1 for mod in mod_to_dir if (root / f'{mod}.py').exists())
            print(f'WOULD MOVE: {planned_packages} package directories and {planned_modules} module files; no conflicts')
            if not args.rewrite:
                return 0

    files = iter_python_files(root) if (args.rewrite or args.check) else []
    if args.check:
        changed = []
        for py_file in files:
            try:
                if rewrite_file(py_file, mod_to_fqn, packages, dry_run=True):
                    changed.append(py_file.relative_to(root).as_posix())
            except (OSError, SyntaxError) as exc:
                print(f'REWRITE CONFLICT {py_file.relative_to(root)}: {exc}', file=sys.stderr)
                return 1
        print(f'WOULD CHANGE: {len(changed)} files')
        for item in changed[:40]:
            print(f'  {item}')
        if len(changed) > 40:
            print(f'  ... and {len(changed) - 40} more')
        return 1 if changed else 0

    stamp = time.strftime('%Y%m%d-%H%M%S')
    backup_dir = root / '.local-tmp' / f'layout-migration-{stamp}'
    moved: list[tuple[Path, Path]] = []
    created: list[Path] = []
    shims: list[Path] = []
    try:
        if args.rewrite:
            snapshot_files(root, files, backup_dir)
            for py_file in files:
                rewrite_file(py_file, mod_to_fqn, packages)
        if args.move:
            moved, created = move_batch(root, mod_to_dir, packages)
            shims = create_root_shims(root, mod_to_fqn, root_shims)
    except Exception as exc:
        if moved:
            errors = reverse_moves(root, moved)
            if errors:
                print('reverse moves were incomplete:', file=sys.stderr)
                for error in errors:
                    print('  ' + error, file=sys.stderr)
        for shim in shims:
            try:
                shim.unlink(missing_ok=True)
            except OSError:
                pass
        for init_file in reversed(created):
            try:
                init_file.unlink(missing_ok=True)
            except OSError:
                pass
        if backup_dir.is_dir():
            errors = restore_snapshot(root, backup_dir)
            if errors:
                print('restore snapshot was incomplete:', file=sys.stderr)
                for error in errors:
                    print('  ' + error, file=sys.stderr)
            print(f'Pre-run files restored from {backup_dir}', file=sys.stderr)
        print(f'migration failed: {exc}', file=sys.stderr)
        return 1

    if args.rewrite:
        print(f'REWRITTEN: {len(files)} files')
    if args.move:
        print(f'MOVED: {len(moved)} files; created {len(shims)} root compatibility entrypoints')
    if args.rewrite:
        print(f'Pre-run file snapshot kept at {backup_dir}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
