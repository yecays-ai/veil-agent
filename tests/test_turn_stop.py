import json
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

from veil_agent import Budget, Chat, RequestFailed, Store, load_character, send_message_schema

TOML = '''name = "Test"
provider = "fake"
persona = "persona/test.md"
history_rounds = 20
max_output_tokens = 50
[providers.fake]
model = "fake-model"
base_url = "https://example.invalid/v1"
api_key_env = "FAKE_KEY"
max_input_tokens = 6000
input_cny_per_million = "2"
cached_input_cny_per_million = "1"
output_cny_per_million = "8"
'''


def sends(*items, step=0):
    calls = [SimpleNamespace(id=f'call-{step}-{index}', function=SimpleNamespace(
        name='send_message', arguments=json.dumps(item))) for index, item in enumerate(items)]
    return SimpleNamespace(content='', tool_calls=calls, model_dump=lambda exclude_none=True: {
        'role': 'assistant', 'tool_calls': [{'id': c.id, 'type': 'function', 'function': {
            'name': c.function.name, 'arguments': c.function.arguments}} for c in calls]})


class TurnStopTests(unittest.TestCase):
    """Ticket 138: Peter ends his own turn with done; Lan's stop button ends it between steps."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        (self.root / 'characters' / 'test').mkdir(parents=True)
        (self.root / 'persona').mkdir()
        (self.root / 'persona' / 'test.md').write_text('Fictional test persona', encoding='utf-8')
        (self.root / 'characters' / 'test' / 'character.toml').write_text(TOML, encoding='utf-8')
        self.cfg = replace(load_character(self.root, 'test'), tools={'enabled': True, 'max_steps': 20})
        self.store = Store(self.root / 'data')
        self.budget = Budget(self.store)

    def assistant_rows(self):
        return [row['content'] for row in self.store.read('test/chat.jsonl') if row['role'] == 'assistant']

    def balanced(self):
        rows = self.store.read('costs.jsonl')
        reserved = {row['id'] for row in rows if row['type'] == 'reserve'}
        settled = {row['id'] for row in rows if row['type'] == 'settle'}
        return reserved == settled

    def test_schema_offers_done(self):
        self.assertEqual(send_message_schema()['function']['parameters']['properties']['done']['type'], 'boolean')

    def test_done_ends_the_turn_without_another_model_step(self):
        model = Mock(side_effect=[(sends({'text': 'night'}, {'text': 'sleep well', 'done': True}), 100, 20, 0)])
        chat = Chat(self.cfg, self.store, self.budget, model)
        reply = chat.send('fictional goodnight')
        self.assertEqual(model.call_count, 1)
        self.assertEqual(reply, 'sleep well')
        self.assertTrue(chat.reply_displayed)
        self.assertEqual(self.assistant_rows(), ['night', 'sleep well'])
        self.assertTrue(self.balanced())

    def test_without_done_he_keeps_going_until_he_stops(self):
        model = Mock(side_effect=[(sends({'text': 'one'}), 100, 20, 0),
                                  (sends({'text': 'two'}, step=1), 100, 20, 0),
                                  (SimpleNamespace(content='', tool_calls=[]), 100, 5, 0)])
        Chat(self.cfg, self.store, self.budget, model).send('fictional')
        self.assertEqual(model.call_count, 3)
        self.assertEqual(self.assistant_rows(), ['one', 'two'])

    def test_done_must_be_boolean(self):
        model = Mock(side_effect=[(sends({'text': 'x', 'done': 'yes'}), 100, 20, 0),
                                  (SimpleNamespace(content='fictional plain reply', tool_calls=[]), 100, 5, 0)])
        Chat(self.cfg, self.store, self.budget, model).send('fictional')
        self.assertEqual(self.assistant_rows(), ['fictional plain reply'])

    def test_stop_button_ends_after_the_running_step(self):
        chat = None

        def model(*args, **kwargs):
            if model.calls == 1:
                chat.user_stop.set()  # Lan presses stop while the second step is running
            model.calls += 1
            return sends({'text': f'bubble {model.calls}'}, step=model.calls), 100, 20, 0
        model.calls = 0
        chat = Chat(self.cfg, self.store, self.budget, model)
        reply = chat.send('fictional')
        self.assertEqual(model.calls, 2)
        # The step that was running when she pressed stop does not send anything.
        self.assertEqual(self.assistant_rows(), ['bubble 1'])
        self.assertEqual(reply, 'bubble 1')
        self.assertTrue(self.balanced())
        # The next turn starts fresh.
        chat.model = Mock(side_effect=[(SimpleNamespace(content='fresh', tool_calls=[]), 100, 5, 0)])
        self.assertEqual(chat.send('again'), 'fresh')

    def test_stop_before_any_message_says_so(self):
        chat = None

        def model(*args, **kwargs):
            chat.user_stop.set()
            return sends({'text': 'never shown'}), 100, 20, 0
        chat = Chat(self.cfg, self.store, self.budget, model)
        with self.assertRaises(RequestFailed) as result:
            chat.send('fictional')
        self.assertIn('已停止', str(result.exception))
        self.assertEqual(self.assistant_rows(), [])
        self.assertTrue(self.balanced())

    def test_hub_stop_only_while_replying(self):
        from veil_agent import ActivityState
        from web_agent import Hub
        hub = Hub('Test', ActivityState(self.store.root / 'test' / 'resident-state.json'),
                  media_root=self.store.root / 'test' / 'media')
        self.assertFalse(hub.stop_turn())
        hub.chat = Chat(self.cfg, self.store, self.budget, Mock())
        hub.busy, hub.autonomy_busy = True, True
        self.assertFalse(hub.stop_turn())
        hub.autonomy_busy = False
        self.assertTrue(hub.stop_turn())
        self.assertTrue(hub.chat.user_stop.is_set())
        hub.busy = False
        self.assertFalse(hub.stop_turn())


if __name__ == '__main__':
    unittest.main()
