import json
import random
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

from thoughts import ThoughtIndex


class Clock:
    def __init__(self):
        self.value = datetime(2026, 10, 29, 9, 0, tzinfo=timezone(timedelta(hours=8)))

    def __call__(self):
        return self.value

    def advance(self, **kwargs):
        self.value += timedelta(**kwargs)


class ThoughtIndexTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.clock = Clock()
        self.index = ThoughtIndex(SimpleNamespace(root=Path(self.temp.name)), 'peter',
                                  clock=self.clock, rng=random.Random(1))

    def note(self, text, **extra):
        result, state, _ = self.index.invoke({'action': 'note', 'text': text, **extra})
        self.assertEqual(state, '成功', result)
        return result.split()[2].rstrip(':')

    def saved(self):
        return json.loads((Path(self.temp.name) / 'peter' / 'thoughts.json').read_text(encoding='utf-8'))['thoughts']

    def test_passing_thought_fades_but_one_he_returns_to_stays(self):
        fleeting = self.note('Why do pigeons bob their heads?')
        lasting = self.note('Build a tiny web-slinging physics toy', memory_id='mem-1')
        for _ in range(2):
            self.clock.advance(days=1)
            self.assertEqual(self.index.invoke({'action': 'touch', 'thought_id': lasting})[1], '成功')
        self.assertEqual({item['id']: item['state'] for item in self.saved()}[lasting], 'kept')
        self.clock.advance(days=10)
        listing, state, _ = self.index.invoke({'action': 'list'})
        self.assertEqual(state, '成功')
        self.assertIn(lasting, listing)
        self.assertNotIn(fleeting, listing)
        self.assertEqual(self.saved()[0]['memory_ids'], ['mem-1'])

    def test_he_can_change_finish_drop_and_pick_up_again(self):
        thought = self.note('Write Lan a fake weather report')
        result, state, _ = self.index.invoke({'action': 'touch', 'thought_id': thought,
                                              'text': 'Write Lan a fake horoscope instead'})
        self.assertEqual(state, '成功')
        self.assertIn('horoscope', result)
        self.assertEqual(self.index.invoke({'action': 'drop', 'thought_id': thought})[1], '成功')
        self.assertIn('No thoughts', self.index.invoke({'action': 'list'})[0])
        self.assertEqual(self.index.invoke({'action': 'touch', 'thought_id': thought})[1], '成功')
        # Third time he comes back to it, so it stays.
        self.assertEqual(self.saved()[0]['state'], 'kept')
        self.assertEqual(self.index.invoke({'action': 'done', 'thought_id': thought})[1], '成功')

    def test_line_stays_short_so_the_index_is_not_a_second_memory(self):
        result, state, _ = self.index.invoke({'action': 'note', 'text': 'x' * 161})
        self.assertEqual(state, '被拒绝')
        self.assertIn('Ombre', result)
        self.assertEqual(self.index.invoke({'action': 'touch', 'thought_id': 'missing'})[1], '被拒绝')
        self.assertEqual(self.index.invoke({'action': 'note'})[1], '被拒绝')

    def test_wake_shows_at_most_two_and_not_the_same_one_again_within_a_day(self):
        self.assertEqual(self.index.wake_text(), '')
        kept = self.note('Learn why spider silk is so strong')
        self.index.invoke({'action': 'keep', 'thought_id': kept})
        self.note('Make a desktop pet that steals her cursor')
        self.note('Look up my own fan edits')
        self.clock.advance(days=2, hours=1)
        first = self.index.wake_text()
        self.assertIn(kept, first)
        self.assertEqual(first.count('\n- '), 2)
        self.assertIn('nothing here has to be done', first)
        self.clock.advance(hours=2)
        second = self.index.wake_text()
        self.assertNotIn(kept, second)
        self.assertLessEqual(second.count('\n- '), 1)


class WakeLabContextTests(unittest.TestCase):
    def test_wake_lists_lab_files_and_only_previews_the_newest(self):
        import os
        from autonomy import AutonomyController as Autonomy
        with tempfile.TemporaryDirectory() as temp:
            lab = Path(temp) / 'lab'
            (lab / 'hunt').mkdir(parents=True)
            old = lab / 'hunt' / '01.md'
            old.write_text('OLD BODY ' * 200, encoding='utf-8')
            os.utime(old, (1_700_000_000, 1_700_000_000))
            new = lab / 'notes.md'
            new.write_text('Newest idea: a cursor-stealing pet.', encoding='utf-8')
            fake = SimpleNamespace(chat=SimpleNamespace(tools=SimpleNamespace(lab=lab)))
            text = Autonomy._lab_context(fake)
        self.assertIn('- notes.md', text)
        self.assertIn('- hunt/01.md', text)
        self.assertIn('Newest idea: a cursor-stealing pet.', text)
        self.assertNotIn('OLD BODY', text)


if __name__ == '__main__':
    unittest.main()
