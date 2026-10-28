import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import desktop_files
from desktop_files import DesktopFileReadTools, MAX_READ_BYTES
from tools_agent import tool_policy
from veil_agent import load_character


class DesktopFileReadTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parents[1])
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / 'Desktop'
        self.root.mkdir()
        self.tools = DesktopFileReadTools(True, folder_for=lambda _name: self.root, platform_enabled=True)

    def test_lists_finds_and_reads_visible_text_only(self):
        (self.root / 'Note.md').write_bytes('# Lan\nhello'.encode('utf-8'))
        (self.root / 'Folder').mkdir()
        (self.root / 'Folder' / 'note.txt').write_text('nested', encoding='utf-8')
        listing, state, _ = self.tools.invoke('desktop_list', {})
        self.assertEqual(state, '成功')
        self.assertIn('[FILE] Note.md', listing)
        self.assertIn('[DIR] Folder', listing)
        found, state, _ = self.tools.invoke('desktop_find_file', {'name': 'note.txt'})
        self.assertEqual(state, '成功')
        self.assertIn('Folder/note.txt', found)
        content, state, summary = self.tools.invoke('desktop_read_text_file', {'path': 'Note.md'})
        self.assertEqual((state, content, summary), ('成功', '# Lan\nhello', 'desktop/Note.md'))

    def test_refuses_paths_outside_desktop_and_links(self):
        outside = Path(self.temp.name) / 'outside.txt'
        outside.write_text('outside', encoding='utf-8')
        for path in ('../outside.txt', str(outside), 'C:\\Windows\\win.ini', 'notes.txt:stream'):
            result, state, _ = self.tools.invoke('desktop_read_text_file', {'path': path})
            self.assertEqual(state, '被拒绝', path)
            self.assertNotIn('outside', result)
        if not hasattr(Path, 'symlink_to'):
            self.skipTest('symlinks are unavailable')
        link = self.root / 'escape.txt'
        try:
            link.symlink_to(outside)
        except OSError:
            self.skipTest('symlink creation is unavailable')
        result, state, _ = self.tools.invoke('desktop_read_text_file', {'path': 'escape.txt'})
        self.assertEqual(state, '被拒绝')
        self.assertNotIn('outside', result)

    def test_refuses_hidden_sensitive_nontext_and_oversize_files(self):
        for name, content in (('.hidden.txt', 'secret'), ('password.txt', 'secret'),
                              ('keys.pem', 'secret'), ('page.html', '<html/>')):
            (self.root / name).write_text(content, encoding='utf-8')
            result, state, _ = self.tools.invoke('desktop_read_text_file', {'path': name})
            self.assertEqual(state, '被拒绝', name)
            self.assertNotIn(content, result)
        (self.root / 'large.md').write_text('x' * (MAX_READ_BYTES + 1), encoding='utf-8')
        result, state, _ = self.tools.invoke('desktop_read_text_file', {'path': 'large.md'})
        self.assertEqual(state, '被拒绝')
        self.assertNotIn('x' * 100, result)

    def test_refuses_hidden_file_attributes_and_hidden_parent_folders(self):
        hidden = self.root / 'ordinary.txt'
        hidden.write_text('private', encoding='utf-8')
        private_folder = self.root / 'visible-name'
        private_folder.mkdir()
        nested = private_folder / 'note.txt'
        nested.write_text('private folder', encoding='utf-8')
        visible = desktop_files._plain_visible

        def hide_one(path):
            return visible(path) and path not in {hidden, private_folder}

        with patch.object(desktop_files, '_plain_visible', side_effect=hide_one):
            for path in ('ordinary.txt', 'visible-name/note.txt'):
                result, state, _ = self.tools.invoke('desktop_read_text_file', {'path': path})
                self.assertEqual(state, '被拒绝')
                self.assertNotIn('private', result)

    def test_refuses_hard_linked_files(self):
        original = self.root / 'ordinary.txt'
        original.write_text('linked', encoding='utf-8')
        alias = self.root / 'alias.txt'
        try:
            alias.hardlink_to(original)
        except (OSError, NotImplementedError):
            self.skipTest('hard links are unavailable')
        result, state, _ = self.tools.invoke('desktop_read_text_file', {'path': 'alias.txt'})
        self.assertEqual(state, '被拒绝')
        self.assertNotIn('linked', result)

    def test_disabled_read_permission_is_absent_and_cannot_be_invoked(self):
        disabled = DesktopFileReadTools(False, folder_for=lambda _name: self.root, platform_enabled=True)
        self.assertEqual(disabled.tools, [])
        self.assertEqual(disabled.invoke('desktop_read_text_file', {'path': 'x.txt'})[1], '被拒绝')
        self.assertEqual(tool_policy('desktop_read_text_file'), 'desktop_file_read')

    def test_character_can_enable_desktop_read_without_enabling_other_desktop_permissions(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parents[1]) as directory:
            root = Path(directory)
            (root / 'characters' / 'fixture').mkdir(parents=True)
            (root / 'persona').mkdir()
            (root / 'persona' / 'fixture.md').write_text('Fictional test persona', encoding='utf-8')
            (root / 'characters' / 'fixture' / 'character.toml').write_text('''name = "Fixture"
provider = "fake"
persona = "persona/fixture.md"
[providers.fake]
model = "fake-model"
base_url = "https://example.invalid/v1"
api_key_env = "FAKE_KEY"
max_input_tokens = 6000
input_cny_per_million = "2"
cached_input_cny_per_million = "1"
output_cny_per_million = "8"
[desktop_file_read]
enabled = true
[windows_media]
enabled = false
[windows_interaction]
enabled = false
''', encoding='utf-8')
            cfg = load_character(root, 'fixture')
        self.assertEqual(cfg.desktop_file_read, {'enabled': True})
        self.assertEqual(cfg.windows_media, {'enabled': False})
        self.assertEqual(cfg.windows_interaction['enabled'], False)


if __name__ == '__main__':
    unittest.main()
