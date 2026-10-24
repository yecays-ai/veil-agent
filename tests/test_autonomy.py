import json
import tempfile
import unittest
from datetime import datetime, timedelta
from dataclasses import replace
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

from autonomy import AutonomyController
from veil_agent import ActivityState, Budget, BudgetExceeded, Chat, RequestFailed, Store, UserError, load_character
from web_agent import Hub


class AutonomyTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        (self.root / 'characters' / 'test').mkdir(parents=True)
        (self.root / 'persona').mkdir()
        (self.root / 'persona' / 'test.md').write_text('Fictional test persona', encoding='utf-8')
        (self.root / 'characters' / 'test' / 'character.toml').write_text('''name = "Test"
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
[autonomy]
interval_minutes = 1
daily_max_runs = 2
daily_budget_cny = "2"
''', encoding='utf-8')
        self.cfg = load_character(self.root, 'test')
        self.store = Store(self.root / 'data')
        self.model = Mock(return_value=(
            '决定：暂不行动\n原因：没有适合继续的工作。\n当前关注：实验室整理\n下一步：等待新信息。',
            100, 30, 20))
        self.budget = Budget(self.store)
        self.chat = Chat(self.cfg, self.store, self.budget, self.model)
        self.activity = ActivityState(self.store.root / 'test' / 'resident-state.json')
        self.hub = Hub('Test', self.activity)
        self.controller = AutonomyController(self.chat, self.hub)

    def autonomous_message_chat(self, calls):
        cfg = replace(self.cfg, tools={'enabled': True, 'max_steps': 8, 'max_output_tokens': 50})
        first = SimpleNamespace(tool_calls=calls, content='独立活动总结：继续整理实验室。',
                                model_dump=lambda **_: {'role': 'assistant', 'tool_calls': []})
        final = SimpleNamespace(tool_calls=[], content=(
            '决定：发消息告诉 lan\n原因：想分享实验室进展。\n当前关注：实验室整理\n下一步：等 lan 回复。'))
        model = Mock(side_effect=[(first, 100, 30, 0), (final, 100, 20, 0)])
        return Chat(cfg, self.store, self.budget, model, output=self.hub.output), model

    @staticmethod
    def message_call(call_id, text):
        return SimpleNamespace(id=call_id, function=SimpleNamespace(
            name='send_message', arguments=json.dumps({'text': text}, ensure_ascii=False)))

    def test_autonomous_message_is_saved_emitted_and_summary_stays_in_autonomy_log(self):
        self.activity.set(True)
        text = '刚刚整理了实验室，想告诉你。'
        chat, model = self.autonomous_message_chat([self.message_call('call-1', text)])
        controller = AutonomyController(chat, self.hub)

        controller.run()

        rows = self.store.read('test/chat.jsonl')
        self.assertEqual([(row['role'], row['content']) for row in rows], [('assistant', text)])
        self.assertEqual(rows[0]['origin'], 'reply')
        self.assertTrue(rows[0]['autonomy_run_id'])
        self.assertEqual(len({row['autonomy_tool_call_id'] for row in rows}), 1)
        self.assertEqual([(event['kind'], event['text']) for event in self.hub.snapshot(0)['events']],
                         [('assistant', text)])
        self.assertEqual([message['content'] for message in chat.history], [text])
        self.assertEqual(len(self.store.read('test/autonomy.jsonl')), 1)
        self.assertEqual(model.call_count, 2)
        self.assertEqual(controller.rows[-1]['current_focus'], '实验室整理')
        self.assertEqual(controller.rows[-1]['next_step'], '等 lan 回复。')
        self.assertEqual(self.store.read('test/tools.jsonl')[-1]['state'], '成功')

    def test_autonomous_wake_stops_at_three_messages_and_rejects_the_fourth(self):
        self.activity.set(True)
        calls = [self.message_call(f'call-{i}', f'消息 {i}') for i in range(1, 5)]
        chat, model = self.autonomous_message_chat(calls)
        controller = AutonomyController(chat, self.hub)

        controller.run()

        rows = self.store.read('test/chat.jsonl')
        self.assertEqual([row['content'] for row in rows], ['消息 1', '消息 2', '消息 3'])
        self.assertEqual(len(self.hub.snapshot(0)['events']), 3)
        tool_rows = [row for row in self.store.read('test/tools.jsonl') if row.get('state') != '进行中']
        self.assertEqual([row['state'] for row in tool_rows], ['成功', '成功', '成功', '被拒绝'])
        self.assertEqual(model.call_count, 2)
        self.assertEqual(controller.rows[-1]['next_step'], '等 lan 回复。')

    def test_autonomy_can_do_tool_work_and_send_a_message_in_the_same_wake(self):
        self.activity.set(True)
        cfg = replace(self.cfg, tools={'enabled': True, 'max_steps': 6, 'max_output_tokens': 50})
        message_call = self.message_call('message-1', '检查完了，文件也已整理好。')
        read_call = SimpleNamespace(id='read-1', function=SimpleNamespace(
            name='read_text_file', arguments=json.dumps({'path': 'lab/draft.md'})))
        first = SimpleNamespace(tool_calls=[message_call, read_call], content=None,
                                model_dump=lambda **_: {'role': 'assistant', 'tool_calls': []})
        final = SimpleNamespace(tool_calls=[], content='决定：完成检查\n原因：草稿内容正常。')
        model = Mock(side_effect=[(first, 100, 30, 0), (final, 100, 20, 0)])
        tools = SimpleNamespace(
            tools=[SimpleNamespace(name='read_text_file', description='Read a file.', inputSchema={})],
            capability_event=None, description='Authorized tools are available. ',
            preview_summary=Mock(return_value='lab/draft.md'),
            invoke=Mock(return_value=('fictional draft', '成功', 'lab/draft.md')))
        chat = Chat(cfg, self.store, self.budget, model, tools=tools, output=self.hub.output)
        controller = AutonomyController(chat, self.hub)

        controller.run()

        self.assertEqual([row['content'] for row in self.store.read('test/chat.jsonl')],
                         ['检查完了，文件也已整理好。'])
        tools.invoke.assert_called_once()
        name, args, approval = tools.invoke.call_args.args
        self.assertEqual((name, args), ('read_text_file', {'path': 'lab/draft.md'}))
        self.assertEqual(approval('test'), 'n')
        self.assertEqual(model.call_count, 2)
        self.assertEqual([row['tool'] for row in self.store.read('test/tools.jsonl') if row.get('state') != '进行中'],
                         ['send_message', 'read_text_file'])
        self.assertEqual(len(self.hub.snapshot(0)['events']), 1)

    def test_autonomous_message_gate_and_recovery_never_duplicate_a_saved_call(self):
        chat, model = self.autonomous_message_chat([])
        run_id = '2026-10-21T12:00:00+08:00'
        chat.autonomy_mode = True
        chat.autonomy_run_id = run_id
        chat.autonomy_message_lock = self.hub.cond
        chat.autonomy_message_output = self.hub.output
        chat.autonomy_should_yield = lambda: False
        text = '恢复时不要重复这条留言。'
        args = json.dumps({'text': text}, ensure_ascii=False)

        first = chat.send_message(args, 'request-1', 'call-1')
        duplicate = chat.send_message(args, 'request-1', 'call-1')

        self.assertEqual(first[1], '成功')
        self.assertEqual(duplicate[1], '已存在')
        self.assertEqual(len(self.store.read('test/chat.jsonl')), 1)
        self.assertEqual(len(self.hub.snapshot(0)['events']), 1)

        chat.autonomy_should_yield = lambda: True
        blocked = chat.send_message(json.dumps({'text': '不应再插入。'}), 'request-2', 'call-2')
        self.assertEqual(blocked[1], '被拒绝')
        self.assertEqual(len(self.store.read('test/chat.jsonl')), 1)

        chat.autonomy_should_yield = None
        controller = AutonomyController(chat, self.hub)
        controller.active_path.parent.mkdir(parents=True, exist_ok=True)
        controller.active_path.write_text(json.dumps({'started_at': run_id}), encoding='utf-8')
        recovered = AutonomyController(chat, self.hub)
        self.assertEqual(len(self.store.read('test/chat.jsonl')), 1)
        self.assertEqual(recovered.rows[-1]['result'], '失败')
        model.assert_not_called()

    def test_autonomous_wake_is_logged_without_changing_chat_history(self):
        self.activity.set(True)
        enabled_at = datetime.fromisoformat(self.activity.snapshot()['enabled_at']) - timedelta(minutes=2)
        self.activity.path.write_text(json.dumps({'enabled': True, 'enabled_at': enabled_at.isoformat()}),
                                      encoding='utf-8')
        self.controller.started_at = enabled_at
        self.assertTrue(self.controller.due())
        self.controller.run()
        rows = self.store.read('test/autonomy.jsonl')
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['result'], '搁置')
        self.assertEqual(rows[0]['decision'], '暂不行动')
        self.assertEqual(rows[0]['reason'], '没有适合继续的工作。')
        self.assertEqual(rows[0]['current_focus'], '实验室整理')
        self.assertEqual(rows[0]['next_step'], '等待新信息。')
        self.assertEqual(self.store.read('test/chat.jsonl'), [])
        self.assertEqual(self.budget.category_totals('test')[0], Decimal('0.000420'))
        self.assertEqual(self.controller.command().split(' | ')[2], '搁置：按 Test 本次决定结束。')

    def test_last_autonomy_record_is_given_once_then_looked_up(self):
        self.controller.run()
        activity_bytes = (self.store.root / 'test' / 'autonomy.jsonl').read_bytes()
        self.model.reset_mock()

        self.chat.send('你下午做了什么？')
        messages = self.model.call_args.args[0]
        context = '\n'.join(message['content'] for message in messages if message['role'] == 'system')
        self.assertIn('most recent completed autonomous activity record', context)
        self.assertIn('实验室整理', context)
        self.assertIn('等待新信息', context)
        self.assertIn('System record | Last autonomous activity | Occurred at:', context)
        self.assertIn('historical execution data', context)
        self.assertIn('do not assume it still exists now', context)
        self.assertIn('use autonomy_history', context)

        self.model.reset_mock()
        self.chat.send('我们接着聊别的。')
        messages = self.model.call_args.args[0]
        context = '\n'.join(message['content'] for message in messages if message['role'] == 'system')
        self.assertNotIn('most recent completed autonomous activity record', context)
        self.assertEqual((self.store.root / 'test' / 'autonomy.jsonl').read_bytes(), activity_bytes)

        restarted_model = Mock(return_value=('Fictional reply after restart', 100, 20, 0))
        restarted_chat = Chat(self.cfg, self.store, self.budget, restarted_model)
        restarted_chat.send('重启后继续聊天。')
        messages = restarted_model.call_args.args[0]
        context = '\n'.join(message['content'] for message in messages if message['role'] == 'system')
        self.assertNotIn('most recent completed autonomous activity record', context)

        from autonomy import activity_history
        looked_up = activity_history(self.store, self.cfg)
        self.assertIn('实验室整理', looked_up)
        self.assertIn('等待新信息', looked_up)

        self.store.append('test/autonomy.jsonl', {
            'started_at': '2099-01-01T00:00:00+00:00', 'decision': '继续整理',
            'reason': '出现了新的虚构活动。', 'tools': [], 'lab_files_changed': [],
            'result': '完成', 'result_reason': '已完成虚构步骤。',
            'current_focus': '新活动', 'next_step': '等待后续。',
        })
        new_activity_model = Mock(return_value=('Fictional reply to new activity', 100, 20, 0))
        new_activity_chat = Chat(self.cfg, self.store, self.budget, new_activity_model)
        new_activity_chat.send('新活动做了什么？')
        messages = new_activity_model.call_args.args[0]
        context = '\n'.join(message['content'] for message in messages if message['role'] == 'system')
        self.assertIn('新活动', context)
        self.assertIn('most recent completed autonomous activity record', context)
        looked_up = activity_history(self.store, self.cfg, 2).splitlines()
        self.assertIn('新活动', looked_up[1])
        self.assertIn('实验室整理', looked_up[2])

    def test_failed_chat_request_keeps_autonomy_handoff_pending(self):
        self.controller.run()
        self.model.side_effect = RequestFailed('synthetic provider failure')

        with self.assertRaises(RequestFailed):
            self.chat.send('第一次尝试。')

        failed_messages = self.model.call_args.args[0]
        failed_context = '\n'.join(message['content'] for message in failed_messages
                                   if message['role'] == 'system')
        self.assertIn('most recent completed autonomous activity record', failed_context)

        self.model.side_effect = None
        self.model.reset_mock()
        self.chat.send('重试。')
        messages = self.model.call_args.args[0]
        context = '\n'.join(message['content'] for message in messages if message['role'] == 'system')
        self.assertIn('most recent completed autonomous activity record', context)

        self.model.reset_mock()
        self.chat.send('后续聊天。')
        messages = self.model.call_args.args[0]
        context = '\n'.join(message['content'] for message in messages if message['role'] == 'system')
        self.assertNotIn('most recent completed autonomous activity record', context)

    def test_autonomy_context_labels_previous_tool_failure_as_historical(self):
        self.store.append('test/autonomy.jsonl', {
            'started_at': '2099-01-01T00:00:00+00:00',
            'finished_at': '2099-01-01T00:01:00+00:00',
            'decision': 'Inspect the fictional draft.', 'reason': 'A fictional check is due.',
            'tools': [{'tool': 'read_text_file', 'path': 'lab/draft.md', 'state': '出错'}],
            'lab_files_changed': [], 'result': '失败', 'result_reason': 'Synthetic read failure.',
            'current_focus': 'Fictional draft', 'next_step': 'Verify the current file state.',
            'cost_cny': '0',
        })
        controller = AutonomyController(self.chat, self.hub)

        context = controller._context()

        self.assertIn('System record | Last autonomous activity | Occurred at: 2099-01-01T00:00:00+00:00', context)
        self.assertIn('failure recorded here happened during that past activity', context)
        self.assertIn('do not assume it still exists now', context)
        self.assertIn('Synthetic read failure.', context)

    def test_long_task_survives_controller_restart_and_is_in_autonomy_status(self):
        _, state, _ = self.chat.long_tasks.invoke('long_task_update', {
            'action': 'create', 'title': 'Three-part study', 'goal': 'Write three parts across wakes.'})
        self.assertEqual(state, '成功')
        task_id = self.chat.long_tasks.read()['task_id']
        _, state, _ = self.chat.long_tasks.invoke('long_task_update', {
            'action': 'update', 'task_id': task_id,
            'progress_update': 'Part one is saved in lab/part-one.md.',
            'next_step': 'Write part two.'})
        self.assertEqual(state, '成功')

        restarted = AutonomyController(self.chat, self.hub)
        context = restarted._context()
        self.assertIn(task_id, context)
        self.assertIn('Part one is saved in lab/part-one.md.', context)
        self.assertIn('Write part two.', context)
        shown = restarted.command()
        self.assertIn('长期任务：Three-part study（active）', shown)
        self.assertIn('下一步：Write part two.', shown)

        self.chat.long_tasks.invoke('long_task_update', {
            'action': 'update', 'task_id': task_id, 'status': 'completed',
            'progress_update': 'All three verified parts are present in lab/part-one.md through part-three.md.'})
        self.assertNotIn(task_id, restarted._context())

    def test_cost_display_counts_autonomy_for_every_character(self):
        peter = replace(self.cfg, id='peter', name='Peter')
        ghost = replace(self.cfg, id='ghost', name='Ghost')
        for cfg in (peter, ghost):
            with self.budget.autonomous(cfg.id):
                request_id, _ = self.budget.reserve(cfg)
                self.budget.settle(request_id, cfg, 100, 20, 0)

        peter_autonomy, _ = self.budget.category_totals('peter')
        ghost_autonomy, _ = self.budget.category_totals('ghost')
        total_autonomy, _ = self.budget.category_totals(None)
        self.assertGreater(total_autonomy, peter_autonomy)
        self.assertGreater(total_autonomy, ghost_autonomy)
        self.assertIn(f'自主 ¥{peter_autonomy:.6f}', self.budget.display(character='peter'))
        self.assertIn(f'自主 ¥{ghost_autonomy:.6f}', self.budget.display(character='ghost'))
        self.assertNotIn(f'自主 ¥{total_autonomy:.6f}', self.budget.display(character='ghost'))

    def test_autonomy_budget_blocks_before_model_request(self):
        with self.budget.autonomous('test', Decimal('0.000001')):
            with self.assertRaises(BudgetExceeded):
                self.budget.reserve(self.cfg)
        self.model.assert_not_called()

    def test_chat_cache_display_excludes_autonomy_requests(self):
        with self.budget.autonomous('test'):
            request_id, _ = self.budget.reserve(self.cfg)
            self.budget.settle(request_id, self.cfg, 100, 20, 50)
        self.assertIn('还没有聊天模型的用量记录', self.budget.cache_display())

    def test_incomplete_autonomy_record_stops_safely(self):
        self.store.append('test/autonomy.jsonl', {'started_at': 'not-a-time', 'result': '搁置'})
        with self.assertRaises(UserError):
            AutonomyController(self.chat, self.hub)

    def test_paused_service_has_no_wake_and_daily_cap_hides_next_wake(self):
        self.assertIsNone(self.controller.next_wake())
        self.activity.set(True)
        self.controller.rows = [{'started_at': self.budget.clock(), 'result': '搁置'},
                                {'started_at': self.budget.clock(), 'result': '搁置'}]
        self.assertIsNone(self.controller.next_wake())

    def test_chat_can_enqueue_once_and_interrupt_autonomy_without_busy_indicator(self):
        self.hub.autonomy_busy = True
        self.assertEqual(self.hub.submit('continue chat', 'one'), 'accepted')
        self.assertEqual(self.hub.submit('continue chat', 'one'), 'duplicate')
        self.assertEqual(self.hub.submit('another message', 'two'), 'busy')
        self.assertTrue(self.hub.autonomy_interrupted.is_set())
        self.assertFalse(self.hub.snapshot(0)['busy'])
        queued = self.hub.inbox.get_nowait()
        self.assertEqual(queued[0], 'continue chat')
        self.assertIsNone(queued[1])
        self.assertTrue(callable(queued[2]))
        self.assertEqual(queued[3], 'one')

    def test_chat_queued_during_due_check_wins_over_autonomy_wake(self):
        self.activity.set(True)
        self.hub.autonomy = self.controller

        def due_with_chat():
            self.hub.inbox.put('continue chat')
            return True
        self.controller.due = due_with_chat
        self.assertEqual(self.hub.input_fn(), 'continue chat')
        self.assertFalse(self.hub.autonomy_busy)

    def test_interrupt_arriving_before_run_is_not_cleared(self):
        self.activity.set(True)
        self.hub.autonomy_interrupted.set()
        self.controller.run()
        row = self.store.read('test/autonomy.jsonl')[-1]
        self.assertEqual(row['result'], '被打断')

    def progress_chat(self, followup):
        from test_tools import ToolLoopTests
        helper = ToolLoopTests()
        cfg = replace(self.cfg, tools={'enabled': True, 'max_steps': 4, 'max_output_tokens': 50})
        call = helper.tool_call('append_file', '{"path":"lab/progress.md","content":"fixture"}', 'a')
        model = Mock(side_effect=[(helper.message([call]), 100, 20, 0), followup])
        tools = Mock()
        tools.tools = [SimpleNamespace(name='append_file', description='Append', inputSchema={})]
        tools.capability_event = None
        tools.invoke.return_value = ('saved', '成功', 'lab/progress.md')
        self.activity.set(True)
        chat = Chat(cfg, self.store, self.budget, model, tools=tools)
        return AutonomyController(chat, self.hub), model, tools

    def test_later_provider_failure_keeps_progress_and_next_wake_evidence(self):
        controller, model, tools = self.progress_chat(RuntimeError('fixture transport failed'))
        controller.run()
        row = controller.rows[-1]
        self.assertEqual(row['result'], '部分完成')
        self.assertEqual(row['lab_files_changed'], ['lab/progress.md'])
        self.assertEqual(row['tools'][0]['state'], '成功')
        self.assertIn('核实', row['next_step'])
        restarted = AutonomyController(controller.chat, self.hub)
        self.assertIn('lab/progress.md', restarted._context())
        self.assertEqual(restarted.status()['last_activity']['result'], '部分完成')
        self.assertEqual(tools.invoke.call_count, 1)

    def test_length_stop_keeps_saved_file_and_is_not_a_personal_decision(self):
        from test_tools import ToolLoopTests
        helper = ToolLoopTests()
        denied = helper.message([helper.tool_call('append_file',
            '{"path":"lab/next.md","content":"fixture"}', 'b')], reasoning='x' * 14000)
        controller, model, tools = self.progress_chat((denied, 120, 20, 0))
        controller.run()
        row = controller.rows[-1]
        self.assertEqual(row['result'], '部分完成')
        self.assertIn('程序限制', row['decision'])
        self.assertIn('size limit', row['result_reason'])
        self.assertEqual(row['lab_files_changed'], ['lab/progress.md'])
        self.assertEqual(tools.invoke.call_count, 1)
        self.assertIn('lab/progress.md', controller.command())

    def test_legacy_false_success_is_corrected_for_display_without_rewriting_file(self):
        self.controller.run()
        row = self.controller.rows[-1]
        row.update(result='完成', decision='工具调用记录超过长度上限，未执行工具。', tools=[
            {'tool': 'append_file', 'path': 'lab/progress.md', 'state': '成功'},
            {'tool': 'pulse', 'path': 'pulse', 'state': '出错'}],
            lab_files_changed=['lab/progress.md', 'lab/progress.md'])
        original = self.store.read('test/autonomy.jsonl')
        shown = self.controller.status()['last_activity']
        self.assertEqual(shown['result'], '部分完成')
        self.assertNotIn('本次决定结束', self.controller.status()['last_result'])
        self.assertEqual(shown['lab_files_changed'], ['lab/progress.md'])
        self.assertEqual(self.store.read('test/autonomy.jsonl'), original)

    def test_english_summary_keeps_focus_and_next_step(self):
        prompt = self.controller._context()
        self.assertIn('Decision: ...\nReason: ...\nFocus: ...\nNext step: ...', prompt)
        self.assertIn('This time is yours. Nobody is waiting on you.', prompt)
        self.assertIn('make something, read, write to Lan, rest, or nothing at all.', prompt)
        self.assertIn('If you want to talk to Lan, use send_message (up to three per wake).', prompt)
        self.assertIn("It's for you, not a report.", prompt)
        self.assertNotIn('Choose a safe, useful task to continue', prompt)
        self.assertNotIn('Do not force activity just to produce output.', prompt)
        self.assertNotRegex(prompt, r'[\u3400-\u9fff]')
        self.model.return_value = ('Decision: wait\nReason: safe\nFocus: fixture\nNext step: resume',
                                   100, 20, 0)
        self.controller.run()
        self.assertEqual(self.controller.rows[-1]['current_focus'], 'fixture')
        self.assertEqual(self.controller.rows[-1]['next_step'], 'resume')

    def test_crash_recovery_restores_only_this_wakes_confirmed_tools_and_cost(self):
        run_id = self.budget.clock()
        with self.budget.autonomous('test'):
            request_id, _ = self.budget.reserve(self.cfg)
            self.budget.settle(request_id, self.cfg, 100, 20, 0)
        self.store.append('test/tools.jsonl', {'request_id': request_id, 'autonomy_run_id': run_id,
            'tool': 'append_file', 'path': 'lab/saved.md', 'state': '成功'})
        self.store.append('test/tools.jsonl', {'request_id': 'other', 'autonomy_run_id': 'old-wake',
            'tool': 'write_file', 'path': 'lab/unrelated.md', 'state': '成功'})
        self.controller.active_path.parent.mkdir(parents=True, exist_ok=True)
        self.controller.active_path.write_text(json.dumps({'started_at': run_id}), encoding='utf-8')
        recovered = AutonomyController(self.chat, self.hub)
        row = recovered.rows[-1]
        self.assertEqual(row['result'], '部分完成')
        self.assertEqual(row['lab_files_changed'], ['lab/saved.md'])
        self.assertEqual(len(row['tools']), 1)
        self.assertEqual(Decimal(row['cost_cny']), self.budget.category_totals('test')[0])
        self.assertIn('lab/saved.md', recovered._context())
        self.model.assert_not_called()

    def test_failed_tools_only_are_not_reported_as_a_completed_activity(self):
        final = SimpleNamespace(tool_calls=[], content='决定：结束\n原因：工具不可用')
        controller, model, tools = self.progress_chat((final, 100, 20, 0))
        tools.invoke.return_value = ('fixture service failed', '出错', 'lab/progress.md')
        controller.run()
        row = controller.rows[-1]
        self.assertEqual(row['result'], '失败')
        self.assertEqual(row['lab_files_changed'], [])
        self.assertIn('append_file（出错）', row['result_reason'])

    def test_full_lab_does_not_cut_previous_progress_or_final_format(self):
        self.controller.run()
        row = self.controller.rows[-1]
        row.update(decision='决' * 300, reason='因' * 500, current_focus='关' * 300,
                   next_step='步' * 300, result='部分完成', result_reason='错' * 400,
                   tools=[{'tool': 'append_file', 'path': 'lab/已保存.md', 'state': '成功'}] * 3,
                   lab_files_changed=['lab/已保存.md'])
        self.controller._lab_context = Mock(return_value='LAB_TEXT' + 'x' * 5990)
        text = self.controller._context()
        self.assertNotIn('LAB_TEXT', text)
        self.assertIn('lab/已保存.md', text)
        self.assertIn('步' * 300, text)
        self.assertIn("When you're done, leave yourself a short note for next time", text)
        self.assertIn('Decision: ...\nReason: ...\nFocus: ...\nNext step: ...', text)
        self.assertLessEqual(len(text.encode('utf-8')), 12 * 1024)

    def test_oversized_state_stops_without_sending_an_oversized_wake(self):
        self.controller.run()
        self.controller.rows[-1]['decision'] = '不可丢失的状态' * 3000
        self.model.reset_mock()
        with self.assertRaisesRegex(UserError, '活动记录仍保存在'):
            self.controller._context()
        self.model.assert_not_called()

    def test_large_receipt_cannot_erase_confirmed_write_when_recovering(self):
        from test_tools import ToolLoopTests
        from veil_agent import RequestFailed
        helper = ToolLoopTests()
        cfg = replace(self.cfg, tools={'enabled': True, 'max_steps': 4, 'max_output_tokens': 50})
        calls = [helper.tool_call('read_text_file', '{"path":"lab/source.md"}', 'read'),
                 helper.tool_call('append_file', '{"path":"lab/progress.md","content":"confirmed"}', 'write')]
        model = Mock(side_effect=[(helper.message(calls), 100, 20, 0), RuntimeError('fixture lost reply')])
        target = self.root / 'confirmed-progress.md'
        def invoke(name, args, approval):
            if name == 'read_text_file':
                return 'x' * 6000, '成功', 'lab/source.md'
            with target.open('a', encoding='utf-8') as stream:
                stream.write('confirmed\n')
            return 'receipt' * 1800, '成功', 'lab/progress.md'
        tools = Mock()
        tools.tools = [SimpleNamespace(name=name, description=name, inputSchema={})
                       for name in ('read_text_file', 'append_file')]
        tools.capability_event = None
        tools.invoke.side_effect = invoke
        chat = Chat(cfg, self.store, self.budget, model, tools=tools, output=lambda _: None)
        controller = AutonomyController(chat, self.hub)
        run_id = self.budget.clock()
        chat.autonomy_run_id = run_id
        controller.active_path.parent.mkdir(parents=True, exist_ok=True)
        controller.active_path.write_text(json.dumps({'started_at': run_id}), encoding='utf-8')
        with self.assertRaises(RequestFailed):
            chat.send('synthetic read and append', autonomy=True)
        row = self.store.read('test/tools.jsonl')[-1]
        self.assertEqual(row['state'], '成功')
        self.assertEqual(row['result_state'], '结果过长')
        recovered = AutonomyController(chat, self.hub)
        self.assertEqual(recovered.rows[-1]['lab_files_changed'], ['lab/progress.md'])
        self.assertEqual(recovered.rows[-1]['tools'][-1]['state'], '成功')
        self.assertEqual(recovered.rows[-1]['tools'][-1]['result_state'], '结果过长')
        self.assertEqual(target.read_text(encoding='utf-8'), 'confirmed\n')
        self.assertEqual(tools.invoke.call_count, 2)


if __name__ == '__main__':
    unittest.main()
