"""Repository layout guard tests for Ticket 458."""
from __future__ import annotations

import ast
import json
import subprocess
import unittest
from pathlib import Path

from veil.paths import PROJECT_ROOT, repo_path

ROOT = Path(__file__).resolve().parents[1]
MAP_PATH = ROOT / 'scripts' / 'dev' / 'module_map.json'

# Flipped to True in batch B7 once all modules have migrated into veil/.
STRICT_LAYOUT = False


def _tracked_files() -> list[str]:
    out = subprocess.run(
        ['git', '-c', 'safe.directory=*', 'ls-files'],
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding='utf-8',
        check=True,
    ).stdout
    return [line for line in out.splitlines() if line]


class RepoLayoutGuardTests(unittest.TestCase):
    def test_project_root_matches_repository_root(self):
        self.assertEqual(PROJECT_ROOT, ROOT)
        self.assertEqual(repo_path('data'), ROOT / 'data')
        self.assertEqual(repo_path('characters', 'peter'), ROOT / 'characters' / 'peter')

    def test_module_map_has_no_duplicates(self):
        data = json.loads(MAP_PATH.read_text(encoding='utf-8'))
        seen = {}
        for batch, dirs in data['batches'].items():
            for target_dir, mods in dirs.items():
                for mod in mods:
                    self.assertNotIn(mod, seen, f'Duplicate module {mod} in {batch} and {seen.get(mod)}')
                    seen[mod] = (batch, target_dir)
        self.assertEqual(len(seen), 104)

    def test_rollback_script_never_fetches_or_pulls(self):
        ps1 = (ROOT / 'scripts' / 'rollback_version.ps1').read_text(encoding='utf-8')
        cmd = (ROOT / 'scripts' / 'windows' / 'rollback-version.cmd').read_text(encoding='utf-8')
        for label, content in (('rollback_version.ps1', ps1), ('rollback-version.cmd', cmd)):
            lowered = content.lower()
            for line in lowered.splitlines():
                stripped = line.strip()
                if stripped.startswith(('#', 'rem ', 'echo ')):
                    continue
                self.assertNotIn('git fetch', stripped, f'{label} must never run git fetch')
                self.assertNotIn('git pull', stripped, f'{label} must never run git pull')

    def test_root_python_files_and_legacy_imports(self):
        if not STRICT_LAYOUT:
            self.skipTest('Strict layout assertions activate in batch B7 after all batches migrate')
        data = json.loads(MAP_PATH.read_text(encoding='utf-8'))
        allowed_shims = set(data['root_shims'])
        tracked = _tracked_files()
        root_py = {f for f in tracked if '/' not in f and f.endswith('.py')}
        self.assertEqual(root_py, allowed_shims, f'Unexpected root .py files: {sorted(root_py - allowed_shims)}')

        legacy_names = set()
        for dirs in data['batches'].values():
            for target_dir, mods in dirs.items():
                if target_dir.startswith('veil'):
                    legacy_names.update(mods)
        legacy_names.update(data.get('packages', {}).keys())

        offenders = []
        for rel in tracked:
            if not rel.endswith('.py') or rel in allowed_shims:
                continue
            if rel in ('scripts/dev/migrate_layout.py', 'tests/test_repo_layout.py'):
                continue
            tree = ast.parse((ROOT / rel).read_text(encoding='utf-8'), filename=rel)
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        top = alias.name.split('.', 1)[0]
                        if top in legacy_names:
                            offenders.append(f'{rel}:{node.lineno}: import {alias.name}')
                elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                    top = node.module.split('.', 1)[0]
                    if top in legacy_names:
                        offenders.append(f'{rel}:{node.lineno}: from {node.module} import ...')
        self.assertEqual(offenders, [], 'Legacy top-level imports remain:\n' + '\n'.join(offenders[:30]))


if __name__ == '__main__':
    unittest.main()
