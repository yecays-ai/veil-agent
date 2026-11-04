import shutil
import subprocess
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

import persona_versions

ROOT = Path(__file__).resolve().parents[1]


def at(second):
    return datetime(2026, 11, 4, 1, 2, second, tzinfo=timezone.utc)


class PersonaVersionsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.persona = Path(self.tmp.name) / 'persona' / 'peter.md'
        self.persona.parent.mkdir()

    def write(self, text):
        self.persona.write_bytes(text.encode('utf-8'))

    def test_first_start_saves_a_copy(self):
        self.write('Fictional persona one\n')
        saved = persona_versions.snapshot(self.persona, at(1))
        self.assertEqual(saved.name, 'peter.20261104-010201.md')
        self.assertEqual(saved.read_bytes(), self.persona.read_bytes())

    def test_same_content_is_not_saved_again(self):
        self.write('Fictional persona one\n')
        persona_versions.snapshot(self.persona, at(1))
        self.assertIsNone(persona_versions.snapshot(self.persona, at(2)))
        self.assertEqual(len(persona_versions.list_versions(self.persona)), 1)

    def test_edit_makes_second_version_newest_first(self):
        self.write('Fictional persona one\n')
        persona_versions.snapshot(self.persona, at(1))
        self.write('Fictional persona two\n')
        persona_versions.snapshot(self.persona, at(2))
        names = [item.name for item in persona_versions.list_versions(self.persona)]
        self.assertEqual(names, ['peter.20261104-010202.md', 'peter.20261104-010201.md'])

    def test_going_back_to_an_old_text_is_not_duplicated(self):
        self.write('Fictional persona one\n')
        persona_versions.snapshot(self.persona, at(1))
        self.write('Fictional persona two\n')
        persona_versions.snapshot(self.persona, at(2))
        self.write('Fictional persona one\n')
        self.assertIsNone(persona_versions.snapshot(self.persona, at(3)))

    def test_same_second_does_not_overwrite(self):
        self.write('Fictional persona one\n')
        first = persona_versions.snapshot(self.persona, at(1))
        self.write('Fictional persona two\n')
        second = persona_versions.snapshot(self.persona, at(1))
        self.assertNotEqual(first, second)
        self.assertEqual(first.read_text(encoding='utf-8'), 'Fictional persona one\n')
        self.assertEqual(second.read_text(encoding='utf-8'), 'Fictional persona two\n')

    def test_more_than_ten_in_one_second_still_newest_first(self):
        names = []
        for number in range(12):
            self.write(f'Fictional persona {number}\n')
            names.append(persona_versions.snapshot(self.persona, at(1)).name)
        listed = [item.name for item in persona_versions.list_versions(self.persona)]
        self.assertEqual(listed, names[::-1])

    def test_unreadable_old_copy_does_not_block_new_one(self):
        self.write('Fictional persona one\n')
        persona_versions.snapshot(self.persona, at(1))
        (self.persona.parent / 'versions' / 'peter.20261104-010209.md').mkdir()  # not a file: ignored
        self.write('Fictional persona two\n')
        self.assertIsNotNone(persona_versions.snapshot(self.persona, at(2)))

    def test_no_partial_file_is_left_behind(self):
        self.write('Fictional persona one\n')
        persona_versions.snapshot(self.persona, at(1))
        left = [item.name for item in (self.persona.parent / 'versions').iterdir()]
        self.assertEqual(left, ['peter.20261104-010201.md'])

    def test_empty_or_missing_file_saves_nothing(self):
        self.assertIsNone(persona_versions.snapshot(self.persona, at(1)))
        self.write('  \n')
        self.assertIsNone(persona_versions.snapshot(self.persona, at(2)))
        self.assertEqual(persona_versions.list_versions(self.persona), [])

    def test_unwritable_folder_does_not_raise(self):
        self.write('Fictional persona one\n')
        (self.persona.parent / 'versions').write_text('a file, not a folder')
        self.assertIsNone(persona_versions.snapshot(self.persona, at(1)))


@unittest.skipUnless(shutil.which('powershell'), 'needs Windows PowerShell')
class RestorePersonaTests(unittest.TestCase):
    def test_restore_picks_old_text_and_keeps_current(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / 'persona').mkdir()
            shutil.copy(ROOT / 'restore-persona.ps1', root)
            persona = root / 'persona' / 'peter.md'
            persona.write_bytes(b'Fictional one\n')
            persona_versions.snapshot(persona, at(1))
            persona.write_bytes(b'Fictional two\n')
            persona_versions.snapshot(persona, at(2))
            persona.write_bytes(b'Fictional three, unsaved\n')
            result = subprocess.run(['powershell', '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File',
                                     str(root / 'restore-persona.ps1'), '-Pick', '2'],
                                    capture_output=True, timeout=60)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(persona.read_bytes(), b'Fictional one\n')
            texts = [item.read_bytes() for item in persona_versions.list_versions(persona)]
            self.assertIn(b'Fictional three, unsaved\n', texts)
            self.assertEqual(len(texts), 3)


if __name__ == '__main__':
    unittest.main()
