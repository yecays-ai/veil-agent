import json
import threading
import tempfile
import unittest
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path
from types import SimpleNamespace

from calendar_agreements import ACCEPT, TIME_WORDS, CalendarAgreements
from life_records import LifeRecords


@dataclass
class Config:
    id: str = 'peter'
    max_output_tokens: int = 2048


class CalendarAgreementsTest(unittest.TestCase):
    def test_prefilter_recognizes_chinese_times_and_rejects_negations(self):
        for text in ('就八点吧','十点见','20:00'):
            self.assertTrue(TIME_WORDS.search(text),text)
        for text in ('不行','不好','不可以','不同意','not sure'):
            self.assertFalse(ACCEPT.search(text),text)
        for text in ('好，就这么定了','行吧','可以'):
            self.assertTrue(ACCEPT.search(text),text)

    def test_cancelled_agreement_is_revived_by_a_new_agreement(self):
        root=Path.cwd()/'.task-test-temp'
        root.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=root) as directory:
            logs=[]
            def append(path,event):
                logs.append((path,event))
                target=Path(directory)/path; target.parent.mkdir(parents=True,exist_ok=True)
                with target.open('a',encoding='utf-8') as handle:
                    handle.write(json.dumps(event,ensure_ascii=False)+'\n')
            store=SimpleNamespace(root=Path(directory),mutex=threading.RLock(),
                                  read=lambda path:[{'role':'user','content':'明天下午三点取快递'}],
                                  append=append)
            records=LifeRecords(store,'peter')
            target=(date.today()+timedelta(days=1)).isoformat()
            event=records.write({'kind':'event','title':'Package','date':target,'time':'15:00',
                                 'state':'cancelled'},'Lan')
            def model(*args,**kwargs):
                return json.dumps({'action':'create','date':target,'time':'15:00',
                                   'title':'Package','remind':False}),12,25,0
            budget=SimpleNamespace(reserve=lambda *args,**kwargs:('reservation',1),
                                   settle=lambda *args,**kwargs:None,
                                   settle_estimate=lambda *args,**kwargs:None)
            chat=SimpleNamespace(cfg=Config(),store=store,path='chat.jsonl',budget=budget,model=model,
                                 life_records=records)
            notices=[]
            CalendarAgreements(chat,notices.append)._process('明天下午三点取快递','好')
            rows=records.read()
            self.assertEqual(len(rows),1)
            self.assertEqual(rows[0]['id'],event['id'])
            self.assertEqual(rows[0]['state'],'active')
            self.assertEqual(logs[-1][1]['before']['state'],'cancelled')
            self.assertTrue(notices and notices[0].calendar_undo_id)
            records.undo_auto(logs[-1][1]['undo_id'])
            self.assertEqual(records.read()[0]['state'],'cancelled')

    def test_clear_agreement_is_recorded_and_visible(self):
        root=Path.cwd()/'.task-test-temp'
        root.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=root) as directory:
            logs=[]
            store=SimpleNamespace(root=Path(directory),mutex=threading.RLock(),
                                  read=lambda path:[{'role':'user','content':'明天下午三点取快递'}],
                                  append=lambda path,event:logs.append((path,event)))
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

    def test_confirm_and_cancel_turns_queue_in_order(self):
        root=Path.cwd()/'.task-test-temp'
        root.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=root) as directory:
            logs=[]; first_started=threading.Event(); release=threading.Event(); calls=[]
            store=SimpleNamespace(root=Path(directory),mutex=threading.RLock(),
                                  read=lambda path:[{'role':'user','content':'明天下午三点取快递'}],
                                  append=lambda path,event:logs.append((path,event)))
            target=(date.today()+timedelta(days=1)).isoformat()
            records=LifeRecords(store,'peter')
            def model(*args,**kwargs):
                calls.append(1)
                if len(calls)==1:
                    first_started.set(); release.wait(2)
                    decision={'action':'create','date':target,'time':'15:00','title':'Package','remind':False}
                else:
                    decision={'action':'cancel','id':records.read()[0]['id']}
                return json.dumps(decision),12,25,0
            budget=SimpleNamespace(reserve=lambda *args,**kwargs:('reservation',1),
                                   settle=lambda *args,**kwargs:None,
                                   settle_estimate=lambda *args,**kwargs:None)
            chat=SimpleNamespace(cfg=Config(),store=store,path='chat.jsonl',budget=budget,model=model,
                                 life_records=records)
            agent=CalendarAgreements(chat,lambda notice:None)
            agent.consider('好，就这么定了','那就说好了')
            self.assertTrue(first_started.wait(1))
            agent.consider('取消刚才的约定','好，取消')
            worker=agent.worker
            release.set(); worker.join(3)
            self.assertEqual(len(calls),2)
            self.assertEqual(records.read()[0]['state'],'cancelled')

    def test_auto_update_aborts_if_record_changed_during_model_call(self):
        root=Path.cwd()/'.task-test-temp'
        root.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=root) as directory:
            logs=[]
            store=SimpleNamespace(root=Path(directory),mutex=threading.RLock(),
                                  read=lambda path:[],append=lambda path,event:logs.append((path,event)))
            records=LifeRecords(store,'peter')
            event=records.write({'kind':'event','title':'Movie','date':(date.today()+timedelta(days=1)).isoformat(),
                                 'time':'15:00'},'Lan')
            def model(*args,**kwargs):
                records.write({'id':event['id'],'time':'16:00'},'Lan')
                return json.dumps({'action':'update','id':event['id'],'title':'New movie'}),12,25,0
            budget=SimpleNamespace(reserve=lambda *args,**kwargs:('reservation',1),
                                   settle=lambda *args,**kwargs:None,
                                   settle_estimate=lambda *args,**kwargs:None)
            chat=SimpleNamespace(cfg=Config(),store=store,path='chat.jsonl',budget=budget,model=model,
                                 life_records=records)
            CalendarAgreements(chat,lambda notice:None)._process('明天电影改个名字','好')
            current=records.read()[0]
            self.assertEqual(current['time'],'16:00')
            self.assertEqual(current['title'],'Movie')
