import json
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import Mock

from memory_agent import Memory
from memory_native import HOLD, NativeMemoryTools, ToolSet
from test_memory import Backend
from tools_agent import permitted_tools
from veil_agent import Budget, Character, Chat, Store, build_self_description, memory_mode


class MergingBackend(Backend):
    """Like Ombre: a hold with the title of an existing memory merges into it."""
    def call(self, name, args):
        if name == 'hold' and not args.get('feel'):
            for key, row in self.rows.items():
                if row.get('title') == args.get('title'):
                    self.calls.append((name, args))
                    row.update(args)
                    return f'合并→{key}'
        return super().call(name, args)


class NativeMemoryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = Store(Path(self.temp.name) / 'data')
        provider = {'model': 'fake', 'max_input_tokens': 20000, 'input_cny_per_million': Decimal(2),
                    'output_cny_per_million': Decimal(8), 'cached_input_cny_per_million': Decimal(1)}
        self.cfg = Character('fake', 'Example', 'fictional persona', 20, 1024, provider, 'fake',
            {'model': 'fake-embedding', 'input_cny_per_million': '0.07', 'mode': 'native'},
            {'enabled': False, 'folder': 'data/fake/workspace', 'max_steps': 4, 'max_result_bytes': 200})
        self.budget = Budget(self.store)
        self.backend = MergingBackend()
        self.model = Mock()
        self.memory = Memory(self.cfg, self.store, self.budget, self.model, self.backend)
        self.addCleanup(self.memory.__exit__, None, None, None)
        files = SimpleNamespace(tools=[], capability_event=None, description='You have no file tools. ',
                                consume_capability_event=lambda: None,
                                invoke=lambda n, a, ap: ('程序拒绝：此工具没有授权。', '被拒绝', n),
                                preview_summary=lambda n, a: n)
        self.tools = ToolSet(files, NativeMemoryTools(self.memory, 200))
        self.output = []

    def call(self, name, args, call_id='c'):
        return SimpleNamespace(id=call_id, function=SimpleNamespace(
            name=name, arguments=json.dumps(args, ensure_ascii=False)))

    def message(self, calls=None, content=None):
        payload = {'role': 'assistant', 'content': content,
                   'tool_calls': [{'id': c.id, 'type': 'function', 'function': {
                       'name': c.function.name, 'arguments': c.function.arguments}} for c in calls or []]}
        return SimpleNamespace(content=content, tool_calls=calls or [], reasoning_content=None,
                               model_dump=lambda exclude_none=True: payload)

    def chat(self, *replies):
        self.model.side_effect = [(r, 100, 20, 0) for r in replies]
        return Chat(self.cfg, self.store, self.budget, self.model, self.memory, self.tools,
                    output=self.output.append)

    def hold(self, title, content):
        return self.call('hold', {'title': title, 'content': content})

    def test_peter_saves_on_his_own_and_it_shows_in_memory_and_screen(self):
        chat = self.chat(self.message([self.hold('Blue lamp', 'lan likes the blue lamp because it was a gift.')]),
                         self.message(content='I will remember that.'))
        self.assertEqual(chat.send('I love this blue lamp, my sister gave it to me'), 'I will remember that.')
        self.assertEqual([c[0] for c in self.backend.calls if c[0] == 'hold'], ['hold'])
        args = next(a for n, a in self.backend.calls if n == 'hold')
        self.assertEqual(set(args), {'content', 'title'})
        self.assertIn('Blue lamp', self.memory.command('/memory'))
        self.assertTrue(any('hold' in line and '新建：Blue lamp' in line and '成功' in line
                            for line in self.output))
        rows = self.store.read('fake/tools.jsonl')
        self.assertEqual((rows[0]['tool'], rows[0]['state']), ('hold', '成功'))
        costs = self.store.read('costs.jsonl')
        self.assertTrue(any(c.get('accounting') == 'ombre-model-estimate' for c in costs))
        self.assertTrue(any(c.get('accounting') == 'embedding-estimate' for c in costs))
        self.assertEqual(self.budget.totals()[2], 0)

    def test_native_mode_has_no_background_supplement(self):
        for i in range(8):
            self.store.append('fake/chat.jsonl', {'role': 'user', 'content': f'fact {i}'})
            self.store.append('fake/chat.jsonl', {'role': 'assistant', 'content': f'reply {i}'})
            self.memory.completed()
        self.memory.jobs.join()
        self.model.assert_not_called()

    def test_same_event_updates_the_existing_entry_instead_of_adding_one(self):
        chat = self.chat(self.message([self.hold('Blue lamp', 'A gift lamp.')]), self.message(content='ok'),
                         self.message([self.hold('Blue lamp', 'A gift lamp from her sister, kept by the bed.')]),
                         self.message(content='updated'))
        chat.send('first')
        chat.send('more detail')
        self.assertEqual(len(self.memory.state['entries']), 1)
        self.assertIn('kept by the bed', next(iter(self.memory.state['entries'].values()))['text'])
        self.assertTrue(any('合并：Blue lamp' in line for line in self.output))

    def test_invalid_or_oversized_save_never_reaches_ombre(self):
        for args in ({'title': 'x', 'content': '  '}, {'title': '', 'content': 'a'},
                     {'title': 'x', 'content': 'a' * 1501}, {'content': 'no title'}):
            result, state, _ = self.tools.invoke('hold', args)
            self.assertEqual(state, '被拒绝', args)
        self.assertNotIn('hold', [n for n, _ in self.backend.calls])

    def test_feel_is_not_offered_because_ombre_needs_a_source_bucket_for_it(self):
        self.assertNotIn('feel', HOLD.inputSchema['properties'])
        self.tools.invoke('hold', {'title': 't', 'content': 'c', 'feel': True})
        self.assertNotIn('feel', next(a for n, a in self.backend.calls if n == 'hold'))

    def test_failed_save_is_reported_to_the_model_and_not_tracked(self):
        self.backend.fail = True
        result, state, _ = self.tools.invoke('hold', {'title': 't', 'content': 'c'})
        self.assertEqual(state, '出错')
        self.assertEqual(self.memory.state['entries'], {})

    def test_unconfirmed_save_is_not_claimed(self):
        self.backend.call = lambda name, args: '没有返回保存编号'
        result, state, _ = self.tools.invoke('hold', {'title': 't', 'content': 'c'})
        self.assertEqual(state, '出错')
        self.assertIn('不要说已经记住', result)

    def test_search_hides_forgotten_memories_and_truncates(self):
        self.tools.invoke('hold', {'title': 'a', 'content': 'lamp memory'})
        key = next(iter(self.memory.state['entries']))
        text, state, _ = self.tools.invoke('breath_search', {'query': 'lamp'})
        self.assertIn('lamp memory', text)
        self.memory.command(f'/forget {key}')
        text, _, _ = self.tools.invoke('breath_search', {'query': 'lamp'})
        self.assertEqual(text, '没有找到相关记忆。')
        self.tools.invoke('hold', {'title': 'b', 'content': 'long ' * 200})
        text, _, _ = self.tools.invoke('breath_search', {'query': 'long'})
        self.assertIn('[已截断]', text)

    def test_forget_and_delete_still_work_on_memories_peter_saved(self):
        self.tools.invoke('hold', {'title': 'Blue lamp', 'content': 'A gift lamp.'})
        key = next(iter(self.memory.state['entries']))
        self.assertIn('已隐藏', self.memory.command(f'/forget {key}'))
        self.assertIn('已恢复', self.memory.command(f'/restore {key}'))
        self.assertIn('确认请输入', self.memory.command(f'/delete {key}'))
        self.assertIn('已删除', self.memory.command(f'/delete {key} 确认'))

    def test_only_the_two_memory_tools_are_offered_and_delete_is_not_one_of_them(self):
        self.assertEqual({t['function']['name'] for t in permitted_tools(self.tools.tools)},
                         {'hold', 'breath_search'})
        result, state, _ = self.tools.invoke('trace', {'bucket_id': 'x', 'delete': True})
        self.assertEqual(state, '被拒绝')

    def test_self_description_tells_peter_he_saves_himself(self):
        self.assertEqual(memory_mode(self.memory), 'native')
        text = build_self_description(self.cfg, self.budget, 'native', self.tools)
        self.assertIn('you decide what to save', text)
        self.assertIn('hold', text)
        self.assertNotIn('every four completed rounds', text)
        self.assertIn('every four completed rounds', build_self_description(self.cfg, self.budget, True, None))


if __name__ == '__main__':
    unittest.main()
