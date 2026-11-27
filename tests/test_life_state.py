"""Ticket 352 fixture: fake clock, fictional chat log, fake model, no paid calls.

Covers gaps of 10 minutes / 3 hours / 2 days, a long task that is active / paused / completed,
and life records with today's event plus checked and unchecked to-dos. Asserts what is injected
and that it sits in the per-turn tail, never in the cached prefix."""
import json
import tempfile
import unittest
from pathlib import Path

from veil.life.life_state import gap_text, state_text, time_separator, return_window
from veil.core.veil_agent import Budget, Character, Chat, Store

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
        self.assertIn('may state the duration', text)
        self.assertIn('Her silence may mean busy, asleep, or upset.', text)
        self.assertNotIn('does not mean she was upset', text)

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

    def test_regular_proactive_messages_do_not_hide_a_long_absence(self):
        rows = chat_rows('2026-11-12T03:00:00+08:00', [f'2026-11-12T0{hour}:00:00+08:00' for hour in range(4, 9)])
        text = gap_text(rows, NOW)
        self.assertIn('about 6 hours', text)
        self.assertIn('on your own 5 time(s)', text)
        self.assertIn('sleep after that message is unknown', text)

    def test_current_message_is_excluded(self):
        rows = chat_rows('2026-11-10T08:30:00+08:00') + [
            {'time': NOW, 'role': 'user', 'content': '我回来了', 'message_id': 'now'}]
        self.assertEqual(gap_text(rows, NOW), '')
        self.assertIn('about 2 days', gap_text(rows, NOW, ['now']))

    def test_no_previous_message_or_bad_time(self):
        self.assertEqual(gap_text([], NOW), '')
        self.assertEqual(gap_text([{'role': 'user', 'time': 'broken'}], NOW), '')

    def test_recalled_user_message_does_not_reset_absence(self):
        rows = chat_rows('2026-11-10T08:30:00+08:00') + [
            {'time': '2026-11-12T08:55:00+08:00', 'role': 'user', 'content': 'hidden', 'recalled': True}]
        self.assertIn('about 2 days', gap_text(rows, NOW))
        self.assertNotIn('hidden', gap_text(rows, NOW))

    def test_history_boundary_uses_actual_gap(self):
        self.assertIn('about 3 hours later', time_separator('2026-11-12T06:00:00+08:00', NOW))
        self.assertEqual(time_separator('2026-11-12T08:30:00+08:00', NOW), '')

    def test_return_window_keeps_two_followups_and_then_expires(self):
        rows = chat_rows('2026-11-12T03:00:00+08:00')
        self.assertEqual(return_window(rows, NOW), ('2026-11-12T03:00:00+08:00', NOW))
        for minute in range(3):
            rows.extend([{'role': 'user', 'content': '话', 'time': f'2026-11-12T09:0{minute}:00+08:00'},
                         {'role': 'assistant', 'content': '答', 'time': f'2026-11-12T09:0{minute}:10+08:00'}])
            found = return_window(rows, f'2026-11-12T09:0{minute + 1}:00+08:00')
            if minute < 2:
                self.assertEqual(found, ('2026-11-12T03:00:00+08:00', NOW))
            else:
                self.assertIsNone(found)

    def test_return_window_counts_silent_requests_but_groups_a_batch(self):
        rows = chat_rows('2026-11-12T03:00:00+08:00')
        rows.extend([{'role': 'user', 'time': '2026-11-12T09:00:00+08:00', 'request_id': 'one'},
                     {'role': 'user', 'time': '2026-11-12T09:00:01+08:00', 'request_id': 'one'},
                     {'role': 'user', 'time': '2026-11-12T09:01:00+08:00', 'request_id': 'two'},
                     {'role': 'user', 'time': '2026-11-12T09:02:00+08:00', 'request_id': 'three'}])
        self.assertIsNone(return_window(rows, '2026-11-12T09:03:00+08:00'))
        self.assertIsNotNone(return_window(rows[:-1], '2026-11-12T09:02:00+08:00'))

    def test_failed_activity_separates_failure_from_plan(self):
        from veil.autonomy.autonomy import activities_since
        import tempfile
        from pathlib import Path
        from types import SimpleNamespace
        with tempfile.TemporaryDirectory() as folder:
            store = Store(Path(folder))
            store.append('fake/autonomy.jsonl', {
                'started_at': '2026-11-12T05:00:00+08:00', 'finished_at': '2026-11-12T06:00:00+08:00',
                'decision': 'build a rocket', 'reason': '',
                'tools': [{'tool': 'write_file', 'path': 'blueprint', 'state': '出错'}],
                'lab_files_changed': [], 'result': '失败', 'result_reason': 'write failed',
                'current_focus': 'rocket', 'next_step': 'repair the blueprint'})
            text = activities_since(store, SimpleNamespace(id='fake'), '2026-11-12T03:00:00+08:00', NOW)
            self.assertIn('[failed]', text)
            self.assertIn('failed or interrupted', text)
            self.assertIn('planned next step', text)
            self.assertNotIn('completed tool actions', text)
            self.assertLessEqual(len(text), 300)
            future = {'started_at': '2026-11-12T08:30:00+08:00', 'finished_at': '2026-11-12T09:30:00+08:00',
                      'decision': 'future', 'reason': '', 'tools': [], 'lab_files_changed': [], 'result': '完成',
                      'result_reason': '', 'current_focus': '', 'next_step': ''}
            store.append('fake/autonomy.jsonl', future)
            self.assertNotIn('future', activities_since(store, SimpleNamespace(id='fake'),
                                                       '2026-11-12T03:00:00+08:00', NOW))


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
        self.assertEqual([e['title'] for e in data['calendar_events_today_and_next_3_days']], ['下午和 Lan 一起看电影'])
        # n1 unchecked, n3 is daily and was ticked yesterday so it is unchecked again; n2 done; n4 is Lan's and,
        # since ticket 385 (one shared calendar), he sees it too.
        self.assertEqual(data['unchecked_todo_count'], 3)
        self.assertEqual({t['title'] for t in data['unchecked_todos']}, {'给多肉浇水', '每天读一页书', 'Lan 自己的待办'})
        self.assertTrue(all(t['date'] for t in data['unchecked_todos']))
        self.assertNotIn('写完那首诗', json.dumps(data, ensure_ascii=False))

    def test_next_three_days_in_time_order_with_the_rest_counted(self):
        # Ticket 385: today plus three days, sorted by date then time; past MAX_EVENTS he is told how many more.
        events = [{'id': f'x{i}', 'kind': 'event', 'title': f'事{i}', 'date': f'2026-11-1{2 + i % 4}',
                   'time': f'{9 + i:02d}:00', 'state': 'active', 'created_by': 'Lan'} for i in range(8)]
        events.append({'id': 'late', 'kind': 'event', 'title': '四天后', 'date': '2026-11-16', 'state': 'active'})
        text = state_text(None, (), events, (), '2026-11-12', moods={'2026-11-12': {'Lan': {'emoji': '🥰'}}})
        data = data_of(text)
        shown = data['calendar_events_today_and_next_3_days']
        self.assertEqual(len(shown), 6)
        self.assertEqual([(e['date'], e['time']) for e in shown], sorted((e['date'], e['time']) for e in shown))
        self.assertEqual(shown[0]['written_by'], 'Lan')
        self.assertEqual(data['calendar_events_not_shown'], 2)
        self.assertIn('2 more calendar events', text)
        self.assertNotIn('四天后', text)
        self.assertEqual(data['todays_moods'], {'Lan': '🥰'})

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

    def test_returning_chat_receives_only_activities_during_absence(self):
        chat = self.chat('2026-11-12T03:00:00+08:00')
        def activity(stamp, decision):
            return {'started_at': stamp, 'finished_at': stamp, 'decision': decision, 'reason': '', 'tools': [
                        {'tool': 'write_file', 'path': decision, 'state': '成功'}],
                    'lab_files_changed': [], 'result': '完成', 'result_reason': '',
                    'current_focus': '', 'next_step': ''}
        self.store.append('fake/autonomy.jsonl', activity('2026-11-12T02:00:00+08:00', 'old'))
        self.store.append('fake/autonomy.jsonl', activity('2026-11-12T05:00:00+08:00', 'finished a drawing'))
        chat.send('我回来了')
        prompt = json.dumps(self.sent[-1], ensure_ascii=False)
        self.assertIn('finished a drawing', prompt)
        self.assertNotIn('write_file old', prompt)
        self.assertIn('about 6 hours', prompt)
        saved = [json.loads(line) for line in (Path(self.store.root) / 'fake' / 'chat.jsonl').read_text(
            encoding='utf-8').splitlines()]
        from datetime import datetime, timedelta
        last_user = next(row for row in reversed(saved) if row['role'] == 'user')
        self.clock[0] = (datetime.fromisoformat(last_user['time']) + timedelta(minutes=1)).isoformat()
        chat.send('接着聊')
        self.assertIn('finished a drawing', json.dumps(self.sent[-1], ensure_ascii=False))

    def test_history_gap_is_shown_to_model_without_changing_log(self):
        chat = self.chat('2026-11-10T08:30:00+08:00')
        chat.send('我回来了')
        self.assertIn('later ·', json.dumps(self.sent[-1], ensure_ascii=False))
        self.assertNotIn('later ·', (Path(self.store.root) / 'fake' / 'chat.jsonl').read_text(encoding='utf-8'))

    def test_regeneration_uses_original_return_time_and_activity_window(self):
        chat = self.chat('2026-11-12T03:00:00+08:00')
        self.store.append('fake/autonomy.jsonl', {
            'started_at': '2026-11-12T05:00:00+08:00', 'finished_at': '2026-11-12T06:00:00+08:00',
            'decision': 'made a drawing', 'reason': '',
            'tools': [{'tool': 'write_file', 'path': 'made a drawing', 'state': '成功'}],
            'lab_files_changed': [], 'result': '完成', 'result_reason': '',
            'current_focus': '', 'next_step': ''})
        path = Path(self.store.root) / 'fake' / 'chat.jsonl'
        with path.open('a', encoding='utf-8') as handle:
            handle.write(json.dumps({'time': NOW, 'role': 'user', 'content': 'back', 'message_id': 'u2',
                                     'request_id': 'r2'}) + '\n')
            handle.write(json.dumps({'time': NOW, 'role': 'assistant', 'content': 'hello', 'message_id': 'a2',
                                     'request_id': 'r2'}) + '\n')
        chat.history, chat.rounds = chat.recent()
        chat.prepare_regeneration('a2')
        self.clock[0] = '2026-11-13T09:00:00+08:00'
        chat.send('back', persist_user=False, regeneration_parent_message_id='u2')
        prompt = json.dumps(self.sent[-1], ensure_ascii=False)
        self.assertIn('about 6 hours', prompt)
        self.assertIn('made a drawing', prompt)
        self.assertNotIn('about 1 day', prompt)

    def test_followup_retains_the_original_absence_without_guessing_sleep(self):
        chat = self.chat('2026-11-12T03:00:00+08:00')
        path = Path(self.store.root) / 'fake' / 'chat.jsonl'
        with path.open('a', encoding='utf-8') as handle:
            handle.write(json.dumps({'time': NOW, 'role': 'user', 'content': 'back', 'message_id': 'u2'}) + '\n')
            handle.write(json.dumps({'time': NOW, 'role': 'assistant', 'content': 'hello', 'message_id': 'a2'}) + '\n')
        chat.history, chat.rounds = chat.recent()
        self.clock[0] = '2026-11-12T09:02:00+08:00'
        chat.send('我不是回来了么？')
        prompt = json.dumps(self.sent[-1], ensure_ascii=False)
        self.assertIn('Earlier in this conversation, when she returned', prompt)
        self.assertIn('about 6 hours', prompt)
        self.assertIn('sleep after that message is unknown', prompt)

    def test_queued_message_uses_received_time_even_after_long_lock_wait(self):
        chat = self.chat('2026-11-12T08:50:00+08:00')
        self.store.append('fake/incoming-submissions.jsonl', {
            'state': 'received', 'time': NOW, 'content': 'hello', 'message_id': 'queued'})
        self.store.append('fake/autonomy.jsonl', {
            'started_at': '2026-11-12T10:00:00+08:00', 'finished_at': '2026-11-12T10:05:00+08:00',
            'decision': 'later activity', 'reason': '', 'tools': [], 'lab_files_changed': [],
            'result': '完成', 'result_reason': '', 'current_focus': '', 'next_step': ''})
        self.clock[0] = '2026-11-12T11:00:00+08:00'
        chat.send('hello', message_id='queued', user_events=[{'content': 'hello', 'message_id': 'queued',
                                                               'persisted': True}])
        self.assertNotIn('about 2 hours', json.dumps(self.sent[-1], ensure_ascii=False))
        self.assertNotIn('later activity', json.dumps(self.sent[-1], ensure_ascii=False))
        saved = self.store.read('fake/chat.jsonl')
        self.assertEqual(next(row['time'] for row in saved if row.get('message_id') == 'queued'), NOW)

    def test_regeneration_excludes_later_activity_even_without_a_return_gap(self):
        chat = self.chat('2026-11-12T08:50:00+08:00')
        path = Path(self.store.root) / 'fake' / 'chat.jsonl'
        with path.open('a', encoding='utf-8') as handle:
            handle.write(json.dumps({'time': NOW, 'role': 'user', 'content': 'hello', 'message_id': 'u2',
                                     'request_id': 'r2'}) + '\n')
            handle.write(json.dumps({'time': NOW, 'role': 'assistant', 'content': 'hi', 'message_id': 'a2',
                                     'request_id': 'r2'}) + '\n')
        self.store.append('fake/autonomy.jsonl', {
            'started_at': '2026-11-12T10:00:00+08:00', 'finished_at': '2026-11-12T10:05:00+08:00',
            'decision': 'future activity', 'reason': '', 'tools': [], 'lab_files_changed': [],
            'result': '完成', 'result_reason': '', 'current_focus': '', 'next_step': ''})
        chat.history, chat.rounds = chat.recent()
        chat.prepare_regeneration('a2')
        self.clock[0] = '2026-11-12T11:00:00+08:00'
        chat.send('hello', persist_user=False, regeneration_parent_message_id='u2')
        self.assertNotIn('future activity', json.dumps(self.sent[-1], ensure_ascii=False))
        self.assertEqual(self.store.read('fake/autonomy-chat-handoffs.jsonl'), [])

    def test_regeneration_excludes_activity_started_before_but_finished_after_reply(self):
        chat = self.chat('2026-11-12T08:50:00+08:00')
        path = Path(self.store.root) / 'fake' / 'chat.jsonl'
        with path.open('a', encoding='utf-8') as handle:
            handle.write(json.dumps({'time': NOW, 'role': 'user', 'content': 'hello', 'message_id': 'u2',
                                     'request_id': 'r2'}) + '\n')
            handle.write(json.dumps({'time': NOW, 'role': 'assistant', 'content': 'hi', 'message_id': 'a2',
                                     'request_id': 'r2'}) + '\n')
        self.store.append('fake/autonomy.jsonl', {
            'started_at': '2026-11-12T08:55:00+08:00', 'finished_at': '2026-11-12T09:05:00+08:00',
            'decision': 'finished later', 'reason': '', 'tools': [], 'lab_files_changed': [],
            'result': '完成', 'result_reason': '', 'current_focus': '', 'next_step': ''})
        chat.history, chat.rounds = chat.recent()
        chat.prepare_regeneration('a2')
        self.clock[0] = '2026-11-12T11:00:00+08:00'
        chat.send('hello', persist_user=False, regeneration_parent_message_id='u2')
        self.assertNotIn('finished later', json.dumps(self.sent[-1], ensure_ascii=False))

    def test_regeneration_keeps_activity_completed_before_original_turn(self):
        chat = self.chat('2026-11-12T08:50:00+08:00')
        path = Path(self.store.root) / 'fake' / 'chat.jsonl'
        with path.open('a', encoding='utf-8') as handle:
            handle.write(json.dumps({'time': NOW, 'role': 'user', 'content': 'hello', 'message_id': 'u2',
                                     'request_id': 'r2'}) + '\n')
            handle.write(json.dumps({'time': NOW, 'role': 'assistant', 'content': 'hi', 'message_id': 'a2',
                                     'request_id': 'r2'}) + '\n')
        self.store.append('fake/autonomy.jsonl', {
            'started_at': '2026-11-12T08:51:00+08:00', 'finished_at': '2026-11-12T08:55:00+08:00',
            'decision': 'before original', 'reason': '', 'tools': [], 'lab_files_changed': [],
            'result': '完成', 'result_reason': '', 'current_focus': '', 'next_step': ''})
        chat.history, chat.rounds = chat.recent()
        chat.prepare_regeneration('a2')
        self.clock[0] = '2026-11-12T11:00:00+08:00'
        chat.send('hello', persist_user=False, regeneration_parent_message_id='u2')
        self.assertIn('before original', json.dumps(self.sent[-1], ensure_ascii=False))

    def test_next_turn_keeps_return_gap_without_claiming_a_new_absence(self):
        chat = self.chat('2026-11-10T08:30:00+08:00')
        chat.send('我回来了')
        self.assertIn('Time since Lan last wrote', self.sent[-1][-2]['content'])
        # Store.append stamps real time; move the fake clock to just after that real time.
        last = json.loads((Path(self.store.root) / 'fake' / 'chat.jsonl').read_text(
            encoding='utf-8').splitlines()[-2])['time']
        from datetime import datetime, timedelta
        self.clock[0] = (datetime.fromisoformat(last) + timedelta(minutes=10)).isoformat()
        chat.send('刚才在忙')
        self.assertIn('Earlier in this conversation, when she returned', self.sent[-1][-2]['content'])

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

    def test_a_notice_from_autonomy_uses_the_channel_kept_for_it(self):
        # During autonomy the ordinary output is muted; the notice must still reach Lan.
        chat = self.chat('2026-11-12T08:55:00+08:00')
        muted, kept = [], []
        chat.output, chat.autonomy_message_output, chat.autonomy_mode = muted.append, kept.append, True
        hers = chat.life_records.write({'kind': 'event', 'title': '体检', 'date': '2026-11-22'}, 'Lan')
        chat.life_records.invoke({'action': 'update', 'id': hers['id'], 'time': '09:00'})
        self.assertEqual((len(muted), len(kept)), (0, 1))
        self.assertEqual(kept[0].life_id, hers['id'])

    def test_peter_changing_lans_record_tells_her_in_a_notice_not_in_his_history(self):
        # Ticket 385: one grey line for Lan; ticking her daily to-do sends nothing.
        from veil.core.veil_agent import LifeNotice
        chat = self.chat('2026-11-12T08:55:00+08:00')
        shown = []
        chat.output = shown.append
        hers = chat.life_records.write({'kind': 'event', 'title': '带团子体检', 'date': '2026-11-22'}, 'Lan')
        chat.life_records.invoke({'action': 'update', 'id': hers['id'], 'date': '2026-11-23'})
        self.assertEqual(len(shown), 1)
        self.assertIsInstance(shown[0], LifeNotice)
        self.assertEqual(shown[0].life_id, hers['id'])
        self.assertIn('Peter 改了你的日程：11/22 带团子体检 → 11/23 带团子体检', shown[0])
        daily = chat.life_records.write({'kind': 'note', 'title': '写日记', 'repeat': 'daily'}, 'Lan')
        chat.life_records.invoke({'action': 'update', 'id': daily['id'], 'state': 'completed'})
        self.assertEqual(len(shown), 1)
        chat.send('hi')
        self.assertNotIn('Peter 改了你的', json.dumps(self.sent[-1], ensure_ascii=False))


if __name__ == '__main__':
    unittest.main()
