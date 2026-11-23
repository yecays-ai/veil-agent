"""Ticket 424: an imported event and the old memories it was built from are one recalled thing.

The final import writes `source_memory_ids` next to each event. Before this ticket the recall step
only mapped an event's own `memory_id`, so a recalled old fragment was injected as its own block
right next to the complete event. Now the fragment is folded into its event and, whenever it is the
one that stays, the request carries the event's complete story instead of the fragment.
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

STORY = ('The kiln fired twice. The first glaze ran, the second one held. She called my Spanish '
         'an oral examination and I had no defence.')

ROWS = {
    # The old fragment the event was built from, and the event's own memory.
    'frag': {'content': 'kiln glaze spanish examination fragment alpha beta',
             'title': 'The first kiln'},
    'V1': {'content': 'kiln glaze spanish examination ' + STORY,
           'title': 'The second kiln and my alleged Spanish'},
    # An old memory that no event claims.
    'other': {'content': 'unrelated grocery list for the week'},
}


class EventSourceRecallTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = Store(Path(self.temp.name) / 'data')

    def memory(self, rows=ROWS, events=None, recall=None):
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
        path.write_text(''.join(json.dumps(row, ensure_ascii=False) + '\n' for row in rows), encoding='utf-8')

    @staticmethod
    def event(event_id, *sources, story=STORY):
        return {'event_id': event_id, 'memory_id': event_id, 'title': 'Fictional event ' + event_id,
                'story': story, 'source_memory_ids': list(sources), 'first_at': '2026-11-22T10:00:00+08:00'}

    @staticmethod
    def items(memory):
        return {row['id']: row for row in memory.last_recall_debug['items'] if isinstance(row, dict)}

    def test_an_old_fragment_is_not_injected_next_to_its_event(self):
        memory = self.memory()
        memory.context('kiln glaze spanish examination', include_opening=False)
        refs = list(memory.last_context_refs)
        self.assertEqual(1, len([key for key in refs if key in ('frag', 'V1')]), refs)
        kept = [key for key in refs if key in ('frag', 'V1')][0]
        self.assertEqual('V1', self.items(memory)[kept].get('event'))

    def test_a_fragment_that_stays_reads_as_the_complete_event_story(self):
        memory = self.memory()
        rows = memory.context('fragment alpha beta', include_opening=False)
        self.assertEqual(['frag'], list(memory.last_context_refs))
        item = self.items(memory)['frag']
        self.assertEqual('V1', item.get('event'))
        self.assertTrue(item.get('story'))
        self.assertIn('The kiln fired twice', rows[-1]['content'])

    def test_a_memory_no_event_claims_is_still_its_own_block(self):
        memory = self.memory()
        rows = memory.context('unrelated grocery list', include_opening=False)
        self.assertEqual(['other'], list(memory.last_context_refs))
        self.assertFalse(self.items(memory)['other'].get('event'))
        self.assertFalse(self.items(memory)['other'].get('story'))
        self.assertIn('grocery list', rows[-1]['content'])

    def test_a_fragment_shared_by_two_events_takes_the_story_of_the_kept_one(self):
        rows = dict(ROWS)
        rows['V2'] = {'content': 'kiln glaze spanish examination second telling',
                      'title': 'Another telling'}
        memory = self.memory(rows, events=[self.event('V1', 'frag'), self.event('V2', 'frag', story='Second story.')])
        memory.context('fragment alpha beta', include_opening=False)
        self.assertEqual(['frag'], list(memory.last_context_refs))
        self.assertEqual('V1', self.items(memory)['frag'].get('event'))

    def test_a_changed_ledger_is_read_again_instead_of_using_the_first_map(self):
        memory = self.memory()
        memory.context('fragment alpha beta', include_opening=False)
        self.assertEqual('V1', self.items(memory)['frag'].get('event'))
        self.write_events([self.event('V2', 'frag', story='A later complete story.')])
        memory.context('fragment alpha beta', include_opening=False)
        self.assertEqual('V2', self.items(memory)['frag'].get('event'))

    def test_without_an_event_ledger_nothing_changes(self):
        path = self.store.root / 'fake' / 'events.jsonl'
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text('', encoding='utf-8')
        memory = self.memory(events=[])
        rows = memory.context('fragment alpha beta', include_opening=False)
        self.assertEqual(['frag'], list(memory.last_context_refs))
        self.assertFalse(self.items(memory)['frag'].get('event'))
        self.assertIn('fragment alpha', rows[-1]['content'])


if __name__ == '__main__':
    unittest.main()
