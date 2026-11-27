import json
import threading
from types import SimpleNamespace

import tempfile
import unittest
from datetime import datetime, timezone, timedelta
from unittest.mock import patch
from pathlib import Path
from veil.life.life_records import LifeRecords, effective, fold_completed
from veil.core.agent_types import now


class LifeRecordsTest(unittest.TestCase):
    def test_reminder_claims_once_and_skips_inactive_events(self):
        temp_root=Path.cwd() / '.task-test-temp'
        temp_root.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=temp_root) as directory:
            store=SimpleNamespace(root=Path(directory),mutex=threading.RLock())
            records=LifeRecords(store,'peter')
            with patch('veil.life.life_records.now',return_value='2026-11-23T19:00:00+08:00'):
                event=records.write({'kind':'event','title':'Meet','date':'2026-11-23','time':'20:00',
                                     'remind':True,'remind_before':15},'Lan')
            at=datetime(2026,11,23,19,45,tzinfo=timezone(timedelta(hours=8)))
            self.assertIsNone(records.claim_due_reminder(at-timedelta(minutes=1)))
            due=records.claim_due_reminder(at)
            self.assertEqual(due['id'],event['id'])
            self.assertEqual(records.claim_due_reminder(at)['id'],event['id'])
            records.complete_due_reminder(due)
            self.assertIsNone(records.claim_due_reminder(at))
            records.write({'id':event['id'],'state':'cancelled'},'Lan')
            self.assertIsNone(records.claim_due_reminder(at+timedelta(minutes=1)))
            with self.assertRaises(ValueError):
                records.write({'kind':'event','title':'No time','remind':True},'Lan')

    def test_title_edit_after_claim_uses_latest_event_and_can_complete(self):
        temp_root=Path.cwd() / '.task-test-temp'
        temp_root.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=temp_root) as directory:
            store=SimpleNamespace(root=Path(directory),mutex=threading.RLock())
            records=LifeRecords(store,'peter')
            with patch('veil.life.life_records.now',return_value='2026-11-23T19:00:00+08:00'):
                event=records.write({'kind':'event','title':'Meet','date':'2026-11-23','time':'20:00',
                                     'remind':True,'remind_before':15},'Lan')
            due=records.claim_due_reminder(datetime(2026,11,23,19,45,5,
                                                     tzinfo=timezone(timedelta(hours=8))))
            self.assertEqual(due['title'],'Meet')
            with patch('veil.life.life_records.now',return_value='2026-11-23T19:45:06+08:00'):
                records.write({'id':event['id'],'title':'Meet outside'},'Lan')
            current=records.current_due_reminder(due)
            self.assertEqual(current['title'],'Meet outside')
            self.assertTrue(records.reminder_still_current(due))
            records.complete_due_reminder(current)
            self.assertIsNone(records.claim_due_reminder(datetime(2026,11,23,19,45,7,
                                                                  tzinfo=timezone(timedelta(hours=8)))))

    def test_cancel_invalidates_claim_and_reschedule_gets_new_claim(self):
        temp_root=Path.cwd() / '.task-test-temp'
        temp_root.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=temp_root) as directory:
            store=SimpleNamespace(root=Path(directory),mutex=threading.RLock())
            records=LifeRecords(store,'peter')
            with patch('veil.life.life_records.now',return_value='2026-11-23T19:00:00+08:00'):
                event=records.write({'kind':'event','title':'Meet','date':'2026-11-23','time':'20:00',
                                     'remind':True,'remind_before':15},'Lan')
            at=datetime(2026,11,23,19,45,tzinfo=timezone(timedelta(hours=8)))
            claimed=records.claim_due_reminder(at)
            with patch('veil.life.life_records.now',return_value='2026-11-23T19:45:01+08:00'):
                records.write({'id':event['id'],'state':'cancelled'},'Lan')
            self.assertIsNone(records.current_due_reminder(claimed))
            self.assertIsNone(records.claim_due_reminder(at+timedelta(minutes=1)))

            with patch('veil.life.life_records.now',return_value='2026-11-23T19:45:02+08:00'):
                records.write({'id':event['id'],'state':'active','date':'2026-11-23','time':'21:00'},'Lan')
            self.assertIsNone(records.current_due_reminder(claimed))
            new_due=records.claim_due_reminder(datetime(2026,11,23,20,45,
                                                         tzinfo=timezone(timedelta(hours=8))))
            self.assertEqual(new_due['id'],event['id'])
            self.assertEqual(new_due['time'],'21:00')
            records.complete_due_reminder(new_due)

    def test_cancelled_claim_is_not_marked_processed(self):
        temp_root=Path.cwd() / '.task-test-temp'
        temp_root.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=temp_root) as directory:
            store=SimpleNamespace(root=Path(directory),mutex=threading.RLock())
            records=LifeRecords(store,'peter')
            event=records.write({'kind':'event','title':'Meet','date':'2026-11-23','time':'20:00',
                                 'remind':True,'remind_before':15},'Lan')
            due=records.claim_due_reminder(datetime(2026,11,23,19,45,
                                                    tzinfo=timezone(timedelta(hours=8))))
            with patch('veil.life.life_records.now',return_value='2026-11-23T19:45:06+08:00'):
                records.write({'id':event['id'],'state':'cancelled'},'Lan')
            self.assertIsNone(records.current_due_reminder(due))

    def test_daily_reminder_uses_original_day(self):
        temp_root=Path.cwd() / '.task-test-temp'
        temp_root.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=temp_root) as directory:
            store=SimpleNamespace(root=Path(directory),mutex=threading.RLock())
            records=LifeRecords(store,'peter')
            todo=records.write({'kind':'note','title':'Diary','repeat':'daily'},'Lan')
            day=datetime.now().astimezone().replace(hour=23,minute=30,second=0,microsecond=0)
            reminder=records.claim_daily_reminder(day)
            self.assertEqual(reminder['ids'],[todo['id']])
            self.assertIsNone(records.claim_daily_reminder(day))
            after_midnight=(day+timedelta(minutes=72)).isoformat()
            records.daily_reminder_path.unlink()
            with patch('veil.life.life_records.now',return_value=after_midnight):
                result=records.write({'id':todo['id'],'state':'completed','date':day.date().isoformat()},'Peter')
            self.assertEqual(result['completion_day'],day.date().isoformat())
            self.assertEqual(result['today_state'],'active')
            past=records.daily_checks()['days'][day.date().isoformat()][todo['id']]
            self.assertEqual(past['state'],'completed')
            self.assertEqual(past['at'][:10],(day+timedelta(days=1)).date().isoformat())
            self.assertEqual(records.read()[0]['state'],'active')
            with patch('veil.life.life_records.now',return_value=(day+timedelta(minutes=73)).isoformat()):
                records.write({'id':todo['id'],'title':'Diary updated'},'Lan')
            self.assertEqual(records.daily_checks()['days'][day.date().isoformat()][todo['id']]['state'],'completed')

    def test_automatic_change_can_be_undone_only_while_unedited(self):
        temp_root=Path.cwd() / '.task-test-temp'
        temp_root.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=temp_root) as directory:
            store=SimpleNamespace(root=Path(directory),mutex=threading.RLock())
            records=LifeRecords(store,'peter')
            row=records.write({'kind':'event','title':'Walk','date':'2026-11-25'},'Peter',auto=True)
            path=records.path.with_name('calendar-agreements.jsonl')
            path.write_text(json.dumps({'at':now(),'undo_id':'a'*24,'before':None,'after':row})+'\n',encoding='utf-8')
            self.assertIsNone(records.undo_auto('a'*24))
            self.assertEqual(records.read(),[])
            with self.assertRaises(ValueError): records.undo_auto('a'*24)

    def test_undo_refuses_manual_reminder_change(self):
        temp_root=Path.cwd() / '.task-test-temp'
        temp_root.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=temp_root) as directory:
            store=SimpleNamespace(root=Path(directory),mutex=threading.RLock())
            records=LifeRecords(store,'peter')
            before=records.write({'kind':'event','title':'Walk','date':'2026-11-25','time':'20:00'},'Peter')
            after=records.write({'id':before['id'],'title':'Long walk'},'Peter',auto=True)
            path=records.path.with_name('calendar-agreements.jsonl')
            path.write_text(json.dumps({'at':now(),'undo_id':'b'*24,'before':before,'after':after})+'\n',encoding='utf-8')
            records.write({'id':after['id'],'remind':True,'remind_before':15},'Lan')
            with self.assertRaisesRegex(ValueError,'record changed'):
                records.undo_auto('b'*24)

    def test_revision_refuses_automatic_write_after_a_manual_change(self):
        temp_root=Path.cwd() / '.task-test-temp'
        temp_root.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=temp_root) as directory:
            store=SimpleNamespace(root=Path(directory),mutex=threading.RLock())
            records=LifeRecords(store,'peter')
            # A coarse clock can give two writes the same updated_at. The revision must still catch the edit.
            with patch('veil.life.life_records.now',return_value='2026-11-25T20:00:00+08:00'):
                before=records.write({'kind':'event','title':'Walk','date':'2026-11-25','time':'20:00'},'Peter')
                manual=records.write({'id':before['id'],'time':'20:30'},'Lan')
                self.assertEqual(manual['updated_at'],before['updated_at'])
                self.assertEqual(manual['revision'],before['revision']+1)
                with self.assertRaisesRegex(ValueError,'record changed'):
                    records.write({'id':before['id'],'title':'Automatic title'},'Peter',auto=True,
                                  expected_revision=before['revision'])

    def test_undo_refuses_a_manual_change_with_the_same_timestamp(self):
        temp_root=Path.cwd() / '.task-test-temp'
        temp_root.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=temp_root) as directory:
            store=SimpleNamespace(root=Path(directory),mutex=threading.RLock())
            records=LifeRecords(store,'peter')
            with patch('veil.life.life_records.now',return_value='2026-11-25T20:00:00+08:00'):
                before=records.write({'kind':'event','title':'Walk','date':'2026-11-25','time':'20:00'},'Peter')
                after=records.write({'id':before['id'],'title':'Long walk'},'Peter',auto=True)
                path=records.path.with_name('calendar-agreements.jsonl')
                path.write_text(json.dumps({'at':'2026-11-25T20:00:00+08:00','undo_id':'c'*24,
                                            'before':before,'after':after})+'\n',encoding='utf-8')
                manual=records.write({'id':after['id'],'remind':True,'remind_before':15},'Lan')
                self.assertEqual(manual['updated_at'],after['updated_at'])
                with self.assertRaisesRegex(ValueError,'record changed'):
                    records.undo_auto('c'*24)

    def test_daily_hint_marks_the_previous_day_after_midnight(self):
        temp_root=Path.cwd() / '.task-test-temp'
        temp_root.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=temp_root) as directory:
            store=SimpleNamespace(root=Path(directory),mutex=threading.RLock())
            records=LifeRecords(store,'peter')
            with patch('veil.life.life_records.now',return_value='2026-11-20T23:30:00+08:00'):
                todo=records.write({'kind':'note','title':'Diary','repeat':'daily'},'Lan')
            with patch('veil.life.life_records.now',return_value='2026-11-20T23:51:00+08:00'):
                hint=records.claim_daily_hint()
            self.assertIn('2026-11-20',hint)
            with patch('veil.life.life_records.now',return_value='2026-11-21T00:10:00+08:00'):
                result=records.write({'id':todo['id'],'state':'completed'},'Peter')
            self.assertEqual(result['completion_day'],'2026-11-20')
            yesterday=records.daily_checks()['days']['2026-11-20'][todo['id']]
            self.assertEqual(yesterday['state'],'completed')
            self.assertEqual(yesterday['at'][:10],'2026-11-21')
            self.assertNotIn('2026-11-21',records.daily_checks()['days'])
            self.assertEqual(records.read()[0]['state'],'active')

    def test_shared_records_persist_status_and_protect_lan_owner(self):
        temp_root=Path.cwd() / '.task-test-temp'
        temp_root.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=temp_root) as directory:
            store=SimpleNamespace(root=Path(directory),mutex=threading.RLock())
            records=LifeRecords(store,'peter')
            row=records.write({'kind':'plan','title':'Play together','content':'Someday','owner':'共同','date':'2026-11-01'},'Lan')
            changed=records.write({'id':row['id'],'state':'completed'},'Peter')
            self.assertTrue(changed['completed_at'])
            self.assertEqual(changed['created_by'],'Lan')
            self.assertEqual(changed['updated_by'],'Peter')
            self.assertEqual(LifeRecords(store,'peter').read()[0]['state'],'completed')
            self.assertEqual(len(records.read()),1)
            private=records.write({'kind':'note','title':'Lan diary','owner':'Lan'},'Lan')
            # Ticket 385: the calendar is shared; Peter may edit what Lan wrote (she is told) but not delete it.
            self.assertEqual(records.write({'id':private['id'],'title':'Lan diary'},'Peter')['created_by'],'Lan')
            with self.assertRaises(ValueError): records.delete(private['id'],'Peter')
            # Ticket 427: plans are calendar records now; Peter creates and converts them here.
            self.assertEqual(records.write({'kind':'plan','title':'Duplicate plan'},'Peter')['kind'],'plan')
            self.assertEqual(records.write({'id':private['id'],'kind':'plan'},'Peter')['kind'],'plan')
            with self.assertRaises(ValueError): records.write({'kind':'event','title':'Bad date','date':'2026-02-31'},'Lan')
            peter_note=records.write({'kind':'note','title':'Peter note'},'Peter')
            self.assertEqual(records.write({'id':peter_note['id'],'kind':'plan'},'Peter')['kind'],'plan')
            self.assertEqual(records.read()[1]['title'],'Lan diary')

    def test_daily_todo_resets_at_midnight_and_one_off_notes_fold(self):
        temp_root=Path.cwd() / '.task-test-temp'
        temp_root.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=temp_root) as directory:
            store=SimpleNamespace(root=Path(directory),mutex=threading.RLock())
            records=LifeRecords(store,'peter')
            todo=records.write({'kind':'note','title':'Write diary','repeat':'daily','owner':'Peter'},'Lan')
            done=records.write({'id':todo['id'],'state':'completed'},'Peter')
            self.assertEqual(effective(done)['state'],'completed')
            self.assertEqual(effective(done,'2999-01-01')['state'],'active')
            # completing again on a later day works and is not blocked by yesterday's check
            stale=dict(done,completed_at='2000-01-01T00:00:00')
            self.assertEqual(effective(stale)['state'],'active')
            records.path.write_text(json.dumps({'records':[stale]}),encoding='utf-8')
            again=records.write({'id':todo['id'],'state':'completed'},'Peter')
            self.assertTrue(again['completed_at'].startswith(effective(again)['completed_at'][:4]))
            self.assertEqual(effective(again)['state'],'completed')
            with self.assertRaises(ValueError): records.write({'kind':'event','title':'x','repeat':'daily'},'Lan')
            with self.assertRaises(ValueError): records.write({'kind':'diary','title':'new diary'},'Lan')
            # Ticket 385: moods moved to the two daily slots; a record no longer takes one, old text is kept.
            event=records.write({'kind':'event','title':'Out','mood':'😊','date':'2026-11-03'},'Peter')
            self.assertEqual(event['mood'],'')
            rows=[{'id':str(i),'kind':'note','state':'completed','completed_at':'2026-11-%02dT10:00:00'%(i%28+1)} for i in range(35)]
            visible,folded=fold_completed(rows)
            self.assertEqual((len(visible),folded),(30,5))
            self.assertEqual({r['id'] for r in visible},{str(i) for i in range(35)}-{'0','1','28','29','30'})
            records.path.write_text(json.dumps({'records':[{'id':'old','kind':'diary','title':'Old diary','owner':'共同','date':'2026-10-01','state':'active','content':'','created_by':'Peter','created_at':'x','updated_by':'Peter','updated_at':'x','history':[]}]}),encoding='utf-8')
            self.assertEqual(len(records.read()),1)
            self.assertEqual(json.loads(records.invoke({'action':'list'})[0]),[])
            with self.assertRaises(ValueError): records.write({'id':'old','title':'changed'},'Peter')
            self.assertEqual(records.read()[0]['title'],'Old diary')
            note=records.write({'kind':'note','title':'Visible','owner':'共同'},'Lan')
            with self.assertRaises(ValueError): records.write({'id':note['id'],'kind':'diary'},'Lan')
            self.assertEqual([r['title'] for r in json.loads(records.invoke({'action':'list'})[0])],['Visible'])

    def test_delete_keeps_a_copy_and_only_removes_events_and_notes(self):
        temp_root=Path.cwd() / '.task-test-temp'
        temp_root.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=temp_root) as directory:
            store=SimpleNamespace(root=Path(directory),mutex=threading.RLock())
            records=LifeRecords(store,'peter')
            note=records.write({'kind':'note','title':'Typo','owner':'共同'},'Lan')
            plan=records.write({'kind':'plan','title':'Keep','owner':'共同'},'Lan')
            records.delete(note['id'])
            self.assertEqual([r['id'] for r in records.read()],[plan['id']])
            saved=(records.path.with_name('life-records-deleted.jsonl')).read_text(encoding='utf-8')
            self.assertIn('Typo',saved)
            with self.assertRaises(ValueError): records.delete(plan['id'])
            with self.assertRaises(ValueError): records.delete('missing')

if __name__ == '__main__': unittest.main()
