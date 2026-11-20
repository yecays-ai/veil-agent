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

    def test_save_instruction_replaces_whole_text(self):
        import instructions
        with tempfile.TemporaryDirectory() as tmp:
            old = prompts._store
            try:
                prompts._store = PromptStore(prompts.DEFAULTS, tmp)
                row = instructions.save('chat.stay_silent', 'A much shorter line.')
                self.assertEqual(row['text'], 'A much shorter line.')
                self.assertTrue(row['overridden'])
                self.assertEqual(instructions.page()['categories'][0]['entries'][0]['kind'], 'text')
            finally:
                prompts._store = old

    def test_save_instruction_refuses_readonly_entry(self):
        import instructions
        with tempfile.TemporaryDirectory() as tmp:
            old = prompts._store
            try:
                prompts._store = PromptStore(prompts.DEFAULTS, tmp)
                with self.assertRaises(Exception):
                    instructions.save('memory.recalled', 'x')
            finally:
                prompts._store = old

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
            data = instructions.page()
            groups = {group['id']: group for group in data['categories']}
            self.assertEqual(len(groups), 8)
            rows = {row['id']: row for group in data['categories'] for row in group['entries']}
            self.assertEqual(len(rows), len(prompts.entries()))
            # Persona, background, samples and notes have their own pages; never listed twice here.
            for absent in ('identity.persona', 'identity.user', 'background.library',
                           'voice.samples', 'memory.notes'):
                self.assertNotIn(absent, rows)
            self.assertTrue(all(row['kind'] in ('text', 'marker') for row in rows.values()))
            for row in rows.values():
                if row['kind'] == 'text':
                    self.assertTrue(row['text'].strip(), row['id'])
                if row['kind'] == 'marker':
                    self.assertEqual(row['text'], '')
                    self.assertFalse(row['editable'])


class InlineProseTests(unittest.TestCase):
    def test_no_unreviewed_inline_prose(self):
        allowed = inline.feedback()
        unknown = [row for row in inline.collect() if row['hash'] not in allowed]
        self.assertEqual(unknown, [], (
            'Prompt prose was written directly into code again. Move it to prompts/defaults and call '
            'prompts.text(<id>), or give it a reason in prompts/inline_feedback_allowlist.txt. '
            + ', '.join(row['file'] + ':' + str(row['line']) + ' ' + row['preview'] for row in unknown)))

    def test_feedback_list_is_reviewed_and_shrinks(self):
        allowed = inline.feedback()
        self.assertLessEqual(len(allowed), inline.BASELINE, (
            'The feedback list may only shrink. Raising inline.BASELINE is a reviewed decision.'))
        for digest, reason in allowed.items():
            self.assertTrue(reason.startswith(inline.REASONS), digest + ' has no typed reason: ' + reason)
        present = {row['hash'] for row in inline.collect()}
        stale = [digest for digest in allowed if digest not in present]
        self.assertEqual(stale, [], 'feedback list has stale entries; run python -m prompts.inline --update')


class ToolRowTests(unittest.TestCase):
    class FakeChat:
        def __init__(self, description):
            self.description = description

        def tool_schemas(self, full=False):
            return [{'type': 'function', 'function': {'name': 'windows_list', 'description': self.description}}]

        def canonical_tool_name(self, name):
            return name

    def test_tool_rows_come_from_the_tool_list_and_are_editable(self):
        import instructions
        from prompts.tools import ToolText
        with tempfile.TemporaryDirectory() as tmp:
            old = prompts._tools
            try:
                prompts._tools = ToolText(tmp)
                rows = instructions._tool_rows(self.FakeChat('Original tool text.'))
                self.assertEqual(len(rows), 1)
                row = rows[0]
                self.assertEqual(row['id'], 'tool.windows_list')
                self.assertEqual(row['kind_label'], '工具说明')
                self.assertTrue(row['editable'])
                self.assertFalse(row['overridden'])
                prompts.save_tool('windows_list', 'Edited tool text.')
                rows = instructions._tool_rows(self.FakeChat('Original tool text.'))
                self.assertEqual(rows[0]['text'], 'Edited tool text.')
                self.assertTrue(rows[0]['overridden'])
            finally:
                prompts._tools = old

    def test_save_tool_does_not_touch_registered_text(self):
        import instructions
        from prompts.tools import ToolText
        with tempfile.TemporaryDirectory() as tmp:
            old = prompts._tools
            try:
                prompts._tools = ToolText(tmp)
                self.assertIsNone(instructions.save('tool.windows_list', 'Changed.'))
                self.assertEqual(prompts.tool_text('windows_list', 'default'), 'Changed.')
                with self.assertRaises(Exception):
                    instructions.save('tool.', 'x')
            finally:
                prompts._tools = old


if __name__ == '__main__':
    unittest.main()
