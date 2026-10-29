import unittest
from types import SimpleNamespace
from unittest.mock import Mock

from tool_aliases import alias_system_messages, alias_tool_schemas, resolve_tool_name, validate_tool_aliases
from tools_agent import ALLOW, tool_policy
from veil_agent import Budget, Chat, Character, Store, model_tool_result, raw_tool_call_text_format


class ToolAliasTests(unittest.TestCase):
    def test_alias_validation_rejects_duplicates_and_name_collisions(self):
        with self.assertRaises(ValueError):
            validate_tool_aliases({'read_text_file': 'read_notes', 'write_file': 'read_notes'})
        with self.assertRaises(ValueError):
            validate_tool_aliases({'read_text_file': 'write_file', 'write_file': 'read_notes'})
        with self.assertRaises(ValueError):
            validate_tool_aliases({'read text': 'read_notes'})

    def test_retired_media_tool_is_not_offered_or_authorized(self):
        self.assertNotIn('windows_now_playing', ALLOW)
        self.assertEqual(tool_policy('windows_now_playing'), 'deny')

    def test_schema_aliasing_changes_name_and_cross_references_without_mutating_source(self):
        schemas = [{'type': 'function', 'function': {
            'name': 'windows_foreground',
            'description': 'Read windows_foreground, then pass its ID to windows_observe.',
            'parameters': {'type': 'object', 'properties': {
                'id': {'type': 'string', 'description': 'From windows_foreground.'}}}}},
            {'type': 'function', 'function': {'name': 'windows_observe', 'description': 'Read.',
                                              'parameters': {'type': 'object'}}}]
        result, active = alias_tool_schemas(schemas, {'windows_foreground': 'peek_screen'})
        self.assertEqual(result[0]['function']['name'], 'peek_screen')
        self.assertIn('peek_screen', result[0]['function']['description'])
        self.assertIn('windows_observe', result[0]['function']['description'])
        self.assertIn('peek_screen', result[0]['function']['parameters']['properties']['id']['description'])
        self.assertEqual(schemas[0]['function']['name'], 'windows_foreground')
        self.assertEqual(active, {'windows_foreground': 'peek_screen'})

    def test_mcp_short_tool_name_aliases_actual_service_name(self):
        schemas, active = alias_tool_schemas([{'type': 'function', 'function': {
            'name': 'mcp__veil_github__github_read', 'description': 'Read github_read.',
            'parameters': {'type': 'object'}}}], {'github_read': 'snoop_github'})
        self.assertEqual(schemas[0]['function']['name'], 'snoop_github')
        self.assertEqual(schemas[0]['function']['description'], 'Read snoop_github.')
        available = {'mcp__veil_github__github_read'}
        self.assertEqual(resolve_tool_name('snoop_github', {'github_read': 'snoop_github'},
                                           active, available), 'mcp__veil_github__github_read')
        self.assertIsNone(resolve_tool_name('mcp__veil_github__github_read',
                                            {'github_read': 'snoop_github'}, active, available))

    def test_mcp_alias_is_executed_through_the_existing_mcp_permission_entry(self):
        import tempfile
        from pathlib import Path
        with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parents[1]) as directory:
            store = Store(Path(directory) / 'data')
            cfg = Character('fixture', 'Fixture', 'fictional persona', 2, 100,
                            {'model': 'fake', 'max_input_tokens': 8000}, 'fake',
                            tools={'enabled': True, 'max_steps': 4},
                            tool_aliases={'github_read': 'snoop_github'})
            manager = SimpleNamespace(tools=[], description='Fictional tools.', capability_event=None,
                                      enabled=True)
            chat = Chat(cfg, store, Budget(store), Mock(), tools=manager, output=lambda *_: None)
            mcp_schema = {'type': 'function', 'function': {'name': 'mcp__veil_github__github_read',
                'description': 'Public read-only GitHub access.', 'parameters': {'type': 'object'}}}
            invoke = Mock(return_value=('fictional repository data', '成功', 'github_read'))
            chat.mcp_services = SimpleNamespace(
                schemas=lambda: [mcp_schema], permission_for=lambda _name: 'mcp_read',
                needs_checkpoint=lambda _name: False, invoke=invoke)
            schemas = chat.tool_schemas()
            self.assertIn('snoop_github', {row['function']['name'] for row in schemas})
            call = SimpleNamespace(id='mcp-call', function=SimpleNamespace(
                name='snoop_github', arguments='{}'))
            execution = chat._execute_tool_call(call, 'request', 'mcp-call', True, schemas)
            self.assertEqual(execution.state, '成功')
            self.assertEqual(invoke.call_args.args[0], 'mcp__veil_github__github_read')
            self.assertEqual(invoke.call_args.args[1], {})

    def test_program_tool_references_change_but_persona_and_chat_history_do_not(self):
        messages = [
            {'role': 'system', 'content': 'Persona mentions send_message.'},
            {'role': 'system', 'content': 'Use send_message when you want to reply.'},
            {'role': 'assistant', 'content': 'Lan said send_message yesterday.'},
        ]
        result = alias_system_messages(messages, {'send_message': 'bubble_up'})
        self.assertEqual(result[0]['content'], messages[0]['content'])
        self.assertEqual(result[1]['content'], 'Use bubble_up when you want to reply.')
        self.assertIs(result[2], messages[2])

    def test_chat_executes_alias_via_original_tool_and_refuses_hidden_old_name(self):
        with self.subTest('dispatch'):
            import tempfile
            from pathlib import Path
            with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parents[1]) as directory:
                root = Path(directory)
                store = Store(root / 'data')
                cfg = Character('fixture', 'Fixture', 'fictional persona', 2, 100,
                                {'model': 'fake', 'max_input_tokens': 8000}, 'fake',
                                tools={'enabled': True, 'max_steps': 4},
                                tool_aliases={'windows_foreground': 'peek_screen'})
                manager = SimpleNamespace(
                    tools=[SimpleNamespace(name='windows_foreground', description='Read the foreground window.',
                                            inputSchema={'type': 'object', 'properties': {}, 'required': []})],
                    description='Fictional tools.', capability_event=None, capability_event_model=None,
                    invoke=Mock(return_value=('Fictional window.', '成功', 'windows_foreground')))
                chat = Chat(cfg, store, Budget(store), Mock(), tools=manager, output=lambda *_: None)
                schemas = chat.tool_schemas()
                names = {row['function']['name'] for row in schemas}
                self.assertIn('peek_screen', names)
                self.assertNotIn('windows_foreground', names)

                call = SimpleNamespace(id='fixture-call', function=SimpleNamespace(
                    name='peek_screen', arguments='{}'))
                result = chat._execute_tool_call(call, 'fixture-request', 'fixture-call', False, schemas)
                self.assertEqual(result.state, '成功')
                self.assertEqual(manager.invoke.call_count, 1)
                self.assertEqual(manager.invoke.call_args.args[:3],
                                 ('windows_foreground', {}, chat.approval))
                self.assertIn('should_stop', manager.invoke.call_args.kwargs)

                old_call = SimpleNamespace(id='old-call', function=SimpleNamespace(
                    name='windows_foreground', arguments='{}'))
                rejected = chat._execute_tool_call(old_call, 'fixture-request', 'old-call', False, schemas)
                self.assertEqual(rejected.state, '被拒绝')
                self.assertEqual(manager.invoke.call_count, 1)

    def test_raw_message_call_guard_accepts_selected_alias(self):
        self.assertEqual(raw_tool_call_text_format('bubble_up(text="hello")',
                                                   {'send_message': 'bubble_up'}),
                         'send_message_arguments')
        self.assertIsNone(raw_tool_call_text_format('Peter said bubble_up casually.',
                                                    {'send_message': 'bubble_up'}))

    def test_alias_results_keep_existing_data_and_untrusted_content_handling(self):
        self.assertEqual(model_tool_result('read_text_file', '真实文件内容', '成功'), '真实文件内容')
        self.assertIn('<untrusted_mcp>', model_tool_result(
            'mcp__github__github_read', 'external content', '成功'))


if __name__ == '__main__':
    unittest.main()
