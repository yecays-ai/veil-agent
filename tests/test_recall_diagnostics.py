"""Ticket 391: the read-only record of where each recall candidate went."""
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


ROWS = {
    'rose': {'content': 'garden roses planted together in spring', 'importance': 8},
    'archived_rose': {'content': 'archived garden roses from last year', 'type': 'archived'},
    'train': {'content': 'train trip to the coast with a broken window'},
}


class RecallDiagnosticsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = Store(Path(self.temp.name) / 'data')
        vault = vault_path(self.store.root, 'fake')
        self.backend = FictionalOmbre(vault / 'dynamic', ROWS)
        provider = {'model': 'fake', 'max_input_tokens': 20000, 'input_cny_per_million': Decimal(2),
                    'output_cny_per_million': Decimal(8), 'cached_input_cny_per_million': Decimal(1)}
        memory_cfg = {'model': 'fake-embedding', 'input_cny_per_million': '0', 'mode': 'native',
                      'base_url': 'https://example.invalid/v1'}
        cfg = Character('fake', 'Example', 'fictional persona', 20, 1024, provider, 'fake', memory_cfg)
        self.memory = Memory(cfg, self.store, Budget(self.store), Mock(), self.backend)
        self.memory.rng = random.Random(7)
        self.addCleanup(self.memory.__exit__, None, None, None)

    def test_every_stage_records_ids_and_the_final_injection(self):
        self.memory.context('garden roses')
        debug = self.memory.last_recall_debug
        self.assertTrue(debug['ombre'])
        self.assertTrue(debug['injected'])
        self.assertEqual(debug['injected'], ['rose'])
        self.assertEqual(debug['judge']['status'], 'skipped')  # no judge attached

    def test_an_archived_hit_is_explained_instead_of_silently_dropped(self):
        self.memory.context('archived garden roses')
        hidden = self.memory.last_recall_debug['hidden']
        self.assertIn({'id': 'archived_rose', 'reason': 'archived'}, hidden)

    def test_the_file_has_ids_and_reasons_but_no_query_or_memory_text(self):
        self.memory.context('garden roses')
        self.memory.save_recall_debug('r1')
        raw = (self.store.root / 'fake' / 'recall-diagnostics.jsonl').read_text(encoding='utf-8')
        self.assertNotIn('garden', raw)
        self.assertNotIn('roses', raw)
        row = self.store.read('fake/recall-diagnostics.jsonl')[-1]
        self.assertEqual(row['request_id'], 'r1')
        self.assertEqual(row['injected'], ['rose'])
        self.assertEqual(row['query_chars'], len('garden roses'))

    def test_budget_drops_are_separate_from_relevance(self):
        self.memory.context('garden roses')
        self.memory.note_budget_dropped(['rose', 'train'])
        self.memory.save_recall_debug('r2')
        row = self.store.read('fake/recall-diagnostics.jsonl')[-1]
        self.assertEqual(row['budget_dropped'], ['rose', 'train'])
        self.assertEqual(row['injected'], ['rose'])  # still marked as injected; the caller owns the cut

    def test_the_diagnostics_file_is_rolling(self):
        for index in range(230):
            self.memory.context('garden roses')
            self.memory.save_recall_debug(f'r{index}')
        rows = self.store.read('fake/recall-diagnostics.jsonl')
        self.assertLessEqual(len(rows), 240)
        self.assertEqual(rows[-1]['request_id'], 'r229')


if __name__ == '__main__':
    unittest.main()
