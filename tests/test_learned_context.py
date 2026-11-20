
import json
import tempfile
import unittest
from pathlib import Path

from agent_types import UserError
from learned_context import LearnedContext


class LearnedContextTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.legacy = "Lan likes blue.\n\nShe ships orders herself.\n"
        (self.root / 'test').mkdir(parents=True)
        (self.root / 'test' / 'about-lan.md').write_text(self.legacy, encoding='utf-8')
        self.ctx = LearnedContext(self.root, 'test', {
            'file': 'learned-context.json', 'legacy_file': 'about-lan.md', 'max_bytes': 4000})

    def test_migration_keeps_legacy_bytes_and_starts_three_sections(self):
        data = self.ctx.read()
        self.assertEqual(data['facts']['text'], self.legacy)
        self.assertEqual(data['growth']['text'], '')
        self.assertEqual(data['ongoing']['items'], [])
        self.assertTrue((self.root / 'test' / 'learned-context.json').exists())

    def test_append_and_replace_report_a_three_value_success(self):
        # Ticket 409: the save path returned a 1-tuple, so the caller's three-value unpack
        # raised and every successful save was reported as invalid JSON.
        result, state, name = self.ctx.invoke(
            'profile_append', {'text': 'She hates being managed.', 'section': 'growth'})
        self.assertEqual((state, name), ('成功', 'profile_append'))
        self.assertIn('Saved.', result)

        result, state, name = self.ctx.invoke(
            'profile_replace', {'old_text': 'She hates being managed.',
                                'new_text': 'She hates being managed, not being cared for.',
                                'section': 'growth'})
        self.assertEqual((state, name), ('成功', 'profile_replace'))
        self.assertIn('Saved.', result)
        self.assertIn('not being cared for', self.ctx.read()['growth']['text'])

    def test_stable_and_dynamic_blocks(self):
        self.ctx.invoke('profile_append', {'text': 'When she is angry, do not explain like a report.',
                                           'section': 'growth'})
        self.ctx.invoke('ongoing_update', {'action': 'add', 'text': 'We argued about the desk; she has not replied.',
                                           'sources': ['chat:m1']})
        stable = self.ctx.context()
        self.assertIn('RELATIONSHIP GROWTH', stable)
        self.assertIn('do not explain', stable)
        self.assertIn('IMPORTANT FACTS', stable)
        self.assertIn('Lan likes blue.', stable)
        dynamic = self.ctx.dynamic_context()
        self.assertIn('We argued about the desk', dynamic)
        self.assertNotIn('We argued about the desk', stable)
        self.assertEqual(self.ctx.status()['ongoing']['items'][0]['sources'], ['chat:m1'])

    def test_completed_and_archived_are_not_injected(self):
        added = self.ctx.invoke('ongoing_update', {'action': 'add', 'text': 'unfinished'})[0]
        item_id = self.ctx.read()['ongoing']['items'][0]['id']
        self.ctx.invoke('ongoing_update', {'action': 'complete', 'id': item_id})
        self.assertIsNone(self.ctx.dynamic_context())
        self.ctx.invoke('ongoing_update', {'action': 'reopen', 'id': item_id})
        self.assertIn('unfinished', self.ctx.dynamic_context())
        self.ctx.invoke('ongoing_update', {'action': 'archive', 'id': item_id})
        self.assertIsNone(self.ctx.dynamic_context())

    def test_shared_budget_rejects_without_changing(self):
        small = LearnedContext(self.root, 'test', {
            'file': 'learned-context.json', 'legacy_file': 'about-lan.md', 'max_bytes': 60})
        result, state, _ = small.invoke('profile_append', {'text': 'x' * 60, 'section': 'growth'})
        self.assertEqual(state, '被拒绝')
        self.assertIn('shared limit', result)
        self.assertEqual(small.read()['growth']['text'], '')

    def test_replace_only_within_section(self):
        self.ctx.invoke('profile_append', {'text': 'growth only', 'section': 'growth'})
        result, state, _ = self.ctx.invoke('profile_replace', {
            'old_text': 'Lan likes blue.', 'new_text': 'x', 'section': 'growth'})
        self.assertEqual(state, '被拒绝')
        self.assertIn('0 times', result)

    def test_user_save_wins_and_detects_stale(self):
        before = self.ctx.status()['sections']['facts']['updated_at']
        self.ctx.save_section('facts', 'Lan likes green.')
        with self.assertRaises(UserError):
            self.ctx.save_section('facts', 'late edit', expected_updated_at=before)
        self.assertEqual(self.ctx.read()['facts']['text'], 'Lan likes green.')
        self.ctx.save_section('facts', 'forced edit', expected_updated_at=before, force=True)
        self.assertEqual(self.ctx.read()['facts']['text'], 'forced edit')

    def test_corrupt_file_falls_back_and_logs(self):
        path = self.root / 'test' / 'learned-context.json'
        path.write_text('{not json', encoding='utf-8')
        self.assertIn('Lan likes blue.', self.ctx.context())
        self.assertFalse(self.ctx.status()['available'])
        self.assertTrue((self.root / 'test' / 'peter-page-errors.jsonl').exists())

    def test_empty_ongoing_text_is_rejected(self):
        _, state, _ = self.ctx.invoke('ongoing_update', {'action': 'add', 'text': '  '})
        self.assertEqual(state, '被拒绝')
