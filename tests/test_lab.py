import hashlib
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from tools_agent import ALLOW, ASK, DENY, LAB_ONLY, FilesystemTools, _rename_noreplace, permitted_tools
from veil_agent import Budget, Chat, Store, UserError, load_character


class LabTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        (self.root / 'characters/test').mkdir(parents=True)
        (self.root / 'persona').mkdir()
        (self.root / 'persona/test.md').write_text('Fictional persona', encoding='utf-8')
        self.config = self.root / 'characters/test/character.toml'
        self.config.write_text('''name = "Test"
provider = "fake"
persona = "persona/test.md"
max_output_tokens = 50
[providers.fake]
model = "fake"
base_url = "https://example.invalid/v1"
api_key_env = "FAKE_KEY"
max_input_tokens = 32768
input_cny_per_million = "2"
cached_input_cny_per_million = "1"
output_cny_per_million = "8"
[tools]
enabled = true
persistent_append_file = "growth-journal.md"
''', encoding='utf-8')
        self.cfg = load_character(self.root, 'test')
        self.store = Store(self.root / 'data')
        self.worker = SimpleNamespace(tools=[SimpleNamespace(
            name=name, description=name, inputSchema={})
            for name in ALLOW | ASK | DENY | LAB_ONLY | {'delete_file', 'execute_script'}],
            call=Mock(return_value='fictional read result'), close=Mock())
        self.no_approval = Mock(side_effect=AssertionError('lab must not ask'))
        self.tools = self.open_tools()
        self.lab = self.tools.lab

    def open_tools(self):
        with patch('tools_agent.shutil.which', return_value='fake-node'), \
                patch('tools_agent._McpWorker', return_value=self.worker):
            return FilesystemTools(self.root, self.cfg, self.store)

    def call(self, name, path, content=None, approval=None, tools=None):
        args = {'path': path}
        if content is not None:
            args['content'] = content
        return (tools or self.tools).invoke(name, args,
            self.no_approval if approval is None else approval)

    def move(self, source, destination, tools=None):
        return (tools or self.tools).invoke('move_lab_file',
            {'source': source, 'destination': destination}, self.no_approval)

    def test_lab_attachment_is_a_bounded_verified_snapshot(self):
        source = self.lab / 'story.md'
        source.write_text('first version', encoding='utf-8')

        snapshot = self.tools.read_lab_attachment('lab/story.md')

        self.assertEqual(snapshot['name'], 'story.md')
        self.assertEqual(snapshot['view'], 'text')
        self.assertEqual(snapshot['data'], b'first version')
        source.write_text('edited after sending', encoding='utf-8')
        self.assertEqual(snapshot['data'], b'first version')

    def test_lab_attachment_rejects_unsafe_paths_links_and_types(self):
        outside = self.root / 'secret.txt'
        outside.write_text('not for sharing', encoding='utf-8')
        (self.lab / 'secret.txt').write_text('lab copy', encoding='utf-8')
        with self.assertRaises(ValueError):
            self.tools.read_lab_attachment('lab/../secret.txt')
        with self.assertRaises(ValueError):
            self.tools.read_lab_attachment(str(outside))
        unsupported = self.lab / 'payload.exe'
        unsupported.write_bytes(b'MZ')
        with self.assertRaises(ValueError):
            self.tools.read_lab_attachment('lab/payload.exe')
        (self.lab / 'too-large.txt').write_bytes(b'x' * (5 * 1024 * 1024 + 1))
        with self.assertRaises(ValueError):
            self.tools.read_lab_attachment('lab/too-large.txt')
        linked = self.lab / 'linked.txt'
        try:
            linked.symlink_to(outside)
        except (OSError, NotImplementedError):
            self.skipTest('symlinks are unavailable for this account')
        with self.assertRaises(ValueError):
            self.tools.read_lab_attachment('lab/linked.txt')

    def test_file_attachment_capability_notice_is_recorded_once(self):
        first = self.tools.announce_file_attachments()
        second = self.tools.announce_file_attachments()
        row = json.loads(self.tools.cap_path.read_text(encoding='utf-8'))

        self.assertTrue(first)
        self.assertIsNone(second)
        self.assertTrue(row['file_attachments_announced'])
        self.assertIn('send_message with file', row['pending_event'])
        self.assertEqual(row['pending_event'], row['pending_event_model'])

    def test_create_directories_files_append_and_restart_without_approval(self):
        self.assertEqual(self.call('create_directory', 'lab/research/notes')[1], '成功')
        original = '# First draft\n原文\r\n'
        self.assertEqual(self.call('write_file', 'lab/research/notes/v1.md', original)[1], '成功')
        result = self.call('append_file', 'lab/research/notes/v1.md', '\nsecond')
        self.assertEqual(result[1], '成功', result)
        self.assertIn('SHA-256', result[0])
        reopened = self.open_tools()
        self.assertEqual(self.call('append_file', 'lab/research/notes/v1.md', '\nthird', tools=reopened)[1], '成功')
        self.assertEqual((self.lab / 'research/notes/v1.md').read_bytes(),
                         (original + '\nsecond\nthird').encode('utf-8'))
        self.no_approval.assert_not_called()
        self.worker.call.assert_not_called()

    def test_rewrite_keeps_every_previous_version_and_create_race_is_refused(self):
        self.assertEqual(self.call('write_file', 'lab/V1.md', 'original')[1], '成功')
        for name, text in (('lab/V1.md', 'second'), ('lab/v1.md', 'third')):
            result, state, _ = self.call('write_file', name, text)
            self.assertEqual(state, '成功', result)
            self.assertIn('kept the previous version', result)
        self.assertEqual([p.name for p in self.lab.iterdir()], ['V1.md'])
        self.assertEqual((self.lab / 'V1.md').read_bytes(), b'third')
        versions = sorted((self.root / 'data/test/own-work-versions/lab').iterdir())
        self.assertEqual([p.read_bytes() for p in versions], [b'original', b'second'])
        real_open = Path.open
        def racing_open(path, mode='r', *args, **kwargs):
            if path.name == 'race.md' and mode == 'xb':
                with real_open(path, 'wb') as file:
                    file.write(b'racing original')
            return real_open(path, mode, *args, **kwargs)
        with patch.object(Path, 'open', racing_open):
            self.assertEqual(self.call('write_file', 'lab/race.md', 'replacement')[1], '被拒绝')
        self.assertEqual((self.lab / 'race.md').read_bytes(), b'racing original')

    def test_windows_unsafe_names_traversal_absolute_and_long_paths_are_denied(self):
        bad = ['CON', 'CON.txt', 'PRN.md', 'AUX', 'NUL.txt', 'COM1.md', 'COM9',
               'LPT1', 'LPT9.txt', 'COM¹.txt', 'a.md:stream', 'a<b', 'a>b', 'a"b',
               'a|b', 'a?b', 'a*b', 'a\x01b', 'trailing.', 'trailing ',
               '../growth-journal.md', 'nested/../../growth-journal.md']
        for name in bad:
            with self.subTest(name=name):
                result = self.call('write_file', 'lab/' + name, 'blocked')
                self.assertEqual(result[1], '被拒绝', result)
        for path in (str(self.lab / 'absolute.md'), r'\\?\C:\outside.txt', '../outside.txt'):
            self.assertEqual(self.call('write_file', path, 'blocked')[1], '被拒绝')
        if os.name == 'nt':
            self.assertEqual(self.call('write_file', r'C:\outside.txt', 'blocked')[1], '被拒绝')
        long = 'lab/' + 'x' * (242 - len(str(self.lab)))
        self.assertEqual(self.call('write_file', long, 'blocked')[1], '被拒绝')
        self.assertEqual(list(self.lab.iterdir()), [])

    def test_oversize_utf8_create_and_append_write_zero_bytes(self):
        self.tools.lab_max_write_bytes = 4
        result = self.call('write_file', 'lab/large.md', '你好')
        self.assertEqual(result[1], '被拒绝')
        self.assertIn('no content was written', result[0])
        self.assertFalse((self.lab / 'large.md').exists())
        self.assertEqual(self.call('write_file', 'lab/small.md', 'old')[1], '成功')
        result = self.call('append_file', 'lab/small.md', '你好')
        self.assertEqual(result[1], '被拒绝')
        self.assertEqual((self.lab / 'small.md').read_bytes(), b'old')
        self.assertEqual(self.call('append_file', 'lab/missing.md', 'x')[1], '被拒绝')
        self.assertFalse((self.lab / 'missing.md').exists())

    def test_new_version_reports_both_byte_counts_and_preserves_prior(self):
        original = 'first 中文\n'
        self.call('write_file', 'lab/v1.md', original)
        result = self.call('write_file', 'lab/v2.md', 'short')
        self.assertEqual(result[1], '成功')
        self.assertIn(f'v1.md：{len(original.encode("utf-8"))} 字节', result[0])
        self.assertIn('v2.md：5 字节', result[0])
        self.assertIn('漏掉', result[0])
        self.assertEqual((self.lab / 'v1.md').read_bytes(), original.encode('utf-8'))

    def test_failed_hash_verification_is_not_reported_as_success(self):
        real_read = Path.read_bytes
        def wrong_read(path):
            return b'corrupt' if path.name == 'hash.md' else real_read(path)
        with patch.object(Path, 'read_bytes', wrong_read):
            result = self.call('write_file', 'lab/hash.md', 'expected')
        self.assertEqual(result[1], '出错')
        self.assertIn('complete save cannot be confirmed', result[0])

    def test_journal_grant_is_independent_and_outside_permissions_are_unchanged(self):
        journal = self.tools.workspace / 'growth-journal.md'
        journal.write_bytes(b'original journal')
        self.assertEqual(self.call('grant_append', 'growth-journal.md', approval=lambda _: 'y')[1], '成功')
        grant = self.tools.grant_path.read_bytes()
        self.call('write_file', 'lab/notes.md', 'fictional notes')
        self.call('append_file', 'lab/notes.md', 'continued')
        self.assertEqual(self.tools.grant_path.read_bytes(), grant)
        self.assertEqual(journal.read_bytes(), b'original journal')
        self.assertEqual(self.call('append_file', 'growth-journal.md', '\nsecond', tools=self.open_tools())[1], '成功')
        self.assertEqual(self.call('write_file', 'outside.md', 'new', approval=lambda _: 'n')[1], '被拒绝')
        self.assertEqual(self.call('write_file', 'outside.md', 'new', approval=lambda _: 'y')[1], '成功')
        self.assertEqual(self.call('append_file', 'outside.md', 'more', approval=lambda _: 'n')[1], '被拒绝')
        self.assertEqual(self.call('create_directory', 'outside')[1], '被拒绝')
        self.assertEqual(self.call('write_file', 'growth-journal.md', 'overwrite')[1], '被拒绝')
        for name in ('edit_file', 'move_file', 'delete_file', 'execute_script'):
            self.assertEqual(self.call(name, 'lab/notes.md', 'bad')[1], '被拒绝')
        self.assertEqual(self.move('growth-journal.md', 'lab/archive/growth-journal.md')[1], '被拒绝')
        self.assertEqual(self.move('lab/notes.md', 'archive/notes.md')[1], '被拒绝')
        self.assertEqual(journal.read_bytes(), b'original journal\nsecond')

    def test_archive_and_restore_preserve_complete_file_and_survive_reopen(self):
        self.call('create_directory', 'lab/play')
        original = b'<!doctype html>\r\n<!-- old version -->\x00\xff\r\n'
        source = self.lab / 'play/find-the-bug-v1.html'
        source.write_bytes(original)
        result = self.move('lab/play/find-the-bug-v1.html',
                           'lab/archive/play/find-the-bug-v1.html')
        self.assertEqual(result[1], '成功', result)
        self.assertIn('SHA-256', result[0])
        archived = self.lab / 'archive/play/find-the-bug-v1.html'
        self.assertFalse(source.exists())
        self.assertEqual(archived.read_bytes(), original)
        reopened = self.open_tools()
        result = self.move('lab/archive/play/find-the-bug-v1.html',
                           'lab/play/recovered-v1.html', tools=reopened)
        self.assertEqual(result[1], '成功', result)
        self.assertFalse(archived.exists())
        self.assertEqual((self.lab / 'play/recovered-v1.html').read_bytes(), original)
        self.no_approval.assert_not_called()

    def test_move_conflict_and_failed_atomic_move_keep_source_and_target_intact(self):
        source = self.lab / 'play.md'
        source.write_bytes(b'complete original')
        target = self.lab / 'archive/play.md'
        target.parent.mkdir()
        target.write_bytes(b'existing destination')
        result = self.move('lab/play.md', 'lab/archive/play.md')
        self.assertEqual(result[1], '被拒绝', result)
        self.assertEqual(source.read_bytes(), b'complete original')
        self.assertEqual(target.read_bytes(), b'existing destination')

        target.unlink()
        atomic_rename = _rename_noreplace
        def create_racing_destination(src, dst, *args, **kwargs):
            Path(dst).write_bytes(b'racing destination')
            return atomic_rename(src, dst)
        with patch('tools_agent._rename_noreplace', side_effect=create_racing_destination):
            result = self.move('lab/play.md', 'lab/archive/play.md')
        self.assertEqual(result[1], '被拒绝', result)
        self.assertEqual(source.read_bytes(), b'complete original')
        self.assertEqual(target.read_bytes(), b'racing destination')

        target.unlink()
        with patch('tools_agent._rename_noreplace', side_effect=OSError('simulated move failure')):
            result = self.move('lab/play.md', 'lab/archive/play.md')
        self.assertEqual(result[1], '出错', result)
        self.assertEqual(source.read_bytes(), b'complete original')
        self.assertFalse(target.exists())

    def test_source_replacement_during_move_is_restored_without_loss(self):
        source = self.lab / 'play.md'
        source.write_bytes(b'original source')
        destination = self.lab / 'archive/play.md'
        calls = 0

        def replace_before_atomic_move(src, dst):
            nonlocal calls
            if calls == 0:
                Path(src).unlink()
                Path(src).write_bytes(b'concurrent replacement')
            calls += 1
            return _rename_noreplace(src, dst)

        with patch('tools_agent._rename_noreplace', side_effect=replace_before_atomic_move):
            result = self.move('lab/play.md', 'lab/archive/play.md')
        self.assertEqual(result[1], '出错', result)
        self.assertEqual(source.read_bytes(), b'concurrent replacement')
        self.assertFalse(destination.exists())

    def test_move_rejects_paths_outside_archive_boundary_and_non_regular_files(self):
        (self.lab / 'play').mkdir()
        source = self.lab / 'play/item.md'
        source.write_bytes(b'original')
        for destination in ('lab/archive/other/item.md', 'lab/other/item.md',
                            '../outside.md', str(self.tools.workspace / 'outside.md')):
            result = self.move('lab/play/item.md', destination)
            self.assertEqual(result[1], '被拒绝', (destination, result))
            self.assertEqual(source.read_bytes(), b'original')
        (self.lab / 'play/folder').mkdir()
        self.assertEqual(self.move('lab/play/folder', 'lab/archive/folder')[1], '被拒绝')
        try:
            (self.lab / 'play/link.md').symlink_to(source)
        except (OSError, NotImplementedError):
            pass
        else:
            self.assertEqual(self.move('lab/play/link.md', 'lab/archive/play/link.md')[1], '被拒绝')
        self.assertEqual(source.read_bytes(), b'original')

    def test_move_rejects_hardlink_and_preserves_both_names(self):
        (self.lab / 'play').mkdir()
        source = self.lab / 'play/source.md'
        alias = self.lab / 'play/alias.md'
        source.write_bytes(b'linked original')
        try:
            os.link(source, alias)
        except OSError:
            self.skipTest('hardlinks unavailable')
        result = self.move('lab/play/source.md', 'lab/archive/play/source.md')
        self.assertEqual(result[1], '被拒绝', result)
        self.assertEqual(source.read_bytes(), b'linked original')
        self.assertEqual(alias.read_bytes(), b'linked original')
        self.assertFalse((self.lab / 'archive/play/source.md').exists())

    def test_lab_file_digest_hashes_large_binary_files_in_bounded_chunks(self):
        content = bytes(range(256)) * 128
        path = self.lab / 'archive/large.bin'
        path.parent.mkdir(parents=True)
        path.write_bytes(content)
        result, state, _ = self.tools.invoke('lab_file_digest', {'path': 'lab/archive/large.bin'})
        self.assertEqual(state, '成功', result)
        self.assertEqual(json.loads(result), {
            'path': 'lab/archive/large.bin', 'byte_length': len(content),
            'sha256': hashlib.sha256(content).hexdigest()})
        denied, state, _ = self.tools.invoke('lab_file_digest', {'path': 'growth-journal.md'})
        self.assertEqual(state, '被拒绝', denied)

    def test_move_tool_is_lab_only_and_never_exposes_filesystem_server_move(self):
        names = {tool.name for tool in self.tools.tools}
        self.assertIn('move_lab_file', names)
        self.assertIn('lab_file_digest', names)
        self.assertNotIn('move_file', {item['function']['name']
                                      for item in permitted_tools(self.tools.tools)})
        self.cfg.tools['lab'] = False
        disabled = self.open_tools()
        self.assertNotIn('move_lab_file', {tool.name for tool in disabled.tools})
        self.assertEqual(self.move('lab/a.md', 'lab/archive/a.md', tools=disabled)[1], '被拒绝')

    def test_hardlink_cannot_use_lab_permission_to_append_journal(self):
        journal = self.tools.workspace / 'growth-journal.md'
        journal.write_bytes(b'original')
        try:
            os.link(journal, self.lab / 'alias.md')
        except OSError:
            self.skipTest('hardlinks unavailable')
        self.assertEqual(self.call('append_file', 'lab/alias.md', 'bad')[1], '被拒绝')
        self.assertEqual(journal.read_bytes(), b'original')

    def test_symlinks_cannot_escape_or_get_lab_privilege(self):
        try:
            (self.lab / 'escape').symlink_to(self.tools.workspace, target_is_directory=True)
        except (OSError, NotImplementedError):
            self.skipTest('symlinks unavailable')
        for name in ('write_file', 'append_file', 'create_directory'):
            self.assertEqual(self.call(name, 'lab/escape/growth-journal.md', 'bad')[1], '被拒绝')
        self.assertFalse((self.tools.workspace / 'growth-journal.md').exists())

    @unittest.skipUnless(os.name == 'nt', 'Windows junction behavior')
    def test_windows_junction_root_disables_lab(self):
        self.lab.rmdir()
        destination = self.root / 'outside'
        destination.mkdir()
        subprocess.run(['cmd', '/c', 'mklink', '/J', str(self.lab), str(destination)],
                       check=True, capture_output=True)
        reopened = self.open_tools()
        self.assertIsNone(reopened.lab)
        self.assertNotIn('create_directory', {t.name for t in reopened.tools})
        self.assertFalse(json.loads(reopened.cap_path.read_text(encoding='utf-8'))['lab'])
        self.assertIn('实验室不可用', reopened.startup_notice)

    def test_symlink_lab_root_disables_lab(self):
        self.lab.rmdir()
        destination = self.root / 'outside'
        destination.mkdir()
        try:
            self.lab.symlink_to(destination, target_is_directory=True)
        except (OSError, NotImplementedError):
            self.skipTest('symlinks unavailable')
        reopened = self.open_tools()
        self.assertIsNone(reopened.lab)
        self.assertNotIn('create_directory', {t.name for t in reopened.tools})

    @unittest.skipUnless(os.name == 'nt', 'Windows junction behavior')
    def test_windows_nested_junction_cannot_escape_to_journal(self):
        link = self.lab / 'escape'
        subprocess.run(['cmd', '/c', 'mklink', '/J', str(link), str(self.tools.workspace)],
                       check=True, capture_output=True)
        (self.lab / 'source.md').write_bytes(b'original')
        self.assertEqual(self.call('write_file', 'lab/escape/journal.md', 'bad')[1], '被拒绝')
        self.assertEqual(self.move('lab/source.md', 'lab/escape/archive/source.md')[1], '被拒绝')
        self.assertEqual((self.lab / 'source.md').read_bytes(), b'original')
        self.assertFalse((self.tools.workspace / 'journal.md').exists())

    def test_append_replacement_race_is_refused_before_writing(self):
        self.call('write_file', 'lab/race.md', 'old')
        real_open = os.open
        def replace_then_open(path, flags, *args, **kwargs):
            if Path(path).name == 'race.md':
                target = self.lab / 'replacement.md'
                target.write_bytes(b'replacement different length')
                os.replace(target, path)
            return real_open(path, flags, *args, **kwargs)
        with patch('tools_agent.os.open', side_effect=replace_then_open):
            self.assertEqual(self.call('append_file', 'lab/race.md', 'bad')[1], '被拒绝')
        self.assertEqual((self.lab / 'race.md').read_bytes(), b'replacement different length')

    def test_capability_open_notice_is_delivered_once_and_instructions_are_complete(self):
        cap = json.loads(self.tools.cap_path.read_text(encoding='utf-8'))
        self.assertEqual(cap['lab'], {'folder': 'lab', 'max_write_bytes': 200000})
        self.assertIn('Your lab folder "lab/" is available', self.tools.capability_event)
        self.tools.consume_capability_event()
        self.assertIsNone(self.open_tools().capability_event)
        for text in ('lab/research/v1.md', 'rewrite existing files', 'lab/trash/', 'Nothing can be deleted',
                     'independent', 'scripts cannot run'):
            self.assertIn(text, self.tools.description)

    def test_disabled_lab_removes_lab_only_tool_and_free_permissions(self):
        self.cfg.tools['lab'] = False
        reopened = self.open_tools()
        self.assertIsNone(reopened.lab)
        self.assertNotIn('create_directory', {t['function']['name'] for t in permitted_tools(reopened.tools)})
        asked = Mock(return_value='n')
        result = self.call('write_file', 'lab/new.md', 'new', approval=asked, tools=reopened)
        self.assertEqual(result[1], '被拒绝')
        asked.assert_called_once()

    def test_configuration_defaults_custom_name_and_invalid_values(self):
        self.assertEqual(self.cfg.tools['lab'], 'lab')
        self.assertEqual(self.cfg.tools['lab_max_write_bytes'], 200000)
        original = self.config.read_text(encoding='utf-8')
        for option in ('lab = true', 'lab = "../escape"', 'lab = "nested/lab"',
                       'lab = "CON.txt"', 'lab = "bad."', 'lab = "Growth-Journal.md"',
                       'lab_max_write_bytes = 0', 'lab_max_write_bytes = true'):
            with self.subTest(option=option):
                self.config.write_text(original + '\n' + option, encoding='utf-8')
                with self.assertRaises(UserError):
                    load_character(self.root, 'test')
        self.config.write_text(original + '\nlab = "atelier"\nlab_max_write_bytes = 400', encoding='utf-8')
        self.cfg = load_character(self.root, 'test')
        reopened = self.open_tools()
        self.assertEqual(reopened.lab.name, 'atelier')
        self.assertEqual(self.call('write_file', 'atelier/v1.md', 'notes', tools=reopened)[1], '成功')

    def test_real_chat_initialization_offers_lab_and_executes_without_approval(self):
        actions = [('create_directory', {'path': 'lab/research'}),
                   ('write_file', {'path': 'lab/research/v1.md', 'content': 'original'}),
                   ('append_file', {'path': 'lab/research/v1.md', 'content': '\naddition'})]
        offered = []
        def model(messages, **kwargs):
            offered.append({t['function']['name'] for t in kwargs.get('tools') or []})
            index = len(offered) - 1
            calls = ([SimpleNamespace(id=f'call{index}', type='function', function=SimpleNamespace(
                name=actions[index][0], arguments=json.dumps(actions[index][1])))] if index < 3 else [])
            content = None if calls else 'Saved.'
            payload = {'role': 'assistant', 'content': content,
                       'tool_calls': [{'id': c.id, 'type': 'function', 'function': {
                           'name': c.function.name, 'arguments': c.function.arguments}} for c in calls]}
            message = SimpleNamespace(content=content, tool_calls=calls, reasoning_content='',
                                      model_dump=lambda **kw: payload)
            return message, 100, 20, 0
        chat = Chat(self.cfg, self.store, Budget(self.store), model, tools=self.tools,
                    approval=self.no_approval, output=lambda text: None)
        self.assertEqual(chat.send('The lab is open; choose what to create.'), 'Saved.')
        self.assertTrue({'write_file', 'append_file', 'grant_append', 'create_directory'} <= offered[0])
        self.assertEqual((self.lab / 'research/v1.md').read_bytes(), b'original\naddition')
        self.no_approval.assert_not_called()
        self.worker.call.assert_not_called()
