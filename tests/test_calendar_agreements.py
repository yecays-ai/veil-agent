import json
import threading
import tempfile
import unittest
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path
from types import SimpleNamespace

from calendar_agreements import CalendarAgreements
from life_records import LifeRecords


@dataclass
class Config:
    id: str = 'peter'
    max_output_tokens: int = 2048


class CalendarAgreementsTest(unittest.TestCase):
    def test_clear_agreement_is_recorded_and_visible(self):
        root=Path.cwd()/'.task-test-temp'
        root.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=root) as directory:
            logs=[]
            store=SimpleNamespace(root=Path(directory),mutex=threading.RLock(),
                                  read=lambda path:[],append=lambda path,event:logs.append((path,event)))
            target=(date.today()+timedelta(days=1)).isoformat()
            model=lambda *args,**kwargs:(json.dumps({'action':'create','date':target,'time':'15:00',
                                                       'title':'Get package','remind':False}),12,25,0)
            budget=SimpleNamespace(reserve=lambda *args,**kwargs:('reservation',1),
                                   settle=lambda *args,**kwargs:None,
                                   settle_estimate=lambda *args,**kwargs:None)
            chat=SimpleNamespace(cfg=Config(),store=store,path='chat.jsonl',budget=budget,model=model,
                                 life_records=LifeRecords(store,'peter'))
            notices=[]
            agent=CalendarAgreements(chat,notices.append)
            agent.lock.acquire()
            agent._process('明天下午三点一起取快递','好，约定了')
            rows=chat.life_records.read()
            self.assertEqual(len(rows),1)
            self.assertTrue(rows[0]['auto_recorded'])
            self.assertEqual(rows[0]['time'],'15:00')
            self.assertEqual(notices[0].life_id,rows[0]['id'])
            self.assertEqual(logs[0][1]['before'],None)
            chat.life_records.set_auto_record_enabled(False)
            self.assertFalse(chat.life_records.auto_record_enabled())
            agent.consider('明天三点再去','好')
            self.assertEqual(len(chat.life_records.read()),1)
