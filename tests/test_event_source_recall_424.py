"""Ticket 424: an imported event and the old memories it was built from are one recalled thing.

The final import writes `source_memory_ids` next to each event. Recall used to ignore that field, so an
old fragment was injected as its own block right next to the complete event it belongs to. Now a fragment
with exactly one owner is folded into that event and, when it is the block that stays, the request carries
the event's reviewed story instead. Shared fragments (two events claim one old memory) keep their own text,
an event without a story cannot be folded by another owner's story, and malformed ledger data is ignored.
"""
import json
import random
import tempfile
import unittest
from decimal import Decimal
from pathlib import Path
from unittest.mock import Mock

from memory_agent import Memory, vault_path
from test_recall_rotation import FictionalOmbre
from veil_agent import Budget, Character, Store

STORY_V1 = ('The kiln fired twice. The first glaze ran, the second one held. V1STORYMARK and the end.')
STORY_V2 = ('A different telling about the same evening. V2STORYMARK and nothing else.')
FRAGMENT = 'kiln glaze spanish examination fragment alpha beta FRAGMARK'

ROWS = {
    'frag': {'content': FRAGMENT, 'title': 'The first kiln'},
    'V1': {'content': 'kiln glaze spanish examination ' + STORY_V1, 'title': 'V1 event'},
    'V2': {'content': 'kiln glaze spanish examination ' + STORY_V2, 'title': 'V2 event'},
    'other': {'content': 'unrelated grocery list for the week'},
}


class FlakyStore:
    """A store whose first events.jsonl read fails, like a transient disk error."""

    def __init__(self, real):
        self.real, self.fail = real, True

    def read(self, path):
        if self.fail and str(path).endswith('events.jsonl'):
            self.fail = False
            raise OSError('transient read failure')
        return self.real.read(path)

    def __getattr__(self, name):
        return getattr(self.real, name)


class EventSourceRecallTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = Store(Path(self.temp.name) / 'data')

    def memory(self, rows=None, events=None, recall=None):
        rows = dict(rows or ROWS)
        vault = vault_path(self.store.root, 'fake')
        self.backend = FictionalOmbre(vault / 'dynamic', rows)
        provider = {'model': 'fake', 'max_input_tokens': 20000, 'input_cny_per_million': Decimal(2),
                    'output_cny_per_million': Decimal(8), 'cached_input_cny_per_million': Decimal(1)}
        memory_cfg = {'model': 'fake-embedding', 'input_cny_per_million': '0', 'mode': 'native',
                      'base_url': 'https://example.invalid/v1'}
        memory_cfg['recall'] = recall or {'cooldown_turns': 0, 'association_enabled': False}
        cfg = Character('fake', 'Example', 'fictional persona', 20, 1024, provider, 'fake', memory_cfg)
        self.write_events(events if events is not None else [self.event('V1', 'frag')])
        memory = Memory(cfg, self.store, Budget(self.store), Mock(), self.backend)
        memory.rng = random.Random(7)
        self.addCleanup(memory.__exit__, None, None, None)
        return memory

    def write_events(self, rows):
        path = self.store.root / 'fake' / 'events.jsonl'
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(''.join(json.dumps(row, ensure_ascii=False) + '\n' for row in rows),
                        encoding='utf-8')

    @staticmethod
    def event(event_id, *sources, story=STORY_V1, story_source='final-import'):
        return {'event_id': event_id, 'memory_id': event_id, 'title': 'Fictional event ' + event_id,
                'story': story, 'story_source': story_source,
                'source_memory_ids': list(sources), 'first_at': '2026-11-22T10:00:00+08:00'}

    @staticmethod
    def items(memory):
        return {row['id']: row for row in memory.last_recall_debug['items'] if isinstance(row, dict)}

    def recalled(self, memory, query):
        rows = memory.context(query, include_opening=False)
        return list(memory.last_context_refs), rows[-1]['content']

    # ---- a fragment owned by exactly one event ----
    def test_a_unique_source_is_not_injected_next_to_its_event(self):
        memory = self.memory()
        refs, text = self.recalled(memory, 'kiln glaze spanish examination')
        self.assertEqual(1, len([key for key in refs if key in ('frag', 'V1')]), refs)
        kept = [key for key in refs if key in ('frag', 'V1')][0]
        self.assertIn('V1STORYMARK', text)
        self.assertNotEqual('frag', kept, refs)

    def test_a_unique_source_that_stays_reads_as_the_imported_story(self):
        memory = self.memory()
        refs, text = self.recalled(memory, 'fragment alpha beta')
        self.assertEqual(['frag'], refs)
        self.assertIn('The kiln fired twice', text)
        self.assertNotIn('FRAGMARK', text)
        self.assertEqual('V1', self.items(memory)['frag'].get('event'))
        self.assertTrue(self.items(memory)['frag'].get('story'))

    def test_a_unique_source_that_stays_reads_its_own_text_when_the_story_is_not_imported(self):
        memory = self.memory(events=[self.event('V1', 'frag', story_source='composed')])
        refs, text = self.recalled(memory, 'fragment alpha beta')
        self.assertEqual(['frag'], refs)
        self.assertIn('FRAGMARK', text)
        self.assertNotIn('The kiln fired twice', text)

    # ---- one old memory claimed by two events ----
    def shared(self, events=None):
        return self.memory(events=events or [self.event('V1', 'frag'), self.event('V2', 'frag', story=STORY_V2)])

    def test_a_shared_source_keeps_its_own_text_instead_of_borrowing_an_event_story(self):
        memory = self.shared()
        refs, text = self.recalled(memory, 'fragment alpha beta')
        self.assertEqual(['frag'], refs)
        self.assertIn('FRAGMARK', text)
        self.assertNotIn('V1STORYMARK', text)
        self.assertNotIn('V2STORYMARK', text)
        self.assertEqual('', self.items(memory)['frag'].get('event'))
        self.assertFalse(self.items(memory)['frag'].get('story'))

    def test_recalling_each_owner_never_crosses_the_other_story(self):
        memory = self.shared()
        refs, text = self.recalled(memory, 'V1STORYMARK')
        self.assertIn('V1', refs)
        self.assertIn('V1STORYMARK', text)
        self.assertNotIn('V2STORYMARK', text)
        memory = self.shared()
        refs, text = self.recalled(memory, 'V2STORYMARK')
        self.assertIn('V2', refs)
        self.assertIn('V2STORYMARK', text)
        self.assertNotIn('V1STORYMARK', text)

    def test_a_story_event_and_a_storyless_owner_do_not_fold_the_shared_stage(self):
        rows = dict(ROWS)
        rows['B'] = {'content': 'BONLY the stage that has no story yet'}
        memory = self.memory(rows=rows, events=[self.event('A', 'frag'), self.event('B', 'frag', story=None)])
        refs, text = self.recalled(memory, 'BONLY fragment alpha beta')
        self.assertIn('B', refs)
        self.assertIn('BONLY', text)
        self.assertNotIn('V1STORYMARK', text)

    def test_a_shared_fragment_gets_no_event_directory_hint(self):
        memory = self.shared()
        refs, text = self.recalled(memory, 'fragment alpha beta')
        self.assertEqual(['frag'], refs)
        self.assertNotIn('memory_timeline', text.split('FRAGMARK')[0])

    # ---- malformed ledger data ----
    def test_malformed_source_lists_are_ignored_row_by_row(self):
        rows = dict(ROWS)
        rows['ok'] = {'content': 'okmark a valid old source'}
        events = [
            {'event_id': 'E1', 'memory_id': 'E1', 'story': STORY_V1, 'story_source': 'final-import', 'source_memory_ids': 123},
            {'event_id': 'E2', 'memory_id': 'E2', 'story': STORY_V1, 'story_source': 'final-import', 'source_memory_ids': 'abc'},
            {'event_id': 'E3', 'memory_id': 'E3', 'story': STORY_V1, 'story_source': 'final-import', 'source_memory_ids': {'a': 1}},
            {'event_id': 'E4', 'memory_id': 'E4', 'story': STORY_V1, 'story_source': 'final-import', 'source_memory_ids': None},
            {'event_id': 'E5', 'memory_id': 'E5', 'story': STORY_V1, 'story_source': 'final-import',
             'source_memory_ids': ['ok', '', 7, None, {'x': 1}, ['y']]},
        ]
        memory = self.memory(rows=rows, events=events)
        ledger = memory._event_ledger()
        self.assertEqual({'ok': 'E5'}, {k: v for k, v in ledger['unique'].items() if k == 'ok'})
        self.assertNotIn('a', ledger['unique'])          # a string is not split into characters
        self.assertNotIn('b', ledger['unique'])
        refs, text = self.recalled(memory, 'okmark')
        self.assertEqual(['ok'], refs)
        self.assertIn('V1STORYMARK', text)                  # the good row still owns its source
        self.recalled(memory, 'grocery list')               # recall keeps working after the bad rows

    # ---- transient disk failure ----
    def test_a_transient_ledger_read_failure_is_retried_next_turn(self):
        memory = self.memory()
        memory.store = FlakyStore(memory.store)
        refs, text = self.recalled(memory, 'fragment alpha beta')
        self.assertEqual(['frag'], refs)
        self.assertIn('FRAGMARK', text)                     # failed closed: fragment kept as itself
        refs, text = self.recalled(memory, 'fragment alpha beta')
        self.assertIn('The kiln fired twice', text)         # retried, now upgraded

    # ---- no ledger at all ----
    def test_without_an_event_ledger_nothing_changes(self):
        path = self.store.root / 'fake' / 'events.jsonl'
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text('', encoding='utf-8')
        memory = self.memory(events=[])
        refs, text = self.recalled(memory, 'fragment alpha beta')
        self.assertEqual(['frag'], refs)
        self.assertIn('FRAGMARK', text)
        self.assertFalse(self.items(memory)['frag'].get('event'))


if __name__ == '__main__':
    unittest.main()
