"""Ticket 199: lifecycle-aware ledger reconciliation, JSONL diagnosis/repair, unverified-cost guard.

Only fictional data in temporary folders; nothing here touches a real ledger."""
import json
import os
import tempfile
import unittest
from decimal import Decimal
from pathlib import Path
from unittest import mock

import backup_restore
import data_repair
import ledger_recovery as ledger
from veil_agent import Budget, BudgetExceeded, Store, UserError

DAY = '2026-11-01T10:00:00+08:00'


def row(kind, request_id, **fields):
    base = {'type': kind, 'id': request_id, 'time': DAY}
    if kind == 'reserve':
        base.update(period=DAY, amount='0.5', character='fixture', model='m', category='chat')
    if kind == 'settle':
        base.update(amount='0.3')
    base.update(fields)
    return (json.dumps(base, ensure_ascii=False) + '\n').encode('utf-8')


def ledger_bytes(*rows):
    return b''.join(rows)


class ReadLinesTests(unittest.TestCase):
    def test_torn_tail_missing_newline_and_garbage_are_told_apart(self):
        raw = row('reserve', 'a') + b'not json\n' + b'5\n' + row('dispatch', 'a') + b'{"type": "settle", "id'
        problems = {line.number: problem for line, problem in
                    ledger.find_problems(ledger.read_lines(raw), cost_ledger=True)}
        self.assertEqual(problems, {2: 'invalid-json', 3: 'not-an-object', 5: 'torn-tail'})
        complete_without_newline = row('reserve', 'a').rstrip(b'\n')
        self.assertEqual(ledger.read_lines(complete_without_newline)[0].problem, 'no-newline')

    def test_cost_rows_are_checked_the_way_budget_reads_them(self):
        for bad, expected in (({'type': 'mystery', 'id': 'x'}, 'unknown-type'),
                              ({'type': 'settle', 'id': ''}, 'missing-id'),
                              ({'type': 'settle', 'id': 'x', 'amount': 'abc'}, 'invalid-amount'),
                              ({'type': 'settle', 'id': 'x', 'amount': '-1'}, 'invalid-amount'),
                              ({'type': 'reserve', 'id': 'x', 'amount': '1'}, 'invalid-period')):
            self.assertEqual(ledger.check_cost_row(bad), expected)


class MergeTests(unittest.TestCase):
    def test_requests_after_the_backup_keep_their_whole_lifecycle(self):
        backup = ledger_bytes(row('reserve', 'old'), row('settle', 'old'), row('reserve', 'open'),
                              row('dispatch', 'open'))
        current = ledger_bytes(row('reserve', 'old'), row('settle', 'old'), row('reserve', 'open'),
                               row('dispatch', 'open'), row('settle', 'open', amount='0.4'),
                               row('reserve', 'new'), row('dispatch', 'new'), row('settle', 'new'))
        result = ledger.merge_cost_ledgers(backup, current)
        self.assertEqual(result.new_requests, 1)
        self.assertEqual(result.completed, 1)  # the settle of the request the backup left open
        self.assertFalse(result.unverified)
        kinds = [(r['type'], r['id']) for r in map(json.loads, result.merged.splitlines())]
        for step in (('reserve', 'new'), ('dispatch', 'new'), ('settle', 'new'), ('settle', 'open')):
            self.assertEqual(kinds.count(step), 1)
        ledger.validate_cost_ledger(result.merged)

    def test_merging_twice_changes_nothing(self):
        backup = ledger_bytes(row('reserve', 'a'))
        current = ledger_bytes(row('reserve', 'a'), row('settle', 'a'), row('reserve', 'b'))
        once = ledger.merge_cost_ledgers(backup, current).merged
        twice = ledger.merge_cost_ledgers(once, current)
        self.assertEqual(twice.merged, once)
        self.assertEqual((twice.new_requests, twice.completed), (0, 0))

    def test_disagreement_keeps_backup_row_and_saves_the_other_version(self):
        backup = ledger_bytes(row('reserve', 'a'), row('settle', 'a', amount='0.3'))
        current = ledger_bytes(row('reserve', 'a'), row('settle', 'a', amount='0.9'))
        result = ledger.merge_cost_ledgers(backup, current)
        self.assertEqual(result.merged, backup)
        self.assertEqual([item['kind'] for item in result.evidence], ['conflict'])
        self.assertIn('0.9', result.evidence[0]['preview'])

    def test_unreadable_current_lines_become_evidence_never_zero_cost(self):
        backup = ledger_bytes(row('reserve', 'a'))
        current = ledger_bytes(row('reserve', 'a')) + b'{"type": "settle", "id": "a", "amo'
        result = ledger.merge_cost_ledgers(backup, current)
        self.assertEqual(result.merged, backup)
        self.assertEqual(result.evidence[0]['kind'], 'unreadable-current-line')
        self.assertTrue(result.unverified)


class BudgetGuardTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.directory.name))
        self.budget = Budget(self.store, clock=lambda: DAY)

    def tearDown(self):
        self.directory.cleanup()

    def test_unverified_costs_stop_new_paid_requests_until_verified(self):
        reservation = self.budget.reserve_amount('fixture', 'm', '0.1', 'chat')
        ledger.mark_unverified(self.store.root, 'restore-reconciliation', 1, 'costs-unverified.jsonl')
        with self.assertRaisesRegex(BudgetExceeded, '待核实'):
            self.budget.reserve_amount('fixture', 'm', '0.1', 'chat')
        with self.assertRaisesRegex(BudgetExceeded, '待核实'):
            self.budget.estimate('fixture', 'm', 10, 1)
        self.assertIn('待核实', self.budget.display())
        # A request that already left the machine can still be settled.
        self.assertTrue(self.budget.settle_estimate(reservation, accounting='chat'))
        ledger.archive_marker(self.store.root)
        self.budget.reserve_amount('fixture', 'm', '0.1', 'chat')


def make_project(root, costs=None, chat=None):
    (root / 'data' / 'fixture').mkdir(parents=True)
    (root / 'characters' / 'fixture').mkdir(parents=True)
    (root / 'persona').mkdir()
    (root / '.env').write_bytes(b'FAKE=1\n')
    (root / 'persona' / 'fixture.md').write_bytes(b'fiction')
    (root / 'characters' / 'fixture' / 'character.toml').write_text(
        'name = "F"\npersona = "persona/fixture.md"\n', encoding='utf-8')
    (root / 'data' / 'costs.jsonl').write_bytes(costs if costs is not None else b'')
    (root / 'data' / 'fixture' / 'chat.jsonl').write_bytes(chat if chat is not None else b'{"role":"user"}\n')


class RestoreReconciliationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.base = Path(self.temp.name)
        self.source, self.target, self.backups = (self.base / 'source', self.base / 'target', self.base / 'bk')
        self.source.mkdir()
        self.target.mkdir()

    def tearDown(self):
        self.temp.cleanup()

    def _backup(self, ledger_data):
        make_project(self.source, ledger_data)
        return backup_restore.create_backup(self.source, self.backups, desktop_dir=None)[0]

    def _restore(self, backup, notices):
        return backup_restore.restore_backup(backup, self.target, replace=True, confirm=lambda _: 'RESTORE',
                                             desktop_dir=None, notice=notices.append)

    def test_restoring_an_old_backup_keeps_costs_recorded_after_it(self):
        backup = self._backup(ledger_bytes(row('reserve', 'old'), row('settle', 'old', amount='0.3')))
        make_project(self.target, ledger_bytes(
            row('reserve', 'old'), row('settle', 'old', amount='0.3'),
            row('reserve', 'later', amount='2'), row('dispatch', 'later'), row('settle', 'later', amount='1.5')))
        notices = []
        self._restore(backup, notices)
        store = Store(self.target / 'data')
        day, month, pending = Budget(store, clock=lambda: DAY).totals()
        self.assertEqual((day, pending), (Decimal('1.8'), Decimal('0')))
        self.assertFalse(ledger.is_unverified(self.target / 'data'))
        self.assertTrue(any('对账' in text for text in notices))
        rows = store.read('costs.jsonl')
        self.assertEqual([r['type'] for r in rows if r['id'] == 'later'], ['reserve', 'dispatch', 'settle'])

    def test_unconfirmable_costs_pause_paid_requests_and_are_kept(self):
        backup = self._backup(ledger_bytes(row('reserve', 'a'), row('settle', 'a', amount='0.3')))
        make_project(self.target, ledger_bytes(row('reserve', 'a'), row('settle', 'a', amount='0.9'))
                     + b'{"type": "settle", "id": "zz", "amo')
        notices = []
        self._restore(backup, notices)
        data = self.target / 'data'
        self.assertTrue(ledger.is_unverified(data))
        evidence = [json.loads(line) for line in (data / ledger.UNVERIFIED_EVIDENCE).read_text().splitlines()]
        self.assertEqual(sorted(item['kind'] for item in evidence), ['conflict', 'unreadable-current-line'])
        budget = Budget(Store(data), clock=lambda: DAY)
        with self.assertRaises(BudgetExceeded):
            budget.reserve_amount('fixture', 'm', '0.1', 'chat')
        # The kept ledger still reads normally and the backup's amount stands.
        self.assertEqual(budget.totals()[0], Decimal('0.3'))

    def test_a_rerun_after_an_interruption_still_finds_the_original_ledger(self):
        backup = self._backup(ledger_bytes(row('reserve', 'old'), row('settle', 'old')))
        make_project(self.target, ledger_bytes(row('reserve', 'old'), row('settle', 'old'),
                                               row('reserve', 'later'), row('settle', 'later')))
        original = backup_restore.os.replace
        calls = {'n': 0}

        def die(source, destination):
            calls['n'] += 1
            result = original(source, destination)
            if calls['n'] == 4:
                raise KeyboardInterrupt
            return result

        with mock.patch.object(backup_restore.os, 'replace', side_effect=die):
            with self.assertRaises(KeyboardInterrupt):
                self._restore(backup, [])
        self._restore(backup, [])
        ids = {r['id'] for r in Store(self.target / 'data').read('costs.jsonl')}
        self.assertEqual(ids, {'old', 'later'})


class RepairTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        make_project(self.root, ledger_bytes(row('reserve', 'a'), row('settle', 'a')),
                     b'{"n":1}\n{"n":2}\n{"n":3, "tex')
        self.chat = self.root / 'data' / 'fixture' / 'chat.jsonl'

    def tearDown(self):
        self.temp.cleanup()

    def test_diagnose_names_the_problem_and_changes_nothing(self):
        before = self.chat.read_bytes()
        text = '\n'.join(data_repair.diagnose(self.root, self.chat))
        self.assertIn('第 3 行', text)
        self.assertIn('写到一半', text)
        self.assertEqual(self.chat.read_bytes(), before)

    def test_repair_saves_original_quarantines_verbatim_and_deletes_nothing(self):
        before = self.chat.read_bytes()
        with self.assertRaises(backup_restore.BackupError):
            data_repair.repair(self.root, self.chat, confirm=lambda _: 'no', notice=lambda _: None)
        self.assertEqual(self.chat.read_bytes(), before)
        outcome = data_repair.repair(self.root, self.chat, confirm=lambda _: 'REPAIR', notice=lambda _: None)
        self.assertEqual(Path(outcome['original_copy']).read_bytes(), before)
        self.assertEqual(Path(outcome['quarantine']).read_bytes(), b'{"n":3, "tex\n')
        self.assertEqual(Store(self.root / 'data').read('fixture/chat.jsonl'), [{'n': 1}, {'n': 2}])
        Store(self.root / 'data').append('fixture/chat.jsonl', {'n': 4})  # appending works again
        self.assertFalse(ledger.is_unverified(self.root / 'data'))  # chat repair does not block payments

    def test_complete_last_line_without_newline_only_gets_its_newline(self):
        self.chat.write_bytes(b'{"n":1}\n{"n":2}')
        data_repair.repair(self.root, self.chat, confirm=lambda _: 'REPAIR', notice=lambda _: None)
        self.assertEqual(self.chat.read_bytes(), b'{"n":1}\n{"n":2}\n')
        self.assertEqual(list((self.root / 'data' / 'fixture').glob('*.corrupt-*')), [])

    def test_a_damaged_cost_ledger_is_never_zeroed_it_pauses_paid_requests(self):
        costs = self.root / 'data' / 'costs.jsonl'
        costs.write_bytes(ledger_bytes(row('reserve', 'a')) + b'{"type": "settle", "id": "a", "am\n'
                          + row('reserve', 'b'))
        outcome = data_repair.repair(self.root, costs, confirm=lambda _: 'REPAIR', notice=lambda _: None)
        self.assertEqual(Path(outcome['quarantine']).read_bytes(), b'{"type": "settle", "id": "a", "am\n')
        self.assertTrue(ledger.is_unverified(self.root / 'data'))
        budget = Budget(Store(self.root / 'data'), clock=lambda: DAY)
        with self.assertRaises(BudgetExceeded):
            budget.reserve_amount('fixture', 'm', '0.1', 'chat')
        self.assertIn('待核实', '\n'.join(data_repair.ledger_status(self.root)))
        with self.assertRaises(backup_restore.BackupError):
            data_repair.ledger_verify(self.root, confirm=lambda _: 'yes', notice=lambda _: None)
        self.assertTrue(ledger.is_unverified(self.root / 'data'))
        data_repair.ledger_verify(self.root, confirm=lambda _: 'VERIFIED', notice=lambda _: None)
        self.assertFalse(ledger.is_unverified(self.root / 'data'))
        self.assertTrue(list((self.root / 'data').glob('costs-unverified.verified-*.json')))
        self.assertTrue(Path(outcome['quarantine']).exists())

    def test_refuses_files_outside_data_and_while_veil_runs(self):
        outside = self.root / 'outside.jsonl'
        outside.write_bytes(b'x\n')
        with self.assertRaises(backup_restore.BackupError):
            data_repair.diagnose(self.root, outside)
        lock = backup_restore._FileLock(self.root / 'data' / 'session.lock', create=True)
        try:
            with self.assertRaises(backup_restore.BackupError):
                data_repair.repair(self.root, self.chat, confirm=lambda _: 'REPAIR', notice=lambda _: None)
        finally:
            lock.close()

    def test_store_errors_tell_the_user_which_command_to_run(self):
        store = Store(self.root / 'data')
        with self.assertRaises(UserError) as caught:
            store.read('fixture/chat.jsonl')
        self.assertIn('data_repair.py diagnose --file', str(caught.exception))
        with self.assertRaises(UserError) as caught:
            store.append('fixture/chat.jsonl', {'n': 9})
        self.assertIn('data_repair.py diagnose --file', str(caught.exception))


if __name__ == '__main__':
    unittest.main()
