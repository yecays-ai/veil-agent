"""Ticket 400: the instruction registry stays the only place prompt text can come from."""
import tempfile
import unittest
from pathlib import Path

import prompts
import prompts.inline as inline
from prompts.store import PromptStore


class RegistryTests(unittest.TestCase):
    def test_registry_is_valid(self):
        self.assertTrue(prompts.validate())

    def test_every_text_entry_has_text(self):
        for entry in prompts.entries():
            if entry.kind == 'text':
                self.assertTrue(prompts.text(entry.id).strip(), entry.id)

    def test_categories_and_ids_are_unique(self):
        ids = [entry.id for entry in prompts.entries()]
        self.assertEqual(len(ids), len(set(ids)))
        for group in prompts.registry.by_category():
            self.assertTrue(group['entries'], group['id'])

    def test_override_wins_whole_and_can_be_replaced(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = PromptStore(prompts.DEFAULTS, tmp)
            original = store.text('chat.send_message')
            saved = store.save('chat.send_message', 'First edit.')
            self.assertEqual(saved['text'], 'First edit.')
            self.assertTrue(saved['overridden'])
            store.save('chat.send_message', 'Second edit, replacing the first.')
            self.assertEqual(store.text('chat.send_message'), 'Second edit, replacing the first.')
            self.assertNotEqual(store.text('chat.send_message'), original)

    def test_save_refuses_unknown_or_oversize(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = PromptStore(prompts.DEFAULTS, tmp)
            with self.assertRaises(KeyError):
                store.save('no.such.entry', 'x')
            with self.assertRaises(Exception):
                store.save('chat.stay_silent', 'x' * 100000)

    def test_broken_override_falls_back_to_default(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = PromptStore(prompts.DEFAULTS, tmp)
            path = Path(tmp) / 'chat.send_message.md'
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b'\xff\xfe broken')
            self.assertEqual(store.text('chat.send_message'), store.default('chat.send_message'))

    def test_tool_description_is_read_per_request(self):
        import veil_agent
        with tempfile.TemporaryDirectory() as tmp:
            old = prompts._store
            try:
                prompts._store = PromptStore(prompts.DEFAULTS, tmp)
                prompts._store.save('chat.send_message', 'Edited tool wording.')
                schema = veil_agent.send_message_schema()
                self.assertEqual(schema['function']['description'], 'Edited tool wording.')
            finally:
                prompts._store = old


class InstructionsPageTests(unittest.TestCase):
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

    def test_page_lists_every_entry_with_text(self):
        import instructions
        from veil_agent import load_character
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / 'characters' / 'test').mkdir(parents=True)
            (root / 'persona').mkdir()
            (root / 'persona' / 'test.md').write_text('Fictional persona body', encoding='utf-8')
            (root / 'characters' / 'test' / 'character.toml').write_text(self.CONFIG, encoding='utf-8')
            cfg = load_character(root, 'test')
            data = instructions.page(cfg)
            groups = {group['id']: group for group in data['categories']}
            self.assertEqual(len(groups), 8)
            rows = {row['id']: row for group in data['categories'] for row in group['entries']}
            self.assertEqual(len(rows), len(prompts.entries()))
            self.assertEqual(rows['identity.persona']['text'], 'Fictional persona body')
            for row in rows.values():
                if row['kind'] == 'text':
                    self.assertTrue(row['text'].strip(), row['id'])
                if row['kind'] == 'marker':
                    self.assertEqual(row['text'], '')
                    self.assertFalse(row['editable'])


class InlineProseTests(unittest.TestCase):
    def test_no_new_inline_prompt_text(self):
        allowed = inline.allowlist()
        found = inline.collect()
        unknown = [row for row in found if row['hash'] not in allowed]
        self.assertEqual(unknown, [], (
            'New prompt text was written directly into code. Move it to prompts/defaults and reference '
            'its id (preferred), or, if it must stay, run "python -m prompts.inline --update" and explain '
            'in the allowlist. Unregistered: '
            + ', '.join(row['file'] + ':' + str(row['line']) + ' ' + row['preview'] for row in unknown)))


if __name__ == '__main__':
    unittest.main()