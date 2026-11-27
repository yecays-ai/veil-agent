"""Ticket 400: the instruction registry stays the only place prompt text can come from."""
import tempfile
import unittest
from pathlib import Path

from veil import prompts
import veil.prompts.inline as inline
from veil.prompts.store import PromptStore


class ScanCoverageTests(unittest.TestCase):
    """Ticket 405 review: a module that builds model text must never fall outside the scan."""

    PROBE = (
        'import prompts\n\n'
        'PROSE = ("This sentence is written straight into a model module. It is long enough to look "\n'
        '         "like prompt text, so the scanner has to catch it before it ships.")\n'
    )

    @staticmethod
    def _all_source_files():
        files = list(inline.ROOT.glob('*.py'))
        veil_dir = inline.ROOT / 'veil'
        if veil_dir.is_dir():
            files.extend(path for path in veil_dir.rglob('*.py') if path.name != '__init__.py')
        return files

    def test_every_module_that_imports_prompts_is_scanned(self):
        importing = {path.relative_to(inline.ROOT).as_posix() for path in self._all_source_files()
                     if inline._imports_prompts(path)}
        self.assertTrue(importing)
        self.assertIn('veil/prompts/instructions.py', importing)
        self.assertEqual(sorted(importing - set(inline.modules())), [])

    def test_every_module_that_builds_requests_is_scanned(self):
        builders = {path.relative_to(inline.ROOT).as_posix() for path in self._all_source_files()
                    if inline.builds_requests(path)}
        self.assertTrue(builders)
        self.assertEqual(sorted(builders - set(inline.modules())), [],
                         'a module builds model messages but is not scanned')

    def test_request_building_module_without_prompts_import_is_flagged(self):
        probe_text = (
            'PROSE = ("This text is written into a module that never imports prompts. It builds the "\n'
            '         "request by hand, so only the request shape can reveal it to the scanner.")\n\n'
            'def turn(user_text):\n'
            '    return [{"role": "system", "content": PROSE},\n'
            '            {"role": "user", "content": user_text}]\n'
        )
        with tempfile.TemporaryDirectory(dir=inline.ROOT) as tmp:
            probe = Path(tmp) / 'probe_builder.py'
            probe.write_text(probe_text, encoding='utf-8')
            self.assertFalse(inline._imports_prompts(probe))
            self.assertTrue(inline.builds_requests(probe))
            name = probe.relative_to(inline.ROOT).as_posix()
            self.assertNotIn(name, inline.modules(),
                             'an unscanned request builder would slip through')

    def test_new_prose_in_a_model_module_is_caught(self):
        with tempfile.TemporaryDirectory(dir=inline.ROOT) as tmp:
            probe = Path(tmp) / 'probe_module.py'
            probe.write_text(self.PROBE, encoding='utf-8')
            rows = inline.collect([probe.relative_to(inline.ROOT).as_posix()])
        self.assertTrue(rows, 'scanner did not see the probe module')
        self.assertTrue(all(row['hash'] not in inline.feedback() for row in rows))


class PlaceholderTests(unittest.TestCase):
    """Ticket 405 review: required placeholders carry real data into the request."""

    def test_save_refuses_missing_required_placeholder(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = PromptStore(prompts.DEFAULTS, tmp)
            with self.assertRaises(Exception) as caught:
                store.save('life.time_gap', 'It has been a long time since her last message.')
            message = str(caught.exception)
            self.assertIn('{duration}', message)
            self.assertIn('{when}', message)
            self.assertFalse(store.overridden('life.time_gap'))

    def test_wording_change_keeping_placeholders_saves(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = PromptStore(prompts.DEFAULTS, tmp)
            saved = store.save('life.time_gap', 'She last wrote {when}, about {duration} ago.')
            self.assertTrue(saved['overridden'])
            self.assertEqual(store.text('life.time_gap'), 'She last wrote {when}, about {duration} ago.')

    def test_entry_without_placeholders_still_edits(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = PromptStore(prompts.DEFAULTS, tmp)
            saved = store.save('life.time_guidance', 'Only one short line.')
            self.assertEqual(saved['text'], 'Only one short line.')

    def test_registry_exposes_placeholders(self):
        self.assertEqual(prompts.entry('life.time_gap').placeholders, ('duration', 'when'))
        self.assertEqual(prompts.entry('life.time_guidance').placeholders, ())
        rows = {entry.id: entry.as_dict() for group in prompts.registry.by_category()
                for entry in group['entries']}
        self.assertEqual(rows['autonomy.budget.line']['placeholders'],
                         ['used', 'pending', 'left', 'cap'])

    def test_edited_guidance_reaches_the_next_gap_text(self):
        from datetime import datetime, timedelta, timezone
        from veil.life import life_state
        with tempfile.TemporaryDirectory() as tmp:
            old = prompts._store
            try:
                prompts._store = PromptStore(prompts.DEFAULTS, tmp)
                prompts._store.save('life.time_guidance', 'Edited guidance for the next request.')
                stamp = datetime(2026, 11, 20, 17, 0, tzinfo=timezone(timedelta(hours=8)))
                rows = [{'role': 'user', 'time': stamp - timedelta(hours=13),
                         'content': 'hello', 'message_id': 'm1'}]
                rows[0]['time'] = rows[0]['time'].isoformat()
                text = life_state.gap_text(rows, stamp.isoformat(), minimum=0)
                self.assertIn('Edited guidance for the next request.', text)
                self.assertNotIn('Her silence may mean busy, asleep, or upset.', text)
            finally:
                prompts._store = old


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
        from veil.prompts import instructions
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
        from veil.prompts import instructions
        with tempfile.TemporaryDirectory() as tmp:
            old = prompts._store
            try:
                prompts._store = PromptStore(prompts.DEFAULTS, tmp)
                with self.assertRaises(Exception):
                    instructions.save('memory.recalled', 'x')
            finally:
                prompts._store = old

    def test_tool_description_is_read_per_request(self):
        from veil.core import veil_agent
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
        from veil.prompts import instructions
        from veil.core.veil_agent import load_character
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / 'characters' / 'test').mkdir(parents=True)
            (root / 'persona').mkdir()
            (root / 'persona' / 'test.md').write_text('Fictional persona body', encoding='utf-8')
            (root / 'characters' / 'test' / 'character.toml').write_text(self.CONFIG, encoding='utf-8')
            cfg = load_character(root, 'test')
            old_store = prompts._store
            prompts._store = PromptStore(prompts.DEFAULTS, root / 'instructions')
            try:
                data = instructions.page()
            finally:
                prompts._store = old_store
            groups = {group['id']: group for group in data['categories']}
            self.assertEqual(len(groups), 8)
            self.assertEqual(data['maintenance']['overridden'], 0)
            self.assertEqual(data['maintenance']['default_changed'], 0)
            self.assertEqual(data['maintenance']['entries'], len(prompts.entries()))
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


class MaintenanceTests(unittest.TestCase):
    def test_default_change_is_visible_while_the_override_wins(self):
        with tempfile.TemporaryDirectory() as tmp:
            defaults = Path(tmp) / 'defaults'
            defaults.mkdir()
            (defaults / 'chat.stay_silent.md').write_text('Old default wording.', encoding='utf-8')
            store = PromptStore(defaults, Path(tmp) / 'overrides')
            store.save('chat.stay_silent', 'Lan wrote this instead.')
            status = store.status('chat.stay_silent')
            self.assertEqual(status['text'], 'Lan wrote this instead.')
            self.assertTrue(status['overridden'])
            self.assertFalse(status['default_changed'])
            (defaults / 'chat.stay_silent.md').write_text('A later default wording.', encoding='utf-8')
            status = store.status('chat.stay_silent')
            self.assertEqual(status['text'], 'Lan wrote this instead.')
            self.assertTrue(status['default_changed'])

    def test_save_succeeds_when_the_default_fingerprint_cannot_be_read(self):
        with tempfile.TemporaryDirectory() as tmp:
            defaults = Path(tmp) / 'defaults'
            defaults.mkdir()
            store = PromptStore(defaults, Path(tmp) / 'overrides')
            # No default file for this entry: the override must still save, and status must not raise.
            status = store.save('chat.stay_silent', 'Saved even without a baseline.')
            self.assertEqual(status['text'], 'Saved even without a baseline.')
            self.assertTrue((Path(tmp) / 'overrides' / 'chat.stay_silent.md').exists())
            again = store.status('chat.stay_silent')
            self.assertEqual(again['text'], 'Saved even without a baseline.')
            self.assertFalse(again['default_changed'])

    def test_existing_override_gets_a_baseline_without_rewriting_it(self):
        with tempfile.TemporaryDirectory() as tmp:
            defaults = Path(tmp) / 'defaults'
            overrides = Path(tmp) / 'overrides'
            defaults.mkdir()
            overrides.mkdir()
            (defaults / 'chat.stay_silent.md').write_text('Default A.', encoding='utf-8')
            original = overrides / 'chat.stay_silent.md'
            original.write_text('Text Lan saved before fingerprints existed.\n', encoding='utf-8')
            store = PromptStore(defaults, overrides)
            status = store.status('chat.stay_silent')
            self.assertEqual(status['text'], 'Text Lan saved before fingerprints existed.')
            self.assertFalse(status['default_changed'])
            self.assertTrue((overrides / '.base.json').exists())
            self.assertEqual(original.read_text(encoding='utf-8').strip(),
                             'Text Lan saved before fingerprints existed.')
            (defaults / 'chat.stay_silent.md').write_text('Default B, shipped later.', encoding='utf-8')
            later = store.status('chat.stay_silent')
            self.assertTrue(later['default_changed'])
            self.assertEqual(later['text'], 'Text Lan saved before fingerprints existed.')

    def test_overrides_live_outside_the_repository_work(self):
        root = Path(__file__).resolve().parents[1]
        ignore = (root / '.gitignore').read_text(encoding='utf-8')
        self.assertIn('persona/instructions/', ignore)
        updater = (root / 'update-main.cmd').read_text(encoding='utf-8', errors='replace')
        self.assertNotIn('persona/instructions', updater)

    def test_git_really_ignores_the_override_file(self):
        import subprocess
        root = Path(__file__).resolve().parents[1]
        if not (root / '.git').exists() and not (root / '.git').is_file():
            self.skipTest('not a git checkout')
        result = subprocess.run(['git', '-C', str(root), 'check-ignore', 'persona/instructions/chat.send_message.md'],
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)


class InlineProseTests(unittest.TestCase):
    def test_synthetic_english_prompt_is_flagged(self):
        source = (
            'PROMPT = ("Write like texting and never explain yourself. "\n'
            '          "Keep every answer short and always ask one question at the end.")\n'
        )
        rows = inline.collect_text(source)
        self.assertTrue(rows)
        self.assertNotIn(rows[0]['hash'], inline.feedback())

    def test_synthetic_chinese_prompt_is_flagged(self):
        source = 'PROMPT = "和他说话的时候，要记得先停一下，不要急着回答，也不要替他决定任何事情，除非他明确问你怎么做。"\n'
        rows = inline.collect_text(source)
        self.assertTrue(rows)
        self.assertNotIn(rows[0]['hash'], inline.feedback())

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
        from veil.prompts import instructions
        from veil.prompts.tools import ToolText
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

    def test_tool_override_cache_sees_edits_to_any_file(self):
        from veil.prompts.tools import ToolText
        with tempfile.TemporaryDirectory() as tmp:
            tools = ToolText(tmp)
            tools.save('read_text_file', 'first read text')
            tools.save('write_file', 'first write text')
            tools.save('read_text_file', 'second read text')
            self.assertEqual(tools.text('read_text_file', ''), 'second read text')
            self.assertEqual(tools.text('write_file', ''), 'first write text')

    def test_alias_tool_rows_use_the_name_the_model_sees(self):
        from veil.prompts import instructions
        from veil.prompts.tools import ToolText

        class AliasChat(self.FakeChat):
            def tool_schemas(self, full=False):
                return [{'type': 'function', 'function': {'name': 'lookup', 'description': 'Search text.'}}]

            def canonical_tool_name(self, name):
                return 'mcp__service__search'

        with tempfile.TemporaryDirectory() as tmp:
            old = prompts._tools
            try:
                prompts._tools = ToolText(tmp)
                rows = instructions._tool_rows(AliasChat('Search text.'))
                self.assertEqual(rows[0]['id'], 'tool.lookup')
                prompts.save_tool('lookup', 'Edited by Lan.')
                rows = instructions._tool_rows(AliasChat('Search text.'))
                self.assertEqual(rows[0]['text'], 'Edited by Lan.')
                self.assertTrue(rows[0]['overridden'])
            finally:
                prompts._tools = old

    def test_save_tool_does_not_touch_registered_text(self):
        from veil.prompts import instructions
        from veil.prompts.tools import ToolText
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
