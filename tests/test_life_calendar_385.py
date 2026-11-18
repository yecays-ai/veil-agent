"""Ticket 385: one shared calendar (both see everything), Peter edits but never deletes Lan's records,
optional time, a per-day log of daily to-dos, and two daily mood emoji."""
import json
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import life_records
from life_records import LifeRecords, valid_emoji


class SharedCalendarTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = SimpleNamespace(root=Path(self.temp.name), mutex=threading.RLock())
        self.records = LifeRecords(self.store, 'peter')
        self.changes = []
        self.records.on_lan_record_changed = lambda before, after: self.changes.append((before, after))

    def test_peter_sees_lans_records_and_edits_but_cannot_delete_them(self):
        hers = self.records.write({'kind': 'event', 'title': '带团子体检', 'date': '2026-11-22'}, 'Lan')
        listed = json.loads(self.records.invoke({'action': 'list'})[0])
        self.assertEqual([r['title'] for r in listed], ['带团子体检'])
        result, state, _ = self.records.invoke({'action': 'update', 'id': hers['id'], 'date': '2026-11-23',
                                                'time': '10:30'})
        self.assertEqual(state, '成功')
        row = self.records.read()[0]
        self.assertEqual((row['date'], row['time'], row['created_by'], row['updated_by']),
                         ('2026-11-23', '10:30', 'Lan', 'Peter'))
        self.assertEqual(row['history'][-1]['date'], '2026-11-22')
        self.assertEqual(len(self.changes), 1)
        result, state, _ = self.records.invoke({'action': 'delete', 'id': hers['id']})
        self.assertEqual(state, '被拒绝')
        self.assertIn('cannot delete', result)
        self.assertEqual(len(self.records.read()), 1)

    def test_peter_deletes_his_own_and_lan_deletes_anything(self):
        his = self.records.write({'kind': 'note', 'title': '买猫粮'}, 'Peter')
        self.assertEqual(self.records.invoke({'action': 'delete', 'id': his['id']})[1], '成功')
        again = self.records.write({'kind': 'note', 'title': '再写一条'}, 'Peter')
        self.records.delete(again['id'])   # from the desktop page, as Lan
        self.assertEqual(self.records.read(), [])
        log = self.records.path.with_name('life-records-deleted.jsonl').read_text(encoding='utf-8')
        self.assertIn('"deleted_by": "Peter"', log)

    def test_peters_own_edits_and_lans_edits_send_no_notice(self):
        his = self.records.write({'kind': 'event', 'title': '他的', 'date': '2026-11-20'}, 'Peter')
        self.records.write({'id': his['id'], 'title': '他的改'}, 'Peter')
        hers = self.records.write({'kind': 'event', 'title': '她的', 'date': '2026-11-20'}, 'Lan')
        self.records.write({'id': hers['id'], 'title': '她自己改'}, 'Lan')
        self.records.write({'id': hers['id'], 'title': '她自己改'}, 'Peter')   # nothing actually changed
        self.assertEqual(self.changes, [])

    def test_turning_lans_note_into_a_daily_one_is_reported(self):
        note = self.records.write({'kind': 'note', 'title': '喝水'}, 'Lan')
        self.records.write({'id': note['id'], 'repeat': 'daily'}, 'Peter')
        self.assertEqual(len(self.changes), 1)
        self.assertEqual(self.changes[0][1]['repeat'], 'daily')

    def test_time_is_optional_events_only_and_checked(self):
        event = self.records.write({'kind': 'event', 'title': '看电影', 'date': '2026-11-20', 'time': '19:05'}, 'Lan')
        self.assertEqual(event['time'], '19:05')
        self.assertEqual(self.records.write({'id': event['id'], 'time': ''}, 'Lan')['time'], '')
        for bad in ('25:00', '7:00', '19:5', 'evening'):
            with self.assertRaises(ValueError):
                self.records.write({'kind': 'event', 'title': 'x', 'date': '2026-11-20', 'time': bad}, 'Lan')
        with self.assertRaises(ValueError):
            self.records.write({'kind': 'note', 'title': 'x', 'time': '10:00'}, 'Lan')

    def test_range_list_returns_every_event_in_order(self):
        for day, time in (('2026-11-21', ''), ('2026-11-20', '18:00'), ('2026-11-20', '09:00'), ('2026-12-01', '')):
            self.records.write({'kind': 'event', 'title': day + time, 'date': day, 'time': time}, 'Lan')
        rows = json.loads(self.records.invoke({'action': 'list', 'date_from': '2026-11-20',
                                               'date_to': '2026-11-23'})[0])
        self.assertEqual([r['title'] for r in rows], ['2026-11-2009:00', '2026-11-2018:00', '2026-11-21'])
        self.assertEqual(self.records.invoke({'action': 'list', 'date_from': '20-11'})[1], '被拒绝')


class DailyLogTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = SimpleNamespace(root=Path(self.temp.name), mutex=threading.RLock())
        self.records = LifeRecords(self.store, 'peter')

    def test_each_tick_is_logged_by_day_and_the_last_one_wins(self):
        self.records.ensure_checks_started('2026-11-18')
        todo = self.records.write({'kind': 'note', 'title': '写日记', 'repeat': 'daily'}, 'Lan')
        with mock.patch.object(life_records, 'now', return_value='2026-11-18T23:40:00+08:00'):
            self.records.write({'id': todo['id'], 'state': 'completed'}, 'Peter')
        with mock.patch.object(life_records, 'now', return_value='2026-11-19T10:00:00+08:00'):
            self.records.write({'id': todo['id'], 'state': 'completed'}, 'Lan')
            self.records.write({'id': todo['id'], 'state': 'active'}, 'Lan')
        checks = self.records.daily_checks()
        self.assertEqual(checks['started'], '2026-11-18')
        self.assertEqual(checks['days']['2026-11-18'][todo['id']]['state'], 'completed')
        self.assertEqual(checks['days']['2026-11-18'][todo['id']]['by'], 'Peter')
        self.assertEqual(checks['days']['2026-11-19'][todo['id']]['state'], 'unchecked')

    def test_a_tick_whose_log_line_failed_is_logged_when_saved_again_and_only_once(self):
        self.records.ensure_checks_started('2026-11-18')
        todo = self.records.write({'kind': 'note', 'title': '写日记', 'repeat': 'daily'}, 'Lan')
        with mock.patch.object(life_records, 'now', return_value='2026-11-18T21:00:00+08:00'):
            with mock.patch.object(LifeRecords, '_log_check', side_effect=OSError('locked')):
                with self.assertRaises(OSError):
                    self.records.write({'id': todo['id'], 'state': 'completed'}, 'Lan')
            self.assertNotIn('2026-11-18', self.records.daily_checks()['days'])
            self.records.write({'id': todo['id'], 'state': 'completed'}, 'Lan')
            self.records.write({'id': todo['id'], 'state': 'completed'}, 'Lan')
        lines = [l for l in self.records.checks_path.read_text(encoding='utf-8').splitlines() if '"day"' in l]
        self.assertEqual(len(lines), 1)
        self.assertEqual(self.records.daily_checks()['days']['2026-11-18'][todo['id']]['state'], 'completed')

    def test_a_tick_whose_log_line_failed_is_put_back_on_its_own_day_when_saved_the_next_day(self):
        self.records.ensure_checks_started('2026-11-18')
        todo = self.records.write({'kind': 'note', 'title': '写日记', 'repeat': 'daily'}, 'Lan')
        with mock.patch.object(life_records, 'now', return_value='2026-11-18T23:50:00+08:00'):
            with mock.patch.object(LifeRecords, '_log_check', side_effect=OSError('locked')):
                with self.assertRaises(OSError):
                    self.records.write({'id': todo['id'], 'state': 'completed'}, 'Peter')
        with mock.patch.object(life_records, 'now', return_value='2026-11-19T09:00:00+08:00'):
            self.records.write({'id': todo['id'], 'title': '写日记（睡前）'}, 'Lan')   # an edit, not a tick
            self.records.write({'id': todo['id'], 'title': '写日记'}, 'Lan')
        days = self.records.daily_checks()['days']
        self.assertEqual(days['2026-11-18'][todo['id']],
                         {'state': 'completed', 'at': '2026-11-18T23:50:00+08:00', 'by': 'Peter', 'title': '写日记'})
        self.assertNotIn('2026-11-19', days)   # no tick invented for the day of the retry
        lines = [l for l in self.records.checks_path.read_text(encoding='utf-8').splitlines() if '"day"' in l]
        self.assertEqual(len(lines), 1)

    def test_when_putting_yesterdays_line_back_fails_again_nothing_moves_on_and_the_next_save_retries(self):
        self.records.ensure_checks_started('2026-11-18')
        todo = self.records.write({'kind': 'note', 'title': '写日记', 'repeat': 'daily'}, 'Lan')
        with mock.patch.object(life_records, 'now', return_value='2026-11-18T23:50:00+08:00'):
            with mock.patch.object(LifeRecords, '_log_check', side_effect=OSError('locked')):
                with self.assertRaises(OSError):
                    self.records.write({'id': todo['id'], 'state': 'completed'}, 'Lan')
        with mock.patch.object(life_records, 'now', return_value='2026-11-19T09:00:00+08:00'):
            with mock.patch.object(LifeRecords, '_log_check', side_effect=OSError('locked')):
                with self.assertRaises(OSError):
                    self.records.write({'id': todo['id'], 'state': 'completed'}, 'Lan')
            saved = next(r for r in self.records.read() if r['id'] == todo['id'])
            self.assertEqual(saved['updated_at'], '2026-11-18T23:50:00+08:00')   # the record did not move on
            self.records.write({'id': todo['id'], 'state': 'completed'}, 'Lan')
        days = self.records.daily_checks()['days']
        self.assertEqual(days['2026-11-18'][todo['id']]['at'], '2026-11-18T23:50:00+08:00')
        self.assertEqual(days['2026-11-19'][todo['id']]['state'], 'completed')

    def test_a_line_cut_short_by_a_failed_write_does_not_swallow_the_retry(self):
        self.records.ensure_checks_started('2026-11-18')
        todo = self.records.write({'kind': 'note', 'title': '写日记', 'repeat': 'daily'}, 'Lan')
        real = LifeRecords._log_check

        def half_written(records, row, state, actor, stamp):
            with records.checks_path.open('a', encoding='utf-8') as handle:
                handle.write('{"day":"2026-11-18","id":"' + row['id'] + '","sta')
            raise OSError('disk full')
        with mock.patch.object(life_records, 'now', return_value='2026-11-18T23:50:00+08:00'):
            with mock.patch.object(LifeRecords, '_log_check', half_written):
                with self.assertRaises(OSError):
                    self.records.write({'id': todo['id'], 'state': 'completed'}, 'Lan')
        with mock.patch.object(life_records, 'now', return_value='2026-11-19T09:00:00+08:00'):
            self.records.write({'id': todo['id'], 'title': '写日记（睡前）'}, 'Lan')
        self.assertIs(LifeRecords._log_check, real)
        days = self.records.daily_checks()['days']
        self.assertEqual(days['2026-11-18'][todo['id']]['at'], '2026-11-18T23:50:00+08:00')

    def test_an_untick_whose_log_line_failed_is_corrected_on_its_own_day(self):
        self.records.ensure_checks_started('2026-11-18')
        todo = self.records.write({'kind': 'note', 'title': '写日记', 'repeat': 'daily'}, 'Lan')
        with mock.patch.object(life_records, 'now', return_value='2026-11-18T21:00:00+08:00'):
            self.records.write({'id': todo['id'], 'state': 'completed'}, 'Lan')
        with mock.patch.object(life_records, 'now', return_value='2026-11-18T22:00:00+08:00'):
            with mock.patch.object(LifeRecords, '_log_check', side_effect=OSError('locked')):
                with self.assertRaises(OSError):
                    self.records.write({'id': todo['id'], 'state': 'active'}, 'Lan')
        with mock.patch.object(life_records, 'now', return_value='2026-11-19T09:00:00+08:00'):
            self.records.write({'id': todo['id'], 'state': 'completed'}, 'Lan')
        days = self.records.daily_checks()['days']
        self.assertEqual(days['2026-11-18'][todo['id']]['state'], 'unchecked')
        self.assertEqual(days['2026-11-19'][todo['id']]['state'], 'completed')

    def test_a_done_note_that_becomes_daily_gets_its_first_log_line(self):
        self.records.ensure_checks_started('2026-11-18')
        with mock.patch.object(life_records, 'now', return_value='2026-11-18T09:00:00+08:00'):
            note = self.records.write({'kind': 'note', 'title': '拉伸', 'state': 'completed'}, 'Lan')
            self.records.write({'id': note['id'], 'repeat': 'daily'}, 'Lan')
        self.assertEqual(self.records.daily_checks()['days']['2026-11-18'][note['id']]['state'], 'completed')

    def test_ticks_made_on_the_start_day_before_the_log_existed_are_kept(self):
        with mock.patch.object(life_records, 'now', return_value='2026-11-18T08:00:00+08:00'):
            todo = self.records.write({'kind': 'note', 'title': '写日记', 'repeat': 'daily'}, 'Lan')
            self.records.write({'id': todo['id'], 'state': 'completed'}, 'Peter')
        self.records.checks_path.unlink()   # as on the day of the update: ticked before the log started
        with mock.patch.object(life_records, 'now', return_value='2026-11-18T12:00:00+08:00'):
            self.records.ensure_checks_started()
        checks = self.records.daily_checks()
        self.assertEqual(checks['started'], '2026-11-18')
        self.assertEqual(checks['days']['2026-11-18'][todo['id']]['by'], 'Peter')

    def test_days_before_the_log_are_rebuilt_from_record_history_only_as_completions(self):
        row = {'id': 'd1', 'kind': 'note', 'title': '成长日记', 'repeat': 'daily', 'owner': '共同', 'date': '2026-11-01',
               'state': 'completed', 'completed_at': '2026-11-17T00:24:04+08:00', 'created_by': 'Lan',
               'created_at': '2026-11-01T00:00:00+08:00', 'updated_by': 'Peter',
               'updated_at': '2026-11-17T00:24:04+08:00', 'content': '', 'mood': '',
               'history': [{'at': '2026-11-15T23:50:00+08:00', 'by': 'Peter', 'state': 'completed',
                            'title': '成长日记', 'content': ''},
                           {'at': '2026-11-16T00:00:01+08:00', 'by': 'Lan', 'state': 'active',
                            'title': '成长日记', 'content': ''}]}
        self.records.path.parent.mkdir(parents=True, exist_ok=True)
        self.records.path.write_text(json.dumps({'records': [row]}), encoding='utf-8')
        self.records.ensure_checks_started('2026-11-18')
        days = self.records.daily_checks()['days']
        self.assertTrue(days['2026-11-15']['d1']['reconstructed'])
        self.assertEqual(days['2026-11-17']['d1']['state'], 'completed')
        self.assertNotIn('2026-11-16', days)   # an unchecked day is never invented


class MoodTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = SimpleNamespace(root=Path(self.temp.name), mutex=threading.RLock())
        self.records = LifeRecords(self.store, 'peter')

    def test_each_person_sets_and_clears_only_their_own_slot(self):
        with mock.patch.object(life_records, 'now', return_value='2026-11-18T12:00:00+08:00'):
            self.records.set_mood('2026-11-18', 'Lan', '🥰')
            result, state, _ = self.records.invoke({'action': 'mood', 'emoji': '😴'})
            self.assertEqual(state, '成功')
            self.assertEqual({k: v['emoji'] for k, v in self.records.moods()['2026-11-18'].items()},
                             {'Lan': '🥰', 'Peter': '😴'})
            self.records.invoke({'action': 'mood', 'emoji': ''})
            self.assertEqual(set(self.records.moods()['2026-11-18']), {'Lan'})
            self.records.set_mood('2026-11-18', 'Lan', '')
            self.assertEqual(self.records.moods(), {})
            with self.assertRaises(ValueError): self.records.set_mood('2026-11-19', 'Lan', '🥰')
            with self.assertRaises(ValueError): self.records.set_mood('2026-11-18', 'Ghost', '🥰')
            self.assertEqual(self.records.invoke({'action': 'mood', 'emoji': 'happy'})[1], '被拒绝')

    def test_any_emoji_is_accepted_and_text_is_not(self):
        for emoji in ('🥰', '👩🏽‍💻', '🏳️‍🌈', '❤️', '🫠', '🇨🇳'):
            self.assertTrue(valid_emoji(emoji), emoji)
        for bad in ('', 'ok', ':)', '🥰 🥰', '😀' * 20, None, '开心', '，', '1'):
            self.assertFalse(valid_emoji(bad), bad)

    def test_every_emoji_in_the_full_picker_can_be_saved(self):
        # desktop/emoji-data.js is what "更多 emoji" offers; each one must pass the server's check.
        text = (Path(__file__).resolve().parents[1] / 'desktop' / 'emoji-data.js').read_text(encoding='utf-8')
        groups = json.loads(text.split('window.VEIL_EMOJI=', 1)[1].rstrip().rstrip(';'))
        emoji = [item[0] for group in groups for item in group[1]]
        self.assertGreater(len(emoji), 1500)
        self.assertEqual([e for e in emoji if not valid_emoji(e)], [])
        for keycap in ('#️⃣', '1️⃣', '‼️', 'ℹ️', '〽️'):
            self.assertTrue(valid_emoji(keycap), keycap)


if __name__ == '__main__':
    unittest.main()
