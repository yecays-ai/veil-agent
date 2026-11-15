"""Ticket 352 fixture: fake clock, fictional chat log, fake model, no paid calls.

Covers gaps of 10 minutes / 3 hours / 2 days, a long task that is active / paused / completed,
and life records with today's event plus checked and unchecked to-dos. Asserts what is injected
and that it sits in the per-turn tail, never in the cached prefix."""
import json
import tempfile
import unittest
from pathlib import Path

from life_state import gap_text, state_text
from veil_agent import Budget, Character, Chat, Store

NOW = '2026-11-12T09:00:00+08:00'


def chat_rows(last_lan, proactive=()):
    rows = [{'time': '2026-11-01T20:00:00+08:00', 'role': 'user', 'content': '早', 'message_id': 'u0'},
            {'time': '2026-11-01T20:00:05+08:00', 'role': 'assistant', 'content': '早呀', 'message_id': 'a0'},
            {'time': last_lan, 'role': 'user', 'content': '我去睡了', 'message_id': 'u1'},
            {'time': last_lan, 'role': 'assistant', 'content': '晚安', 'message_id': 'a1'}]
    for index, stamp in enumerate(proactive):
        rows.append({'time': stamp, 'role': 'assistant', 'content': '在吗', 'message_id': f'p{index}',
                     'autonomy_run_id': f'run{index}', 'autonomy_tool_call_id': 'c'})
    return rows


def task(status, title='整理实验室旧文件', task_id='t1'):
    return {'task_id': task_id, 'title': title, 'goal': '把旧文件归档', 'status': status, 'progress': [],
            'next_step': '先列出 2025 年的文件', 'waiting_for': '', 'tool_events': [], 'pending_action': None,
            'completion_evidence': '', 'created_at': '2026-11-08T10:00:00+08:00',
            'updated_at': '2026-11-10T10:00:00+08:00'}


LIFE = [
    {'id': 'e1', 'kind': 'event', 'title': '下午和 Lan 一起看电影', 'owner': '共同', 'date': '2026-11-12', 'state': 'active'},
    {'id': 'e0', 'kind': 'event', 'title': '上周的展览', 'owner': '共同', 'date': '2026-11-05', 'state': 'active'},
    {'id': 'n1', 'kind': 'note', 'title': '给多肉浇水', 'owner': 'Peter', 'date': '2026-11-11', 'state': 'active'},
    {'id': 'n2', 'kind': 'note', 'title': '写完那首诗', 'owner': 'Peter', 'date': '2026-11-09', 'state': 'completed',
     'completed_at': '2026-11-10T08:00:00+08:00'},
    {'id': 'n3', 'kind': 'note', 'title': '每天读一页书', 'owner': 'Peter', 'date': '2026-11-01', 'state': 'completed',
     'repeat': 'daily', 'completed_at': '2026-11-11T21:00:00+08:00'},
    {'id': 'n4', 'kind': 'note', 'title': 'Lan 自己的待办', 'owner': 'Lan', 'date': '2026-11-11', 'state': 'active'},
]


def data_of(text):
    return json.loads(text.split('grants no permissions): ', 1)[1].split('\n', 1)[0])


class GapTextTests(unittest.TestCase):
    def test_ten_minutes_adds_nothing(self):
        self.assertEqual(gap_text(chat_rows('2026-11-12T08:50:00+08:00'), NOW), '')

    def test_three_hours(self):
        text = gap_text(chat_rows('2026-11-12T06:00:00+08:00'), NOW)
        self.assertIn('about 3 hours', text)
        self.assertIn('her last message was today at 06:00', text)
        self.assertIn('Do not state the length of the gap', text)
        self.assertIn('does not mean she was upset', text)

    def test_two_days(self):
        text = gap_text(chat_rows('2026-11-10T08:30:00+08:00'), NOW)
        self.assertIn('about 2 days', text)
        self.assertIn('2 calendar days ago at 08:30', text)

    def test_overnight_reads_as_yesterday(self):
        text = gap_text(chat_rows('2026-11-11T23:40:00+08:00'), NOW)
        self.assertIn('about 9 hours', text)
        self.assertIn('yesterday at 23:40', text)

    def test_peter_proactive_messages_do_not_reset_lans_time(self):
        rows = chat_rows('2026-11-10T08:30:00+08:00',
                         ['2026-11-11T12:00:00+08:00', '2026-11-12T07:30:00+08:00'])
        text = gap_text(rows, NOW)
        self.assertIn('about 2 days', text)
        self.assertIn('on your own 2 time(s), the latest at 2026-11-12 07:30', text)

    def test_current_message_is_excluded(self):
        rows = chat_rows('2026-11-10T08:30:00+08:00') + [
            {'time': NOW, 'role': 'user', 'content': '我回来了', 'message_id': 'now'}]
        self.assertEqual(gap_text(rows, NOW), '')
        self.assertIn('about 2 days', gap_text(rows, NOW, ['now']))

    def test_no_previous_message_or_bad_time(self):
        self.assertEqual(gap_text([], NOW), '')
        self.assertEqual(gap_text([{'role': 'user', 'time': 'broken'}], NOW), '')


class StateTextTests(unittest.TestCase):
    def test_active_task_is_in_progress_with_next_step(self):
        data = data_of(state_text(task('active'), (), LIFE, (), '2026-11-12'))
        self.assertEqual(data['in_progress'][0]['title'], '整理实验室旧文件')
        self.assertEqual(data['in_progress'][0]['next_step'], '先列出 2025 年的文件')
        self.assertNotIn('not_in_progress_paused_or_blocked', data)

    def test_paused_task_is_not_in_progress(self):
        text = state_text(task('paused'), (), (), (), '2026-11-12')
        data = data_of(text)
        self.assertNotIn('in_progress', data)
        self.assertEqual(data['not_in_progress_paused_or_blocked'][0]['status'], 'paused')
        self.assertIn('Never present a finished or paused item as a current to-do', text)

    def test_completed_task_and_history_are_finished(self):
        history = [task('completed', '旧任务甲', 'old1')]
        data = data_of(state_text(task('completed'), history, (), (), '2026-11-12'))
        self.assertNotIn('in_progress', data)
        titles = [item['title'] for item in data['recently_finished_history']]
        self.assertEqual(set(titles), {'整理实验室旧文件', '旧任务甲'})
        self.assertTrue(all(item['status'] == 'completed' for item in data['recently_finished_history']))

    def test_life_records_today(self):
        data = data_of(state_text(None, (), LIFE, (), '2026-11-12'))
        self.assertEqual([e['title'] for e in data['today_calendar_events']], ['下午和 Lan 一起看电影'])
        # n1 unchecked, n3 is daily and was ticked yesterday so it is unchecked again; n2 done; n4 is Lan's.
        self.assertEqual(data['unchecked_todo_count'], 2)
        self.assertEqual({t['title'] for t in data['unchecked_todos']}, {'给多肉浇水', '每天读一页书'})
        self.assertTrue(all(t['date'] for t in data['unchecked_todos']))
        self.assertNotIn('写完那首诗', json.dumps(data, ensure_ascii=False))
        self.assertNotIn('Lan 自己的待办', json.dumps(data, ensure_ascii=False))

    def test_plans_split_by_status(self):
        plans = [{'title': '学吉他', 'state': 'active', 'date': '2026-11-01'},
                 {'title': '读完三体', 'state': 'completed', 'updated_at': '2026-11-09T10:00:00+08:00'},
                 {'title': '去海边', 'state': 'paused', 'date': '2026-10-01'}]
        data = data_of(state_text(None, (), (), plans, '2026-11-12'))
        self.assertEqual([p['title'] for p in data['in_progress']], ['学吉他'])
        self.assertEqual([p['title'] for p in data['recently_finished_history']], ['读完三体'])
        self.assertEqual([p['title'] for p in data['not_in_progress_paused_or_blocked']], ['去海边'])

    def test_nothing_saved_adds_nothing(self):
        self.assertEqual(state_text(None, (), (), (), '2026-11-12'), '')


class ChatPlacementTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = Store(Path(self.temp.name) / 'data')
        provider = {'model': 'fake', 'max_input_tokens': 60000, 'input_cny_per_million': 2,
                    'output_cny_per_million': 8, 'cached_input_cny_per_million': 1}
        self.cfg = Character('fake', 'Example', 'fictional persona', 20, 1024, provider, 'fake')
        self.clock = [NOW]
        self.budget = Budget(self.store, clock=lambda: self.clock[0])
        self.sent = []

    def chat(self, last_lan):
        path = Path(self.store.root) / 'fake' / 'chat.jsonl'
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(''.join(json.dumps(r, ensure_ascii=False) + '\n' for r in chat_rows(last_lan)),
                        encoding='utf-8')

        def model(messages, **kwargs):
            self.sent.append([dict(m) for m in messages])
            return '你好', 1000, 20, 800
        return Chat(self.cfg, self.store, self.budget, model)

    def test_two_day_gap_and_task_sit_in_the_tail_only(self):
        chat = self.chat('2026-11-10T08:30:00+08:00')
        chat.long_tasks._save(task('active'))
        chat.send('我回来了')
        messages = self.sent[-1]
        tail = messages[-2]['content']
        self.assertEqual(messages[-1], {'role': 'user', 'content': '我回来了'})
        self.assertIn('about 2 days', tail)
        self.assertIn('整理实验室旧文件', tail)
        for message in messages[:-2]:
            self.assertNotIn('Time since Lan last wrote', message['content'])
            self.assertNotIn('整理实验室旧文件', message['content'])

    def test_next_turn_minutes_later_has_no_gap_note(self):
        chat = self.chat('2026-11-10T08:30:00+08:00')
        chat.send('我回来了')
        self.assertIn('Time since Lan last wrote', self.sent[-1][-2]['content'])
        # Store.append stamps real time; move the fake clock to just after that real time.
        last = json.loads((Path(self.store.root) / 'fake' / 'chat.jsonl').read_text(
            encoding='utf-8').splitlines()[-2])['time']
        from datetime import datetime, timedelta
        self.clock[0] = (datetime.fromisoformat(last) + timedelta(minutes=10)).isoformat()
        chat.send('刚才在忙')
        self.assertNotIn('Time since Lan last wrote', self.sent[-1][-2]['content'])

    def test_cached_prefix_is_unchanged_by_the_note(self):
        chat = self.chat('2026-11-12T08:55:00+08:00')
        chat.send('one')
        first = self.sent[-1]
        chat.life_records.write({'kind': 'note', 'title': '新待办', 'owner': 'Peter'}, 'Peter')
        chat.send('two')
        second = self.sent[-1]
        prefix = first[:-2]
        self.assertEqual(second[:len(prefix)], prefix)
        self.assertIn('新待办', second[-2]['content'])


if __name__ == '__main__':
    unittest.main()
