
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

    def test_finished_items_are_not_injected_but_their_count_is(self):
        # Ticket 413: archived is gone; finished items leave the context but he is told how many exist.
        self.ctx.invoke('ongoing_update', {'action': 'add', 'text': 'unfinished'})
        item_id = self.ctx.read()['ongoing']['items'][0]['id']
        self.ctx.invoke('ongoing_update', {'action': 'complete', 'id': item_id})
        dynamic = self.ctx.dynamic_context()
        self.assertNotIn('unfinished', dynamic)
        self.assertIn('1 finished item(s)', dynamic)
        self.assertIn('ongoing_update', dynamic)
        self.ctx.invoke('ongoing_update', {'action': 'reopen', 'id': item_id})
        reopened = self.ctx.dynamic_context()
        self.assertIn('unfinished', reopened)
        self.assertNotIn('finished item(s)', reopened)

    def test_archive_action_is_gone(self):
        self.ctx.invoke('ongoing_update', {'action': 'add', 'text': 'still open'})
        item_id = self.ctx.read()['ongoing']['items'][0]['id']
        text, state, _ = self.ctx.invoke('ongoing_update', {'action': 'archive', 'id': item_id})
        self.assertEqual(state, '被拒绝')
        self.assertIn('complete', text)
        self.assertEqual(self.ctx.read()['ongoing']['items'][0]['status'], 'active')

    def test_list_returns_finished_items_with_their_completion_time(self):
        self.ctx.invoke('ongoing_update', {'action': 'add', 'text': 'argument about the desk'})
        item_id = self.ctx.read()['ongoing']['items'][0]['id']
        self.ctx.invoke('ongoing_update', {'action': 'complete', 'id': item_id})
        text, state, _ = self.ctx.invoke('ongoing_update', {'action': 'list'})
        self.assertEqual(state, '成功')
        self.assertIn(item_id, text)
        self.assertIn('argument about the desk', text)
        self.assertIn('completed ', text)
        active, _, _ = self.ctx.invoke('ongoing_update', {'action': 'list', 'status': 'active'})
        self.assertIn('No active', active)

    def test_old_archived_entries_read_as_completed(self):
        # Ticket 413 removed the archived state. Old files must not become unreadable.
        path = self.root / 'test' / 'learned-context.json'
        self.ctx.read()
        data = json.loads(path.read_text(encoding='utf-8'))
        data['ongoing']['items'] = [{'id': 'old1', 'text': 'old archived thing', 'status': 'archived',
                                     'sources': [], 'updated_at': '2026-11-21T08:20:55+08:00'}]
        path.write_text(json.dumps(data, ensure_ascii=False), encoding='utf-8')
        item = self.ctx.read()['ongoing']['items'][0]
        self.assertEqual(item['status'], 'completed')
        self.assertIn('2026-11-21', item['completed_at'])
        dynamic = self.ctx.dynamic_context()
        self.assertNotIn('old archived thing', dynamic)
        self.assertIn('1 finished item(s)', dynamic)

    def test_finished_items_do_not_use_the_shared_budget(self):
        # Ticket 413: what the model never sees must not block new ongoing items.
        small = LearnedContext(self.root, 'test', {
            'file': 'learned-context.json', 'legacy_file': 'about-lan.md', 'max_bytes': 100})
        small.invoke('ongoing_update', {'action': 'add', 'text': 'a' * 40})
        item_id = small.read()['ongoing']['items'][0]['id']
        small.invoke('ongoing_update', {'action': 'complete', 'id': item_id})
        result, state, _ = small.invoke('ongoing_update', {'action': 'add', 'text': 'b' * 40})
        self.assertEqual(state, '成功', result)

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
