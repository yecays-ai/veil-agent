import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from veil.brain.learned_context import LearnedContext


class SovereigntyTests(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.profile = LearnedContext(Path(self.tmp.name), 'test', {'file': 'learned.json'})

    def test_relationship_correction_blocks_cross_section_replace_and_compound_append(self):
        wrong = 'Open the balcony door every morning before breakfast.'
        right = 'Do not open the balcony door before breakfast.'
        self.profile.invoke('profile_append', {'section': 'facts', 'text': 'Spare note.'})
        self.profile.save_relationship([{'id': 'r', 'text': wrong, 'status': 'confirmed'}])
        self.profile.save_relationship([{'id': 'r', 'text': right, 'status': 'confirmed'}])
        for section in ('facts', 'growth', 'relationship', 'ongoing'):
            with self.subTest(section=section):
                self.assertEqual(self.profile.submit_candidate(section, wrong)[1], '被拒绝')
        self.assertEqual(self.profile.invoke('profile_append', {
            'section': 'growth', 'text': 'Another note. ' + wrong,
        })[1], '被拒绝')
        self.assertEqual(self.profile.invoke('profile_replace', {
            'section': 'facts', 'old_text': 'Spare note.', 'new_text': wrong,
        })[1], '被拒绝')

    def test_lan_confirmed_unlocked_is_not_replaced_or_retired(self):
        text = 'The balcony is closed in the evenings.'
        self.profile.save_relationship([{'id': 'r', 'text': text, 'status': 'confirmed'}])
        for replacement in ('', 'The balcony is open at night.'):
            with self.subTest(replacement=replacement):
                self.assertEqual(self.profile.invoke('profile_replace', {
                    'section': 'relationship', 'old_text': text, 'new_text': replacement,
                })[1], '被拒绝')
        self.assertEqual(self.profile.read()['relationship']['items'][0]['text'], text)

    def test_lan_facts_edit_remains_authoritative(self):
        old = 'Lan accepts phone calls during long work meetings.'
        self.profile.invoke('profile_append', {'section': 'facts', 'text': old})
        self.profile.save_section('facts', 'Lan does not take calls at work.')
        self.assertEqual(self.profile.submit_candidate('growth', old)[1], '被拒绝')
        self.assertEqual(self.profile.invoke('profile_replace', {
            'section': 'facts', 'old_text': 'not ', 'new_text': '',
        })[1], '被拒绝')

    def test_veto_remains_after_more_than_200_subsequent_deletions(self):
        first = 'Keep outdoor lights on after midnight.'
        self.profile.save_relationship([{'id': 'first', 'text': first, 'status': 'confirmed'}])
        self.profile.save_relationship([])
        for group in range(5):
            self.profile.save_relationship([
                {'id': str(i), 'text': f'entry label {i}', 'status': 'confirmed'}
                for i in range(41 * group, 41 * (group + 1))
            ])
            self.profile.save_relationship([])
        new_instance = LearnedContext(Path(self.tmp.name), 'test', {'file': 'learned.json'})
        self.assertEqual(new_instance.submit_candidate('relationship', first)[1], '被拒绝')

    def test_ongoing_deleted_or_user_completed_cannot_be_revived(self):
        text = 'Return the library book by Saturday.'
        self.profile.invoke('ongoing_update', {'action': 'add', 'text': text})
        item = self.profile.read()['ongoing']['items'][0]
        self.profile.save_ongoing([{**item, 'status': 'completed'}])
        self.assertEqual(self.profile.invoke('ongoing_update', {
            'action': 'reopen', 'id': item['id'],
        })[1], '被拒绝')
        self.profile.save_ongoing([])
        self.assertEqual(self.profile.invoke('ongoing_update', {
            'action': 'add', 'text': text,
        })[1], '被拒绝')
