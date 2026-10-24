import json
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

from profile_notes import ProfileNotes
from veil_agent import Budget, Chat, Store, UserError, load_character


CONFIG = '''name = "Test"
provider = "fake"
persona = "persona/test.md"
[providers.fake]
model = "fake-model"
base_url = "https://example.invalid/v1"
api_key_env = "FAKE_KEY"
max_input_tokens = 6000
input_cny_per_million = "2"
cached_input_cny_per_million = "1"
output_cny_per_million = "8"
'''


class ProfileNotesTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.notes = ProfileNotes(self.root, 'test', {'file': 'about-user.md', 'max_bytes': 60})

    def test_append_replace_and_versions(self):
        self.assertIsNone(self.notes.context())
        self.assertEqual(self.notes.invoke('profile_append', {'text': 'Likes blue.'})[1], '成功')
        self.assertEqual(self.notes.invoke('profile_append', {'text': 'Stays up late.'})[1], '成功')
        self.assertEqual(self.notes.read(), 'Likes blue.\n\nStays up late.')
        self.assertEqual(self.notes.invoke('profile_replace',
                                           {'old_text': 'Likes blue.', 'new_text': 'Favorite color: blue.'})[1], '成功')
        self.assertEqual(self.notes.invoke('profile_replace', {'old_text': '\n\nStays up late.', 'new_text': ''})[1],
                         '成功')
        self.assertEqual(self.notes.read(), 'Favorite color: blue.')
        self.assertIn('Favorite color: blue.', self.notes.context())
        rows = [json.loads(line) for line in self.notes.versions.read_text(encoding='utf-8').splitlines()]
        self.assertEqual([row['tool'] for row in rows],
                         ['profile_append', 'profile_append', 'profile_replace', 'profile_replace'])
        self.assertEqual(rows[2]['before'], 'Likes blue.\n\nStays up late.')

    def test_rejections_leave_file_unchanged(self):
        self.notes.invoke('profile_append', {'text': 'Likes tea. Likes tea.'})
        result, state, _ = self.notes.invoke('profile_replace', {'old_text': 'Likes tea.', 'new_text': 'x'})
        self.assertEqual(state, '被拒绝')
        self.assertIn('2 times', result)
        result, state, _ = self.notes.invoke('profile_append', {'text': 'y' * 80})
        self.assertEqual(state, '被拒绝')
        self.assertIn('limit is 60', result)
        self.assertEqual(self.notes.invoke('profile_append', {'text': '  '})[1], '被拒绝')
        self.assertEqual(self.notes.read(), 'Likes tea. Likes tea.')

    def test_hand_edit_is_read_next_request(self):
        self.notes.path.parent.mkdir(parents=True)
        self.notes.path.write_text('﻿Written by hand.\n', encoding='utf-8')
        self.assertIn('Written by hand.', self.notes.context())


class ProfileChatTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        (self.root / 'characters' / 'test').mkdir(parents=True)
        (self.root / 'persona').mkdir()
        (self.root / 'persona' / 'test.md').write_text('Fictional persona', encoding='utf-8')

    def load(self, extra=''):
        (self.root / 'characters' / 'test' / 'character.toml').write_text(CONFIG + extra, encoding='utf-8')
        return load_character(self.root, 'test')

    def test_config(self):
        self.assertIsNone(self.load().profile)
        self.assertEqual(self.load('[profile]\nfile = "about-user.md"\n').profile,
                         {'file': 'about-user.md', 'max_bytes': 3000})
        with self.assertRaises(UserError):
            self.load('[profile]\nfile = "../escape.md"\n')

    def test_notes_follow_persona_and_tool_edits_reach_next_request(self):
        cfg = replace(self.load('[profile]\nfile = "about-user.md"\nmax_bytes = 500\n'),
                      tools={'enabled': True, 'max_steps': 3})
        store = Store(self.root / 'data')
        call = SimpleNamespace(id='p1', function=SimpleNamespace(
            name='profile_append', arguments='{"text": "Kills every plant, even cacti."}'))
        first = SimpleNamespace(content=None, tool_calls=[call],
            model_dump=lambda exclude_none=True: {'role': 'assistant', 'content': None,
                'tool_calls': [{'id': 'p1', 'type': 'function', 'function': {
                    'name': 'profile_append', 'arguments': call.function.arguments}}]})
        final = SimpleNamespace(content='noted', tool_calls=[],
            model_dump=lambda exclude_none=True: {'role': 'assistant', 'content': 'noted'})
        tools = SimpleNamespace(tools=[], description='Fictional tools.', capability_event=None,
            capability_event_model=None, consume_capability_event=Mock(), invoke=Mock(),
            announce_profile_notes=Mock())
        prompts = []
        def fake_model(messages, **kwargs):
            prompts.append((list(messages), kwargs.get('tools')))
            return (first if len(prompts) == 1 else final), 100, 20, 0
        chat = Chat(cfg, store, Budget(store), Mock(side_effect=fake_model), tools=tools, output=lambda _: None)
        tools.announce_profile_notes.assert_called_once_with(500)

        chat.send('I killed my cactus.')
        first_prompt, offered = prompts[0]
        self.assertIn('profile_append', {item['function']['name'] for item in offered})
        self.assertNotIn('NOTES ABOUT', first_prompt[1]['content'])
        tools.invoke.assert_not_called()

        chat.send('Guess what else died.')
        later = prompts[-1][0]
        self.assertEqual(later[0]['content'], 'Fictional persona')
        self.assertIn('NOTES ABOUT LAN', later[1]['content'])
        self.assertIn('Kills every plant', later[1]['content'])
        self.assertIn('SYSTEM FACTS', later[2]['content'])


if __name__ == '__main__':
    unittest.main()
