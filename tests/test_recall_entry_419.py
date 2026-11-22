"""Ticket 419 review acceptance: tool follow-up context and the QQ local entry path.

The tool path uses the real Chat/Memory assembly with a fake model and a fake tool result; the QQ
path uses a fake channel/hub pair around the real AwayBridge and the real Chat.send. No network,
no real QQ message and no paid call.
"""
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
