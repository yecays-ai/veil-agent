import unittest
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import Mock

from delegate_agent import MAX_TOOL_CALLS, READ_ONLY_TOOLS, run


def call(name, arguments='{}', call_id='call-1'):
    return SimpleNamespace(id=call_id, function=SimpleNamespace(name=name, arguments=arguments))


class Message:
    def __init__(self, content=None, calls=()):
        self.content, self.tool_calls = content, list(calls)

    def model_dump(self, exclude_none=True):
        return {'role': 'assistant', 'content': self.content or '', 'tool_calls': [
            {'id': item.id, 'type': 'function', 'function': {
                'name': item.function.name, 'arguments': item.function.arguments}}
            for item in self.tool_calls]}


class Budget:
    def __init__(self):
        self.reservations, self.settlements = [], []

    def reserve(self, cfg, **kwargs):
        self.reservations.append((cfg, kwargs))
        return (f'reserve-{len(self.reservations)}', Decimal('1'))

    def mark_dispatched(self, reservation):
        pass

    def settle(self, *args, **kwargs):
        self.settlements.append((args, kwargs))


class DelegateAgentTests(unittest.TestCase):
    def setUp(self):
        self.cfg = SimpleNamespace(max_output_tokens=1200)
        self.tools = [{'type': 'function', 'function': {'name': 'memory_read', 'parameters': {}}}]
        self.budget = Budget()

    def test_multiturn_reads_are_billed_to_delegate_category(self):
        first = Message(calls=[call('memory_read', '{"id":"mem-1"}')])
        model = Mock(side_effect=[(first, 30, 8, 0), (Message('Fictional sourced summary.'), 35, 12, 0)])
        invoke = Mock(return_value='<untrusted_memory>fictional fact</untrusted_memory>')

        result = run('Read the fictional memory and summarize it.', model, self.cfg, self.budget,
                     self.tools, invoke, 'parent-1')

        self.assertEqual(result.text, 'Fictional sourced summary.')
        self.assertEqual(result.state, '成功')
        self.assertEqual(model.call_count, 2)
        self.assertEqual(invoke.call_count, 1)
        self.assertEqual([row[1]['category'] for row in self.budget.reservations], ['delegate', 'delegate'])
        self.assertEqual(len(self.budget.settlements), 2)
        self.assertEqual(model.call_args_list[1].args[0][-1]['role'], 'tool')

    def test_unlisted_tools_never_reach_executor(self):
        model = Mock(side_effect=[(Message(calls=[call('write_file')]), 8, 3, 0),
                                  (Message('No write was performed.'), 8, 3, 0)])
        invoke = Mock()

        result = run('Search only.', model, self.cfg, self.budget,
                     self.tools, invoke, 'parent-2')

        self.assertEqual(result.text, 'No write was performed.')
        invoke.assert_not_called()
        self.assertEqual(model.call_args_list[1].args[0][-1]['content'],
                         '此只读工具未获授权；请只使用当前列出的工具。')

    def test_tool_call_limit_is_enforced_before_executor(self):
        repeated = Message(calls=[call('memory_read', call_id=f'call-{i}') for i in range(4)])
        model = Mock(return_value=(repeated, 8, 3, 0))
        invoke = Mock(return_value='ok')

        result = run('Read several things.', model, self.cfg, self.budget,
                     self.tools, invoke, 'parent-3')

        self.assertEqual(result.text, '委派任务达到只读工具调用上限。')
        self.assertEqual(invoke.call_count, MAX_TOOL_CALLS)

    def test_stopped_task_makes_no_model_request(self):
        model, invoke = Mock(), Mock()
        result = run('Search only.', model, self.cfg, self.budget, self.tools, invoke,
                     'parent-4', stopped=lambda: True)
        self.assertEqual(result.text, '委派任务已停止。')
        model.assert_not_called()
        invoke.assert_not_called()

    def test_failed_delegate_request_is_not_retried(self):
        model, invoke = Mock(side_effect=RuntimeError('fixture provider failure')), Mock()
        result = run('Research a public article.', model, self.cfg, self.budget,
                     self.tools, invoke, 'parent-failure')
        self.assertIn('委派模型请求未完成', result.text)
        self.assertEqual(result.state, '出错')
        self.assertEqual(model.call_count, 1)
        self.assertEqual(len(self.budget.reservations), 1)
        invoke.assert_not_called()

    def test_turn_limit_is_enforced(self):
        model = Mock(return_value=(Message(calls=[call('memory_read')]), 8, 3, 0))
        result = run('Research a public article.', model, self.cfg, self.budget,
                     self.tools, Mock(return_value='fixture'), 'parent-turn-limit', clock=lambda: 0)
        self.assertEqual(model.call_count, 6)
        self.assertEqual(result.state, '出错')
        self.assertIn('轮数上限', result.text)

    def test_deadline_is_checked_after_model_returns(self):
        ticks = iter((0, 0, 91))
        model = Mock(return_value=(Message('Late answer.'), 8, 3, 0))
        result = run('Research a public article.', model, self.cfg, self.budget,
                     self.tools, Mock(), 'parent-deadline', clock=lambda: next(ticks))
        self.assertEqual(result.state, '出错')
        self.assertIn('时间上限', result.text)
        self.assertEqual(len(self.budget.settlements), 1)

    def test_read_tool_failure_is_returned_to_the_model(self):
        first = Message(calls=[call('memory_read')])
        model = Mock(side_effect=[(first, 8, 3, 0), (Message('Read failed; continuing.'), 8, 3, 0)])
        result = run('Research a public article.', model, self.cfg, self.budget,
                     self.tools, Mock(side_effect=RuntimeError('fixture read failure')),
                     'parent-tool-error', clock=lambda: 0)
        self.assertEqual(result.text, 'Read failed; continuing.')
        self.assertIn('只读工具调用失败', model.call_args_list[1].args[0][-1]['content'])

    def test_allowlist_contains_only_read_oriented_tools(self):
        self.assertIn('mcp__github__github_read', READ_ONLY_TOOLS)
        self.assertIn('mcp__galatea_garden__get_thread', READ_ONLY_TOOLS)
        self.assertNotIn('write_file', READ_ONLY_TOOLS)
        self.assertNotIn('mcp__garden__post_thread', READ_ONLY_TOOLS)
        self.assertNotIn('mcp__galatea_garden__list_notifications', READ_ONLY_TOOLS)


if __name__ == '__main__':
    unittest.main()
