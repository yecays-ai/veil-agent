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


SAMPLES = {
    # (persona, old memory, thinking, discussion, limits, tokens per UTF-8 byte a real provider would report)
    'zh': ('他是 lan 的男朋友和 AI 伴侣，说话温柔。' * 90, '一起经历过的事和原因。' * 60,
           '他想了想这件事要不要记下来。' * 70, '我们聊了记忆能不能编辑、合并。' * 90, '每条记忆最多 1500 字。' * 90, 0.4),
    'en': ('He is lan\'s boyfriend and AI companion and speaks warmly. ' * 80,
           'Things we went through together and the reasons behind them. ' * 40,
           'He thought about whether this is worth remembering for the long term. ' * 70,
           'We talked about whether memories can be edited or merged. ' * 25,
           'Each memory is limited to fifteen hundred characters in length. ' * 22, 0.3),
}


class SearchThenSaveTests(unittest.TestCase):
    """Real-chat shape: persona and memories, two searches, then two saves in one turn."""
    language = 'zh'

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = Store(Path(self.temp.name) / 'data')
        provider = {'model': 'fake', 'max_input_tokens': 32768, 'input_cny_per_million': Decimal(2),
                    'output_cny_per_million': Decimal(8), 'cached_input_cny_per_million': Decimal(1)}
        persona, self.old_memory, self.thinking, self.talk, self.limit_text, self.rate = SAMPLES[self.language]
        self.cfg = Character('fake', 'Example', persona, 20, 1024, provider, 'fake',
            {'model': 'fake-embedding', 'input_cny_per_million': '0.07', 'mode': 'native'},
            {'enabled': False, 'folder': 'data/fake/workspace', 'max_steps': 6, 'max_result_bytes': 8192})
        self.budget = Budget(self.store)
        self.backend = MergingBackend()
        for i in range(3):
            self.backend.rows[f'{i:012x}'] = {'content': f'memory {i}: ' + self.old_memory}
        self.memory = Memory(self.cfg, self.store, self.budget, Mock(), self.backend)
        self.addCleanup(self.memory.__exit__, None, None, None)
        files = SimpleNamespace(tools=[], capability_event=None, description='You have no file tools. ',
                                consume_capability_event=lambda: None)
        self.tools = ToolSet(files, NativeMemoryTools(self.memory, 8192))

    def message(self, calls=None, content=None, reasoning=None):
        reasoning = self.thinking if reasoning is None else reasoning
        payload = {'role': 'assistant', 'content': content, 'reasoning_content': reasoning,
                   'tool_calls': [{'id': c.id, 'type': 'function', 'function': {
                       'name': c.function.name, 'arguments': c.function.arguments}} for c in calls or []]}
        return SimpleNamespace(content=content, tool_calls=calls or [], reasoning_content=reasoning,
                               model_dump=lambda exclude_none=True: payload)

    def call(self, name, args, call_id):
        return SimpleNamespace(id=call_id, function=SimpleNamespace(
            name=name, arguments=json.dumps(args, ensure_ascii=False)))

    def test_two_searches_then_two_saves_complete_within_the_real_token_limit(self):
        steps = [
            self.message([self.call('breath_search', {'query': '蓝色 喜欢的原因'}, 's1')]),
            self.message([self.call('breath_search', {'query': '重复 记忆'}, 's2')]),
            self.message([self.call('hold', {'title': '关于记忆的讨论', 'content': self.talk}, 'h1'),
                          self.call('hold', {'title': '记忆长度', 'content': self.limit_text}, 'h2')]),
            self.message(content='好，我都记下了。'),
        ]
        requests = []

        def model(messages, **_kwargs):
            requests.append(json.loads(json.dumps(messages)))
            size = len(json.dumps(messages, ensure_ascii=False).encode('utf-8'))
            # A real provider counts well under one token per UTF-8 byte, in any language.
            return steps[len(requests) - 1], int(size * self.rate), 300, 0
        output = []
        chat = Chat(self.cfg, self.store, self.budget, model, self.memory, self.tools, output=output.append)
        self.assertEqual(chat.send('你能删除、编辑、合并重复的记忆吗？每条有长度限制吗？'), '好，我都记下了。')
        self.assertEqual(len(requests), 4)
        self.assertEqual(len([n for n, _ in self.backend.calls if n == 'hold']), 2)
        self.assertFalse(any('被拒绝' in line for line in output))
        self.assertGreater(len(json.dumps(requests[2], ensure_ascii=False).encode('utf-8')), 32768)

    def test_a_request_that_really_is_too_large_still_stops_before_saving(self):
        huge = self.message([self.call('hold', {'title': 't', 'content': 'c'}, 'h1')], reasoning='长' * 20000)
        output = []
        # The provider reports a prompt as large as the bytes, so no calibration discount applies.
        chat = Chat(self.cfg, self.store, self.budget, lambda messages, **_: (huge, 30000, 300, 0),
                    self.memory, self.tools, output=output.append)
        self.assertIn('未执行工具', chat.send('hi'))
        self.assertNotIn('hold', [n for n, _ in self.backend.calls])
        self.assertTrue(any('长度检查' in line for line in output))


class EnglishSearchThenSaveTests(SearchThenSaveTests):
    language = 'en'
