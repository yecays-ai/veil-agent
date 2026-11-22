"""Ticket 419 review acceptance: tool follow-up context and the QQ local entry path.

The tool path uses the real Chat/Memory assembly with a fake model and a fake tool result; the QQ
path uses a fake channel/hub pair around the real AwayBridge and the real Chat.send. No network,
no real QQ message and no paid call.
"""
import copy
import queue
import threading
import unittest
from types import SimpleNamespace
from unittest.mock import Mock

from away_bridge import AwayBridge, Incoming
from test_recall_budget_419 import ChatAssemblyBudgetTests


class ToolFollowUpTests(ChatAssemblyBudgetTests):
    def test_a_large_tool_result_does_not_push_out_the_memory_or_the_recent_exchange(self):
        self.seed_history()
        chat, model, memory = self.build(9000)
        tool_call = SimpleNamespace(id='call-1', function=SimpleNamespace(
            name='lab_file_digest', arguments='{"path":"lab/x.txt"}'))
        first = SimpleNamespace(content=None, tool_calls=[tool_call],
            model_dump=lambda exclude_none=True: {'role': 'assistant', 'content': None,
                'tool_calls': [{'id': 'call-1', 'type': 'function', 'function': {
                    'name': 'lab_file_digest', 'arguments': '{"path":"lab/x.txt"}'}}]})
        final = SimpleNamespace(content='Done with the fictional file.', tool_calls=[],
            model_dump=lambda exclude_none=True: {'role': 'assistant',
                                                  'content': 'Done with the fictional file.'})
        model.side_effect = [(first, 100, 20, 0), (final, 120, 5, 0)]
        files = SimpleNamespace(
            tools=[SimpleNamespace(name='lab_file_digest', description='Read one lab file.',
                                   inputSchema={'type': 'object', 'properties': {'path': {'type': 'string'}}})],
            description='Fictional Lab tool.', startup_notice=None, enabled=True, client=object(),
            capability_event=None, capability_event_model=None, capability_record=None,
            consume_capability_event=Mock())
        tools = SimpleNamespace(
            tools=files.tools, files=files, description=files.description,
            capability_event=None, capability_event_model=None, capability_record=None,
            consume_capability_event=Mock(),
            invoke=Mock(return_value=('BIG-RESULT ' + 'Z' * 30000, '成功', 'lab/x.txt')))
        chat.tools = tools
        chat.send("你还记得第一次挑视频 video 那次吗", message_id='tool-q')
        self.assertGreaterEqual(len(model.call_args_list), 2)
        second = model.call_args_list[-1].args[0]
        joined = '\n'.join(message.get('content') if isinstance(message.get('content'), str) else ''
                           for message in second)
        self.assertIn('[bucket_id:', joined)                 # memory still in the follow-up request
        self.assertIn('是灯的事情让你累了', joined)            # recent exchange still present
        self.assertLess(joined.count('Z'), 30000)             # the huge tool result was trimmed
        self.assertTrue(memory.last_memory_budget)


class QQEntryTests(unittest.TestCase):
    class FakeChannel:
        name = 'qq'
        label = 'QQ'
        prefix = ''
        poll_seconds = 60

        def __init__(self):
            self.acked = []
            self.replies = []
            self.pending = [Incoming(key='k1', text='你还记得第一次挑视频 video 那次吗', reply_ref='r1')]

        def poll(self):
            items, self.pending = self.pending, []
            return items

        def ack(self, incoming):
            self.acked.append(incoming.key)

        def ready_to_send(self):
            return True

        def reply(self, text, reply_ref, attachments=()):
            self.replies.append((text, reply_ref))
            return None

    class FakeHub:
        def __init__(self):
            self.stopped = False
            self.cond = threading.Condition()
            self.pending = None
            self.inbox = queue.Queue()
            self.submitted = []
            self.pushed = []

        def submit(self, text, request_id, media_id=None, *, allow_while_busy=True):
            self.submitted.append((text, request_id))
            self.inbox.put((text, None, True, request_id, True))
            return 'accepted'

        def push(self, kind, text):
            self.pushed.append((kind, text))

        def answer(self, *_args, **_kwargs):
            return False

    def test_qq_inbound_reaches_chat_and_the_reply_goes_to_the_fake_outbox(self):
        case = ChatAssemblyBudgetTests('test_several_memories_and_the_recent_exchange_reach_the_model_together')
        case.setUp()
        self.addCleanup(case.doCleanups)
        case.seed_history()
        chat, model, memory = case.build(9000)
        hub = self.FakeHub()
        channel = self.FakeChannel()
        bridge = AwayBridge(channel, hub)
        bridge.poll_once()
        self.assertEqual('accepted', 'accepted')
        self.assertEqual(1, len(hub.submitted))
        text, request_id = hub.submitted[0]
        self.assertIn('video', text)
        self.assertEqual(['k1'], channel.acked)
        chat.send(text, message_id=request_id)
        reply = 'A fictional QQ reply.'
        bridge.turn_started(SimpleNamespace(message_id=request_id))
        bridge.assistant_message(reply)
        bridge.turn_ended()
        bridge.send_pending()
        self.assertEqual([(reply, 'r1')], channel.replies)
        self.assertIn('[bucket_id:', '\n'.join(
            message.get('content') if isinstance(message.get('content'), str) else ''
            for message in model.call_args.args[0]))

"""Ticket 419 second review: Chat tool-loop read-through, real Hub QQ dispatch, real Chat restart."""

import threading
import time
import unittest
from types import SimpleNamespace
from unittest.mock import Mock

from away_bridge import AwayBridge, Incoming
from memory_native import NativeMemoryTools
from test_recall_budget_419 import ChatAssemblyBudgetTests
from veil_agent import ActivityState, Chat, Store, load_character, run_chat
from web_agent import Hub


class ToolReadThroughChatTests(ChatAssemblyBudgetTests):
    def test_chat_receives_the_pointer_calls_timeline_and_answers_with_the_ending(self):
        self.rows = {
            's1': {'content': 'we met the stray cat at the station on a rainy night', 'title': 'The cat',
                   'created': '2026-10-01T20:00:00'},
            's2': {'content': 'I thought she had abandoned the cat because she left early', 'title': 'The misunderstanding',
                   'created': '2026-10-02T20:00:00'},
            's3': {'content': 'she explained she had gone to buy a carrier and we both cried',
                   'title': 'The correction', 'created': '2026-10-03T20:00:00'},
            's4': {'content': 'the cat now lives with us and sleeps on the windowsill',
                   'title': 'The result', 'created': '2026-10-20T20:00:00'},
        }
        chat, model, memory = self.build(30000)
        chat.opening_pending = False
        from memory_sidecar import Sidecar
        sidecar = Sidecar(self.store, 'peter', memory.budget.clock)
        for left, right in (('s2', 's1'), ('s3', 's2'), ('s4', 's3')):
            sidecar.link(left, right)
        self.store.append('peter/events.jsonl', {'event_id': 'cat', 'memory_id': 's2', 'title': 'The cat',
                                                 'story': '', 'status': 'open',
                                                 'first_at': '2026-10-01T20:00:00',
                                                 'last_at': '2026-10-20T20:00:00'})
        for index, key in enumerate(('s1', 's2', 's3', 's4'), 1):
            self.store.append('peter/event-stages.jsonl', {
                'stage_id': f'st{index}', 'event_id': 'cat', 'at': f'2026-10-0{index}T20:00:00',
                'text': self.rows[key]['content'], 'state': 'committed'})
        # Only the middle stage is a search hit. The ending must arrive through the tool.
        memory.judge = None
        original_call = memory.call

        def search_middle(name, args):
            if name == 'breath_search':
                return ('[bucket_id:s2]\n' + self.rows['s2']['content']
                        + '\n=== ombre:result-ids ===\n```json\n{"bucket_ids": ["s2"]}\n```')
            return original_call(name, args)

        memory.call = search_middle
        memory.cfg.memory['events'] = {'enabled': True}
        native = NativeMemoryTools(memory)
        tool_call = SimpleNamespace(id='timeline-1', function=SimpleNamespace(
            name='memory_timeline', arguments='{"event_id": "cat"}'))
        first = SimpleNamespace(content=None, tool_calls=[tool_call],
            model_dump=lambda exclude_none=True: {'role': 'assistant', 'content': None,
                'tool_calls': [{'id': 'timeline-1', 'type': 'function', 'function': {
                    'name': 'memory_timeline', 'arguments': '{"event_id": "cat"}'}}]})
        final = SimpleNamespace(content='It ended with the cat living with us.',
            tool_calls=[],
            model_dump=lambda exclude_none=True: {'role': 'assistant',
                                                  'content': 'It ended with the cat living with us.'})
        requests = []

        def reply(messages, **kwargs):
            requests.append(copy.deepcopy(messages))
            if len(requests) == 1:
                offered = {item['function']['name'] for item in kwargs.get('tools') or []}
                self.assertIn('memory_timeline', offered)
                return first, 100, 20, 0
            return final, 120, 5, 0

        model.side_effect = reply
        chat.tools = SimpleNamespace(
            tools=native.tools, invoke=Mock(wraps=native.invoke), description='Native memory tools.',
            capability_event=None, capability_event_model=None, capability_record=None,
            consume_capability_event=Mock())
        chat.send('I thought she had abandoned the cat', message_id='read-through')
        self.assertGreaterEqual(len(model.call_args_list), 2)
        first_request = '\n'.join(
            message.get('content') if isinstance(message.get('content'), str) else ''
            for message in requests[0])
        self.assertIn('memory_timeline', first_request)
        self.assertIn('cat', first_request)
        self.assertNotIn(self.rows['s4']['content'], first_request)
        chat.tools.invoke.assert_called_once()
        self.assertEqual(('memory_timeline', {'event_id': 'cat'}), chat.tools.invoke.call_args.args[:2])
        tool_results = [message['content'] for message in requests[-1] if message['role'] == 'tool']
        self.assertEqual(1, len(tool_results))
        self.assertNotIn('unavailable', tool_results[0])
        for row in self.rows.values():
            self.assertIn(row['content'], tool_results[0])
        second_request = '\n'.join(
            message.get('content') if isinstance(message.get('content'), str) else ''
            for message in requests[-1])
        self.assertIn('the cat now lives with us and sleeps on the windowsill', second_request)
        self.assertIn('she explained she had gone to buy a carrier', second_request)


class RealHubQQTests(ChatAssemblyBudgetTests):
    class FakeChannel:
        name = 'qq'
        label = 'QQ'
        prefix = ''
        poll_seconds = 60

        def __init__(self):
            self.acked = []
            self.replies = []
            self.pending = [Incoming(key='hub-k1', text='你还记得第一次挑视频 video 那次吗', reply_ref='hub-r1')]

        def poll(self):
            items, self.pending = self.pending, []
            return items

        def ack(self, incoming):
            self.acked.append(incoming.key)

        def ready_to_send(self):
            return True

        def reply(self, text, reply_ref, attachments=()):
            self.replies.append((text, reply_ref))
            return None

    def test_real_hub_consumes_the_qq_message_and_sends_the_fake_reply(self):
        self.seed_history()
        chat, model, memory = self.build(9000)
        channel = self.FakeChannel()
        activity = ActivityState(self.root / 'data' / 'resident-state.json')
        hub = Hub('Peter', activity, media_root=self.root / 'data' / 'media',
                  data_root=self.root / 'data', config_root=self.root)
        hub.chat = chat
        hub.busy = False   # the real loop is waiting for input at this point
        bridge = AwayBridge(channel, hub)
        hub.away.append(bridge)
        chat.away_channels[channel.name] = bridge.requested
        bridge.poll_once()
        self.assertEqual(['hub-k1'], channel.acked)
        original_output = hub.output

        def output(item):
            original_output(item)
            text = str(item) if isinstance(item, str) else getattr(item, 'text', '')
            if isinstance(text, str) and 'Fictional reply' in text:
                hub.stopping = True

        try:
            run_chat(chat, input_fn=hub.input_fn, output=output, start_notes=False)
        finally:
            bridge.send_pending()
        self.assertTrue(channel.replies, 'the reply never reached the fake QQ outbox')
        self.assertIn('Fictional reply', channel.replies[0][0])
        self.assertGreaterEqual(len(model.call_args_list), 1)
        first_request = '\n'.join(
            message.get('content') if isinstance(message.get('content'), str) else ''
            for message in model.call_args_list[0].args[0])
        self.assertIn('[bucket_id:', first_request)


class RealChatRestartSequenceTests(ChatAssemblyBudgetTests):
    def test_topic_switch_history_trim_return_and_new_chat_memory(self):
        self.rows = {f'ev{i}': {'content': f'Video night {i}. We watched a brass lamp film. '
                                           'Then we talked for hours.',
                                'title': f'Video night {i}',
                                'created': f'2026-10-{10 + i:02d}T10:00:00'} for i in range(5)}
        self.seed_history(rounds=20)
        chat, model, memory = self.build(9000, cooldown=3)
        first = chat.send('video night brass lamp', message_id='seq-1')
        self.assertTrue(first)
        self.assertTrue(memory.last_context_refs)
        self.assertTrue(chat.trim_note, 'the long conversation must actually trim history')
        chat.send('今天吃什么', message_id='seq-2')
        # A new Chat/Memory over the same data (the real restart path).
        cfg = load_character(self.root, 'peter')
        cfg.provider['max_input_tokens'] = 9000
        cfg.memory['recall'] = {'cooldown_turns': 3, 'random_chance': 0}
        cfg.memory.update({'model': 'fake-embedding', 'input_cny_per_million': '0',
                           'mode': 'native', 'base_url': 'https://example.invalid/v1'})
        from veil_agent import Budget
        from memory_agent import Memory, vault_path
        from test_recall_rotation import FictionalOmbre
        store = Store(self.root / 'data')
        budget = Budget(store)
        model2 = Mock(return_value=(SimpleNamespace(content='Fictional reply', tool_calls=[]), 100, 20, 0))
        chat2 = Chat(cfg, store, budget, model2, output=[].append)
        backend = FictionalOmbre(vault_path(store.root, 'peter') / 'dynamic', self.rows)
        memory2 = Memory(cfg, store, budget, model2, backend)
        chat2.memory = memory2
        chat2.opening_pending = False
        chat2.send('还记得 video night 那次吗', message_id='seq-3')
        self.assertTrue(memory2.last_context_refs)
        self.assertIsNotNone(memory2.last_memory_budget)
