"""Ticket 355: the local "how I talk" file reaches every model the same way, and Lan can switch and edit it."""
import tempfile
import unittest
from pathlib import Path

from agent_types import UserError
from veil_agent import Budget, Chat, Store, load_character
from veil.persona.voice_style import FRAME, MAX_CHARS, VoiceStyle, clean, sample_count, voice_path

SAMPLE = '# Rules\n\n- Short answers.\n\n# Samples\n\n> hello\nPeter: hi\nPeter： what now?\n\n\n\n> Lan: bye\nokay\n'


class FileTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        (root / 'persona').mkdir()
        self.path = voice_path(root / 'persona' / 'fake.md')
        self.store = Store(root / 'data')
        self.voice = VoiceStyle(self.store, 'fake', self.path, 'Peter')

    def test_file_sits_next_to_the_persona(self):
        self.assertEqual(self.path.name, 'fake.voice.md')

    def test_speaker_labels_are_removed_and_bubbles_stay_one_per_line(self):
        text = clean(SAMPLE, ('Peter',))
        self.assertNotIn('Peter', text)
        self.assertNotIn('Lan:', text)
        self.assertIn('> hello\nhi\nwhat now?\n\n> bye\nokay', text)
        self.assertEqual(sample_count(text), 2)

    def test_a_sentence_with_a_colon_is_not_mistaken_for_a_label(self):
        self.assertEqual(clean('note: keep this\nhonestly: no'), 'note: keep this\nhonestly: no')

    def test_missing_or_empty_file_adds_nothing(self):
        self.assertIsNone(self.voice.context())
        self.path.write_text('  \n', encoding='utf-8')
        self.assertIsNone(self.voice.context())
        self.assertEqual(self.voice.status()['characters'], 0)

    def test_context_frames_the_file_and_the_switch_removes_it(self):
        self.path.write_text(SAMPLE, encoding='utf-8')
        content = self.voice.context()['content']
        self.assertTrue(content.startswith(FRAME))
        self.assertIn('never say they happened', content)
        self.assertIn('> hello\nhi', content)
        self.assertFalse(self.voice.set_enabled(False))
        self.assertIsNone(self.voice.context())
        self.assertFalse(VoiceStyle(self.store, 'fake', self.path, 'Peter').enabled())  # remembered across restarts
        self.assertTrue(self.voice.set_enabled(True))

    def test_save_keeps_the_replaced_version_and_refuses_an_oversized_text(self):
        self.path.write_text('old text\n', encoding='utf-8')
        status = self.voice.save('Peter: new text')
        self.assertEqual(self.path.read_text(encoding='utf-8'), 'new text\n')
        self.assertEqual(status['characters'], 8)
        kept = list((self.path.parent / 'versions').iterdir())
        self.assertEqual([item.read_text(encoding='utf-8') for item in kept], ['old text\n'])
        with self.assertRaises(UserError):
            self.voice.save('x' * (MAX_CHARS + 1))
        self.assertEqual(self.path.read_text(encoding='utf-8'), 'new text\n')


class ChatTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        (root / 'characters' / 'test').mkdir(parents=True)
        (root / 'persona').mkdir()
        (root / 'persona' / 'test.md').write_text('Fictional test persona', encoding='utf-8')
        (root / 'characters' / 'test' / 'character.toml').write_text('''name = "Test"
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
''', encoding='utf-8')
        self.root = root
        self.seen = []

        def model(messages, **kwargs):
            self.seen.append([dict(item) for item in messages])
            return 'ok', 10, 2, 0
        cfg = load_character(root, 'test')
        store = Store(root / 'data')
        self.chat = Chat(cfg, store, Budget(store), model, output=lambda *_: None)

    def test_reference_follows_the_persona_on_every_request_and_edits_apply_at_once(self):
        self.chat.send('hello')
        self.assertFalse(any(m['content'].startswith(FRAME) for m in self.seen[-1] if m['role'] == 'system'))
        (self.root / 'persona' / 'test.voice.md').write_text(SAMPLE.replace('Peter', 'Test'), encoding='utf-8')
        self.chat.send('hello again')
        first, second = self.seen[-1][0], self.seen[-1][1]
        self.assertEqual(first['content'], 'Fictional test persona')
        self.assertTrue(second['content'].startswith(FRAME))
        self.assertNotIn('Test:', second['content'])
        self.chat.voice_style.set_enabled(False)
        self.chat.send('and again')
        self.assertFalse(any(m['content'].startswith(FRAME) for m in self.seen[-1] if m['role'] == 'system'))

    def test_each_request_leaves_a_finished_tool_list_for_the_page(self):
        self.assertFalse(hasattr(self.chat, 'tool_inventory_snapshot'))
        self.chat.send('hello')
        self.assertIn('rows', self.chat.tool_inventory_snapshot)
        self.chat.tool_schemas = lambda **_: 1 / 0
        self.chat.refresh_tool_inventory()  # a failure keeps the last list and never reaches the chat
        self.assertIn('rows', self.chat.tool_inventory_snapshot)

    def test_context_log_records_only_the_size(self):
        (self.root / 'persona' / 'test.voice.md').write_text(SAMPLE.replace('Peter', 'Test'), encoding='utf-8')
        self.chat.send('hello')
        row = self.chat.store.read('test/context-log.jsonl')[-1]
        self.assertGreater(row['voice_style_chars'], len(FRAME))
        self.assertNotIn('what now?', str(row))


if __name__ == '__main__':
    unittest.main()
