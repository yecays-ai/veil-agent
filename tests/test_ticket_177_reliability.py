"""Ticket 177: interrupted-request budget recovery, snapshot guard, submit error reason."""
import json
import tempfile
import unittest
from decimal import Decimal
from pathlib import Path

from desktop_icons import (DesktopIcon, DesktopIconsError, DesktopIconsManager, DisplayMetrics,
                           FakeDesktopIconsBackend)
from veil_agent import Budget, Store


def reserve(store, request_id, amount='0.2', category='autonomy', accounting=None, character='fixture'):
    row = {'type': 'reserve', 'id': request_id, 'period': store.root and '2026-10-01T10:00:00+08:00',
           'amount': amount, 'character': character, 'model': 'fixture', 'category': category}
    if accounting:
        row['accounting'] = accounting
    store.append('costs.jsonl', row)


class InterruptedBudgetTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.directory.name))

    def tearDown(self):
        self.directory.cleanup()

    def test_unsent_reservation_is_released_and_dispatched_one_is_charged_once(self):
        reserve(self.store, 'unsent')
        reserve(self.store, 'sent')
        self.store.append('costs.jsonl', {'type': 'dispatch', 'id': 'sent'})
        budget = Budget(self.store)  # a new process starts: earlier open requests are recovered
        rows = {row['id']: row for row in self.store.read('costs.jsonl') if row['type'] == 'settle'}
        self.assertEqual(Decimal(rows['unsent']['amount']), 0)
        self.assertEqual(Decimal(rows['sent']['amount']), Decimal('0.2'))
        self.assertEqual(rows['sent']['accounting'], 'chat-recovered')
        # Neither one stays pending, on this day or any later one.
        used, pending = budget.category_totals('fixture', stamp='2026-10-02T10:00:00+08:00')
        self.assertEqual(pending, 0)
        self.assertEqual(used, 0)
        self.assertEqual(budget.totals('2026-10-01T12:00:00+08:00')[2], 0)
        self.assertEqual(budget.totals('2026-10-01T12:00:00+08:00')[0], Decimal('0.2'))

    def test_recovery_is_idempotent_and_keeps_estimate_charges(self):
        reserve(self.store, 'embed', accounting='embedding-estimate', category='chat')
        Budget(self.store)
        count = len(self.store.read('costs.jsonl'))
        Budget(self.store)
        self.assertEqual(len(self.store.read('costs.jsonl')), count)
        settle = [r for r in self.store.read('costs.jsonl') if r['type'] == 'settle'][0]
        self.assertEqual(Decimal(settle['amount']), Decimal('0.2'))

    def test_dispatch_marker_does_not_break_ledger_reads(self):
        budget = Budget(self.store)
        cfg = type('Cfg', (), {'provider': {'cny_per_call': Decimal('0.06'), 'model': 'fixture'}, 'id': 'fixture',
                               'max_output_tokens': 10})()
        request_id, _ = budget.reserve(cfg)
        budget.mark_dispatched(request_id)
        self.assertEqual(budget.totals()[2], Decimal('0.06'))
        self.assertEqual(budget.category_totals('fixture')[1], 0)


class SnapshotGuardTests(unittest.TestCase):
    def test_empty_layout_is_never_saved_and_no_icon_moves(self):
        with tempfile.TemporaryDirectory() as directory:
            metrics = DisplayMetrics(resolution=(1920, 1080), auto_arrange=False,
                                     monitors=[{'id': 0, 'left': 0, 'top': 0, 'right': 1920,
                                                'bottom': 1080, 'is_primary': True}])
            icons = [DesktopIcon('a_file', 'a.txt', 'file', 20, 20)]
            backend = FakeDesktopIconsBackend(icons=icons, metrics=metrics)
            manager = DesktopIconsManager(store=Store(Path(directory)), character_id='peter', backend=backend)
            with self.assertRaises(DesktopIconsError):
                manager.take_snapshot(icons=[])
            self.assertEqual(manager.list_snapshots(), [])
            # The snapshot of an arrangement is exactly the layout that was validated.
            result = manager.arrange_icons([{'icon_id': 'a_file', 'x': 300, 'y': 300}])
            saved = manager.get_latest_snapshot()
            self.assertEqual(saved['snapshot_id'], result['snapshot_id'])
            self.assertEqual((saved['icons'][0]['x'], saved['icons'][0]['y']), (20, 20))


if __name__ == '__main__':
    unittest.main()


class UsageOverBoundTests(unittest.TestCase):
    def test_provider_usage_above_bounds_is_settled_not_fatal(self):
        with tempfile.TemporaryDirectory() as directory:
            budget = Budget(Store(Path(directory)))
            provider = {'model': 'fixture', 'max_input_tokens': 1000, 'input_cny_per_million': Decimal(2),
                        'cached_input_cny_per_million': Decimal(1), 'output_cny_per_million': Decimal(8)}
            cfg = type('Cfg', (), {'provider': provider, 'id': 'fixture', 'max_output_tokens': 100})()
            request_id, _ = budget.reserve(cfg)
            self.assertTrue(budget.settle(request_id, cfg, 500, 5000, 0, tolerate_over_bound=True))
            row = [r for r in budget.store.read('costs.jsonl') if r['type'] == 'settle'][0]
            self.assertIn('usage-over-bound', row['accounting'])
            self.assertEqual(row['completion_tokens'], 5000)
            self.assertEqual(budget.totals()[2], 0)  # nothing left pending

    def test_other_callers_still_reject_over_bound_usage(self):
        with tempfile.TemporaryDirectory() as directory:
            budget = Budget(Store(Path(directory)))
            provider = {'model': 'fixture', 'max_input_tokens': 1000, 'input_cny_per_million': Decimal(2),
                        'cached_input_cny_per_million': Decimal(1), 'output_cny_per_million': Decimal(8)}
            cfg = type('Cfg', (), {'provider': provider, 'id': 'fixture', 'max_output_tokens': 100})()
            request_id, _ = budget.reserve(cfg)
            from veil_agent import UserError
            with self.assertRaises(UserError):
                budget.settle(request_id, cfg, 500, 5000, 0)
