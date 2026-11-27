"""Ticket 458: the migration tool must be safe to run on a dirty branch."""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts' / 'dev'))

import migrate_layout as migrate  # noqa: E402


class MigrationToolTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.git('init', '-q')
        self.git('config', 'user.name', 'Test')
        self.git('config', 'user.email', 'test@example.com')

    def git(self, *args):
        subprocess.run(['git', '-c', 'safe.directory=*', '-C', str(self.root), *args],
                       check=True, capture_output=True)

    def write(self, relative, text, newline='\n'):
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding='utf-8', newline=newline)
        return path

    def commit(self):
        self.git('add', '-A')
        self.git('commit', '-qm', 'fixture')

    def test_package_moves_before_modules_and_keeps_prompt_init(self):
        self.write('prompts/__init__.py', 'VALUE = 1\n')
        self.write('prompts/defaults/memory.x.md', 'text\n')
        self.write('instructions.py', 'from prompts import registry\n')
        self.commit()
        moved, created = migrate.move_batch(
            self.root,
            {'instructions': 'veil/prompts'},
            {'prompts': {'target_dir': 'veil/prompts', 'target_module': 'veil.prompts'}})
        self.assertTrue((self.root / 'veil/prompts/__init__.py').is_file())
        self.assertTrue((self.root / 'veil/prompts/instructions.py').is_file())
        self.assertFalse((self.root / 'prompts/__init__.py').exists())
        self.assertGreaterEqual(len(moved), 3)

    def test_preflight_reports_existing_destination_without_moving(self):
        self.write('instructions.py', 'VALUE = 1\n')
        self.write('veil/prompts/instructions.py', 'existing\n')
        self.commit()
        problems = migrate.preflight_moves(
            self.root, {'instructions': 'veil/prompts'}, {})
        self.assertTrue(problems)
        self.assertTrue((self.root / 'instructions.py').is_file())
        self.assertEqual((self.root / 'veil/prompts/instructions.py').read_text(encoding='utf-8'),
                         'existing\n')

    def test_rewrite_preserves_crlf_and_creates_compatibility_shim(self):
        source = self.write('instructions.py',
                            'from agent_types import X\r\n\r\nVALUE = 1\r\n', newline='')
        self.commit()
        changed = migrate.rewrite_file(source, {'agent_types': 'veil.core.agent_types'}, {})
        self.assertTrue(changed)
        raw = source.read_bytes()
        self.assertIn(b'from veil.core.agent_types import X\r\n', raw)
        self.assertNotIn(b'\n', raw.replace(b'\r\n', b''))
        shim = migrate.create_root_shims(
            self.root, {'web_agent': 'veil.server.web_agent'}, ['web_agent.py'])
        self.assertEqual(len(shim), 1)
        text = (self.root / 'web_agent.py').read_text(encoding='utf-8')
        self.assertIn('from veil.server import web_agent', text)
        self.assertNotIn('runpy', text)


if __name__ == '__main__':
    unittest.main()
