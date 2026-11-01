import json
import threading
from types import SimpleNamespace

import tempfile
import unittest
from pathlib import Path
from life_records import LifeRecords


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
            private=records.write({'kind':'diary','title':'Lan diary','owner':'Lan'},'Lan')
            with self.assertRaises(ValueError): records.write({'id':private['id'],'title':'Changed'},'Peter')
            with self.assertRaises(ValueError): records.write({'kind':'plan','title':'Duplicate plan'},'Peter')
            with self.assertRaises(ValueError): records.write({'id':private['id'],'kind':'plan'},'Peter')
            with self.assertRaises(ValueError): records.write({'kind':'event','title':'Bad date','date':'2026-02-31'},'Lan')
            peter_note=records.write({'kind':'note','title':'Peter note'},'Peter')
            with self.assertRaises(ValueError): records.write({'id':peter_note['id'],'kind':'plan'},'Peter')
            self.assertEqual(records.read()[1]['title'],'Lan diary')

if __name__ == '__main__': unittest.main()
