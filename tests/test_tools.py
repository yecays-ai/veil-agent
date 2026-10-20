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
from veil_agent import Budget, Chat, Store, load_character, run_chat


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
        self.assertIn('拒绝', result)
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
        self.assertIn('未同意', result)
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
        self.assertEqual(names, {'write_file', 'append_file', 'grant_append', 'create_directory'})
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
        self.assertIn('目标已存在', result)
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
        self.assertIn('目标已存在', result)
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
        self.assertIn('你的能力有变化', manager.capability_event)
        manager.consume_capability_event()
        reopened = FilesystemTools.__new__(FilesystemTools)
        reopened.cfg, reopened.store = manager.cfg, store
        reopened.folder_name, reopened.cap_path = manager.folder_name, manager.cap_path
        reopened.capability_event = None
        reopened._record_capabilities(['read_text_file', 'write_file'])
        self.assertIsNone(reopened.capability_event)

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
            capability_event='你的能力有变化：现在可读测试文件夹。',
            description='You can read files in workspace.')
        model = Mock()
        chat = Chat(self.cfg, self.store, self.budget, model, tools=tools)
        output = []
        run_chat(chat, Mock(side_effect=['/self', '/quit']), output.append)
        self.assertIn('你的能力有变化', output[1])
        self.assertIn('You can read files in workspace.', output[3])
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
        self.assertIn('参数不是合法 JSON', model.call_args_list[1].args[0][-1]['content'])
        self.assertTrue(any('参数不是合法 JSON' in line for line in output))

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

    def test_length_finish_refuses_partial_write_without_repeating_model_call(self):
        first = self.message([self.tool_call('write_file',
            '{"path":"journal.md","content":"partial"}', 'write')])
        model = Mock(return_value=(first, 100, 50, 0, 'length'))
        tools = Mock()
        tools.tools = [SimpleNamespace(name='write_file', description='Write', inputSchema={})]
        tools.capability_event = None
        output = []
        chat = Chat(self.cfg, self.store, self.budget, model, tools=tools, output=output.append)
        self.assertIn('输出上限', chat.send('create journal.md'))
        tools.invoke.assert_not_called()
        model.assert_called_once()
        self.assertTrue(any('输出上限' in line for line in output))

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
                    self.assertTrue(any('未同意写入' in line for line in output))
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
        self.assertEqual(chat.send('read it'), '这次工具步骤用完了。')
        self.assertEqual(model.call_count, 2)
        self.assertEqual(tools.invoke.call_count, 1)
        self.assertIn('这次工具步骤用完了。', output)

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
        self.assertIn('这次工具步骤用完了。', output)
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
        self.assertEqual(chat.send('write it'), '这次工具步骤用完了。')
        tools.invoke.assert_not_called()
        self.assertEqual(model.call_count, 1)
        self.assertTrue(any('费用上限' in line for line in output))
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
        self.assertIn('未执行工具', chat.send('write it'))
        tools.invoke.assert_not_called()

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
        self.assertIn('超过本轮请求长度上限', forwarded)
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
            self.assertEqual(manager.invoke('create_directory', {'path': 'lab/research'}, no_prompt)[1], '成功')
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


if __name__ == '__main__':
    unittest.main()
