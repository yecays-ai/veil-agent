import os
import tempfile
import unittest
from pathlib import Path

from desktop_files import DesktopFileReadTools


class DesktopExplorationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        for name in ('desktop', 'documents', 'downloads', 'pictures'):
            (self.root / name).mkdir()
        self.tools = DesktopFileReadTools(True, platform_enabled=True,
                                          folder_for=lambda name: self.root / name)

    def test_four_roots_and_continuation(self):
        for folder in ('desktop', 'documents', 'downloads', 'pictures'):
            path = self.root / folder / 'notes'
            path.mkdir()
            (path / 'story.txt').write_text('A' * 13000 + 'END', encoding='utf-8')
            listed, state, _ = self.tools.invoke('desktop_list', {'folder': folder, 'path': 'notes'})
            self.assertEqual(state, '成功')
            self.assertIn('story.txt', listed)
            found, state, _ = self.tools.invoke('desktop_find_file', {'folder': folder, 'name': 'story.txt'})
            self.assertEqual(state, '成功')
            self.assertIn('notes/story.txt', found)
            first, state, _ = self.tools.invoke('desktop_read_text_file', {'folder': folder, 'path': 'notes/story.txt'})
            self.assertEqual(state, '成功')
            self.assertIn('Continue with offset 12000', first)
            second, state, _ = self.tools.invoke('desktop_read_text_file',
                                                 {'folder': folder, 'path': 'notes/story.txt', 'offset': 12000})
            self.assertEqual(state, '成功')
            self.assertIn('END', second)

    def test_refuses_escape_and_sensitive_items(self):
        (self.root / 'documents' / 'passwords.txt').write_text('secret')
        for path in ('../desktop/secret.txt', 'C:/Windows/win.ini', 'passwords.txt'):
            _, state, _ = self.tools.invoke('desktop_read_text_file', {'folder': 'documents', 'path': path})
            self.assertEqual(state, '被拒绝')
        _, state, _ = self.tools.invoke('desktop_list', {'folder': 'unknown'})
        self.assertEqual(state, '被拒绝')

    def test_reads_real_docx_and_pdf_text(self):
        from docx import Document
        from pypdf import PdfWriter
        from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

        doc = Document()
        doc.add_paragraph('Ordinary Word passage for Peter')
        doc.save(self.root / 'documents' / 'story.docx')

        writer = PdfWriter()
        page = writer.add_blank_page(width=300, height=300)
        font = DictionaryObject({NameObject('/Type'): NameObject('/Font'),
                                 NameObject('/Subtype'): NameObject('/Type1'),
                                 NameObject('/BaseFont'): NameObject('/Helvetica')})
        page[NameObject('/Resources')] = DictionaryObject({
            NameObject('/Font'): DictionaryObject({NameObject('/F1'): writer._add_object(font)})})
        stream = DecodedStreamObject()
        stream.set_data(b'BT /F1 12 Tf 20 250 Td (Ordinary PDF passage for Peter) Tj ET')
        page[NameObject('/Contents')] = writer._add_object(stream)
        with (self.root / 'documents' / 'story.pdf').open('wb') as handle:
            writer.write(handle)

        for name, expected in [('story.docx', 'Ordinary Word passage'),
                               ('story.pdf', 'Ordinary PDF passage')]:
            text, state, _ = self.tools.invoke('desktop_read_text_file',
                                               {'folder': 'documents', 'path': name})
            self.assertEqual(state, '成功', text)
            self.assertIn(expected, text)

    @unittest.skipIf(os.name == 'nt', 'Symlink creation may need privileges')
    def test_refuses_linked_directory(self):
        (self.root / 'documents' / 'link').symlink_to(self.root / 'desktop', target_is_directory=True)
        _, state, _ = self.tools.invoke('desktop_list', {'folder': 'documents', 'path': 'link'})
        self.assertEqual(state, '被拒绝')


if __name__ == '__main__':
    unittest.main()
