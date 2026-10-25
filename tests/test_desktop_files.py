import os
import tempfile
import unittest
from pathlib import Path

from desktop_files import MAX_CREATE_BYTES, DesktopFileTools
from tools_agent import tool_policy


class DesktopFileToolTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        for name in ('Desktop', 'Documents', 'Pictures'):
            (self.root / name).mkdir()
        self.tools = DesktopFileTools(True, folder_for=lambda name: self.root / name.capitalize())

    def create(self, path, content='hi', folder='desktop'):
        return self.tools.invoke('desktop_create_file', {'folder': folder, 'path': path, 'content': content})

    def test_creates_new_files_and_folders_without_approval(self):
        result, state, summary = self.create('raccoon/plan.md', '# Plan\n你好')
        self.assertEqual(state, '成功', result)
        self.assertEqual((self.root / 'Desktop' / 'raccoon' / 'plan.md').read_text(encoding='utf-8'), '# Plan\n你好')
        self.assertEqual(summary, 'desktop/raccoon/plan.md')
        result, state, _ = self.create('art.svg', '<svg/>', folder='pictures')
        self.assertEqual(state, '成功', result)
        self.assertEqual(tool_policy('desktop_create_file'), 'desktop_create')

    def test_never_overwrites_an_existing_file(self):
        mine = self.root / 'Documents' / 'diary.txt'
        mine.write_text('Lan wrote this', encoding='utf-8')
        result, state, _ = self.create('diary.txt', 'replaced', folder='documents')
        self.assertEqual(state, '被拒绝')
        self.assertIn('already exists', result)
        self.assertEqual(mine.read_text(encoding='utf-8'), 'Lan wrote this')

    def test_refuses_escapes_bad_names_and_runnable_types(self):
        for path in ('../escape.txt', 'C:\\Windows\\x.txt', '\\x.txt', 'a/../../x.txt', 'CON.txt',
                     'run.bat', 'run.ps1', 'page.js', 'tool.exe', 'shortcut.lnk', 'noext', 'bad?.txt'):
            result, state, _ = self.create(path)
            self.assertEqual(state, '被拒绝', path)
        self.assertEqual(sorted(p.name for p in self.root.rglob('*')), ['Desktop', 'Documents', 'Pictures'])

    def test_size_limit_per_file(self):
        result, state, _ = self.create('big.txt', 'x' * (MAX_CREATE_BYTES + 1))
        self.assertEqual(state, '被拒绝')
        self.assertFalse((self.root / 'Desktop' / 'big.txt').exists())

    @unittest.skipIf(os.name == 'nt', 'symlink creation needs privileges on Windows')
    def test_does_not_follow_a_linked_folder(self):
        outside = self.root / 'outside'
        outside.mkdir()
        (self.root / 'Desktop' / 'link').symlink_to(outside, target_is_directory=True)
        result, state, _ = self.create('link/x.txt')
        self.assertEqual(state, '被拒绝')
        self.assertEqual(list(outside.iterdir()), [])

    def test_disabled_tool_is_absent(self):
        tools = DesktopFileTools(False)
        self.assertEqual(tools.tools, [])
        self.assertEqual(tools.invoke('desktop_create_file', {})[1], '被拒绝')


if __name__ == '__main__':
    unittest.main()
