"""Ticket 357: every tool is registered in one table, rarely used ones are opened on demand, and the text carried
on every request has a ceiling. Fictional tools and a fake model only."""
import importlib
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

from veil.tools import tool_groups
from veil.tools.tool_groups import (ALWAYS_ON, BUDGET, GROUP_TOOL_NAMES, OVERSIZE_EXEMPT, SOCIAL_PARTS, effective_group,
                         group_of, groups_named, inventory, regroup_text)
from veil.core.veil_agent import Budget, Chat, Store, load_character

PETER_GROUPS = ('web', 'github', 'desktop_control', 'memory_admin', 'build', 'desktop_files', 'garden_forum',
                'garden_play', 'garden_more', 'nowhere', 'xiaohongshu', 'other')
# Modules whose tool definitions are plain values in the repository (MCP services define theirs at run time).
SCHEMA_MODULES = ('veil.core.veil_agent', 'veil.brain.memory_native', 'veil.brain.experience_feedback', 'veil.autonomy.long_tasks', 'veil.tools.tools_agent', 'veil.integrations.browser_tools', 'veil.life.life_records',
                  'veil.tools.desktop_files', 'veil.integrations.xiaohongshu_tools', 'veil.windows.windows_interaction', 'veil.windows.windows_observer', 'veil.autonomy.thoughts',
                  'veil.chat.chat_search', 'veil.integrations.mcp_services', 'veil.persona.profile_notes', 'veil.life.virtual_cards', 'veil.system.maintenance',
                  'veil.tools.executor_opencode', 'veil.integrations.web_tools', 'veil.integrations.github_readonly', 'veil.windows.screen_pop', 'veil.life.lab_games', 'veil.life.lab_preview',
                  'veil.life.lab_sandbox')


def tool(name, text=''):
    return SimpleNamespace(name=name, description=text or f'{name} tool.',
                           inputSchema={'type': 'object', 'properties': {}})


def schema(name, text='x'):
    return {'type': 'function', 'function': {'name': name, 'description': text,
                                             'parameters': {'type': 'object', 'properties': {}}}}


def repository_schemas():
    found = {}

    def consider(value):
        if isinstance(value, dict) and value.get('type') == 'function' and isinstance(value.get('function'), dict):
            found[value['function']['name']] = value
        elif hasattr(value, 'inputSchema') and isinstance(getattr(value, 'name', None), str):
            found[value.name] = {'type': 'function', 'function': {
                'name': value.name, 'description': getattr(value, 'description', ''),
                'parameters': value.inputSchema}}
        elif isinstance(value, (list, tuple)):
            for item in value:
                consider(item)

    for module in SCHEMA_MODULES:
        for value in list(vars(importlib.import_module(module)).values()):
            consider(value)
    return found


class RegistryTests(unittest.TestCase):
    def test_every_tool_the_program_defines_is_registered(self):
        from veil.tools import tools_agent
        known = (tools_agent.ALLOW | tools_agent.ASK | tools_agent.OWN_WORK | tools_agent.LAB_ONLY
                 | tools_agent.NATIVE | tools_agent.AUTONOMY_DIRECT | set(repository_schemas()))
        unregistered = sorted(name for name in known if group_of(name) == 'other')
        self.assertEqual(unregistered, [], 'add these tools to ALWAYS_ON or a group in tool_groups.py')

    def test_a_tool_sits_in_one_place_only(self):
        grouped = [name for names in GROUP_TOOL_NAMES.values() for name in names]
        self.assertEqual(len(grouped), len(set(grouped)))
        self.assertEqual(ALWAYS_ON & set(grouped), set())

    def test_new_tools_and_new_services_are_not_carried_on_every_request(self):
        self.assertEqual(effective_group('brand_new_tool', PETER_GROUPS), 'other')
        self.assertEqual(effective_group('mcp__weather__today', PETER_GROUPS), 'mcp_weather')
        self.assertEqual(effective_group('mcp__galatea_garden__nostos_status', PETER_GROUPS), 'garden_play')
        self.assertIsNone(effective_group('hold', PETER_GROUPS))
        self.assertIsNone(effective_group('long_task_update', PETER_GROUPS))

    def test_the_earlier_grouping_is_one_config_line_away(self):
        before = ('web', 'github', 'social', 'desktop_control')
        for name in ('trace', 'workbench', 'desktop_list', 'brand_new_tool', 'mcp__weather__today'):
            self.assertIsNone(effective_group(name, before), name)
        for name in ('xiaohongshu', 'mcp__nowhere__walk', 'mcp__galatea_garden__get_thread'):
            self.assertEqual(effective_group(name, before), 'social')
        self.assertIsNone(regroup_text(before))
        self.assertIsNone(effective_group('trace', ()))

    def test_asking_for_social_still_opens_its_parts(self):
        self.assertEqual(groups_named(['social'], PETER_GROUPS), list(SOCIAL_PARTS))
        self.assertEqual(groups_named(['social'], ('web', 'social')), ['social'])
        self.assertEqual(groups_named(['mcp_weather', 'lab'], PETER_GROUPS), ['mcp_weather'])
        self.assertEqual(groups_named(['mcp_weather'], ('web',)), [])

    def test_regroup_notice_names_what_moved(self):
        text = regroup_text(PETER_GROUPS)
        for word in ('memory_admin', 'build', 'desktop_files', 'garden_forum', 'nowhere', 'Nothing was removed'):
            self.assertIn(word, text)


class BudgetTests(unittest.TestCase):
    def test_repository_tools_stay_under_the_per_tool_limit(self):
        sizes = {name: len(json.dumps(value, ensure_ascii=False).encode('utf-8'))
                 for name, value in repository_schemas().items()}
        too_big = {name: size for name, size in sizes.items()
                   if size > BUDGET['tool'] and name not in OVERSIZE_EXEMPT}
        self.assertEqual(too_big, {}, 'shorten the description or split the tool; the exempt list takes no new names')

    def test_repository_tools_carried_on_every_request_fit_the_budget(self):
        rows = inventory(list(repository_schemas().values()), PETER_GROUPS)
        self.assertLessEqual(rows['always_on_bytes'], BUDGET['always_on'])
        self.assertEqual([item for item in rows['over'] if item['kind'] == 'always_on'], [])

    def test_inventory_reports_sizes_groups_and_overruns(self):
        big = 'x' * (BUDGET['group'] + 1)
        report = inventory([schema('hold'), schema('trace'), schema('mcp__weather__today', big)], PETER_GROUPS,
                           {'hold': 'keep_this'})
        by_tool = {row['tool']: row for row in report['rows']}
        self.assertEqual(by_tool['hold']['name'], 'keep_this')
        self.assertIsNone(by_tool['hold']['group'])
        self.assertEqual(by_tool['trace']['group'], 'memory_admin')
        self.assertEqual(report['always_on_bytes'], by_tool['hold']['bytes'])
        self.assertEqual([(item['kind'], item['group']) for item in report['over']], [('group', 'mcp_weather')])


class ChatTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        (root / 'characters' / 'test').mkdir(parents=True)
        (root / 'persona').mkdir()
        (root / 'persona' / 'test.md').write_text('Fictional test persona', encoding='utf-8')
        groups = ', '.join(f'"{name}"' for name in PETER_GROUPS)
        (root / 'characters' / 'test' / 'character.toml').write_text(f'''name = "Test"
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
tool_groups = [{groups}]
''', encoding='utf-8')
        self.cfg = load_character(root, 'test')
        store = Store(root / 'data')
        self.tools = SimpleNamespace(
            tools=[tool('read_text_file'), tool('hold'), tool('trace'), tool('desktop_list')],
            capability_event=None, capability_event_model=None, description='Tools. ',
            preview_summary=Mock(return_value='x'), invoke=Mock(return_value=('ok', '成功', 'x')))
        self.chat = Chat(self.cfg, store, Budget(store), Mock(), tools=self.tools, output=lambda *_: None)
        self.chat.mcp_services = Mock()
        self.chat.mcp_services.schemas.return_value = [
            schema('mcp__galatea_garden__get_thread'), schema('mcp__galatea_garden__nostos_status'),
            schema('mcp__nowhere__walk'), schema('mcp__weather__today')]
        self.chat.mcp_services.has_stateful_services.return_value = False

    def names(self):
        return {item['function']['name'] for item in self.chat.tool_schemas()}

    def test_everyday_tools_stay_and_rare_ones_wait_behind_expand(self):
        names = self.names()
        self.assertLessEqual({'read_text_file', 'hold', 'send_message', 'expand_capability'}, names)
        for hidden in ('trace', 'desktop_list', 'mcp__galatea_garden__get_thread',
                       'mcp__nowhere__walk', 'mcp__weather__today'):
            self.assertNotIn(hidden, names)
        expand = next(item for item in self.chat.tool_schemas() if item['function']['name'] == 'expand_capability')
        enum = expand['function']['parameters']['properties']['groups']['items']['enum']
        self.assertLessEqual({'memory_admin', 'desktop_files', 'garden_forum', 'garden_play', 'nowhere',
                              'mcp_weather'}, set(enum))
        self.assertIn('editing or removing a memory', expand['function']['description'])
        self.assertIn('tools from the weather service', expand['function']['description'])

    def test_opening_the_forum_does_not_bring_nowhere_along(self):
        result, state, _ = self.chat.expand_capability(json.dumps({'groups': ['garden_forum']}))
        self.assertEqual(state, '成功')
        self.assertIn('mcp__galatea_garden__get_thread', result)
        names = self.names()
        self.assertIn('mcp__galatea_garden__get_thread', names)
        self.assertNotIn('mcp__nowhere__walk', names)
        self.assertNotIn('mcp__galatea_garden__nostos_status', names)

    def test_old_habit_of_opening_social_opens_every_part(self):
        _, state, _ = self.chat.expand_capability(json.dumps({'groups': ['social']}))
        self.assertEqual(state, '成功')
        self.assertLessEqual({'mcp__galatea_garden__get_thread', 'mcp__galatea_garden__nostos_status',
                              'mcp__nowhere__walk'}, self.names())

    def test_a_new_service_can_be_opened_by_its_own_group(self):
        _, state, _ = self.chat.expand_capability(json.dumps({'groups': ['mcp_weather']}))
        self.assertEqual(state, '成功')
        self.assertIn('mcp__weather__today', self.names())

    def test_running_maintenance_stays_offered_while_build_is_closed(self):
        from veil.system.maintenance import TOOL_SCHEMA
        self.assertEqual(group_of(TOOL_SCHEMA['function']['name']), 'build')
        self.chat.maintenance = Mock()
        self.chat.maintenance.active_summary.return_value = ''
        self.assertNotIn('maintenance', self.names())
        self.chat.maintenance.active_summary.return_value = 'A maintenance task is in progress.'
        self.assertIn('maintenance', self.names())

    def test_inventory_lists_every_tool_whether_open_or_closed(self):
        report = self.chat.tool_schemas(inventory=True)
        tools = {row['tool']: row['group'] for row in report['rows']}
        self.assertIsNone(tools['hold'])
        self.assertEqual(tools['trace'], 'memory_admin')
        self.assertEqual(tools['mcp__weather__today'], 'mcp_weather')
        self.assertNotIn('expand_capability', tools)
        self.assertGreater(report['always_on_bytes'], 0)


if __name__ == '__main__':
    unittest.main()
