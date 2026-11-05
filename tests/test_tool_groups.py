import json
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

from tool_groups import CHAT_WARM_REQUESTS, group_of
from agent_types import UserError
from veil_agent import Budget, Chat, Store, load_character


def tool(name):
    return SimpleNamespace(name=name, description=f'{name} tool.', inputSchema={'type': 'object', 'properties': {}})


class ToolGroupTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        (root / 'characters' / 'test').mkdir(parents=True)
        (root / 'persona').mkdir()
        (root / 'persona' / 'test.md').write_text('Fictional test persona', encoding='utf-8')
        (root / 'characters' / 'test' / 'character.toml').write_text('''name = "Test"
provider = "fake"
persona = "persona/test.md"
max_output_tokens = 50
[providers.fake]
model = "fake-model"
base_url = "https://example.invalid/v1"
api_key_env = "FAKE_KEY"
max_input_tokens = 6000
input_cny_per_million = "2"
cached_input_cny_per_million = "1"
output_cny_per_million = "8"
[tools]
enabled = true
max_steps = 4
tool_groups = ["web", "github"]
''', encoding='utf-8')
        self.cfg = load_character(root, 'test')
        store = Store(root / 'data')
        self.tools = SimpleNamespace(
            tools=[tool('read_text_file'), tool('web_search'), tool('github_read')],
            capability_event=None, capability_event_model=None, description='Tools. ',
            preview_summary=Mock(return_value='x'), invoke=Mock(return_value=('ok', '成功', 'x')))
        self.chat = Chat(self.cfg, store, Budget(store), Mock(), tools=self.tools, output=lambda *_: None)

    def names(self):
        return {schema['function']['name'] for schema in self.chat.tool_schemas()}

    def test_group_membership(self):
        self.assertEqual(group_of('web_search'), 'web')
        self.assertEqual(group_of('browser'), 'web')
        self.assertEqual(group_of('github_read'), 'github')
        self.assertEqual(group_of('xiaohongshu'), 'social')
        self.assertEqual(group_of('mcp__github__github_read'), 'github')
        self.assertEqual(group_of('mcp__xiaohongshu__xiaohongshu'), 'social')
        self.assertEqual(group_of('desktop_icons_arrange'), 'desktop_control')
        self.assertIsNone(group_of('windows_discover_apps'))
        self.assertIsNone(group_of('desktop_icons_read'))
        self.assertEqual(group_of('mcp__galatea_garden__get_thread'), 'social')
        self.assertEqual(group_of('mcp__nowhere__walk'), 'social')
        self.assertEqual(group_of('windows_launch'), 'desktop_control')
        self.assertIsNone(group_of('windows_observe'))
        self.assertIsNone(group_of('read_text_file'))
        self.assertIsNone(group_of('send_message'))

    def test_closed_groups_are_not_offered_and_expand_tool_names_them(self):
        names = self.names()
        self.assertIn('read_text_file', names)
        self.assertIn('expand_capability', names)
        self.assertNotIn('web_search', names)
        self.assertNotIn('github_read', names)
        expand = [s for s in self.chat.tool_schemas() if s['function']['name'] == 'expand_capability'][0]
        self.assertEqual(expand['function']['parameters']['properties']['groups']['items']['enum'],
                         ['web', 'github'])
        self.assertIn('web (', expand['function']['description'])

    def test_opening_a_group_offers_its_tools_and_keeps_permissions(self):
        result, state, _ = self.chat.expand_capability(json.dumps({'groups': ['web'], 'reason': 'look up'}))
        self.assertEqual(state, '成功')
        self.assertIn('web_search', result)
        names = self.names()
        self.assertIn('web_search', names)
        self.assertNotIn('github_read', names)
        enum = [s for s in self.chat.tool_schemas() if s['function']['name'] == 'expand_capability'][0][
            'function']['parameters']['properties']['groups']['items']['enum']
        self.assertEqual(enum, ['github'])
        self.chat.expand_capability(json.dumps({'groups': ['github']}))
        self.assertNotIn('expand_capability', self.names())

    def test_model_call_to_expand_runs_through_the_normal_dispatch(self):
        call = SimpleNamespace(id='c1', function=SimpleNamespace(
            name='expand_capability', arguments=json.dumps({'groups': ['web']})))
        execution = self.chat._execute_tool_call(call, 'req', 'step:0', False, self.chat.tool_schemas())
        self.assertEqual(execution.state, '成功')
        self.assertIn('web_search', self.names())
        hidden = SimpleNamespace(id='c2', function=SimpleNamespace(name='github_read', arguments='{}'))
        refused = self.chat._execute_tool_call(hidden, 'req', 'step:1', False, self.chat.tool_schemas())
        self.assertEqual(refused.state, '被拒绝')
        self.tools.invoke.assert_not_called()

    def test_bad_expand_requests_are_rejected(self):
        for arguments in ('{}', '{"groups": []}', '{"groups": ["lab"]}', '{"groups": [1]}',
                          '{"groups": ["web"], "extra": 1}', '[]'):
            result, state, _ = self.chat.expand_capability(arguments)
            self.assertEqual(state, '被拒绝', arguments)
        self.assertNotIn('web_search', self.names())

    def test_chat_groups_age_out_and_a_wake_starts_closed(self):
        self.chat.expand_capability(json.dumps({'groups': ['web']}))
        for _ in range(CHAT_WARM_REQUESTS):
            self.chat._tick_expanded_groups(False)
            self.assertIn('web_search', self.names())
        self.chat._tick_expanded_groups(False)
        self.assertNotIn('web_search', self.names())
        self.chat.expand_capability(json.dumps({'groups': ['web']}))
        self.chat._tick_expanded_groups(True)
        self.assertNotIn('web_search', self.names())

    def test_a_group_opened_in_a_wake_closes_when_the_next_request_starts(self):
        self.chat.autonomy_mode = True
        self.chat.expand_capability(json.dumps({'groups': ['web']}))
        self.assertIn('web_search', self.names())
        self.assertIn('web_search', self.names())  # still open on later steps of the same wake
        self.chat._tick_expanded_groups(False)
        self.assertNotIn('web_search', self.names())

    def test_role_without_groups_offers_everything(self):
        store = Store(Path(self.tmp.name) / 'data2')
        cfg = replace(self.cfg, tools={**self.cfg.tools, 'tool_groups': ()})
        chat = Chat(cfg, store, Budget(store), Mock(), tools=self.tools, output=lambda *_: None)
        names = {schema['function']['name'] for schema in chat.tool_schemas()}
        self.assertIn('web_search', names)
        self.assertNotIn('expand_capability', names)

    def test_unknown_group_in_config_is_refused(self):
        root = Path(self.tmp.name)
        path = root / 'characters' / 'test' / 'character.toml'
        path.write_text(path.read_text(encoding='utf-8').replace('["web", "github"]', '["lab"]'), encoding='utf-8')
        with self.assertRaises(UserError):
            load_character(root, 'test')


if __name__ == '__main__':
    unittest.main()
