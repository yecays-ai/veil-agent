import json
import shutil
import socket
import subprocess
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from dataclasses import replace
from unittest.mock import Mock, patch

from veil_agent import ActivityState, Budget, Chat, Store, load_character, run_chat
from web_agent import Hub, serve_chat, start_server

TOKEN = 'test-token'


def free_port():
    with socket.socket() as s:
        s.bind(('127.0.0.1', 0))
        return s.getsockname()[1]


class WebTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        (root / 'characters' / 'test').mkdir(parents=True)
        (root / 'persona').mkdir()
        (root / 'persona' / 'test.md').write_text('Fictional test persona', encoding='utf-8')
        (root / 'characters' / 'test' / 'character.toml').write_text('''name = "Test"
provider = "fake"
persona = "persona/test.md"
max_output_tokens = 50
[providers.fake]
model = "fake-model"
base_url = "https://example.invalid/v1"
api_key_env = "FAKE_KEY"
max_input_tokens = 6000
input_cny_per_million = "2"
cached_input_cny_per_million = "1"
output_cny_per_million = "8"
''', encoding='utf-8')
        self.cfg = load_character(root, 'test')
        self.store = Store(root / 'data')
        self.model = Mock(return_value=('A fictional reply', 100, 20, 10))
        self.hub = Hub('Test', ActivityState(self.store.root / 'test' / 'resident-state.json'))
        self.request_number = 0
        self.port = free_port()
        self.server = start_server(self.hub, TOKEN, self.port)
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)

    def call(self, path, body=None, token=TOKEN, host=None, origin=None):
        if path == '/api/send' and body is not None:
            self.request_number += 1
            body.setdefault('request_id', f'test-request-{self.request_number}')
        headers = {'X-Token': token, 'Content-Type': 'application/json'}
        if host:
            headers['Host'] = host
        if origin:
            headers['Origin'] = origin
        req = urllib.request.Request(f'http://127.0.0.1:{self.port}{path}', headers=headers,
                                     data=None if body is None else json.dumps(body).encode())
        try:
            with urllib.request.urlopen(req, timeout=5) as r:
                return r.status, json.loads(r.read() or b'{}')
        except urllib.error.HTTPError as exc:
            return exc.code, None

    def test_submission_deduplication_window_is_bounded(self):
        hub = Hub('Test', ActivityState(self.store.root / 'test' / 'other-state.json'))
        with patch('web_agent.MAX_SUBMISSIONS', 2):
            for text, request_id in (('one', 'one'), ('two', 'two')):
                hub.busy = False
                self.assertEqual(hub.submit(text, request_id), 'accepted')
            self.assertEqual(hub.submit('one', 'one'), 'duplicate')
            hub.busy = False
            self.assertEqual(hub.submit('three', 'three'), 'accepted')
            self.assertEqual(len(hub.submissions), 2)
            self.assertNotIn('one', hub.submissions)

    def wait(self, predicate, seconds=5):
        end = threading.Event()
        for _ in range(int(seconds * 50)):
            if predicate():
                return True
            end.wait(0.02)
        return False

    def run_loop(self, scenario):
        chat = Chat(self.cfg, self.store, Budget(self.store), self.model,
                    approval=self.hub.approval, output=self.hub.output)
        result = {}

        def client():
            try:
                scenario(result)
            except BaseException as exc:  # reported in the main thread
                result['error'] = repr(exc)
            finally:
                self.hub.stopped = True

        thread = threading.Thread(target=client)
        thread.start()
        serve_chat(chat, self.hub)   # main thread: the chat loop uses signal handlers
        thread.join()
        self.assertNotIn('error', result, result.get('error'))
        return result

    def test_rejects_wrong_token_host_and_origin(self):
        self.assertEqual(self.call('/api/events', token='wrong')[0], 403)
        self.assertEqual(self.call('/api/events', host='evil.example')[0], 403)
        self.assertEqual(self.call('/api/events', origin='http://evil.example')[0], 403)
        self.assertEqual(self.call('/api/send', {'text': 'x'}, token='wrong')[0], 403)
        self.assertEqual(self.call('/api/events')[0], 200)
        self.model.assert_not_called()

    def test_activity_toggle_is_authorized_persistent_and_task_free(self):
        status, state = self.call('/api/events')
        self.assertEqual(status, 200)
        self.assertFalse(state['activity_enabled'])
        self.assertNotIn('token', state)
        self.assertEqual(self.call('/api/activity', {'enabled': True}, token='wrong')[0], 403)
        self.assertFalse(self.hub.activity.enabled())
        self.assertEqual(self.call('/api/activity', {'enabled': True})[0], 200)
        reopened = ActivityState(self.store.root / 'test' / 'resident-state.json')
        self.assertTrue(reopened.enabled())
        self.assertEqual(self.call('/api/activity', {'enabled': False})[0], 200)
        self.assertFalse(reopened.enabled())

    def test_explicit_exit_stops_new_submissions(self):
        self.assertEqual(self.call('/api/exit', {})[0], 200)
        self.assertTrue(self.hub.stopped)
        self.assertEqual(self.call('/api/send', {'text': 'not sent'})[0], 409)

    def test_terminal_pause_commands_are_persisted_without_model_calls(self):
        results = {}

        def scenario(_):
            self.assertTrue(self.wait(lambda: not self.call('/api/events')[1]['busy']))
            self.assertEqual(self.call('/api/send', {'text': '/resume'})[0], 200)
            self.assertTrue(self.wait(lambda: not self.call('/api/events')[1]['busy']))
            self.assertTrue(self.hub.activity.enabled())
            self.assertEqual(self.call('/api/send', {'text': '/pause'})[0], 200)
            self.assertTrue(self.wait(lambda: not self.call('/api/events')[1]['busy']))
            results['paused'] = not self.hub.activity.enabled()
            self.call('/api/send', {'text': '/quit'})

        self.run_loop(scenario)
        self.assertTrue(results['paused'])
        self.model.assert_not_called()

    def test_message_round_trip_and_commands(self):
        def scenario(result):
            self.assertTrue(self.wait(lambda: self.call('/api/events')[1]['busy'] is False))
            self.assertEqual(self.call('/api/send', {'text': '你好 hello'})[0], 200)
            self.assertTrue(self.wait(lambda: any(e['kind'] == 'assistant'
                                                  for e in self.call('/api/events')[1]['events'])))
            self.assertTrue(self.wait(lambda: self.call('/api/events')[1]['busy'] is False))
            self.call('/api/send', {'text': '/cost'})
            self.assertTrue(self.wait(lambda: any('今天' in e['text'] for e in self.call('/api/events')[1]['events'])))
            self.assertEqual(self.call('/api/send', {'text': '/quit'})[0], 200)   # ends the loop normally

        self.run_loop(scenario)
        kinds = [(e['kind'], e['text']) for e in self.hub.events]
        self.assertIn(('user', '你好 hello'), kinds)
        self.assertIn(('assistant', 'A fictional reply'), kinds)
        self.assertTrue(any(k == 'notice' and '正在保存并退出' in t for k, t in kinds))
        self.assertEqual(self.model.call_count, 1)
        saved = self.store.read('test/chat.jsonl')
        self.assertEqual([m['content'] for m in saved], ['你好 hello', 'A fictional reply'])

    def test_second_message_while_busy_is_refused(self):
        gate = threading.Event()
        self.model.side_effect = lambda *a, **k: (gate.wait(5), ('slow', 10, 5, 0))[1]

        def scenario(result):
            self.assertTrue(self.wait(lambda: self.call('/api/events')[1]['busy'] is False))
            result['first'] = self.call('/api/send', {'text': 'one', 'request_id': 'one'})[0]
            result['duplicate'] = self.call('/api/send', {'text': 'one', 'request_id': 'one'})[0]
            result['conflict'] = self.call('/api/send', {'text': 'different', 'request_id': 'one'})[0]
            result['second'] = self.call('/api/send', {'text': 'two', 'request_id': 'two'})[0]
            gate.set()
            self.assertTrue(self.wait(lambda: self.call('/api/events')[1]['busy'] is False))
            result['late_duplicate'] = self.call('/api/send', {'text': 'one', 'request_id': 'one'})[0]
            result['retry'] = self.call('/api/send', {'text': 'two', 'request_id': 'two'})[0]
            self.assertTrue(self.wait(lambda: sum(e['kind'] == 'assistant'
                                                  for e in self.call('/api/events')[1]['events']) == 2))

        result = self.run_loop(scenario)
        self.assertEqual((result['first'], result['duplicate'], result['conflict'], result['second'],
                          result['late_duplicate'], result['retry']), (200, 200, 409, 409, 200, 200))
        self.assertEqual(self.model.call_count, 2)
        self.assertEqual([m['content'] for m in self.store.read('test/chat.jsonl')
                          if m['role'] == 'user'], ['one', 'two'])
    def test_provider_failure_does_not_stop_web_chat_or_retry(self):
        self.model.side_effect = [RuntimeError('private provider detail'),
                                  ('A fictional reply', 100, 20, 10)]

        def scenario(result):
            self.assertTrue(self.wait(lambda: self.call('/api/events')[1]['busy'] is False))
            self.assertEqual(self.call('/api/send', {'text': 'first', 'request_id': 'first'})[0], 200)
            self.assertTrue(self.wait(lambda: any(e['kind'] == 'error'
                                                  for e in self.call('/api/events')[1]['events'])))
            self.assertTrue(self.wait(lambda: self.call('/api/events')[1]['busy'] is False))
            result['second'] = self.call('/api/send', {'text': 'second', 'request_id': 'second'})[0]
            self.assertTrue(self.wait(lambda: any(e['text'] == 'A fictional reply'
                                                  for e in self.call('/api/events')[1]['events'])))

        self.assertEqual(self.run_loop(scenario)['second'], 200)
        self.assertEqual(self.model.call_count, 2)
        self.assertEqual([m['content'] for m in self.store.read('test/chat.jsonl')
                          if m['role'] == 'user'], ['first', 'second'])
        self.assertGreater(Budget(self.store).totals()[2], 0)

    def test_concurrent_distinct_sends_have_one_atomic_winner(self):
        gate = threading.Event()
        self.model.side_effect = lambda *a, **k: (gate.wait(5), ('slow', 10, 5, 0))[1]

        def scenario(result):
            self.assertTrue(self.wait(lambda: self.call('/api/events')[1]['busy'] is False))
            barrier = threading.Barrier(3)
            statuses = []

            def send(text, request_id):
                barrier.wait()
                statuses.append(self.call('/api/send', {'text': text, 'request_id': request_id})[0])

            threads = [threading.Thread(target=send, args=('one', 'race-one')),
                       threading.Thread(target=send, args=('two', 'race-two'))]
            for thread in threads:
                thread.start()
            barrier.wait()
            for thread in threads:
                thread.join(5)
            result['statuses'] = statuses
            gate.set()
            self.assertTrue(self.wait(lambda: any(e['kind'] == 'assistant'
                                                  for e in self.call('/api/events')[1]['events'])))

        result = self.run_loop(scenario)
        self.assertEqual(sorted(result['statuses']), [200, 409])
        self.assertEqual(self.model.call_count, 1)

    def test_approval_buttons_map_to_y_and_n(self):
        answers = {}

        def ask(name, prompt):
            answers[name] = self.hub.approval(prompt)

        for name, button in (('yes', 'y'), ('no', 'n')):
            thread = threading.Thread(target=ask, args=(name, f'Peter 想把内容写到 {name}.txt（新文件）。同意吗？y/N '))
            thread.start()
            self.assertTrue(self.wait(lambda: any(e['kind'] == 'approval' and name in e['text']
                                                  for e in self.hub.events)))
            pending = self.call('/api/events')[1]['pending']
            self.assertEqual(self.call('/api/approve', {'answer': button, 'id': pending})[0], 200)
            self.assertEqual(self.call('/api/approve', {'answer': 'y', 'id': pending})[0], 409)  # once only
            thread.join(5)
        self.assertEqual(answers, {'yes': 'y', 'no': 'n'})
        shown = [e['text'] for e in self.hub.events if e['kind'] == 'approval']
        self.assertEqual(shown[0], 'Peter 想把内容写到 yes.txt（新文件）。同意吗？y/N ')
        self.assertEqual(self.call('/api/approve', {'answer': 'maybe'})[0], 400)

    def test_stale_or_unasked_approval_answers_are_refused(self):
        # No approval pending: a stray click must not be remembered for the next one.
        self.assertEqual(self.call('/api/approve', {'answer': 'y', 'id': 1})[0], 409)
        self.assertEqual(self.call('/api/approve', {'answer': 'y'})[0], 409)
        result = {}
        thread = threading.Thread(target=lambda: result.update(a=self.hub.approval('Peter 想把内容写到 b.txt（新文件）。同意吗？y/N ')))
        thread.start()
        self.assertTrue(self.wait(lambda: self.hub.pending is not None))
        current = self.hub.pending
        self.assertEqual(self.call('/api/approve', {'answer': 'y', 'id': current + 1})[0], 409)  # wrong card
        self.assertEqual(self.call('/api/approve', {'answer': 'n', 'id': current})[0], 200)
        thread.join(5)
        self.assertEqual(result['a'], 'n')

    def test_history_is_shown_on_open(self):
        self.store.append('test/chat.jsonl', {'role': 'user', 'content': 'earlier'})
        self.store.append('test/chat.jsonl', {'role': 'assistant', 'content': 'earlier reply'})

        def scenario(result):
            self.assertTrue(self.wait(lambda: self.call('/api/events')[1]['busy'] is False))
            result['events'] = self.call('/api/events')[1]['events']

        events = self.run_loop(scenario)['events']
        self.assertEqual([(e['kind'], e['text']) for e in events[:2]],
                         [('user', 'earlier'), ('assistant', 'earlier reply')])
        self.assertTrue(all(e['history'] and e['time'] for e in events[:2]))

    def test_multi_message_http_events_match_persisted_history(self):
        from tests.test_tools import ToolLoopTests
        fixture = ToolLoopTests()
        texts = ['Hello.', '你好。', 'See you，明天见。']
        calls = [fixture.tool_call('send_message', json.dumps({'text': text}), str(i))
                 for i, text in enumerate(texts)]
        self.cfg = replace(self.cfg, tools={'enabled': True, 'max_steps': 3})
        self.model.return_value = (fixture.message(calls), 100, 30, 0)

        def scenario(result):
            self.assertTrue(self.wait(lambda: not self.call('/api/events')[1]['busy']))
            self.assertEqual(self.call('/api/send', {'text': 'hello'})[0], 200)
            self.assertTrue(self.wait(lambda: not self.call('/api/events')[1]['busy']))
            result['messages'] = [e['text'] for e in self.call('/api/events')[1]['events'] if e['kind'] == 'assistant']
            self.call('/api/send', {'text': '/cost'})
            self.assertTrue(self.wait(lambda: not self.call('/api/events')[1]['busy']))
            self.call('/api/send', {'text': '/quit'})

        self.assertEqual(self.run_loop(scenario)['messages'], texts)
        self.assertEqual([m['content'] for m in self.store.read('test/chat.jsonl')][1:], texts)
        self.model.assert_called_once()


class PacingTests(unittest.TestCase):
    @unittest.skipUnless(shutil.which('node'), 'Node required for page behavior test')
    def test_page_pacing_grouping_and_refresh(self):
        result = subprocess.run([shutil.which('node'), str(Path(__file__).with_name('web_pacing.cjs'))],
                                capture_output=True, text=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == '__main__':
    unittest.main()

