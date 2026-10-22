import ast
import json
import os
import shutil
import tempfile
import unittest
from decimal import Decimal
from unittest.mock import patch
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

from tools_agent import (ALLOW, ASK, DENY, LAB_ONLY, FilesystemTools, checked_path,
                         permitted_tools, tool_policy)
from veil_agent import Budget, Chat, Store, load_character, model_tool_result, run_chat


class ToolPolicyTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.workspace = self.root / 'workspace'
        self.workspace.mkdir()

    def manager(self):
        item = FilesystemTools.__new__(FilesystemTools)
        item.workspace = self.workspace
        item.cfg = SimpleNamespace(tools={'max_result_bytes': 64})
        item.tools = [SimpleNamespace(name=name) for name in ALLOW | ASK | DENY]
        item.client = Mock()
        return item

    def test_only_explicitly_allowed_tools_are_offered_to_model(self):
        server = [SimpleNamespace(name=name, description='desc', inputSchema={})
                  for name in sorted(ALLOW | ASK | DENY | LAB_ONLY | {'unknown'})]
        result = permitted_tools(server)
        names = {item['function']['name'] for item in result}
        self.assertEqual(names, ALLOW | ASK | LAB_ONLY)
        self.assertEqual(tool_policy('write_file'), 'ask')
        self.assertEqual(tool_policy('edit_file'), 'deny')
        self.assertEqual(tool_policy('unknown'), 'deny')

    def test_path_traversal_and_device_paths_are_rejected(self):
        for value in ('../outside.txt', str(self.root / 'outside.txt'), r'\\?\C:\secret.txt'):
            with self.subTest(value=value), self.assertRaises(ValueError):
                checked_path(self.workspace, value)

    def test_symlink_to_outside_is_rejected(self):
        outside = self.root / 'outside'
        outside.mkdir()
        link = self.workspace / 'escape'
        try:
            link.symlink_to(outside, target_is_directory=True)
        except (OSError, NotImplementedError):
            self.skipTest('symlinks are unavailable for this account')
        with self.assertRaises(ValueError):
            checked_path(self.workspace, str(link / 'secret.txt'))

    def test_write_requires_consent_and_never_overwrites(self):
        manager = self.manager()
        manager.client.call.return_value = 'written'
        asked = Mock(return_value='n')
        result, state, path = manager.invoke('write_file',
            {'path': 'summary.txt', 'content': 'fictional'}, asked)
        self.assertIn('Lan did not approve', result)
        self.assertEqual(state, '被拒绝')
        manager.client.call.assert_not_called()
        asked.assert_called_once()
        allowed = Mock(return_value='y')
        result, state, path = manager.invoke('write_file',
            {'path': 'summary.txt', 'content': 'fictional'}, allowed)
        self.assertEqual((result, state, path), ('新文件已创建。', '成功', 'summary.txt'))
        self.assertEqual((self.workspace / 'summary.txt').read_text(encoding='utf-8'), 'fictional')
        manager.client.call.assert_not_called()
        (self.workspace / 'summary.txt').write_text('old', encoding='utf-8')
        manager.client.call.reset_mock()
        manager.invoke('write_file', {'path': 'summary.txt', 'content': 'new'}, allowed)
        manager.client.call.assert_not_called()

    def test_append_grant_is_scoped_persistent_and_keeps_original_bytes(self):
        target = self.workspace / 'growth-journal.md'
        original = b'# Growth Journal\n\n## 2026-10-19\nfirst\n'
        target.write_bytes(original)
        manager = self.manager()
        manager.cfg = SimpleNamespace(tools={'max_result_bytes': 64,
            'persistent_append_file': 'growth-journal.md'})
        manager.grant_path = self.root / 'append-grant.json'
        manager.tools += [SimpleNamespace(name='grant_append'), SimpleNamespace(name='append_file')]
        asked = Mock(return_value='y')
        result, state, _ = manager.invoke('grant_append', {'path': 'growth-journal.md'}, asked)
        self.assertEqual(state, '成功', result)
        self.assertTrue(manager.grant_path.exists())
        asked.assert_called_once()
        restarted = self.manager()
        restarted.cfg = manager.cfg
        restarted.grant_path = manager.grant_path
        restarted.tools += [SimpleNamespace(name='grant_append'), SimpleNamespace(name='append_file')]
        no_prompt = Mock(side_effect=AssertionError('grant should survive restart'))
        result, state, _ = restarted.invoke('append_file',
            {'path': 'growth-journal.md', 'content': '\n## 2026-10-20\nsecond\n'}, no_prompt)
        self.assertEqual(state, '成功')
        self.assertEqual(target.read_bytes(), original + b'\n## 2026-10-20\nsecond\n')
        grant = json.loads(manager.grant_path.read_text(encoding='utf-8'))
        self.assertEqual((grant['ctime_ns'], grant['size']),
                         (target.stat().st_ctime_ns, target.stat().st_size))
        result, state, _ = restarted.invoke('append_file',
            {'path': 'growth-journal.md', 'content': '\n## 2026-10-21\nthird\n'}, no_prompt)
        self.assertEqual(state, '成功', result)
        self.assertTrue(target.read_bytes().endswith(b'\n## 2026-10-21\nthird\n'))
        no_prompt.assert_not_called()

    def test_append_other_file_asks_and_refusal_is_clear(self):
        manager = self.manager()
        manager.cfg = SimpleNamespace(tools={'max_result_bytes': 64,
            'persistent_append_file': 'growth-journal.md'})
        manager.grant_path = self.root / 'append-grant.json'
        manager.tools += [SimpleNamespace(name='grant_append'), SimpleNamespace(name='append_file')]
        other = self.workspace / 'other.md'
        other.write_text('old', encoding='utf-8')
        result, state, _ = manager.invoke('append_file',
            {'path': 'other.md', 'content': 'new'}, lambda _: 'n')
        self.assertEqual(state, '被拒绝')
        self.assertIn('Lan did not approve', result)
        self.assertEqual(other.read_text(encoding='utf-8'), 'old')
        result, state, _ = manager.invoke('append_file',
            {'path': 'other.md', 'content': 'new'}, lambda _: 'y')
        self.assertEqual(state, '成功')
        self.assertEqual(other.read_text(encoding='utf-8'), 'oldnew')
        self.assertFalse(manager.grant_path.exists())
        result, state, _ = manager.invoke('grant_append', {'path': 'other.md'}, lambda _: 'y')
        self.assertEqual(state, '被拒绝')
        self.assertFalse(manager.grant_path.exists())

    def test_append_never_creates_or_escapes_and_replaced_file_loses_grant(self):
        manager = self.manager()
        manager.cfg = SimpleNamespace(tools={'max_result_bytes': 64,
            'persistent_append_file': 'growth-journal.md'})
        manager.grant_path = self.root / 'append-grant.json'
        manager.tools += [SimpleNamespace(name='grant_append'), SimpleNamespace(name='append_file')]
        target = self.workspace / 'growth-journal.md'
        for path in ('missing.md', '../outside.md'):
            result, state, _ = manager.invoke('append_file',
                {'path': path, 'content': 'new'}, lambda _: 'y')
            self.assertEqual(state, '被拒绝')
        target.write_text('old', encoding='utf-8')
        self.assertEqual(manager.invoke('grant_append', {'path': 'growth-journal.md'}, lambda _: 'y')[1], '成功')
        target.unlink()
        target.write_text('replacement', encoding='utf-8')
        result, state, _ = manager.invoke('append_file',
            {'path': 'growth-journal.md', 'content': 'new'}, lambda _: 'n')
        self.assertEqual(state, '被拒绝')
        self.assertEqual(target.read_text(encoding='utf-8'), 'replacement')

    def test_journal_tools_are_offered_when_filesystem_starts(self):
        store = Store(self.root / 'data')
        cfg = SimpleNamespace(id='test', tools={'enabled': True,
            'folder': 'data/test/workspace', 'max_steps': 3,
            'max_result_bytes': 64, 'persistent_append_file': 'growth-journal.md'})
        worker = SimpleNamespace(tools=[SimpleNamespace(name='write_file',
            description='Write', inputSchema={})], close=Mock())
        with patch('tools_agent.shutil.which', return_value='node'), \
             patch('tools_agent._McpWorker', return_value=worker):
            manager = FilesystemTools(self.root, cfg, store)
        names = {tool.name for tool in manager.tools}
        self.assertEqual(names, {'write_file', 'append_file', 'grant_append',
                                 'create_directory', 'move_lab_file', 'lab_file_digest'})
        self.assertEqual({item['function']['name'] for item in permitted_tools(manager.tools)}, names)
        self.assertEqual(manager.grant_path, self.root / 'data/test/append-grant.json')

    def test_append_refuses_file_replaced_between_check_and_open(self):
        manager = self.manager()
        manager.cfg = SimpleNamespace(tools={'max_result_bytes': 64})
        manager.grant_path = self.root / 'append-grant.json'
        manager.tools += [SimpleNamespace(name='append_file')]
        target = self.workspace / 'journal.md'
        target.write_text('original', encoding='utf-8')
        real_open = os.open
        def replace_then_open(path, flags, *args, **kwargs):
            if Path(path).name == target.name:
                target.unlink()
                target.write_text('replacement', encoding='utf-8')
            return real_open(path, flags, *args, **kwargs)
        with patch('tools_agent.os.open', side_effect=replace_then_open):
            result, state, _ = manager.invoke('append_file',
                {'path': 'journal.md', 'content': 'new'}, lambda _: 'y')
        self.assertEqual(state, '被拒绝')
        self.assertEqual(target.read_text(encoding='utf-8'), 'replacement')

    def test_exclusive_write_refuses_a_file_created_during_approval(self):
        manager = self.manager()
        target = self.workspace / 'summary.txt'
        def approve(_):
            target.write_text('racing content', encoding='utf-8')
            return 'y'
        result, state, _ = manager.invoke('write_file',
            {'path': 'summary.txt', 'content': 'new content'}, approve)
        self.assertEqual(state, '被拒绝')
        self.assertIn('target already exists', result)
        self.assertEqual(target.read_text(encoding='utf-8'), 'racing content')

    def test_exclusive_create_does_not_overwrite_at_the_open_boundary(self):
        manager = self.manager()
        target = self.workspace / 'summary.txt'
        original_open = Path.open
        def racing_open(path, mode='r', *args, **kwargs):
            if mode == 'x' and path.name == target.name:
                with original_open(path, 'w', encoding='utf-8') as handle:
                    handle.write('racing content')
            return original_open(path, mode, *args, **kwargs)
        with patch.object(Path, 'open', racing_open):
            result, state, _ = manager.invoke('write_file',
                {'path': 'summary.txt', 'content': 'new content'}, lambda _: 'y')
        self.assertEqual(state, '被拒绝')
        self.assertIn('target already exists', result)
        self.assertEqual(target.read_text(encoding='utf-8'), 'racing content')

    def test_denied_tools_and_traversal_never_reach_server(self):
        manager = self.manager()
        result, state, _ = manager.invoke('edit_file', {'path': 'a.txt', 'edits': []})
        self.assertEqual(state, '被拒绝')
        result, state, _ = manager.invoke('read_text_file', {'path': '../secret.txt'})
        self.assertEqual(state, '被拒绝')
        manager.client.call.assert_not_called()

    def test_result_is_truncated_by_utf8_bytes(self):
        manager = self.manager()
        manager.client.call.return_value = '你' * 40
        result, state, _ = manager.invoke('read_text_file', {'path': 'a.txt'})
        self.assertIn('已截断，共 120 字节', result)
        self.assertLessEqual(len(result.split('\n')[0].encode('utf-8')), 64)
        self.assertEqual(state, '成功')

    def test_capability_manifest_is_versioned_relative_and_delivered_once(self):
        store = Store(self.root / 'data')
        manager = FilesystemTools.__new__(FilesystemTools)
        manager.cfg = SimpleNamespace(id='test')
        manager.store = store
        manager.folder_name = 'workspace'
        manager.cap_path = self.root / 'data' / 'test' / 'capabilities.json'
        manager.capability_event = None
        manager._record_capabilities(['read_text_file', 'write_file'])
        manifest = json.loads(manager.cap_path.read_text(encoding='utf-8'))
        self.assertEqual(manifest['version'], 1)
        self.assertEqual(manifest['folder'], 'workspace')
        self.assertEqual(manifest['tools'], {'read_text_file': 'allow', 'write_file': 'ask'})
        self.assertNotIn(str(self.root), json.dumps(manifest))
        self.assertTrue(manager.capability_event.startswith('Capability update:'))
        self.assertEqual(manager.capability_event_model, manager.capability_event)
        self.assertIn('ask lan first', FilesystemTools._description(manager, ['write_file']))
        manager.consume_capability_event()
        reopened = FilesystemTools.__new__(FilesystemTools)
        reopened.cfg, reopened.store = manager.cfg, store
        reopened.folder_name, reopened.cap_path = manager.folder_name, manager.cap_path
        reopened.capability_event = None
        reopened._record_capabilities(['read_text_file', 'write_file'])
        self.assertIsNone(reopened.capability_event)

    def test_lab_capability_notice_is_english_complete_and_survives_reopen(self):
        store = Store(self.root / 'data')

        def manager():
            result = FilesystemTools.__new__(FilesystemTools)
            result.cfg = SimpleNamespace(id='fictional', memory=None)
            result.store, result.folder_name = store, 'fictional_lab'
            result.cap_path = self.root / 'data' / 'fictional' / 'capabilities.json'
            result.capability_event = result.capability_event_model = None
            result.enabled, result.tools, result.description = True, [], 'Tools. '
            return result

        first = manager()
        first._record_capabilities(['lab_file_digest', 'move_lab_file'])
        event = first.capability_event
        manifest = json.loads(first.cap_path.read_text(encoding='utf-8'))
        self.assertTrue(event.startswith('Capability update:'))
        self.assertNotRegex(event, r'[\u3400-\u9fff]')
        self.assertIn('lab_file_digest', event)
        self.assertIn('move_lab_file', event)
        self.assertIn('direct use only inside the lab folder', event)
        self.assertEqual(manifest['pending_event'], event)
        self.assertEqual(manifest['pending_event_model'], event)

        reopened = manager()
        reopened._record_capabilities(['lab_file_digest', 'move_lab_file'])
        self.assertEqual(reopened.capability_event, event)
        self.assertEqual(reopened.capability_event_model, event)

        legacy = json.loads(first.cap_path.read_text(encoding='utf-8'))
        legacy['pending_event'] = '你的能力有变化：旧版未送达通知。'
        legacy['pending_event_model'] = 'Current capability update: fixture tools are available.'
        first.cap_path.write_text(json.dumps(legacy), encoding='utf-8')
        migrated = manager()
        migrated._record_capabilities(['lab_file_digest', 'move_lab_file'])
        self.assertTrue(migrated.capability_event.startswith('Capability update:'))
        self.assertIn('lab_file_digest', migrated.capability_event)
        self.assertEqual(migrated.capability_event_model, migrated.capability_event)

        reopened.consume_capability_event()
        changed = manager()
        changed._record_capabilities(['lab_file_digest'])
        self.assertIn('Removed move_lab_file', changed.capability_event)

        manifest = json.loads(changed.cap_path.read_text(encoding='utf-8'))
        manifest['tools']['lab_file_digest'] = 'ask'
        manifest['pending_event'] = manifest['pending_event_model'] = None
        changed.cap_path.write_text(json.dumps(manifest), encoding='utf-8')
        permission_changed = manager()
        permission_changed._record_capabilities(['lab_file_digest'])
        self.assertIn('Changed lab_file_digest', permission_changed.capability_event)

    def test_existing_manifest_announces_send_message_once(self):
        store = Store(self.root / 'data')
        def manager():
            m = FilesystemTools.__new__(FilesystemTools)
            m.cfg, m.store, m.folder_name = SimpleNamespace(id='test'), store, 'workspace'
            m.cap_path = self.root / 'data' / 'test' / 'capabilities.json'
            m.capability_event, m.enabled = None, True
            return m
        m = manager()
        m.cap_path.parent.mkdir(parents=True, exist_ok=True)
        m.cap_path.write_text(json.dumps({'version': 1, 'folder': 'workspace',
            'tools': {'read_text_file': 'allow'}, 'lab': False, 'pending_event': None}), encoding='utf-8')
        m._record_capabilities(['read_text_file'])
        self.assertIn('Added send_message', m.capability_event)
        self.assertTrue(json.loads(m.cap_path.read_text(encoding='utf-8'))['messaging'])
        m.consume_capability_event()
        again = manager()
        again._record_capabilities(['read_text_file'])
        self.assertIsNone(again.capability_event)

    def test_autonomy_capability_is_announced_once_and_survives_reopen(self):
        store = Store(self.root / 'data')
        manager = FilesystemTools.__new__(FilesystemTools)
        manager.cfg, manager.store, manager.folder_name = SimpleNamespace(id='test'), store, 'workspace'
        manager.cap_path = self.root / 'data' / 'test' / 'capabilities.json'
        manager.capability_event, manager.enabled = None, True
        manager._record_capabilities(['read_text_file'])
        manager.consume_capability_event()
        manager.announce_autonomy()
        self.assertIn('send_message', manager.capability_event)
        self.assertIn('up to three chat messages per wake', manager.capability_event)
        self.assertIn('or none', manager.capability_event)
        self.assertEqual(manager.capability_event_model, manager.capability_event)
        manager.consume_capability_event()
        reopened = FilesystemTools.__new__(FilesystemTools)
        reopened.cfg, reopened.store, reopened.folder_name = manager.cfg, store, 'workspace'
        reopened.cap_path, reopened.capability_event, reopened.enabled = manager.cap_path, None, True
        reopened._record_capabilities(['read_text_file'])
        self.assertTrue(json.loads(reopened.cap_path.read_text(encoding='utf-8'))['autonomy_announced'])
        reopened.announce_autonomy()
        self.assertIsNone(reopened.capability_event)

    def test_long_task_capability_is_announced_once_and_survives_capability_refresh(self):
        store = Store(self.root / 'data')
        manager = FilesystemTools.__new__(FilesystemTools)
        manager.cfg, manager.store, manager.folder_name = SimpleNamespace(id='test'), store, 'workspace'
        manager.cap_path = self.root / 'data' / 'test' / 'capabilities.json'
        manager.capability_event, manager.enabled = None, True
        manager._record_capabilities(['read_text_file'])
        manager.consume_capability_event()
        manager.announce_long_tasks()
        self.assertIn('long-task tools are available', manager.capability_event)
        self.assertEqual(manager.capability_event_model, manager.capability_event)
        manager.consume_capability_event()

        reopened = FilesystemTools.__new__(FilesystemTools)
        reopened.cfg, reopened.store, reopened.folder_name = manager.cfg, store, 'workspace'
        reopened.cap_path, reopened.capability_event, reopened.enabled = manager.cap_path, None, True
        reopened._record_capabilities(['read_text_file'])
        self.assertTrue(json.loads(reopened.cap_path.read_text(encoding='utf-8'))['long_tasks_announced'])
        reopened.announce_long_tasks()
        self.assertIsNone(reopened.capability_event)

    def test_resident_memory_capability_is_announced_once(self):
        store = Store(self.root / 'data')
        def manager():
            m = FilesystemTools.__new__(FilesystemTools)
            m.cfg, m.store, m.folder_name = SimpleNamespace(id='test', memory={'enabled': True}), store, 'workspace'
            m.cap_path = self.root / 'data' / 'test' / 'capabilities.json'
            m.capability_event, m.enabled = None, True
            return m
        first = manager()
        first._record_capabilities(['read_text_file'])
        self.assertIn('memories now fade naturally', first.capability_event)
        self.assertTrue(json.loads(first.cap_path.read_text(encoding='utf-8'))['resident_memory'])
        first.consume_capability_event()
        second = manager()
        second._record_capabilities(['read_text_file'])
        self.assertIsNone(second.capability_event)

    def test_autonomy_announcement_is_noop_without_file_tools(self):
        store = Store(self.root / 'data')
        manager = FilesystemTools(self.root, SimpleNamespace(id='test', tools=None), store)
        manager.announce_autonomy()
        self.assertIsNone(manager.capability_event)

    def test_missing_node_disables_tools_without_blocking_chat_startup(self):
        store = Store(self.root / 'data')
        cfg = SimpleNamespace(id='test', tools={'enabled': True, 'folder': 'data/test/workspace',
            'max_steps': 6, 'max_result_bytes': 8192})
        with patch('tools_agent.shutil.which', return_value=None):
            manager = FilesystemTools(self.root, cfg, store)
        self.assertEqual(manager.tools, [])
        self.assertIsNone(manager.client)
        self.assertIn('文件工具不可用', manager.startup_notice)
        self.assertTrue((self.root / 'data' / 'test' / 'capabilities.json').exists())


class ToolLoopTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / 'characters' / 'test').mkdir(parents=True)
        (self.root / 'persona').mkdir()
        (self.root / 'persona' / 'test.md').write_text('test persona', encoding='utf-8')
        (self.root / 'characters' / 'test' / 'character.toml').write_text('''name = "Peter"
provider = "fake"
persona = "persona/test.md"
max_output_tokens = 50
[providers.fake]
model = "fake"
base_url = "https://example.invalid/v1"
api_key_env = "FAKE_KEY"
max_input_tokens = 6000
input_cny_per_million = "2"
cached_input_cny_per_million = "1"
output_cny_per_million = "8"
[tools]
enabled = true
max_steps = 3
max_result_bytes = 200
''', encoding='utf-8')
        self.cfg = load_character(self.root, 'test')
        self.store = Store(self.root / 'data')
        self.budget = Budget(self.store)

    def message(self, calls=None, content=None, reasoning='thinking'):
        serialized_calls = [{'id': call.id, 'type': 'function', 'function': {
            'name': call.function.name, 'arguments': call.function.arguments}} for call in calls or []]
        payload = {'role': 'assistant', 'content': content, 'tool_calls': serialized_calls}
        if calls:
            payload['reasoning_content'] = reasoning
        return SimpleNamespace(content=content, tool_calls=calls or [], reasoning_content=reasoning,
                               model_dump=lambda exclude_none=True: payload)

    def tool_call(self, name, args, call_id):
        return SimpleNamespace(id=call_id, function=SimpleNamespace(name=name, arguments=args))

    def test_chat_can_grant_then_append_without_second_terminal_prompt(self):
        self.cfg.tools['persistent_append_file'] = 'growth-journal.md'
        tools = FilesystemTools.__new__(FilesystemTools)
        tools.workspace = self.root / 'workspace'
        tools.workspace.mkdir()
        target = tools.workspace / 'growth-journal.md'
        target.write_text('first', encoding='utf-8')
        tools.cfg = self.cfg
        tools.grant_path = self.root / 'append-grant.json'
        tools.capability_event = None
        tools.tools = [SimpleNamespace(name=name, description=name, inputSchema={})
                       for name in ('grant_append', 'append_file')]
        model = Mock(side_effect=[
            (self.message([self.tool_call('grant_append',
                '{"path":"growth-journal.md"}', 'grant')]), 100, 20, 0),
            (self.message([self.tool_call('append_file',
                '{"path":"growth-journal.md","content":"\\n## 2026-10-20\\nsecond"}', 'append')]), 120, 30, 0),
            (self.message(content='Second entry added.'), 120, 20, 0)])
        approval = Mock(return_value='y')
        output = []
        chat = Chat(self.cfg, self.store, self.budget, model, tools=tools,
                    approval=approval, output=output.append)
        self.assertEqual(chat.send('Please continue the journal'), 'Second entry added.')
        self.assertEqual(target.read_text(encoding='utf-8'), 'first\n## 2026-10-20\nsecond')
        approval.assert_called_once()
        self.assertEqual([row['state'] for row in self.store.read('test/tools.jsonl')],
                         ['成功', '成功'])

    def test_first_self_preview_shows_tools_and_capability_notice(self):
        tools = SimpleNamespace(
            tools=[SimpleNamespace(name='read_text_file', description='Read', inputSchema={})],
            capability_event='Capability update: read access is available in the fictional test folder.',
            description='You can read files in workspace.')
        model = Mock()
        chat = Chat(self.cfg, self.store, self.budget, model, tools=tools)
        output = []
        run_chat(chat, Mock(side_effect=['/self', '/quit']), output.append)
        self.assertNotIn('Capability update:', '\n'.join(output))
        self.assertIn('You can read files in workspace.', '\n'.join(output))
        model.assert_not_called()

    def test_multi_step_parallel_calls_and_chat_log_roles(self):
        first = self.message([
            self.tool_call('read_text_file', '{"path":"one.txt"}', 'one'),
            self.tool_call('read_text_file', '{"path":"two.txt"}', 'two')])
        final = self.message(content='Here is the summary.')
        model = Mock(side_effect=[(first, 100, 20, 0), (final, 150, 20, 0)])
        tools = Mock()
        tools.tools = [SimpleNamespace(name='read_text_file', description='Read', inputSchema={})]
        tools.capability_event = None
        tools.invoke.side_effect = [('one contents', '成功', 'one.txt'),
                                    ('two contents', '成功', 'two.txt')]
        output = []
        chat = Chat(self.cfg, self.store, self.budget, model, tools=tools, output=output.append)
        self.assertEqual(chat.send('summarize both'), 'Here is the summary.')
        self.assertEqual([row['role'] for row in self.store.read('test/chat.jsonl')], ['user', 'assistant'])
        self.assertEqual(len(self.store.read('test/tools.jsonl')), 2)
        self.assertEqual(len(model.call_args_list), 2)
        second_messages = model.call_args_list[1].args[0]
        self.assertIn('reasoning_content', second_messages[-3])
        self.assertEqual([row['role'] for row in second_messages[-2:]], ['tool', 'tool'])
        self.assertEqual(len(output), 2)
        self.assertGreater(self.budget.totals()[0], 0)


    def test_program_tool_failures_are_actionable_english_for_model_and_user(self):
        first = self.message([self.tool_call('write_file',
            '{"path":"report.md","content":"fictional"}', 'write')])
        final = self.message(content='I will ask before trying again.')
        model = Mock(side_effect=[(first, 100, 20, 0), (final, 120, 20, 0)])
        tools = Mock()
        tools.tools = [SimpleNamespace(name='write_file', description='Write', inputSchema={})]
        tools.capability_event = None
        tools.invoke.return_value = ('Lan did not approve this write, so the file was not changed; request approval before retrying.', '被拒绝', 'report.md')
        output = []
        chat = Chat(self.cfg, self.store, self.budget, model, tools=tools, output=output.append)

        self.assertEqual(chat.send('create a fictional report'), 'I will ask before trying again.')
        tool_result = model.call_args_list[1].args[0][-1]['content']
        expected = 'Lan did not approve this write, so the file was not changed; request approval before retrying.'
        self.assertEqual(tool_result, expected)
        self.assertTrue(any(tool_result in line for line in output))

    def test_memory_target_id_error_is_source_authored_english_and_passed_through(self):
        source = 'target_id must be an exact visible result from breath_search in this turn; search again and use its ID.'
        translated = model_tool_result('trace', source, '被拒绝')
        self.assertEqual(translated, source)
        self.assertIn('breath_search in this turn', translated)
        self.assertIn('use its ID', translated)

        task_state = 'The task status is invalid; choose active, paused, blocked, or completed.'
        self.assertEqual(model_tool_result('long_task_update', task_state, '被拒绝'), task_state)
        failure = 'The fictional file target is not available; choose a permitted path.'
        self.assertEqual(model_tool_result('read_text_file', failure, '被拒绝'), failure)
        digest = '{"path":"lab/archive/设计.md","byte_length":12000,"sha256":"abc"}'
        self.assertEqual(model_tool_result('lab_file_digest', digest, '成功'), digest)

    def test_invalid_json_is_returned_as_tool_error_and_counted(self):
        first = self.message([self.tool_call('read_text_file', '{bad', 'bad')])
        final = self.message(content='I could not read it.')
        model = Mock(side_effect=[(first, 100, 20, 0), (final, 120, 20, 0)])
        tools = Mock()
        tools.tools = [SimpleNamespace(name='read_text_file', description='Read', inputSchema={})]
        tools.capability_event = None
        output = []
        chat = Chat(self.cfg, self.store, self.budget, model, tools=tools, output=output.append)
        self.assertEqual(chat.send('read it'), 'I could not read it.')
        tools.invoke.assert_not_called()
        self.assertIn('arguments are not a valid JSON object', model.call_args_list[1].args[0][-1]['content'])
        self.assertTrue(any('arguments are not a valid JSON object' in line for line in output))

    def test_tool_output_limit_is_reserved_sent_and_settled_without_raising_daily_cap(self):
        self.cfg.tools['max_output_tokens'] = 4096
        self.cfg.tools['max_steps'] = 2
        first = self.message([self.tool_call('write_file',
            '{"path":"journal.md","content":"hello"}', 'write')])
        final = self.message(content='Created journal.md.')
        model = Mock(side_effect=[(first, 100, 2048, 0), (final, 120, 20, 0)])
        tools = Mock()
        tools.tools = [SimpleNamespace(name='write_file', description='Write', inputSchema={})]
        tools.capability_event = None
        tools.invoke.return_value = ('新文件已创建。', '成功', 'journal.md')
        chat = Chat(self.cfg, self.store, self.budget, model, tools=tools, output=lambda _: None)
        self.assertEqual(chat.send('create journal.md'), 'Created journal.md.')
        self.assertEqual([call.kwargs['max_tokens'] for call in model.call_args_list], [4096, 50])
        reserves = [row for row in self.store.read('costs.jsonl') if row['type'] == 'reserve']
        self.assertEqual(Decimal(reserves[0]['amount']), Decimal('0.044768'))
        self.assertEqual(self.budget.totals()[2], 0)

    def test_length_finish_refuses_partial_write_and_recovers_at_most_once(self):
        first = self.message([self.tool_call('write_file',
            '{"path":"journal.md","content":"partial"}', 'write')])
        model = Mock(return_value=(first, 100, 50, 0, 'length'))
        tools = Mock()
        tools.tools = [SimpleNamespace(name='write_file', description='Write', inputSchema={})]
        tools.capability_event = None
        output = []
        chat = Chat(self.cfg, self.store, self.budget, model, tools=tools, output=output.append)
        self.assertIn('output limit', chat.send('create journal.md'))
        tools.invoke.assert_not_called()
        self.assertEqual(model.call_count, 2)
        self.assertTrue(any('output limit' in line for line in output))

    def test_output_recovery_keeps_confirmed_write_and_discards_truncated_json(self):
        self.cfg.tools['max_steps'] = 5
        self.cfg.tools['max_output_tokens'] = 1024
        first = self.message([self.tool_call('append_file', '{"path":"one","content":"kept"}', 'ok')])
        partial = self.message([self.tool_call('append_file', '{"path":"two","content":"BROKEN', 'bad')])
        recovered = self.message([self.tool_call('append_file', '{"path":"two","content":"small"}', 'new')])
        snapshots = []
        replies = iter([(first, 100, 20, 0), (partial, 120, 1024, 0, 'length'),
                        (recovered, 140, 20, 0), (self.message(content='done'), 160, 20, 0)])
        def model(messages, **kwargs):
            snapshots.append(json.loads(json.dumps(messages)))
            return next(replies)
        tools = Mock()
        tools.tools = [SimpleNamespace(name='append_file', description='Append', inputSchema={})]
        tools.capability_event = None
        tools.invoke.side_effect = [('kept', '成功', 'one'), ('small', '成功', 'two')]
        chat = Chat(self.cfg, self.store, self.budget, model, tools=tools, output=lambda _: None)
        self.assertEqual(chat.send('continue'), 'done')
        self.assertEqual(tools.invoke.call_count, 2)
        serialized = json.dumps(snapshots[2])
        self.assertNotIn('BROKEN', serialized)
        self.assertIn('kept', serialized)
        self.assertEqual(chat.tool_stop_reason, '')
        self.assertEqual([r['state'] for r in self.store.read('test/tools.jsonl')], ['成功', '成功'])
        self.assertEqual(self.budget.totals()[2], 0)

    def test_output_recovery_respects_remaining_steps(self):
        self.cfg.tools['max_steps'] = 1
        partial = self.message([self.tool_call('write_file', '{', 'bad')])
        model = Mock(return_value=(partial, 100, 50, 0, 'length'))
        tools = Mock(tools=[SimpleNamespace(name='write_file', description='Write', inputSchema={})],
                     capability_event=None)
        chat = Chat(self.cfg, self.store, self.budget, model, tools=tools, output=lambda _: None)
        self.assertIn('this step did not run', chat.send('continue'))
        model.assert_called_once()
        tools.invoke.assert_not_called()
        self.assertEqual(self.budget.totals()[2], 0)

    def test_length_finish_keeps_text_when_no_tool_was_called(self):
        model = Mock(return_value=(self.message(content='A partial answer.'), 100, 50, 0, 'length'))
        tools = Mock()
        tools.tools = [SimpleNamespace(name='write_file', description='Write', inputSchema={})]
        tools.capability_event = None
        chat = Chat(self.cfg, self.store, self.budget, model, tools=tools)
        self.assertEqual(chat.send('explain this'), 'A partial answer.')
        tools.invoke.assert_not_called()

    def test_chat_permission_still_needs_terminal_y_and_shows_refusal(self):
        tools = FilesystemTools.__new__(FilesystemTools)
        tools.workspace = self.root / 'workspace'
        tools.workspace.mkdir()
        tools.tools = [SimpleNamespace(name='write_file', description='Write', inputSchema={})]
        tools.capability_event = None
        tools.cfg = self.cfg
        call = self.message([self.tool_call('write_file',
            '{"path":"growth-journal.md","content":"fictional entry"}', 'write')])
        for approval, expected_state in [('n', '被拒绝'), ('y', '成功')]:
            with self.subTest(approval=approval):
                model = Mock(side_effect=[(call, 100, 40, 0),
                                          (self.message(content='done'), 120, 20, 0)])
                output = []
                chat = Chat(self.cfg, self.store, self.budget, model, tools=tools,
                            approval=Mock(return_value=approval), output=output.append)
                chat.send('I agree to create the journal')
                self.assertTrue(any(expected_state in line for line in output))
                self.assertEqual((tools.workspace / 'growth-journal.md').exists(), approval == 'y')
                if approval == 'n':
                    self.assertTrue(any('Lan did not approve this write' in line for line in output))
                else:
                    self.assertEqual((tools.workspace / 'growth-journal.md').read_text(encoding='utf-8'),
                                     'fictional entry')

    def test_larger_tool_reservation_still_obeys_daily_cap(self):
        self.cfg.tools['max_output_tokens'] = 4096
        self.budget.daily = Decimal('0.02')
        tools = Mock()
        tools.tools = [SimpleNamespace(name='write_file', description='Write', inputSchema={})]
        tools.capability_event = None
        model = Mock()
        chat = Chat(self.cfg, self.store, self.budget, model, tools=tools)
        from veil_agent import BudgetExceeded
        with self.assertRaises(BudgetExceeded):
            chat.send('create a journal')
        model.assert_not_called()
        tools.invoke.assert_not_called()

    def test_max_steps_stops_without_another_model_request(self):
        self.cfg.tools['max_steps'] = 2
        first = self.message([self.tool_call('read_text_file', '{"path":"one.txt"}', 'one')])
        final_unexpected_call = self.message([self.tool_call('read_text_file', '{"path":"two.txt"}', 'two')])
        model = Mock(side_effect=[(first, 100, 20, 0), (final_unexpected_call, 100, 20, 0)])
        tools = Mock()
        tools.tools = [SimpleNamespace(name='read_text_file', description='Read', inputSchema={})]
        tools.capability_event = None
        tools.invoke.return_value = ('contents', '成功', 'one.txt')
        output = []
        chat = Chat(self.cfg, self.store, self.budget, model, tools=tools, output=output.append)
        self.assertEqual(chat.send('read it'), 'The tool-step limit for this request has been reached.')
        self.assertEqual(model.call_count, 2)
        self.assertEqual(tools.invoke.call_count, 1)
        self.assertIn('The tool-step limit for this request has been reached.', output)

    def test_final_step_uses_existing_tool_results_without_offering_more_tools(self):
        self.cfg.tools['max_steps'] = 2
        first = self.message([self.tool_call('read_text_file', '{"path":"one.txt"}', 'one')])
        final = self.message(content='The file says hello.')
        model = Mock(side_effect=[(first, 100, 20, 0), (final, 130, 20, 0)])
        tools = Mock()
        tools.tools = [SimpleNamespace(name='read_text_file', description='Read', inputSchema={})]
        tools.capability_event = None
        tools.invoke.return_value = ('hello', '成功', 'one.txt')
        output = []
        chat = Chat(self.cfg, self.store, self.budget, model, tools=tools, output=output.append)
        self.assertEqual(chat.send('read it'), 'The file says hello.')
        self.assertEqual(model.call_count, 2)
        self.assertIsNotNone(model.call_args_list[0].kwargs['tools'])
        self.assertIsNone(model.call_args_list[1].kwargs['tools'])
        self.assertIn('The tool-step limit for this request has been reached.', output)
        costs = self.store.read('costs.jsonl')
        self.assertEqual(len([row for row in costs if row['type'] == 'reserve']), 2)
        self.assertEqual(len([row for row in costs if row['type'] == 'settle']), 2)

    def test_budget_exhaustion_refuses_tool_before_side_effect(self):
        self.budget.daily = Decimal('0.0125')
        first = self.message([self.tool_call('write_file',
            '{"path":"summary.txt","content":"new"}', 'write')])
        model = Mock(return_value=(first, 100, 20, 0))
        tools = Mock()
        tools.tools = [SimpleNamespace(name='write_file', description='Write', inputSchema={})]
        tools.capability_event = None
        tools.preview_summary.return_value = 'summary.txt'
        output = []
        chat = Chat(self.cfg, self.store, self.budget, model, tools=tools, output=output.append)
        self.assertEqual(chat.send('write it'), 'The tool-step limit for this request has been reached.')
        tools.invoke.assert_not_called()
        self.assertEqual(model.call_count, 1)
        self.assertTrue(any('spending limit' in line for line in output))
        logs = self.store.read('test/tools.jsonl')
        self.assertEqual(logs[0]['state'], '被拒绝')

    def test_short_result_runs_even_when_maximum_result_could_overflow(self):
        self.cfg.tools['max_result_bytes'] = 6000
        first = self.message([self.tool_call('read_text_file', '{"path":"large.txt"}', 'read')])
        final = self.message(content='The file says hello.')
        model = Mock(side_effect=[(first, 100, 20, 0), (final, 120, 20, 0)])
        tools = Mock()
        tools.tools = [SimpleNamespace(name='read_text_file', description='Read', inputSchema={})]
        tools.capability_event = None
        tools.preview_summary.return_value = 'large.txt'
        tools.invoke.return_value = ('hello', '成功', 'large.txt')
        output = []
        chat = Chat(self.cfg, self.store, self.budget, model, tools=tools, output=output.append)
        self.assertEqual(chat.send('read it'), 'The file says hello.')
        tools.invoke.assert_called_once()
        self.assertEqual(model.call_args_list[1].args[0][-1]['content'], 'hello')

    def test_windows_reported_read_tools_all_run_for_short_results(self):
        self.cfg.tools['max_result_bytes'] = 6000
        names = ('list_directory', 'get_file_info', 'read_text_file')
        first = self.message([self.tool_call(name, '{"path":"test.txt"}', name) for name in names])
        final = self.message(content='test.txt contains hello')
        model = Mock(side_effect=[(first, 100, 20, 0), (final, 120, 20, 0)])
        tools = Mock()
        tools.tools = [SimpleNamespace(name=name, description=name, inputSchema={}) for name in names]
        tools.capability_event = None
        tools.invoke.side_effect = [(name + ': hello', '成功', 'test.txt') for name in names]
        chat = Chat(self.cfg, self.store, self.budget, model, tools=tools, output=lambda _: None)
        self.assertEqual(chat.send('inspect test.txt'), 'test.txt contains hello')
        self.assertEqual([call.args[0] for call in tools.invoke.call_args_list], list(names))
        self.assertEqual([row['content'] for row in model.call_args_list[1].args[0][-3:]],
                         [name + ': hello' for name in names])

    def test_oversized_tool_call_record_refuses_write_before_approval(self):
        first = self.message([self.tool_call('write_file',
            '{"path":"new.txt","content":"hello"}', 'write')], reasoning='x' * 14000)
        model = Mock(return_value=(first, 100, 20, 0))
        tools = Mock()
        tools.tools = [SimpleNamespace(name='write_file', description='Write', inputSchema={})]
        tools.capability_event = None
        chat = Chat(self.cfg, self.store, self.budget, model, tools=tools, output=lambda _: None)
        self.assertIn('tool was not executed', chat.send('write it'))
        tools.invoke.assert_not_called()

    def test_measured_prompt_baseline_keeps_room_for_next_tool_record(self):
        first = self.message([self.tool_call('write_file',
            '{"path":"lab/new.md","content":"hello"}', 'write')], reasoning='x' * 8000)
        model = Mock(side_effect=[(first, 200, 20, 0), (self.message(content='Saved.'), 2400, 20, 0)])
        tools = Mock()
        tools.tools = [SimpleNamespace(name='write_file', description='Write', inputSchema={})]
        tools.capability_event = None
        tools.invoke.return_value = ('saved', '成功', 'lab/new.md')
        chat = Chat(self.cfg, self.store, self.budget, model, tools=tools, output=lambda _: None)
        self.assertEqual(chat.send('write it'), 'Saved.')
        self.assertEqual(tools.invoke.call_count, 1)
        self.assertEqual(model.call_count, 2)

    def test_real_result_over_limit_is_not_sent_to_model(self):
        self.cfg.tools['max_result_bytes'] = 6000
        self.store.append('test/chat.jsonl', {'role': 'user', 'content': 'old ' + 'x' * 2100})
        self.store.append('test/chat.jsonl', {'role': 'assistant', 'content': 'old answer'})
        first = self.message([self.tool_call('read_text_file', '{"path":"large.txt"}', 'read')])
        final = self.message(content='The result was too long.')
        model = Mock(side_effect=[(first, 100, 20, 0), (final, 120, 20, 0)])
        tools = Mock()
        tools.tools = [SimpleNamespace(name='read_text_file', description='Read', inputSchema={})]
        tools.capability_event = None
        tools.invoke.return_value = ('secret' * 3000, '成功', 'large.txt')
        chat = Chat(self.cfg, self.store, self.budget, model, tools=tools, output=lambda _: None)
        self.assertEqual(chat.send('read it'), 'The result was too long.')
        forwarded = model.call_args_list[1].args[0][-1]['content']
        self.assertIn('exceeded this request input limit', forwarded)
        self.assertNotIn('secret', forwarded)
        self.assertTrue(any((m.get('content') or '').startswith('old ') for m in model.call_args_list[1].args[0]))

    def test_tool_result_drops_oldest_history_round_before_reply(self):
        self.cfg.tools['max_result_bytes'] = 6000
        self.store.append('test/chat.jsonl', {'role': 'user', 'content': 'old ' + 'x' * 2100})
        self.store.append('test/chat.jsonl', {'role': 'assistant', 'content': 'old answer'})
        first = self.message([self.tool_call('read_text_file', '{"path":"test.txt"}', 'read')])
        final = self.message(content='The file has test data.')
        requests = []
        responses = [first, final]
        def model(messages, **_kwargs):
            requests.append(json.loads(json.dumps(messages)))
            # Report a prompt size that matches the request, as a real provider would.
            return responses[len(requests) - 1], min(6000, len(json.dumps(messages, ensure_ascii=False).encode('utf-8')) + 256 * len(messages) + 500), 20, 0
        tools = Mock()
        tools.tools = [SimpleNamespace(name='read_text_file', description='Read', inputSchema={})]
        tools.capability_event = None
        tools.invoke.return_value = ('test data ' * 220, '成功', 'test.txt')
        chat = Chat(self.cfg, self.store, self.budget, model, tools=tools, output=lambda _: None)
        self.assertEqual(chat.send('read test.txt'), 'The file has test data.')
        first_request, second_request = requests
        self.assertTrue(any((m.get('content') or '').startswith('old ') for m in first_request))
        self.assertFalse(any((m.get('content') or '').startswith('old ') for m in second_request))
        self.assertEqual(second_request[-1]['content'], 'test data ' * 220)
        self.assertIsNotNone(chat.trim_note)


class MultiMessageTests(unittest.TestCase):
    setUp = ToolLoopTests.setUp
    message = ToolLoopTests.message
    tool_call = ToolLoopTests.tool_call
    def test_messages_in_all_languages_use_one_request_and_survive_restart(self):
        texts = ['Good morning.', '早上好。今天想去走走。', 'Bonjour 👋', '一会儿见，see you!']
        calls = [self.tool_call('send_message', json.dumps({'text': text}), str(i))
                 for i, text in enumerate(texts)]
        model = Mock(return_value=(self.message(calls), 100, 40, 0))
        output = []
        chat = Chat(self.cfg, self.store, self.budget, model, output=output.append)
        run_chat(chat, Mock(side_effect=['hello', '/quit']), output.append)
        self.assertEqual([s for s in output if s.startswith('Peter: ')], ['Peter: ' + t for t in texts])
        model.assert_called_once()
        rows = self.store.read('test/chat.jsonl')
        self.assertEqual([r['content'] for r in rows], ['hello', *texts])
        self.assertTrue(all(r['origin'] == 'reply' for r in rows[1:]))
        self.assertEqual(len({r['request_id'] for r in rows}), 1)
        reopened = Chat(self.cfg, self.store, self.budget, model)
        self.assertEqual([m['content'] for m in reopened.history], ['hello', *texts])
        costs = self.store.read('costs.jsonl')
        self.assertEqual(sum(r['type'] == 'reserve' for r in costs), 1)
        self.assertEqual(self.budget.totals()[2], 0)

    def test_mixed_tools_continue_and_final_duplicate_is_not_displayed_or_saved(self):
        first = self.message([self.tool_call('send_message', '{"text":"先看一下。"}', 'say'),
                              self.tool_call('read_text_file', '{"path":"lab/a.md"}', 'read')])
        model = Mock(side_effect=[(first, 100, 30, 0), (self.message(content='先看一下。'), 120, 20, 0)])
        tools = Mock()
        tools.capability_event = None
        tools.tools = [SimpleNamespace(name='read_text_file', description='Read', inputSchema={})]
        tools.invoke.return_value = ('fictional lab', '成功', 'lab/a.md')
        output = []
        chat = Chat(self.cfg, self.store, self.budget, model, tools=tools, output=output.append)
        run_chat(chat, Mock(side_effect=['read', '/quit']), output.append)
        self.assertEqual(model.call_count, 2)
        tools.invoke.assert_called_once()
        self.assertEqual(output.count('Peter: 先看一下。'), 1)
        self.assertEqual([r['content'] for r in self.store.read('test/chat.jsonl')], ['read', '先看一下。'])
        self.assertEqual([m['role'] for m in model.call_args.args[0][-2:]], ['tool', 'tool'])

    def test_pure_message_with_distinct_final_keeps_both(self):
        model = Mock(return_value=(self.message([
            self.tool_call('send_message', '{"text":"Hi."}', 'one')], content='See you.'), 100, 20, 0))
        chat = Chat(self.cfg, self.store, self.budget, model, output=lambda _: None)
        self.assertEqual(chat.send('hello'), 'See you.')
        self.assertFalse(chat.reply_displayed)
        self.assertEqual([r['content'] for r in self.store.read('test/chat.jsonl')], ['hello', 'Hi.', 'See you.'])

    def test_pure_messages_need_no_followup_budget_and_complete_memory_once(self):
        self.budget.daily = Decimal('0.0125')  # Enough for this request, not a second reservation.
        model = Mock(return_value=(self.message([
            self.tool_call('send_message', '{"text":"Hello."}', 'one')]), 100, 20, 0))
        memory = Mock()
        memory.native = False
        memory.context.return_value = []
        chat = Chat(self.cfg, self.store, self.budget, model, memory=memory, output=lambda _: None)
        self.assertEqual(chat.send('hello'), 'Hello.')
        model.assert_called_once()
        memory.completed.assert_called_once_with()
        self.assertEqual(self.budget.totals()[2], 0)

    def test_invalid_message_arguments_do_not_send_or_request_again(self):
        for arguments in ('{bad', '[]', '{"text":" "}', '{"text":7}', '{"text":"hi","path":"x"}'):
            model = Mock(return_value=(self.message([
                self.tool_call('send_message', arguments, 'invalid')]), 100, 20, 0))
            chat = Chat(self.cfg, self.store, self.budget, model, output=lambda _: None)
            self.assertIn('send_message requires non-empty text', chat.send('hello'))
            self.assertEqual(chat.sent_messages, [])
            model.assert_called_once()

    def test_assistant_first_old_history_is_read_without_rewriting(self):
        self.store.append('test/chat.jsonl', {'role': 'assistant', 'content': 'I can start.'})
        self.store.append('test/chat.jsonl', {'role': 'assistant', 'content': '你好。', 'origin': 'proactive'})
        before = (self.root / 'data/test/chat.jsonl').read_bytes()
        chat = Chat(self.cfg, self.store, self.budget, Mock())
        self.assertEqual([m['content'] for m in chat.history], ['I can start.', '你好。'])
        self.assertEqual([m['origin'] for m in chat.history_events], ['reply', 'proactive'])
        self.assertEqual((self.root / 'data/test/chat.jsonl').read_bytes(), before)


@unittest.skipUnless(shutil.which('node') and shutil.which('npx'),
                     'optional filesystem MCP integration requires Node.js LTS with npm/npx')
class FilesystemServerTests(unittest.TestCase):
    def test_real_filesystem_server_lists_only_the_sandbox_folder(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        root = Path(temp.name)
        store = Store(root / 'data')
        cfg = SimpleNamespace(id='test', tools={'enabled': True,
            'folder': 'data/test/workspace', 'max_steps': 6, 'max_result_bytes': 8192})
        manager = FilesystemTools(root, cfg, store)
        with manager:
            (manager.workspace / 'fiction.txt').write_text('fictional', encoding='utf-8')
            result, state, summary = manager.invoke('list_directory',
                {'path': str(manager.workspace)})
            no_prompt = Mock(side_effect=AssertionError('lab should not ask'))
            created, create_state, create_summary = manager.invoke(
                'create_directory', {'path': 'lab/research'}, no_prompt)
            self.assertEqual(create_state, '成功',
                f'create_directory returned {create_state}: {created}; '
                f'lab={manager.lab!r}; startup_notice={manager.startup_notice!r}; '
                f'summary={create_summary!r}')
            self.assertEqual(manager.invoke('write_file',
                {'path': 'lab/research/v1.md', 'content': 'original'}, no_prompt)[1], '成功')
            self.assertEqual(manager.invoke('append_file',
                {'path': 'lab/research/v1.md', 'content': '\naddition'}, no_prompt)[1], '成功')
            read, read_state, _ = manager.invoke('read_text_file', {'path': 'lab/research/v1.md'})
            self.assertEqual(read_state, '成功')
            self.assertEqual(read, 'original\naddition')
            self.assertEqual(manager.invoke('write_file',
                {'path': 'lab/research/v1.md', 'content': 'overwrite'}, no_prompt)[1], '被拒绝')
        self.assertEqual((state, summary), ('成功', '.'))
        self.assertIn('fiction.txt', result)


    def test_tool_failure_source_strings_are_english(self):
        root = Path(__file__).resolve().parents[1]
        paths = ('long_tasks.py', 'memory_native.py', 'tools_agent.py', 'veil_agent.py', 'memory_agent.py')
        failure_states = {'被拒绝', '出错', False}
        found = 0

        def literal_text(node):
            parts = []
            for child in ast.walk(node):
                if isinstance(child, ast.Constant) and isinstance(child.value, str):
                    parts.append(child.value)
            return ''.join(parts)

        for relative_path in paths:
            source = (root / relative_path).read_text(encoding='utf-8')
            tree = ast.parse(source, filename=relative_path)
            for node in ast.walk(tree):
                if not isinstance(node, ast.Return) or not isinstance(node.value, ast.Tuple):
                    continue
                if len(node.value.elts) < 2:
                    continue
                state = node.value.elts[1]
                if not isinstance(state, ast.Constant) or state.value not in failure_states:
                    continue
                found += 1
                reason = literal_text(node.value.elts[0])
                self.assertNotRegex(
                    reason, r'[\u3400-\u9fff]',
                    f'{relative_path}:{node.lineno} has a non-English tool failure reason')
            if relative_path == 'memory_agent.py':
                for node in ast.walk(tree):
                    if isinstance(node, ast.ClassDef) and node.name == 'MemoryToolError':
                        diagnostics = ''.join(
                            child.value for child in ast.walk(node)
                            if isinstance(child, ast.Constant) and isinstance(child.value, str))
                        self.assertNotRegex(
                            diagnostics, r'[\u3400-\u9fff]',
                            'MemoryToolError diagnostics must be authored in English')

        self.assertGreater(found, 0, 'the scan must inspect tool failure returns')


if __name__ == '__main__':
    unittest.main()
