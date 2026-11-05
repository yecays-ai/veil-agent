import json
import tempfile
import unittest
from pathlib import Path

import stickers
from stickers import StickerError, StickerLibrary, parse_sticker_text, run_tool

PNG = b'\x89PNG\r\n\x1a\n' + b'0' * 30
GIF = b'GIF89a' + b'0' * 30
SVG = '<svg viewBox="0 0 10 10"><circle cx="5" cy="5" r="4" fill="red"/></svg>'


class StickerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.library = StickerLibrary(Path(self.tmp.name) / 'stickers',
                                      fetch=lambda url: GIF if url.endswith('.gif') else PNG)

    def test_pasted_collections_become_name_and_link_rows(self):
        rows, skipped = parse_sticker_text(
            '1. 打人：https://a.test/1.gif\nhttps://a.test/two.png\n看不懂这行\n'
            '开心 https://a.test/3.webp 哭 https://a.test/4.png\n打人：https://a.test/1.gif')
        self.assertEqual([r['name'] for r in rows], ['打人', 'two', '开心', '哭'])
        self.assertEqual(skipped, ['看不懂这行'])

    def test_wrapped_link_is_joined(self):
        rows, _ = parse_sticker_text('猫：https://a.test/long/\npath/cat.png')
        self.assertEqual(rows, [{'name': '猫', 'url': 'https://a.test/long/path/cat.png'}])

    def test_pack_sticker_names_are_short_unique_and_findable(self):
        self.library.add_pack('丑猫')
        first = self.library.add_bytes('丑猫', '打人打人打人打人打人打人', PNG)
        second = self.library.add_bytes('丑猫', first['name'], GIF)
        self.assertEqual(len(first['name']), 10)
        self.assertNotEqual(first['name'], second['name'])
        self.assertEqual(self.library.find('丑猫', first['name'].upper())['id'], first['id'])
        self.assertEqual(self.library.pack_summary(), '丑猫 (2)')
        self.assertEqual(stickers.marker('丑猫', '打人'), '[表情包：丑猫/打人]')
        self.assertEqual(self.library.resolve_marker('[表情包：丑猫/' + first['name'] + ']\n好')['id'], first['id'])

    def test_rename_and_delete_remove_files(self):
        pack = self.library.add_pack('p')
        sticker = self.library.add_bytes('p', 'a', PNG)
        self.assertEqual(self.library.rename_sticker(sticker['id'], 'b'), 'b')
        self.assertTrue(self.library.read_file(sticker['file']))
        self.library.delete_sticker(sticker['id'])
        self.assertIsNone(self.library.read_file(sticker['file']))
        self.library.delete_pack(pack['id'])
        self.assertEqual(self.library.snapshot(), {'packs': []})

    def test_links_download_in_bulk_and_report_failures(self):
        self.library.add_pack('狗')

        def fetch(url):
            if 'bad' in url:
                raise StickerError('HTTP 404')
            return PNG
        self.library.fetch = fetch
        added, failed = self.library.add_links('狗', [
            {'name': '一', 'url': 'https://a.test/1.png'}, {'name': '二', 'url': 'https://a.test/bad.png'}])
        self.assertEqual(added, 1)
        self.assertEqual(failed[0]['reason'], 'HTTP 404')

    def test_non_pictures_and_unsafe_svg_are_rejected(self):
        self.library.add_pack('p')
        with self.assertRaises(StickerError):
            self.library.add_bytes('p', 'x', b'<html></html>')
        for bad in ('<svg><script>alert(1)</script></svg>', '<svg onload="x()"></svg>',
                    '<svg><image href="https://evil.test/a.png"/></svg>', 'hello'):
            with self.assertRaises(StickerError):
                stickers.check_svg(bad)
        self.assertIn(b'xmlns', stickers.check_svg(SVG))

    def test_download_refuses_private_addresses(self):
        for url in ('http://127.0.0.1/a.png', 'http://localhost/a.png', 'ftp://a.test/a.png'):
            with self.assertRaises(StickerError):
                stickers.download_image(url)

    def test_file_names_are_restricted(self):
        self.assertIsNone(self.library.read_file('../index.json'))
        self.assertIsNone(self.library.read_file('a' * 32 + '.exe'))


class StickerToolTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.library = StickerLibrary(Path(self.tmp.name))
        self.library.add_bytes('丑猫', '打人', PNG, create_pack=True)

    def test_description_lists_packs_only_and_pack_call_lists_names(self):
        schema = stickers.tool_schema(self.library)
        self.assertIn('丑猫 (1)', schema['function']['description'])
        self.assertNotIn('打人', schema['function']['description'])
        text, state = run_tool(self.library, {'pack': '丑猫'})
        self.assertEqual(state, '成功')
        self.assertIn('打人', text)

    def test_peter_looks_only_when_he_asks(self):
        text, state = run_tool(self.library, {'pack': '丑猫', 'look': '打人'})
        self.assertEqual(state, '成功')
        self.assertTrue(text.image_data_uris[0].startswith('data:image/png;base64,'))
        _, state = run_tool(self.library, {'pack': '丑猫', 'look': '没有'})
        self.assertEqual(state, '被拒绝')

    def test_peter_saves_his_own_svg_and_it_can_be_looked_at_as_source(self):
        text, state = run_tool(self.library, {'pack': '我的', 'add': '红圆', 'svg': SVG})
        self.assertEqual(state, '成功')
        self.assertIn('我的/红圆', text)
        self.assertEqual(self.library.find('我的', '红圆')['by'], 'peter')
        source, _ = run_tool(self.library, {'pack': '我的', 'look': '红圆'})
        self.assertIn('<circle', source)

    def test_peter_saves_a_lab_picture(self):
        reader = lambda path: {'data': PNG, 'suffix': '.png', 'name': 'a.png'}
        _, state = run_tool(self.library, {'pack': '我的', 'add': '图', 'file': 'lab/a.png'}, reader)
        self.assertEqual(state, '成功')

    def test_bad_arguments_are_refused(self):
        for args in ({}, {'pack': 'x', 'add': 'y'}, {'pack': 'x', 'svg': SVG}, {'pack': 'x', 'bogus': 1}):
            self.assertEqual(run_tool(self.library, args)[1], '被拒绝')


if __name__ == '__main__':
    unittest.main()
