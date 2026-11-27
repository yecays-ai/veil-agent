import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock

from veil.core.veil_agent import Budget, Chat, Store, UserError, load_character

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


class RegenerationTests(unittest.TestCase):
    """Ticket 137: regeneration finds the original user turn and never borrows another one."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        (root / 'characters' / 'test').mkdir(parents=True)
        (root / 'persona').mkdir()
        (root / 'persona' / 'test.md').write_text('Fictional test persona', encoding='utf-8')
        (root / 'characters' / 'test' / 'character.toml').write_text(TOML, encoding='utf-8')
        self.cfg = load_character(root, 'test')
        self.store = Store(root / 'data')

    def chat(self, rows):
        for row in rows:
            self.store.append(f'{self.cfg.id}/chat.jsonl', row)
        return Chat(self.cfg, self.store, Budget(self.store), Mock())

    def test_repeated_regeneration_uses_recorded_parent(self):
        chat = self.chat([
            {'role': 'user', 'content': 'older', 'request_id': 'r0', 'message_id': 'u0'},
            {'role': 'assistant', 'content': 'a0', 'request_id': 'r0', 'message_id': 'a0'},
            {'role': 'user', 'content': 'original', 'request_id': 'r1', 'message_id': 'u1'},
            {'role': 'assistant', 'content': 'again', 'request_id': 'r2', 'message_id': 'a2',
             'regeneration_parent_message_id': 'u1'}])
        user, removed = chat.prepare_regeneration('a2')
        self.assertEqual((user['message_id'], removed), ('u1', ['a2']))

    def test_deleted_parent_does_not_fall_back_to_an_older_turn(self):
        chat = self.chat([
            {'role': 'user', 'content': 'older', 'request_id': 'r0', 'message_id': 'u0'},
            {'role': 'assistant', 'content': 'a0', 'request_id': 'r0', 'message_id': 'a0'},
            {'role': 'assistant', 'content': 'again', 'request_id': 'r2', 'message_id': 'a2',
             'regeneration_parent_message_id': 'u1'}])
        with self.assertRaises(UserError):
            chat.prepare_regeneration('a2')

    def test_autonomous_message_is_not_tied_to_an_older_user_turn(self):
        chat = self.chat([
            {'role': 'user', 'content': 'older', 'request_id': 'r0', 'message_id': 'u0'},
            {'role': 'assistant', 'content': 'a0', 'request_id': 'r0', 'message_id': 'a0'},
            {'role': 'assistant', 'content': 'woke up', 'request_id': 'w1', 'message_id': 'a1',
             'autonomy_run_id': 'run1', 'origin': 'autonomy'}])
        with self.assertRaises(UserError):
            chat.prepare_regeneration('a1')

    def test_regeneration_collects_the_memories_the_replaced_reply_was_given(self):
        chat = self.chat([
            {'role': 'user', 'content': 'q', 'request_id': 'r1', 'message_id': 'u1'},
            {'role': 'assistant', 'content': 'a', 'request_id': 'r1', 'message_id': 'a1',
             'memory_refs': ['m1', 'm2']}])
        chat.prepare_regeneration('a1')
        self.assertEqual(chat.regeneration_release_refs, ['m1', 'm2'])

    def test_a_turn_that_delivers_nothing_gives_back_its_recall_cooldown(self):
        chat = self.chat([])
        chat.memory = Mock()
        chat.memory.last_recall_marks = {'m1': None}

        def failing(*args, **kwargs):
            chat.memory.last_recall_marks = {'m1': None}
            raise UserError('boom')
        chat._send_under_model_lock = failing
        with self.assertRaises(UserError):
            chat.send('hi')
        chat.memory.rollback_recall.assert_called_once()
        chat.memory.rollback_recall.reset_mock()
        chat._send_under_model_lock = lambda *a, **k: setattr(chat, 'reply_row_saved', True) or 'ok'
        chat.send('hi')
        chat.memory.rollback_recall.assert_not_called()


if __name__ == '__main__':
    unittest.main()
