import json
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

from veil_agent import Budget, Chat, Store, load_character
from agent_types import RequestFailed
from web_agent import Hub, ChatAction
from veil_agent import ActivityState

TOML = '''name = "Test"
provider = "fake"
persona = "persona/test.md"
history_rounds = 20
max_output_tokens = 50
[providers.fake]
model = "fake"
base_url = "https://example.invalid/v1"
api_key_env = "FAKE"
max_input_tokens = 6000
input_cny_per_million = "2"
cached_input_cny_per_million = "1"
output_cny_per_million = "8"
'''


class FreeReplyTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        (self.root / 'characters/test').mkdir(parents=True)
        (self.root / 'persona').mkdir()
        (self.root / 'persona/test.md').write_text('test persona', encoding='utf-8')
        (self.root / 'characters/test/character.toml').write_text(TOML, encoding='utf-8')
        self.cfg = replace(load_character(self.root, 'test'), tools={'enabled': True, 'max_steps': 20})
        self.store = Store(self.root / 'data')

    def test_hub_accepts_and_batches_while_busy(self):
        hub = Hub('Test', ActivityState(self.store.root / 'test/state.json'),
                  media_root=self.store.root / 'test/media')
        hub.busy = True
        self.assertEqual(hub.submit('one', 'id-1'), 'accepted')
        self.assertEqual(hub.submit('two', 'id-2'), 'accepted')
        batch = hub._chat_batch(hub.inbox.get_nowait())
        self.assertEqual([x['content'] for x in batch.user_events], ['one', 'two'])

    def test_accepted_rows_are_durable_immediately_and_not_saved_twice(self):
        model = Mock(return_value=(SimpleNamespace(content='answer', tool_calls=[]), 10, 2, 0))
        chat = Chat(self.cfg, self.store, Budget(self.store), model, output=[].append)
        hub = Hub('Test', ActivityState(self.store.root / 'test/state.json'),
                  media_root=self.store.root / 'test/media')
        hub.chat = chat
        hub.busy = True
        self.assertEqual(hub.submit('one', 'id-1'), 'accepted')
        self.assertEqual(hub.submit('two', 'id-2'), 'accepted')
        self.assertEqual([r['content'] for r in self.store.read('test/incoming-submissions.jsonl')
                          if r.get('state') == 'received'], ['one', 'two'])
        # chat.jsonl must remain clean until the messages actually enter turn processing
        self.assertEqual(self.store.read('test/chat.jsonl'), [])
        batch = hub._chat_batch(hub.inbox.get_nowait())
        chat.send(str(batch), user_events=batch.user_events)
        rows = self.store.read('test/chat.jsonl')
        self.assertEqual([r['content'] for r in rows if r['role'] == 'user'], ['one', 'two'])

    def test_empty_answer_is_asked_once_more_not_taken_as_silence(self):
        # Ticket 153: only stay_silent is silence; an empty answer is a provider failure.
        model = Mock(side_effect=[(SimpleNamespace(content='', tool_calls=[]), 10, 2, 0),
                                  (SimpleNamespace(content='here I am', tool_calls=[]), 10, 2, 0)])
        shown = []
        chat = Chat(self.cfg, self.store, Budget(self.store), model, output=shown.append)
        self.assertEqual(chat.send('hello'), 'here I am')
        self.assertEqual(model.call_count, 2)
        self.assertTrue(any('empty reply' in str(item) for item in shown))
        self.assertEqual(self.store.read('test/message-processing.jsonl')[-1]['state'], 'replied')
        diagnostics = self.store.read('test/memory/_agent/diagnostics.jsonl')
        self.assertEqual([row['code'] for row in diagnostics], ['empty_reply'])

    def test_empty_answer_twice_fails_visibly_instead_of_silence(self):
        model = Mock(return_value=(SimpleNamespace(content='', tool_calls=[]), 10, 2, 0))
        chat = Chat(self.cfg, self.store, Budget(self.store), model, output=[].append)
        with self.assertRaises(RequestFailed) as caught:
            chat.send('hello')
        self.assertIn('不是他选择不回', str(caught.exception))
        self.assertEqual(model.call_count, 2)
        self.assertEqual(self.store.read('test/message-processing.jsonl')[-1]['state'], 'failed')
        self.assertEqual([r for r in self.store.read('test/chat.jsonl') if r['role'] == 'assistant'], [])

    def test_eleventh_message_is_rejected(self):
        chat = Chat(self.cfg, self.store, Budget(self.store), Mock(), output=[].append)
        chat.autonomy_mode = False
        chat.send_message_count = 10
        result, state, _ = chat.send_message(json.dumps({'text': 'eleven'}), 'request')
        self.assertEqual(state, '被拒绝')
        self.assertIn('at most 10', result)

    def test_tenth_message_ends_the_turn(self):
        chat = Chat(self.cfg, self.store, Budget(self.store), Mock(), output=[].append)
        chat.autonomy_mode = False
        chat.send_message_count = 9
        result, state, _ = chat.send_message(json.dumps({'text': 'ten'}), 'request')
        self.assertEqual(state, '成功')
        self.assertTrue(chat.turn_done)
        self.assertIn('10th message', result)

    def test_lan_steering_in_gives_a_fresh_message_allowance(self):
        chat = Chat(self.cfg, self.store, Budget(self.store), Mock(), output=[].append)
        chat.send_message_count = 7
        chat.accept_steer(SimpleNamespace(user_events=[{'content': 'hey', 'message_id': 'm1', 'persisted': True}],
                                          complete=None, runtime_context=None), 'request')
        self.assertEqual(chat.send_message_count, 0)

    def test_stay_silent_ends_the_turn_showing_nothing(self):
        call = SimpleNamespace(id='quiet', function=SimpleNamespace(name='stay_silent', arguments='{}'))
        message = SimpleNamespace(content='', tool_calls=[call], model_dump=lambda exclude_none=True: {
            'role': 'assistant', 'tool_calls': [{'id': 'quiet', 'type': 'function', 'function': {
                'name': 'stay_silent', 'arguments': '{}'}}]})
        shown = []
        chat = Chat(self.cfg, self.store, Budget(self.store), Mock(return_value=(message, 10, 2, 0)),
                    output=shown.append)
        self.assertEqual(chat.send('do not reply'), '')
        self.assertEqual(self.store.read('test/message-processing.jsonl')[-1]['state'], 'silent')
        self.assertFalse([row for row in self.store.read('test/chat.jsonl') if row.get('role') == 'assistant'])

    def test_reply_later_persists(self):
        chat = Chat(self.cfg, self.store, Budget(self.store), Mock(), output=[].append)
        chat.autonomy_mode = False
        result, state, _ = chat.reply_later(json.dumps({'minutes': 5, 'note': 'think again'}))
        self.assertEqual(state, '成功')
        self.assertIn('Reminder saved', result)
        saved = json.loads((self.store.root / 'test/reply-later.json').read_text(encoding='utf-8'))
        self.assertEqual(saved['pending'][0]['note'], 'think again')

    def test_reply_later_tool_ends_an_ordinary_turn_without_error(self):
        call = SimpleNamespace(id='later', function=SimpleNamespace(
            name='reply_later', arguments=json.dumps({'minutes': 5, 'note': 'think again'})))
        message = SimpleNamespace(content='', tool_calls=[call], model_dump=lambda exclude_none=True: {
            'role': 'assistant', 'tool_calls': [{'id': 'later', 'type': 'function', 'function': {
                'name': 'reply_later', 'arguments': call.function.arguments}}]})
        model = Mock(return_value=(message, 10, 2, 0))
        chat = Chat(self.cfg, self.store, Budget(self.store), model, output=[].append)
        self.assertEqual(chat.send('later please'), '')
        states = self.store.read('test/message-processing.jsonl')
        self.assertEqual(states[-1]['state'], 'reply_later')

    def test_reply_later_cancel_drops_pending_without_ending_turn(self):
        chat = Chat(self.cfg, self.store, Budget(self.store), Mock(), output=[].append)
        chat.reply_later(json.dumps({'minutes': 5, 'note': 'think again'}))
        self.assertTrue(chat.turn_done)
        self.assertTrue(chat.reply_later_called)
        result, state, _ = chat.reply_later(json.dumps({'cancel': True}))
        self.assertEqual(state, '成功')
        self.assertIn('Cancelled 1', result)
        self.assertFalse(chat.turn_done)
        self.assertFalse(chat.reply_later_called)
        saved = json.loads((self.store.root / 'test/reply-later.json').read_text(encoding='utf-8'))
        self.assertEqual(saved['pending'][0]['state'], 'cancelled_by_peter')
        self.assertIsNone(chat.claim_due_reply_later())

    def test_reply_later_cancel_preserves_send_message_done(self):
        chat = Chat(self.cfg, self.store, Budget(self.store), Mock(), output=[].append)
        chat.send_message(json.dumps({'text': 'done message', 'done': True}), 'req-1')
        self.assertTrue(chat.turn_done)
        self.assertTrue(chat.message_turn_done)
        # Cancel a pending reply_later after message done has finished the turn
        self.store.replace('test/reply-later.json', {'pending': [{
            'id': 'old', 'batch_id': 'b1', 'due': '2026-10-28T12:00:00+08:00',
            'note': 'pending note', 'state': 'pending'}], 'wake_log': []})
        result, state, _ = chat.reply_later(json.dumps({'cancel': True}))
        self.assertEqual(state, '成功')
        # turn_done must remain True because send_message(done=True) set it
        self.assertTrue(chat.turn_done)
        self.assertTrue(chat.message_turn_done)

    def test_reply_later_cancel_in_same_turn_preserves_send_message_done(self):
        chat = Chat(self.cfg, self.store, Budget(self.store), Mock(), output=[].append)
        chat.reply_later(json.dumps({'minutes': 5, 'note': 'later'}))
        self.assertTrue(chat.reply_later_called)
        chat.send_message(json.dumps({'text': 'done message', 'done': True}), 'req-1')
        self.assertTrue(chat.message_turn_done)
        chat.reply_later(json.dumps({'cancel': True}))
        self.assertFalse(chat.reply_later_called)
        # message_turn_done prevents turn_done from being cleared
        self.assertTrue(chat.turn_done)

    def test_claimed_reply_later_counts_toward_same_batch_limit(self):
        chat = Chat(self.cfg, self.store, Budget(self.store), Mock(), output=[].append)
        chat.reply_later_batch = 'batch'
        self.store.replace('test/reply-later.json', {'pending': [{
            'id': 'old', 'batch_id': 'batch', 'due': '2026-10-28T08:00:00+08:00',
            'note': 'old', 'state': 'claimed'}], 'wake_log': []})
        self.assertEqual(chat.reply_later(json.dumps({'minutes': 5, 'note': 'second'}))[1], '成功')
        result, state, _ = chat.reply_later(json.dumps({'minutes': 6, 'note': 'third'}))
        self.assertEqual(state, '被拒绝')
        self.assertIn('at most 2', result)
        saved = json.loads((self.store.root / 'test/reply-later.json').read_text(encoding='utf-8'))
        self.assertEqual([row['state'] for row in saved['pending']], ['claimed', 'pending'])

    def test_command_waits_behind_current_batch_and_is_not_steered(self):
        hub = Hub('Test', ActivityState(self.store.root / 'test/state.json'),
                  media_root=self.store.root / 'test/media')
        hub.inbox.put(('hello', None, None, 'one', False))
        hub.inbox.put(('/quit', None, None, 'quit', False))
        batch = hub._chat_batch(hub.inbox.get_nowait())
        self.assertEqual(str(batch), 'hello')
        self.assertIsNone(hub.claim_steer())
        self.assertEqual(hub.inbox.get_nowait()[0], '/quit')

    def test_media_batch_marks_the_first_persisted_event(self):
        hub = Hub('Test', ActivityState(self.store.root / 'test/state.json'),
                  media_root=self.store.root / 'test/media')
        media = {'id': 'm', 'kind': 'image'}
        hub.inbox.put(('photo', media, None, 'photo-id', True))
        hub.inbox.put(('after', None, None, 'after-id', True))
        batch = hub._chat_batch(hub.inbox.get_nowait())
        self.assertTrue(batch.user_events[0]['persisted'])
        self.assertEqual(hub.inbox.get_nowait()[3], 'after-id')

    def test_chat_batch_does_not_swallow_request_reply_action(self):
        hub = Hub('Test', ActivityState(self.store.root / 'test/state.json'),
                  media_root=self.store.root / 'test/media')
        action = ChatAction('request_reply', {})
        hub.inbox.put(('first message', None, None, 'id-1', False))
        hub.inbox.put(action)
        hub.inbox.put(('second message', None, None, 'id-2', False))
        batch = hub._chat_batch(hub.inbox.get_nowait())
        self.assertEqual(str(batch), 'first message')
        # ChatAction remains untouched at the head of the queue
        self.assertIs(hub.inbox.queue[0], action)
        steered = hub.claim_steer()
        self.assertIs(steered, action)
        # Next item after steer is the second message
        self.assertEqual(hub.inbox.get_nowait()[0], 'second message')

    def test_voice_requeue_maintains_chronological_order(self):
        hub = Hub('Test', ActivityState(self.store.root / 'test/state.json'),
                  media_root=self.store.root / 'test/media')
        voice_media = {'id': 'v1', 'kind': 'voice', 'transcript': 'hello voice'}
        # Lan sends voice A and text B in rapid succession
        hub.inbox.put(('voice-raw', voice_media, None, 'id-a', True))
        hub.inbox.put(('text B', None, None, 'id-b', True))
        # Hook into _chat_input to simulate Lan sending text C during voice transcription
        orig_chat_input = hub._chat_input
        def mock_chat_input(row):
            res = orig_chat_input(row)
            if row[0] == 'voice-raw' and hub.inbox.empty():
                # Concurrent message C arrives while voice A is processing
                hub.inbox.put(('text C', None, None, 'id-c', True))
            return res
        hub._chat_input = mock_chat_input

        batch = hub._chat_batch(hub.inbox.get_nowait())
        # First batch returned must be voice A
        self.assertEqual(batch.message_id, 'id-a')
        self.assertIn('hello voice', str(batch))

        # Remaining items in inbox must be strictly B, then C (chronological order A -> B -> C)
        next_item = hub.inbox.get_nowait()
        self.assertEqual(next_item[3], 'id-b')
        self.assertEqual(next_item[0], 'text B')

        third_item = hub.inbox.get_nowait()
        self.assertEqual(third_item[3], 'id-c')
        self.assertEqual(third_item[0], 'text C')
        self.assertTrue(hub.inbox.empty())

    def test_reply_later_cancel_in_tool_loop_allows_continued_conversation(self):
        call_later = SimpleNamespace(id='call_later', function=SimpleNamespace(
            name='reply_later', arguments=json.dumps({'minutes': 5, 'note': 'wait'})))
        call_cancel = SimpleNamespace(id='call_cancel', function=SimpleNamespace(
            name='reply_later', arguments=json.dumps({'cancel': True})))
        call_send = SimpleNamespace(id='call_send', function=SimpleNamespace(
            name='send_message', arguments=json.dumps({'text': 'nevermind, here is the answer', 'done': True})))

        msg1 = SimpleNamespace(content='', tool_calls=[call_later, call_cancel], model_dump=lambda exclude_none=True: {
            'role': 'assistant', 'tool_calls': [
                {'id': 'call_later', 'type': 'function', 'function': {'name': 'reply_later', 'arguments': call_later.function.arguments}},
                {'id': 'call_cancel', 'type': 'function', 'function': {'name': 'reply_later', 'arguments': call_cancel.function.arguments}}
            ]})
        msg2 = SimpleNamespace(content='', tool_calls=[call_send], model_dump=lambda exclude_none=True: {
            'role': 'assistant', 'tool_calls': [
                {'id': 'call_send', 'type': 'function', 'function': {'name': 'send_message', 'arguments': call_send.function.arguments}}
            ]})

        model = Mock(side_effect=[(msg1, 10, 2, 0), (msg2, 10, 2, 0)])
        outputs = []
        chat = Chat(self.cfg, self.store, Budget(self.store), model, output=outputs.append)
        res = chat.send('can you help?')
        self.assertEqual(res, 'nevermind, here is the answer')
        states = self.store.read('test/message-processing.jsonl')
        self.assertEqual(states[-1]['state'], 'replied')

    def test_queued_messages_maintain_strict_chronological_order_without_prompt_duplication(self):
        prompts = []
        def fake_model(messages, **kwargs):
            prompts.append(list(messages))
            if len(prompts) == 1:
                return ('reply A', 10, 2, 0)
            return ('reply B', 10, 2, 0)

        cfg = replace(self.cfg, tools=None)
        chat = Chat(cfg, self.store, Budget(self.store), fake_model, output=[].append)

        # User submits A and it starts turn 1
        chat.persist_incoming('message A', 'req-a')
        # Simulate message B submitted over HTTP while turn 1 is pending/in flight
        chat.persist_incoming('message B', 'req-b')

        # chat.jsonl must NOT yet contain message B (it is safely in incoming-submissions.jsonl)
        chat_rows_before = self.store.read('test/chat.jsonl')
        self.assertNotIn('message B', [r.get('content') for r in chat_rows_before])

        # Turn 1 completes
        chat.send('message A', message_id='req-a')
        # Now chat.jsonl has [message A, reply A]
        chat_rows_mid = self.store.read('test/chat.jsonl')
        self.assertEqual([r.get('content') for r in chat_rows_mid], ['message A', 'reply A'])

        # Turn 2 runs for message B
        chat.send('message B', message_id='req-b')

        # Model prompt for Turn 2 must NOT duplicate message B in history
        prompt_b = prompts[1]
        # Count occurrences of message B in prompt_b
        b_occurrences = [msg for msg in prompt_b if msg.get('role') == 'user' and 'message B' in msg.get('content', '')]
        self.assertEqual(len(b_occurrences), 1, "message B should only appear once in prompt as current message")

        # Chat history on disk must maintain strict chronological order: [User A, Reply A, User B, Reply B]
        chat_rows_final = self.store.read('test/chat.jsonl')
        self.assertEqual(
            [(r.get('role'), r.get('content')) for r in chat_rows_final],
            [('user', 'message A'), ('assistant', 'reply A'), ('user', 'message B'), ('assistant', 'reply B')]
        )

        # recent() grouping must correctly pair A with Reply A, and B with Reply B
        _, rounds = chat.recent()
        self.assertEqual(rounds, 2)

    def test_uncommitted_incoming_submissions_recovered_after_crash(self):
        cfg = replace(self.cfg, tools=None)
        chat = Chat(cfg, self.store, Budget(self.store), Mock(), output=[].append)

        # Message is accepted into incoming-submissions
        chat.persist_incoming('crash test message', 'req-crash')

        # Before turn processing, it is uncommitted
        uncommitted = chat.recover_uncommitted_incoming()
        self.assertEqual(len(uncommitted), 1)
        self.assertEqual(uncommitted[0]['content'], 'crash test message')

        # Now simulate message entering turn and being committed
        chat.model = Mock(return_value=('reply to crash', 10, 2, 0))
        chat.send('crash test message', message_id='req-crash')

        # After turn completion, it is committed and not returned by recover_uncommitted_incoming
        uncommitted_after = chat.recover_uncommitted_incoming()
        self.assertEqual(uncommitted_after, [])


if __name__ == '__main__':
    unittest.main()


class _Said(str):
    """A steered Lan message as the hub hands it over."""


def _tool_message(*calls):
    rows = [{'id': call_id, 'type': 'function', 'function': {'name': name, 'arguments': json.dumps(args)}}
            for call_id, name, args in calls]
    tool_calls = [SimpleNamespace(id=row['id'], function=SimpleNamespace(
        name=row['function']['name'], arguments=row['function']['arguments'])) for row in rows]
    return SimpleNamespace(content='', tool_calls=tool_calls,
                           model_dump=lambda exclude_none=True: {'role': 'assistant', 'tool_calls': rows})


def _text_message(text):
    return SimpleNamespace(content=text, tool_calls=None,
                           model_dump=lambda exclude_none=True: {'role': 'assistant', 'content': text})


class LanWroteMeanwhileTests(unittest.TestCase):
    """Ticket 150: words Lan sends while a step is generating reach Peter in the same turn."""

    setUp = FreeReplyTests.setUp

    def chat_with(self, replies, waiting):
        prompts = []

        def model(messages, **kwargs):
            prompts.append(list(messages))
            return (replies[len(prompts) - 1], 10, 2, 0)

        shown = []
        chat = Chat(self.cfg, self.store, Budget(self.store), model, output=shown.append)
        queue = [_Said(text) for text in waiting]
        chat.steer_pending = lambda: bool(queue)
        chat.claim_steer = lambda: queue.pop(0) if queue else None
        return chat, prompts

    def assistant_rows(self):
        return [row['content'] for row in self.store.read('test/chat.jsonl') if row.get('role') == 'assistant']

    def test_done_does_not_end_the_turn_over_her_newer_message(self):
        chat, prompts = self.chat_with([
            _tool_message(('a', 'send_message', {'text': 'about dragons', 'done': True})),
            _tool_message(('b', 'send_message', {'text': 'oh, the money. sorry', 'done': True})),
        ], ['wait, my money!!'])
        self.assertEqual(chat.send('so, 一言难尽'), 'oh, the money. sorry')
        self.assertEqual(len(prompts), 2)
        self.assertIn('wait, my money!!', prompts[1][-1]['content'])
        self.assertEqual(self.assistant_rows(), ['about dragons', 'oh, the money. sorry'])

    def test_plain_reply_is_shown_then_her_new_message_is_answered(self):
        chat, prompts = self.chat_with([_text_message('first thought'), _text_message('and to that: yes')],
                                       ['also this'])
        self.assertEqual(chat.send('hi'), 'and to that: yes')
        self.assertEqual(len(prompts), 2)
        self.assertEqual(self.assistant_rows(), ['first thought', 'and to that: yes'])
        users = [row['content'] for row in self.store.read('test/chat.jsonl') if row.get('role') == 'user']
        self.assertEqual(users, ['hi', 'also this'])

    def test_plain_text_after_steer_is_not_swallowed_as_restatement(self):
        chat, prompts = self.chat_with([
            _tool_message(('a', 'send_message', {'text': 'bubble one'})),
            _text_message('answer to the new thing'),
        ], ['new thing'])
        self.assertEqual(chat.send('hi'), 'answer to the new thing')
        self.assertEqual(self.assistant_rows(), ['bubble one', 'answer to the new thing'])

    def test_silence_is_reconsidered_when_she_writes_again(self):
        chat, prompts = self.chat_with([
            _tool_message(('a', 'stay_silent', {})),
            _tool_message(('b', 'send_message', {'text': 'ok ok I am here', 'done': True})),
        ], ['TALK BACK NOW'])
        self.assertEqual(chat.send('do not reply'), 'ok ok I am here')
        self.assertEqual(self.store.read('test/message-processing.jsonl')[-1]['state'], 'replied')

    def test_nothing_waiting_ends_the_turn_as_before(self):
        chat, prompts = self.chat_with([_text_message('just this')], [])
        self.assertEqual(chat.send('hi'), 'just this')
        self.assertEqual(len(prompts), 1)
        self.assertEqual(self.assistant_rows(), ['just this'])


class WaitWhileLanTypesTests(unittest.TestCase):
    """Ticket 150: Peter starts only once Lan is not typing her next line."""

    setUp = FreeReplyTests.setUp

    def hub(self):
        return Hub('Test', ActivityState(self.store.root / 'test/state.json'),
                   media_root=self.store.root / 'test/media')

    def test_lines_sent_while_she_keeps_typing_are_read_together(self):
        import threading
        import time
        from unittest.mock import patch
        hub = self.hub()
        hub.inbox.put(('wait', None, None, 'id-1', False))
        hub.set_typing(True)

        def keep_typing_then_send():
            for _ in range(5):
                time.sleep(0.1)
                hub.set_typing(True)
            hub.inbox.put(('listen to me', None, None, 'id-2', False))
            hub.set_typing(False)

        worker = threading.Thread(target=keep_typing_then_send)
        with patch('web_agent.TYPING_GRACE_SECONDS', 0.2), patch('web_agent.TYPING_ACTIVE_SECONDS', 0.3):
            worker.start()
            batch = hub._input_fn()
        worker.join()
        self.assertEqual([row['content'] for row in batch.user_events], ['wait', 'listen to me'])

    def test_no_typing_starts_right_after_the_grace(self):
        import time
        from unittest.mock import patch
        hub = self.hub()
        hub.inbox.put(('hi', None, None, 'id-1', False))
        with patch('web_agent.TYPING_GRACE_SECONDS', 0.2):
            started = time.monotonic()
            batch = hub._input_fn()
        self.assertEqual(str(batch), 'hi')
        self.assertLess(time.monotonic() - started, 1.5)

    def test_an_untouched_draft_does_not_hold_him(self):
        import time
        from unittest.mock import patch
        hub = self.hub()
        hub.set_typing(True)
        hub.typed_at = time.monotonic() - 10
        hub.inbox.put(('hi', None, None, 'id-1', False))
        with patch('web_agent.TYPING_GRACE_SECONDS', 0.2), patch('web_agent.TYPING_ACTIVE_SECONDS', 0.3):
            started = time.monotonic()
            batch = hub._input_fn()
        self.assertEqual(str(batch), 'hi')
        self.assertEqual(batch.runtime_context, 'Lan is typing.')
        self.assertLess(time.monotonic() - started, 1.5)

    def test_request_reply_does_not_wait_for_typing(self):
        import time
        hub = self.hub()
        hub.set_typing(True)
        hub.inbox.put(('hi', None, None, 'id-1', False))
        hub.inbox.put(ChatAction('request_reply', {}))
        started = time.monotonic()
        batch = hub._input_fn()
        self.assertEqual(str(batch), 'hi')
        self.assertLess(time.monotonic() - started, 1.0)
