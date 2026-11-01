import json
import threading
from types import SimpleNamespace

import tempfile
import unittest
from pathlib import Path
from life_records import LifeRecords, effective, fold_completed


class LifeRecordsTest(unittest.TestCase):
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
            with self.assertRaises(ValueError): records.write({'id':private['id'],'title':'Changed'},'Peter')
            with self.assertRaises(ValueError): records.write({'kind':'plan','title':'Duplicate plan'},'Peter')
            with self.assertRaises(ValueError): records.write({'id':private['id'],'kind':'plan'},'Peter')
            with self.assertRaises(ValueError): records.write({'kind':'event','title':'Bad date','date':'2026-02-31'},'Lan')
            peter_note=records.write({'kind':'note','title':'Peter note'},'Peter')
            with self.assertRaises(ValueError): records.write({'id':peter_note['id'],'kind':'plan'},'Peter')
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
            event=records.write({'kind':'event','title':'Out','mood':'😊','date':'2026-11-03'},'Peter')
            self.assertEqual(event['mood'],'😊')
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

if __name__ == '__main__': unittest.main()
