"""Conservative appointment capture from one completed chat turn."""
import json
import re
import threading
import secrets
from collections import deque
from dataclasses import replace
from datetime import date, timedelta

from veil import prompts
from veil.core.agent_types import now

TIME_WORDS = re.compile(r'明天|后天|下周|周[一二三四五六日天]|星期[一二三四五六日天]|[零一二三四五六七八九十两]{1,3}点(?:半|一刻|整|钟|[0-5]?\d分)?|\d{1,2}[号日点時时]|\d{1,2}[:：]\d{2}|上午|下午|晚上|tomorrow|next week|monday|tuesday|wednesday|thursday|friday|saturday|sunday|\d{1,2}\s*(?:am|pm)', re.I)
ACCEPT = re.compile(r'(?<![不没未别甭])(?:可以|约定|没问题|同意)(?![不没])'
                    r'|(?<![不没未别甭你您])好(?![不没])'
                    r'|(?<![不没未别甭银商])行(?=[吧啊呀，。！？,.\s]|$)'
                    r'|(?<!not )(?<!no )\b(?:yes|sure|okay|ok|deal)\b',re.I)
CANCEL = re.compile(r'取消|不去了|不看了|算了|cancel|call it off',re.I)


class CalendarAgreements:
    def __init__(self, chat, output):
        self.chat=chat
        self.output=output
        self.lock=threading.Lock()
        self.pending=deque()
        self.worker=None

    def consider(self, user, reply):
        try:
            if not self.chat.life_records.auto_record_enabled(): return
        except (OSError,ValueError,UnicodeError):
            return
        if not isinstance(user,str) or not isinstance(reply,str):
            return
        recent=' '.join(str(r.get('content','')) for r in self.chat.store.read(self.chat.path)[-6:]
                        if isinstance(r,dict) and r.get('role') in ('user','assistant'))
        if not (TIME_WORDS.search(user) or (TIME_WORDS.search(reply) and ACCEPT.search(user))
                or CANCEL.search(user) or (ACCEPT.search(user) and TIME_WORDS.search(recent))):
            return
        with self.lock:
            self.pending.append((user,reply))
            if self.worker is not None and self.worker.is_alive():
                return
            self.worker=threading.Thread(target=self._drain,daemon=True)
            self.worker.start()

    def _drain(self):
        while True:
            with self.lock:
                if not self.pending:
                    self.worker=None
                    return
                user,reply=self.pending.popleft()
            self._process(user,reply)

    def _process(self, user, reply):
        chat=self.chat
        try:
            today=date.fromisoformat(now()[:10]); end=today+timedelta(days=60)
            existing=[r for r in chat.life_records.read() if r.get('kind')=='event' and
                      today.isoformat()<=r.get('date','')<=end.isoformat()]
            context=[{'role':'user' if r.get('role')=='user' else 'assistant','content':r.get('content','')}
                     for r in chat.store.read(chat.path)[-6:] if r.get('role') in ('user','assistant')]
            instruction=prompts.fill(prompts.text('life.calendar.agreement'),{
                'today':f'{today.isoformat()} ({today.strftime("%A")})',
                'existing':json.dumps(existing,ensure_ascii=False)[:16000],
                'recent_chat':json.dumps(context,ensure_ascii=False)[:8000],
                'current_turn':json.dumps({'user':user,'assistant':reply},ensure_ascii=False)})
            cfg=replace(chat.cfg,max_output_tokens=512)
            reservation,amount=chat.budget.reserve(cfg,accounting='calendar-agreement',category='calendar_agreements')
            try:
                output,prompt,completion,cached=chat.model([{'role':'user','content':instruction}],
                                                             json_output=True,max_tokens=512,thinking='disabled')
            except Exception:
                chat.budget.settle_estimate((reservation,amount),accounting='calendar-agreement-failed')
                raise
            try:
                chat.budget.settle(reservation,cfg,prompt,completion,cached,
                                   accounting='calendar-agreement',category='calendar_agreements')
            except Exception:
                chat.budget.settle_estimate((reservation,amount),accounting='calendar-agreement-usage-invalid')
                raise
            decision=json.loads(output)
            if not chat.life_records.auto_record_enabled(): return
            action=decision.get('action')
            if action=='none': return
            if action=='create':
                before=None
                day=decision.get('date'); title=decision.get('title'); when=decision.get('time','')
                if (not isinstance(day,str) or not today<=date.fromisoformat(day)<=end or
                        not isinstance(title,str) or not title.strip() or len(title)>240 or
                        not isinstance(when,str) or when and not re.fullmatch(r'([01]\d|2[0-3]):[0-5]\d',when) or
                        type(decision.get('remind',False)) is not bool or decision.get('remind') and not when): return
                match=next((r for r in existing if r.get('date')==day and r.get('time','')==when
                            and r.get('title','').casefold()==title.casefold()),None)
                if match is not None and match.get('state')!='cancelled': return
                if match is not None:
                    before=match
                    body={'id':match['id'],'state':'active'}
                    if 'remind' in decision: body['remind']=decision['remind']
                    row=chat.life_records.write(body,'Peter',auto=True,
                                                expected_revision=match.get('revision',0))
                    notice='已记到日历：'+day+' '+(when+' ' if when else '')+title
                else:
                    before=None
                    row=chat.life_records.write({'kind':'event','title':title,'date':day,'time':when,
                                                 'remind':decision.get('remind',False)},'Peter',auto=True)
                    notice='已记到日历：'+day+' '+(when+' ' if when else '')+title
            elif action in ('update','cancel'):
                matches=[r for r in existing if r.get('id')==decision.get('id')]
                if len(matches)!=1: return
                before=matches[0]
                body={'id':before['id']}
                if action=='cancel': body['state']='cancelled'
                else:
                    for key in ('date','time','title'):
                        if key in decision: body[key]=decision[key]
                    if len(body)==1: return
                    if 'date' in body and not today<=date.fromisoformat(body['date'])<=end: return
                row=chat.life_records.write(body,'Peter',auto=True,
                                             expected_revision=before.get('revision',0))
                notice=('已标为取消：' if action=='cancel' else '已改日历：')+row['title']
            else: return
            undo_id=secrets.token_hex(12)
            chat.store.append(f'{chat.cfg.id}/calendar-agreements.jsonl',
                              {'at':now(),'undo_id':undo_id,'before':before,'after':row})
            chat.pending_calendar_context=notice
            from veil.core.veil_agent import LifeNotice
            line=LifeNotice(notice,row['id'])
            line.calendar_undo_id=undo_id
            line.calendar_can_remind=bool(row.get('time') and not row.get('remind') and row.get('state')=='active')
            self.output(line)
        except Exception as exc:
            chat.store.append(f'{chat.cfg.id}/calendar-agreements.jsonl',
                              {'at':now(),'error':type(exc).__name__})
