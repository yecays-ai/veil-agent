import json
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

from veil_agent import Budget, Chat, Store, load_character
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
        self.assertEqual([r['content'] for r in self.store.read('test/chat.jsonl')], ['one', 'two'])
        batch = hub._chat_batch(hub.inbox.get_nowait())
        chat.send(str(batch), user_events=batch.user_events)
        rows = self.store.read('test/chat.jsonl')
        self.assertEqual([r['content'] for r in rows if r['role'] == 'user'], ['one', 'two'])

    def test_silence_is_success_and_has_no_assistant_row(self):
        model = Mock(return_value=(SimpleNamespace(content='', tool_calls=[]), 10, 2, 0))
        chat = Chat(self.cfg, self.store, Budget(self.store), model, output=[].append)
        self.assertEqual(chat.send('hello'), '')
        self.assertTrue(chat.reply_displayed)
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


if __name__ == '__main__':
    unittest.main()
