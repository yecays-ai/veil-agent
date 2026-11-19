
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock

from veil_agent import Budget, Chat, Store, UserError, load_character
from web_agent import Hub


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


class PeterPageTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        (self.root / 'characters' / 'test').mkdir(parents=True)
        (self.root / 'persona').mkdir()
        (self.root / 'persona' / 'test.md').write_text('Old persona', encoding='utf-8')
        (self.root / 'persona' / 'lan.md').write_text('Lan is a designer.', encoding='utf-8')
        (self.root / 'characters' / 'test' / 'character.toml').write_text(CONFIG, encoding='utf-8')
        cfg = load_character(self.root, 'test')
        store = Store(self.root / 'data')
        (self.root / 'data' / 'test').mkdir(parents=True, exist_ok=True)
        (self.root / 'data' / 'test' / 'about-lan.md').write_text('Lan likes blue.\n', encoding='utf-8')
        chat = Chat(cfg, store, Budget(store), Mock(return_value=('ok', 1, 1, 0)), output=lambda _: None)
        self.hub = Hub.__new__(Hub)
        self.hub.chat = chat

    def test_page_data_and_edits(self):
        data = Hub.peter_page(self.hub)
        self.assertEqual(data['persona']['text'], 'Old persona')
        self.assertEqual(data['user_persona']['text'], 'Lan is a designer.')
        self.assertEqual(data['learned']['sections']['facts']['text'], 'Lan likes blue.\n')
        saved = Hub.save_peter_page(self.hub, {'action': 'save_learned', 'section': 'growth',
                                               'text': 'Repair first, explain later.'})
        self.assertIn('Repair first', saved['learned']['sections']['growth']['text'])
        saved = Hub.save_peter_page(self.hub, {'action': 'save_persona', 'text': 'New persona'})
        self.assertEqual(saved['persona']['text'], 'New persona')
        self.assertEqual(self.hub.chat.cfg.persona, 'New persona')
        saved = Hub.save_peter_page(self.hub, {'action': 'save_user_persona', 'text': 'Lan is a painter.'})
        self.assertEqual(saved['user_persona']['text'], 'Lan is a painter.')

    def test_over_budget_and_empty_persona_are_rejected(self):
        with self.assertRaises(UserError):
            Hub.save_peter_page(self.hub, {'action': 'save_learned', 'section': 'facts', 'text': 'x' * 5000})
        with self.assertRaises(UserError):
            Hub.save_peter_page(self.hub, {'action': 'save_persona', 'text': '   '})

    def test_restore_persona_version(self):
        Hub.save_peter_page(self.hub, {'action': 'save_persona', 'text': 'Second persona'})
        data = Hub.peter_page(self.hub)
        version = data['persona']['versions'][0]['name']
        restored = Hub.save_peter_page(self.hub, {'action': 'restore', 'section': 'persona', 'version': version})
        self.assertIn(restored['persona']['text'], ('Old persona', 'Second persona'))
