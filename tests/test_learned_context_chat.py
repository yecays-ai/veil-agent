
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

from veil.core.veil_agent import Budget, Chat, Store, load_character
from veil.tools.learned_tools import ONGOING_TOOL


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
[profile]
file = "learned-context.json"
learned = true
legacy_file = "about-lan.md"
max_bytes = 4000
[user_persona]
file = "lan.md"
max_bytes = 4000
'''


class LearnedContextChatTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        (self.root / 'characters' / 'test').mkdir(parents=True)
        (self.root / 'persona').mkdir()
        (self.root / 'persona' / 'test.md').write_text('Fictional persona', encoding='utf-8')
        (self.root / 'persona' / 'lan.md').write_text('Lan is a designer.', encoding='utf-8')
        (self.root / 'characters' / 'test' / 'character.toml').write_text(CONFIG, encoding='utf-8')
        self.cfg = load_character(self.root, 'test')
        self.store = Store(self.root / 'data')
        (self.root / 'data' / 'test').mkdir(parents=True, exist_ok=True)
        (self.root / 'data' / 'test' / 'about-lan.md').write_text('Lan likes blue.\n', encoding='utf-8')
        self.prompts = []

    def chat(self):
        def fake_model(messages, **kwargs):
            self.prompts.append((list(messages), kwargs.get('tools')))
            return ('ok', 10, 1, 0)
        return Chat(self.cfg, self.store, Budget(self.store), Mock(side_effect=fake_model), output=lambda _: None)

    def test_user_persona_and_learned_context_are_injected(self):
        chat = self.chat()
        chat.profile.invoke('profile_append', {'text': 'Anger needs repair, not a report.', 'section': 'growth'})
        chat.profile.invoke('ongoing_update', {'action': 'add', 'text': 'Lan is angry and has not replied.'})
        chat.send('hello')
        messages = self.prompts[0][0]
        contents = [m['content'] for m in messages if isinstance(m.get('content'), str)]
        self.assertEqual(contents[0], 'Fictional persona')
        self.assertIn('LAN\'S OWN SETTINGS', contents[1])
        self.assertTrue(any('RELATIONSHIP GROWTH' in c and 'Anger needs repair' in c for c in contents))
        self.assertTrue(any('IMPORTANT FACTS' in c and 'Lan likes blue.' in c for c in contents))
        self.assertTrue(any('ONGOING' in c and 'Lan is angry' in c for c in contents))
        ongoing_index = next(i for i, m in enumerate(messages) if 'ONGOING' in str(m.get('content')))
        user_index = next(i for i, m in enumerate(messages) if m.get('role') == 'user')
        self.assertLess(ongoing_index, user_index)

    def test_persona_reload_applies_next_request(self):
        chat = self.chat()
        chat.send('hello')
        (self.root / 'persona' / 'test.md').write_text('A new persona', encoding='utf-8')
        chat.reload_persona()
        self.assertEqual(chat.cfg.persona, 'A new persona')
        chat.send('again')
        self.assertEqual(self.prompts[-1][0][0]['content'], 'A new persona')

    def test_tool_call_identity_distinguishes_a_retry_from_a_new_todo(self):
        chat = self.chat()

        def run(call_id):
            call = SimpleNamespace(
                id=call_id,
                function=SimpleNamespace(
                    name='ongoing_update',
                    arguments=json.dumps({'action': 'add', 'text': 'Buy stamps.'}),
                ),
            )
            return chat._execute_tool_call(call, 'req-1', f'0:{call_id}', False, [ONGOING_TOOL])

        first = run('call-a')
        retry = run('call-a')
        second = run('call-b')
        self.assertEqual('成功', first.state)
        self.assertEqual(first.result, retry.result)
        self.assertEqual('成功', second.state)
        self.assertNotEqual(first.result, second.result)
        active = [row for row in chat.profile.read()['ongoing']['items'] if row['status'] == 'active']
        self.assertEqual(2, len(active))
        self.assertEqual({'Buy stamps.'}, {row['text'] for row in active})
