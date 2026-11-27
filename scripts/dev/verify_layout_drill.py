"""Ticket 458: isolated, synthetic old->new upgrade and rollback drill.

The drill clones this repository into a temporary directory, adds only fictional data,
checks that the old layout upgrades in place, then runs the real offline rollback script
back to the old commit.  It never touches the real data/, persona/, .env, or a running
Peter.  Its JSON output contains commit ids and synthetic hashes only.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

SYNTHETIC = {
    'data/peter/costs.jsonl': '{"request_id":"fictional-drill-1","kind":"settle","amount":"0.00"}\n',
    'data/peter/chat.jsonl': '{"message_id":"fictional-drill-1","role":"user","content":"FICTIONAL DRILL"}\n',
    'data/peter/brain.db': 'FICTIONAL BRAIN PLACEHOLDER\n',
    'persona/peter.md': '# Fictional drill persona\n\nThis file exists only in the temporary drill clone.\n',
    '.env': 'VEIL_DRILL_FICTIONAL=1\n',
}


def run(args, cwd, *, check=True, timeout=180):
    return subprocess.run([str(item) for item in args], cwd=str(cwd), capture_output=True,
                          text=True, encoding='utf-8', errors='replace', timeout=timeout,
                          check=check)


def git(cwd, *args, check=True):
    return run(['git', '-c', 'safe.directory=*', '-C', cwd, *args], cwd, check=check)


def synthetic_digest(root: Path) -> str:
    rows = []
    for relative in sorted(SYNTHETIC):
        path = root / relative
        rows.append(relative + ':' + hashlib.sha256(path.read_bytes()).hexdigest())
    return hashlib.sha256('\n'.join(rows).encode('utf-8')).hexdigest()


def write_synthetic(root: Path) -> None:
    for relative, text in SYNTHETIC.items():
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding='utf-8', newline='\n')


def entrypoint_results(root: Path):
    commands = [
        ('python -m veil', [sys.executable, '-m', 'veil', '--help']),
        ('data_repair.py', [sys.executable, 'data_repair.py', '--help']),
        ('veil_brain.py', [sys.executable, 'veil_brain.py', '--help']),
        ('web_agent.py', [sys.executable, 'web_agent.py', '--help']),
        ('web_launcher.py', [sys.executable, 'web_launcher.py', '--help']),
        ('backup_restore.py', [sys.executable, 'backup_restore.py', '--help']),
        ('veil_agent.py', [sys.executable, 'veil_agent.py', '--help']),
    ]
    results = {}
    for label, command in commands:
        results[label] = run(command, root, check=False, timeout=90).returncode
    return results


def rollback(root: Path, target: str, *, skip_package=True):
    command = ['powershell.exe', '-NoProfile', '-ExecutionPolicy', 'Bypass',
               '-File', str(root / 'scripts' / 'rollback_version.ps1'),
               '-RepositoryPath', str(root), '-TargetRef', target]
    if skip_package:
        command += ['-SkipPackage', '-SkipPythonDeps']
    return run(command, root, check=False, timeout=180)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--repo', default=str(Path(__file__).resolve().parents[2]))
    parser.add_argument('--old', default='d78a8b7')
    parser.add_argument('--new', default='baa74f4')
    parser.add_argument('--out', default='')
    parser.add_argument('--keep', action='store_true')
    args = parser.parse_args(argv)

    source = Path(args.repo).resolve()
    old = git(source, 'rev-parse', args.old).stdout.strip()
    new = git(source, 'rev-parse', args.new).stdout.strip()
    if not old or not new:
        raise SystemExit('could not resolve --old/--new')

    temp = Path(tempfile.mkdtemp(prefix='veil-458-drill-'))
    clone = temp / 'veil-agent'
    result = {'tool': 'pr458-isolation-drill', 'old_commit': old, 'new_commit': new,
              'synthetic_files': len(SYNTHETIC), 'os': platform.system(),
              'python': platform.python_version()}
    try:
        run(['git', 'clone', '--quiet', '--no-hardlinks', '--no-checkout', str(source), str(clone)],
            temp, timeout=300)
        git(clone, 'checkout', '-q', old)
        write_synthetic(clone)
        result['upgrade_before_sha256'] = synthetic_digest(clone)
        result['old_root_web_agent'] = (clone / 'web_agent.py').is_file()
        git(clone, 'checkout', '-q', new)
        result['upgrade_after_sha256'] = synthetic_digest(clone)
        result['upgrade_data_unchanged'] = result['upgrade_before_sha256'] == result['upgrade_after_sha256']
        result['new_layout_present'] = (clone / 'veil' / 'server' / 'web_agent.py').is_file()
        result['entrypoints'] = entrypoint_results(clone)

        rollback_result = rollback(clone, old)
        result['rollback_exit'] = rollback_result.returncode
        result['rollback_head'] = git(clone, 'rev-parse', 'HEAD').stdout.strip()
        result['rollback_ok'] = (rollback_result.returncode == 0 and result['rollback_head'] == old)
        result['rollback_data_unchanged'] = synthetic_digest(clone) == result['upgrade_before_sha256']
        result['old_layout_restored'] = ((clone / 'prompts' / 'README.md').is_file()
                                           and not (clone / 'veil' / 'server' / 'web_agent.py').is_file())

        git(clone, 'checkout', '-q', new)
        conflict = clone / 'restore-persona.cmd'
        conflict.write_text('FICTIONAL LOCAL FILE\n', encoding='utf-8', newline='\n')
        conflict_result = rollback(clone, old)
        result['conflict_guard_exit'] = conflict_result.returncode
        result['conflict_guard_aborted'] = conflict_result.returncode != 0
        result['conflict_head'] = git(clone, 'rev-parse', 'HEAD').stdout.strip()
        result['conflict_head_unchanged'] = result['conflict_head'] == new
        result['conflict_file_intact'] = conflict.read_text(encoding='utf-8') == 'FICTIONAL LOCAL FILE\n'
        conflict.unlink()
    finally:
        if args.keep:
            result['temp_kept'] = True
        else:
            shutil.rmtree(temp, ignore_errors=True)

    text = json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True)
    if args.out:
        Path(args.out).write_text(text + '\n', encoding='utf-8', newline='\n')
    print(text)
    passed = (result.get('upgrade_data_unchanged') and result.get('new_layout_present')
              and result.get('rollback_ok') and result.get('rollback_data_unchanged')
              and result.get('old_layout_restored') and result.get('conflict_guard_aborted')
              and result.get('conflict_head_unchanged') and result.get('conflict_file_intact')
              and all(code == 0 for code in result.get('entrypoints', {}).values()))
    return 0 if passed else 1


if __name__ == '__main__':
    raise SystemExit(main())
