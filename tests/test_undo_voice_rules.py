import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BEGIN = '<!-- VEIL-VOICE-RULES-BEGIN -->'
END = '<!-- VEIL-VOICE-RULES-END -->'


@unittest.skipUnless(shutil.which('powershell'), 'needs Windows PowerShell')
class UndoVoiceRulesTests(unittest.TestCase):
    def run_undo(self, root):
        return subprocess.run(['powershell', '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', str(root / 'undo-voice-rules.ps1')],
                              capture_output=True, timeout=60)

    def make_root(self, tmp, body):
        root = Path(tmp)
        (root / 'persona').mkdir()
        shutil.copy(ROOT / 'undo-voice-rules.ps1', root)
        (root / 'persona' / 'peter.md').write_bytes(body.encode('utf-8'))
        return root

    def test_removes_only_the_marked_block_and_keeps_a_copy(self):
        original = 'Fictional intro\n\nFictional outro\n'
        block = f'\n{BEGIN}\n## Fictional rules\n1. Fictional rule\n{END}\n'
        with tempfile.TemporaryDirectory() as tmp:
            root = self.make_root(tmp, original + block)
            self.assertEqual(self.run_undo(root).returncode, 0)
            self.assertEqual((root / 'persona' / 'peter.md').read_bytes().decode('utf-8'), original)
            copies = list((root / 'persona').glob('peter.before-undo-voice-rules-*.md'))
            self.assertEqual(len(copies), 1)
            self.assertEqual(copies[0].read_bytes(), (original + block).encode('utf-8'))

    def test_block_in_the_middle_keeps_text_after_it(self):
        text = f'Fictional intro\n\n{BEGIN}\n## Fictional rules\n{END}\nFictional outro\n'
        with tempfile.TemporaryDirectory() as tmp:
            root = self.make_root(tmp, text)
            self.assertEqual(self.run_undo(root).returncode, 0)
            self.assertEqual((root / 'persona' / 'peter.md').read_bytes().decode('utf-8'),
                             'Fictional intro\n\nFictional outro\n')

    def test_incomplete_block_fails_and_changes_nothing(self):
        text = f'Fictional intro\n\n{BEGIN}\n## Fictional rules\n'
        with tempfile.TemporaryDirectory() as tmp:
            root = self.make_root(tmp, text)
            self.assertNotEqual(self.run_undo(root).returncode, 0)
            self.assertEqual((root / 'persona' / 'peter.md').read_bytes().decode('utf-8'), text)
            self.assertEqual(list((root / 'persona').glob('peter.before-undo-*')), [])

    def test_without_block_changes_nothing(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = self.make_root(tmp, 'Fictional persona\r\n')
            self.assertEqual(self.run_undo(root).returncode, 0)
            self.assertEqual((root / 'persona' / 'peter.md').read_bytes(), b'Fictional persona\r\n')
            self.assertEqual(list((root / 'persona').glob('peter.before-undo-*')), [])


if __name__ == '__main__':
    unittest.main()
