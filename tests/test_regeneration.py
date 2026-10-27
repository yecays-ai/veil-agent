import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock

from veil_agent import Budget, Chat, Store, UserError, load_character

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


if __name__ == '__main__':
    unittest.main()
