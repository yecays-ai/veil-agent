import json
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

from veil_agent import Budget, Chat, Store, load_character
from web_agent import Hub
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
        chat.turn_done = False
        result, state, _ = chat.reply_later(json.dumps({'cancel': True}))
        self.assertEqual(state, '成功')
        self.assertIn('Cancelled 1', result)
        self.assertFalse(chat.turn_done)
        saved = json.loads((self.store.root / 'test/reply-later.json').read_text(encoding='utf-8'))
        self.assertEqual(saved['pending'][0]['state'], 'cancelled_by_peter')
        self.assertIsNone(chat.claim_due_reply_later())

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


if __name__ == '__main__':
    unittest.main()
