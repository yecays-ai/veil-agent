"""Ticket 458: root compatibility entrypoints still reach the real CLIs."""
from __future__ import annotations

import os
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SHIMS = ('web_agent.py', 'web_launcher.py', 'backup_restore.py',
         'veil_agent.py', 'data_repair.py', 'veil_brain.py')


class EntrypointTests(unittest.TestCase):
    def run_shim(self, name, *args):
        env = dict(os.environ)
        env['PYTHONDONTWRITEBYTECODE'] = '1'
        # The real CLIs print Chinese argparse text; a Windows CI console defaults to cp1252.
        env['PYTHONUTF8'] = '1'
        env['PYTHONIOENCODING'] = 'utf-8'
        return subprocess.run([sys.executable, str(ROOT / name), *args],
                              cwd=ROOT, capture_output=True, text=True, encoding='utf-8',
                              errors='replace', env=env, timeout=60)

    def test_saved_repair_and_brain_cli_commands_still_resolve(self):
        for name in ('data_repair.py', 'veil_brain.py'):
            with self.subTest(name=name):
                result = self.run_shim(name, '--help')
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn('usage:', result.stdout.lower())

    def test_desktop_and_terminal_shims_still_resolve(self):
        for name in SHIMS:
            if name in ('data_repair.py', 'veil_brain.py', 'web_launcher.py'):
                continue
            with self.subTest(name=name):
                result = self.run_shim(name, '--help')
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn('usage:', result.stdout.lower())

    def test_web_launcher_shim_imports_without_starting_the_service(self):
        env = dict(os.environ)
        env['PYTHONDONTWRITEBYTECODE'] = '1'
        result = subprocess.run(
            [sys.executable, '-c', 'import web_launcher; assert web_launcher.web_launcher.main'],
            cwd=ROOT, capture_output=True, text=True, encoding='utf-8', errors='replace',
            env=env, timeout=60)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_root_shims_import_the_real_module_instead_of_reloading_it(self):
        for name in (*SHIMS, 'setup_fetch.py'):
            with self.subTest(name=name):
                source = (ROOT / name).read_text(encoding='utf-8')
                self.assertNotIn('runpy', source)
                self.assertIn('from veil', source)


if __name__ == '__main__':
    unittest.main()
