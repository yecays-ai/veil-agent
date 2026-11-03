"""Ticket 205: the cost ledger is read incrementally, with identical results and safe invalidation."""
import json
import tempfile
import time
import unittest
from decimal import Decimal
from pathlib import Path

from veil_agent import Budget, Store, UserError

STAMP = '2026-11-03T09:00:00+08:00'


def fake_ledger(path, requests):
    lines = []
    for i in range(requests):
        lines.append(json.dumps({'type': 'reserve', 'id': f'r{i}', 'period': STAMP, 'amount': '0.01',
                                 'character': 'fake', 'model': 'm', 'category': 'chat' if i % 3 else 'autonomy'}))
        lines.append(json.dumps({'type': 'settle', 'id': f'r{i}', 'amount': '0.005'}))
    path.write_text('\n'.join(lines) + '\n', encoding='utf-8')


class IncrementalLedgerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.store = Store(self.root / 'data')
        self.budget = Budget(self.store, clock=lambda: STAMP)
        self.ledger = self.root / 'data' / 'costs.jsonl'

    def test_matches_full_read_while_appending(self):
        fake_ledger(self.ledger, 50)
        for i in range(5):
            self.budget.reserve_amount('fake', 'm', '0.02', 'test')
            self.assertEqual(self.store.read_incremental('costs.jsonl'), self.store.read('costs.jsonl'))
        day, month, pending = self.budget.totals()
        self.assertEqual(pending, Decimal('0.10'))
        self.assertEqual(day, Decimal('0.25'))

    def test_rewritten_file_is_reparsed(self):
        fake_ledger(self.ledger, 10)
        self.assertEqual(len(self.store.read_incremental('costs.jsonl')), 20)
        fake_ledger(self.ledger, 3)  # smaller replacement
        self.assertEqual(len(self.store.read_incremental('costs.jsonl')), 6)
        replacement = self.ledger.with_name('other.tmp')
        fake_ledger(replacement, 40)  # larger replacement with a new identity
        replacement.replace(self.ledger)
        self.assertEqual(self.store.read_incremental('costs.jsonl'), self.store.read('costs.jsonl'))
        self.assertEqual(len(self.store.read_incremental('costs.jsonl')), 80)

    def test_same_size_rewrite_is_reparsed(self):
        fake_ledger(self.ledger, 5)
        self.store.read_incremental('costs.jsonl')
        text = self.ledger.read_text(encoding='utf-8').replace('"r0"', '"q0"')
        self.ledger.write_text(text, encoding='utf-8')
        self.assertEqual(self.store.read_incremental('costs.jsonl'), self.store.read('costs.jsonl'))

    def test_damaged_row_still_stops(self):
        fake_ledger(self.ledger, 3)
        self.store.read_incremental('costs.jsonl')
        with self.ledger.open('ab') as handle:
            handle.write(b'{broken\n')
        with self.assertRaises(UserError):
            self.store.read_incremental('costs.jsonl')
        with self.assertRaises(UserError):
            self.budget.totals()

    def test_torn_last_row_is_damage_and_valid_unterminated_row_is_read(self):
        fake_ledger(self.ledger, 2)
        with self.ledger.open('ab') as handle:
            handle.write(b'{"type": "dispatch", "id": "r0"}')
        self.assertEqual(self.store.read_incremental('costs.jsonl'), self.store.read('costs.jsonl'))
        with self.ledger.open('ab') as handle:
            handle.write(b'{"torn')
        with self.assertRaises(UserError):
            self.store.read_incremental('costs.jsonl')

    def test_running_totals_equal_full_pass(self):
        import random
        rng = random.Random(205)
        stamps = ['2026-11-02T10:00:00+08:00', STAMP, '2026-12-01T00:00:00+08:00']
        for step in range(300):
            rid = f'r{rng.randrange(60)}'
            kind = rng.choice(['reserve', 'settle', 'settle', 'dispatch'])
            if kind == 'reserve':
                row = {'type': 'reserve', 'id': rid, 'period': rng.choice(stamps), 'amount': str(rng.random()),
                       'character': rng.choice(['a', 'b']), 'model': 'm',
                       'category': rng.choice(['chat', 'autonomy', 'glance', 'autonomy'])}
            elif kind == 'settle':
                row = {'type': 'settle', 'id': rid, 'amount': str(rng.random() / 3)}
            else:
                row = {'type': 'dispatch', 'id': rid}
            self.store.append('costs.jsonl', row)
            if step % 7 == 0:
                for stamp in stamps:
                    self.assertEqual(self.budget.totals(stamp), self.budget._totals_full(stamp))
                    for category in ('autonomy', 'glance'):
                        for who in (None, 'a', 'b'):
                            self.assertEqual(self.budget.category_totals(who, stamp, category),
                                             self.budget._category_totals_full(who, stamp, category))

    def test_invalid_amount_still_stops_every_time(self):
        self.store.append('costs.jsonl', {'type': 'reserve', 'id': 'x', 'period': STAMP, 'amount': '-1',
                                          'character': 'a', 'model': 'm'})
        for _ in range(2):
            with self.assertRaises(UserError):
                self.budget.totals()

    def test_missing_file(self):
        self.assertEqual(self.store.read_incremental('nope.jsonl'), [])

    def test_large_ledger_is_fast_after_first_read(self):
        fake_ledger(self.ledger, 30000)  # 60,000 rows
        self.budget.totals()
        started = time.perf_counter()
        for _ in range(5):
            self.budget.totals()
        per_call = (time.perf_counter() - started) / 5
        self.assertLess(per_call, 0.35)  # the full re-read took about 0.7 s on the audit machine


if __name__ == '__main__':
    unittest.main()
